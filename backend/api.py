from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing
from pathlib import Path

from fastapi import BackgroundTasks, Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.staticfiles import NotModifiedResponse
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
    roster: str = Field(default="frontier_live_8", pattern="^(frontier_live_8|cheap_recent_openrouter|lab_mix_openrouter)$")
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
PAGE_CACHE_CONTROL = "no-cache"
ROOT_FILE_CACHE_CONTROL = "public, max-age=3600"
IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"


class ImmutableStaticFiles(StaticFiles):
    """Next.js /_next files: every name carries a content hash or the build id,
    so a new build gets new URLs and old ones never change."""

    def file_response(self, full_path, stat_result, scope, status_code: int = 200) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        if response.status_code == 200:
            response.headers["Cache-Control"] = IMMUTABLE_CACHE_CONTROL
        return response


app.mount("/_next", ImmutableStaticFiles(directory=str(FRONTEND_DIST / "_next"), check_dir=False), name="next-static")

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
    # paid entries run one at a time when no season is playing (backend/entry_queue.py)
    from .entry_queue import start_dispatcher

    start_dispatcher()


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


@app.post("/api/live-season/run")
def api_live_season_run(request: LiveSeasonRequest, _: None = Depends(require_operator)) -> dict:
    """Operator-only. Runs a full live OpenRouter season under hard caps."""
    return run_live_season(
        roster=request.roster,
        season_seed=request.season_seed,
        max_cost_cents=request.max_cost_cents,
        max_live_calls=request.max_live_calls,
    )


# shared with the paid-entry dispatcher, so the two can never start seasons at once
from .season_launch import LAUNCH_LOCK as _launch_lock  # noqa: E402


def _runner_flag() -> dict | None:
    from .live_season import SEASONS_DIR

    try:
        return json.loads((Path(SEASONS_DIR) / "runner_status.json").read_text())
    except Exception:
        return None


def _detached_runner_alive() -> bool:
    """True while scripts/run_public_season.py is running. Seasons can run
    long, so never expire on time alone: the process itself must be alive."""
    flag = _runner_flag()
    if not flag or flag.get("status") != "running":
        return False
    try:
        # this checkout's runner only (its full path), so a run from another checkout never blocks it
        runner = str(APP_ROOT / "scripts" / "run_public_season.py")
        return subprocess.run(["pgrep", "-f", runner], capture_output=True, timeout=5).returncode == 0
    except Exception:
        return True  # can't tell; trust the flag


@app.get("/api/live-season/status")
def api_live_season_status() -> dict:
    """Status works across processes: the public runner is detached, so prefer
    its flag file, then fall back to the in-process operator run."""
    flag = _runner_flag()
    if _detached_runner_alive():
        return {"status": "running", "started_at": (flag or {}).get("started_at"), "report": None}
    inproc = live_season_status()
    if inproc.get("status") == "running":
        return inproc
    if flag and flag.get("status") == "done":
        return {"status": "done", "started_at": None, "report": flag.get("report")}
    return inproc


@app.post("/api/live-season/public-run")
def api_live_season_public_run(payload: dict | None = Body(default=None)) -> dict:
    """Public live OpenRouter season under hard budget gates.

    Anyone can click. The runner enforces the per-run cost cap in
    public_gate.PUBLIC_RUN_MAX_COST_CENTS; there is no daily cap. One season
    runs at a time. Runs public_gate.PUBLIC_ROSTER (lab_mix_openrouter, one model per lab).

    Body (optional): {"openrouter_key": "sk-or-..."} — run the season on the
    visitor's own OpenRouter key instead of the operator's. The key is used
    only for this run's subprocess environment, never logged or persisted.
    """
    from .live_season import current_status

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

    runner = APP_ROOT / "scripts" / "run_public_season.py"
    seasons_dir = APP_ROOT / "artifacts" / "seasons"
    seasons_dir.mkdir(parents=True, exist_ok=True)
    child_env = dict(os.environ)
    if byok:
        child_env["OPENROUTER_API_KEY"] = byok
        child_env["SURVIVOR_BYOK"] = "1"

    # One season at a time. The runner is a detached process, so the check must
    # look at the runner itself; the lock closes the gap between check and launch.
    from . import entries_store

    with _launch_lock:
        if _detached_runner_alive() or current_status().get("status") == "running":
            raise HTTPException(status_code=409, detail="a season is already running, check status")
        from .entry_queue import startable

        # only entries the dispatcher can actually start hold the button back
        if startable() or entries_store.with_status("running"):
            raise HTTPException(status_code=409, detail="paid entries are waiting to play; try again after them")
        (seasons_dir / "runner_status.json").write_text(json.dumps({"status": "running", "started_at": time.time()}))
        # detached so API restarts can't kill it; it writes its report and
        # status into the seasons dir when done
        with open(seasons_dir / "runner.log", "ab") as log_file:
            subprocess.Popen(
                [sys.executable, str(runner)],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=child_env,
            )
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
            "SELECT * FROM Agents WHERE status = 'active' ORDER BY agent_id"
        ).fetchall()
        for r in active:
            # event text uses the in-game alias; people also need the real model name
            display_name = r["display_name"] if "display_name" in r.keys() and r["display_name"] else None
            players.append(
                {
                    "agent_id": r["agent_id"],
                    "name": display_name or r["pseudonym"],
                    "alias": r["pseudonym"] if display_name else None,
                    "model": r["model_id"],
                }
            )
        conn.close()
    except Exception:
        pass
    from .live_season import current_status

    running = current_status().get("status") == "running" or _detached_runner_alive()
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
            # sqlite3's context manager commits but does not close; close explicitly
            # or every request leaks one file handle per season
            with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as conn:
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
                    "interrupted": report.get("interrupted"),
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


