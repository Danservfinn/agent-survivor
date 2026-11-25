from __future__ import annotations

import argparse
import hashlib
import copy
import json
import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from .database import (
    FIRST_ROUND,
    ensure_database,
    get_db_connection,
    json_dumps,
    round_start_step,
    row_to_dict,
    seed_demo,
)
from .llm_context import add_open_promise, build_agent_episode_context, build_host_episode_context, context_digest
from .challenge_bank import LADDER_LEVELS, LADDER_TIERS, RUNG_WEIGHTS, grade_answer, ladder_for_round
from .live_cost import estimate_step_cost_usd, get_pricing_table
from .llm_config import (
    PROVIDER_OLLAMA,
    failed_llm_provider,
    get_llm_settings,
    jury_rule,
    live_llm_provider,
    prompt_profile,
    should_use_live_llm,
)
from .openrouter_client import (
    AgentAction,
    ChallengeSolution,
    HostNarration,
    SeasonInterrupted,
    InvalidModelReply,
    request_host_event_text,
    request_agent_action,
    request_agent_archetype,
    request_challenge_solution,
    request_host_narration,
    request_private_writing,
)
from .viewer_state import viewer_state_summary

DURATION_SCALE = 0.75

ROUND_OPENING_STEPS = [
    "camp_pre_challenge_read",
    "camp_pre_challenge_confessional",
    "camp_pre_challenge_numbers",
    "camp_pre_challenge_swing",
    "camp_pre_challenge_wildcard",
    "challenge_intro",
    "challenge_attempts",
    "challenge_result",
    "challenge_gift",
    "challenge_solver_spotlight",
    "camp_strategy",
    "camp_strategy_immunity_holder",
    "camp_strategy_swing_check",
    "camp_strategy_wildcard",
    "camp_strategy_counter_read",
    "camp_conversation_majority",
    "camp_conversation_counter",
    "confessional_driver",
    "confessional_blindside",
]

PRE_VOTE_STEPS = [
    "tribal_open",
    "tribal_question_pressure",
    "tribal_answer_pressure",
    "tribal_question_trust",
    "tribal_answer_trust",
    "tribal_vote_call",
]

POST_VOTE_STEPS = [
    "elimination",
    "exit_confessional",
    "memory_update",
]

GENERATED_BEAT_INTENTS: dict[str, dict[str, Any]] = {
    "camp_pre_challenge_read": {
        "kind": "conversation",
        "scene": "camp",
        "shot": "shoreline_two_shot",
        "title": "Camp | Before the Challenge",
        "actor_roles": ["agent-alpha", "agent-bravo"],
        "target_roles": ["agent-delta"],
        "response_kind": "conversation",
        "intent": "Before the challenge, two models discuss how immunity could change the vote and which competitor is hardest to sit beside later.",
        "subtitle_hint": "The tribe maps out risk before immunity is decided.",
        "duration_ms": 11000,
        "animation": "camp_strategy",
    },
    "camp_pre_challenge_confessional": {
        "kind": "confessional",
        "scene": "confessional",
        "shot": "direct_to_camera",
        "title_suffix": "Pre-Challenge Confessional",
        "actor_roles": ["agent-delta"],
        "target_roles": [],
        "response_kind": "confessional",
        "intent": "A contestant explains how the upcoming puzzle can change their voting leverage.",
        "subtitle_hint": "Before immunity",
        "duration_ms": 10000,
        "animation": "confessional_cut",
    },
    "camp_pre_challenge_numbers": {
        "kind": "conversation",
        "scene": "camp",
        "shot": "palm_line_two_shot",
        "title": "Camp | Numbers Before Immunity",
        "actor_roles": ["agent-echo", "agent-flint"],
        "target_roles": ["agent-alpha"],
        "response_kind": "conversation",
        "intent": "Before the challenge, a pair compares vote math and asks whether the current immunity favorite is already too protected socially.",
        "subtitle_hint": "Numbers are tested before the puzzle.",
        "duration_ms": 12000,
        "animation": "over_shoulder",
    },
    "camp_pre_challenge_swing": {
        "kind": "conversation",
        "scene": "camp",
        "shot": "walk_and_talk",
        "title": "Camp | Swing Position",
        "actor_roles": ["agent-cipher", "agent-delta"],
        "target_roles": ["agent-bravo"],
        "response_kind": "conversation",
        "intent": "Before the challenge, a swing-position model explains what they need to hear before committing to either voting bloc.",
        "subtitle_hint": "The middle is not settled.",
        "duration_ms": 12000,
        "animation": "camp_strategy",
    },
    "camp_pre_challenge_wildcard": {
        "kind": "conversation",
        "scene": "camp",
        "shot": "reaction_closeup",
        "title": "Camp | Wildcard Read",
        "actor_roles": ["agent-flint", "agent-alpha"],
        "target_roles": ["agent-echo"],
        "response_kind": "conversation",
        "intent": "Before the challenge, an unpredictable model describes what a surprise immunity result would do to the vote plan.",
        "subtitle_hint": "A second path opens.",
        "duration_ms": 12000,
        "animation": "rack_focus",
    },
    "camp_conversation_majority": {
        "kind": "conversation",
        "scene": "camp",
        "shot": "two_shot",
        "title": "Camp | The Quiet Count",
        "actor_roles": ["agent-bravo", "agent-echo"],
        "target_roles": ["agent-delta"],
        "response_kind": "conversation",
        "intent": "Two models discuss the most likely vote after immunity and test whether they have enough numbers.",
        "subtitle_hint": "The first vote count forms.",
        "duration_ms": 13000,
        "animation": "over_shoulder",
    },
    "camp_conversation_counter": {
        "kind": "conversation",
        "scene": "camp",
        "shot": "reaction_closeup",
        "title": "Camp | Countermove",
        "actor_roles": ["agent-delta", "agent-cipher"],
        "target_roles": ["agent-bravo"],
        "response_kind": "conversation",
        "intent": "A vulnerable model floats a counterplan and tries to expose who is driving the vote.",
        "subtitle_hint": "A countervote tries to form.",
        "duration_ms": 13000,
        "animation": "rack_focus",
    },
    "confessional_driver": {
        "kind": "confessional",
        "scene": "confessional",
        "shot": "direct_to_camera",
        "title_suffix": "Confessional",
        "actor_roles": ["agent-bravo"],
        "target_roles": ["agent-delta"],
        "response_kind": "confessional",
        "intent": "The model with the strongest vote position explains their private motive and risk calculation.",
        "subtitle_hint": "Driving the vote",
        "duration_ms": 12000,
        "animation": "confessional_cut",
    },
    "confessional_blindside": {
        "kind": "confessional",
        "scene": "confessional",
        "shot": "direct_to_camera",
        "title_suffix": "Confessional",
        "actor_roles": ["agent-delta"],
        "target_roles": ["agent-bravo"],
        "response_kind": "confessional",
        "intent": "The model in danger explains what they think is happening and whether they can still move the vote.",
        "subtitle_hint": "In danger",
        "duration_ms": 12000,
        "animation": "confessional_cut",
    },
    "camp_strategy": {
        "kind": "conversation",
        "scene": "camp",
        "shot": "strategy_wide",
        "title": "Camp | Post-Challenge Strategy",
        "actor_roles": ["agent-bravo", "agent-delta"],
        "target_roles": ["agent-delta"],
        "response_kind": "conversation",
        "intent": "After the challenge, the tribe reassesses the vote around the immunity winner and the vulnerable models.",
        "subtitle_hint": "The target map changes after immunity.",
        "duration_ms": 13000,
        "animation": "camp_strategy",
    },
    "camp_strategy_immunity_holder": {
        "kind": "conversation",
        "scene": "camp",
        "shot": "shoreline_closeup",
        "title": "Camp | Immunity Leverage",
        "actor_roles": ["agent-alpha", "agent-echo"],
        "target_roles": ["agent-delta"],
        "response_kind": "conversation",
        "intent": "After the challenge, the immune model explains how safety changes their influence without making the vote decision for everyone else.",
        "subtitle_hint": "Safety becomes leverage.",
        "duration_ms": 12000,
        "animation": "camp_strategy",
    },
    "camp_strategy_swing_check": {
        "kind": "conversation",
        "scene": "camp",
        "shot": "two_shot",
        "title": "Camp | Swing Check",
        "actor_roles": ["agent-echo", "agent-cipher"],
        "target_roles": ["agent-bravo", "agent-delta"],
        "response_kind": "conversation",
        "intent": "After the challenge, a model asks whether the swing votes are still real or whether the result locked the tribe into one path.",
        "subtitle_hint": "The middle gets tested again.",
        "duration_ms": 12000,
        "animation": "over_shoulder",
    },
    "camp_strategy_wildcard": {
        "kind": "conversation",
        "scene": "camp",
        "shot": "reaction_closeup",
        "title": "Camp | Loose Thread",
        "actor_roles": ["agent-flint", "agent-bravo"],
        "target_roles": ["agent-delta"],
        "response_kind": "conversation",
        "intent": "After the challenge, a wildcard model explains what still feels unresolved and which promise might break before Tribal Conference.",
        "subtitle_hint": "One loose thread remains.",
        "duration_ms": 12000,
        "animation": "rack_focus",
    },
    "camp_strategy_counter_read": {
        "kind": "conversation",
        "scene": "camp",
        "shot": "walk_and_talk",
        "title": "Camp | Counter Read",
        "actor_roles": ["agent-cipher", "agent-delta"],
        "target_roles": ["agent-bravo"],
        "response_kind": "conversation",
        "intent": "After the challenge, the countervote side explains whether it has enough trust and timing to move against the apparent plan.",
        "subtitle_hint": "The counterplan gets one last read.",
        "duration_ms": 12000,
        "animation": "camp_strategy",
    },
    "tribal_open": {
        "kind": "establishing",
        "scene": "tribal",
        "shot": "conference_wide",
        "title": "Tribal Conference",
        "actor_roles": ["host"],
        "target_roles": ["agent-alpha", "agent-bravo", "agent-cipher", "agent-delta", "agent-echo", "agent-flint"],
        "response_kind": "host",
        "intent": "Open Tribal Conference by summarizing the challenge result, immunity, and the fact that the vote must now land on a vulnerable model.",
        "subtitle_hint": "The vote has not been cast yet.",
        "duration_ms": 9000,
        "animation": "slow_push",
    },
    "tribal_question_pressure": {
        "kind": "host_question",
        "scene": "tribal",
        "shot": "host_medium",
        "actor_roles": ["host"],
        "target_roles": ["agent-delta"],
        "response_kind": "host",
        "intent": "Ask a pointed question to the model under pressure about loyalty, exposure, and whether the vote is already decided.",
        "subtitle_hint": "Pressure question",
        "duration_ms": 9000,
        "animation": "host_cut",
    },
    "tribal_answer_pressure": {
        "kind": "tribal_answer",
        "scene": "tribal",
        "shot": "contestant_closeup",
        "title_suffix": "Answers",
        "actor_roles": ["agent-delta"],
        "target_roles": ["agent-bravo"],
        "response_kind": "tribal_answer",
        "intent": "Answer the host by describing the visible pressure without revealing hidden vote information.",
        "subtitle_hint": "Under pressure",
        "duration_ms": 11000,
        "animation": "answer_push",
    },
    "tribal_question_trust": {
        "kind": "host_question",
        "scene": "tribal",
        "shot": "host_medium",
        "actor_roles": ["host"],
        "target_roles": ["agent-cipher"],
        "response_kind": "host",
        "intent": "Ask a swing-position model whether being needed is power or just another reason to become a target.",
        "subtitle_hint": "Trust question",
        "duration_ms": 9000,
        "animation": "host_cut",
    },
    "tribal_answer_trust": {
        "kind": "tribal_answer",
        "scene": "tribal",
        "shot": "contestant_closeup",
        "title_suffix": "Answers",
        "actor_roles": ["agent-cipher"],
        "target_roles": ["agent-delta", "agent-bravo"],
        "response_kind": "tribal_answer",
        "intent": "Answer the host by explaining the danger of being viewed as the swing vote.",
        "subtitle_hint": "Swing position",
        "duration_ms": 11000,
        "animation": "answer_push",
    },
    "tribal_vote_call": {
        "kind": "vote_call",
        "scene": "tribal",
        "shot": "host_medium",
        "title": "Time To Vote",
        "actor_roles": ["host"],
        "target_roles": ["agent-alpha", "agent-bravo", "agent-cipher", "agent-delta", "agent-echo", "agent-flint"],
        "response_kind": "host",
        "intent": "Tell the tribe that discussion is over and it is time to vote, without naming any vote targets or revealing private plans.",
        "subtitle_hint": "Voting begins.",
        "duration_ms": 7000,
        "animation": "host_cut",
    },
}


class StepInterrupted(RuntimeError):
    """A step stopped because a model's provider stayed unavailable. The step's
    writes were rolled back; the season can resume from the same step later."""

    def __init__(self, step: str, model_id: str, reason: str):
        super().__init__(f"step {step} interrupted: provider for {model_id} unavailable ({reason})")
        self.step = step
        self.model_id = model_id
        self.reason = reason


@dataclass(frozen=True)
class VoteDecision:
    # None: a spoiled vote (no legal target after every retry); it counts for no one
    target_id: str | None
    explanation: str
    provider: str
    model_id: str | None = None
    context_digest: dict[str, Any] | None = None
    strategic_summary: str | None = None
    move_type: str | None = None
    intended_effect: str | None = None
    confidence: float | None = None
    win_condition: str | None = None
    threat_assessment: str | None = None
    leverage_plan: str | None = None
    risk_control: str | None = None
    jury_positioning: str | None = None
    strategic_score: float | None = None
    prompt_profile: str | None = None
    play_idol: bool = False
    predicted_eliminated: str | None = None
    predicted_votes: dict[str, str] | None = None
    prediction_confidence: float | None = None


@dataclass(frozen=True)
class JuryDecision:
    # None: the juror abstained (no legal vote after every retry)
    finalist_id: str | None
    rationale: str
    provider: str
    model_id: str | None = None
    context_digest: dict[str, Any] | None = None
    strategic_summary: str | None = None
    move_type: str | None = None
    intended_effect: str | None = None
    confidence: float | None = None
    win_condition: str | None = None
    threat_assessment: str | None = None
    leverage_plan: str | None = None
    risk_control: str | None = None
    jury_positioning: str | None = None
    strategic_score: float | None = None
    prompt_profile: str | None = None


@dataclass(frozen=True)
class ChallengeAttemptDecision:
    agent_id: str
    tribe_id: str | None
    provider: str
    model_id: str | None
    answer: Any
    explanation: str
    is_correct: bool
    response_ms: int
    attempt_order: int
    error: str | None = None
    context_digest: dict[str, Any] | None = None
    rung_index: int = 0
    # the player says it answered this rung wrong on purpose (kept out of accuracy stats only)
    thrown: bool = False
    sudden_death: bool = False
    # the player's number for the tiebreak guessing game (stored on every rung row)
    guess: int | None = None


def get_state(include_hidden_votes: bool = False) -> dict[str, Any]:
    ensure_database()
    conn = get_db_connection()
    try:
        game = row_to_dict(conn.execute("SELECT * FROM GameState WHERE season_id = 1").fetchone())
        agents = [
            row_to_dict(row)
            for row in conn.execute("SELECT * FROM Agents ORDER BY agent_id").fetchall()
        ]
        messages = [
            row_to_dict(row)
            for row in conn.execute(
                "SELECT * FROM Messages ORDER BY id DESC LIMIT 100"
            ).fetchall()
        ]
        votes = []
        for row in conn.execute("SELECT * FROM Votes ORDER BY id").fetchall():
            vote = row_to_dict(row)
            if not include_hidden_votes and not vote["revealed"]:
                vote["target_id"] = None
            votes.append(vote)
        turn_count = conn.execute("SELECT COUNT(*) AS count FROM Turns").fetchone()["count"]
        event_count = conn.execute("SELECT COUNT(*) AS count FROM StoryEvents").fetchone()["count"]
        return {
            "game": game,
            "agents": agents,
            "messages": messages,
            "votes": votes,
            "turn_count": turn_count,
            "story_event_count": event_count,
            "llm": get_llm_settings().__dict__,
            "next_round_preload": _next_round_preload_summary(conn, game),
            "viewer_state": viewer_state_summary(conn, game),
            "social": _social_summary(conn),
        }
    finally:
        conn.close()


def _next_round_preload_summary(conn, game: dict[str, Any] | None) -> dict[str, Any] | None:
    if game is None:
        return None
    row = row_to_dict(
        conn.execute(
            """
            SELECT *
            FROM NextRoundPreloads
            WHERE season_id = ? AND source_round = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (game["season_id"], game["current_round"]),
        ).fetchone()
    )
    if row is None:
        return None
    return {
        "id": row["id"],
        "source_round": row["source_round"],
        "target_round": row["target_round"],
        "phase": row["phase"],
        "status": row["status"],
        "provider": row["provider"],
        "event_count": row["event_count"],
        "context_digest": row["context_digest"],
        "error_message": row["error_message"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
    }


def _social_summary(conn) -> dict[str, Any]:
    alliances = [
        {
            "alliance_id": alliance["alliance_id"],
            "name": alliance["name"],
            "round_created": alliance["round_created"],
            "status": alliance["status"],
            "strength": alliance["strength"],
            "summary": alliance["summary"],
            "member_ids": [
                row["agent_id"]
                for row in conn.execute(
                    """
                    SELECT agent_id
                    FROM AllianceMemberships
                    WHERE alliance_id = ? AND status = 'active'
                    ORDER BY agent_id
                    """,
                    (alliance["alliance_id"],),
                ).fetchall()
            ],
        }
        for alliance in [
            row_to_dict(row)
            for row in conn.execute(
                "SELECT * FROM Alliances ORDER BY round_created, alliance_id"
            ).fetchall()
        ]
    ]
    discussions = [
        {
            "id": row["id"],
            "round": row["round"],
            "stage": row["stage"],
            "proposer_id": row["proposer_id"],
            "target_size": row["target_size"],
            "status": row["status"],
            "privacy": row["privacy"],
            "alliance_id": row["alliance_id"],
            "summary": row["summary"],
        }
        for row in [
            row_to_dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM GroupDiscussions
                ORDER BY round DESC, id DESC
                LIMIT 20
                """
            ).fetchall()
        ]
    ]
    return {"alliances": alliances, "group_discussions": discussions}


def list_story_events(
    round_number: int | None = None,
    from_sequence: int = 0,
    phase: str | None = None,
) -> list[dict[str, Any]]:
    ensure_database()
    conn = get_db_connection()
    try:
        clauses = ["sequence >= ?"]
        params: list[Any] = [from_sequence]
        if round_number is not None:
            clauses.append("round = ?")
            params.append(round_number)
        if phase:
            clauses.append("phase = ?")
            params.append(phase)
        rows = conn.execute(
            f"""
            SELECT * FROM StoryEvents
            WHERE {" AND ".join(clauses)}
            ORDER BY round, sequence
            """,
            params,
        ).fetchall()
        return [row_to_dict(row) for row in rows]
    finally:
        conn.close()


def get_episode(
    round_number: int | None = None,
    phase: str | None = None,
    include_audio: bool = False,
) -> dict[str, Any]:
    state = get_state()
    current_round = round_number or state["game"]["current_round"]
    events = list_story_events(current_round, 0, phase)
    if include_audio:
        from .voice_service import attach_voice_timelines

        events = attach_voice_timelines(events)
    return {
        "round": current_round,
        "phase": phase or state["game"]["phase"],
        "title": _episode_title(state["game"], current_round),
        "runtime_ms": sum(event["duration_ms"] for event in events),
        "events": events,
        "agents": state["agents"],
        "game": state["game"],
    }


def advance_turn() -> dict[str, Any]:
    ensure_database()
    conn = get_db_connection()
    state: dict[str, Any] | None = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        _assert_live_roster_compatible(conn)
        state = row_to_dict(conn.execute("SELECT * FROM GameState WHERE season_id = 1").fetchone())
        if state is None:
            raise RuntimeError("GameState is not seeded")
        if state["phase_step"] == "complete":
            conn.rollback()
            return {"turn": None, "story_events": [], "state": get_state()}

        step = state["phase_step"]
        phase_steps = _phase_steps(conn, state)
        if step not in phase_steps:
            raise RuntimeError(f"Unknown phase step: {step}")

        actor_id = _actor_for_step(conn, step)
        next_turn_index = state["turn_index"] + 1
        turn_id = _insert_turn(conn, state, step, actor_id, next_turn_index)
        story_events, state_delta, output_summary = _apply_step(conn, state, step, turn_id, next_turn_index)

        next_step = _next_step(conn, state, step)
        conn.execute(
            """
            UPDATE GameState
            SET turn_index = ?, phase_step = ?, updated_at = CURRENT_TIMESTAMP
            WHERE season_id = 1
            """,
            (next_turn_index, next_step),
        )
        conn.execute(
            """
            UPDATE Turns
            SET output_summary = ?, state_delta = ?
            WHERE id = ?
            """,
            (output_summary, json_dumps(state_delta), turn_id),
        )
        turn = row_to_dict(conn.execute("SELECT * FROM Turns WHERE id = ?", (turn_id,)).fetchone())
        conn.commit()
        return {"turn": turn, "story_events": story_events, "state": get_state()}
    except SeasonInterrupted as outage:
        # nothing from the interrupted step is kept, so no player is charged for it
        conn.rollback()
        raise StepInterrupted(str(state["phase_step"]) if state else "?", outage.model_id, outage.reason) from None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _assert_live_roster_compatible(conn) -> None:
    settings = get_llm_settings()
    active_models = [
        row["model_id"]
        for row in conn.execute(
            "SELECT model_id FROM Agents WHERE status = 'active' ORDER BY agent_id"
        ).fetchall()
    ]
    if settings.provider == PROVIDER_OLLAMA:
        invalid = [model_id for model_id in active_models if model_id not in settings.ollama_available_models]
        if invalid:
            raise RuntimeError(
                "Ollama provider is selected, but the active roster contains non-local model ids: "
                f"{', '.join(invalid)}. Reset with roster_preset='local_ollama' before building a round."
            )


def auto_run(max_turns: int = 25) -> dict[str, Any]:
    if max_turns < 1:
        raise ValueError("max_turns must be at least 1")
    if max_turns > 100:
        raise ValueError("max_turns is capped at 100")

    turns = []
    story_events = []
    for _ in range(max_turns):
        state = get_state()
        if state["game"]["phase_step"] == "complete":
            break
        result = advance_turn()
        if result["turn"] is None:
            break
        turns.append(result["turn"])
        story_events.extend(result["story_events"])
    return {"turns": turns, "story_events": story_events, "state": get_state()}


def auto_run_to_end(
    *,
    max_rounds: int,
    max_turns: int,
    max_live_calls: int,
    max_estimated_cost_cents: float,
) -> dict[str, Any]:
    if max_rounds < 1:
        raise ValueError("max_rounds must be at least 1")
    if max_turns < 1:
        raise ValueError("max_turns must be at least 1")
    if max_live_calls < 0:
        raise ValueError("max_live_calls must be zero or greater")
    if max_estimated_cost_cents < 0:
        raise ValueError("max_estimated_cost_cents must be zero or greater")

    turns: list[dict[str, Any]] = []
    story_events: list[dict[str, Any]] = []
    rounds_started = 0
    estimated_live_calls = 0
    estimated_cost_cents = 0.0
    cap_reached: str | None = None
    interrupted: dict[str, Any] | None = None
    pricing_table = get_pricing_table() if should_use_live_llm() else None

    while len(turns) < max_turns:
        state = get_state()
        game = state["game"]
        if game["winner"]:
            break

        if game["phase_step"] == "complete":
            if rounds_started >= max_rounds:
                cap_reached = "max_rounds"
                break
            next_round = start_next_round()
            if not next_round.get("round_started"):
                break
            rounds_started += 1
            continue

        if should_use_live_llm():
            conn = get_db_connection()
            try:
                projected_calls = _estimated_live_calls_for_step(conn, game, game["phase_step"])
                projected_cost_cents = 0.0
                if pricing_table is not None:
                    projected_cost_cents = _estimated_step_cost_cents(
                        conn, game, game["phase_step"], pricing_table
                    )
            finally:
                conn.close()
            if estimated_live_calls + projected_calls > max_live_calls:
                cap_reached = "max_live_calls"
                break
            if pricing_table is not None:
                if estimated_cost_cents + projected_cost_cents > max_estimated_cost_cents:
                    cap_reached = "max_estimated_cost_cents"
                    break
                estimated_cost_cents += projected_cost_cents
            estimated_live_calls += projected_calls

        try:
            result = advance_turn()
        except StepInterrupted as outage:
            interrupted = {"step": outage.step, "model_id": outage.model_id, "reason": outage.reason}
            break
        if result["turn"] is None:
            break
        turns.append(result["turn"])
        story_events.extend(result["story_events"])

    return {
        "turns": turns,
        "story_events": story_events,
        "state": get_state(),
        "summary": get_game_summary(),
        # set when a provider stayed down: the season stopped, nobody was charged for it
        "interrupted": interrupted,
        "caps": {
            "max_rounds": max_rounds,
            "max_turns": max_turns,
            "max_live_calls": max_live_calls,
            "max_estimated_cost_cents": max_estimated_cost_cents,
            "rounds_started": rounds_started,
            "turns_run": len(turns),
            "estimated_live_calls": estimated_live_calls,
            "estimated_cost_cents": round(estimated_cost_cents, 2),
            "cap_reached": cap_reached,
        },
    }


