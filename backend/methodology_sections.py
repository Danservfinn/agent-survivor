"""Which engine functions each Methodology section describes, and whether they changed.

Each section of the Methodology page is written by hand and describes some of
the engine's functions. Their code hashes are pinned in
methodology_sections.json. When a described function changes, the section is
"stale": test_methodology_sections.py fails, naming the section to re-read, and
the facts feed marks it so the page shows "being updated" instead of a wrong
rule. After re-reading a section, re-pin it:

    python3 -m backend.methodology_sections --update <section id>   (or --update all)
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

from .rules_changelog import code_hash

PINS_PATH = Path(__file__).with_name("methodology_sections.json")

# section id -> (title, functions it describes, as module.function under backend)
SECTIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "setup": ("Setup and hidden identities", ("database._with_aliases", "database._shuffled_roster", "llm_context._visible_game_state")),
    "round": ("A round, step by step", ("turn_controller._phase_steps", "turn_controller.start_next_round")),
    "challenge": (
        "The challenge",
        ("turn_controller._ensure_challenge_result", "turn_controller._ladder_level", "challenge_bank.ladder_for_round", "challenge_bank.grade_answer"),
    ),
    "idols": (
        "Idols and immunity",
        (
            "turn_controller._challenge_gift_events",
            "turn_controller._immunity_choice_events",
            "turn_controller._idol_search_events",
            "turn_controller._idol_plays_for_round",
            "turn_controller._expire_idols_if_due",
        ),
    ),
    "talks": (
        "Talks, promises and pacts",
        (
            "turn_controller._camp_talk_events",
            "turn_controller._record_promise",
            "turn_controller.promise_record",
            "turn_controller._maybe_update_alliance",
            "turn_controller._apply_pact_exits",
        ),
    ),
    "information": ("Quotes, spying and proof", ("advantages.receipts_for", "advantages.line_stamps", "advantages.find_talk", "advantages.spy_caught")),
    "inner_life": (
        "Inner life and lies",
        ("inner_life.record_lie", "inner_life.train_of_thought", "inner_life.expectation_read", "inner_life.contradictions"),
    ),
    "vote": (
        "Tribal council and the vote",
        (
            "turn_controller._host_questioning_event",
            "turn_controller._tribal_council_event",
            "turn_controller._resolve_vote_decision",
            "turn_controller._eligible_targets",
        ),
    ),
    "ties": ("Ties", ("turn_controller._maybe_run_revote", "turn_controller._deadlock")),
    "auction": ("The auction", ("auction.tokens", "auction.auction_due", "auction.clean_bids", "auction.winners")),
    "finale": (
        "The finale",
        (
            "turn_controller._finale_steps",
            "turn_controller._finalist_dossier",
            "turn_controller.juror_history",
            "turn_controller._jury_rows",
            "turn_controller._resolve_jury_decision",
            "turn_controller._winner_tiebreak_key",
        ),
    ),
    "scoring": ("How the score is built", ("benchmark_scoring.score_season", "season_character.build_character", "strategy_judging.judges")),
    "replies": ("What every prompt and reply holds", ("openrouter_client._agent_prompt_v3",)),
    "fairness": (
        "Fairness controls",
        (
            "openrouter_client._post_chat_completion",
            "openrouter_client.request_agent_action",
            "openrouter_client.request_challenge_solution",
            "openrouter_client.request_private_writing",
        ),
    ),
}


def _resolve(dotted: str) -> Any:
    module, _, name = dotted.rpartition(".")
    return getattr(importlib.import_module(f"backend.{module}"), name)


def current_hash(section: str) -> str:
    return code_hash(*(_resolve(name) for name in SECTIONS[section][1]))


def pins() -> dict[str, str]:
    try:
        return json.loads(PINS_PATH.read_text()).get("pins", {})
    except FileNotFoundError:
        return {}


def status() -> dict[str, dict[str, Any]]:
    """{section: {title, describes, fresh}}. A section with no pin counts as stale."""
    pinned = pins()
    return {
        sid: {"title": title, "describes": list(names), "fresh": pinned.get(sid) == current_hash(sid)}
        for sid, (title, names) in SECTIONS.items()
    }


def stale() -> list[str]:
    return [sid for sid, info in status().items() if not info["fresh"]]


def update(sections: list[str]) -> list[str]:
    pinned = pins()
    for sid in sections:
        pinned[sid] = current_hash(sid)
    PINS_PATH.write_text(json.dumps({"pins": dict(sorted(pinned.items()))}, indent=2) + "\n")
    return sections


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--update"]:
        chosen = list(SECTIONS) if args[1:] in ([], ["all"]) else args[1:]
        unknown = [s for s in chosen if s not in SECTIONS]
        if unknown:
            sys.exit(f"unknown section(s): {', '.join(unknown)}")
        print("pinned:", ", ".join(update(chosen)))
    else:
        old = stale()
        print("stale:", ", ".join(old) if old else "none")
        sys.exit(1 if old else 0)
