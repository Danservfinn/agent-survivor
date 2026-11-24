from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from fastapi import BackgroundTasks, Body, Depends, FastAPI, HTTPException, Query
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
from .demo_harness import latest_report, run_demo
from .live_season import current_status as live_season_status
from .live_season import latest_report as live_latest_report
from .live_season import run_live_season
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


class LiveSeasonRequest(BaseModel):
    roster: str = Field(default="frontier_live_8", pattern="^(frontier_live_8|cheap_recent_openrouter)$")
    season_seed: int | None = Field(default=None, ge=0, le=10_000_000)
    max_cost_cents: float | None = Field(default=None, ge=0, le=2500)
    max_live_calls: int | None = Field(default=None, ge=0, le=2500)


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
# season directory names are "<timestamp>-<roster>"; reject anything that could leave SEASONS_DIR
_SEASON_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")
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


@app.post("/api/demo/run")
def api_demo_run() -> dict:
    """Public one-click benchmark. Full deterministic season, no auth, no spend."""
    return run_demo()


@app.get("/api/demo/report")
def api_demo_report() -> dict:
    report = latest_report()
    if report is None:
        raise HTTPException(status_code=404, detail="No benchmark run yet")
    return report


@app.post("/api/live-season/run")
def api_live_season_run(request: LiveSeasonRequest, _: None = Depends(require_operator)) -> dict:
    """Operator-only. Runs a full live OpenRouter season under hard caps."""
    return run_live_season(
        roster=request.roster,
        season_seed=request.season_seed,
        max_cost_cents=request.max_cost_cents,
        max_live_calls=request.max_live_calls,
    )


@app.get("/api/live-season/status")
def api_live_season_status() -> dict:
    """Status works across processes: prefer the runner flag file, fall back to
    the in-process lock. A done-flag older than the latest season dir means the
    runner finished; show its report."""
    import json as _json
    from pathlib import Path as _Path
    from .live_season import SEASONS_DIR as _SEASONS

    flag = _Path(_SEASONS) / "runner_status.json"
    try:
        data = _json.loads(flag.read_text())
    except Exception:
        data = None
    if data and data.get("status") == "running":
        # stale guard: the runner process itself must be alive. seasons can
        # legitimately run long, so never expire on time alone.
        import subprocess as _sp

        alive = False
        try:
            _r = _sp.run(["pgrep", "-f", "run_public_season.py"], capture_output=True, timeout=5)
            alive = _r.returncode == 0
        except Exception:
            alive = True  # can't tell; trust the flag
        if alive:
            return {
                "status": "running",
                "started_at": data.get("started_at"),
                "report": None,
            }
    inproc = live_season_status()
    if inproc.get("status") == "running":
        return inproc
    if data and data.get("status") == "done":
        return {"status": "done", "started_at": None, "report": data.get("report")}
    return inproc


