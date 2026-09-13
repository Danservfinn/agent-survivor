from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from backend.frontier_roster import (
    FrontierRosterError,
    is_eligible,
    select_frontier_roster,
    verify_roster_against_catalog,
)
from backend.model_rosters import agents_for_roster


def model(
    model_id: str,
    *,
    name: str | None = None,
    intelligence: float | None = None,
    agentic: float | None = None,
    created: int = 1_700_000_000,
    context_length: int = 128_000,
    expiration_date: str | None = None,
    output_modalities: list[str] | None = None,
) -> dict:
    payload = {
        "id": model_id,
        "canonical_slug": model_id,
        "name": name or model_id,
        "created": created,
        "context_length": context_length,
        "expiration_date": expiration_date,
        "architecture": {
            "input_modalities": ["text"],
            "output_modalities": output_modalities or ["text"],
        },
        "benchmarks": {
            "artificial_analysis": {
                "intelligence_index": intelligence,
                "coding_index": intelligence,
                "agentic_index": agentic if agentic is not None else intelligence,
            }
        },
    }
    return payload


FIXTURE_CATALOG = [
    model("openai/gpt-5.5-pro", name="GPT-5.5 Pro", intelligence=48, created=1_700_000_000),
    model("openai/gpt-6-astra", name="GPT-6 Astra", intelligence=53, created=1_780_000_000),
    model("openai/gpt-5-mini", name="GPT-5 Mini", intelligence=40),
    model("anthropic/claude-opus-4.8", name="Claude Opus 4.8", intelligence=49),
    model("anthropic/claude-fable-5.1", name="Claude Fable 5.1", intelligence=53, created=1_780_000_100),
    model("anthropic/claude-sonnet-4.6", name="Claude Sonnet 4.6", intelligence=41),
    model("google/gemini-3.1-pro-preview", name="Gemini 3.1 Pro Preview", intelligence=42),
    model("google/gemini-3.8-pro", name="Gemini 3.8 Pro", intelligence=50, created=1_780_000_200),
    model("google/gemma-4-31b-it", name="Gemma 4 31B", intelligence=30),
    model("x-ai/grok-4.3", name="Grok 4.3", intelligence=40),
    model("x-ai/grok-4.6", name="Grok 4.6", intelligence=44, created=1_780_000_300),
    model("deepseek/deepseek-v4-pro", name="DeepSeek V4 Pro", intelligence=46),
    model("deepseek/deepseek-v4-flash", name="DeepSeek V4 Flash", intelligence=39),
    model("qwen/qwen3.7-max", name="Qwen3.7 Max", intelligence=45),
    model("qwen/qwen3.8-max", name="Qwen3.8 Max", intelligence=53, created=1_780_000_400),
    model("z-ai/glm-5.2", name="GLM 5.2", intelligence=40),
    model("z-ai/glm-5.3", name="GLM 5.3", intelligence=45, created=1_780_000_500),
    model("moonshotai/kimi-k2.7-code", name="Kimi K2.7 Code", intelligence=38),
    model("minimax/minimax-m3", name="MiniMax M3", intelligence=37),
    model("nvidia/nemotron-3-ultra-550b-a55b", name="Nemotron 3 Ultra", intelligence=36),
    model("mistralai/mistral-large-2512", name="Mistral Large 3 2512", intelligence=35),
    model("meta-llama/llama-4-maverick", name="Llama 4 Maverick", intelligence=34),
    model("cohere/command-a", name="Command A", intelligence=33),
    model("ibm-granite/granite-4.1-8b", name="Granite 4.1 8B", intelligence=28, context_length=128_000),
    model("rekaai/reka-edge", name="Reka Edge", intelligence=27),
    model("tencent/hy4-preview", name="Hy4 Preview", intelligence=47),
    model("openai/gpt-6-astra:free", name="GPT-6 Astra Free", intelligence=53),
    model("openai/openrouter-auto", name="OpenRouter Auto", intelligence=99),
    model("expired/lab-old", name="Expired Lab", intelligence=90, expiration_date="2020-01-01"),
    model("tiny/ctx", name="Tiny Context", intelligence=90, context_length=8000),
]


