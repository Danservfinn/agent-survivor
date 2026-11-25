"""Recording each model call's prompt and reply, never its private reasoning."""

from __future__ import annotations

import gzip
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import openrouter_client, prompt_log

PROMPT = "You are Cedar, a player in an island social-strategy game.\n\nThis step: vote_booth_agent-03\nScene: vote now"


def _payload(text: str = PROMPT) -> dict:
    return {"model": "x-ai/grok-4.7", "max_tokens": 800, "_reasoning_effort": "high",
            "messages": [{"role": "system", "content": "Return JSON."}, {"role": "user", "content": text}]}


def _response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}], "_model_ms": 1234}


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


class PromptLogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / prompt_log.LOG_NAME

    def test_one_line_per_call_without_the_private_reasoning(self) -> None:
        reply = json.dumps({"think": "my secret plan is to betray Elm", "summary": "vote Elm", "target_id": "agent-02", "dialogue": "Elm."})
        logged = prompt_log.wrap(lambda payload, key: _response(reply), self.path)
        result = logged(_payload(), "key")
        self.assertEqual(result["_model_ms"], 1234)  # the caller gets the reply unchanged
        [entry] = _lines(self.path)
        self.assertEqual(entry["model"], "x-ai/grok-4.7")
        self.assertEqual(entry["player"], "Cedar")
        self.assertEqual(entry["step"], "vote_booth_agent-03")
        self.assertEqual(entry["messages"][1]["content"], PROMPT)
        self.assertEqual(entry["reply"], {"summary": "vote Elm", "target_id": "agent-02", "dialogue": "Elm."})
        self.assertNotIn("secret plan", self.path.read_text())
        self.assertEqual((entry["finish_reason"], entry["model_ms"]), ("stop", 1234))

    def test_an_unreadable_reply_is_withheld(self) -> None:
        logged = prompt_log.wrap(lambda payload, key: _response("think: my secret plan, then {broken"), self.path)
        logged(_payload(), "key")
        [entry] = _lines(self.path)
        self.assertIsNone(entry["reply"])
        self.assertGreater(entry["reply_unreadable_chars"], 0)
        self.assertNotIn("secret plan", self.path.read_text())

    def test_a_failed_call_is_recorded_and_raised_unchanged(self) -> None:
        def outage(payload, key):
            raise openrouter_client.SeasonInterrupted("x-ai/grok-4.7", "down")

        logged = prompt_log.wrap(outage, self.path)
        with self.assertRaises(openrouter_client.SeasonInterrupted):
            logged(_payload(), "key")
        self.assertEqual(_lines(self.path)[0]["error"], "SeasonInterrupted")

    def test_keys_are_scrubbed(self) -> None:
        key = "sk-or-v1-" + "b" * 40
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": key}):
            logged = prompt_log.wrap(lambda payload, k: _response('{"summary": "' + key + '"}'), self.path)
            logged(_payload(PROMPT + " " + key), "key")
        self.assertNotIn(key, self.path.read_text())
        self.assertIn("[redacted]", self.path.read_text())

    def test_parallel_calls_write_whole_lines(self) -> None:
        logged = prompt_log.wrap(lambda payload, key: _response('{"summary": "' + "x" * 5000 + '"}'), self.path)
        threads = [threading.Thread(target=logged, args=(_payload(), "key")) for _ in range(40)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(_lines(self.path)), 40)

    def test_recording_wraps_for_the_season_then_restores_and_compresses(self) -> None:
        original = openrouter_client._post_chat_completion
        with patch.object(openrouter_client, "_post_chat_completion", lambda payload, key: _response('{"summary": "s"}')):
            stub = openrouter_client._post_chat_completion
            with prompt_log.recording(self.dir):
                self.assertIsNot(openrouter_client._post_chat_completion, stub)
                openrouter_client._post_chat_completion(_payload(), "key")
            self.assertIs(openrouter_client._post_chat_completion, stub)
        self.assertIs(openrouter_client._post_chat_completion, original)
        self.assertFalse(self.path.exists())
        with gzip.open(self.dir / prompt_log.GZ_NAME, "rt") as handle:
            self.assertEqual(json.loads(handle.readline())["reply"], {"summary": "s"})

    def test_it_records_the_prompt_the_engine_really_builds(self) -> None:
        reply = json.dumps({"think": "private", "summary": "vote Elm", "target_id": "agent-02", "play_idol": False,
                            "confidence": 0.7, "dialogue": "Elm goes tonight."})
        actor = {"agent_id": "agent-03", "pseudonym": "Cedar", "model_id": "x-ai/grok-4.7", "confessional_memory": ""}
        targets = [{"agent_id": "agent-02", "pseudonym": "Elm"}]
        env = {"OPENROUTER_API_KEY": "sk-or-v1-" + "e" * 40, "LLM_PROVIDER": "openrouter", "LLM_SURVIVOR_LIVE_LLM": "1"}
        with patch.dict(os.environ, env), patch.object(openrouter_client, "_post_chat_completion", lambda payload, key: _response(reply)):
            with prompt_log.recording(self.dir):
                action = openrouter_client.request_agent_action(
                    actor=actor, step="vote_booth_agent-03", scene_context="It is time to vote.",
                    allowed_targets=targets, response_kind="vote", episode_context={},
                )
        self.assertEqual(action.target_id, "agent-02")
        with gzip.open(self.dir / prompt_log.GZ_NAME, "rt") as handle:
            [entry] = [json.loads(line) for line in handle]
        prompt = entry["messages"][-1]["content"]
        self.assertEqual((entry["player"], entry["step"]), ("Cedar", "vote_booth_agent-03"))
        self.assertIn("You are Cedar", prompt)
        self.assertIn("exactly one of agent-02 (Elm)", prompt)  # the ballot written into the field
        self.assertNotIn("think", entry["reply"])
        self.assertNotIn("sk-or-v1-", json.dumps(entry))

    def test_key_shapes_are_found_in_plain_and_compressed_files(self) -> None:
        clean = self.dir / "clean.txt"
        clean.write_text("nothing to see")
        self.assertFalse(prompt_log.has_key_shapes(clean))
        dirty = self.dir / "dirty.jsonl.gz"
        with gzip.open(dirty, "wt") as handle:
            handle.write("x" * 3_000_000 + "sk-or-v1-" + "c" * 40)
        self.assertTrue(prompt_log.has_key_shapes(dirty))


if __name__ == "__main__":
    unittest.main()
