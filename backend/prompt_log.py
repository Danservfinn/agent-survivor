"""Record the exact prompt and reply of every model call in a season.

The Methodology page shows readers and builders the real text a model is
given and what it answered. Replies were always stored; prompts were not.
While a season runs, `recording(season_dir)` wraps
openrouter_client._post_chat_completion, the one function every model call
passes through, and appends one JSON line per call to prompts.jsonl in the
season folder. At the end the file is compressed to prompts.jsonl.gz.

What is kept: the model, the messages sent, the reply with its `think` field
removed (players are told their private reasoning is never kept), the finish
reason, the model's answering time, and the provider's usage record (token
counts and cost, numbers only) with the call id. A reply that cannot be parsed is not
kept, only its length, because it could hold the private reasoning. Any API
key that appears is replaced with [redacted]. Nothing is written to the season
database, so the game itself is unchanged by recording.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

LOG_NAME = "prompts.jsonl"
GZ_NAME = "prompts.jsonl.gz"

_lock = threading.Lock()
_STEP = re.compile(r"^This step: (.+)$", re.M)
_PLAYER = re.compile(r"\bYou are ([A-Z][a-z]+), a player\b")
_SECRET_ENV = ("OPENROUTER_API_KEY", "ZAI_API_KEY", "GLM_API_KEY")
# key-shaped strings, whatever their source
KEY_SHAPES = re.compile(rb"sk-or-v1-[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|sk-proj-[A-Za-z0-9_-]{20,}|Bearer [A-Za-z0-9._-]{20,}")


def _scrub(text: str) -> str:
    for name in _SECRET_ENV:
        secret = os.environ.get(name)
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


def _clean_reply(content: Any) -> dict[str, Any]:
    """The reply without its private reasoning, or only its length if it cannot be read."""
    from .openrouter_client import _parse_json_content

    if not isinstance(content, str) or not content.strip():
        return {"reply": None}
    try:
        parsed = _parse_json_content(content)
    except Exception:  # noqa: BLE001 - any unreadable reply is withheld, not raised
        return {"reply": None, "reply_unreadable_chars": len(content)}
    if not isinstance(parsed, dict):
        return {"reply": None, "reply_unreadable_chars": len(content)}
    parsed.pop("think", None)
    return {"reply": parsed}


def _labels(messages: list[dict[str, Any]]) -> dict[str, str | None]:
    text = "\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "user")
    step = _STEP.search(text)
    player = _PLAYER.search(text)
    return {"step": step.group(1).strip() if step else None, "player": player.group(1) if player else None}


def _entry(payload: dict[str, Any], response: dict[str, Any] | None, error: BaseException | None) -> dict[str, Any]:
    messages = payload.get("messages") or []
    entry: dict[str, Any] = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": payload.get("model"),
        **_labels(messages),
        "max_tokens": payload.get("max_tokens"),
        "messages": messages,
    }
    if error is not None:
        entry["error"] = type(error).__name__
        return entry
    choice = ((response or {}).get("choices") or [{}])[0]
    entry.update(_clean_reply((choice.get("message") or {}).get("content")))
    entry["finish_reason"] = choice.get("finish_reason")
    entry["model_ms"] = (response or {}).get("_model_ms")
    # what the provider billed for the call, so a report can show measured cost
    usage = (response or {}).get("usage")
    if isinstance(usage, dict):
        entry["usage"] = _numbers(usage)
    if isinstance((response or {}).get("id"), str):
        entry["id"] = response["id"][:120]
    return entry


def _numbers(value: dict[str, Any]) -> dict[str, Any]:
    """Only the numeric parts of a usage record (token counts, cost), nested one level."""
    out: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, bool):
            continue
        if isinstance(item, (int, float)):
            out[key] = item
        elif isinstance(item, dict):
            inner = {k: v for k, v in item.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
            if inner:
                out[key] = inner
    return out


def _write(path: Path, entry: dict[str, Any]) -> None:
    line = _scrub(json.dumps(entry, ensure_ascii=False))
    with _lock, path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def wrap(post: Callable[..., dict[str, Any]], path: Path) -> Callable[..., dict[str, Any]]:
    def logged(payload: dict[str, Any], api_key: str | None) -> dict[str, Any]:
        try:
            response = post(payload, api_key)
        except BaseException as exc:  # recorded, then raised unchanged
            try:
                _write(path, _entry(payload, None, exc))
            finally:
                raise
        try:
            _write(path, _entry(payload, response, None))
        except Exception as exc:  # noqa: BLE001 - recording must never stop a season
            print(f"prompt log: could not record a call: {type(exc).__name__}")
        return response

    logged.__wrapped__ = post  # type: ignore[attr-defined]
    return logged


def compress(season_dir: Path) -> Path | None:
    raw = season_dir / LOG_NAME
    if not raw.exists():
        return None
    target = season_dir / GZ_NAME
    with raw.open("rb") as src, gzip.open(target, "wb") as dst:
        shutil.copyfileobj(src, dst)
    raw.unlink()
    return target


@contextmanager
def recording(season_dir: Path) -> Iterator[Path]:
    """Record every model call made inside the block to season_dir/prompts.jsonl."""
    from . import openrouter_client

    path = Path(season_dir) / LOG_NAME
    original = openrouter_client._post_chat_completion
    openrouter_client._post_chat_completion = wrap(original, path)
    try:
        yield path
    finally:
        openrouter_client._post_chat_completion = original
        try:
            compress(Path(season_dir))
        except OSError as exc:
            print(f"prompt log: could not compress: {exc}")


def has_key_shapes(path: Path) -> bool:
    """True when a file holds anything shaped like an API key or bearer token (checked before it is offered for download)."""
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as handle:  # type: ignore[operator]
        tail = b""
        while chunk := handle.read(1 << 20):
            if KEY_SHAPES.search(tail + chunk):
                return True
            tail = chunk[-200:]
    return False
