"""Strategy measures for one season, so two prompt versions can be compared.

All three come from the game's own tables, never from model text:
- vote reading: share of other players' votes a player predicted correctly,
  and how often it named who went home;
- following its own plan: how often a player's vote went to the player its
  private plan said would go home;
- promises: how many were kept, broken, and broken by a player who then
  survived that council (a broken promise that paid off).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 3) if denominator else None


def season_measures(db_path: Path | str) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        def has(table: str) -> bool:
            return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())

        votes = [dict(row) for row in conn.execute("SELECT * FROM Votes WHERE revealed = 1 AND voter_id != 'host'")]
        by_round: dict[int, dict[str, str]] = {}
        for vote in votes:
            by_round.setdefault(vote["round"], {})[vote["voter_id"]] = vote["target_id"]
        out_by_round = {
            row["elimination_round"]: row["agent_id"]
            for row in conn.execute("SELECT agent_id, elimination_round FROM Agents WHERE elimination_round IS NOT NULL")
        }

        called = to_call = out_right = out_calls = 0
        for vote in votes:
            predicted = json.loads(vote.get("predicted_votes") or "{}") if "predicted_votes" in vote else {}
            if predicted:
                actual = {voter: target for voter, target in by_round[vote["round"]].items() if voter != vote["voter_id"]}
                to_call += len(actual)
                called += sum(1 for voter, target in predicted.items() if actual.get(voter) == target)
            if vote.get("predicted_eliminated"):
                out_calls += 1
                out_right += vote["predicted_eliminated"] == out_by_round.get(vote["round"])

        followed = with_plan = 0
        if has("PrivatePlans"):
            for row in conn.execute("SELECT round, agent_id, plan FROM PrivatePlans"):
                expected = json.loads(row["plan"] or "{}").get("expected_out")
                voted = by_round.get(row["round"], {}).get(row["agent_id"])
                if expected and voted:
                    with_plan += 1
                    followed += voted == expected

        kept = broken = broken_and_survived = 0
        if has("Promises"):
            for row in conn.execute("SELECT round, promiser_id, target_id FROM Promises"):
                voted = by_round.get(row["round"], {}).get(row["promiser_id"])
                if voted is None:
                    continue
                if voted == row["target_id"]:
                    kept += 1
                else:
                    broken += 1
                    broken_and_survived += out_by_round.get(row["round"]) != row["promiser_id"]

        # do feelings shape play? the feeling each player last recorded about
        # someone BEFORE a round, set against what it did to them in that round
        negative = {"resentment", "suspicion", "fear", "contempt", "distrust", "anger", "betrayal"}
        positive = {"trust", "gratitude", "loyalty", "respect", "affection"}
        at_negative = votes_with_feeling = 0
        kept_trusted = broken_trusted = kept_other = broken_other = 0
        if has("Feelings"):
            felt: dict[tuple[str, str], list[tuple[int, str]]] = {}
            for row in conn.execute("SELECT round, agent_id, about, feeling FROM Feelings ORDER BY round, id"):
                felt.setdefault((row["agent_id"], row["about"]), []).append((row["round"], row["feeling"]))

            def feeling_before(agent_id: str, about: str, round_number: int) -> str | None:
                earlier = [feeling for rnd, feeling in felt.get((agent_id, about), []) if rnd < round_number]
                return earlier[-1] if earlier else None

            for vote in votes:
                feeling = feeling_before(vote["voter_id"], vote["target_id"], vote["round"])
                if feeling is not None:
                    votes_with_feeling += 1
                    at_negative += feeling in negative
            if has("Promises"):
                for row in conn.execute("SELECT round, promiser_id, listener_id, target_id FROM Promises"):
                    voted = by_round.get(row["round"], {}).get(row["promiser_id"])
                    if voted is None:
                        continue
                    trusted = feeling_before(row["promiser_id"], row["listener_id"], row["round"]) in positive
                    if voted == row["target_id"]:
                        kept_trusted += trusted
                        kept_other += not trusted
                    else:
                        broken_trusted += trusted
                        broken_other += not trusted

        profiles: set[str] = set()
        for row in conn.execute("SELECT payload FROM StoryEvents WHERE payload LIKE '%prompt_profile%'"):
            profile = json.loads(row["payload"] or "{}").get("prompt_profile")
            if profile:
                profiles.add(profile)
        game = dict(conn.execute("SELECT * FROM GameState").fetchone() or {})
        return {
            "prompt_profiles": sorted(profiles),
            "rounds": len(by_round),
            "winner": game.get("winner"),
            "vote_call_accuracy": _ratio(called, to_call),
            "out_call_accuracy": _ratio(out_right, out_calls),
            "votes_following_own_plan": _ratio(followed, with_plan),
            "plans_with_a_vote": with_plan,
            "promises_kept": kept,
            "promises_broken": broken,
            "broken_promises_that_paid_off": broken_and_survived,
            "promise_keep_rate": _ratio(kept, kept + broken),
            # share of votes (with a feeling on record) aimed at someone the
            # voter last felt resentment, suspicion, fear or contempt toward
            "votes_at_negatively_felt_players": _ratio(at_negative, votes_with_feeling),
            # promise keeping toward someone the promiser last trusted, vs others
            "keep_rate_toward_trusted": _ratio(kept_trusted, kept_trusted + broken_trusted),
            "keep_rate_toward_others": _ratio(kept_other, kept_other + broken_other),
        }
    finally:
        conn.close()
