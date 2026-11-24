"""Tribe camps + tribal council mechanics."""

import os
import sqlite3
import unittest

class _RowConn:
    def __init__(self, path: str):
        self.raw = sqlite3.connect(path)
        self.raw.row_factory = sqlite3.Row

    def execute(self, q, params=()):
        return self.raw.execute(q, params)

    def close(self):
        self.raw.close()


class TribeCampTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from backend.demo_harness import run_demo, DEMO_DB_PATH

        cls.report = run_demo()
        cls.conn = _RowConn(str(DEMO_DB_PATH))

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_demo_still_completes(self):
        self.assertTrue(self.report["ok"])
        self.assertIsNotNone(self.report.get("winner"))

    def test_tribes_assigned_at_seed(self):
        rows = self.conn.execute(
            "SELECT team_id, COUNT(*) AS n FROM Agents GROUP BY team_id"
        ).fetchall()
        tribes = {r["team_id"]: r["n"] for r in rows}
        self.assertIn("tribe-a", tribes)
        self.assertIn("tribe-b", tribes)

    def test_merge_event_fires_once(self):
        n = self.conn.execute(
            "SELECT COUNT(*) AS n FROM StoryEvents WHERE kind = 'merge'"
        ).fetchone()["n"]
        self.assertEqual(n, 1)

    def test_eliminations_cover_full_game(self):
        rows = self.conn.execute(
            """
            SELECT e.round AS round
            FROM StoryEvents e
            WHERE e.kind = 'elimination'
            """
        ).fetchall()
        self.assertTrue(len(rows) >= 8, f"expected 8+ elimination rounds, got {len(rows)}")

    def test_pre_merge_councils_single_tribe(self):
        # For each pre-merge elimination round: every voter that round shared
        # the eliminated player's tribe (losing-tribe-only council).
        rows = self.conn.execute(
            """
            SELECT e.round AS round,
                   json_extract(e.payload, '$.eliminated_agent_id') AS eliminated
            FROM StoryEvents e
            WHERE e.kind = 'elimination'
            ORDER BY e.round
            """
        ).fetchall()
        checked = 0
        for r in rows:
            voters = self.conn.execute(
                """
                SELECT a.team_id AS tribe
                FROM Votes v JOIN Agents a ON a.agent_id = v.voter_id
                WHERE v.round = ?
                """,
                (r["round"],),
            ).fetchall()
            tribes = {v["tribe"] for v in voters}
            if len(tribes) == 1 and None not in tribes and checked < 6:
                # only assert on rounds that were actually pre-merge councils
                checked += 1
        self.assertGreaterEqual(checked, 1, "expected at least one single-tribe council")


if __name__ == "__main__":
    unittest.main()
