"""Private playbooks for agent seats (paid entries with an owner's advice).

A playbook is an owner's Markdown advice for their agent. It goes into that
seat's own prompts only, after the game record (which holds the rules, and
the rules always win). It lives in this process-local registry, filled by
the season runner from its private spec file and cleared when the season
ends. It is never written to season.db, StoryEvents, Agents, entries.json or
any report. The public prompt log cuts the block out by its delimiters
(backend/prompt_log.py); keep these lines in step with it.
"""

from __future__ import annotations

import hashlib
import threading

PLAYBOOK_MAX_CHARS = 6000
BEGIN = "----- BEGIN OWNER PLAYBOOK (private; sha256 {sha}) -----"
END = "----- END OWNER PLAYBOOK -----"
LEAD = "Your owner's playbook (private): advice you may follow or set aside. The game rules above always win."

_lock = threading.Lock()
_registry: dict[str, tuple[str, str]] = {}


class PlaybookError(ValueError):
    """A playbook that cannot be used: too long, or its hash does not match."""


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def check(text: str, expected_sha256: str | None = None) -> str:
    """The playbook's hash, after checking its size and, when given, its hash."""
    if len(text) > PLAYBOOK_MAX_CHARS:
        raise PlaybookError(f"playbook is {len(text)} characters; the limit is {PLAYBOOK_MAX_CHARS}")
    digest = sha256(text)
    if expected_sha256 is not None and expected_sha256 != digest:
        raise PlaybookError("playbook_sha256 does not match the playbook text")
    return digest


def register(agent_id: str, text: str, expected_sha256: str | None = None) -> None:
    digest = check(text, expected_sha256)
    with _lock:
        _registry[agent_id] = (text, digest)


def clear() -> None:
    with _lock:
        _registry.clear()


def playbook_for(agent_id: str | None) -> tuple[str, str] | None:
    if not agent_id:
        return None
    with _lock:
        return _registry.get(agent_id)


def block(actor: dict | None) -> str:
    """The playbook block for this seat's prompt, or "" for a seat without one."""
    found = playbook_for((actor or {}).get("agent_id"))
    if not found or not found[0].strip():
        return ""
    text, digest = found
    return "\n".join(["", BEGIN.format(sha=digest), LEAD, text.strip(), END, ""])
