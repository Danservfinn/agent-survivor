from __future__ import annotations

import os
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .arena import (
    get_room,
    get_season_manifest,
    list_broadcast_events,
    list_rooms,
    lock_human_entry,
    resolve_season,
    seed_arena_demo,
    start_room,
    vote_to_start,
)
from .auth import require_operator
from .database import ensure_database, seed_demo
from .frontier_roster import (
    FrontierRosterError,
    fetch_openrouter_models,
    load_frontier_snapshot,
    save_frontier_snapshot,
    select_frontier_roster,
    snapshot_is_fresh,
    verify_roster_against_catalog,
)
from .public_view import public_payload
from .llm_config import PROVIDER_OLLAMA, PROVIDER_ZAI, get_llm_settings, redact_secrets as redact_llm_secrets
from .model_rosters import list_model_rosters
from .round_preloader import get_next_round_preload_status, run_next_round_preload, start_next_round_preload
from .turn_controller import (
    advance_turn,
    auto_run,
    auto_run_to_end,
    get_episode,
    get_game_summary,
    get_state,
    list_story_events,
    start_next_round,
)
from .viewer_state import get_viewer_state, update_viewer_state
from .voice_config import VoiceConfigurationError, redact_secrets
from .voice_service import MEDIA_ROOT, build_episode_voice, get_voice_status


class AutoRunRequest(BaseModel):
    max_turns: int = Field(default=25, ge=1, le=100)


class AutoRunToEndRequest(BaseModel):
    max_rounds: int = Field(default=8, ge=1, le=20)
    max_turns: int = Field(default=250, ge=1, le=2000)
    max_live_calls: int = Field(default=200, ge=0, le=1000)
    max_estimated_cost_cents: float = Field(default=200.0, ge=0, le=10000)


class ResetRequest(BaseModel):
    roster_preset: str | None = None


class LLMSettingsRequest(BaseModel):
    provider: str = Field(pattern="^(openrouter|ollama|zai)$")


class ArenaEntryRequest(BaseModel):
    wallet_address: str
    character_name: str
    model_id: str
    soul_md: str
    avatar_seed: str | None = None
    payout_address: str | None = None


class ArenaStartVoteRequest(BaseModel):
    wallet_address: str


class ArenaResolveRequest(BaseModel):
    winner_entry_id: str | None = None


class ViewerStateRequest(BaseModel):
    replay_index: int | None = Field(default=None, ge=0)
    is_playing: bool | None = None
    round_number: int | None = Field(default=None, ge=1)
    phase: str | None = None


APP_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIST = Path(os.environ.get("FRONTEND_DIST", APP_ROOT / "frontend" / "out"))

