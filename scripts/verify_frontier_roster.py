#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.frontier_roster import (  # noqa: E402
    FrontierRosterError,
    fetch_openrouter_models,
    load_frontier_snapshot,
    snapshot_is_fresh,
    verify_roster_against_catalog,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail if the locked frontier roster is stale.")
    parser.add_argument("--allow-stale", action="store_true", help="Local only")
    parser.add_argument("--max-age-hours", type=float, default=24.0)
    args = parser.parse_args()

    snapshot = load_frontier_snapshot()
    if snapshot is None:
        print(json.dumps({"ok": False, "error": "no locked frontier roster"}))
        return 1
    try:
        catalog = fetch_openrouter_models(os.environ.get("OPENROUTER_API_KEY"))
        findings = verify_roster_against_catalog(snapshot, catalog)
    except FrontierRosterError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1

    fresh = snapshot_is_fresh(snapshot, max_age_hours=args.max_age_hours)
    receipt = {
        "ok": not findings and (fresh or args.allow_stale),
        "fresh": fresh,
        "findings": findings,
        "locked_at": snapshot.get("locked_at"),
        "seats": [
            {"author": seat["author"], "model_id": seat["model_id"], "display_name": seat["display_name"]}
            for seat in snapshot.get("seats") or []
        ],
    }
    print(json.dumps(receipt, indent=2, ensure_ascii=False))
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
