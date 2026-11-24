"""Field-aware challenge difficulty: frontier tier iff a frontier seat plays."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.challenge_bank import (  # noqa: E402
    _is_frontier_model_id,
    season_puzzle_plan,
)

CHEAP = [
    "rekaai/reka-edge",
    "google/gemma-4-26b-a4b-it",
    "cohere/command-r7b-12-2024",
    "mistralai/mistral-nemo",
]
WITH_FRONTIER = CHEAP + ["x-ai/grok-4.7"]


class FieldDifficultyTest(unittest.TestCase):
    def test_frontier_detection(self) -> None:
        self.assertTrue(_is_frontier_model_id("x-ai/grok-4.7"))
        self.assertTrue(_is_frontier_model_id("openai/gpt-6-sol"))
        self.assertTrue(_is_frontier_model_id("anthropic/claude-opus-4.8"))
        self.assertFalse(_is_frontier_model_id("google/gemma-4-26b-a4b-it"))
        self.assertFalse(_is_frontier_model_id("ibm-granite/granite-4.2-8b"))
        self.assertFalse(_is_frontier_model_id("rekaai/reka-edge"))

    def test_frontier_seat_forces_frontier_tier(self) -> None:
        plan = season_puzzle_plan(7, rounds=4, active_model_ids=WITH_FRONTIER)
        for round_number, puzzles in plan.items():
            for puzzle in puzzles:
                self.assertEqual(puzzle["difficulty"], "frontier", f"round {round_number}")
                self.assertEqual(puzzle["time_budget_s"], 15)

    def test_cheap_field_gets_hard_tier_with_extended_window(self) -> None:
        plan = season_puzzle_plan(7, rounds=4, active_model_ids=CHEAP)
        for round_number, puzzles in plan.items():
            for puzzle in puzzles:
                self.assertEqual(puzzle["difficulty"], "hard", f"round {round_number}")
                self.assertEqual(puzzle["time_budget_s"], 60)

    def test_deterministic_given_same_seed_and_field(self) -> None:
        a = season_puzzle_plan(7, rounds=3, active_model_ids=CHEAP)
        b = season_puzzle_plan(7, rounds=3, active_model_ids=CHEAP)
        self.assertEqual(a, b)

    def test_legacy_call_without_field_keeps_progressive_mix(self) -> None:
        plan = season_puzzle_plan(7, rounds=32)
        self.assertEqual([p["difficulty"] for p in plan[7]], ["easy", "easy", "medium"])
        self.assertEqual([p["difficulty"] for p in plan[31]], ["hard", "frontier", "frontier"])


if __name__ == "__main__":
    unittest.main()
