"""Detached public-season runner. Survives API restarts.

Writes runner_status.json into the seasons dir: {"status": "running"} at
start, {"status": "done", "report": ...} at completion. The API status
endpoint prefers this flag file so status works across processes.
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_env_file() -> None:
    env_path = Path.home() / ".kublai" / "secrets" / "llm-survivor.env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file()
os.environ.setdefault("LLM_PROVIDER", "openrouter")


def main() -> int:
    from backend.live_season import run_live_season, SEASONS_DIR
    from backend.public_gate import public_run_caps, record_public_run

    caps = public_run_caps()
    started = time.time()
    flag = SEASONS_DIR / "runner_status.json"
    try:
        flag.write_text(json.dumps({"status": "running", "started_at": started}))
    except Exception:
        pass
    report = {"ok": False, "error": "runner crashed"}
    try:
        report = run_live_season(
            roster=caps["roster"],
            season_seed=caps["season_seed"],
            max_cost_cents=caps["max_cost_cents"],
        )
        if report.get("ok"):
            if os.environ.get("SURVIVOR_BYOK") != "1":
                # operator-keyed runs hit the public ledger; visitor-keyed runs
                # spend the visitor's own OpenRouter credits and stay off it.
                record_public_run(report)
    except Exception as exc:
        report = {"ok": False, "error": str(exc)}
    try:
        flag.write_text(
            json.dumps(
                {
                    "status": "done",
                    "finished_at": time.time(),
                    "elapsed_s": time.time() - started,
                    "report": report,
                }
            )
        )
    except Exception:
        pass
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
