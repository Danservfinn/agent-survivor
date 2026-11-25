"""Who may see a season's report, replay and data.

A season folder can hold `visibility.json`:

    {"state": "public" | "unlisted" | "withdrawn", "token": "<random>", "embargo": false}

- public: anyone, and the season may appear in lists (history, leaderboards).
- unlisted: only with the link, which carries `?k=<token>` (season ids are
  start times, so a bare id is guessable); never listed.
- withdrawn: nobody; every route answers 404. An operator sets it.

A house season has no file and is public. A paid entry's season starts
unlisted (the buyer sees it first, then chooses), so `ensure()` writes the
file with a fresh token the first time the season is seen. `embargo` is the
hook for own-endpoint entries: while true the season is hidden until it ends,
whatever the state.

Standard library only.
"""

from __future__ import annotations

import hmac
import json
import secrets
from pathlib import Path
from typing import Any

FILE = "visibility.json"
STATES = ("public", "unlisted", "withdrawn")


def _read(season_dir: Path) -> dict[str, Any] | None:
    path = Path(season_dir) / FILE
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) and value.get("state") in STATES else None


def _write(season_dir: Path, record: dict[str, Any]) -> None:
    path = Path(season_dir) / FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record))
    tmp.chmod(0o600)
    tmp.replace(path)


def is_entry(season_dir: Path) -> bool:
    return (Path(season_dir) / "entry.json").exists()


def ensure(season_dir: Path) -> dict[str, Any]:
    """The season's visibility record; an entry season without one becomes
    unlisted with a new token."""
    record = _read(season_dir)
    if record is not None:
        return record
    if is_entry(season_dir):
        record = {"state": "unlisted", "token": secrets.token_urlsafe(18), "embargo": False}
        _write(season_dir, record)
        return record
    return {"state": "public", "token": None, "embargo": False}


def state(season_dir: Path) -> str:
    return ensure(season_dir)["state"]


def set_state(season_dir: Path, new_state: str) -> dict[str, Any]:
    if new_state not in STATES:
        raise ValueError(f"state must be one of {', '.join(STATES)}")
    record = dict(ensure(season_dir))
    record["state"] = new_state
    if new_state == "unlisted" and not record.get("token"):
        record["token"] = secrets.token_urlsafe(18)
    _write(season_dir, record)
    return record


def set_embargo(season_dir: Path, on: bool) -> None:
    record = dict(ensure(season_dir))
    record["embargo"] = bool(on)
    _write(season_dir, record)


def finished(season_dir: Path) -> bool:
    try:
        return bool(json.loads((Path(season_dir) / "scorecard.json").read_text()).get("finished"))
    except (OSError, ValueError):
        return False


def can_view(season_dir: Path, key: str | None) -> bool:
    """May a visitor holding `key` (or none) see this season?"""
    record = ensure(season_dir)
    if record["state"] == "withdrawn":
        return False
    if record.get("embargo") and not finished(season_dir):
        return False
    if record["state"] == "unlisted":
        token = record.get("token") or ""
        return bool(key) and bool(token) and hmac.compare_digest(str(key), str(token))
    return True


def listed(season_dir: Path) -> bool:
    """May the season appear in public lists (history, latest, leaderboards)?"""
    record = ensure(season_dir)
    return record["state"] == "public" and not (record.get("embargo") and not finished(season_dir))


def link_key(season_dir: Path) -> str | None:
    """The `k` a link to this season must carry, or None when it needs none."""
    record = ensure(season_dir)
    return record.get("token") if record["state"] == "unlisted" else None


def main(argv: list[str] | None = None) -> int:
    """Operator: python3 -m backend.season_visibility <season folder> [public|unlisted|withdrawn]"""
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(main.__doc__)
        return 2
    season_dir = Path(args[0]).expanduser()
    if not (season_dir / "season.db").exists():
        print(f"no season in {season_dir}")
        return 1
    record = set_state(season_dir, args[1]) if len(args) > 1 else ensure(season_dir)
    print(json.dumps({"state": record["state"], "has_token": bool(record.get("token")), "embargo": record.get("embargo", False)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
