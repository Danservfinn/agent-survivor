"""Receipts (forwarding a heard private line with the game's stamp) and
spying (spending the last whisper to listen in on a talk)."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from backend import advantages
from backend import turn_controller as tc
from backend.llm_context import build_agent_episode_context
from backend.openrouter_client import AgentAction
from backend.tests.test_model_failures import _Base


def _action(**fields) -> AgentAction:
    return AgentAction(**({"dialogue": "line", "inner_thought": "", "model_id": "m"} | fields))


class _Talks(_Base):
    def _talk(self, members: list[str], lines: list[tuple[str, str]], round_number: int = 1) -> int:
        event = tc._insert_story_event(
            self.conn, turn_id=1, round_number=round_number, phase="camp", kind="conversation", scene="camp",
            shot="group_huddle", actor_ids=members, target_ids=[], visibility="audience", title="Talk", dialogue="x",
            payload={
                "privacy": "participants_only",
                "participant_ids": members,
                "speaker_lines": [{"agent_id": a, "text": t} for a, t in lines],
            },
        )
        return event["id"]


class ReceiptTest(_Talks):
    def test_a_player_can_forward_only_what_others_said_to_it(self) -> None:
        a, b, c, *_ = self.players
        talk = self._talk([a, b], [(a, "Vote Cedar tonight."), (b, "Deal.")])
        receipts = advantages.receipts_for(self.conn, b)
        self.assertEqual(list(receipts), [f"m{talk}-0"])  # a's line, not b's own
        self.assertEqual(receipts[f"m{talk}-0"]["said"], "Vote Cedar tonight.")
        self.assertEqual(advantages.receipts_for(self.conn, c), {})  # c was not there

    def test_the_receipt_id_reaches_the_listener_context(self) -> None:
        a, b, *_ = self.players
        talk = self._talk([a, b], [(a, "Vote Cedar tonight.")])
        claims = build_agent_episode_context(self.conn, actor_id=b, round_number=1, current_step="x")["what_others_told_you"]
        self.assertEqual(claims[0]["receipt_id"], f"m{talk}-0")

    def test_a_real_receipt_is_stamped_and_a_made_up_one_is_not(self) -> None:
        a, b, *_ = self.players
        talk = self._talk([a, b], [(a, "Vote Cedar tonight.")])
        receipts = advantages.receipts_for(self.conn, b)
        real = advantages.line_stamps(_action(receipt_id=f"m{talk}-0"), receipts, False, "B")
        self.assertEqual(real[0]["forwarded"]["said"], "Vote Cedar tonight.")
        self.assertEqual(advantages.line_stamps(_action(receipt_id=f"m{talk}-9"), receipts, False, "B"), [])
        self.assertEqual(advantages.line_stamps(_action(receipt_id="m999-0"), receipts, False, "B"), [])
        self.assertIn("stamped real by the game", advantages.with_stamps("Look.", real))

    def test_a_forwarded_line_is_stamped_in_the_next_talk(self) -> None:
        a, b, c, *_ = self.players
        talk = self._talk([a, b], [(a, "Vote Cedar tonight.")])
        names = advantages._names(self.conn)
        scenes = []

        def reply(**kwargs):
            scenes.append(kwargs["scene_context"])
            if kwargs["actor"]["agent_id"] == b:
                return _action(dialogue=f"{names[a]} is playing you.", receipt_id=f"m{talk}-0")
            return _action(dialogue="Noted.")

        self._live(reply)
        lines, _ = tc._group_speaker_lines(self.conn, 1, "camp_talk_1", [b, c], [], "post_challenge", talk_asks={})
        self.assertEqual(lines[0]["stamped"][0]["forwarded"]["from"], names[a])
        self.assertNotIn("stamped", lines[1])
        self.assertIn("stamped real by the game", scenes[1])  # c hears the stamp

    @patch.dict(os.environ, {"SURVIVOR_ADVANTAGES": "0"})
    def test_off_means_no_receipt_and_no_stamp(self) -> None:
        a, b, *_ = self.players
        talk = self._talk([a, b], [(a, "Vote Cedar tonight.")])
        claims = build_agent_episode_context(self.conn, actor_id=b, round_number=1, current_step="x")["what_others_told_you"]
        self.assertNotIn("receipt_id", claims[0])
        receipts = advantages.receipts_for(self.conn, b)
        self.assertEqual(advantages.line_stamps(_action(receipt_id=f"m{talk}-0"), receipts, False, "B"), [])


class SpyTest(_Talks):
    def _spy(self, spy: str, target: str, seed: int) -> list[dict]:
        with patch.object(tc, "_season_seed", return_value=seed):
            return tc._spy_events(self.conn, 1, 1, spy, target)

    def _seed(self, spy: str, caught: bool) -> int:
        return next(s for s in range(200) if advantages.spy_caught(s, 1, spy) == caught)

    def test_the_spy_hears_the_whole_talk_privately(self) -> None:
        a, b, spy, other, *_ = self.players
        self._talk([a, b], [(a, "Vote Cedar tonight."), (b, "Deal.")])
        events = self._spy(spy, a, self._seed(spy, caught=False))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["participant_ids"], [spy])
        self.assertIn("Vote Cedar tonight.", events[0]["payload"]["group_summary"])
        self.assertNotIn("speaker_lines", events[0]["payload"])  # not a claim made to the spy
        row = self.conn.execute("SELECT spy_id, target_id, caught FROM Spying").fetchone()
        self.assertEqual((row["spy_id"], row["target_id"], row["caught"]), (spy, a, 0))
        # nobody else learns of it
        for player in (a, b, other):
            memory = build_agent_episode_context(self.conn, actor_id=player, round_number=1, current_step="x")
            self.assertNotIn("Listens In", json.dumps(memory))

    def test_a_caught_spy_is_named_to_the_talks_members_only(self) -> None:
        a, b, spy, other, *_ = self.players
        self._talk([a, b], [(a, "Vote Cedar tonight.")])
        events = self._spy(spy, a, self._seed(spy, caught=True))
        self.assertEqual(len(events), 2)
        self.assertEqual(sorted(events[1]["payload"]["participant_ids"]), sorted([a, b]))
        seen = json.dumps(build_agent_episode_context(self.conn, actor_id=b, round_number=1, current_step="x"))
        self.assertIn("was seen listening in", seen)
        unseen = json.dumps(build_agent_episode_context(self.conn, actor_id=other, round_number=1, current_step="x"))
        self.assertNotIn("was seen listening in", unseen)

    def test_the_catch_rate_is_about_one_in_three(self) -> None:
        caught = sum(advantages.spy_caught(seed, 1, "agent-01") for seed in range(3000))
        self.assertAlmostEqual(caught / 3000, 1 / 3, delta=0.03)

    def test_no_talk_with_that_player_means_nothing_heard(self) -> None:
        a, b, spy, *_ = self.players
        self._talk([a, spy], [(a, "hi")])  # the spy's own talk does not count
        [event] = self._spy(spy, a, 0)
        self.assertIn("held no private talk", event["payload"]["group_summary"])

    def test_the_whisper_step_routes_spy_on(self) -> None:
        a, b, spy, *_ = self.players
        self._talk([a, b], [(a, "Vote Cedar tonight.")])
        names = advantages._names(self.conn)
        self._live(lambda **kwargs: _action(spy_on=names[a], target_id=b) if kwargs["actor"]["agent_id"] == spy else _action(target_id=None))
        with patch.object(tc, "_season_seed", return_value=self._seed(spy, caught=False)):
            events = tc._followup_events(self.conn, 1, 1)
        self.assertEqual([e["kind"] for e in events], ["spying"])  # no whisper sent too


if __name__ == "__main__":
    unittest.main()
