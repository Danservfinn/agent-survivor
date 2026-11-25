from __future__ import annotations

import json
from typing import Any

from .database import row_to_dict
from .llm_config import prompt_profile

MAX_CONTEXT_EVENTS = 60
# Upper bound on a serialized contestant context. When over it, the oldest
# timeline and private events are dropped first; the ledgers and the rules,
# which are the verified record of the game, are never trimmed.
MAX_CONTEXT_CHARS = 40_000

# One statement of the rules every player reads. Keep it in step with the engine
# in turn_controller.py: players can only plan around rules they are told.
GAME_RULES = [
    "All players live and vote as one group for the whole season; there are no tribes.",
    "Each round has a challenge: the same five puzzles for every player; the difficulty rises when half the field solves them all and falls when nobody reaches three. Most correct answers wins; a tie goes to whoever solved the harder puzzles, then to a guessing game played in the same answer: every player names a whole number from 0 to 100 and the tied player closest to two-thirds of the average wins; if still tied, nobody wins. Speed does not count. Every player's score is public (challenge_history).",
    "The challenge winner is immune: they cannot be voted for that round, but they still vote. A player may answer puzzles wrong on purpose to look less threatening.",
    "In the first two rounds of the season the challenge winner must give a hidden idol to another player. Everyone learns that an idol was given; only the giver and the holder know who has it. Only one idol is in play at a time.",
    "A holder plays the idol with their vote at a council; every vote against them that round is then cancelled, and the play is announced. An unplayed idol expires when five players remain.",
    "Each voter casts one secret vote for an eligible player. After everyone votes, the votes are revealed one by one and stay public: every player sees who voted for whom (vote_history).",
    "The player with the most votes leaves. On a tie there is a revote among the tied players only; tied players cannot vote for themselves.",
    "If the revote is also tied, the tied player with the fewest challenge wins this season leaves; if still level, the one who has received the most votes this season leaves.",
    "Alliances are declared, never assumed: in a private talk each player may declare a pact, and an alliance forms only when every player in the talk ends it by declaring one. Your active alliances are listed in your private memory, and the jury sees them.",
    "Each round, before the vote, every player picks one other player for a private talk; only the two of them hear it. A promise to vote for someone, made in a talk, is recorded and later checked against the revealed vote (your_promises).",
    "Each round you privately write a plan after the challenge, privately predict the vote when you cast yours, and rewrite your private notebook after the council. Only you see these; your prediction accuracy is shown to you (your_predictions).",
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
# Host framing that carries no strategic fact; dropped from players' timeline (v3)
LOW_VALUE_PUBLIC_KINDS = {
    "challenge_intro",
    "challenge_attempts",
    "challenge_solver_spotlight",
    "memory_update",
    "finale_intro",
    "establishing",
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
    v3 = prompt_profile() == "v3"
    public_timeline = [
        _agent_public_event(event)
        for event in prior_events
        if _is_public_event(event) and not (v3 and event["kind"] in LOW_VALUE_PUBLIC_KINDS)
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
    context: dict[str, Any] = {}
    if v3:
        # first in the prompt: facts the engine computed, so the model does not
        # have to reconstruct the state of the game from the raw log
        context["situation_brief"] = _situation_brief(conn, actor_id, round_number)
        context["what_others_told_you"] = _claims_to_you(conn, actor_id, round_number, prior_events)
    context |= {
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
            # only events this player can see; counting every event would reveal
            # how many private talks happened elsewhere
            "season_events_seen": sum(
                1
                for event in prior_events
                if _is_public_event(event)
                or (actor_id in event["actor_ids"] and not _participants_only_event(event))
                or (_participants_only_event(event) and actor_id in _participant_ids(event))
            ),
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
        -- by game round, then newest: wall-clock order changed with call speed
        ORDER BY COALESCE(m.last_reinforced_round, m.joined_round) DESC, a.rowid DESC
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
                "ladder_level": result.get("ladder_level", 0),
                # everyone's number in the guessing game, public after the round
                "guesses": {names.get(agent_id, agent_id): guess for agent_id, guess in (result.get("guesses") or {}).items()},
                "two_thirds_of_average": result.get("guess_target"),
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


def _situation_brief(conn, actor_id: str, round_number: int) -> dict[str, Any]:
    """The facts a strategist needs, computed by the engine from the tables:
    the vote math tonight, who can be targeted, who votes together, who has
    voted against you, each player's promise record and threat level, and who
    sits on the jury. Everything here is verified; nothing is model text."""
    from .turn_controller import promise_record

    names = _names_by_id(conn)
    agents = [dict(row) for row in conn.execute("SELECT * FROM Agents ORDER BY agent_id").fetchall()]
    active = [agent for agent in agents if agent["status"] == "active"]
    me = next((agent for agent in agents if agent["agent_id"] == actor_id), None)
    others = [agent for agent in active if agent["agent_id"] != actor_id]

    votes = conn.execute(
        "SELECT round, voter_id, target_id FROM Votes WHERE revealed = 1 AND voter_id != 'host' ORDER BY round, id"
    ).fetchall()
    by_round: dict[int, dict[str, str]] = {}
    for vote in votes:
        by_round.setdefault(vote["round"], {})[vote["voter_id"]] = vote["target_id"]

    # pairs of active players who voted for the same target in the same council
    active_ids = [agent["agent_id"] for agent in active]
    pair_counts: dict[tuple[str, str], int] = {}
    for ballots in by_round.values():
        for i, first in enumerate(active_ids):
            for second in active_ids[i + 1 :]:
                if first in ballots and ballots.get(first) == ballots.get(second):
                    key = (first, second)
                    pair_counts[key] = pair_counts.get(key, 0) + 1
    voting_together = [
        {"players": [names[a], names[b]], "councils_voting_together": count}
        for (a, b), count in sorted(pair_counts.items(), key=lambda item: -item[1])
        if count >= 1
    ][:8]

    votes_against_you = [
        {"round": round_number_, "by": names.get(voter, voter)}
        for round_number_, ballots in sorted(by_round.items())
        for voter, target in ballots.items()
        if target == actor_id
    ]

    challenge_totals: dict[str, dict[str, int]] = {}
    for row in conn.execute("SELECT winning_agent_id, result_payload FROM ChallengeResults").fetchall():
        payload = json.loads(row["result_payload"] or "{}")
        for agent_id, score in (payload.get("scores") or {}).items():
            total = challenge_totals.setdefault(agent_id, {"correct": 0, "wins": 0})
            total["correct"] += int(score.get("correct", 0))
        if row["winning_agent_id"]:
            challenge_totals.setdefault(row["winning_agent_id"], {"correct": 0, "wins": 0})["wins"] += 1

    players = []
    for agent in others:
        record = promise_record(conn, agent["agent_id"])
        settled = [entry for entry in record if entry["status"] != "open"]
        to_you = [entry for entry in settled if entry["promised_to"] == names.get(actor_id)]
        with_you = sum(
            1 for ballots in by_round.values() if actor_id in ballots and ballots.get(agent["agent_id"]) == ballots[actor_id]
        )
        against_you = sum(1 for ballots in by_round.values() if ballots.get(agent["agent_id"]) == actor_id)
        totals = challenge_totals.get(agent["agent_id"], {"correct": 0, "wins": 0})
        players.append(
            {
                "name": names[agent["agent_id"]],
                "id": agent["agent_id"],
                "immune_tonight": bool(agent["has_immunity"]),
                "voted_with_you": with_you,
                "voted_against_you": against_you,
                "promises_kept": sum(1 for entry in settled if entry["status"] == "kept"),
                "promises_broken": sum(1 for entry in settled if entry["status"] == "broken"),
                "promises_to_you_broken": sum(1 for entry in to_you if entry["status"] == "broken"),
                "challenge_correct": totals["correct"],
                "challenge_wins": totals["wins"],
            }
        )
    players.sort(key=lambda player: (-player["challenge_wins"], -player["challenge_correct"]))

    voters = len(active)
    jury = []
    for agent in agents:
        if agent["status"] == "active" or agent["elimination_round"] is None:
            continue
        ballots = by_round.get(agent["elimination_round"], {})
        jury.append(
            {
                "name": names[agent["agent_id"]],
                "out_in_round": agent["elimination_round"],
                "voted_out_by": [names.get(voter, voter) for voter, target in ballots.items() if target == agent["agent_id"]],
                "you_voted_them_out": ballots.get(actor_id) == agent["agent_id"],
            }
        )
    return {
        "you": {
            "name": names.get(actor_id),
            "immune_tonight": bool(me and me["has_immunity"]),
            "challenge_correct": challenge_totals.get(actor_id, {}).get("correct", 0),
            "challenge_wins": challenge_totals.get(actor_id, {}).get("wins", 0),
        },
        "tonight": {
            "round": round_number,
            "players_left": len(active),
            "voters": voters,
            "votes_for_a_majority": voters // 2 + 1,
            "immune": [names[agent["agent_id"]] for agent in active if agent["has_immunity"]],
            "can_be_voted_out": [names[agent["agent_id"]] for agent in active if not agent["has_immunity"]],
            "finale_when_players_left": 3,
        },
        "other_players_by_threat": players,
        "voting_together": voting_together,
        "votes_against_you": votes_against_you,
        "jury_so_far": jury,
    }


def _claims_to_you(
    conn, actor_id: str, round_number: int, prior_events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """What other players said to this player in private this round and last,
    next to each speaker's promise record, so a claim can be weighed."""
    from .turn_controller import promise_record

    names = _names_by_id(conn)
    records: dict[str, dict[str, int]] = {}

    def record_of(agent_id: str) -> dict[str, int]:
        if agent_id not in records:
            settled = [entry for entry in promise_record(conn, agent_id) if entry["status"] != "open"]
            records[agent_id] = {
                "promises_kept": sum(1 for entry in settled if entry["status"] == "kept"),
                "promises_broken": sum(1 for entry in settled if entry["status"] == "broken"),
            }
        return records[agent_id]

    claims = []
    for event in prior_events:
        if event["round"] < round_number - 1 or not _participants_only_event(event):
            continue
        if actor_id not in _participant_ids(event):
            continue
        for line in (event.get("payload") or {}).get("speaker_lines", []):
            speaker = line.get("agent_id")
            if not isinstance(speaker, str) or speaker == actor_id or not line.get("text"):
                continue
            claims.append(
                {
                    "round": event["round"],
                    "from": names.get(speaker, speaker),
                    "said": str(line["text"])[:300],
                    **record_of(speaker),
                }
            )
    return claims[-16:]


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


PROTECTED_PRIVATE_KINDS = {"confessional_memory", "hidden_idol", "active_alliance"}


def add_open_promise(context: dict[str, Any], entry: dict[str, Any], *, made_by_you: bool) -> dict[str, Any]:
    """Add a promise made earlier in the current talk to a context built before
    the talk, exactly as rebuilding the context from the database would show it
    (an open entry at the end of the list), then refit the size budget. Used when
    talks run in parallel and contexts are built once per talk."""
    promises = context.get("your_promises")
    if promises is None:
        return context
    promises["made_by_you" if made_by_you else "made_to_you"].append(entry)
    private = context.get("actor_private_memory") or []
    protected = 0
    while protected < len(private) and private[protected].get("kind") in PROTECTED_PRIVATE_KINDS:
        protected += 1
    already = context["context_digest"].get("trimmed_events", 0)
    _fit_budget(context, protected_private=protected)
    context["context_digest"]["trimmed_events"] += already
    return context


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
