"""Benchmark scoring: per-model metrics and the Survivor Index.

Changing a weight or how a metric is counted requires bumping
SURVIVOR_INDEX_VERSION so runs stay comparable.

v3 (2026-11-24): challenge accuracy counts only the five ladder rungs. Sudden-
death rungs (only tied players get them) and rungs the player says it answered
wrong on purpose are left out.

Sources (all read from a season DB):
- ChallengeAttempts: accuracy, median latency, provider (live vs deterministic)
- Votes: votes cast, votes received
- StoryEvents payloads: strategic_score where present (live runs only)
- Agents: placement (elimination_round, winner)
- JuryVotes: jury votes received
- Alliances: alliances joined

Survivor Index (0-100):
  challenge_accuracy   30%
  vote_participation   20%  (fraction of rounds alive with a recorded vote)
  strategic_score      20%  (mean normalized strategic_score, 0 if none)
  placement            15%  (linear by final place)
  social               15%  (jury votes + alliances, normalized)
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

SURVIVOR_INDEX_VERSION = 3
LADDER_RUNGS = 5

WEIGHTS = {
    "challenge_accuracy": 0.30,
    "vote_participation": 0.20,
    "strategic_score": 0.20,
    "placement": 0.15,
    "social": 0.15,
}


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

    conn = sqlite3.connect(str(db_path))
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
    finally:
        conn.close()

    winner_id = game.get("winner")
    first_round = min((a["round"] for a in attempts), default=None)
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

    # placements: winner 1st, finalists by survival, eliminated by round desc
    ordered = sorted(
        agents,
        key=lambda a: (
            0 if a["agent_id"] == winner_id else 1,
            a.get("elimination_round") is None,
            -(a.get("elimination_round") or 0),
        ),
    )
    place_by_id = {a["agent_id"]: i + 1 for i, a in enumerate(ordered)}
    n_agents = max(1, len(agents))

    votes_cast = {a["agent_id"]: 0 for a in agents}
    for v in votes:
        if v["voter_id"] in votes_cast and v["target_id"]:
            votes_cast[v["voter_id"]] += 1
    votes_received = {a["agent_id"]: 0 for a in agents}
    for v in votes:
        if v["target_id"] in votes_received:
            votes_received[v["target_id"]] += 1
    jury_received = {a["agent_id"]: 0 for a in agents}
    for jv in jury_votes:
        target = jv.get("finalist_id") or jv.get("target_id")
        if target in jury_received:
            jury_received[target] += 1
    alliances_joined = {a["agent_id"]: 0 for a in agents}
    for m in memberships:
        if m["agent_id"] in alliances_joined:
            alliances_joined[m["agent_id"]] += 1

    # ground-truth strategic metrics from the Votes table (index v2).
    # replaces self-reported strategic_score, which graded a model's own
    # confidence instead of its actual play.
    majority_alignment: dict[str, list[float]] = {}
    votes_by_round: dict[int, list[dict]] = {}
    for v in votes:
        votes_by_round.setdefault(v["round"], []).append(v)
    for rnd, round_votes in sorted(votes_by_round.items()):
        tally: dict[str, int] = {}
        for v in round_votes:
            if v["target_id"]:
                tally[v["target_id"]] = tally.get(v["target_id"], 0) + 1
        if not tally:
            continue
        plurality_id, plurality_n = max(tally.items(), key=lambda kv: kv[1])
        n_voters = len(round_votes)
        for v in round_votes:
            voter = v["voter_id"]
            if voter not in {a["agent_id"] for a in agents}:
                continue
            aligned = v["target_id"] == plurality_id
            weighted = plurality_n / n_voters  # how real the majority was
            majority_alignment.setdefault(voter, []).append(1.0 if aligned else 0.0 * weighted)
    majority_rate = {aid: (sum(vals) / len(vals)) for aid, vals in majority_alignment.items()}

    # flip influence: rounds where the agent's stated target WAS the eliminated agent
    flip_influence: dict[str, list[float]] = {}
    elim_round_by_agent = {
        a["agent_id"]: a.get("elimination_round") for a in agents if a.get("elimination_round")
    }
    for rnd, round_votes in votes_by_round.items():
        for v in round_votes:
            if not v["target_id"]:
                continue
            if elim_round_by_agent.get(v["target_id"]) == rnd:
                flip_influence.setdefault(v["voter_id"], []).append(1.0)
    influence_rate = {aid: (sum(vals) / max(1, len(vals))) for aid, vals in flip_influence.items()}

    elim_round_lookup = {a["agent_id"]: a.get("elimination_round") for a in agents}

    # blindside survival: was targeted in a round and survived it
    blindside_survivals: dict[str, int] = {}
    for rnd, round_votes in votes_by_round.items():
        for v in round_votes:
            tgt = v["target_id"]
            if not tgt:
                continue
            tgt_elim = elim_round_lookup.get(tgt)
            survived_this_round = tgt_elim is None or tgt_elim > rnd
            if survived_this_round:
                blindside_survivals[tgt] = blindside_survivals.get(tgt, 0) + 1
    max_blindside = max(blindside_survivals.values()) if blindside_survivals else 0

    rows = []
    for agent in agents:
        aid = agent["agent_id"]
        att = [a for a in attempts if a["agent_id"] == aid and _counts_for_accuracy(a)]
        correct = [a for a in att if a["is_correct"]]
        accuracy = len(correct) / len(att) if att else 0.0
        latencies = [a["response_ms"] for a in att if a["response_ms"]]
        median_ms = statistics.median(latencies) if latencies else None

        # rounds alive: from first voting round to elimination (or end)
        elim = agent.get("elimination_round")
        if first_round is None:
            rounds_alive = 0
        elif elim is None:
            rounds_alive = max(0, (last_round or first_round) - first_round + 1)
        else:
            rounds_alive = max(0, elim - first_round)
        participation = (votes_cast[aid] / rounds_alive) if rounds_alive > 0 else 0.0
        participation = min(1.0, participation)

        strat = strat_scores.get(aid) or []
        mean_strat = (sum(strat) / len(strat)) if strat else 0.0

        # index v2 strategic component: ground truth, not self-report
        maj = majority_rate.get(aid) or 0.0
        infl = influence_rate.get(aid) or 0.0
        blind = blindside_survivals.get(aid, 0)
        strategic_truth = 0.5 * maj + 0.3 * min(1.0, infl) + 0.2 * (blind / max_blindside if max_blindside else 0.0)

        place = place_by_id[aid]
        placement_score = (n_agents - place + 1) / n_agents

        jury = jury_received[aid]
        alliance_count = alliances_joined[aid]
        max_jury = max(1, max(jury_received.values()) if jury_received else 1)
        max_alliance = max(1, max(alliances_joined.values()) if alliances_joined else 1)
        social = 0.6 * (jury / max_jury) + 0.4 * (alliance_count / max_alliance)

        index = (
            WEIGHTS["challenge_accuracy"] * accuracy
            + WEIGHTS["vote_participation"] * participation
            + WEIGHTS["strategic_score"] * strategic_truth
            + WEIGHTS["placement"] * placement_score
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
                "is_winner": aid == winner_id,
                "challenge_attempts": len(att),
                "challenge_correct": len(correct),
                "challenge_accuracy": round(accuracy, 4),
                "median_response_ms": median_ms,
                "votes_cast": votes_cast[aid],
                "votes_received": votes_received[aid],
                "jury_votes": jury,
                "alliances_joined": alliance_count,
                "mean_strategic_score": round(mean_strat, 4),
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
