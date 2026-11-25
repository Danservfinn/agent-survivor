"""Paid model-pick entries: pricing, payment, queue and what happens when a season ends."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from backend import entries_store as store
from backend import entry_payments, entry_pricing, entry_queue


def _estimate(total: float = 31.86, **extra) -> dict:
    return {"model_id": "x-ai/grok-4.7", "entry_priced": True, "variable_price": False, "house_usd": 25.0, "entry_usd": total - 25.0,
            "total_usd": total, "basis": "estimated", "season_id": None, "replaced_model_id": "x-ai/grok-4.7",
            "replaced_because": "same lab", "calls_per_seat": 60, **extra}


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.env = patch.dict(os.environ, {"SURVIVOR_ENTRIES_DB": str(self.dir / "entries.db")})
        self.env.start()
        self.seasons = self.dir / "seasons"
        self.seasons.mkdir()
        self.seasons_patch = patch("backend.live_season.SEASONS_DIR", self.seasons)
        self.seasons_patch.start()

    def tearDown(self) -> None:
        self.seasons_patch.stop()
        self.env.stop()

    def entry(self, status: str = "queued", **fields) -> dict:
        e = store.create(name="Night Owl", model_id="x-ai/grok-4.7", price_cents=6500, cap_cents=5525, estimate=_estimate(), ip="1.2.3.4", mode="live")
        store.update(e["entry_id"], status=status, stripe_payment_intent="pi_1", paid_at=time.time(), **fields)
        return store.get(e["entry_id"])


class PricingTest(unittest.TestCase):
    def test_twice_the_cost_rounded_up_to_five_dollars_never_below_fifty(self) -> None:
        self.assertEqual(entry_pricing.price_cents_for(10.0), 5000)
        self.assertEqual(entry_pricing.price_cents_for(29.39), 6000)
        self.assertEqual(entry_pricing.price_cents_for(31.86), 6500)
        self.assertEqual(entry_pricing.price_cents_for(39.88), 8000)
        self.assertEqual(entry_pricing.price_cents_for(30.0), 6000)
        self.assertEqual(entry_pricing.cap_cents_for(6500), 5525)

    def test_unpriced_and_variable_models_are_refused(self) -> None:
        with patch("backend.live_cost.estimate_entry_season_cost", return_value=_estimate(entry_priced=False)):
            with self.assertRaisesRegex(entry_pricing.QuoteError, "not in OpenRouter's catalog"):
                entry_pricing.quote("x-ai/grok-4.7", check=False)
        with patch("backend.live_cost.estimate_entry_season_cost", return_value=_estimate(variable_price=True)):
            with self.assertRaisesRegex(entry_pricing.QuoteError, "routers"):
                entry_pricing.quote("openrouter/auto", check=False)
        with self.assertRaises(entry_pricing.QuoteError):
            entry_pricing.quote("not a model id", check=False)

    def test_a_model_that_fails_the_live_call_is_refused(self) -> None:
        with patch("backend.live_cost.estimate_entry_season_cost", return_value=_estimate()), patch.object(
            entry_pricing, "live_check", return_value={"ok": False, "error": "the model did not answer in the required format"}
        ):
            with self.assertRaisesRegex(entry_pricing.QuoteError, "did not answer a test call"):
                entry_pricing.quote("x-ai/grok-4.7")

    def test_a_good_quote(self) -> None:
        with patch("backend.live_cost.estimate_entry_season_cost", return_value=_estimate()), patch.object(entry_pricing, "live_check", return_value={"ok": True, "error": None}):
            q = entry_pricing.quote("x-ai/grok-4.7")
        self.assertEqual((q["price_cents"], q["cap_cents"]), (6500, 5525))
        self.assertEqual(q["estimate"]["replaced_because"], "same lab")


class WebhookSignatureTest(unittest.TestCase):
    def sign(self, raw: bytes, secret: str = "whsec_x", stamp: int | None = None) -> str:
        stamp = stamp or int(time.time())
        sig = hmac.new(secret.encode(), f"{stamp}.".encode() + raw, hashlib.sha256).hexdigest()
        return f"t={stamp},v1={sig}"

    def test_good_bad_and_stale(self) -> None:
        raw = b'{"id": "evt_1", "type": "checkout.session.completed"}'
        self.assertEqual(entry_payments.verify_webhook(raw, self.sign(raw), secret="whsec_x")["id"], "evt_1")
        for header in (self.sign(raw, "whsec_other"), self.sign(raw, stamp=int(time.time()) - 3600), "t=1,v1=00", None):
            with self.assertRaises(entry_payments.PaymentError):
                entry_payments.verify_webhook(raw, header, secret="whsec_x")
        with self.assertRaises(entry_payments.PaymentError):
            entry_payments.verify_webhook(raw.replace(b"evt_1", b"evt_2"), self.sign(raw), secret="whsec_x")

    def test_params_are_flattened_the_way_stripe_reads_them(self) -> None:
        flat = dict(entry_payments._flatten({"line_items": [{"quantity": 1, "price_data": {"unit_amount": 6500}}], "metadata": {"k": "v"}}))
        self.assertEqual(flat["line_items[0][price_data][unit_amount]"], "6500")
        self.assertEqual(flat["metadata[k]"], "v")

    def test_keys_come_from_their_own_file(self) -> None:
        path = Path(tempfile.mkdtemp()) / "stripe.env"
        path.write_text("STRIPE_TEST_SECRET_KEY=rk_test_abc\nSTRIPE_TEST_WEBHOOK_SECRET=whsec_abc\nSTRIPE_API_VERSION=2026-11-18.endive\n")
        with patch.dict(os.environ, {"SURVIVOR_STRIPE_ENV": str(path)}):
            cfg = entry_payments.config()
            self.assertEqual((cfg["mode"], cfg["key"]), ("test", "rk_test_abc"))
            self.assertNotIn("STRIPE_TEST_SECRET_KEY", os.environ)
            with patch.dict(os.environ, {"STRIPE_MODE": "live"}):
                self.assertIsNone(entry_payments.config())  # no live key yet


class StoreTest(_Base):
    def test_moves_happen_once(self) -> None:
        e = self.entry("awaiting_payment")
        self.assertTrue(store.move(e["entry_id"], ("awaiting_payment",), "queued"))
        self.assertFalse(store.move(e["entry_id"], ("awaiting_payment",), "queued"))
        self.assertEqual(store.queue_position(e["entry_id"]), 1)
        self.assertFalse(store.seen_event("evt_1", "x"))
        self.assertTrue(store.seen_event("evt_1", "x"))
        self.assertEqual(os.stat(store.db_path()).st_mode & 0o777, 0o600)


class QueueTest(_Base):
    def scorecard(self, entry: dict, **card) -> Path:
        folder = self.seasons / "20261126-100000-lab_mix_entry"
        folder.mkdir(exist_ok=True)
        (folder / "entry.json").write_text(json.dumps({"entry_id": entry["entry_id"]}))
        (folder / "scorecard.json").write_text(json.dumps(card))
        return folder

    def test_starts_the_next_entry_with_a_private_spec(self) -> None:
        e = self.entry()
        launched = []
        with patch.object(entry_queue, "recheck", return_value=None):
            result = entry_queue.tick(launch=launched.append, busy=lambda: False)
        self.assertEqual(result, f"started {e['entry_id']}")
        spec = launched[0]
        self.assertEqual(os.stat(spec).st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(spec.read_text())["cap_cents"], 5525)
        self.assertEqual(store.get(e["entry_id"])["status"], "running")

    def test_a_test_mode_entry_never_starts_a_real_season_unless_allowed(self) -> None:
        e = store.create(name="Night Owl", model_id="x-ai/grok-4.7", price_cents=6500, cap_cents=5525, estimate=_estimate(), ip="1.2.3.4", mode="test")
        store.update(e["entry_id"], status="queued")
        self.assertEqual(entry_queue.tick(launch=lambda s: self.fail("must not start"), busy=lambda: False), "idle")
        store.set_setting("allow_test_runs", "1")
        with patch.object(entry_queue, "recheck", return_value=None):
            self.assertEqual(entry_queue.tick(launch=lambda s: None, busy=lambda: False), f"started {e['entry_id']}")

    def test_entries_are_closed_until_an_operator_opens_them(self) -> None:
        self.assertFalse(store.is_open())
        store.set_setting("open", "1")
        self.assertTrue(store.is_open())
        store.set_setting("paused", "1")
        self.assertFalse(store.is_open())

    def test_waits_while_a_season_runs_or_entries_are_paused(self) -> None:
        self.entry()
        self.assertEqual(entry_queue.tick(launch=lambda s: None, busy=lambda: True), "busy")
        store.set_setting("paused", "1")
        self.assertEqual(entry_queue.tick(launch=lambda s: None, busy=lambda: False), "paused")

    def test_a_finished_season_is_settled(self) -> None:
        e = self.entry("running")
        folder = self.scorecard(e, finished=True)
        self.assertIn("finished", entry_queue.tick(busy=lambda: False))
        self.assertEqual(store.get(e["entry_id"])["season_id"], folder.name)

    def test_our_failures_are_refunded(self) -> None:
        for card, words in (({"interrupted": "step"}, "provider"), ({"caps": {"cap_reached": "max_estimated_cost_cents"}}, "spending limit")):
            e = self.entry("running")
            self.scorecard(e, **card)
            with patch.object(entry_payments, "refund") as refund:
                entry_queue.tick(busy=lambda: False)
            refund.assert_called_once()
            after = store.get(e["entry_id"])
            self.assertEqual(after["status"], "refunded")
            self.assertIn(words, after["note"])

    def test_a_crash_is_retried_once_then_refunded(self) -> None:
        e = self.entry("running", attempts=1)
        self.assertIn("requeued", entry_queue.tick(busy=lambda: False))
        store.update(e["entry_id"], status="running", attempts=2)
        with patch.object(entry_payments, "refund"):
            self.assertIn("refunded", entry_queue.tick(busy=lambda: False))

    def test_a_model_that_got_dearer_or_vanished_is_refunded_before_it_plays(self) -> None:
        e = self.entry()
        dearer = {"price_cents": 9000}
        with patch("backend.entry_pricing.quote", return_value=dearer), patch.object(entry_payments, "refund") as refund:
            result = entry_queue.tick(launch=lambda s: self.fail("must not start"), busy=lambda: False)
        self.assertIn("price rose", result)
        refund.assert_called_once()
        self.assertEqual(store.get(e["entry_id"])["status"], "refunded")

    def test_a_failed_refund_stays_visible(self) -> None:
        e = self.entry("running", attempts=2)
        with patch.object(entry_payments, "refund", side_effect=entry_payments.PaymentError("no network")):
            entry_queue.tick(busy=lambda: False)
        after = store.get(e["entry_id"])
        self.assertEqual(after["status"], "running")
        self.assertIn("refund pending", after["note"])


class ApiTest(_Base):
    def setUp(self) -> None:
        super().setUp()
        from backend import api

        self.api = api
        api._quote_hits.clear()
        store.set_setting("open", "1")

    def request(self, ip: str = "9.9.9.9"):
        return type("R", (), {"headers": {"cf-connecting-ip": ip}, "client": None})()

    def test_create_then_pay_then_status(self) -> None:
        quoted = {"model_id": "x-ai/grok-4.7", "price_cents": 6500, "cap_cents": 5525, "estimate": _estimate()}
        with patch.object(entry_payments, "enabled", return_value=True), patch("backend.entry_pricing.quote", return_value=quoted), patch.object(
            entry_payments, "create_checkout", return_value={"id": "cs_1", "url": "https://checkout.stripe.com/c/cs_1"}
        ):
            made = self.api.api_entries_create(self.request(), {"name": "Night Owl", "model_id": "x-ai/grok-4.7"})
        self.assertEqual(made["checkout_url"], "https://checkout.stripe.com/c/cs_1")
        token = made["status_url"].split("t=")[1]
        entry = store.by_token(token)
        self.assertEqual(entry["status"], "awaiting_payment")
        session = {"id": "cs_1", "payment_status": "paid", "payment_intent": "pi_9", "metadata": {"llm_survivor_entry": entry["entry_id"]}}
        self.api._fulfil(session)
        self.api._fulfil(session)  # Stripe may send it twice
        status = self.api.api_entries_status(token)
        self.assertEqual((status["status"], status["queue_position"]), ("queued", 1))
        self.assertEqual(store.by_token(token)["stripe_payment_intent"], "pi_9")

    def test_a_session_for_another_entry_or_unpaid_does_nothing(self) -> None:
        e = self.entry("awaiting_payment", stripe_session_id="cs_mine")
        self.api._fulfil({"id": "cs_other", "payment_status": "paid", "metadata": {"llm_survivor_entry": e["entry_id"]}})
        self.api._fulfil({"id": "cs_mine", "payment_status": "unpaid", "metadata": {"llm_survivor_entry": e["entry_id"]}})
        self.assertEqual(store.get(e["entry_id"])["status"], "awaiting_payment")

    def test_bad_names_and_closed_entries(self) -> None:
        with patch.object(entry_payments, "enabled", return_value=True):
            with self.assertRaises(HTTPException) as caught:
                self.api.api_entries_create(self.request(), {"name": "Ash", "model_id": "x-ai/grok-4.7"})
            self.assertEqual(caught.exception.status_code, 400)
        with patch.object(entry_payments, "enabled", return_value=False):
            with self.assertRaises(HTTPException) as caught:
                self.api.api_entries_create(self.request(), {"name": "Night Owl", "model_id": "x-ai/grok-4.7"})
            self.assertEqual(caught.exception.status_code, 503)

    def test_quotes_are_rate_limited(self) -> None:
        with patch("backend.entry_pricing.quote", return_value={"model_id": "m/x", "price_cents": 5000, "cap_cents": 4250, "estimate": {}}):
            for _ in range(self.api.QUOTES_PER_10_MIN):
                self.api.api_entries_quote(self.request("5.5.5.5"), {"model_id": "m/x"})
            with self.assertRaises(HTTPException) as caught:
                self.api.api_entries_quote(self.request("5.5.5.5"), {"model_id": "m/x"})
        self.assertEqual(caught.exception.status_code, 429)

    def test_the_public_button_waits_for_paid_entries(self) -> None:
        self.entry("queued")
        with patch.object(self.api, "_detached_runner_alive", return_value=False):
            with self.assertRaises(HTTPException) as caught:
                self.api.api_live_season_public_run(None)
        self.assertEqual(caught.exception.status_code, 409)

    def test_the_webhook_ignores_other_products_and_repeats(self) -> None:
        e = self.entry("awaiting_payment", stripe_session_id="cs_w")
        events = [
            {"id": "evt_parse", "type": "checkout.session.completed", "data": {"object": {"id": "cs_parse", "payment_status": "paid", "metadata": {"tier": "pro"}}}},
            {"id": "evt_ours", "type": "checkout.session.completed", "data": {"object": {"id": "cs_w", "payment_status": "paid", "payment_intent": "pi_w", "metadata": {"llm_survivor_entry": e["entry_id"]}}}},
        ]

        class Req:
            def __init__(self, body):
                self.body_bytes = json.dumps(body).encode()
                self.headers = {"stripe-signature": "ignored"}

            async def body(self):
                return self.body_bytes

        import asyncio

        with patch.object(entry_payments, "verify_webhook", side_effect=lambda raw, header: json.loads(raw)):
            for event in events + events:
                self.assertEqual(asyncio.run(self.api.api_entries_stripe_webhook(Req(event))), {"ok": True})
        self.assertEqual(store.get(e["entry_id"])["status"], "queued")


class EntrySeasonsStayApartTest(unittest.TestCase):
    def test_entry_seasons_are_never_the_latest_or_on_the_house_board(self) -> None:
        from backend import live_season, season_report

        base = Path(tempfile.mkdtemp())
        house, entry = base / "20261126-090000-lab_mix_openrouter", base / "20261126-100000-lab_mix_entry"
        for folder in (house, entry):
            folder.mkdir()
            (folder / "scorecard.json").write_text(json.dumps({"ok": True, "season": folder.name}))
        (entry / "entry.json").write_text(json.dumps({"entry_id": "e_1", "name": "Night Owl", "model_id": "x-ai/grok-4.7", "secret": "no"}))
        with patch.object(live_season, "SEASONS_DIR", base):
            self.assertEqual(live_season.latest_report()["season"], house.name)
        self.assertEqual(season_report.entry_record(entry), {"entry_id": "e_1", "name": "Night Owl", "model_id": "x-ai/grok-4.7"})
        self.assertIsNone(season_report.entry_record(house))
        with patch.object(season_report, "_season_brief", side_effect=lambda d: {"season_id": d.name}):
            self.assertEqual(season_report.reportable_season_dir(base), house)


class RunnerSpecTest(unittest.TestCase):
    def test_the_runner_reads_and_deletes_the_spec(self) -> None:
        import importlib.util
        import sys

        root = Path(__file__).resolve().parents[2]
        spec_file = Path(tempfile.mkdtemp()) / "e_1.json"
        spec_file.write_text(json.dumps({"entry_id": "e_1", "name": "Night Owl", "model_id": "x-ai/grok-4.7", "cap_cents": 5525}))
        loader = importlib.util.spec_from_file_location("run_public_season_under_test", root / "scripts" / "run_public_season.py")
        module = importlib.util.module_from_spec(loader)
        # importing the runner loads the production secrets file and switches the provider to
        # OpenRouter; keep that out of every other test, which must never make a live call
        with patch.dict(os.environ, {}), patch.object(module, "__name__", module.__name__):
            with patch("pathlib.Path.home", return_value=Path(tempfile.mkdtemp())):
                loader.loader.exec_module(module)
        with patch.object(sys, "argv", ["run_public_season.py", "--entry-spec", str(spec_file)]):
            self.assertEqual(module._entry_spec()["cap_cents"], 5525)
        self.assertFalse(spec_file.exists())


if __name__ == "__main__":
    unittest.main()
