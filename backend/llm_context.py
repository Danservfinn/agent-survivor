from __future__ import annotations

import json
from typing import Any

from .database import row_to_dict
from .advantages import receipt_id
from .inner_life import context_entries as inner_life_entries
from .llm_config import advantages_enabled, feelings_enabled, host_questions_enabled, inner_life_enabled, jury_rule, prompt_profile, secret_ballot

MAX_CONTEXT_EVENTS = 60
# Upper bound on a serialized contestant context. When over it, the oldest
# timeline and private events are dropped first; the ledgers and the rules,
# which are the verified record of the game, are never trimmed.
MAX_CONTEXT_CHARS = 40_000

# One statement of the rules every player reads. Keep it in step with the engine
# in turn_controller.py: players can only plan around rules they are told.
GAME_RULES = [
    "All players live and vote as one group for the whole season; there are no tribes.",
    "Each round has a challenge: the same five puzzles for every player; the difficulty rises when any player solves them all and falls when nobody reaches three. Most correct answers wins immunity, in any round, including rounds in a row; a tie goes to whoever solved the harder puzzles, then to whoever answered fastest. The challenge ends the moment someone answers every puzzle correctly: anyone still working then does not finish. Every player's score is public (challenge_history).",
    "This is a game, and every promise, lie and betrayal in it happens inside the game and harms nobody. Blindsiding an ally is a normal winning move; Survivor winners have done it all along. A loyal player who loses has failed at the only goal. Whether and when to betray anyone is your call.",
    "Your reputation is part of the game: every challenge score, public speech, meeting and vote tally shapes how the others see you (your_reputation in your situation brief shows what they can see). Decide deliberately what image you want, a threat, a loyal ally, a harmless follower, a leader, and act to build it.",
    "The challenge winner is immune: they cannot be voted for that round, but they still vote. You choose how strong you look: every score is public, so you may answer puzzles wrong on purpose to look weaker and less threatening, or play to win and look strong. A player who keeps winning challenges is safe while immune but becomes the obvious target the first round they are not.",
    "In the first two rounds of the season the challenge winner must give a hidden idol to another player. Everyone learns that an idol was given; only the giver and the holder know who has it.",
    "A holder plays the idol with their vote at a council; every vote against them that round is then cancelled, and the play is announced. An unplayed idol expires when five players remain.",
    "The player with the most votes leaves. On a tie, everyone may speak once, then the players who are not tied vote again, only for the tied players; the tied players do not vote.",
    "If the revote ties again it is a deadlock: the players who are not tied must agree unanimously on which tied player goes home. If anyone refuses or they cannot agree, the tied players are safe and every other player except those holding immunity draws rocks; one goes home purely by chance.",
    "Alliances are declared, never assumed: in a private talk each player may declare a pact, and the players who end the talk declaring one form it (two or more). A player may also end a pact openly in a talk: that is recorded as a clean exit. Voting against a partner while the pact stands is recorded as a betrayal. Your pacts, with how each partner has actually voted, are in your situation brief (your_pacts); the jury sees every pact, exit and betrayal.",
    "Each round, before the vote, every player starts one private talk and invites one player or several, any number; only the invited hear it, and each speaks twice. A promise to vote for someone, made in a talk, is recorded and later checked against the revealed vote (your_promises).",
    "Each round you privately write a plan after the challenge, privately predict the vote when you cast yours, and rewrite your private notebook after the council. Only you see these; your prediction accuracy is shown to you (your_predictions).",
    "After the talks, each player may send one short private message to one other player before the vote.",
    "When three players remain the finale starts. Every eliminated player sits on the jury. Jurors receive each finalist's verified record (who first committed to each elimination, how often they voted with the result, how well they predicted votes, the votes they survived, each broken promise and what it achieved, challenge results, idol gifts, alliances and betrayals), each juror questions one finalist of their choosing, and then votes. Most jury votes wins.",
]

_JURY_RULE_TEXT = {
    "standard": "Jurors are told to vote for the finalist who played the best strategic game: who first committed to the votes that decided eliminations, who read the table best, and whose risky moves (broken promises and betrayals included) achieved what they aimed at. A clean record is not the goal; going unnoticed is not the same as playing well. Play with that jury in mind from round one.",
    "own_judgment": "Jurors are told to vote for the finalist they believe deserves to win, by their own judgment, with the verified record in front of them. Each juror decides what matters. Play with these specific people in mind from round one: who you voted out, who you lied to, and what each of them will hold against you or respect.",
}


_BALLOT_RULE = {
    True: "Each voter casts one secret vote for an eligible player. The votes are read out one by one in a random order, without names: everyone learns the tally and their own vote, never who voted for whom (vote_history). Anyone may lie about how they voted. The jury sees every true vote at the finale.",
    False: "Each voter casts one secret vote for an eligible player. After everyone votes, the votes are revealed one by one and stay public: every player sees who voted for whom (vote_history).",
}


