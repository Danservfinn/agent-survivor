"""The facts feed behind the Methodology page."""

from __future__ import annotations

import inspect
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import benchmark_scoring, methodology, openrouter_client, rules_changelog


class MethodologyFactsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.facts = methodology.facts(Path(tempfile.mkdtemp()))

    def test_the_feed_is_plain_json(self) -> None:
        json.dumps(self.facts)
        for key in ("rules", "player_rules", "switches", "roster", "challenge", "idols", "advantages", "reply_fields",
                    "reply_common", "fairness", "host_model", "strategy_judges", "scoring", "cost", "glossary", "sections"):
            self.assertIn(key, self.facts)

    def test_numbers_come_from_the_code(self) -> None:
        self.assertEqual(self.facts["scoring"]["weights"], benchmark_scoring.WEIGHTS)
        self.assertEqual(self.facts["rules"]["version"], rules_changelog.current_version())
        self.assertEqual(self.facts["rules"]["changes"][0]["version"], rules_changelog.current_version())
        self.assertEqual(self.facts["fairness"]["reasoning_effort"], openrouter_client.REASONING_SETTINGS["effort"])
        self.assertEqual(self.facts["roster"]["players"], len(self.facts["roster"]["models"]))

    def test_every_reply_field_is_listed_with_its_wording(self) -> None:
        for kind, fields in openrouter_client.V3_REPLY_FIELDS.items():
            self.assertEqual([f["field"] for f in self.facts["reply_fields"][kind]], [name for name, _ in fields])
        vote = {f["field"]: f for f in self.facts["reply_fields"]["vote"]}
        self.assertTrue(vote["show_ballot"]["optional"])
        self.assertEqual(vote["show_ballot"]["needs"], "advantages")
        self.assertEqual(vote["expected_of_me"]["needs"], "inner_life")
        self.assertFalse(vote["target_id"]["optional"])
        self.assertIn("auction", self.facts["reply_fields"])

    def test_the_shared_reply_fields_match_the_prompt_builder(self) -> None:
        source = inspect.getsource(openrouter_client._agent_prompt_v3)
        order = [source.index(f'("{name}",') for name in (*methodology.COMMON_FIELDS_FIRST, *methodology.COMMON_FIELDS_LAST)]
        self.assertEqual(order, sorted(order))
        self.assertLess(order[1], source.index("step_fields = V3_REPLY_FIELDS"))
        self.assertGreater(order[2], source.index("fields += step_fields"))

    def test_nothing_secret_is_in_the_feed(self) -> None:
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-v1-" + "a" * 64}):
            methodology._code_facts.cache_clear()
            blob = json.dumps(methodology.facts(Path(tempfile.mkdtemp())))
        methodology._code_facts.cache_clear()
        self.assertNotIn("sk-or-", blob)
        self.assertIsNone(re.search(r"sk-[A-Za-z0-9-]{20,}", blob))

    def test_the_reference_season_is_the_pinned_one_or_the_newest(self) -> None:
        base = Path(tempfile.mkdtemp())
        self.assertIsNone(methodology.reference_season(base))
        for name in ("20261124-175223-lab_mix_openrouter", "20261125-150000-lab_mix_openrouter"):
            (base / name).mkdir()
            (base / name / "scorecard.json").write_text(json.dumps({"finished": True}))
        with patch("backend.season_report.reportable_season_dir", return_value=base / "20261125-150000-lab_mix_openrouter"):
            ref = methodology.reference_season(base)
        self.assertEqual(ref["season_id"], "20261125-150000-lab_mix_openrouter")
        self.assertFalse(ref["pinned"])
        self.assertEqual(ref["rules_version"], 6)
        pin = base / "reference.json"
        pin.write_text(json.dumps({"season_id": "20261124-175223-lab_mix_openrouter"}))
        with patch.object(methodology, "REFERENCE_PATH", pin):
            ref = methodology.reference_season(base)
        self.assertEqual(ref, {"season_id": "20261124-175223-lab_mix_openrouter", "pinned": True, "rules_version": 1,
                               "current_rules": False})

    def test_the_api_serves_the_feed(self) -> None:
        from backend import api

        self.assertEqual(api.api_methodology()["rules"]["version"], rules_changelog.current_version())

    def test_the_report_reads_the_shared_glossary_and_rules_version(self) -> None:
        from backend.season_report import render_report_html

        report = {"season": {"season_id": "x", "weights": benchmark_scoring.WEIGHTS, "index_version": 4,
                             "rules": "social-game", "rules_version": 1}, "takeaways": []}
        with patch("backend.season_story.build_thread", return_value=[]):
            page = render_report_html(report)
        blob = json.loads(re.search(r'id="report-data" type="application/json">(.*?)</script>', page, re.S).group(1).replace("<\\/", "</"))
        self.assertEqual(blob["glossary"][0]["term"], "Survivor Index")
        self.assertIn("version 4", blob["glossary"][0]["text"])
        self.assertEqual(blob["rules"], {"version": 1, "current": rules_changelog.current_version()})
        self.assertNotIn('["Survivor Index", "A 0 to 100', page)


if __name__ == "__main__":
    unittest.main()
