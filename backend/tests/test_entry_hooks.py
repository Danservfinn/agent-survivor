"""Engine hooks for paid entries: a buyer's OpenRouter model takes one
lab_mix seat, and the season's cost is estimated before payment."""

from __future__ import annotations

import gzip
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import database, live_cost, live_season
from backend.model_rosters import LAB_MIX_OPENROUTER_MODELS, EntryError, entry_agents, validate_entry

TABLE = {m["model_id"]: {"prompt_per_m": 1.0, "completion_per_m": 4.0} for m in LAB_MIX_OPENROUTER_MODELS}
TABLE["acme/big-model"] = {"prompt_per_m": 10.0, "completion_per_m": 40.0}
TABLE["anthropic/claude-opus-5.5"] = {"prompt_per_m": 1.0, "completion_per_m": 4.0}


class ValidateTest(unittest.TestCase):
    def test_names_and_ids(self) -> None:
        self.assertEqual(validate_entry({"name": " Rook ", "model_id": "acme/big-model", "entry_id": "e1"})["name"], "Rook")
        for bad in (
            {"name": "", "model_id": "acme/x"},
            {"name": "x" * 21, "model_id": "acme/x"},
            {"name": "Ash", "model_id": "acme/x"},  # a player alias
            {"name": "Claude Sonnet 5.5", "model_id": "acme/x"},  # a house model
            {"name": "Rook<script>", "model_id": "acme/x"},
            {"name": "Rook", "model_id": "no-slash"},
            {"name": "Rook", "model_id": "https://evil/x"},
        ):
            with self.assertRaises(EntryError, msg=bad):
                validate_entry(bad)


class SeatTest(unittest.TestCase):
    def test_a_lab_already_in_the_roster_gives_up_its_own_seat(self) -> None:
        agents, record = entry_agents({"name": "Rook", "model_id": "anthropic/claude-opus-5.5"}, 7)
        self.assertEqual(record["replaced_because"], "same lab")
        self.assertEqual(record["replaced_model_id"], "anthropic/claude-sonnet-5.5")
        models = [a["model_id"] for a in agents]
        self.assertIn("anthropic/claude-opus-5.5", models)
        self.assertNotIn("anthropic/claude-sonnet-5.5", models)
        self.assertEqual(len(agents), len(LAB_MIX_OPENROUTER_MODELS))

    def test_another_lab_takes_a_seat_chosen_by_the_seed(self) -> None:
        first = entry_agents({"name": "Rook", "model_id": "acme/big-model"}, 7)[1]
        self.assertEqual(first["replaced_because"], "seed")
        self.assertEqual(first, entry_agents({"name": "Rook", "model_id": "acme/big-model"}, 7)[1])
        seats = {entry_agents({"name": "Rook", "model_id": "acme/big-model"}, s)[1]["replaced_model_id"] for s in range(40)}
        self.assertGreater(len(seats), 3)  # it rotates

    def test_seeding_names_the_seat_and_entry_json_finds_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"SURVIVOR_SEASON_SEED": "99"}):
            db = Path(tmp) / "season.db"
            original = database.DATABASE_PATH
            database.DATABASE_PATH = db
            try:
                agents, record = entry_agents({"name": "Rook", "model_id": "acme/big-model", "entry_id": "e1"}, 99)
                database.seed_demo(reset=True, path=db, roster_preset="lab_mix_openrouter", agents=agents)
                entry = live_season._entry_record(db, record, 99)
            finally:
                database.DATABASE_PATH = original
            conn = sqlite3.connect(db)
            row = conn.execute("SELECT display_name, model_id, pseudonym FROM Agents WHERE agent_id = ?", (entry["agent_id"],)).fetchone()
            conn.close()
        self.assertEqual(row[:2], ("Rook", "acme/big-model"))
        self.assertEqual(entry["alias"], row[2])
        self.assertEqual(entry["maker"], "Community")
        self.assertNotIn("token", json.dumps(entry).lower())

    def test_the_runner_refuses_a_bad_entry_or_another_roster(self) -> None:
        self.assertIn("lab_mix_openrouter", live_season.run_live_season(roster="cheap_recent_openrouter", entry={"name": "Rook", "model_id": "acme/x"})["error"])
        self.assertIn("entry:", live_season.run_live_season(roster="lab_mix_openrouter", entry={"name": "Ash", "model_id": "acme/x"})["error"])