class FrontierRosterTest(unittest.TestCase):
    def test_selects_sixteen_unique_required_authors(self) -> None:
        snapshot = select_frontier_roster(FIXTURE_CATALOG)
        seats = snapshot["seats"]
        self.assertEqual(len(seats), 16)
        self.assertEqual(len({seat["author"] for seat in seats}), 16)
        self.assertEqual([seat["author"] for seat in seats], snapshot["required_authors"])
        self.assertTrue(all(seat["reason"] for seat in seats))

    def test_picks_newer_openai_and_anthropic_flagships(self) -> None:
        snapshot = select_frontier_roster(FIXTURE_CATALOG)
        by_author = {seat["author"]: seat for seat in snapshot["seats"]}
        self.assertEqual(by_author["openai"]["model_id"], "openai/gpt-6-astra")
        self.assertEqual(by_author["anthropic"]["model_id"], "anthropic/claude-fable-5.1")
        self.assertEqual(by_author["x-ai"]["model_id"], "x-ai/grok-4.6")
        self.assertEqual(by_author["qwen"]["model_id"], "qwen/qwen3.8-max")
        self.assertIn("ahead of openai/gpt-5.5-pro", by_author["openai"]["reason"])

    def test_google_prefers_gemini_over_gemma(self) -> None:
        snapshot = select_frontier_roster(FIXTURE_CATALOG)
        by_author = {seat["author"]: seat for seat in snapshot["seats"]}
        self.assertEqual(by_author["google"]["model_id"], "google/gemini-3.8-pro")

    def test_skips_free_expired_router_and_tiny_context(self) -> None:
        now = datetime(2026, 9, 12, tzinfo=timezone.utc)
        self.assertFalse(is_eligible(model("openai/gpt-6-astra:free", intelligence=99), now=now))
        self.assertFalse(is_eligible(model("expired/lab-old", expiration_date="2020-01-01", intelligence=99), now=now))
        self.assertFalse(is_eligible(model("openai/openrouter-auto", name="OpenRouter Auto", intelligence=99), now=now))
        self.assertFalse(is_eligible(model("tiny/ctx", context_length=8000, intelligence=99), now=now))

    def test_downranks_flash_when_pro_exists(self) -> None:
        snapshot = select_frontier_roster(FIXTURE_CATALOG)
        by_author = {seat["author"]: seat for seat in snapshot["seats"]}
        self.assertEqual(by_author["deepseek"]["model_id"], "deepseek/deepseek-v4-pro")
        self.assertIn("downranked", by_author["deepseek"]["reason"])

    def test_missing_required_lab_fails_closed(self) -> None:
        catalog = [item for item in FIXTURE_CATALOG if not str(item["id"]).startswith("tencent/")]
        with self.assertRaises(FrontierRosterError) as ctx:
            select_frontier_roster(catalog)
        self.assertIn("tencent", str(ctx.exception))

    def test_verify_detects_stale_seated_model(self) -> None:
        snapshot = select_frontier_roster(FIXTURE_CATALOG)
        stale_catalog = [
            item if item["id"] != "openai/gpt-6-astra" else model(
                "openai/gpt-7-apex",
                name="GPT-7 Apex",
                intelligence=60,
                created=1_790_000_000,
            )
            for item in FIXTURE_CATALOG
        ]
        stale_catalog.append(model("openai/gpt-6-astra", name="GPT-6 Astra", intelligence=53))
        findings = verify_roster_against_catalog(snapshot, stale_catalog)
        openai_finding = next(item for item in findings if item["author"] == "openai")
        self.assertEqual(openai_finding["fresh_model_id"], "openai/gpt-7-apex")

    def test_verify_passes_when_catalog_matches_lock(self) -> None:
        snapshot = select_frontier_roster(FIXTURE_CATALOG)
        self.assertEqual(verify_roster_against_catalog(snapshot, FIXTURE_CATALOG), [])

    def test_frontier_live_preset_requires_locked_snapshot(self) -> None:
        with patch("backend.model_rosters.load_frontier_snapshot", return_value=None):
            with self.assertRaises(FrontierRosterError):
                agents_for_roster("frontier_live")


if __name__ == "__main__":
    unittest.main()
