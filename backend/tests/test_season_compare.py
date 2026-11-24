"""Strategy measures used to compare prompt versions."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import database
from backend.season_compare import season_measures


class SeasonMeasuresTest(unittest.TestCase):
    def test_measures_come_from_votes_plans_and_promises(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        db = Path(temp_dir.name) / "season.db"
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}):
            database.seed_demo(reset=True, path=db, roster_preset="cheap_recent_openrouter")
        conn = database.get_db_connection(db)
        a, b, c, d, *_ = [row["agent_id"] for row in conn.execute("SELECT agent_id FROM Agents ORDER BY agent_id")]
        conn.execute("UPDATE Agents SET status = 'eliminated', elimination_round = 1 WHERE agent_id = ?", (d,))
        rows = [
            # a predicts b->d (right) and c->a (wrong), and names d as out (right)
            (a, d, json.dumps({b: d, c: a}), d),
            (b, d, "{}", None),
            (c, d, "{}", None),
        ]
        for voter, target, predicted, out in rows:
            conn.execute(
                "INSERT INTO Votes (round, turn_index, voter_id, target_id, revealed, predicted_votes, predicted_eliminated)"
                " VALUES (1, 0, ?, ?, 1, ?, ?)",
                (voter, target, predicted, out),
            )
        conn.execute("INSERT INTO PrivatePlans (round, agent_id, plan) VALUES (1, ?, ?)", (a, json.dumps({"expected_out": d})))
        conn.execute("INSERT INTO PrivatePlans (round, agent_id, plan) VALUES (1, ?, ?)", (b, json.dumps({"expected_out": c})))
        conn.execute("INSERT INTO Promises (round, promiser_id, listener_id, target_id, discussion_step) VALUES (1, ?, ?, ?, 't')", (a, b, d))
        conn.execute("INSERT INTO Promises (round, promiser_id, listener_id, target_id, discussion_step) VALUES (1, ?, ?, ?, 't')", (b, a, c))
        conn.commit()
        conn.close()

        measures = season_measures(db)
        self.assertEqual(measures["vote_call_accuracy"], 0.5)
        self.assertEqual(measures["out_call_accuracy"], 1.0)
        self.assertEqual(measures["votes_following_own_plan"], 0.5)
        self.assertEqual((measures["promises_kept"], measures["promises_broken"]), (1, 1))
        self.assertEqual(measures["broken_promises_that_paid_off"], 1)


if __name__ == "__main__":
    unittest.main()
