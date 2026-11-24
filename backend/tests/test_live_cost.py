"""Tests for live cost estimation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.live_cost import (  # noqa: E402
    TOKEN_BUDGETS,
    estimate_call_cost_usd,
    estimate_step_cost_usd,
    get_pricing_table,
)


TABLE = {
    "org/cheap": {"prompt_per_m": 0.20, "completion_per_m": 0.60},
    "org/free": {"prompt_per_m": 0.0, "completion_per_m": 0.0},
}


class CostTest(unittest.TestCase):
    def test_known_model_cost(self) -> None:
        # 3500 in, 420 out at 0.20/0.60 per 1M
        cost = estimate_call_cost_usd("org/cheap", input_tokens=3500, output_tokens=420, table=TABLE)
        expected = 0.20 * 3500 / 1e6 + 0.60 * 420 / 1e6
        self.assertAlmostEqual(cost, expected, places=9)

    def test_free_model_is_zero(self) -> None:
        self.assertEqual(
            estimate_call_cost_usd("org/x:free", input_tokens=100000, output_tokens=10000, table=TABLE),
            0.0,
        )

    def test_unknown_model_uses_conservative_bound(self) -> None:
        cost = estimate_call_cost_usd("org/unknown", input_tokens=3500, output_tokens=420, table=TABLE)
        expected = 1.00 * 3500 / 1e6 + 2.00 * 420 / 1e6
        self.assertAlmostEqual(cost, expected, places=9)

    def test_step_cost_sums_models(self) -> None:
        one = estimate_step_cost_usd(["org/cheap"], "conversation", table=TABLE)
        two = estimate_step_cost_usd(["org/cheap", "org/cheap"], "conversation", table=TABLE)
        self.assertAlmostEqual(two, one * 2, places=9)

    def test_token_budgets_defined_for_all_kinds(self) -> None:
        for kind, (inp, out) in TOKEN_BUDGETS.items():
            self.assertGreater(inp, 0)
            self.assertGreater(out, 0)

    def test_pricing_table_loads_offline_fallback(self) -> None:
        with mock.patch("backend.live_cost._fetch_catalog", side_effect=OSError("offline")):
            table = get_pricing_table(refresh=True)
        self.assertIn("deepseek/deepseek-v4-flash", table)

    def test_full_cheap_season_estimate_under_cap(self) -> None:
        # rough full-season shape: 8 agents, 12 rounds, per round:
        # 8 challenge + ~20 conversation/confessional + ~8 vote + 1 host calls
        per_round_calls = 8 * 1 + 20 * 1 + 8 * 1 + 1
        table = {m: {"prompt_per_m": 0.20, "completion_per_m": 0.60} for m in ["org/cheap"]}
        per_round_cost = (
            estimate_call_cost_usd("org/cheap", **{"input_tokens": 2200, "output_tokens": 360}, table=table) * 8
            + estimate_call_cost_usd("org/cheap", **{"input_tokens": 3500, "output_tokens": 420}, table=table) * 28
            + estimate_call_cost_usd("org/cheap", **{"input_tokens": 3600, "output_tokens": 300}, table=table)
        )
        season = per_round_cost * 12
        self.assertLess(season, 5.0, f"cheap season estimate {season:.2f} USD exceeds smoke budget")


if __name__ == "__main__":
    unittest.main()
