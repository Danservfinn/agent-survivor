"""Councils as the report reads them: revotes, deadlocks, rocks, spoiled and
extra ballots, handed-over immunity, and promises counted once."""

from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path

from backend import council_record, database, prompt_log, report_facts, season_report
from backend import turn_controller as tc
from backend.tests.test_model_failures import ROUND
from backend.tests.test_tie_rules import TieRulesTest


class CouncilFromTheEngineTest(TieRulesTest):
    """Drive the engine's own tie rules, then read the council back."""

    def _council(self) -> tuple[dict, dict]:
        self.conn.commit()
        data = season_report._load(database.DATABASE_PATH)
        return council_record.councils(data)[ROUND], data

    def test_unanimous_deadlock(self) -> None:
        a, b, *_ = self.players
        self._deadlock(lambda actor: b)
        rec, _data = self._council()
        self.assertEqual(sum(rec["first_tally"].values()), 7)  # the seven untied players
        self.assertEqual(set(rec["first_tally"]), {a, b})
        self.assertEqual(sum(rec["revote_tally"].values()), 6)  # the seventh revoter spoiled
        self.assertEqual(rec["decided_by"], "unanimous")
        self.assertEqual(rec["eliminated_id"], b)
        self.assertEqual(rec["spoiled"][self.players[-1]]["ballot"], "revote")
        self.assertTrue(council_record.tally_text(rec).endswith("revote 3-3, deadlock, unanimous"))

    def test_rocks(self) -> None:
        a, b, c, *_ = self.players
        self.conn.execute("UPDATE Agents SET has_immunity = 1 WHERE agent_id = ?", (self.players[-1],))
        self._deadlock(lambda actor: None if actor == c else a)
        rec, _data = self._council()
        self.assertEqual(rec["decided_by"], "rocks")
        self.assertNotIn(rec["eliminated_id"], (a, b, self.players[-1]))
        self.assertTrue(council_record.tally_text(rec).endswith(", rocks"))

    def test_report_rounds_show_the_revote_and_the_spoiled_ballot(self) -> None:
        a, b, *_ = self.players
        self._deadlock(lambda actor: b)
        self.conn.commit()
        data = season_report._load(database.DATABASE_PATH)
        names = {p: p for p in self.players}
        rnd = next(r for r in season_report._build_rounds(data, names) if r["round"] == ROUND)
        self.assertEqual(rnd["decided_by"], "unanimous")
        self.assertEqual(rnd["eliminated_id"], b)
        revotes = [v for v in rnd["votes"] if v["revote"]]
        self.assertEqual(len(revotes), 7)
        self.assertEqual(sum(1 for v in revotes if v["spoiled"]), 1)
        # each voter's deciding ballot is its revote
        for v in revotes:
            if not v["spoiled"]:
                self.assertEqual(rnd["final_votes"][v["voter_id"]], v["target_id"])


def _vote(rn: int, voter: str, target: str, **extra) -> dict:
    return {"round": rn, "voter_id": voter, "target_id": target, "is_revote": 0, "is_extra": 0, "predicted_votes": "{}", **extra}


def _event(rn: int, kind: str, payload: dict, actors: list[str] | None = None, targets: list[str] | None = None, dialogue: str = "") -> dict:
    return {"round": rn, "kind": kind, "payload": json.dumps(payload), "actor_ids": json.dumps(actors or ["host"]),
            "target_ids": json.dumps(targets or []), "dialogue": dialogue, "inner_thought": ""}


class CouncilByHandTest(unittest.TestCase):
    def test_extra_vote_counts_and_the_final_tally_decides(self) -> None:
        data = {
            "votes": [_vote(1, "a", "c"), _vote(1, "b", "c"), _vote(1, "c", "a"), _vote(1, "a", "c", is_extra=1)],
            "events": [_event(1, "elimination", {"eliminated_id": "c", "final_tally": {"c": 3, "a": 1}})],
        }
        rec = council_record.councils(data)[1]
        self.assertEqual(rec["first"], {"a": "c", "b": "c", "c": "a"})
        self.assertEqual(rec["extra"], [("a", "c")])
        self.assertEqual(rec["first_tally"], {"c": 3, "a": 1})
        self.assertEqual(rec["decided_by"], "vote")

    def test_immunity_handed_over(self) -> None:
        data = {"votes": [], "events": [_event(2, "immunity_given", {"giver_id": "a", "recipient_id": "b"}, ["host", "a", "b"], ["b"])]}
        self.assertEqual(council_record.councils(data)[2]["immunity_given"], {"giver_id": "a", "recipient_id": "b"})

    def test_plurality_on_a_tie_is_the_player_who_left(self) -> None:
        self.assertEqual(council_record.plurality({"a": 3, "b": 3}, "b"), "b")
        self.assertIsNone(council_record.plurality({"a": 3, "b": 3}, "c"))
        self.assertEqual(council_record.plurality({"a": 3, "b": 2}, "b"), "a")

    def test_promises_count_once_and_a_spoiled_vote_breaks_them(self) -> None:
        rows = [
            {"round": 1, "promiser_id": "a", "listener_id": "b", "target_id": "c"},
            {"round": 1, "promiser_id": "a", "listener_id": "b", "target_id": "c"},
            {"round": 1, "promiser_id": "d", "listener_id": "b", "target_id": "c"},
        ]
        deduped = season_report._dedupe_promises(rows)
        self.assertEqual(len(deduped), 2)
        agents = [{"agent_id": x, "pseudonym": x.upper(), "elimination_round": None} for x in "abcd"]
        data = {
            "agents": agents, "attempts": [], "results": [], "plans": [], "spoiled": [{"round": 1, "voter_id": "d", "ballot": "vote", "reason": "x"}],
            "votes": [_vote(1, "a", "c"), _vote(1, "b", "c"), _vote(1, "c", "a")],
            "promises": deduped,
            "events": [_event(1, "elimination", {"eliminated_id": "c", "final_tally": {"c": 2, "a": 1}})],
        }
        rnd = season_report._build_rounds(data, {x: x for x in "abcd"})[0]
        status = {p["by_id"]: p["status"] for p in rnd["promises"]}
        self.assertEqual(status, {"a": "kept", "d": "broken"})
        self.assertTrue(any(v["spoiled"] and v["voter_id"] == "d" for v in rnd["votes"]))