class CostTest(unittest.TestCase):
    def _estimate(self, model_id: str, seasons_dir: Path) -> dict:
        with patch.object(live_cost, "get_pricing_table", return_value=TABLE):
            return live_cost.estimate_entry_season_cost(model_id, seasons_dir=seasons_dir)

    def test_estimated_from_the_profile_when_no_season_was_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cheap = self._estimate("anthropic/claude-opus-5.5", Path(tmp))
            dear = self._estimate("acme/big-model", Path(tmp))
            unknown = self._estimate("acme/unknown", Path(tmp))
        self.assertEqual(cheap["basis"], "estimated")
        self.assertIsNone(cheap["season_id"])
        self.assertEqual(cheap["replaced_model_id"], "anthropic/claude-sonnet-5.5")
        self.assertGreater(dear["entry_usd"], cheap["entry_usd"] * 5)  # ten times the price
        self.assertAlmostEqual(dear["total_usd"], dear["house_usd"] + dear["entry_usd"], places=1)
        self.assertFalse(unknown["entry_priced"])
        self.assertIsNone(unknown["total_usd"])

    def test_recorded_from_the_newest_finished_house_season(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            season = Path(tmp) / "20261126-100000-lab_mix_openrouter"
            season.mkdir()
            (season / "scorecard.json").write_text(json.dumps({"finished": True}))
            lines = [
                {"model": "x", "player": p, "messages": [{"role": "user", "content": "x" * 4000}], "reply": {"dialogue": "y" * 400}}
                for p in ("Ash", "Ash", "Birch")
            ] + [{"model": "host", "player": None, "messages": [{"role": "user", "content": "h" * 400}], "reply": {"t": "z"}}]
            with gzip.open(season / "prompts.jsonl.gz", "wt") as handle:
                handle.write("\n".join(json.dumps(line) for line in lines))
            unfinished = Path(tmp) / "20261127-100000-lab_mix_openrouter"
            unfinished.mkdir()
            (unfinished / "scorecard.json").write_text(json.dumps({"finished": False}))
            estimate = self._estimate("acme/big-model", Path(tmp))
        self.assertEqual(estimate["basis"], "recorded")
        self.assertEqual(estimate["season_id"], season.name)
        self.assertEqual(estimate["calls_per_seat"], 2)  # 3 player calls over 2 seats, rounded
        self.assertGreater(estimate["entry_usd"], 0)


if __name__ == "__main__":
    unittest.main()


class VariablePriceTest(unittest.TestCase):
    def test_a_router_listed_at_minus_one_is_priced_high_not_negative(self) -> None:
        raw = {"typesafe/jev-router": {"prompt_per_m": -1_000_000.0, "completion_per_m": -1_000_000.0, "max_out": 128000}}
        with patch.object(live_cost, "_load_cache", return_value=raw):
            table = live_cost.get_pricing_table()
        entry = table["typesafe/jev-router"]
        self.assertEqual((entry["prompt_per_m"], entry["completion_per_m"]), live_cost.MEASURED_VARIABLE_PRICES_PER_M["typesafe/jev-router"])
        self.assertTrue(entry["variable"])
        raw = {"openrouter/auto": {"prompt_per_m": -1_000_000.0, "completion_per_m": -1_000_000.0}}
        with patch.object(live_cost, "_load_cache", return_value=raw):
            unmeasured = live_cost.get_pricing_table()["openrouter/auto"]
        self.assertEqual((unmeasured["prompt_per_m"], unmeasured["completion_per_m"]), live_cost.VARIABLE_PRICE_PER_M)
        self.assertGreater(live_cost.estimate_call_cost_usd("typesafe/jev-router", input_tokens=1000, output_tokens=1000, table=table), 0)
