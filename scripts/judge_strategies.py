"""Score each player's strategy in a finished season with the judge panel.

    .venv/bin/python scripts/judge_strategies.py artifacts/seasons/<season>

Reads the operator key from ~/.kublai/secrets/llm-survivor.env. Replaces
earlier ratings from the same judges. Judges: SURVIVOR_STRATEGY_JUDGES.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    for line in (Path.home() / ".kublai" / "secrets" / "llm-survivor.env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    os.environ["LLM_PROVIDER"] = "openrouter"
    from backend import database, live_cost
    from backend.strategy_judging import judge_season, judges

    live_cost.get_pricing_table()
    db = Path(argv[0]) / "season.db"
    database.init_database(path=db)  # an older season gets the newer tables
    print(f"judges: {', '.join(judges())}")
    print(f"ratings written: {judge_season(db)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
