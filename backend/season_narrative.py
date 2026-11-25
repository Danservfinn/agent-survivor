"""Deterministic per-season narrative built from recorded StoryEvents.

No LLM calls: the summary is assembled from the models' own recorded
speech and private strategy statements, so it is free, instant, and
byte-identical on every render.
"""

from __future__ import annotations

import json
import re
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
    """agent id -> the name people see: the real model name."""
    return {r["agent_id"]: _display(r) for r in conn.execute("SELECT * FROM Agents")}


def _display(row: sqlite3.Row) -> str:
    # seasons before 2026-11-24 have no display_name and kept the model name in pseudonym
    if "display_name" in row.keys() and row["display_name"]:
        return row["display_name"]
    return row["pseudonym"]


def _alias_map(conn: sqlite3.Connection) -> dict[str, str]:
    """in-game alias -> real model name, for names parsed out of stored event text."""
    return {
        r["pseudonym"]: r["display_name"]
        for r in conn.execute("SELECT * FROM Agents")
        if "display_name" in r.keys() and r["display_name"]
    }


def _swap_names(text: str, aliases: dict[str, str]) -> str:
    """Replace whole-word aliases with model names in short engine-written strings
    (vote targets, eliminations, tallies). Quoted speech is left as the model said it."""
    if not text or not aliases:
        return text
    pattern = re.compile(r"\b(" + "|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True)) + r")\b")
    return pattern.sub(lambda m: aliases[m.group(1)], text)


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
        aliases = _alias_map(conn)
        rounds: list[dict[str, Any]] = []
        # confessionals to the camera, by round (seasons from 2026-11-25 on)
        confessionals: dict[int, list[dict[str, str]]] = {}
        try:
            for row in conn.execute("SELECT round, agent_id, text FROM Confessionals ORDER BY round, id"):
                confessionals.setdefault(row["round"], []).append(
                    {"who": names.get(row["agent_id"], row["agent_id"]), "text": _swap_names(_clean(row["text"]), aliases)}
                )
        except sqlite3.OperationalError:
            pass  # older seasons have no Confessionals table
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
                    challenge_line = _swap_names(sub, aliases)
                    try:
                        payload = json.loads(e["payload"] or "{}")
                        winners = payload.get("immunity_agent_ids") or []
                        immunity = [names.get(w, w) for w in winners]
                        if payload.get("standings") is not None:
                            # ladder seasons (2026-11-24 on): every player's score, no timing
                            winner_id = payload.get("winning_agent_id")
                            challenge = {
                                "type": "ladder",
                                "winner": names.get(winner_id, winner_id) if winner_id else None,
                                "winner_id": winner_id or "",
                                "immunity": immunity,
                                "standings": [
                                    {
                                        "who": names.get(row["agent_id"], row["agent_id"]),
                                        "correct": row["correct"],
                                        "sudden_death": row.get("sudden_death"),
                                        "guess": row.get("guess"),
                                        "seconds": row.get("seconds"),
                                    }
                                    for row in payload["standings"]
                                ],
                                "guess_target": payload.get("guess_target"),
                                "ladder_level": payload.get("ladder_level"),
                                "barred_last_winner": payload.get("barred_last_winner"),
                                "idol_given": any(
                                    (_payload(g) or {}).get("idol_given")
                                    for g in events
                                    if g["kind"] == "challenge_gift"
                                ),
                            }
                            break
                        challenge = {
                            "type": payload.get("challenge_type", ""),
                            "winner": names.get(payload.get("winning_agent_id", ""), payload.get("winning_agent_name", "")),
                            "winner_id": payload.get("winning_agent_id", ""),
                            "tribe": payload.get("winning_tribe_id") or "",
                            "solved": not str(payload.get("result_status", "")).endswith("unsolved_fastest"),
                            "immunity": immunity,
                        }
                    except Exception:
                        immunity = []
                    break

            # older seasons: every attempt, who tried, how fast, who solved first
            if challenge is not None and challenge.get("type") != "ladder":
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
                    target = _swap_names(e["subtitle"].replace("Vote: ", "").replace("Revote: ", ""), aliases)
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
                    elimination = _swap_names((e["title"] or "").replace(" Leaves the Game", ""), aliases)
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
                    "confessionals": confessionals.get(rn, []),
                }
            )

        finale = None
        for e in conn.execute(
            "SELECT * FROM StoryEvents WHERE kind='winner_declared' ORDER BY id DESC LIMIT 1"
        ):
            tally_line = _swap_names(e["subtitle"] or "", aliases)
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
            sub = _swap_names(sub, aliases)
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
