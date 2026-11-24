"""Tests for the generated challenge bank and grader."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.challenge_bank import (  # noqa: E402
    DIFFICULTY_TIERS,
    FAMILIES,
    generate_puzzle,
    grade_answer,
    season_puzzle_plan,
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

    def test_instruction_answers_are_verifiable(self) -> None:
        for seed in range(1, 11):
            puzzle = generate_puzzle("instruction_exact", "medium", seed)
            self.assertIsNotNone(puzzle["answer"])
            self.assertTrue(str(puzzle["answer"]).strip() != "")


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


class SeasonPlan(unittest.TestCase):
    def test_plan_is_deterministic(self) -> None:
        a = season_puzzle_plan(99, rounds=9)
        b = season_puzzle_plan(99, rounds=9)
        self.assertEqual(
            [p["puzzle_id"] for ps in a.values() for p in ps],
            [p["puzzle_id"] for ps in b.values() for p in ps],
        )

    def test_plan_difficulty_progression(self) -> None:
        plan = season_puzzle_plan(1, rounds=9)
        rounds_sorted = sorted(plan)
        first_round_diffs = [p["difficulty"] for p in plan[rounds_sorted[0]]]
        last_round_diffs = [p["difficulty"] for p in plan[rounds_sorted[-1]]]
        self.assertIn("easy", first_round_diffs)
        self.assertNotIn("easy", last_round_diffs)

    def test_plan_puzzle_count(self) -> None:
        plan = season_puzzle_plan(7, rounds=6)
        self.assertEqual(len(plan), 6)
        for puzzles in plan.values():
            self.assertEqual(len(puzzles), 3)

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
