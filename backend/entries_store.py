"""Paid entries: who bought a season, for which model, and where it stands.

Its own SQLite file, outside the public artifacts tree and never through the
game's process-wide database.DATABASE_PATH, so a running season can never read
or write it. Holds no card data (Stripe keeps that) and no email (Stripe's
receipt carries the status link). Mode 0600.

Statuses: awaiting_payment -> queued -> running -> finished
          awaiting_payment -> expired          (checkout abandoned)
          queued | running -> refunded         (our fault, or the model vanished)
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

# "refunding": a refund was asked for but not confirmed; such an entry never starts a season
STATUSES = ("awaiting_payment", "queued", "running", "finished", "refunding", "refunded", "expired")
OPEN = ("queued", "running")


def db_path() -> Path:
    return Path(os.environ.get("SURVIVOR_ENTRIES_DB") or Path.home() / ".kublai" / "state" / "agent-survivor" / "entries.db")


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists()
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    if fresh:
        os.chmod(path, 0o600)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS Entries (
            entry_id TEXT PRIMARY KEY,
            token TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL DEFAULT 'model',
            mode TEXT NOT NULL DEFAULT 'test',
            name TEXT NOT NULL,
            model_id TEXT NOT NULL,
            price_cents INTEGER NOT NULL,
            cap_cents INTEGER NOT NULL,
            estimate_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL,
            stripe_session_id TEXT UNIQUE,
            stripe_payment_intent TEXT,
            season_id TEXT,
            note TEXT NOT NULL DEFAULT '',
            attempts INTEGER NOT NULL DEFAULT 0,
            ip_hash TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            paid_at REAL,
            started_at REAL,
            finished_at REAL,
            refunded_at REAL
        );
        CREATE TABLE IF NOT EXISTS StripeEvents (
            event_id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            received_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS Settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    have = {row["name"] for row in conn.execute("PRAGMA table_info(Entries)")}
    for column, kind in (("refund_reason", "TEXT"), ("refund_tried_at", "REAL")):
        if column not in have:
            conn.execute(f"ALTER TABLE Entries ADD COLUMN {column} {kind}")
    return conn


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    """One connection: committed on success, rolled back on error, always closed."""
    conn = connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row)
    out["estimate"] = json.loads(out.pop("estimate_json") or "{}")
    return out


def ip_hash(ip: str | None) -> str:
    return hashlib.sha256(f"agent-survivor:{ip or ''}".encode()).hexdigest()[:16]


def create(*, name: str, model_id: str, price_cents: int, cap_cents: int, estimate: dict[str, Any], ip: str | None, kind: str = "model", mode: str = "test") -> dict[str, Any]:
    now = time.time()
    entry_id = "e_" + secrets.token_hex(8)
    token = secrets.token_urlsafe(24)
    with _db() as conn:
        conn.execute(
            """INSERT INTO Entries (entry_id, token, kind, mode, name, model_id, price_cents, cap_cents, estimate_json,
                   status, ip_hash, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'awaiting_payment', ?, ?, ?)""",
            (entry_id, token, kind, mode, name, model_id, int(price_cents), int(cap_cents), json.dumps(estimate), ip_hash(ip), now, now),
        )
    return get(entry_id)  # type: ignore[return-value]


def get(entry_id: str) -> dict[str, Any] | None:
    with _db() as conn:
        return _row(conn.execute("SELECT * FROM Entries WHERE entry_id = ?", (entry_id,)).fetchone())


def by_token(token: str) -> dict[str, Any] | None:
    with _db() as conn:
        return _row(conn.execute("SELECT * FROM Entries WHERE token = ?", (token,)).fetchone())


def by_session(session_id: str) -> dict[str, Any] | None:
    with _db() as conn:
        return _row(conn.execute("SELECT * FROM Entries WHERE stripe_session_id = ?", (session_id,)).fetchone())


def update(entry_id: str, **fields: Any) -> dict[str, Any] | None:
    if "status" in fields and fields["status"] not in STATUSES:
        raise ValueError(f"unknown status {fields['status']}")
    fields["updated_at"] = time.time()
    names = ", ".join(f"{key} = ?" for key in fields)
    with _db() as conn:
        conn.execute(f"UPDATE Entries SET {names} WHERE entry_id = ?", (*fields.values(), entry_id))
    return get(entry_id)


def move(entry_id: str, frm: tuple[str, ...], to: str, **fields: Any) -> bool:
    """Change status only from one of `frm`; True when this call made the change (safe to repeat)."""
    if to not in STATUSES:
        raise ValueError(f"unknown status {to}")
    fields.update(status=to, updated_at=time.time())
    names = ", ".join(f"{key} = ?" for key in fields)
    marks = ", ".join("?" for _ in frm)
    with _db() as conn:
        cur = conn.execute(f"UPDATE Entries SET {names} WHERE entry_id = ? AND status IN ({marks})", (*fields.values(), entry_id, *frm))
        return cur.rowcount == 1


def with_status(*statuses: str) -> list[dict[str, Any]]:
    marks = ", ".join("?" for _ in statuses)
    with _db() as conn:
        rows = conn.execute(f"SELECT * FROM Entries WHERE status IN ({marks}) ORDER BY paid_at, created_at", statuses).fetchall()
    return [_row(r) for r in rows]  # type: ignore[misc]


def queue_position(entry_id: str) -> int | None:
    """1 for the next to run; None when the entry is not waiting."""
    waiting = [e["entry_id"] for e in with_status("queued")]
    return waiting.index(entry_id) + 1 if entry_id in waiting else None


def event_handled(event_id: str) -> bool:
    with _db() as conn:
        return conn.execute("SELECT 1 FROM StripeEvents WHERE event_id = ?", (event_id,)).fetchone() is not None


def seen_event(event_id: str, kind: str) -> bool:
    """Record a Stripe event; True if it was already handled (Stripe sends some twice)."""
    with _db() as conn:
        try:
            conn.execute("INSERT INTO StripeEvents (event_id, type, received_at) VALUES (?, ?, ?)", (event_id, kind, time.time()))
        except sqlite3.IntegrityError:
            return True
    return False


def setting(key: str, default: str = "") -> str:
    with _db() as conn:
        row = conn.execute("SELECT value FROM Settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _db() as conn:
        conn.execute("INSERT INTO Settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def paused() -> bool:
    return setting("paused", "0") == "1"


def is_open() -> bool:
    """Entries are closed until an operator opens them (POST /api/entries/settings)."""
    return setting("open", "0") == "1" and not paused()


def runs_test_entries() -> bool:
    """A test-mode payment moves no money, so its season (real model fees) runs only when an operator allows it."""
    return setting("allow_test_runs", "0") == "1"
