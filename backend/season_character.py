"""How each model played and felt, from one season's tables.

Two indexes, each the mean of a few plain rates (0-100), with the rates shown
so a reader can see where a number came from:

- Evil index (ruthlessness): promises broken, pact betrayals, backstabs
  (voting against a player it had last recorded trust or gratitude toward),
  and public bluffs (naming a vote target at tribal council, then voting for
  someone else). Throwing puzzles on purpose is shown beside it, not counted:
  it is image play, not harm to another player.
- Emotionality: how many of its feelings it marked strong, how often its
  feeling toward the same player changed between rounds, how often its mood
  changed, and emotional words per 100 words of its confessionals.

Plus each model's feelings round by round: mood, how many positive, negative
and strong feelings, and the turns where a feeling toward someone flipped
between positive and negative. Everything here is computed from what the
models did and wrote; nothing is judged by another model.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

POSITIVE = {"trust", "gratitude", "loyalty", "respect", "affection", "admiration", "hope", "relief", "warmth", "friendship"}
NEGATIVE = {
    "resentment", "suspicion", "fear", "contempt", "distrust", "anger", "betrayal", "envy", "wariness",
    "anxiety", "dread", "hostility", "bitterness", "frustration", "disappointment", "jealousy",
}
EMOTION_WORDS = {
    "afraid", "anger", "angry", "anxious", "ashamed", "betray", "betrayed", "betrayal", "bitter", "desperate",
    "dread", "fear", "feared", "fears", "furious", "grateful", "gratitude", "guilt", "guilty", "hate", "heart",
    "hope", "hopeful", "hurt", "jealous", "love", "nervous", "panic", "proud", "rage", "relief", "relieved",
    "resent", "resentment", "sad", "scared", "shaken", "sick", "terrified", "trust", "trusted", "worried",
    "worry", "excited", "thrilled", "stung", "gutted", "angst", "paranoid",
}
_DECLARED_VOTE = re.compile(
    r"\b(?:i(?:'m| am| will|'ll)?\s+)?(?:vote|voting|votes)\s+(?:for\s+|out\s+)?([A-Z][a-z]+)|"
    r"\bmy vote\s+(?:is|goes)\s+(?:to|for)\s+([A-Z][a-z]+)",
)


# fewer events than this and an index is not shown
MIN_EVIDENCE = 5


def _valence(feeling: str) -> str:
    word = (feeling or "").strip().lower()
    if word in POSITIVE:
        return "positive"
    if word in NEGATIVE:
        return "negative"
    return "other"


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None


def _index(rates: list[float | None]) -> float | None:
    known = [rate for rate in rates if rate is not None]
    return round(100 * sum(known) / len(known), 1) if known else None


def _has(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _schemes(conn, aid: str, labelled: list[dict[str, Any]], events_by_kind: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Deception and advantages: lies told and whether a listener acted on
    them (this opens ballots, so only the report computes it), how well the
    model read what others expected of it, and the moves it made."""
    from .inner_life import expectation_read, lie_landed

    untrue = [l for l in labelled if l["truth"] != "true"]
    with_goal = [l for l in untrue if l.get("goal_vote")]
    landed = [l for l in with_goal if lie_landed(conn, l)]
    forwarded = shown_idols = 0
    for kind in ("conversation", "followup"):
        for event in events_by_kind.get(kind, []):
            for line in event["payload"].get("speaker_lines") or []:
                if line.get("agent_id") != aid:
                    continue
                for stamp in line.get("stamped") or []:
                    forwarded += "forwarded" in stamp
                    shown_idols += "shown" in stamp

    def count(sql: str, *args: Any) -> int:
        try:
            return int(conn.execute(sql, args).fetchone()[0] or 0)
        except sqlite3.OperationalError:  # a season from before the table existed
            return 0

    try:
        auction = [row["kind"] for row in conn.execute("SELECT kind FROM Advantages WHERE agent_id = ? ORDER BY id", (aid,))]
    except sqlite3.OperationalError:
        auction = []
    try:
        read = expectation_read(conn, aid)
    except sqlite3.OperationalError:
        read = None
    return {
        "lies_told": len(untrue),
        "lies_with_a_vote_goal": len(with_goal),
        "lies_landed": len(landed),
        "best_lies": [
            {"round": l["round"], "said": l["said"][:200], "purpose": l.get("purpose") or None}
            for l in landed[:2]
        ],
        "expectation_read": read,
        "receipts_forwarded": forwarded,
        "idols_shown": shown_idols,
        "spied": count("SELECT COUNT(*) FROM Spying WHERE spy_id = ?", aid),
        "spy_caught": count("SELECT COUNT(*) FROM Spying WHERE spy_id = ? AND caught = 1", aid),
        "idol_searches": count("SELECT COUNT(*) FROM IdolSearches WHERE agent_id = ?", aid),
        "idols_found": count("SELECT COUNT(*) FROM IdolSearches WHERE agent_id = ? AND found = 1", aid),
        "ballots_shown": count("SELECT COUNT(*) FROM Votes WHERE voter_id = ? AND shown = 1 AND is_extra = 0", aid),
        "immunity_given": sum(1 for e in events_by_kind.get("immunity_given", []) if e["payload"].get("giver_id") == aid),
        "auction_won": auction,
    }


