"""The report page itself: what a hostile name or speech can do to it, and
whether it still renders when the data is odd. Rendering checks run headless
Chrome and are skipped when no Chrome is installed."""

from __future__ import annotations

import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend import season_report
from backend.season_report import build_season_report, fill_template, render_report_html
from backend.tests.report_fixtures import copy_season, rendered_dom

DATA_BLOCK = re.compile(r'<script id="report-data" type="application/json">(.*?)</script>', re.S)
NOTICE = '">This part of the report could not be shown'  # the notice as rendered text, not the script source
SECTIONS = ("moments", "leaderboard", "players", "insights", "character", "rounds", "finale", "method")


def _data(html: str) -> dict:
    blocks = DATA_BLOCK.findall(html)
    assert len(blocks) == 1, f"{len(blocks)} data blocks"
    return json.loads(blocks[0])


class HostileTextTest(unittest.TestCase):
    """A paid entry names its own player, and every player writes speeches."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.season_dir = copy_season(Path(cls.tmp.name))
        conn = sqlite3.connect(cls.season_dir / "season.db")
        # the winner is renamed to the template's own data slot; a speech tries to open a comment
        winner = conn.execute("SELECT winner_agent_id FROM GameState").fetchone()[0] if _has_column(conn, "GameState", "winner_agent_id") else None
        target = winner or conn.execute("SELECT agent_id FROM Agents ORDER BY agent_id LIMIT 1").fetchone()[0]
        conn.execute("UPDATE Agents SET display_name = ? WHERE agent_id = ?", ("__REPORT_DATA__", target))
        conn.execute(
            "UPDATE StoryEvents SET dialogue = ? WHERE id = (SELECT MIN(id) FROM StoryEvents WHERE kind = 'vote_booth')",
            ("<!--<script>window.pwned=1</script>-->",),
        )
        conn.commit()
        conn.close()
        cls.report = build_season_report(cls.season_dir)
        # make sure the slot name reaches the link-preview text, as a winner's name does
        cls.report["takeaways"] = [{"label": "Winner", "text": "__REPORT_DATA__ won the season. __REPORT_HEAD__ __REPORT_TITLE__", "models": []}] + list(cls.report.get("takeaways") or [])
        cls.html = render_report_html(cls.report, site_url="https://example.test", share_base="/report/x/share/")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_slot_names_in_model_text_stay_text(self) -> None:
        self.assertEqual(len(DATA_BLOCK.findall(self.html)), 1)
        description = re.search(r'<meta property="og:description" content="([^"]*)">', self.html).group(1)
        self.assertEqual(description, "__REPORT_DATA__ won the season. __REPORT_HEAD__ __REPORT_TITLE__")
        self.assertEqual(self.html.count("<meta property=\"og:description\""), 1)

    def test_data_block_holds_no_markup_characters(self) -> None:
        blob = DATA_BLOCK.search(self.html).group(1)
        for ch in "<>&":
            self.assertNotIn(ch, blob)
        reasons = [v["reason"] for r in _data(self.html)["rounds"] for v in r["votes"]]
        self.assertTrue(any("<!--<script>" in (reason or "") for reason in reasons))  # survives as data

    def test_fill_template_is_one_pass(self) -> None:
        out = fill_template("[__REPORT_TITLE__][__REPORT_HEAD__][__REPORT_DATA__]", {"TITLE": "__REPORT_DATA__", "HEAD": "h", "DATA": "d"})
        self.assertEqual(out, "[__REPORT_DATA__][h][d]")

    def test_hostile_page_renders_and_runs_nothing(self) -> None:
        dom = rendered_dom(self.html + "<!-- probe -->")
        if dom is None:
            self.skipTest("no Chrome")
        for sec in SECTIONS:
            self.assertIn(f'id="{sec}"', dom)
        # the speech is shown as text; no element was made from it
        self.assertIn("&lt;!--&lt;script&gt;window.pwned=1", dom)
        self.assertNotIn("<script>window.pwned", dom)


class StylesTest(unittest.TestCase):
    def test_no_class_has_two_top_level_rules(self) -> None:
        css = season_report.TEMPLATE_PATH.read_text().split("<style>", 1)[1].split("</style>", 1)[0]
        selectors = re.findall(r"^(\.[a-z0-9-]+) \{", css, re.M)
        repeated = sorted({s for s in selectors if selectors.count(s) > 1})
        self.assertEqual(repeated, [])

    def test_share_card_rules_apply_only_in_card_mode(self) -> None:
        css = season_report.TEMPLATE_PATH.read_text()
        self.assertNotRegex(css, r"(?m)^\.sc \{")
        self.assertIn("html.card-mode .sc {", css)


class OddDataTest(unittest.TestCase):
    """Missing or null fields must cost at most one section, never the page."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.report = build_season_report(copy_season(Path(cls.tmp.name)))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def _render(self, report: dict) -> str:
        dom = rendered_dom(render_report_html(report))
        if dom is None:
            self.skipTest("no Chrome")
        return dom

    def test_full_report_renders_every_section(self) -> None:
        dom = self._render(self.report)
        for sec in SECTIONS:
            self.assertIn(f'id="{sec}"', dom)
        self.assertNotIn(NOTICE, dom)
        self.assertIn('id="round-1"', dom)

    def test_nulls_cost_nothing(self) -> None:
        report = json.loads(json.dumps(self.report))
        for r in report["rounds"]:
            r["tally"] = None
            r["talks"] = None
        for m in report["models"]:
            m["tags"] = None
            m["per_round"] = None
        if report.get("finale"):
            report["finale"]["tally"] = None
        dom = self._render(report)
        for sec in SECTIONS:
            self.assertIn(f'id="{sec}"', dom)

    def test_a_broken_section_leaves_the_rest(self) -> None:
        report = json.loads(json.dumps(self.report))
        report["vote_matrices"] = "not a matrix"
        for m in report["models"]:
            m["character"] = {"evil": None, "strategy": "?"}
        dom = self._render(report)
        for sec in SECTIONS:
            self.assertIn(f'id="{sec}"', dom)
        self.assertIn(NOTICE, dom)


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


if __name__ == "__main__":
    unittest.main()
