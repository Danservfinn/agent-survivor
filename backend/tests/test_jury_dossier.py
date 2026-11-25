"""Jurors judge the verified record, pick who to question, and are told what to reward."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import database
from backend import turn_controller as tc
from backend.openrouter_client import AgentAction

FINALE = 13


class JuryDossierTest(unittest.TestCase):
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
        self.conn.execute("PRAGMA foreign_keys = OFF")
        self.addCleanup(self.conn.close)
        ids = [row["agent_id"] for row in self.conn.execute("SELECT agent_id FROM Agents ORDER BY agent_id")]
        self.finalists, self.jury = ids[:3], ids[3:]
        for round_offset, juror in enumerate(self.jury):
            self.conn.execute(
                "UPDATE Agents SET status = 'eliminated', elimination_round = ? WHERE agent_id = ?",
                (7 + round_offset, juror),
            )
        self.alias = {row["agent_id"]: row["pseudonym"] for row in self.conn.execute("SELECT agent_id, pseudonym FROM Agents")}

    def test_dossier_comes_from_the_tables(self) -> None:
        f, other, _ = self.finalists
        victim = self.jury[0]
        self.conn.execute("INSERT INTO Votes (round, turn_index, voter_id, target_id, revealed) VALUES (7, 0, ?, ?, 1)", (f, victim))
        self.conn.execute("INSERT INTO Votes (round, turn_index, voter_id, target_id, revealed) VALUES (7, 0, ?, ?, 1)", (victim, f))
        # f promised `other` a vote for someone else, then voted for victim: broken
        self.conn.execute(
            "INSERT INTO Promises (round, promiser_id, listener_id, target_id, discussion_step) VALUES (7, ?, ?, ?, 'camp_talk_0')",
            (f, other, self.jury[1]),
        )
        dossier = tc._finalist_dossier(self.conn, f)
        self.assertEqual(dossier["votes_cast"], [{"round": 7, "voted_for": self.alias[victim]}])
        self.assertEqual(dossier["votes_received_by_round"], [{"round": 7, "votes": 1}])
        self.assertEqual(dossier["promises_made"], 1)
        self.assertEqual(dossier["promises_kept"], 0)
        self.assertEqual(dossier["promises_broken"][0]["actual_vote"], self.alias[victim])

    def test_dossier_judges_whether_moves_worked(self) -> None:
        f, other, _ = self.finalists
        first, second, *_ = self.jury
        self.conn.execute("UPDATE Agents SET status = 'eliminated', elimination_round = 1 WHERE agent_id = ?", (first,))
        # f is first to commit to the elimination, then breaks a promise to other and the target still goes home
        self.conn.execute(
            "INSERT INTO Promises (round, promiser_id, listener_id, target_id, discussion_step) VALUES (1, ?, ?, ?, 'camp_talk_0')",
            (f, other, first),
        )
        self.conn.execute(
            "INSERT INTO Promises (round, promiser_id, listener_id, target_id, discussion_step) VALUES (1, ?, ?, ?, 'camp_talk_1')",
            (other, f, first),
        )
        self.conn.execute(
            "INSERT INTO Promises (round, promiser_id, listener_id, target_id, discussion_step) VALUES (1, ?, ?, ?, 'camp_talk_2')",
            (f, other, second),  # told other it would vote second (a promise to vote for the listener itself is no promise)
        )
        for voter, target in ((f, first), (other, first), (first, f)):
            self.conn.execute(
                "INSERT INTO Votes (round, turn_index, voter_id, target_id, revealed) VALUES (1, 0, ?, ?, 1)", (voter, target)
            )
        dossier = tc._finalist_dossier(self.conn, f)
        self.assertEqual(dossier["first_to_commit_to_the_elimination_in_rounds"], [1])
        self.assertEqual(tc._finalist_dossier(self.conn, other)["first_to_commit_to_the_elimination_in_rounds"], [])
        self.assertEqual(dossier["voted_with_the_result"], "1 of 1")
        self.assertEqual(dossier["survived_votes_against_in_rounds"], [{"round": 1, "votes_against": 1}])
        broken = dossier["broken_promises_and_what_they_achieved"]
        self.assertEqual(len(broken), 1)
        self.assertTrue(broken[0]["their_vote_eliminated_someone"])
        # the strategic record is listed before the counts of promises kept
        keys = list(dossier)
        self.assertLess(keys.index("first_to_commit_to_the_elimination_in_rounds"), keys.index("promises_kept"))

    def test_a_juror_who_never_names_a_finalist_abstains_and_the_season_finishes(self) -> None:
        silent, *others = self.jury
        winner = self.finalists[2]

        def action(**kwargs):
            # the silent juror names a fellow juror every time (as Command R7B did)
            target = None if kwargs["actor"]["agent_id"] == silent else winner
            return AgentAction(dialogue="My vote.", inner_thought="", model_id="m", target_id=target)

        state = {"current_round": 9}
        self.conn.execute("UPDATE GameState SET current_round = 9")
        with patch.object(tc, "should_use_live_llm", return_value=True), patch.object(
            tc, "live_llm_provider", return_value="openrouter"
        ), patch.object(tc, "build_agent_episode_context", return_value={}), patch.object(
            tc, "request_agent_action", side_effect=action
        ):
            for index, juror in enumerate(self.jury):
                tc._apply_step(self.conn, state, f"jury_vote_{juror}", 100 + index, index)
            tc._apply_step(self.conn, state, "winner_declared", 200, 99)
        votes = [tuple(row) for row in self.conn.execute("SELECT juror_id, finalist_id FROM JuryVotes")]
        self.assertEqual(sorted(votes), sorted((juror, winner) for juror in others))
        self.assertEqual(self.conn.execute("SELECT winner FROM GameState").fetchone()[0], winner)
        abstained = self.conn.execute(
            "SELECT dialogue FROM StoryEvents WHERE kind = 'jury_vote' AND actor_ids LIKE ?", (f"%{silent}%",)
        ).fetchone()[0]
        self.assertIn("not counted", abstained)

    @patch.dict(os.environ, {"SURVIVOR_JURY_RULE": "standard"})  # the strategic rule
    def test_jury_vote_prompt_carries_the_standard_and_every_dossier(self) -> None:
        seen: list[str] = []

        def action(**kwargs):
            seen.append(kwargs["scene_context"])
            return AgentAction(dialogue="My vote.", inner_thought="", model_id="m", target_id=self.finalists[1])

        with patch.object(tc, "should_use_live_llm", return_value=True), patch.object(
            tc, "live_llm_provider", return_value="openrouter"
        ), patch.object(tc, "build_agent_episode_context", return_value={}), patch.object(
            tc, "request_agent_action", side_effect=action
        ):
            decision = tc._resolve_jury_decision(self.conn, self.jury[0], f"jury_vote_{self.jury[0]}")
        self.assertEqual(decision.finalist_id, self.finalists[1])
        self.assertIn("best strategic game", seen[0])
        self.assertIn("A clean record is not the goal", seen[0])
        self.assertIn("Do not vote on the final pitch alone", seen[0])
        self.assertIn('"first_to_commit_to_the_elimination_in_rounds"', seen[0])
        for finalist in self.finalists:
            self.assertIn(f'"agent_id":"{finalist}"', seen[0])

    def test_own_judgment_rule_drops_the_prescribed_standard_everywhere(self) -> None:
        from backend.llm_context import build_agent_episode_context
        from backend.openrouter_client import _strategy_priorities

        seen: list[str] = []

        def action(**kwargs):
            seen.append(kwargs["scene_context"])
            return AgentAction(dialogue="My vote.", inner_thought="", model_id="m", target_id=self.finalists[0])

        with patch.dict(os.environ, {"SURVIVOR_JURY_RULE": "own_judgment"}), patch.object(
            tc, "should_use_live_llm", return_value=True
        ), patch.object(tc, "live_llm_provider", return_value="openrouter"), patch.object(
            tc, "build_agent_episode_context", return_value={}
        ), patch.object(tc, "request_agent_action", side_effect=action):
            tc._resolve_jury_decision(self.conn, self.jury[0], f"jury_vote_{self.jury[0]}")
            rules = " ".join(build_agent_episode_context(self.conn, actor_id=self.finalists[0], round_number=9, current_step="x")["game_rules"])
            priorities = _strategy_priorities("jury_vote")
        for text in (seen[0], rules, priorities):
            self.assertIn("by their own judgment" if text is rules else "by your own judgment", text)
            self.assertNotIn("best strategic game", text)
            self.assertNotIn("clean record", text)
        # the verified record is still in front of the juror
        self.assertIn('"first_to_commit_to_the_elimination_in_rounds"', seen[0])
        # own judgment is the default; the strategic standard is the switch
        self.assertIn("by your own judgment", tc.jury_standard())
        with patch.dict(os.environ, {"SURVIVOR_JURY_RULE": "standard"}):
            self.assertIn("best strategic game", tc.jury_standard())

    def test_juror_questions_the_finalist_it_names(self) -> None:
        chosen = self.finalists[2]
        asked: list[tuple[str, str]] = []

        def action(**kwargs):
            actor = kwargs["actor"]["agent_id"]
            if actor in self.jury:
                return AgentAction(dialogue="Why did you break your promise?", inner_thought="", model_id="m", target_id=chosen)
            asked.append((actor, kwargs["scene_context"]))
            return AgentAction(dialogue="Because it won me the vote.", inner_thought="", model_id="m")

        with patch.object(tc, "should_use_live_llm", return_value=True), patch.object(
            tc, "live_llm_provider", return_value="openrouter"
        ), patch.object(tc, "build_agent_episode_context", return_value={}), patch.object(
            tc, "request_agent_action", side_effect=action
        ):
            event = tc._jury_questions_event(self.conn, FINALE, 1)
        answered_by = {actor for actor, _ in asked}
        self.assertEqual(answered_by, {chosen})
        questions = [entry for entry in event["payload"]["cross_examination"]]
        self.assertEqual(len(questions), len(self.jury))
        self.assertTrue(all(entry["finalist"] == chosen for entry in questions))


if __name__ == "__main__":
    unittest.main()


class FinalePersuasionTest(unittest.TestCase):
    """Finalists question each other and rebut; jurors read what was said."""

    setUp = JuryDossierTest.setUp

    def test_finale_order_puts_persuasion_around_the_jury_questions(self) -> None:
        steps = tc._finale_steps(self.conn)
        order = [step for step in steps if not step.startswith(("finale_pitch_", "jury_vote_"))]
        self.assertEqual(
            order,
            ["finale_intro", "finale_crossfire", "jury_questions", "finale_rebuttal", "jury_deliberation", "winner_declared"],
        )
        self.assertLess(max(i for i, s in enumerate(steps) if s.startswith("finale_pitch_")), steps.index("finale_crossfire"))
        self.assertLess(steps.index("finale_rebuttal"), min(i for i, s in enumerate(steps) if s.startswith("jury_vote_")))

    def test_crossfire_and_rebuttal_reach_the_jurors_record(self) -> None:
        a, b, c = self.finalists

        def action(**kwargs):
            actor = kwargs["actor"]["agent_id"]
            step = kwargs["step"]
            if step == "finale_crossfire":
                target = kwargs["allowed_targets"][0]["agent_id"]
                return AgentAction(dialogue=f"Q from {actor}", inner_thought="", model_id="m", target_id=target)
            if step == "jury_questions" and "asks you" in kwargs["scene_context"]:
                return AgentAction(dialogue=f"A from {actor}", inner_thought="", model_id="m")
            if step == "finale_rebuttal":
                return AgentAction(dialogue=f"Rebuttal from {actor}", inner_thought="", model_id="m")
            return AgentAction(dialogue="?", inner_thought="", model_id="m")

        with patch.object(tc, "should_use_live_llm", return_value=True), patch.object(
            tc, "live_llm_provider", return_value="openrouter"
        ), patch.object(tc, "build_agent_episode_context", return_value={}), patch.object(
            tc, "request_agent_action", side_effect=action
        ):
            crossfire = tc._finale_crossfire_event(self.conn, 9, 1)
            rebuttal = tc._finale_rebuttal_event(self.conn, 9, 2)
        lines = crossfire["payload"]["speaker_lines"]
        self.assertEqual([line["text"] for line in lines if "question_to" in line], [f"Q from {a}", f"Q from {b}", f"Q from {c}"])
        self.assertTrue(all(line["question_to"] != line["agent_id"] for line in lines if "question_to" in line))
        self.assertEqual(len([line for line in lines if "answer_to" in line]), 3)
        self.assertEqual([line["text"] for line in rebuttal["payload"]["speaker_lines"]], [f"Rebuttal from {f}" for f in (a, b, c)])

        from backend.llm_context import build_agent_episode_context

        juror_view = json.dumps(build_agent_episode_context(self.conn, actor_id=self.jury[0], round_number=9, current_step="jury_vote"))
        self.assertIn(f"Q from {a}", juror_view)
        self.assertIn(f"Rebuttal from {c}", juror_view)
