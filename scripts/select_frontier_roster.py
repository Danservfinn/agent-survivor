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
    preflight_models,
    save_frontier_snapshot,
    select_frontier_roster,
    verify_roster_against_catalog,
)


def load_env_file(path: str | None) -> None:
    if not path:
        return
    env_path = Path(path).expanduser()
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def main() -> int:
    parser = argparse.ArgumentParser(description="Select and lock the live OpenRouter frontier roster.")
    parser.add_argument("--lock", action="store_true", help="Write artifacts/rosters/frontier_live.json")
    parser.add_argument("--allow-fill", action="store_true", help="Local smoke only: fill missing labs from leftovers")
    parser.add_argument("--skip-preflight", action="store_true")
    parser.add_argument("--env-file", default=os.environ.get("OPENROUTER_ENV_FILE"))
    args = parser.parse_args()

    load_env_file(args.env_file)
    api_key = os.environ.get("OPENROUTER_API_KEY")
    try:
        catalog = fetch_openrouter_models(api_key)
        snapshot = select_frontier_roster(catalog, allow_fill=args.allow_fill)
        findings = verify_roster_against_catalog(snapshot, catalog)
        if findings:
            raise FrontierRosterError("freshness check failed: " + json.dumps(findings))
        if args.lock and not args.skip_preflight:
            if not api_key:
                raise FrontierRosterError("OPENROUTER_API_KEY is required to preflight a lock")
            from backend.frontier_roster import chat_preflight

            preflight_models(snapshot["seats"], call_fn=lambda model_id: chat_preflight(model_id, api_key))
        if args.lock:
            path = save_frontier_snapshot(snapshot)
            snapshot["path"] = str(path)
        print(json.dumps(snapshot, indent=2, ensure_ascii=False))
        return 0
    except FrontierRosterError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
