"""Run one live season with a fixed roster, seed and prompt version, for A/B comparisons.

    SURVIVOR_PROMPT_PROFILE=v2 .venv/bin/python scripts/run_comparison_season.py cheap_recent_openrouter 4242 400
    SURVIVOR_JURY_RULE=own_judgment .venv/bin/python scripts/run_comparison_season.py cheap_recent_openrouter 4242 400

Arguments: roster, season seed, estimated-cost cap in cents. The season is written
under artifacts/seasons/ of the tree this runs from, so run it from a development
worktree to keep it out of the public history. Compare two with
scripts/compare_seasons.py. Reads the operator key from ~/.kublai/secrets/agent-survivor.env.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_env_file() -> None:
    env_path = Path.home() / ".kublai" / "secrets" / "agent-survivor.env"
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    roster, seed, cap = argv[0], int(argv[1]), float(argv[2])
    _load_env_file()
    os.environ["LLM_PROVIDER"] = "openrouter"
    os.environ["AGENT_SURVIVOR_FAST_BENCHMARK"] = "1"
    from backend.live_season import run_live_season
    from backend.llm_config import feelings_enabled, jury_rule, prompt_profile, secret_ballot

    print(
        f"roster={roster} seed={seed} cap={cap}c prompt={prompt_profile()} jury={jury_rule()} "
        f"feelings={feelings_enabled()} secret_ballot={secret_ballot()}",
        flush=True,
    )
    started = time.time()
    report = run_live_season(roster=roster, season_seed=seed, max_cost_cents=cap)
    print(json.dumps({
        "prompt": prompt_profile(),
        "jury_rule": jury_rule(),
        "feelings": feelings_enabled(),
        "ok": report.get("ok"),
        "error": report.get("error"),
        "winner": (report.get("game") or {}).get("winner"),
        "finished": report.get("finished"),
        "interrupted": report.get("interrupted"),
        "cap_reached": (report.get("caps") or {}).get("cap_reached"),
        "est_cost_cents": (report.get("caps") or {}).get("estimated_cost_cents"),
        "minutes": round((time.time() - started) / 60, 1),
        "season_id": report.get("season_id"),
    }), flush=True)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
