"""Stripe for paid entries, with the standard library only.

Keys live in their own file, ~/.kublai/secrets/llm-survivor-stripe.env (0600),
read only here. They are never put in os.environ, so the season runner, which
inherits the API's environment, never holds them. The account is LLM
Survivor's own (acct_1Ufn5jID3jlvPtO2), separate from Parse's.

STRIPE_MODE in that file (or the env) picks "test" (default) or "live".
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

API = "https://api.stripe.com/v1"
MARKER = "llm_survivor_entry"  # set on every session and payment, so other products' events are ignored
SESSION_MINUTES = 30
WEBHOOK_TOLERANCE_S = 300


class PaymentError(RuntimeError):
    pass


def _secrets_path() -> Path:
    return Path(os.environ.get("SURVIVOR_STRIPE_ENV") or Path.home() / ".kublai" / "secrets" / "llm-survivor-stripe.env")


def config() -> dict[str, str] | None:
    """The mode's key, webhook secret and API version, or None when not set up."""
    path = _secrets_path()
    if not path.exists():
        return None
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    mode = (os.environ.get("STRIPE_MODE") or values.get("STRIPE_MODE") or "test").lower()
    key = values.get(f"STRIPE_{mode.upper()}_SECRET_KEY")
    secret = values.get(f"STRIPE_{mode.upper()}_WEBHOOK_SECRET")
    if mode not in ("test", "live") or not key or not secret:
        return None
    return {"mode": mode, "key": key, "webhook_secret": secret, "version": values.get("STRIPE_API_VERSION", ""), "account": values.get("STRIPE_ACCOUNT_ID", "")}


def enabled() -> bool:
    return config() is not None


def _flatten(params: dict[str, Any], prefix: str = "") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for key, value in params.items():
        name = f"{prefix}[{key}]" if prefix else key
        if isinstance(value, dict):
            out += _flatten(value, name)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, dict):
                    out += _flatten(item, f"{name}[{index}]")
                else:
                    out.append((f"{name}[{index}]", str(item)))
        elif value is not None:
            out.append((name, "true" if value is True else "false" if value is False else str(value)))
    return out


def _call(method: str, path: str, params: dict[str, Any] | None = None, *, idempotency_key: str | None = None) -> dict[str, Any]:
    cfg = config()
    if cfg is None:
        raise PaymentError("payments are not set up")
    data = urllib.parse.urlencode(_flatten(params or {})).encode() if method == "POST" else None
    url = f"{API}{path}"
    if method == "GET" and params:
        url += "?" + urllib.parse.urlencode(_flatten(params))
    headers = {"Authorization": f"Bearer {cfg['key']}"}
    if cfg["version"]:
        headers["Stripe-Version"] = cfg["version"]
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read().decode()).get("error", {}).get("message", "")
        except Exception:  # noqa: BLE001
            message = ""
        raise PaymentError(f"Stripe refused the request (HTTP {exc.code}){': ' + message if message else ''}") from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise PaymentError(f"could not reach Stripe: {type(exc).__name__}") from None


def create_checkout(entry: dict[str, Any], *, site: str) -> dict[str, Any]:
    """A hosted Checkout Session for one entry. Returns {id, url}."""
    status_url = f"{site}/entry?t={entry['token']}"
    session = _call(
        "POST",
        "/checkout/sessions",
        {
            "mode": "payment",
            "client_reference_id": entry["entry_id"],
            "success_url": status_url + "&paid=1",
            "cancel_url": f"{site}/enter?cancelled=1",
            "expires_at": int(time.time()) + SESSION_MINUTES * 60 + 60,
            "line_items": [
                {
                    "quantity": 1,
                    "price_data": {
                        "currency": "usd",
                        "unit_amount": int(entry["price_cents"]),
                        "product_data": {
                            "name": "LLM Survivor season entry",
                            "description": f"{entry['name']} plays one full season as {entry['model_id']}. Results are public.",
                        },
                    },
                }
            ],
            "metadata": {MARKER: entry["entry_id"]},
            "payment_intent_data": {"metadata": {MARKER: entry["entry_id"]}, "description": f"LLM Survivor entry {entry['entry_id']}: {status_url}"},
            "custom_text": {"submit": {"message": f"Your status page: {status_url}"}},
        },
        idempotency_key=f"checkout-{entry['entry_id']}",
    )
    return {"id": session["id"], "url": session["url"]}


def get_session(session_id: str) -> dict[str, Any]:
    return _call("GET", f"/checkout/sessions/{urllib.parse.quote(session_id)}")


def refund(entry: dict[str, Any], reason: str) -> dict[str, Any]:
    if not entry.get("stripe_payment_intent"):
        raise PaymentError("no payment to refund")
    return _call(
        "POST",
        "/refunds",
        {"payment_intent": entry["stripe_payment_intent"], "metadata": {MARKER: entry["entry_id"], "reason": reason[:200]}},
        idempotency_key=f"refund-{entry['entry_id']}",
    )


def verify_webhook(raw: bytes, header: str | None, *, secret: str | None = None, now: float | None = None) -> dict[str, Any]:
    """The event, if `header` (Stripe-Signature) signs `raw` with our secret and is recent; else PaymentError."""
    if secret is None:
        cfg = config()
        if cfg is None:
            raise PaymentError("payments are not set up")
        secret = cfg["webhook_secret"]
    parts: dict[str, list[str]] = {}
    for item in (header or "").split(","):
        key, _, value = item.strip().partition("=")
        parts.setdefault(key, []).append(value)
    try:
        stamp = int(parts.get("t", [""])[0])
    except ValueError:
        raise PaymentError("bad signature header") from None
    if abs((now or time.time()) - stamp) > WEBHOOK_TOLERANCE_S:
        raise PaymentError("stale signature")
    expected = hmac.new(secret.encode(), f"{stamp}.".encode() + raw, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, sig) for sig in parts.get("v1", [])):
        raise PaymentError("bad signature")
    return json.loads(raw.decode())


def is_ours(obj: dict[str, Any]) -> bool:
    return bool((obj.get("metadata") or {}).get(MARKER))
