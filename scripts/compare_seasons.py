"""Compare strategy measures across seasons, e.g. a v2 and a v3 prompt run.

    .venv/bin/python scripts/compare_seasons.py artifacts/seasons/<A> artifacts/seasons/<B>

Each argument is a season directory (containing season.db) or a .db file.
Run the seasons on the same roster and SURVIVOR_SEASON_SEED, changing only
SURVIVOR_PROMPT_PROFILE, so the prompt is the only difference.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.season_compare import season_measures  # noqa: E402


def main(paths: list[str]) -> int:
    if not paths:
        print(__doc__)
        return 2
    columns = []
    for raw in paths:
        path = Path(raw)
        db = path / "season.db" if path.is_dir() else path
        columns.append((path.name, season_measures(db)))
    keys = list(columns[0][1])
    width = max(len(key) for key in keys) + 2
    print("".ljust(width) + "".join(name[:28].ljust(30) for name, _ in columns))
    for key in keys:
        print(key.ljust(width) + "".join(str(measures[key]).ljust(30) for _, measures in columns))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
