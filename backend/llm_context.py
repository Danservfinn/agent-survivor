from __future__ import annotations

import json
from typing import Any

from .database import row_to_dict

MAX_CONTEXT_EVENTS = 60
# Upper bound on a serialized contestant context. When over it, the oldest
# timeline and private events are dropped first; the ledgers and the rules,
# which are the verified record of the game, are never trimmed.
MAX_CONTEXT_CHARS = 40_000

# One statement of the rules every player reads. Keep it in step with the engine
# in turn_controller.py: players can only plan around rules they are told.
GAME_RULES = [
    "All players live and vote as one group for the whole season; there are no tribes.",
    "Each round has a challenge: the same five puzzles for every player, easiest to hardest. Most correct answers wins; a tie goes to whoever solved the harder puzzles, then to a sudden-death set; if still tied, nobody wins. Speed does not count. Every player's score is public (challenge_history).",
    "The challenge winner is immune: they cannot be voted for that round, but they still vote. A player may answer puzzles wrong on purpose to look less threatening.",
    "In the first two rounds of the season the challenge winner must give a hidden idol to another player. Everyone learns that an idol was given; only the giver and the holder know who has it. Only one idol is in play at a time.",
    "A holder plays the idol with their vote at a council; every vote against them that round is then cancelled, and the play is announced. An unplayed idol expires when five players remain.",
    "Each voter casts one secret vote for an eligible player. After everyone votes, the votes are revealed one by one and stay public: every player sees who voted for whom (vote_history).",
    "The player with the most votes leaves. On a tie there is a revote among the tied players only; tied players cannot vote for themselves.",
    "If the revote is also tied, the tied player with the fewest challenge wins this season leaves; if still level, the one who has received the most votes this season leaves.",
    "Each round, before the vote, every player picks one other player for a private talk; only the two of them hear it. A promise to vote for someone, made in a talk, is recorded and later checked against the revealed vote (your_promises).",
    "Each round you privately write a plan before the challenge, privately predict the vote when you cast yours, and rewrite your private notebook after the council. Only you see these; your prediction accuracy is shown to you (your_predictions).",
    "After the talks, each player may send one short private message to one other player before the vote.",
    "When three players remain the finale starts. Every eliminated player sits on the jury. Jurors receive each finalist's verified record (votes cast and received, promises kept and broken, challenge results, idol gifts, alliances and betrayals), each juror questions one finalist of their choosing, and then votes. Most jury votes wins.",
    "Jurors are told to vote for the finalist who played the best game over the whole season, judged on that record, not on the final pitch alone and not on a grudge. Play with that jury in mind from round one.",
]
AGENT_PUBLIC_KINDS = {
    "merge",
    "challenge_intro",
    "challenge_attempts",
    "challenge_result",
    "challenge_gift",
    "challenge_solver_spotlight",
    "establishing",
    "conversation",
    "host_question",
    "tribal_answer",
    "vote_reveal",
    "elimination",
    "memory_update",
    "finale_intro",
    "finale_pitch",
    "jury_questions",
    "jury_vote",
    "winner_declared",
}
AGENT_PRIVATE_KINDS = {"confessional", "exit_confessional", "vote_booth"}


def build_agent_episode_context(
    conn,
    *,
    actor_id: str,
    round_number: int,
    current_step: str,
) -> dict[str, Any]:
    prior_events = _prior_events(conn, round_number)
    eliminated_rounds = _eliminated_rounds(prior_events)
    public_timeline = [
        _agent_public_event(event)
        for event in prior_events
        if _is_public_event(event)
    ]
    static_private_memory = _actor_static_memory(conn, actor_id)
    event_private_memory = [
        _actor_private_event(event, public=_is_public_event(event))
        for event in prior_events
        if actor_id in event["actor_ids"]
        and not _participants_only_event(event)  # those arrive once, via private_group_memory
        and (event["kind"] in AGENT_PRIVATE_KINDS or event.get("inner_thought"))
    ]
    private_group_memory = [
        _actor_group_event(event)
        for event in prior_events
        if _participants_only_event(event) and actor_id in _participant_ids(event)
    ]
    alliance_memory = _actor_alliance_memory(conn, actor_id)
    actor_private_memory = static_private_memory + alliance_memory + _tail([*event_private_memory, *private_group_memory])
    public_timeline = _tail(public_timeline)
    context = {
        "visibility": "contestant_public_plus_own",
        "current_step": current_step,
        "game_rules": GAME_RULES,
        "season_context": _season_context(conn, round_number),
        "vote_history": _vote_history(conn, prior_events, eliminated_rounds),
        "challenge_history": _challenge_history(conn),
        "eliminations": _eliminations(conn),
        # private: promises this player made, and promises made to them, with kept/broken
        "your_promises": _your_promises(conn, actor_id),
        # private: this round's plan, and how well past vote predictions held up
        "your_plan_this_round": _your_plan(conn, actor_id, round_number),
        "your_predictions": _your_predictions(conn, actor_id),
        "visible_game_state": _visible_game_state(conn, current_step),
        "public_timeline": public_timeline,
        "actor_private_memory": actor_private_memory,
        "context_digest": {
            "visibility": "contestant_public_plus_own",
            "public_events": len(public_timeline),
            "actor_private_events": len(actor_private_memory),
            "private_group_events": len(private_group_memory),
            "active_alliances": len(alliance_memory),
            "revealed_votes": _revealed_vote_count(conn),
            "season_events_seen": len(prior_events),
        },
    }
    return _fit_budget(context, protected_private=len(static_private_memory) + len(alliance_memory))


