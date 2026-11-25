"""The host questions players at tribal council, from the public record only."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from backend import turn_controller as tc
from backend.llm_context import build_agent_episode_context, public_record
from backend.openrouter_client import AgentAction
from backend.tests.test_model_failures import _Base


def _action(**fields) -> AgentAction:
    return AgentAction(**({"dialogue": "line", "inner_thought": "", "model_id": "m"} | fields))


class PublicRecordTest(_Base):
    def test_the_record_has_public_facts_and_nothing_private(self) -> None:
        a, b, c, *_ = self.players
        names = {r["agent_id"]: r["pseudonym"] for r in self.conn.execute("SELECT agent_id, pseudonym FROM Agents")}
        tc._insert_story_event(
            self.conn, turn_id=1, round_number=2, phase="camp", kind="conversation", scene="camp", shot="x",
            actor_ids=[a, b], target_ids=[], visibility="audience", title="Talk", dialogue="SECRET PLAN",
            payload={"privacy": "participants_only", "participant_ids": [a, b], "speaker_lines": [{"agent_id": a, "text": "SECRET PLAN"}]},
        )
        tc._insert_story_event(
            self.conn, turn_id=1, round_number=2, phase="camp", kind="camp_meetings", scene="camp", shot="x",
            actor_ids=["host"], target_ids=[], visibility="audience", title="Who Met Whom",
            dialogue=f"Seen around camp: {names[a]} met {names[b]}.", payload={},
        )
        tc._insert_story_event(
            self.conn, turn_id=1, round_number=2, phase="challenge", kind="immunity_given", scene="challenge", shot="x",
            actor_ids=["host", a, c], target_ids=[c], visibility="audience", title=f"{names[a]} Gives Away Immunity",
            dialogue="d", payload={"giver_id": a, "recipient_id": c},
        )
        self.conn.execute("INSERT INTO Votes (round, turn_index, voter_id, target_id, revealed) VALUES (1, 0, ?, ?, 1)", (a, b))
        with patch.dict(os.environ, {"SURVIVOR_SECRET_BALLOT": "1"}):
            record = public_record(self.conn, 2)
        text = json.dumps(record)
        self.assertNotIn("SECRET PLAN", text)
        self.assertIn("Who Met Whom", text)
        self.assertIn("Gives Away Immunity", text)
        self.assertEqual(record["vote_tallies"], [{"round": 1, "tally": {names[b]: 1}}])  # the tally, not the voter


class QuestioningTest(_Base):
    def _run(self, questions, answer=None):
        heard = []

        def reply(**kwargs):
            heard.append((kwargs["actor"]["agent_id"], kwargs["scene_context"]))
            return answer(kwargs) if answer else _action(dialogue=f"answer from {kwargs['actor']['agent_id']}", truth="lie")

        self._live(reply)
        with patch.object(tc, "request_host_questions", return_value=questions):
            event = tc._host_questioning_event(self.conn, 1, 1)
        return event, heard

    def test_each_asked_player_answers_in_public_hearing_the_earlier_answers(self) -> None:
        a, b, c, *_ = self.players
        names = {r["agent_id"]: r["pseudonym"] for r in self.conn.execute("SELECT agent_id, pseudonym FROM Agents")}
        event, heard = self._run([
            {"to": names[a], "question": "Why did you meet Fir?"},
            {"to": "Nobody", "question": "Who are you?"},  # not a player: skipped
            {"to": names[a], "question": "Again?"},  # asked already: skipped
            {"to": b, "question": "Are you on the bottom?"},
        ])
        self.assertEqual([actor for actor, _ in heard], [a, b])
        self.assertIn("Why did you meet Fir?", heard[0][1])
        self.assertIn(f"answer from {a}", heard[1][1])  # b hears a's answer
        self.assertEqual(event["visibility"], "audience")
        lines = event["payload"]["speaker_lines"]
        self.assertEqual([l["agent_id"] for l in lines], ["host", a, "host", b])
        self.assertEqual(lines[0]["question_to"], a)
        # an answer is a public speech: its lie label is kept with everyone as listeners
        row = self.conn.execute("SELECT agent_id, truth, listeners FROM Lies ORDER BY id").fetchone()
        self.assertEqual((row["agent_id"], row["truth"], json.loads(row["listeners"])), (a, "lie", None))

    def test_the_others_see_the_questions_and_answers(self) -> None:
        a, b, c, *_ = self.players
        self._run([{"to": a, "question": "Why did you meet Fir?"}])
        context = build_agent_episode_context(self.conn, actor_id=c, round_number=1, current_step="tribal_council")
        self.assertIn("Why did you meet Fir?", json.dumps(context))

    def test_no_usable_answer_is_a_silent_line(self) -> None:
        a, *_ = self.players
        event, _ = self._run([{"to": a, "question": "Well?"}], answer=lambda kwargs: None)
        self.assertEqual(event["payload"]["speaker_lines"][1]["text"], tc.SILENT_LINE)

    def test_a_host_failure_skips_the_questions(self) -> None:
        self._live(lambda **kwargs: _action())
        with patch.object(tc, "request_host_questions", side_effect=RuntimeError("down")):
            self.assertIsNone(tc._host_questioning_event(self.conn, 1, 1))

    def test_the_step_runs_before_the_speeches_and_can_be_switched_off(self) -> None:
        state = {"current_round": 1, "phase": "round", "phase_step": "challenge_intro"}
        with patch.dict(os.environ, {"LLM_SURVIVOR_FAST_BENCHMARK": "1"}):
            steps = tc._phase_steps(self.conn, state)
            self.assertLess(steps.index("host_questioning"), steps.index("tribal_council"))
            with patch.dict(os.environ, {"SURVIVOR_HOST_QUESTIONS": "0"}):
                self.assertNotIn("host_questioning", tc._phase_steps(self.conn, state))


if __name__ == "__main__":
    unittest.main()


class HostRuleTest(unittest.TestCase):
    def test_the_rules_mention_the_host_only_while_on(self) -> None:
        from backend.llm_context import game_rules

        self.assertTrue(any("host questions a few players" in rule for rule in game_rules()))
        with patch.dict(os.environ, {"SURVIVOR_HOST_QUESTIONS": "0"}):
            self.assertFalse(any("host questions a few players" in rule for rule in game_rules()))
