"""Facts about one season that the report states with care: how reliably each
player answered, what the season cost and on what basis, and the display rules
for small counts (k of n, intervals, when a "best at" claim is allowed).

One season is one game. These rules keep the report from claiming more than
one game shows. Standard library only.
"""

from __future__ import annotations

import gzip
import json
import math
import statistics
from pathlib import Path
from typing import Any

from . import prompt_log

# a rate prints as a percentage only from this many events up
MIN_N_PERCENT = 10
# a count-based tag ("kept every promise") needs at least this many events
MIN_N_TAG = 3
Z95 = 1.959964


# ------------------------------------------------------------------ counts


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float] | None:
    """95% Wilson score interval for k successes in n tries; None when n is 0."""
    if not n:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def k_of_n(k: int | None, n: int | None, *, interval: bool = False) -> str:
    """"26 of 33 (79%)", "2 of 2", or "–" when nothing was counted."""
    if k is None or not n:
        return "–"
    text = f"{k} of {n}"
    if n >= MIN_N_PERCENT:
        text += f" ({round(100 * k / n)}%"
        if interval:
            lo, hi = wilson(k, n) or (0, 0)
            text += f", {round(100 * lo)}–{round(100 * hi)}%"
        text += ")"
    return text


def separated_leader(counts: dict[str, tuple[int, int]]) -> str | None:
    """The one player whose rate is clearly above every other's in this season:
    at least MIN_N_PERCENT events and a Wilson lower bound above every other
    player's upper bound. None when no one stands apart."""
    eligible = {aid: kn for aid, kn in counts.items() if kn[1] >= MIN_N_PERCENT}
    if len(eligible) < 2:
        return None
    best = max(eligible, key=lambda aid: (eligible[aid][0] / eligible[aid][1], eligible[aid][1]))
    lo = (wilson(*eligible[best]) or (0, 0))[0]
    for aid, (k, n) in counts.items():
        if aid != best and n and (wilson(k, n) or (0, 1))[1] >= lo:
            return None
    return best


# ------------------------------------------------------------- prompt log


def read_prompt_log(season_dir: Path) -> list[dict[str, Any]]:
    """Every recorded model call of the season, or [] when none was recorded."""
    path = Path(season_dir) / prompt_log.GZ_NAME
    raw = Path(season_dir) / prompt_log.LOG_NAME
    try:
        if path.exists():
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        elif raw.exists():
            lines = raw.read_text(encoding="utf-8").splitlines()
        else:
            return []
    except (OSError, EOFError):
        return []
    entries = []
    for line in lines:
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    return entries


