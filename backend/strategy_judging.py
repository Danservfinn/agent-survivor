"""After a season, a panel of judges scores how sophisticated each player's
strategy was. Execution is measured from the record (season_character); this is
the part that needs judgment.

Each judge reads every player's written strategy at once, under its in-game
tree name only (never a model name): the game plan it wrote at the start and
at half the cast, its strategy after each council, and its private round
plans, beside the public elimination order. It scores each player 1-10 on a
fixed rubric. Judges come from different labs (SURVIVOR_STRATEGY_JUDGES) so a
judge's taste for its own lab's style is diluted; scores are averaged.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from .openrouter_client import _message_content, _parse_json_content, _post_chat_completion

DEFAULT_JUDGES = ("anthropic/claude-opus-5.5", "openai/gpt-6.2-sol-pro")
CRITERIA = {
    "anticipation": "models the other players: their motives, their likely moves, who will turn on whom",
    "endgame": "plans the path to the final three and how to win this particular jury",
    "adaptation": "changes the plan sensibly when the game changes, and says why",
    "information": "uses information, timing, image and deception deliberately",
    "coherence": "a consistent plan that the round-by-round tactics actually serve",
}
RUBRIC = (
    "You are judging the strategies of players in a Survivor-style social strategy game. Score each player 1-10 "
    "on each criterion, comparing the players with each other. Judge the quality of the thinking, not whether it "
    "worked: a sophisticated plan can fail, and a lucky player can win with a plain one. Ignore writing style and "
    "length; a short plan can be the sharpest.\n\nCriteria:\n"
    + "\n".join(f"- {name}: {description}" for name, description in CRITERIA.items())
)


def judges() -> list[str]:
    raw = os.environ.get("SURVIVOR_STRATEGY_JUDGES", "")
    chosen = [item.strip() for item in raw.split(",") if item.strip()]
    return chosen or list(DEFAULT_JUDGES)


def _has(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def strategy_dossiers(conn: sqlite3.Connection) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Per player (by tree name): its written strategy; plus the public elimination order."""
    agents = {row["agent_id"]: row["pseudonym"] for row in conn.execute("SELECT agent_id, pseudonym FROM Agents")}
    dossiers: dict[str, dict[str, Any]] = {name: {"game_plans": [], "strategy_by_round": [], "round_plans": []} for name in agents.values()}
    if _has(conn, "GamePlans"):
        for row in conn.execute("SELECT round, agent_id, plan FROM GamePlans ORDER BY round, id"):
            dossiers[agents[row["agent_id"]]]["game_plans"].append({"round": row["round"], **json.loads(row["plan"] or "{}")})
    if _has(conn, "Strategies"):
        for row in conn.execute("SELECT round, agent_id, text FROM Strategies ORDER BY round, id"):
            dossiers[agents[row["agent_id"]]]["strategy_by_round"].append({"after_round": row["round"], "strategy": row["text"]})
    for row in conn.execute("SELECT round, agent_id, plan FROM PrivatePlans ORDER BY round, id"):
        plan = json.loads(row["plan"] or "{}")
        expected = plan.get("expected_out")
        dossiers[agents[row["agent_id"]]]["round_plans"].append(
            {
                "round": row["round"],
                "threats": plan.get("threats"),
                "allies": plan.get("allies"),
                "image_goal": plan.get("image_goal"),
                "image_moves": plan.get("image_moves"),
                "expected_out": agents.get(expected, expected),
            }
        )
    order = [
        {"round": row["elimination_round"], "voted_out": agents[row["agent_id"]]}
        for row in conn.execute(
            "SELECT agent_id, elimination_round FROM Agents WHERE elimination_round IS NOT NULL ORDER BY elimination_round"
        )
    ]
    return dossiers, order


def _ask_judge(model: str, dossiers: dict[str, Any], order: list[dict[str, Any]]) -> dict[str, Any]:
    names = list(dossiers)
    prompt = (
        f"{RUBRIC}\n\nThe public order in which players were voted out: {json.dumps(order)}\n\n"
        f"Each player's own written strategy (private during the game):\n{json.dumps(dossiers, ensure_ascii=False)}\n\n"
        "Return only one JSON object: {\"players\": {name: {"
        + ", ".join(f"\"{c}\": 1-10" for c in CRITERIA)
        + ", \"reason\": one sentence on what most sets this strategy apart}}}, with an entry for each of: "
        + ", ".join(names)
        + "."
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a careful, impartial judge. Return only valid JSON."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 4000,
    }
    return _parse_json_content(_message_content(_post_chat_completion(payload, os.environ.get("OPENROUTER_API_KEY"))))


def judge_season(db: Path | str, *, models: list[str] | None = None) -> int:
    """Score every player's strategy with each judge and store the scores.
    Replaces earlier ratings from the same judge. Returns rows written."""
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    with closing(conn):
        if not _has(conn, "StrategyRatings"):
            return 0
        dossiers, order = strategy_dossiers(conn)
        if not any(d["game_plans"] or d["strategy_by_round"] for d in dossiers.values()):
            return 0  # a season from before strategies were written
        by_name = {row["pseudonym"]: row["agent_id"] for row in conn.execute("SELECT agent_id, pseudonym FROM Agents")}
        written = 0
        for model in models or judges():
            try:
                reply = _ask_judge(model, dossiers, order)
            except Exception as exc:  # a judge that fails leaves the others' scores standing
                print(f"strategy judge {model} failed: {exc}")
                continue
            conn.execute("DELETE FROM StrategyRatings WHERE judge_model = ?", (model,))
            for name, entry in (reply.get("players") or {}).items():
                agent_id = by_name.get(name)
                if not agent_id or not isinstance(entry, dict):
                    continue
                scores = {}
                for criterion in CRITERIA:
                    try:
                        scores[criterion] = max(1.0, min(10.0, float(entry.get(criterion))))
                    except (TypeError, ValueError):
                        continue
                if not scores:
                    continue
                conn.execute(
                    "INSERT INTO StrategyRatings (agent_id, judge_model, scores, reason) VALUES (?, ?, ?, ?)",
                    (agent_id, model, json.dumps(scores), str(entry.get("reason") or "")[:400]),
                )
                written += 1
            conn.commit()
        return written
