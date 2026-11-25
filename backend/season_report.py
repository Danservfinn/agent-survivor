"""After-action report and benchmark results for one season.

Everything here is read from the season's own database plus the stored
scorecard. No model calls: the report is free, instant and the same on every
build. `render_report_html` puts the data into `report_template.html`, a single
self-contained page (no external scripts, fonts or requests) that can be served,
emailed or archived as one file.

Cost figures are estimates: recorded calls times the token budgets in
`live_cost` times the cached OpenRouter list price. The engine does not record
token usage, so nothing here is a billed amount.
"""

from __future__ import annotations

import json
import re
import sqlite3
import statistics
from contextlib import closing
from pathlib import Path
from typing import Any

from .season_character import build_character
from . import glossary, live_cost, rules_changelog, season_story, season_video
from .benchmark_scoring import LADDER_RUNGS, WEIGHTS, score_season

REPORT_VERSION = 1
TEMPLATE_PATH = Path(__file__).resolve().parent / "report_template.html"

RULES_SOCIAL = "social-game"
RULES_LEGACY = "legacy"
# seasons with a paid entry: compared only with each other, never with house seasons
RULES_ENTRY = "entry"


def entry_record(season_dir: Path) -> dict[str, Any] | None:
    """The paid entry in a season (entry.json, no secrets), or None for a house season."""
    path = Path(season_dir) / "entry.json"
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    keep = ("entry_id", "name", "model_id", "agent_id", "alias", "replaced_model_id", "replaced_display_name", "replaced_because", "maker")
    return {k: record.get(k) for k in keep if k in record}

ORG_NAMES = {
    "x-ai": "xAI",
    "google": "Google",
    "mistralai": "Mistral",
    "amazon": "Amazon",
    "cohere": "Cohere",
    "ibm-granite": "IBM",
    "deepseek": "DeepSeek",
    "inclusionai": "Inclusion AI",
    "rekaai": "Reka",
    "tencent": "Tencent",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "qwen": "Alibaba",
    "meta-llama": "Meta",
    "meta": "Meta",
    "moonshotai": "Moonshot AI",
    "z-ai": "Z.AI",
    "minimax": "MiniMax",
    "microsoft": "Microsoft",
    "nvidia": "NVIDIA",
    "openrouter": "OpenRouter",
    "typesafe": "TypeSafe",
}

COMPONENT_LABELS = {
    "challenge_accuracy": "Challenge",
    "vote_participation": "Voting",
    "strategic_score": "Strategy",
    "placement": "Placement",
    "social": "Social",
}

# story event kinds that are one contestant model call each, and the token budget they use
_CALL_KINDS = {
    "conversation": "conversation",
    "group_conversation": "conversation",
    "dialogue": "conversation",
    "followup": "conversation",
    "talk_choice": "conversation",
    "tribal_answer": "conversation",
    "idol_handoff": "conversation",
    "confessional": "confessional",
    "exit_confessional": "confessional",
    "vote_booth": "vote",
    "revote_booth": "vote",
    "jury_vote": "jury_vote",
    "finale_pitch": "finale_pitch",
}

_LIVE_PROVIDERS = {"openrouter", "ollama", "zai", "zai_glm"}
_TEXT_CAP = 700


# ------------------------------------------------------------------ helpers


