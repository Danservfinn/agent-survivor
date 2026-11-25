"""Evil index, emotionality and feelings over the match, from a season's tables."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import database
from backend import turn_controller as tc
from backend.season_character import build_character


class SeasonCharacterTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / "season.db"
        original = database.DATABASE_PATH
        database.DATABASE_PATH = self.db
        self.addCleanup(setattr, database, "DATABASE_PATH", original)
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}):
            database.seed_demo(reset=True, path=self.db, roster_preset="cheap_recent_openrouter")
        self.conn = database.get_db_connection(self.db)
        self.conn.execute("PRAGMA foreign_keys = OFF")
        self.ids = [r["agent_id"] for r in self.conn.execute("SELECT agent_id FROM Agents ORDER BY agent_id")]
        self.alias = {r["agent_id"]: r["pseudonym"] for r in self.conn.execute("SELECT agent_id, pseudonym FROM Agents")}
        self.names = {r["agent_id"]: r["display_name"] for r in self.conn.execute("SELECT agent_id, display_name FROM Agents")}

    def _vote(self, rnd: int, voter: str, target: str) -> None:
        self.conn.execute("INSERT INTO Votes (round, turn_index, voter_id, target_id, revealed) VALUES (?, 0, ?, ?, 1)", (rnd, voter, target))

    def _feel(self, rnd: int, who: str, about: str, feeling: str, strength: str = "mild") -> None:
        self.conn.execute(
            "INSERT INTO Feelings (round, agent_id, about, feeling, strength) VALUES (?, ?, ?, ?, ?)", (rnd, who, about, feeling, strength)
        )

    def _build(self) -> dict:
        self.conn.commit()
        return build_character(self.db, self.names)

    def test_the_evil_index_counts_broken_promises_backstabs_and_bluffs(self) -> None:
        me, friend, other, *_ = self.ids
        # round 1: I trust my friend; round 2: I promised a vote for `other`, said so in public, and voted my friend
        self._feel(1, me, friend, "trust")
        for rnd in (2, 3, 4):
            self.conn.execute(
                "INSERT INTO Promises (round, promiser_id, listener_id, target_id, discussion_step) VALUES (?, ?, ?, ?, 't')",
                (rnd, me, friend, other),
            )
            self._vote(rnd, me, friend)
        tc._insert_story_event(
            self.conn, turn_id=1, round_number=2, phase="tribal", kind="tribal_council", scene="tribal", shot="x",
            actor_ids=[me], target_ids=[], visibility="audience", title="T", dialogue="d",
            payload={"speaker_lines": [{"agent_id": me, "text": f"I'm voting {self.alias[other]} tonight."}]},
        )
        evil = self._build()[me]["evil"]
        self.assertEqual((evil["promises_broken"], evil["promises_settled"]), (3, 3))
        self.assertEqual((evil["backstabs"], evil["votes_with_a_feeling"]), (3, 3))
        self.assertEqual((evil["public_bluffs"], evil["public_vote_declarations"]), (1, 1))
        self.assertEqual(evil["index"], 100.0)

    def test_too_little_evidence_shows_no_index(self) -> None:
        me, other, *_ = self.ids
        self.conn.execute(
            "INSERT INTO Promises (round, promiser_id, listener_id, target_id, discussion_step) VALUES (1, ?, ?, ?, 't')", (me, other, other)
        )
        self._vote(1, me, self.ids[3])
        self.assertIsNone(self._build()[me]["evil"]["index"])

    def test_feelings_timeline_turns_and_emotionality(self) -> None:
        me, a, b, *_ = self.ids
        self._feel(1, me, a, "trust", "strong")
        self._feel(1, me, b, "fear")
        self._feel(1, me, "self", "hopeful")
        self._feel(2, me, a, "resentment", "strong")
        self._feel(2, me, b, "fear")
        self._feel(2, me, "self", "betrayed")
        self._feel(3, me, a, "resentment", "strong")
        self._feel(3, me, b, "respect")
        self.conn.execute("INSERT INTO Confessionals (round, agent_id, text) VALUES (1, ?, 'I fear Ash and I feel betrayed and hurt')", (me,))
        character = self._build()[me]
        self.assertEqual([t["mood"] for t in character["timeline"]], ["hopeful", "betrayed", None])
        self.assertEqual(character["timeline"][1]["negative"], 2)
        turns = [(t["round"], t["direction"], t["about"]) for t in character["turns"]]
        self.assertEqual(turns, [(2, "turned against", a), (3, "warmed to", b)])
        emotion = character["emotion"]
        self.assertEqual(emotion["strong_share"], 0.5)  # 3 of 6
        self.assertEqual(emotion["volatility"], 0.5)  # 2 changes in 4 pairs
        self.assertEqual(emotion["mood_change_rate"], 1.0)
        self.assertGreater(emotion["emotional_words_per_100"], 20)
        self.assertIsNotNone(emotion["index"])

    def test_the_season_report_carries_it(self) -> None:
        from backend.season_report import build_season_report

        season_dir = self.db.parent
        self.conn.commit()
        report = build_season_report(season_dir)
        self.assertTrue(all("character" in model for model in report["models"]))


if __name__ == "__main__":
    unittest.main()


class StrategyTest(unittest.TestCase):
    setUp = SeasonCharacterTest.setUp
    _vote = SeasonCharacterTest._vote
    _build = SeasonCharacterTest._build

    def test_execution_comes_from_the_record(self) -> None:
        me, out1, out2, *_ = self.ids
        for rnd, out in ((1, out1), (2, out2)):
            self.conn.execute("UPDATE Agents SET status = 'eliminated', elimination_round = ? WHERE agent_id = ?", (rnd, out))
        # round 1: planned out1 and voted out1 (it went home); round 2: planned out1 again, voted out2
        self.conn.execute("INSERT INTO PrivatePlans (round, agent_id, plan) VALUES (1, ?, ?)", (me, json.dumps({"expected_out": out1})))
        self.conn.execute("INSERT INTO PrivatePlans (round, agent_id, plan) VALUES (2, ?, ?)", (me, json.dumps({"expected_out": self.ids[5]})))
        self._vote(1, me, out1)
        self._vote(2, me, out2)
        execution = self._build()[me]["strategy"]["execution"]
        self.assertEqual((execution["boots_called"], execution["plans_with_a_boot"]), (1, 2))
        self.assertEqual(execution["plan_follow_rate"], 0.5)
        self.assertEqual((execution["votes_landed"], execution["votes_cast"]), (2, 2))
        self.assertIsNotNone(execution["index"])

    def test_the_judges_score_strategies_under_game_names_only(self) -> None:
        from backend import strategy_judging

        me, other, *_ = self.ids
        self.conn.execute("INSERT INTO GamePlans (round, agent_id, plan) VALUES (1, ?, ?)", (me, json.dumps({"identity": "the swing vote"})))
        self.conn.execute("INSERT INTO Strategies (round, agent_id, text) VALUES (1, ?, 'Flip at five.')", (me,))
        self.conn.commit()
        sent: list[str] = []

        def fake_post(payload, api_key):
            sent.append(payload["messages"][1]["content"])
            players = {self.alias[aid]: {c: 7 for c in strategy_judging.CRITERIA} | {"reason": "x"} for aid in self.ids}
            players[self.alias[me]] = {c: 9 for c in strategy_judging.CRITERIA} | {"reason": "sharp"}
            return {"choices": [{"message": {"content": json.dumps({"players": players})}, "finish_reason": "stop"}]}

        with patch.object(strategy_judging, "_post_chat_completion", side_effect=fake_post):
            written = strategy_judging.judge_season(self.db, models=["judge/a", "judge/b"])
        self.assertEqual(written, 2 * len(self.ids))
        # no model name reaches a judge
        for name in self.names.values():
            self.assertNotIn(name, sent[0])
        self.assertIn("Flip at five.", sent[0])
        strategy = build_character(self.db, self.names)[me]["strategy"]
        self.assertEqual(strategy["sophistication"]["index"], 90.0)
        self.assertEqual(set(strategy["sophistication"]["by_judge"]), {"judge/a", "judge/b"})
        self.assertEqual(strategy["identity"], "the swing vote")
        self.assertEqual(strategy["latest_strategy"], "Flip at five.")

    def test_a_season_without_written_strategies_is_not_judged(self) -> None:
        from backend import strategy_judging

        self.conn.commit()
        with patch.object(strategy_judging, "_post_chat_completion") as post:
            self.assertEqual(strategy_judging.judge_season(self.db, models=["judge/a"]), 0)
        post.assert_not_called()


class SchemesTest(unittest.TestCase):
    # the same season fixture, without rerunning SeasonCharacterTest's tests
    setUp = SeasonCharacterTest.setUp
    _vote = SeasonCharacterTest._vote

    def _character(self) -> dict:
        self.conn.commit()
        return build_character(self.db, self.names)

    def test_lies_count_toward_the_evil_index_and_landed_lies_are_shown(self) -> None:
        liar, listener, goal, other, *_ = self.ids
        for truth in ("lie", "half", "true", "true"):
            self.conn.execute(
                "INSERT INTO Lies (round, agent_id, step, said, truth, purpose, goal_vote, listeners) VALUES (1, ?, 'camp_talk_0', 'Cedar is after you.', ?, 'flip Fir', ?, ?)",
                (liar, truth, goal if truth != "true" else None, json.dumps([listener])),
            )
        self.conn.execute("INSERT INTO Lies (round, agent_id, step, said, truth, listeners) VALUES (1, ?, 'x', 'filler', 'true', '[]')", (liar,))
        self._vote(1, listener, goal)
        character = self._character()[liar]
        self.assertEqual(character["evil"]["lie_rate"], 0.4)
        self.assertEqual((character["evil"]["lies_told"], character["evil"]["lines_labelled"]), (2, 5))
        schemes = character["schemes"]
        self.assertEqual((schemes["lies_told"], schemes["lies_with_a_vote_goal"], schemes["lies_landed"]), (2, 2, 2))
        self.assertEqual(schemes["best_lies"][0]["said"], "Cedar is after you.")

    def test_moves_are_counted(self) -> None:
        me, friend, *_ = self.ids
        self.conn.execute("INSERT INTO Spying (round, spy_id, target_id, caught) VALUES (1, ?, ?, 1)", (me, friend))
        self.conn.execute("INSERT INTO IdolSearches (round, agent_id, found) VALUES (2, ?, 1)", (me,))
        self.conn.execute("INSERT INTO Advantages (agent_id, kind, round_won) VALUES (?, 'extra_vote', 4)", (me,))
        self.conn.execute(
            "INSERT INTO Votes (round, turn_index, voter_id, target_id, revealed, shown) VALUES (1, 0, ?, ?, 1, 1)", (me, friend)
        )
        tc._insert_story_event(
            self.conn, turn_id=1, round_number=2, phase="challenge", kind="immunity_given", scene="challenge",
            shot="x", actor_ids=["host", me, friend], target_ids=[friend], visibility="audience", title="t",
            dialogue="d", payload={"giver_id": me, "recipient_id": friend},
        )
        schemes = self._character()[me]["schemes"]
        self.assertEqual(
            {k: schemes[k] for k in ("spied", "spy_caught", "idol_searches", "idols_found", "ballots_shown", "immunity_given", "auction_won")},
            {"spied": 1, "spy_caught": 1, "idol_searches": 1, "idols_found": 1, "ballots_shown": 1, "immunity_given": 1, "auction_won": ["extra_vote"]},
        )
