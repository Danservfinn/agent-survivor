"""The once-a-season auction: tokens, sealed bids, ties, and the three
items (an extra vote, an idol, a peek at the last council's votes)."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from backend import auction
from backend import turn_controller as tc
from backend.llm_context import build_agent_episode_context
from backend.tests.test_model_failures import _Base


class BidRulesTest(unittest.TestCase):
    def test_bids_are_whole_and_known_and_void_over_budget(self) -> None:
        self.assertEqual(auction.clean_bids({"extra_vote": 3.7, "idol": "2", "boat": 9, "vote_peek": -1}, 10), {"extra_vote": 3, "idol": 2})
        self.assertEqual(auction.clean_bids({"extra_vote": 6, "idol": 5}, 10), {})
        self.assertEqual(auction.clean_bids("all of it", 10), {})

    def test_highest_bid_wins_and_a_tie_wins_nothing(self) -> None:
        won = auction.winners({"a": {"extra_vote": 5, "idol": 3}, "b": {"extra_vote": 4, "idol": 3}, "c": {"vote_peek": 1}})
        self.assertEqual(won, {"extra_vote": ("a", 5), "idol": None, "vote_peek": ("c", 1)})


class AuctionTest(_Base):
    def setUp(self) -> None:
        super().setUp()
        for out in self.players[:3]:
            self.conn.execute("UPDATE Agents SET status = 'eliminated', elimination_round = 1 WHERE agent_id = ?", (out,))
        self.active = self.players[3:]

    def test_due_once_with_three_players_gone(self) -> None:
        self.assertTrue(auction.auction_due(self.conn, 4, len(self.active)))
        self.assertFalse(auction.auction_due(self.conn, 4, len(self.active) + 1))
        self.conn.execute("INSERT INTO AuctionBids (round, agent_id) VALUES (4, ?)", (self.active[0],))
        self.assertTrue(auction.auction_due(self.conn, 4, 5))  # stays in the round it was held
        self.assertFalse(auction.auction_due(self.conn, 5, 5))
        with patch.dict(os.environ, {"SURVIVOR_ADVANTAGES": "0"}):
            self.assertFalse(auction.auction_due(self.conn, 4, 5))

    def test_tokens_count_rounds_played_and_puzzles_solved(self) -> None:
        me = self.active[0]
        for rnd, correct in ((1, 1), (1, 1), (2, 0), (3, 1)):
            self.conn.execute(
                "INSERT INTO ChallengeAttempts (round, puzzle_id, agent_id, is_correct) VALUES (?, 'p', ?, ?)", (rnd, me, correct)
            )
        self.assertEqual(auction.tokens(self.conn, me, 4, 1), 3 * 3 + 3)
        self.assertEqual(auction.tokens(self.conn, me, 3, 1), 3 * 2 + 2)

    def _run(self, bids: dict[str, dict]) -> list[dict]:
        def private(**kwargs):
            return {"bids": bids.get(kwargs["actor"]["agent_id"], {}), "why": "x"}

        self._live(lambda **kwargs: None)
        with patch.object(tc, "request_private_writing", side_effect=private), patch.object(
            tc, "_season_first_round", return_value=1
        ), patch.object(tc, "_parallel_enabled", return_value=False):
            return tc._auction_events(self.conn, 4, 1)

    def test_the_items_go_to_the_top_bidders(self) -> None:
        a, b, c, d, *_ = self.active
        self.conn.execute("INSERT INTO Votes (round, turn_index, voter_id, target_id, revealed) VALUES (3, 0, ?, ?, 1)", (a, b))
        self.conn.execute("INSERT INTO Votes (round, turn_index, voter_id, target_id, revealed) VALUES (3, 0, ?, ?, 1)", (c, b))
        events = self._run({a: {"extra_vote": 5}, b: {"extra_vote": 4, "idol": 2}, c: {"vote_peek": 1}, d: {"idol": 2}})
        kinds = {row["kind"]: row["agent_id"] for row in self.conn.execute("SELECT kind, agent_id FROM Advantages")}
        self.assertEqual(kinds, {"extra_vote": a, "vote_peek": c})  # idol tied at 2: no sale
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM AuctionBids").fetchone()[0], len(self.active))
        public = events[0]
        self.assertEqual(public["visibility"], "audience")
        self.assertIn("idol: no sale", public["dialogue"])
        self.assertNotIn("5", public["dialogue"])  # amounts stay secret
        peek = next(e for e in events if e["kind"] == "vote_peek")
        self.assertEqual(peek["payload"]["participant_ids"], [c])
        names = auction  # noqa: F841
        self.assertIn("voted", peek["payload"]["group_summary"])

    def test_a_sold_idol_is_held_and_its_buyer_is_not_named(self) -> None:
        a, b, *_ = self.active
        events = self._run({a: {"idol": 3}})
        self.assertEqual(self.conn.execute("SELECT idol_held FROM Agents WHERE agent_id = ?", (a,)).fetchone()[0], 1)
        name = self.conn.execute("SELECT pseudonym FROM Agents WHERE agent_id = ?", (a,)).fetchone()[0]
        self.assertNotIn(name, events[0]["dialogue"])
        self.assertIn("idol: sold", events[0]["dialogue"])

    def test_the_extra_vote_adds_one_ballot_and_one_reveal_step(self) -> None:
        a, b, c, *_ = self.active
        self.conn.execute("INSERT INTO Advantages (agent_id, kind, round_won) VALUES (?, 'extra_vote', 4)", (a,))
        self.conn.execute("UPDATE GameState SET current_round = 4, phase = 'round', phase_step = 'vote_booth_x'")
        state = {"current_round": 4, "phase": "round", "phase_step": "camp_talks"}
        with patch.dict(os.environ, {"AGENT_SURVIVOR_FAST_BENCHMARK": "1"}):
            before = [s for s in tc._phase_steps(self.conn, state) if s.startswith("vote_reveal_")]
            self.assertEqual(len(before), len(self.active) + 1)
            decision = tc.VoteDecision(b, "x", "openrouter", use_extra_vote=True)
            tc._insert_vote(self.conn, 4, 0, a, b, decision)
            tc._insert_extra_vote(self.conn, 4, 0, a, decision)
            after = [s for s in tc._phase_steps(self.conn, state) if s.startswith("vote_reveal_")]
        self.assertEqual(len(after), len(before))  # used this round: still read tonight
        votes = self.conn.execute("SELECT target_id, is_extra FROM Votes WHERE round = 4 AND voter_id = ?", (a,)).fetchall()
        self.assertEqual([(v["target_id"], v["is_extra"]) for v in votes], [(b, 0), (b, 1)])
        self.assertFalse(auction.holds(self.conn, a, "extra_vote"))
        self.assertEqual(auction.extra_votes_for_round(self.conn, 5, self.active), 0)  # gone next round

    def test_the_brief_shows_tokens_before_and_advantages_after(self) -> None:
        me = self.active[0]
        with patch.object(tc, "_season_first_round", return_value=1):
            you = build_agent_episode_context(self.conn, actor_id=me, round_number=3, current_step="x")["situation_brief"]["you"]
            self.assertEqual(you["advantages"]["auction_tokens"], 6)
            self.conn.execute("INSERT INTO AuctionBids (round, agent_id) VALUES (4, ?)", (me,))
            self.conn.execute("INSERT INTO Advantages (agent_id, kind, round_won) VALUES (?, 'extra_vote', 4)", (me,))
            you = build_agent_episode_context(self.conn, actor_id=me, round_number=4, current_step="x")["situation_brief"]["you"]
        self.assertNotIn("auction_tokens", you["advantages"])
        self.assertEqual(you["advantages"]["held"], [{"advantage": "extra_vote", "won_in_round": 4}])


if __name__ == "__main__":
    unittest.main()
