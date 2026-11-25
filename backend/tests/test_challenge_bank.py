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

        order = ("easy", "medium", "hard", "frontier", "extreme", "master")
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


class ExtremeGridTest(unittest.TestCase):
    """Extreme grids are harder by an unstated twist on a mid-sized grid, not by size.
    Answer keys are checked independently of the generator."""

    def test_mirrored_row_copy_keys_are_right(self) -> None:
        for seed in range(200):
            puzzle = generate_puzzle("grid_row_copy", "extreme", seed)
            for case in [*puzzle["examples"], {"input": puzzle["test_input"], "output": puzzle["answer"]}]:
                grid, out = case["input"], case["output"]
                self.assertLessEqual(len(grid) * len(grid[0]), 100)
                for r, row in enumerate(grid):
                    if any(row) or r == 0:
                        self.assertEqual(out[r], row)
                    else:
                        self.assertEqual(out[r], list(reversed(grid[r - 1])))
            self.assertIn("NOT stated", puzzle["prompt"])
            self.assertNotIn("copied", puzzle["prompt"])

    def test_line_cross_crossing_takes_the_vertical_color_and_examples_rule_out_max_and_min(self) -> None:
        def expected(grid: list[list[int]]) -> tuple[list[list[int]], bool]:
            """Rebuild the answer from the input's endpoints alone."""
            points = [(r, c, v) for r, row in enumerate(grid) for c, v in enumerate(row) if v]
            (r1, c1, h), (r2, c2, _) = next(
                (p, q) for p in points for q in points if p < q and p[0] == q[0] and p[2] == q[2]
            )
            (s1, k1, v), (s2, k2, _) = next(
                (p, q) for p in points for q in points if p < q and p[1] == q[1] and p[2] == q[2]
            )
            out = [list(row) for row in grid]
            for c in range(min(c1, c2) + 1, max(c1, c2)):
                out[r1][c] = h
            for r in range(min(s1, s2) + 1, max(s1, s2)):
                out[r][k1] = v
            out[r1][k1] = v  # the crossing takes the vertical line's color
            return out, v > h

        for seed in range(200):
            puzzle = generate_puzzle("grid_line_cross", "extreme", seed)
            higher = []
            for case in [*puzzle["examples"], {"input": puzzle["test_input"], "output": puzzle["answer"]}]:
                self.assertLessEqual(len(case["input"]) * len(case["input"][0]), 100)
                out, vertical_higher = expected(case["input"])
                self.assertEqual(case["output"], out)
                higher.append(vertical_higher)
            # one example with the vertical color higher, one lower: max or min cannot explain both
            self.assertEqual(higher[:2], [True, False])
            self.assertIn("NOT stated", puzzle["prompt"])

    def test_extreme_answers_are_far_smaller_than_before(self) -> None:
        for family, old_cells in (("grid_row_copy", 18 * 12), ("grid_line_cross", 14 * 14)):
            answer = generate_puzzle(family, "extreme", 3)["answer"]
            self.assertLessEqual(sum(len(row) for row in answer), 100)
            self.assertLess(sum(len(row) for row in answer), old_cells)