@app.get("/api/methodology")
def api_methodology() -> dict:
    """Public: the facts the Methodology page prints, read from the running code
    (rules text, switches, reply fields, limits, score weights, rules version)."""
    from .methodology import facts

    return facts()


@app.get("/api/methodology/prompt/{family}")
def api_methodology_prompt(family: str) -> dict:
    """Public: one real prompt and reply of a kind of step, from the reference season's log."""
    from .methodology import prompt_example

    if not re.fullmatch(r"[a-z_]{1,32}", family):
        raise HTTPException(status_code=404, detail="Not Found")
    example = prompt_example(family)
    if example is None:
        raise HTTPException(status_code=404, detail="no recorded example yet")
    return example


# ---------------------------------------------------------------- paid entries
# A buyer picks an OpenRouter model to play one season against the house roster
# (docs/plans/2026-11-25-methodology-page-and-agent-entries.md, "Entries").

_quote_hits: dict[str, list[float]] = {}
_quote_day: list[float] = []
QUOTES_PER_10_MIN = 6
QUOTES_PER_DAY = 200  # every quote that reaches a model makes one real call


def _client_ip(request: Request) -> str:
    # traffic arrives through cloudflared, which names the visitor here
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "")


def _rate_limited(ip: str) -> bool:
    """Per visitor: at most QUOTES_PER_10_MIN tries in ten minutes. Old entries are dropped as it goes."""
    now = time.time()
    if len(_quote_hits) > 5000:
        for key in [k for k, v in _quote_hits.items() if not v or now - v[-1] >= 600]:
            del _quote_hits[key]
    hits = [t for t in _quote_hits.get(ip, []) if now - t < 600]
    hits.append(now)
    _quote_hits[ip] = hits
    return len(hits) > QUOTES_PER_10_MIN


def _daily_budget_spent() -> bool:
    """One more real model call today? Counted only for requests that pass validation."""
    now = time.time()
    _quote_day[:] = [t for t in _quote_day if now - t < 86400]
    if len(_quote_day) >= QUOTES_PER_DAY:
        return True
    _quote_day.append(now)
    return False


def _site() -> str:
    return (os.environ.get("SURVIVOR_PUBLIC_URL") or "https://llmsurvivor.kurult.ai").rstrip("/")


@app.get("/api/entries/config")
def api_entries_config() -> dict:
    """Public: whether entries are open, the pricing rule, and the queue."""
    from . import entries_store, entry_payments, entry_pricing

    from .model_rosters import LAB_MIX_OPENROUTER_MODELS

    cfg = entry_payments.config()
    return {
        "players": len(LAB_MIX_OPENROUTER_MODELS),
        "open": bool(cfg) and entries_store.is_open(),
        "mode": cfg["mode"] if cfg else None,
        "floor_cents": entry_pricing.FLOOR_CENTS,
        "multiplier": entry_pricing.MULTIPLIER,
        "cap_share": entry_pricing.CAP_SHARE,
        "queued": len(entries_store.with_status("queued")),
        "running": len(entries_store.with_status("running")),
    }


