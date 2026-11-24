"""Operator-only live benchmark season runner.

Runs one complete season with real OpenRouter calls: every conversation,
confessional, vote, jury decision, and challenge attempt is a live model
call, graded by the deterministic challenge bank. Hard caps:

  MAX_LIVE_CALLS       (default 2500)
  MAX_COST_CENTS       (default 2500 = $25)

Seasons run on a dedicated per-season database under artifacts/seasons/ and
write a scorecard via benchmark_scoring. The public demo button cannot reach
this module; the API route requires the operator token.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from . import database
from .benchmark_scoring import score_season
from .database import seed_demo
from .demo_harness import _run_lock as _shared_run_lock
from .llm_config import PROVIDER_OPENROUTER, live_llm_provider, should_use_live_llm
from .turn_controller import auto_run_to_end

SEASONS_DIR = Path(__file__).resolve().parent.parent / "artifacts" / "seasons"

MAX_ROUNDS = 20
MAX_TURNS = 2500
MAX_LIVE_CALLS = 2500
MAX_COST_CENTS = float(os.environ.get("SURVIVOR_MAX_COST_CENTS", "2500"))

ALLOWED_ROSTERS = ("frontier_live_8", "cheap_recent_openrouter")

# shared with demo_harness: both swap database.DATABASE_PATH process-wide, so
# only one season (demo or live) may run at a time
_run_lock = _shared_run_lock
_current: dict[str, Any] = {"status": "idle", "started_at": None, "report": None}


def current_status() -> dict[str, Any]:
    return dict(_current)


def latest_season_dir() -> Path | None:
    if not SEASONS_DIR.exists():
        return None
    dirs = sorted(p for p in SEASONS_DIR.iterdir() if p.is_dir())
    return dirs[-1] if dirs else None


def latest_report() -> dict[str, Any]:
    latest = latest_season_dir()
    if latest is None:
        return {"ok": False, "error": "no live seasons recorded"}
    report_path = latest / "scorecard.json"
    if not report_path.exists():
        return {"ok": False, "error": "latest season has no scorecard"}
    try:
        return json.loads(report_path.read_text())
    except Exception as exc:
        return {"ok": False, "error": f"scorecard unreadable: {exc}"}


def run_live_season(
    *,
    roster: str = "frontier_live_8",
    season_seed: int | None = None,
    max_cost_cents: float | None = None,
    max_live_calls: int | None = None,
) -> dict[str, Any]:
    """Run one full live season. Operator-only; never call from public routes."""
    if roster not in ALLOWED_ROSTERS:
        return {"ok": False, "error": f"roster must be one of {ALLOWED_ROSTERS}"}

    acquired = _run_lock.acquire(blocking=False)
    if not acquired:
        return {"ok": False, "error": "a live season is already running"}

    _current.update(status="running", started_at=time.time(), report=None)
    original_path = database.DATABASE_PATH
    original_seed = os.environ.get("SURVIVOR_SEASON_SEED")
    try:
        if not should_use_live_llm() or live_llm_provider() != PROVIDER_OPENROUTER:
            return {
                "ok": False,
                "error": (
                    "Live OpenRouter provider is not active in this process "
                    f"(provider={live_llm_provider()}). Set LLM_PROVIDER=openrouter and restart."
                ),
            }

        season_id = time.strftime("%Y%m%d-%H%M%S")
        season_dir = SEASONS_DIR / f"{season_id}-{roster}"
        season_dir.mkdir(parents=True, exist_ok=True)
        db_path = season_dir / "season.db"

        if season_seed is not None:
            os.environ["SURVIVOR_SEASON_SEED"] = str(season_seed)

        database.DATABASE_PATH = db_path
        seed_demo(reset=True, path=db_path, roster_preset=roster)

        started = time.monotonic()
        result = auto_run_to_end(
            max_rounds=MAX_ROUNDS,
            max_turns=MAX_TURNS,
            max_live_calls=max_live_calls or MAX_LIVE_CALLS,
            max_estimated_cost_cents=max_cost_cents if max_cost_cents is not None else MAX_COST_CENTS,
        )
        elapsed = time.monotonic() - started

        scorecard = score_season(db_path)
        scorecard.update(
            {
                "ok": True,
                "mode": "live openrouter",
                "roster": roster,
                "season_id": season_id,
                "season_seed": season_seed if season_seed is not None else os.environ.get("SURVIVOR_SEASON_SEED"),
                "db": str(db_path),
                "caps": result.get("caps") or {},
                "elapsed_s": round(elapsed, 1),
                "finished": bool((result.get("state") or {}).get("game", {}).get("winner"))
                and (result.get("caps") or {}).get("cap_reached") is None,
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + " UTC",
            }
        )
        (season_dir / "scorecard.json").write_text(json.dumps(scorecard, indent=2))
        _current.update(status="done", report=scorecard)
        return scorecard
    except Exception as exc:
        _current.update(status="error", error=str(exc))
        raise
    finally:
        if season_seed is not None:
            if original_seed is None:
                os.environ.pop("SURVIVOR_SEASON_SEED", None)
            else:
                os.environ["SURVIVOR_SEASON_SEED"] = original_seed
        database.DATABASE_PATH = original_path
        if _current["status"] == "running":
            _current.update(status="idle")
        _run_lock.release()
