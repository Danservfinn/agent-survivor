"""Run paid entries one at a time, and settle each when its season ends.

A thread in the API process ticks every TICK_S seconds:
1. Retry refunds that did not go through ("refunding"). An entry in that state
   never starts a season; it leaves it only as "refunded", once Stripe shows
   the money returned.
2. If an entry is running and its runner has stopped, settle it: finished
   (the season has a result), refunded (the season failed for our reasons:
   a provider outage, the cost cap, or a crash on its second try), or queued
   again after a first crash.
3. If no season is running and an entry can start (live mode, or test mode
   with the operator's allow_test_runs), re-check its model (still in the
   catalog at a fixed price, still answering, not dearer than paid for) and
   confirm with Stripe that its payment was not refunded, then start it
   through scripts/run_public_season.py --entry-spec, the same runner the
   public button uses, under the same lock (backend/season_launch.py).
   A model that fails the re-check is refunded; a check that could not run
   (a network or catalog error) is retried for a while before that.

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
from .season_launch import LAUNCH_LOCK

TICK_S = 15
MAX_ATTEMPTS = 2  # a crash is retried once before the entry is refunded
REFUND_RETRY_S = 600  # a refund that failed is tried again every 10 minutes
RECHECK_DEFERRALS = 20  # about 5 minutes of failed checks before an entry is refunded

# buyer-visible notes: plain words, never a provider's or Stripe's error text
NOTE_REFUND_PENDING = "your refund is being processed"

_thread: threading.Thread | None = None
_deferred: dict[str, int] = {}


def _seasons_dir() -> Path:
    from .live_season import SEASONS_DIR

    return Path(SEASONS_DIR)


def _runner_path() -> str:
    return str(Path(__file__).resolve().parent.parent / "scripts" / "run_public_season.py")


def _flag() -> dict[str, Any] | None:
    try:
        return json.loads((_seasons_dir() / "runner_status.json").read_text())
    except (OSError, ValueError):
        return None


def runner_busy() -> bool:
    """True while any season runs from this checkout (the public button's or an entry's)."""
    flag = _flag()
    if not flag or flag.get("status") != "running":
        return False
    try:
        # this checkout's runner only (its full path), so a run from another checkout never blocks it
        return subprocess.run(["pgrep", "-f", _runner_path()], capture_output=True, timeout=5).returncode == 0
    except Exception:  # noqa: BLE001 - cannot tell; trust the flag
        return True


def startable() -> list[dict[str, Any]]:
    """Queued entries the dispatcher may start now, in the order they were paid."""
    if store.paused():
        return []
    return [e for e in store.with_status("queued") if e["mode"] == "live" or store.runs_test_entries()]


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


def refund_now(entry: dict[str, Any], reason: str) -> str:
    """Move the entry to "refunding" (it can never start from there), then ask Stripe.

    "refunded" once Stripe has returned the money; "refunding" if the request
    failed (it is retried, and confirmed against the payment first)."""
    from .entry_payments import PaymentError, refund, refunded_cents

    entry_id = entry["entry_id"]
    if entry["status"] != "refunding":
        if not store.move(entry_id, ("awaiting_payment", "queued", "running"), "refunding", note=NOTE_REFUND_PENDING, refund_reason=reason):
            return store.get(entry_id)["status"]  # type: ignore[index]
    try:
        if refunded_cents(entry) >= int(entry["price_cents"]):
            store.move(entry_id, ("refunding",), "refunded", refunded_at=time.time(), note=reason)
            return "refunded"
        refund(entry, reason)
    except PaymentError as exc:
        if exc.already_refunded:
            store.move(entry_id, ("refunding",), "refunded", refunded_at=time.time(), note=reason)
            return "refunded"
        store.update(entry_id, refund_tried_at=time.time())
        print(f"entry queue: refund for {entry_id} not confirmed yet: {exc}")
        return "refunding"
    store.move(entry_id, ("refunding",), "refunded", refunded_at=time.time(), note=reason)
    return "refunded"


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
        return refund_now(entry, "an AI provider went down during the season")
    if (card.get("caps") or {}).get("cap_reached"):
        return refund_now(entry, "the season reached its spending limit before it ended")
    if entry["attempts"] < MAX_ATTEMPTS:
        store.move(entry["entry_id"], ("running",), "queued", note="restarted after the season stopped unexpectedly")
        return "requeued"
    return refund_now(entry, "the season stopped unexpectedly twice")


class RecheckDeferred(Exception):
    """The re-check could not run (network, catalog); try again later."""


def recheck(entry: dict[str, Any]) -> str | None:
    """Why the entry can no longer run as paid for, or None if it can. Raises RecheckDeferred
    when the check itself failed, so one transient error never refunds anyone."""
    from .entry_payments import PaymentError, refunded_cents
    from .entry_pricing import QuoteError, quote

    try:
        if refunded_cents(entry) > 0:
            return "the payment was refunded"
    except PaymentError as exc:
        raise RecheckDeferred(f"could not read the payment: {exc}") from None
    try:
        fresh = quote(entry["model_id"])
    except QuoteError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001 - a catalog or network failure is not a verdict
        raise RecheckDeferred(f"could not check the model ({type(exc).__name__})") from None
    if fresh["price_cents"] > entry["price_cents"]:
        return "the model's price rose above what was paid"
    return None


def start(entry: dict[str, Any], *, launch: Callable[[Path], None] | None = None) -> bool:
    """Claim the entry and launch its season. False if another caller claimed it first."""
    if not store.move(entry["entry_id"], ("queued",), "running", started_at=time.time(), attempts=entry["attempts"] + 1):
        return False
    spec_dir = _seasons_dir().parent / "entries"
    spec_dir.mkdir(parents=True, exist_ok=True)
    spec = spec_dir / f"{entry['entry_id']}.json"
    fd = os.open(spec, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(
            {"entry_id": entry["entry_id"], "name": entry["name"], "model_id": entry["model_id"], "cap_cents": entry["cap_cents"]},
            handle,
        )
    (launch or _launch)(spec)
    return True


def _launch(spec: Path) -> None:
    seasons = _seasons_dir()
    seasons.mkdir(parents=True, exist_ok=True)
    (seasons / "runner_status.json").write_text(json.dumps({"status": "running", "started_at": time.time(), "entry": True}))
    with open(seasons / "runner.log", "ab") as log:
        subprocess.Popen(
            [sys.executable, _runner_path(), "--entry-spec", str(spec)],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=dict(os.environ),
        )


def tick(*, launch: Callable[[Path], None] | None = None, busy: Callable[[], bool] | None = None) -> str:
    """One pass of the dispatcher. Returns what it did, for logs and tests."""
    is_busy = busy or runner_busy
    now = time.time()
    for entry in store.with_status("refunding"):
        if now - (entry.get("refund_tried_at") or 0) >= REFUND_RETRY_S:
            return f"refund {entry['entry_id']}: {refund_now(entry, entry.get('refund_reason') or 'refund')}"
    if not is_busy():
        for entry in store.with_status("running"):
            return f"settled {entry['entry_id']}: {settle(entry)}"
    if is_busy():
        return "busy"
    if store.paused():
        return "paused"
    waiting = startable()
    if not waiting:
        return "idle"
    entry = waiting[0]
    try:
        problem = recheck(entry)  # network calls: outside the launch lock
    except RecheckDeferred as exc:
        _deferred[entry["entry_id"]] = _deferred.get(entry["entry_id"], 0) + 1
        if _deferred[entry["entry_id"]] < RECHECK_DEFERRALS:
            return f"deferred {entry['entry_id']}: {exc}"
        problem = "the model could not be checked before its season"
    _deferred.pop(entry["entry_id"], None)
    if problem:
        return f"refund {entry['entry_id']}: {refund_now(entry, problem)} ({problem})"
    with LAUNCH_LOCK:
        if is_busy():
            return "busy"
        current = store.get(entry["entry_id"])
        if not current or current["status"] != "queued":
            return "changed"
        if not start(current, launch=launch):
            return "claimed elsewhere"
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
