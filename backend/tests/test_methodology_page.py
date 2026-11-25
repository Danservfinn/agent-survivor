"""The Methodology page's hand-written content must match the game it describes.

frontend/src/app/methodology/content.json quotes the players' rules, names reply
formats, prompt examples and the engine sections each card is pinned to. Each
of those must exist in the running code, or the page would show an empty
fold-out or a rule players are never given.
"""

from __future__ import annotations

import gzip
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import llm_context, methodology, methodology_sections, openrouter_client

CONTENT = Path(__file__).resolve().parents[2] / "frontend" / "src" / "app" / "methodology" / "content.json"
FAMILIES = {
    "challenge", "round_plan", "game_plan", "auction", "notebook", "vote", "whisper", "council", "host_answer",
    "parting", "tie", "pitch", "crossfire", "jury_question", "rebuttal", "deliberation", "jury_vote", "talk_choice",
    "talk", "idol_gift", "immunity_choice",
}


def _all_rules() -> list[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("SURVIVOR_")}
    with patch.dict(os.environ, env, clear=True):
        return llm_context.game_rules()


class MethodologyPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.content = json.loads(CONTENT.read_text())
        cls.cards = cls.content["round"] + cls.content["finale"]

    def test_every_quoted_rule_is_one_players_are_given(self) -> None:
        rules = _all_rules()
        phrases = [p for card in self.cards for p in card.get("rules", [])]
        phrases += ["Jurors are told", "Jurors receive", "When three players remain"]  # the finale section's rules
        for phrase in phrases:
            self.assertTrue(any(phrase in r for r in rules), f"no player rule contains {phrase!r}")

    def test_every_reply_format_exists(self) -> None:
        kinds = set(openrouter_client.V3_REPLY_FIELDS) | {"auction"}
        for card in self.cards:
            if "replyKind" in card:
                self.assertIn(card["replyKind"], kinds, card["id"])

    def test_every_card_is_pinned_to_a_section(self) -> None:
        for card in self.cards:
            self.assertIn(card["pin"], methodology_sections.SECTIONS, card["id"])
            self.assertIn(card["who"], self.content["who"], card["id"])

    def test_every_prompt_family_is_one_the_log_can_produce(self) -> None:
        for card in self.cards:
            if "family" in card:
                self.assertIn(card["family"], FAMILIES, card["id"])

    def test_the_log_is_sorted_into_families(self) -> None:
        def entry(user: str, step: str | None = None, reply: dict | None = None) -> dict:
            return {"step": step, "messages": [{"role": "user", "content": user}], "reply": reply or {"summary": "s"}}

        cases = [
            (entry("You are a player... This is the round's challenge."), "challenge"),
            (entry("Task (private: no other player will ever see this):\nThe challenge is over and immunity is settled."), "round_plan"),
            (entry("Task (private: ...):\nBefore the game starts, decide who you are"), "game_plan"),
            (entry("Task (private: ...):\nThe auction is open, once this season."), "auction"),
            (entry("Task (private: ...):\nThe council is over. Rewrite your private notebook"), "notebook"),
            (entry("x", "vote_booth_agent-03"), "vote"),
            (entry("x", "camp_talks", {"invite": ["agent-02"]}), "talk_choice"),
            (entry("x", "camp_talks", {"dialogue": "hi"}), "talk"),
            (entry("give a hidden idol to one other player", "challenge_gift"), "idol_gift"),
            (entry("keep immunity or hand it", "challenge_gift"), "immunity_choice"),
            (entry("x", "jury_vote_agent-05"), "jury_vote"),
            (entry("x", "finale_pitch_agent-07"), "pitch"),
            (entry("x", "challenge_intro"), None),
        ]
        for case, family in cases:
            self.assertEqual(methodology.family_of(case), family, case)

    def test_examples_come_from_the_reference_seasons_log(self) -> None:
        base = Path(tempfile.mkdtemp())
        season = base / "20261126-100000-lab_mix_openrouter"
        season.mkdir()
        lines = [
            {"step": "vote_booth_agent-01", "messages": [{"role": "user", "content": "first"}], "reply": None},
            {"step": "vote_booth_agent-02", "messages": [{"role": "user", "content": "second"}], "reply": {"target_id": "agent-03"}},
            {"step": "vote_booth_agent-04", "messages": [{"role": "user", "content": "third"}], "reply": {"target_id": "agent-05"}},
        ]
        with gzip.open(season / "prompts.jsonl.gz", "wt") as handle:
            handle.write("\n".join(json.dumps(x) for x in lines) + "\nnot json\n")
        examples = methodology.prompt_examples(season)
        self.assertEqual(list(examples), ["vote"])
        self.assertEqual(examples["vote"]["messages"][0]["content"], "second")  # the first with a readable reply
        with patch("backend.season_report.reportable_season_dir", return_value=season):
            self.assertEqual(methodology.prompt_example("vote", base)["season_id"], season.name)
            self.assertIsNone(methodology.prompt_example("pitch", base))
            self.assertEqual(methodology.reference_season(base)["prompt_examples"], ["vote"])


if __name__ == "__main__":
    unittest.main()
