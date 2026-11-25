"""Tests for the generated challenge bank and grader."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import re  # noqa: E402

from backend.challenge_bank import (  # noqa: E402
    DIFFICULTY_TIERS,
    FAMILIES,
    LADDER_TIERS,
    RUNG_WEIGHTS,
    answer_format,
    generate_puzzle,
    grade_answer,
    ladder_for_round,
)


class GeneratorDeterminism(unittest.TestCase):
    def test_same_seed_same_puzzle(self) -> None:
        for family, difficulty in [
            ("grid_fill_border", "easy"),
            ("grid_row_copy", "medium"),
            ("grid_line_cross", "hard"),
            ("sequence_next", "easy"),
            ("instruction_exact", "medium"),
        ]:
            a = generate_puzzle(family, difficulty, 42)
            b = generate_puzzle(family, difficulty, 42)
            self.assertEqual(a, b, f"{family}/{difficulty} not deterministic")

    def test_different_seed_different_puzzle(self) -> None:
        a = generate_puzzle("grid_fill_border", "easy", 1)
        b = generate_puzzle("grid_fill_border", "easy", 2)
        self.assertNotEqual(a["test_input"], b["test_input"] or a["test_input"])
        # puzzle ids differ
        self.assertNotEqual(a["puzzle_id"], b["puzzle_id"])

    def test_all_families_all_difficulties_generate(self) -> None:
        combos = 0
        for family in FAMILIES:
            for difficulty in DIFFICULTY_TIERS:
                try:
                    puzzle = generate_puzzle(family, difficulty, 7)
                except ValueError:
                    continue
                self.assertIn("prompt", puzzle)
                self.assertIn("answer", puzzle)
                self.assertIn("difficulty", puzzle)
                combos += 1
        self.assertGreaterEqual(combos, 12)


class AnswerSanity(unittest.TestCase):
    def test_grid_answers_are_grids(self) -> None:
        for family in ("grid_fill_border", "grid_row_copy", "grid_line_cross"):
            for difficulty in ("easy", "medium", "hard"):
                puzzle = generate_puzzle(family, difficulty, 11)
                self.assertIsInstance(puzzle["answer"], list)
                self.assertIsInstance(puzzle["answer"][0], list)
                self.assertEqual(len(puzzle["answer"]), len(puzzle["test_input"]))

    def test_line_cross_has_exactly_one_crossing(self) -> None:
        for seed in range(1, 16):
            puzzle = generate_puzzle("grid_line_cross", "hard", seed)
            nines = sum(1 for row in puzzle["answer"] for v in row if v == 9)
            self.assertEqual(nines, 1, f"seed {seed} crossing count {nines}")

    def test_sequence_answer_is_next_term(self) -> None:
        puzzle = generate_puzzle("sequence_next", "easy", 5)
        terms = puzzle["test_input"]
        self.assertIsInstance(terms, list)
        diffs = {terms[i + 1] - terms[i] for i in range(len(terms) - 1)}
        self.assertEqual(len(diffs), 1, "sequence not arithmetic")
        self.assertEqual(puzzle["answer"], terms[-1] + diffs.pop())

    def test_instruction_answers_match_their_prompts(self) -> None:
        """Recompute every answer from the prompt text alone. The old generator drew
        the position and word twice, so about a quarter of medium and most hard keys
        disagreed with their own prompts."""
        for difficulty in ("easy", "medium", "hard", "frontier"):
            for seed in range(400):
                puzzle = generate_puzzle("instruction_exact", difficulty, seed)
                prompt, answer = puzzle["prompt"], puzzle["answer"]
                word = re.search(r"word '([a-z]+)'", prompt).group(1)
                if difficulty == "frontier":
                    n = int(re.search(r"Then take the (\d+)(?:st|nd|rd|th) letter", prompt).group(1))
                    expected = (ord(word[::-1][n - 1]) - 96) * 2 + 1
                elif prompt.startswith("Count the vowels"):
                    expected = sum(1 for ch in word if ch in "aeiou")
                elif prompt.startswith("Return the exact reverse"):
                    expected = word[::-1]
                else:
                    n = int(re.search(r"Return the (\d+)(?:st|nd|rd|th) letter", prompt).group(1))
                    expected = word[n - 1]
                self.assertEqual(answer, expected, f"{difficulty} seed {seed}: {prompt}")

    def test_ordinals_read_correctly(self) -> None:
        for family in ("instruction_exact", "chain_two_step"):
            for difficulty in DIFFICULTY_TIERS:
                for seed in range(200):
                    prompt = generate_puzzle(family, difficulty, seed)["prompt"]
                    self.assertNotRegex(prompt, r"\b(1th|2th|3th|11st|12nd|13rd)\b", prompt)

    def test_no_prompt_promises_a_speed_bonus(self) -> None:
        for family in FAMILIES:
            for difficulty in DIFFICULTY_TIERS:
                prompt = generate_puzzle(family, difficulty, 5)["prompt"].lower()
                self.assertNotIn("speed", prompt)


class Grader(unittest.TestCase):
    def test_exact_grid_match(self) -> None:
        puzzle = generate_puzzle("grid_fill_border", "easy", 3)
        self.assertTrue(grade_answer(puzzle["answer"], puzzle["answer"]))

    def test_stringified_grid_matches(self) -> None:
        puzzle = generate_puzzle("grid_fill_border", "easy", 3)
        import json

        self.assertTrue(grade_answer(json.dumps(puzzle["answer"]), puzzle["answer"]))

    def test_near_miss_rejected(self) -> None:
        puzzle = generate_puzzle("grid_fill_border", "easy", 3)
        wrong = [row[:] for row in puzzle["answer"]]
        wrong[0][0] = (wrong[0][0] + 1) % 10
        self.assertFalse(grade_answer(wrong, puzzle["answer"]))

    def test_number_vs_string_number(self) -> None:
        self.assertTrue(grade_answer("13", 13))
        self.assertFalse(grade_answer(14, 13))

    def test_case_insensitive_string(self) -> None:
        self.assertTrue(grade_answer("Langis", "langis"))
        self.assertFalse(grade_answer("langiss", "langis"))


class Ladder(unittest.TestCase):
    def test_ladder_is_deterministic_and_rises_in_difficulty(self) -> None:
        a = ladder_for_round(99, 7)
        b = ladder_for_round(99, 7)
        self.assertEqual([p["puzzle_id"] for p in a], [p["puzzle_id"] for p in b])
        self.assertEqual(tuple(p["difficulty"] for p in a), LADDER_TIERS)
        self.assertEqual([p["rung"] for p in a], list(range(len(LADDER_TIERS))))
        self.assertEqual(len(RUNG_WEIGHTS), len(LADDER_TIERS))
        self.assertEqual(list(RUNG_WEIGHTS), sorted(RUNG_WEIGHTS))

    def test_ladders_differ_by_round_and_seed(self) -> None:
        self.assertNotEqual(
            [p["puzzle_id"] for p in ladder_for_round(1, 7)], [p["puzzle_id"] for p in ladder_for_round(1, 8)]
        )
        self.assertNotEqual(
            [p["puzzle_id"] for p in ladder_for_round(1, 7)], [p["puzzle_id"] for p in ladder_for_round(2, 7)]
        )

    def test_levels_rise_in_difficulty_and_keep_level_zero_unchanged(self) -> None:
        from backend.challenge_bank import LADDER_LEVELS

        order = ("easy", "medium", "hard", "frontier", "extreme")
        for level, tiers in enumerate(LADDER_LEVELS):
            ladder = ladder_for_round(3, 9, level)
            self.assertEqual(tuple(p["difficulty"] for p in ladder), tiers)
            self.assertEqual([order.index(t) for t in tiers], sorted(order.index(t) for t in tiers))
        self.assertEqual(
            [p["puzzle_id"] for p in ladder_for_round(3, 9)], [p["puzzle_id"] for p in ladder_for_round(3, 9, 0)]
        )

    def test_extreme_sequences_are_verified_independently(self) -> None:
        for seed in range(300):
            puzzle = generate_puzzle("sequence_next", "extreme", seed)
            odd, even = puzzle["_terms"][0::2], puzzle["_terms"][1::2]
            steps = [b - a for a, b in zip(odd, odd[1:])]
            self.assertEqual(len({b - a for a, b in zip(steps, steps[1:])}), 1)
            self.assertEqual(len({b - a for a, b in zip(even, even[1:])}), 1)
            self.assertEqual(puzzle["answer"], odd[-1] + steps[-1] + (steps[-1] - steps[-2]))

    def test_every_puzzle_states_its_answer_format(self) -> None:
        for seed in range(30):
            for puzzle in ladder_for_round(seed, 7) + ladder_for_round(seed, 7, 1) + ladder_for_round(seed, 7, 2):
                self.assertNotEqual(answer_format(puzzle), "a JSON value", puzzle["puzzle_id"])
                if puzzle["answer_kind"] == "grid":
                    self.assertIsInstance(puzzle["answer"], list)
                elif puzzle["answer_kind"] == "number":
                    self.assertIsInstance(puzzle["answer"], int)
                else:
                    self.assertIsInstance(puzzle["answer"], str)

    def test_200_distinct_puzzles(self) -> None:
        ids = set()
        for seed in range(1, 15):
            for family in FAMILIES:
                for difficulty in DIFFICULTY_TIERS:
                    try:
                        ids.add(generate_puzzle(family, difficulty, seed)["puzzle_id"])
                    except ValueError:
                        continue
        self.assertGreaterEqual(len(ids), 200)


if __name__ == "__main__":
    unittest.main()
