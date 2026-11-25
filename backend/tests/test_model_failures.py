"""A model's unusable reply is charged to that player; the season goes on.

Talk lines become silence, votes are spoiled and count for no one, a pitch
is left out. No line or vote is ever invented. Provider outages are a
separate path (SeasonInterrupted) and are tested elsewhere.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import database
from backend import turn_controller as tc
from backend.openrouter_client import AgentAction, InvalidModelReply

ROUND = 1


class _Base(unittest.TestCase):
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
        self.state = {"current_round": ROUND, "phase": "round", "phase_step": "vote_booth_x"}

    def _live(self, action):
        stack = [
            patch.object(tc, "should_use_live_llm", return_value=True),
            patch.object(tc, "live_llm_provider", return_value="openrouter"),
            patch.object(tc, "build_agent_episode_context", return_value={}),
            patch.object(tc, "request_agent_action", side_effect=action),
            patch.object(tc, "_real_host_narration", return_value=None),
            patch.object(tc, "_real_host_event_text", return_value=None),
        ]
        for item in stack:
            item.start()
            self.addCleanup(item.stop)


class ModelFailureTest(_Base):
    def test_an_unusable_talk_line_is_silence_and_the_talk_goes_on(self) -> None:
        a, b, *_ = self.players

        def action(**kwargs):
            actor = kwargs["actor"]["agent_id"]
            if kwargs["response_kind"] == "talk_choice":
                return AgentAction(dialogue="Talk?", inner_thought="", model_id="m", target_id=b if actor == a else None)
            if actor == b:
                raise InvalidModelReply("empty reply")
            return AgentAction(dialogue="Vote with me.", inner_thought="", model_id="m", promised_vote_target=self.players[3])

        self._live(action)
        events = tc._camp_talk_events(self.conn, ROUND, 1)
        self.assertEqual(len(events), 1)
        lines = events[0]["payload"]["speaker_lines"]
        self.assertEqual([line["text"] for line in lines if line["agent_id"] == b], [tc.SILENT_LINE] * 2)
        self.assertEqual([line["text"] for line in lines if line["agent_id"] == a], ["Vote with me."] * 2)
        # the silent player made no promise and declared no pact
        promisers = {row[0] for row in self.conn.execute("SELECT promiser_id FROM Promises")}
        self.assertEqual(promisers, {a})

    def _vote_round(self, spoiler: str, target: str) -> dict:
        def action(**kwargs):
            if kwargs["actor"]["agent_id"] == spoiler:
                return AgentAction(dialogue="I vote for the moon.", inner_thought="", model_id="m", target_id=None)
            return AgentAction(dialogue="My vote.", inner_thought="", model_id="m", target_id=target)

        self._live(action)
        voters = tc._round_voter_ids(self.conn, ROUND)
        for index, voter in enumerate(voters):
            tc._apply_step(self.conn, self.state, f"vote_booth_{voter}", 10 + index, index)
        for index in range(len(voters)):
            tc._apply_step(self.conn, self.state, f"vote_reveal_{index + 1}", 30 + index, index)
        events, result, _ = tc._apply_step(self.conn, self.state, "elimination", 50, 0)
        return result

    def test_a_spoiled_vote_counts_for_no_one_and_breaks_its_promise(self) -> None:
        spoiler, listener, target, *_ = self.players
        self.conn.execute(
            "INSERT INTO Promises (round, promiser_id, listener_id, target_id, discussion_step) VALUES (?, ?, ?, ?, 'camp_talk_0')",
            (ROUND, spoiler, listener, target),
        )
        result = self._vote_round(spoiler, target)
        self.assertEqual(result["eliminated_id"], target)
        self.assertEqual(
            [tuple(row) for row in self.conn.execute("SELECT voter_id, ballot FROM SpoiledVotes")], [(spoiler, "vote")]
        )
        self.assertNotIn(spoiler, {row[0] for row in self.conn.execute("SELECT voter_id FROM Votes")})
        record = tc.promise_record(self.conn, spoiler)[0]
        self.assertEqual((record["status"], record["actual_vote"]), ("broken", "a spoiled vote"))
        booth = self.conn.execute(
            "SELECT dialogue FROM StoryEvents WHERE kind = 'vote_booth' AND actor_ids LIKE ?", (f"%{spoiler}%",)
        ).fetchone()[0]
        self.assertIn("spoiled", booth)

    def test_a_promise_stays_open_until_the_votes_are_read(self) -> None:
        spoiler, listener, target, *_ = self.players
        self.conn.execute(
            "INSERT INTO Promises (round, promiser_id, listener_id, target_id, discussion_step) VALUES (?, ?, ?, ?, 'camp_talk_0')",
            (ROUND, spoiler, listener, target),
        )
        tc._record_spoiled_vote(self.conn, ROUND, spoiler, "vote", "x")
        self.assertEqual(tc.promise_record(self.conn, spoiler)[0]["status"], "open")

    def test_when_every_revote_is_spoiled_it_goes_to_deadlock(self) -> None:
        a, b, *_ = self.players
        self._live(lambda **kwargs: AgentAction(dialogue="?", inner_thought="", model_id="m", target_id=None))
        from collections import Counter

        c, d, e, f, *_ = self.players[2:]
        for voter, target in ((c, a), (d, a), (e, b), (f, b)):
            tc._insert_vote(self.conn, ROUND, 0, voter, target)
        counts = tc._maybe_run_revote(self.conn, ROUND, 1, Counter({a: 2, b: 2}))
        # every revote spoiled is a deadlock; nobody agrees, so the others draw rocks
        self.assertEqual(len(counts), 1)
        self.assertNotIn(next(iter(counts)), {a, b})
        self.assertTrue(self.conn.execute("SELECT COUNT(*) FROM SpoiledVotes WHERE ballot = 'revote'").fetchone()[0])

    def test_an_unusable_pitch_is_left_out(self) -> None:
        def action(**kwargs):
            raise InvalidModelReply("empty reply")

        self._live(action)
        finalist = self.players[0]
        event = tc._finale_pitch_event(self.conn, 9, 1, finalist, f"finale_pitch_{finalist}")
        self.assertTrue(event["dialogue"].endswith("gives no pitch."))
        self.assertNotIn("My case is consistency", event["dialogue"])


if __name__ == "__main__":
    unittest.main()


class BallotPromptTest(_Base):
    def test_the_vote_field_lists_the_ballot_and_a_retry_says_what_was_wrong(self) -> None:
        voter, *_ = self.players
        scenes: list[str] = []
        prompts: list[str] = []

        def action(**kwargs):
            scenes.append(kwargs["scene_context"])
            if len(scenes) == 1:
                return AgentAction(dialogue="x", inner_thought="", model_id="m", target_id=None, rejected_target="Holly")
            return AgentAction(dialogue="x", inner_thought="", model_id="m", target_id=kwargs["allowed_targets"][0]["agent_id"])

        self._live(action)
        tc._resolve_vote_decision(self.conn, voter, f"vote_booth_{voter}")
        self.assertIn('You answered "Holly", who is not on the ballot.', scenes[1])

        import json as _json
        from backend import openrouter_client

        def fake_post(payload, api_key):
            prompts.append(payload["messages"][1]["content"])
            return {"choices": [{"message": {"content": _json.dumps({"think": "t", "summary": "s", "target_id": "Nobody Here", "confidence": 0.5, "dialogue": "d"})}, "finish_reason": "stop"}]}

        actor = dict(self.conn.execute("SELECT * FROM Agents WHERE agent_id = ?", (voter,)).fetchone())
        targets = [dict(r) for r in self.conn.execute("SELECT * FROM Agents WHERE agent_id != ? ORDER BY agent_id LIMIT 3", (voter,))]
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "k"}), patch.object(openrouter_client, "_post_chat_completion", side_effect=fake_post):
            result = openrouter_client.request_agent_action(
                actor=actor, step="jury_vote_x", scene_context="Vote.", allowed_targets=targets,
                response_kind="jury_vote", episode_context={},
            )
        ballot = ", ".join(f"{t['agent_id']} ({t['pseudonym']})" for t in targets)
        self.assertIn(f"the finalist you vote for: exactly one of {ballot}. Nobody else is on the ballot.", prompts[0])
        self.assertIsNone(result.target_id)
        self.assertEqual(result.rejected_target, "Nobody Here")
