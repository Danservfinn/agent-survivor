"""Tests for the public live-run budget gate."""

import unittest
from pathlib import Path

import backend.public_gate as gate


class PublicGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.orig_path = gate._LEDGER_PATH
        gate._LEDGER_PATH = Path(self.orig_path).parent / "public_live_ledger_test.json"
        if gate._LEDGER_PATH.exists():
            gate._LEDGER_PATH.unlink()

    def tearDown(self) -> None:
        if gate._LEDGER_PATH.exists():
            gate._LEDGER_PATH.unlink()
        gate._LEDGER_PATH = self.orig_path

    def test_run_cap_is_flat_per_run(self) -> None:
        gate.record_public_run({"caps": {"estimated_cost_cents": 500.0}, "roster": "x", "finished": True})
        caps = gate.public_run_caps()
        self.assertEqual(caps["max_cost_cents"], 1500.0)
        self.assertEqual(caps["roster"], "lab_mix_openrouter")

    def test_cheap_roster_has_nine_cheap_seats(self) -> None:
        from backend.model_rosters import CHEAP_RECENT_OPENROUTER_MODELS

        models = [m["model_id"] for m in CHEAP_RECENT_OPENROUTER_MODELS if m["status"] == "cheap_recent"]
        self.assertNotIn("x-ai/grok-4.7", models)
        self.assertEqual(len(models), 9)


    def test_public_roster_is_one_model_per_lab(self) -> None:
        from backend.model_rosters import LAB_MIX_OPENROUTER_MODELS, agents_for_roster

        labs = [m["model_id"].split("/", 1)[0] for m in LAB_MIX_OPENROUTER_MODELS]
        self.assertEqual(len(labs), 9)
        self.assertEqual(len(set(labs)), 9)
        self.assertNotIn("z-ai/glm-4.5-air", [m["model_id"] for m in LAB_MIX_OPENROUTER_MODELS])
        agents = agents_for_roster("lab_mix_openrouter")
        self.assertEqual([a["model_id"] for a in agents], [m["model_id"] for m in LAB_MIX_OPENROUTER_MODELS])

    def test_public_roster_season_estimate_fits_the_cap(self) -> None:
        """A full season is about 280 player calls; even with every call priced at
        the dearest seat's challenge rate halved, the estimate stays under 60% of the cap."""
        from backend.live_cost import estimate_step_cost_usd, get_pricing_table
        from backend.model_rosters import LAB_MIX_OPENROUTER_MODELS

        table = get_pricing_table()
        ids = [m["model_id"] for m in LAB_MIX_OPENROUTER_MODELS]
        if not all(i in table for i in ids):
            self.skipTest("pricing catalog not cached on this machine")
        mean_call = sum(estimate_step_cost_usd([i], "vote", table=table) for i in ids) / len(ids)
        self.assertLess(mean_call * 280 * 100, gate.PUBLIC_RUN_MAX_COST_CENTS * 0.6)


if __name__ == "__main__":
    unittest.main()