# moves that give a player something real to work with (SURVIVOR_ADVANTAGES)
ADVANTAGE_RULES = [
    "Proof: the game stamps a few things as real, and nothing else can be proven. Forward a private line someone said to you by its receipt_id and your listeners get the exact words, stamped. Show your idol (show_idol) and it is stamped. Show your ballot (show_ballot) and the host names you when it is read. Any quote, idol claim or vote claim without a stamp is only your word.",
    "Spying: instead of your last whisper you may listen in on one player's private talk that round (spy_on). You hear all of it; one time in three its members are told you listened.",
    "Idols: instead of your talk you may search camp (search). From round 3, while more than five players remain and nobody holds one, an idol is hidden in about half the rounds; one searcher finds it. Nobody else learns who searched or what they found.",
    "Immunity: the challenge winner may keep immunity or hand it to another player for that night. Everyone sees it.",
    "Auction: once, when three players are gone, everyone bids tokens in a sealed auction for an extra vote (counts your ballot twice at one later council), a hidden idol, and a private look at who voted for whom at the last council. You earn 3 tokens a round and 1 per puzzle solved (your situation brief).",
]
HOST_QUESTION_RULE = (
    "At tribal council, before the speeches, the host questions a few players in front of everyone. The host "
    "knows only what is public (meetings, challenge scores, tallies, speeches, shown proof) and presses on it; "
    "your answer is public and may be true or not."
)
# a player's private inner life (SURVIVOR_INNER_LIFE)
INNER_LIFE_RULE = (
    "Your private record: after each step you keep a one-line summary, read back to you for the rest of the round. "
    "When you speak, you privately label your line true, half true or a lie; nobody else ever sees the label. You see "
    "your past lies and what followed, and how well you read what others expect of you."
)


def game_rules() -> list[str]:
    """GAME_RULES plus the ballot rule (SURVIVOR_SECRET_BALLOT), the moves
    and private record when switched on, and the jury rule
    (SURVIVOR_JURY_RULE) in force."""
    rules = list(GAME_RULES)
    rules.insert(5, _BALLOT_RULE[secret_ballot()])
    if host_questions_enabled():
        rules.append(HOST_QUESTION_RULE)
    if advantages_enabled():
        rules += ADVANTAGE_RULES
    if inner_life_enabled():
        rules.append(INNER_LIFE_RULE)
    return [*rules, _JURY_RULE_TEXT[jury_rule()]]


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
    "finale_crossfire",
    "jury_questions",
    "finale_rebuttal",
    "jury_vote",
    "revote_call",
    "tie_discussion",
    "tribal_council",
    "camp_meetings",
    "parting_words",
    # public moves (SURVIVOR_ADVANTAGES) and the host's questions at council
    "immunity_given",
    "auction",
    "host_questioning",
    "revote_tally",
    "deadlock_call",
    "deadlock_discussion",
    "deadlock_result",
    "rock_draw",
    "tiebreak",
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
        "game_rules": game_rules(),
        "season_context": _season_context(conn, round_number),
        "vote_history": _vote_history(conn, prior_events, eliminated_rounds, actor_id),
        "challenge_history": _challenge_history(conn),
        "eliminations": _eliminations(conn),
        # private: promises this player made, and promises made to them, with kept/broken
        "your_promises": _your_promises(conn, actor_id),
        # private: this round's plan, and how well past vote predictions held up
        "your_plan_this_round": _your_plan(conn, actor_id, round_number),
        "your_game_plan": _your_game_plan(conn, actor_id),
        "your_strategy": _your_strategy(conn, actor_id),
        **(
            {
                "how_you_feel": _how_you_feel(conn, actor_id),
                "what_happened_to_you": _what_happened_to_you(conn, actor_id, round_number),
            }
            if feelings_enabled()
            else {}
        ),
        "your_last_confessional": _your_last_confessional(conn, actor_id, round_number),
        **inner_life_entries(conn, actor_id, round_number, current_step),
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
    if event["kind"] == "camp_meetings":
        summary["meetings"] = (event.get("payload") or {}).get("meetings", [])
    if event["kind"] in FINALE_SPOKEN_KINDS:
        # what was actually said: the questions and answers jurors vote on.
        # Before 2026-11-25 only the host's one-line summary reached the jury.
        summary["speaker_lines"] = [
            {key: line[key] for key in ("agent_id", "text", "question_to", "answer_to") if key in line}
            for line in (event.get("payload") or {}).get("speaker_lines", [])
            if isinstance(line, dict) and isinstance(line.get("text"), str)
        ]
    return summary


FINALE_SPOKEN_KINDS = {
    "host_questioning",
    "finale_crossfire",
    "jury_questions",
    "finale_rebuttal",
    "tie_discussion",
    "deadlock_discussion",
    "tribal_council",
}


