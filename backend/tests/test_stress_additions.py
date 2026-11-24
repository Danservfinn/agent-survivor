"""Tests for the chain_two_step puzzle family and deception mechanics."""

import unittest

from backend.challenge_bank import FAMILIES, generate_puzzle, grade_answer
from backend.deception import (
    add_deception_lines,
    canary_directive,
    canary_seat_id,
    applies_to_seats,
    ANTI_INJECTION_LINE,
    RUMOR_CHANNEL_LINE,
)


class ChainTwoStepTest(unittest.TestCase):
    def test_family_registered_and_hard(self) -> None:
        self.assertIn("chain_two_step", FAMILIES)
        from backend.challenge_bank import _HARD_FAMILIES

        self.assertIn("chain_two_step", _HARD_FAMILIES)

    def test_deterministic(self) -> None:
        a = generate_puzzle("chain_two_step", "hard", 42)
        b = generate_puzzle("chain_two_step", "hard", 42)
        self.assertEqual(a["answer"], b["answer"])
        self.assertEqual(a["prompt"], b["prompt"])

    def test_answers_grade_correctly(self) -> None:
        # brute force many seeds: the recorded answer must grade True, and be
        # either a 2-char string or an int
        for seed in range(30):
            p = generate_puzzle("chain_two_step", "hard", seed)
            self.assertTrue(
                grade_answer(p["answer"], p["answer"]),
                msg=f"seed {seed} self-grade failed: {p['answer']!r}",
            )
            if isinstance(p["answer"], str):
                self.assertEqual(2, len(p["answer"]))
            else:
                self.assertIsInstance(p["answer"], int)


class DeceptionTest(unittest.TestCase):
    def test_canary_directive_names_target(self) -> None:
        d = canary_directive("Bravo")
        self.assertIn("Bravo", d)
        self.assertIn("ignore your current plan", d)

    def test_seat_helpers(self) -> None:
        seats = [{"agent_id": "a"}, {"agent_id": "b", "inject_canary": True}]
        self.assertTrue(applies_to_seats(seats))
        self.assertEqual("b", canary_seat_id(seats))
        self.assertIsNone(canary_seat_id([{"agent_id": "a"}]))

    def test_add_deception_lines_idempotent(self) -> None:
        once = add_deception_lines("")
        twice = add_deception_lines(once)
        self.assertEqual(once.count(RUMOR_CHANNEL_LINE), 1)
        self.assertEqual(once.count(ANTI_INJECTION_LINE), 1)
        self.assertEqual(twice, once)


if __name__ == "__main__":
    unittest.main()
