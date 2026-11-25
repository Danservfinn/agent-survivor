"""Players choose who they talk to; vote promises made in talks are kept or broken."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import database
from backend import turn_controller as tc
from backend.llm_context import build_agent_episode_context
from backend.openrouter_client import AgentAction

ROUND = 1


class CampTalksTest(unittest.TestCase):
    def setUp(self) -> None:
        env = patch.dict(
            os.environ,
            {"OPENROUTER_API_KEY": "", "LLM_PROVIDER": "openrouter", "LLM_SURVIVOR_FAST_BENCHMARK": "1"},
            clear=False,
        )
        env.start()
        self.addCleanup(env.stop)
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        original = database.DATABASE_PATH
        database.DATABASE_PATH = Path(temp_dir.name) / "survivor.db"
        self.addCleanup(setattr, database, "DATABASE_PATH", original)
        database.seed_demo(reset=True, roster_preset="cheap_recent_openrouter")
        self.conn = database.get_db_connection()
        self.conn.execute("PRAGMA foreign_keys = OFF")
        self.addCleanup(self.conn.close)
        self.players = [row["agent_id"] for row in self.conn.execute("SELECT agent_id FROM Agents ORDER BY agent_id")]

    def _talks(self) -> list[tuple[str, frozenset[str]]]:
        return [
            (event["payload"]["chosen_by"], frozenset(event["payload"]["participant_ids"]))
            for event in tc._camp_talk_events(self.conn, ROUND, 1)
        ]

    def test_fast_round_has_talks_and_no_engine_seated_scene(self) -> None:
        state = {"phase": "round", "phase_step": "challenge_intro", "current_round": ROUND}
        steps = tc._phase_steps(self.conn, state)
        self.assertIn("camp_talks", steps)
        self.assertNotIn("camp_pre_challenge_read", steps)
        self.assertLess(steps.index("challenge_gift"), steps.index("camp_talks"))
        self.assertLess(steps.index("camp_talks"), steps.index("tribal_vote_call"))
        # seeding and new rounds start where the active step list starts
        self.assertEqual(steps[0], database.round_start_step())
        seeded = self.conn.execute("SELECT phase_step FROM GameState").fetchone()[0]
        self.assertEqual(seeded, steps[0])

    def test_every_player_starts_a_talk_and_each_side_speaks_twice(self) -> None:
        talks = self._talks()
        self.assertEqual({chooser for chooser, _ in talks}, set(self.players))
        self.assertTrue(all(len(pair) == 2 for _, pair in talks))
        for event in tc._camp_talk_events(self.conn, ROUND + 1, 2):
            speakers = [line["agent_id"] for line in event["payload"]["speaker_lines"]]
            self.assertEqual(len(speakers), 4)
            self.assertEqual(sorted(set(speakers)), sorted(event["payload"]["participant_ids"]))

    def test_chosen_partner_is_honoured_and_mutual_picks_share_one_talk(self) -> None:
        a, b, c, *_ = self.players
        choices = {a: b, b: a, c: a}

        def pick(conn, round_number, agent_id, others, index):
            return choices.get(agent_id)

        with patch.object(tc, "_choose_talk_partner", side_effect=pick):
            talks = self._talks()
        pairs = [pair for _, pair in talks]
        self.assertEqual(pairs.count(frozenset((a, b))), 1)
        self.assertIn(frozenset((c, a)), pairs)
        self.assertEqual(len(talks), 2)

    @patch.dict(os.environ, {"SURVIVOR_SECRET_BALLOT": "0"})  # the open-ballot rule

    def test_live_talk_records_promises_and_marks_them_kept_or_broken(self) -> None:
        a, b, target, other, *_ = self.players

        def action(**kwargs):
            actor = kwargs["actor"]["agent_id"]
            if kwargs["response_kind"] == "talk_choice":
                return AgentAction(dialogue="Can we talk?", inner_thought="", model_id="m", target_id=b if actor == a else None)
            promise = target if actor == a else None
            return AgentAction(dialogue="I am with you tonight.", inner_thought="", model_id="m", promised_vote_target=promise)

        with patch.object(tc, "should_use_live_llm", return_value=True), patch.object(
            tc, "live_llm_provider", return_value="openrouter"
        ), patch.object(tc, "request_agent_action", side_effect=action), patch.object(
            tc, "build_agent_episode_context", return_value={}
        ):
            talks = self._talks()
        self.assertEqual([pair for _, pair in talks], [frozenset((a, b))])
        promises = self.conn.execute("SELECT promiser_id, listener_id, target_id FROM Promises").fetchall()
        self.assertEqual({tuple(row) for row in promises}, {(a, b, target)})

        self.assertEqual(tc.promise_record(self.conn, a)[0]["status"], "open")
        self.conn.execute(
            "INSERT INTO Votes (round, turn_index, voter_id, target_id, revealed) VALUES (?, 0, ?, ?, 1)",
            (ROUND, a, other),
        )
        record = tc.promise_record(self.conn, a)[0]
        self.assertEqual(record["status"], "broken")
        # the listener sees the broken promise; a bystander does not
        listener_view = build_agent_episode_context(self.conn, actor_id=b, round_number=ROUND, current_step="x")
        self.assertEqual(listener_view["your_promises"]["made_to_you"][0]["status"], "broken")
        bystander_view = build_agent_episode_context(self.conn, actor_id=other, round_number=ROUND, current_step="x")
        self.assertEqual(bystander_view["your_promises"], {"made_by_you": [], "made_to_you": []})


if __name__ == "__main__":
    unittest.main()