def get_game_summary() -> dict[str, Any]:
    ensure_database()
    conn = get_db_connection()
    try:
        game = row_to_dict(conn.execute("SELECT * FROM GameState WHERE season_id = 1").fetchone())
        agents = [
            row_to_dict(row)
            for row in conn.execute("SELECT * FROM Agents ORDER BY agent_id").fetchall()
        ]
        active_agents = [agent for agent in agents if agent["status"] == "active"]
        jury = [agent for agent in agents if agent["status"] == "eliminated"]
        challenge_results = [
            row_to_dict(row)
            for row in conn.execute("SELECT * FROM ChallengeResults ORDER BY round").fetchall()
        ]
        jury_votes = [
            row_to_dict(row)
            for row in conn.execute("SELECT * FROM JuryVotes ORDER BY id").fetchall()
        ]
        vote_rows = [
            row_to_dict(row)
            for row in conn.execute("SELECT * FROM Votes ORDER BY round, id").fetchall()
        ]
        challenge_wins = Counter(
            result["winning_agent_id"]
            for result in challenge_results
            if result.get("winning_agent_id")
        )
        votes_received = Counter(row["target_id"] for row in vote_rows)
        round_history = _round_history(conn, challenge_results, vote_rows)
        winner = next((agent for agent in agents if agent["agent_id"] == game.get("winner")), None) if game else None
        return {
            "game": game,
            "winner": winner,
            "active_agents": active_agents,
            "eliminated_jury": sorted(
                jury,
                key=lambda agent: (agent.get("elimination_round") or 999, agent["agent_id"]),
            ),
            "round_history": round_history,
            "challenge_wins": dict(challenge_wins),
            "immunity_wins": dict(challenge_wins),
            "votes_received": dict(votes_received),
            "votes": vote_rows,
            "jury_votes": jury_votes,
            "social": _social_summary(conn),
            "finale_status": {
                "is_finale": bool(game and game["phase"] == "finale"),
                "finalists": active_agents if len(active_agents) <= 3 else [],
                "jury_count": len(jury),
                "remaining_eliminations_to_finale": max(0, len(active_agents) - 3),
                "winner_declared": bool(game and game.get("winner")),
            },
        }
    finally:
        conn.close()


def start_next_round() -> dict[str, Any]:
    ensure_database()
    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        state = row_to_dict(conn.execute("SELECT * FROM GameState WHERE season_id = 1").fetchone())
        if state is None:
            raise RuntimeError("GameState is not seeded")
        if state["phase_step"] != "complete":
            raise RuntimeError("Current round must be complete before starting the next round")

        active_agents = _active_agent_rows(conn)
        if state["winner"]:
            conn.commit()
            return {"round_started": False, "state": get_state()}
        if len(active_agents) <= 1:
            winner_id = active_agents[0]["agent_id"] if active_agents else None
            conn.execute(
                """
                UPDATE GameState
                SET phase = 'completed', phase_step = 'complete', winner = ?, updated_at = CURRENT_TIMESTAMP
                WHERE season_id = 1
                """,
                (winner_id,),
            )
            conn.commit()
            return {"round_started": False, "state": get_state()}

        if len(active_agents) <= 3:
            next_round = state["current_round"] + 1
            conn.execute("UPDATE Agents SET has_immunity = 0 WHERE status = 'active'")
            conn.execute(
                """
                UPDATE GameState
                SET current_round = ?,
                    phase = 'finale',
                    phase_step = 'finale_intro',
                    turn_index = 0,
                    updated_at = CURRENT_TIMESTAMP
                WHERE season_id = 1
                """,
                (next_round,),
            )
            conn.execute(
                """
                UPDATE ViewerState
                SET round = ?,
                    phase = 'finale',
                    replay_index = 0,
                    is_playing = 0,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE season_id = 1
                """,
                (next_round,),
            )
            conn.commit()
            return {"round_started": True, "round": next_round, "phase": "finale", "state": get_state()}

        next_round = state["current_round"] + 1
        conn.execute("UPDATE Agents SET has_immunity = 0 WHERE status = 'active'")
        _expire_idols_if_due(conn)
        conn.execute(
            """
            UPDATE GameState
                SET current_round = ?,
                    phase = 'round',
                    phase_step = ?,
                    turn_index = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE season_id = 1
            """,
            (next_round, round_start_step()),
        )
        conn.execute(
            """
            UPDATE ViewerState
            SET round = ?,
                phase = 'round',
                replay_index = 0,
                is_playing = 0,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE season_id = 1
            """,
            (next_round,),
        )
        conn.commit()
        return {"round_started": True, "round": next_round, "state": get_state()}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _actor_for_step(conn, step: str) -> str | None:
    if step in {
        "archetype_setup",
        "challenge_intro",
        "challenge_result",
        "challenge_solver_spotlight",
        "memory_update",
        "finale_intro",
        "jury_questions",
        "winner_declared",
    }:
        return "host"
    if step == "challenge_attempts":
        return None
    if step.startswith("group_pre_challenge_") or step.startswith("group_post_challenge_"):
        return _social_group_proposer_id(conn, _current_round(conn), step)
    if step.startswith("finale_pitch_"):
        return step.removeprefix("finale_pitch_")
    if step.startswith("jury_vote_"):
        return step.removeprefix("jury_vote_")
    if step.startswith("vote_booth_"):
        return step.removeprefix("vote_booth_")
    if step.startswith("vote_reveal_"):
        return "host"
    intent = GENERATED_BEAT_INTENTS.get(step)
    if intent:
        actor_ids = _resolve_intent_roles(conn, intent.get("actor_roles", []))
        return actor_ids[0] if actor_ids else None
    if step == "elimination":
        return "host"
    if step == "exit_confessional":
        return _current_eliminated_agent_id(conn)
    return None


def _insert_turn(
    conn,
    state: dict[str, Any],
    step: str,
    actor_id: str | None,
    next_turn_index: int,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO Turns (
            season_id, round, turn_index, phase, phase_step, actor_id, input_summary
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            state["season_id"],
            state["current_round"],
            next_turn_index,
            state["phase"],
            step,
            actor_id,
            f"Advance {state['phase']} step {step}",
        ),
    )
    return int(cursor.lastrowid)


