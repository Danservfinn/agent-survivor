"""Facts for the Methodology page, read from the running code.

The page's prose is written by hand, but every number, switch, rule text and
reply field it shows comes from here, so the page follows the game when the
game changes. `GET /api/methodology` serves `facts()`.

Nothing here is secret: model ids, limits and rule texts are what every
player and every reader may see. No key, token or private game text is read.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import (
    advantages,
    auction,
    benchmark_scoring,
    challenge_bank,
    glossary,
    live_cost,
    llm_context,
    methodology_sections,
    model_rosters,
    openrouter_client,
    public_gate,
    rules_changelog,
    season_character,
    strategy_judging,
)
from . import turn_controller as tc
from .llm_config import get_llm_settings

REFERENCE_PATH = Path(__file__).resolve().parent.parent / "artifacts" / "methodology" / "reference.json"

# Every action reply opens with think and summary and closes with confidence and
# dialogue, around the step's own fields (openrouter_client._agent_prompt_v3).
# test_methodology_facts checks these against that function's code.
COMMON_FIELDS_FIRST = ("think", "summary")
COMMON_FIELDS_LAST = ("confidence", "dialogue")


def _reply_fields() -> dict[str, list[dict[str, Any]]]:
    """Every field a player may return, per kind of step, with the wording it is given."""
    optional = openrouter_client.INNER_LIFE_FIELDS | openrouter_client.ADVANTAGE_FIELDS
    out = {
        kind: [
            {
                "field": name,
                "says": text,
                "optional": name in optional or text.startswith("optional"),
                "needs": "inner_life" if name in openrouter_client.INNER_LIFE_FIELDS else "advantages" if name in openrouter_client.ADVANTAGE_FIELDS else None,
            }
            for name, text in fields
        ]
        for kind, fields in openrouter_client.V3_REPLY_FIELDS.items()
    }
    out["auction"] = [{"field": name, "says": text, "optional": False, "needs": "advantages"} for name, text in tc.AUCTION_FIELDS.items()]
    return out


@lru_cache(maxsize=1)
def _code_facts() -> dict[str, Any]:
    """The parts that cannot change while this process runs."""
    settings = get_llm_settings()
    roster = model_rosters.LAB_MIX_OPENROUTER_MODELS
    return {
        "rules": {
            "version": rules_changelog.current_version(),
            "since": rules_changelog.current()["since"],
            "title": rules_changelog.current()["title"],
            "fingerprint": rules_changelog.fingerprint(),
            "changes": list(reversed(rules_changelog.CHANGES)),
        },
        "player_rules": llm_context.game_rules(),
        "switches": rules_changelog.switches_in_force(),
        "switch_defaults": rules_changelog.default_switches(),
        "roster": {
            "name": public_gate.PUBLIC_ROSTER,
            "players": len(roster),
            "models": [{"model": m["display_name"], "model_id": m["model_id"]} for m in roster],
            "aliases": list(_aliases()),
        },
        "challenge": {
            "puzzles_per_round": len(challenge_bank.LADDER_LEVELS[0]),
            "tiers": list(challenge_bank.DIFFICULTY_TIERS),
            "levels": [list(level) for level in challenge_bank.LADDER_LEVELS],
            "rung_weights": list(challenge_bank.RUNG_WEIGHTS),
            "first_level": tc.FIRST_LEVEL_BY_ROSTER.get(public_gate.PUBLIC_ROSTER, tc.FIRST_LADDER_LEVEL),
        },
        "idols": {
            "gift_rounds": tc.IDOL_GIFT_ROUNDS,
            "expires_at_players": tc.IDOL_EXPIRES_AT,
            "search_from_round": tc.IDOL_SEARCH_FROM_ROUND,
            "hidden_chance": tc.IDOL_HIDDEN_CHANCE,
        },
        "advantages": {
            "spy_caught_chance": advantages.SPY_CAUGHT_CHANCE,
            "auction_tokens_per_round": auction.TOKENS_PER_ROUND,
            "auction_after_players_gone": auction.PLAYERS_GONE_BEFORE_AUCTION,
        },
        "talks": {"per_round": tc.PRIVATE_TALKS_PER_ROUND, "notebook_max_chars": tc.NOTEBOOK_MAX_CHARS},
        "council": {"host_questions": tc.HOST_QUESTIONS},
        "reply_fields": _reply_fields(),
        "reply_common": {"first": list(COMMON_FIELDS_FIRST), "last": list(COMMON_FIELDS_LAST)},
        "fairness": {
            "reasoning_effort": openrouter_client.REASONING_SETTINGS.get("effort"),
            "reasoning_hidden": bool(openrouter_client.REASONING_SETTINGS.get("exclude")),
            "reasoning_headroom_tokens": openrouter_client.REASONING_TOKEN_HEADROOM,
            "provider_sort": openrouter_client.PROVIDER_ROUTING.get("sort"),
            "precisions": list(openrouter_client.PROVIDER_ROUTING.get("quantizations") or []),
            "timeout_s": settings.timeout_seconds,
            "outage_hold_s": openrouter_client.PROVIDER_HOLD_SECONDS,
            "context_max_chars": llm_context.MAX_CONTEXT_CHARS,
        },
        "host_model": settings.default_model_id,
        "strategy_judges": strategy_judging.judges(),
        "scoring": {
            "index_version": benchmark_scoring.SURVIVOR_INDEX_VERSION,
            "weights": dict(benchmark_scoring.WEIGHTS),
            "character_min_evidence": season_character.MIN_EVIDENCE,
        },
        "cost": {"cap_cents": public_gate.PUBLIC_RUN_MAX_COST_CENTS, "bill_factor": live_cost.BILL_FACTOR},
        "glossary": glossary.definitions(benchmark_scoring.WEIGHTS, benchmark_scoring.SURVIVOR_INDEX_VERSION),
        "sections": methodology_sections.status(),
    }


def _aliases() -> tuple[str, ...]:
    from .database import PLAYER_ALIASES

    return PLAYER_ALIASES


def reference_season(seasons_dir: Path | None = None) -> dict[str, Any] | None:
    """The season the page's examples come from: pinned in reference.json, else the newest finished one."""
    from .live_season import SEASONS_DIR
    from .season_report import reportable_season_dir

    base = Path(seasons_dir or SEASONS_DIR)
    season_id = None
    try:
        season_id = json.loads(REFERENCE_PATH.read_text()).get("season_id")
    except (FileNotFoundError, ValueError):
        pass
    folder = base / season_id if season_id and (base / season_id).is_dir() else reportable_season_dir(base)
    if not folder:
        return None
    version = None
    try:
        version = json.loads((folder / "scorecard.json").read_text()).get("rules_version")
    except (FileNotFoundError, ValueError):
        pass
    version = version or rules_changelog.version_for_season(folder.name)
    return {
        "season_id": folder.name,
        "pinned": bool(season_id) and folder.name == season_id,
        "rules_version": version,
        "current_rules": version == rules_changelog.current_version(),
        "prompt_examples": sorted(prompt_examples(folder)),
    }