# ---------------------------------------------------------------- reliability


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(event.get("payload") or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _actors(event: dict[str, Any]) -> list[str]:
    try:
        value = json.loads(event.get("actor_ids") or "[]")
    except (TypeError, ValueError):
        return []
    return [str(v) for v in value] if isinstance(value, list) else []


def _failed(provider: Any) -> bool:
    return isinstance(provider, str) and (provider == "did_not_finish" or provider.endswith("_failed"))


def _pct_ms(values: list[float], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
    return int(ordered[index])


def reliability(data: dict[str, Any], prompt_entries: list[dict[str, Any]], aliases: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Per player: how often it failed to give a usable answer.

    From the season's own tables: spoiled ballots, silent lines, puzzle rounds
    with no answer, plans not written. From the prompt log when the season has
    one: calls made, calls that errored, replies that could not be read, and
    answering time. Without a prompt log, answering time comes from the puzzle
    calls only (`latency_basis` says which)."""
    out: dict[str, dict[str, Any]] = {}
    for agent in data.get("agents") or []:
        aid = agent["agent_id"]
        out[aid] = {
            "spoiled_votes": [], "silent_lines": 0, "puzzle_rounds_unanswered": [], "plans_missing": 0,
            "calls": None, "call_errors": None, "unreadable_replies": None,
            "latency_p50_ms": None, "latency_p95_ms": None, "latency_basis": None,
        }
    for row in data.get("spoiled") or []:
        if row.get("voter_id") in out:
            out[row["voter_id"]]["spoiled_votes"].append({"round": row.get("round"), "ballot": row.get("ballot") or "vote"})
    for event in data.get("events") or []:
        payload = _payload(event)
        for meta in payload.get("speaker_line_metadata") or []:
            if meta.get("llm_provider") == "silent" and meta.get("agent_id") in out:
                out[meta["agent_id"]]["silent_lines"] += 1
        if not payload.get("speaker_line_metadata") and payload.get("llm_provider") == "silent":
            actor = next((a for a in _actors(event) if a != "host"), None)
            if actor in out:
                out[actor]["silent_lines"] += 1
    unanswered: dict[str, set[int]] = {}
    for attempt in data.get("attempts") or []:
        if _failed(attempt.get("provider")) and attempt.get("agent_id") in out:
            unanswered.setdefault(attempt["agent_id"], set()).add(attempt.get("round"))
    for aid, rounds in unanswered.items():
        out[aid]["puzzle_rounds_unanswered"] = sorted(r for r in rounds if r is not None)
    for plan in data.get("plans") or []:
        if _failed(plan.get("provider")) and plan.get("agent_id") in out:
            out[plan["agent_id"]]["plans_missing"] += 1

    by_alias = {alias: aid for aid, alias in aliases.items() if alias}
    if prompt_entries:
        per: dict[str, list[dict[str, Any]]] = {}
        for entry in prompt_entries:
            aid = by_alias.get(entry.get("player") or "")
            if aid in out:
                per.setdefault(aid, []).append(entry)
        for aid, entries in per.items():
            times = [e["model_ms"] for e in entries if isinstance(e.get("model_ms"), (int, float))]
            out[aid].update({
                "calls": len(entries),
                "call_errors": sum(1 for e in entries if e.get("error")),
                "unreadable_replies": sum(1 for e in entries if not e.get("error") and e.get("reply") is None),
                "latency_p50_ms": _pct_ms(times, 0.5),
                "latency_p95_ms": _pct_ms(times, 0.95),
                "latency_basis": "every call",
            })
    for aid, rel in out.items():
        if rel["latency_basis"]:
            continue
        times = [a["response_ms"] for a in data.get("attempts") or [] if a.get("agent_id") == aid and a.get("response_ms") and not _failed(a.get("provider"))]
        if times:
            rel.update({"latency_p50_ms": int(statistics.median(times)), "latency_p95_ms": _pct_ms(times, 0.95), "latency_basis": "puzzle calls"})
    for rel in out.values():
        rel["failures"] = len(rel["spoiled_votes"]) + rel["silent_lines"] + len(rel["puzzle_rounds_unanswered"]) + rel["plans_missing"] + (rel["call_errors"] or 0)
    return out


# ------------------------------------------------------------------- cost


def measured_cost(prompt_entries: list[dict[str, Any]], aliases: dict[str, str], table: dict[str, dict[str, float]]) -> dict[str, float] | None:
    """Per player, the model fees of its own calls as the provider reported
    them (`usage.cost`), else its recorded tokens at list price. None when the
    prompt log holds no usage for any call (seasons before usage was kept)."""
    by_alias = {alias: aid for aid, alias in aliases.items() if alias}
    totals: dict[str, float] = {}
    any_usage = False
    for entry in prompt_entries:
        usage = entry.get("usage")
        aid = by_alias.get(entry.get("player") or "")
        if not isinstance(usage, dict) or aid is None:
            continue
        any_usage = True
        cost = usage.get("cost")
        if not isinstance(cost, (int, float)):
            price = table.get(entry.get("model") or "")
            if not price:
                continue
            cost = price["prompt_per_m"] * (usage.get("prompt_tokens") or 0) / 1e6 + price["completion_per_m"] * (usage.get("completion_tokens") or 0) / 1e6
        totals[aid] = totals.get(aid, 0.0) + float(cost)
    return {aid: round(v, 6) for aid, v in totals.items()} if any_usage else None