@app.post("/api/entries/quote")
def api_entries_quote(request: Request, payload: dict = Body(...)) -> dict:
    """Public: the price for entering a model, after a live test call. Rate-limited."""
    from .entry_pricing import QuoteError, quote

    from .model_rosters import EntryError, validate_entry

    if _rate_limited(_client_ip(request)):
        raise HTTPException(status_code=429, detail="too many quotes from here; try again in a few minutes")
    model_id = str(payload.get("model_id") or "")
    try:
        validate_entry({"name": "Quote", "model_id": model_id})
    except EntryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if _daily_budget_spent():
        raise HTTPException(status_code=429, detail="quotes are busy today; try again tomorrow")
    try:
        return {"ok": True, **quote(model_id)}
    except QuoteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@app.post("/api/entries")
def api_entries_create(request: Request, payload: dict = Body(...)) -> dict:
    """Public: start an entry. Prices it again, then returns the Stripe Checkout address."""
    from . import entries_store, entry_payments
    from .entry_pricing import QuoteError, quote
    from .model_rosters import EntryError, validate_entry

    if not entry_payments.enabled() or not entries_store.is_open():
        raise HTTPException(status_code=503, detail="entries are closed right now")
    if _rate_limited(_client_ip(request)):
        raise HTTPException(status_code=429, detail="too many tries from here; try again in a few minutes")
    name = str(payload.get("name") or "").strip()
    model_id = str(payload.get("model_id") or "").strip()
    try:
        validate_entry({"name": name, "model_id": model_id})
    except EntryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if _daily_budget_spent():
        raise HTTPException(status_code=429, detail="entries are busy today; try again tomorrow")
    try:
        priced = quote(model_id)
    except QuoteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    entry = entries_store.create(
        name=name, model_id=model_id, price_cents=priced["price_cents"], cap_cents=priced["cap_cents"],
        estimate=priced["estimate"], ip=_client_ip(request), mode=(entry_payments.config() or {}).get("mode", "test"),
    )
    try:
        session = entry_payments.create_checkout(entry, site=_site())
    except entry_payments.PaymentError as exc:
        print(f"entries: checkout for {entry['entry_id']} failed: {exc}")
        entries_store.move(entry["entry_id"], ("awaiting_payment",), "expired", note="the payment page could not be opened")
        raise HTTPException(status_code=502, detail="the payment page could not be opened; nothing was charged") from None
    entries_store.update(entry["entry_id"], stripe_session_id=session["id"])
    return {"ok": True, "checkout_url": session["url"], "status_url": f"/entry?t={entry['token']}", "price_cents": entry["price_cents"]}


def _fulfil(session: dict) -> None:
    """A paid Checkout Session: queue its entry (safe to call twice).

    Matched by our own reference on the session (metadata, else client_reference_id),
    so a paid session still finds its entry if saving the session id failed."""
    from . import entries_store

    if session.get("payment_status") not in ("paid", "no_payment_required"):
        return
    entry_id = (session.get("metadata") or {}).get("llm_survivor_entry") or session.get("client_reference_id")
    entry = entries_store.get(str(entry_id)) if entry_id else None
    if not entry or entry.get("stripe_session_id") not in (None, session.get("id")):
        return
    entries_store.move(
        entry["entry_id"], ("awaiting_payment", "expired"), "queued",
        paid_at=time.time(), stripe_payment_intent=session.get("payment_intent"), stripe_session_id=session.get("id"),
    )


@app.get("/api/entries/status")
def api_entries_status(t: str = Query(..., min_length=20, max_length=64)) -> dict:
    """Public with the private token: where an entry stands."""
    from . import entries_store, entry_payments

    entry = entries_store.by_token(t)
    if not entry:
        raise HTTPException(status_code=404, detail="no entry with that link")
    if entry["status"] == "awaiting_payment" and entry.get("stripe_session_id"):
        # if the webhook is late, ask Stripe directly
        try:
            session = entry_payments.get_session(entry["stripe_session_id"])
            _fulfil(session)
            if session.get("status") == "expired":
                entries_store.move(entry["entry_id"], ("awaiting_payment",), "expired")
            entry = entries_store.by_token(t) or entry
        except entry_payments.PaymentError:
            pass
    out = {k: entry[k] for k in ("entry_id", "name", "model_id", "status", "price_cents", "season_id", "note", "created_at", "paid_at", "started_at", "finished_at", "refunded_at")}
    out["queue_position"] = entries_store.queue_position(entry["entry_id"])
    out["estimate"] = {k: entry["estimate"].get(k) for k in ("replaced_model_id", "replaced_because")}
    if entry["season_id"]:
        out["links"] = {"report": f"/report/{entry['season_id']}", "watch": f"/watch?season={entry['season_id']}"}
    return out


