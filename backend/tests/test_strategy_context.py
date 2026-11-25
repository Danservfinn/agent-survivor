"""v3 player context and prompt: computed facts first, a strategist's checklist,
thinking before deciding, fewer reply fields, and talks with a purpose."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import database, openrouter_client
from backend import turn_controller as tc
from backend.llm_context import build_agent_episode_context
from backend.openrouter_client import AgentAction


class _Season(unittest.TestCase):
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
        self.ids = [row["agent_id"] for row in self.conn.execute("SELECT agent_id FROM Agents ORDER BY agent_id")]
        self.alias = {row["agent_id"]: row["pseudonym"] for row in self.conn.execute("SELECT agent_id, pseudonym FROM Agents")}

    def _vote(self, round_number: int, voter: str, target: str) -> None:
        self.conn.execute(
            "INSERT INTO Votes (round, turn_index, voter_id, target_id, revealed) VALUES (?, 0, ?, ?, 1)",
            (round_number, voter, target),
        )


class SituationBriefTest(_Season):
    @patch.dict(os.environ, {"SURVIVOR_SECRET_BALLOT": "0"})  # the open-ballot rule
    def test_brief_counts_the_vote_and_finds_who_votes_together(self) -> None:
        me, a, b, out, *rest = self.ids
        self.conn.execute("UPDATE Agents SET status = 'eliminated', elimination_round = 1 WHERE agent_id = ?", (out,))
        for voter in (a, b):
            self._vote(1, voter, out)
        self._vote(1, me, a)  # a and b voted together; I voted against a
        self._vote(1, out, me)
        self.conn.execute("UPDATE Agents SET has_immunity = 1 WHERE agent_id = ?", (b,))

        brief = build_agent_episode_context(self.conn, actor_id=me, round_number=2, current_step="x")["situation_brief"]
        tonight = brief["tonight"]
        self.assertEqual(tonight["players_left"], len(self.ids) - 1)
        self.assertEqual(tonight["votes_for_a_majority"], (len(self.ids) - 1) // 2 + 1)
        self.assertIn(self.alias[b], tonight["immune"])
        self.assertNotIn(self.alias[b], tonight["can_be_voted_out"])
        self.assertEqual(brief["voting_together"][0]["players"], [self.alias[a], self.alias[b]])
        self.assertEqual(brief["votes_against_you"], [{"round": 1, "by": self.alias[out]}])
        juror = brief["jury_so_far"][0]
        self.assertEqual(juror["name"], self.alias[out])
        self.assertEqual(sorted(juror["voted_out_by"]), sorted([self.alias[a], self.alias[b]]))
        self.assertFalse(juror["you_voted_them_out"])
        players = {player["name"]: player for player in brief["other_players_by_threat"]}
        self.assertEqual(players[self.alias[a]]["voted_against_you"], 0)
        self.assertNotIn(self.alias[out], players)

    @patch.dict(os.environ, {"SURVIVOR_SECRET_BALLOT": "0"})  # the open-ballot rule

    def test_claims_carry_the_speakers_promise_record(self) -> None:
        me, liar, target, other, *_ = self.ids
        self.conn.execute(
            "INSERT INTO Promises (round, promiser_id, listener_id, target_id, discussion_step) VALUES (1, ?, ?, ?, 'camp_talk_0')",
            (liar, me, target),
        )
        self._vote(1, liar, other)  # broke it
        tc._insert_story_event(
            self.conn,
            turn_id=1,
            round_number=2,
            phase="camp",
            kind="conversation",
            scene="camp",
            shot="group_huddle",
            actor_ids=[me, liar],
            target_ids=[],
            visibility="audience",
            title="Talk",
            dialogue="x",
            payload={
                "privacy": "participants_only",
                "participant_ids": [me, liar],
                "speaker_lines": [{"agent_id": liar, "text": "I am with you tonight."}],
            },
        )
        claims = build_agent_episode_context(self.conn, actor_id=me, round_number=2, current_step="x")["what_others_told_you"]
        self.assertEqual(claims, [{"round": 2, "from": self.alias[liar], "said": "I am with you tonight.", "promises_kept": 0, "promises_broken": 1}])
        bystander = build_agent_episode_context(self.conn, actor_id=other, round_number=2, current_step="x")
        self.assertEqual(bystander["what_others_told_you"], [])


class PromptV3Test(_Season):
    def _prompt(self, response_kind: str, reply: dict) -> tuple[str, AgentAction]:
        sent: list[dict] = []

        def fake_post(payload: dict, api_key: str) -> dict:
            sent.append(payload)
            return {"choices": [{"message": {"content": json.dumps(reply)}}]}

        actor = dict(self.conn.execute("SELECT * FROM Agents WHERE agent_id = ?", (self.ids[0],)).fetchone())
        targets = [dict(row) for row in self.conn.execute("SELECT * FROM Agents WHERE agent_id != ?", (self.ids[0],))]
        context = build_agent_episode_context(self.conn, actor_id=self.ids[0], round_number=1, current_step="x")
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "k"}), patch(
            "backend.openrouter_client._post_chat_completion", side_effect=fake_post
        ):
            action = openrouter_client.request_agent_action(
                actor=actor, step="x", scene_context="Vote now.", allowed_targets=targets,
                response_kind=response_kind, episode_context=context,
            )
        return sent[0]["messages"][1]["content"], action

    def test_facts_come_first_and_thinking_precedes_the_spoken_line(self) -> None:
        reply = {"think": "Cedar and Ash vote together; I need Fir.", "summary": "Vote Cedar to break the pair.",
                 "target_id": self.ids[1], "confidence": 0.7, "dialogue": "I am voting for the bigger threat."}
        prompt, action = self._prompt("vote", reply)
        self.assertLess(prompt.index("Your situation, computed by the game"), prompt.index("Game record visible to you"))
        self.assertIn("Where are the votes tonight?", prompt)
        self.assertIn("who beats me with this jury", prompt)
        self.assertLess(prompt.index('"think"'), prompt.index('"target_id"'))
        self.assertLess(prompt.index('"target_id"'), prompt.index('"dialogue"'))
        self.assertIn('"predicted_votes"', prompt)
        self.assertNotIn("Do not output chain-of-thought", prompt)
        for dropped in ("win_condition", "threat_assessment", "public_archetype", "move_type"):
            self.assertNotIn(dropped, prompt)
        # the summary is kept as the private thought; the thinking itself is not stored
        self.assertEqual(action.inner_thought, "Vote Cedar to break the pair.")
        self.assertNotIn("Fir", json.dumps(action.__dict__))
        self.assertEqual(action.prompt_profile, "win-max-v3-think-first")

    def test_only_the_steps_own_fields_are_asked_for(self) -> None:
        prompt, action = self._prompt(
            "group_conversation",
            {"think": "t", "summary": "s", "target_id": None, "promised_vote_target": self.ids[2], "confidence": 0.5, "dialogue": "I will vote with you."},
        )
        self.assertIn('"promised_vote_target"', prompt)
        self.assertNotIn('"predicted_votes"', prompt)
        self.assertEqual(action.promised_vote_target, self.ids[2])
        self.assertEqual(action.move_type, "vote_commitment")


class TalkPurposeTest(_Season):
    def test_the_chosen_ask_reaches_the_chooser_and_the_last_line_must_answer(self) -> None:
        chooser, partner, *_ = self.ids
        scenes: list[tuple[str, str]] = []

        def action(**kwargs):
            actor = kwargs["actor"]["agent_id"]
            if kwargs["response_kind"] == "talk_choice":
                if actor != chooser:
                    return None
                return AgentAction(dialogue="Talk?", inner_thought="", model_id="m", target_id=partner,
                                   strategic_summary="I want your vote on Cedar tonight.")
            scenes.append((actor, kwargs["scene_context"]))
            return AgentAction(dialogue="Yes, Cedar it is.", inner_thought="", model_id="m")

        with patch.object(tc, "should_use_live_llm", return_value=True), patch.object(
            tc, "live_llm_provider", return_value="openrouter"
        ), patch.object(tc, "build_agent_episode_context", return_value={}), patch.object(
            tc, "request_agent_action", side_effect=action
        ):
            tc._camp_talk_events(self.conn, 1, 1)
        chooser_scenes = [json.loads(scene) for actor, scene in scenes if actor == chooser]
        self.assertEqual(chooser_scenes[0]["your_ask_in_this_talk"], "I want your vote on Cedar tonight.")
        self.assertEqual(chooser_scenes[0]["instruction"], "Open by making your ask plainly.")
        last_lines = [json.loads(scene)["instruction"] for _, scene in scenes[-2:]]
        self.assertTrue(all("yes, no, or counter-offer" in line for line in last_lines))


if __name__ == "__main__":
    unittest.main()
