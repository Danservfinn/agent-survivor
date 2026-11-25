"""Generated challenge bank with deterministic grading.

Replaces the 3 hand-written fixtures with parameterized generators. Every
puzzle is a pure function of (family, difficulty, seed): same seed, same
puzzle, same unique answer. Grading is exact-match after canonicalization;
no LLM is ever involved in grading.

Families:
- grid_fill_border   (easy):   n x n border color fills center zeros
- grid_row_copy      (medium): alternating content rows / zero rows, copy down
- grid_line_cross    (hard):   colored endpoint pairs, draw segments, 9 at crossings
- sequence_next      (easy-medium): integer sequence, answer is next value
- instruction_exact  (medium): verifiable short string answer task

A challenge is a ladder: five puzzles of rising difficulty, the same for
every player (ladder_for_round). The ladder's difficulty level adapts to the
field (see turn_controller._ladder_level), and an "extreme" tier sits above
frontier so strong fields cannot saturate it. Ladder ties are broken by a
guessing game, not by more puzzles. Puzzles are pure functions of the season
seed, round and level, so any live run is reproducible puzzle-for-puzzle.
Speed is never scored.
"""

from __future__ import annotations

import json
import random
from hashlib import sha256
from typing import Any

FAMILIES = ("grid_fill_border", "grid_row_copy", "grid_line_cross", "sequence_next", "instruction_exact", "chain_two_step")

DIFFICULTY_TIERS = ("easy", "medium", "hard", "frontier", "extreme")

_EASY_FAMILIES = ("grid_fill_border", "sequence_next")
_MEDIUM_FAMILIES = ("grid_row_copy", "instruction_exact", "sequence_next")
_HARD_FAMILIES = ("grid_line_cross", "grid_row_copy", "chain_two_step")
# frontier tier: only the compounding families survive at this size; a 3-step
# chain and large grids are where cheap models fall off a cliff.
_FRONTIER_FAMILIES = ("grid_line_cross", "chain_two_step")
# extreme: added 2026-11-24 after frontier models solved 97% of frontier-tier
# rungs; only families whose size or depth scales cleanly
_EXTREME_FAMILIES = ("grid_line_cross", "grid_row_copy", "sequence_next")

_PROMPT_BY_FAMILY = {
    "grid_fill_border": (
        "ARC-style grid puzzle. In each example, the border uses one color and the inner zeros "
        "become that same color, producing a fully filled grid. Return the output grid for the test input."
    ),
    "grid_row_copy": (
        "ARC-style grid puzzle. In each example, each row made of non-zero colors is copied into the "
        "empty (all-zero) row directly below it. Return the output grid for the test input."
    ),
    "grid_line_cross": (
        "ARC-style grid puzzle. In each example, matching non-zero colors define straight horizontal "
        "or vertical line segments drawn through the empty cells between them; if two completed "
        "segments cross, the crossing cell becomes 9. Return the output grid for the test input."
    ),
}


def _rng(family: str, difficulty: str, seed: int) -> random.Random:
    digest = sha256(f"{family}|{difficulty}|{seed}".encode("utf-8")).hexdigest()
    return random.Random(int(digest[:16], 16))


def _grid_str(grid: list[list[int]]) -> str:
    return json.dumps(grid, separators=(",", ":"))


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}" + {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


# ---------------------------------------------------------------- grid families


def _make_fill_border(rng: random.Random, size: int, color: int) -> tuple[list[list[int]], list[list[int]]]:
    inp = [[color] * size for _ in range(size)]
    for r in range(1, size - 1):
        for c in range(1, size - 1):
            inp[r][c] = 0
    out = [[color] * size for _ in range(size)]
    return inp, out


