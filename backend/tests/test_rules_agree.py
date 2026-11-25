"""The rules every player is given state numbers; each must be the number the engine enforces.

The Methodology page quotes these rules, so a rule that says "five" while the
engine uses six would put a wrong rule in front of every player and every
reader. Each check names the rule sentence and the constant behind it.
"""

from __future__ import annotations

import inspect
import os
import unittest
from unittest.mock import patch

from backend import advantages, auction, challenge_bank, llm_context, strategy_challenge
from backend import turn_controller as tc

WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}


def _rules() -> str:
    # every switch on, so every rule is in the list
    env = {k: v for k, v in os.environ.items() if not k.startswith("SURVIVOR_")}
    with patch.dict(os.environ, env, clear=True):
        return "\n".join(llm_context.game_rules())


class RulesAgreeWithEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rules = _rules()

    def says(self, phrase: str) -> None:
        self.assertIn(phrase, self.rules, f"the players' rules no longer say {phrase!r}")

    def test_puzzles_per_round(self) -> None:
        n = len(challenge_bank.LADDER_LEVELS[0])
        self.assertTrue(all(len(level) == n for level in challenge_bank.LADDER_LEVELS))
        self.assertEqual(len(challenge_bank.RUNG_WEIGHTS), n)
        self.says(f"the same {WORDS[n]} puzzles for every player")

    def test_the_ladder_moves_on_one_perfect_score(self) -> None:
        self.says("the difficulty rises when any player solves them all and falls when nobody reaches three")

    def test_idol_gift_rounds(self) -> None:
        self.says(f"In the first {WORDS[tc.IDOL_GIFT_ROUNDS]} rounds of the season the challenge winner must give a hidden idol")

    def test_idol_expiry(self) -> None:
        self.says(f"An unplayed idol expires when {WORDS[tc.IDOL_EXPIRES_AT]} players remain")

    def test_idol_search(self) -> None:
        self.says(f"From round {tc.IDOL_SEARCH_FROM_ROUND}, while more than {WORDS[tc.IDOL_EXPIRES_AT]} players remain and nobody holds one")
        self.assertEqual(tc.IDOL_HIDDEN_CHANCE, 0.5)
        self.says("an idol is hidden in about half the rounds")

    def test_spying_is_caught_one_time_in_three(self) -> None:
        self.assertAlmostEqual(advantages.SPY_CAUGHT_CHANCE, 1 / 3)
        self.says("one time in three its members are told you listened")

    def test_auction(self) -> None:
        self.says(f"once, when {WORDS[auction.PLAYERS_GONE_BEFORE_AUCTION]} players are gone")
        self.says(f"You earn {auction.TOKENS_PER_ROUND} tokens a round and 1 per puzzle solved")

    def test_one_talk_each_speaking_twice(self) -> None:
        self.assertEqual(tc.PRIVATE_TALKS_PER_ROUND, 1)
        self.assertIn("passes = 2 if len(participants) > 1 else 1", inspect.getsource(tc))
        self.says("every player starts one private talk")
        self.says("each speaks twice")

    def test_strategy_rounds(self) -> None:
        self.assertEqual(strategy_challenge.STRATEGY_EVERY, 2)
        self.says("Every second round, starting with round 2")
        for players in (3, 9, 12):
            self.assertEqual(strategy_challenge.highest_pick(players), 2 * players)
        self.says("picks a whole number from 1 to twice the number of players")
        self.says("the lowest number picked by exactly one player wins immunity")

    def test_finale_at_three(self) -> None:
        self.assertIn("if len(active_agents) <= 3:", inspect.getsource(tc.start_next_round))
        self.says("When three players remain the finale starts")


if __name__ == "__main__":
    unittest.main()