# --- real prompts and replies, one per kind of step, from a season's prompt log

def family_of(entry: dict[str, Any]) -> str | None:
    """Which kind of step a recorded call was, from its step line or its opening text."""
    text = "\n".join(str(m.get("content") or "") for m in entry.get("messages") or [] if m.get("role") == "user")
    reply = entry.get("reply") if isinstance(entry.get("reply"), dict) else {}
    step = entry.get("step") or ""
    if "This is the round's challenge." in text:
        return "challenge"
    if "Task (private" in text:
        task = text.split("Task (private", 1)[1]
        for opening, family in (
            ("The challenge is over", "round_plan"),
            ("Before the game starts", "game_plan"),
            ("Half the cast is gone", "game_plan"),
            ("The auction is open", "auction"),
            ("The council is over", "notebook"),
        ):
            if opening in task[:400]:
                return family
        return None
    prefixes = (
        ("vote_booth_", "vote"),
        ("camp_followup", "whisper"),
        ("tribal_council", "council"),
        ("host_questioning", "host_answer"),
        ("parting_words", "parting"),
        ("elimination", "tie"),
        ("finale_pitch_", "pitch"),
        ("finale_crossfire", "crossfire"),
        ("jury_questions", "jury_question"),
        ("finale_rebuttal", "rebuttal"),
        ("jury_deliberation", "deliberation"),
        ("jury_vote_", "jury_vote"),
    )
    if step == "camp_talks":
        return "talk_choice" if ("invite" in reply or "search" in reply) else "talk"
    if step == "challenge_gift":
        return "idol_gift" if "hidden idol" in text else "immunity_choice"
    for prefix, family in prefixes:
        if step.startswith(prefix):
            return family
    return None


_examples_cache: dict[tuple[str, float], dict[str, dict[str, Any]]] = {}


def prompt_examples(season_dir: Path) -> dict[str, dict[str, Any]]:
    """{family: the first recorded call of that kind with a readable reply}. Empty before recording began."""
    import gzip

    from .prompt_log import GZ_NAME

    path = Path(season_dir) / GZ_NAME
    if not path.is_file():
        return {}
    key = (str(path), path.stat().st_mtime)
    if key not in _examples_cache:
        found: dict[str, dict[str, Any]] = {}
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                family = family_of(entry)
                if family and family not in found and entry.get("reply"):
                    found[family] = entry
        _examples_cache[key] = found
    return _examples_cache[key]


def prompt_example(family: str, seasons_dir: Path | None = None) -> dict[str, Any] | None:
    ref = reference_season(seasons_dir)
    if not ref:
        return None
    from .live_season import SEASONS_DIR

    entry = prompt_examples(Path(seasons_dir or SEASONS_DIR) / ref["season_id"]).get(family)
    return {"season_id": ref["season_id"], "family": family, **entry} if entry else None


def facts(seasons_dir: Path | None = None) -> dict[str, Any]:
    return {"ok": True, **_code_facts(), "reference": reference_season(seasons_dir)}
