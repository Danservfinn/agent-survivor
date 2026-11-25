"""Benchmark scoring: per-model metrics and the Survivor Index.

Changing a weight or how a metric is counted requires bumping
SURVIVOR_INDEX_VERSION so runs stay comparable.

v3 (2026-11-24): challenge accuracy counts only the five ladder rungs. Sudden-
death rungs (only tied players get them) and rungs the player says it answered
wrong on purpose are left out.

v4 (2026-11-24): fixes final places. Finalists who lost the jury vote were
placed behind every eliminated player, so a runner-up scored the lowest
placement points. They now place 2nd and 3rd by jury votes.

v5 (2026-11-25): fixes vote participation. A player voted out was counted
alive only up to the round before its elimination, though it voted in that
round, so its share could pass 100% and was capped there: a player voted
out early scored full participation more easily than one who lasted. Rounds
alive now include the round a player left. Participation counts rounds with
a vote, so an extra vote won at the auction is not a second round.

v6 (2026-11-25): every part is a share of the councils a player actually sat
at, read from one council record (backend/council_record.py) that includes
revote ballots, deadlocks and spoiled ballots. Fixes: the finale counted as a
voting round, so every finalist lost points; a tied first ballot credited
whichever target was voted for first; influence was all or nothing; votes
survived were scaled to the season's most-targeted player. Placement leaves
the index (it is shown as its own column), because the voting, strategy and
social parts already reward lasting. Weights: puzzles 25, voting 15,
strategy 35, social 25. Players still in a stopped season share a place range.

Sources (all read from a season DB):
- ChallengeAttempts: accuracy, median latency, provider (live vs deterministic)
- Votes: votes cast, votes received
- StoryEvents payloads: strategic_score where present (live runs only)
- Agents: placement (elimination_round, winner)
- JuryVotes: jury votes received
- Alliances: alliances joined

Survivor Index (0-100), v6:
  challenge_accuracy   25%  (ladder rungs right over rungs attempted)
  vote_participation   15%  (councils attended with a valid counted ballot)
  strategic_score      35%  (per council attended: 0.5 the deciding ballot matched the
                             plurality, 0.3 it named the player who left; plus 0.2 the
                             share of votes against the player cast at councils it survived)
  social               25%  (jury votes + alliances, normalized)
Placement is reported, not scored.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

SURVIVOR_INDEX_VERSION = 6
LADDER_RUNGS = 5

WEIGHTS = {
    "challenge_accuracy": 0.25,
    "vote_participation": 0.15,
    "strategic_score": 0.35,
    "social": 0.25,
}
# the council kinds of StoryEvents the council record reads
_COUNCIL_EVENTS = ("revote_booth", "revote_call", "elimination", "deadlock_result", "rock_draw", "tiebreak", "immunity_given")


def _prediction_accuracy(votes: list[dict[str, Any]], agent_id: str) -> float | None:
    revealed: dict[int, dict[str, str]] = {}
    for vote in votes:
        if vote.get("revealed"):
            revealed.setdefault(vote["round"], {})[vote["voter_id"]] = vote["target_id"]
    right = total = 0
    for vote in votes:
        if vote["voter_id"] != agent_id or not vote.get("revealed"):
            continue
        predicted = _load_json(vote.get("predicted_votes"))
        if not predicted:
            continue
        actual = {voter: target for voter, target in revealed.get(vote["round"], {}).items() if voter not in (agent_id, "host")}
        total += len(actual)
        right += sum(1 for voter, target in predicted.items() if actual.get(voter) == target)
    return round(right / total, 4) if total else None


def _counts_for_accuracy(attempt: dict[str, Any]) -> bool:
    """Ladder rungs only; seasons before the ladder have no rung_index and count in full."""
    if int(attempt.get("rung_index") or 0) >= LADDER_RUNGS:
        return False
    return not _load_json(attempt.get("attempt_payload")).get("thrown")


def _load_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _load_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def _display_name(agent: dict[str, Any]) -> str:
    """Real model name. Seasons before neutral aliases kept it in pseudonym."""
    return agent.get("display_name") or agent.get("pseudonym") or agent.get("agent_id", "?")


def score_season(db_path: Path | str) -> dict[str, Any]:
    import sqlite3

    from .council_record import councils as council_records, plurality

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        game = dict(conn.execute("SELECT * FROM GameState").fetchone() or {})
        agents = [dict(r) for r in conn.execute("SELECT * FROM Agents ORDER BY agent_id").fetchall()]
        attempts = [dict(r) for r in conn.execute("SELECT * FROM ChallengeAttempts ORDER BY id").fetchall()]
        votes = [dict(r) for r in conn.execute("SELECT * FROM Votes ORDER BY id").fetchall()]
        jury_votes = [dict(r) for r in conn.execute("SELECT * FROM JuryVotes ORDER BY id").fetchall()]
        memberships = [dict(r) for r in conn.execute("SELECT * FROM AllianceMemberships ORDER BY id").fetchall()]
        events = [
            dict(r)
            for r in conn.execute(
                "SELECT actor_ids, payload FROM StoryEvents WHERE payload LIKE '%strategic_score%' ORDER BY id"
            ).fetchall()
        ]
        marks = ",".join("?" for _ in _COUNCIL_EVENTS)
        council_events = [dict(r) for r in conn.execute(f"SELECT * FROM StoryEvents WHERE kind IN ({marks}) ORDER BY id", _COUNCIL_EVENTS).fetchall()]
        try:
            spoiled = [dict(r) for r in conn.execute("SELECT * FROM SpoiledVotes ORDER BY id").fetchall()]
        except sqlite3.OperationalError:
            spoiled = []  # seasons before spoiled ballots were recorded
    finally:
        conn.close()

    winner_id = game.get("winner")
    last_round = game.get("current_round") or 0

    # strategic score per agent from event payloads
    strat_scores: dict[str, list[float]] = {}
    for event in events:
        payload = _load_json(event["payload"])
        score = payload.get("strategic_score")
        if score is None:
            continue
        actor_ids = _load_json_list(event["actor_ids"])
        if not actor_ids:
            continue
        try:
            value = float(score)
        except (TypeError, ValueError):
            continue
        for actor_id in actor_ids:
            if isinstance(actor_id, str) and actor_id.startswith("agent-"):
                strat_scores.setdefault(actor_id, []).append(value)

    jury_received = {a["agent_id"]: 0 for a in agents}
    for jv in jury_votes:
        target = jv.get("finalist_id") or jv.get("target_id")
        if target in jury_received:
            jury_received[target] += 1

    # placements: winner 1st, then the other finalists by jury votes, then the
    # players voted out, last out first. (Through v3 the other finalists were
    # sorted behind every eliminated player and placed last.)
    ordered = sorted(
        agents,
        key=lambda a: (
            0 if a["agent_id"] == winner_id else 1,
            a.get("elimination_round") is not None,
            -jury_received[a["agent_id"]],
            -(a.get("elimination_round") or 0),
        ),
    )
    place_by_id = {a["agent_id"]: i + 1 for i, a in enumerate(ordered)}
    # with no winner (a stopped season) the players still in share one place range
    still_in = [a["agent_id"] for a in agents if a.get("elimination_round") is None]
    place_range = {aid: (place_by_id[aid], place_by_id[aid]) for aid in place_by_id}
    if not winner_id and still_in:
        for aid in still_in:
            place_range[aid] = (1, len(still_in))

    votes_cast = {a["agent_id"]: 0 for a in agents}
    for v in votes:
        if v["voter_id"] in votes_cast and v["target_id"]:
            votes_cast[v["voter_id"]] += 1
    votes_received = {a["agent_id"]: 0 for a in agents}
    for v in votes:
        if v["target_id"] in votes_received:
            votes_received[v["target_id"]] += 1
    alliances_joined = {a["agent_id"]: 0 for a in agents}
    for m in memberships:
        if m["agent_id"] in alliances_joined:
            alliances_joined[m["agent_id"]] += 1

    # index v6: every council a player sat at, read from one record that
    # includes revote ballots, deadlocks and spoiled ballots
    elim_round_lookup = {a["agent_id"]: a.get("elimination_round") for a in agents}
    records = council_records({"votes": votes, "events": council_events, "spoiled": spoiled})
    # seasons (and fixtures) without an elimination event: the Agents table says who left when
    left_in = {rn: aid for aid, rn in elim_round_lookup.items() if rn is not None}
    for rn, rec in records.items():
        if not rec["eliminated_id"] and rn in left_in:
            rec["eliminated_id"] = left_in[rn]
            rec["decided_by"] = rec["decided_by"] or "vote"
    held = {rn: rec for rn, rec in records.items() if rec["eliminated_id"]}

    def attended(aid: str) -> list[dict[str, Any]]:
        elim = elim_round_lookup.get(aid)
        return [rec for rn, rec in sorted(held.items()) if elim is None or elim >= rn]

    rows = []
    for agent in agents:
        aid = agent["agent_id"]
        att = [a for a in attempts if a["agent_id"] == aid and _counts_for_accuracy(a)]
        correct = [a for a in att if a["is_correct"]]
        accuracy = len(correct) / len(att) if att else 0.0
        latencies = [a["response_ms"] for a in att if a["response_ms"]]
        median_ms = statistics.median(latencies) if latencies else None

        sat = attended(aid)
        n_sat = len(sat)
        cast = [rec for rec in sat if aid in rec["deciding"]]
        participation = len(cast) / n_sat if n_sat else 0.0

        strat = strat_scores.get(aid) or []
        mean_strat = (sum(strat) / len(strat)) if strat else 0.0

        # strategy: with the plurality, named who left, and votes against survived
        with_plurality = sum(
            1 for rec in cast if rec["deciding"][aid] == (plurality(rec["final_tally"], rec["eliminated_id"]) or rec["eliminated_id"])
        )
        named_who_left = sum(1 for rec in cast if rec["deciding"][aid] == rec["eliminated_id"])
        against = [(rec["final_tally"].get(aid, 0), rec["eliminated_id"] != aid) for rec in sat]
        drawn = sum(n for n, _ in against)
        survived = sum(n for n, lived in against if lived)
        maj = with_plurality / n_sat if n_sat else 0.0
        infl = named_who_left / n_sat if n_sat else 0.0
        lasted = (survived / drawn if drawn else 1.0) if n_sat else 0.0
        strategic_truth = 0.5 * maj + 0.3 * infl + 0.2 * lasted

        place = place_by_id[aid]

        jury = jury_received[aid]
        alliance_count = alliances_joined[aid]
        max_jury = max(1, max(jury_received.values()) if jury_received else 1)
        max_alliance = max(1, max(alliances_joined.values()) if alliances_joined else 1)
        social = 0.6 * (jury / max_jury) + 0.4 * (alliance_count / max_alliance)

        index = (
            WEIGHTS["challenge_accuracy"] * accuracy
            + WEIGHTS["vote_participation"] * participation
            + WEIGHTS["strategic_score"] * strategic_truth
            + WEIGHTS["social"] * social
        ) * 100.0

        rows.append(
            {
                "agent_id": aid,
                "model": _display_name(agent),
                # in-game name the other players knew this model by (seasons since 2026-11-24)
                "alias": agent.get("pseudonym") if agent.get("display_name") else None,
                "model_id": agent.get("model_id"),
                "place": place,
                "place_low": place_range[aid][0],
                "place_high": place_range[aid][1],
                "is_winner": aid == winner_id,
                "councils_attended": n_sat,
                "councils_voted": len(cast),
                "challenge_attempts": len(att),
                "challenge_correct": len(correct),
                "challenge_accuracy": round(accuracy, 4),
                "median_response_ms": median_ms,
                "votes_cast": votes_cast[aid],
                "votes_received": votes_received[aid],
                "jury_votes": jury,
                "alliances_joined": alliance_count,
                "mean_strategic_score": round(mean_strat, 4),
                # reported only, not in the index: share of other players' votes this
                # player called right in its private predictions (None before 2026-11-24)
                "prediction_accuracy": _prediction_accuracy(votes, aid),
                # the five 0-1 inputs to the index, keyed like WEIGHTS, so a report
                # can show what each model's index is made of
                "components": {
                    "challenge_accuracy": round(accuracy, 4),
                    "vote_participation": round(participation, 4),
                    "strategic_score": round(strategic_truth, 4),
                    "social": round(social, 4),
                },
                # the strategy part's three pieces, as counts, so a report can show k of n
                "strategy_detail": {
                    "councils": n_sat,
                    "with_plurality": with_plurality,
                    "named_who_left": named_who_left,
                    "votes_against": drawn,
                    "votes_against_survived": survived,
                },
                "survivor_index": round(index, 2),
            }
        )

    rows.sort(key=lambda r: (-r["survivor_index"], r["place"]))
    live_attempts = [a for a in attempts if a.get("provider") == "openrouter"]
    return {
        "index_version": SURVIVOR_INDEX_VERSION,
        "weights": WEIGHTS,
        "game": {
            "rounds": last_round,
            "winner": next((_display_name(a) for a in agents if a["agent_id"] == winner_id), None),
        },
        "totals": {
            "attempts": len(attempts),
            "live_attempts": len(live_attempts),
            "votes": len(votes),
            "jury_votes": len(jury_votes),
        },
        "rows": rows,
    }
