"""Tests for benchmark scoring against a fabricated season with known values."""

from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.benchmark_scoring import SURVIVOR_INDEX_VERSION, WEIGHTS, score_season  # noqa: E402


def _build_fixture_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE GameState (season_id INTEGER PRIMARY KEY, current_round INTEGER, phase TEXT,
            phase_step TEXT, turn_index INTEGER, is_merged INTEGER, winner TEXT, updated_at TEXT);
        CREATE TABLE Agents (agent_id TEXT PRIMARY KEY, pseudonym TEXT, model_id TEXT, archetype TEXT,
            team_id TEXT, status TEXT, has_immunity INTEGER, confessional_memory TEXT,
            action_points INTEGER, elimination_round INTEGER, portrait_seed TEXT);
        CREATE TABLE ChallengeAttempts (id INTEGER PRIMARY KEY, round INTEGER, puzzle_id TEXT,
            agent_id TEXT, tribe_id TEXT, provider TEXT, model_id TEXT, answer TEXT,
            is_correct INTEGER, response_ms INTEGER, attempt_order INTEGER, attempt_payload TEXT, created_at TEXT);
        CREATE TABLE Votes (id INTEGER PRIMARY KEY, round INTEGER, turn_index INTEGER,
            voter_id TEXT, target_id TEXT, revealed INTEGER, is_revote INTEGER, created_at TEXT);
        CREATE TABLE JuryVotes (id INTEGER PRIMARY KEY, round INTEGER, juror_id TEXT,
            finalist_id TEXT, target_id TEXT, created_at TEXT);
        CREATE TABLE AllianceMemberships (id INTEGER PRIMARY KEY, alliance_id TEXT,
            agent_id TEXT, status TEXT, loyalty INTEGER, created_at TEXT);
        CREATE TABLE StoryEvents (id INTEGER PRIMARY KEY, turn_id INTEGER, round INTEGER,
            sequence INTEGER, phase TEXT, kind TEXT, scene TEXT, shot TEXT, actor_ids TEXT,
            target_ids TEXT, visibility TEXT, title TEXT, dialogue TEXT, subtitle TEXT,
            inner_thought TEXT, trust_telemetry TEXT, duration_ms INTEGER, animation TEXT,
            spoiler_group TEXT, payload TEXT, created_at TEXT);
        """
    )
    # winner: Model W; eliminated: Model X round 9, Model Y round 8; Model Z finalist lost
    conn.execute("INSERT INTO GameState VALUES (1, 12, 'finale', 'complete', 40, 1, 'agent-w', 'now')")
    agents = [
        ("agent-w", "Model W", "org/w", "arch", "team", "active", 0, "", 0, None, "s"),
        ("agent-x", "Model X", "org/x", "arch", "team", "eliminated", 0, "", 0, 9, "s"),
        ("agent-y", "Model Y", "org/y", "arch", "team", "eliminated", 0, "", 0, 8, "s"),
        ("agent-z", "Model Z", "org/z", "arch", "team", "active", 0, "", 0, None, "s"),
    ]
    conn.executemany("INSERT INTO Agents VALUES (?,?,?,?,?,?,?,?,?,?,?)", agents)

    # challenges: rounds 7-12; W perfect 6/6, X 3/6, Y 2/6, Z 4/6
    def attempts_for(agent, n_correct, n_total):
        rows = []
        for i in range(n_total):
            rows.append((agent, 7 + i, "p%d" % i, "openrouter", "[[1]]" if i < n_correct else "[[0]]", 1 if i < n_correct else 0, 1000 + i))
        return rows

    data = (
        attempts_for("agent-w", 6, 6)
        + attempts_for("agent-x", 3, 6)
        + attempts_for("agent-y", 2, 6)
        + attempts_for("agent-z", 4, 6)
    )
    conn.executemany(
        "INSERT INTO ChallengeAttempts (agent_id, round, puzzle_id, provider, answer, is_correct, response_ms) VALUES (?,?,?,?,?,?,?)",
        data,
    )
    # votes: W voted every round alive (6); X voted rounds 7-8 (2 of 2 alive voting rounds? elim 9 -> alive 7,8 => 2)
    votes = [
        (7, "agent-w", "agent-y"), (8, "agent-w", "agent-y"), (9, "agent-w", "agent-x"),
        (10, "agent-w", "agent-x"), (11, "agent-w", "agent-z"), (12, "agent-w", "agent-z"),
        (7, "agent-x", "agent-y"), (8, "agent-x", "agent-y"),
        (7, "agent-y", "agent-x"), (8, "agent-y", "agent-x"),
        (7, "agent-z", "agent-y"), (8, "agent-z", "agent-y"), (9, "agent-z", "agent-x"),
        (10, "agent-z", "agent-x"), (11, "agent-z", "agent-x"), (12, "agent-z", "agent-w"),
    ]
    conn.executemany("INSERT INTO Votes (round, voter_id, target_id) VALUES (?,?,?)", votes)
    # jury: 3 jurors vote for W over Z
    jury = [(12, j, "agent-w", "agent-w") for j in ("agent-x", "agent-y", "agent-x")]
    # dedupe juror: use x, y only (2 votes)
    jury = [(12, "agent-x", "agent-w", "agent-w"), (12, "agent-y", "agent-w", "agent-w")]
    conn.executemany("INSERT INTO JuryVotes (round, juror_id, finalist_id, target_id) VALUES (?,?,?,?)", jury)
    # alliances: W in 2, X in 1, Y 0, Z 1
    conn.executemany(
        "INSERT INTO AllianceMemberships (alliance_id, agent_id, status) VALUES (?,?,?)",
        [("a1", "agent-w", "active"), ("a2", "agent-w", "active"), ("a1", "agent-x", "active"), ("a3", "agent-z", "active")],
    )
    # strategic scores in event payloads: W 0.8 avg, X 0.4, Y 0.2, Z 0.6
    strat = [
        ("agent-w", 0.8), ("agent-w", 0.8), ("agent-x", 0.4), ("agent-y", 0.2), ("agent-z", 0.6),
    ]
    for i, (aid, score) in enumerate(strat):
        conn.execute(
            "INSERT INTO StoryEvents (round, sequence, kind, actor_ids, payload) VALUES (?,?,?,?,?)",
            (7, i, "conversation", '["%s"]' % aid, '{"strategic_score": %s}' % score),
        )
    conn.commit()
    conn.close()


class ScoringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Path("/private/tmp/survivor_scoring_fixture.db")
        if self.db.exists():
            self.db.unlink()
        _build_fixture_db(self.db)
        self.report = score_season(self.db)

    def test_version_and_weights_frozen(self) -> None:
        self.assertEqual(SURVIVOR_INDEX_VERSION, 2)
        self.assertAlmostEqual(sum(WEIGHTS.values()), 1.0)

    def test_rows_cover_all_agents(self) -> None:
        self.assertEqual(len(self.report["rows"]), 4)

    def test_challenge_accuracy(self) -> None:
        by_model = {r["model"]: r for r in self.report["rows"]}
        self.assertEqual(by_model["Model W"]["challenge_accuracy"], 1.0)
        self.assertEqual(by_model["Model X"]["challenge_accuracy"], 0.5)
        self.assertEqual(by_model["Model Y"]["challenge_accuracy"], round(2 / 6, 4))
        self.assertEqual(by_model["Model Z"]["challenge_accuracy"], round(4 / 6, 4))

    def test_winner_places_first(self) -> None:
        top = self.report["rows"][0]
        self.assertTrue(top["is_winner"])
        self.assertEqual(top["place"], 1)

    def test_index_bounds(self) -> None:
        for row in self.report["rows"]:
            self.assertGreaterEqual(row["survivor_index"], 0.0)
            self.assertLessEqual(row["survivor_index"], 100.0)

    def test_index_ordering_matches_skill_gap(self) -> None:
        by_model = {r["model"]: r for r in self.report["rows"]}
        # W dominates every component; must rank above X, Y, Z
        w = by_model["Model W"]["survivor_index"]
        for other in ("Model X", "Model Y", "Model Z"):
            self.assertGreater(w, by_model[other]["survivor_index"])

    def test_live_attempt_count(self) -> None:
        self.assertEqual(self.report["totals"]["live_attempts"], 24)
        self.assertEqual(self.report["totals"]["attempts"], 24)


if __name__ == "__main__":
    unittest.main()
