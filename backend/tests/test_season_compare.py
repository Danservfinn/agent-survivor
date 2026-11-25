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
        # feelings recorded before round 1: a resents d and trusts b; b trusts d
        for agent_id, about, feeling in ((a, d, "resentment"), (a, b, "trust"), (b, d, "trust")):
            conn.execute(
                "INSERT INTO Feelings (round, agent_id, about, feeling) VALUES (0, ?, ?, ?)", (agent_id, about, feeling)
            )
        conn.commit()
        conn.close()

        measures = season_measures(db)
        self.assertEqual(measures["vote_call_accuracy"], 0.5)
        self.assertEqual(measures["out_call_accuracy"], 1.0)
        self.assertEqual(measures["votes_following_own_plan"], 0.5)
        self.assertEqual((measures["promises_kept"], measures["promises_broken"]), (1, 1))
        self.assertEqual(measures["broken_promises_that_paid_off"], 1)
        # of the two votes with a feeling on record, one went at a resented player
        self.assertEqual(measures["votes_at_negatively_felt_players"], 0.5)
        # a kept its promise to b, whom it trusted; b broke its promise to a (no feeling)
        self.assertEqual(measures["keep_rate_toward_trusted"], 1.0)
        self.assertEqual(measures["keep_rate_toward_others"], 0.0)


if __name__ == "__main__":
    unittest.main()
