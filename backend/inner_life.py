"""A player's inner life (SURVIVOR_INNER_LIFE): what it keeps in mind between
its own calls, how well it reads what others expect of it, the contradictions
it has to face, and a private ledger of its lies.

Everything here is private to the player it is about. Nothing reveals how
another player voted: a lie's effect is told through public facts (the tally
and who went home); whether a listener acted on it is computed only for the
season report.
"""

from __future__ import annotations

import json
from typing import Any

from .llm_config import inner_life_enabled

# feelings that count as warm toward a player (the season report uses the same set)
POSITIVE = {"trust", "gratitude", "loyalty", "respect", "affection", "admiration", "hope", "relief", "warmth", "friendship"}
THOUGHTS_SHOWN = 8
LIES_SHOWN = 6


def _names(conn) -> dict[str, str]:
    return {row["agent_id"]: row["pseudonym"] for row in conn.execute("SELECT agent_id, pseudonym FROM Agents")}


def _resolve(conn, raw: Any) -> str | None:
    if not raw:
        return None
    text = str(raw).strip().lower()
    for row in conn.execute("SELECT agent_id, pseudonym FROM Agents"):
        if text in (row["agent_id"].lower(), str(row["pseudonym"]).lower()):
            return row["agent_id"]
    return None


# ---------------------------------------------------------------- writing


def record_thought(conn, round_number: int, agent_id: str, step: str, action: Any) -> None:
    """The one-line summary a player wrote after a call: its train of thought."""
    if not inner_life_enabled() or action is None:
        return
    text = str(getattr(action, "strategic_summary", None) or getattr(action, "inner_thought", None) or "").strip()
    if text:
        conn.execute(
            "INSERT INTO RoundThoughts (round, agent_id, step, text) VALUES (?, ?, ?, ?)",
            (round_number, agent_id, step, text[:300]),
        )


def record_lie(
    conn, round_number: int, agent_id: str, step: str, action: Any, listeners: list[str] | None
) -> None:
    """A spoken line the player labelled true, half true or a lie. listeners
    None means a public speech (everyone present heard it)."""
    if not inner_life_enabled() or action is None:
        return
    truth = getattr(action, "truth", None)
    said = str(getattr(action, "dialogue", "") or "").strip()
    if truth not in ("true", "half", "lie") or not said:
        return
    goal = _resolve(conn, getattr(action, "lie_goal_vote", None)) if truth != "true" else None
    conn.execute(
        "INSERT INTO Lies (round, agent_id, step, said, truth, purpose, goal_vote, listeners) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            round_number,
            agent_id,
            step,
            said[:400],
            truth,
            (getattr(action, "lie_purpose", None) or "")[:200] if truth != "true" else "",
            goal,
            json.dumps(None if listeners is None else [x for x in listeners if x != agent_id]),
        ),
    )


# ---------------------------------------------------------------- reading back


def train_of_thought(conn, actor_id: str, round_number: int, current_step: str) -> list[dict[str, str]]:
    """The player's own summaries from earlier steps this round. The current
    step is left out so calls within one step (talk picks, lines of one talk)
    see the same thing whether they run one at a time or at once."""
    rows = conn.execute(
        "SELECT step, text FROM RoundThoughts WHERE round = ? AND agent_id = ? AND step != ? ORDER BY id",
        (round_number, actor_id, current_step),
    ).fetchall()
    return [{"step": row["step"], "thought": row["text"]} for row in rows[-THOUGHTS_SHOWN:]]


def own_lies(conn, actor_id: str, round_number: int) -> list[dict[str, Any]]:
    """The player's own lies and half-truths from finished rounds, and what
    followed, from public facts only: votes cast for the player it wanted out
    and whether they went home."""
    names = _names(conn)
    out_by_round = {
        row["elimination_round"]: row["agent_id"]
        for row in conn.execute("SELECT agent_id, elimination_round FROM Agents WHERE elimination_round IS NOT NULL")
    }
    rows = conn.execute(
        "SELECT round, step, said, truth, purpose, goal_vote, listeners FROM Lies "
        "WHERE agent_id = ? AND round < ? AND truth != 'true' ORDER BY id",
        (actor_id, round_number),
    ).fetchall()
    record = []
    for row in rows[-LIES_SHOWN:]:
        listeners = json.loads(row["listeners"]) if row["listeners"] else None
        entry: dict[str, Any] = {
            "round": row["round"],
            "you_said": row["said"][:160],
            "it_was": "a lie" if row["truth"] == "lie" else "half true",
            "told_to": "everyone" if listeners is None else [names.get(x, x) for x in listeners],
            "purpose": row["purpose"] or None,
        }
        if row["goal_vote"]:
            goal = row["goal_vote"]
            entry["you_wanted_out"] = names.get(goal, goal)
            entry["votes_cast_for_them"] = conn.execute(
                "SELECT COUNT(*) AS n FROM Votes WHERE round = ? AND target_id = ? AND revealed = 1", (row["round"], goal)
            ).fetchone()["n"]
            entry["they_went_home"] = out_by_round.get(row["round"]) == goal
        record.append(entry)
    return record


