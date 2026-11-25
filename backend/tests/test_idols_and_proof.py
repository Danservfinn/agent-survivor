"""Idol search, a shown idol, a shown ballot, and handing immunity away."""

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


class IdolSearchTest(_Base):
    def _hidden_seed(self, round_number: int, hidden: bool) -> int:
        import random

        return next(s for s in range(500) if (random.Random(f"idol:{s}:{round_number}").random() < tc.IDOL_HIDDEN_CHANCE) == hidden)

    def _search(self, round_number: int, searchers: list[str], hidden: bool) -> list[dict]:
        with patch.object(tc, "_season_seed", return_value=self._hidden_seed(round_number, hidden)), patch.object(
            tc, "_season_first_round", return_value=1
        ):
            return tc._idol_search_events(self.conn, round_number, 1, searchers)

    def test_a_hidden_idol_goes_to_one_searcher_privately(self) -> None:
        a, b, other, *_ = self.players
        events = self._search(3, [a, b], hidden=True)
        holders = [row["agent_id"] for row in self.conn.execute("SELECT agent_id FROM Agents WHERE idol_held = 1")]
        self.assertEqual(len(holders), 1)
        self.assertIn(holders[0], (a, b))
        self.assertEqual([e["payload"]["participant_ids"] for e in events], [[a], [b]])
        found = {e["payload"]["participant_ids"][0]: e["payload"]["found"] for e in events}
        self.assertEqual(sum(found.values()), 1)
        seen = json.dumps(build_agent_episode_context(self.conn, actor_id=other, round_number=3, current_step="x"))
        self.assertNotIn("searched camp", seen)

    def test_nothing_is_found_in_an_empty_round_before_round_3_or_while_one_is_held(self) -> None:
        a, b, holder, *_ = self.players
        self._search(3, [a], hidden=False)
        self._search(2, [a], hidden=True)
        self.conn.execute("UPDATE Agents SET idol_held = 1 WHERE agent_id = ?", (holder,))
        self._search(4, [a, b], hidden=True)
        self.assertEqual(self.conn.execute("SELECT SUM(found) FROM IdolSearches").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM Agents WHERE idol_held = 1").fetchone()[0], 1)

    def test_the_hidden_rate_is_about_half(self) -> None:
        import random

        hits = sum(random.Random(f"idol:{s}:4").random() < tc.IDOL_HIDDEN_CHANCE for s in range(2000))
        self.assertAlmostEqual(hits / 2000, 0.5, delta=0.04)

    def test_a_search_choice_skips_the_talk(self) -> None:
        others = [dict(self.conn.execute("SELECT * FROM Agents WHERE agent_id = ?", (p,)).fetchone()) for p in self.players[1:3]]
        self.assertEqual(tc._talk_choice_result(_action(search=True, invite_ids=(self.players[1],)), others), tc.IDOL_SEARCH)
        with patch.dict(os.environ, {"SURVIVOR_ADVANTAGES": "0"}):
            self.assertEqual(tc._talk_choice_result(_action(search=True, invite_ids=(self.players[1],)), others)[0], [self.players[1]])


class ShowIdolTest(_Base):
    def test_only_a_real_holder_gets_the_stamp(self) -> None:
        stamped = advantages.line_stamps(_action(show_idol=True), {}, True, "Pine")
        self.assertEqual(stamped, [{"shown": "Pine showed a hidden immunity idol.", "verified_by": "the game"}])
        self.assertEqual(advantages.line_stamps(_action(show_idol=True), {}, False, "Pine"), [])


class ShowBallotTest(_Base):
    def _cast(self, voter: str, target: str, shown: bool) -> None:
        decision = tc.VoteDecision(target, "x", "openrouter", show_ballot=shown)
        tc._insert_vote(self.conn, 1, 0, voter, target, decision)

    @patch.dict(os.environ, {"SURVIVOR_SECRET_BALLOT": "1"})
    def test_a_shown_ballot_names_only_its_own_voter(self) -> None:
        shower, hider, target, viewer, *_ = self.players
        self._cast(shower, target, True)
        self._cast(hider, target, False)
        with patch.object(tc, "_total_votes_for_round", return_value=2):
            titles = [tc._vote_reveal_event(self.conn, 1, 1, i)["title"] for i in (1, 2)]
        names = advantages._names(self.conn)
        self.assertEqual(sum(names[shower] in t for t in titles), 1)
        self.assertFalse(any(names[hider] in t for t in titles))
        history = build_agent_episode_context(self.conn, actor_id=viewer, round_number=2, current_step="x")["vote_history"]
        self.assertEqual(history[0]["shown_ballots"], [{"voter": names[shower], "target": names[target]}])
        self.assertNotIn(names[hider], json.dumps(history))


class GiveImmunityTest(_Base):
    def _winner(self) -> str:
        winner = self.players[0]
        self.conn.execute(
            "INSERT INTO ChallengeResults (round, puzzle_id, challenge_type, winning_agent_id) VALUES (1, 'p', 'ladder', ?)",
            (winner,),
        )
        self.conn.execute("UPDATE Agents SET has_immunity = 1 WHERE agent_id = ?", (winner,))
        return winner

    def _immune(self) -> list[str]:
        return [r["agent_id"] for r in self.conn.execute("SELECT agent_id FROM Agents WHERE has_immunity = 1")]

    def test_handing_it_over_moves_immunity_and_is_announced(self) -> None:
        winner = self._winner()
        friend = self.players[3]
        self._live(lambda **kwargs: _action(target_id=friend, dialogue="You need it more."))
        [event] = tc._immunity_choice_events(self.conn, 1, 1)
        self.assertEqual(self._immune(), [friend])
        self.assertEqual(event["kind"], "immunity_given")
        self.assertEqual(event["visibility"], "audience")

    def test_keeping_it_changes_nothing(self) -> None:
        winner = self._winner()
        self._live(lambda **kwargs: _action(target_id=None))
        self.assertEqual(tc._immunity_choice_events(self.conn, 1, 1), [])
        self.assertEqual(self._immune(), [winner])

    def test_an_illegal_pick_keeps_it(self) -> None:
        winner = self._winner()
        self._live(lambda **kwargs: _action(target_id=winner))
        tc._immunity_choice_events(self.conn, 1, 1)
        self.assertEqual(self._immune(), [winner])


if __name__ == "__main__":
    unittest.main()