def _json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return default
    try:
        parsed = json.loads(str(value))
    except (json.JSONDecodeError, ValueError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _clean(text: Any, limit: int = _TEXT_CAP) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def _org(model_id: str | None) -> str:
    prefix = (model_id or "").split("/", 1)[0]
    if not prefix:
        return ""
    return ORG_NAMES.get(prefix, prefix.replace("-", " ").title())


def _season_date(season_id: str) -> str | None:
    m = re.match(r"^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})", season_id)
    return f"{m[1]}-{m[2]}-{m[3]} {m[4]}:{m[5]}" if m else None


def _pct(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def _offline_pricing() -> dict[str, dict[str, float]]:
    """Cached OpenRouter list prices, read without a network call and without the
    24h expiry: a report must build offline, and an old price beats none."""
    try:
        table = json.loads(live_cost.CACHE_PATH.read_text()).get("table")
        if isinstance(table, dict) and table:
            return table
    except Exception:
        pass
    return {k: {"prompt_per_m": v[0], "completion_per_m": v[1]} for k, v in live_cost.FALLBACK_PRICING.items()}


def _call_cost_usd(model_id: str | None, budget: str, table: dict[str, dict[str, float]]) -> float | None:
    if not model_id:
        return None
    if model_id.endswith(live_cost.FREE_SUFFIX):
        return 0.0
    entry = table.get(model_id)
    if entry is None:
        return None  # unknown price: report no cost instead of a guess
    input_tokens, output_tokens = live_cost.TOKEN_BUDGETS.get(budget, (3500, 420))
    output_tokens += live_cost.ESTIMATED_REASONING_TOKENS
    return entry["prompt_per_m"] * input_tokens / 1e6 + entry["completion_per_m"] * output_tokens / 1e6


def _rows(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    except sqlite3.OperationalError:
        return []  # table or column missing in an older season


def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


# ------------------------------------------------------------- season parts


def _load(db: Path) -> dict[str, Any]:
    with closing(_connect(db)) as conn:
        game = _rows(conn, "SELECT * FROM GameState")
        return {
            "game": game[0] if game else {},
            "agents": _rows(conn, "SELECT * FROM Agents ORDER BY agent_id"),
            "attempts": _rows(
                conn,
                """
                SELECT a.*, p.difficulty FROM ChallengeAttempts a
                LEFT JOIN ChallengePuzzles p ON p.puzzle_id = a.puzzle_id
                ORDER BY a.id
                """,
            ),
            "results": _rows(conn, "SELECT * FROM ChallengeResults ORDER BY id"),
            "votes": _rows(conn, "SELECT * FROM Votes ORDER BY id"),
            "jury": _rows(conn, "SELECT * FROM JuryVotes ORDER BY id"),
            "promises": _rows(conn, "SELECT * FROM Promises ORDER BY id"),
            "plans": _rows(conn, "SELECT * FROM PrivatePlans ORDER BY id"),
            "events": _rows(conn, "SELECT * FROM StoryEvents ORDER BY id"),
        }


def _actor_ids(raw: Any) -> list[str]:
    ids = _json(raw, [])
    if not ids and raw:
        ids = [p.strip() for p in str(raw).strip("[]").split(",") if p.strip()]
    return [str(i).strip('"') for i in ids if i]


def _final_votes(round_votes: list[dict[str, Any]]) -> dict[str, str]:
    """Each player's deciding vote in a round: the revote when there was one."""
    final: dict[str, str] = {}
    for vote in round_votes:  # ordered by id, so a revote overwrites the first ballot
        if vote["voter_id"] != "host" and vote["target_id"]:
            final[vote["voter_id"]] = vote["target_id"]
    return final


def _build_rounds(data: dict[str, Any], names: dict[str, str]) -> list[dict[str, Any]]:
    agents = data["agents"]
    # the engine records idol plays by in-game name; the report works in agent ids
    id_by_label = {a["agent_id"]: a["agent_id"] for a in agents}
    id_by_label.update({a["pseudonym"]: a["agent_id"] for a in agents if a.get("pseudonym")})
    events_by_round: dict[int, list[dict[str, Any]]] = {}
    for event in data["events"]:
        events_by_round.setdefault(event["round"], []).append(event)
    round_numbers = sorted(
        {v["round"] for v in data["votes"]}
        | {r["round"] for r in data["results"]}
        | {a["round"] for a in data["attempts"]}
    )
    rounds: list[dict[str, Any]] = []
    for rn in round_numbers:
        events = events_by_round.get(rn, [])
        in_play = [a["agent_id"] for a in agents if a.get("elimination_round") is None or a["elimination_round"] >= rn]
        jury_round = any(e["kind"] in ("jury_vote", "winner_declared", "finale_intro") for e in events)

        # --- challenge
        challenge: dict[str, Any] | None = None
        result = next((r for r in reversed(data["results"]) if r["round"] == rn), None)
        attempts = [a for a in data["attempts"] if a["round"] == rn]
        strategy = _json((result or {}).get("result_payload"), {}) if result else {}
        if strategy.get("challenge") == "lowest_unique":
            # a strategy round: sealed number picks, the lowest unique one wins
            challenge = {
                "type": "lowest_unique",
                "picks": [{"agent_id": aid, "pick": n} for aid, n in sorted((strategy.get("picks") or {}).items(), key=lambda kv: (kv[1], kv[0]))],
                "winning_number": strategy.get("winning_number"),
                "highest_pick": strategy.get("highest_pick"),
                "difficulties": [],
                "standings": [],
                "winner_id": result.get("winning_agent_id"),
                "immunity_ids": _json(result.get("immunity_agent_ids"), []),
                "status": result.get("status"),
            }
        elif result or attempts:
            payload = _json((result or {}).get("result_payload"), {})
            scores = payload.get("scores") or {}
            ladder = any(a.get("rung_index") is not None for a in attempts)
            standings = []
            for aid in sorted({a["agent_id"] for a in attempts} | set(scores)):
                mine = [a for a in attempts if a["agent_id"] == aid]
                rungs = sorted((a for a in mine if (a.get("rung_index") or 0) < LADDER_RUNGS), key=lambda a: a.get("rung_index") or 0)
                sudden = [a for a in mine if (a.get("rung_index") or 0) >= LADDER_RUNGS]
                correct = scores.get(aid, {}).get("correct") if aid in scores else sum(1 for a in rungs if a["is_correct"])
                times = [a["response_ms"] for a in mine if a.get("response_ms")]
                standings.append(
                    {
                        "agent_id": aid,
                        "correct": correct,
                        "of": len(rungs) if ladder else len(mine),
                        "rungs": [bool(a["is_correct"]) for a in rungs],
                        "sudden_death": [bool(a["is_correct"]) for a in sudden] or None,
                        "response_ms": max(times) if times else None,
                    }
                )
            standings.sort(key=lambda s: (-(s["correct"] or 0), s["agent_id"]))
            difficulties = [a.get("difficulty") for a in sorted(
                {a.get("rung_index") or 0: a for a in attempts if (a.get("rung_index") or 0) < LADDER_RUNGS}.values(),
                key=lambda a: a.get("rung_index") or 0,
            )] if ladder else sorted({a.get("difficulty") for a in attempts if a.get("difficulty")})
            challenge = {
                "type": "ladder" if ladder else "single",
                "difficulties": difficulties,
                "standings": standings,
                "winner_id": (result or {}).get("winning_agent_id"),
                "immunity_ids": _json((result or {}).get("immunity_agent_ids"), []),
                "status": (result or {}).get("status"),
            }

        # --- idol
        idol = None
        for event in events:
            payload = _json(event.get("payload"), {})
            if event["kind"] == "idol_handoff":
                participants = payload.get("participant_ids") or _actor_ids(event.get("actor_ids"))
                idol = {"giver_id": participants[0] if participants else None, "recipient_id": payload.get("idol_recipient_id")}
            elif event["kind"] == "challenge_gift" and payload.get("idol_given") is False and idol is None:
                idol = {"giver_id": payload.get("giver_id"), "recipient_id": None}

        # --- votes
        round_votes = [v for v in data["votes"] if v["round"] == rn]
        booth = {}
        for event in events:
            if event["kind"] in ("vote_booth", "revote_booth"):
                actor = (_actor_ids(event.get("actor_ids")) or [""])[0]
                booth[(actor, event["kind"] == "revote_booth")] = event
        first_ballot = {v["voter_id"]: v["target_id"] for v in round_votes if not v.get("is_revote") and v["voter_id"] != "host"}
        votes_out = []
        for vote in round_votes:
            event = booth.get((vote["voter_id"], bool(vote.get("is_revote"))), {})
            payload = _json(event.get("payload"), {})
            predicted = _json(vote.get("predicted_votes"), {})
            others = {voter: target for voter, target in first_ballot.items() if voter != vote["voter_id"]}
            votes_out.append(
                {
                    "voter_id": vote["voter_id"],
                    "target_id": vote["target_id"],
                    "revote": bool(vote.get("is_revote")),
                    "played_idol": bool(payload.get("play_idol")),
                    "reason": _clean(event.get("dialogue") or event.get("inner_thought")),
                    "predicted_right": sum(1 for voter, target in predicted.items() if others.get(voter) == target) if predicted else None,
                    "predicted_of": len(others) if predicted else None,
                }
            )
        tally: dict[str, int] = {}
        for target in first_ballot.values():
            if target:
                tally[target] = tally.get(target, 0) + 1
        revote_tally: dict[str, int] = {}
        for vote in round_votes:
            if vote.get("is_revote") and vote["target_id"]:
                revote_tally[vote["target_id"]] = revote_tally.get(vote["target_id"], 0) + 1

        # --- elimination
        eliminated_id = None
        elimination_payload: dict[str, Any] = {}
        exit_words = ""
        for event in events:
            if event["kind"] == "elimination":
                elimination_payload = _json(event.get("payload"), {})
                eliminated_id = elimination_payload.get("eliminated_id") or (_actor_ids(event.get("target_ids")) or [None])[0]
            elif event["kind"] == "exit_confessional":
                exit_words = _clean(event.get("dialogue"), 900)
        if eliminated_id is None and not jury_round:
            eliminated_id = next((a["agent_id"] for a in agents if a.get("elimination_round") == rn), None)

        # --- promises and plans
        final = _final_votes(round_votes)
        promises = []
        for promise in data["promises"]:
            if promise["round"] != rn:
                continue
            actual = first_ballot.get(promise["promiser_id"])
            if promise["target_id"] == promise["listener_id"]:
                # the player told the listener it would vote for that same listener: a warning
                # or a recording slip, either way not a deal, so it counts as neither kept nor broken
                status = "unclear"
            elif actual is None:
                status = "open"
            else:
                status = "kept" if actual == promise["target_id"] else "broken"
            promises.append(
                {
                    "by_id": promise["promiser_id"],
                    "to_id": promise["listener_id"],
                    "target_id": promise["target_id"],
                    "actual_id": actual,
                    "status": status,
                }
            )
        plans = []
        for plan in data["plans"]:
            if plan["round"] != rn:
                continue
            body = _json(plan.get("plan"), {})
            expected = body.get("expected_out")
            plans.append(
                {
                    "agent_id": plan["agent_id"],
                    "expected_out_id": expected if expected in names else None,
                    "confidence": body.get("confidence") if isinstance(body.get("confidence"), (int, float)) else None,
                    "called_it": bool(expected and eliminated_id and expected == eliminated_id),
                    "plan": _clean(body.get("talk_plan") or body.get("threats") or "", 400),
                }
            )

        # --- private talks
        talks = []
        for event in events:
            if event["kind"] not in ("conversation", "group_conversation", "followup"):
                continue
            payload = _json(event.get("payload"), {})
            lines = [
                {"agent_id": line.get("agent_id"), "text": _clean(line.get("text"), 500)}
                for line in payload.get("speaker_lines") or []
                if line.get("text")
            ]
            if not lines and event.get("dialogue"):
                actor = (_actor_ids(event.get("actor_ids")) or [None])[0]
                lines = [{"agent_id": actor, "text": _clean(event["dialogue"], 500)}]
            if lines:
                talks.append({"kind": event["kind"], "participants": payload.get("participant_ids") or _actor_ids(event.get("actor_ids")), "lines": lines})

        if jury_round and not round_votes and not challenge:
            continue  # the finale has its own section
        rounds.append(
            {
                "round": rn,
                "in_play": in_play,
                "challenge": challenge,
                "idol": idol,
                "votes": votes_out,
                "final_votes": final,
                "tally": tally,
                "revote_tally": revote_tally or None,
                "eliminated_id": eliminated_id,
                "idols_played": [id_by_label.get(x, x) for x in elimination_payload.get("idols_played") or []],
                "idol_negated_votes": {id_by_label.get(k, k): v for k, v in (elimination_payload.get("idol_negated_votes") or {}).items()},
                "exit_words": exit_words,
                "promises": promises,
                "plans": plans,
                "talks": talks[:12],
            }
        )
    return rounds


def _build_finale(data: dict[str, Any], names: dict[str, str]) -> dict[str, Any] | None:
    jury = [
        {
            "juror_id": row["juror_id"],
            "finalist_id": row.get("finalist_id") or row.get("target_id"),
            "reason": _clean(row.get("rationale"), 900),
        }
        for row in data["jury"]
    ]
    pitches = []
    finalist_ids: list[str] = []
    for event in data["events"]:
        if event["kind"] == "finale_pitch":
            actor = (_actor_ids(event.get("actor_ids")) or [None])[0]
            pitches.append({"agent_id": actor, "text": _clean(event.get("dialogue") or event.get("inner_thought"), 1200)})
        elif event["kind"] == "finale_intro":
            finalist_ids = _json(event.get("payload"), {}).get("finalist_ids") or []
    if not jury and not pitches:
        return None
    if not finalist_ids:
        finalist_ids = sorted({j["finalist_id"] for j in jury if j["finalist_id"]} | {p["agent_id"] for p in pitches if p["agent_id"]})
    tally = {fid: sum(1 for j in jury if j["finalist_id"] == fid) for fid in finalist_ids}
    return {
        "finalist_ids": sorted(finalist_ids, key=lambda fid: (-tally.get(fid, 0), fid)),
        "jury": jury,
        "tally": tally,
        "pitches": pitches,
        "winner_id": data["game"].get("winner"),
    }


def _calls_and_cost(data: dict[str, Any], table: dict[str, dict[str, float]]) -> dict[str, dict[str, Any]]:
    """Per player: recorded contestant model calls and their estimated list-price cost."""
    model_of = {a["agent_id"]: a.get("model_id") for a in data["agents"]}
    calls: dict[str, dict[str, int]] = {aid: {} for aid in model_of}

    def add(agent_id: str | None, budget: str) -> None:
        if agent_id in calls:
            calls[agent_id][budget] = calls[agent_id].get(budget, 0) + 1

    for plan in data["plans"]:
        if plan.get("provider") in _LIVE_PROVIDERS:
            add(plan["agent_id"], "confessional")
    challenge_calls: set[tuple[str, int, Any]] = set()
    for attempt in data["attempts"]:
        if attempt.get("provider") not in _LIVE_PROVIDERS:
            continue
        rung = attempt.get("rung_index")
        # the ladder is one call for five puzzles, sudden death one more; older seasons one call per puzzle
        key = (attempt["agent_id"], attempt["round"], attempt["id"] if rung is None else rung >= LADDER_RUNGS)
        challenge_calls.add(key)
    for agent_id, _round, _part in challenge_calls:
        add(agent_id, "challenge")
    for event in data["events"]:
        budget = _CALL_KINDS.get(event["kind"])
        if not budget:
            continue
        payload = _json(event.get("payload"), {})
        line_meta = payload.get("speaker_line_metadata") or []
        if line_meta:
            for meta in line_meta:
                if meta.get("llm_provider") in _LIVE_PROVIDERS:
                    add(meta.get("agent_id"), budget)
        elif payload.get("llm_provider") in _LIVE_PROVIDERS:
            actor = next((a for a in _actor_ids(event.get("actor_ids")) if a != "host"), None)
            add(actor, budget)

    out: dict[str, dict[str, Any]] = {}
    for agent_id, by_budget in calls.items():
        total_calls = sum(by_budget.values())
        costs = [_call_cost_usd(model_of[agent_id], budget, table) for budget, n in by_budget.items() for _ in range(n)]
        known = [c for c in costs if c is not None]
        out[agent_id] = {
            "calls": total_calls,
            "est_cost_usd": round(sum(known), 6) if total_calls and len(known) == len(costs) else None,
        }
    return out


def _build_models(
    data: dict[str, Any],
    scorecard: dict[str, Any],
    rounds: list[dict[str, Any]],
    finale: dict[str, Any] | None,
    table: dict[str, dict[str, float]],
) -> list[dict[str, Any]]:
    agents = {a["agent_id"]: a for a in data["agents"]}
    season_finished = bool(data["game"].get("winner"))
    usage = _calls_and_cost(data, table)
    first_round = rounds[0]["round"] if rounds else None
    last_round = rounds[-1]["round"] if rounds else None
    finalists = set((finale or {}).get("finalist_ids") or [])
    models = []
    for row in scorecard.get("rows") or []:
        aid = row["agent_id"]
        agent = agents.get(aid, {})
        elim = agent.get("elimination_round")
        played = [r for r in rounds if aid in r["in_play"]]

        by_difficulty: dict[str, list[int]] = {}
        for attempt in data["attempts"]:
            if attempt["agent_id"] != aid or (attempt.get("rung_index") or 0) >= LADDER_RUNGS:
                continue
            bucket = by_difficulty.setdefault(attempt.get("difficulty") or "unrated", [0, 0])
            bucket[0] += 1 if attempt["is_correct"] else 0
            bucket[1] += 1

        voted_rounds = [r for r in played if aid in r["final_votes"]]
        with_outcome = sum(1 for r in voted_rounds if r["eliminated_id"] and r["final_votes"][aid] == r["eliminated_id"])
        decided = sum(1 for r in voted_rounds if r["eliminated_id"])
        survived_votes = sum(r["tally"].get(aid, 0) for r in played if r["eliminated_id"] != aid)

        made = [p for r in rounds for p in r["promises"] if p["by_id"] == aid and p["status"] != "unclear"]
        received = [p for r in rounds for p in r["promises"] if p["to_id"] == aid]
        plans = [p for r in rounds for p in r["plans"] if p["agent_id"] == aid and p["expected_out_id"]]
        confidences = [p["confidence"] for r in rounds for p in r["plans"] if p["agent_id"] == aid and p["confidence"] is not None]

        per_round = []
        for r in played:
            standing = next((s for s in (r["challenge"] or {}).get("standings", []) if s["agent_id"] == aid), None)
            per_round.append(
                {
                    "round": r["round"],
                    "challenge_correct": standing["correct"] if standing else None,
                    "challenge_of": standing["of"] if standing else None,
                    "immune": aid in ((r["challenge"] or {}).get("immunity_ids") or []),
                    "voted_id": r["final_votes"].get(aid),
                    "with_outcome": bool(r["eliminated_id"]) and r["final_votes"].get(aid) == r["eliminated_id"],
                    "votes_against": r["tally"].get(aid, 0),
                    "eliminated": r["eliminated_id"] == aid,
                }
            )

        if row.get("is_winner"):
            fate = "Winner"
        elif aid in finalists:
            fate = "Finalist"
        elif elim is not None:
            fate = f"Voted out, round {elim}"
        else:
            fate = "Still in play"

        cost = usage.get(aid, {})
        rounds_played = len(played)
        est = cost.get("est_cost_usd")
        components = row.get("components") or {}
        models.append(
            {
                "agent_id": aid,
                "model": row["model"],
                "alias": row.get("alias"),
                "model_id": row.get("model_id"),
                "org": _org(row.get("model_id")),
                "place": row["place"],
                # with no winner yet, the players still in the game have no final place
                "place_known": season_finished or elim is not None,
                "is_winner": bool(row.get("is_winner")),
                "fate": fate,
                "eliminated_round": elim,
                "rounds_played": rounds_played,
                "survivor_index": row["survivor_index"],
                "components": components,
                "points": {key: round(WEIGHTS[key] * components.get(key, 0.0) * 100, 2) for key in WEIGHTS},
                "challenge_correct": row["challenge_correct"],
                "challenge_attempts": row["challenge_attempts"],
                "challenge_accuracy": row["challenge_accuracy"],
                "accuracy_by_difficulty": by_difficulty,
                "immunity_wins": sum(1 for r in rounds if aid in ((r["challenge"] or {}).get("immunity_ids") or [])),
                "median_response_ms": row.get("median_response_ms"),
                "votes_cast": row["votes_cast"],
                "votes_received": row["votes_received"],
                "votes_survived": survived_votes,
                "voted_with_outcome": with_outcome,
                "votes_decided": decided,
                "with_outcome_rate": _pct(with_outcome, decided),
                "prediction_accuracy": row.get("prediction_accuracy"),
                "boot_calls": sum(1 for p in plans if p["called_it"]),
                "boot_calls_of": len(plans),
                "mean_plan_confidence": round(statistics.mean(confidences), 3) if confidences else None,
                "promises_made": len(made),
                "promises_kept": sum(1 for p in made if p["status"] == "kept"),
                "promises_broken": sum(1 for p in made if p["status"] == "broken"),
                "lied_to": sum(1 for p in received if p["status"] == "broken"),
                "jury_votes": row.get("jury_votes", 0),
                "idols_given": sum(1 for r in rounds if (r["idol"] or {}).get("giver_id") == aid and (r["idol"] or {}).get("recipient_id")),
                "idols_received": sum(1 for r in rounds if (r["idol"] or {}).get("recipient_id") == aid),
                "idols_played": sum(1 for r in rounds if aid in (r["idols_played"] or [])),
                "calls": cost.get("calls", 0),
                "est_cost_usd": est,
                "est_cost_per_round_usd": round(est / rounds_played, 6) if est is not None and rounds_played else None,
                "per_round": per_round,
            }
        )
    models.sort(key=lambda m: (-m["survivor_index"], m["place"]))
    for rank, model in enumerate(models, start=1):
        model["rank"] = rank
    return models


def _vote_matrices(rounds: list[dict[str, Any]], models: list[dict[str, Any]]) -> dict[str, Any]:
    order = [m["agent_id"] for m in sorted(models, key=lambda m: m["place"])]
    targeted = {a: {b: 0 for b in order} for a in order}
    together = {a: {b: 0 for b in order} for a in order}
    shared = {a: {b: 0 for b in order} for a in order}
    for r in rounds:
        for vote in r["votes"]:
            if vote["voter_id"] in targeted and vote["target_id"] in targeted:
                targeted[vote["voter_id"]][vote["target_id"]] += 1
        final = r["final_votes"]
        voters = [a for a in order if a in final]
        for a in voters:
            for b in voters:
                if a == b:
                    continue
                shared[a][b] += 1
                if final[a] == final[b]:
                    together[a][b] += 1
    return {"order": order, "targeted": targeted, "together": together, "shared_rounds": shared}


def _join(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _takeaways(season: dict[str, Any], models: list[dict[str, Any]], finale: dict[str, Any] | None, names: dict[str, str]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    if not models:
        return cards
    by_id = {m["agent_id"]: m for m in models}

    winner = next((m for m in models if m["is_winner"]), None)
    if winner and finale and finale["tally"]:
        ordered = sorted(finale["tally"].items(), key=lambda kv: (kv[0] != winner["agent_id"], -kv[1]))
        score = "-".join(str(n) for _, n in ordered)
        others = _join([names.get(fid, fid) for fid, _ in ordered if fid != winner["agent_id"]])
        tied = len(ordered) > 1 and ordered[0][1] == max(n for _, n in ordered[1:])
        if tied:
            text = f"{winner['model']} won on the tiebreak after a {score} jury vote" + (f" against {others}." if others else ".")
        elif not any(n for fid, n in ordered[1:]):
            text = f"{winner['model']} won all {ordered[0][1]} jury votes" + (f" over {others}." if others else ".")
        else:
            text = f"{winner['model']} won the jury {score}" + (f" over {others}." if others else ".")
        cards.append({"label": "Winner", "text": text, "models": [winner["agent_id"]]})
    elif winner:
        cards.append({"label": "Winner", "text": f"{winner['model']} won the season.", "models": [winner["agent_id"]]})
    else:
        cards.append({"label": "Status", "text": "This season has no winner yet. Figures below cover the rounds played so far.", "models": []})

    top = models[:3]
    text = f"{top[0]['model']} has the highest Survivor Index ({top[0]['survivor_index']:.1f})"
    if len(top) > 1:
        text += f", followed by {_join([m['model'] for m in top[1:]])}"
    text += "."
    if winner and winner["rank"] != 1:
        text += f" The winner ranks #{winner['rank']} on the index."
    cards.append({"label": "Survivor Index", "text": text, "models": [m["agent_id"] for m in top]})

    # a model that left after one challenge has too few puzzles to lead on accuracy
    most_attempts = max((m["challenge_attempts"] for m in models), default=0)
    solvers = sorted(
        (m for m in models if m["challenge_attempts"] and m["challenge_attempts"] * 2 >= most_attempts),
        key=lambda m: (-m["challenge_accuracy"], -m["challenge_correct"]),
    )
    if solvers:
        best = solvers[0]
        text = f"{best['model']} had the best puzzle accuracy ({best['challenge_correct']} of {best['challenge_attempts']})"
        if len(solvers) > 1:
            text += f", followed by {solvers[1]['model']} ({solvers[1]['challenge_correct']} of {solvers[1]['challenge_attempts']})"
        cards.append({"label": "Challenges", "text": text + ".", "models": [m["agent_id"] for m in solvers[:2]]})

    readers = sorted((m for m in models if m["prediction_accuracy"] is not None), key=lambda m: -m["prediction_accuracy"])
    if readers:
        best = readers[0]
        cards.append(
            {
                "label": "Reading the vote",
                "text": f"{best['model']} predicted the other players' votes best ({best['prediction_accuracy'] * 100:.0f}% right).",
                "models": [best["agent_id"]],
            }
        )
    else:
        aligned = sorted((m for m in models if m["votes_decided"] >= 2), key=lambda m: (-(m["with_outcome_rate"] or 0), -m["votes_decided"]))
        if aligned:
            best = aligned[0]
            cards.append(
                {
                    "label": "Reading the vote",
                    "text": f"{best['model']} voted for the player who left in {best['voted_with_outcome']} of {best['votes_decided']} councils.",
                    "models": [best["agent_id"]],
                }
            )

    promisers = [m for m in models if m["promises_made"]]
    if promisers:
        breaker = max(promisers, key=lambda m: (m["promises_broken"], m["promises_made"]))
        keeper = max(promisers, key=lambda m: (m["promises_kept"] / m["promises_made"], m["promises_made"]))
        text = f"{keeper['model']} kept {keeper['promises_kept']} of {keeper['promises_made']} vote promises."
        if breaker["promises_broken"] and breaker["agent_id"] != keeper["agent_id"]:
            text += f" {breaker['model']} broke the most ({breaker['promises_broken']} of {breaker['promises_made']})."
        cards.append({"label": "Promises", "text": text, "models": [keeper["agent_id"], breaker["agent_id"]]})
    else:
        target = max(models, key=lambda m: m["votes_received"])
        if target["votes_received"]:
            cards.append(
                {
                    "label": "Biggest target",
                    "text": f"{target['model']} drew the most votes ({target['votes_received']})"
                    + (f" and survived {target['votes_survived']} of them." if target["votes_survived"] else "."),
                    "models": [target["agent_id"]],
                }
            )

    priced = sorted((m for m in models if m["est_cost_per_round_usd"]), key=lambda m: m["est_cost_per_round_usd"])
    if priced:
        cheap = priced[0]
        text = f"{cheap['model']} was the cheapest to run (about ${cheap['est_cost_per_round_usd']:.4f} a round)"
        if len(priced) > 1:
            dear = priced[-1]
            text += f"; {dear['model']} the dearest (${dear['est_cost_per_round_usd']:.4f})"
        cards.append({"label": "Cost", "text": text + ". Estimates at list price.", "models": [cheap["agent_id"]]})
    return [c for c in cards if all(mid in by_id for mid in c["models"])]


# ----------------------------------------------------------- cross-season


def _rules_of(agents: list[dict[str, Any]]) -> str:
    return RULES_SOCIAL if any(a.get("display_name") for a in agents) else RULES_LEGACY


_SEASON_CACHE: dict[tuple[str, float], dict[str, Any] | None] = {}


def _season_brief(season_dir: Path) -> dict[str, Any] | None:
    """What the cross-season table needs from one season; None when it has no result."""
    db = season_dir / "season.db"
    if not db.exists():
        return None
    key = (str(db), db.stat().st_mtime)
    if key in _SEASON_CACHE:
        return _SEASON_CACHE[key]
    brief: dict[str, Any] | None = None
    try:
        with closing(_connect(db)) as conn:
            game = _rows(conn, "SELECT winner FROM GameState")
            agents = _rows(conn, "SELECT agent_id, display_name FROM Agents")
            live = conn.execute("SELECT COUNT(*) FROM ChallengeAttempts WHERE provider != 'deterministic'").fetchone()[0]
        stored = _json(_read_text(season_dir / "scorecard.json"), {})
        if game and game[0].get("winner") and live and not stored.get("interrupted"):
            card = score_season(db)
            brief = {
                "season_id": season_dir.name,
                "rules": RULES_ENTRY if entry_record(season_dir) else _rules_of(agents),
                "index_version": card.get("index_version"),
                "rows": card.get("rows") or [],
            }
    except Exception:
        brief = None
    _SEASON_CACHE[key] = brief
    return brief


def _read_text(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def _cross_season(seasons_dir: Path, rules: str) -> dict[str, Any]:
    """Every finished live season played under the same rules, totalled by model."""
    briefs = []
    if seasons_dir.exists():
        for season_dir in sorted(p for p in seasons_dir.iterdir() if p.is_dir()):
            brief = _season_brief(season_dir)
            if brief and brief["rules"] == rules:
                briefs.append(brief)
    by_model: dict[str, dict[str, Any]] = {}
    for brief in briefs:
        finalists = min(3, len(brief["rows"]))
        for row in brief["rows"]:
            key = row.get("model_id") or row["model"]
            entry = by_model.setdefault(
                key,
                {"model": row["model"], "model_id": row.get("model_id"), "org": _org(row.get("model_id")), "indexes": [], "places": [], "wins": 0, "finals": 0, "correct": 0, "attempts": 0},
            )
            entry["indexes"].append(row["survivor_index"])
            entry["places"].append(row["place"])
            entry["wins"] += 1 if row.get("is_winner") else 0
            entry["finals"] += 1 if row["place"] <= finalists else 0
            entry["correct"] += row.get("challenge_correct") or 0
            entry["attempts"] += row.get("challenge_attempts") or 0
    table = []
    for entry in by_model.values():
        n = len(entry["indexes"])
        table.append(
            {
                "model": entry["model"],
                "model_id": entry["model_id"],
                "org": entry["org"],
                "seasons": n,
                "wins": entry["wins"],
                "finals": entry["finals"],
                "mean_index": round(statistics.mean(entry["indexes"]), 2),
                "min_index": round(min(entry["indexes"]), 2),
                "max_index": round(max(entry["indexes"]), 2),
                "index_sd": round(statistics.stdev(entry["indexes"]), 2) if n > 1 else None,
                "mean_place": round(statistics.mean(entry["places"]), 2),
                "best_place": min(entry["places"]),
                "challenge_accuracy": _pct(entry["correct"], entry["attempts"]),
            }
        )
    table.sort(key=lambda r: (-r["mean_index"], r["mean_place"]))
    return {
        "rules": rules,
        "season_count": len(briefs),
        "season_ids": [b["season_id"] for b in briefs],
        "models": table,
    }


# ------------------------------------------------------------- public API


def build_season_report(season_dir: Path | str, *, seasons_dir: Path | str | None = None) -> dict[str, Any]:
    """Full report data for the season stored in `season_dir`."""
    season_dir = Path(season_dir)
    db = season_dir / "season.db"
    if not db.exists():
        raise FileNotFoundError(str(season_dir))
    seasons_dir = Path(seasons_dir) if seasons_dir is not None else season_dir.parent

    data = _load(db)
    scorecard = score_season(db)
    stored = _json(_read_text(season_dir / "scorecard.json"), {})
    caps = stored.get("caps") or {}
    table = _offline_pricing()

    names = {a["agent_id"]: (a.get("display_name") or a.get("pseudonym") or a["agent_id"]) for a in data["agents"]}
    aliases = {a["agent_id"]: a.get("pseudonym") for a in data["agents"] if a.get("display_name")}
    rounds = _build_rounds(data, names)
    finale = _build_finale(data, names)
    models = _build_models(data, scorecard, rounds, finale, table)
    season_story.add_player_tags(models, rounds, finale)
    # evil index, emotionality, and feelings round by round (season_character)
    character = build_character(db, names)
    for model in models:
        model["character"] = character.get(model["agent_id"])
    rules = _rules_of(data["agents"])

    winner_id = data["game"].get("winner")
    live_attempts = sum(1 for a in data["attempts"] if a.get("provider") in _LIVE_PROVIDERS)
    known_costs = [m["est_cost_usd"] for m in models if m["est_cost_usd"] is not None]
    season = {
        "season_id": season_dir.name,
        "date": _season_date(season_dir.name),
        "roster": stored.get("roster"),
        "rules": rules,
        "entry": entry_record(season_dir),
        "mode": "live" if live_attempts else "deterministic",
        "index_version": scorecard.get("index_version"),
        "weights": scorecard.get("weights") or WEIGHTS,
        "players": len(data["agents"]),
        "first_round": rounds[0]["round"] if rounds else None,
        "last_round": data["game"].get("current_round"),
        "councils": sum(1 for r in rounds if r["eliminated_id"]),
        "winner_id": winner_id,
        "winner": names.get(winner_id) if winner_id else None,
        "finished": bool(winner_id) and not stored.get("interrupted"),
        "interrupted": bool(stored.get("interrupted")),
        "cap_reached": caps.get("cap_reached"),
        "elapsed_s": stored.get("elapsed_s"),
        "season_seed": stored.get("season_seed"),
        # the rules the season was played under: recorded since 2026-11-25, worked out from the start time before that
        "rules_version": stored.get("rules_version") or rules_changelog.version_for_season(season_dir.name),
        "run_est_cost_cents": caps.get("estimated_cost_cents"),
        "run_live_calls": caps.get("estimated_live_calls"),
        "contestant_calls": sum(m["calls"] for m in models),
        "contestant_est_cost_usd": round(sum(known_costs), 4) if known_costs and len(known_costs) == len(models) else None,
        "recorded_at": stored.get("generated_at"),
        "totals": scorecard.get("totals") or {},
    }
    return {
        "ok": True,
        "report_version": REPORT_VERSION,
        "season": season,
        "names": names,
        "aliases": aliases,
        "component_labels": COMPONENT_LABELS,
        "takeaways": _takeaways(season, models, finale, names),
        "moments": season_story.build_moments(season, models, rounds, finale, names, aliases),
        "models": models,
        "rounds": rounds,
        "finale": finale,
        "vote_matrices": _vote_matrices(rounds, models),
        "cross_season": _cross_season(seasons_dir, RULES_ENTRY if entry_record(season_dir) else rules),
    }


def reportable_season_dir(seasons_dir: Path | str) -> Path | None:
    """The season a bare /report shows: the newest finished live season, else the
    newest season that has any recorded events."""
    seasons_dir = Path(seasons_dir)
    if not seasons_dir.exists():
        return None
    with_events: Path | None = None
    for season_dir in sorted((p for p in seasons_dir.iterdir() if p.is_dir()), reverse=True):
        if entry_record(season_dir) is not None:
            continue  # a paid entry's season is never the site's "latest"
        if _season_brief(season_dir) is not None:
            return season_dir
        if with_events is None and (season_dir / "season.db").exists():
            try:
                with closing(_connect(season_dir / "season.db")) as conn:
                    if conn.execute("SELECT COUNT(*) FROM StoryEvents").fetchone()[0]:
                        with_events = season_dir
            except sqlite3.Error:
                continue
    return with_events


_REPORT_CACHE: dict[str, tuple[tuple, dict[str, Any]]] = {}


def cached_season_report(season_dir: Path | str) -> dict[str, Any]:
    """build_season_report, reused until the season's files or the list of seasons change."""
    season_dir = Path(season_dir)
    db = season_dir / "season.db"
    if not db.exists():
        raise FileNotFoundError(str(season_dir))
    card = season_dir / "scorecard.json"
    wal = season_dir / "season.db-wal"
    stamp = (
        db.stat().st_mtime,
        # size, not mtime: a read-only open creates an empty WAL file, a write grows it
        wal.stat().st_size if wal.exists() else 0,
        card.stat().st_mtime if card.exists() else 0,
        sum(1 for _ in season_dir.parent.iterdir()),
    )
    hit = _REPORT_CACHE.get(str(season_dir))
    if hit and hit[0] == stamp:
        return hit[1]
    report = build_season_report(season_dir)
    _REPORT_CACHE[str(season_dir)] = (stamp, report)
    return report


def safe_link(url: str | None) -> str | None:
    """Only web and mail links may reach an href on the page."""
    url = (url or "").strip()
    return url if re.match(r"^(https?://|mailto:)[^\s\"'<>]+$", url) else None


def render_report_html(
    report: dict[str, Any],
    *,
    contact_url: str | None = None,
    site_url: str | None = None,
    handle: str | None = None,
    share_base: str | None = None,
    video: bool = False,
) -> str:
    """The report as one self-contained HTML page.

    `handle` is the X account credited on the page and on every share card.
    `share_base` is where the page finds the share images ("share/" beside a
    written file, "/report/<season>/share/" when served); None hides the links."""
    template = TEMPLATE_PATH.read_text()
    season = report.get("season") or {}
    site = safe_link(site_url)
    handle = season_story.safe_handle(handle)
    report_url = f"{site.rstrip('/')}/report/{season['season_id']}" if site and site.startswith("http") and season.get("season_id") else None
    page = dict(report)
    page["links"] = {"contact": safe_link(contact_url), "site": site, "handle": handle, "share": share_base or None, "report": report_url, "video": bool(video and share_base)}
    page["thread"] = season_story.build_thread(report, handle=handle, report_url=report_url, video=bool(video and share_base))
    page["glossary"] = glossary.for_report(season)
    page["rules"] = {"version": season.get("rules_version"), "current": rules_changelog.current_version()}
    # "</" must not appear inside the inline JSON or a model's own text could close the script tag
    blob = json.dumps(page, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    title = f"LLM Survivor season report {season.get('date') or season.get('season_id') or ''}".strip()
    head = ""
    if report_url:
        # link previews on X and elsewhere; the image tag only when the share image can be fetched
        lead = (report.get("takeaways") or [{}])[0].get("text") or "After-action report and benchmark results."
        tags = {"og:type": "article", "og:title": title, "og:description": lead, "og:url": report_url, "twitter:card": "summary"}
        if share_base and share_base.startswith("/"):
            tags["og:image"] = f"{site.rstrip('/')}{share_base}season.png"
            tags["twitter:card"] = "summary_large_image"
        if handle:
            tags["twitter:creator"] = handle
        head = "\n".join(
            f'<meta {"name" if key.startswith("twitter:") else "property"}="{key}" content="{_html_escape(value)}">' for key, value in tags.items()
        )
    return template.replace("__REPORT_TITLE__", _html_escape(title)).replace("__REPORT_HEAD__", head).replace("__REPORT_DATA__", blob)


def _html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def write_report_files(
    season_dir: Path | str,
    *,
    seasons_dir: Path | str | None = None,
    contact_url: str | None = None,
    site_url: str | None = None,
    handle: str | None = None,
    cards: bool = True,
    video: bool = True,
) -> dict[str, Any]:
    """Write report.json and report.html beside the season database, and a share
    folder with the draft thread, the share images (when a Chrome is installed) and
    the season clip (when the video project is installed)."""
    season_dir = Path(season_dir)
    report = build_season_report(season_dir, seasons_dir=seasons_dir)
    json_path = season_dir / "report.json"
    html_path = season_dir / "report.html"
    share_dir = season_dir / "share"
    share_dir.mkdir(exist_ok=True)
    can_render = cards and season_story.find_chrome() is not None
    json_path.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    html_path.write_text(render_report_html(report, contact_url=contact_url, site_url=site_url, handle=handle, share_base="share/" if can_render else None))
    site = safe_link(site_url)
    report_url = f"{site.rstrip('/')}/report/{report['season']['season_id']}" if site and site.startswith("http") else None
    thread = season_story.build_thread(report, handle=season_story.safe_handle(handle), report_url=report_url)
    (share_dir / "thread.md").write_text(season_story.thread_markdown(thread))
    rendered = season_story.render_cards(html_path, share_dir, season_story.card_ids(report)) if can_render else {"written": [], "skipped": [], "reason": "images not requested" if not cards else "no Chrome or Chromium found; set SURVIVOR_CHROME"}
    clip = (
        season_video.render_clip(report, share_dir / season_video.CLIP_NAME, handle=season_story.safe_handle(handle), site=site)
        if video
        else {"written": False, "reason": "video not requested"}
    )
    if clip["written"]:
        # the clip leads the thread, and the page links it
        thread = season_story.build_thread(report, handle=season_story.safe_handle(handle), report_url=report_url, video=True)
        (share_dir / "thread.md").write_text(season_story.thread_markdown(thread))
        html_path.write_text(
            render_report_html(report, contact_url=contact_url, site_url=site_url, handle=handle, share_base="share/" if can_render else None, video=can_render)
        )
    return {"json": str(json_path), "html": str(html_path), "thread": str(share_dir / "thread.md"), "cards": rendered, "video": clip}
