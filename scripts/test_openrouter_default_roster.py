#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.fixtures import DEMO_AGENTS  # noqa: E402

OPENROUTER_URL = os.environ.get("OPENROUTER_CHAT_URL", "https://openrouter.ai/api/v1/chat/completions")
PROMPT = 'Return exactly: {"status":"ready"}'


def redact(value: str) -> str:
    secret = os.environ.get("OPENROUTER_API_KEY")
    if secret:
        value = value.replace(secret, "[redacted]")
    return value


def call_model(model_id: str, api_key: str) -> dict[str, Any]:
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": PROMPT}],
        "temperature": 0,
        "max_tokens": int(os.environ.get("OPENROUTER_TEST_MAX_TOKENS", "160")),
    }
    request = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": os.environ.get("OPENROUTER_SITE_URL", "http://localhost:3000"),
            "X-Title": os.environ.get("OPENROUTER_APP_NAME", "Agent Survivor model smoke test"),
        },
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=float(os.environ.get("OPENROUTER_TIMEOUT_SECONDS", "90"))) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body_text = redact(exc.read().decode("utf-8", errors="replace"))
        return {
            "model_id": model_id,
            "ok": False,
            "status": exc.code,
            "latency_seconds": round(time.monotonic() - started, 2),
            "error": body_text[:1000],
        }
    except Exception as exc:  # noqa: BLE001 - command-line smoke test should report every model error
        return {
            "model_id": model_id,
            "ok": False,
            "latency_seconds": round(time.monotonic() - started, 2),
            "error": redact(str(exc))[:1000],
        }

    content = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    ready = "status" in content.lower() and "ready" in content.lower()
    return {
        "model_id": model_id,
        "ok": ready,
        "latency_seconds": round(time.monotonic() - started, 2),
        "response_preview": content[:240],
        "usage": body.get("usage"),
    }


def load_env_file(path: str | None) -> None:
    if not path:
        return
    env_path = Path(path).expanduser()
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def main() -> int:
    load_env_file(os.environ.get("OPENROUTER_ENV_FILE"))
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print(json.dumps({"ok": False, "error": "OPENROUTER_API_KEY is not set"}, indent=2))
        return 2

    results = []
    for agent in DEMO_AGENTS:
        result = call_model(agent["model_id"], api_key)
        result["display_name"] = agent["pseudonym"]
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)

    receipt = {
        "ok": all(result["ok"] for result in results),
        "tested_count": len(results),
        "passed_count": sum(1 for result in results if result["ok"]),
        "results": results,
    }
    out = Path(os.environ.get("OPENROUTER_ROSTER_TEST_RECEIPT", "/tmp/agent-survivor-openrouter-roster-test.json"))
    out.write_text(json.dumps(receipt, indent=2, ensure_ascii=False))
    print(f"receipt={out}")
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
