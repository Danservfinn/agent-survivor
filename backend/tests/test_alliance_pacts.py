"""Alliances form only from declared, mutual pacts, and never with oneself."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import database, openrouter_client
from backend import turn_controller as tc
from backend.openrouter_client import AgentAction


class AlliancePactTest(unittest.TestCase):
    def setUp(self) -> None:
        env = patch.dict(os.environ, {"OPENROUTER_API_KEY": "", "LLM_PROVIDER": "openrouter"}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        original = database.DATABASE_PATH
        database.DATABASE_PATH = Path(temp_dir.name) / "survivor.db"
        self.addCleanup(setattr, database, "DATABASE_PATH", original)
        database.seed_demo(reset=True, roster_preset="cheap_recent_openrouter")
        self.conn = database.get_db_connection()
        self.addCleanup(self.conn.close)
        self.a, self.b, *_ = [row["agent_id"] for row in self.conn.execute("SELECT agent_id FROM Agents ORDER BY agent_id")]

    def _lines(self, *intents: tuple[str, str]) -> list[dict]:
        return [{"agent_id": agent, "join_intent": intent} for agent, intent in intents]

    def _alliances(self) -> list[tuple]:
        return [tuple(row) for row in self.conn.execute("SELECT name FROM Alliances")]

    def test_a_player_cannot_ally_with_itself(self) -> None:
        result = tc._maybe_update_alliance(self.conn, 1, 1, [self.a, self.b], self._lines((self.a, "join"), (self.a, "join")))
        self.assertEqual(result, (None, "none"))
        self.assertEqual(self._alliances(), [])

    def test_both_players_must_declare_a_pact(self) -> None:
        one_sided = self._lines((self.a, "join"), (self.b, "none"), (self.a, "join"), (self.b, "none"))
        self.assertEqual(tc._maybe_update_alliance(self.conn, 1, 1, [self.a, self.b], one_sided), (None, "none"))
        mutual = self._lines((self.a, "none"), (self.b, "join"), (self.a, "join"), (self.b, "join"))
        alliance_id, status = tc._maybe_update_alliance(self.conn, 1, 2, [self.a, self.b], mutual)
        self.assertEqual(status, "formed")
        name = self.conn.execute("SELECT name FROM Alliances WHERE alliance_id = ?", (alliance_id,)).fetchone()[0]
        self.assertEqual(len(name.split(" + ")), 2)
        self.assertEqual(len(set(name.split(" + "))), 2)

    def test_the_last_line_decides(self) -> None:
        retracted = self._lines((self.a, "join"), (self.b, "join"), (self.a, "join"), (self.b, "none"))
        self.assertEqual(tc._maybe_update_alliance(self.conn, 1, 1, [self.a, self.b], retracted), (None, "none"))

    def test_join_intent_comes_only_from_a_declared_pact(self) -> None:
        promise_only = AgentAction(dialogue="x", inner_thought="", model_id="m", move_type="vote_commitment")
        self.assertEqual(tc._join_intent(promise_only), "none")
        self.assertEqual(tc._join_intent(AgentAction(dialogue="x", inner_thought="", model_id="m", pact=True)), "join")

    def test_the_talk_reply_asks_for_a_pact(self) -> None:
        sent: list[dict] = []

        def fake_post(payload: dict, api_key: str) -> dict:
            sent.append(payload)
            reply = {"think": "t", "summary": "s", "target_id": None, "promised_vote_target": None,
                     "pact": True, "confidence": 0.5, "dialogue": "We stick together."}
            return {"choices": [{"message": {"content": json.dumps(reply)}}]}

        actor = dict(self.conn.execute("SELECT * FROM Agents WHERE agent_id = ?", (self.a,)).fetchone())
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "k"}), patch(
            "backend.openrouter_client._post_chat_completion", side_effect=fake_post
        ):
            action = openrouter_client.request_agent_action(
                actor=actor, step="camp_talk_0", scene_context="{}", allowed_targets=[],
                response_kind="group_conversation", episode_context={},
            )
        self.assertIn('"pact"', sent[0]["messages"][1]["content"])
        self.assertTrue(action.pact)


if __name__ == "__main__":
    unittest.main()
