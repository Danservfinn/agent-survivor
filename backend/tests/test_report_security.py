"""Who can see a season, what the report routes and files expose, and the
page's script policy."""

from __future__ import annotations

import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from backend import api, live_season, season_report, season_story, season_visibility
from backend.season_report import build_season_report, render_report_html
from backend.tests.report_fixtures import copy_season


class ScriptPolicyTest(unittest.TestCase):
    def test_header_and_page_allow_only_the_page_script(self) -> None:
        template = season_report.TEMPLATE_PATH.read_text()
        digest = season_report.script_hash(template)
        self.assertRegex(digest, r"^'sha256-[A-Za-z0-9+/=]{44}'$")
        header = season_report.report_csp(header=True)
        self.assertIn(f"script-src {digest}; script-src-attr 'none'", header)
        self.assertIn("frame-ancestors 'none'", header)
        self.assertNotIn("frame-ancestors", season_report.report_csp(header=False))

    def test_the_page_carries_its_policy_before_any_style_or_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            html = render_report_html(build_season_report(copy_season(Path(tmp))))
        head = html.split("<style>", 1)[0]
        meta = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', head)
        self.assertIsNotNone(meta)
        self.assertIn(season_report.script_hash(), meta.group(1).replace("&#39;", "'"))

    def test_card_renderer_has_no_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(season_story.subprocess, "run") as run:
            page = Path(tmp) / "report.html"
            page.write_text("<html></html>")
            season_story.render_cards(page, Path(tmp) / "share", ["season"], chrome="/bin/echo")
            args = run.call_args[0][0]
        self.assertIn("--host-resolver-rules=MAP * ~NOTFOUND", args)


class VisibilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.season_dir = copy_season(Path(self.tmp.name))
        self.seasons = self.season_dir.parent
        (self.season_dir / "scorecard.json").write_text(json.dumps({"finished": True}))
        patcher = patch.object(live_season, "SEASONS_DIR", self.seasons)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sid = self.season_dir.name

    def _make_entry(self) -> str:
        conn = sqlite3.connect(self.season_dir / "season.db")
        agent_id = conn.execute("SELECT agent_id FROM Agents ORDER BY agent_id LIMIT 1").fetchone()[0]
        conn.close()
        (self.season_dir / "entry.json").write_text(json.dumps({"entry_id": "e1", "name": "Night Owl", "model_id": "acme/x", "agent_id": agent_id}))
        return season_visibility.ensure(self.season_dir)["token"]

    def _routes_answer(self, key: str | None) -> dict[str, int]:
        out = {}
        page = api.season_report_page(self.sid, key)
        out["page"] = page.status_code
        for name, call in (
            ("api", lambda: api.api_live_season_report(self.sid, key)),
            ("data", lambda: api.season_report_data(self.sid, "report.json", key)),
        ):
            try:
                call()
                out[name] = 200
            except HTTPException as exc:
                out[name] = exc.status_code
        return out

    def test_a_house_season_is_public_and_listed(self) -> None:
        self.assertEqual(season_visibility.state(self.season_dir), "public")
        self.assertTrue(season_visibility.listed(self.season_dir))
        self.assertIsNone(season_visibility.link_key(self.season_dir))

    def test_an_entry_season_starts_unlisted_and_opens_only_with_its_key(self) -> None:
        token = self._make_entry()
        build = build_season_report(self.season_dir)
        (self.season_dir / "report.json").write_text(json.dumps(build))
        self.assertEqual(season_visibility.state(self.season_dir), "unlisted")
        self.assertFalse(season_visibility.listed(self.season_dir))
        self.assertEqual(self._routes_answer(None), {"page": 404, "api": 404, "data": 404})
        self.assertEqual(self._routes_answer("wrong-key-123"), {"page": 404, "api": 404, "data": 404})
        self.assertEqual(self._routes_answer(token), {"page": 200, "api": 200, "data": 200})
        page = api.season_report_page(self.sid, token)
        self.assertEqual(page.headers["x-robots-tag"], "noindex")
        self.assertIn(f'"key":"{token}"'.encode(), page.body)  # the page's own links carry the key
        # never listed, never the live default
        self.assertNotIn(self.sid, [s["season_id"] for s in api.api_live_season_history()["seasons"]])
        self.assertNotEqual(api.api_live_season_feed().get("season_id"), self.sid)

    @unittest.skipUnless(hasattr(api, "api_entries_visibility"), "this checkout has no paid entries")
    def test_the_buyer_publishes_and_unlists_with_the_entry_token(self) -> None:
        self._make_entry()
        entry = {"season_id": self.sid}
        with patch("backend.entries_store.by_token", side_effect=lambda t: entry if t == "t" * 24 else None):
            made = api.api_entries_visibility({"t": "t" * 24, "state": "public"})
            self.assertEqual(made, {"state": "public", "report": f"/report/{self.sid}"})
            self.assertTrue(season_visibility.listed(self.season_dir))
            again = api.api_entries_visibility({"t": "t" * 24, "state": "unlisted"})
            self.assertIn("?k=", again["report"])
            for bad in ({"t": "x" * 24, "state": "public"}, {"t": "t" * 24, "state": "withdrawn"}):
                with self.assertRaises(HTTPException):
                    api.api_entries_visibility(bad)
            season_visibility.set_state(self.season_dir, "withdrawn")
            with self.assertRaises(HTTPException) as ctx:
                api.api_entries_visibility({"t": "t" * 24, "state": "public"})
            self.assertEqual(ctx.exception.status_code, 409)

    def test_withdrawn_is_gone_for_everyone(self) -> None:
        token = self._make_entry()
        season_visibility.set_state(self.season_dir, "withdrawn")
        self.assertEqual(self._routes_answer(token), {"page": 404, "api": 404, "data": 404})
        public = season_visibility.set_state(self.season_dir, "public")
        self.assertEqual(public["state"], "public")
        self.assertEqual(self._routes_answer(None), {"page": 200, "api": 200, "data": 404})  # no report.json written

    def test_an_archived_season_still_opens(self) -> None:
        archive = Path(self.tmp.name) / "archive"
        archive.mkdir()
        moved = archive / "20260927-000001-cheap_recent_openrouter"
        self.season_dir.rename(moved)
        with patch.object(api, "ARCHIVE_DIRS", [archive]):
            self.assertEqual(api.season_report_page(moved.name).status_code, 200)
        self.assertEqual(api.season_report_page(moved.name).status_code, 404)

    def test_downloaded_database_has_no_puzzle_answers(self) -> None:
        public = season_report.public_season_db(self.season_dir)
        with sqlite3.connect(public) as conn:
            answers = {row[0] for row in conn.execute("SELECT answer FROM ChallengePuzzles")}
            votes = conn.execute("SELECT COUNT(*) FROM Votes").fetchone()[0]
        self.assertEqual(answers, {""})
        self.assertGreater(votes, 0)
        response = api.season_report_data(self.sid, "season.db")
        self.assertTrue(str(response.path).endswith("season.public.db"))


class PublicTextTest(unittest.TestCase):
    def test_status_carries_no_paths_or_entry_ids(self) -> None:
        card = {"db": "/Users/someone/season.db", "rows": [], "entry": {"entry_id": "e1", "name": "Owl"}}
        self.assertEqual(api._public_scorecard(card), {"rows": [], "entry": {"name": "Owl"}})

    def test_old_reasons_lose_the_provider_text(self) -> None:
        text = 'No legal vote after 3 tries (OpenRouter HTTP 400: {"error":{"metadata":{"user_id":"user_x"}}}).'
        self.assertEqual(season_report._public_reason(text), "No legal vote after 3 tries (provider error HTTP 400).")
        self.assertEqual(season_report._public_reason("I vote for Ash."), "I vote for Ash.")


if __name__ == "__main__":
    unittest.main()


class DeliveryTest(unittest.TestCase):
    def test_etag_gives_304_and_the_page_names_its_canonical_address(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            season_dir = copy_season(Path(tmp))
            with patch.object(live_season, "SEASONS_DIR", season_dir.parent), patch.dict("os.environ", {"SURVIVOR_PUBLIC_URL": "https://example.test"}):
                first = api._report_page(season_dir.name)
                etag = first.headers["etag"]
                self.assertEqual(api._report_page(season_dir.name, None, etag).status_code, 304)
                self.assertEqual(api._report_page(season_dir.name, None, '"other"').status_code, 200)
            self.assertIn(f'<link rel="canonical" href="https://example.test/report/{season_dir.name}">'.encode(), first.body)
            report = build_season_report(season_dir)
        self.assertRegex(report["season"]["started_utc"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC$")
