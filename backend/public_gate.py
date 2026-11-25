"""Public live-run budget gate.

The site's public button may run a live OpenRouter season on the operator's
key. To keep that safe this module enforces:

- a per-run cost cap (max_cost_cents), enforced by the runner from its own
  cost estimate
- a daily USD ledger under artifacts/, for display only: there is no daily cap

The API allows one season at a time (see api.api_live_season_public_run).
"""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Any


# $25 per public run (raised from $5 to $10 to $15 on 2026-11-24 with one model per
# lab). The cap is checked against the runner's own estimate, which is scaled
# to the real OpenRouter bill (live_cost.BILL_FACTOR): the first full season,
# 2026-11-24, billed about $10.5 and the newer prompt is a little dearer, so the
# cap leaves room for a season to finish. Raised to $25 on 2026-11-25 when
# every call moved to medium reasoning (estimate about $11-12 a season).
PUBLIC_RUN_MAX_COST_CENTS = 2500  # $25 of real spend (estimates are scaled to the bill by live_cost.BILL_FACTOR)
PUBLIC_ROSTER = "lab_mix_openrouter"

_LEDGER_PATH = Path(__file__).resolve().parent.parent / "artifacts" / "public_live_ledger.json"


def _utc_day() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _load_ledger() -> dict[str, Any]:
    if not _LEDGER_PATH.exists():
        return {"day": None, "spent_cents": 0.0, "runs": []}
    try:
        return json.loads(_LEDGER_PATH.read_text())
    except Exception:
        return {"day": None, "spent_cents": 0.0, "runs": []}


def _save_ledger(ledger: dict[str, Any]) -> None:
    _LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    _LEDGER_PATH.write_text(json.dumps(ledger, indent=2))


def public_budget_status() -> dict[str, Any]:
    ledger = _load_ledger()
    day = _utc_day()
    if ledger.get("day") != day:
        ledger = {"day": day, "spent_cents": 0.0, "runs": []}
    return {
        "day": day,
        "spent_cents": round(float(ledger.get("spent_cents", 0.0)), 2),
        "per_run_cap_cents": PUBLIC_RUN_MAX_COST_CENTS,
        "roster": PUBLIC_ROSTER,
    }


def record_public_run(report: dict[str, Any]) -> None:
    caps = (report or {}).get("caps") or {}
    spent = caps.get("estimated_cost_cents") or 0.0
    ledger = _load_ledger()
    day = _utc_day()
    if ledger.get("day") != day:
        ledger = {"day": day, "spent_cents": 0.0, "runs": []}
    ledger["spent_cents"] = round(float(ledger.get("spent_cents", 0.0)) + float(spent), 2)
    ledger["runs"].append(
        {
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "roster": report.get("roster"),
            "cost_cents": round(float(spent), 2),
            "finished": report.get("finished"),
        }
    )
    ledger["runs"] = ledger["runs"][-50:]
    _save_ledger(ledger)


def public_run_caps() -> dict[str, Any]:
    """Server-side caps for a public run."""
    return {
        "roster": PUBLIC_ROSTER,
        # random, not the start time: a seed someone can predict lets them work out
        # the season's puzzles and seating before it starts
        "season_seed": secrets.randbelow(99999) + 1,
        "max_cost_cents": float(PUBLIC_RUN_MAX_COST_CENTS),
    }