@app.post("/api/entries/stripe-webhook", include_in_schema=False)
async def api_entries_stripe_webhook(request: Request) -> dict:
    """Stripe: payment events for entries. Verified on the raw body; other products' events are ignored."""
    from . import entries_store, entry_payments

    raw = await request.body()
    try:
        event = entry_payments.verify_webhook(raw, request.headers.get("stripe-signature"))
    except entry_payments.PaymentError:
        raise HTTPException(status_code=400, detail="bad signature") from None
    obj = (event.get("data") or {}).get("object") or {}
    if not entry_payments.is_ours(obj) or entries_store.event_handled(str(event.get("id"))):
        return {"ok": True}
    kind = event.get("type")
    # handling is safe to repeat; the event is recorded only after it succeeds, so a
    # failure here returns 500 and Stripe's retry is handled, not dropped
    if kind in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        _fulfil(obj)
    elif kind == "checkout.session.expired":
        entry_id = (obj.get("metadata") or {}).get("llm_survivor_entry")
        if entry_id:
            entries_store.move(str(entry_id), ("awaiting_payment",), "expired")
    entries_store.seen_event(str(event.get("id")), str(kind))
    return {"ok": True}


@app.post("/api/entries/settings")
def api_entries_settings(payload: dict = Body(...), _: None = Depends(require_operator)) -> dict:
    """Operator: {"open": bool} takes entries, {"paused": bool} stops taking and starting them,
    {"allow_test_runs": bool} lets test-mode entries start real seasons (real model fees)."""
    from . import entries_store

    for key in ("open", "paused", "allow_test_runs"):
        if key in payload and not isinstance(payload[key], bool):
            raise HTTPException(status_code=400, detail=f"{key} must be true or false")
    for key in ("open", "paused", "allow_test_runs"):
        if key in payload:
            entries_store.set_setting(key, "1" if payload[key] else "0")
    return {"ok": True, "open": entries_store.is_open(), "paused": entries_store.paused(), "allow_test_runs": entries_store.runs_test_entries()}


@app.post("/api/entries/{entry_id}/cancel")
def api_entries_cancel(entry_id: str, _: None = Depends(require_operator)) -> dict:
    """Operator: cancel an entry that has not started. Refunds it if it was paid."""
    from . import entries_store
    from .entry_queue import refund_now

    entry = entries_store.get(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="no such entry")
    if entry["status"] == "awaiting_payment":
        entries_store.move(entry_id, ("awaiting_payment",), "expired", note="cancelled")
        return {"ok": True, "status": "expired"}
    if entry["status"] in ("queued", "refunding"):
        return {"ok": True, "status": refund_now(entry, "cancelled before its season")}
    raise HTTPException(status_code=409, detail=f"an entry that is {entry['status']} cannot be cancelled")


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
    round_number: int = Query(default=1, alias="round"),
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
    round_number: int = Query(default=1, alias="round"),
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


# ---------------------------------------------------------- season report
# The report page shows text the models wrote, so it runs under a policy that
# allows only its own inline script and style and no network requests at all.
REPORT_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def _report_season_dir(season_id: str | None) -> Path:
    from . import live_season
    from .season_report import reportable_season_dir

    seasons_dir = Path(live_season.SEASONS_DIR)
    if season_id is None:
        season_dir = reportable_season_dir(seasons_dir)
    else:
        season_dir = seasons_dir / season_id if _SEASON_ID.fullmatch(season_id) else None
    if season_dir is None or not (season_dir / "season.db").exists():
        raise HTTPException(status_code=404, detail="season not found")
    return season_dir


@app.get("/api/live-season/report")
def api_live_season_report_latest() -> dict:
    """Public: full report data for the newest finished season."""
    from .season_report import cached_season_report

    return cached_season_report(_report_season_dir(None))


@app.get("/api/live-season/report/{season_id}")
def api_live_season_report(season_id: str) -> dict:
    """Public: full report data for one season (scores, votes, challenges, jury)."""
    from .season_report import cached_season_report

    return cached_season_report(_report_season_dir(season_id))


def _report_page(season_id: str | None) -> Response:
    from .season_report import cached_season_report, render_report_html

    season_dir = _report_season_dir(season_id)
    has_cards = (season_dir / "share" / "season.png").is_file()
    has_video = (season_dir / "share" / "season.mp4").is_file()
    html = render_report_html(
        cached_season_report(season_dir),
        contact_url=os.environ.get("SURVIVOR_REPORT_CONTACT_URL"),
        site_url=os.environ.get("SURVIVOR_PUBLIC_URL"),
        handle=os.environ.get("SURVIVOR_X_HANDLE"),
        share_base=f"/report/{season_dir.name}/share/" if has_cards else None,
        video=has_video,
    )
    return Response(
        content=html,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": PAGE_CACHE_CONTROL, "Content-Security-Policy": REPORT_CSP, "X-Content-Type-Options": "nosniff"},
    )


