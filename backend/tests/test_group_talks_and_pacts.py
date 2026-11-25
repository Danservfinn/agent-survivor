"""Group talks of any size, pacts among declarers, clean exits vs betrayals,
and each player's pact record in its situation brief."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

from backend import turn_controller as tc
from backend.llm_context import build_agent_episode_context
from backend.openrouter_client import AgentAction
from backend.tests.test_model_failures import ROUND, _Base


class GroupTalkTest(_Base):
    def test_a_player_can_invite_several_and_everyone_hears_every_line(self) -> None:
        a, b, c, d, *_ = self.players
        scenes: list[tuple[str, dict]] = []

        def action(**kwargs):
            actor = kwargs["actor"]["agent_id"]
            if kwargs["response_kind"] == "talk_choice":
                invite = (b, c, d) if actor == a else ()
                return AgentAction(dialogue="", inner_thought="", model_id="m", invite_ids=invite, strategic_summary="bloc")
            scenes.append((actor, json.loads(kwargs["scene_context"])))
            return AgentAction(dialogue=f"{actor} line {len(scenes)}", inner_thought="", model_id="m")

        self._live(action)
        events = tc._camp_talk_events(self.conn, ROUND, 1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["participant_ids"], [a, b, c, d])
        self.assertEqual(len(events[0]["payload"]["speaker_lines"]), 8)  # four players, twice each
        # the fourth speaker hears all three earlier lines, not only the last one
        heard = scenes[3][1]["said_so_far_in_this_talk"]
        for earlier in scenes[:3]:
            self.assertIn(f"{earlier[0]} line", heard)
        self.assertNotIn("active_alliances", scenes[0][1])


class PactTest(_Base):
    def _talk(self, members: list[str], intents: dict[str, str], exits: set[str] = frozenset(), round_number: int = ROUND):
        lines = [
            {"agent_id": agent_id, "join_intent": intents.get(agent_id, "none"), "end_pact": agent_id in exits}
            for agent_id in members
        ]
        self.talks = getattr(self, "talks", 0) + 1  # each talk has its own id, as in a season
        return tc._maybe_update_alliance(self.conn, round_number, self.talks, members, lines)

    def _members(self, alliance_id: str) -> dict[str, str]:
        return {
            row["agent_id"]: row["status"]
            for row in self.conn.execute("SELECT agent_id, status FROM AllianceMemberships WHERE alliance_id = ?", (alliance_id,))
        }

    def test_the_declarers_in_a_group_form_the_pact(self) -> None:
        a, b, c, d, *_ = self.players
        alliance_id, status = self._talk([a, b, c, d], {a: "join", b: "join", c: "join"})
        self.assertEqual(status, "formed")
        self.assertEqual(set(self._members(alliance_id)), {a, b, c})

    def test_a_group_pact_does_not_absorb_an_older_pair(self) -> None:
        a, b, c, *_ = self.players
        pair, _ = self._talk([a, b], {a: "join", b: "join"})
        trio, status = self._talk([a, b, c], {a: "join", b: "join", c: "join"})
        self.assertEqual(status, "formed")
        self.assertNotEqual(pair, trio)
        self.assertEqual(set(self._members(pair)), {a, b})

    def test_a_clean_exit_is_not_a_betrayal(self) -> None:
        a, b, c, *_ = self.players
        pact, _ = self._talk([a, b], {a: "join", b: "join"})
        self._talk([a, b], {}, exits={a}, round_number=ROUND + 1)
        member = self.conn.execute(
            "SELECT status, left_round, betrayed_round FROM AllianceMemberships WHERE alliance_id = ? AND agent_id = ?", (pact, a)
        ).fetchone()
        self.assertEqual((member["status"], member["left_round"], member["betrayed_round"]), ("left", ROUND + 1, None))
        self.assertEqual(self.conn.execute("SELECT status FROM Alliances WHERE alliance_id = ?", (pact,)).fetchone()[0], "dissolved")
        # voting against the former partner later is no betrayal
        tc._insert_vote(self.conn, ROUND + 1, 0, a, b)
        tc._vote_reveal_event(self.conn, ROUND + 1, 1, 1)
        self.assertEqual(self._members(pact)[a], "left")
        dossier = tc._finalist_dossier(self.conn, a)
        self.assertEqual(dossier["alliances"][0]["left_openly_round"], ROUND + 1)
        self.assertIsNone(dossier["alliances"][0]["betrayed_round"])

    def test_a_betrayal_is_marked_when_the_vote_is_revealed_not_when_cast(self) -> None:
        a, b, *_ = self.players
        pact, _ = self._talk([a, b], {a: "join", b: "join"})
        tc._insert_vote(self.conn, ROUND, 0, a, b)
        # still secret: b, waiting to vote, must not see a leave the pact
        self.assertEqual(self._members(pact)[a], "active")
        tc._vote_reveal_event(self.conn, ROUND, 1, 1)
        self.assertEqual(self._members(pact)[a], "betrayed")

    @patch.dict(os.environ, {"SURVIVOR_SECRET_BALLOT": "0"})  # the open-ballot rule

    def test_the_pact_record_shows_how_each_partner_voted(self) -> None:
        a, b, c, x, y, *_ = self.players
        self._talk([a, b, c], {a: "join", b: "join", c: "join"})
        for voter, target in ((a, x), (b, x), (c, y)):
            self.conn.execute(
                "INSERT INTO Votes (round, turn_index, voter_id, target_id, revealed) VALUES (?, 0, ?, ?, 1)",
                (ROUND, voter, target),
            )
        brief = build_agent_episode_context(self.conn, actor_id=a, round_number=ROUND + 1, current_step="x")["situation_brief"]
        pact = brief["your_pacts"][0]
        self.assertEqual(pact["you"], "in the pact")
        partners = {partner["name"]: partner for partner in pact["partners"]}
        alias = {row["agent_id"]: row["pseudonym"] for row in self.conn.execute("SELECT agent_id, pseudonym FROM Agents")}
        self.assertEqual(partners[alias[b]]["councils_voted_together"], 1)
        self.assertEqual(partners[alias[c]]["councils_voted_apart"], 1)
        self.assertEqual(partners[alias[c]]["status"], "in the pact")
        # nobody else sees this player's pacts
        other = build_agent_episode_context(self.conn, actor_id=x, round_number=ROUND + 1, current_step="x")
        self.assertEqual(other["situation_brief"]["your_pacts"], [])
