"""Deterministic per-season narrative built from recorded StoryEvents.

No LLM calls: the summary is assembled from the models' own recorded
speech and private strategy statements, so it is free, instant, and
byte-identical on every render.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from . import live_season as _ls


def _connect(season_id: str) -> sqlite3.Connection:
    db = Path(_ls.SEASONS_DIR) / season_id / "season.db"
    if not db.exists():
        raise FileNotFoundError(season_id)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _names(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        r["agent_id"]: r["pseudonym"]
        for r in conn.execute("SELECT agent_id, pseudonym FROM Agents")
    }


def _actor_id(raw: Any) -> str:
    """actor_ids is stored as a JSON array string, a comma list, or a bare id."""
    ids = _all_actor_ids(raw)
    return ids[0] if ids else ""


def _all_actor_ids(raw: Any) -> list[str]:
    if not raw:
        return []
    text = str(raw).strip()
    parsed: Any = None
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        parsed = [p.strip() for p in text.split(",") if p.strip()]
    if isinstance(parsed, list):
        return [str(x) for x in parsed if x]
    return [str(parsed)]


def _payload(e: Any) -> dict[str, Any] | None:
    raw = e["payload"] if "payload" in e.keys() else None
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw))
        return parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def _is_ledger_note(text: str) -> bool:
    """True for host-generated knowledge-state notes, not model reasoning."""
    t = (text or "").lstrip()
    return t.startswith("I know ")


def _clean(text: str, limit: int = 4000) -> str:
    """Whitespace-normalized full text, hard-capped only for payload safety."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def season_narrative(season_id: str) -> dict[str, Any]:
    conn = _connect(season_id)
    try:
        names = _names(conn)
        rounds: list[dict[str, Any]] = []
        for r in conn.execute(
            "SELECT DISTINCT round FROM StoryEvents ORDER BY round"
        ):
            rn = r["round"]
            events = conn.execute(
                "SELECT * FROM StoryEvents WHERE round = ? ORDER BY id", (rn,)
            ).fetchall()

            challenge_line = ""
            immunity: list[str] = []
            challenge: dict[str, Any] | None = None
            for e in events:
                if e["kind"] == "challenge_result":
                    sub = e["subtitle"] or ""
                    challenge_line = sub
                    try:
                        payload = json.loads(e["payload"] or "{}")
                        winners = payload.get("immunity_agent_ids") or []
                        immunity = [names.get(w, w) for w in winners]
                        challenge = {
                            "type": payload.get("challenge_type", ""),
                            "winner": payload.get("winning_agent_name", ""),
                            "winner_id": payload.get("winning_agent_id", ""),
                            "tribe": payload.get("winning_tribe_id") or "",
                            "solved": not str(payload.get("result_status", "")).endswith("unsolved_fastest"),
                            "immunity": immunity,
                        }
                    except Exception:
                        immunity = []
                    break

            # every attempt: who tried, how fast, who was right, who solved first
            if challenge is not None:
                attempts = conn.execute(
                    """
                    SELECT a.agent_id, a.is_correct, a.response_ms, a.answer,
                           p.difficulty, p.puzzle_id
                    FROM ChallengeAttempts a
                    JOIN ChallengePuzzles p ON a.puzzle_id = p.puzzle_id
                    WHERE a.round = ?
                    ORDER BY a.response_ms
                    """,
                    (rn,),
                ).fetchall()
                correct = [a for a in attempts if a["is_correct"]]
                first = correct[0] if correct else None
                challenge["difficulty"] = first["difficulty"] if first else (
                    attempts[0]["difficulty"] if attempts else ""
                )
                challenge["puzzle_id"] = first["puzzle_id"] if first else (
                    attempts[0]["puzzle_id"] if attempts else ""
                )
                challenge["first_solver"] = names.get(first["agent_id"], first["agent_id"]) if first else None
                challenge["first_solve_ms"] = first["response_ms"] if first else None
                challenge["solved_by"] = [names.get(a["agent_id"], a["agent_id"]) for a in correct]
                challenge["attempts"] = [
                    {
                        "who": names.get(a["agent_id"], a["agent_id"]),
                        "correct": bool(a["is_correct"]),
                        "ms": a["response_ms"],
                    }
                    for a in attempts
                ]

            convos: list[dict[str, Any]] = []
            for e in events:
                if e["kind"] == "conversation" and e["dialogue"]:
                    payload = _payload(e)
                    speaker_ids = _all_actor_ids(e["actor_ids"])
                    speakers = [names.get(aid, aid) for aid in speaker_ids]
                    speaker = speakers[0] if speakers else (e["title"] or "?")
                    thought = e["inner_thought"] or ""
                    if _is_ledger_note(thought):
                        thought = ""  # host gossip ledger, not the model's reasoning
                    # full transcript lines when the event carries them
                    lines = []
                    for line in (payload or {}).get("speaker_lines", []):
                        lines.append(
                            {
                                "who": names.get(line.get("agent_id"), line.get("agent_id", "?")),
                                "text": _clean(line.get("text", "")),
                            }
                        )
                    convos.append(
                        {
                            "who": speaker,
                            "who_all": speakers,
                            "said": _clean(e["dialogue"]),
                            "thinking": _clean(thought),
                            "lines": lines,
                        }
                    )

            votes: list[dict[str, Any]] = []
            for e in events:
                if e["kind"] in ("vote_booth", "revote_booth") and e["subtitle"]:
                    voter = names.get(
                        _actor_id(e["actor_ids"]),
                        (e["title"] or "?").replace(" Votes", "").replace(" Revotes", ""),
                    )
                    target = e["subtitle"].replace("Vote: ", "").replace("Revote: ", "")
                    votes.append(
                        {
                            "who": voter,
                            "target": target,
                            "reason": _clean(e["dialogue"] or e["inner_thought"] or ""),
                        }
                    )

            elimination = None
            for e in events:
                if e["kind"] == "elimination":
                    elimination = (e["title"] or "").replace(" Leaves the Game", "")
                    break

            # tally of first-ballot targets
            tally: dict[str, int] = {}
            for v in votes:
                if v["target"]:
                    tally[v["target"]] = tally.get(v["target"], 0) + 1
            tally_line = ", ".join(
                f"{t} {n}" for t, n in sorted(tally.items(), key=lambda kv: -kv[1])
            )

            had_revote = any(e["kind"] == "revote_call" for e in events)

            summary_parts: list[str] = []
            if challenge_line:
                summary_parts.append(f"Challenge: {challenge_line}")
            if immunity:
                summary_parts.append(f"Immunity: {', '.join(immunity)}")
            if tally_line:
                summary_parts.append(f"Vote {tally_line}")
            if had_revote:
                summary_parts.append("deadlocked, went to a revote")
            if elimination:
                summary_parts.append(f"{elimination} voted out")

            rounds.append(
                {
                    "round": rn,
                    "summary": ". ".join(summary_parts) + "." if summary_parts else "",
                    "immunity": immunity,
                    "challenge": challenge,
                    "conversations": convos[:6],
                    "votes": votes,
                    "elimination": elimination,
                    "had_revote": had_revote,
                }
            )

        finale = None
        for e in conn.execute(
            "SELECT * FROM StoryEvents WHERE kind='winner_declared' ORDER BY id DESC LIMIT 1"
        ):
            tally_line = e["subtitle"] or ""
            winner = ""
            # "Jury tally: Reka Edge: 3, Gemma 4 26B A4B: 2, Command R7B: 1"
            if tally_line.startswith("Jury tally: "):
                body = tally_line[len("Jury tally: "):]
                parts = [p.strip() for p in body.split(",")]
                if parts:
                    winner = parts[0].rsplit(":", 1)[0].strip()
            finale = {
                "winner": winner,
                "line": tally_line,
            }

        jury: list[dict[str, Any]] = []
        for e in conn.execute(
            "SELECT * FROM StoryEvents WHERE kind='jury_vote' ORDER BY id"
        ):
            juror = names.get(_actor_id(e["actor_ids"]), (e["title"] or "").replace(" Votes", ""))
            sub = e["subtitle"] or ""
            for prefix in ("Winner vote: ", "Jury vote: ", "Vote: "):
                sub = sub.replace(prefix, "")
            jury.append(
                {
                    "who": juror,
                    "target": sub,
                    "reason": _clean(e["dialogue"] or e["inner_thought"] or ""),
                }
            )

        pitches: list[dict[str, Any]] = []
        for e in conn.execute(
            "SELECT * FROM StoryEvents WHERE kind='finale_pitch' ORDER BY id"
        ):
            pitches.append(
                {
                    "who": names.get(_actor_id(e["actor_ids"]), e["title"] or "?"),
                    "pitch": _clean(e["dialogue"] or e["inner_thought"] or ""),
                }
            )

        return {
            "ok": True,
            "season_id": season_id,
            "rounds": rounds,
            "jury": jury,
            "pitches": pitches,
            "finale": finale,
        }
    finally:
        conn.close()
