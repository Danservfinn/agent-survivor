"""Secret ballots: players learn the tally and their own vote, never who voted
for whom; the jury sees the true votes at the finale."""

from __future__ import annotations

import json

from backend import turn_controller as tc
from backend.llm_context import build_agent_episode_context
from backend.tests.test_model_failures import ROUND, _Base


class SecretBallotTest(_Base):
    def setUp(self) -> None:
        super().setUp()
        self.alias = {row["agent_id"]: row["pseudonym"] for row in self.conn.execute("SELECT agent_id, pseudonym FROM Agents")}
        self.me, self.ally, self.traitor, self.out, *self.rest = self.players

    def _council(self) -> None:
        """traitor (my pact partner) votes for me; ally and I vote out `out`."""
        tc._maybe_update_alliance(
            self.conn, ROUND, 1, [self.me, self.traitor],
            [{"agent_id": self.me, "join_intent": "join"}, {"agent_id": self.traitor, "join_intent": "join"}],
        )
        self.conn.execute(
            "INSERT INTO Promises (round, promiser_id, listener_id, target_id, discussion_step) VALUES (?, ?, ?, ?, 't')",
            (ROUND, self.traitor, self.me, self.out),
        )
        ballots = {self.me: self.out, self.ally: self.out, self.traitor: self.me, self.out: self.me}
        for voter, target in ballots.items():
            tc._insert_vote(self.conn, ROUND, 0, voter, target)
        for index in range(len(ballots)):
            tc._vote_reveal_event(self.conn, ROUND, 1, index + 1)
        self.conn.execute(
            "UPDATE Agents SET status = 'eliminated', elimination_round = ? WHERE agent_id = ?", (ROUND, self.out)
        )

    def _context(self, actor: str) -> dict:
        return build_agent_episode_context(self.conn, actor_id=actor, round_number=ROUND + 1, current_step="x")

    def test_players_see_the_tally_and_their_own_vote_only(self) -> None:
        self._council()
        context = self._context(self.me)
        history = context["vote_history"][0]
        self.assertEqual(history["tally"], {self.alias[self.out]: 2, self.alias[self.me]: 2})
        self.assertEqual(history["your_vote"], self.alias[self.out])
        self.assertNotIn("votes", history)
        brief = context["situation_brief"]
        self.assertNotIn("voting_together", brief)
        self.assertEqual(brief["votes_against_you"], [{"round": ROUND, "votes": 2}])
        for player in brief["other_players_by_threat"]:
            self.assertNotIn("voted_against_you", player)
            self.assertNotIn("promises_broken", player)
        juror = brief["jury_so_far"][0]
        self.assertNotIn("voted_out_by", juror)
        self.assertEqual(juror["votes_against_them"], 2)

    def test_a_broken_promise_and_a_betrayal_stay_secret(self) -> None:
        self._council()
        context = self._context(self.me)
        made_to_me = context["your_promises"]["made_to_you"][0]
        self.assertEqual(made_to_me["status"], "secret ballot: unknown")
        self.assertIsNone(made_to_me["actual_vote"])
        pact = context["situation_brief"]["your_pacts"][0]
        self.assertEqual(pact["partners"][0]["status"], "in the pact")
        self.assertNotIn("councils_voted_apart", pact["partners"][0])
        alliances = [m for m in context["actor_private_memory"] if m.get("kind") == "active_alliance"]
        self.assertIn(self.traitor, alliances[0]["member_ids"])
        self.assertNotIn("strength", alliances[0])
        # the traitor knows its own broken promise
        mine = self._context(self.traitor)["your_promises"]["made_by_you"][0]
        self.assertEqual(mine["status"], "broken")

    def test_nobody_can_pair_a_voter_with_a_vote(self) -> None:
        self._council()
        for actor in (self.me, self.ally, self.traitor):
            text = json.dumps(self._context(actor))
            # the traitor's vote for me appears nowhere as a (voter, target) pair
            if actor != self.traitor:
                self.assertNotIn(f'"voter": "{self.alias[self.traitor]}"', text)
        reveals = [
            json.loads(row["payload"]) for row in self.conn.execute("SELECT payload FROM StoryEvents WHERE kind = 'vote_reveal'")
        ]
        self.assertTrue(all("voter" not in json.dumps(payload) for payload in reveals))

    def test_votes_are_read_in_a_shuffled_but_fixed_order(self) -> None:
        for voter in self.players[:8]:
            tc._insert_vote(self.conn, ROUND, 0, voter, self.players[8])
        order = tc._reveal_order(self.conn, ROUND)
        cast = [row["id"] for row in self.conn.execute("SELECT id FROM Votes WHERE round = ? ORDER BY id", (ROUND,))]
        self.assertNotEqual(order, cast)
        self.assertEqual(sorted(order), cast)
        self.assertEqual(order, tc._reveal_order(self.conn, ROUND))

    def test_the_jury_sees_the_true_votes(self) -> None:
        self._council()
        dossier = tc._finalist_dossier(self.conn, self.traitor)
        self.assertEqual(dossier["votes_cast"][0]["voted_for"], self.alias[self.me])


