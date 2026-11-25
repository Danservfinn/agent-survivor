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
import secrets
import time
from pathlib import Path
from typing import Any

from . import database, prompt_log, rules_changelog
from .benchmark_scoring import score_season
from .database import seed_demo
from . import playbooks
from .model_rosters import EntryError, entry_agents, table_agents, validate_entry
from .demo_harness import _run_lock as _shared_run_lock
from .llm_config import PROVIDER_OPENROUTER, live_llm_provider, should_use_live_llm
from .turn_controller import FIRST_LEVEL_BY_ROSTER, auto_run_to_end

SEASONS_DIR = Path(__file__).resolve().parent.parent / "artifacts" / "seasons"

MAX_ROUNDS = 20
MAX_TURNS = 2500
MAX_LIVE_CALLS = 2500
MAX_COST_CENTS = float(os.environ.get("SURVIVOR_MAX_COST_CENTS", "2500"))

ALLOWED_ROSTERS = ("frontier_live_8", "cheap_recent_openrouter", "lab_mix_openrouter")

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
    """Scorecard of the newest season with a result. Aborted runs leave a season
    dir with no scorecard, and interrupted runs are skipped; neither may hide
    the last real result."""
    if not SEASONS_DIR.exists():
        return {"ok": False, "error": "no live seasons recorded"}
    for season_dir in sorted((p for p in SEASONS_DIR.iterdir() if p.is_dir()), reverse=True):
        report_path = season_dir / "scorecard.json"
        if not report_path.exists() or (season_dir / "entry.json").exists():
            continue  # no result yet, or a paid entry's season (never the site's "latest")
        try:
            report = json.loads(report_path.read_text())
        except Exception as exc:
            return {"ok": False, "error": f"scorecard unreadable: {exc}"}
        if report.get("interrupted"):
            continue  # a provider outage stopped it; it is not a result
        return report
    return {"ok": False, "error": "no live season has a scorecard"}


def _write_season_report(season_dir: Path) -> None:
    """Save report.json and report.html beside the season database. The season's
    result is already on disk, so a report failure is logged and never raised."""
    try:
        from .season_report import write_report_files

        write_report_files(
            season_dir,
            contact_url=os.environ.get("SURVIVOR_REPORT_CONTACT_URL"),
            site_url=os.environ.get("SURVIVOR_PUBLIC_URL"),
            handle=os.environ.get("SURVIVOR_X_HANDLE"),
        )
    except Exception as exc:
        print(f"season report failed for {season_dir.name}: {exc}")


def _entry_record(db_path: Path, record: dict[str, Any], season_seed: int) -> dict[str, Any]:
    """entry.json: who the entry is and which seat it took, with the agent id
    it got after seating (seats are shuffled by the seed)."""
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT agent_id, pseudonym FROM Agents WHERE model_id = ? AND display_name = ?",
            (record["model_id"], record["name"]),
        ).fetchone()
    finally:
        conn.close()
    return {**record, "agent_id": row[0] if row else None, "alias": row[1] if row else None, "season_seed": season_seed, "maker": "Community"}


def _variable_price(model_id: str) -> bool:
    from .live_cost import get_pricing_table

    try:
        return bool((get_pricing_table().get(model_id) or {}).get("variable"))
    except Exception:  # noqa: BLE001 - no catalog: the cost estimate refuses unpriced models
        return False


def _table_record(db_path: Path, seats: list[dict[str, Any]], season_seed: int) -> dict[str, Any]:
    """entries.json: every seat with its season agent id and alias. Entry seats
    carry their entry and agent references and playbook hash, never its text."""
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        rows = {
            (model_id, name): (agent_id, alias)
            for agent_id, alias, model_id, name in conn.execute("SELECT agent_id, pseudonym, model_id, display_name FROM Agents")
        }
    finally:
        conn.close()
    out = []
    for seat in seats:
        agent_id, alias = rows.get((seat["model_id"], seat["name"]), (None, None))
        keep = ("seat", "entry_id", "agent_ref", "version", "name", "model_id", "playbook_sha256") if seat["seat"] == "entry" else ("seat", "name", "model_id")
        out.append({"agent_id": agent_id, "alias": alias, **{k: seat.get(k) for k in keep}})
    return {"season_seed": season_seed, "seats": out}


