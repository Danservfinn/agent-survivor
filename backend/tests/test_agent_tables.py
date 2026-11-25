"""Agents phase 1: tables of 1 to 10 entries, private playbooks that reach
only their own seat's prompts and are never stored, and a whole-table cost."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import database, live_cost, live_season, openrouter_client, playbooks
from backend import turn_controller as tc
from backend.llm_context import build_agent_episode_context
from backend.model_rosters import LAB_MIX_OPENROUTER_MODELS, TABLE_SEATS, EntryError, table_agents
from backend.tests.test_entry_hooks import TABLE
from backend.tests.test_model_failures import _Base

SECRET = "Always target the quietest player first. Codeword: PERIWINKLE-42."


class TableSeatingTest(unittest.TestCase):
    def test_house_fill_skips_the_entries_labs(self) -> None:
        agents, seats = table_agents(
            [{"name": "Night Owl", "model_id": "anthropic/claude-opus-5.5"}, {"name": "Rook", "model_id": "openai/o9"}], 5
        )
        self.assertEqual(len(agents), TABLE_SEATS)
        house_labs = {s["model_id"].split("/")[0] for s in seats if s["seat"] == "house"}
        self.assertFalse(house_labs & {"anthropic", "openai"})
        self.assertEqual(sum(s["seat"] == "entry" for s in seats), 2)

    def test_the_seed_picks_the_house_seats_when_labs_are_spare(self) -> None:
        picks = {
            tuple(sorted(s["model_id"] for s in table_agents([{"name": "Rook", "model_id": "acme/m"}], seed)[1] if s["seat"] == "house"))
            for seed in range(30)
        }
        self.assertGreater(len(picks), 1)

    def test_a_full_table_of_ten_and_its_limits(self) -> None:
        ten = [{"name": f"Agent {i}", "model_id": "anthropic/claude-opus-5.5"} for i in range(TABLE_SEATS)]
        agents, seats = table_agents(ten, 1)
        self.assertTrue(all(s["seat"] == "entry" for s in seats))
        with self.assertRaises(EntryError):
            table_agents(ten + [{"name": "Eleven", "model_id": "acme/m"}], 1)
        with self.assertRaises(EntryError):
            table_agents([{"name": "Rook", "model_id": "acme/m"}, {"name": "rook", "model_id": "acme/n"}], 1)
        with self.assertRaises(EntryError):
            table_agents([], 1)


class PlaybookPromptTest(_Base):
    def tearDown(self) -> None:
        playbooks.clear()

    def _actors(self):
        rows = [dict(r) for r in self.conn.execute("SELECT * FROM Agents ORDER BY agent_id")]
        return rows[0], rows[1], rows[2:]

    def _prompts(self, actor, others) -> list[str]:
        sent: list[str] = []

        def fake_post(payload, api_key):
            sent.append(payload["messages"][-1]["content"])
            content = {"answers": {}, "explanation": "x"} if "Puzzles:" in payload["messages"][-1]["content"] else {
                "think": "t", "summary": "s", "target_id": None, "confidence": 0.5, "dialogue": "hi", "pick": 1, "why": "w"}
            return {"choices": [{"message": {"content": json.dumps(content)}}]}

        context = build_agent_episode_context(self.conn, actor_id=actor["agent_id"], round_number=1, current_step="x")
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "k"}), patch.object(openrouter_client, "_post_chat_completion", side_effect=fake_post):
            openrouter_client.request_agent_action(actor=actor, step="x", scene_context="Talk.", allowed_targets=others,
                                                   response_kind="group_conversation", episode_context=context)
            openrouter_client.request_private_writing(actor=actor, task="Plan.", fields={"pick": "n", "why": "w"}, episode_context=context)
            try:
                openrouter_client.request_challenge_solution(actor=actor, puzzles=[], episode_context=context)
            except Exception:  # noqa: BLE001 - an empty ladder may not parse; the prompt was still sent
                pass
        return sent

    def test_a_playbook_reaches_only_its_own_seats_prompts(self) -> None:
        owner, other, rest = self._actors()
        playbooks.register(owner["agent_id"], SECRET, playbooks.sha256(SECRET))
        mine = self._prompts(owner, [other, *rest])
        theirs = self._prompts(other, [owner, *rest])
        self.assertEqual(len(mine), 3)
        for prompt in mine:
            self.assertIn(SECRET, prompt)
            self.assertIn(playbooks.BEGIN.format(sha=playbooks.sha256(SECRET)), prompt)
            self.assertIn(playbooks.LEAD, prompt)
            self.assertIn(playbooks.END, prompt)
            # after the record (which holds the rules), so the rules come first
            self.assertLess(prompt.index("Game record visible to you"), prompt.index(playbooks.END))
        for prompt in theirs:
            self.assertNotIn("PERIWINKLE", prompt)
            self.assertNotIn("OWNER PLAYBOOK", prompt)

    def test_no_playbook_leaves_the_prompt_unchanged(self) -> None:
        owner, other, rest = self._actors()
        before = self._prompts(owner, [other, *rest])
        playbooks.register(other["agent_id"], SECRET)
        after = self._prompts(owner, [other, *rest])
        self.assertEqual(before, after)

    def test_size_and_hash_are_checked(self) -> None:
        with self.assertRaises(playbooks.PlaybookError):
            playbooks.register("agent-01", "x" * (playbooks.PLAYBOOK_MAX_CHARS + 1))
        with self.assertRaises(playbooks.PlaybookError):
            playbooks.register("agent-01", SECRET, "0" * 64)


class NeverStoredTest(unittest.TestCase):
    def test_a_season_with_a_playbook_stores_none_of_it(self) -> None:
        from backend.tests.test_parallel_equivalence import _Stub

        stub = _Stub()
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"OPENROUTER_API_KEY": "stub", "LLM_PROVIDER": "openrouter", "LLM_SURVIVOR_FAST_BENCHMARK": "1", "SURVIVOR_SEASON_SEED": "4242"},
        ), patch.object(tc, "request_agent_action", side_effect=stub.agent), patch.object(
            tc, "request_challenge_solution", side_effect=stub.challenge
        ), patch.object(tc, "request_private_writing", side_effect=stub.private), patch.object(
            tc, "request_host_event_text", side_effect=stub.host_text
        ), patch.object(tc, "request_host_narration", side_effect=stub.host_narration), patch.object(
            tc, "request_host_questions", side_effect=stub.host_questions
        ), patch.object(tc, "get_pricing_table", return_value=None), patch.object(
            tc, "_assert_live_roster_compatible", return_value=None
        ), patch("builtins.print"):
            db = Path(tmp) / "season.db"
            original = database.DATABASE_PATH
            database.DATABASE_PATH = db
            try:
                agents, seats = table_agents([{"name": "Night Owl", "model_id": "acme/m", "playbook_sha256": playbooks.sha256(SECRET)}], 4242)
                database.seed_demo(reset=True, path=db, roster_preset="lab_mix_openrouter", agents=agents)
                record = live_season._table_record(db, seats, 4242)
                owl = next(s["agent_id"] for s in record["seats"] if s["seat"] == "entry")
                playbooks.register(owl, SECRET)
                tc.auto_run_to_end(max_rounds=20, max_turns=2500, max_live_calls=10000, max_estimated_cost_cents=1e9)
            finally:
                database.DATABASE_PATH = original
                playbooks.clear()
            conn = sqlite3.connect(db)
            for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                for row in conn.execute(f"SELECT * FROM {table}"):
                    self.assertNotIn("PERIWINKLE", json.dumps(row, default=str), table)
            conn.close()
        self.assertNotIn("PERIWINKLE", json.dumps(record))
        self.assertEqual(record["seats"][0]["playbook_sha256"], playbooks.sha256(SECRET))
        self.assertIsNotNone(owl)


class RunnerGuardTest(unittest.TestCase):
    def test_the_runner_refuses_bad_tables(self) -> None:
        run = live_season.run_live_season
        self.assertIn("not both", run(roster="lab_mix_openrouter", entry={"name": "A1", "model_id": "acme/m"}, entries=[])["error"])
        bad_hash = [{"name": "Rook", "model_id": "acme/m", "playbook": SECRET, "playbook_sha256": "0" * 64}]
        self.assertIn("does not match", run(roster="lab_mix_openrouter", entries=bad_hash)["error"])
        with patch.object(live_season, "_variable_price", return_value=True):
            self.assertIn("variable price", run(roster="lab_mix_openrouter", entries=[{"name": "Rook", "model_id": "typesafe/jev-router"}])["error"])


class TableCostTest(unittest.TestCase):
    def test_a_table_is_priced_seat_by_seat(self) -> None:
        table = dict(TABLE, **{"typesafe/jev-router": {"prompt_per_m": 1.25, "completion_per_m": 5.0, "variable": True}})
        with tempfile.TemporaryDirectory() as tmp, patch.object(live_cost, "get_pricing_table", return_value=table):
            estimate = live_cost.estimate_table_season_cost(["acme/big-model", "anthropic/claude-opus-5.5", "typesafe/jev-router", "nope/x"], seasons_dir=Path(tmp))
        self.assertEqual(len(estimate["seats"]), TABLE_SEATS)
        house = [s for s in estimate["seats"] if s["seat"] == "house"]
        self.assertFalse({s["model_id"].split("/")[0] for s in house} & {"anthropic", "typesafe"})
        self.assertEqual(estimate["variable_price_models"], ["typesafe/jev-router"])
        self.assertEqual(estimate["unpriced_models"], ["nope/x"])
        self.assertGreater(estimate["total_max_usd"], estimate["total_usd"])
        self.assertEqual(estimate["basis"], "estimated")
        with self.assertRaises(ValueError):
            live_cost.estimate_table_season_cost([])


if __name__ == "__main__":
    unittest.main()
