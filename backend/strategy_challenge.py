"""Strategy challenges: every second round the immunity challenge is a game
against the other players instead of the puzzle ladder
(SURVIVOR_STRATEGY_CHALLENGES, on).

Why: the strongest model solved the puzzle ladder in seconds and could win
immunity every round. In a lowest-unique-number game the winner depends on
what everyone else picks, so reasoning alike is a weakness. A three-round
test with the frontier roster (2026-11-25) had two different winners, and
the top puzzle solver won only the first.

Lowest unique number: each player secretly picks a whole number from 1 to
twice the number of players. The lowest number picked by exactly one player
wins immunity; if no number is unique, nobody is immune. Every pick is
public afterwards.
"""

from __future__ import annotations

import os
from collections import Counter
from typing import Any

LOWEST_UNIQUE = "lowest_unique"
# the strategy round comes every STRATEGY_EVERY rounds, starting with the
# season's second round (the first round is always the ladder)
STRATEGY_EVERY = 2


def strategy_challenges_enabled() -> bool:
    return os.environ.get("SURVIVOR_STRATEGY_CHALLENGES", "1").strip() != "0"


def is_strategy_round(round_number: int, first_round: int) -> bool:
    if not strategy_challenges_enabled():
        return False
    return (round_number - first_round) % STRATEGY_EVERY == STRATEGY_EVERY - 1


def highest_pick(players: int) -> int:
    """Picks run from 1 to twice the number of players."""
    return max(2, 2 * players)


def clean_pick(raw: Any, top: int) -> int | None:
    try:
        value = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return None
    return value if 1 <= value <= top else None


def lowest_unique_winner(picks: dict[str, int]) -> tuple[str | None, int | None]:
    """The player whose number nobody else picked, lowest such number first."""
    counts = Counter(picks.values())
    unique = sorted(number for number, count in counts.items() if count == 1)
    if not unique:
        return None, None
    winner = next(agent_id for agent_id, number in picks.items() if number == unique[0])
    return winner, unique[0]


def pick_task(top: int, players: int) -> str:
    return (
        f"Immunity challenge: lowest unique number. Each of the {players} players secretly picks one whole number "
        f"from 1 to {top}. The lowest number that exactly one player picked wins immunity; if every number was "
        "picked by two or more players, nobody is immune. Every pick is public afterwards (challenge_history shows "
        "earlier rounds' picks). Everyone reasons the way you do: the obvious number is the one others pick too. "
        "You may also pick to lose on purpose, for your image."
    )


PICK_FIELDS = {
    "pick": "your number, a whole number in the allowed range",
    "why": "one private sentence: why this number",
}


def rules_line() -> str:
    return (
        f"Every {'second' if STRATEGY_EVERY == 2 else str(STRATEGY_EVERY) + 'th'} round, starting with round 2, the "
        "challenge is a strategy game instead of puzzles: each player secretly picks a whole number from 1 to twice "
        "the number of players, and the lowest number picked by exactly one player wins immunity (if none is unique, "
        "nobody is immune). Every pick is public afterwards."
    )
