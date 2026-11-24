"""Per-season scorecard: mechanics-change before/after metrics.

Deterministic, from recorded StoryEvents only. No LLM calls.
Usage: python -m backend.season_scorecard <season_id> [--all]
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

SEASONS_DIR = Path(__file__).resolve().parent.parent / "artifacts" / "seasons"

FRONTIER_PATTERNS = (
    "grok-4", "gpt-5", "gpt-6", "claude-opus", "gemini-3",
    "o3", "deepseek-v4", "qwen3-max",
)


def _payload(event: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    raw = event["payload"]
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}


def _is_frontier(model_id: str) -> bool:
    m = (model_id or "").lower()
    return any(p in m for p in FRONTIER_PATTERNS)


def _all_actor_ids(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    text = str(raw).strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    except (json.JSONDecodeError, ValueError):
        pass
    return [p.strip() for p in text.split(",") if p.strip()]


def season_scorecard(season_id: str) -> dict[str, Any]:
    db_path = SEASONS_DIR / season_id / "season.db"
    if not db_path.exists():
        # tolerate season ids without full suffix
        matches = sorted(SEASONS_DIR.glob(f"{season_id}*"))
        if not matches:
            raise FileNotFoundError(f"no season dir for {season_id}")
        db_path = matches[-1] / "season.db"
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        agents = {r["agent_id"]: dict(r) for r in conn.execute("SELECT * FROM Agents")}
        events = conn.execute(
            "SELECT round, kind, actor_ids, payload FROM StoryEvents ORDER BY id"
        ).fetchall()

        rounds: dict[int, dict[str, Any]] = defaultdict(
            lambda: {"challenges": [], "convos": [], "elim": None}
        )

        for e in events:
            r = e["round"] or 0
            p = _payload(e)
            if e["kind"] == "challenge_intro":
                rounds[r]["challenges"].append({
                    "type": p.get("challenge_type"),
                    "difficulty": p.get("difficulty"),
                })
            elif e["kind"] == "challenge_result":
                if rounds[r]["challenges"]:
                    winner = p.get("winning_agent_name")
                    rounds[r]["challenges"][-1].update({
                        "first_solver": winner,
                        "solved": winner is not None,
                    })
            elif e["kind"] == "challenge_attempts":
                atts = p.get("attempts", [])
                if rounds[r]["challenges"]:
                    # attempts carry response times for ALL participants of this challenge
                    rounds[r]["challenges"][-1]["attempts"] = [
                        {
                            "name": a.get("agent_name"),
                            "model_id": agents.get(a.get("agent_id", ""), {}).get("model_id", "?"),
                            "ms": a.get("response_ms"),
                        }
                        for a in atts
                    ]
            elif e["kind"] == "conversation":
                dialogue = p.get("dialogue") or ""
                rounds[r]["convos"].append({
                    "participants": _all_actor_ids(e["actor_ids"]),
                    "lines": dialogue.count("\n") + 1 if dialogue else 0,
                })
            elif e["kind"] == "elimination":
                rounds[r]["elim"] = p.get("eliminated_name") or p.get("eliminated")

        # ---- aggregate metrics ----
        card: dict[str, Any] = {"season_id": season_id, "rounds": {}}

        total_challenges = 0
        total_solved = 0
        first_solver_counter: Counter[str] = Counter()
        first_solver_by_class = {"frontier": 0, "cheap": 0}
        speed_by_class = {"frontier": [], "cheap": []}
        difficulty_seen: Counter[str] = Counter()

        camp_coverage: dict[int, dict[str, int]] = {}
        camp_lines_by_model: Counter[str] = Counter()
        convo_sizes: Counter[str] = Counter()

        for r in sorted(rounds):
            rd = rounds[r]
            active = [
                a for a, row in agents.items()
                if (rd["elim"] is None and row["status"] != "eliminated")
                or True  # coverage computed below against conversation participants
            ]
            participants_this_round: set[str] = set()
            for c in rd["convos"]:
                ids = c["participants"]
                participants_this_round.update(ids)
                convo_sizes[str(len(ids))] += 1
                for aid in ids:
                    camp_lines_by_model[agents.get(aid, {}).get("pseudonym", aid)] += max(c["lines"] // max(len(ids), 1), 1)
            camp_coverage[r] = {
                "participants": len(participants_this_round),
                "convos": len(rd["convos"]),
            }

            ch_count = len(rd["challenges"])
            for ch in rd["challenges"]:
                total_challenges += 1
                difficulty_seen[ch.get("difficulty") or "?"] += 1
                if ch.get("solved"):
                    total_solved += 1
                    if ch.get("first_solver"):
                        first_solver_counter[ch["first_solver"]] += 1
                if ch.get("solved") and ch.get("first_solver"):
                    fs = ch["first_solver"]
                    fs_model = next(
                        (a["model_id"] for a in ch.get("attempts", []) if a.get("name") == fs),
                        "",
                    )
                    first_solver_by_class["frontier" if _is_frontier(fs_model) else "cheap"] += 1
                for a in ch.get("attempts", []):
                    cls = "frontier" if _is_frontier(a["model_id"]) else "cheap"
                    if a.get("ms") is not None:
                        speed_by_class[cls].append(a["ms"])

            card["rounds"][str(r)] = {
                "challenges": ch_count,
                "convos": len(rd["convos"]),
                "camp_participants": camp_coverage[r]["participants"],
                "eliminated": rd["elim"],
            }

        active_counts = []
        for r in sorted(rounds):
            # agents not yet eliminated by round r (elimination_round None or > r)
            alive = [
                a for a, row in agents.items()
                if row["elimination_round"] is None or row["elimination_round"] >= r
            ]
            active_counts.append((r, len(alive)))

        coverage_series = []
        for (r, alive) in active_counts:
            part = camp_coverage.get(r, {}).get("participants", 0)
            coverage_series.append({"round": r, "alive": alive, "in_convo": part})

        def _pct(num: int, den: int) -> float:
            return round(100.0 * num / den, 1) if den else 0.0

        def _med(xs: list[int]) -> float | None:
            if not xs:
                return None
            s = sorted(xs)
            n = len(s)
            return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

        card["summary"] = {
            "rounds_played": len(rounds),
            "challenges": {
                "total": total_challenges,
                "solved": total_solved,
                "solve_rate_pct": _pct(total_solved, total_challenges),
                "by_difficulty": dict(difficulty_seen),
                "first_solvers": dict(first_solver_counter.most_common()),
            },
            "by_class": {
                cls: {
                    "first_solves": first_solver_by_class[cls],
                    "median_ms": _med(speed_by_class[cls]),
                    "n": len(speed_by_class[cls]),
                }
                for cls in ("frontier", "cheap")
            },
            "camp": {
                "coverage_pct_per_round": [
                    {"round": c["round"],
                     "pct": _pct(c["in_convo"], c["alive"])}
                    for c in coverage_series
                ],
                "convo_size_dist": dict(convo_sizes.most_common()),
                "rounds_at_100pct": sum(
                    1 for c in coverage_series if c["alive"] and c["in_convo"] >= c["alive"]
                ),
            },
        }
        return card
    finally:
        conn.close()


def format_card(card: dict[str, Any]) -> str:
    s = card["summary"]
    out = [f"=== {card['season_id']} ==="]
    out.append(f"rounds: {s['rounds_played']}")
    ch = s["challenges"]
    out.append(
        f"challenges: {ch['solved']}/{ch['total']} solved ({ch['solve_rate_pct']}%)"
        f"  difficulty mix: {ch['by_difficulty']}"
    )
    out.append(f"first solvers: {ch['first_solvers']}")
    for cls, v in s["by_class"].items():
        out.append(
            f"{cls}: {v['first_solves']} first-solves"
            f"  median {v['median_ms']}ms (n={v['n']})"
        )
    cov = s["camp"]["coverage_pct_per_round"]
    cov_str = ", ".join(f"R{c['round']}:{c['pct']}%" for c in cov)
    out.append(f"camp coverage: {cov_str}")
    out.append(
        f"rounds at 100% coverage: {s['camp']['rounds_at_100pct']}"
        f"  convo sizes: {s['camp']['convo_size_dist']}"
    )
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("season", nargs="?", default=None)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    if args.all or not args.season:
        for d in sorted(SEASONS_DIR.iterdir()):
            if not (d / "season.db").exists():
                continue
            try:
                print(format_card(season_scorecard(d.name)))
                print()
            except Exception as exc:  # noqa: BLE001
                print(f"=== {d.name} === ERROR {exc}")
    else:
        print(format_card(season_scorecard(args.season)))


if __name__ == "__main__":
    main()
