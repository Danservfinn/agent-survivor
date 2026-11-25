import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import sqlite3
import tempfile

from backend.database import seed_demo, DATABASE_PATH  # noqa: E402
from backend import database  # noqa: E402
from backend import season_narrative as sn  # noqa: E402
from backend.season_narrative import season_narrative  # noqa: E402


class SeasonNarrativeTest(unittest.TestCase):
    def test_narrative_from_seeded_season(self):
        """A seeded DB with story events yields rounds, votes, and a finale shape."""
        with tempfile.TemporaryDirectory() as td:
            season_root = Path(td) / "myseason"
            season_root.mkdir()
            db = season_root / "season.db"
            database.DATABASE_PATH = db
            seed_demo(reset=True, path=db)
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            # stored event text uses in-game aliases; the narrative shows model names
            names = {
                r["agent_id"]: r["pseudonym"]
                for r in conn.execute("SELECT agent_id, pseudonym FROM Agents")
            }
            shown = {
                r["agent_id"]: r["display_name"]
                for r in conn.execute("SELECT agent_id, display_name FROM Agents")
            }
            aid = sorted(names)[0]
            bid = sorted(names)[1]
            cid = sorted(names)[2]
            conn.execute(
                "INSERT INTO StoryEvents (turn_id, round, sequence, phase, kind, scene, shot, title, subtitle, dialogue, inner_thought, actor_ids, visibility)"
                " VALUES (1, 7, 1, 'challenge', 'challenge_result', 'challenge', 'wide', 'Challenge Result', 'Alpha leads tribe-a to victory!', '', NULL, '[]', 'public')"
            )
            conn.execute(
                "INSERT INTO StoryEvents (turn_id, round, sequence, phase, kind, scene, shot, title, subtitle, dialogue, inner_thought, actor_ids, visibility)"
                " VALUES (1, 7, 2, 'camp', 'conversation', 'camp', 'medium', 'Camp', NULL, 'We should target the big threat.', 'Grok immunity shifts my target.', ?, 'tribe')",
                (f'["{aid}"]',),
            )
            conn.execute(
                "INSERT INTO StoryEvents (turn_id, round, sequence, phase, kind, scene, shot, title, subtitle, dialogue, inner_thought, actor_ids, visibility)"
                " VALUES (1, 7, 3, 'council', 'vote_booth', 'council', 'close', ?, ?, 'I am voting for the big threat tonight.', 'Removing the threat protects my game.', ?, 'private')",
                (f"{names[aid]} Votes", f"Vote: {names[bid]}", f'["{aid}"]'),
            )
            conn.execute(
                "INSERT INTO StoryEvents (turn_id, round, sequence, phase, kind, scene, shot, title, subtitle, dialogue, inner_thought, actor_ids, visibility)"
                " VALUES (1, 7, 4, 'council', 'elimination', 'council', 'wide', ?, 'The vote lands.', '', NULL, ?, 'public')",
                (f"{names[bid]} Leaves the Game", f'["{bid}"]'),
            )
            conn.execute(
                "INSERT INTO StoryEvents (turn_id, round, sequence, phase, kind, scene, shot, title, subtitle, dialogue, inner_thought, actor_ids, visibility)"
                " VALUES (1, 13, 1, 'finale', 'winner_declared', 'finale', 'wide', 'Winner Declared', ?, '', NULL, '[]', 'public')",
                (f"Jury tally: {names[cid]}: 3, {names[bid]}: 2, {names[aid]}: 1",),
            )
            conn.execute(
                "INSERT INTO Confessionals (round, agent_id, text) VALUES (7, ?, ?)",
                (aid, f"I don't trust {names[bid]} and I want the jury."),
            )
            conn.commit()
            conn.close()

            original_dir = sn._ls.SEASONS_DIR
            sn._ls.SEASONS_DIR = str(season_root.parent)
            try:
                result = season_narrative("myseason")
            finally:
                sn._ls.SEASONS_DIR = original_dir
            self.assertTrue(result["ok"])
            self.assertEqual(len(result["rounds"]), 2)
            r7 = result["rounds"][0]
            self.assertIn("Alpha leads tribe-a", r7["summary"])
            self.assertEqual(r7["elimination"], shown[bid])
            self.assertEqual(r7["votes"][0]["who"], shown[aid])
            self.assertEqual(r7["votes"][0]["target"], shown[bid])
            self.assertEqual(r7["conversations"][0]["who"], shown[aid])
            self.assertEqual(result["finale"]["winner"], shown[cid])
            self.assertNotEqual(names[bid], shown[bid])
            # confessionals show under model names, with names in the text swapped too
            self.assertEqual(
                r7["confessionals"], [{"who": shown[aid], "text": f"I don't trust {shown[bid]} and I want the jury."}]
            )
            self.assertEqual(result["rounds"][1]["confessionals"], [])
            self.assertEqual(result["jury"], [])
            self.assertEqual(result["pitches"], [])

    def test_a_season_from_before_confessionals_still_loads(self):
        with tempfile.TemporaryDirectory() as td:
            season_root = Path(td) / "oldseason"
            season_root.mkdir()
            db = season_root / "season.db"
            database.DATABASE_PATH = db
            seed_demo(reset=True, path=db)
            conn = sqlite3.connect(db)
            conn.execute("DROP TABLE Confessionals")
            conn.execute(
                "INSERT INTO StoryEvents (turn_id, round, sequence, phase, kind, scene, shot, title, subtitle, dialogue, inner_thought, actor_ids, visibility)"
                " VALUES (1, 1, 1, 'challenge', 'challenge_result', 'challenge', 'wide', 'Challenge Result', 'x', '', NULL, '[]', 'public')"
            )
            conn.commit()
            conn.close()
            original_dir = sn._ls.SEASONS_DIR
            sn._ls.SEASONS_DIR = str(season_root.parent)
            try:
                result = season_narrative("oldseason")
            finally:
                sn._ls.SEASONS_DIR = original_dir
            self.assertTrue(result["ok"])
            self.assertEqual(result["rounds"][0]["confessionals"], [])

    def test_missing_season_returns_not_found(self):
        with self.assertRaises(FileNotFoundError):
            season_narrative("no-such-season")


if __name__ == "__main__":
    unittest.main()