def run_live_season(
    *,
    roster: str = "frontier_live_8",
    season_seed: int | None = None,
    max_cost_cents: float | None = None,
    max_live_calls: int | None = None,
    entry: dict[str, Any] | None = None,
    entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run one full live season. Operator-only; never call from public routes.

    entry: a paid entry, {"model_id", "name", "entry_id"}: the buyer's
    OpenRouter model takes one lab_mix seat (model_rosters.entry_agents).
    The season folder ends in -lab_mix_entry and holds entry.json (no secrets).

    entries: a table of 1 to 10 agents, each {"entry_id", "agent_ref",
    "version", "name", "model_id", "playbook", "playbook_sha256"}; house
    models fill the other seats (model_rosters.table_agents). Each playbook
    goes into its own seat's prompts only (backend/playbooks.py) and is never
    stored; entries.json records only its sha256.
    """
    if roster not in ALLOWED_ROSTERS:
        return {"ok": False, "error": f"roster must be one of {ALLOWED_ROSTERS}"}
    if entry is not None and entries is not None:
        return {"ok": False, "error": "pass entry or entries, not both"}
    if entry is not None or entries is not None:
        if roster != "lab_mix_openrouter":
            return {"ok": False, "error": "an entry plays the lab_mix_openrouter roster"}
        try:
            for item in [entry] if entry is not None else entries:
                validate_entry(item)
                if item.get("playbook"):
                    playbooks.check(item["playbook"], item.get("playbook_sha256"))
                if _variable_price(item["model_id"]):
                    raise EntryError(f"{item['model_id']} has a variable price")
        except (EntryError, playbooks.PlaybookError) as exc:
            return {"ok": False, "error": f"entry: {exc}"}
        if season_seed is None:
            # the seat an entry takes is chosen by the seed, so fix one now
            season_seed = secrets.randbelow(2**31 - 1) + 1

    acquired = _run_lock.acquire(blocking=False)
    if not acquired:
        return {"ok": False, "error": "a live season is already running"}

    _current.update(status="running", started_at=time.time(), report=None)
    original_path = database.DATABASE_PATH
    original_seed = os.environ.get("SURVIVOR_SEASON_SEED")
    original_level = os.environ.get("SURVIVOR_FIRST_LADDER_LEVEL")
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
        season_dir = SEASONS_DIR / f"{season_id}-{'lab_mix_entry' if entry is not None or entries is not None else roster}"
        season_dir.mkdir(parents=True, exist_ok=True)
        db_path = season_dir / "season.db"

        if season_seed is not None:
            os.environ["SURVIVOR_SEASON_SEED"] = str(season_seed)
        # a frontier roster starts the challenge ladder at its top level
        if roster in FIRST_LEVEL_BY_ROSTER and original_level is None:
            os.environ["SURVIVOR_FIRST_LADDER_LEVEL"] = str(FIRST_LEVEL_BY_ROSTER[roster])

        database.DATABASE_PATH = db_path
        entry_record = None
        table_record = None
        if entry is not None:
            agents, entry_record = entry_agents(entry, season_seed)
            seed_demo(reset=True, path=db_path, roster_preset=roster, agents=agents)
            entry_record = _entry_record(db_path, entry_record, season_seed)
            (season_dir / "entry.json").write_text(json.dumps(entry_record, indent=2))
            if entry.get("playbook") and entry_record.get("agent_id"):
                playbooks.register(entry_record["agent_id"], entry["playbook"], entry.get("playbook_sha256"))
        elif entries is not None:
            agents, seats = table_agents(entries, season_seed)
            seed_demo(reset=True, path=db_path, roster_preset=roster, agents=agents)
            table_record = _table_record(db_path, seats, season_seed)
            (season_dir / "entries.json").write_text(json.dumps(table_record, indent=2))
            by_name = {seat["name"]: seat["agent_id"] for seat in table_record["seats"]}
            for item in entries:
                if item.get("playbook"):
                    playbooks.register(by_name[validate_entry(item)["name"]], item["playbook"], item.get("playbook_sha256"))
        else:
            seed_demo(reset=True, path=db_path, roster_preset=roster)

        started = time.monotonic()
        # every model call's prompt and reply (never its private "think") goes
        # to prompts.jsonl.gz in the season folder, for the Methodology page
        with prompt_log.recording(season_dir):
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
                and (result.get("caps") or {}).get("cap_reached") is None
                and not result.get("interrupted"),
                "interrupted": result.get("interrupted"),
                # the rules this season was played under (backend/rules_changelog.py)
                "rules_version": rules_changelog.current_version(),
                "rules_fingerprint": rules_changelog.fingerprint(),
                "rules_switches": rules_changelog.switches_in_force(),
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + " UTC",
            }
        )
        scorecard["report_url"] = f"/report/{season_dir.name}"
        if entry_record is not None:
            scorecard["entry"] = entry_record
        if table_record is not None:
            scorecard["entries"] = table_record
        if scorecard["finished"]:
            # judges score each player's strategy for the report; a failure here
            # never fails the season
            try:
                from .strategy_judging import judge_season

                scorecard["strategy_ratings"] = judge_season(db_path)
            except Exception as exc:  # noqa: BLE001
                print(f"strategy judging failed: {exc}")
        (season_dir / "scorecard.json").write_text(json.dumps(scorecard, indent=2))
        _write_season_report(season_dir)
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
        playbooks.clear()  # private text never outlives its season
        if roster in FIRST_LEVEL_BY_ROSTER and original_level is None:
            os.environ.pop("SURVIVOR_FIRST_LADDER_LEVEL", None)
        if _current["status"] == "running":
            _current.update(status="idle")
        _run_lock.release()
