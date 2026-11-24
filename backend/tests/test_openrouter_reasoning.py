"""OpenRouter reasoning settings: one budget for every model, never disabled."""

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

    def test_empty_reply_retries_with_more_room_and_reasoning_still_on(self) -> None:
        result = self._run([_ok("", finish="length"), _ok('{"ok": true}')])
        self.assertEqual(result["choices"][0]["message"]["content"], '{"ok": true}')
        retry = self.sent[1]
        self.assertEqual(retry["max_tokens"], 2 * (420 + REASONING_TOKEN_HEADROOM))
        self.assertEqual(retry["reasoning"], REASONING_SETTINGS)
        for sent in self.sent:
            self.assertNotEqual(sent.get("reasoning"), {"enabled": False})

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
