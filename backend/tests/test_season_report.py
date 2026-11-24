"""The after-action report, built from a full season played with scripted fallbacks."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from backend import api, database, live_season, season_report, turn_controller
from backend.benchmark_scoring import WEIGHTS, score_season
from backend.season_report import (
    _calls_and_cost,
    build_season_report,
    cached_season_report,
    render_report_html,
    reportable_season_dir,
    safe_link,
    write_report_files,
)

SEASON = "20261124-000001-cheap_recent_openrouter"
HOSTILE = '</script><img src=x onerror="alert(1)">'


class SeasonReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.env = patch.dict(os.environ, {"OPENROUTER_API_KEY": "", "LLM_PROVIDER": "openrouter"}, clear=False)
        cls.env.start()
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.seasons_dir = Path(cls.temp_dir.name) / "seasons"
        cls.season_dir = cls.seasons_dir / SEASON
        cls.season_dir.mkdir(parents=True)
        cls.db = cls.season_dir / "season.db"
        cls.original_path = database.DATABASE_PATH
        database.DATABASE_PATH = cls.db
        database.seed_demo(reset=True, path=cls.db, roster_preset="cheap_recent_openrouter")
        turn_controller.auto_run_to_end(max_rounds=20, max_turns=2000, max_live_calls=0, max_estimated_cost_cents=0)
        database.DATABASE_PATH = cls.original_path
        # a model can write anything in a vote speech; the page must never run it
        conn = sqlite3.connect(cls.db)
        conn.execute(
            "UPDATE StoryEvents SET dialogue = ? WHERE id = (SELECT MIN(id) FROM StoryEvents WHERE kind = 'vote_booth')",
            (HOSTILE,),
        )
        conn.commit()
        conn.close()
        cls.report = build_season_report(cls.season_dir)
        cls.conn = sqlite3.connect(cls.db)
        cls.conn.row_factory = sqlite3.Row

    @classmethod
    def tearDownClass(cls) -> None:
        cls.conn.close()
        cls.temp_dir.cleanup()
        cls.env.stop()

    def test_season_facts(self) -> None:
        season = self.report["season"]
        self.assertEqual(season["season_id"], SEASON)
        self.assertEqual(season["date"], "2026-11-24 00:00")
        self.assertEqual(season["rules"], "social-game")
        self.assertEqual(season["mode"], "deterministic")
        self.assertEqual(season["players"], 9)
        self.assertTrue(season["winner"])
        self.assertTrue(season["finished"])

    def test_one_row_per_model_ranked_by_index(self) -> None:
        models = self.report["models"]
        self.assertEqual(len(models), 9)
        indexes = [m["survivor_index"] for m in models]
        self.assertEqual(indexes, sorted(indexes, reverse=True))
        self.assertEqual([m["rank"] for m in models], list(range(1, 10)))
        scored = {r["agent_id"]: r["survivor_index"] for r in score_season(self.db)["rows"]}
        self.assertEqual({m["agent_id"]: m["survivor_index"] for m in models}, scored)

    def test_index_points_add_up_to_the_index(self) -> None:
        for model in self.report["models"]:
            self.assertEqual(set(model["points"]), set(WEIGHTS))
            self.assertAlmostEqual(sum(model["points"].values()), model["survivor_index"], delta=0.05)

    def test_places_and_fates_agree(self) -> None:
        by_place = {m["place"]: m for m in self.report["models"]}
        self.assertTrue(all(m["place_known"] for m in self.report["models"]))
        self.assertEqual(by_place[1]["fate"], "Winner")
        self.assertEqual(by_place[2]["fate"], "Finalist")
        self.assertEqual(by_place[3]["fate"], "Finalist")
        for place in range(4, 10):
            self.assertTrue(by_place[place]["fate"].startswith("Voted out"), by_place[place]["fate"])
        # among the players voted out, a later exit is a better place
        exits = [by_place[p]["eliminated_round"] for p in range(4, 10)]
        self.assertEqual(exits, sorted(exits, reverse=True))

    def test_each_round_names_the_player_who_left(self) -> None:
        out = {r["agent_id"]: r["elimination_round"] for r in self.conn.execute("SELECT * FROM Agents") if r["elimination_round"]}
        from_report = {r["eliminated_id"]: r["round"] for r in self.report["rounds"] if r["eliminated_id"]}
        self.assertEqual(from_report, out)
        for r in self.report["rounds"]:
            self.assertNotIn("agent", str(r["round"]))
            self.assertEqual(sum(r["tally"].values()), len([v for v in r["votes"] if not v["revote"] and v["voter_id"] != "host"]))

    def test_challenge_standings_match_recorded_scores(self) -> None:
        first = self.report["rounds"][0]
        self.assertEqual(first["challenge"]["type"], "ladder")
        self.assertEqual(len(first["challenge"]["difficulties"]), 5)
        row = self.conn.execute("SELECT result_payload FROM ChallengeResults WHERE round = ?", (first["round"],)).fetchone()
        scores = json.loads(row["result_payload"])["scores"]
        for standing in first["challenge"]["standings"]:
            self.assertEqual(standing["correct"], scores[standing["agent_id"]]["correct"])
            self.assertEqual(sum(standing["rungs"]), standing["correct"])

    def test_idol_gifts_are_reported(self) -> None:
        gifts = [r["idol"] for r in self.report["rounds"] if r["idol"] and r["idol"]["recipient_id"]]
        recorded = self.conn.execute("SELECT COUNT(*) FROM StoryEvents WHERE kind = 'idol_handoff'").fetchone()[0]
        self.assertEqual(len(gifts), recorded)
        self.assertEqual(sum(m["idols_received"] for m in self.report["models"]), recorded)

    def test_vote_map_counts_every_player_vote(self) -> None:
        matrices = self.report["vote_matrices"]
        total = sum(sum(row.values()) for row in matrices["targeted"].values())
        recorded = self.conn.execute("SELECT COUNT(*) FROM Votes WHERE voter_id != 'host' AND target_id IS NOT NULL").fetchone()[0]
        self.assertEqual(total, recorded)
        for a in matrices["order"]:
            for b in matrices["order"]:
                self.assertEqual(matrices["together"][a][b], matrices["together"][b][a])
                self.assertLessEqual(matrices["together"][a][b], matrices["shared_rounds"][a][b])

    def test_finale_tally_matches_jury_votes(self) -> None:
        finale = self.report["finale"]
        self.assertEqual(sum(finale["tally"].values()), len(finale["jury"]))
        self.assertEqual(len(finale["finalist_ids"]), 3)
        self.assertIn(finale["winner_id"], finale["finalist_ids"])

    def test_takeaways_name_the_winner(self) -> None:
        labels = [card["label"] for card in self.report["takeaways"]]
        self.assertEqual(labels[0], "Winner")
        self.assertIn(self.report["season"]["winner"], self.report["takeaways"][0]["text"])
        self.assertIn("Survivor Index", labels)

    def test_scripted_seasons_stay_out_of_cross_season_totals(self) -> None:
        self.assertEqual(self.report["cross_season"]["season_count"], 0)
        self.assertEqual(self.report["cross_season"]["models"], [])

    def test_no_cost_is_claimed_without_live_calls(self) -> None:
        for model in self.report["models"]:
            self.assertEqual(model["calls"], 0)
            self.assertIsNone(model["est_cost_usd"])

    def test_page_embeds_the_data_and_cannot_be_broken_out_of(self) -> None:
        html = render_report_html(self.report, contact_url="javascript:alert(1)", site_url="https://example.test")
        self.assertNotIn("__REPORT_DATA__", html)
        self.assertNotIn("__REPORT_TITLE__", html)
        self.assertNotIn(HOSTILE, html)
        blob = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', html, re.S).group(1)
        page = json.loads(blob)
        self.assertEqual(page["season"]["season_id"], SEASON)
        reasons = [v["reason"] for r in page["rounds"] for v in r["votes"]]
        self.assertIn(HOSTILE, reasons)  # the text survives as data
        self.assertEqual(page["links"], {"contact": None, "site": "https://example.test"})
        self.assertNotIn("innerHTML", html.split("<script>", 1)[1].split("/*", 1)[0])

    def test_page_makes_no_outside_requests(self) -> None:
        html = render_report_html(self.report)
        head = html.split('<script id="report-data"', 1)[0]
        self.assertNotRegex(head, r'(src|href)="https?://')
        self.assertNotIn("@import", head)
        self.assertNotIn("fetch(", html.rsplit("</script>", 2)[1])

    def test_writes_json_and_html_beside_the_database(self) -> None:
        paths = write_report_files(self.season_dir)
        self.assertEqual(Path(paths["json"]).parent, self.season_dir)
        self.assertEqual(json.loads(Path(paths["json"]).read_text())["season"]["season_id"], SEASON)
        self.assertTrue(Path(paths["html"]).read_text().startswith("<!doctype html>"))

    def test_report_routes(self) -> None:
        with patch.object(live_season, "SEASONS_DIR", self.seasons_dir):
            page = api.season_report_page(SEASON)
            self.assertEqual(page.status_code, 200)
            self.assertIn("default-src 'none'", page.headers["content-security-policy"])
            self.assertEqual(page.headers["cache-control"], "no-cache")
            self.assertEqual(api.api_live_season_report(SEASON)["season"]["season_id"], SEASON)
            # a bare /report falls back to the newest season with events when none is a live result
            self.assertEqual(api.api_live_season_report_latest()["season"]["season_id"], SEASON)
            for bad in ("no-such-season", "../" + SEASON, "a/b"):
                with self.assertRaises(HTTPException) as ctx:
                    api.season_report_page(bad)
                self.assertEqual(ctx.exception.status_code, 404)

    def test_cached_report_is_rebuilt_when_the_season_changes(self) -> None:
        first = cached_season_report(self.season_dir)
        self.assertTrue(cached_season_report(self.season_dir) is first)
        os.utime(self.db, (self.db.stat().st_atime, self.db.stat().st_mtime + 5))
        self.assertFalse(cached_season_report(self.season_dir) is first)

    def test_latest_reportable_season(self) -> None:
        self.assertEqual(reportable_season_dir(self.seasons_dir), self.season_dir)
        self.assertIsNone(reportable_season_dir(self.seasons_dir / "missing"))


class ReportHelpersTest(unittest.TestCase):
    def test_safe_link(self) -> None:
        self.assertEqual(safe_link("https://llmsurvivor.example/x"), "https://llmsurvivor.example/x")
        self.assertEqual(safe_link("mailto:hello@example.com"), "mailto:hello@example.com")
        for bad in ("javascript:alert(1)", "data:text/html,x", "", None, 'https://x.test/"onclick=1'):
            self.assertIsNone(safe_link(bad))

    def test_calls_and_cost_from_recorded_live_calls(self) -> None:
        table = {"org/priced": {"prompt_per_m": 1.0, "completion_per_m": 2.0}}
        data = {
            "agents": [
                {"agent_id": "agent-01", "model_id": "org/priced"},
                {"agent_id": "agent-02", "model_id": "org/unknown"},
                {"agent_id": "agent-03", "model_id": "org/gift:free"},
            ],
            "plans": [{"agent_id": "agent-01", "provider": "openrouter"}],
            "attempts": [
                # one ladder call covers five puzzles; sudden death is a second call
                *[{"id": i, "agent_id": "agent-01", "round": 7, "provider": "openrouter", "rung_index": i} for i in range(5)],
                {"id": 9, "agent_id": "agent-01", "round": 7, "provider": "openrouter", "rung_index": 5},
                {"id": 10, "agent_id": "agent-02", "round": 7, "provider": "deterministic", "rung_index": 0},
            ],
            "events": [
                {"kind": "vote_booth", "actor_ids": '["agent-01"]', "payload": '{"llm_provider": "openrouter"}'},
                {"kind": "vote_booth", "actor_ids": '["agent-02"]', "payload": '{"llm_provider": "openrouter"}'},
                {"kind": "vote_booth", "actor_ids": '["agent-03"]', "payload": '{"llm_provider": "openrouter"}'},
                {
                    "kind": "conversation",
                    "actor_ids": '["agent-01","agent-02"]',
                    "payload": json.dumps(
                        {"speaker_line_metadata": [{"agent_id": "agent-01", "llm_provider": "openrouter"}, {"agent_id": "agent-02", "llm_provider": "deterministic"}]}
                    ),
                },
                {"kind": "challenge_result", "actor_ids": '["host"]', "payload": '{"llm_provider": "openrouter"}'},
            ],
        }
        usage = _calls_and_cost(data, table)
        self.assertEqual(usage["agent-01"]["calls"], 5)  # plan, ladder, sudden death, vote, one talk line
        self.assertGreater(usage["agent-01"]["est_cost_usd"], 0)
        self.assertEqual(usage["agent-02"]["calls"], 1)
        self.assertIsNone(usage["agent-02"]["est_cost_usd"])  # no list price: no guess
        self.assertEqual(usage["agent-03"], {"calls": 1, "est_cost_usd": 0.0})

    def test_promise_status(self) -> None:
        agents = [{"agent_id": f"agent-0{i}", "pseudonym": f"P{i}", "elimination_round": None} for i in (1, 2, 3)]
        data = {
            "agents": agents,
            "attempts": [],
            "results": [],
            "plans": [],
            "events": [],
            "votes": [
                {"id": 1, "round": 7, "voter_id": "agent-01", "target_id": "agent-03", "is_revote": 0},
                {"id": 2, "round": 7, "voter_id": "agent-02", "target_id": "agent-01", "is_revote": 0},
            ],
            "promises": [
                {"round": 7, "promiser_id": "agent-01", "listener_id": "agent-02", "target_id": "agent-03"},
                {"round": 7, "promiser_id": "agent-02", "listener_id": "agent-01", "target_id": "agent-03"},
                {"round": 7, "promiser_id": "agent-03", "listener_id": "agent-01", "target_id": "agent-02"},
                # a promise to vote for the listener cannot be kept or broken
                {"round": 7, "promiser_id": "agent-02", "listener_id": "agent-03", "target_id": "agent-03"},
            ],
        }
        rounds = season_report._build_rounds(data, {a["agent_id"]: a["pseudonym"] for a in agents})
        self.assertEqual([p["status"] for p in rounds[0]["promises"]], ["kept", "broken", "open", "unclear"])

    def test_template_is_one_self_contained_file(self) -> None:
        template = season_report.TEMPLATE_PATH.read_text()
        self.assertEqual(template.count("__REPORT_DATA__"), 1)
        self.assertEqual(template.count("__REPORT_TITLE__"), 1)


if __name__ == "__main__":
    unittest.main()
