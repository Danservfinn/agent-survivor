"""The challenge ladder decides immunity on correct answers only, and the
winner's hidden-idol gift follows its rules."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import database
from backend import turn_controller as tc
from backend.challenge_bank import LADDER_TIERS
from backend.llm_context import build_agent_episode_context
from backend.openrouter_client import ChallengeSolution

ROUND = 1


class _SeasonDb(unittest.TestCase):
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
        self.players = [row["agent_id"] for row in self.conn.execute("SELECT agent_id FROM Agents ORDER BY agent_id")]


class LadderScoringTest(_SeasonDb):
    """Live path with the model call stubbed: each player solves a chosen set of rungs."""

    def _run(self, solved: dict[str, set[int]], delays: dict[str, float] | None = None) -> dict:
        delays = delays or {}

        def fake_solution(*, actor, puzzles, episode_context):
            time.sleep(delays.get(actor["agent_id"], 0))
            rungs = solved.get(actor["agent_id"], set())
            answers = {
                puzzle["puzzle_id"]: puzzle["answer"] if puzzle["rung"] in rungs else "wrong"
                for puzzle in puzzles
            }
            return ChallengeSolution(answers=answers, explanation="", model_id="test/model")

        with patch.object(tc, "should_use_live_llm", return_value=True), patch.object(
            tc, "live_llm_provider", return_value="openrouter"
        ), patch.object(tc, "build_agent_episode_context", return_value={}), patch.object(
            tc, "request_challenge_solution", side_effect=fake_solution
        ):
            ladder = tc._round_ladder(self.conn, ROUND)
            for attempt in tc._live_challenge_attempts(self.conn, ROUND, ladder, tc._active_agent_rows(self.conn)):
                tc._insert_challenge_attempt(self.conn, ROUND, ladder[attempt.rung_index]["puzzle_id"], attempt)
            return tc._ensure_challenge_result(self.conn, ROUND)

    def _immune(self) -> list[str]:
        return [row["agent_id"] for row in self.conn.execute("SELECT agent_id FROM Agents WHERE has_immunity = 1")]

    def test_most_correct_wins_even_when_slowest(self) -> None:
        slow, *others = self.players
        solved = {slow: {0, 1, 2, 3}, **{agent: {0, 1} for agent in others}}
        result = self._run(solved, delays={slow: 0.3})
        self.assertEqual(result["winning_agent_id"], slow)
        self.assertEqual(self._immune(), [slow])
        # every player was waited for: one row per player per rung
        rows = self.conn.execute("SELECT COUNT(*) AS n FROM ChallengeAttempts WHERE round = ?", (ROUND,)).fetchone()
        self.assertEqual(rows["n"], len(self.players) * len(LADDER_TIERS))

    def test_equal_count_goes_to_the_harder_rungs(self) -> None:
        a, b, *_ = self.players
        result = self._run({a: {0, 1, 2, 3}, b: {0, 1, 2, 4}})
        self.assertEqual(result["winning_agent_id"], b)
        self.assertIsNone(result["result_payload"]["sudden_death_scores"])

    def test_exact_tie_goes_to_sudden_death(self) -> None:
        a, b, *_ = self.players
        # rungs 5-9 are the sudden-death puzzles
        result = self._run({a: {0, 1, 2, 5, 6}, b: {0, 1, 2, 5}})
        self.assertEqual(result["winning_agent_id"], a)
        self.assertEqual(result["result_payload"]["sudden_death_scores"], {a: 2, b: 1})
        sudden_players = {
            row["agent_id"]
            for row in self.conn.execute("SELECT agent_id FROM ChallengeAttempts WHERE rung_index >= 5")
        }
        self.assertEqual(sudden_players, {a, b})

    def test_tie_after_sudden_death_means_nobody_is_immune(self) -> None:
        a, b, *_ = self.players
        result = self._run({a: {0, 1, 5}, b: {0, 1, 5}})
        self.assertIsNone(result["winning_agent_id"])
        self.assertEqual(result["immunity_agent_ids"], [])
        self.assertEqual(result["result_payload"]["tied"], sorted([a, b]))
        self.assertEqual(self._immune(), [])

    def test_nobody_scores_means_nobody_is_immune(self) -> None:
        result = self._run({})
        self.assertIsNone(result["winning_agent_id"])
        self.assertEqual(self._immune(), [])


class IdolGiftTest(_SeasonDb):
    """Deterministic season: the round-1 winner is the first player and gives the
    idol to the first other player."""

    def _advance_to(self, step: str, round_number: int) -> None:
        for _ in range(400):
            state = self.conn.execute("SELECT current_round, phase_step FROM GameState").fetchone()
            if state["current_round"] == round_number and state["phase_step"] == step:
                return
            self.conn.commit()
            if state["phase_step"] == "complete":
                tc.start_next_round()
            else:
                tc.advance_turn()
        self.fail(f"never reached {step} in round {round_number}")

    def _holders(self) -> list[str]:
        return [row["agent_id"] for row in self.conn.execute("SELECT agent_id FROM Agents WHERE idol_held = 1")]

    def test_winner_gives_a_hidden_idol_in_round_one(self) -> None:
        self._advance_to("tribal_vote_call", ROUND)
        winner = self.conn.execute("SELECT winning_agent_id FROM ChallengeResults WHERE round = ?", (ROUND,)).fetchone()[0]
        holders = self._holders()
        self.assertEqual(len(holders), 1)
        self.assertNotEqual(holders[0], winner)

        public = database.row_to_dict(
            self.conn.execute("SELECT * FROM StoryEvents WHERE kind = 'challenge_gift'").fetchone()
        )
        holder_alias = self.conn.execute("SELECT pseudonym FROM Agents WHERE agent_id = ?", (holders[0],)).fetchone()[0]
        self.assertTrue(public["payload"]["idol_given"])
        self.assertNotIn(holders[0], public["target_ids"])
        self.assertNotIn(holder_alias, public["dialogue"])

        # the holder knows; a bystander only knows an idol was given
        bystander = next(agent for agent in self.players if agent not in (winner, holders[0]))
        holder_view = json.dumps(build_agent_episode_context(self.conn, actor_id=holders[0], round_number=ROUND, current_step="x"))
        bystander_ctx = build_agent_episode_context(self.conn, actor_id=bystander, round_number=ROUND, current_step="x")
        self.assertIn("hidden_idol", holder_view)
        self.assertNotIn("hidden_idol", json.dumps(bystander_ctx))
        self.assertNotIn("idol_handoff", json.dumps(bystander_ctx))
        self.assertTrue(bystander_ctx["challenge_history"][0]["idol_given_by_winner"])

    def test_no_second_idol_while_one_is_held(self) -> None:
        # stop after round 2's result, before its gift step runs
        self._advance_to("challenge_gift", ROUND + 1)
        winner = self.conn.execute("SELECT winning_agent_id FROM ChallengeResults WHERE round = ?", (ROUND + 1,)).fetchone()[0]
        holder = next(
            row["agent_id"]
            for row in self.conn.execute("SELECT agent_id FROM Agents WHERE status = 'active' ORDER BY agent_id")
            if row["agent_id"] != winner
        )
        self.conn.execute("UPDATE Agents SET idol_held = CASE WHEN agent_id = ? THEN 1 ELSE 0 END", (holder,))
        self.conn.commit()
        self._advance_to("tribal_vote_call", ROUND + 1)
        self.assertEqual(self._holders(), [holder])
        gift_rounds = [row[0] for row in self.conn.execute("SELECT round FROM StoryEvents WHERE kind = 'challenge_gift'")]
        self.assertNotIn(ROUND + 1, gift_rounds)

    def test_eliminated_holder_takes_the_idol_out_of_play(self) -> None:
        self._advance_to("challenge_intro", ROUND + 1)
        eliminated = self.conn.execute("SELECT agent_id FROM Agents WHERE status = 'eliminated'").fetchall()
        self.assertTrue(eliminated)
        for row in eliminated:
            held = self.conn.execute("SELECT idol_held FROM Agents WHERE agent_id = ?", (row[0],)).fetchone()[0]
            self.assertEqual(held, 0)

    def test_no_gift_after_the_first_two_rounds(self) -> None:
        self._advance_to("challenge_intro", ROUND + 2)
        self.conn.execute("UPDATE Agents SET idol_held = 0")  # clear so only the round limit can stop a gift
        self.conn.commit()
        self._advance_to("tribal_vote_call", ROUND + 2)
        self.assertEqual(self._holders(), [])
        gift_rounds = [row[0] for row in self.conn.execute("SELECT round FROM StoryEvents WHERE kind = 'challenge_gift'")]
        self.assertNotIn(ROUND + 2, gift_rounds)

    def test_played_idol_cancels_the_votes_against_its_holder(self) -> None:
        holder, target, *rest = self.players
        self.conn.execute("UPDATE Agents SET idol_held = 1 WHERE agent_id = ?", (holder,))
        ballots = {holder: target, target: holder, **{agent: holder for agent in rest[:4]}, **{agent: target for agent in rest[4:]}}
        for voter, voted_for in ballots.items():
            self.conn.execute(
                "INSERT INTO Votes (round, turn_index, voter_id, target_id, revealed) VALUES (?, 0, ?, ?, 1)",
                (ROUND, voter, voted_for),
            )
            tc._insert_story_event(
                self.conn,
                turn_id=1,
                round_number=ROUND,
                phase="tribal",
                kind="vote_booth",
                scene="tribal",
                shot="vote_booth",
                actor_ids=[voter],
                target_ids=[voted_for],
                visibility="private",
                title="Vote",
                dialogue="Vote",
                payload={"vote_target_id": voted_for, "play_idol": voter == holder},
            )
        event = tc._elimination_event(self.conn, ROUND, 1)
        self.assertEqual(event["target_ids"], [target])
        self.assertIn("played a hidden idol", event["dialogue"])
        self.assertEqual(self._holders(), [])

    def test_unplayed_idol_expires_at_five_players(self) -> None:
        holder, *rest = self.players
        self.conn.execute("UPDATE Agents SET idol_held = 1 WHERE agent_id = ?", (holder,))
        for agent in rest[: len(self.players) - tc.IDOL_EXPIRES_AT]:
            self.conn.execute("UPDATE Agents SET status = 'eliminated', elimination_round = 1 WHERE agent_id = ?", (agent,))
        tc._expire_idols_if_due(self.conn)
        self.assertEqual(self._holders(), [])
        memory = self.conn.execute("SELECT confessional_memory FROM Agents WHERE agent_id = ?", (holder,)).fetchone()[0]
        self.assertIn("expired", memory)


if __name__ == "__main__":
    unittest.main()
