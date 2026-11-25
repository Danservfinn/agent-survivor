"""Running model calls in parallel must not change the game.

A whole live season is played twice, once one call at a time
(SURVIVOR_PARALLEL_CALLS=0) and once in parallel. Every stubbed model reply is
a pure function of the call's exact inputs, so if any call saw different inputs
(a different context, scene or target list) its reply, and everything after
it, would differ. The test checks that every call received identical inputs
and that every table ends up identical, row by row.
"""

from __future__ import annotations

import hashlib
import re
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from backend import database
from backend import turn_controller as tc
from backend.openrouter_client import AgentAction, ChallengeSolution, HostEventText, HostNarration, InvalidModelReply

# wall-clock columns differ between any two runs, parallel or not
VOLATILE_COLUMNS = {"created_at", "updated_at", "timestamp", "started_at", "response_ms"}
# measured call times also appear inside event payloads; never scored (speed does not count)
RESPONSE_MS = re.compile(r'"response_ms":\d+')


def _key(name: str, kwargs: dict) -> str:
    return name + ":" + json.dumps(kwargs, sort_keys=True, default=str)


def _digest(text: str) -> int:
    return int(hashlib.sha256(text.encode()).hexdigest()[:12], 16)


class _Stub:
    """Deterministic model: each reply depends only on the call's inputs."""

    def __init__(self, delay: float = 0.0) -> None:
        self.calls: list[str] = []
        self.lock = threading.Lock()
        self.delay = delay

    def _record(self, name: str, kwargs: dict) -> int:
        key = _key(name, kwargs)
        with self.lock:
            self.calls.append(key)
        if self.delay:
            time.sleep(self.delay)
        return _digest(key)

    def agent(self, **kwargs) -> AgentAction:
        h = self._record("agent", kwargs)
        kind = kwargs["response_kind"]
        # some replies are unusable: silent talk lines and spoiled votes must
        # come out the same sequential or parallel
        if kind == "group_conversation" and h % 17 == 0:
            raise InvalidModelReply("stub: unusable reply")
        # one seat fails every retry of every ballot, so the season always has
        # spoiled votes to compare (a hash of the call varies across retries)
        if kind == "vote" and kwargs["actor"]["agent_id"] == "agent-05":
            raise InvalidModelReply("stub: unusable vote")
        targets = kwargs.get("allowed_targets") or []
        target = targets[h % len(targets)]["agent_id"] if targets else None
        claims = (kwargs.get("episode_context") or {}).get("what_others_told_you") or []
        promise = targets[(h >> 3) % len(targets)]["agent_id"] if kind == "group_conversation" and targets and h % 3 == 0 else None
        return AgentAction(
            dialogue=f"line {h % 100000}",
            inner_thought=f"thought {h % 977}",
            model_id=kwargs["actor"]["model_id"],
            target_id=target if (kind != "followup" or h % 2) and (kind != "immunity_choice" or h % 3 == 0) else None,
            strategic_summary=f"ask {h % 613}",
            confidence=0.5,
            play_idol=bool(h % 5 == 0),
            promised_vote_target=promise,
            move_type="vote_commitment" if promise else None,
            # talk invitations: mostly one player, sometimes two or three, so group
            # talks are compared too (talks sharing a player cannot run at once)
            invite_ids=tuple(
                dict.fromkeys(t["agent_id"] for t in targets[h % 3 : h % 3 + 1 + (((h >> 5) % 3) if h % 5 == 0 else 0)])
            )
            if kind == "talk_choice"
            else (),
            end_pact=kind == "group_conversation" and h % 23 == 0,
            pact=kind == "group_conversation" and h % 4 == 0,
            predicted_eliminated=target,
            predicted_votes={t["agent_id"]: target for t in targets[:3]} if kind == "vote" and target else None,
            prediction_confidence=0.6,
            # the inner life: lies with a vote goal, and the voter's read of what
            # others expect of it; both are written and read back, so compared too
            truth=("true", "half", "lie")[h % 3] if kind in ("group_conversation", "followup", "dialogue") else None,
            lie_purpose=f"purpose {h % 37}" if h % 3 else None,
            lie_goal_vote=target if h % 3 else None,
            expected_of_me={t["agent_id"]: target for t in targets[1:4]} if kind == "vote" and target else None,
            # forward a heard line now and then (stamped only if really heard),
            # and sometimes spend the whisper listening in on a talk
            receipt_id=claims[h % len(claims)].get("receipt_id") if claims and h % 4 == 1 else None,
            spy_on=target if kind == "followup" and h % 7 == 0 else None,
            search=kind == "talk_choice" and h % 6 == 0,
            show_idol=h % 4 == 2,
            show_ballot=kind == "vote" and h % 5 == 1,
            use_extra_vote=kind == "vote" and h % 2 == 0,
        )

    def challenge(self, **kwargs) -> ChallengeSolution:
        h = self._record("challenge", kwargs)
        return ChallengeSolution(
            answers={p["puzzle_id"]: (p["answer"] if (h >> i) % 2 else "x") for i, p in enumerate(kwargs["puzzles"])},
            explanation="e",
            model_id=kwargs["actor"]["model_id"],
            solve_ms=1000 + h % 9000,  # equal scores go to the fastest answer
        )

    def private(self, **kwargs) -> dict:
        h = self._record("private", kwargs)
        return {
            "threats": f"t{h % 97}",
            "allies": "a",
            "talk_plan": "p",
            "expected_out": None,
            "confidence": 0.5,
            "players": {"x": f"n{h % 89}"},
            "plan": "q",
            "notebook": f"nb{h % 83}",
            "confessional": f"conf {h % 71}",
            # feelings are stored and read back next round: compared too
            "feelings": {
                f"agent-0{1 + (h >> k) % 9}": {"feeling": ("trust", "resentment", "fear")[(h >> k) % 3], "strength": "strong", "because": "x"}
                for k in (0, 4)
            },
            "mood": ("confident", "cornered")[h % 2],
            # the auction: sealed bids (some over budget, so void)
            "bids": {"extra_vote": h % 7, "idol": (h >> 3) % 6, "vote_peek": (h >> 6) % 5},
        }

    def host_text(self, **kwargs) -> HostEventText:
        h = self._record("host_text", kwargs)
        return HostEventText(f"host {h % 991}", "sub", "narr", "stub/host")

    def host_questions(self, **kwargs) -> list[dict]:
        h = self._record("host_questions", kwargs)
        players = kwargs["players"]
        # three questions, one to a player not at council, so filtering is compared too
        return [{"to": players[(h >> k) % len(players)], "question": f"q{(h >> k) % 97}"} for k in (0, 5, 10)] + [
            {"to": "Nobody", "question": "x"}
        ]

    def host_narration(self, **kwargs) -> HostNarration:
        h = self._record("host_narration", kwargs)
        return HostNarration(f"narr {h % 991}", "stub/host")