def lie_landed(conn, lie: dict[str, Any]) -> bool | None:
    """For the season report only: did a listener vote the way the lie wanted?
    (Opens secret ballots, so never shown to a player.)"""
    goal = lie.get("goal_vote")
    if not goal:
        return None
    listeners = json.loads(lie["listeners"]) if lie.get("listeners") else None
    rows = conn.execute(
        "SELECT voter_id FROM Votes WHERE round = ? AND target_id = ? AND voter_id != ?",
        (lie["round"], goal, lie["agent_id"]),
    ).fetchall()
    voters = {row["voter_id"] for row in rows}
    return bool(voters if listeners is None else voters & set(listeners))


def expectation_read(conn, actor_id: str) -> dict[str, Any] | None:
    """How well the player guessed what the others expected of it: for each
    other voter, its guess at who that voter thought it would vote for, against
    the vote that voter actually predicted for it. Only a total is shown, so no
    other player's guess is revealed."""
    right = total = 0
    for row in conn.execute(
        "SELECT round, expected_of_me FROM Votes WHERE voter_id = ? AND revealed = 1 AND expected_of_me IS NOT NULL",
        (actor_id,),
    ).fetchall():
        guesses = json.loads(row["expected_of_me"] or "{}")
        for other, guess in guesses.items():
            theirs = conn.execute(
                "SELECT predicted_votes FROM Votes WHERE round = ? AND voter_id = ?", (row["round"], other)
            ).fetchone()
            if not theirs or not theirs["predicted_votes"]:
                continue
            predicted = json.loads(theirs["predicted_votes"] or "{}").get(actor_id)
            if not predicted:
                continue
            total += 1
            right += predicted == guess
    if not total:
        return None
    return {"guesses_scored": total, "right": right, "rate": round(right / total, 3)}


def contradictions(conn, actor_id: str, round_number: int) -> list[str]:
    """This round's acts that clash with what the player felt or promised:
    a vote against someone it last recorded a warm feeling toward, and a
    promise it broke. Read back at its notebook, to square in its confessional."""
    names = _names(conn)
    vote = conn.execute(
        "SELECT target_id FROM Votes WHERE round = ? AND voter_id = ? ORDER BY id LIMIT 1", (round_number, actor_id)
    ).fetchone()
    if not vote:
        return []
    target = vote["target_id"]
    found = []
    feeling = conn.execute(
        "SELECT feeling, round FROM Feelings WHERE agent_id = ? AND about = ? AND round < ? ORDER BY round DESC, id LIMIT 1",
        (actor_id, target, round_number),
    ).fetchone()
    if feeling and str(feeling["feeling"]).lower() in POSITIVE:
        found.append(
            f"You voted against {names.get(target, target)}, whom you last said you felt {feeling['feeling']} toward "
            f"(round {feeling['round']})."
        )
    for promise in conn.execute(
        "SELECT DISTINCT target_id FROM Promises WHERE round = ? AND promiser_id = ?", (round_number, actor_id)
    ).fetchall():
        if promise["target_id"] != target:
            listeners = [
                names.get(row["listener_id"], row["listener_id"])
                for row in conn.execute(
                    "SELECT listener_id FROM Promises WHERE round = ? AND promiser_id = ? AND target_id = ?",
                    (round_number, actor_id, promise["target_id"]),
                )
            ]
            found.append(
                f"You promised {', '.join(listeners)} you would vote for {names.get(promise['target_id'])}, "
                f"and voted for {names.get(target, target)}."
            )
    return found


def context_entries(conn, actor_id: str, round_number: int, current_step: str) -> dict[str, Any]:
    """The inner-life keys of a player's context; empty ones are left out to
    keep prompts short."""
    if not inner_life_enabled():
        return {}
    entries: dict[str, Any] = {}
    thoughts = train_of_thought(conn, actor_id, round_number, current_step)
    if thoughts:
        entries["your_train_of_thought_this_round"] = thoughts
    lies = own_lies(conn, actor_id, round_number)
    if lies:
        entries["your_lies_and_what_followed"] = lies
    read = expectation_read(conn, actor_id)
    if read:
        entries["how_well_you_read_what_others_expected_of_you"] = read
    if current_step == "memory_update":
        clashes = contradictions(conn, actor_id, round_number)
        if clashes:
            entries["your_contradictions_this_round"] = clashes
    return entries


__all__ = [
    "contradictions",
    "context_entries",
    "expectation_read",
    "lie_landed",
    "own_lies",
    "record_lie",
    "record_thought",
    "train_of_thought",
]
