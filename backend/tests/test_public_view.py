from __future__ import annotations

import unittest

from backend.public_view import public_payload


class PublicViewTest(unittest.TestCase):
    def test_strips_inner_thought_and_prompts(self) -> None:
        payload = {
            "events": [
                {
                    "dialogue": "I vote for Grok.",
                    "inner_thought": "private motive",
                    "payload": {"raw_prompt": "SYSTEM: do not leak", "vote_target_name": "Grok 4.6"},
                }
            ],
            "messages": [{"content": "public line", "inner_thought": "secret"}],
        }
        public = public_payload(payload)
        self.assertEqual(public["events"][0]["dialogue"], "I vote for Grok.")
        self.assertNotIn("inner_thought", public["events"][0])
        self.assertNotIn("raw_prompt", public["events"][0]["payload"])
        self.assertEqual(public["events"][0]["payload"]["vote_target_name"], "Grok 4.6")
        self.assertNotIn("inner_thought", public["messages"][0])


if __name__ == "__main__":
    unittest.main()