class MasterTierTest(unittest.TestCase):
    """Master puzzles: each key re-derived from the puzzle text alone, by brute force."""

    def test_mastermind_has_exactly_one_code_and_it_is_the_key(self) -> None:
        import re
        from itertools import product

        def score(secret: str, guess: str) -> tuple[int, int]:
            exact = sum(a == b for a, b in zip(secret, guess))
            return exact, sum(min(secret.count(c), guess.count(c)) for c in set(guess)) - exact

        for seed in range(4):  # 262,144 codes each: a few seeds keep the test quick
            puzzle = generate_puzzle("mastermind", "master", seed)
            clues = [(g, (int(e), int(n))) for g, e, n in re.findall(r"([A-H]{6}) -> (\d) right color in the right place, (\d) right", puzzle["prompt"])]
            self.assertTrue(clues)
            fits = ["".join(c) for c in product("ABCDEFGH", repeat=6) if all(score("".join(c), g) == f for g, f in clues)]
            self.assertEqual(fits, [puzzle["answer"]])

    def test_knights_and_knaves_has_exactly_one_assignment_and_it_is_the_key(self) -> None:
        import re
        from itertools import product

        people = "ABCDEFGHIJKL"
        for seed in range(25):
            puzzle = generate_puzzle("knights_knaves", "master", seed)
            claims = []
            for speaker, text in re.findall(r'([A-L]) says: "([^"]+)"', puzzle["prompt"]):
                s = people.index(speaker)
                if m := re.fullmatch(r"([A-L]) is a (knight|knave)\.", text):
                    j, k = people.index(m[1]), m[2] == "knight"
                    claims.append((s, lambda w, j=j, k=k: w[j] == k))
                elif m := re.fullmatch(r"([A-L]) and ([A-L]) are the same kind\.", text):
                    j, k = people.index(m[1]), people.index(m[2])
                    claims.append((s, lambda w, j=j, k=k: w[j] == w[k]))
                elif m := re.fullmatch(r"Exactly (\d) of ([A-L, ]+) are knights\.", text):
                    group = [people.index(x.strip()) for x in m[2].split(",")]
                    claims.append((s, lambda w, g=tuple(group), n=int(m[1]): sum(w[i] for i in g) == n))
                elif m := re.fullmatch(r"At least one of ([A-L]) and ([A-L]) is a knave\.", text):
                    j, k = people.index(m[1]), people.index(m[2])
                    claims.append((s, lambda w, j=j, k=k: not (w[j] and w[k])))
                elif m := re.fullmatch(r"An odd number of ([A-L, ]+) are knights\.", text):
                    group = [people.index(x.strip()) for x in m[1].split(",")]
                    claims.append((s, lambda w, g=tuple(group): sum(w[i] for i in g) % 2 == 1))
                elif m := re.fullmatch(r"If ([A-L]) is a knight, then ([A-L]) is a knave\.", text):
                    j, k = people.index(m[1]), people.index(m[2])
                    claims.append((s, lambda w, j=j, k=k: (not w[j]) or (not w[k])))
                else:
                    self.fail(f"unparsed statement: {text}")
            fits = ["".join("K" if x else "N" for x in w) for w in product((True, False), repeat=12)
                    if all(claim(w) == w[s] for s, claim in claims)]
            self.assertEqual(fits, [puzzle["answer"]])

    def test_order_count_matches_an_independent_count(self) -> None:
        import re
        from itertools import permutations

        names = ["Ava", "Ben", "Cy", "Dee", "Eli", "Fay", "Gus", "Hal", "Ivy"]
        for seed in range(6):
            puzzle = generate_puzzle("order_count", "master", seed)
            rules_text = puzzle["prompt"].split("Rules: ")[1].split(" How many")[0]
            checks = []
            for sentence in re.findall(r"[^.]+\.", rules_text):
                sentence = sentence.strip()
                if m := re.fullmatch(r"(\w+) stands somewhere before (\w+)\.", sentence):
                    checks.append(lambda o, a=m[1], b=m[2]: o.index(a) < o.index(b))
                elif m := re.fullmatch(r"(\w+) stands next to (\w+)\.", sentence):
                    checks.append(lambda o, a=m[1], b=m[2]: abs(o.index(a) - o.index(b)) == 1)
                elif m := re.fullmatch(r"(\w+) does not stand next to (\w+)\.", sentence):
                    checks.append(lambda o, a=m[1], b=m[2]: abs(o.index(a) - o.index(b)) != 1)
                elif m := re.fullmatch(r"(\w+) is not at either end of the line\.", sentence):
                    checks.append(lambda o, a=m[1]: o.index(a) not in (0, 8))
                elif m := re.fullmatch(r"Exactly two people stand between (\w+) and (\w+)\.", sentence):
                    checks.append(lambda o, a=m[1], b=m[2]: abs(o.index(a) - o.index(b)) == 3)
                else:
                    self.fail(f"unparsed rule: {sentence}")
            count = sum(1 for o in permutations(names) if all(c(o) for c in checks))
            self.assertEqual(count, puzzle["answer"])
            self.assertGreater(count, 0)

    def test_level_three_uses_the_master_tier(self) -> None:
        from backend.challenge_bank import LADDER_LEVELS

        self.assertEqual(LADDER_LEVELS[3].count("master"), 4)
        for puzzle in ladder_for_round(5, 3, 3):
            self.assertTrue(grade_answer(puzzle["answer"], puzzle["answer"]))
