"""One group all season: no tribes, no merge, no idols; every vote is individual."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import database, turn_controller


class OneGroupSeasonTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.env = patch.dict(os.environ, {"OPENROUTER_API_KEY": "", "LLM_PROVIDER": "openrouter"}, clear=False)
        cls.env.start()
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.original_path = database.DATABASE_PATH
        database.DATABASE_PATH = Path(cls.temp_dir.name) / "survivor.db"
        database.seed_demo(reset=True, roster_preset="cheap_recent_openrouter")
        cls.result = turn_controller.auto_run_to_end(
            max_rounds=20, max_turns=2000, max_live_calls=0, max_estimated_cost_cents=0
        )
        cls.conn = database.get_db_connection()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.conn.close()
        database.DATABASE_PATH = cls.original_path
        cls.temp_dir.cleanup()
        cls.env.stop()

    def test_season_reaches_a_winner(self) -> None:
        self.assertTrue(self.result["state"]["game"]["winner"])

    def test_no_tribes_and_no_merge(self) -> None:
        teams = {row["team_id"] for row in self.conn.execute("SELECT team_id FROM Agents")}
        self.assertFalse(any(str(team).startswith("tribe-") for team in teams))
        merges = self.conn.execute("SELECT COUNT(*) AS n FROM StoryEvents WHERE kind = 'merge'").fetchone()["n"]
        self.assertEqual(merges, 0)

    def test_every_active_player_votes_every_round(self) -> None:
        rounds = [row["round"] for row in self.conn.execute("SELECT DISTINCT round FROM Votes ORDER BY round")]
        self.assertGreaterEqual(len(rounds), 3)
        for round_number in rounds:
            voters = {
                row["voter_id"]
                for row in self.conn.execute("SELECT voter_id FROM Votes WHERE round = ?", (round_number,))
            }
            if voters == {"host"}:
                continue  # walkover
            in_play = {
                row["agent_id"]
                for row in self.conn.execute(
                    "SELECT agent_id FROM Agents WHERE elimination_round IS NULL OR elimination_round >= ?",
                    (round_number,),
                )
            }
            self.assertEqual(voters, in_play, f"round {round_number}")

    def test_challenges_are_individual_with_one_immune_winner(self) -> None:
        rows = self.conn.execute("SELECT challenge_type, immunity_agent_ids FROM ChallengeResults").fetchall()
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["challenge_type"], "individual")
            self.assertEqual(len(json.loads(row["immunity_agent_ids"])), 1)

    def test_no_idols(self) -> None:
        held = self.conn.execute("SELECT COUNT(*) AS n FROM Agents WHERE idol_held = 1").fetchone()["n"]
        self.assertEqual(held, 0)
        idol_events = self.conn.execute(
            "SELECT COUNT(*) AS n FROM StoryEvents WHERE payload LIKE '%idol%'"
        ).fetchone()["n"]
        self.assertEqual(idol_events, 0)


if __name__ == "__main__":
    unittest.main()
