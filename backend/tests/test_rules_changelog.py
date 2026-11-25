"""The rules version must change whenever the rules do."""

from __future__ import annotations

import unittest
from datetime import datetime

from backend import rules_changelog


class RulesChangelogTest(unittest.TestCase):
    def test_the_current_rules_have_a_changelog_entry(self) -> None:
        now = rules_changelog.fingerprint()
        pinned = rules_changelog.current().get("fingerprint")
        self.assertEqual(
            now,
            pinned,
            "The rules changed (text, switches, reply fields, steps, challenge, idols, auction, ties, promises, "
            "thinking or score weights). Add a new entry at the end of CHANGES in backend/rules_changelog.py "
            f"saying what changed for players, with \"fingerprint\": \"{now}\". If no rule changed, the entry "
            "says so. Then re-read the Methodology page (frontend/src/app/methodology).",
        )

    def test_versions_count_up_and_dates_move_forward(self) -> None:
        versions = [e["version"] for e in rules_changelog.CHANGES]
        self.assertEqual(versions, list(range(1, len(versions) + 1)))
        since = [datetime.fromisoformat(e["since"]) for e in rules_changelog.CHANGES]
        self.assertEqual(since, sorted(since))
        for entry in rules_changelog.CHANGES:
            self.assertTrue(entry["title"] and entry["changes"], entry["version"])

    def test_a_season_is_matched_to_the_rules_it_was_played_under(self) -> None:
        self.assertEqual(rules_changelog.version_for_season("20261124-175223-lab_mix_openrouter"), 1)
        self.assertEqual(rules_changelog.version_for_season("20261125-140000-lab_mix_openrouter"), 6)
        self.assertIsNone(rules_changelog.version_for_season("20261101-120000-lab_mix_openrouter"))
        self.assertIsNone(rules_changelog.version_for_season("not-a-season"))

    def test_the_fingerprint_ignores_the_process_switches(self) -> None:
        import os
        from unittest.mock import patch

        before = rules_changelog.fingerprint()
        with patch.dict(os.environ, {"SURVIVOR_ADVANTAGES": "0", "SURVIVOR_JURY_RULE": "standard"}):
            self.assertEqual(rules_changelog.fingerprint(), before)
            self.assertFalse(rules_changelog.switches_in_force()["advantages"])
        self.assertTrue(rules_changelog.default_switches()["advantages"])


if __name__ == "__main__":
    unittest.main()
