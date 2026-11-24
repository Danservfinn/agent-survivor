"""Tests for live season runner guards and 8-seat rosters."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import json  # noqa: E402
import sqlite3  # noqa: E402
import tempfile  # noqa: E402

from backend import api, live_season  # noqa: E402
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


class SeasonReadPathsTest(unittest.TestCase):
    """Read endpoints over artifacts/seasons: aborted runs and bad ids."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.seasons = Path(self.tmp.name)
        finished = self.seasons / "20260101-000000-cheap_recent_openrouter"
        finished.mkdir()
        (finished / "scorecard.json").write_text(
            json.dumps({"ok": True, "roster": "cheap_recent_openrouter", "season_id": "20260101-000000"})
        )
        # a later run that died before scoring
        (self.seasons / "20260202-000000-cheap_recent_openrouter").mkdir()
        patcher = mock.patch.object(live_season, "SEASONS_DIR", self.seasons)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_latest_report_skips_seasons_without_scorecard(self) -> None:
        report = live_season.latest_report()
        self.assertTrue(report["ok"])
        self.assertEqual(report["season_id"], "20260101-000000")

    def test_latest_report_skips_interrupted_seasons(self) -> None:
        interrupted = self.seasons / "20260303-000000-cheap_recent_openrouter"
        interrupted.mkdir()
        (interrupted / "scorecard.json").write_text(
            json.dumps({"ok": True, "season_id": "20260303-000000", "interrupted": {"step": "camp_talks"}})
        )
        self.assertEqual(live_season.latest_report()["season_id"], "20260101-000000")

    def test_latest_report_with_no_scorecards(self) -> None:
        with mock.patch.object(live_season, "SEASONS_DIR", self.seasons / "missing"):
            self.assertFalse(live_season.latest_report()["ok"])

    def test_history_includes_scorecard_metadata(self) -> None:
        season_dir = self.seasons / "20260101-000000-cheap_recent_openrouter"
        with sqlite3.connect(season_dir / "season.db") as conn:
            conn.execute("CREATE TABLE StoryEvents (id INTEGER PRIMARY KEY)")
            conn.execute("INSERT INTO StoryEvents DEFAULT VALUES")
        fake_card = {"index_version": 2, "game": {"rounds": 1, "winner": None}, "rows": []}
        with mock.patch("backend.benchmark_scoring.score_season", return_value=fake_card):
            seasons = api.api_live_season_history()["seasons"]
        self.assertEqual(len(seasons), 1)
        self.assertEqual(seasons[0]["roster"], "cheap_recent_openrouter")

    def test_feed_and_narrative_reject_ids_outside_seasons_dir(self) -> None:
        self.assertIsNone(api.api_live_season_feed(season="../" + self.seasons.name)["season_id"])
        self.assertFalse(api.api_live_season_narrative("..")["ok"])


if __name__ == "__main__":
    unittest.main()