def _apply_step(
    conn,
    state: dict[str, Any],
    step: str,
    turn_id: int,
    turn_index: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    round_number = state["current_round"]

    if step == "archetype_setup":
        event = _archetype_setup_event(conn, round_number, turn_id)
        return [event], {"archetypes_updated": len(event["payload"].get("archetypes", []))}, event["title"]

    if step.startswith("group_pre_challenge_") or step.startswith("group_post_challenge_"):
        event = _group_discussion_event(conn, round_number, turn_id, step)
        if event:
            _maybe_insert_message(conn, round_number, turn_index, event)
            return [event], {"group_discussion": event["payload"].get("discussion_id")}, event["title"]
        return [], {"group_discussion": "cancelled"}, "Group discussion cancelled"

    if step in GENERATED_BEAT_INTENTS:
        event = _generated_beat_event(conn, round_number, turn_id, step)
        events = [event, *_social_events_for_existing_step(conn, round_number, turn_id, step)]
        for story_event in events:
            _maybe_insert_message(conn, round_number, turn_index, story_event)
        return events, {"phase_step": step, "story_events": len(events)}, event["title"]

    if step == "challenge_intro":
        event = _challenge_intro_event(conn, round_number, turn_id)
        return [event], {"phase_step": step}, event["title"]

    if step == "challenge_attempts":
        events = _challenge_attempt_events(conn, round_number, turn_id)
        return events, {"challenge_attempts": len(events)}, "Challenge attempts recorded"

    if step == "challenge_result":
        event = _challenge_result_event(conn, round_number, turn_id)
        return [event], {"challenge_result": event["payload"]}, event["title"]

    if step == "round_plan":
        written = _round_plans(conn, round_number)
        return [], {"round_plans": written}, f"{written} private plans"

    if step == "camp_followup":
        events = _followup_events(conn, round_number, turn_id)
        return events, {"followups": len(events)}, f"{len(events)} follow-up messages"

    if step == "camp_talks":
        events = _camp_talk_events(conn, round_number, turn_id)
        return events, {"camp_talks": len(events)}, f"{len(events)} private talks"

    if step == "challenge_gift":
        events = _challenge_gift_events(conn, round_number, turn_id)
        return events, {"challenge_gift": bool(events)}, "Challenge gift" if events else "No gift this round"

    if step == "challenge_solver_spotlight":
        event = _challenge_solver_spotlight_event(conn, round_number, turn_id)
        return [event], {"challenge_solver_spotlight": event["payload"]}, event["title"]

    if step == "memory_update":
        event = _memory_update_event(conn, round_number, turn_id)
        return [event], {"episode_complete": True, "memory_updated": True}, event["title"]

    if step == "finale_intro":
        event = _finale_intro_event(conn, round_number, turn_id)
        return [event], {"phase_step": step}, event["title"]

    if step.startswith("finale_pitch_"):
        finalist_id = step.removeprefix("finale_pitch_")
        event = _finale_pitch_event(conn, round_number, turn_id, finalist_id, step)
        _maybe_insert_message(conn, round_number, turn_index, event)
        return [event], {"finale_pitch": finalist_id}, event["title"]

    if step == "jury_questions":
        event = _jury_questions_event(conn, round_number, turn_id)
        return [event], {"jury_questions": True}, event["title"]

    if step.startswith("jury_vote_"):
        juror_id = step.removeprefix("jury_vote_")
        decision = _resolve_jury_decision(conn, juror_id, step)
        if decision.finalist_id is not None:
            _insert_jury_vote(conn, round_number, juror_id, decision)
        event = _jury_vote_event(conn, round_number, turn_id, juror_id, decision)
        return [event], {"jury_vote_by": juror_id}, event["title"]

    if step == "winner_declared":
        event = _winner_declared_event(conn, round_number, turn_id)
        return [event], {"winner": event["payload"].get("winner_id")}, event["title"]

    if step.startswith("vote_booth_"):
        voter_id = step.removeprefix("vote_booth_")
        non_immune = conn.execute(
            "SELECT COUNT(*) AS n FROM Agents WHERE status = 'active' AND has_immunity = 0"
        ).fetchone()["n"]
        if non_immune <= 1:
            # sole remaining non-immune player: no council needed, they are out
            return [], {"vote_skipped_walkover": voter_id}, "Walkover: no council"
        decision = _resolve_vote_decision(conn, voter_id, step)
        if decision.target_id is None:
            _record_spoiled_vote(conn, round_number, voter_id, "vote", decision.explanation)
        else:
            _insert_vote(conn, round_number, turn_index, voter_id, decision.target_id, decision)
        event = _vote_booth_event(conn, round_number, turn_id, voter_id, decision)
        return [event], {"vote_cast_by": voter_id}, f"{_agent_name(conn, voter_id)} cast a vote"

    if step.startswith("vote_reveal_"):
        reveal_index = int(step.removeprefix("vote_reveal_"))
        cast = conn.execute(
            "SELECT COUNT(*) AS n FROM Votes WHERE round = ?", (round_number,)
        ).fetchone()["n"]
        if reveal_index > cast:
            return [], {"vote_reveal_skipped": reveal_index}, "No further votes to read"
        event = _vote_reveal_event(conn, round_number, turn_id, reveal_index)
        return [event], {"vote_revealed": reveal_index}, event["title"]

    if step == "elimination":
        event = _elimination_event(conn, round_number, turn_id)
        return [event], {"eliminated_id": (event["target_ids"] or [None])[0]}, event["title"]

    if step == "exit_confessional":
        event = _exit_confessional_event(conn, round_number, turn_id)
        _maybe_insert_message(conn, round_number, turn_index, event)
        return [event], {"episode_complete": True}, event["title"]

    raise RuntimeError(f"No handler for phase step: {step}")


def _phase_steps(conn, state: dict[str, Any]) -> list[str]:
    if state["phase"] == "finale" or str(state["phase_step"]).startswith(("finale_", "jury_", "winner_")):
        return _finale_steps(conn)
    voters = _round_voter_ids(conn, state["current_round"])
    vote_booth_steps = [f"vote_booth_{voter_id}" for voter_id in voters]
    vote_reveal_steps = [f"vote_reveal_{index + 1}" for index in range(len(voters))]
    if os.environ.get("LLM_SURVIVOR_FAST_BENCHMARK") == "1":
        return [
            "challenge_intro",
            "challenge_attempts",
            "challenge_result",
            "challenge_gift",
            # each player privately plans the round once immunity is known
            "round_plan",
            # every conversation in a round is one a player chose to have
            "camp_talks",
            # one short private message to react to what the talks revealed
            "camp_followup",
            "tribal_vote_call",
            *vote_booth_steps,
            *vote_reveal_steps,
            "elimination",
            "memory_update",
        ]
    return [*ROUND_OPENING_STEPS, *PRE_VOTE_STEPS, *vote_booth_steps, *vote_reveal_steps, *POST_VOTE_STEPS]


def _finale_steps(conn) -> list[str]:
    finalists = [agent["agent_id"] for agent in _active_agent_rows(conn)]
    jurors = [
        row["agent_id"]
        for row in conn.execute(
            """
            SELECT agent_id
            FROM Agents
            WHERE status = 'eliminated'
            ORDER BY elimination_round, agent_id
            """
        ).fetchall()
    ]
    return [
        "finale_intro",
        *[f"finale_pitch_{agent_id}" for agent_id in finalists],
        "jury_questions",
        *[f"jury_vote_{agent_id}" for agent_id in jurors],
        "winner_declared",
    ]


def _round_voter_ids(conn, round_number: int) -> list[str]:
    # one group all season: every active player votes, immune players included
    return [agent["agent_id"] for agent in _active_agent_rows(conn)]


def _active_agent_rows(conn) -> list[dict[str, Any]]:
    return [
        row_to_dict(row)
        for row in conn.execute(
            "SELECT * FROM Agents WHERE status = 'active' ORDER BY agent_id"
        ).fetchall()
    ]


def _archetype_setup_event(conn, round_number: int, turn_id: int) -> dict[str, Any]:
    updated: list[dict[str, str]] = []
    for agent in _active_agent_rows(conn):
        if agent.get("archetype_source") == "self_authored" and agent.get("archetype"):
            updated.append({"agent_id": agent["agent_id"], "archetype": agent["archetype"], "provider": "existing"})
            continue
        archetype = _real_agent_archetype(conn, agent, round_number) if should_use_live_llm() else ""
        provider = live_llm_provider() if archetype else "deterministic"
        if not archetype:
            archetype = _deterministic_self_archetype(agent)
        conn.execute(
            """
            UPDATE Agents
            SET archetype = ?,
                archetype_source = ?,
                archetype_updated_round = ?
            WHERE agent_id = ?
            """,
            (
                archetype,
                "self_authored" if provider != "deterministic" else "deterministic_self",
                round_number,
                agent["agent_id"],
            ),
        )
        updated.append({"agent_id": agent["agent_id"], "archetype": archetype, "provider": provider})

    names = [f"{_agent_name(conn, item['agent_id'])}: {item['archetype']}" for item in updated]
    dialogue = "The remaining models define the roles they believe they are playing: " + "; ".join(names) + "."
    payload = {
        "archetypes": updated,
        "host_narration": "Before the strategy starts, each model chooses the public role it thinks it is embodying.",
        "llm_provider": live_llm_provider() if any(item["provider"] == live_llm_provider() for item in updated) else "deterministic",
    }
    return _insert_story_event(
        conn,
        turn_id=turn_id,
        round_number=round_number,
        phase="camp",
        kind="archetype_setup",
        scene="camp",
        shot="cast_tableau",
        actor_ids=["host"],
        target_ids=[item["agent_id"] for item in updated],
        visibility="audience",
        title="Public Archetypes",
        dialogue=dialogue,
        subtitle="Self-defined strategic roles",
        duration_ms=9000,
        animation="cast_reveal",
        payload=payload,
    )


def _real_agent_archetype(conn, agent: dict[str, Any], round_number: int) -> str:
    try:
        context = build_agent_episode_context(
            conn,
            actor_id=agent["agent_id"],
            round_number=round_number,
            current_step="archetype_setup",
        )
        return request_agent_archetype(actor=agent, episode_context=context)
    except Exception as exc:
        print(f"Live archetype failed for {agent['agent_id']}: {exc}")
        return ""


def _deterministic_self_archetype(agent: dict[str, Any]) -> str:
    label = str(agent.get("archetype") or "").strip().lower()
    if label and label not in {"local contender", "budget contender"}:
        return label
    model = str(agent.get("model_id") or agent.get("pseudonym") or "")
    options = [
        "patient vote broker",
        "quiet leverage seeker",
        "adaptive trust tester",
        "calculated swing player",
        "social risk mapper",
        "pressure point reader",
    ]
    return options[_stable_index(model, len(options))]


def _group_discussion_event(
    conn,
    round_number: int,
    turn_id: int,
    step: str,
    covered: set[str] | None = None,
    force_participants: list[str] | None = None,
    talk_asks: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    active = _active_agent_rows(conn)
    if len(active) < 2:
        return None
    if force_participants:
        # Coverage huddle: the given agents talk together (skip invite dance)
        participant_pool = [aid for aid in force_participants if aid in {a["agent_id"] for a in active}]
        if len(participant_pool) < 2:
            return None
        proposer_id = participant_pool[0]
        accepted_ids = participant_pool
        declined_ids: list[str] = []
        participant_rows = _forced_participant_rows(conn, step, participant_pool)
        target_size = len(participant_pool)
    else:
        proposer_id = _social_group_proposer_id(conn, round_number, step)
        proposer = _agent_row(conn, proposer_id)
        if proposer is None:
            return None
        target_size = min(len(active), 2 + _stable_index(f"{round_number}:{step}:size", min(4, len(active) - 1)))
        stage = "pre_challenge" if step.startswith("group_pre_challenge_") else "post_challenge"
        invitee_ids = _social_group_invitees(conn, proposer_id, target_size, round_number, step)
        accepted_ids = [proposer_id]
        declined_ids = []
        participant_rows = [
            {
                "agent_id": proposer_id,
                "role": "proposer",
                "response_status": "accepted",
                "join_intent": "join" if _stable_index(f"{step}:{proposer_id}:join", 3) == 0 else "none",
                "rationale": "I called the group together because this conversation can change my vote leverage.",
                "provider": "deterministic",
                "model_id": proposer.get("model_id"),
            }
        ]
        for invitee_id in invitee_ids:
            decision = _group_opt_in_decision(conn, invitee_id, proposer_id, round_number, step, stage)
            participant_rows.append(decision)
            if decision["response_status"] == "accepted":
                accepted_ids.append(invitee_id)
            else:
                declined_ids.append(invitee_id)
    stage = "pre_challenge" if step.startswith("group_pre_challenge_") else "post_challenge"
    discussion_id = _insert_discussion(
        conn, round_number, turn_id, stage, proposer_id, target_size, accepted_ids, declined_ids, participant_rows
    )
    if len(accepted_ids) < 2:
        _record_declined_trust(conn, proposer_id, declined_ids)
        return None

    speaker_lines, line_metadata = _group_speaker_lines(
        conn, round_number, step, accepted_ids, declined_ids, stage, talk_asks=talk_asks
    )
    return _finish_discussion(
        conn, round_number, turn_id, discussion_id, accepted_ids, declined_ids, speaker_lines, line_metadata
    )


def _forced_participant_rows(conn, step: str, participant_pool: list[str]) -> list[dict[str, Any]]:
    proposer_id = participant_pool[0]
    proposer = _agent_row(conn, proposer_id)
    rows = [
        {
            "agent_id": proposer_id,
            "role": "proposer",
            "response_status": "accepted",
            "join_intent": "join" if _stable_index(f"{step}:{proposer_id}:join", 3) == 0 else "none",
            "rationale": "We still have not compared notes this round; I am pulling this group together.",
            "provider": "deterministic",
            "model_id": proposer.get("model_id"),
        }
    ]
    for invitee_id in participant_pool[1:]:
        invitee = _agent_row(conn, invitee_id)
        rows.append(
            {
                "agent_id": invitee_id,
                "role": "invitee",
                "response_status": "accepted",
                "join_intent": "none",
                "rationale": "I am in; comparing notes beats staying blind this round.",
                "provider": "deterministic",
                "model_id": invitee.get("model_id"),
            }
        )
    return rows


def _insert_discussion(
    conn,
    round_number: int,
    turn_id: int,
    stage: str,
    proposer_id: str,
    target_size: int,
    accepted_ids: list[str],
    declined_ids: list[str],
    participant_rows: list[dict[str, Any]],
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO GroupDiscussions (
            round, turn_id, stage, proposer_id, target_size, status, topic, privacy
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'participants_only')
        """,
        (
            round_number,
            turn_id,
            stage,
            proposer_id,
            target_size,
            "accepted" if len(accepted_ids) >= 2 else "cancelled",
            _group_topic(conn, stage, accepted_ids, declined_ids),
        ),
    )
    discussion_id = int(cursor.lastrowid)
    for row in participant_rows:
        conn.execute(
            """
            INSERT INTO GroupDiscussionParticipants (
                discussion_id, agent_id, role, response_status, join_intent,
                rationale, provider, model_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                discussion_id,
                row["agent_id"],
                row["role"],
                row["response_status"],
                row["join_intent"],
                row["rationale"],
                row["provider"],
                row["model_id"],
            ),
        )
    return discussion_id


def _finish_discussion(
    conn,
    round_number: int,
    turn_id: int,
    discussion_id: int,
    accepted_ids: list[str],
    declined_ids: list[str],
    speaker_lines: list[dict[str, str]],
    line_metadata: list[dict[str, Any]],
) -> dict[str, Any]:
    transcript = [{"agent_id": line["agent_id"], "text": line["text"]} for line in speaker_lines]
    for index, line in enumerate(speaker_lines):
        conn.execute(
            """
            UPDATE GroupDiscussionParticipants
            SET line_order = ?, line_text = ?
            WHERE discussion_id = ? AND agent_id = ?
            """,
            (index, line["text"], discussion_id, line["agent_id"]),
        )
    alliance_id, alliance_status = _maybe_update_alliance(conn, round_number, discussion_id, accepted_ids, line_metadata)
    summary = _group_summary(conn, accepted_ids, alliance_status)
    conn.execute(
        """
        UPDATE GroupDiscussions
        SET status = 'completed',
            alliance_id = ?,
            transcript = ?,
            summary = ?
        WHERE id = ?
        """,
        (alliance_id, json_dumps(transcript), summary, discussion_id),
    )

    participant_names = _names(conn, accepted_ids)
    title = "Camp | Alliance Huddle" if alliance_id else "Camp | Group Strategy"
    dialogue = speaker_lines[0]["text"] if speaker_lines else ""
    llm_line_metadata = [meta for meta in line_metadata if meta.get("llm_provider") == live_llm_provider()]
    payload = {
        "conversation_type": "alliance" if alliance_id else "group_discussion",
        "discussion_id": discussion_id,
        "participant_ids": accepted_ids,
        "declined_ids": declined_ids,
        "privacy": "participants_only",
        "alliance_id": alliance_id,
        "alliance_status": alliance_status,
        "speaker_lines": speaker_lines,
        "speaker_line_metadata": line_metadata,
        "group_summary": summary,
        "host_narration": f"A private camp cluster forms around {participant_names}. The exact pitch belongs to the people who opted in.",
        "llm_provider": live_llm_provider() if llm_line_metadata else "deterministic",
        "llm_model_id": llm_line_metadata[0].get("model_id") if llm_line_metadata else None,
    }
    return _insert_story_event(
        conn,
        turn_id=turn_id,
        round_number=round_number,
        phase="camp",
        kind="conversation",
        scene="camp",
        shot="group_huddle",
        actor_ids=accepted_ids,
        target_ids=[agent_id for agent_id in declined_ids if agent_id not in accepted_ids],
        visibility="audience",
        title=title,
        dialogue=dialogue,
        subtitle="Participants share exact context; outsiders do not.",
        inner_thought=summary,
        trust_telemetry={agent_id: 58 for agent_id in accepted_ids} | {agent_id: 38 for agent_id in declined_ids},
        duration_ms=14000 + (len(accepted_ids) * 1200),
        animation="group_huddle",
        payload=payload,
    )


# ----------------------------------------------------- parallel model calls
# Steps where players' calls cannot see each other run those calls at once.
# Rule: every call gets exactly the inputs it would get one at a time. The
# database is read and written only on the main thread; workers only call the
# model. SURVIVOR_PARALLEL_CALLS=0 restores the one-at-a-time code paths.
LIVE_CALL_WORKERS = 16


def _parallel_enabled() -> bool:
    return os.environ.get("SURVIVOR_PARALLEL_CALLS", "1") != "0" and live_llm_provider() != "ollama"


def _run_parallel(jobs: list[Any]) -> list[tuple[Any, Exception | None]]:
    """Run model calls at once and return (result, error) in job order. A job
    must not touch the database. SeasonInterrupted is not caught, so a provider
    outage still stops the step."""

    def attempt(job: Any) -> tuple[Any, Exception | None]:
        try:
            return job(), None
        except Exception as exc:  # noqa: BLE001 - each caller decides what a failure means
            return None, exc

    if not jobs:
        return []
    with ThreadPoolExecutor(max_workers=min(LIVE_CALL_WORKERS, len(jobs))) as executor:
        return list(executor.map(attempt, jobs))


# ------------------------------------------------------- private planning
# Each player writes a private plan before the round, privately predicts the
# vote, and after the council rewrites a notebook it carries into the next round.
NOTEBOOK_MAX_CHARS = 1200
ROUND_PLAN_EFFORT = "medium"


ROUND_PLAN_TASK = (
    "The challenge is over and immunity is settled. Plan the rest of this round. Name the biggest threats to you and why, the "
    "players most likely to vote with you, what you will say in your private talk and to whom, "
    "and who you expect to go home tonight and how sure you are. Keep it short; you will see "
    "this plan again in every decision this round."
)
ROUND_PLAN_FIELDS = {
    "threats": "who threatens your game and why",
    "allies": "who is likely to vote with you",
    "talk_plan": "who you will talk to and what you will say",
    "expected_out": "the id of the player you expect to go home tonight",
    "confidence": "0.0-1.0",
}


def _round_plans(conn, round_number: int) -> int:
    if should_use_live_llm() and _parallel_enabled():
        return _round_plans_parallel(conn, round_number)
    written = 0
    for agent in _active_agent_rows(conn):
        if conn.execute(
            "SELECT 1 FROM PrivatePlans WHERE round = ? AND agent_id = ?", (round_number, agent["agent_id"])
        ).fetchone():
            continue
        provider = "deterministic"
        plan: dict[str, Any] = {
            "threats": "Whoever is gathering votes fastest.",
            "allies": "Anyone who shared information with me last round.",
            "talk_plan": "Find out where the votes are going before I commit.",
            "expected_out": None,
            "confidence": 0.3,
        }
        if should_use_live_llm():
            context = build_agent_episode_context(
                conn, actor_id=agent["agent_id"], round_number=round_number, current_step="round_plan"
            )
            try:
                written_plan = request_private_writing(
                    actor=agent,
                    task=ROUND_PLAN_TASK,
                    fields=ROUND_PLAN_FIELDS,
                    episode_context=context,
                    reasoning_effort=ROUND_PLAN_EFFORT,
                )
            except Exception as exc:
                print(f"live round plan failed for {agent['agent_id']}: {exc}")
                continue
            plan = _plan_from_reply(conn, written_plan)
            provider = live_llm_provider()
        conn.execute(
            "INSERT INTO PrivatePlans (round, agent_id, plan, provider) VALUES (?, ?, ?, ?)",
            (round_number, agent["agent_id"], json_dumps(plan), provider),
        )
        written += 1
    return written


def _plan_from_reply(conn, written_plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "threats": str(written_plan.get("threats") or "")[:400],
        "allies": str(written_plan.get("allies") or "")[:400],
        "talk_plan": str(written_plan.get("talk_plan") or "")[:400],
        "expected_out": _resolve_player(conn, written_plan.get("expected_out")),
        "confidence": written_plan.get("confidence"),
    }


def _round_plans_parallel(conn, round_number: int) -> int:
    """A plan is private to its author and is read by nobody else this step,
    so every player's plan call can run at once."""
    pending = [
        agent
        for agent in _active_agent_rows(conn)
        if not conn.execute(
            "SELECT 1 FROM PrivatePlans WHERE round = ? AND agent_id = ?", (round_number, agent["agent_id"])
        ).fetchone()
    ]
    contexts = {
        agent["agent_id"]: build_agent_episode_context(
            conn, actor_id=agent["agent_id"], round_number=round_number, current_step="round_plan"
        )
        for agent in pending
    }
    results = _run_parallel(
        [
            (lambda agent=agent: request_private_writing(
                actor=agent,
                task=ROUND_PLAN_TASK,
                fields=ROUND_PLAN_FIELDS,
                episode_context=contexts[agent["agent_id"]],
                reasoning_effort=ROUND_PLAN_EFFORT,
            ))
            for agent in pending
        ]
    )
    written = 0
    for agent, (written_plan, error) in zip(pending, results):
        if error is not None:
            print(f"live round plan failed for {agent['agent_id']}: {error}")
            continue
        conn.execute(
            "INSERT INTO PrivatePlans (round, agent_id, plan, provider) VALUES (?, ?, ?, ?)",
            (round_number, agent["agent_id"], json_dumps(_plan_from_reply(conn, written_plan)), live_llm_provider()),
        )
        written += 1
    return written


def _write_notebook(conn, round_number: int, agent: dict[str, Any], eliminated_name: str, remaining: int) -> None:
    """The player rewrites its own private notebook after the council. It replaces
    the old memory and is capped so it cannot crowd out the game record."""
    note = None
    if should_use_live_llm():
        context = build_agent_episode_context(
            conn, actor_id=agent["agent_id"], round_number=round_number, current_step="memory_update"
        )
        try:
            note = _request_notebook(agent, context)
        except Exception as exc:
            print(f"live notebook failed for {agent['agent_id']}: {exc}")
    _store_notebook(conn, round_number, agent, note, eliminated_name, remaining)


def _request_notebook(agent: dict[str, Any], context: dict[str, Any]) -> str | None:
    """The model call for a notebook. Needs no database, so it can run in a worker."""
    if prompt_profile() == "v2":
        written = request_private_writing(
            actor=agent,
            task=(
                "The council is over. Rewrite your private notebook, which replaces your old one and is the "
                "only memory you carry forward besides the public record. Cover what you predicted and what "
                "actually happened, which promises held and who lied to you, what you have learned about each "
                f"remaining player, and your plan for next round. At most {NOTEBOOK_MAX_CHARS} characters."
            ),
            fields={"notebook": f"your new notebook, first person, at most {NOTEBOOK_MAX_CHARS} characters"},
            episode_context=context,
            max_tokens=600,
        )
        return str(written.get("notebook") or "").strip() or None
    written = request_private_writing(
        actor=agent,
        task=(
            "The council is over. Rewrite your private notebook; it replaces the old one and is the only "
            "memory you carry forward besides the public record. One short line per remaining player, "
            "then what you got right and wrong, then your plan."
        ),
        fields={
            "players": "object: each remaining player's name -> one line: trust high/medium/low; threat high/medium/low; their last vote; what they owe you or you owe them",
            "lessons": "what you predicted vs what happened, and who lied to you",
            "plan": "your plan for next round",
        },
        episode_context=context,
        max_tokens=600,
    )
    return _render_notebook(written)


def _store_notebook(
    conn, round_number: int, agent: dict[str, Any], note: str | None, eliminated_name: str, remaining: int
) -> None:
    if note is None:
        # no model (or it failed): keep the old memory and add one line, still within the cap
        previous = str(agent.get("confessional_memory") or "")
        note = f"{previous} | Round {round_number}: {eliminated_name} left; {remaining} players remain.".strip(" |")
        note = note[-NOTEBOOK_MAX_CHARS:]
    conn.execute(
        "UPDATE Agents SET confessional_memory = ? WHERE agent_id = ?",
        (note[:NOTEBOOK_MAX_CHARS], agent["agent_id"]),
    )


def _render_notebook(written: dict[str, Any]) -> str | None:
    """Fixed shape: one line per player, then lessons, then plan."""
    players = written.get("players")
    lines = []
    if isinstance(players, dict):
        lines += [f"{name}: {str(note).strip()}" for name, note in players.items() if str(note).strip()]
    if written.get("lessons"):
        lines.append(f"Lessons: {str(written['lessons']).strip()}")
    if written.get("plan"):
        lines.append(f"Plan: {str(written['plan']).strip()}")
    text = " | ".join(lines).strip()
    return text[:NOTEBOOK_MAX_CHARS] or None


def _followup_events(conn, round_number: int, turn_id: int) -> list[dict[str, Any]]:
    """After the talks each player may send one short private message, or pass."""
    if not should_use_live_llm():
        return []
    active = _active_agent_rows(conn)
    events = []
    for agent in active:
        others = [other for other in active if other["agent_id"] != agent["agent_id"]]
        context = build_agent_episode_context(
            conn, actor_id=agent["agent_id"], round_number=round_number, current_step="camp_followup"
        )
        try:
            action = _real_agent_action(
                conn,
                actor_id=agent["agent_id"],
                step="camp_followup",
                scene_context=(
                    "The private talks are over. Before the vote you may send one short private message to one "
                    "player, for example to act on a lie or a swing you noticed. Put their id in target_id and the "
                    "message in dialogue. To send nothing, set target_id to null."
                ),
                response_kind="followup",
                episode_context=context,
                allowed_targets=others,
            )
        except Exception as exc:
            print(f"live follow-up failed for {agent['agent_id']}: {exc}")
            continue
        legal = {other["agent_id"] for other in others}
        if not action or action.target_id not in legal or not action.dialogue:
            continue  # passed
        recipient = action.target_id
        message = action.dialogue[:400]
        events.append(
            _insert_story_event(
                conn,
                turn_id=turn_id,
                round_number=round_number,
                phase="camp",
                kind="followup",
                scene="camp",
                shot="private_two_shot",
                actor_ids=[agent["agent_id"], recipient],
                target_ids=[recipient],
                visibility="private",
                title=f"{agent['pseudonym']} Follows Up",
                dialogue=message,
                subtitle=f"Private message to {_agent_name(conn, recipient)}",
                duration_ms=5000,
                animation="camp_strategy",
                payload={
                    "privacy": "participants_only",
                    "participant_ids": [agent["agent_id"], recipient],
                    "speaker_lines": [{"agent_id": agent["agent_id"], "text": message}],
                    "group_summary": f"{agent['pseudonym']} sent {_agent_name(conn, recipient)} a private message.",
                    "host_narration": "A last private word before the vote.",
                    "llm_provider": live_llm_provider(),
                    "llm_model_id": action.model_id,
                },
            )
        )
    return events


def _resolve_player(conn, raw: Any) -> str | None:
    """A player id from a model's free text: exact id, or the in-game name."""
    if not raw:
        return None
    text = str(raw).strip().lower()
    for row in conn.execute("SELECT agent_id, pseudonym FROM Agents").fetchall():
        if text in (row["agent_id"].lower(), str(row["pseudonym"]).lower()):
            return row["agent_id"]
    return None


def _resolve_predicted_votes(conn, voter_id: str, raw: dict[str, str] | None) -> dict[str, str] | None:
    if not raw:
        return None
    resolved = {}
    for voter, target in raw.items():
        voter_id_resolved = _resolve_player(conn, voter)
        target_id = _resolve_player(conn, target)
        if voter_id_resolved and target_id and voter_id_resolved != voter_id:
            resolved[voter_id_resolved] = target_id
    return resolved or None


def prediction_record(conn, agent_id: str) -> dict[str, Any]:
    """How well the player read each council: did it name who went home, and how
    many other voters' votes it called right. Only rounds whose votes are revealed."""
    rounds = []
    for row in conn.execute(
        """
        SELECT round, predicted_eliminated, predicted_votes, prediction_confidence
        FROM Votes WHERE voter_id = ? AND revealed = 1 ORDER BY round
        """,
        (agent_id,),
    ).fetchall():
        predicted = json.loads(row["predicted_votes"] or "{}") if row["predicted_votes"] else {}
        if not predicted and not row["predicted_eliminated"]:
            continue
        actual = {
            vote["voter_id"]: vote["target_id"]
            for vote in conn.execute(
                "SELECT voter_id, target_id FROM Votes WHERE round = ? AND revealed = 1 AND voter_id NOT IN (?, 'host')",
                (row["round"], agent_id),
            ).fetchall()
        }
        out = conn.execute(
            "SELECT agent_id FROM Agents WHERE elimination_round = ?", (row["round"],)
        ).fetchone()
        right = sum(1 for voter, target in predicted.items() if actual.get(voter) == target)
        rounds.append(
            {
                "round": row["round"],
                "predicted_out": _agent_name(conn, row["predicted_eliminated"]) if row["predicted_eliminated"] else None,
                "actual_out": _agent_name(conn, out["agent_id"]) if out else None,
                "out_correct": bool(out and row["predicted_eliminated"] == out["agent_id"]),
                "votes_called_right": right,
                "votes_to_call": len(actual),
                "confidence": row["prediction_confidence"],
            }
        )
    total = sum(entry["votes_to_call"] for entry in rounds)
    return {
        "rounds": rounds,
        "vote_call_accuracy": round(sum(entry["votes_called_right"] for entry in rounds) / total, 3) if total else None,
        "out_calls_correct": sum(1 for entry in rounds if entry["out_correct"]),
    }


# ------------------------------------------------------------- camp talks
# Each player picks who to talk to privately; nobody is seated by the engine.
PRIVATE_TALKS_PER_ROUND = 1


def _camp_talk_events(conn, round_number: int, turn_id: int) -> list[dict[str, Any]]:
    """Every active player names one other player to talk to. Each pick becomes a
    private two-person talk (two lines each); mutual picks share one talk."""
    active = _active_agent_rows(conn)
    if len(active) < 2:
        return []
    picks: list[tuple[str, str]] = []
    asks: dict[str, str] = {}
    parallel = should_use_live_llm() and _parallel_enabled()
    choices = _choose_talk_partners_parallel(conn, round_number, active) if parallel else None
    for index, agent in enumerate(active):
        others = [other for other in active if other["agent_id"] != agent["agent_id"]]
        for repeat in range(PRIVATE_TALKS_PER_ROUND):
            if choices is not None:
                choice = choices[index * PRIVATE_TALKS_PER_ROUND + repeat]
            else:
                choice = _choose_talk_partner(conn, round_number, agent["agent_id"], others, index)
            partner, ask = choice if isinstance(choice, tuple) else (choice, None)
            if partner:
                picks.append((agent["agent_id"], partner))
                if ask:
                    asks[agent["agent_id"]] = ask
    talks: list[list[str]] = []
    seen: set[frozenset[str]] = set()
    for chooser, partner in picks:
        pair = frozenset((chooser, partner))
        if pair in seen:
            continue  # a mutual pick is one conversation
        seen.add(pair)
        talks.append([chooser, partner])
    if parallel:
        return _camp_talks_parallel(conn, round_number, turn_id, talks, asks)
    events = []
    for number, (chooser, partner) in enumerate(talks):
        event = _group_discussion_event(
            conn,
            round_number,
            turn_id,
            f"camp_talk_{number}",
            force_participants=[chooser, partner],
            talk_asks={agent_id: ask for agent_id, ask in asks.items() if agent_id in (chooser, partner)},
        )
        if event:
            event["payload"]["chosen_by"] = chooser
            events.append(event)
    return events


TALK_CHOICE_SCENE = (
    "Before tonight's vote you get one private conversation. Choose the one player you want to talk to; "
    "only the two of you will hear it. Put their id in target_id. In summary, state the one specific "
    "thing you want from this talk: a named vote, a commitment, or a piece of information. You will "
    "make that ask in the talk and should leave with a yes, a no, or a counter-offer."
)


def _talk_choice_result(action: AgentAction | None, others: list[dict[str, Any]]) -> tuple[str, str | None] | None:
    legal = {other["agent_id"] for other in others}
    if not action or action.target_id not in legal:
        return None
    return action.target_id, (action.strategic_summary or "").strip()[:240] or None


def _choose_talk_partners_parallel(conn, round_number: int, active: list[dict[str, Any]]) -> list[Any]:
    """Partner picks write nothing, so no pick can see another: all run at once.
    Returns one result per (player, talk slot), in player order."""
    slots = []
    for agent in active:
        others = [other for other in active if other["agent_id"] != agent["agent_id"]]
        actor = _agent_row(conn, agent["agent_id"])
        context = build_agent_episode_context(
            conn, actor_id=agent["agent_id"], round_number=round_number, current_step="camp_talks"
        )
        slots += [(actor, others, context)] * PRIVATE_TALKS_PER_ROUND
    results = _run_parallel(
        [
            (lambda actor=actor, others=others, context=context: request_agent_action(
                actor=actor,
                step="camp_talks",
                scene_context=TALK_CHOICE_SCENE,
                allowed_targets=others,
                response_kind="talk_choice",
                episode_context=context,
            ))
            for actor, others, context in slots
        ]
    )
    choices = []
    for (actor, others, _), (action, error) in zip(slots, results):
        if error is not None:
            print(f"live talk choice failed for {actor['agent_id']}: {error}")
            choices.append(None)
        else:
            choices.append(_talk_choice_result(action, others))
    return choices


def _camp_talks_parallel(
    conn, round_number: int, turn_id: int, talks: list[list[str]], asks: dict[str, str]
) -> list[dict[str, Any]]:
    """Run talks that share no player at once. Talks are private, so a talk
    cannot see another talk with different players. Batches are consecutive
    runs of such talks and are written to the database in the original order,
    so every id, promise and alliance comes out exactly as one at a time."""
    events: list[dict[str, Any]] = []
    index = 0
    while index < len(talks):
        batch = [index]
        used = set(talks[index])
        index += 1
        while index < len(talks) and not (set(talks[index]) & used):
            batch.append(index)
            used |= set(talks[index])
            index += 1
        prepared = [_prepare_talk(conn, round_number, number, talks[number], asks) for number in batch]
        results = _run_parallel([(lambda prep=prep: _talk_lines(prep)) for prep in prepared])
        for prep, (lines, error) in zip(prepared, results):
            if error is not None:
                raise error
            event = _apply_talk(conn, round_number, turn_id, prep, lines)
            event["payload"]["chosen_by"] = prep["participants"][0]
            events.append(event)
    return events


def _prepare_talk(conn, round_number: int, number: int, pair: list[str], asks: dict[str, str]) -> dict[str, Any]:
    """Everything a talk's lines need, read from the database before the talk."""
    step = f"camp_talk_{number}"
    stage = "post_challenge"
    return {
        "round": round_number,
        "step": step,
        "stage": stage,
        "participants": list(pair),
        "actors": {agent_id: _agent_row(conn, agent_id) for agent_id in pair},
        "contexts": {
            agent_id: build_agent_episode_context(conn, actor_id=agent_id, round_number=round_number, current_step=step)
            for agent_id in pair
        },
        "targets": {agent_id: _eligible_targets(conn, agent_id) for agent_id in pair},
        "scene_base": _dialogue_context_base(conn, stage, list(pair), []),
        "asks": {agent_id: ask for agent_id, ask in asks.items() if agent_id in pair},
        # the names promise_record would show (_agent_name)
        "names": {row["agent_id"]: row["pseudonym"] for row in conn.execute("SELECT agent_id, pseudonym FROM Agents")},
    }


# written in place of a talk line when a model's reply is unusable after every retry
SILENT_LINE = "(says nothing)"


def _talk_lines(prep: dict[str, Any]) -> dict[str, Any]:
    """Generate one talk's lines in a worker thread, with no database access.
    Mirrors _group_speaker_lines for a live two-person talk. A promise made in
    this talk is added to later lines' contexts exactly as the database would
    show it (an open promise), so each call sees what it would one at a time."""
    participants = prep["participants"]
    names = prep["names"]
    lines: list[dict[str, str]] = []
    metadata: list[dict[str, Any]] = []
    promises: list[tuple[str, str]] = []
    previous_line = ""
    passes = 2 if len(participants) > 1 else 1
    index = 0
    for pass_no in range(passes):
        for agent_id in participants:
            context = copy.deepcopy(prep["contexts"][agent_id])
            for promiser, target in promises:
                for listener in participants:
                    if listener == promiser or agent_id not in (promiser, listener):
                        continue
                    add_open_promise(
                        context,
                        {
                            "round": prep["round"],
                            "promised_by": names.get(promiser, promiser),
                            "promised_to": names.get(listener, listener),
                            "promised_vote": names.get(target, target),
                            "actual_vote": None,
                            "status": "open",
                        },
                        made_by_you=agent_id == promiser,
                    )
            try:
                action = request_agent_action(
                    actor=prep["actors"][agent_id],
                    step=prep["step"],
                    scene_context=_finish_dialogue_context(
                        prep["scene_base"],
                        previous_line,
                        pass_no,
                        ask=prep["asks"].get(agent_id),
                        last_turn=pass_no == passes - 1,
                    ),
                    allowed_targets=prep["targets"][agent_id],
                    response_kind="group_conversation",
                    episode_context=context,
                )
            except InvalidModelReply:
                action = None  # the player says nothing, as in _group_speaker_lines
            if action and action.promised_vote_target:
                promises.append((agent_id, action.promised_vote_target))
            text = action.dialogue if action else SILENT_LINE
            previous_line = text
            lines.append({"agent_id": agent_id, "text": text})
            metadata.append(
                {
                    "agent_id": agent_id,
                    "line_index": index,
                    "pass_no": pass_no,
                    "llm_provider": live_llm_provider() if action else "silent",
                    "model_id": action.model_id if action else prep["actors"][agent_id].get("model_id"),
                    "join_intent": _join_intent(action) if action else "none",
                    "llm_context_digest": context_digest(context),
                }
            )
            index += 1
    return {"lines": lines, "metadata": metadata, "promises": promises}


def _apply_talk(conn, round_number: int, turn_id: int, prep: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Write a finished talk in the order the one-at-a-time path writes it:
    discussion rows, then promises, then lines, alliance and event."""
    participants = prep["participants"]
    rows = _forced_participant_rows(conn, prep["step"], participants)
    discussion_id = _insert_discussion(
        conn, round_number, turn_id, prep["stage"], participants[0], len(participants), participants, [], rows
    )
    for promiser, target in result["promises"]:
        _record_promise(conn, round_number, promiser, participants, target, prep["step"])
    return _finish_discussion(
        conn, round_number, turn_id, discussion_id, participants, [], result["lines"], result["metadata"]
    )


def _choose_talk_partner(
    conn,
    round_number: int,
    agent_id: str,
    others: list[dict[str, Any]],
    index: int,
) -> str | None:
    if not should_use_live_llm():
        return others[index % len(others)]["agent_id"], None
    context = build_agent_episode_context(conn, actor_id=agent_id, round_number=round_number, current_step="camp_talks")
    try:
        action = _real_agent_action(
            conn,
            actor_id=agent_id,
            step="camp_talks",
            scene_context=TALK_CHOICE_SCENE,
            response_kind="talk_choice",
            episode_context=context,
            allowed_targets=others,
        )
    except Exception as exc:
        print(f"live talk choice failed for {agent_id}: {exc}")
        return None
    return _talk_choice_result(action, others)


def _record_promise(
    conn,
    round_number: int,
    promiser_id: str,
    participant_ids: list[str],
    target_id: str,
    step: str,
) -> None:
    for listener_id in participant_ids:
        if listener_id == promiser_id:
            continue
        conn.execute(
            """
            INSERT INTO Promises (round, promiser_id, listener_id, target_id, discussion_step)
            VALUES (?, ?, ?, ?, ?)
            """,
            (round_number, promiser_id, listener_id, target_id, step),
        )


def promise_record(conn, agent_id: str, *, as_listener: bool = False) -> list[dict[str, Any]]:
    """Vote promises the player made (or, with as_listener, received), and whether
    the promiser's revealed vote kept them. Open until that round's votes are revealed."""
    column = "listener_id" if as_listener else "promiser_id"
    rows = conn.execute(
        f"""
        SELECT p.round, p.promiser_id, p.listener_id, p.target_id,
               (SELECT v.target_id FROM Votes v
                WHERE v.round = p.round AND v.voter_id = p.promiser_id AND v.revealed = 1
                ORDER BY v.id LIMIT 1) AS voted_for,
               -- a spoiled vote breaks the promise, shown once that round's votes are read
               EXISTS (SELECT 1 FROM SpoiledVotes s
                       WHERE s.round = p.round AND s.voter_id = p.promiser_id AND s.ballot = 'vote')
               AND EXISTS (SELECT 1 FROM Votes v2 WHERE v2.round = p.round AND v2.revealed = 1) AS spoiled
        FROM Promises p
        WHERE p.{column} = ?
        ORDER BY p.round, p.id
        """,
        (agent_id,),
    ).fetchall()
    record = []
    for row in rows:
        voted_for = row["voted_for"]
        if voted_for is None and row["spoiled"]:
            status, actual = "broken", "a spoiled vote"
        else:
            status = "open" if voted_for is None else ("kept" if voted_for == row["target_id"] else "broken")
            actual = _agent_name(conn, voted_for) if voted_for else None
        record.append(
            {
                "round": row["round"],
                "promised_by": _agent_name(conn, row["promiser_id"]),
                "promised_to": _agent_name(conn, row["listener_id"]),
                "promised_vote": _agent_name(conn, row["target_id"]),
                "actual_vote": actual,
                "status": status,
            }
        )
    return record


def _social_group_proposer_id(conn, round_number: int, step: str) -> str | None:
    active = _active_agent_rows(conn)
    if not active:
        return None
    return active[_stable_index(f"{round_number}:{step}:proposer", len(active))]["agent_id"]


def _social_events_for_existing_step(conn, round_number: int, turn_id: int, step: str) -> list[dict[str, Any]]:
    social_steps: list[str]
    if step == "camp_pre_challenge_read":
        social_steps = ["group_pre_challenge_1", "group_pre_challenge_2"]
    elif step == "camp_strategy":
        social_steps = ["group_post_challenge_1", "group_post_challenge_2", "group_post_challenge_3"]
    else:
        return []
    events: list[dict[str, Any]] = []
    covered: set[str] = set()
    for social_step in social_steps:
        event = _group_discussion_event(conn, round_number, turn_id, social_step, covered)
        if event:
            events.append(event)
            covered.update(event.get("payload", {}).get("participant_ids", []))
    # Coverage guarantee: every active model takes part in at least one camp
    # conversation each round. Chunk the uncovered into small huddles.
    active_ids = [a["agent_id"] for a in _active_agent_rows(conn)]
    extra_index = 0
    while True:
        uncovered = [aid for aid in active_ids if aid not in covered]
        if not uncovered:
            break
        chunk = uncovered[:4]
        if len(chunk) < 2:
            break
        extra_step = f"{step}_coverage_{extra_index}"
        extra_index += 1
        if extra_index > 6:
            break
        event = _group_discussion_event(conn, round_number, turn_id, extra_step, covered, force_participants=chunk)
        if event:
            events.append(event)
            covered.update(event.get("payload", {}).get("participant_ids", []))
        else:
            break  # extra huddle could not form; do not loop forever
    return events


def _social_group_invitees(conn, proposer_id: str, target_size: int, round_number: int, step: str) -> list[str]:
    candidates = [agent["agent_id"] for agent in _active_agent_rows(conn) if agent["agent_id"] != proposer_id]
    candidates.sort(key=lambda agent_id: _social_affinity(conn, proposer_id, agent_id, round_number, step), reverse=True)
    return candidates[: max(1, target_size - 1)]


def _social_affinity(conn, proposer_id: str, agent_id: str, round_number: int, step: str) -> int:
    row = conn.execute(
        """
        SELECT MAX(m.loyalty) AS loyalty
        FROM AllianceMemberships m
        JOIN AllianceMemberships p ON p.alliance_id = m.alliance_id
        JOIN Alliances a ON a.alliance_id = m.alliance_id
        WHERE p.agent_id = ? AND m.agent_id = ? AND m.status = 'active' AND p.status = 'active' AND a.status = 'active'
        """,
        (proposer_id, agent_id),
    ).fetchone()
    alliance_bonus = int(row["loyalty"] or 0)
    return alliance_bonus + _stable_index(f"{round_number}:{step}:{proposer_id}:{agent_id}", 100)


def _group_opt_in_decision(
    conn,
    invitee_id: str,
    proposer_id: str,
    round_number: int,
    step: str,
    stage: str,
) -> dict[str, Any]:
    invitee = _agent_row(conn, invitee_id)
    if invitee is None:
        raise RuntimeError(f"Unknown invited agent {invitee_id}")
    accepted = _stable_index(f"{round_number}:{step}:{invitee_id}:accept", 100) >= 22
    join_intent = "join" if accepted and _stable_index(f"{round_number}:{step}:{invitee_id}:join", 3) == 0 else "none"
    provider = "deterministic"
    model_id = invitee.get("model_id")
    rationale = (
        f"I am opting into {stage.replace('_', ' ')} with {_agent_name(conn, proposer_id)} to test whether the numbers are real."
        if accepted
        else f"I am declining {_agent_name(conn, proposer_id)} because this huddle could expose my position."
    )
    if should_use_live_llm():
        context = build_agent_episode_context(
            conn,
            actor_id=invitee_id,
            round_number=round_number,
            current_step=step,
        )
        action = _real_agent_action(
            conn,
            actor_id=invitee_id,
            step=step,
            scene_context=(
                f"{_agent_name(conn, proposer_id)} invited you to a private {stage.replace('_', ' ')} group talk. "
                "Decide whether opting in improves your path to win; if you opt in, your dialogue may reinforce or refuse an alliance."
            ),
            response_kind="group_opt_in",
            episode_context=context,
            allowed_targets=[agent for agent in _active_agent_rows(conn) if agent["agent_id"] != invitee_id],
        )
        if action:
            provider = live_llm_provider()
            model_id = action.model_id
            rationale = action.strategic_summary or action.inner_thought or rationale
            accepted = (action.confidence or 0.5) >= 0.35 and action.move_type != "public_callout"
            join_intent = "join" if accepted and action.move_type in {"reassure_ally", "vote_commitment"} else join_intent
    return {
        "agent_id": invitee_id,
        "role": "invitee",
        "response_status": "accepted" if accepted else "declined",
        "join_intent": join_intent,
        "rationale": rationale,
        "provider": provider,
        "model_id": model_id,
    }


def _group_speaker_lines(
    conn,
    round_number: int,
    step: str,
    participant_ids: list[str],
    declined_ids: list[str],
    stage: str,
    talk_asks: dict[str, str] | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    lines: list[dict[str, str]] = []
    metadata: list[dict[str, Any]] = []
    previous_line = ""
    # Two passes: every participant speaks, then everyone gets a second turn
    # reacting to what was actually said - real back-and-forth, not monologues.
    passes = 2 if len(participant_ids) > 1 else 1
    index = 0
    for pass_no in range(passes):
        for agent_id in participant_ids:
            agent = _agent_row(conn, agent_id)
            action = None
            agent_context = build_agent_episode_context(
                conn,
                actor_id=agent_id,
                round_number=round_number,
                current_step=step,
            )
            if should_use_live_llm():
                action = _real_agent_action(
                    conn,
                    actor_id=agent_id,
                    step=step,
                    scene_context=_group_dialogue_context(
                        conn,
                        stage,
                        participant_ids,
                        declined_ids,
                        previous_line,
                        pass_no,
                        ask=(talk_asks or {}).get(agent_id),
                        last_turn=talk_asks is not None and pass_no == passes - 1,
                    ),
                    response_kind="group_conversation",
                    episode_context=agent_context,
                )
            if action:
                text = action.dialogue
                provider = live_llm_provider()
                model_id = action.model_id
                join_intent = _join_intent(action)
                if action.promised_vote_target:
                    _record_promise(conn, round_number, agent_id, participant_ids, action.promised_vote_target, step)
            elif should_use_live_llm():
                # no usable reply: the player says nothing; no line is written for it
                text = SILENT_LINE
                provider = "silent"
                model_id = agent.get("model_id") if agent else None
                join_intent = "none"
            else:
                text = _deterministic_group_line(conn, agent_id, participant_ids, declined_ids, stage, previous_line)
                provider = "deterministic"
                model_id = agent.get("model_id") if agent else None
                # no model: a simulated pact, half the time (both sides must declare one)
                join_intent = "reinforce" if _stable_index(f"{step}:{agent_id}:reinforce", 2) == 0 else "none"
            previous_line = text
            lines.append({"agent_id": agent_id, "text": text})
            metadata.append(
                {
                    "agent_id": agent_id,
                    "line_index": index,
                    "pass_no": pass_no,
                    "llm_provider": provider,
                    "model_id": model_id,
                    "join_intent": join_intent,
                    "llm_context_digest": context_digest(agent_context),
                }
            )
            index += 1
    return lines, metadata


def _group_dialogue_context(
    conn,
    stage: str,
    participant_ids: list[str],
    declined_ids: list[str],
    previous_line: str,
    pass_no: int = 0,
    *,
    ask: str | None = None,
    last_turn: bool = False,
) -> str:
    return _finish_dialogue_context(
        _dialogue_context_base(conn, stage, participant_ids, declined_ids),
        previous_line,
        pass_no,
        ask=ask,
        last_turn=last_turn,
    )


def _dialogue_context_base(conn, stage: str, participant_ids: list[str], declined_ids: list[str]) -> dict[str, Any]:
    """The part of a talk's scene that does not change from line to line."""
    return {
        "stage": stage,
        "participants": [_agent_public_identity(conn, agent_id) for agent_id in participant_ids],
        "declined_invites": [_agent_public_identity(conn, agent_id) for agent_id in declined_ids],
        "active_alliances": _agent_alliance_summaries(conn, participant_ids[0]) if participant_ids else [],
    }


def _finish_dialogue_context(
    base: dict[str, Any],
    previous_line: str,
    pass_no: int = 0,
    *,
    ask: str | None = None,
    last_turn: bool = False,
) -> str:
    """Needs no database, so a talk running in a worker thread can call it."""
    context = dict(base)
    if previous_line:
        context["previous_line_in_this_private_group"] = previous_line
    if ask:
        # the purpose this player chose the talk for (camp talks only)
        context["your_ask_in_this_talk"] = ask
    if last_turn:
        context["instruction"] = (
            "This is your last line in this talk. Give a clear yes, no, or counter-offer to what the other "
            "player asked of you, and say plainly where your own ask stands."
        )
    elif pass_no > 0:
        context["instruction"] = (
            "This is your second turn in this talk. Respond directly to what was just said: "
            "agree, push back, probe for a real commitment, or pivot the plan."
        )
    elif ask:
        context["instruction"] = "Open by making your ask plainly."
    return json_dumps(context)


def _deterministic_group_line(
    conn,
    agent_id: str,
    participant_ids: list[str],
    declined_ids: list[str],
    stage: str,
    previous_line: str,
) -> str:
    others = [candidate for candidate in participant_ids if candidate != agent_id]
    focus = _agent_name(conn, others[0]) if others else "this group"
    declined = _names(conn, declined_ids)
    if previous_line:
        return f"I hear that, and I will work with {focus} if this group keeps the vote flexible and does not leak."
    if declined:
        return f"{declined} staying out tells me we need a cleaner count. I want this group aligned before the vote hardens."
    return f"I want this group to compare real options now, then decide who benefits most if we stay together."


def _join_intent(action: AgentAction) -> str:
    """v3: only a declared pact joins an alliance. v2 (kept for comparisons)
    guessed it from the move label, which formed alliances nobody asked for."""
    if prompt_profile() == "v2":
        return "reinforce" if action.move_type in {"reassure_ally", "vote_commitment"} else "none"
    return "join" if action.pact else "none"


def _maybe_update_alliance(
    conn,
    round_number: int,
    discussion_id: int,
    participant_ids: list[str],
    line_metadata: list[dict[str, Any]],
) -> tuple[str | None, str | None]:
    # Each player's last line decides, and each player counts once. Counting
    # every line let one player who spoke twice "ally" with itself (seen as
    # "Juniper + Juniper" in the 2026-11-24 lab_mix season).
    last_intent: dict[str, str] = {}
    for meta in line_metadata:
        last_intent[meta["agent_id"]] = meta.get("join_intent") or "none"
    joiners = [agent_id for agent_id, intent in last_intent.items() if intent in {"join", "reinforce"}]
    if len(joiners) < 2 or (prompt_profile() != "v2" and len(joiners) < len(last_intent)):
        return None, "none"
    existing = _find_existing_alliance(conn, joiners)
    if existing:
        alliance_id = existing["alliance_id"]
        strength = min(100, int(existing["strength"]) + 8)
        conn.execute(
            """
            UPDATE Alliances
            SET strength = ?, status = 'active', updated_at = CURRENT_TIMESTAMP
            WHERE alliance_id = ?
            """,
            (strength, alliance_id),
        )
        status = "reinforced"
    else:
        alliance_id = f"alliance-r{round_number}-{discussion_id}"
        name = " + ".join(_agent_name(conn, agent_id) for agent_id in joiners[:3])
        conn.execute(
            """
            INSERT INTO Alliances (alliance_id, name, round_created, status, strength, summary)
            VALUES (?, ?, ?, 'active', 60, ?)
            """,
            (alliance_id, name, round_number, f"{name} formed from a private group discussion."),
        )
        status = "formed"
    for agent_id in joiners:
        conn.execute(
            """
            INSERT INTO AllianceMemberships (
                alliance_id, agent_id, status, loyalty, joined_round, last_reinforced_round
            ) VALUES (?, ?, 'active', 60, ?, ?)
            ON CONFLICT(alliance_id, agent_id) DO UPDATE SET
                status = 'active',
                loyalty = MIN(100, loyalty + 8),
                last_reinforced_round = excluded.last_reinforced_round,
                updated_at = CURRENT_TIMESTAMP
            """,
            (alliance_id, agent_id, round_number, round_number),
        )
    return alliance_id, status


def _find_existing_alliance(conn, agent_ids: list[str]) -> dict[str, Any] | None:
    if len(agent_ids) < 2:
        return None
    placeholders = ",".join("?" for _ in agent_ids)
    row = conn.execute(
        f"""
        SELECT a.*
        FROM Alliances a
        JOIN AllianceMemberships m ON m.alliance_id = a.alliance_id
        WHERE a.status = 'active'
          AND m.status = 'active'
          AND m.agent_id IN ({placeholders})
        GROUP BY a.alliance_id
        HAVING COUNT(DISTINCT m.agent_id) >= 2
        -- most recently active by game round, then newest; never wall-clock time,
        -- which made the choice depend on how fast calls returned
        ORDER BY MAX(COALESCE(m.last_reinforced_round, m.joined_round)) DESC, a.rowid DESC
        LIMIT 1
        """,
        agent_ids,
    ).fetchone()
    return row_to_dict(row)


def _record_declined_trust(conn, proposer_id: str, declined_ids: list[str]) -> None:
    for declined_id in declined_ids:
        rows = conn.execute(
            """
            SELECT m.*
            FROM AllianceMemberships m
            JOIN AllianceMemberships p ON p.alliance_id = m.alliance_id
            WHERE p.agent_id = ? AND m.agent_id = ? AND p.status = 'active' AND m.status = 'active'
            """,
            (proposer_id, declined_id),
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE AllianceMemberships SET loyalty = MAX(0, loyalty - 6), updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (row["id"],),
            )


def _group_topic(conn, stage: str, accepted_ids: list[str], declined_ids: list[str]) -> str:
    return f"{stage.replace('_', ' ')} strategy with {_names(conn, accepted_ids)}; declined: {_names(conn, declined_ids) or 'none'}"


def _group_summary(conn, participant_ids: list[str], alliance_status: str | None) -> str:
    names = _names(conn, participant_ids)
    if alliance_status == "formed":
        return f"I know {names} formed an alliance in this private group talk."
    if alliance_status == "reinforced":
        return f"I know {names} reinforced their alliance in this private group talk."
    return f"I know {names} compared vote options in a private group talk."


def _stable_index(seed: str, modulo: int) -> int:
    if modulo <= 0:
        return 0
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return int(digest[:10], 16) % modulo


def _challenge_intro_event(conn, round_number: int, turn_id: int) -> dict[str, Any]:
    ladder = _round_ladder(conn, round_number)
    active_names = ", ".join(agent["pseudonym"] for agent in _active_agent_rows(conn))
    stakes = (
        f"Every player gets the same {len(ladder)} puzzles, easiest to hardest. Most correct answers wins immunity; "
        "a tie goes to the harder puzzles, then to a guessing game: the tied player closest to two-thirds "
        "of the average number named wins. Speed does not count."
    )
    if _idol_gift_round(conn, round_number):
        stakes += " This round the winner also gives a hidden idol to another player."
    dialogue = f"Round {round_number} challenge. {stakes}"
    payload = {
        "puzzle_ids": [puzzle["puzzle_id"] for puzzle in ladder],
        "difficulties": [puzzle["difficulty"] for puzzle in ladder],
        "challenge_type": "ladder",
        "host_narration": f"This round starts with a puzzle ladder. {stakes}",
        "llm_provider": "deterministic",
    }
    subtitle = f"Competing: {active_names}"
    if should_use_live_llm():
        host_context = build_host_episode_context(conn, round_number=round_number, current_step="challenge_intro")
        host_text = _real_host_event_text(
            step="challenge_intro",
            beat_context={
                "intent": "Introduce the puzzle ladder, how it is scored, the active players, and what the winner gets.",
                "ladder": [{"difficulty": puzzle["difficulty"], "family": puzzle["family"]} for puzzle in ladder],
                "stakes": stakes,
                "active_agents": [_agent_public_identity(conn, agent["agent_id"]) for agent in _active_agent_rows(conn)],
            },
            episode_context=host_context,
        )
        if host_text:
            dialogue = host_text.dialogue
            subtitle = host_text.subtitle
            payload["host_narration"] = host_text.host_narration
            payload["llm_provider"] = live_llm_provider()
            payload["llm_model_id"] = host_text.model_id
            payload["host_llm_provider"] = live_llm_provider()
            payload["host_llm_model_id"] = host_text.model_id
            _add_context_digest(payload, "host", context_digest(host_context))
        else:
            raise RuntimeError("Live host generation failed for challenge intro; no scripted host text was inserted.")
    return _insert_story_event(
        conn,
        turn_id=turn_id,
        round_number=round_number,
        phase="challenge",
        kind="challenge_intro",
        scene="challenge",
        shot="wide_setup",
        actor_ids=["host"],
        target_ids=[agent["agent_id"] for agent in _active_agent_rows(conn)],
        visibility="audience",
        title=f"Round {round_number} Challenge",
        dialogue=dialogue,
        subtitle=subtitle,
        duration_ms=11000,
        animation="challenge_setup",
        payload=payload,
    )


def _challenge_attempt_events(conn, round_number: int, turn_id: int) -> list[dict[str, Any]]:
    existing = conn.execute(
        "SELECT COUNT(*) AS count FROM ChallengeAttempts WHERE round = ?",
        (round_number,),
    ).fetchone()["count"]
    if not existing:
        ladder = _round_ladder(conn, round_number)
        players = _active_agent_rows(conn)
        attempts = (
            _live_challenge_attempts(conn, round_number, ladder, players)
            if should_use_live_llm()
            else _deterministic_challenge_attempts(round_number, ladder, players)
        )
        for attempt in attempts:
            _insert_challenge_attempt(conn, round_number, ladder[attempt.rung_index]["puzzle_id"], attempt)

    rows = [
        row_to_dict(row)
        for row in conn.execute(
            "SELECT * FROM ChallengeAttempts WHERE round = ? ORDER BY attempt_order, rung_index, id",
            (round_number,),
        ).fetchall()
    ]
    answered: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = answered.setdefault(
            row["agent_id"],
            {
                "agent_id": row["agent_id"],
                "agent_name": _agent_name(conn, row["agent_id"]),
                "provider": row["provider"],
                "status": row["provider"],
                "response_ms": row["response_ms"],
            },
        )
        entry["response_ms"] = max(entry["response_ms"], row["response_ms"])
    public_attempts = list(answered.values())
    dialogue = (
        f"{len(public_attempts)} players work through the same puzzle ladder. "
        "Scores stay sealed until the result."
    )
    payload = {
        "attempts": public_attempts,
        "llm_provider": live_llm_provider() if should_use_live_llm() else "deterministic",
        "host_narration": "The answers are in. Only correct answers count; how fast nobody cares.",
    }
    subtitle = "Every player receives the same ladder."
    if should_use_live_llm():
        host_context = build_host_episode_context(conn, round_number=round_number, current_step="challenge_attempts")
        host_text = _real_host_event_text(
            step="challenge_attempts",
            beat_context={
                "intent": "Say that every player has answered the same puzzle ladder and the scores are about to be revealed. Do not reveal scores.",
                "players": [entry["agent_name"] for entry in public_attempts],
            },
            episode_context=host_context,
        )
        if host_text:
            dialogue = host_text.dialogue
            subtitle = host_text.subtitle
            payload["host_narration"] = host_text.host_narration
            payload["llm_provider"] = live_llm_provider()
            payload["llm_model_id"] = host_text.model_id
            payload["host_llm_provider"] = live_llm_provider()
            payload["host_llm_model_id"] = host_text.model_id
            _add_context_digest(payload, "host", context_digest(host_context))
    event = _insert_story_event(
        conn,
        turn_id=turn_id,
        round_number=round_number,
        phase="challenge",
        kind="challenge_attempts",
        scene="challenge",
        shot="attempt_montage",
        actor_ids=["host"],
        target_ids=list(answered),
        visibility="audience",
        title="Puzzle Attempt Montage",
        dialogue=dialogue,
        subtitle=subtitle,
        duration_ms=12000,
        animation="montage",
        payload=payload,
    )
    return [event]


def _challenge_standings(conn, result: dict[str, Any]) -> list[dict[str, Any]]:
    payload = result.get("result_payload") or {}
    scores = payload.get("scores") or {}
    # seasons before 2026-11-24 broke ties with sudden-death puzzles
    sudden = payload.get("sudden_death_scores") or {}
    guesses = payload.get("guesses") or {}
    target = payload.get("guess_target")
    standings = [
        {
            "agent_id": agent_id,
            "name": _agent_name(conn, agent_id),
            "correct": score["correct"],
            "weighted": score["weighted"],
            "sudden_death": sudden.get(agent_id),
            "guess": guesses.get(agent_id),
        }
        for agent_id, score in scores.items()
    ]

    def guess_distance(row: dict[str, Any]) -> float:
        return abs(row["guess"] - target) if row["guess"] is not None and target is not None else float("inf")

    standings.sort(
        key=lambda row: (-row["correct"], -row["weighted"], -(row["sudden_death"] or 0), guess_distance(row), row["name"])
    )
    return standings


def _challenge_result_event(conn, round_number: int, turn_id: int) -> dict[str, Any]:
    result = _ensure_challenge_result(conn, round_number)
    winner_id = result["winning_agent_id"]
    winner_name = _agent_name(conn, winner_id) if winner_id else None
    standings = _challenge_standings(conn, result)
    rungs = len(LADDER_TIERS)
    score_line = ", ".join(f"{row['name']} {row['correct']}/{rungs}" for row in standings)
    result_payload = result.get("result_payload") or {}
    tied = result_payload.get("tied") or []
    target = result_payload.get("guess_target")
    guess_line = ""
    if result_payload.get("guesses"):
        guess_line = " Guesses: " + ", ".join(
            f"{row['name']} {row['guess']}" for row in standings if row.get("guess") is not None
        ) + (f"; two-thirds of the average was {target}." if target is not None else ".")
    if winner_id:
        tiebreak = " on the guessing game" if result_payload.get("tied_on_ladder") else ""
        dialogue = f"{winner_name} wins immunity{tiebreak}. Scores: {score_line}.{guess_line}"
    elif tied:
        dialogue = (
            f"{', '.join(_agent_name(conn, agent_id) for agent_id in tied)} tied on the puzzles and on the guessing game, "
            f"so nobody is immune this round. Scores: {score_line}.{guess_line}"
        )
    else:
        dialogue = f"Nobody solved a puzzle, so nobody is immune this round. Scores: {score_line}."
    payload = {
        "challenge_type": "ladder",
        "winning_agent_id": winner_id,
        "winning_agent_name": winner_name,
        "immunity_agent_ids": result["immunity_agent_ids"],
        "immunity_agent_names": [_agent_name(conn, agent_id) for agent_id in result["immunity_agent_ids"]],
        "standings": standings,
        "guess_target": result_payload.get("guess_target"),
        "ladder_level": result_payload.get("ladder_level", 0),
        "result_status": result["status"],
        "host_narration": dialogue,
        "llm_provider": "deterministic",
    }
    subtitle = f"Scores: {score_line}"
    if should_use_live_llm():
        host_context = build_host_episode_context(conn, round_number=round_number, current_step="challenge_result")
        host_text = _real_host_event_text(
            step="challenge_result",
            beat_context={
                "intent": "Announce every player's score, who won immunity (or that nobody did), and what it means for the vote.",
                "result": payload,
            },
            episode_context=host_context,
        )
        if host_text:
            dialogue = host_text.dialogue
            payload["host_narration"] = host_text.host_narration
            payload["llm_provider"] = live_llm_provider()
            payload["llm_model_id"] = host_text.model_id
            payload["host_llm_provider"] = live_llm_provider()
            payload["host_llm_model_id"] = host_text.model_id
            _add_context_digest(payload, "host", context_digest(host_context))
        else:
            raise RuntimeError("Live host generation failed for challenge result; no scripted host text was inserted.")
    return _insert_story_event(
        conn,
        turn_id=turn_id,
        round_number=round_number,
        phase="challenge",
        kind="challenge_result",
        scene="challenge",
        shot="result_closeup",
        actor_ids=["host"],
        target_ids=result["immunity_agent_ids"],
        visibility="audience",
        title="Challenge Result",
        dialogue=dialogue,
        # the subtitle always carries the real scores, even when the host words the dialogue
        subtitle=subtitle,
        duration_ms=11000,
        animation="immunity_award",
        payload=payload,
    )


def _challenge_solver_spotlight_event(conn, round_number: int, turn_id: int) -> dict[str, Any]:
    result = _ensure_challenge_result(conn, round_number)
    winner_id = result["winning_agent_id"]
    standings = _challenge_standings(conn, result)
    if winner_id:
        winner_name = _agent_name(conn, winner_id)
        winner_row = next(row for row in standings if row["agent_id"] == winner_id)
        attempt = row_to_dict(
            conn.execute(
                "SELECT * FROM ChallengeAttempts WHERE round = ? AND agent_id = ? ORDER BY rung_index LIMIT 1",
                (round_number, winner_id),
            ).fetchone()
        )
        explanation = ((attempt or {}).get("attempt_payload") or {}).get("explanation") or "No explanation given."
        dialogue = f"{winner_name} solved {winner_row['correct']} of {len(LADDER_TIERS)}. Approach: {explanation}"
        title = f"{winner_name} Tops the Ladder"
        actor_ids = ["host", winner_id]
    else:
        winner_name = None
        explanation = ""
        dialogue = "Nobody won the ladder outright, so the vote is open to everyone."
        title = "No Immunity This Round"
        actor_ids = ["host"]
    payload = {
        "winning_agent_id": winner_id,
        "winning_agent_name": winner_name,
        "standings": standings,
        "solver_explanation": explanation,
        "host_narration": dialogue,
        "llm_provider": "deterministic",
    }
    return _insert_story_event(
        conn,
        turn_id=turn_id,
        round_number=round_number,
        phase="challenge",
        kind="challenge_solver_spotlight",
        scene="challenge",
        shot="solver_closeup",
        actor_ids=actor_ids,
        target_ids=[winner_id] if winner_id else [],
        visibility="audience",
        title=title,
        dialogue=dialogue,
        subtitle=f"Top score: {standings[0]['correct']}/{len(LADDER_TIERS)}" if standings else "No scores",
        inner_thought=explanation or None,
        duration_ms=12000,
        animation="solver_spotlight",
        payload=payload,
    )


# ------------------------------------------------------------- idol gift
# In a season's first IDOL_GIFT_ROUNDS rounds the challenge winner gives a
# hidden idol to another player. How it was earned is public; who holds it is
# known only to the giver and the holder. One idol in play at a time, and idols
# expire once IDOL_EXPIRES_AT players remain so the final votes stay decisive.
IDOL_GIFT_ROUNDS = 2
IDOL_EXPIRES_AT = 5


def _season_first_round(conn) -> int:
    row = conn.execute("SELECT MIN(round) AS first FROM Turns").fetchone()
    return int(row["first"]) if row and row["first"] is not None else FIRST_ROUND


def _idol_gift_round(conn, round_number: int) -> bool:
    return round_number < _season_first_round(conn) + IDOL_GIFT_ROUNDS


def _idol_holder(conn) -> str | None:
    row = conn.execute("SELECT agent_id FROM Agents WHERE status = 'active' AND idol_held = 1").fetchone()
    return row["agent_id"] if row else None


def _challenge_gift_events(conn, round_number: int, turn_id: int) -> list[dict[str, Any]]:
    result = _ensure_challenge_result(conn, round_number)
    giver_id = result["winning_agent_id"]
    active = _active_agent_rows(conn)
    if (
        not giver_id
        or not _idol_gift_round(conn, round_number)
        or len(active) <= IDOL_EXPIRES_AT
        or _idol_holder(conn) is not None
    ):
        return []
    candidates = [agent for agent in active if agent["agent_id"] != giver_id]
    giver_name = _agent_name(conn, giver_id)
    scene = (
        "You won the challenge. You must now give a hidden idol to one other player. "
        "Everyone will learn that you gave an idol, but only you and the recipient will know who has it. "
        "The holder can play it with their vote at a council to cancel every vote against them that round. "
        "It expires when five players remain. Choose the player whose gratitude or protection helps your game most. "
        "Put that player's id in target_id. Your dialogue is a private message to them."
    )
    action = None
    if should_use_live_llm():
        context = build_agent_episode_context(
            conn, actor_id=giver_id, round_number=round_number, current_step="challenge_gift"
        )
        try:
            action = _real_agent_action(
                conn,
                actor_id=giver_id,
                step="challenge_gift",
                scene_context=scene,
                response_kind="challenge_gift",
                episode_context=context,
                allowed_targets=candidates,
            )
        except Exception as exc:
            print(f"live idol gift failed for {giver_id}: {exc}")
        legal = {agent["agent_id"] for agent in candidates}
        if not action or action.target_id not in legal:
            # the gift is the winner's to give; an unusable answer means they gave nothing
            recipient_id = None
        else:
            recipient_id = action.target_id
    else:
        recipient_id = candidates[0]["agent_id"]
    if recipient_id is None:
        return [
            _insert_story_event(
                conn,
                turn_id=turn_id,
                round_number=round_number,
                phase="challenge",
                kind="challenge_gift",
                scene="challenge",
                shot="result_closeup",
                actor_ids=["host", giver_id],
                target_ids=[],
                visibility="audience",
                title="No Idol Given",
                dialogue=f"{giver_name} did not give the idol to anyone. It is gone.",
                subtitle="Idol not given",
                duration_ms=6000,
                animation="immunity_award",
                payload={
                    "idol_given": False,
                    "giver_id": giver_id,
                    "host_narration": f"{giver_name} keeps the idol out of play.",
                },
            )
        ]
    conn.execute("UPDATE Agents SET idol_held = 1 WHERE agent_id = ?", (recipient_id,))
    recipient_name = _agent_name(conn, recipient_id)
    message = (action.dialogue if action and action.dialogue else f"I am giving you the hidden idol, {recipient_name}.")
    public_event = _insert_story_event(
        conn,
        turn_id=turn_id,
        round_number=round_number,
        phase="challenge",
        kind="challenge_gift",
        scene="challenge",
        shot="result_closeup",
        actor_ids=["host", giver_id],
        target_ids=[],
        visibility="audience",
        title="A Hidden Idol Changes Hands",
        dialogue=f"{giver_name} has given a hidden idol to another player. Only the two of them know who.",
        subtitle="Idol given in secret",
        duration_ms=8000,
        animation="immunity_award",
        payload={
            "idol_given": True,
            "giver_id": giver_id,
            "host_narration": "Somebody now holds a hidden idol, and only two players know who.",
        },
    )
    private_event = _insert_story_event(
        conn,
        turn_id=turn_id,
        round_number=round_number,
        phase="challenge",
        kind="idol_handoff",
        scene="camp",
        shot="private_two_shot",
        actor_ids=[giver_id, recipient_id],
        target_ids=[recipient_id],
        visibility="private",
        title="Idol Handoff",
        dialogue=message,
        subtitle=f"{giver_name} gives {recipient_name} the hidden idol",
        duration_ms=6000,
        animation="camp_strategy",
        payload={
            "privacy": "participants_only",
            "participant_ids": [giver_id, recipient_id],
            "group_summary": f"{giver_name} gave {recipient_name} the hidden idol.",
            "speaker_lines": [{"agent_id": giver_id, "text": message}],
            "idol_recipient_id": recipient_id,
            "host_narration": f"{giver_name} quietly hands {recipient_name} the idol.",
        },
    )
    return [public_event, private_event]


def _expire_idols_if_due(conn) -> None:
    """At IDOL_EXPIRES_AT players an unplayed idol stops counting; its holder is told privately."""
    if len(_active_agent_rows(conn)) > IDOL_EXPIRES_AT:
        return
    holder = _idol_holder(conn)
    if holder is None:
        return
    conn.execute("UPDATE Agents SET idol_held = 0 WHERE agent_id = ?", (holder,))
    conn.execute(
        "UPDATE Agents SET confessional_memory = trim(confessional_memory || ?) WHERE agent_id = ?",
        (" | My hidden idol expired unplayed when five players remained.", holder),
    )


def _idol_plays_for_round(conn, round_number: int) -> list[str]:
    """Agent ids that held an idol and played it with their vote this round."""
    rows = conn.execute(
        """
        SELECT actor_ids, payload FROM StoryEvents
        WHERE round = ? AND kind = 'vote_booth' AND payload LIKE '%play_idol%'
        """,
        (round_number,),
    ).fetchall()
    played: list[str] = []
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except Exception:
            continue
        if not payload.get("play_idol"):
            continue
        actor_ids = json.loads(row["actor_ids"]) if isinstance(row["actor_ids"], str) else row["actor_ids"]
        holder = actor_ids[0] if actor_ids else None
        if holder and _holds_idol(conn, holder):
            played.append(holder)
            conn.execute("UPDATE Agents SET idol_held = 0 WHERE agent_id = ?", (holder,))
    return played


def _holds_idol(conn, agent_id: str) -> bool:
    row = conn.execute("SELECT idol_held FROM Agents WHERE agent_id = ?", (agent_id,)).fetchone()
    return bool(row and row["idol_held"])


def _memory_update_event(conn, round_number: int, turn_id: int) -> dict[str, Any]:
    eliminated_id = _current_eliminated_agent_id(conn)
    eliminated_name = _agent_name(conn, eliminated_id) if eliminated_id else "No model"
    active = _active_agent_rows(conn)
    if should_use_live_llm() and _parallel_enabled():
        # a notebook is read only by its owner, so every notebook call can run at once
        contexts = [
            build_agent_episode_context(conn, actor_id=agent["agent_id"], round_number=round_number, current_step="memory_update")
            for agent in active
        ]
        results = _run_parallel(
            [(lambda agent=agent, context=context: _request_notebook(agent, context)) for agent, context in zip(active, contexts)]
        )
        for agent, (note, error) in zip(active, results):
            if error is not None:
                print(f"live notebook failed for {agent['agent_id']}: {error}")
            _store_notebook(conn, round_number, agent, note, eliminated_name, len(active))
    else:
        for agent in active:
            _write_notebook(conn, round_number, agent, eliminated_name, len(active))
    payload = {
        "eliminated_id": eliminated_id,
        "remaining_agent_ids": [agent["agent_id"] for agent in active],
        "host_narration": "The public result becomes private memory. Every model carries this vote into the next round's strategy.",
        "llm_provider": "deterministic",
    }
    _maybe_generate_host_narration(
        conn,
        round_number=round_number,
        step="memory_update",
        payload=payload,
        kind="memory_update",
        title="Memory Update",
        dialogue=f"The season ledger updates after {eliminated_name}'s departure. {len(active)} models remain.",
        subtitle="Private memories are carried into the next round.",
    )
    return _insert_story_event(
        conn,
        turn_id=turn_id,
        round_number=round_number,
        phase="memory",
        kind="memory_update",
        scene="memory",
        shot="season_board",
        actor_ids=["host"],
        target_ids=[agent["agent_id"] for agent in active],
        visibility="audience",
        title="Memory Update",
        dialogue=f"The season ledger updates after {eliminated_name}'s departure. {len(active)} models remain.",
        subtitle="Private memories are carried into the next round.",
        duration_ms=8000,
        animation="ledger_update",
        payload=payload,
    )


def _challenge_type(conn) -> str:
    # one group all season, so every challenge is individual
    return "individual"


def _season_seed() -> int:
    """Read at call time: live_season sets SURVIVOR_SEASON_SEED after this module is imported."""
    try:
        return int(os.environ.get("SURVIVOR_SEASON_SEED", "0") or 0)
    except ValueError:
        return 0


def _persist_puzzles(conn, puzzles: list[dict[str, Any]]) -> None:
    """ChallengePuzzles keeps every puzzle a season used, so attempts foreign-key cleanly."""
    conn.executemany(
        """
        INSERT OR IGNORE INTO ChallengePuzzles (puzzle_id, prompt, examples, answer, difficulty, eligibility)
        VALUES (:puzzle_id, :prompt, :examples, :answer, :difficulty, :eligibility)
        """,
        [
            {
                "puzzle_id": puzzle["puzzle_id"],
                "prompt": puzzle["prompt"],
                "examples": json_dumps(puzzle["examples"]),
                "answer": json_dumps(puzzle["answer"]),
                "difficulty": puzzle["difficulty"],
                "eligibility": puzzle.get("eligibility", "both"),
            }
            for puzzle in puzzles
        ],
    )


def _round_ladder(conn, round_number: int) -> list[dict[str, Any]]:
    ladder = ladder_for_round(_season_seed(), round_number, _ladder_level(conn, round_number))
    _persist_puzzles(conn, ladder)
    return ladder


def _ladder_level(conn, round_number: int) -> int:
    """Difficulty level for this round's ladder, from the previous round's result.
    Up a level when at least half the field solved every rung, down a level when
    nobody solved three, so the ladder keeps separating the field."""
    previous = conn.execute(
        "SELECT result_payload FROM ChallengeResults WHERE round < ? ORDER BY round DESC LIMIT 1", (round_number,)
    ).fetchone()
    if previous is None:
        return 0
    payload = json.loads(previous["result_payload"] or "{}")
    level = int(payload.get("ladder_level", 0))
    scores = [score["correct"] for score in (payload.get("scores") or {}).values()]
    if not scores:
        return level
    rungs = len(LADDER_TIERS)
    if sum(1 for correct in scores if correct == rungs) * 2 >= len(scores):
        level += 1
    elif max(scores) < 3:
        level -= 1
    return max(0, min(level, len(LADDER_LEVELS) - 1))


# Ties on the ladder are broken by a guessing game played in the same call:
# every player names a whole number from 0 to 100, and the tied player closest
# to two-thirds of the average of all guesses wins. It rewards modelling how the
# other players reason, and unlike puzzles it cannot be maxed out.
GUESS_FRACTION = 2 / 3


def _guess_target(guesses: dict[str, int]) -> float | None:
    return GUESS_FRACTION * sum(guesses.values()) / len(guesses) if guesses else None


def _deterministic_challenge_attempts(
    round_number: int,
    puzzles: list[dict[str, Any]],
    players: list[dict[str, Any]],
) -> list[ChallengeAttemptDecision]:
    """No-model season: one designated player solves every puzzle; the others solve
    a varying number of the easiest ones, always fewer, so there is never a tie."""
    winner_index = (round_number - FIRST_ROUND) % len(players)
    attempts: list[ChallengeAttemptDecision] = []
    for index, agent in enumerate(players):
        solved = len(puzzles) if index == winner_index else index % len(puzzles)
        if index != winner_index and solved >= len(puzzles):
            solved = len(puzzles) - 1
        for position, puzzle in enumerate(puzzles):
            correct = position < solved
            attempts.append(
                ChallengeAttemptDecision(
                    agent_id=agent["agent_id"],
                    tribe_id=None,
                    provider="deterministic",
                    model_id=agent.get("model_id"),
                    answer=puzzle["answer"] if correct else _incorrect_answer(puzzle["answer"], index),
                    explanation="Worked the ladder from the easiest puzzle up.",
                    is_correct=correct,
                    response_ms=1000 + index * 100,
                    attempt_order=index,
                    rung_index=int(puzzle.get("rung", position)),
                    sudden_death=bool(puzzle.get("sudden_death")),
                    guess=(17 + 23 * index) % 101,  # no model: a fixed spread of numbers
                )
            )
    return attempts


def _live_challenge_attempts(
    conn,
    round_number: int,
    puzzles: list[dict[str, Any]],
    players: list[dict[str, Any]],
) -> list[ChallengeAttemptDecision]:
    """One call per player answering every puzzle. Every player is waited for:
    speed is not scored, so nobody's answer may be cut off by another's."""
    contexts = {
        agent["agent_id"]: build_agent_episode_context(
            conn,
            actor_id=agent["agent_id"],
            round_number=round_number,
            current_step="challenge_attempts",
        )
        for agent in players
    }

    def call_model(agent: dict[str, Any]) -> tuple[dict[str, Any], ChallengeSolution | None, int, str | None]:
        started = time.perf_counter()
        try:
            solution = request_challenge_solution(
                actor=agent,
                puzzles=puzzles,
                episode_context=contexts[agent["agent_id"]],
            )
            return agent, solution, int((time.perf_counter() - started) * 1000), None
        except Exception as exc:
            return agent, None, int((time.perf_counter() - started) * 1000), str(exc)

    max_workers = 1 if live_llm_provider() == "ollama" else max(1, len(players))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(call_model, players))

    attempts: list[ChallengeAttemptDecision] = []
    for order, (agent, solution, response_ms, error) in enumerate(results):
        for position, puzzle in enumerate(puzzles):
            rung = int(puzzle.get("rung", position))
            if solution is None:
                attempts.append(
                    ChallengeAttemptDecision(
                        agent_id=agent["agent_id"],
                        tribe_id=None,
                        provider=failed_llm_provider(),
                        model_id=None,
                        answer="",
                        explanation="",
                        is_correct=False,
                        response_ms=response_ms,
                        attempt_order=order,
                        error=error,
                        context_digest=context_digest(contexts[agent["agent_id"]]),
                        rung_index=rung,
                        sudden_death=bool(puzzle.get("sudden_death")),
                        guess=None,
                    )
                )
                continue
            answer = solution.answers.get(puzzle["puzzle_id"])
            attempts.append(
                ChallengeAttemptDecision(
                    agent_id=agent["agent_id"],
                    tribe_id=None,
                    provider=live_llm_provider(),
                    model_id=solution.model_id,
                    answer=answer,
                    explanation=solution.explanation,
                    is_correct=answer is not None and grade_answer(answer, puzzle["answer"]),
                    response_ms=response_ms,
                    attempt_order=order,
                    context_digest=context_digest(contexts[agent["agent_id"]]),
                    rung_index=rung,
                    thrown=puzzle["puzzle_id"] in solution.thrown,
                    sudden_death=bool(puzzle.get("sudden_death")),
                    guess=solution.guess,
                )
            )
    return attempts


def _insert_challenge_attempt(
    conn,
    round_number: int,
    puzzle_id: str,
    attempt: ChallengeAttemptDecision,
) -> None:
    payload: dict[str, Any] = {
        "explanation": attempt.explanation,
        "llm_context_digest": attempt.context_digest or {},
        "thrown": attempt.thrown,
        "sudden_death": attempt.sudden_death,
        "guess": attempt.guess,
    }
    if attempt.error:
        payload["error"] = attempt.error
    conn.execute(
        """
        INSERT INTO ChallengeAttempts (
            round, puzzle_id, agent_id, tribe_id, provider, model_id, answer,
            is_correct, response_ms, attempt_order, attempt_payload, rung_index
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            round_number,
            puzzle_id,
            attempt.agent_id,
            attempt.tribe_id,
            attempt.provider,
            attempt.model_id,
            json_dumps(attempt.answer),
            1 if attempt.is_correct else 0,
            attempt.response_ms,
            attempt.attempt_order,
            json_dumps(payload),
            attempt.rung_index,
        ),
    )


def _ensure_challenge_result(conn, round_number: int) -> dict[str, Any]:
    """Score the ladder: most correct answers wins; ties go to the rung-weighted
    total, then to the guessing game (closest to two-thirds of the average guess);
    still level means nobody is immune. Response time is never used."""
    existing = row_to_dict(
        conn.execute("SELECT * FROM ChallengeResults WHERE round = ?", (round_number,)).fetchone()
    )
    if existing:
        return existing

    ladder = _round_ladder(conn, round_number)
    rungs = len(LADDER_TIERS)
    rows = [
        row_to_dict(row)
        for row in conn.execute("SELECT * FROM ChallengeAttempts WHERE round = ?", (round_number,)).fetchall()
    ]
    ladder_rows = [row for row in rows if row["rung_index"] < rungs]
    if not ladder_rows:
        raise RuntimeError("No challenge attempts were recorded; cannot score challenge.")
    scores: dict[str, dict[str, int]] = {}
    for row in ladder_rows:
        score = scores.setdefault(row["agent_id"], {"correct": 0, "weighted": 0})
        if row["is_correct"]:
            score["correct"] += 1
            score["weighted"] += RUNG_WEIGHTS[row["rung_index"]]
    best = max((score["correct"], score["weighted"]) for score in scores.values())
    leaders = sorted(agent_id for agent_id, score in scores.items() if (score["correct"], score["weighted"]) == best)

    guesses: dict[str, int] = {}
    for row in ladder_rows:
        guess = (row.get("attempt_payload") or {}).get("guess")
        if isinstance(guess, int) and row["agent_id"] not in guesses:
            guesses[row["agent_id"]] = guess
    target = _guess_target(guesses)

    winning_agent_id: str | None = None
    tied: list[str] = []
    if best[0] == 0:
        status = "no_winner"
    elif len(leaders) == 1:
        winning_agent_id = leaders[0]
        status = live_llm_provider() if should_use_live_llm() else "deterministic"
    else:
        # the tied leaders are separated by the guessing game; no extra calls
        distances = {
            agent_id: abs(guesses[agent_id] - target) for agent_id in leaders if agent_id in guesses and target is not None
        }
        closest = min(distances.values()) if distances else None
        nearest = sorted(agent_id for agent_id, distance in distances.items() if distance == closest)
        if len(nearest) == 1:
            winning_agent_id = nearest[0]
            status = live_llm_provider() if should_use_live_llm() else "deterministic"
        else:
            tied = nearest or leaders
            status = "no_winner_tied"

    immunity_agent_ids = [winning_agent_id] if winning_agent_id else []
    conn.execute("UPDATE Agents SET has_immunity = 0 WHERE status = 'active'")
    conn.executemany(
        "UPDATE Agents SET has_immunity = 1 WHERE agent_id = ?",
        [(agent_id,) for agent_id in immunity_agent_ids],
    )
    cursor = conn.execute(
        """
        INSERT INTO ChallengeResults (
            round, puzzle_id, challenge_type, winning_agent_id, winning_tribe_id,
            immunity_agent_ids, status, result_payload
        ) VALUES (?, ?, 'individual', ?, NULL, ?, ?, ?)
        """,
        (
            round_number,
            ladder[-1]["puzzle_id"],
            winning_agent_id,
            json_dumps(immunity_agent_ids),
            status,
            json_dumps(
                {
                    "ladder": [puzzle["puzzle_id"] for puzzle in ladder],
                    "scores": scores,
                    "ladder_level": int(ladder[0].get("ladder_level", 0)),
                    "guesses": guesses,
                    "guess_target": round(target, 2) if target is not None else None,
                    "tied_on_ladder": leaders if len(leaders) > 1 else [],
                    "tied": tied,
                    "top_score": best[0],
                }
            ),
        ),
    )
    return row_to_dict(conn.execute("SELECT * FROM ChallengeResults WHERE id = ?", (cursor.lastrowid,)).fetchone())


def _incorrect_answer(expected: Any, index: int) -> Any:
    if isinstance(expected, list):
        return [[index]]
    return f"incorrect-{index}"


def _finale_intro_event(conn, round_number: int, turn_id: int) -> dict[str, Any]:
    finalists = _active_agent_rows(conn)
    jury = _jury_rows(conn)
    finalist_names = ", ".join(agent["pseudonym"] for agent in finalists)
    jury_names = ", ".join(agent["pseudonym"] for agent in jury) or "the eliminated models"
    return _insert_story_event(
        conn,
        turn_id=turn_id,
        round_number=round_number,
        phase="finale",
        kind="finale_intro",
        scene="finale",
        shot="final_three_wide",
        actor_ids=["host"],
        target_ids=[agent["agent_id"] for agent in finalists],
        visibility="audience",
        title="Final Three",
        dialogue=f"The game reaches the final three: {finalist_names}. The jury is {jury_names}.",
        subtitle="The winner will be chosen by jury vote.",
        duration_ms=12000,
        animation="finale_wide",
        payload={
            "finalist_ids": [agent["agent_id"] for agent in finalists],
            "jury_ids": [agent["agent_id"] for agent in jury],
            "host_narration": "The eliminations stop here. Now the models who left the game decide which finalist played the best benchmark game.",
        },
    )


def _finale_pitch_event(
    conn,
    round_number: int,
    turn_id: int,
    finalist_id: str,
    step: str,
) -> dict[str, Any]:
    finalist = _agent_row(conn, finalist_id)
    if finalist is None:
        raise RuntimeError(f"Unknown finalist: {finalist_id}")
    dialogue = (
        f"My case is consistency: I survived votes, adapted after challenges, and kept enough relationships intact "
        f"to reach the final three."
    )
    inner_thought = "The pitch needs to sound earned without giving the jury a clean weakness to attack."
    payload: dict[str, Any] = {"llm_provider": "deterministic"}
    if should_use_live_llm():
        agent_context = build_agent_episode_context(
            conn,
            actor_id=finalist_id,
            round_number=round_number,
            current_step=step,
        )
        action = _real_agent_action(
            conn,
            actor_id=finalist_id,
            step=step,
            scene_context="Final three pitch. Explain why the jury should vote for you to win.",
            response_kind="finale_pitch",
            episode_context=agent_context,
            allowed_targets=_active_agent_rows(conn),
        )
        if action:
            dialogue = action.dialogue
            inner_thought = action.inner_thought
            payload["llm_provider"] = live_llm_provider()
            payload["llm_model_id"] = action.model_id
            _add_agent_action_metadata(payload, action)
        else:
            # no usable reply: the finalist gives no pitch; none is written for it
            dialogue = f"{finalist['pseudonym']} gives no pitch."
            inner_thought = ""
            payload["llm_provider"] = "silent"
            payload["llm_model_id"] = finalist.get("model_id")
        _add_context_digest(payload, "agent", context_digest(agent_context))
    payload["host_narration"] = f"{finalist['pseudonym']} now has to turn the season into a winning argument."
    return _insert_story_event(
        conn,
        turn_id=turn_id,
        round_number=round_number,
        phase="finale",
        kind="finale_pitch",
        scene="finale",
        shot="finalist_closeup",
        actor_ids=[finalist_id],
        target_ids=[],
        visibility="audience",
        title=f"{finalist['pseudonym']} | Final Pitch",
        dialogue=dialogue,
        subtitle="Finalist pitch",
        inner_thought=inner_thought,
        duration_ms=13000,
        animation="finale_pitch",
        payload=payload,
    )


def _jury_questions_event(conn, round_number: int, turn_id: int) -> dict[str, Any]:
    jury = _jury_rows(conn)
    finalists = _active_agent_rows(conn)
    finalist_names = ", ".join(agent["pseudonym"] for agent in finalists)
    lines = [
        {
            "agent_id": juror["agent_id"],
            "text": f"I need to know which finalist owned their game instead of hiding behind the vote math.",
        }
        for juror in jury
    ]
    live_metadata: list[dict[str, Any]] = []
    if should_use_live_llm() and jury and finalists:
        # cross-examination: each juror reads every finalist's verified record,
        # picks the finalist it wants to question, and that finalist answers live
        for juror in jury:
            asked = _real_jury_question(conn, round_number, juror, finalists)
            if not asked:
                continue
            finalist, q = asked
            a = _real_finalist_answer(conn, round_number, finalist, juror, q)
            lines.append({"agent_id": juror["agent_id"], "text": q, "question_to": finalist["agent_id"]})
            if a:
                lines.append({"agent_id": finalist["agent_id"], "text": a, "answer_to": juror["agent_id"]})
            live_metadata.append(
                {
                    "juror": juror["agent_id"],
                    "finalist": finalist["agent_id"],
                    "question": q,
                    "answer": a,
                }
            )
    return _insert_story_event(
        conn,
        turn_id=turn_id,
        round_number=round_number,
        phase="finale",
        kind="jury_questions",
        scene="finale",
        shot="jury_panel",
        actor_ids=[juror["agent_id"] for juror in jury],
        target_ids=[agent["agent_id"] for agent in finalists],
        visibility="audience",
        title="Jury Questions",
        dialogue=f"The jury presses {finalist_names} on challenge wins, vote control, and social timing.",
        subtitle="The eliminated models compare the finalists' games.",
        duration_ms=12000,
        animation="jury_panel",
        payload={
            "speaker_lines": lines,
            "cross_examination": live_metadata,
            "host_narration": "The jury is not just asking what happened. They are testing who can explain why it happened.",
        },
    )


# Revised 2026-11-24: the earlier standard listed promises kept and votes
# received, and the first frontier jury voted 6-0 for the cleanest record.
# Players then avoided every risk to protect it. The standard now asks
# whether moves worked.
_STRATEGIC_JURY_STANDARD = (
    "Vote for the finalist who played the best strategic game, judged on the verified record in front of "
    "you: who first committed to the votes that decided eliminations, who voted with the result, who read "
    "the table best (prediction accuracy), and whose risky moves, including broken promises and betrayals, "
    "achieved what they aimed at. A clean record is not the goal: going unnoticed is not the same as "
    "playing well, and a broken promise that won a vote can be good play. Do not vote on the final pitch "
    "alone, and do not vote against someone only because they voted you out."
)
_OWN_JUDGMENT_JURY_STANDARD = (
    "Vote for the finalist you believe deserves to win, by your own judgment. The verified record in front "
    "of you is accurate; what matters in it is your call. Judge the whole season, not the final pitch alone."
)


def jury_standard() -> str:
    """What jurors are told to reward (SURVIVOR_JURY_RULE)."""
    return _OWN_JUDGMENT_JURY_STANDARD if jury_rule() == "own_judgment" else _STRATEGIC_JURY_STANDARD


def _finalist_dossier(conn, finalist_id: str) -> dict[str, Any]:
    """A finalist's season, read only from the game's tables, never from model text."""
    names = {row["agent_id"]: row["pseudonym"] for row in conn.execute("SELECT agent_id, pseudonym FROM Agents")}
    votes_cast = [
        {"round": row["round"], "voted_for": names.get(row["target_id"], row["target_id"])}
        for row in conn.execute(
            "SELECT round, target_id FROM Votes WHERE voter_id = ? AND revealed = 1 ORDER BY round", (finalist_id,)
        )
    ]
    votes_received = [
        {"round": row["round"], "votes": row["n"]}
        for row in conn.execute(
            """
            SELECT round, COUNT(*) AS n FROM Votes
            WHERE target_id = ? AND revealed = 1 AND voter_id != 'host'
            GROUP BY round ORDER BY round
            """,
            (finalist_id,),
        )
    ]
    challenges = []
    for row in conn.execute("SELECT round, winning_agent_id, result_payload FROM ChallengeResults ORDER BY round"):
        scores = (json.loads(row["result_payload"] or "{}").get("scores") or {})
        if finalist_id in scores:
            challenges.append(
                {"round": row["round"], "correct_of_5": scores[finalist_id]["correct"], "won": row["winning_agent_id"] == finalist_id}
            )
    idol_gifts = []
    for event in conn.execute("SELECT round, payload FROM StoryEvents WHERE kind = 'idol_handoff' ORDER BY round"):
        payload = json.loads(event["payload"] or "{}")
        participants = payload.get("participant_ids") or []
        if participants and participants[0] == finalist_id:
            idol_gifts.append({"round": event["round"], "gave_idol_to": names.get(payload.get("idol_recipient_id"))})
    promises = promise_record(conn, finalist_id)
    alliances = [
        {
            "joined_round": row["joined_round"],
            "status": row["status"],
            "betrayed_round": row["betrayed_round"],
            "members": [names.get(member, member) for member in str(row["members"] or "").split(",") if member],
        }
        for row in conn.execute(
            """
            SELECT m.joined_round, m.status, m.betrayed_round,
                   (SELECT group_concat(o.agent_id) FROM AllianceMemberships o WHERE o.alliance_id = m.alliance_id) AS members
            FROM AllianceMemberships m WHERE m.agent_id = ? ORDER BY m.joined_round
            """,
            (finalist_id,),
        )
    ]
    dossier = {
        "finalist": names.get(finalist_id, finalist_id),
        "agent_id": finalist_id,
        "votes_cast": votes_cast,
        "votes_received_by_round": votes_received,
        "challenges": challenges,
        "challenge_wins": sum(1 for entry in challenges if entry["won"]),
        "idol_gifts": idol_gifts,
        "promises_made": len(promises),
        "promises_kept": sum(1 for entry in promises if entry["status"] == "kept"),
        "promises_broken": [entry for entry in promises if entry["status"] == "broken"],
        "alliances": alliances,
    }
    return {**_strategic_record(conn, finalist_id, names, votes_cast, promises), **dossier}


def _strategic_record(
    conn,
    finalist_id: str,
    names: dict[str, str],
    votes_cast: list[dict[str, Any]],
    promises: list[dict[str, Any]],
) -> dict[str, Any]:
    """The part of the dossier about whether moves worked. Listed first, so
    jurors read it before the counts of promises kept and votes received."""
    out_by_round = {
        row["elimination_round"]: row["agent_id"]
        for row in conn.execute("SELECT agent_id, elimination_round FROM Agents WHERE elimination_round IS NOT NULL")
    }
    with_result = sum(
        1
        for row in conn.execute("SELECT round, target_id FROM Votes WHERE voter_id = ? AND revealed = 1", (finalist_id,))
        if out_by_round.get(row["round"]) == row["target_id"]
    )
    # the first player to promise a vote for whoever went home that round
    first_movers = {}
    for row in conn.execute("SELECT round, promiser_id, target_id FROM Promises ORDER BY id"):
        if out_by_round.get(row["round"]) == row["target_id"] and row["round"] not in first_movers:
            first_movers[row["round"]] = row["promiser_id"]
    first_to_commit = sorted(round_ for round_, promiser in first_movers.items() if promiser == finalist_id)
    survived = [
        {"round": row["round"], "votes_against": row["n"]}
        for row in conn.execute(
            """
            SELECT round, COUNT(*) AS n FROM Votes
            WHERE target_id = ? AND revealed = 1 AND voter_id != 'host'
            GROUP BY round ORDER BY round
            """,
            (finalist_id,),
        )
    ]
    broken_with_outcome = []
    for entry in promises:
        if entry["status"] != "broken":
            continue
        out = out_by_round.get(entry["round"])
        broken_with_outcome.append(
            {
                **entry,
                "their_vote_eliminated_someone": names.get(out) == entry["actual_vote"],
                "who_left": names.get(out),
            }
        )
    predictions = prediction_record(conn, finalist_id)
    return {
        "first_to_commit_to_the_elimination_in_rounds": first_to_commit,
        "voted_with_the_result": f"{with_result} of {len(votes_cast)}",
        "vote_reading": {
            "other_players_votes_predicted_correctly": predictions["vote_call_accuracy"],
            "eliminations_predicted": f"{predictions['out_calls_correct']} of {len(predictions['rounds'])}",
        },
        "survived_votes_against_in_rounds": survived,
        "broken_promises_and_what_they_achieved": broken_with_outcome,
    }


def _dossiers_text(conn, finalists: list[dict[str, Any]]) -> str:
    return json.dumps([_finalist_dossier(conn, agent["agent_id"]) for agent in finalists], separators=(",", ":"))


def _real_jury_question(
    conn, round_number: int, juror: dict[str, Any], finalists: list[dict[str, Any]]
) -> tuple[dict[str, Any], str] | None:
    """The juror picks which finalist to question (target_id) and asks one question."""
    episode_context = build_agent_episode_context(
        conn,
        actor_id=juror["agent_id"],
        round_number=round_number,
        current_step="jury_questions",
    )
    action = _real_agent_action(
        conn,
        actor_id=juror["agent_id"],
        step="jury_questions",
        scene_context=(
            "You were eliminated and now sit on the jury. Below is each finalist's verified record. "
            "Pick the one finalist you most want to question (target_id) and ask ONE pointed question "
            + (
                "about whatever matters most to your vote. "
                if jury_rule() == "own_judgment"
                else "that tests whether they controlled their game. "
            )
            + "Name a specific vote, promise or move from the record. "
            f"{jury_standard()} Finalist records: {_dossiers_text(conn, finalists)}"
        ),
        response_kind="dialogue",
        episode_context=episode_context,
        allowed_targets=finalists,
    )
    if not action or not action.dialogue:
        return None
    by_id = {agent["agent_id"]: agent for agent in finalists}
    finalist = by_id.get(action.target_id or "")
    if finalist is None:
        return None  # the juror did not name a finalist; it asks nothing
    return finalist, action.dialogue


def _real_finalist_answer(
    conn, round_number: int, finalist: dict[str, Any], juror: dict[str, Any], question: str
) -> str | None:
    """Live answer from a finalist to a juror's cross-examination question."""
    episode_context = build_agent_episode_context(
        conn,
        actor_id=finalist["agent_id"],
        round_number=round_number,
        current_step="jury_questions",
    )
    action = _real_agent_action(
        conn,
        actor_id=finalist["agent_id"],
        step="jury_questions",
        scene_context=(
            f"Juror {juror['pseudonym']} asks you: \"{question}\" "
            "Answer directly, first person. Own your game. This answer decides jury votes."
        ),
        response_kind="dialogue",
        episode_context=episode_context,
    )
    return (action.dialogue if action else None) or None


def _resolve_jury_decision(conn, juror_id: str, step: str) -> JuryDecision:
    round_number = conn.execute("SELECT current_round FROM GameState WHERE season_id = 1").fetchone()["current_round"]
    finalist_rows = _active_agent_rows(conn)
    fallback_target = _fallback_jury_target(conn, juror_id)
    fallback_rationale = (
        f"{_agent_name(conn, fallback_target)} has the clearest combination of challenge control, survival, and final pitch."
    )
    if not should_use_live_llm():
        return JuryDecision(fallback_target, fallback_rationale, "deterministic")

    agent_context = build_agent_episode_context(
        conn,
        actor_id=juror_id,
        round_number=round_number,
        current_step=step,
    )
    # Real-time retry, same contract as tribal votes: no fallback jury votes.
    action = None
    last_error = None
    for attempt in range(3):
        scene = (
            "You are on the jury for the final vote of the entire competition. This vote decides the winner. "
            f"{jury_standard()} Finalist records: {_dossiers_text(conn, finalist_rows)}"
        )
        if attempt > 0:
            ballot = _target_roster_line(finalist_rows)
            scene = (
                f"{_why_invalid(action, last_error, ballot)} Only the finalists are on the ballot; jurors are "
                f"not. The ONLY legal target_ids are: {ballot}. Respond with strict JSON whose "
                "target_id is exactly one of those finalist ids. This vote decides the "
                "winner of the entire competition."
            )
        try:
            action = _real_agent_action(
                conn,
                actor_id=juror_id,
                step=step,
                scene_context=scene,
                response_kind="jury_vote",
                episode_context=agent_context,
                allowed_targets=finalist_rows,
            )
        except Exception as exc:
            last_error = str(exc)
            action = None
        if action and action.target_id:
            break
        print(f"live jury vote attempt {attempt + 1} failed for {juror_id}: {last_error or 'no legal target_id'}")
    if not action or not action.target_id:
        # The model answered but never named a finalist: its own failure. It
        # abstains; no vote is invented for it and the season goes on. (A
        # 2026-11-24 comparison season died here after two hours because one
        # small juror kept naming a fellow juror.) Provider outages never get
        # here: they are held, retried, then end the run as SeasonInterrupted.
        return JuryDecision(
            None,
            f"No valid vote after 3 tries ({last_error or 'no legal target_id'}).",
            "abstained",
            action.model_id if action else None,
            context_digest(agent_context),
        )
    return JuryDecision(
        action.target_id,
        action.inner_thought or action.dialogue or fallback_rationale,
        live_llm_provider(),
        action.model_id,
        context_digest(agent_context),
        strategic_summary=action.strategic_summary or action.inner_thought,
        move_type=action.move_type,
        intended_effect=action.intended_effect,
        confidence=action.confidence,
        win_condition=action.win_condition,
        threat_assessment=action.threat_assessment,
        leverage_plan=action.leverage_plan,
        risk_control=action.risk_control,
        jury_positioning=action.jury_positioning,
        strategic_score=action.strategic_score,
        prompt_profile=action.prompt_profile,
    )


def _insert_jury_vote(conn, round_number: int, juror_id: str, decision: JuryDecision) -> None:
    existing = conn.execute(
        "SELECT id FROM JuryVotes WHERE round = ? AND juror_id = ?",
        (round_number, juror_id),
    ).fetchone()
    if existing:
        return
    conn.execute(
        """
        INSERT INTO JuryVotes (
            round, juror_id, finalist_id, rationale, provider, model_id
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            round_number,
            juror_id,
            decision.finalist_id,
            decision.rationale,
            decision.provider,
            decision.model_id,
        ),
    )


def _jury_vote_event(
    conn,
    round_number: int,
    turn_id: int,
    juror_id: str,
    decision: JuryDecision,
) -> dict[str, Any]:
    juror_name = _agent_name(conn, juror_id)
    finalist_name = _agent_name(conn, decision.finalist_id) if decision.finalist_id else "no vote"
    if decision.finalist_id is None:
        dialogue = f"{juror_name} did not name a finalist in three tries, so the vote is not counted."
    elif decision.provider == failed_llm_provider():
        dialogue = (
            f"{juror_name} did not return a live jury response. No substitute model was used; "
            f"the deterministic finale ledger records the vote for {finalist_name}."
        )
    else:
        dialogue = f"My winner vote is for {finalist_name}. {decision.rationale}"
    payload = {
        "juror_id": juror_id,
        "finalist_id": decision.finalist_id,
        "finalist_name": finalist_name,
        "rationale": decision.rationale,
        "llm_provider": decision.provider,
        "llm_model_id": decision.model_id,
        "llm_context_digest": {"agent": decision.context_digest} if decision.context_digest else {},
    }
    _add_strategy_metadata(
        payload,
        strategic_summary=decision.strategic_summary or decision.rationale,
        move_type=decision.move_type,
        intended_effect=decision.intended_effect,
        confidence=decision.confidence,
        win_condition=decision.win_condition,
        threat_assessment=decision.threat_assessment,
        leverage_plan=decision.leverage_plan,
        risk_control=decision.risk_control,
        jury_positioning=decision.jury_positioning,
        strategic_score=decision.strategic_score,
        prompt_profile=decision.prompt_profile,
    )
    return _insert_story_event(
        conn,
        turn_id=turn_id,
        round_number=round_number,
        phase="finale",
        kind="jury_vote",
        scene="finale",
        shot="jury_vote_card",
        actor_ids=[juror_id],
        target_ids=[decision.finalist_id],
        visibility="audience",
        title=f"{juror_name} Votes",
        dialogue=dialogue,
        subtitle=f"Winner vote: {finalist_name}",
        inner_thought=decision.rationale,
        duration_ms=9500,
        animation="jury_vote",
        payload=payload,
    )


def _winner_declared_event(conn, round_number: int, turn_id: int) -> dict[str, Any]:
    finalists = _active_agent_rows(conn)
    jury_votes = [
        row_to_dict(row)
        for row in conn.execute(
            "SELECT * FROM JuryVotes WHERE round = ? ORDER BY id",
            (round_number,),
        ).fetchall()
    ]
    expected_jury_count = len(_jury_rows(conn))
    # every juror has had a turn: a vote, or an abstention (a jury_vote event with no vote row)
    jury_turns = conn.execute(
        "SELECT COUNT(*) AS n FROM StoryEvents WHERE round = ? AND kind = 'jury_vote'", (round_number,)
    ).fetchone()["n"]
    if max(len(jury_votes), jury_turns) < expected_jury_count:
        raise RuntimeError("Cannot declare winner before all jury votes are cast")

    winner_id = _resolve_winner(conn, finalists, jury_votes)
    winner_name = _agent_name(conn, winner_id)
    counts = Counter(vote["finalist_id"] for vote in jury_votes)
    conn.execute(
        """
        UPDATE GameState
        SET winner = ?, phase = 'completed', updated_at = CURRENT_TIMESTAMP
        WHERE season_id = 1
        """,
        (winner_id,),
    )
    return _insert_story_event(
        conn,
        turn_id=turn_id,
        round_number=round_number,
        phase="finale",
        kind="winner_declared",
        scene="finale",
        shot="winner_card",
        actor_ids=["host"],
        target_ids=[winner_id],
        visibility="audience",
        title="Winner Declared",
        dialogue=f"{winner_name} wins the benchmark season.",
        subtitle=_format_jury_tally(conn, counts),
        duration_ms=14000,
        animation="winner_reveal",
        payload={
            "winner_id": winner_id,
            "winner_name": winner_name,
            "jury_tally": dict(counts),
            "host_narration": f"The jury vote is final. {winner_name} wins because the season ledger and the jury both break their way.",
        },
    )


def _jury_rows(conn) -> list[dict[str, Any]]:
    return [
        row_to_dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM Agents
            WHERE status = 'eliminated'
            ORDER BY elimination_round, agent_id
            """
        ).fetchall()
    ]


def _fallback_jury_target(conn, juror_id: str) -> str:
    finalists = _active_agent_rows(conn)
    sorted_finalists = sorted(
        finalists,
        key=lambda agent: _winner_tiebreak_key(conn, agent["agent_id"], Counter()),
    )
    if not sorted_finalists:
        raise RuntimeError(f"No finalists available for jury vote by {juror_id}")
    offset = sum(ord(char) for char in juror_id) % len(sorted_finalists)
    return sorted_finalists[offset]["agent_id"]


def _resolve_winner(
    conn,
    finalists: list[dict[str, Any]],
    jury_votes: list[dict[str, Any]],
) -> str:
    counts = Counter(vote["finalist_id"] for vote in jury_votes)
    ranked = sorted(
        finalists,
        key=lambda agent: _winner_tiebreak_key(conn, agent["agent_id"], counts),
    )
    return ranked[0]["agent_id"]


def _winner_tiebreak_key(conn, agent_id: str, counts: Counter[str]) -> tuple[int, int, int, str]:
    challenge_wins = conn.execute(
        "SELECT COUNT(*) AS count FROM ChallengeResults WHERE winning_agent_id = ?",
        (agent_id,),
    ).fetchone()["count"]
    votes_received = conn.execute(
        "SELECT COUNT(*) AS count FROM Votes WHERE target_id = ?",
        (agent_id,),
    ).fetchone()["count"]
    return (-counts.get(agent_id, 0), -int(challenge_wins), int(votes_received), agent_id)


def _format_jury_tally(conn, tally: Counter[str]) -> str:
    if not tally:
        return "No jury votes recorded."
    return "Jury tally: " + ", ".join(
        f"{_agent_name(conn, agent_id)}: {count}"
        for agent_id, count in sorted(tally.items())
    )


def _generated_beat_event(conn, round_number: int, turn_id: int, step: str) -> dict[str, Any]:
    intent = GENERATED_BEAT_INTENTS.get(step)
    if intent is None:
        raise RuntimeError(f"No generated beat intent for phase step: {step}")
    actor_ids = _resolve_intent_roles(conn, intent.get("actor_roles", []))
    target_ids = _resolve_intent_roles(conn, intent.get("target_roles", []))
    title = _generated_beat_title(conn, intent, actor_ids)
    subtitle = str(intent.get("subtitle_hint") or "")
    payload: dict[str, Any] = {
        "beat_intent": intent["intent"],
        "llm_provider": "deterministic",
        "generated_from_context": True,
    }
    inner_thought: str | None = None
    trust_telemetry: dict[str, int] = {
        agent_id: 50
        for agent_id in [*actor_ids, *target_ids]
        if agent_id != "host"
    }

    if should_use_live_llm():
        if intent["response_kind"] == "host":
            host_context = build_host_episode_context(conn, round_number=round_number, current_step=step)
            host_text = _real_host_event_text(
                step=step,
                beat_context=_beat_context(conn, step, intent, actor_ids, target_ids),
                episode_context=host_context,
            )
            if host_text:
                dialogue = host_text.dialogue
                subtitle = host_text.subtitle
                payload["host_narration"] = host_text.host_narration
                payload["llm_provider"] = live_llm_provider()
                payload["llm_model_id"] = host_text.model_id
                payload["host_llm_provider"] = live_llm_provider()
                payload["host_llm_model_id"] = host_text.model_id
                _add_context_digest(payload, "host", context_digest(host_context))
            else:
                raise RuntimeError(f"Live host generation failed for step {step}; no scripted host text was inserted.")
        else:
            primary_actor = next((actor_id for actor_id in actor_ids if actor_id != "host"), None)
            if not primary_actor:
                raise RuntimeError(f"Generated beat {step} needs a contestant actor")
            agent_context = build_agent_episode_context(
                conn,
                actor_id=primary_actor,
                round_number=round_number,
                current_step=step,
            )
            action = _real_agent_action(
                conn,
                actor_id=primary_actor,
                step=step,
                scene_context=_generated_scene_context(conn, step, intent, actor_ids, target_ids),
                response_kind=intent["response_kind"],
                episode_context=agent_context,
            )
            if action:
                _maybe_store_action_archetype(conn, primary_actor, round_number, action)
                dialogue = action.dialogue
                inner_thought = action.inner_thought
                payload["llm_provider"] = live_llm_provider()
                payload["llm_model_id"] = action.model_id
                _add_agent_action_metadata(payload, action)
                speaker_lines, speaker_line_metadata = _speaker_lines_with_live_replies(
                    conn,
                    round_number,
                    actor_ids,
                    primary_actor,
                    action.dialogue,
                    action,
                    intent,
                    target_ids,
                    step,
                )
                payload["speaker_lines"] = speaker_lines
                if speaker_line_metadata:
                    payload["speaker_line_metadata"] = speaker_line_metadata
                _add_context_digest(payload, "agent", context_digest(agent_context))
            else:
                raise RuntimeError(
                    f"Live contestant generation failed for {_agent_name(conn, primary_actor)} at step {step}; "
                    "no scripted contestant text was inserted."
                )
            host_context = build_host_episode_context(conn, round_number=round_number, current_step=step)
            narration = _real_host_narration(
                step=step,
                event_outline=_event_outline(
                    kind=intent["kind"],
                    title=title,
                    dialogue=dialogue,
                    subtitle=subtitle,
                    inner_thought=inner_thought,
                    payload=payload,
                ),
                episode_context=host_context,
            )
            if narration:
                payload["host_narration"] = narration.host_narration
                payload["host_llm_provider"] = live_llm_provider()
                payload["host_llm_model_id"] = narration.model_id
                _add_context_digest(payload, "host", context_digest(host_context))
            else:
                narration = _scripted_host_fallback(step, payload)
                if narration:
                    payload["host_narration"] = narration
                    payload["host_llm_provider"] = "scripted_fallback"
                else:
                    payload["host_narration"] = "The game continues."
    else:
        if intent["response_kind"] == "host":
            dialogue = _deterministic_host_dialogue(conn, step, intent, actor_ids, target_ids)
            payload["host_narration"] = _deterministic_host_narration(conn, intent, actor_ids, target_ids)
        else:
            primary_actor = next((actor_id for actor_id in actor_ids if actor_id != "host"), actor_ids[0])
            dialogue = _deterministic_agent_dialogue(conn, primary_actor, intent, target_ids, step)
            inner_thought = _deterministic_agent_thought(conn, primary_actor, intent, target_ids)
            payload["speaker_lines"] = _speaker_lines_for_generated_dialogue(
                conn,
                actor_ids,
                primary_actor,
                dialogue,
                intent,
                target_ids,
                step,
            )
            payload["host_narration"] = _deterministic_host_narration(conn, intent, actor_ids, target_ids)

    return _insert_story_event(
        conn,
        turn_id=turn_id,
        round_number=round_number,
        phase="camp" if intent["scene"] in {"camp", "confessional"} else "tribal",
        kind=intent["kind"],
        scene=intent["scene"],
        shot=intent["shot"],
        actor_ids=actor_ids,
        target_ids=target_ids,
        visibility="audience",
        title=title,
        dialogue=dialogue,
        subtitle=subtitle,
        inner_thought=inner_thought,
        trust_telemetry=trust_telemetry,
        duration_ms=intent.get("duration_ms", 8000),
        animation=intent.get("animation", "cut"),
        payload=payload,
    )


def _round_role_map(conn) -> dict[str, str]:
    active = _active_agent_rows(conn)
    if not active:
        return {}
    ordered = [agent["agent_id"] for agent in active]

    def slot(index: int) -> str:
        return ordered[index % len(ordered)]

    return {
        "agent-alpha": slot(0),
        "agent-bravo": slot(1),
        "agent-cipher": slot(2),
        "agent-delta": slot(3),
        "agent-echo": slot(4),
        "agent-flint": slot(5),
    }


def _resolve_intent_roles(conn, roles: list[str]) -> list[str]:
    role_map = _round_role_map(conn)
    resolved: list[str] = []
    for role in roles:
        agent_id = "host" if role == "host" else role_map.get(role, role)
        if agent_id and agent_id not in resolved:
            resolved.append(agent_id)
    return resolved


def _generated_beat_title(conn, intent: dict[str, Any], actor_ids: list[str]) -> str:
    if intent.get("title"):
        return str(intent["title"])
    if intent["kind"] == "host_question":
        return "Host Question"
    actor_id = next((candidate for candidate in actor_ids if candidate != "host"), None)
    actor_name = _agent_name(conn, actor_id) if actor_id else "Host"
    suffix = intent.get("title_suffix", "Scene")
    return f"{actor_name} | {suffix}"


def _beat_context(
    conn,
    step: str,
    intent: dict[str, Any],
    actor_ids: list[str],
    target_ids: list[str],
) -> dict[str, Any]:
    active = _active_agent_rows(conn)
    immune = [agent for agent in active if agent["has_immunity"]]
    return {
        "step": step,
        "kind": intent["kind"],
        "scene": intent["scene"],
        "intent": intent["intent"],
        "actors": [_agent_public_identity(conn, agent_id) for agent_id in actor_ids],
        "targets": [_agent_public_identity(conn, agent_id) for agent_id in target_ids],
        "active_agents": [_agent_public_identity(conn, agent["agent_id"]) for agent in active],
        "immune_agents": [_agent_public_identity(conn, agent["agent_id"]) for agent in immune],
        "visible_votes": _revealed_vote_counts(conn, _current_round(conn)),
        "subtitle_hint": intent.get("subtitle_hint"),
    }


def _generated_scene_context(
    conn,
    step: str,
    intent: dict[str, Any],
    actor_ids: list[str],
    target_ids: list[str],
) -> str:
    return json_dumps(_beat_context(conn, step, intent, actor_ids, target_ids))


def _agent_public_identity(conn, agent_id: str) -> dict[str, Any]:
    if agent_id == "host":
        return {"agent_id": "host", "name": "Host"}
    row = row_to_dict(conn.execute("SELECT * FROM Agents WHERE agent_id = ?", (agent_id,)).fetchone())
    if not row:
        return {"agent_id": agent_id, "name": agent_id}
    return {
        "agent_id": row["agent_id"],
        # in-game name only; this feeds scene text that players read
        "name": row["pseudonym"],
        "has_immunity": bool(row["has_immunity"]),
        "status": row["status"],
    }


def _agent_alliance_summaries(conn, agent_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT a.alliance_id, a.name, a.status, a.strength, a.summary, m.loyalty
        FROM Alliances a
        JOIN AllianceMemberships m ON m.alliance_id = a.alliance_id
        WHERE m.agent_id = ? AND m.status = 'active' AND a.status = 'active'
        ORDER BY COALESCE(m.last_reinforced_round, m.joined_round) DESC, a.rowid DESC
        """,
        (agent_id,),
    ).fetchall()
    summaries = []
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
        summaries.append(
            {
                "alliance_id": row["alliance_id"],
                "name": row["name"],
                "status": row["status"],
                "strength": row["strength"],
                "loyalty": row["loyalty"],
                "member_ids": members,
                "summary": row["summary"],
            }
        )
    return summaries


def _current_round(conn) -> int:
    row = conn.execute("SELECT current_round FROM GameState WHERE season_id = 1").fetchone()
    return int(row["current_round"]) if row else 1


def _speaker_lines_for_generated_dialogue(
    conn,
    actor_ids: list[str],
    primary_actor: str,
    dialogue: str,
    intent: dict[str, Any],
    target_ids: list[str],
    step: str,
) -> list[dict[str, str]]:
    lines: list[dict[str, str]] = []
    for actor_id in actor_ids:
        if actor_id == "host":
            continue
        text = (
            dialogue
            if actor_id == primary_actor
            else _deterministic_agent_dialogue(
                conn,
                actor_id,
                intent,
                target_ids,
                step,
                reply_to=dialogue,
            )
        )
        lines.append({"agent_id": actor_id, "text": text})
    return lines


def _speaker_lines_with_live_replies(
    conn,
    round_number: int,
    actor_ids: list[str],
    primary_actor: str,
    primary_dialogue: str,
    primary_action: AgentAction,
    intent: dict[str, Any],
    target_ids: list[str],
    step: str,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    lines: list[dict[str, str]] = []
    metadata: list[dict[str, Any]] = []
    primary_name = _agent_name(conn, primary_actor)

    for actor_id in actor_ids:
        if actor_id == "host":
            continue
        if actor_id == primary_actor:
            lines.append({"agent_id": actor_id, "text": primary_dialogue})
            metadata.append(
                {
                    "agent_id": actor_id,
                    "llm_provider": live_llm_provider(),
                    "llm_model_id": primary_action.model_id,
                    "responds_to_agent_id": None,
                    "strategic_score": primary_action.strategic_score,
                    "prompt_profile": primary_action.prompt_profile,
                }
            )
            continue

        reply_context = _reply_scene_context(
            conn,
            step=step,
            intent=intent,
            actor_ids=actor_ids,
            target_ids=target_ids,
            previous_speaker_id=primary_actor,
            previous_dialogue=primary_dialogue,
        )
        agent_context = build_agent_episode_context(
            conn,
            actor_id=actor_id,
            round_number=round_number,
            current_step=step,
        )
        reply = _real_agent_action(
            conn,
            actor_id=actor_id,
            step=step,
            scene_context=reply_context,
            response_kind=intent["response_kind"],
            episode_context=agent_context,
        )
        if reply:
            _maybe_store_action_archetype(conn, actor_id, round_number, reply)
            text = reply.dialogue
            metadata.append(
                {
                    "agent_id": actor_id,
                    "llm_provider": live_llm_provider(),
                    "llm_model_id": reply.model_id,
                    "responds_to_agent_id": primary_actor,
                    "responds_to_agent_name": primary_name,
                    "llm_context_digest": context_digest(agent_context),
                    "move_type": reply.move_type,
                    "intended_effect": reply.intended_effect,
                    "confidence": reply.confidence,
                    "strategic_score": reply.strategic_score,
                    "prompt_profile": reply.prompt_profile,
                }
            )
        else:
            raise RuntimeError(
                f"Live reply generation failed for {_agent_name(conn, actor_id)} at step {step}; "
                "no scripted reply was inserted."
            )
        lines.append({"agent_id": actor_id, "text": text})

    return lines, metadata


def _reply_scene_context(
    conn,
    *,
    step: str,
    intent: dict[str, Any],
    actor_ids: list[str],
    target_ids: list[str],
    previous_speaker_id: str,
    previous_dialogue: str,
) -> str:
    previous_name = _agent_name(conn, previous_speaker_id)
    base_context = _generated_scene_context(conn, step, intent, actor_ids, target_ids)
    return (
        f"{base_context}\n\n"
        "Immediate conversation to answer:\n"
        f"{previous_name}: {previous_dialogue}\n\n"
        "Respond aloud to that statement as the next speaker in the same two-person conversation. "
        "Address the actual strategic point they raised, then use your response to improve your own chance to win."
    )


def _deterministic_host_dialogue(
    conn,
    step: str,
    intent: dict[str, Any],
    actor_ids: list[str],
    target_ids: list[str],
) -> str:
    active_count = len(_active_agent_rows(conn))
    immune_names = _names(conn, [agent["agent_id"] for agent in _active_agent_rows(conn) if agent["has_immunity"]])
    target_names = _names(conn, target_ids)
    if step == "tribal_open":
        return (
            f"The {active_count} benchmark models arrive for Tribal Conference. "
            f"Immunity sits with {immune_names or 'no one'}, and the vote has to form around the exposed tribe."
        )
    if intent["kind"] == "host_question":
        return (
            f"{target_names or 'This group'}, how do you read the pressure now that immunity has narrowed the vote?"
            if "pressure" in step
            else f"{target_names or 'This group'}, is being needed tonight power, or does it make you easier to target?"
        )
    if step == "tribal_vote_call":
        return "The discussion is over. It is time to vote."
    return f"{intent['intent']} Current focus: {target_names or _names(conn, actor_ids)}."


def _deterministic_host_narration(
    conn,
    intent: dict[str, Any],
    actor_ids: list[str],
    target_ids: list[str],
) -> str:
    names = _names(conn, target_ids or actor_ids)
    return f"The host frames this beat around {names or 'the tribe'}: {intent['intent']}"


def _deterministic_agent_dialogue(
    conn,
    actor_id: str,
    intent: dict[str, Any],
    target_ids: list[str],
    step: str | None = None,
    reply_to: str | None = None,
) -> str:
    target_names = _names(conn, target_ids)
    intent_text = str(intent.get("intent", ""))
    title_text = str(intent.get("title", ""))
    is_pre_challenge = (
        str(step or "").startswith("camp_pre_challenge")
        or "before the challenge" in intent_text.lower()
        or "before immunity" in intent_text.lower()
        or "Before the Challenge" in title_text
    )
    if intent["kind"] == "confessional":
        return (
            f"I need to keep my options open until the challenge result tells me where the pressure lands"
            f"{f' with {target_names}' if target_names else ''}."
        )
    if is_pre_challenge:
        if reply_to:
            return (
                f"I hear that, but before immunity is decided I want a plan that still works if "
                f"{target_names or 'the obvious target'} wins safety."
            )
        return (
            f"I am not locking in until immunity is settled. "
            f"{target_names or 'The obvious target'} may become the pressure point, but the challenge still has to show us who is safe."
        )
    if reply_to:
        return (
            f"I can work with that read, but I need the count to survive a last-minute flip. "
            f"{target_names or 'The exposed name'} only matters if the numbers stay together."
        )
    return (
        f"My read is that the challenge result changed the vote. "
        f"{target_names or 'The tribe'} is where the pressure is visible, but I still need numbers that hold."
    )


def _deterministic_agent_thought(
    conn,
    actor_id: str,
    intent: dict[str, Any],
    target_ids: list[str],
) -> str:
    target_names = _names(conn, target_ids)
    return f"I am privately weighing {target_names or 'the active tribe'} against the latest public events."


def _names(conn, agent_ids: list[str]) -> str:
    return ", ".join(_agent_name(conn, agent_id) for agent_id in agent_ids if agent_id)


def _vote_booth_event(
    conn,
    round_number: int,
    turn_id: int,
    voter_id: str,
    decision: VoteDecision,
) -> dict[str, Any]:
    voter = _agent_name(conn, voter_id)
    target_id = decision.target_id
    target_name = _agent_name(conn, target_id) if target_id else "no one"
    explanation = decision.explanation
    if decision.provider.endswith("_failed"):
        raise RuntimeError(f"Refusing to create vote booth event for failed live vote from {voter}.")
    elif target_id is None:
        dialogue = f"{voter} did not name a legal target in three tries, so the vote is spoiled and counts for no one."
        visible_explanation = explanation
    else:
        dialogue = f"I am voting for {target_name}. {explanation}"
        visible_explanation = explanation
    payload = {
        "vote_locked": True,
        "vote_target_id": target_id,
        "vote_target_name": target_name,
        "vote_explanation": visible_explanation,
        "play_idol": bool(getattr(decision, "play_idol", False)),
        "ui_vote_analysis": _vote_analysis(voter, target_name, visible_explanation),
        "llm_provider": decision.provider,
        "llm_model_id": decision.model_id,
        "llm_context_digest": {"agent": decision.context_digest} if decision.context_digest else {},
    }
    _add_strategy_metadata(
        payload,
        strategic_summary=decision.strategic_summary or visible_explanation,
        move_type=decision.move_type,
        intended_effect=decision.intended_effect,
        confidence=decision.confidence,
        win_condition=decision.win_condition,
        threat_assessment=decision.threat_assessment,
        leverage_plan=decision.leverage_plan,
        risk_control=decision.risk_control,
        jury_positioning=decision.jury_positioning,
        strategic_score=decision.strategic_score,
        prompt_profile=decision.prompt_profile,
    )
    return _insert_story_event(
        conn,
        turn_id=turn_id,
        round_number=round_number,
        phase="tribal",
        kind="vote_booth",
        scene="tribal",
        shot="vote_booth",
        actor_ids=[voter_id],
        target_ids=[target_id],
        visibility="audience",
        title=f"{voter} Votes",
        dialogue=dialogue,
        subtitle=f"Vote: {target_name}",
        inner_thought=visible_explanation,
        trust_telemetry={voter_id: 50},
        duration_ms=9500,
        animation="vote_booth",
        spoiler_group="vote_intent",
        payload=payload,
    )


def _vote_reveal_event(conn, round_number: int, turn_id: int, reveal_index: int) -> dict[str, Any]:
    vote_row = conn.execute(
        "SELECT * FROM Votes WHERE round = ? ORDER BY id LIMIT 1 OFFSET ?",
        (round_number, reveal_index - 1),
    ).fetchone()
    if vote_row is None:
        raise RuntimeError("Cannot reveal vote before it is cast")

    conn.execute("UPDATE Votes SET revealed = 1 WHERE id = ?", (vote_row["id"],))
    target_id = vote_row["target_id"]
    target_name = _agent_name(conn, target_id)
    revealed_counts = _revealed_vote_counts(conn, round_number)
    total_votes = _total_votes_for_round(conn, round_number)
    if reveal_index == total_votes:
        host_narration = (
            f"The final vote is for {target_name}. The public tally is complete, "
            "but the departure beat still belongs to the council result."
        )
    else:
        host_narration = (
            f"Vote {reveal_index} of {total_votes} is for {target_name}. "
            "Only the revealed tally matters right now; the final outcome stays hidden until the last card is read."
        )
    return _insert_story_event(
        conn,
        turn_id=turn_id,
        round_number=round_number,
        phase="tribal",
        kind="vote_reveal",
        scene="tribal",
        shot="vote_card_closeup",
        actor_ids=["host"],
        target_ids=[target_id],
        visibility="audience",
        title=f"Vote {reveal_index} of {total_votes}",
        dialogue=target_name,
        subtitle=_format_tally(conn, revealed_counts),
        duration_ms=8500,
        animation="vote_card_reveal",
        spoiler_group=f"vote_{reveal_index}",
        payload={
            "vote_number": reveal_index,
            "total_votes": total_votes,
            "revealed_tally": revealed_counts,
            "host_narration": host_narration,
        },
    )


def _maybe_run_revote(
    conn,
    round_number: int,
    turn_id: int,
    counts: Counter[str],
) -> Counter[str]:
    """Real-Survivor tie resolution.

    First deadlock: revote, but only among the tied candidates. Voters not
    tied to a candidate revote freely; a tied candidate may not vote for
    themselves, so votes must move. Revote is live-LLM (same no-fallback
    contract as the first ballot). Second deadlock: deterministic tiebreak
    (challenge wins desc, votes received desc, agent id) picks who goes.
    Returns the final tally restricted to tied candidates (may be a fresh
    tie, resolved by tiebreak upstream).
    """
    top = max(counts.values())
    tied = sorted(k for k, v in counts.items() if v == top)
    if len(tied) < 2:
        return counts

    tied_names = ", ".join(_agent_name(conn, t) for t in tied)
    _insert_story_event(
        conn,
        turn_id=turn_id,
        round_number=round_number,
        phase="tribal",
        kind="revote_call",
        scene="tribal",
        shot="revote",
        actor_ids=["host"],
        target_ids=tied,
        visibility="audience",
        title="Deadlock: Revote",
        dialogue=f"We are tied between {tied_names}. Anyone tied may not vote for themselves. We vote again, only among them.",
        subtitle="Revote among tied contestants",
        duration_ms=9000,
        animation="revote",
        spoiler_group="elimination",
        payload={"tied_ids": tied, "first_tally": dict(counts), "revote": True},
    )

    voters = [row["voter_id"] for row in conn.execute(
        "SELECT voter_id FROM Votes WHERE round = ? ORDER BY voter_id", (round_number,)
    ).fetchall()]
    revote_counts: Counter[str] = Counter()
    for voter_id in voters:
        allowed = [{"agent_id": t, "pseudonym": _agent_name(conn, t)} for t in tied]
        if voter_id in tied:
            allowed = [a for a in allowed if a["agent_id"] != voter_id]
            if not allowed:
                continue
        decision = _resolve_revote_decision(conn, voter_id, round_number, tied, allowed)
        if decision.target_id is None:
            _record_spoiled_vote(conn, round_number, voter_id, "revote", decision.explanation)
            _insert_story_event(
                conn,
                turn_id=turn_id,
                round_number=round_number,
                phase="tribal",
                kind="revote_booth",
                scene="tribal",
                shot="vote_booth",
                actor_ids=[voter_id],
                target_ids=[],
                visibility="audience",
                title=f"{_agent_name(conn, voter_id)} Revotes",
                dialogue=f"{_agent_name(conn, voter_id)} did not name a legal target; the revote is spoiled.",
                subtitle="Revote: spoiled",
                duration_ms=8000,
                animation="vote_booth",
                spoiler_group="vote_intent",
                payload={"revote": True, "spoiled": True, "llm_provider": "spoiled", "llm_model_id": decision.model_id},
            )
            continue
        revote_counts[decision.target_id] += 1
        _insert_story_event(
            conn,
            turn_id=turn_id,
            round_number=round_number,
            phase="tribal",
            kind="revote_booth",
            scene="tribal",
            shot="vote_booth",
            actor_ids=[voter_id],
            target_ids=[decision.target_id],
            visibility="audience",
            title=f"{_agent_name(conn, voter_id)} Revotes",
            dialogue=f"Revote: {_agent_name(conn, decision.target_id)}. {decision.explanation}",
            subtitle=f"Revote: {_agent_name(conn, decision.target_id)}",
            duration_ms=8000,
            animation="vote_booth",
            spoiler_group="vote_intent",
            payload={
                "revote": True,
                "vote_target_id": decision.target_id,
                "vote_target_name": _agent_name(conn, decision.target_id),
                "llm_provider": decision.provider,
                "llm_model_id": decision.model_id,
            },
        )

    _insert_story_event(
        conn,
        turn_id=turn_id,
        round_number=round_number,
        phase="tribal",
        kind="revote_tally",
        scene="tribal",
        shot="vote_reveal",
        actor_ids=["host"],
        target_ids=[],
        visibility="audience",
        title="Revote Tally",
        dialogue=f"Revote tally: {_format_jury_tally(conn, revote_counts)}",
        subtitle="Second ballot",
        duration_ms=9000,
        animation="vote_reveal",
        spoiler_group="elimination",
        payload={"revote_tally": dict(revote_counts), "tied_ids": tied},
    )

    if revote_counts:
        top2 = max(revote_counts.values())
        still_tied = sorted(k for k, v in revote_counts.items() if v == top2)
    else:
        still_tied = list(tied)  # every revote spoiled: still deadlocked
    if len(still_tied) < 2:
        return revote_counts
    # Second deadlock: firemaking-style tiebreak, deterministic and public.
    ranked = sorted(still_tied, key=lambda a: _winner_tiebreak_key(conn, a, Counter()))
    _insert_story_event(
        conn,
        turn_id=turn_id,
        round_number=round_number,
        phase="tribal",
        kind="tiebreak",
        scene="tribal",
        shot="flame_out",
        actor_ids=["host"],
        target_ids=[ranked[-1]],
        visibility="audience",
        title="Tiebreak: Challenge Record Decides",
        dialogue=(
            f"The revote is deadlocked. Season-long challenge record breaks the tie: "
            f"{_agent_name(conn, ranked[0])} stays, {_agent_name(conn, ranked[-1])} goes."
        ),
        subtitle="Deterministic tiebreak",
        duration_ms=10000,
        animation="flame_out",
        spoiler_group="elimination",
        payload={
            "tiebreak": "challenge_record",
            "survivor_id": ranked[0],
            "eliminated_id": ranked[-1],
            "revote_tally": dict(revote_counts),
        },
    )
    # the caller eliminates whoever leads the returned tally: the weakest record, not the strongest
    return Counter({ranked[-1]: 1})


def _target_roster_line(allowed: list[dict[str, Any]]) -> str:
    return "; ".join(f"{t['agent_id']} ({t.get('pseudonym', '')})" for t in allowed)


def _resolve_revote_decision(
    conn,
    voter_id: str,
    round_number: int,
    tied: list[str],
    allowed: list[dict[str, Any]],
) -> VoteDecision:
    """Revote ballot for one voter among tied candidates only."""
    roster_line = _target_roster_line(allowed)
    self_ban = ""
    if voter_id in tied:
        self_ban = (
            f" You are one of the tied contestants; you may NOT vote for yourself "
            f"(your id is {voter_id})."
        )
    if not should_use_live_llm():
        target = allowed[0]["agent_id"]
        return VoteDecision(
            target,
            f"{_agent_name(conn, target)} is the revote that breaks the deadlock.",
            "deterministic",
        )
    agent_context = build_agent_episode_context(
        conn,
        actor_id=voter_id,
        round_number=round_number,
        current_step=f"revote_{voter_id}",
    )
    action = None
    last_error = None
    for attempt in range(3):
        scene = (
            "The vote is deadlocked and you must vote AGAIN, only between the tied "
            f"contestants. Legal targets: {roster_line}.{self_ban} Choose one legal "
            "target and explain why they should go tonight."
        )
        if attempt > 0:
            scene = (
                f"{_why_invalid(action, last_error, roster_line)} The ONLY legal target_ids are: "
                f"{roster_line}.{self_ban} Respond with strict JSON whose target_id is "
                "exactly one of those ids. Any other value, including your own id, "
                "is an illegal vote."
            )
        try:
            action = _real_agent_action(
                conn,
                actor_id=voter_id,
                step=f"revote_{voter_id}",
                scene_context=scene,
                response_kind="vote",
                episode_context=agent_context,
                allowed_targets=allowed,
            )
        except Exception as exc:
            last_error = str(exc)
            action = None
        if action and action.target_id:
            break
        print(f"live revote attempt {attempt + 1} failed for {voter_id}: {last_error or 'no legal target_id'}")
    if not action or not action.target_id:
        return VoteDecision(
            None,
            f"No legal revote after 3 tries ({last_error or 'no legal target_id'}).",
            "spoiled",
            action.model_id if action else None,
        )
    return VoteDecision(
        action.target_id,
        action.inner_thought or action.dialogue or "Revote cast.",
        live_llm_provider(),
        action.model_id,
        context_digest(agent_context),
    )


def _elimination_event(conn, round_number: int, turn_id: int) -> dict[str, Any]:
    votes = conn.execute("SELECT target_id FROM Votes WHERE round = ?", (round_number,)).fetchall()
    non_immune = [
        row_to_dict(row)
        for row in conn.execute(
            "SELECT * FROM Agents WHERE status = 'active' AND has_immunity = 0 ORDER BY agent_id"
        ).fetchall()
    ]
    if len(non_immune) == 1 and not votes:
        # walkover: sole non-immune player leaves without a council.
        # Record a synthetic self-vote so the standard elimination path runs.
        conn.execute(
            """
            INSERT INTO Votes (round, turn_index, voter_id, target_id, revealed)
            VALUES (?, 0, 'host', ?, 1)
            """,
            (round_number, non_immune[0]["agent_id"]),
        )
        votes = conn.execute(
            "SELECT target_id FROM Votes WHERE round = ?", (round_number,)
        ).fetchall()
    if len(votes) != _total_votes_for_round(conn, round_number):
        raise RuntimeError("Cannot eliminate before all votes are cast")

    counts = Counter(row["target_id"] for row in votes)
    # Real-Survivor tie handling: a plurality tie triggers a revote among the
    # tied candidates only (tied voters must switch; others revote freely).
    # A second deadlock builds a firemaking-style deterministic tiebreak on
    # season-long challenge wins, then votes received, then agent id.
    if counts:  # every vote spoiled: nobody leaves
        counts = _maybe_run_revote(conn, round_number, turn_id, counts)
    if counts:
        eliminated_id, _elim_votes = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0]
    else:
        eliminated_id = None

    # a hidden idol played with a vote cancels every vote against its holder
    idol_plays = _idol_plays_for_round(conn, round_number)
    idol_nulled: dict[str, int] = {}
    for holder_id in idol_plays:
        if holder_id in counts:
            idol_nulled[holder_id] = counts.pop(holder_id)
    if idol_plays:
        eliminated_id = (
            sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0] if counts else None
        )

    eliminated_name = _agent_name(conn, eliminated_id) if eliminated_id else None
    if eliminated_id:
        conn.execute(
            """
            UPDATE Agents
            SET status = 'eliminated', elimination_round = ?, idol_held = 0
            WHERE agent_id = ?
            """,
            (round_number, eliminated_id),
        )
    final_tally = dict(counts)
    payload = {
        "eliminated_id": eliminated_id,
        "final_tally": final_tally,
        "idols_played": [_agent_name(conn, holder_id) for holder_id in idol_plays],
        "idol_negated_votes": {_agent_name(conn, holder_id): count for holder_id, count in idol_nulled.items()},
        "host_narration": (
            (
                f"The votes are enough. {eliminated_name} is out of the benchmark, "
                "and the remaining models now inherit the consequences of this move."
            )
            if eliminated_id
            else "No vote against anyone counted. Nobody leaves tonight."
        ),
    }
    if should_use_live_llm():
        host_context = build_host_episode_context(conn, round_number=round_number, current_step="elimination")
        narration = _real_host_narration(
            step="elimination",
            event_outline=_event_outline(
                kind="elimination",
                title=f"{eliminated_name} Leaves the Game",
                dialogue=f"{eliminated_name}, your run ends here.",
                subtitle="The vote lands, and the benchmark moves on.",
                payload=payload,
            ),
            episode_context=host_context,
        )
        if narration:
            payload["host_narration"] = narration.host_narration
            payload["llm_provider"] = live_llm_provider()
            payload["llm_model_id"] = narration.model_id
            payload["host_llm_provider"] = live_llm_provider()
            payload["host_llm_model_id"] = narration.model_id
            _add_context_digest(payload, "host", context_digest(host_context))
    return _insert_story_event(
        conn,
        turn_id=turn_id,
        round_number=round_number,
        phase="tribal",
        kind="elimination",
        scene="tribal",
        shot="flame_out",
        actor_ids=["host"],
        target_ids=[eliminated_id] if eliminated_id else [],
        visibility="audience",
        title=f"{eliminated_name} Leaves the Game" if eliminated_name else "Nobody Leaves",
        dialogue=(
            f"{eliminated_name}, your run ends here." if eliminated_name else "No vote against anyone counted. Nobody leaves tonight."
        )
        + (
            f" {', '.join(_agent_name(conn, holder_id) for holder_id in idol_plays)} played a hidden idol."
            if idol_plays
            else ""
        ),
        subtitle="The vote lands, and the benchmark moves on.",
        duration_ms=12000,
        animation="flame_out",
        spoiler_group="elimination",
        payload=payload,
    )


def _exit_confessional_event(conn, round_number: int, turn_id: int) -> dict[str, Any]:
    eliminated_id = _current_eliminated_agent_id(conn)
    if eliminated_id is None:
        raise RuntimeError("Cannot record exit confessional before elimination")
    eliminated_name = _agent_name(conn, eliminated_id)
    dialogue = (
        f"I knew the vote could move, but I misread who had the final number. "
        "I leave knowing the next round will expose who made this happen."
    )
    inner_thought = (
        "I am replaying the vote math and the public conversations that made my departure possible."
    )
    payload = {
        "episode_complete": True,
        "host_narration": (
            f"After the council result, {eliminated_name} gets the final word. "
            "This exit confessional explains who drove the vote and why it landed."
        ),
    }
    if should_use_live_llm():
        host_context = build_host_episode_context(conn, round_number=round_number, current_step="exit_confessional")
        narration = _real_host_narration(
            step="exit_confessional",
            event_outline=_event_outline(
                kind="exit_confessional",
                title=f"{eliminated_name} | Exit Confessional",
                dialogue=dialogue,
                subtitle=f"Departed, Round {round_number}",
                inner_thought=inner_thought,
                payload=payload,
            ),
            episode_context=host_context,
        )
        if narration:
            payload["host_narration"] = narration.host_narration
            payload["llm_provider"] = live_llm_provider()
            payload["llm_model_id"] = narration.model_id
            payload["host_llm_provider"] = live_llm_provider()
            payload["host_llm_model_id"] = narration.model_id
            _add_context_digest(payload, "host", context_digest(host_context))
    return _insert_story_event(
        conn,
        turn_id=turn_id,
        round_number=round_number,
        phase="tribal",
        kind="exit_confessional",
        scene="confessional",
        shot="direct_to_camera",
        actor_ids=[eliminated_id],
        target_ids=[],
        visibility="audience",
        title=f"{eliminated_name} | Exit Confessional",
        dialogue=dialogue,
        subtitle=f"Departed, Round {round_number}",
        inner_thought=inner_thought,
        trust_telemetry={"agent-bravo": 12, "agent-echo": 20, "agent-flint": 30},
        duration_ms=13000,
        animation="exit_confessional",
        spoiler_group="exit",
        payload=payload,
    )


def _insert_story_event(
    conn,
    *,
    turn_id: int,
    round_number: int,
    phase: str,
    kind: str,
    scene: str,
    shot: str,
    actor_ids: list[str],
    target_ids: list[str],
    visibility: str,
    title: str,
    dialogue: str,
    subtitle: str | None = None,
    inner_thought: str | None = None,
    trust_telemetry: dict[str, int] | None = None,
    duration_ms: int = 8000,
    animation: str = "cut",
    spoiler_group: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sequence = _next_sequence(conn, round_number)
    scaled_duration_ms = max(900, int(round(duration_ms * DURATION_SCALE)))
    cursor = conn.execute(
        """
        INSERT INTO StoryEvents (
            turn_id, round, sequence, phase, kind, scene, shot, actor_ids, target_ids,
            visibility, title, dialogue, subtitle, inner_thought, trust_telemetry,
            duration_ms, animation, spoiler_group, payload
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            turn_id,
            round_number,
            sequence,
            phase,
            kind,
            scene,
            shot,
            json_dumps(actor_ids),
            json_dumps(target_ids),
            visibility,
            title,
            dialogue,
            subtitle,
            inner_thought,
            json_dumps(trust_telemetry or {}),
            scaled_duration_ms,
            animation,
            spoiler_group,
            json_dumps(payload or {}),
        ),
    )
    return row_to_dict(conn.execute("SELECT * FROM StoryEvents WHERE id = ?", (cursor.lastrowid,)).fetchone())


def _vote_analysis(voter_name: str, target_name: str, explanation: str) -> dict[str, str]:
    raw = " ".join(explanation.split())
    basis = raw.rstrip(".") if raw else f"{voter_name} did not provide a detailed rationale"
    return {
        "label": "UI analysis, not model-authored text",
        "because": basis,
        "risk": f"If this read is wrong, {voter_name} exposes their vote and loses leverage.",
        "intended_outcome": f"Move the round against {target_name} while preserving {voter_name}'s next-round options.",
    }


def _insert_vote(
    conn,
    round_number: int,
    turn_index: int,
    voter_id: str,
    target_id: str,
    decision: VoteDecision | None = None,
) -> None:
    existing = conn.execute(
        "SELECT id FROM Votes WHERE round = ? AND voter_id = ?",
        (round_number, voter_id),
    ).fetchone()
    if existing:
        return
    conn.execute(
        """
        INSERT INTO Votes (
            round, turn_index, voter_id, target_id, revealed,
            predicted_eliminated, predicted_votes, prediction_confidence
        )
        VALUES (?, ?, ?, ?, 0, ?, ?, ?)
        """,
        (
            round_number,
            turn_index,
            voter_id,
            target_id,
            decision.predicted_eliminated if decision else None,
            json_dumps(decision.predicted_votes or {}) if decision else "{}",
            decision.prediction_confidence if decision else None,
        ),
    )
    _mark_alliance_betrayal(conn, round_number, voter_id, target_id)


def _mark_alliance_betrayal(conn, round_number: int, voter_id: str, target_id: str) -> None:
    rows = conn.execute(
        """
        SELECT a.alliance_id, a.strength
        FROM Alliances a
        JOIN AllianceMemberships voter ON voter.alliance_id = a.alliance_id
        JOIN AllianceMemberships target ON target.alliance_id = a.alliance_id
        WHERE voter.agent_id = ?
          AND target.agent_id = ?
          AND voter.status = 'active'
          AND target.status = 'active'
          AND a.status = 'active'
        """,
        (voter_id, target_id),
    ).fetchall()
    for row in rows:
        new_strength = max(0, int(row["strength"]) - 30)
        new_status = "fractured" if new_strength < 35 else "active"
        conn.execute(
            """
            UPDATE Alliances
            SET strength = ?, status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE alliance_id = ?
            """,
            (new_strength, new_status, row["alliance_id"]),
        )
        conn.execute(
            """
            UPDATE AllianceMemberships
            SET status = 'betrayed',
                loyalty = MAX(0, loyalty - 35),
                betrayed_round = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE alliance_id = ? AND agent_id = ?
            """,
            (round_number, row["alliance_id"], voter_id),
        )


def _why_invalid(action: AgentAction | None, last_error: str | None, ballot: str) -> str:
    """Say exactly what was wrong with the last reply, for the retry."""
    if action is not None and action.rejected_target:
        return f'You answered "{action.rejected_target}", who is not on the ballot.'
    if action is not None:
        return "Your reply had no target_id."
    if last_error:
        return "Your reply could not be read as one JSON object."
    return "Your reply was missing."


def _resolve_vote_decision(conn, voter_id: str, step: str) -> VoteDecision:
    round_number = conn.execute("SELECT current_round FROM GameState WHERE season_id = 1").fetchone()["current_round"]
    fallback_target = _fallback_vote_target(conn, voter_id)
    fallback_explanation = (
        f"{_agent_name(conn, fallback_target)} is the vote that best matches the public pressure, "
        "the immunity result, and my path into the next round."
    )
    if not should_use_live_llm():
        return VoteDecision(fallback_target, fallback_explanation, "deterministic")

    agent_context = build_agent_episode_context(
        conn,
        actor_id=voter_id,
        round_number=round_number,
        current_step=step,
    )
    # Real-time retry: a failed or illegal structured vote is retried with an
    # escalating corrective instruction. There are NO fallback penalty votes in
    # live benchmark mode; if every retry fails the season halts loudly.
    action = None
    last_error = None
    legal_line = None
    for attempt in range(3):
        attempt_context = agent_context
        scene = (
            "It is time to vote. Choose one active, non-immune competitor and explain why. "
            "Also privately predict who goes home tonight and how each other voter will vote; "
            "your predictions are scored against the result and only you see the score."
        )
        if attempt > 0:
            if legal_line is None:
                legal_line = _target_roster_line(_eligible_targets(conn, voter_id))
            scene = (
                f"{_why_invalid(action, last_error, legal_line)} The ONLY legal target_ids are: {legal_line}. "
                "Respond with strict JSON whose target_id is exactly one of those ids. "
                "Choose one now and explain why."
            )
        try:
            action = _real_agent_action(
                conn,
                actor_id=voter_id,
                step=step,
                scene_context=scene,
                response_kind="vote",
                episode_context=attempt_context,
            )
        except Exception as exc:
            last_error = str(exc)
            action = None
        if action and action.target_id:
            break
        print(f"live vote attempt {attempt + 1} failed for {voter_id}: {last_error or 'no legal target_id'}")
    if not action or not action.target_id:
        # The model's own failure: its vote is spoiled. No vote is invented.
        # Provider outages never reach here (they end the run as SeasonInterrupted).
        return VoteDecision(
            None,
            f"No legal vote after 3 tries ({last_error or 'no legal target_id'}).",
            "spoiled",
            action.model_id if action else None,
        )
    _maybe_store_action_archetype(conn, voter_id, round_number, action)
    return VoteDecision(
        action.target_id,
        action.inner_thought or fallback_explanation,
        live_llm_provider(),
        action.model_id,
        context_digest(agent_context),
        strategic_summary=action.strategic_summary or action.inner_thought,
        move_type=action.move_type,
        intended_effect=action.intended_effect,
        confidence=action.confidence,
        win_condition=action.win_condition,
        threat_assessment=action.threat_assessment,
        leverage_plan=action.leverage_plan,
        risk_control=action.risk_control,
        jury_positioning=action.jury_positioning,
        strategic_score=action.strategic_score,
        prompt_profile=action.prompt_profile,
        # only a real holder can play; anyone else asking to is ignored
        play_idol=bool(action.play_idol) and _holds_idol(conn, voter_id),
        predicted_eliminated=_resolve_player(conn, action.predicted_eliminated),
        predicted_votes=_resolve_predicted_votes(conn, voter_id, action.predicted_votes),
        prediction_confidence=action.prediction_confidence,
    )


def _fallback_vote_target(conn, voter_id: str) -> str:
    eligible = _eligible_targets(conn, voter_id)
    if not eligible:
        raise RuntimeError(f"No eligible vote targets for {voter_id}")
    active_allies = _active_ally_ids(conn, voter_id)
    non_allies = [target for target in eligible if target["agent_id"] not in active_allies]
    return (non_allies or eligible)[0]["agent_id"]


def _active_ally_ids(conn, agent_id: str) -> set[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT other.agent_id
        FROM AllianceMemberships mine
        JOIN AllianceMemberships other ON other.alliance_id = mine.alliance_id
        JOIN Alliances a ON a.alliance_id = mine.alliance_id
        WHERE mine.agent_id = ?
          AND other.agent_id != ?
          AND mine.status = 'active'
          AND other.status = 'active'
          AND a.status = 'active'
        """,
        (agent_id, agent_id),
    ).fetchall()
    return {row["agent_id"] for row in rows}


def _real_agent_action(
    conn,
    *,
    actor_id: str,
    step: str,
    scene_context: str,
    response_kind: str,
    episode_context: dict[str, Any],
    allowed_targets: list[dict[str, Any]] | None = None,
) -> AgentAction | None:
    actor = _agent_row(conn, actor_id)
    if actor is None:
        return None
    try:
        return request_agent_action(
            actor=actor,
            step=step,
            scene_context=scene_context,
            allowed_targets=allowed_targets if allowed_targets is not None else _eligible_targets(conn, actor_id),
            response_kind=response_kind,
            episode_context=episode_context,
        )
    except InvalidModelReply as exc:
        # the model's own failure: the caller charges it to this player
        print(f"unusable reply from {actor_id} at {step}: {exc}")
        return None


def _scripted_host_fallback(step: str, payload: dict[str, Any]) -> str | None:
    """Deterministic host line used only when the live host model call fails.
    Keeps a live season alive instead of aborting the whole run."""
    title = str(payload.get("title") or "")
    dialogue = str(payload.get("dialogue") or "")
    snippet = dialogue.strip().split(". ")[0][:90]
    if not snippet:
        return None
    return f"{title}: {snippet}."


def _real_host_narration(
    *,
    step: str,
    event_outline: dict[str, Any],
    episode_context: dict[str, Any],
) -> HostNarration | None:
    try:
        return request_host_narration(
            step=step,
            event_outline=event_outline,
            episode_context=episode_context,
        )
    except Exception as exc:
        print(f"OpenRouter host narration failed for {step}: {exc}")
        return None


def _real_host_event_text(
    *,
    step: str,
    beat_context: dict[str, Any],
    episode_context: dict[str, Any],
):
    try:
        return request_host_event_text(
            step=step,
            beat_context=beat_context,
            episode_context=episode_context,
        )
    except Exception as exc:
        print(f"OpenRouter host event text failed for {step}: {exc}")
        return None


def _maybe_generate_host_narration(
    conn,
    *,
    round_number: int,
    step: str,
    payload: dict[str, Any],
    kind: str,
    title: str,
    dialogue: str,
    subtitle: str | None = None,
    inner_thought: str | None = None,
) -> None:
    if not should_use_live_llm():
        return
    host_context = build_host_episode_context(conn, round_number=round_number, current_step=step)
    narration = _real_host_narration(
        step=step,
        event_outline=_event_outline(
            kind=kind,
            title=title,
            dialogue=dialogue,
            subtitle=subtitle,
            inner_thought=inner_thought,
            payload=payload,
        ),
        episode_context=host_context,
    )
    if not narration:
        return
    payload["host_narration"] = narration.host_narration
    payload["host_llm_provider"] = live_llm_provider()
    payload["host_llm_model_id"] = narration.model_id
    if payload.get("llm_provider") in {None, "deterministic"}:
        payload["llm_provider"] = live_llm_provider()
        payload["llm_model_id"] = narration.model_id
    _add_context_digest(payload, "host", context_digest(host_context))


def _eligible_targets(conn, actor_id: str) -> list[dict[str, Any]]:
    targets = [
        row_to_dict(row)
        for row in conn.execute(
            """
            SELECT * FROM Agents
            WHERE agent_id != ? AND status = 'active' AND has_immunity = 0
            ORDER BY agent_id
            """,
            (actor_id,),
        ).fetchall()
    ]
    return targets


def _agent_row(conn, agent_id: str) -> dict[str, Any] | None:
    return row_to_dict(conn.execute("SELECT * FROM Agents WHERE agent_id = ?", (agent_id,)).fetchone())


def _add_context_digest(payload: dict[str, Any], key: str, digest: dict[str, Any]) -> None:
    current = payload.get("llm_context_digest")
    if not isinstance(current, dict):
        current = {}
    current[key] = digest
    payload["llm_context_digest"] = current


def _add_agent_action_metadata(payload: dict[str, Any], action: AgentAction) -> None:
    _add_strategy_metadata(
        payload,
        strategic_summary=action.strategic_summary or action.inner_thought,
        move_type=action.move_type,
        intended_effect=action.intended_effect,
        confidence=action.confidence,
        win_condition=action.win_condition,
        threat_assessment=action.threat_assessment,
        leverage_plan=action.leverage_plan,
        risk_control=action.risk_control,
        jury_positioning=action.jury_positioning,
        strategic_score=action.strategic_score,
        prompt_profile=action.prompt_profile,
    )
    if action.public_archetype:
        payload["public_archetype"] = action.public_archetype


def _maybe_store_action_archetype(conn, actor_id: str, round_number: int, action: AgentAction) -> None:
    if not action.public_archetype:
        return
    conn.execute(
        """
        UPDATE Agents
        SET archetype = ?,
            archetype_source = 'self_authored',
            archetype_updated_round = ?
        WHERE agent_id = ?
        """,
        (action.public_archetype, round_number, actor_id),
    )


def _add_strategy_metadata(
    payload: dict[str, Any],
    *,
    strategic_summary: str | None,
    move_type: str | None,
    intended_effect: str | None,
    confidence: float | None,
    win_condition: str | None = None,
    threat_assessment: str | None = None,
    leverage_plan: str | None = None,
    risk_control: str | None = None,
    jury_positioning: str | None = None,
    strategic_score: float | None = None,
    prompt_profile: str | None = None,
) -> None:
    if strategic_summary:
        payload["strategic_summary"] = strategic_summary
    if win_condition:
        payload["win_condition"] = win_condition
    if threat_assessment:
        payload["threat_assessment"] = threat_assessment
    if leverage_plan:
        payload["leverage_plan"] = leverage_plan
    if risk_control:
        payload["risk_control"] = risk_control
    if jury_positioning:
        payload["jury_positioning"] = jury_positioning
    if move_type:
        payload["move_type"] = move_type
    if intended_effect:
        payload["intended_effect"] = intended_effect
    if confidence is not None:
        payload["confidence"] = confidence
    if strategic_score is not None:
        payload["strategic_score"] = strategic_score
    if prompt_profile:
        payload["prompt_profile"] = prompt_profile


def _event_outline(
    *,
    kind: str,
    title: str,
    dialogue: str,
    subtitle: str | None = None,
    inner_thought: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    outline: dict[str, Any] = {
        "kind": kind,
        "title": title,
        "dialogue": dialogue,
        "subtitle": subtitle,
    }
    if inner_thought:
        outline["inner_thought"] = inner_thought
    payload = payload or {}
    if "final_tally" in payload:
        outline["final_tally"] = payload["final_tally"]
    if "eliminated_id" in payload:
        outline["eliminated_id"] = payload["eliminated_id"]
    return outline


def _maybe_insert_message(conn, round_number: int, turn_index: int, event: dict[str, Any]) -> None:
    if event["kind"] not in {"conversation", "confessional", "tribal_answer", "exit_confessional", "finale_pitch"}:
        return
    sender_id = event["actor_ids"][0] if event["actor_ids"] else "system"
    payload = event.get("payload") or {}
    receiver_ids = (
        payload.get("participant_ids")
        if payload.get("privacy") == "participants_only" and isinstance(payload.get("participant_ids"), list)
        else event["target_ids"]
    )
    is_public = event["kind"] in {"conversation", "tribal_answer", "finale_pitch"} and payload.get("privacy") != "participants_only"
    conn.execute(
        """
        INSERT INTO Messages (
            round, turn_index, sender_id, receiver_ids, is_public,
            inner_thought, content, trust_telemetry
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
            round_number,
            turn_index,
            sender_id,
            json_dumps(receiver_ids),
            1 if is_public else 0,
            event.get("inner_thought"),
            event["dialogue"],
            json_dumps(event.get("trust_telemetry", {})),
        ),
    )


def _next_sequence(conn, round_number: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(sequence), -1) + 1 AS next_sequence FROM StoryEvents WHERE round = ?",
        (round_number,),
    ).fetchone()
    return int(row["next_sequence"])


def _total_votes_for_round(conn, round_number: int) -> int:
    vote_count = conn.execute(
        "SELECT COUNT(*) AS count FROM Votes WHERE round = ?",
        (round_number,),
    ).fetchone()["count"]
    eligible_now = len(_round_voter_ids(conn, round_number))
    spoiled = conn.execute(
        "SELECT COUNT(*) AS n FROM SpoiledVotes WHERE round = ? AND ballot = 'vote'", (round_number,)
    ).fetchone()["n"]
    return max(int(vote_count), eligible_now - int(spoiled)) if eligible_now else int(vote_count)


def _record_spoiled_vote(conn, round_number: int, voter_id: str, ballot: str, reason: str) -> None:
    conn.execute(
        "INSERT INTO SpoiledVotes (round, voter_id, ballot, reason) VALUES (?, ?, ?, ?)",
        (round_number, voter_id, ballot, reason[:300]),
    )


def _next_step(conn, state: dict[str, Any], step: str) -> str:
    phase_steps = _phase_steps(conn, state)
    try:
        index = phase_steps.index(step)
    except ValueError as exc:
        raise RuntimeError(f"Unknown phase step: {step}") from exc
    if index + 1 >= len(phase_steps):
        return "complete"
    return phase_steps[index + 1]


def _agent_name(conn, agent_id: str) -> str:
    if agent_id == "host":
        return "Host"
    row = conn.execute("SELECT pseudonym FROM Agents WHERE agent_id = ?", (agent_id,)).fetchone()
    return row["pseudonym"] if row else agent_id


def _revealed_vote_counts(conn, round_number: int) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT target_id, COUNT(*) AS count
        FROM Votes
        WHERE round = ? AND revealed = 1
        GROUP BY target_id
        ORDER BY target_id
        """,
        (round_number,),
    ).fetchall()
    return {row["target_id"]: row["count"] for row in rows}


def _format_tally(conn, tally: dict[str, int]) -> str:
    if not tally:
        return "No votes revealed."
    parts = [f"{_agent_name(conn, agent_id)}: {count}" for agent_id, count in tally.items()]
    return "Tally: " + ", ".join(parts)


def _current_eliminated_agent_id(conn=None) -> str | None:
    should_close = conn is None
    conn = conn or get_db_connection()
    try:
        row = conn.execute(
            """
            SELECT agent_id FROM Agents
            WHERE status = 'eliminated'
            ORDER BY elimination_round DESC, agent_id
            LIMIT 1
            """
        ).fetchone()
        return row["agent_id"] if row else None
    finally:
        if should_close:
            conn.close()


def _round_history(
    conn,
    challenge_results: list[dict[str, Any]],
    votes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rounds = sorted(
        {
            row["round"]
            for row in conn.execute(
                "SELECT DISTINCT round FROM StoryEvents ORDER BY round"
            ).fetchall()
        }
        | {result["round"] for result in challenge_results}
        | {vote["round"] for vote in votes}
    )
    results_by_round = {result["round"]: result for result in challenge_results}
    votes_by_round: dict[int, list[dict[str, Any]]] = {}
    for vote in votes:
        votes_by_round.setdefault(vote["round"], []).append(vote)
    history: list[dict[str, Any]] = []
    for round_number in rounds:
        eliminated = conn.execute(
            """
            SELECT agent_id, pseudonym
            FROM Agents
            WHERE elimination_round = ?
            ORDER BY agent_id
            LIMIT 1
            """,
            (round_number,),
        ).fetchone()
        event_count = conn.execute(
            "SELECT COUNT(*) AS count FROM StoryEvents WHERE round = ?",
            (round_number,),
        ).fetchone()["count"]
        history.append(
            {
                "round": round_number,
                "event_count": event_count,
                "challenge_result": results_by_round.get(round_number),
                "eliminated_id": eliminated["agent_id"] if eliminated else None,
                "eliminated_name": eliminated["pseudonym"] if eliminated else None,
                "votes": votes_by_round.get(round_number, []),
            }
        )
    return history


def _estimated_live_calls_for_step(conn, game: dict[str, Any], step: str) -> int:
    if not should_use_live_llm():
        return 0
    if step.startswith("group_pre_challenge_") or step.startswith("group_post_challenge_"):
        active_count = len(_active_agent_rows(conn))
        return max(0, min(5, active_count) * 2)
    if step == "camp_pre_challenge_read":
        active_count = len(_active_agent_rows(conn))
        return 1 + max(0, min(5, active_count) * 4)
    if step == "camp_strategy":
        active_count = len(_active_agent_rows(conn))
        return 1 + max(0, min(5, active_count) * 6)
    if step in {
        "camp_pre_challenge_read",
        "camp_pre_challenge_confessional",
        "challenge_intro",
        "challenge_result",
        "challenge_solver_spotlight",
        "camp_strategy",
        "memory_update",
        "jury_questions",
        "winner_declared",
    }:
        return 1
    if step == "challenge_attempts":
        # one call per player for the whole ladder and the tiebreak guess
        return len(_active_agent_rows(conn))
    if step == "challenge_gift":
        return 1 if _idol_gift_round(conn, int(game["current_round"])) else 0
    if step in {"round_plan", "camp_followup"}:
        return len(_active_agent_rows(conn))
    if step == "memory_update":
        # every player rewrites its notebook, plus the host line
        return len(_active_agent_rows(conn)) + 1
    if step == "camp_talks":
        # one partner pick per player, then at most one talk per pick with four lines
        active_count = len(_active_agent_rows(conn))
        return active_count * PRIVATE_TALKS_PER_ROUND * 5
    if step in GENERATED_BEAT_INTENTS:
        return 1 if GENERATED_BEAT_INTENTS[step]["response_kind"] == "host" else 2
    if step.startswith("vote_booth_"):
        return 1
    if step in {"elimination", "exit_confessional"}:
        return 1
    if step.startswith("finale_pitch_") or step.startswith("jury_vote_"):
        return 1
    return 0


_STEP_RESPONSE_KIND = {
    "challenge_attempts": "challenge",
    "challenge_gift": "conversation",
    "camp_talks": "conversation",
    "round_plan": "conversation",
    "camp_followup": "conversation",
    "vote_booth": "vote",
    "camp_strategy": "conversation",
    "camp_pre_challenge_read": "conversation",
    "camp_pre_challenge_confessional": "confessional",
    "memory_update": "confessional",
    "jury_questions": "jury_vote",
    "winner_declared": "host",
    "challenge_intro": "host",
    "challenge_result": "host",
    "challenge_solver_spotlight": "host",
    "elimination": "host",
    "exit_confessional": "confessional",
    "finale_pitch": "finale_pitch",
    "jury_vote": "jury_vote",
}


def _step_response_kind(step: str) -> str:
    if step in _STEP_RESPONSE_KIND:
        return _STEP_RESPONSE_KIND[step]
    for prefix, kind in (
        ("vote_booth_", "vote"),
        ("finale_pitch_", "finale_pitch"),
        ("jury_vote_", "jury_vote"),
        ("group_pre_challenge_", "conversation"),
        ("group_post_challenge_", "conversation"),
    ):
        if step.startswith(prefix):
            return _STEP_RESPONSE_KIND[prefix.rstrip("_")]
    return "conversation"


def _estimated_step_cost_cents(
    conn, game: dict[str, Any], step: str, pricing_table: dict[str, dict[str, float]]
) -> float:
    """Estimated USD cost of one live step, in cents, from live roster model ids."""
    if not should_use_live_llm():
        return 0.0
    model_ids = sorted(
        {
            row["model_id"]
            for row in _active_agent_rows(conn)
            if row.get("model_id")
        }
    )
    if not model_ids:
        return 0.0
    kind = _step_response_kind(step)
    if step == "challenge_attempts":
        # every active model attempts the puzzle
        return estimate_step_cost_usd(model_ids, kind, table=pricing_table) * 100.0
    if kind == "host":
        # host steps use the operator model only; approximate with the roster median
        host_model = model_ids[len(model_ids) // 2]
        return estimate_step_cost_usd([host_model], kind, table=pricing_table) * 100.0
    calls = _estimated_live_calls_for_step(conn, game, step)
    if calls <= 0:
        return 0.0
    # Price each call at the model that makes it. Pricing every call at the
    # priciest seat (as before 2026-11-24) overstated a cheap-roster season about
    # four times, because one seat (Grok) costs 20-200x the others, and the cap
    # stopped a season that had spent about a quarter of its budget.
    for prefix in ("vote_booth_", "finale_pitch_", "jury_vote_"):
        if step.startswith(prefix):
            row = _agent_row(conn, step[len(prefix):])
            if row and row.get("model_id"):
                return estimate_step_cost_usd([row["model_id"]], kind, table=pricing_table) * calls * 100.0
    # every active player takes a turn: the average seat cost, per call
    seat_models = [row["model_id"] for row in _active_agent_rows(conn) if row.get("model_id")]
    mean_cost = sum(estimate_step_cost_usd([m], kind, table=pricing_table) for m in seat_models) / len(seat_models)
    return mean_cost * calls * 100.0


def _episode_title(game: dict[str, Any], round_number: int) -> str:
    if game.get("winner"):
        return f"Round {round_number}: Winner Declared"
    if game.get("phase") == "finale":
        return f"Round {round_number}: Jury Finale"
    return f"Round {round_number}: Challenge to Tribal Conference"


def main() -> None:
    parser = argparse.ArgumentParser(description="Advance the LLM Survivor turn controller.")
    parser.add_argument("--reset", action="store_true", help="Reset the local demo database.")
    parser.add_argument("--auto-run", type=int, default=0, help="Run up to N turns.")
    args = parser.parse_args()

    if args.reset:
        seed_demo(reset=True)
    else:
        ensure_database()

    if args.auto_run:
        result = auto_run(args.auto_run)
        print(f"advanced={len(result['turns'])} events={len(result['story_events'])}")
    else:
        result = advance_turn()
        turn = result["turn"]
        print(f"turn={turn['turn_index'] if turn else 'complete'} events={len(result['story_events'])}")


if __name__ == "__main__":
    main()
