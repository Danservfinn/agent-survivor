"""OpenRouter reasoning settings: one budget for every model. Hidden reasoning
is turned off only for a model that ignores the budget and answers empty."""

from __future__ import annotations

import io
import json
import os
import unittest
import urllib.error
from unittest.mock import patch

from backend import openrouter_client
from backend.openrouter_client import REASONING_SETTINGS, REASONING_TOKEN_HEADROOM, _post_chat_completion


def _ok(content: str, finish: str = "stop") -> io.BytesIO:
    body = {"choices": [{"message": {"content": content}, "finish_reason": finish}]}
    return io.BytesIO(json.dumps(body).encode("utf-8"))


def _http_error(code: int, message: str) -> urllib.error.HTTPError:
    body = json.dumps({"error": {"message": message, "code": code}}).encode("utf-8")
    return urllib.error.HTTPError("https://openrouter.ai", code, message, {}, io.BytesIO(body))


class ReasoningSettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch.dict(os.environ, {"LLM_PROVIDER": "openrouter"}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sent: list[dict] = []
        openrouter_client._RUNAWAY_REASONING_MODELS.clear()
        openrouter_client._RUNAWAY_STREAKS.clear()
        self.addCleanup(openrouter_client._RUNAWAY_REASONING_MODELS.clear)
        self.addCleanup(openrouter_client._RUNAWAY_STREAKS.clear)

    def _run(self, responses: list) -> dict:
        def fake_urlopen(request, timeout=None, context=None):
            self.sent.append(json.loads(request.data.decode("utf-8")))
            item = responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        payload = {"model": "org/model", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 420}
        with patch.object(openrouter_client.urllib.request, "urlopen", side_effect=fake_urlopen), patch.object(
            openrouter_client.time, "sleep"
        ):
            return _post_chat_completion(payload, "test-key")

    def test_every_call_sends_the_same_reasoning_budget(self) -> None:
        self._run([_ok('{"ok": true}')])
        self.assertEqual(self.sent[0]["reasoning"], REASONING_SETTINGS)
        self.assertEqual(self.sent[0]["max_tokens"], 420 + REASONING_TOKEN_HEADROOM)

    def test_a_long_thinker_gets_double_room_with_reasoning_still_on(self) -> None:
        result = self._run([_ok("", finish="length"), _ok('{"ok": true}')])
        self.assertEqual(result["choices"][0]["message"]["content"], '{"ok": true}')
        retry = self.sent[1]
        self.assertEqual(retry["max_tokens"], 2 * (420 + REASONING_TOKEN_HEADROOM))
        self.assertEqual(retry["reasoning"], REASONING_SETTINGS)
        self.assertFalse(openrouter_client._RUNAWAY_REASONING_MODELS)

    def test_a_runaway_call_answers_with_hidden_reasoning_off_without_marking_the_model(self) -> None:
        empty = lambda: _ok("", finish="length")  # noqa: E731
        result = self._run([empty(), empty(), _ok('{"ok": true}')])
        self.assertEqual(result["choices"][0]["message"]["content"], '{"ok": true}')
        self.assertEqual(self.sent[1]["max_tokens"], 2 * (420 + REASONING_TOKEN_HEADROOM))
        self.assertEqual(self.sent[2]["reasoning"], {"enabled": False})
        # one runaway call does not cost the model its reasoning: the next call has it
        self._run([_ok('{"ok": 2}')])
        self.assertEqual(self.sent[3]["reasoning"], REASONING_SETTINGS)
        self.assertFalse(openrouter_client._RUNAWAY_REASONING_MODELS)

    def test_three_runaway_calls_in_a_row_mark_the_model(self) -> None:
        empty = lambda: _ok("", finish="length")  # noqa: E731
        for _ in range(3):
            self._run([empty(), empty(), _ok('{"ok": true}')])
        self.assertIn("org/model", openrouter_client._RUNAWAY_REASONING_MODELS)
        sent = len(self.sent)
        self._run([_ok('{"ok": 2}')])
        self.assertEqual(self.sent[sent]["reasoning"], {"enabled": False})
        self.assertEqual(len(self.sent), sent + 1)

    def test_an_answer_with_reasoning_resets_the_streak(self) -> None:
        empty = lambda: _ok("", finish="length")  # noqa: E731
        self._run([empty(), empty(), _ok("{}")])
        self._run([empty(), empty(), _ok("{}")])
        self._run([_ok("{}")])  # answered with reasoning on
        self._run([empty(), empty(), _ok("{}")])
        self.assertFalse(openrouter_client._RUNAWAY_REASONING_MODELS)

    def test_mandatory_reasoning_keeps_the_empty_reply_and_is_not_remembered(self) -> None:
        result = self._run(
            [
                _ok("", finish="length"),
                _ok("", finish="length"),
                _http_error(400, "Reasoning is mandatory for this endpoint and cannot be disabled."),
            ]
        )
        self.assertEqual(result["choices"][0]["message"]["content"], "")
        self.assertEqual(len(self.sent), 3)
        self.assertFalse(openrouter_client._RUNAWAY_REASONING_MODELS)

    def test_a_reply_cut_off_at_the_limit_is_retried_with_more_room(self) -> None:
        result = self._run([_ok('{"think": "half a', finish="length"), _ok('{"ok": true}')])
        self.assertEqual(result["choices"][0]["message"]["content"], '{"ok": true}')
        self.assertEqual(self.sent[1]["max_tokens"], 2 * (420 + REASONING_TOKEN_HEADROOM))

    def test_a_budget_too_big_for_the_context_window_is_clamped(self) -> None:
        message = (
            "Provider returned error: 'max_tokens' or 'max_completion_tokens' is too large: 2020. This model's "
            "maximum context length is 16384 tokens and your request has 15000 input tokens (2020 > 16384 - 15000)."
        )
        result = self._run([_http_error(400, message), _ok('{"ok": true}')])
        self.assertEqual(result["choices"][0]["message"]["content"], '{"ok": true}')
        self.assertEqual(self.sent[1]["max_tokens"], 16384 - 15000 - 32)

    def test_the_reply_budget_never_exceeds_the_models_output_limit(self) -> None:
        from backend import live_cost

        live_cost._ACTIVE_TABLE["org/model"] = {"prompt_per_m": 1, "completion_per_m": 1, "max_out": 4000}
        self.addCleanup(live_cost._ACTIVE_TABLE.pop, "org/model", None)
        self._run([_ok("", finish="length"), _ok('{"ok": true}')])
        self.assertEqual(self.sent[0]["max_tokens"], 4000)
        # already at the limit: no bigger retry is possible, so it goes to hidden reasoning off
        self.assertEqual(self.sent[1]["reasoning"], {"enabled": False})

    def test_answer_time_counts_answered_calls_only(self) -> None:
        result = self._run([_http_error(503, "upstream error"), _ok('{"ok": true}')])
        self.assertIn("_model_ms", result)
        self.assertGreaterEqual(result["_model_ms"], 0)
        self.assertEqual(len(self.sent), 2)

    def test_400_naming_reasoning_retries_once_without_the_parameter(self) -> None:
        result = self._run(
            [
                _http_error(400, "Reasoning is mandatory for this endpoint and cannot be disabled."),
                _ok('{"ok": true}'),
            ]
        )
        self.assertEqual(result["choices"][0]["message"]["content"], '{"ok": true}')
        self.assertIn("reasoning", self.sent[0])
        self.assertNotIn("reasoning", self.sent[1])

    def test_other_400s_still_fail(self) -> None:
        with self.assertRaises(RuntimeError):
            self._run([_http_error(400, "Invalid model id")])
        self.assertEqual(len(self.sent), 1)


if __name__ == "__main__":
    unittest.main()
