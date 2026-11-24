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

    def test_run_always_allowed_no_daily_cap(self) -> None:
        for _ in range(10):
            gate.record_public_run({"caps": {"estimated_cost_cents": 100.0}, "roster": "x", "finished": True})
        ok, reason, _ = gate.check_public_run_allowed()
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_run_cap_is_flat_per_run(self) -> None:
        gate.record_public_run({"caps": {"estimated_cost_cents": 250.0}, "roster": "x", "finished": True})
        caps = gate.public_run_caps()
        self.assertEqual(caps["max_cost_cents"], 250.0)

    def test_roster_includes_grok(self) -> None:
        from backend.model_rosters import CHEAP_RECENT_OPENROUTER_MODELS

        models = [m["model_id"] for m in CHEAP_RECENT_OPENROUTER_MODELS if m["status"] == "cheap_recent"]
        self.assertIn("x-ai/grok-4.7", models)
        self.assertEqual(len(models), 9)


if __name__ == "__main__":
    unittest.main()