def _play_season(parallel: bool, delay: float = 0.0) -> tuple[list[str], dict[str, list[tuple]], float]:
    stub = _Stub(delay)
    with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
        os.environ,
        {
            "OPENROUTER_API_KEY": "stub",
            "LLM_PROVIDER": "openrouter",
            "LLM_SURVIVOR_FAST_BENCHMARK": "1",
            "SURVIVOR_SEASON_SEED": "4242",
            "SURVIVOR_PARALLEL_CALLS": "1" if parallel else "0",
        },
    ), patch.object(tc, "request_agent_action", side_effect=stub.agent), patch.object(
        tc, "request_challenge_solution", side_effect=stub.challenge
    ), patch.object(tc, "request_private_writing", side_effect=stub.private), patch.object(
        tc, "request_host_event_text", side_effect=stub.host_text
    ), patch.object(tc, "request_host_narration", side_effect=stub.host_narration), patch.object(
        tc, "request_host_questions", side_effect=stub.host_questions
    ), patch.object(
        tc, "get_pricing_table", return_value=None
    ), patch.object(tc, "_assert_live_roster_compatible", return_value=None), patch(
        "builtins.print"
    ):
        original = database.DATABASE_PATH
        database.DATABASE_PATH = Path(temp_dir) / "survivor.db"
        try:
            database.seed_demo(reset=True, roster_preset="cheap_recent_openrouter")
            started = time.perf_counter()
            result = tc.auto_run_to_end(max_rounds=20, max_turns=2500, max_live_calls=10000, max_estimated_cost_cents=1e9)
            elapsed = time.perf_counter() - started
            assert result["state"]["game"]["winner"], "season did not finish"
            conn = sqlite3.connect(database.DATABASE_PATH)
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            dump = {}
            for table in tables:
                columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})") if row[1] not in VOLATILE_COLUMNS]
                dump[table] = [
                    tuple(RESPONSE_MS.sub('"response_ms":0', value) if isinstance(value, str) else value for value in row)
                    for row in conn.execute(f"SELECT {', '.join(columns)} FROM {table} ORDER BY rowid")
                ]
            conn.close()
        finally:
            database.DATABASE_PATH = original
    return stub.calls, dump, elapsed


class ParallelEquivalenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sequential = _play_season(parallel=False)
        cls.parallel = _play_season(parallel=True)

    def test_every_model_call_gets_identical_inputs(self) -> None:
        sequential_calls, parallel_calls = self.sequential[0], self.parallel[0]
        self.assertGreater(len(sequential_calls), 200)
        self.assertEqual(Counter(sequential_calls), Counter(parallel_calls))

    def test_every_table_ends_identical(self) -> None:
        sequential_dump, parallel_dump = self.sequential[1], self.parallel[1]
        self.assertEqual(sorted(sequential_dump), sorted(parallel_dump))
        for table in sequential_dump:
            self.assertEqual(sequential_dump[table], parallel_dump[table], f"table {table} differs")
        # the season exercised what parallelism touches
        self.assertTrue(sequential_dump["Promises"])
        self.assertTrue(sequential_dump["PrivatePlans"])
        self.assertTrue(sequential_dump["GroupDiscussions"])
        self.assertTrue(sequential_dump["SpoiledVotes"])
        self.assertTrue(sequential_dump["Feelings"])
        self.assertTrue(sequential_dump["Confessionals"])
        self.assertTrue(sequential_dump["RoundThoughts"])
        self.assertTrue(sequential_dump["Lies"])
        self.assertTrue(sequential_dump["Spying"])
        self.assertTrue(sequential_dump["IdolSearches"])
        self.assertTrue(sequential_dump["AuctionBids"])
        self.assertTrue(sequential_dump["Advantages"])
        self.assertTrue(any('"stamped"' in str(cell) for row in sequential_dump["StoryEvents"] for cell in row))
        # group talks of three or more happened (column 5 is target_size, the talk's size)
        self.assertTrue(any(row[5] > 2 for row in sequential_dump["GroupDiscussions"]))
        self.assertIn("(says nothing)", json.dumps(sequential_dump["StoryEvents"]))
        self.assertTrue(any(row[5] == "host_questioning" for row in sequential_dump["StoryEvents"]))


class ParallelSpeedTest(unittest.TestCase):
    def test_parallel_season_is_faster_and_still_identical_with_slow_calls(self) -> None:
        sequential = _play_season(parallel=False, delay=0.02)
        parallel = _play_season(parallel=True, delay=0.02)
        # votes, follow-ups and the public speaking steps (tribal council, tie
        # talks, parting words, jury deliberation) run one speaker at a time
        # because each hears the last; the rest must still save real time
        self.assertLess(parallel[2], sequential[2] * 0.9)
        # slower calls cross clock seconds differently; nothing may depend on that
        self.assertEqual(Counter(sequential[0]), Counter(parallel[0]))
        self.assertEqual(sequential[1], parallel[1])


if __name__ == "__main__":
    unittest.main()