def build_host_episode_context(
    conn,
    *,
    round_number: int,
    current_step: str,
) -> dict[str, Any]:
    prior_events = _prior_events(conn, round_number)
    host_timeline = _tail([_host_event(event) for event in prior_events])
    return {
        "visibility": "host_omniscient_nonspoiling",
        "current_step": current_step,
        "season_context": _season_context(conn, round_number),
        "visible_game_state": _visible_game_state(conn, current_step),
        "host_timeline": host_timeline,
        "context_digest": {
            "visibility": "host_omniscient_nonspoiling",
            "host_events": len(host_timeline),
            "revealed_votes": _revealed_vote_count(conn),
            "season_events_seen": len(prior_events),
        },
    }


def context_digest(context: dict[str, Any]) -> dict[str, Any]:
    return dict(context.get("context_digest", {}))


def _prior_events(conn, round_number: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM StoryEvents
        WHERE round <= ?
        ORDER BY round, sequence
        """,
        (round_number,),
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def _actor_static_memory(conn, actor_id: str) -> list[dict[str, Any]]:
    row = conn.execute(
        "SELECT confessional_memory, idol_held FROM Agents WHERE agent_id = ?",
        (actor_id,),
    ).fetchone()
    if row is None:
        return []
    memory: list[dict[str, Any]] = []
    if row["confessional_memory"]:
        memory.append({"kind": "confessional_memory", "actor_id": actor_id, "memory": row["confessional_memory"]})
    if row["idol_held"]:
        # only the holder ever sees this
        memory.append(
            {
                "kind": "hidden_idol",
                "actor_id": actor_id,
                "memory": "You hold the hidden idol. To play it, set play_idol to true when you vote; every vote against you that round is then cancelled. It expires when five players remain.",
            }
        )
    return memory


def _agent_public_event(event: dict[str, Any]) -> dict[str, Any]:
    summary = _base_event(event)
    if event["kind"] == "vote_reveal":
        summary["revealed_vote"] = event["dialogue"]
        summary["tally"] = event.get("subtitle")
    if event["kind"] == "elimination":
        summary["result"] = event.get("subtitle")
    return summary


def _is_public_event(event: dict[str, Any]) -> bool:
    return event["kind"] in AGENT_PUBLIC_KINDS and not _participants_only_event(event)


def _actor_private_event(event: dict[str, Any], *, public: bool = False) -> dict[str, Any]:
    if public:
        # the event itself is already in public_timeline; add only what is private
        summary = {"round": event["round"], "sequence": event["sequence"], "kind": event["kind"]}
        if event.get("inner_thought"):
            summary["your_private_thought"] = event["inner_thought"]
        return summary
    summary = _base_event(event)
    if event.get("inner_thought"):
        summary["your_private_thought"] = event["inner_thought"]
    if event["kind"] == "vote_booth":
        summary["your_vote"] = event.get("subtitle")
    return summary


def _host_event(event: dict[str, Any]) -> dict[str, Any]:
    summary = _base_event(event)
    if event["kind"] == "vote_booth":
        summary["dialogue"] = f"{_actor_label(event)} cast a vote. Target hidden until reveal."
        summary.pop("target_ids", None)
        summary.pop("subtitle", None)
        return summary
    if event.get("inner_thought"):
        summary["private_thought"] = event["inner_thought"]
    if event["kind"] == "vote_reveal":
        summary["revealed_vote"] = event["dialogue"]
        summary["tally"] = event.get("subtitle")
    return summary


def _actor_group_event(event: dict[str, Any]) -> dict[str, Any]:
    summary = _base_event(event)
    payload = event.get("payload") or {}
    summary["privacy"] = "participants_only"
    summary["participant_ids"] = _participant_ids(event)
    summary["group_summary"] = payload.get("group_summary")
    summary["speaker_lines"] = [
        {"agent_id": line.get("agent_id"), "text": line.get("text")}
        for line in payload.get("speaker_lines", [])
        if isinstance(line, dict) and isinstance(line.get("agent_id"), str) and isinstance(line.get("text"), str)
    ]
    if payload.get("alliance_id"):
        summary["alliance_id"] = payload["alliance_id"]
        summary["alliance_status"] = payload.get("alliance_status")
    return summary


def _base_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "round": event["round"],
        "sequence": event["sequence"],
        "kind": event["kind"],
        "title": event["title"],
        "actor_ids": event["actor_ids"],
        "target_ids": event["target_ids"],
        "dialogue": event["dialogue"],
        "subtitle": event.get("subtitle"),
    }


def _participants_only_event(event: dict[str, Any]) -> bool:
    payload = event.get("payload") or {}
    return payload.get("privacy") == "participants_only"


def _participant_ids(event: dict[str, Any]) -> list[str]:
    payload = event.get("payload") or {}
    participant_ids = payload.get("participant_ids")
    if isinstance(participant_ids, list):
        return [agent_id for agent_id in participant_ids if isinstance(agent_id, str)]
    return [agent_id for agent_id in event.get("actor_ids", []) if isinstance(agent_id, str)]


def _actor_alliance_memory(conn, actor_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT a.alliance_id, a.name, a.status, a.strength, a.summary, m.loyalty
        FROM Alliances a
        JOIN AllianceMemberships m ON m.alliance_id = a.alliance_id
        WHERE m.agent_id = ? AND m.status = 'active' AND a.status = 'active'
        ORDER BY a.updated_at DESC
        """,
        (actor_id,),
    ).fetchall()
    memories = []
    for row in rows:
        members = [
            member["agent_id"]
            for member in conn.execute(
                """
                SELECT agent_id
                FROM AllianceMemberships
                WHERE alliance_id = ? AND status = 'active'
                ORDER BY agent_id
                """,
                (row["alliance_id"],),
            ).fetchall()
        ]
        memories.append(
            {
                "kind": "active_alliance",
                "alliance_id": row["alliance_id"],
                "name": row["name"],
                "member_ids": members,
                "strength": row["strength"],
                "loyalty": row["loyalty"],
                "summary": row["summary"],
            }
        )
    return memories


