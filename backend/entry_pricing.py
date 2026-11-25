"""What a model-pick entry costs, and whether a model can be entered.

Danny, 2026-11-25: the price is 2 x the estimated season cost (house seats,
the entry's seat and the host), rounded up to the next $5, never below $50.
A season stops if its spend reaches 85% of what was paid. Models with a
variable catalog price (routers such as openrouter/auto) are refused: they are
not one model, and their cost cannot be quoted before the season.

The estimate is live_cost.estimate_entry_season_cost; its basis turns from
"estimated" to "recorded" once a billed frontier season exists.
"""

from __future__ import annotations

import math
import os
import time
from typing import Any

from . import live_cost
from .model_rosters import EntryError, validate_entry

FLOOR_CENTS = 5000
MULTIPLIER = 2.0
STEP_CENTS = 500
CAP_SHARE = 0.85
CHECK_TTL_S = 3600

_checks: dict[str, tuple[float, dict[str, Any]]] = {}


class QuoteError(ValueError):
    """A model that cannot be entered, with a reason fit to show the buyer."""


def price_cents_for(total_usd: float) -> int:
    raw = math.ceil(total_usd * MULTIPLIER * 100)
    stepped = math.ceil(raw / STEP_CENTS) * STEP_CENTS
    return max(FLOOR_CENTS, stepped)


def cap_cents_for(price_cents: int) -> int:
    return int(price_cents * CAP_SHARE)


def live_check(model_id: str) -> dict[str, Any]:
    """One real call: does the model answer on OpenRouter right now? Cached for an hour."""
    from .frontier_roster import chat_preflight

    now = time.time()
    hit = _checks.get(model_id)
    if hit and now - hit[0] < CHECK_TTL_S:
        return hit[1]
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return {"ok": False, "error": "the server has no OpenRouter key"}
    result = chat_preflight(model_id, key)
    result = {"ok": bool(result.get("ok")), "error": "the model did not answer in the required format" if not result.get("ok") else None}
    _checks[model_id] = (now, result)
    return result


def quote(model_id: str, *, check: bool = True) -> dict[str, Any]:
    """Price an entry for `model_id`, or raise QuoteError saying why it cannot be entered."""
    model_id = str(model_id or "").strip()
    try:
        validate_entry({"name": "Quote", "model_id": model_id})
    except EntryError as exc:
        raise QuoteError(str(exc)) from None
    estimate = live_cost.estimate_entry_season_cost(model_id)
    if not estimate.get("entry_priced"):
        raise QuoteError("that model is not in OpenRouter's catalog")
    if estimate.get("variable_price"):
        raise QuoteError("routers and other models without a fixed price cannot be entered; pick a specific model")
    if check:
        result = live_check(model_id)
        if not result["ok"]:
            raise QuoteError(f"that model did not answer a test call ({result['error']}); try another")
    price = price_cents_for(float(estimate["total_usd"]))
    return {
        "model_id": model_id,
        "price_cents": price,
        "cap_cents": cap_cents_for(price),
        "estimate": {k: estimate.get(k) for k in ("house_usd", "entry_usd", "total_usd", "basis", "season_id", "replaced_model_id", "replaced_because", "calls_per_seat")},
    }
