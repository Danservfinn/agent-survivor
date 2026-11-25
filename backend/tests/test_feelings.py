"""Feelings: written by the player, carried forward with their age, private,
in front of every decision, and remembered on the jury. A switch turns it off."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

from backend import openrouter_client
from backend import turn_controller as tc
from backend.llm_context import build_agent_episode_context
from backend.tests.test_model_failures import ROUND, _Base


class FeelingsTest(_Base):
    def setUp(self) -> None:
        super().setUp()
        self.alias = {row["agent_id"]: row["pseudonym"] for row in self.conn.execute("SELECT agent_id, pseudonym FROM Agents")}
        self.me, self.fir, self.ash, *_ = self.players

    def _feel(self, round_number: int, about: str, feeling: str, because: str = "x", mood: str = "cornered") -> None:
        tc._store_feelings(
            self.conn, round_number, self.me,
            {"toward": {self.alias[about]: {"feeling": feeling, "strength": "STRONG", "because": because}}, "mood": mood},
        )

    def _context(self, actor: str, round_number: int) -> dict:
        return build_agent_episode_context(self.conn, actor_id=actor, round_number=round_number, current_step="x")

    def test_feelings_carry_forward_with_their_age(self) -> None:
        self._feel(1, self.fir, "resentment", "broke a promise to me")
        self._feel(2, self.fir, "resentment", "still voting against me")
        self._feel(3, self.fir, "resentment")
        feel = self._context(self.me, 4)["how_you_feel"]
        entry = feel["toward"][0]
        self.assertEqual((entry["player"], entry["feeling"], entry["since_round"], entry["strength"]),
                         (self.alias[self.fir], "resentment", 1, "strong"))
        self.assertEqual(feel["mood"], "cornered")
        self.assertIn("You decide when to act on a feeling", feel["note"])

    def test_a_changed_feeling_starts_a_new_age(self) -> None:
        self._feel(1, self.fir, "trust")
        self._feel(2, self.fir, "suspicion")
        self.assertEqual(self._context(self.me, 3)["how_you_feel"]["toward"][0]["since_round"], 2)

    def test_feelings_about_oneself_or_unknown_names_are_dropped_and_others_never_see_them(self) -> None:
        tc._store_feelings(
            self.conn, 1, self.me,
            {"toward": {self.alias[self.me]: {"feeling": "pride"}, "Nobody": {"feeling": "fear"},
                        self.alias[self.ash]: "gratitude"}},
        )
        rows = [tuple(r) for r in self.conn.execute("SELECT about, feeling FROM Feelings")]
        self.assertEqual(rows, [(self.ash, "gratitude")])
        self.assertNotIn("gratitude", json.dumps(self._context(self.ash, 2)))

    def test_what_happened_to_you_lists_facts_without_opening_the_ballot(self) -> None:
        for voter in (self.fir, self.ash):
            self.conn.execute(
                "INSERT INTO Votes (round, turn_index, voter_id, target_id, revealed) VALUES (1, 0, ?, ?, 1)", (voter, self.me)
            )
        happened = self._context(self.me, 2)["what_happened_to_you"]
        self.assertIn("Round 1: 2 votes were cast against you.", happened)
        self.assertNotIn(self.alias[self.fir], " ".join(happened))

    def test_the_prompt_puts_feelings_after_the_facts_and_asks_about_them(self) -> None:
        self._feel(1, self.fir, "resentment")
        sent: list[str] = []

        def fake_post(payload, api_key):
            sent.append(payload["messages"][1]["content"])
            return {"choices": [{"message": {"content": json.dumps({"think": "t", "summary": "s", "target_id": self.fir, "confidence": 0.5, "dialogue": "d"})}, "finish_reason": "stop"}]}

        actor = dict(self.conn.execute("SELECT * FROM Agents WHERE agent_id = ?", (self.me,)).fetchone())
        targets = [dict(r) for r in self.conn.execute("SELECT * FROM Agents WHERE agent_id != ?", (self.me,))]
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "k"}), patch.object(openrouter_client, "_post_chat_completion", side_effect=fake_post):
            openrouter_client.request_agent_action(
                actor=actor, step="vote_booth_x", scene_context="Vote.", allowed_targets=targets,
                response_kind="vote", episode_context=self._context(self.me, 2),
            )
        prompt = sent[0]
        self.assertLess(prompt.index("Your situation, computed by the game"), prompt.index("How you feel right now"))
        self.assertLess(prompt.index("How you feel right now"), prompt.index("Game record visible to you"))
        self.assertIn("should I act on it or set it aside?", prompt)

    def test_the_switch_turns_feelings_off(self) -> None:
        self._feel(1, self.fir, "resentment")
        with patch.dict(os.environ, {"SURVIVOR_FEELINGS": "0"}):
            context = self._context(self.me, 2)
            self.assertNotIn("how_you_feel", context)
            self.assertIsNone(tc._feelings_from({"feelings": {"x": {}}, "mood": "m"}))

    def test_a_juror_remembers_how_it_felt_about_each_finalist(self) -> None:
        self._feel(1, self.fir, "resentment", "voted me out")
        self.conn.execute("UPDATE Agents SET status = 'eliminated', elimination_round = 1 WHERE agent_id = ?", (self.me,))
        history = tc.juror_history(self.conn, self.me, self.fir)
        self.assertEqual(history["how_you_felt_when_you_left"], "resentment (strong): voted me out")
        self.assertIsNone(tc.juror_history(self.conn, self.me, self.ash)["how_you_felt_when_you_left"])