def _actor_label(event: dict[str, Any]) -> str:
    return ", ".join(event.get("actor_ids") or ["unknown"])


def _tail(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return items[-MAX_CONTEXT_EVENTS:]


def _season_context(conn, round_number: int) -> dict[str, Any]:
    counts = conn.execute(
        """
        SELECT
            SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active,
            SUM(CASE WHEN status != 'active' THEN 1 ELSE 0 END) AS eliminated
        FROM Agents
        """
    ).fetchone()
    return {
        "current_round": round_number,
        "players_left": int(counts["active"] or 0),
        "players_eliminated": int(counts["eliminated"] or 0),
    }


def _names_by_id(conn) -> dict[str, str]:
    return {row["agent_id"]: row["pseudonym"] for row in conn.execute("SELECT agent_id, pseudonym FROM Agents")}


def _eliminated_rounds(prior_events: list[dict[str, Any]]) -> set[int]:
    return {event["round"] for event in prior_events if event["kind"] == "elimination"}


def _vote_history(conn, prior_events: list[dict[str, Any]], eliminated_rounds: set[int]) -> list[dict[str, Any]]:
    """Who voted for whom, round by round: only votes already revealed to everyone.

    First-ballot votes come from Votes (revealed = 1). Revote ballots exist only
    as revote_booth events, which are written while the revote is still running,
    so they are shown only for rounds whose elimination has happened.
    """
    names = _names_by_id(conn)
    rounds: dict[int, dict[str, Any]] = {}

    def entry(round_number: int) -> dict[str, Any]:
        return rounds.setdefault(round_number, {"round": round_number, "votes": [], "revote": [], "eliminated": None})

    for row in conn.execute(
        "SELECT round, voter_id, target_id FROM Votes WHERE revealed = 1 ORDER BY round, id"
    ).fetchall():
        if row["voter_id"] == "host":
            continue  # walkover: the last non-immune player left without a vote
        entry(row["round"])["votes"].append(
            {"voter": names.get(row["voter_id"], row["voter_id"]), "target": names.get(row["target_id"], row["target_id"])}
        )
    for event in prior_events:
        if event["kind"] == "revote_booth" and event["round"] in eliminated_rounds:
            target_id = (event.get("payload") or {}).get("vote_target_id")
            voter_id = (event.get("actor_ids") or [None])[0]
            if target_id and voter_id:
                entry(event["round"])["revote"].append(
                    {"voter": names.get(voter_id, voter_id), "target": names.get(target_id, target_id)}
                )
        elif event["kind"] == "elimination":
            eliminated_ids = event.get("target_ids") or []
            if eliminated_ids:
                entry(event["round"])["eliminated"] = names.get(eliminated_ids[0], eliminated_ids[0])
    return [rounds[key] for key in sorted(rounds)]


def _challenge_history(conn) -> list[dict[str, Any]]:
    names = _names_by_id(conn)
    history = []
    idol_rounds = set()
    for row in conn.execute("SELECT round, payload FROM StoryEvents WHERE kind = 'challenge_gift'").fetchall():
        try:
            if json.loads(row["payload"] or "{}").get("idol_given"):
                idol_rounds.add(row["round"])
        except (TypeError, ValueError):
            continue
    for row in conn.execute("SELECT * FROM ChallengeResults ORDER BY round").fetchall():
        try:
            immune_ids = json.loads(row["immunity_agent_ids"] or "[]")
            result = json.loads(row["result_payload"] or "{}")
        except (TypeError, ValueError):
            immune_ids, result = [], {}
        scores = {
            names.get(agent_id, agent_id): score.get("correct", 0)
            for agent_id, score in (result.get("scores") or {}).items()
        }
        history.append(
            {
                "round": row["round"],
                "winner": names.get(row["winning_agent_id"], row["winning_agent_id"]) if row["winning_agent_id"] else None,
                "immune": [names.get(agent_id, agent_id) for agent_id in immune_ids],
                "scores_out_of_5": scores,
                "idol_given_by_winner": row["round"] in idol_rounds,
            }
        )
    return history


def _your_plan(conn, actor_id: str, round_number: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT plan FROM PrivatePlans WHERE agent_id = ? AND round = ?", (actor_id, round_number)
    ).fetchone()
    if row is None:
        return None
    plan = json.loads(row["plan"] or "{}")
    if plan.get("expected_out"):
        plan["expected_out"] = _names_by_id(conn).get(plan["expected_out"], plan["expected_out"])
    return plan


def _your_predictions(conn, actor_id: str) -> dict[str, Any]:
    from .turn_controller import prediction_record

    return prediction_record(conn, actor_id)


def _your_promises(conn, actor_id: str) -> dict[str, list[dict[str, Any]]]:
    from .turn_controller import promise_record

    return {
        "made_by_you": promise_record(conn, actor_id),
        "made_to_you": promise_record(conn, actor_id, as_listener=True),
    }


def _eliminations(conn) -> list[dict[str, Any]]:
    return [
        {"round": row["elimination_round"], "name": row["pseudonym"]}
        for row in conn.execute(
            """
            SELECT pseudonym, elimination_round FROM Agents
            WHERE status != 'active' AND elimination_round IS NOT NULL
            ORDER BY elimination_round
            """
        ).fetchall()
    ]


def _fit_budget(context: dict[str, Any], *, protected_private: int) -> dict[str, Any]:
    """Drop the oldest timeline events, then the oldest private events, until the
    context fits. The first `protected_private` private entries (the player's own
    memory and alliances) and every ledger are kept."""
    def encoded_len(value: Any) -> int:
        return len(json.dumps(value, separators=(",", ":"), sort_keys=True, default=str))

    timeline = context["public_timeline"]
    private = context["actor_private_memory"]
    # measure once, then subtract each dropped entry (plus its comma) instead of
    # re-encoding the whole context after every drop
    total = encoded_len(context)
    trimmed = 0
    def position(entry: dict[str, Any]) -> tuple[int, int]:
        return (int(entry.get("round") or 0), int(entry.get("sequence") or 0))

    while total > MAX_CONTEXT_CHARS and (timeline or len(private) > protected_private):
        # drop whichever remaining event is oldest, public or private
        take_private = len(private) > protected_private and (
            not timeline or position(private[protected_private]) < position(timeline[0])
        )
        dropped = private.pop(protected_private) if take_private else timeline.pop(0)
        total -= encoded_len(dropped) + 1
        trimmed += 1
    context["context_digest"]["trimmed_events"] = trimmed
    return context


def _visible_game_state(conn, current_step: str) -> dict[str, Any]:
    agents = [
        {
            "agent_id": row["agent_id"],
            # in-game name only: players never see which model another player is
            "name": row["pseudonym"],
            "status": row["status"],
            "has_immunity": bool(row["has_immunity"]),
        }
        for row in conn.execute("SELECT * FROM Agents ORDER BY agent_id").fetchall()
    ]
    return {
        "current_step": current_step,
        "agents": agents,
    }


def _revealed_vote_count(conn) -> int:
    row = conn.execute("SELECT COUNT(*) AS count FROM Votes WHERE revealed = 1").fetchone()
    return int(row["count"])
