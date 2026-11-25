"""The game, the rules text players read, and the report agree (fixes asked
for by the Methodology page, 2026-11-25)."""

from __future__ import annotations

import unittest

from backend import openrouter_client
from backend import turn_controller as tc
from backend.llm_context import GAME_RULES
from backend.tests.test_model_failures import _Base


class RulesTextTest(unittest.TestCase):
    def test_the_ladder_rule_matches_the_engine(self) -> None:
        self.assertIn("rises when any player solves them all", GAME_RULES[1])
        self.assertNotIn("two or more players solve", GAME_RULES[1])


class SelfTargetPromiseTest(_Base):
    def test_a_promise_to_vote_for_the_listener_is_no_promise(self) -> None:
        a, b, c, *_ = self.players
        tc._record_promise(self.conn, 1, a, [a, b], b, "camp_talk_0")  # "I'll vote for you" to b
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM Promises").fetchone()[0], 0)
        tc._record_promise(self.conn, 1, a, [a, b, c], b, "camp_talk_1")  # c hears a will vote b: a deal
        rows = self.conn.execute("SELECT listener_id, target_id FROM Promises").fetchall()
        self.assertEqual([(r["listener_id"], r["target_id"]) for r in rows], [(c, b)])

    def test_old_self_target_rows_are_not_scored(self) -> None:
        a, b, *_ = self.players
        self.conn.execute(
            "INSERT INTO Promises (round, promiser_id, listener_id, target_id, discussion_step) VALUES (1, ?, ?, ?, 'x')", (a, b, b)
        )
        self.conn.execute("INSERT INTO Votes (round, turn_index, voter_id, target_id, revealed) VALUES (1, 0, ?, ?, 1)", (a, b))
        self.assertEqual(tc.promise_record(self.conn, a), [])


class SeedingTest(_Base):
    def test_no_seat_starts_immune(self) -> None:
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM Agents WHERE has_immunity = 1").fetchone()[0], 0)


class DeadCodeTest(unittest.TestCase):
    def test_the_unscheduled_archetype_step_is_gone(self) -> None:
        self.assertFalse(hasattr(openrouter_client, "request_agent_archetype"))
        self.assertFalse(hasattr(tc, "_archetype_setup_event"))


if __name__ == "__main__":
    unittest.main()
