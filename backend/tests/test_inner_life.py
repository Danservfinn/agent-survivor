"""A player's inner life: its train of thought within a round, its read of
what others expect of it, the contradictions it faces at its notebook, and a
private ledger of its lies. None of it may open a secret ballot."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from backend import inner_life, openrouter_client
from backend import turn_controller as tc
from backend.llm_context import build_agent_episode_context
from backend.openrouter_client import AgentAction
from backend.tests.test_model_failures import _Base


def _action(**fields) -> AgentAction:
    base = {"dialogue": "line", "inner_thought": "", "model_id": "m"}
    return AgentAction(**(base | fields))


class TrainOfThoughtTest(_Base):
    def test_a_call_records_its_summary_and_later_steps_read_it_back(self) -> None:
        me, other, *_ = self.players
        self._live(lambda **kwargs: _action(strategic_summary="Pull Fir away from Cedar tonight."))
        tc._real_agent_action(
            self.conn, actor_id=me, step="camp_talks", scene_context="x", response_kind="talk_choice", episode_context={}
        )
        round_number = tc._current_round(self.conn)
        later = inner_life.train_of_thought(self.conn, me, round_number, "camp_followup")
        self.assertEqual(later, [{"step": "camp_talks", "thought": "Pull Fir away from Cedar tonight."}])
        # the same step does not see itself (equal one-at-a-time and in parallel)
        self.assertEqual(inner_life.train_of_thought(self.conn, me, round_number, "camp_talks"), [])
        # private, and only this round
        self.assertEqual(inner_life.train_of_thought(self.conn, other, round_number, "camp_followup"), [])
        self.assertEqual(inner_life.train_of_thought(self.conn, me, round_number + 1, "camp_followup"), [])

    def test_the_thread_reaches_the_prompt(self) -> None:
        me = self.players[0]
        self.conn.execute(
            "INSERT INTO RoundThoughts (round, agent_id, step, text) VALUES (1, ?, 'camp_talks', 'Ask Ash for her vote.')",
            (me,),
        )
        context = build_agent_episode_context(self.conn, actor_id=me, round_number=1, current_step="vote_booth_x")
        prompt = openrouter_client._agent_prompt_v3(
            actor=dict(self.conn.execute("SELECT * FROM Agents WHERE agent_id = ?", (me,)).fetchone()),
            step="vote", scene_context="Vote.", episode_context=context, target_lines="", response_kind="vote",
            strategic_priorities="",
        )
        self.assertIn("Your train of thought this round", prompt)
        self.assertIn("Ask Ash for her vote.", prompt)
        self.assertLess(prompt.index("Your train of thought"), prompt.index("Game record visible to you"))


class LieLedgerTest(_Base):
    def _lie(self, liar: str, listeners, goal: str | None, truth: str = "lie", round_number: int = 1) -> None:
        inner_life.record_lie(
            self.conn, round_number, liar, "camp_talk_0",
            _action(dialogue="Cedar is coming for you.", truth=truth, lie_purpose="turn Fir on Cedar", lie_goal_vote=goal),
            listeners,
        )

    def test_a_lie_is_shown_back_through_public_facts_only(self) -> None:
        liar, listener, goal, bystander, *_ = self.players
        self._lie(liar, [liar, listener], goal)
        self.conn.execute("INSERT INTO Votes (round, turn_index, voter_id, target_id, revealed) VALUES (1, 0, ?, ?, 1)", (listener, goal))
        self.conn.execute("INSERT INTO Votes (round, turn_index, voter_id, target_id, revealed) VALUES (1, 0, ?, ?, 1)", (bystander, goal))
        self.conn.execute("UPDATE Agents SET elimination_round = 1 WHERE agent_id = ?", (goal,))
        names = inner_life._names(self.conn)
        [entry] = inner_life.own_lies(self.conn, liar, 2)
        self.assertEqual(entry["told_to"], [names[listener]])  # the liar is not its own listener
        self.assertEqual(entry["you_wanted_out"], names[goal])
        self.assertEqual(entry["votes_cast_for_them"], 2)  # the tally, not who cast them
        self.assertTrue(entry["they_went_home"])
        self.assertNotIn("landed", json.dumps(entry))
        # only finished rounds; and the report alone can see the listener acted on it
        self.assertEqual(inner_life.own_lies(self.conn, liar, 1), [])
        row = dict(self.conn.execute("SELECT * FROM Lies").fetchone())
        self.assertTrue(inner_life.lie_landed(self.conn, row))

    def test_a_lie_did_not_land_when_only_bystanders_followed_it(self) -> None:
        liar, listener, goal, bystander, *_ = self.players
        self._lie(liar, [listener], goal)
        self.conn.execute("INSERT INTO Votes (round, turn_index, voter_id, target_id, revealed) VALUES (1, 0, ?, ?, 1)", (bystander, goal))
        self.assertFalse(inner_life.lie_landed(self.conn, dict(self.conn.execute("SELECT * FROM Lies").fetchone())))

    def test_true_lines_are_kept_for_the_report_but_not_shown_as_lies(self) -> None:
        liar, listener, goal, *_ = self.players
        self._lie(liar, [listener], goal, truth="true")
        row = self.conn.execute("SELECT truth, goal_vote, purpose FROM Lies").fetchone()
        self.assertEqual((row["truth"], row["goal_vote"], row["purpose"]), ("true", None, ""))
        self.assertEqual(inner_life.own_lies(self.conn, liar, 2), [])

    def test_a_talk_records_each_speakers_lie_with_the_talk_as_listeners(self) -> None:
        a, b, goal, *_ = self.players
        replies = iter([
            _action(dialogue="I'm with you.", truth="lie", lie_goal_vote=goal, strategic_summary="s1"),
            _action(dialogue="Same.", truth="true", strategic_summary="s2"),
            _action(dialogue="Good.", truth="half", lie_goal_vote=goal, strategic_summary="s3"),
            _action(dialogue="Done.", strategic_summary="s4"),
        ])
        self._live(lambda **kwargs: next(replies))
        tc._group_speaker_lines(self.conn, 1, "camp_talk_0", [a, b], [], "post_challenge", talk_asks={})
        rows = self.conn.execute("SELECT agent_id, truth, goal_vote, listeners FROM Lies ORDER BY id").fetchall()
        self.assertEqual([(r["agent_id"], r["truth"]) for r in rows], [(a, "lie"), (b, "true"), (a, "half")])
        self.assertEqual(json.loads(rows[0]["listeners"]), [b])
        self.assertEqual(rows[0]["goal_vote"], goal)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM RoundThoughts").fetchone()[0], 4)


class ExpectationTest(_Base):
    def test_the_read_is_a_total_and_reveals_no_guess(self) -> None:
        me, a, b, x, y, *_ = self.players
        for voter, target, predicted, expected in (
            (me, x, {}, {a: x, b: y}),
            (a, y, {me: x}, {}),  # a expected me on x: my guess right
            (b, x, {me: x}, {}),  # b expected me on x, I guessed y: wrong
        ):
            self.conn.execute(
                "INSERT INTO Votes (round, turn_index, voter_id, target_id, revealed, predicted_votes, expected_of_me) "
                "VALUES (1, 0, ?, ?, 1, ?, ?)",
                (voter, target, json.dumps(predicted), json.dumps(expected)),
            )
        self.assertEqual(inner_life.expectation_read(self.conn, me), {"guesses_scored": 2, "right": 1, "rate": 0.5})
        context = build_agent_episode_context(self.conn, actor_id=me, round_number=2, current_step="x")
        self.assertEqual(context["how_well_you_read_what_others_expected_of_you"]["right"], 1)

    def test_a_vote_stores_the_voters_read(self) -> None:
        me, a, b, *_ = self.players
        names = inner_life._names(self.conn)
        self._live(lambda **kwargs: _action(target_id=b, expected_of_me={names[a]: names[b]}))
        decision = tc._resolve_vote_decision(self.conn, me, "vote_booth_x")
        self.assertEqual(decision.target_id, b)
        self.assertEqual(decision.expected_of_me, {a: b})


class ContradictionTest(_Base):
    def test_a_vote_against_a_trusted_player_and_a_broken_promise_are_named(self) -> None:
        me, friend, listener, other, *_ = self.players
        self.conn.execute("INSERT INTO Feelings (round, agent_id, about, feeling) VALUES (1, ?, ?, 'trust')", (me, friend))
        self.conn.execute(
            "INSERT INTO Promises (round, promiser_id, listener_id, target_id, discussion_step) VALUES (2, ?, ?, ?, 'camp_talk_0')",
            (me, listener, other),
        )
        self.conn.execute("INSERT INTO Votes (round, turn_index, voter_id, target_id, revealed) VALUES (2, 0, ?, ?, 1)", (me, friend))
        clashes = inner_life.contradictions(self.conn, me, 2)
        self.assertEqual(len(clashes), 2)
        self.assertIn("trust", clashes[0])
        context = build_agent_episode_context(self.conn, actor_id=me, round_number=2, current_step="memory_update")
        self.assertEqual(context["your_contradictions_this_round"], clashes)
        # only at the notebook
        self.assertNotIn("your_contradictions_this_round", build_agent_episode_context(self.conn, actor_id=me, round_number=2, current_step="x"))

    def test_a_kept_promise_and_a_vote_against_a_rival_are_not(self) -> None:
        me, rival, listener, *_ = self.players
        self.conn.execute("INSERT INTO Feelings (round, agent_id, about, feeling) VALUES (1, ?, ?, 'resentment')", (me, rival))
        self.conn.execute(
            "INSERT INTO Promises (round, promiser_id, listener_id, target_id, discussion_step) VALUES (2, ?, ?, ?, 'camp_talk_0')",
            (me, listener, rival),
        )
        self.conn.execute("INSERT INTO Votes (round, turn_index, voter_id, target_id, revealed) VALUES (2, 0, ?, ?, 1)", (me, rival))
        self.assertEqual(inner_life.contradictions(self.conn, me, 2), [])


class SwitchTest(_Base):
    @patch.dict(os.environ, {"SURVIVOR_INNER_LIFE": "0"})
    def test_off_means_nothing_recorded_shown_or_asked(self) -> None:
        me, listener, *_ = self.players
        inner_life.record_thought(self.conn, 1, me, "camp_talks", _action(strategic_summary="s"))
        inner_life.record_lie(self.conn, 1, me, "camp_talks", _action(truth="lie"), [listener])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM RoundThoughts").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM Lies").fetchone()[0], 0)
        self.assertEqual(inner_life.context_entries(self.conn, me, 1, "memory_update"), {})
        context = build_agent_episode_context(self.conn, actor_id=me, round_number=1, current_step="x")
        prompt = openrouter_client._agent_prompt_v3(
            actor=dict(self.conn.execute("SELECT * FROM Agents WHERE agent_id = ?", (me,)).fetchone()),
            step="talk", scene_context="Talk.", episode_context=context, target_lines="",
            response_kind="group_conversation", strategic_priorities="",
        )
        for field in ("truth", "lie_purpose", "lie_goal_vote"):
            self.assertNotIn(f'"{field}"', prompt)

    def test_on_asks_for_the_ledger_fields(self) -> None:
        me = self.players[0]
        context = build_agent_episode_context(self.conn, actor_id=me, round_number=1, current_step="x")
        prompt = openrouter_client._agent_prompt_v3(
            actor=dict(self.conn.execute("SELECT * FROM Agents WHERE agent_id = ?", (me,)).fetchone()),
            step="vote", scene_context="Vote.", episode_context=context, target_lines="", response_kind="vote",
            strategic_priorities="",
        )
        self.assertIn('"expected_of_me"', prompt)


if __name__ == "__main__":
    unittest.main()