@app.post("/api/live-season/public-run")
def api_live_season_public_run(payload: dict | None = Body(default=None)) -> dict:
    """Public live OpenRouter season under hard budget gates.

    Anyone can click; the server enforces a $1/run cap and a $3/day global
    ledger, and refuses when either is exhausted. Runs the cheap roster.

    Body (optional): {"openrouter_key": "sk-or-..."} — run the season on the
    visitor's own OpenRouter key instead of the operator's. The key is used
    only for this run's subprocess environment, never logged or persisted.
    """
    from .public_gate import check_public_run_allowed, public_run_caps, record_public_run
    from .live_season import run_live_season, current_status

    byok_raw = (payload or {}).get("openrouter_key")
    byok: str | None = None
    if byok_raw:
        byok = str(byok_raw).strip()
        if not (byok.startswith("sk-or-") and 40 <= len(byok) <= 200
                and all(c.isalnum() or c in "-_" for c in byok)):
            raise HTTPException(status_code=400, detail="that doesn't look like an OpenRouter key (expected sk-or-...)")
        import urllib.error
        import urllib.request
        try:
            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/credits",
                headers={"Authorization": f"Bearer {byok}"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            raise HTTPException(status_code=400, detail=f"OpenRouter rejected that key (HTTP {exc.code})")
        except Exception:
            raise HTTPException(status_code=400, detail="could not verify that key with OpenRouter, try again")

    allowed, reason, status = check_public_run_allowed()
    if not allowed:
        raise HTTPException(status_code=429, detail=reason)
    if current_status().get("status") == "running":
        raise HTTPException(status_code=409, detail="a season is already running, check status")

    caps = public_run_caps()

    # run the season as a DETACHED subprocess so API restarts can't kill it.
    # the runner writes report.json + status into the season dir when done.
    import subprocess
    import sys
    from pathlib import Path

    runner = Path(__file__).resolve().parent.parent / "scripts" / "run_public_season.py"
    seasons_dir = Path(__file__).resolve().parent.parent / "artifacts" / "seasons"
    seasons_dir.mkdir(parents=True, exist_ok=True)
    log_path = seasons_dir / "runner.log"
    log_file = open(log_path, "ab")
    child_env = dict(os.environ)
    if byok:
        child_env["OPENROUTER_API_KEY"] = byok
        child_env["SURVIVOR_BYOK"] = "1"
    subprocess.Popen(
        [sys.executable, str(runner)],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=child_env,
    )
    log_file.close()
    msg = "season launched, poll /api/live-season/status"
    if byok:
        msg = "season launched on your OpenRouter key, poll /api/live-season/status"
    return {"ok": True, "started": True, "message": msg, "byok": bool(byok)}


@app.get("/api/live-season/feed")
def api_live_season_feed(after: int = 0, season: str | None = None) -> dict:
    """Public: live event feed for the running (or just-finished) season.

    Reads the newest season db read-only. `after` is the last event id the
    client has seen; only newer events are returned. Visibility filter:
    audience-only, so no private strategy leaks.
    """
    import sqlite3
    from pathlib import Path
    from .live_season import latest_season_dir, SEASONS_DIR

    if season is not None:
        cand = Path(SEASONS_DIR) / season
        season_dir = cand if _SEASON_ID.fullmatch(season) and (cand / "season.db").exists() else None
    else:
        season_dir = latest_season_dir()
    if season_dir is None or not (season_dir / "season.db").exists():
        return {"ok": True, "season_id": None, "running": False, "events": []}
    db = season_dir / "season.db"
    events = []
    state = {}
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        players = []
        rows = conn.execute(
            """
            SELECT id, round, kind, title, dialogue, subtitle, visibility, created_at
            FROM StoryEvents
            WHERE id > ? AND visibility = 'audience'
            ORDER BY id DESC
            LIMIT 200
            """,
            (after,),
        ).fetchall()
        for r in reversed(rows):
            events.append(
                {
                    "id": r["id"],
                    "round": r["round"],
                    "kind": r["kind"],
                    "title": r["title"],
                    "dialogue": (r["dialogue"] or "")[:500],
                    "subtitle": r["subtitle"],
                    "created_at": r["created_at"],
                }
            )
        gs = conn.execute(
            "SELECT current_round, phase_step, turn_index FROM GameState WHERE season_id = 1"
        ).fetchone()
        if gs:
            state = {
                "current_round": gs["current_round"],
                "phase_step": gs["phase_step"],
                "turn_index": gs["turn_index"],
            }
        active = conn.execute(
            "SELECT agent_id, pseudonym, model_id, team_id FROM Agents WHERE status = 'active' ORDER BY agent_id"
        ).fetchall()
        for r in active:
            players.append(
                {
                    "agent_id": r["agent_id"],
                    "name": r["pseudonym"],
                    "model": r["model_id"],
                    "tribe": r["team_id"] if r["team_id"] and str(r["team_id"]).startswith("tribe-") else None,
                }
            )
        conn.close()
    except Exception:
        pass
    from .live_season import current_status
    from .live_season import SEASONS_DIR as _SD
    from pathlib import Path as _P
    import json as _json

    running = current_status().get("status") == "running"
    if not running:
        try:
            _flag = _json.loads((_P(_SD) / "runner_status.json").read_text())
            if _flag.get("status") == "running":
                import subprocess as _sp

                _r = _sp.run(["pgrep", "-f", "run_public_season.py"], capture_output=True, timeout=5)
                running = _r.returncode == 0
        except Exception:
            pass
    return {
        "ok": True,
        "season_id": season_dir.name,
        "running": running,
        "state": state,
        "players": players,
        "events": events,
    }


@app.get("/api/live-season/history")
def api_live_season_history() -> dict:
    """Public: every recorded live season, freshest first, rescored to current index."""
    from pathlib import Path
    from .live_season import SEASONS_DIR
    from .benchmark_scoring import score_season

    out = []
    for season_dir in sorted(Path(SEASONS_DIR).iterdir(), reverse=True):
        db = season_dir / "season.db"
        if not db.exists():
            continue
        try:
            events = 0
            import sqlite3
            with sqlite3.connect(str(db)) as conn:
                events = conn.execute("SELECT COUNT(*) FROM StoryEvents").fetchone()[0]
            if events == 0:
                continue  # failed smoke attempt, not a real season
            card = score_season(db)
            report = {}
            report_path = season_dir / "scorecard.json"
            if report_path.exists():
                try:
                    report = json.loads(report_path.read_text())
                except Exception:
                    report = {}
            out.append(
                {
                    "season_id": season_dir.name,
                    "recorded_at": report.get("generated_at"),
                    "roster": report.get("roster"),
                    "season_seed": report.get("season_seed"),
                    "elapsed_s": report.get("elapsed_s"),
                    "cost_cents": (report.get("caps") or {}).get("estimated_cost_cents"),
                    "live_calls": (report.get("caps") or {}).get("estimated_live_calls"),
                    "cap_reached": (report.get("caps") or {}).get("cap_reached"),
                    "index_version": card.get("index_version"),
                    "game": card.get("game"),
                    "rows": card.get("rows"),
                }
            )
        except Exception as exc:
            out.append({"season_id": season_dir.name, "error": str(exc)})
    return {"ok": True, "seasons": out}


@app.get("/api/live-season/narrative/{season_id}")
def api_live_season_narrative(season_id: str) -> dict:
    """Public: per-round written narrative for one season, from recorded events."""
    from .season_narrative import season_narrative

    if not _SEASON_ID.fullmatch(season_id):
        return {"ok": False, "error": "season not found"}
    try:
        return season_narrative(season_id)
    except FileNotFoundError:
        return {"ok": False, "error": "season not found"}


@app.get("/api/live-season/budget")
def api_live_season_budget() -> dict:
    from .public_gate import public_budget_status

    return public_budget_status()


@app.get("/api/live-season/scorecard")
def api_live_season_scorecard() -> dict:
    """Public read-only scorecard of the latest live season."""
    report = live_latest_report()
    if not report.get("ok"):
        raise HTTPException(status_code=404, detail=report.get("error") or "no live season recorded")
    # "db" is the season database's absolute path on this host; keep it off the public API
    return {key: value for key, value in report.items() if key != "db"}


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
    return None


def _frontend_response(frontend_path: str) -> FileResponse:
    candidate = _resolve_frontend_file(frontend_path)
    if candidate:
        return FileResponse(candidate)
    # static export: there is no client-side router to hand unknown paths to
    not_found = FRONTEND_DIST / "404.html"
    if not frontend_path.startswith(("api/", "media/")) and not_found.is_file():
        return FileResponse(not_found, status_code=404)
    raise HTTPException(status_code=404, detail="Not Found")


@app.get("/{frontend_path:path}", include_in_schema=False)
def serve_frontend(frontend_path: str = ""):
    return _frontend_response(frontend_path)


@app.head("/{frontend_path:path}", include_in_schema=False)
def head_frontend(frontend_path: str = ""):
    return _frontend_response(frontend_path)
