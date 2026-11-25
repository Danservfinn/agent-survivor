"""Ruthless play made legitimate and legible: permission, an endgame view,
each player's own betrayals and what they achieved, and the jury's view."""

from __future__ import annotations

import json

from backend import turn_controller as tc
from backend.llm_context import build_agent_episode_context, game_rules
from backend.tests.test_model_failures import ROUND, _Base


class RuthlessPlayTest(_Base):
    def setUp(self) -> None:
        super().setUp()
        self.alias = {row["agent_id"]: row["pseudonym"] for row in self.conn.execute("SELECT agent_id, pseudonym FROM Agents")}
        self.me, self.partner, self.listener, self.out, *self.rest = self.players

    def _brief(self, actor: str, round_number: int = ROUND + 1) -> dict:
        return build_agent_episode_context(self.conn, actor_id=actor, round_number=round_number, current_step="x")["situation_brief"]

    def _council(self) -> None:
        """I promised the listener a vote for `out`, but voted my pact partner, who went home."""
        tc._maybe_update_alliance(
            self.conn, ROUND, 1, [self.me, self.partner],
            [{"agent_id": self.me, "join_intent": "join"}, {"agent_id": self.partner, "join_intent": "join"}],
        )
        self.conn.execute(
            "INSERT INTO Promises (round, promiser_id, listener_id, target_id, discussion_step) VALUES (?, ?, ?, ?, 't')",
            (ROUND, self.me, self.listener, self.out),
        )
        ballots = {self.me: self.partner, self.listener: self.partner, self.out: self.partner, self.partner: self.me}
        for voter, target in ballots.items():
            tc._insert_vote(self.conn, ROUND, 0, voter, target)
        for index in range(len(ballots)):
            tc._vote_reveal_event(self.conn, ROUND, 1, index + 1)
        self.conn.execute(
            "UPDATE Agents SET status = 'eliminated', elimination_round = ? WHERE agent_id = ?", (ROUND, self.partner)
        )

    def test_the_rules_and_the_checklist_make_betrayal_a_legitimate_choice(self) -> None:
        rules = " ".join(game_rules())
        self.assertIn("harms nobody", rules)
        self.assertIn("Blindsiding an ally is a normal winning move", rules)
        self.assertIn("your call", rules)  # permission, not an order
        from backend import openrouter_client

        source = open(openrouter_client.__file__).read()
        self.assertIn("Is there anyone I should cut before they cut me, and when is the last safe moment?", source)

    def test_a_player_sees_its_own_betrayals_and_what_they_achieved(self) -> None:
        self._council()
        record = self._brief(self.me)["your_betrayals_and_what_they_achieved"]
        moves = {item["move"]: item for item in record}
        promise = moves[f"broke a promise to {self.alias[self.listener]} to vote {self.alias[self.out]}"]
        self.assertTrue(promise["your_vote_sent_them_home"])
        self.assertIsNone(promise["votes_against_you_next_round"])  # the next council has not happened
        pact = moves[f"voted against pact partner {self.alias[self.partner]}"]
        self.assertEqual(pact["who_went_home"], self.alias[self.partner])
        # nobody else sees my betrayals
        self.assertEqual(self._brief(self.listener)["your_betrayals_and_what_they_achieved"], [])

    def test_the_endgame_view_lists_facts_about_the_jury_without_a_verdict(self) -> None:
        self._council()
        tc._insert_story_event(
            self.conn, turn_id=1, round_number=ROUND, phase="tribal", kind="parting_words", scene="tribal", shot="x",
            actor_ids=[self.partner], target_ids=[], visibility="audience", title="P",
            dialogue=f"{self.alias[self.listener]} played me. Remember that.", payload={},
        )
        endgame = self._brief(self.me)["endgame"]
        self.assertEqual(endgame["councils_until_final_three"], len(self.players) - 1 - 3)
        juror = endgame["jurors_so_far"][0]
        self.assertEqual(juror["name"], self.alias[self.partner])
        self.assertTrue(juror["you_voted_them_out"])
        others = {entry["name"]: entry for entry in endgame["other_players_ties_to_the_jury"]}
        self.assertEqual(others[self.alias[self.listener]]["jurors_who_named_them_in_parting_words"], [self.alias[self.partner]])
        self.assertFalse([key for entry in endgame["other_players_ties_to_the_jury"] for key in entry if "score" in key or "rank" in key])
        # secret ballots: the listener's own vote is known to it, nobody else's
        self.assertNotIn("voted_them_out", json.dumps(others))

    def test_the_jury_sees_pact_betrayals_next_to_what_they_achieved(self) -> None:
        self._council()
        dossier = tc._finalist_dossier(self.conn, self.me)
        betrayal = dossier["pact_betrayals_and_what_they_achieved"][0]
        self.assertEqual(betrayal["who_went_home"], self.alias[self.partner])
        self.assertTrue(betrayal["your_vote_sent_them_home"])


class MutualBetrayalTest(_Base):
    def test_partners_who_vote_against_each_other_both_betray_but_not_a_past_traitor(self) -> None:
        a, b, c, *_ = self.players
        tc._maybe_update_alliance(
            self.conn, ROUND, 1, [a, b], [{"agent_id": a, "join_intent": "join"}, {"agent_id": b, "join_intent": "join"}]
        )
        tc._insert_vote(self.conn, ROUND, 0, a, b)
        tc._insert_vote(self.conn, ROUND, 0, b, a)
        tc._vote_reveal_event(self.conn, ROUND, 1, 1)
        tc._vote_reveal_event(self.conn, ROUND, 1, 2)
        rounds = dict(self.conn.execute("SELECT agent_id, betrayed_round FROM AllianceMemberships").fetchall())
        self.assertEqual(rounds, {a: ROUND, b: ROUND})
