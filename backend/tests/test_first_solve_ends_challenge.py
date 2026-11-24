"""First-correct-solve ends the live challenge immediately."""

import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend import turn_controller as tc  # noqa: E402
from backend.database import seed_demo, get_db_connection  # noqa: E402
from backend.openrouter_client import ChallengeSolution  # noqa: E402


class FirstSolveEndsChallengeTest(unittest.TestCase):
    def test_live_challenge_stops_at_first_correct(self) -> None:
        with patch.object(tc, "live_llm_provider", return_value="openrouter"):
            import tempfile
            self._td = tempfile.TemporaryDirectory()
            self._db = str(Path(self._td.name) / "t.db")
            seed_demo(reset=True, path=self._db)
            conn = get_db_connection(self._db)
            active = tc._active_agent_rows(conn)
            self.assertGreaterEqual(len(active), 3)

            puzzle = {"puzzle_id": "t-1", "answer": {"value": "42"}, "prompt": "p", "examples": "[]", "difficulty": "easy"}
            ctx_patch = patch.object(
                tc, "build_agent_episode_context", return_value={}
            )

            answers = iter([{"value": "42"}, {"value": "wrong"}])
            delays = iter([0.05, 5.0])  # solver fast, straggler very slow

            def fake_solution(actor, puzzle, episode_context):
                next_answer = next(answers, {"value": "late"})
                time.sleep(next(delays, 0))
                return ChallengeSolution(
                    model_id="m", answer=next_answer, explanation=""
                )

            sol_patch = patch.object(tc, "request_challenge_solution", side_effect=fake_solution)

            with ctx_patch, sol_patch:
                start = time.perf_counter()
                attempts = tc._live_challenge_attempts(conn, 7, puzzle, "individual")
                elapsed = time.perf_counter() - start

            correct = [a for a in attempts if a.is_correct]
            self.assertEqual(len(correct), 1)
            self.assertEqual(correct[0].answer, {"value": "42"})
            # straggler's 5s sleep must NOT be awaited: first solve ends it
            self.assertLess(elapsed, 2.0, f"challenge waited for straggler: {elapsed:.2f}s")
            conn.close()

    def test_all_wrong_waits_for_every_attempt(self) -> None:
        with patch.object(tc, "live_llm_provider", return_value="openrouter"):
            import tempfile
            td = tempfile.TemporaryDirectory()
            dbp = str(Path(td.name) / "t2.db")
            seed_demo(reset=True, path=dbp)
            conn = get_db_connection(dbp)
            puzzle = {"puzzle_id": "t-2", "answer": {"value": "42"}, "prompt": "p", "examples": "[]", "difficulty": "easy"}
            ctx_patch = patch.object(tc, "build_agent_episode_context", return_value={})

            def fake_solution(actor, puzzle, episode_context):
                time.sleep(0.02)
                return ChallengeSolution(model_id="m", answer={"value": "no"}, explanation="")

            sol_patch = patch.object(tc, "request_challenge_solution", side_effect=fake_solution)
            with ctx_patch, sol_patch:
                attempts = tc._live_challenge_attempts(conn, 7, puzzle, "individual")

            self.assertGreaterEqual(len(attempts), 3)
            self.assertTrue(all(not a.is_correct for a in attempts))
            conn.close()


if __name__ == "__main__":
    unittest.main()
