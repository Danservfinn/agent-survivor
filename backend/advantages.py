"""Moves that give a player something real to work with (SURVIVOR_ADVANTAGES):
receipts (forwarding a private line the game stamps as real), spying on a
talk, and proving you hold an idol. A stamp is the only thing a player cannot
fake; any claim made without one is just words.
"""

from __future__ import annotations

import json
import random
from typing import Any

from .llm_config import advantages_enabled

SILENT_LINE = "(says nothing)"
SPY_CAUGHT_CHANCE = 1 / 3


def _names(conn) -> dict[str, str]:
    return {row["agent_id"]: row["pseudonym"] for row in conn.execute("SELECT agent_id, pseudonym FROM Agents")}


def receipt_id(event_id: int, line_index: int) -> str:
    return f"m{event_id}-{line_index}"


def receipts_for(conn, actor_id: str) -> dict[str, dict[str, Any]]:
    """Every private line another player said where this player heard it,
    by receipt id: the lines it can forward with the game's stamp."""
    names = _names(conn)
    receipts: dict[str, dict[str, Any]] = {}
    for row in conn.execute(
        "SELECT id, round, payload FROM StoryEvents WHERE payload LIKE '%participants_only%' ORDER BY id"
    ).fetchall():
        payload = json.loads(row["payload"] or "{}")
        if payload.get("privacy") != "participants_only" or actor_id not in (payload.get("participant_ids") or []):
            continue
        for index, line in enumerate(payload.get("speaker_lines") or []):
            speaker, text = line.get("agent_id"), line.get("text")
            if not isinstance(speaker, str) or speaker == actor_id or not text or text == SILENT_LINE:
                continue
            receipts[receipt_id(row["id"], index)] = {
                "from": names.get(speaker, speaker),
                "round": row["round"],
                "said": str(text)[:300],
            }
    return receipts


def line_stamps(
    action: Any, receipts: dict[str, dict[str, Any]], holds_idol: bool, speaker_name: str
) -> list[dict[str, Any]]:
    """What the game vouches for on a spoken line: a forwarded private line the
    speaker really heard, and a shown idol the speaker really holds. A made-up
    receipt or an idol it does not hold attaches nothing."""
    if not advantages_enabled() or action is None:
        return []
    stamps: list[dict[str, Any]] = []
    rid = str(getattr(action, "receipt_id", None) or "").strip()
    if rid in receipts:
        stamps.append({"forwarded": receipts[rid], "verified_by": "the game"})
    if getattr(action, "show_idol", False) and holds_idol:
        stamps.append({"shown": f"{speaker_name} showed a hidden immunity idol.", "verified_by": "the game"})
    return stamps


def with_stamps(text: str, stamps: list[dict[str, Any]]) -> str:
    """The line as the others in the talk hear it."""
    notes = []
    for stamp in stamps:
        if "forwarded" in stamp:
            f = stamp["forwarded"]
            notes.append(f"forwards, stamped real by the game: {f['from']} said in round {f['round']}: \"{f['said']}\"")
        if "shown" in stamp:
            notes.append(f"stamped real by the game: {stamp['shown']}")
    return f"{text} [{'; '.join(notes)}]" if notes else text


# ---------------------------------------------------------------- spying


def find_talk(conn, round_number: int, spy_id: str, target_id: str) -> dict[str, Any] | None:
    """This round's first private talk with the named player that the spy was
    not part of."""
    for row in conn.execute(
        "SELECT id, payload FROM StoryEvents WHERE round = ? AND kind = 'conversation' ORDER BY id", (round_number,)
    ).fetchall():
        payload = json.loads(row["payload"] or "{}")
        members = payload.get("participant_ids") or []
        if target_id in members and spy_id not in members:
            return {"event_id": row["id"], "members": members, "lines": payload.get("speaker_lines") or []}
    return None


def spy_caught(seed: int, round_number: int, spy_id: str) -> bool:
    return random.Random(f"spy:{seed}:{round_number}:{spy_id}").random() < SPY_CAUGHT_CHANCE


def overheard_text(conn, talk: dict[str, Any]) -> str:
    names = _names(conn)
    said = " | ".join(
        f"{names.get(line.get('agent_id'), line.get('agent_id'))}: {line.get('text')}"
        for line in talk["lines"]
        if isinstance(line, dict) and line.get("text") and line.get("text") != SILENT_LINE
    )
    members = ", ".join(names.get(agent_id, agent_id) for agent_id in talk["members"])
    return f"You listened in on the talk between {members}. You heard: {said or '(nothing said)'}"