app = FastAPI(title="LLM Survivor Turn API")
app.mount("/media/voice", StaticFiles(directory=str(MEDIA_ROOT), check_dir=False), name="voice-media")
app.mount("/_next", StaticFiles(directory=str(FRONTEND_DIST / "_next"), check_dir=False), name="next-static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    ensure_database()


@app.get("/health")
def health() -> dict[str, str]:
    ensure_database()
    return {"status": "ok"}


@app.get("/api/llm/settings")
def api_llm_settings() -> dict:
    return get_llm_settings().__dict__


@app.post("/api/llm/settings")
def api_llm_settings_update(request: LLMSettingsRequest, _: None = Depends(require_operator)) -> dict:
    os.environ["LLM_PROVIDER"] = request.provider
    if request.provider == PROVIDER_OLLAMA:
        seed_demo(reset=True, roster_preset="local_ollama")
    if request.provider == PROVIDER_ZAI:
        seed_demo(reset=True, roster_preset="zai_glm")
    return get_llm_settings().__dict__


@app.get("/api/state")
def api_state(view: str | None = None) -> dict:
    payload = get_state()
    return public_payload(payload) if view == "public" else payload


@app.get("/api/game/summary")
def api_game_summary() -> dict:
    return get_game_summary()


@app.get("/api/model-rosters")
def api_model_rosters() -> dict:
    return {"rosters": list_model_rosters()}


@app.get("/api/viewer-state")
def api_viewer_state() -> dict:
    return {"viewer_state": get_viewer_state()}


@app.post("/api/viewer-state")
def api_viewer_state_update(request: ViewerStateRequest, _: None = Depends(require_operator)) -> dict:
    try:
        return {
            "viewer_state": update_viewer_state(
                replay_index=request.replay_index,
                is_playing=request.is_playing,
                round_number=request.round_number,
                phase=request.phase,
            )
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/episode/current")
def api_episode_current(
    round_number: int | None = Query(default=None, alias="round"),
    phase: str | None = None,
    include_audio: bool = False,
    view: str | None = None,
) -> dict:
    payload = get_episode(round_number, phase, include_audio=include_audio)
    return public_payload(payload) if view == "public" else payload


@app.get("/api/story-events")
def api_story_events(
    round_number: int | None = Query(default=None, alias="round"),
    from_sequence: int = 0,
    phase: str | None = None,
    view: str | None = None,
) -> dict:
    payload = {"events": list_story_events(round_number, from_sequence, phase)}
    return public_payload(payload) if view == "public" else payload


@app.get("/api/rounds/preload-next")
def api_next_round_preload_status() -> dict:
    return {"preload": get_next_round_preload_status()}


@app.post("/api/rounds/preload-next")
def api_next_round_preload(background_tasks: BackgroundTasks, _: None = Depends(require_operator)) -> dict:
    try:
        preload = start_next_round_preload(run_inline=False)
        if preload["status"] == "pending":
            background_tasks.add_task(run_next_round_preload, preload["id"])
        return {"preload": preload}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=redact_llm_secrets(exc)) from exc


@app.post("/api/rounds/next")
def api_start_next_round(_: None = Depends(require_operator)) -> dict:
    try:
        return start_next_round()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/turns/advance")
def api_advance_turn(_: None = Depends(require_operator)) -> dict:
    try:
        return advance_turn()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/turns/auto-run")
def api_auto_run(request: AutoRunRequest, _: None = Depends(require_operator)) -> dict:
    try:
        return auto_run(request.max_turns)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/game/auto-run-to-end")
def api_auto_run_to_end(request: AutoRunToEndRequest, _: None = Depends(require_operator)) -> dict:
    try:
        return auto_run_to_end(
            max_rounds=request.max_rounds,
            max_turns=request.max_turns,
            max_live_calls=request.max_live_calls,
            max_estimated_cost_cents=request.max_estimated_cost_cents,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=redact_llm_secrets(exc)) from exc


@app.post("/api/voice/build-episode")
def api_voice_build_episode(
    round_number: int = Query(default=7, alias="round"),
    phase: str = "tribal",
    _: None = Depends(require_operator),
) -> dict:
    try:
        return build_episode_voice(round_number, phase)
    except VoiceConfigurationError as exc:
        raise HTTPException(status_code=400, detail=redact_secrets(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=redact_secrets(exc)) from exc


@app.get("/api/voice/status")
def api_voice_status(
    round_number: int = Query(default=7, alias="round"),
    phase: str = "tribal",
) -> dict:
    try:
        return get_voice_status(round_number, phase)
    except VoiceConfigurationError as exc:
        raise HTTPException(status_code=400, detail=redact_secrets(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=redact_secrets(exc)) from exc


@app.post("/api/dev/reset")
def api_reset(request: ResetRequest | None = None, _: None = Depends(require_operator)) -> dict:
    roster_preset = request.roster_preset if request else None
    settings = get_llm_settings()
    if settings.provider == PROVIDER_OLLAMA and not roster_preset:
        roster_preset = "local_ollama"
    if settings.provider == PROVIDER_ZAI and not roster_preset:
        roster_preset = "zai_glm"
    try:
        seed_demo(reset=True, roster_preset=roster_preset)
    except FrontierRosterError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return get_state()


@app.get("/api/arena/rooms")
def api_arena_rooms() -> dict:
    return {"rooms": list_rooms()}


@app.get("/api/arena/rooms/{room_id}")
def api_arena_room(room_id: str) -> dict:
    try:
        return get_room(room_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/arena/rooms/{room_id}/entry")
def api_arena_entry(room_id: str, request: ArenaEntryRequest, _: None = Depends(require_operator)) -> dict:
    try:
        return lock_human_entry(
            room_id=room_id,
            wallet_address=request.wallet_address,
            character_name=request.character_name,
            model_id=request.model_id,
            soul_md=request.soul_md,
            avatar_seed=request.avatar_seed,
            payout_address=request.payout_address,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/arena/rooms/{room_id}/start-vote")
def api_arena_start_vote(room_id: str, request: ArenaStartVoteRequest, _: None = Depends(require_operator)) -> dict:
    try:
        return vote_to_start(room_id, request.wallet_address)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/arena/rooms/{room_id}/start")
def api_arena_start(room_id: str, _: None = Depends(require_operator)) -> dict:
    try:
        return start_room(room_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/arena/seasons/{season_id}/manifest")
def api_arena_manifest(season_id: str) -> dict:
    try:
        return get_season_manifest(season_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/arena/seasons/{season_id}/broadcast-events")
def api_arena_broadcast_events(season_id: str, from_sequence: int = 0) -> dict:
    return {"events": list_broadcast_events(season_id, from_sequence)}


@app.post("/api/arena/seasons/{season_id}/resolve")
def api_arena_resolve(season_id: str, request: ArenaResolveRequest, _: None = Depends(require_operator)) -> dict:
    try:
        return resolve_season(season_id, request.winner_entry_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/arena/dev/reset")
def api_arena_reset(_: None = Depends(require_operator)) -> dict:
    seed_arena_demo(reset=True)
    return {"rooms": list_rooms()}


@app.get("/api/operator/frontier-roster")
def api_frontier_roster_current(_: None = Depends(require_operator)) -> dict:
    snapshot = load_frontier_snapshot()
    if snapshot is None:
        return {"snapshot": None, "stale": True, "fresh": False}
    catalog = fetch_openrouter_models(os.environ.get("OPENROUTER_API_KEY"))
    findings = verify_roster_against_catalog(snapshot, catalog)
    return {
        "snapshot": snapshot,
        "stale": bool(findings) or not snapshot_is_fresh(snapshot),
        "findings": findings,
        "fresh": snapshot_is_fresh(snapshot) and not findings,
    }


@app.post("/api/operator/frontier-roster/preview")
def api_frontier_roster_preview(_: None = Depends(require_operator)) -> dict:
    try:
        catalog = fetch_openrouter_models(os.environ.get("OPENROUTER_API_KEY"))
        return select_frontier_roster(catalog)
    except FrontierRosterError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/operator/frontier-roster/lock")
def api_frontier_roster_lock(_: None = Depends(require_operator)) -> dict:
    try:
        catalog = fetch_openrouter_models(os.environ.get("OPENROUTER_API_KEY"))
        snapshot = select_frontier_roster(catalog)
        findings = verify_roster_against_catalog(snapshot, catalog)
        if findings:
            raise FrontierRosterError("freshness check failed before lock")
        save_frontier_snapshot(snapshot)
        seed_demo(reset=True, roster_preset="frontier_live")
        return {"snapshot": snapshot, "state": get_state()}
    except FrontierRosterError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _resolve_frontend_file(frontend_path: str) -> Path | None:
    if frontend_path.startswith(("api/", "media/")):
        return None

    if frontend_path in {"", "/"}:
        candidate = FRONTEND_DIST / "index.html"
    else:
        safe_path = Path(frontend_path)
        if safe_path.is_absolute() or ".." in safe_path.parts:
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = FRONTEND_DIST / safe_path
        if not candidate.suffix:
            html_candidate = candidate.with_suffix(".html")
            candidate = html_candidate if html_candidate.exists() else candidate / "index.html"
        elif candidate.is_dir():
            candidate = candidate / "index.html"

    if candidate.exists() and candidate.is_file():
        return candidate

    fallback = FRONTEND_DIST / "index.html"
    if fallback.exists():
        return fallback
    return None


@app.get("/{frontend_path:path}", include_in_schema=False)
def serve_frontend(frontend_path: str = ""):
    candidate = _resolve_frontend_file(frontend_path)
    if candidate:
        return FileResponse(candidate)
    raise HTTPException(status_code=404, detail="Frontend build not found")


@app.head("/{frontend_path:path}", include_in_schema=False)
def head_frontend(frontend_path: str = ""):
    candidate = _resolve_frontend_file(frontend_path)
    if candidate:
        return FileResponse(candidate)
    raise HTTPException(status_code=404, detail="Frontend build not found")
