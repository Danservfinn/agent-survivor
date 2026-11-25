"""Per-model USD pricing and cost estimation for live seasons.

Prices are pulled from the public OpenRouter models catalog (no key needed)
and cached on disk for 24h. Cost per call is estimated from token budgets,
never from guesses. The season runner uses this to enforce the dollar cap.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Any

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

CACHE_PATH = Path(
    os.environ.get(
        "SURVIVOR_PRICING_CACHE",
        str(Path(__file__).resolve().parent.parent / "artifacts" / "pricing_cache.json"),
    )
)

CACHE_TTL_SECONDS = 24 * 60 * 60

# Fallback USD per 1M tokens used only if the catalog is unreachable and no
# cache exists. Conservative (upper-bound) numbers for the cheap roster.
FALLBACK_PRICING = {
    "ibm-granite/granite-4.1-8b": (0.10, 0.10),
    "deepseek/deepseek-v4-flash": (0.20, 0.80),
    "inclusionai/ling-2.6-flash": (0.15, 0.60),
    "google/gemma-4-26b-a4b-it": (0.10, 0.10),
    "google/gemma-4-31b-it": (0.20, 0.20),
    "rekaai/reka-edge": (0.20, 0.20),
    "tencent/hy4-flash": (0.05, 0.20),
    "meta/muse-ember": (0.10, 0.50),
    "qwen/qwen3.5-9b": (0.10, 0.10),
    "openrouter/owl-alpha": (0.25, 0.25),
}

FREE_SUFFIX = ":free"


def _fetch_catalog() -> dict[str, dict[str, float]]:
    request = urllib.request.Request(OPENROUTER_MODELS_URL, method="GET")
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    table: dict[str, dict[str, float]] = {}
    for model in payload.get("data") or []:
        model_id = model.get("id")
        pricing = (model.get("pricing") or {}) if isinstance(model, dict) else {}
        if not model_id or not pricing:
            continue
        try:
            prompt = float(pricing.get("prompt") or 0.0)
            completion = float(pricing.get("completion") or 0.0)
        except (TypeError, ValueError):
            continue
        # catalog prices are USD per token; store per 1M tokens
        table[model_id] = {
            "prompt_per_m": prompt * 1_000_000,
            "completion_per_m": completion * 1_000_000,
            # the most output tokens the model's provider allows in one reply
            "max_out": (model.get("top_provider") or {}).get("max_completion_tokens"),
        }
    return table


def _load_cache() -> dict[str, dict[str, float]] | None:
    try:
        raw = json.loads(CACHE_PATH.read_text())
        table = raw.get("table")
        # a cache written before output limits were stored is treated as stale
        if time.time() - raw.get("fetched_at", 0) < CACHE_TTL_SECONDS and table and any(
            "max_out" in entry for entry in table.values()
        ):
            return table
    except Exception:
        pass
    return None


def _save_cache(table: dict[str, dict[str, float]]) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps({"fetched_at": time.time(), "table": table}))
    except Exception:
        pass


# the catalog this process last loaded; the client reads output limits from it
_ACTIVE_TABLE: dict[str, dict[str, Any]] = {}


# Routers (openrouter/auto, typesafe/jev-router, ...) list their price as -1:
# it depends on the model they pick for each request. Read as a price, -1 per
# token made every estimate hugely negative, which switched cost caps off.
# They are priced at a frontier-flagship rate instead and marked variable.
VARIABLE_PRICE_PER_M = (15.0, 75.0)


def _sanitize(table: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    clean = {}
    for model_id, entry in table.items():
        if entry.get("prompt_per_m", 0) < 0 or entry.get("completion_per_m", 0) < 0:
            entry = {
                **entry,
                "prompt_per_m": VARIABLE_PRICE_PER_M[0],
                "completion_per_m": VARIABLE_PRICE_PER_M[1],
                "variable": True,
            }
        clean[model_id] = entry
    return clean


def get_pricing_table(refresh: bool = False) -> dict[str, dict[str, float]]:
    table = _load_cache() if not refresh else None
    if table is None:
        try:
            table = _fetch_catalog() or None
            if table:
                _save_cache(table)
        except Exception:
            table = None
    if table is None:
        table = {k: {"prompt_per_m": v[0], "completion_per_m": v[1]} for k, v in FALLBACK_PRICING.items()}
    table = _sanitize(table)
    _ACTIVE_TABLE.clear()
    _ACTIVE_TABLE.update(table)
    return table


def max_output_tokens(model_id: str) -> int | None:
    """The model's reply limit from the loaded catalog; None when unknown.
    Never fetches: a live season loads the catalog when it starts."""
    value = (_ACTIVE_TABLE.get(model_id) or {}).get("max_out")
    return int(value) if isinstance(value, (int, float)) and value > 0 else None


def estimate_call_cost_usd(
    model_id: str,
    *,
    input_tokens: int,
    output_tokens: int,
    table: dict[str, dict[str, float]] | None = None,
) -> float:
    table = table if table is not None else get_pricing_table()
    if model_id.endswith(FREE_SUFFIX):
        return 0.0
    entry = table.get(model_id)
    if entry is None:
        # unknown model: use the most expensive known cheap-roster tier as a
        # conservative bound so caps still protect the wallet
        entry = {"prompt_per_m": 1.00, "completion_per_m": 2.00}
    return (
        entry["prompt_per_m"] * input_tokens / 1_000_000
        + entry["completion_per_m"] * output_tokens / 1_000_000
    )


# Token budgets per response kind: (input, output). Contestant calls carry the
# full game record (llm_context.MAX_CONTEXT_CHARS, up to about 10k tokens late
# in a season) on top of a ~2.5k-token prompt; 8000 is a season average.
# v3 replies open with up to ~300 words of private thinking (about 500 tokens;
# 150 words before 2026-11-25) before the decision, so contestant outputs carry
# that on top.
TOKEN_BUDGETS = {
    "conversation": (8000, 920),
    "confessional": (8000, 380),
    "vote": (8000, 920),
    "jury_vote": (8000, 800),
    "finale_pitch": (8000, 920),
    "host": (3600, 300),
    "challenge": (6500, 1500),  # one call answers all five ladder puzzles
    "archetype": (1200, 120),
}


# Every OpenRouter call reasons at "high" effort ("medium" earlier on 2026-11-25)
# (openrouter_client.REASONING_SETTINGS); at "low" this was 400. Medium roughly
# doubles real reasoning (DeepSeek V4 Flash used 900-3,400 tokens at low), so
# this is set 3x the old value until a full season re-measures it. Reasoning
# tokens are billed as output.
ESTIMATED_REASONING_TOKENS = 2400  # high since 2026-11-25 (1200 at medium); re-measure after a season

# The estimate ran about 1.7x under the real OpenRouter bill: a full lab_mix
# season (old prompt, 2026-11-24) was estimated at $6.34 and billed about $10.5.
# The factor makes every estimate, and so every cost cap, mean real dollars.
# Re-measure it after a full season; it is not a constant of nature.
BILL_FACTOR = 1.7


def estimate_step_cost_usd(
    model_ids: list[str],
    response_kind: str,
    *,
    table: dict[str, dict[str, float]] | None = None,
) -> float:
    input_tokens, output_tokens = TOKEN_BUDGETS.get(response_kind, (3500, 420))
    output_tokens += ESTIMATED_REASONING_TOKENS
    return BILL_FACTOR * sum(
        estimate_call_cost_usd(m, input_tokens=input_tokens, output_tokens=output_tokens, table=table)
        for m in model_ids
    )


# ---------------------------------------------------------------- paid entries
# Calls per seat in a season, by kind: the mean of a simulated nine-seat,
# seven-round season with every mechanic on (measured 2026-11-25). A seat that
# reaches the finale made about 1.55 times the mean; an entry is priced as if
# it does. The host made 39 calls.
SEASON_CALLS_PER_SEAT = {
    "conversation": 15.7 + 15.3 + 4.3 + 4.3 + 0.9,  # speeches, talk lines, whispers, talk picks, gift/immunity
    "vote": 7.1,
    "jury_vote": 0.7,
    "finale_pitch": 0.3,
    "challenge": 2.3,
    "confessional": 12.2,  # private writing: plans, notebooks, picks, bids
}
FINALIST_CALL_FACTOR = 1.55
HOST_CALLS_PER_SEASON = 39


def _profile_seat_usd(model_id: str, table: dict[str, dict[str, float]], factor: float = 1.0) -> float:
    return factor * sum(
        estimate_step_cost_usd([model_id], kind, table=table) * calls for kind, calls in SEASON_CALLS_PER_SEAT.items()
    )


def _recorded_usage(seasons_dir: Path) -> tuple[str, dict[str, dict[str, float]], dict[str, float]] | None:
    """Token use per seat and for the host, from the newest finished lab_mix
    house season that recorded its prompts: (season id, per-player usage,
    host usage). Tokens are estimated from text (4 characters a token) plus
    the hidden reasoning estimate per call, as the bill counts reasoning."""
    import gzip

    candidates = sorted(seasons_dir.glob("*-lab_mix_openrouter"), reverse=True) if seasons_dir.exists() else []
    for season in candidates:
        log = next((p for p in (season / "prompts.jsonl.gz", season / "prompts.jsonl") if p.exists()), None)
        card = season / "scorecard.json"
        if log is None or not card.exists():
            continue
        try:
            if not json.loads(card.read_text()).get("finished"):
                continue
            opener = gzip.open if log.suffix == ".gz" else open
            players: dict[str, dict[str, float]] = {}
            host = {"calls": 0.0, "in": 0.0, "out": 0.0}
            with opener(log, "rt", encoding="utf-8") as handle:
                for line in handle:
                    entry = json.loads(line)
                    chars_in = sum(len(str(m.get("content") or "")) for m in entry.get("messages") or [])
                    chars_out = len(json.dumps(entry.get("reply"))) if entry.get("reply") is not None else 0
                    bucket = players.setdefault(entry["player"], {"calls": 0.0, "in": 0.0, "out": 0.0}) if entry.get("player") else host
                    bucket["calls"] += 1
                    bucket["in"] += chars_in / 4
                    bucket["out"] += chars_out / 4 + ESTIMATED_REASONING_TOKENS
        except (OSError, ValueError, KeyError):
            continue
        if players:
            return season.name, players, host
    return None


def estimate_entry_season_cost(
    model_id: str, roster: str = "lab_mix_openrouter", *, seasons_dir: Path | None = None
) -> dict[str, Any]:
    """What a season with a paid entry would cost us, in billed dollars.

    house_usd: the eight house seats the entry leaves in place, plus the host.
    entry_usd: the entry's seat, as if it plays to the finale.
    basis "recorded" rests on the newest finished house season's recorded
    prompts (season_id names it); "estimated" on the measured call profile.
    entry_priced False: the model is not in OpenRouter's catalog, so refuse it.
    """
    from .model_rosters import LAB_MIX_OPENROUTER_MODELS
    from .openrouter_client import _host_model_id
    from .llm_config import get_llm_settings

    if roster != "lab_mix_openrouter":
        raise ValueError("entries play the lab_mix_openrouter roster")
    table = get_pricing_table()
    entry_priced = model_id in table or model_id.endswith(FREE_SUFFIX)
    house = [m["model_id"] for m in LAB_MIX_OPENROUTER_MODELS]
    lab = model_id.split("/", 1)[0]
    same_lab = [m for m in house if m.split("/", 1)[0] == lab]
    # the seat the entry takes: its own lab's, else (seed-chosen, unknown yet)
    # the cheapest house seat, so the house share is never underestimated
    if same_lab:
        replaced = same_lab[0]
    else:
        replaced = min(house, key=lambda m: _profile_seat_usd(m, table))
    remaining = list(house)
    remaining.remove(replaced)
    host_model = _host_model_id(get_llm_settings())

    def price(model: str, tokens_in: float, tokens_out: float) -> float:
        return BILL_FACTOR * estimate_call_cost_usd(model, input_tokens=int(tokens_in), output_tokens=int(tokens_out), table=table)

    if seasons_dir is None:
        seasons_dir = Path(__file__).resolve().parent.parent / "artifacts" / "seasons"
    recorded = _recorded_usage(seasons_dir)
    if recorded is not None:
        season_id, players, host_use = recorded
        seats = list(players.values())
        mean = {k: sum(s[k] for s in seats) / len(seats) for k in ("calls", "in", "out")}
        finalist = max(seats, key=lambda s: s["calls"])
        house_usd = sum(price(m, mean["in"], mean["out"]) for m in remaining) + price(host_model, host_use["in"], host_use["out"])
        entry_usd = price(model_id, finalist["in"], finalist["out"])
        calls_per_seat = round(mean["calls"])
        basis = {"basis": "recorded", "season_id": season_id}
    else:
        host_usd = BILL_FACTOR * HOST_CALLS_PER_SEASON * estimate_call_cost_usd(
            host_model, input_tokens=TOKEN_BUDGETS["host"][0], output_tokens=TOKEN_BUDGETS["host"][1], table=table
        )
        house_usd = sum(_profile_seat_usd(m, table) for m in remaining) + host_usd
        entry_usd = _profile_seat_usd(model_id, table, FINALIST_CALL_FACTOR)
        calls_per_seat = round(sum(SEASON_CALLS_PER_SEAT.values()))
        basis = {"basis": "estimated", "season_id": None}
    return {
        "model_id": model_id,
        "entry_priced": entry_priced,
        # a router's price depends on the model it picks; priced at VARIABLE_PRICE_PER_M
        "variable_price": bool((table.get(model_id) or {}).get("variable")),
        "replaced_model_id": replaced,
        "replaced_because": "same lab" if same_lab else "cheapest seat (the real seat is chosen by the season seed)",
        "house_usd": round(house_usd, 2),
        "entry_usd": round(entry_usd, 2) if entry_priced else None,
        "total_usd": round(house_usd + entry_usd, 2) if entry_priced else None,
        "calls_per_seat": calls_per_seat,
        **basis,
    }