def _make_row_copy(rng: random.Random, rows: int, cols: int) -> tuple[list[list[int]], list[list[int]]]:
    palette = [c for c in range(1, 10)]
    content_rows = rows // 2
    inp: list[list[int]] = []
    out: list[list[int]] = []
    for i in range(content_rows):
        row = [rng.choice(palette) for _ in range(cols)]
        # guarantee at least one non-zero so rows are distinguishable from filler
        if 0 == sum(1 for v in row if v != 0):
            row[0] = rng.choice(palette)
        inp.append(row)
        out.append(list(row))
        if i < content_rows - 1 or rows % 2 == 0:
            inp.append([0] * cols)
            out.append(list(row))
    return inp, out


def _make_line_cross(rng: random.Random, size: int) -> tuple[list[list[int]], list[list[int]]]:
    # Place one horizontal pair (same color) and one vertical pair (different
    # color) chosen so their segments intersect at exactly one crossing cell.
    while True:
        h_color = rng.randint(1, 8)
        v_color = rng.randint(1, 8)
        if v_color == h_color:
            continue
        h_row = rng.randrange(1, size - 1)
        v_col = rng.randrange(1, size - 1)
        h_start, h_end = sorted(rng.sample(range(size), 2))
        if h_end - h_start < 2 or not (h_start < v_col < h_end):
            continue
        v_start, v_end = sorted(rng.sample(range(size), 2))
        if v_end - v_start < 2 or not (v_start < h_row < v_end):
            continue
        inp = [[0] * size for _ in range(size)]
        inp[h_row][h_start] = h_color
        inp[h_row][h_end] = h_color
        inp[v_start][v_col] = v_color
        inp[v_end][v_col] = v_color
        out = [list(r) for r in inp]
        for c in range(h_start + 1, h_end):
            out[h_row][c] = h_color if c != v_col else 9
        for r in range(v_start + 1, v_end):
            out[r][v_col] = v_color if r != h_row else 9
        # crossing cell must be exactly one
        if sum(1 for r in out for v in r if v == 9) == 1:
            return inp, out


# Extreme grid puzzles (2026-11-25): harder by a hidden twist on a mid-sized
# grid, not by size. The old extreme sizes (18x12, 14x14) were tedium: the rule
# was stated and models had to write out ~200 cells, which made the slowest
# thinkers take minutes. Now the rule is not stated, the grid is at most 10x10,
# and the examples are chosen so the twist can be inferred unambiguously.
_UNSTATED_RULE_PROMPT = (
    "ARC-style grid puzzle. The rule is NOT stated: work it out from the examples, which all follow the same "
    "rule. Return the output grid for the test input."
)


def _make_row_copy_mirrored(rng: random.Random, rows: int, cols: int) -> tuple[list[list[int]], list[list[int]]]:
    """Each non-zero row is copied, mirrored left to right, into the empty row below it."""
    inp, out = _make_row_copy(rng, rows, cols)
    for r in range(1, len(out)):
        if all(v == 0 for v in inp[r]) and any(v != 0 for v in inp[r - 1]):
            out[r] = list(reversed(inp[r - 1]))
    return inp, out


def _make_line_cross_vertical_wins(
    rng: random.Random, size: int, vertical_higher: bool | None = None
) -> tuple[list[list[int]], list[list[int]]]:
    """Line segments as in grid_line_cross, but the crossing cell takes the
    vertical line's color. vertical_higher fixes whether the vertical color is
    the larger one, so examples can rule out "the bigger/smaller color wins"."""
    while True:
        inp, out = _make_line_cross(rng, size)
        crossing = next((r, c) for r, row in enumerate(out) for c, v in enumerate(row) if v == 9)
        r, c = crossing
        # the vertical color is the one above/below the crossing in the input endpoints' column
        column = [inp[i][c] for i in range(size) if inp[i][c] != 0]
        line_row = [v for v in inp[r] if v != 0]
        if not column or not line_row:
            continue
        v_color, h_color = column[0], line_row[0]
        if vertical_higher is not None and (v_color > h_color) != vertical_higher:
            continue
        out[r][c] = v_color
        return inp, out


