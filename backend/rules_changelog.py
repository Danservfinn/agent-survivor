"""The rules version, and a dated log of every change to the rules.

A season is only comparable with seasons played under the same rules, and the
Methodology page describes only the rules in force. So the rules carry a
version. `fingerprint()` hashes everything that decides how the game plays:
the rules text every player is given, the default switches, the reply fields,
the round's step lists, the challenge ladder, idol and auction constants, the
thinking and routing settings, and the score weights.

When the fingerprint changes, `test_rules_changelog.py` fails until a new entry
is added to CHANGES with the new fingerprint (an internal change that alters
no rule still gets an entry, saying so). Each season records the version it was
played under; older seasons are matched by date with `version_at`.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
import textwrap
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

# Newest last. `since` is local time (America/New_York), the moment the rules
# took effect; a season started before it was played under the version above.
CHANGES: list[dict[str, Any]] = [
    {
        "version": 1,
        "since": "2026-11-24T15:43",
        "title": "One group, hidden identities",
        "changes": [
            "Players use neutral tree names and never see which model anyone is.",
            "One group all season, no tribes; every player votes every round.",
            "A five-puzzle challenge ladder decides immunity by correct answers; in the first rounds the winner gives a hidden idol to another player.",
            "Players choose who to talk to; vote promises are recorded and checked against the vote.",
            "Private round plans, vote predictions and a notebook each player writes for itself.",
            "Jurors judge a verified record and choose who to question.",
            "A provider outage pauses the season and never counts against a player.",
        ],
    },
    {
        "version": 2,
        "since": "2026-11-24T18:02",
        "title": "Strategic context and an adaptive challenge",
        "changes": [
            "Prompt v3: every player gets a computed situation brief and a strategist's checklist.",
            "The challenge difficulty adapts to the field.",
            "A model's unusable reply costs that player (a silent line or a spoiled vote), never the season.",
        ],
    },
    {
        "version": 3,
        "since": "2026-11-25T00:55",
        "title": "Secret ballots and tribal council",
        "changes": [
            "Secret ballots: players learn the tally and their own vote, never who voted for whom.",
            "Tribal council: everyone speaks once in public before the vote; meetings at camp are visible, their words are not.",
            "Talks of any size; pacts with clean exits and betrayals.",
            "Parting words from the player voted out; the jury deliberates in private.",
            "Ties: everyone speaks, then a revote, then a deadlock vote, then rocks.",
            "Equal challenge scores go to the harder puzzles, then to the faster answer.",
            "Jurors vote by their own judgment.",
        ],
    },
    {
        "version": 4,
        "since": "2026-11-25T02:29",
        "title": "Same thinking for all, reputation and feelings",
        "changes": [
            "Every model thinks at high effort and is routed to its fastest full-precision providers.",
            "A player may win immunity in rounds in a row.",
            "Reputation and image: each player sees how the others can see it, and sets an image goal.",
            "Game plans, confessionals, a last private whisper, personal jury records and recorded feelings.",
        ],
    },
    {
        "version": 5,
        "since": "2026-11-25T04:01",
        "title": "A challenge frontier models cannot saturate",
        "changes": [
            "The challenge is a race: it ends the moment someone answers every puzzle correctly.",
            "Extreme grid puzzles are harder by a hidden rule; a master tier (Mastermind, knights and knaves, counting) tops the ladder.",
            "The public roster starts at the top level.",
        ],
    },
    {
        "version": 6,
        "since": "2026-11-25T13:37",
        "title": "Inner life, provable moves and an auction",
        "changes": [
            "A train of thought within each round; each vote guesses what others expect of the voter; contradictions to face; a private record of every lie.",
            "Forwarded quotes are stamped real by the game; a player may spy on a talk instead of whispering.",
            "Idol searches, shown idols and shown ballots; the challenge winner may hand immunity to another player.",
            "A once-a-season sealed-bid auction for an extra vote, an idol and a peek at the last council's votes.",
            "The host questions a few players at tribal council, from the public record.",
            "The rules text, promise checks and seeding agree with the engine: difficulty rises when any player is perfect, and \"I'll vote for you\" is not a promise.",
        ],
    },
    {
        "version": 7,
        "since": "2026-11-25T14:30",
        "title": "Survivor Index v5",
        "changes": [
            "No change to how the game plays. The score's voting part now counts the round a player was voted out in, as it voted then, and counts rounds with a vote, so an extra vote won at the auction is not a second round.",
        ],
        "fingerprint": "709ecff32281d456",
    },
    {
        "version": 8,
        "since": "2026-11-25T15:43",
        "title": "Strategy rounds: lowest unique number",
        "changes": [
            "Every second round, starting with round 2, the immunity challenge is a strategy game instead of the puzzle ladder: each player secretly picks a whole number from 1 to twice the number of players, and the lowest number picked by exactly one player wins immunity. If no number is unique, nobody is immune. Every pick is public afterwards.",
            "The puzzle ladder's difficulty carries over a strategy round unchanged. Back-to-back immunity is still allowed.",
            "Why: the strongest model solved the puzzle ladder in seconds and could win immunity every round; in a test with the frontier roster, three strategy rounds had two different winners.",
        ],
        "fingerprint": "dc129eea356782f9",
    },
    {
        "version": 9,
        "since": "2026-11-25T15:59",
        "title": "A tenth seat: Jev Router",
        "changes": [
            "The frontier roster has ten models: Jev Router (TypeSafe) joins the nine. It is a router that picks a model and reasoning effort for each request.",
            "With ten players a season has one more round and a jury of seven; the auction comes when seven players remain, and strategy rounds pick from 1 to 20.",
        ],
        "fingerprint": "0a960fd93cd1a3d6",
    },
    {
        "version": 10,
        "since": "2026-11-25T18:00",
        "title": "Survivor Index v6",
        "changes": [
            "No change to how the game plays. The score now counts each part over the councils a player actually sat at, including revotes and spoiled ballots: the finale is no longer a missed vote, a tied first ballot credits the player who left, influence is a share of councils, and votes survived are no longer scaled to the most-targeted player.",
            "Placement leaves the index and is shown on its own; the weights are puzzles 25, voting 15, strategy 35, social 25. Every past season is rescored.",
        ],
        "fingerprint": "cfb170f666f85844",
    },
]


def current() -> dict[str, Any]:
    return CHANGES[-1]


def current_version() -> int:
    return int(current()["version"])


def version_at(when: datetime) -> int | None:
    """The rules version in force at a moment (local time), or None if before version 1."""
    found = None
    for entry in CHANGES:
        if when >= datetime.fromisoformat(entry["since"]):
            found = int(entry["version"])
    return found


def version_for_season(season_id: str) -> int | None:
    """Rules version from a season folder name such as 20261124-175223-lab_mix_openrouter."""
    try:
        return version_at(datetime.strptime(season_id[:15], "%Y%m%d-%H%M%S"))
    except ValueError:
        return None


def code_hash(*objects: Any) -> str:
    """A hash of functions' code that ignores comments, docstrings' layout and blank lines."""
    parts = []
    for obj in objects:
        tree = ast.parse(textwrap.dedent(inspect.getsource(obj)))
        parts.append(ast.dump(tree, annotate_fields=False, include_attributes=False))
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


@contextmanager
def _default_switches() -> Iterator[None]:
    """Read the switches as a fresh install would, whatever this process's env says."""
    saved = {k: v for k, v in os.environ.items() if k.startswith("SURVIVOR_")}
    for key in saved:
        del os.environ[key]
    try:
        yield
    finally:
        os.environ.update(saved)


