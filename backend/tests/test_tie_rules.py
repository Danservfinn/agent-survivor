"""Tie rules (operator, 2026-11-25): talk, revote without the tied players,
then a unanimous deadlock decision, else rocks among everyone else."""

from __future__ import annotations

import json
from collections import Counter

from backend import turn_controller as tc
from backend.openrouter_client import AgentAction
from backend.tests.test_model_failures import ROUND, _Base


class TieRulesTest(_Base):
    def _first_ballot(self, tied: tuple[str, str]) -> None:
        a, b = tied
        for index, voter in enumerate(self.players):
            if voter in tied:
                continue
            tc._insert_vote(self.conn, ROUND, 0, voter, a if index % 2 else b)

    def _events(self, kind: str) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM StoryEvents WHERE kind = ? ORDER BY id", (kind,)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"] or "{}")} for row in rows]

    def test_revote_follows_a_talk_and_the_tied_players_do_not_vote(self) -> None:
        a, b, *others = self.players
        self._first_ballot((a, b))
        seen: list[tuple[str, str]] = []

        def action(**kwargs):
            actor, step = kwargs["actor"]["agent_id"], kwargs["step"]
            seen.append((step, actor))
            if step == "tie_discussion":
                return AgentAction(dialogue=f"{actor} speaks", inner_thought="", model_id="m")
            return AgentAction(dialogue="vote", inner_thought="", model_id="m", target_id=a)

        self._live(action)
        final = tc._maybe_run_revote(self.conn, ROUND, 1, Counter({a: 3, b: 3}))
        self.assertEqual(final.most_common(1)[0][0], a)
        revoters = {actor for step, actor in seen if step.startswith("revote_")}
        self.assertEqual(revoters, set(others))
        # everyone, tied players included, spoke before anyone revoted
        talk = [actor for step, actor in seen if step == "tie_discussion"]
        self.assertEqual(set(talk), set(self.players))
        first_revote = next(i for i, (step, _) in enumerate(seen) if step.startswith("revote_"))
        self.assertTrue(all(step == "tie_discussion" for step, _ in seen[:first_revote]))
        self.assertEqual(len(self._events("tie_discussion")[0]["payload"]["speaker_lines"]), len(self.players))

    def _deadlock(self, decide) -> Counter:
        a, b, *_ = self.players
        self._first_ballot((a, b))
        others = [p for p in self.players if p not in (a, b)]

        def action(**kwargs):
            actor, step = kwargs["actor"]["agent_id"], kwargs["step"]
            if step.startswith("revote_"):
                # split three and three again; a seventh voter spoils
                index = others.index(actor)
                target = a if index < 3 else b if index < 6 else None
                return AgentAction(dialogue="v", inner_thought="", model_id="m", target_id=target)
            if step == "deadlock_decision":
                return AgentAction(dialogue="d", inner_thought="", model_id="m", target_id=decide(actor))
            return AgentAction(dialogue=f"{actor} speaks", inner_thought="", model_id="m")

        self._live(action)
        return tc._maybe_run_revote(self.conn, ROUND, 1, Counter({a: 3, b: 3}))

    def test_a_unanimous_deadlock_sends_that_player_home(self) -> None:
        a, b, *_ = self.players
        final = self._deadlock(lambda actor: b)
        self.assertEqual(final.most_common(1)[0][0], b)
        self.assertEqual(self._events("deadlock_result")[0]["payload"]["eliminated_id"], b)
        self.assertFalse(self._events("rock_draw"))
        self.assertTrue(self._events("deadlock_discussion"))

    def test_one_refusal_means_rocks_and_the_tied_and_immune_are_safe(self) -> None:
        a, b, c, *_ = self.players
        self.conn.execute("UPDATE Agents SET has_immunity = 1 WHERE agent_id = ?", (self.players[-1],))
        final = self._deadlock(lambda actor: None if actor == c else a)
        out = final.most_common(1)[0][0]
        rocks = self._events("rock_draw")[0]["payload"]
        self.assertEqual(rocks["refused"], [c])
        self.assertNotIn(a, rocks["drew"])
        self.assertNotIn(b, rocks["drew"])
        self.assertNotIn(self.players[-1], rocks["drew"])
        self.assertIn(c, rocks["drew"])  # refusing puts you at risk
        self.assertIn(out, rocks["drew"])
        # the draw is fixed by the season seed and round, so a rerun repeats it
        self.assertEqual(rocks["eliminated_id"], out)

    def test_with_nobody_left_to_draw_the_challenge_record_decides(self) -> None:
        a, b, *others = self.players
        self.conn.execute(
            f"UPDATE Agents SET status = 'eliminated' WHERE agent_id IN ({','.join('?' * len(others))})", others
        )
        self._live(lambda **kwargs: AgentAction(dialogue="x", inner_thought="", model_id="m", target_id=None))
        final = tc._deadlock(self.conn, ROUND, 1, [a, b], Counter({a: 0, b: 0}))
        self.assertIn(final.most_common(1)[0][0], {a, b})
        self.assertTrue(self._events("tiebreak"))
