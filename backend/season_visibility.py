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
import os
import secrets
import urllib.request
from pathlib import Path
from typing import Any

FILE = "visibility.json"
# Cloudflare edge cache: share images and data files are cached for hours, so a
# withdrawn season is purged there too. The token can purge cache and nothing
# else (minted 2026-11-25); the zone id is not secret.
PURGE_TOKEN_FILE = Path(os.environ.get("SURVIVOR_CF_PURGE_TOKEN_FILE") or Path.home() / ".kublai" / "secrets" / "agent-survivor-cf-purge-token")
ZONE_ID = os.environ.get("SURVIVOR_CF_ZONE_ID") or "5d7354c125785eca7b769bae72ce0515"
SITE_HOSTS = ("agentsurvivor.kurult.ai", "llmsurvivor.kurult.ai")
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
    was = record["state"]
    record["state"] = new_state
    if new_state == "unlisted" and not record.get("token"):
        record["token"] = secrets.token_urlsafe(18)
    _write(season_dir, record)
    if new_state != "public" and was != new_state:
        # what the edge cached while it was reachable must go too
        record = {**record, "purge": purge_edge_cache(season_dir)}
    return record


def season_urls(season_dir: Path) -> list[str]:
    """Every public address of a season's files, on both hostnames."""
    sid = Path(season_dir).name
    paths = [f"/report/{sid}", f"/api/live-season/report/{sid}", f"/watch?season={sid}"]
    share = Path(season_dir) / "share"
    if share.is_dir():
        paths += [f"/report/{sid}/share/{f.name}" for f in sorted(share.iterdir()) if f.is_file()]
    paths += [f"/report/{sid}/data/{name}" for name in ("report.json", "prompts.jsonl.gz", "season.db", "entry-calls.jsonl.gz")]
    return [f"https://{host}{path}" for host in SITE_HOSTS for path in paths]


def purge_edge_cache(season_dir: Path) -> dict[str, Any]:
    """Ask Cloudflare to drop its cached copies of the season's files. Never
    raises: the result says what happened. Cached image URLs that carried a
    ?v= query are separate entries and expire on their own within hours."""
    try:
        token = PURGE_TOKEN_FILE.read_text().strip()
    except OSError:
        return {"ok": False, "reason": "no purge token on this machine"}
    urls = season_urls(season_dir)
    purged = 0
    for start in range(0, len(urls), 30):  # Cloudflare takes at most 30 files a call
        batch = urls[start:start + 30]
        request = urllib.request.Request(
            f"https://api.cloudflare.com/client/v4/zones/{ZONE_ID}/purge_cache",
            data=json.dumps({"files": batch}).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                body = json.loads(response.read().decode() or "{}")
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            return {"ok": False, "reason": type(exc).__name__, "purged": purged}
        if not body.get("success"):
            return {"ok": False, "reason": "cloudflare refused the purge", "purged": purged}
        purged += len(batch)
    return {"ok": True, "purged": purged}


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
    print(json.dumps({"state": record["state"], "has_token": bool(record.get("token")), "embargo": record.get("embargo", False), "purge": record.get("purge")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