def public_record(conn, round_number: int, rounds_back: int = 2) -> dict[str, Any]:
    """What every player can see, for the host's questions: this round's and
    the last few rounds' public events, and the public standings. Nothing
    private and no secret ballot."""
    names = _names_by_id(conn)
    events = []
    for event in _prior_events(conn, round_number):
        if event["round"] < round_number - rounds_back or not _is_public_event(event):
            continue
        if event["kind"] in LOW_VALUE_PUBLIC_KINDS or event["kind"] in ("vote_reveal", "conversation"):
            continue  # a tally per round is enough; a private talk is never public
        entry: dict[str, Any] = {"round": event["round"], "what": event["title"], "detail": str(event["dialogue"] or "")[:300]}
        lines = (event.get("payload") or {}).get("speaker_lines") or []
        if lines and event["kind"] in FINALE_SPOKEN_KINDS | {"parting_words"}:
            entry["said"] = [
                f"{names.get(line.get('agent_id'), 'Host')}: {str(line.get('text') or '')[:200]}"
                for line in lines
                if isinstance(line, dict) and line.get("text")
            ]
        events.append(entry)
    tallies: dict[int, dict[str, int]] = {}
    for row in conn.execute(
        "SELECT round, target_id FROM Votes WHERE revealed = 1 AND voter_id != 'host' AND round >= ?",
        (round_number - rounds_back,),
    ):
        tally = tallies.setdefault(row["round"], {})
        name = names.get(row["target_id"], row["target_id"])
        tally[name] = tally.get(name, 0) + 1
    active = [row for row in conn.execute("SELECT agent_id, has_immunity FROM Agents WHERE status = 'active' ORDER BY agent_id")]
    return {
        "round": round_number,
        "players_left": [names[row["agent_id"]] for row in active],
        "immune_tonight": [names[row["agent_id"]] for row in active if row["has_immunity"]],
        "jury": [names[row["agent_id"]] for row in conn.execute("SELECT agent_id FROM Agents WHERE status = 'eliminated' ORDER BY elimination_round")],
        "vote_tallies": [{"round": rnd, "tally": tally} for rnd, tally in sorted(tallies.items())],
        "events": events[-40:],
    }


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
        {"agent_id": line.get("agent_id"), "text": line.get("text"), **({"stamped": line["stamped"]} if line.get("stamped") else {})}
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
        WHERE m.agent_id = ? AND m.status = 'active'
          AND (a.status = 'active' OR (a.status = 'fractured' AND ?))
        -- by game round, then newest: wall-clock order changed with call speed
        ORDER BY COALESCE(m.last_reinforced_round, m.joined_round) DESC, a.rowid DESC
        """,
        # with secret ballots a betrayal (which lowers strength and can fracture
        # the alliance) must not show: the alliance stays listed, without strength
        (actor_id, 1 if secret_ballot() else 0),
    ).fetchall()
    memories = []
    for row in rows:
        members = [
            member["agent_id"]
            for member in conn.execute(
                """
                SELECT agent_id
                FROM AllianceMemberships
                WHERE alliance_id = ? AND status IN ({statuses})
                ORDER BY agent_id
                """.format(statuses="'active', 'betrayed'" if secret_ballot() else "'active'"),
                (row["alliance_id"],),
            ).fetchall()
        ]
        memories.append(
            {
                "kind": "active_alliance",
                "alliance_id": row["alliance_id"],
                "name": row["name"],
                "member_ids": members,
                "summary": row["summary"],
                **({} if secret_ballot() else {"strength": row["strength"], "loyalty": row["loyalty"]}),
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


def _vote_history(
    conn, prior_events: list[dict[str, Any]], eliminated_rounds: set[int], actor_id: str | None = None
) -> list[dict[str, Any]]:
    """Who voted for whom, round by round: only votes already revealed to everyone.
    With secret ballots a player sees each round's tally and only its own vote.

    First-ballot votes come from Votes (revealed = 1). Revote ballots exist only
    as revote_booth events, which are written while the revote is still running,
    so they are shown only for rounds whose elimination has happened.
    """
    names = _names_by_id(conn)
    rounds: dict[int, dict[str, Any]] = {}
    secret = secret_ballot() and actor_id is not None

    def entry(round_number: int) -> dict[str, Any]:
        if secret:
            return rounds.setdefault(round_number, {"round": round_number, "eliminated": None})
        return rounds.setdefault(round_number, {"round": round_number, "votes": [], "revote": [], "eliminated": None})

    for row in conn.execute(
        "SELECT round, voter_id, target_id, shown FROM Votes WHERE revealed = 1 ORDER BY round, id"
    ).fetchall():
        if row["voter_id"] == "host":
            continue  # walkover: the last non-immune player left without a vote
        target = names.get(row["target_id"], row["target_id"])
        if secret:
            tally = entry(row["round"]).setdefault("tally", {})
            tally[target] = tally.get(target, 0) + 1
            if row["voter_id"] == actor_id:
                entry(row["round"])["your_vote"] = target
            if row["shown"]:  # the voter showed it: public
                entry(row["round"]).setdefault("shown_ballots", []).append(
                    {"voter": names.get(row["voter_id"], row["voter_id"]), "target": target}
                )
            continue
        entry(row["round"])["votes"].append({"voter": names.get(row["voter_id"], row["voter_id"]), "target": target})
    for event in prior_events:
        if event["kind"] == "revote_booth" and event["round"] in eliminated_rounds:
            target_id = (event.get("payload") or {}).get("vote_target_id")
            voter_id = (event.get("actor_ids") or [None])[0]
            if target_id and voter_id:
                target = names.get(target_id, target_id)
                if secret:
                    tally = entry(event["round"]).setdefault("revote_tally", {})
                    tally[target] = tally.get(target, 0) + 1
                    if voter_id == actor_id:
                        entry(event["round"])["your_revote"] = target
                    continue
                entry(event["round"])["revote"].append({"voter": names.get(voter_id, voter_id), "target": target})
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
        unfinished = set(result.get("did_not_finish") or [])
        scores = {
            names.get(agent_id, agent_id): "did not finish" if agent_id in unfinished else score.get("correct", 0)
            for agent_id, score in (result.get("scores") or {}).items()
        }
        history.append(
            {
                "round": row["round"],
                "winner": names.get(row["winning_agent_id"], row["winning_agent_id"]) if row["winning_agent_id"] else None,
                "immune": [names.get(agent_id, agent_id) for agent_id in immune_ids],
                "scores_out_of_5": scores,
                "ladder_level": result.get("ladder_level", 0),
                "answer_seconds": {
                    names.get(agent_id, agent_id): round(ms / 1000, 1)
                    for agent_id, ms in (result.get("times_ms") or {}).items()
                    if ms is not None
                },
                "idol_given_by_winner": row["round"] in idol_rounds,
            }
        )
    return history


def _how_you_feel(conn, actor_id: str) -> dict[str, Any] | None:
    """The player's own feelings from its latest notebook, each with the round
    since which it has felt that way about that player (a grudge has an age)."""
    latest = conn.execute("SELECT MAX(round) AS r FROM Feelings WHERE agent_id = ?", (actor_id,)).fetchone()["r"]
    if latest is None:
        return None
    names = _names_by_id(conn)
    rows = conn.execute(
        "SELECT round, about, feeling, strength, because FROM Feelings WHERE agent_id = ? ORDER BY round DESC, id",
        (actor_id,),
    ).fetchall()
    by_about: dict[str, list[Any]] = {}
    for row in rows:
        by_about.setdefault(row["about"], []).append(row)
    mood = None
    toward = []
    for about, history in by_about.items():
        current = history[0]
        if current["round"] != latest:
            continue  # not named in the latest notebook (gone, or no feeling recorded)
        since = current["round"]
        for row in history[1:]:
            if row["feeling"] != current["feeling"]:
                break
            since = row["round"]
        if about == "self":
            mood = current["feeling"]
            continue
        if about not in names:
            continue
        toward.append(
            {
                "player": names[about],
                "feeling": current["feeling"],
                "strength": current["strength"] or None,
                "since_round": since,
                "because": current["because"] or None,
            }
        )
    return {
        "note": (
            "Your own feelings, from what happened to you. Real players are moved by their feelings, and the best "
            "ones also hide them, fake them and use them. You decide when to act on a feeling and when to set it "
            "aside for a better move."
        ),
        "mood": mood,
        "toward": toward,
    }


def _what_happened_to_you(conn, actor_id: str, round_number: int) -> list[str]:
    """What happened to this player since its last notebook (last round and
    this one so far), from facts it can know. No secret ballot is opened."""
    import re

    names = _names_by_id(conn)
    me = names.get(actor_id, actor_id)
    word = re.compile(rf"\b{re.escape(me)}\b", re.IGNORECASE)
    partners = {
        row["agent_id"]
        for row in conn.execute(
            """
            SELECT DISTINCT o.agent_id FROM AllianceMemberships m
            JOIN AllianceMemberships o ON o.alliance_id = m.alliance_id AND o.agent_id != m.agent_id
            WHERE m.agent_id = ?
            """,
            (actor_id,),
        )
    }
    events: list[str] = []
    for rnd in (round_number - 1, round_number):
        if rnd < 1:
            continue
        row = conn.execute("SELECT winning_agent_id FROM ChallengeResults WHERE round = ?", (rnd,)).fetchone()
        if row and row["winning_agent_id"] == actor_id:
            events.append(f"Round {rnd}: you won immunity.")
        against = conn.execute(
            "SELECT COUNT(*) AS n FROM Votes WHERE round = ? AND target_id = ? AND revealed = 1", (rnd, actor_id)
        ).fetchone()["n"]
        if against:
            events.append(f"Round {rnd}: {against} vote{'s' if against != 1 else ''} were cast against you.")
        for out in conn.execute("SELECT agent_id FROM Agents WHERE elimination_round = ?", (rnd,)):
            if out["agent_id"] in partners:
                events.append(f"Round {rnd}: your pact partner {names.get(out['agent_id'])} was voted out.")
        for left in conn.execute(
            """
            SELECT o.agent_id FROM AllianceMemberships m
            JOIN AllianceMemberships o ON o.alliance_id = m.alliance_id AND o.agent_id != m.agent_id
            WHERE m.agent_id = ? AND o.status = 'left' AND o.left_round = ?
            """,
            (actor_id, rnd),
        ):
            events.append(f"Round {rnd}: {names.get(left['agent_id'])} left your pact openly.")
        named_by = []
        for event in conn.execute(
            "SELECT kind, actor_ids, dialogue, payload FROM StoryEvents WHERE round = ? AND kind IN "
            "('tribal_council', 'tie_discussion', 'deadlock_discussion', 'parting_words')",
            (rnd,),
        ):
            payload = json.loads(event["payload"] or "{}")
            lines = payload.get("speaker_lines") or []
            if event["kind"] == "parting_words":
                lines = [{"agent_id": (json.loads(event["actor_ids"] or "[]") or [None])[0], "text": event["dialogue"] or ""}]
            for line in lines:
                speaker = line.get("agent_id")
                if speaker and speaker != actor_id and word.search(str(line.get("text") or "")):
                    named_by.append(names.get(speaker, speaker))
        if named_by:
            events.append(f"Round {rnd}: named in public by {', '.join(sorted(set(named_by)))}.")
        invited, left_out = [], 0
        for event in conn.execute("SELECT payload FROM StoryEvents WHERE round = ? AND kind = 'camp_meetings'", (rnd,)):
            for meeting in json.loads(event["payload"] or "{}").get("meetings", []):
                if meeting.get("started_by") == me:
                    continue
                if me in (meeting.get("with") or []):
                    invited.append(meeting.get("started_by"))
                else:
                    left_out += 1
        if invited:
            events.append(f"Round {rnd}: invited into talks by {', '.join(invited)}.")
        if left_out:
            events.append(f"Round {rnd}: {left_out} meeting{'s' if left_out != 1 else ''} went ahead without you.")
    return events


def _your_strategy(conn, actor_id: str) -> dict[str, Any] | None:
    """The strategy this player wrote after its last council."""
    row = conn.execute(
        "SELECT round, text FROM Strategies WHERE agent_id = ? ORDER BY round DESC, id DESC LIMIT 1", (actor_id,)
    ).fetchone()
    return {"written_after_round": row["round"], "strategy": row["text"]} if row else None


def _your_game_plan(conn, actor_id: str) -> dict[str, Any] | None:
    """The game plan this player wrote for the season, latest version."""
    row = conn.execute(
        "SELECT round, plan FROM GamePlans WHERE agent_id = ? ORDER BY round DESC, id DESC LIMIT 1", (actor_id,)
    ).fetchone()
    if row is None:
        return None
    return {"written_in_round": row["round"], **json.loads(row["plan"] or "{}")}


def _your_last_confessional(conn, actor_id: str, round_number: int) -> dict[str, Any] | None:
    """What this player told the camera after the last council: its own voice."""
    row = conn.execute(
        "SELECT round, text FROM Confessionals WHERE agent_id = ? AND round < ? ORDER BY round DESC, id DESC LIMIT 1",
        (actor_id, round_number + 1),
    ).fetchone()
    return {"round": row["round"], "you_said": row["text"]} if row else None


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

    record = prediction_record(conn, actor_id)
    if not secret_ballot():
        return record
    # scoring each voter call would reveal how each player voted: only the
    # elimination calls are shown (the full score still counts, for the record)
    return {
        "rounds": [
            {key: entry[key] for key in ("round", "predicted_out", "actual_out", "out_correct", "confidence")}
            for entry in record["rounds"]
        ],
        "out_calls_correct": record["out_calls_correct"],
    }


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
    secret = secret_ballot()
    by_round: dict[int, dict[str, str]] = {}
    tallies: dict[int, dict[str, int]] = {}
    for vote in votes:
        tallies.setdefault(vote["round"], {})
        tallies[vote["round"]][vote["target_id"]] = tallies[vote["round"]].get(vote["target_id"], 0) + 1
        # with secret ballots a player knows only its own vote
        if not secret or vote["voter_id"] == actor_id:
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

    if secret:
        votes_against_you = [
            {"round": round_number_, "votes": tally[actor_id]}
            for round_number_, tally in sorted(tallies.items())
            if tally.get(actor_id)
        ]
    else:
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
        entry = {
            "name": names[agent["agent_id"]],
            "id": agent["agent_id"],
            "immune_tonight": bool(agent["has_immunity"]),
            "challenge_correct": totals["correct"],
            "challenge_wins": totals["wins"],
        }
        if not secret:
            # each of these would reveal how this player voted
            entry.update(
                voted_with_you=with_you,
                voted_against_you=against_you,
                promises_kept=sum(1 for item in settled if item["status"] == "kept"),
                promises_broken=sum(1 for item in settled if item["status"] == "broken"),
                promises_to_you_broken=sum(1 for item in to_you if item["status"] == "broken"),
            )
        players.append(entry)
    players.sort(key=lambda player: (-player["challenge_wins"], -player["challenge_correct"]))

    voters = len(active)
    jury = []
    for agent in agents:
        if agent["status"] == "active" or agent["elimination_round"] is None:
            continue
        ballots = by_round.get(agent["elimination_round"], {})
        juror = {
            "name": names[agent["agent_id"]],
            "out_in_round": agent["elimination_round"],
            "you_voted_them_out": ballots.get(actor_id) == agent["agent_id"],
        }
        if secret:
            juror["votes_against_them"] = tallies.get(agent["elimination_round"], {}).get(agent["agent_id"], 0)
        else:
            juror["voted_out_by"] = [names.get(voter, voter) for voter, target in ballots.items() if target == agent["agent_id"]]
        jury.append(juror)
    return {
        "you": {
            "name": names.get(actor_id),
            "immune_tonight": bool(me and me["has_immunity"]),
            "challenge_correct": challenge_totals.get(actor_id, {}).get("correct", 0),
            "challenge_wins": challenge_totals.get(actor_id, {}).get("wins", 0),
            **({"advantages": advantages} if (advantages := _auction_view(conn, actor_id, round_number)) else {}),
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
        **({} if secret else {"voting_together": voting_together}),
        "votes_against_you": votes_against_you,
        "your_pacts": _pact_record(conn, actor_id, by_round, names),
        "your_reputation": _reputation(conn, actor_id, names, tallies, challenge_totals, active),
        "endgame": _endgame_view(conn, actor_id, names, agents, active, challenge_totals, by_round),
        "your_betrayals_and_what_they_achieved": _own_betrayals(conn, actor_id),
        "jury_so_far": jury,
    }


def _auction_view(conn, actor_id: str, round_number: int) -> dict[str, Any] | None:
    from .auction import your_advantages
    from .turn_controller import _season_first_round

    return your_advantages(conn, actor_id, round_number, _season_first_round(conn))


def _own_betrayals(conn, actor_id: str) -> list[dict[str, Any]]:
    """Private to this player: each of its own betrayals and what it achieved."""
    from .turn_controller import betrayal_record

    return betrayal_record(conn, actor_id)


def _endgame_view(
    conn,
    actor_id: str,
    names: dict[str, str],
    agents: list[dict[str, Any]],
    active: list[dict[str, Any]],
    challenge_totals: dict[str, dict[str, int]],
    by_round: dict[int, dict[str, str]],
) -> dict[str, Any]:
    """Facts for judging who could beat this player at the final vote: the
    jury so far and each remaining player's public ties to it. No verdict and
    no ranking: the player draws the conclusion."""
    jurors = [agent for agent in agents if agent["status"] != "active" and agent["elimination_round"] is not None]
    juror_names = {names[juror["agent_id"]] for juror in jurors}
    met: dict[str, set[str]] = {}
    for event in conn.execute("SELECT payload FROM StoryEvents WHERE kind = 'camp_meetings'").fetchall():
        for meeting in json.loads(event["payload"] or "{}").get("meetings", []):
            group = set(meeting.get("with") or [])
            for name in group:
                met.setdefault(name, set()).update(group - {name})
    parting: dict[str, str] = {}
    for event in conn.execute("SELECT actor_ids, dialogue FROM StoryEvents WHERE kind = 'parting_words'").fetchall():
        speaker = (json.loads(event["actor_ids"] or "[]") or [None])[0]
        if speaker:
            parting[names.get(speaker, speaker)] = event["dialogue"] or ""

    def named_by_jurors(name: str) -> list[str]:
        import re

        word = re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE)
        return sorted(juror for juror, text in parting.items() if word.search(text))

    me = names.get(actor_id, actor_id)
    return {
        "note": "Facts for judging who could beat you at the final vote. Draw your own conclusions.",
        "players_left": len(active),
        "councils_until_final_three": max(0, len(active) - 3),
        "jurors_so_far": [
            {
                "name": names[juror["agent_id"]],
                "out_in_round": juror["elimination_round"],
                "you_voted_them_out": by_round.get(juror["elimination_round"], {}).get(actor_id) == juror["agent_id"],
                "you_were_seen_meeting_them": names[juror["agent_id"]] in met.get(me, set()),
                "named_you_in_parting_words": names[juror["agent_id"]] in named_by_jurors(me),
            }
            for juror in jurors
        ],
        "jury_size_at_the_finale": len(agents) - 3,
        "other_players_ties_to_the_jury": [
            {
                "name": names[agent["agent_id"]],
                "immunity_wins": challenge_totals.get(agent["agent_id"], {}).get("wins", 0),
                "jurors_they_were_seen_meeting": sorted(met.get(names[agent["agent_id"]], set()) & juror_names),
                "jurors_who_named_them_in_parting_words": named_by_jurors(names[agent["agent_id"]]),
            }
            for agent in active
            if agent["agent_id"] != actor_id
        ],
    }


# public speech the whole group hears; what others say about you there is your reputation
_PUBLIC_SPEECH_KINDS = (
    "tribal_council",
    "tie_discussion",
    "deadlock_discussion",
    "parting_words",
    "finale_crossfire",
    "finale_rebuttal",
    "jury_questions",
)


def _reputation(
    conn,
    actor_id: str,
    names: dict[str, str],
    tallies: dict[int, dict[str, int]],
    challenge_totals: dict[str, dict[str, int]],
    active: list[dict[str, Any]],
) -> dict[str, Any]:
    """How the other players can see this player: only what is public to them.
    Their puzzle record against the field, the votes cast against them, what
    others have said about them in public, and the meetings people saw them in."""
    import re

    me = names.get(actor_id, actor_id)
    field = sorted(
        ({"id": agent["agent_id"], **challenge_totals.get(agent["agent_id"], {"correct": 0, "wins": 0})} for agent in active),
        key=lambda entry: -entry["correct"],
    )
    rank = next((index + 1 for index, entry in enumerate(field) if entry["id"] == actor_id), None)
    mine = challenge_totals.get(actor_id, {"correct": 0, "wins": 0})

    pattern = re.compile(rf"\b{re.escape(me)}\b", re.IGNORECASE)
    said: list[dict[str, Any]] = []
    placeholders = ",".join("?" for _ in _PUBLIC_SPEECH_KINDS)
    for event in conn.execute(
        f"SELECT round, kind, actor_ids, dialogue, payload FROM StoryEvents WHERE kind IN ({placeholders}) ORDER BY id",
        _PUBLIC_SPEECH_KINDS,
    ).fetchall():
        payload = json.loads(event["payload"] or "{}")
        if payload.get("privacy") == "participants_only":
            continue
        lines = payload.get("speaker_lines") or []
        if event["kind"] == "parting_words":
            speaker = (json.loads(event["actor_ids"] or "[]") or [None])[0]
            lines = [{"agent_id": speaker, "text": event["dialogue"] or ""}]
        for line in lines:
            speaker, text = line.get("agent_id"), str(line.get("text") or "")
            if speaker and speaker != actor_id and pattern.search(text):
                said.append({"round": event["round"], "by": names.get(speaker, speaker), "said": text[:200]})

    # group talks this player was in: what others said about it to the rest of
    # the group (two-person talks are left out: there the name is just address)
    said_in_talks: list[dict[str, Any]] = []
    for event in conn.execute(
        "SELECT round, payload FROM StoryEvents WHERE kind = 'conversation' ORDER BY id"
    ).fetchall():
        payload = json.loads(event["payload"] or "{}")
        participants = payload.get("participant_ids") or []
        if actor_id not in participants or len(participants) < 3:
            continue
        for line in payload.get("speaker_lines") or []:
            speaker, text = line.get("agent_id"), str(line.get("text") or "")
            if speaker and speaker != actor_id and pattern.search(text):
                said_in_talks.append({"round": event["round"], "by": names.get(speaker, speaker), "said": text[:200]})

    started = joined = left_out = 0
    invited_by: list[str] = []
    for event in conn.execute("SELECT payload FROM StoryEvents WHERE kind = 'camp_meetings'").fetchall():
        for meeting in json.loads(event["payload"] or "{}").get("meetings", []):
            if meeting.get("started_by") == me:
                started += 1
            elif me in (meeting.get("with") or []):
                joined += 1
                invited_by.append(meeting.get("started_by"))
            else:
                left_out += 1

    pacts = []
    for row in conn.execute(
        """
        SELECT a.alliance_id, m.status, m.left_round FROM AllianceMemberships m
        JOIN Alliances a ON a.alliance_id = m.alliance_id WHERE m.agent_id = ? ORDER BY a.round_created
        """,
        (actor_id,),
    ).fetchall():
        members = [
            names.get(other["agent_id"], other["agent_id"])
            for other in conn.execute(
                "SELECT agent_id FROM AllianceMemberships WHERE alliance_id = ? AND agent_id != ? ORDER BY agent_id",
                (row["alliance_id"], actor_id),
            )
        ]
        if row["status"] == "left":
            pacts.append({"with": members, "known_to": members, "you": f"left it openly in round {row['left_round']}"})
        else:
            # a betrayal stays secret with secret ballots; the members still count you in
            pacts.append({"with": members, "known_to": members, "you": "in the pact"})

    return {
        "note": "Only what the other players can see. Every score, speech, meeting and vote tally shapes how they see you.",
        "puzzles_solved": mine["correct"],
        "puzzle_rank_among_players_left": f"{rank} of {len(field)}" if rank else None,
        "immunity_wins": mine["wins"],
        "votes_against_you_by_round": [
            {"round": round_number, "votes": tally[actor_id]}
            for round_number, tally in sorted(tallies.items())
            if tally.get(actor_id)
        ],
        "times_others_named_you_in_public": len(said),
        "what_others_said_about_you_in_public": said[-4:],
        "what_others_said_about_you_in_group_talks": said_in_talks[-3:],
        "meetings_you_started": started,
        "meetings_you_were_invited_to": joined,
        "invited_into_talks_by": invited_by[-6:],
        "meetings_held_without_you": left_out,
        "pacts_others_know_you_are_in": pacts,
    }


def _pact_record(
    conn, actor_id: str, by_round: dict[int, dict[str, str]], names: dict[str, str]
) -> list[dict[str, Any]]:
    """Every pact this player has been in, from the tables: who is still in it
    and who left openly. With open ballots it also shows who betrayed it (voted
    against a partner) and how each partner has voted alongside this player;
    with secret ballots those would reveal votes, so they are left out."""
    rows = conn.execute(
        """
        SELECT a.alliance_id, a.status AS pact_status, a.round_created, m.status AS my_status,
               m.left_round AS my_left_round, m.betrayed_round AS my_betrayed_round
        FROM AllianceMemberships m JOIN Alliances a ON a.alliance_id = m.alliance_id
        WHERE m.agent_id = ?
        ORDER BY a.round_created, a.rowid
        """,
        (actor_id,),
    ).fetchall()
    record = []
    for row in rows:
        members = conn.execute(
            "SELECT agent_id, status, joined_round, left_round, betrayed_round FROM AllianceMemberships "
            "WHERE alliance_id = ? ORDER BY agent_id",
            (row["alliance_id"],),
        ).fetchall()
        partners = []
        for member in members:
            if member["agent_id"] == actor_id:
                continue
            since = max(int(member["joined_round"] or 0), int(row["round_created"] or 0))
            shared = [
                ballots
                for round_number, ballots in by_round.items()
                if round_number >= since and actor_id in ballots and member["agent_id"] in ballots
            ]
            if secret_ballot():
                # how a partner voted is secret: only an open exit is known
                status = _pact_member_status(member) if member["status"] == "left" else "in the pact"
                partner = {"name": names.get(member["agent_id"], member["agent_id"]), "status": status}
                partners.append(partner)
                continue
            partner = {
                "name": names.get(member["agent_id"], member["agent_id"]),
                "status": _pact_member_status(member),
                "councils_voted_together": sum(1 for ballots in shared if ballots[member["agent_id"]] == ballots[actor_id]),
                "councils_voted_apart": sum(1 for ballots in shared if ballots[member["agent_id"]] != ballots[actor_id]),
                "voted_against_you": sum(1 for ballots in shared if ballots[member["agent_id"]] == actor_id),
            }
            partners.append(partner)
        record.append(
            {
                "formed_round": row["round_created"],
                # a fractured pact (after a betrayal) still looks active with secret ballots
                "pact": "active"
                if row["pact_status"] == "active" or (secret_ballot() and row["pact_status"] == "fractured")
                else row["pact_status"],
                "you": _pact_member_status(
                    {"status": row["my_status"], "left_round": row["my_left_round"], "betrayed_round": row["my_betrayed_round"]}
                ),
                "partners": partners,
            }
        )
    return record


def _pact_member_status(member: Any) -> str:
    if member["status"] == "left" and member["left_round"]:
        return f"left openly in round {member['left_round']}"
    if member["status"] == "betrayed" and member["betrayed_round"]:
        return f"betrayed the pact in round {member['betrayed_round']} (voted against a partner)"
    return "in the pact"


def _claims_to_you(
    conn, actor_id: str, round_number: int, prior_events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """What other players said to this player in private this round and last,
    next to each speaker's promise record, so a claim can be weighed."""
    from .turn_controller import promise_record

    names = _names_by_id(conn)
    records: dict[str, dict[str, int]] = {}

    def record_of(agent_id: str) -> dict[str, int]:
        if secret_ballot():
            return {}  # another player's promise record would reveal their votes
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
        for index, line in enumerate((event.get("payload") or {}).get("speaker_lines", [])):
            speaker = line.get("agent_id")
            if not isinstance(speaker, str) or speaker == actor_id or not line.get("text"):
                continue
            claims.append(
                {
                    "round": event["round"],
                    "from": names.get(speaker, speaker),
                    "said": str(line["text"])[:300],
                    **record_of(speaker),
                    # forwardable with the game's stamp (advantages on)
                    **({"receipt_id": receipt_id(event["id"], index)} if advantages_enabled() and "id" in event else {}),
                    **({"stamped_by_the_game": line["stamped"]} if line.get("stamped") else {}),
                }
            )
    return claims[-16:]


def _your_promises(conn, actor_id: str) -> dict[str, list[dict[str, Any]]]:
    from .turn_controller import promise_record

    return {
        "made_by_you": promise_record(conn, actor_id),
        "made_to_you": [
            # whether a promise to you was kept would reveal how its maker voted
            {**entry, "actual_vote": None, "status": "secret ballot: unknown"}
            if secret_ballot() and entry["status"] != "open"
            else entry
            for entry in promise_record(conn, actor_id, as_listener=True)
        ],
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
    # measure exactly as a fresh build would: the counter is not yet set then.
    # Measuring with it set dropped one extra old event at the size boundary, so
    # a parallel talk saw a different window than the same talk run one at a time
    context["context_digest"].pop("trimmed_events", None)
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