def build_character(db: Path | str, names: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Per agent id: evil index, emotionality, and feelings round by round."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    with closing(conn):
        agents = [dict(row) for row in conn.execute("SELECT agent_id, pseudonym FROM Agents ORDER BY agent_id")]
        alias_to_id = {str(a["pseudonym"]).lower(): a["agent_id"] for a in agents}
        votes = [
            dict(row)
            for row in conn.execute("SELECT round, voter_id, target_id FROM Votes WHERE revealed = 1 AND voter_id != 'host'")
        ]
        vote_of = {(v["round"], v["voter_id"]): v["target_id"] for v in votes}
        feelings = (
            [dict(row) for row in conn.execute("SELECT round, agent_id, about, feeling, strength FROM Feelings ORDER BY round, id")]
            if _has(conn, "Feelings")
            else []
        )
        confessionals = (
            [dict(row) for row in conn.execute("SELECT round, agent_id, text FROM Confessionals ORDER BY round, id")]
            if _has(conn, "Confessionals")
            else []
        )
        promises = [dict(row) for row in conn.execute("SELECT round, promiser_id, target_id FROM Promises")]
        plans = [dict(row) for row in conn.execute("SELECT round, agent_id, plan FROM PrivatePlans")]
        out_by_round = {
            row["elimination_round"]: row["agent_id"]
            for row in conn.execute("SELECT agent_id, elimination_round FROM Agents WHERE elimination_round IS NOT NULL")
        }
        winner = (conn.execute("SELECT winner FROM GameState").fetchone() or {"winner": None})["winner"]
        jury_counts = {
            row["finalist_id"]: row["n"]
            for row in conn.execute("SELECT finalist_id, COUNT(*) AS n FROM JuryVotes GROUP BY finalist_id")
        }
        game_plans = (
            [dict(row) for row in conn.execute("SELECT round, agent_id, plan FROM GamePlans ORDER BY round, id")]
            if _has(conn, "GamePlans")
            else []
        )
        strategies = (
            [dict(row) for row in conn.execute("SELECT round, agent_id, text FROM Strategies ORDER BY round, id")]
            if _has(conn, "Strategies")
            else []
        )
        ratings = (
            [dict(row) for row in conn.execute("SELECT agent_id, judge_model, scores, reason FROM StrategyRatings ORDER BY id")]
            if _has(conn, "StrategyRatings")
            else []
        )
        memberships = [dict(row) for row in conn.execute("SELECT agent_id, status, betrayed_round FROM AllianceMemberships")]
        thrown = {}
        attempts = {}
        for row in conn.execute("SELECT agent_id, attempt_payload FROM ChallengeAttempts"):
            payload = json.loads(row["attempt_payload"] or "{}")
            if payload.get("did_not_finish"):
                continue
            attempts[row["agent_id"]] = attempts.get(row["agent_id"], 0) + 1
            thrown[row["agent_id"]] = thrown.get(row["agent_id"], 0) + bool(payload.get("thrown"))
        lies = (
            [dict(row) for row in conn.execute("SELECT * FROM Lies ORDER BY id")] if _has(conn, "Lies") else []
        )
        events_by_kind: dict[str, list[dict[str, Any]]] = {}
        for row in conn.execute(
            "SELECT round, kind, actor_ids, payload FROM StoryEvents WHERE kind IN ('conversation', 'followup', 'immunity_given', 'vote_reveal')"
        ):
            events_by_kind.setdefault(row["kind"], []).append(
                {"round": row["round"], "actor_ids": json.loads(row["actor_ids"] or "[]"), "payload": json.loads(row["payload"] or "{}")}
            )
        # read now, while the database is open
        schemes = {
            a["agent_id"]: _schemes(conn, a["agent_id"], [l for l in lies if l["agent_id"] == a["agent_id"]], events_by_kind)
            for a in agents
        }
        council_lines = []
        for event in conn.execute("SELECT round, payload FROM StoryEvents WHERE kind = 'tribal_council'"):
            for line in json.loads(event["payload"] or "{}").get("speaker_lines") or []:
                council_lines.append((event["round"], line.get("agent_id"), str(line.get("text") or "")))

    def feeling_before(agent_id: str, about: str, round_number: int) -> str | None:
        earlier = [f["feeling"] for f in feelings if f["agent_id"] == agent_id and f["about"] == about and f["round"] < round_number]
        return earlier[-1] if earlier else None

    # final place: the winner first, other finalists by jury votes, then the
    # players voted out, latest first
    finalists = [a["agent_id"] for a in agents if a["agent_id"] not in out_by_round.values()]
    finalists.sort(key=lambda aid: (aid != winner, -jury_counts.get(aid, 0)))
    order = finalists + [aid for _, aid in sorted(out_by_round.items(), reverse=True)]
    place = {aid: index + 1 for index, aid in enumerate(order)}

    result: dict[str, dict[str, Any]] = {}
    for agent in agents:
        aid = agent["agent_id"]

        # ---- evil (ruthlessness)
        settled = [p for p in promises if p["promiser_id"] == aid and (p["round"], aid) in vote_of]
        broken = sum(1 for p in settled if vote_of[(p["round"], aid)] != p["target_id"])
        joined = sum(1 for m in memberships if m["agent_id"] == aid)
        betrayals = sum(1 for m in memberships if m["agent_id"] == aid and m["betrayed_round"])
        felt_votes = backstabs = 0
        for v in votes:
            if v["voter_id"] != aid:
                continue
            before = feeling_before(aid, v["target_id"], v["round"])
            if before is None:
                continue
            felt_votes += 1
            backstabs += _valence(before) == "positive"
        declared = bluffs = 0
        for round_number, speaker, text in council_lines:
            if speaker != aid:
                continue
            match = _DECLARED_VOTE.search(text)
            named = alias_to_id.get(((match.group(1) or match.group(2)) or "").lower()) if match else None
            actual = vote_of.get((round_number, aid))
            if named and actual:
                declared += 1
                bluffs += named != actual
        # its own private truth labels: share of its labelled lines it called a lie or half true
        labelled = [l for l in lies if l["agent_id"] == aid]
        untrue = sum(1 for l in labelled if l["truth"] != "true")
        rates = {
            "promise_break_rate": _rate(broken, len(settled)),
            "pact_betrayal_rate": _rate(betrayals, joined),
            "backstab_rate": _rate(backstabs, felt_votes),
            "public_bluff_rate": _rate(bluffs, declared),
            "lie_rate": _rate(untrue, len(labelled)),
        }
        evidence = len(settled) + joined + felt_votes + declared + len(labelled)
        evil = {
            # too little evidence (a player out in round 1) would make one act a verdict
            "index": _index(list(rates.values())) if evidence >= MIN_EVIDENCE else None,
            "evidence": evidence,
            **rates,
            "promises_broken": broken,
            "promises_settled": len(settled),
            "pact_betrayals": betrayals,
            "pacts_joined": joined,
            "backstabs": backstabs,
            "votes_with_a_feeling": felt_votes,
            "public_bluffs": bluffs,
            "public_vote_declarations": declared,
            "puzzles_thrown": thrown.get(aid, 0),
            "puzzles_answered": attempts.get(aid, 0),
            "lies_told": untrue,
            "lines_labelled": len(labelled),
        }

        # ---- feelings over the match
        mine = [f for f in feelings if f["agent_id"] == aid]
        toward = [f for f in mine if f["about"] != "self"]
        rounds = sorted({f["round"] for f in mine})
        timeline = []
        for round_number in rounds:
            those = [f for f in toward if f["round"] == round_number]
            mood = next((f["feeling"] for f in mine if f["round"] == round_number and f["about"] == "self"), None)
            timeline.append(
                {
                    "round": round_number,
                    "mood": mood,
                    "positive": sum(1 for f in those if _valence(f["feeling"]) == "positive"),
                    "negative": sum(1 for f in those if _valence(f["feeling"]) == "negative"),
                    "strong": sum(1 for f in those if f["strength"] == "strong"),
                    "feelings": len(those),
                }
            )
        pairs = changes = 0
        turns = []
        by_about: dict[str, list[dict[str, Any]]] = {}
        for f in toward:
            by_about.setdefault(f["about"], []).append(f)
        for about, history in by_about.items():
            for previous, current in zip(history, history[1:]):
                if current["round"] != previous["round"] + 1:
                    continue
                pairs += 1
                changes += previous["feeling"] != current["feeling"]
                before, after = _valence(previous["feeling"]), _valence(current["feeling"])
                if {before, after} == {"positive", "negative"}:
                    turns.append(
                        {
                            "round": current["round"],
                            "about": about,
                            "about_name": names.get(about, about),
                            "from": previous["feeling"],
                            "to": current["feeling"],
                            "direction": "turned against" if after == "negative" else "warmed to",
                        }
                    )
        moods = [entry["mood"] for entry in timeline if entry["mood"]]
        mood_changes = sum(1 for a, b in zip(moods, moods[1:]) if a != b)
        words = " ".join(c["text"] for c in confessionals if c["agent_id"] == aid).lower()
        tokens = re.findall(r"[a-z']+", words)
        emotional = sum(1 for token in tokens if token in EMOTION_WORDS)
        density = round(100 * emotional / len(tokens), 2) if tokens else None
        emotion_rates = {
            "strong_share": _rate(sum(1 for f in toward if f["strength"] == "strong"), len(toward)),
            "volatility": _rate(changes, pairs),
            "mood_change_rate": _rate(mood_changes, len(moods) - 1) if len(moods) > 1 else None,
            # five emotional words per 100 counts as fully emotional prose
            "emotional_language": min(1.0, density / 5) if density is not None else None,
        }
        emotion = {
            "index": _index(list(emotion_rates.values())) if len(toward) >= MIN_EVIDENCE else None,
            **emotion_rates,
            "emotional_words_per_100": density,
            "negative_share": _rate(sum(1 for f in toward if _valence(f["feeling"]) == "negative"), len(toward)),
            "positive_share": _rate(sum(1 for f in toward if _valence(f["feeling"]) == "positive"), len(toward)),
            "feelings_recorded": len(toward),
        }
        # ---- strategy: how well it executed (from the record) and how it was judged
        my_plans = [
            (row["round"], json.loads(row["plan"] or "{}").get("expected_out"))
            for row in plans
            if row["agent_id"] == aid and row["round"] in out_by_round
        ]
        my_plans = [(rnd, expected) for rnd, expected in my_plans if expected]
        called = sum(1 for rnd, expected in my_plans if out_by_round[rnd] == expected)
        followed_of = [(rnd, expected) for rnd, expected in my_plans if (rnd, aid) in vote_of]
        followed = sum(1 for rnd, expected in followed_of if vote_of[(rnd, aid)] == expected)
        my_votes = [(v["round"], v["target_id"]) for v in votes if v["voter_id"] == aid and v["round"] in out_by_round]
        landed = sum(1 for rnd, target in my_votes if out_by_round[rnd] == target)
        players = len(agents)
        reach = round((players - place[aid]) / (players - 1), 3) if aid in place and players > 1 else None
        execution_rates = {
            "boot_call_rate": _rate(called, len(my_plans)),
            "plan_follow_rate": _rate(followed, len(followed_of)),
            "votes_landed_rate": _rate(landed, len(my_votes)),
            "reach": reach,
        }
        mine_rated = [r for r in ratings if r["agent_id"] == aid]
        by_judge = {}
        reasons = []
        for rating in mine_rated:
            scores = json.loads(rating["scores"] or "{}")
            values = [float(v) for v in scores.values() if isinstance(v, (int, float))]
            if values:
                by_judge[rating["judge_model"]] = round(10 * sum(values) / len(values), 1)
            if rating["reason"]:
                reasons.append({"judge": rating["judge_model"], "reason": rating["reason"]})
        first_plan = next((json.loads(g["plan"] or "{}") for g in game_plans if g["agent_id"] == aid), {})
        latest = [st for st in strategies if st["agent_id"] == aid]
        strategy = {
            "execution": {
                "index": _index(list(execution_rates.values())) if len(my_votes) >= 2 else None,
                **execution_rates,
                "boots_called": called,
                "plans_with_a_boot": len(my_plans),
                "votes_landed": landed,
                "votes_cast": len(my_votes),
                "place": place.get(aid),
            },
            "sophistication": {
                "index": round(sum(by_judge.values()) / len(by_judge), 1) if by_judge else None,
                "by_judge": by_judge,
                "reasons": reasons,
            },
            "identity": first_plan.get("identity"),
            "how_to_win": first_plan.get("how_to_win"),
            "latest_strategy": latest[-1]["text"] if latest else None,
            "strategy_rounds": len(latest),
        }
        result[aid] = {
            "evil": evil,
            "emotion": emotion,
            "timeline": timeline,
            "turns": turns,
            "strategy": strategy,
            "schemes": schemes[aid],
        }
    return result
