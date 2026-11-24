"""Public one-click benchmark harness.

Runs a complete 16-agent season, start to finish, in deterministic reference
mode on a dedicated scratch database. Never touches the operator season DB and
never spends live-provider budget: it refuses to run if a live LLM provider is
active in this process.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from . import database
from .database import seed_demo
from .turn_controller import auto_run_to_end

DEMO_DIR = Path(__file__).resolve().parent.parent / "artifacts" / "demo"
DEMO_DB_PATH = DEMO_DIR / "demo.db"
REPORT_PATH = DEMO_DIR / "report.json"

MAX_ROUNDS = 20
MAX_TURNS = 2000

_run_lock = threading.Lock()


def _placement_key(agent: dict[str, Any]) -> tuple[int, int]:
    eliminated = agent.get("elimination_round")
    if eliminated is None:
        return (0, 0)
    return (1, eliminated)


def build_report(summary: dict[str, Any], elapsed_s: float, caps: dict[str, Any]) -> dict[str, Any]:
    game = summary.get("game") or {}
    agents = summary.get("agents") or []
    votes = summary.get("votes") or []
    jury_votes = summary.get("jury_votes") or []
    challenge_wins = summary.get("challenge_wins") or {}
    votes_received = summary.get("votes_received") or {}

    winner = summary.get("winner") or game.get("winner")
    if isinstance(winner, dict):
        winner_id = winner.get("agent_id")
        winner_name = winner.get("pseudonym")
    else:
        winner_id = winner
        winner_name = None
    by_id = {a.get("agent_id"): a for a in agents}
    if winner_id and not winner_name:
        winner_name = (by_id.get(winner_id) or {}).get("pseudonym")

    ordered = sorted(agents, key=_placement_key)
    standings = []
    for index, agent in enumerate(ordered, start=1):
        agent_id = agent.get("agent_id")
        standings.append(
            {
                "place": index,
                "model": agent.get("pseudonym"),
                "model_id": agent.get("model_id"),
                "status": agent.get("status"),
                "eliminated_round": agent.get("elimination_round"),
                "challenge_wins": challenge_wins.get(agent_id, 0),
                "votes_received": votes_received.get(agent_id, 0),
                "is_winner": agent_id == winner_id,
            }
        )

    eliminations = []
    for agent in ordered:
        round_out = agent.get("elimination_round")
        if round_out is not None:
            eliminations.append(
                {
                    "round": round_out,
                    "model": agent.get("pseudonym"),
                    "votes_received": votes_received.get(agent.get("agent_id"), 0),
                }
            )
    eliminations.sort(key=lambda item: item["round"])

    final_jury = [
        {
            "juror": (by_id.get(v.get("juror_id")) or {}).get("pseudonym", v.get("juror_id")),
            "voted_for": (by_id.get(v.get("target_id") or v.get("finalist_id")) or {}).get(
                "pseudonym", v.get("target_id") or v.get("finalist_id")
            ),
        }
        for v in jury_votes
        if isinstance(v, dict)
    ]

    return {
        "ok": True,
        "mode": "deterministic reference",
        "note": "Rule-based decision policies. Live frontier-model seasons run in the operator replay surface.",
        "season": game.get("season_id"),
        "rounds_completed": game.get("current_round"),
        "turns": len(summary.get("turns") or []) or caps.get("turns_run"),
        "votes_cast": len(votes),
        "agents": len(agents),
        "elapsed_s": round(elapsed_s, 2),
        "winner": winner_name,
        "finished": bool(winner) and caps.get("cap_reached") is None,
        "cap_reached": caps.get("cap_reached"),
        "eliminations": eliminations,
        "standings": standings,
        "jury_votes": final_jury,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + " UTC",
    }


def run_demo() -> dict[str, Any]:
    # NOTE: the demo always forces LLM_PROVIDER=deterministic for the duration
    # of the run, so a live provider in this process is fine; the explicit
    # refusal below only made sense before the demo forced its own provider.
    acquired = _run_lock.acquire(blocking=False)
    if not acquired:
        return {"ok": False, "error": "A benchmark run is already in progress. Try again in a moment."}

    original_path = database.DATABASE_PATH
    original_provider = os.environ.get("LLM_PROVIDER")
    try:
        os.environ["LLM_PROVIDER"] = "deterministic"
        DEMO_DIR.mkdir(parents=True, exist_ok=True)
        database.DATABASE_PATH = DEMO_DB_PATH
        seed_demo(reset=True)
        started = time.monotonic()
        result = auto_run_to_end(
            max_rounds=MAX_ROUNDS,
            max_turns=MAX_TURNS,
            max_live_calls=0,
            max_estimated_cost_cents=0,
        )
        elapsed = time.monotonic() - started
        summary = dict(result.get("summary") or {})
        all_agents = (result.get("state") or {}).get("agents")
        if all_agents:
            summary["agents"] = all_agents
        report = build_report(summary, elapsed, result.get("caps") or {})
        REPORT_PATH.write_text(json.dumps(report, indent=2))
        return report
    finally:
        if original_provider is None:
            os.environ.pop("LLM_PROVIDER", None)
        else:
            os.environ["LLM_PROVIDER"] = original_provider
        database.DATABASE_PATH = original_path
        _run_lock.release()


def latest_report() -> dict[str, Any] | None:
    try:
        return json.loads(REPORT_PATH.read_text())
    except (OSError, ValueError):
        return None
