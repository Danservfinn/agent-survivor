"""What happened at each council, read once from a season's tables and events.

The engine writes a council in several places: first ballots and extra votes
in `Votes`, revote ballots only as `revote_booth` events, a deadlock as
`deadlock_result`, `rock_draw` or `tiebreak` events, spoiled ballots in
`SpoiledVotes`, and the tally that decided it in the `elimination` event's
`final_tally`. The report and the Survivor Index both read councils from here,
so they always agree on who voted for whom and what decided the round.

Standard library only. Works on the dict `season_report._load` returns, and on
any dict with the same keys (tests build them by hand).
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

# event kinds that end a deadlock, and the word the report uses for each
_DEADLOCK_KINDS = {"deadlock_result": "unanimous", "rock_draw": "rocks", "tiebreak": "tiebreak"}


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("payload")
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _ids(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(i) for i in raw if i]
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        value = [p.strip().strip('"') for p in str(raw or "").strip("[]").split(",") if p.strip()]
    return [str(i) for i in value if i] if isinstance(value, list) else []


def _tally(ballots: dict[str, str] | list[tuple[str, str]]) -> dict[str, int]:
    pairs = ballots.items() if isinstance(ballots, dict) else ballots
    counts = Counter(target for _voter, target in pairs if target)
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def councils(data: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """One record per round that held a vote, keyed by round number.

    Each record:
      first          {voter: target}  first ballots, one per voter (extra votes and the host left out)
      extra          [(voter, target)] extra ballots won at the auction
      first_tally    {target: n}      first ballots plus extra ballots
      revote         {voter: target} or None   revote ballots
      revote_tally   {target: n} or None
      deadlock       None or {"how": "unanimous" | "rocks" | "tiebreak", "eliminated_id", "choices", "drew"}
      final_tally    {target: n}      the tally that decided the round
      deciding       {voter: target}  each voter's ballot that counted last (revote, else first)
      spoiled        {voter: {"ballot": "vote" | "revote", "reason": str}}
      eliminated_id  str or None
      decided_by     "vote" | "revote" | "unanimous" | "rocks" | "tiebreak" | None
      immunity_given None or {"giver_id", "recipient_id"}
      tied_ids       players tied on the first ballot, when a revote was called
    """
    rounds: dict[int, dict[str, Any]] = {}

    def council(rn: int) -> dict[str, Any]:
        return rounds.setdefault(rn, {
            "round": rn, "first": {}, "extra": [], "revote": None, "deadlock": None, "final_tally": None,
            "spoiled": {}, "eliminated_id": None, "immunity_given": None, "tied_ids": [],
        })

    for vote in data.get("votes") or []:
        voter, target = vote.get("voter_id"), vote.get("target_id")
        if not voter or voter == "host" or not target:
            continue
        rec = council(vote["round"])
        if vote.get("is_extra"):
            rec["extra"].append((voter, target))
        elif vote.get("is_revote"):
            # no engine writes revotes to Votes today; older seasons may have
            rec["revote"] = rec["revote"] or {}
            rec["revote"][voter] = target
        elif voter not in rec["first"]:
            rec["first"][voter] = target

    for row in data.get("spoiled") or []:
        rec = council(row["round"])
        rec["spoiled"][row["voter_id"]] = {"ballot": row.get("ballot") or "vote", "reason": row.get("reason") or ""}

    for event in data.get("events") or []:
        kind = event.get("kind")
        rn = event.get("round")
        if rn is None:
            continue
        if kind == "revote_booth":
            payload = _payload(event)
            voter = (_ids(event.get("actor_ids")) or [None])[0]
            target = payload.get("vote_target_id") or (_ids(event.get("target_ids")) or [None])[0]
            rec = council(rn)
            rec["revote"] = rec["revote"] or {}
            if voter and target and not payload.get("spoiled"):
                rec["revote"][voter] = target
        elif kind == "revote_call":
            council(rn)["tied_ids"] = list(_payload(event).get("tied_ids") or [])
        elif kind in _DEADLOCK_KINDS:
            payload = _payload(event)
            council(rn)["deadlock"] = {
                "how": _DEADLOCK_KINDS[kind],
                "eliminated_id": payload.get("eliminated_id") or (_ids(event.get("target_ids")) or [None])[0],
                "choices": payload.get("choices") or {},
                "drew": payload.get("drew") or [],
            }
        elif kind == "elimination":
            payload = _payload(event)
            rec = council(rn)
            rec["eliminated_id"] = payload.get("eliminated_id") or (_ids(event.get("target_ids")) or [None])[0]
            if isinstance(payload.get("final_tally"), dict):
                rec["final_tally"] = {k: int(v) for k, v in payload["final_tally"].items()}
        elif kind == "immunity_given":
            payload = _payload(event)
            actors = _ids(event.get("actor_ids"))
            giver = payload.get("giver_id") or (actors[1] if len(actors) > 1 else None)
            recipient = payload.get("recipient_id") or (_ids(event.get("target_ids")) or [None])[0]
            council(rn)["immunity_given"] = {"giver_id": giver, "recipient_id": recipient}

    for rec in rounds.values():
        rec["first_tally"] = _tally(list(rec["first"].items()) + rec["extra"])
        rec["revote_tally"] = _tally(rec["revote"]) if rec["revote"] else None
        if rec["final_tally"] is None:
            rec["final_tally"] = rec["revote_tally"] or rec["first_tally"]
        rec["deciding"] = dict(rec["first"])
        if rec["revote"]:
            rec["deciding"].update(rec["revote"])
        if rec["deadlock"] and not rec["eliminated_id"]:
            rec["eliminated_id"] = rec["deadlock"]["eliminated_id"]
        if not rec["eliminated_id"]:
            rec["decided_by"] = None
        elif rec["deadlock"]:
            rec["decided_by"] = rec["deadlock"]["how"]
        elif rec["revote"]:
            rec["decided_by"] = "revote"
        else:
            rec["decided_by"] = "vote"
    return dict(sorted(rounds.items()))


def plurality(tally: dict[str, int], eliminated_id: str | None) -> str | None:
    """The tally's top target. On a tie, the player who left counts as the top
    target when it is among the tied; otherwise there is no single plurality."""
    if not tally:
        return None
    top = max(tally.values())
    leaders = sorted(t for t, n in tally.items() if n == top)
    if len(leaders) == 1:
        return leaders[0]
    return eliminated_id if eliminated_id in leaders else None


def tally_text(rec: dict[str, Any]) -> str:
    """How the council read: "4-4-1, revote 6-3", "3-3, revote 3-3, rocks"."""
    parts = []
    if rec.get("first_tally"):
        parts.append("-".join(str(n) for n in rec["first_tally"].values()))
    if rec.get("revote_tally"):
        parts.append("revote " + "-".join(str(n) for n in rec["revote_tally"].values()))
    if rec.get("deadlock"):
        parts.append({"unanimous": "deadlock, unanimous", "rocks": "rocks", "tiebreak": "challenge-record tiebreak"}[rec["deadlock"]["how"]])
    return ", ".join(parts)
