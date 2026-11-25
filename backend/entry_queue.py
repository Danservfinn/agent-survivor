"""Run paid entries one at a time, and settle each when its season ends.

A thread in the API process ticks every TICK_S seconds:
1. If an entry is running and its runner has stopped, settle it: finished
   (the season has a result), or refunded (the season failed for our
   reasons: a provider outage, the cost cap, or a crash on its second try).
2. If no season is running and an entry is queued, re-check its model (still
   in the catalog at a fixed price, still answering, not dearer than paid
   for) and start it through scripts/run_public_season.py --entry-spec, the
   same runner the public button uses, so the one-season-at-a-time check
   covers both. A model that fails the re-check is refunded.

The runner never touches this store or Stripe; it reports through
runner_status.json and the season's scorecard.json.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import entries_store as store

TICK_S = 15
MAX_ATTEMPTS = 2  # a crash is retried once before the entry is refunded

_lock = threading.Lock()
_thread: threading.Thread | None = None


def _seasons_dir() -> Path:
    from .live_season import SEASONS_DIR

    return Path(SEASONS_DIR)


def _flag() -> dict[str, Any] | None:
    try:
        return json.loads((_seasons_dir() / "runner_status.json").read_text())
    except (OSError, ValueError):
        return None


def runner_busy() -> bool:
    """True while any season runs (the public button's or an entry's)."""
    flag = _flag()
    if not flag or flag.get("status") != "running":
        return False
    try:
        return subprocess.run(["pgrep", "-f", "run_public_season.py"], capture_output=True, timeout=5).returncode == 0
    except Exception:  # noqa: BLE001 - cannot tell; trust the flag
        return True


def _season_for(entry_id: str) -> Path | None:
    base = _seasons_dir()
    if not base.exists():
        return None
    for folder in sorted((p for p in base.iterdir() if p.is_dir() and p.name.endswith("-lab_mix_entry")), reverse=True):
        try:
            if json.loads((folder / "entry.json").read_text()).get("entry_id") == entry_id:
                return folder
        except (OSError, ValueError):
            continue
    return None


def _refund(entry: dict[str, Any], reason: str) -> None:
    from .entry_payments import PaymentError, refund

    try:
        refund(entry, reason)
    except PaymentError as exc:
        # leave it for an operator; the status page says a refund is pending
        store.update(entry["entry_id"], note=f"refund pending: {reason} ({exc})")
        return
    store.move(entry["entry_id"], ("queued", "running"), "refunded", refunded_at=time.time(), note=reason)


def settle(entry: dict[str, Any]) -> str:
    """Decide what happened to a running entry whose runner has stopped."""
    folder = _season_for(entry["entry_id"])
    card: dict[str, Any] = {}
    if folder is not None:
        try:
            card = json.loads((folder / "scorecard.json").read_text())
        except (OSError, ValueError):
            card = {}
    if card.get("finished"):
        store.move(entry["entry_id"], ("running",), "finished", season_id=folder.name, finished_at=time.time())  # type: ignore[union-attr]
        return "finished"
    if card.get("interrupted"):
        _refund(entry, "an AI provider went down during the season")
        return "refunded"
    if (card.get("caps") or {}).get("cap_reached"):
        _refund(entry, "the season reached its spending limit before it ended")
        return "refunded"
    if entry["attempts"] < MAX_ATTEMPTS:
        store.move(entry["entry_id"], ("running",), "queued", note="restarted after the season stopped unexpectedly")
        return "requeued"
    _refund(entry, "the season stopped unexpectedly twice")
    return "refunded"


def recheck(entry: dict[str, Any]) -> str | None:
    """Why the entry can no longer run as paid for, or None if it can."""
    from .entry_pricing import QuoteError, quote

    try:
        fresh = quote(entry["model_id"])
    except QuoteError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001 - a catalog outage is not the buyer's fault, but do not run blind
        return f"could not check the model ({type(exc).__name__})"
    if fresh["price_cents"] > entry["price_cents"]:
        return "the model's price rose above what was paid"
    return None


def start(entry: dict[str, Any], *, launch: Callable[[Path], None] | None = None) -> None:
    """Write the entry's spec (0600, deleted by the runner) and launch the runner."""
    spec_dir = _seasons_dir().parent / "entries"
    spec_dir.mkdir(parents=True, exist_ok=True)
    spec = spec_dir / f"{entry['entry_id']}.json"
    fd = os.open(spec, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(
            {"entry_id": entry["entry_id"], "name": entry["name"], "model_id": entry["model_id"], "cap_cents": entry["cap_cents"]},
            handle,
        )
    store.move(entry["entry_id"], ("queued",), "running", started_at=time.time(), attempts=entry["attempts"] + 1)
    (launch or _launch)(spec)


def _launch(spec: Path) -> None:
    root = Path(__file__).resolve().parent.parent
    seasons = _seasons_dir()
    seasons.mkdir(parents=True, exist_ok=True)
    (seasons / "runner_status.json").write_text(json.dumps({"status": "running", "started_at": time.time(), "entry": True}))
    with open(seasons / "runner.log", "ab") as log:
        subprocess.Popen(
            [sys.executable, str(root / "scripts" / "run_public_season.py"), "--entry-spec", str(spec)],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=dict(os.environ),
        )


def tick(*, launch: Callable[[Path], None] | None = None, busy: Callable[[], bool] | None = None) -> str:
    """One pass of the dispatcher. Returns what it did, for logs and tests."""
    with _lock:
        is_busy = (busy or runner_busy)()
        for entry in store.with_status("running"):
            if not is_busy:
                return f"settled {entry['entry_id']}: {settle(entry)}"
        if is_busy or store.paused():
            return "busy" if is_busy else "paused"
        queued = [e for e in store.with_status("queued") if e["mode"] == "live" or store.runs_test_entries()]
        if not queued:
            return "idle"
        entry = queued[0]
        problem = recheck(entry)
        if problem:
            store.move(entry["entry_id"], ("queued",), "queued", note=problem)
            _refund(entry, problem)
            return f"refunded {entry['entry_id']}: {problem}"
        start(entry, launch=launch)
        return f"started {entry['entry_id']}"


def run_forever() -> None:
    while True:
        try:
            tick()
        except Exception as exc:  # noqa: BLE001 - the dispatcher must outlive any one bad pass
            print(f"entry queue: {type(exc).__name__}: {exc}")
        time.sleep(TICK_S)


def start_dispatcher() -> None:
    global _thread
    # never inside a test run: tests drive tick() themselves
    if "unittest" in sys.modules and os.environ.get("SURVIVOR_ENTRY_DISPATCHER") != "1":
        return
    if _thread is None and os.environ.get("SURVIVOR_ENTRY_DISPATCHER", "1") != "0":
        _thread = threading.Thread(target=run_forever, name="entry-queue", daemon=True)
        _thread.start()
