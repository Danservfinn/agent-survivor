"""Strategy rounds: every second round the challenge is lowest unique number."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from backend import strategy_challenge as sc
from backend import turn_controller as tc
from backend.llm_context import build_agent_episode_context, game_rules
from backend.tests.test_model_failures import _Base


class RulesTest(unittest.TestCase):
    def test_lowest_unique_number_wins_and_a_full_collision_has_no_winner(self) -> None:
        self.assertEqual(sc.lowest_unique_winner({"a": 1, "b": 1, "c": 2, "d": 3}), ("c", 2))
        self.assertEqual(sc.lowest_unique_winner({"a": 4, "b": 9}), ("a", 4))
        self.assertEqual(sc.lowest_unique_winner({"a": 2, "b": 2, "c": 5, "d": 5}), (None, None))
        self.assertEqual(sc.lowest_unique_winner({}), (None, None))

    def test_picks_must_be_whole_numbers_in_range(self) -> None:
        self.assertEqual(sc.clean_pick("3", 10), 3)
        self.assertEqual(sc.clean_pick(4.0, 10), 4)
        self.assertIsNone(sc.clean_pick(0, 10))
        self.assertIsNone(sc.clean_pick(11, 10))
        self.assertIsNone(sc.clean_pick("one", 10))
        self.assertEqual(sc.highest_pick(9), 18)

    def test_every_second_round_starting_with_round_2(self) -> None:
        self.assertEqual([sc.is_strategy_round(r, 1) for r in range(1, 7)], [False, True, False, True, False, True])
        with patch.dict(os.environ, {"SURVIVOR_STRATEGY_CHALLENGES": "0"}):
            self.assertFalse(sc.is_strategy_round(2, 1))

    def test_the_rule_is_told_only_while_on(self) -> None:
        self.assertTrue(any("lowest number picked by exactly one player" in r for r in game_rules()))
        with patch.dict(os.environ, {"SURVIVOR_STRATEGY_CHALLENGES": "0"}):
            self.assertFalse(any("lowest number picked by exactly one player" in r for r in game_rules()))


class StrategyRoundTest(_Base):
    def _picks(self, picks: dict[str, int | None], round_number: int = 2) -> dict:
        self.conn.executemany(
            "INSERT INTO StrategyPicks (round, agent_id, pick, provider) VALUES (?, ?, ?, 'openrouter')",
            [(round_number, aid, n) for aid, n in picks.items()],
        )
        with patch.object(tc, "_season_first_round", return_value=1):
            return tc._ensure_challenge_result(self.conn, round_number)

    def test_the_winner_is_immune_and_the_ladder_level_carries_over(self) -> None:
        a, b, c, d, *_ = self.players
        self.conn.execute(
            "INSERT INTO ChallengeResults (round, puzzle_id, challenge_type, result_payload) VALUES (1, 'p', 'individual', ?)",
            (json.dumps({"ladder_level": 2, "scores": {a: {"correct": 4, "weighted": 9}}}),),
        )
        result = self._picks({a: 1, b: 1, c: 2, d: None})
        self.assertEqual(result["winning_agent_id"], c)
        self.assertEqual(result["challenge_type"], sc.LOWEST_UNIQUE)
        immune = [r["agent_id"] for r in self.conn.execute("SELECT agent_id FROM Agents WHERE has_immunity = 1")]
        self.assertEqual(immune, [c])
        payload = result["result_payload"]
        self.assertEqual(payload["picks"], {a: 1, b: 1, c: 2})  # no pick, no entry
        self.assertEqual(payload["ladder_level"], 2)  # carried: nobody aced or failed the ladder in between
        with patch.object(tc, "_season_first_round", return_value=1):
            self.assertEqual(tc._ladder_level(self.conn, 3), 2)

    def test_a_full_collision_means_nobody_is_immune(self) -> None:
        a, b, *_ = self.players
        result = self._picks({a: 3, b: 3})
        self.assertIsNone(result["winning_agent_id"])
        self.assertEqual(result["status"], "no_winner")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM Agents WHERE has_immunity = 1").fetchone()[0], 0)

    def test_picks_become_public_only_with_the_result(self) -> None:
        a, b, c, *_ = self.players
        self.conn.executemany(
            "INSERT INTO StrategyPicks (round, agent_id, pick) VALUES (2, ?, ?)", [(a, 1), (b, 2)]
        )
        before = build_agent_episode_context(self.conn, actor_id=c, round_number=2, current_step="x")["challenge_history"]
        self.assertEqual(before, [])
        with patch.object(tc, "_season_first_round", return_value=1):
            tc._ensure_challenge_result(self.conn, 2)
        [entry] = build_agent_episode_context(self.conn, actor_id=c, round_number=2, current_step="x")["challenge_history"]
        self.assertEqual(entry["challenge"], "lowest unique number")
        self.assertEqual(sorted(entry["picks"].values()), [1, 2])

    def test_live_picks_are_sealed_and_bad_picks_are_dropped(self) -> None:
        players = [r["agent_id"] for r in self.conn.execute("SELECT agent_id FROM Agents WHERE status = 'active' ORDER BY agent_id")]
        replies = {players[0]: {"pick": 1}, players[1]: {"pick": "x"}, players[2]: {"pick": 99}}

        def private(**kwargs):
            return replies.get(kwargs["actor"]["agent_id"], {"pick": 4})

        self._live(lambda **kwargs: None)
        with patch.object(tc, "request_private_writing", side_effect=private), patch.object(
            tc, "_season_first_round", return_value=1
        ), patch.object(tc, "_parallel_enabled", return_value=False):
            [event] = tc._challenge_attempt_events(self.conn, 2, 1)
        stored = {r["agent_id"]: r["pick"] for r in self.conn.execute("SELECT agent_id, pick FROM StrategyPicks")}
        self.assertEqual(stored[players[0]], 1)
        self.assertIsNone(stored[players[1]])
        self.assertIsNone(stored[players[2]])  # out of range
        self.assertNotIn("1", event["dialogue"].split("players")[0][-3:])  # the event does not reveal picks
        self.assertEqual(event["payload"]["challenge_type"], sc.LOWEST_UNIQUE)

    def test_ladder_rounds_are_unchanged(self) -> None:
        with patch.object(tc, "_season_first_round", return_value=1):
            self.assertFalse(tc._strategy_round(self.conn, 1))
            self.assertTrue(tc._strategy_round(self.conn, 2))


if __name__ == "__main__":
    unittest.main()
