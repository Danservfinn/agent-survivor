"""A finished season's raw data can be downloaded; nothing else can."""

from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from backend import api, live_season

SEASON = "20261125-150000-lab_mix_openrouter"


class DataDownloadsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp())
        self.season = self.base / SEASON
        self.season.mkdir()
        (self.season / "season.db").write_bytes(b"SQLite format 3\x00 plain rows")
        (self.season / "report.json").write_text(json.dumps({"ok": True}))
        with gzip.open(self.season / "prompts.jsonl.gz", "wt") as handle:
            handle.write('{"reply": {"summary": "s"}}\n')
        self.finished(True)
        self.patch = patch.object(live_season, "SEASONS_DIR", self.base)
        self.patch.start()
        api._clean_files.clear()

    def tearDown(self) -> None:
        self.patch.stop()

    def finished(self, done: bool) -> None:
        (self.season / "scorecard.json").write_text(json.dumps({"finished": done}))

    def test_each_file_of_a_finished_season(self) -> None:
        for name, media in (("report.json", "application/json"), ("prompts.jsonl.gz", "application/gzip"), ("season.db", "application/vnd.sqlite3")):
            response = api.season_report_data(SEASON, name)
            self.assertEqual(response.media_type, media)
            self.assertIn(f"llm-survivor-{SEASON}-{name}", response.headers["content-disposition"])

    def test_an_unfinished_season_offers_nothing(self) -> None:
        self.finished(False)
        with self.assertRaises(HTTPException):
            api.season_report_data(SEASON, "season.db")

    def test_only_the_listed_files(self) -> None:
        for name in ("scorecard.json", "../season.db", "season.db-wal", "runner.log"):
            with self.assertRaises(HTTPException):
                api.season_report_data(SEASON, name)

    def test_a_file_with_a_key_in_it_is_withheld(self) -> None:
        (self.season / "season.db").write_bytes(b"rows sk-or-v1-" + b"d" * 40)
        with self.assertRaises(HTTPException):
            api.season_report_data(SEASON, "season.db")


if __name__ == "__main__":
    unittest.main()