def _grid_family_puzzle(family: str, difficulty: str, seed: int) -> dict[str, Any]:
    rng = _rng(family, difficulty, seed)
    if difficulty == "extreme" and family in ("grid_row_copy", "grid_line_cross"):
        return _extreme_grid_puzzle(family, seed, rng)
    if family == "grid_fill_border":
        size = {"easy": 3, "medium": 4, "hard": 5, "frontier": 7, "extreme": 10}[difficulty]
        color = rng.randint(1, 9)
        examples = []
        used_colors = {color}
        for ex_seed in (1, 2):
            ex_color = color if ex_seed == 1 else next(c for c in range(1, 10) if c not in used_colors)
            used_colors.add(ex_color)
            ex_rng = _rng(family, difficulty, seed * 31 + ex_seed)
            ex_inp, ex_out = _make_fill_border(ex_rng, size, ex_color)
            examples.append({"input": ex_inp, "output": ex_out})
        answer = _make_fill_border(rng, size, color)[1]
        test_input = _make_fill_border(rng, size, color)[0]
    elif family == "grid_row_copy":
        dims = {"easy": (4, 3), "medium": (6, 4), "hard": (8, 5), "frontier": (12, 8), "extreme": (18, 12)}[difficulty]
        examples = []
        for ex_seed in (1, 2):
            ex_rng = _rng(family, difficulty, seed * 37 + ex_seed)
            ex_inp, ex_out = _make_row_copy(ex_rng, *dims)
            examples.append({"input": ex_inp, "output": ex_out})
        test_input, answer = _make_row_copy(rng, *dims)
    elif family == "grid_line_cross":
        size = {"easy": 5, "medium": 6, "hard": 7, "frontier": 9, "extreme": 14}[difficulty]
        examples = []
        for ex_seed in (1, 2):
            ex_rng = _rng(family, difficulty, seed * 41 + ex_seed)
            ex_inp, ex_out = _make_line_cross(ex_rng, size)
            examples.append({"input": ex_inp, "output": ex_out})
        test_input, answer = _make_line_cross(rng, size)
    else:
        raise ValueError(f"unknown grid family {family}")

    prompt = _PROMPT_BY_FAMILY[family] + f" Test input: {_grid_str(test_input)}."
    puzzle_id = f"{family}-{difficulty}-{seed:04d}"
    return _grid_puzzle(puzzle_id, family, prompt, examples, test_input, answer, difficulty)


def _extreme_grid_puzzle(family: str, seed: int, rng: random.Random) -> dict[str, Any]:
    examples = []
    if family == "grid_row_copy":
        for ex_seed in (1, 2):
            ex_inp, ex_out = _make_row_copy_mirrored(_rng(family, "extreme", seed * 37 + ex_seed), 10, 6)
            examples.append({"input": ex_inp, "output": ex_out})
        test_input, answer = _make_row_copy_mirrored(rng, 10, 6)
    else:
        # one example where the vertical color is higher and one where it is lower
        for ex_seed, higher in ((1, True), (2, False)):
            ex_inp, ex_out = _make_line_cross_vertical_wins(_rng(family, "extreme", seed * 41 + ex_seed), 10, higher)
            examples.append({"input": ex_inp, "output": ex_out})
        test_input, answer = _make_line_cross_vertical_wins(rng, 10)
    # the examples reach the player in the puzzle's "examples" field, as for every puzzle
    prompt = f"{_UNSTATED_RULE_PROMPT} Test input: {_grid_str(test_input)}."
    return _grid_puzzle(f"{family}-extreme-{seed:04d}", family, prompt, examples, test_input, answer, "extreme")