class ReliabilityAndCostTest(unittest.TestCase):
    def test_reliability_counts_every_kind_of_failure(self) -> None:
        data = {
            "agents": [{"agent_id": "a"}, {"agent_id": "b"}],
            "spoiled": [{"round": 2, "voter_id": "a", "ballot": "vote"}],
            "events": [_event(1, "conversation", {"speaker_line_metadata": [{"agent_id": "a", "llm_provider": "silent"}, {"agent_id": "b", "llm_provider": "openrouter"}]})],
            "attempts": [{"agent_id": "a", "round": 3, "provider": "did_not_finish", "response_ms": 90000},
                         {"agent_id": "b", "round": 3, "provider": "openrouter", "response_ms": 5000}],
            "plans": [{"agent_id": "a", "provider": "openrouter_failed"}],
        }
        rel = report_facts.reliability(data, [], {"a": "Ash", "b": "Birch"})
        self.assertEqual(rel["a"]["spoiled_votes"], [{"round": 2, "ballot": "vote"}])
        self.assertEqual(rel["a"]["silent_lines"], 1)
        self.assertEqual(rel["a"]["puzzle_rounds_unanswered"], [3])
        self.assertEqual(rel["a"]["plans_missing"], 1)
        self.assertEqual(rel["a"]["failures"], 4)
        self.assertEqual(rel["b"]["failures"], 0)
        self.assertEqual(rel["b"]["latency_basis"], "puzzle calls")

    def test_prompt_log_gives_calls_errors_latency_and_measured_cost(self) -> None:
        entries = [
            {"player": "Ash", "model": "m/a", "reply": {"x": 1}, "model_ms": 1000, "usage": {"cost": 0.01, "prompt_tokens": 10}},
            {"player": "Ash", "model": "m/a", "reply": None, "model_ms": 3000, "usage": {"prompt_tokens": 1000, "completion_tokens": 100}},
            {"player": "Ash", "model": "m/a", "error": "RuntimeError"},
            {"player": None, "model": "host/m", "reply": {"x": 1}, "usage": {"cost": 5.0}},
        ]
        rel = report_facts.reliability({"agents": [{"agent_id": "a"}]}, entries, {"a": "Ash"})["a"]
        self.assertEqual((rel["calls"], rel["call_errors"], rel["unreadable_replies"]), (3, 1, 1))
        self.assertEqual(rel["latency_basis"], "every call")
        table = {"m/a": {"prompt_per_m": 1.0, "completion_per_m": 10.0}}
        cost = report_facts.measured_cost(entries, {"a": "Ash"}, table)
        self.assertAlmostEqual(cost["a"], 0.01 + 0.001 + 0.001)  # the host's call is not a player's
        self.assertIsNone(report_facts.measured_cost([{"player": "Ash", "reply": {}}], {"a": "Ash"}, table))

    def test_prompt_log_keeps_usage_numbers_and_the_call_id(self) -> None:
        response = {"id": "gen-123", "choices": [{"message": {"content": "{\"say\": \"hi\"}"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 2, "cost": 0.0001, "is_byok": False, "note": "x",
                              "prompt_tokens_details": {"cached_tokens": 1, "label": "y"}}}
        entry = prompt_log._entry({"model": "m", "messages": []}, response, None)
        self.assertEqual(entry["id"], "gen-123")
        self.assertEqual(entry["usage"], {"prompt_tokens": 5, "completion_tokens": 2, "cost": 0.0001, "prompt_tokens_details": {"cached_tokens": 1}})

    def test_read_prompt_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / prompt_log.GZ_NAME
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                handle.write(json.dumps({"player": "Ash"}) + "\nnot json\n")
            self.assertEqual(report_facts.read_prompt_log(Path(tmp)), [{"player": "Ash"}])
            self.assertEqual(report_facts.read_prompt_log(Path(tmp) / "none"), [])

    def test_wilson_and_k_of_n(self) -> None:
        lo, hi = report_facts.wilson(26, 33)
        self.assertAlmostEqual(lo, 0.6225, places=3)
        self.assertAlmostEqual(hi, 0.8933, places=3)
        self.assertEqual(report_facts.k_of_n(2, 2), "2 of 2")
        self.assertEqual(report_facts.k_of_n(26, 33), "26 of 33 (79%)")
        self.assertEqual(report_facts.k_of_n(0, 0), "–")


if __name__ == "__main__":
    unittest.main()
