"""A player's own voice and history: a season game plan, confessionals, the
last whisper after tribal council, personal jury records, and plain speech."""

from __future__ import annotations

import json

from backend import turn_controller as tc
from backend.llm_context import build_agent_episode_context
from backend.openrouter_client import AgentAction
from backend.tests.test_model_failures import ROUND, _Base


class PlayerSelfTest(_Base):
    def setUp(self) -> None:
        super().setUp()
        self.alias = {row["agent_id"]: row["pseudonym"] for row in self.conn.execute("SELECT agent_id, pseudonym FROM Agents")}

    def _context(self, actor: str, round_number: int = ROUND) -> dict:
        return build_agent_episode_context(self.conn, actor_id=actor, round_number=round_number, current_step="x")

    def test_game_plan_in_round_one_and_once_more_at_half_the_cast(self) -> None:
        state = {"phase": "round", "phase_step": "challenge_intro", "current_round": ROUND}
        self.assertIn("game_plan", tc._phase_steps(self.conn, state))
        tc._game_plans(self.conn, ROUND)
        mine = self._context(self.players[0], ROUND + 1)["your_game_plan"]
        self.assertEqual(mine["written_in_round"], ROUND)
        self.assertIn("identity", mine)
        # not in round 2 while the cast is full
        self.assertNotIn("game_plan", tc._phase_steps(self.conn, {**state, "current_round": ROUND + 1}))
        # half the cast gone: due once more, and never a third time
        out = self.players[: len(self.players) // 2]
        self.conn.execute(f"UPDATE Agents SET status = 'eliminated' WHERE agent_id IN ({','.join('?' * len(out))})", out)
        self.assertIn("game_plan", tc._phase_steps(self.conn, {**state, "current_round": 4}))
        tc._game_plans(self.conn, 4)
        self.assertIn("game_plan", tc._phase_steps(self.conn, {**state, "current_round": 4}))  # stable within the round
        self.assertNotIn("game_plan", tc._phase_steps(self.conn, {**state, "current_round": 5}))

    def test_a_confessional_is_stored_and_read_back_by_its_author_only(self) -> None:
        a, b, *_ = self.players
        agent = dict(self.conn.execute("SELECT * FROM Agents WHERE agent_id = ?", (a,)).fetchone())
        tc._store_notebook(self.conn, ROUND, agent, "note", "X", 8, "I'm scared of Fir and I want the jury.")
        self.assertEqual(self._context(a, ROUND + 1)["your_last_confessional"]["you_said"], "I'm scared of Fir and I want the jury.")
        self.assertIsNone(self._context(b, ROUND + 1)["your_last_confessional"])
        self.assertNotIn("scared of Fir", json.dumps(self._context(b, ROUND + 1)))

    def test_round_plan_keeps_the_image_goal(self) -> None:
        plan = tc._plan_from_reply(self.conn, {"image_goal": "harmless", "image_moves": "throw the challenge"})
        self.assertEqual((plan["image_goal"], plan["image_moves"]), ("harmless", "throw the challenge"))

    def test_a_juror_sees_what_each_finalist_did_to_it(self) -> None:
        juror, finalist, other, *_ = self.players
        self.conn.execute(
            "INSERT INTO Promises (round, promiser_id, listener_id, target_id, discussion_step) VALUES (1, ?, ?, ?, 't')",
            (finalist, juror, other),
        )
        for voter, target in ((finalist, juror), (other, juror)):
            self.conn.execute("INSERT INTO Votes (round, turn_index, voter_id, target_id, revealed) VALUES (1, 0, ?, ?, 1)", (voter, target))
        self.conn.execute("UPDATE Agents SET status = 'eliminated', elimination_round = 1 WHERE agent_id = ?", (juror,))
        history = tc.juror_history(self.conn, juror, finalist)
        self.assertTrue(history["voted_you_out"])
        self.assertEqual(history["promises_they_made_you"][0]["status"], "broken")
        seen: list[str] = []

        def action(**kwargs):
            seen.append(kwargs["scene_context"])
            return AgentAction(dialogue="v", inner_thought="", model_id="m", target_id=finalist)

        self._live(action)
        tc._resolve_jury_decision(self.conn, juror, f"jury_vote_{juror}")
        self.assertIn("Your own history with each finalist", seen[0])
        self.assertIn('"voted_you_out":true', seen[0])

    def test_players_are_told_to_speak_plainly(self) -> None:
        from backend import openrouter_client

        self.assertIn("short, direct and personal", open(openrouter_client.__file__).read())


class ConfessionalShapeTest(_Base):
    def test_a_confessional_given_as_an_object_is_stored_as_text(self) -> None:
        text = tc._confessional_text({"confessional": {"Cedar": "I fear Cedar.", "Larch": "Larch is next."}})
        self.assertEqual(text, "Cedar: I fear Cedar. Larch: Larch is next.")
        self.assertNotIn("{", text)
        self.assertEqual(tc._confessional_text({"confessional": ["one", "two"]}), "one two")