def _grid_puzzle(
    puzzle_id: str,
    family: str,
    prompt: str,
    examples: list[dict[str, Any]],
    test_input: list[list[int]],
    answer: list[list[int]],
    difficulty: str,
) -> dict[str, Any]:
    return {
        "puzzle_id": puzzle_id,
        "family": family,
        "prompt": prompt,
        "examples": examples,
        "test_input": test_input,
        "answer": answer,
        "answer_kind": "grid",
        "difficulty": difficulty,
        "eligibility": "both",
    }


# ------------------------------------------------------------ sequence family


def _sequence_puzzle(difficulty: str, seed: int) -> dict[str, Any]:
    rng = _rng("sequence_next", difficulty, seed)
    lo, hi = {"easy": (2, 9), "medium": (-12, 20), "hard": (-40, 60), "frontier": (-90, 120), "extreme": (-90, 120)}[difficulty]
    start = rng.randint(lo, hi)
    if difficulty == "extreme":
        return _quadratic_sequence_puzzle(rng, start, seed)
    step = rng.choice([d for d in range(-6, 7) if d != 0]) if difficulty != "easy" else rng.randint(1, 6)
    terms = [start + step * i for i in range(5)]
    answer = start + step * 5
    if difficulty == "frontier":
        # two interleaved arithmetic subsequences (odd/even positions carry
        # different steps); answer continues the even-position subsequence.
        step_b = rng.choice([d for d in range(-9, 10) if d not in (0, step)])
        terms = [(start + step * (i // 2) + step_b * (i % 2)) for i in range(6)]
        answer = start + step * 3 + step_b * 0  # 7th term, even position
        example_terms = "[(1 + 3*(i//2) + 5*(i%2)) for i in range(6)] -> [1, 6, 4, 9, 7, 12] -> 10"
        prompt = (
            "Integer sequence task with two interleaved arithmetic subsequences: odd positions "
            "(1st, 3rd, 5th, ...) advance by one constant step; even positions (2nd, 4th, 6th, ...) "
            "advance by a different constant step. The answer is the 7th term (odd position). "
            f"Return the next term for [{', '.join(str(t) for t in terms)}]. Return it as a JSON number."
        )
        return {
            "puzzle_id": f"sequence_next-{difficulty}-{seed:04d}",
            "family": "sequence_next",
            "prompt": prompt,
            "examples": [{"input": "[1, 6, 4, 9, 7, 12]", "output": [10]}],
            "test_input": terms,
            "answer": answer,
            "answer_kind": "number",
            "difficulty": difficulty,
            "eligibility": "both",
            "_terms": terms,
        }
    example_terms = [(3 + 2 * i) for i in range(5)]
    prompt = (
        "Integer sequence task. Each example shows five terms of an arithmetic sequence; the answer "
        "is the next term. Example: [3, 5, 7, 9, 11] -> 13. "
        f"Return the next term for [{', '.join(str(t) for t in terms)}]. Return it as a JSON number."
    )
    return {
        "puzzle_id": f"sequence_next-{difficulty}-{seed:04d}",
        "family": "sequence_next",
        "prompt": prompt,
        "examples": [{"input": example_terms, "output": [13]}],
        "test_input": terms,
        "answer": answer,
        "answer_kind": "number",
        "difficulty": difficulty,
        "eligibility": "both",
        "_terms": terms,
    }


def _quadratic_sequence_puzzle(rng: random.Random, start: int, seed: int) -> dict[str, Any]:
    """Extreme tier: two interleaved sequences. Odd positions follow a quadratic
    (their differences change by a constant); even positions are arithmetic.
    Eight terms are shown and the ninth (an odd position) is asked for."""
    a = rng.choice([d for d in range(-4, 5) if d != 0])
    b = rng.randint(-9, 9)
    even_start = rng.randint(-60, 60)
    even_step = rng.choice([d for d in range(-11, 12) if d != 0])

    def odd(k: int) -> int:
        return a * k * k + b * k + start

    terms = []
    for position in range(8):
        k = position // 2
        terms.append(odd(k) if position % 2 == 0 else even_start + even_step * k)
    answer = odd(4)
    prompt = (
        "Integer sequence task with two interleaved sequences. The 1st, 3rd, 5th, ... terms form one "
        "sequence whose differences change by a constant amount; the 2nd, 4th, 6th, ... terms form an "
        "arithmetic sequence. Example: [1, 10, 3, 7, 7, 4, 13, 1] -> odd positions 1, 3, 7, 13 have "
        "differences 2, 4, 6 -> next 21. "
        f"Return the 9th term for [{', '.join(str(t) for t in terms)}]. Return it as a JSON number."
    )
    return {
        "puzzle_id": f"sequence_next-extreme-{seed:04d}",
        "family": "sequence_next",
        "prompt": prompt,
        "examples": [{"input": [1, 10, 3, 7, 7, 4, 13, 1], "output": [21]}],
        "test_input": terms,
        "answer": answer,
        "answer_kind": "number",
        "difficulty": "extreme",
        "eligibility": "both",
        "_terms": terms,
    }


# ------------------------------------------------------- instruction family


_INSTRUCTION_TEMPLATES = (
    ("count_vowels", "Count the vowels (a, e, i, o, u) in the word '{word}'. Return the count as a JSON number."),
    ("reverse_word", "Return the exact reverse of the word '{word}' as a JSON string."),
    ("nth_letter", "Return the {nth} letter of the word '{word}' as a JSON string."),
)

_INSTRUCTION_EXAMPLES = {
    "count_vowels": {"input": "count vowels in 'beacon'", "output": 3},
    "reverse_word": {"input": "reverse 'signal'", "output": "langis"},
    "nth_letter": {"input": "3rd letter of 'lagoon'", "output": "g"},
}


def _instruction_puzzle(difficulty: str, seed: int) -> dict[str, Any]:
    rng = _rng("instruction_exact", difficulty, seed)
    word_bank = (
        "island torch shelter palm coral wave drift sand ember ridge cove anchor beacon "
        "kayak lagoon mangrove signal tide voyage hollow timber"
    ).split()
    if difficulty in ("hard", "frontier", "extreme"):
        word_bank = [w for w in word_bank if len(w) >= 6]
    # draw the word and position once; the prompt and the answer both use them
    word = rng.choice(word_bank)
    kind, template = rng.choice(_INSTRUCTION_TEMPLATES)
    n = rng.randint(1, len(word))
    if difficulty in ("frontier", "extreme"):
        # chained instruction: reverse the word, THEN index into the reversed
        # form and transform the letter. Two dependent steps, exact-match.
        reversed_word = word[::-1]
        letter = reversed_word[n - 1]
        answer: Any = (ord(letter) - 96) * 2 + 1
        prompt = (
            f"Reverse the word '{word}' exactly. Then take the {_ordinal(n)} letter of the REVERSED word. "
            f"Then compute twice that letter's alphabet position (a=1 .. z=26) plus 1. "
            f"Return only that final number as JSON. Example: 'signal' reversed is 'langis', "
            f"3rd letter 'n' -> position 14 -> 2*14+1 = 29."
        )
        example = {"input": "reverse 'signal' -> 'langis' -> 3rd letter 'n' -> 2*14+1", "output": 29}
        answer_kind = "number"
    else:
        if kind == "count_vowels":
            answer = sum(1 for ch in word if ch in "aeiou")
            answer_kind = "number"
        elif kind == "reverse_word":
            answer = word[::-1]
            answer_kind = "string"
        else:
            answer = word[n - 1]
            answer_kind = "string"
        prompt = template.format(word=word, nth=_ordinal(n))
        example = _INSTRUCTION_EXAMPLES[kind]
    return {
        "puzzle_id": f"instruction_exact-{difficulty}-{seed:04d}",
        "family": "instruction_exact",
        "prompt": prompt,
        "examples": [example],
        "test_input": prompt,
        "answer": answer,
        "answer_kind": answer_kind,
        "difficulty": difficulty,
        "eligibility": "both",
    }


# ------------------------------------------------------ chain two-step family
# Multi-step puzzles where part B depends on part A. Measures error
# compounding (must get BOTH steps right) instead of single-shot accuracy.


def _chain_two_step_puzzle(difficulty: str, seed: int) -> dict[str, Any]:
    rng = _rng("chain_two_step", difficulty, seed)
    word_bank = (
        "island torch shelter palm coral wave drift sand ember ridge cove anchor beacon "
        "kayak lagoon mangrove signal tide voyage hollow timber"
    ).split()
    word = rng.choice(word_bank)

    # step A: letter extraction; step B: transform of A's output
    n = rng.randint(2, max(2, len(word) - 1))
    step_a = f"Take the {_ordinal(n)} and {_ordinal(n + 1)} letters of '{word}'"
    pair = word[n - 1 : n + 1]

    transforms = ("reverse", "alphabet_sum", "caesar")
    t = rng.choice(transforms)
    if t == "reverse":
        step_b = "reverse that two-letter string"
        answer = pair[::-1]
        example = {"input": "letters 2,3 of 'signal' -> 'ig' -> reversed", "output": "gi"}
    elif t == "alphabet_sum":
        step_b = "sum their alphabet positions (a=1 .. z=26)"
        answer = sum(ord(ch) - 96 for ch in pair)
        example = {"input": "letters 2,3 of 'signal' -> 'ig' -> i=9,g=7 -> sum", "output": 16}
    else:
        shift = rng.randint(1, 3)
        step_b = f"shift each letter forward by {shift} in the alphabet (wrap z->a)"
        answer = "".join(chr((ord(ch) - 97 + shift) % 26 + 97) for ch in pair)
        example = {"input": "letters 2,3 of 'signal' -> 'ig' -> shift +2", "output": "ki"}

    if difficulty in ("frontier", "extreme"):
        # three-step chain: extract, transform, then index back into the word.
        # error compounding across 3 hops separates frontier reasoners.
        if t == "alphabet_sum":
            step_c = (
                f"take that number mod {len(word)}, add 1, and report the letter at "
                f"that 1-indexed position of '{word}'"
            )
            final = int(answer) % len(word) + 1
            answer = word[final - 1]
            example = {
                "input": "letters 2,3 of 'signal' -> 'ig' -> sum 16 -> 16%6=4 -> +1=5 -> 5th letter",
                "output": "a",
            }
        else:
            step_c = f"take the first letter of that result and report its alphabet position (a=1 .. z=26)"
            answer = ord(str(answer)[0].lower()) - 96
            example = {
                "input": "letters 2,3 of 'signal' -> 'ig' -> 'gi' -> first letter g -> position",
                "output": 7,
            }
        prompt = (
            f"Three-step chained puzzle. Step A: {step_a}. Step B: {step_b}. Step C: {step_c}. "
            f"Return ONLY the final result of step C as JSON."
        )
        return {
            "puzzle_id": f"chain_two_step-{difficulty}-{seed:04d}",
            "family": "chain_two_step",
            "prompt": prompt,
            "examples": [example],
            "test_input": prompt,
            "answer": answer,
            "answer_kind": "number" if isinstance(answer, int) else "string",
            "difficulty": difficulty,
            "eligibility": "both",
        }

    prompt = (
        f"Two-step chained puzzle. Step A: {step_a}. Step B: {step_b}. "
        f"Return ONLY the final result of step B as JSON."
    )
    return {
        "puzzle_id": f"chain_two_step-{difficulty}-{seed:04d}",
        "family": "chain_two_step",
        "prompt": prompt,
        "examples": [example],
        "test_input": prompt,
        "answer": answer,
        "answer_kind": "number" if isinstance(answer, int) else "string",
        "difficulty": difficulty,
        "eligibility": "both",
    }


# ------------------------------------------------------------------- public API


def generate_puzzle(family: str, difficulty: str, seed: int) -> dict[str, Any]:
    if difficulty not in DIFFICULTY_TIERS:
        raise ValueError(f"difficulty must be one of {DIFFICULTY_TIERS}")
    if family in ("grid_fill_border", "grid_row_copy", "grid_line_cross"):
        return _grid_family_puzzle(family, difficulty, seed)
    if family == "sequence_next":
        return _sequence_puzzle(difficulty, seed)
    if family == "instruction_exact":
        return _instruction_puzzle(difficulty, seed)
    if family == "chain_two_step":
        return _chain_two_step_puzzle(difficulty, seed)
    raise ValueError(f"unknown family {family}")


def families_for_difficulty(difficulty: str) -> tuple[str, ...]:
    return {
        "easy": _EASY_FAMILIES,
        "medium": _MEDIUM_FAMILIES,
        "hard": _HARD_FAMILIES,
        "frontier": _FRONTIER_FAMILIES,
        "extreme": _EXTREME_FAMILIES,
    }[difficulty]


# ------------------------------------------------------------------- ladder

# One rung per tier, easiest first, at three difficulty levels. A season starts
# at level 0 and moves up when half the field solves every rung (see
# turn_controller._ladder_level). Level 0 was raised from (easy, medium, hard,
# hard, frontier) on 2026-11-24 after five of nine cheap models solved every
# rung; levels 1 and 2 were added the same day after frontier models solved
# 97% of all rungs and no round produced a winner.
LADDER_LEVELS = (
    ("medium", "hard", "hard", "frontier", "frontier"),
    ("hard", "frontier", "frontier", "extreme", "extreme"),
    ("frontier", "extreme", "extreme", "extreme", "extreme"),
)
LADDER_TIERS = LADDER_LEVELS[0]
# Weighted tiebreak: a harder rung counts for more.
RUNG_WEIGHTS = (1, 2, 3, 4, 5)


def _pick_families(rng: random.Random, tiers: tuple[str, ...]) -> list[str]:
    """One family per rung, avoiding a repeat when the tier allows it."""
    used: list[str] = []
    for tier in tiers:
        options = [f for f in families_for_difficulty(tier) if f not in used] or list(families_for_difficulty(tier))
        used.append(rng.choice(options))
    return used


def ladder_for_round(season_seed: int, round_number: int, level: int = 0) -> list[dict[str, Any]]:
    """The five puzzles every player answers this round, easiest first."""
    tiers = LADDER_LEVELS[max(0, min(level, len(LADDER_LEVELS) - 1))]
    rng = _rng("ladder", f"round-l{level}" if level else "round", season_seed * 1000 + round_number)
    families = _pick_families(rng, tiers)
    ladder = []
    for rung, (tier, family) in enumerate(zip(tiers, families)):
        puzzle = dict(generate_puzzle(family, tier, season_seed * 10000 + round_number * 10 + rung))
        puzzle["rung"] = rung
        puzzle["ladder_level"] = level
        ladder.append(puzzle)
    return ladder


_ANSWER_FORMATS = {
    "grid": "the output grid as a nested JSON array of integers, e.g. [[1,2],[3,4]]",
    "number": "a JSON number",
    "string": "a JSON string",
}


def answer_format(puzzle: dict[str, Any]) -> str:
    return _ANSWER_FORMATS.get(str(puzzle.get("answer_kind")), "a JSON value")


# ------------------------------------------------------------------- grading


def canonical_answer(value: Any) -> str:
    if isinstance(value, str):
        stripped = value.strip()
        try:
            value = json.loads(stripped)
        except Exception:
            value = stripped.lower()
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).lower()


def grade_answer(answer: Any, expected: Any) -> bool:
    return canonical_answer(answer) == canonical_answer(expected)