class SocialStepsTest(_Base):
    """Tribal council, visible meetings, parting words, jury deliberation."""

    def setUp(self) -> None:
        super().setUp()
        self.alias = {row["agent_id"]: row["pseudonym"] for row in self.conn.execute("SELECT agent_id, pseudonym FROM Agents")}

    def _speak(self, **kwargs):
        from backend.openrouter_client import AgentAction

        actor = kwargs["actor"]["agent_id"]
        if kwargs["response_kind"] == "talk_choice":
            return AgentAction(dialogue="", inner_thought="", model_id="m", invite_ids=(self.players[1],) if actor == self.players[0] else ())
        return AgentAction(dialogue=f"{kwargs['step']} from {actor}", inner_thought="", model_id="m")

    def test_tribal_council_comes_before_the_vote_and_everyone_hears_it(self) -> None:
        state = {"phase": "round", "phase_step": "challenge_intro", "current_round": ROUND}
        steps = tc._phase_steps(self.conn, state)
        self.assertLess(steps.index("tribal_council"), min(i for i, s in enumerate(steps) if s.startswith("vote_booth_")))
        self.assertLess(steps.index("elimination"), steps.index("parting_words"))
        self._live(self._speak)
        event = tc._tribal_council_event(self.conn, ROUND, 1)
        lines = json.loads(event["payload"])["speaker_lines"] if isinstance(event["payload"], str) else event["payload"]["speaker_lines"]
        self.assertEqual({line["agent_id"] for line in lines}, set(self.players))
        bystander = json.dumps(build_agent_episode_context(self.conn, actor_id=self.players[5], round_number=ROUND, current_step="x"))
        self.assertIn(f"tribal_council from {self.players[0]}", bystander)

    def test_everyone_sees_who_met_whom_but_not_what_was_said(self) -> None:
        a, b, c, *_ = self.players
        self._live(self._speak)
        events, _, _ = tc._apply_step(self.conn, {"current_round": ROUND}, "camp_talks", 1, 0)
        self.assertEqual(events[-1]["kind"], "camp_meetings")
        outsider = json.dumps(build_agent_episode_context(self.conn, actor_id=c, round_number=ROUND, current_step="x"))
        self.assertIn("meetings", outsider)
        self.assertIn(self.alias[b], json.dumps(events[-1]["payload"]["meetings"]))
        self.assertNotIn("camp_talk_0 from", outsider)

    def test_parting_words_are_public_and_skipped_when_nobody_left(self) -> None:
        out = self.players[4]
        self._live(self._speak)
        self.assertIsNone(tc._parting_words_event(self.conn, ROUND, 1))
        self.conn.execute("UPDATE Agents SET status = 'eliminated', elimination_round = ? WHERE agent_id = ?", (ROUND, out))
        event = tc._parting_words_event(self.conn, ROUND, 1)
        self.assertEqual(event["dialogue"], f"parting_words from {out}")

    def test_jury_deliberation_is_heard_only_by_jurors(self) -> None:
        jurors, finalists = self.players[3:], self.players[:3]
        for index, juror in enumerate(jurors):
            self.conn.execute(
                "UPDATE Agents SET status = 'eliminated', elimination_round = ? WHERE agent_id = ?", (index + 1, juror)
            )
        self._live(self._speak)
        tc._jury_deliberation_event(self.conn, 9, 1)
        juror_view = json.dumps(build_agent_episode_context(self.conn, actor_id=jurors[0], round_number=9, current_step="x"))
        finalist_view = json.dumps(build_agent_episode_context(self.conn, actor_id=finalists[0], round_number=9, current_step="x"))
        self.assertIn(f"jury_deliberation from {jurors[1]}", juror_view)
        self.assertNotIn("jury_deliberation from", finalist_view)
