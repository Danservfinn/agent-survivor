"""The live cost estimate prices each call at the model that makes it."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import database
from backend import turn_controller as tc
from backend.live_cost import estimate_step_cost_usd


class StepCostEstimateTest(unittest.TestCase):
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
        self.addCleanup(self.conn.close)
        self.rows = [dict(row) for row in self.conn.execute("SELECT agent_id, model_id FROM Agents ORDER BY agent_id")]
        # one seat costs 100x the others, as Grok does on the cheap roster
        self.pricey = self.rows[0]
        self.table = {
            row["model_id"]: {"prompt_per_m": 10.0, "completion_per_m": 30.0}
            if row is self.pricey
            else {"prompt_per_m": 0.1, "completion_per_m": 0.3}
            for row in self.rows
        }
        self.game = {"current_round": 1, "phase_step": "camp_talks"}

    def _estimate(self, step: str) -> float:
        with patch.object(tc, "should_use_live_llm", return_value=True):
            return tc._estimated_step_cost_cents(self.conn, self.game, step, self.table)

    def test_everyone_step_uses_the_average_seat_not_the_priciest(self) -> None:
        per_seat = [estimate_step_cost_usd([row["model_id"]], "conversation", table=self.table) for row in self.rows]
        with patch.object(tc, "should_use_live_llm", return_value=True):
            calls = tc._estimated_live_calls_for_step(self.conn, self.game, "camp_talks")
        expected = sum(per_seat) / len(per_seat) * calls * 100.0
        self.assertAlmostEqual(self._estimate("camp_talks"), expected, places=6)
        self.assertLess(self._estimate("camp_talks"), max(per_seat) * calls * 100.0 / 5)

    def test_a_vote_is_priced_at_the_voters_model(self) -> None:
        cheap = self.rows[1]
        cheap_vote = self._estimate(f"vote_booth_{cheap['agent_id']}")
        pricey_vote = self._estimate(f"vote_booth_{self.pricey['agent_id']}")
        self.assertAlmostEqual(cheap_vote, estimate_step_cost_usd([cheap["model_id"]], "vote", table=self.table) * 100.0)
        self.assertGreater(pricey_vote, cheap_vote * 50)


if __name__ == "__main__":
    unittest.main()