def switches_in_force() -> dict[str, Any]:
    """The switches as this process reads them (a season may run with one turned off)."""
    from . import llm_config as c
    from .strategy_challenge import strategy_challenges_enabled

    return {
        "secret_ballot": c.secret_ballot(),
        "inner_life": c.inner_life_enabled(),
        "advantages": c.advantages_enabled(),
        "host_questions": c.host_questions_enabled(),
        "strategy_challenges": strategy_challenges_enabled(),
        "feelings": c.feelings_enabled(),
        "jury_rule": c.jury_rule(),
        "prompt_profile": c.prompt_profile(),
    }


def default_switches() -> dict[str, Any]:
    with _default_switches():
        return switches_in_force()


def rule_material() -> dict[str, Any]:
    """Everything that decides how the game plays, in one comparable structure."""
    from . import advantages, auction, benchmark_scoring, challenge_bank, llm_context, model_rosters, openrouter_client, public_gate, strategy_challenge
    from . import turn_controller as tc

    with _default_switches():
        player_rules = llm_context.game_rules()
    return {
        "player_rules": player_rules,
        "rule_texts": {
            "ballot": llm_context._BALLOT_RULE,
            "jury": llm_context._JURY_RULE_TEXT,
            "advantages": llm_context.ADVANTAGE_RULES,
            "inner_life": llm_context.INNER_LIFE_RULE,
            "host_questions": llm_context.HOST_QUESTION_RULE,
        },
        "switches": default_switches(),
        "reply_fields": {kind: [name for name, _ in fields] for kind, fields in openrouter_client.V3_REPLY_FIELDS.items()},
        "steps": code_hash(tc._phase_steps, tc._finale_steps),
        "challenge": {
            "tiers": challenge_bank.DIFFICULTY_TIERS,
            "levels": challenge_bank.LADDER_LEVELS,
            "rung_weights": challenge_bank.RUNG_WEIGHTS,
            "first_level": tc.FIRST_LADDER_LEVEL,
            "first_level_by_roster": tc.FIRST_LEVEL_BY_ROSTER,
            "scoring": code_hash(tc._ensure_challenge_result, tc._ladder_level),
        },
        "strategy_challenge": [
            strategy_challenge.STRATEGY_EVERY,
            code_hash(strategy_challenge.is_strategy_round, strategy_challenge.highest_pick, strategy_challenge.lowest_unique_winner, strategy_challenge.clean_pick),
            code_hash(tc._strategy_result),
        ],
        "idols": [tc.IDOL_GIFT_ROUNDS, tc.IDOL_EXPIRES_AT, tc.IDOL_SEARCH_FROM_ROUND, tc.IDOL_HIDDEN_CHANCE],
        "advantages": [advantages.SPY_CAUGHT_CHANCE, auction.TOKENS_PER_ROUND, auction.PLAYERS_GONE_BEFORE_AUCTION],
        "talks": [tc.PRIVATE_TALKS_PER_ROUND, tc.NOTEBOOK_MAX_CHARS],
        "votes_and_ties": code_hash(tc._eligible_targets, tc._maybe_run_revote, tc._deadlock, tc._winner_tiebreak_key),
        "promises": code_hash(tc._record_promise, tc.promise_record),
        "fairness": {
            "reasoning": openrouter_client.REASONING_SETTINGS,
            "reasoning_headroom": openrouter_client.REASONING_TOKEN_HEADROOM,
            "routing": openrouter_client.PROVIDER_ROUTING,
            "outage_hold_s": openrouter_client.PROVIDER_HOLD_SECONDS,
        },
        "scoring": {"version": benchmark_scoring.SURVIVOR_INDEX_VERSION, "weights": benchmark_scoring.WEIGHTS},
        "roster": [public_gate.PUBLIC_ROSTER, [m["model_id"] for m in model_rosters.LAB_MIX_OPENROUTER_MODELS]],
    }


def fingerprint() -> str:
    blob = json.dumps(rule_material(), sort_keys=True, default=list)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]