@app.api_route("/report", methods=["GET", "HEAD"], include_in_schema=False)
def season_report_latest_page() -> Response:
    return _report_page(None)


@app.api_route("/report/{season_id}", methods=["GET", "HEAD"], include_in_schema=False)
def season_report_page(season_id: str) -> Response:
    return _report_page(season_id)


@app.api_route("/report/{season_id}/share/{name}", methods=["GET", "HEAD"], include_in_schema=False)
def season_report_share_image(season_id: str, name: str) -> Response:
    """One share file of a season: an image (season.png, leaderboard.png, moment-1.png,
    model-<id>.png) or the season clip (season.mp4)."""
    from .season_story import SHARE_NAME

    if not SHARE_NAME.fullmatch(name):
        raise HTTPException(status_code=404, detail="Not Found")
    image = _report_season_dir(season_id) / "share" / name
    if not image.is_file():
        raise HTTPException(status_code=404, detail="Not Found")
    media_type = "video/mp4" if image.suffix == ".mp4" else "image/png"
    return FileResponse(image, media_type=media_type, headers={"Cache-Control": ROOT_FILE_CACHE_CONTROL})


# a finished season's raw data, linked from the Methodology page ("check it yourself")
SEASON_DATA_FILES = {
    "report.json": ("report.json", "application/json"),
    "prompts.jsonl.gz": ("prompts.jsonl.gz", "application/gzip"),
    "season.db": ("season.db", "application/vnd.sqlite3"),
}
_clean_files: dict[tuple[str, float], bool] = {}


def _safe_to_publish(path: Path) -> bool:
    """No key-shaped strings in the file (checked once per version of the file)."""
    from .prompt_log import has_key_shapes

    key = (str(path), path.stat().st_mtime)
    if key not in _clean_files:
        _clean_files[key] = not has_key_shapes(path)
    return _clean_files[key]


@app.api_route("/report/{season_id}/data/{name}", methods=["GET", "HEAD"], include_in_schema=False)
def season_report_data(season_id: str, name: str) -> Response:
    """Public: one data file of a finished season (report.json, prompts.jsonl.gz, season.db)."""
    if name not in SEASON_DATA_FILES:
        raise HTTPException(status_code=404, detail="Not Found")
    season_dir = _report_season_dir(season_id)
    try:
        finished = bool(json.loads((season_dir / "scorecard.json").read_text()).get("finished"))
    except (FileNotFoundError, ValueError):
        finished = False
    filename, media_type = SEASON_DATA_FILES[name]
    path = season_dir / filename
    if not finished or not path.is_file() or not _safe_to_publish(path):
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(
        path,
        media_type=media_type,
        filename=f"llm-survivor-{season_dir.name}-{filename}",
        headers={"Cache-Control": ROOT_FILE_CACHE_CONTROL},
    )


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


def _page_cache_control(path: Path) -> str:
    # Page HTML and Next's RSC payloads (.txt) keep the same names across
    # builds, so browsers must revalidate them or a deploy stays invisible.
    return PAGE_CACHE_CONTROL if path.suffix in {".html", ".txt"} else ROOT_FILE_CACHE_CONTROL


def _frontend_response(frontend_path: str, request: Request) -> Response:
    candidate = _resolve_frontend_file(frontend_path)
    status_code = 200
    if candidate is None:
        # static export: there is no client-side router to hand unknown paths to
        not_found = FRONTEND_DIST / "404.html"
        if frontend_path.startswith(("api/", "media/")) or not not_found.is_file():
            raise HTTPException(status_code=404, detail="Not Found")
        candidate, status_code = not_found, 404
    response = FileResponse(
        candidate,
        status_code=status_code,
        headers={"Cache-Control": _page_cache_control(candidate)},
        # stat now so etag/last-modified exist before the not-modified check
        stat_result=os.stat(candidate),
    )
    # StaticFiles.is_not_modified reads no instance state; reuse its etag/date check
    if status_code == 200 and StaticFiles.is_not_modified(None, response.headers, request.headers):  # type: ignore[arg-type]
        return NotModifiedResponse(response.headers)
    return response


@app.get("/{frontend_path:path}", include_in_schema=False)
def serve_frontend(request: Request, frontend_path: str = ""):
    return _frontend_response(frontend_path, request)


@app.head("/{frontend_path:path}", include_in_schema=False)
def head_frontend(request: Request, frontend_path: str = ""):
    return _frontend_response(frontend_path, request)
