"""Tests for live season runner guards and 8-seat rosters."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.live_season import ALLOWED_ROSTERS, run_live_season  # noqa: E402
from backend.model_rosters import agents_for_roster  # noqa: E402


class RosterTest(unittest.TestCase):
    def test_cheap_roster_is_9_seats_with_grok(self) -> None:
        agents = agents_for_roster("cheap_recent_openrouter")
        self.assertEqual(len(agents), 9)
        self.assertIn("x-ai/grok-4.7", {a["model_id"] for a in agents})

    def test_cheap_roster_models_are_live(self) -> None:
        agents = agents_for_roster("cheap_recent_openrouter")
        for agent in agents:
            self.assertNotIn("smollm2", agent["model_id"])
            self.assertTrue("/" in agent["model_id"])

    def test_frontier_8_requires_snapshot(self) -> None:
        with mock.patch("backend.model_rosters.load_frontier_snapshot", return_value=None):
            from backend.frontier_roster import FrontierRosterError

            with self.assertRaises(FrontierRosterError):
                agents_for_roster("frontier_live_8")

    def test_frontier_8_truncates_to_8(self) -> None:
        fake_snapshot = {
            "seats": [
                {"model_id": f"org/model-{i}", "display_name": f"M{i}"} for i in range(16)
            ]
        }
        with mock.patch("backend.model_rosters.load_frontier_snapshot", return_value=fake_snapshot):
            agents = agents_for_roster("frontier_live_8")
        self.assertEqual(len(agents), 8)


class LiveSeasonGuardTest(unittest.TestCase):
    def test_rejects_unknown_roster(self) -> None:
        result = run_live_season(roster="bogus")
        self.assertFalse(result["ok"])
        self.assertIn("roster must be", result["error"])

    def test_allowed_rosters_exact(self) -> None:
        self.assertEqual(ALLOWED_ROSTERS, ("frontier_live_8", "cheap_recent_openrouter"))

    def test_refuses_when_provider_not_openrouter(self) -> None:
        with mock.patch("backend.live_season.should_use_live_llm", return_value=False), mock.patch(
            "backend.live_season.live_llm_provider", return_value="deterministic"
        ):
            result = run_live_season(roster="cheap_recent_openrouter")
        self.assertFalse(result["ok"])
        self.assertIn("not active", result["error"])


if __name__ == "__main__":
    unittest.main()
