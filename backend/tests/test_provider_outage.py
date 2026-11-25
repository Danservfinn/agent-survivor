"""A provider failure pauses the game; it is never charged to the player."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from backend import database, openrouter_client
from backend import turn_controller as tc
from backend.openrouter_client import PROVIDER_HOLD_SECONDS, SeasonInterrupted, _post_chat_completion


def _ok(content: str = '{"ok": true}') -> io.BytesIO:
    return io.BytesIO(json.dumps({"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}).encode())


def _http(code: int, message: str = "upstream error", retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = {"Retry-After": retry_after} if retry_after else {}
    body = json.dumps({"error": {"message": message, "code": code}}).encode()
    return urllib.error.HTTPError("https://openrouter.ai", code, message, headers, io.BytesIO(body))


class HoldTest(unittest.TestCase):
    def setUp(self) -> None:
        env = patch.dict(os.environ, {"LLM_PROVIDER": "openrouter"}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        self.sleeps: list[float] = []

    def _run(self, responses: list) -> dict:
        def fake_urlopen(request, timeout=None, context=None):
            item = responses.pop(0) if responses else _http(503)
            if isinstance(item, Exception):
                raise item
            return item

        payload = {"model": "org/model", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 100}
        with patch.object(openrouter_client.urllib.request, "urlopen", side_effect=fake_urlopen), patch.object(
            openrouter_client.time, "sleep", side_effect=self.sleeps.append
        ):
            return _post_chat_completion(payload, "test-key")

    def test_brief_outage_is_absorbed_by_quick_retries(self) -> None:
        result = self._run([_http(503), _http(503), _ok()])
        self.assertEqual(result["choices"][0]["message"]["content"], '{"ok": true}')

    def test_longer_outage_holds_the_turn_then_succeeds(self) -> None:
        # four quick attempts fail, the hold waits, then the provider is back
        result = self._run([_http(503)] * 4 + [_ok()])
        self.assertEqual(result["choices"][0]["message"]["content"], '{"ok": true}')
        self.assertIn(5.0, self.sleeps)

    def test_retry_after_is_honoured(self) -> None:
        self._run([_http(429, retry_after="90")] * 4 + [_ok()])
        self.assertIn(90.0, self.sleeps)

    def test_outage_past_the_window_interrupts_the_season(self) -> None:
        with self.assertRaises(SeasonInterrupted) as caught:
            self._run([])  # every attempt fails with 503
        self.assertEqual(caught.exception.model_id, "org/model")
        hold_waits = [delay for delay in self.sleeps if delay not in (3, 6, 9)]  # 3/6/9 are the quick retries
        self.assertLessEqual(sum(hold_waits), PROVIDER_HOLD_SECONDS)

    def test_empty_reply_is_the_models_answer_not_an_outage(self) -> None:
        empty = lambda: io.BytesIO(  # noqa: E731
            json.dumps({"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}).encode()
        )
        result = self._run([empty(), empty(), empty()])  # normal, double room, hidden reasoning off
        self.assertEqual(result["choices"][0]["message"]["content"], "")
        self.assertEqual(self.sleeps, [])  # no hold

    def test_bad_key_interrupts_at_once(self) -> None:
        with self.assertRaises(SeasonInterrupted):
            self._run([_http(401, "Missing Authentication header")])
        self.assertEqual(self.sleeps, [])

    def test_bad_request_is_still_an_ordinary_error(self) -> None:
        with self.assertRaises(RuntimeError):
            self._run([_http(400, "Invalid request")])


class SeasonStopsCleanlyTest(unittest.TestCase):
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

    def test_interrupted_vote_is_rolled_back_and_nobody_is_charged(self) -> None:
        # run deterministically up to the first vote booth
        for _ in range(60):
            step = tc.get_state()["game"]["phase_step"]
            if step.startswith("vote_booth_"):
                break
            tc.advance_turn()
        before = tc.get_state(include_hidden_votes=True)

        def outage(**kwargs):
            raise SeasonInterrupted(kwargs["actor"]["model_id"], "HTTP 503 for 10 minutes")

        with patch.object(tc, "should_use_live_llm", return_value=True), patch.object(
            tc, "live_llm_provider", return_value="openrouter"
        ), patch.object(tc, "get_pricing_table", return_value=None), patch.object(
            tc, "build_agent_episode_context", return_value={}
        ), patch.object(tc, "request_agent_action", side_effect=outage), patch.object(
            tc, "_assert_live_roster_compatible", return_value=None
        ):
            result = tc.auto_run_to_end(max_rounds=5, max_turns=50, max_live_calls=1000, max_estimated_cost_cents=1e9)

        self.assertEqual(result["interrupted"]["step"], step)
        self.assertIn("503", result["interrupted"]["reason"])
        after = tc.get_state(include_hidden_votes=True)
        self.assertEqual(after["game"]["phase_step"], step)  # resumes from the same step
        self.assertEqual(after["game"]["turn_index"], before["game"]["turn_index"])
        conn = database.get_db_connection()
        try:
            votes = conn.execute("SELECT COUNT(*) FROM Votes").fetchone()[0]
            eliminated = conn.execute("SELECT COUNT(*) FROM Agents WHERE status = 'eliminated'").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(votes, 0)
        self.assertEqual(eliminated, 0)


if __name__ == "__main__":
    unittest.main()
