"""The auction (SURVIVOR_ADVANTAGES): held once, at the start of the first
round with three players gone. Each player bids sealed amounts of its tokens
on three items. Highest bid wins an item; a tie means nobody gets it.

Tokens: TOKENS_PER_ROUND for every round the player has played, plus one per
puzzle it solved. They have no other use, so the whole game is how a player
splits them across the items.

Items:
- extra_vote: at one later council the winner's ballot counts twice
  (use_extra_vote on its vote). Public.
- idol: a hidden immunity idol. The table learns it was sold, not to whom.
- vote_peek: the winner privately sees who voted for whom at the last council.
"""

from __future__ import annotations

import json
from typing import Any

from .llm_config import advantages_enabled

TOKENS_PER_ROUND = 3
PLAYERS_GONE_BEFORE_AUCTION = 3
ITEMS = {
    "extra_vote": "an extra vote: at one later council of your choice your ballot counts twice (public)",
    "idol": "a hidden immunity idol: play it with your vote to cancel every vote against you (the table learns it was sold, not to whom)",
    "vote_peek": "a private look at who voted for whom at the last council",
}


def tokens(conn, agent_id: str, round_number: int, first_round: int) -> int:
    """Tokens a player holds: rounds played before this one, and puzzles solved."""
    played = max(0, round_number - first_round)
    solved = conn.execute(
        "SELECT COUNT(*) AS n FROM ChallengeAttempts WHERE agent_id = ? AND is_correct = 1 AND round < ?",
        (agent_id, round_number),
    ).fetchone()["n"]
    return TOKENS_PER_ROUND * played + int(solved)


def auction_round(conn) -> int | None:
    row = conn.execute("SELECT MIN(round) AS r FROM AuctionBids").fetchone()
    return int(row["r"]) if row and row["r"] is not None else None


def auction_due(conn, round_number: int, active_count: int) -> bool:
    """The auction runs in the first round that starts with three players gone
    (and stays in that round's step list once held)."""
    if not advantages_enabled():
        return False
    held = auction_round(conn)
    if held is not None:
        return held == round_number
    cast = conn.execute("SELECT COUNT(*) AS n FROM Agents").fetchone()["n"]
    return active_count <= cast - PLAYERS_GONE_BEFORE_AUCTION and active_count > 3


def clean_bids(raw: Any, budget: int) -> dict[str, int]:
    """Whole, non-negative bids on known items. Bids over the player's tokens
    in total are void (all of them), as a check that bounces."""
    bids: dict[str, int] = {}
    if isinstance(raw, dict):
        for item in ITEMS:
            try:
                value = int(float(raw.get(item) or 0))
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                bids[item] = value
    if sum(bids.values()) > budget:
        return {}
    return bids


def winners(all_bids: dict[str, dict[str, int]]) -> dict[str, tuple[str, int] | None]:
    """Each item to its single highest bidder, with the price; a tie for the
    top bid, or no bid, sells nothing."""
    result: dict[str, tuple[str, int] | None] = {}
    for item in ITEMS:
        offers = sorted(((bids.get(item, 0), agent) for agent, bids in all_bids.items() if bids.get(item, 0) > 0), reverse=True)
        if not offers or (len(offers) > 1 and offers[0][0] == offers[1][0]):
            result[item] = None
        else:
            result[item] = (offers[0][1], offers[0][0])
    return result


def holds(conn, agent_id: str, kind: str) -> bool:
    """An unused advantage of this kind."""
    return bool(
        conn.execute(
            "SELECT 1 FROM Advantages WHERE agent_id = ? AND kind = ? AND used_round IS NULL", (agent_id, kind)
        ).fetchone()
    )


def extra_votes_for_round(conn, round_number: int, voter_ids: list[str]) -> int:
    """Extra votes that may be read at this council: unused ones held by a
    voter, and ones used this round (so the step list does not shrink after
    a vote uses one)."""
    if not voter_ids:
        return 0
    marks = ",".join("?" for _ in voter_ids)
    return conn.execute(
        f"SELECT COUNT(*) AS n FROM Advantages WHERE kind = 'extra_vote' AND agent_id IN ({marks}) "
        "AND (used_round IS NULL OR used_round = ?)",
        (*voter_ids, round_number),
    ).fetchone()["n"]


def your_advantages(conn, agent_id: str, round_number: int, first_round: int) -> dict[str, Any] | None:
    """What a player sees about the auction: its tokens before it, and the
    advantages it holds after."""
    if not advantages_enabled():
        return None
    held = [
        {"advantage": row["kind"], "won_in_round": row["round_won"]}
        for row in conn.execute(
            "SELECT kind, round_won FROM Advantages WHERE agent_id = ? AND used_round IS NULL AND kind = 'extra_vote'",
            (agent_id,),
        )
    ]
    entry: dict[str, Any] = {}
    if auction_round(conn) is None:
        cast = conn.execute("SELECT COUNT(*) AS n FROM Agents").fetchone()["n"]
        entry["auction_tokens"] = tokens(conn, agent_id, round_number, first_round)
        entry["auction"] = (
            f"Once, when {cast - PLAYERS_GONE_BEFORE_AUCTION} players remain, everyone bids tokens in a sealed "
            f"auction for: {'; '.join(ITEMS.values())}. You earn {TOKENS_PER_ROUND} tokens a round and 1 per "
            "puzzle solved. Highest bid wins; a tie wins nothing."
        )
    if held:
        entry["held"] = held
    return entry or None


def bids_json(bids: dict[str, int]) -> str:
    return json.dumps(bids, sort_keys=True)
