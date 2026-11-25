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
