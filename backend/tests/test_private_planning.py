"""Private round plans, vote predictions, the notebook, and follow-up messages."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import benchmark_scoring, database
from backend import turn_controller as tc
from backend.llm_context import build_agent_episode_context
from backend.openrouter_client import AgentAction

ROUND = 1


class _Db(unittest.TestCase):
    def setUp(self) -> None:
        env = patch.dict(
            os.environ,
            {"OPENROUTER_API_KEY": "", "LLM_PROVIDER": "openrouter", "LLM_SURVIVOR_FAST_BENCHMARK": "1"},
            clear=False,
        )
        env.start()
        self.addCleanup(env.stop)
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        original = database.DATABASE_PATH
        database.DATABASE_PATH = Path(temp_dir.name) / "survivor.db"
        self.addCleanup(setattr, database, "DATABASE_PATH", original)
        database.seed_demo(reset=True, roster_preset="cheap_recent_openrouter")
        self.conn = database.get_db_connection()
        self.conn.execute("PRAGMA foreign_keys = OFF")
        self.addCleanup(self.conn.close)
        self.players = [row["agent_id"] for row in self.conn.execute("SELECT agent_id FROM Agents ORDER BY agent_id")]

    def _context(self, agent_id: str, round_number: int = ROUND) -> dict:
        return build_agent_episode_context(self.conn, actor_id=agent_id, round_number=round_number, current_step="x")

    def _live(self):
        return (
            patch.object(tc, "should_use_live_llm", return_value=True),
            patch.object(tc, "live_llm_provider", return_value="openrouter"),
            patch.object(tc, "build_agent_episode_context", return_value={}),
        )


class RoundPlanTest(_Db):
    def test_round_opens_with_the_plan_step(self) -> None:
        state = {"phase": "round", "phase_step": "round_plan", "current_round": ROUND}
        steps = tc._phase_steps(self.conn, state)
        self.assertEqual(steps[0], "round_plan")
        self.assertEqual(database.round_start_step(), "round_plan")
        self.assertLess(steps.index("camp_talks"), steps.index("camp_followup"))
        self.assertLess(steps.index("camp_followup"), steps.index("tribal_vote_call"))

    def test_plan_is_seen_only_by_its_author_and_only_that_round(self) -> None:
        a, b, *_ = self.players
        live = self._live()
        written = {"threats": f"threat-from-{a}", "allies": "x", "talk_plan": "y", "expected_out": b, "confidence": 0.7}

        def write(*, actor, **kwargs):
            self.assertEqual(kwargs.get("reasoning_effort"), tc.ROUND_PLAN_EFFORT)
            return dict(written, threats=f"threat-from-{actor['agent_id']}")

        with live[0], live[1], live[2], patch.object(tc, "request_private_writing", side_effect=write):
            self.assertEqual(tc._round_plans(self.conn, ROUND), len(self.players))
        mine = self._context(a)["your_plan_this_round"]
        self.assertEqual(mine["threats"], f"threat-from-{a}")
        self.assertNotIn(f"threat-from-{a}", json.dumps(self._context(b)))
        self.assertIsNone(self._context(a, ROUND + 1)["your_plan_this_round"])


class PredictionTest(_Db):
    def test_vote_predictions_are_stored_and_scored(self) -> None:
        a, b, c, d, *rest = self.players
        alias = {row["agent_id"]: row["pseudonym"] for row in self.conn.execute("SELECT agent_id, pseudonym FROM Agents")}

        def action(**kwargs):
            return AgentAction(
                dialogue="My vote is set.",
                inner_thought="",
                model_id="m",
                target_id=b,
                predicted_eliminated=alias[b],  # by in-game name; the engine resolves it
                predicted_votes={b: a, c: b, d: alias[c]},
                prediction_confidence=0.8,
            )

        live = self._live()
        with live[0], live[1], live[2], patch.object(tc, "request_agent_action", side_effect=action):
            decision = tc._resolve_vote_decision(self.conn, a, f"vote_booth_{a}")
        tc._insert_vote(self.conn, ROUND, 0, a, decision.target_id, decision)
        actual = {b: a, c: b, d: b}  # a called b and c right, d wrong
        for voter, target in actual.items():
            tc._insert_vote(self.conn, ROUND, 0, voter, target)
        self.conn.execute("UPDATE Votes SET revealed = 1")
        self.conn.execute("UPDATE Agents SET status = 'eliminated', elimination_round = ? WHERE agent_id = ?", (ROUND, b))

        record = tc.prediction_record(self.conn, a)
        self.assertEqual(record["rounds"][0]["votes_called_right"], 2)
        self.assertEqual(record["rounds"][0]["votes_to_call"], 3)
        self.assertTrue(record["rounds"][0]["out_correct"])
        self.assertEqual(record["vote_call_accuracy"], round(2 / 3, 3))
        self.assertEqual(self._context(a)["your_predictions"], record)

        votes = [dict(row) for row in self.conn.execute("SELECT * FROM Votes")]
        self.assertEqual(benchmark_scoring._prediction_accuracy(votes, a), round(2 / 3, 4))
        self.assertIsNone(benchmark_scoring._prediction_accuracy(votes, c))


class NotebookTest(_Db):
    def test_notebook_replaces_old_memory_and_is_capped(self) -> None:
        a = self.players[0]
        self.conn.execute("UPDATE Agents SET confessional_memory = 'OLD MEMORY' WHERE agent_id = ?", (a,))
        live = self._live()
        long_note = "N" * 3000
        with live[0], live[1], live[2], patch.object(tc, "request_private_writing", return_value={"notebook": long_note}):
            tc._write_notebook(self.conn, ROUND, dict(self.conn.execute("SELECT * FROM Agents WHERE agent_id = ?", (a,)).fetchone()), "Ash", 8)
        memory = self.conn.execute("SELECT confessional_memory FROM Agents WHERE agent_id = ?", (a,)).fetchone()[0]
        self.assertEqual(memory, "N" * tc.NOTEBOOK_MAX_CHARS)

    def test_without_a_model_the_memory_still_stays_within_the_cap(self) -> None:
        a = self.players[0]
        self.conn.execute("UPDATE Agents SET confessional_memory = ? WHERE agent_id = ?", ("x" * 5000, a))
        agent = dict(self.conn.execute("SELECT * FROM Agents WHERE agent_id = ?", (a,)).fetchone())
        tc._write_notebook(self.conn, ROUND, agent, "Ash", 8)
        memory = self.conn.execute("SELECT confessional_memory FROM Agents WHERE agent_id = ?", (a,)).fetchone()[0]
        self.assertLessEqual(len(memory), tc.NOTEBOOK_MAX_CHARS)
        self.assertTrue(memory.endswith("8 players remain."))


class FollowupTest(_Db):
    def test_followup_reaches_only_its_recipient_and_a_pass_sends_nothing(self) -> None:
        a, b, c, *_ = self.players

        def action(**kwargs):
            if kwargs["actor"]["agent_id"] == a:
                return AgentAction(dialogue="I saw you nod at the others.", inner_thought="", model_id="m", target_id=b)
            return AgentAction(dialogue="pass", inner_thought="", model_id="m", target_id=None)

        live = self._live()
        with live[0], live[1], live[2], patch.object(tc, "request_agent_action", side_effect=action):
            events = tc._followup_events(self.conn, ROUND, 1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["participant_ids"], [a, b])
        self.assertIn("I saw you nod", json.dumps(self._context(b)))
        self.assertNotIn("I saw you nod", json.dumps(self._context(c)))


if __name__ == "__main__":
    unittest.main()
