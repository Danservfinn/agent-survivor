from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .llm_config import redact_secrets

OPENROUTER_MODELS_URL = os.environ.get(
    "OPENROUTER_MODELS_URL",
    "https://openrouter.ai/api/v1/models",
)
MIN_CONTEXT_LENGTH = 32_000
SEAT_COUNT = 16
REQUIRED_AUTHORS = [
    "openai",
    "anthropic",
    "google",
    "x-ai",
    "deepseek",
    "qwen",
    "z-ai",
    "moonshotai",
    "minimax",
    "nvidia",
    "mistralai",
    "meta-llama",
    "cohere",
    "ibm-granite",
    "rekaai",
    "tencent",
]
DOWNRANK_RE = re.compile(
    r"(?:^|[-_/:.\s])(flash|mini|nano|lite|haiku|small|instant|draft)(?:$|[-_/:.\s])",
    re.IGNORECASE,
)
FLAGSHIP_RE = re.compile(
    r"(?:^|[-_/:.\s])(opus|pro|max|ultra|large|premier|astra|fable|gpt-6|grok-4\.6)(?:$|[-_/:.\s])",
    re.IGNORECASE,
)
SNAPSHOT_PATH = Path(
    os.environ.get(
        "FRONTIER_ROSTER_PATH",
        Path(__file__).resolve().parent.parent / "artifacts" / "rosters" / "frontier_live.json",
    )
)


class FrontierRosterError(RuntimeError):
    pass


def author_of(model_id: str) -> str:
    return model_id.split("/", 1)[0]


def intelligence_index(model: dict[str, Any]) -> float | None:
    benchmarks = model.get("benchmarks") or {}
    analysis = benchmarks.get("artificial_analysis") or {}
    value = analysis.get("intelligence_index")
    if value is None:
        return None
    return float(value)


def agentic_index(model: dict[str, Any]) -> float | None:
    benchmarks = model.get("benchmarks") or {}
    analysis = benchmarks.get("artificial_analysis") or {}
    value = analysis.get("agentic_index")
    if value is None:
        return None
    return float(value)


def _blob(model: dict[str, Any]) -> str:
    return f"{model.get('id', '')} {model.get('name', '')}".lower()


def is_downranked(model: dict[str, Any]) -> bool:
    return bool(DOWNRANK_RE.search(_blob(model)))


def is_flagship_named(model: dict[str, Any]) -> bool:
    return bool(FLAGSHIP_RE.search(_blob(model)))


def is_eligible(model: dict[str, Any], now: datetime | None = None) -> bool:
    model_id = str(model.get("id") or "")
    if not model_id or "/" not in model_id:
        return False
    if ":free" in model_id:
        return False
    lowered = model_id.lower()
    name = str(model.get("name") or "").lower()
    if "router" in lowered or lowered.endswith("/auto") or name.endswith(" auto"):
        return False
    expiration = model.get("expiration_date")
    if expiration:
        try:
            expires = datetime.strptime(str(expiration)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            expires = None
        current = now or datetime.now(timezone.utc)
        if expires is not None and expires < current:
            return False
    architecture = model.get("architecture") or {}
    outputs = architecture.get("output_modalities") or ["text"]
    inputs = architecture.get("input_modalities") or ["text"]
    if outputs and "text" not in outputs:
        return False
    if inputs and "text" not in inputs:
        return False
    context_length = model.get("context_length") or 0
    if context_length and int(context_length) < MIN_CONTEXT_LENGTH:
        return False
    return True


def fetch_openrouter_models(api_key: str | None = None) -> list[dict[str, Any]]:
    models: list[dict[str, Any]] = []
    offset = 0
    limit = 500
    while True:
        query = urllib.parse.urlencode(
            {
                "output_modalities": "text",
                "sort": "intelligence-high-to-low",
                "offset": offset,
                "limit": limit,
            }
        )
        request = urllib.request.Request(
            f"{OPENROUTER_MODELS_URL}?{query}",
            headers=_openrouter_headers(api_key),
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=float(os.environ.get("OPENROUTER_TIMEOUT_SECONDS", "45"))) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = redact_secrets(exc.read().decode("utf-8", errors="replace"))
            raise FrontierRosterError(f"OpenRouter models list failed ({exc.code}): {body[:500]}") from exc
        except Exception as exc:  # noqa: BLE001 - surface catalog failures to the lock
            raise FrontierRosterError(redact_secrets(str(exc))) from exc

        page = payload.get("data") or []
        models.extend(page)
        next_link = (payload.get("links") or {}).get("next")
        if not page or not next_link:
            break
        offset += limit
        if offset > 5000:
            break
    return models


def select_frontier_roster(
    catalog: list[dict[str, Any]],
    *,
    seat_count: int = SEAT_COUNT,
    required_authors: list[str] | None = None,
    allow_fill: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    required = list(required_authors or REQUIRED_AUTHORS)
    if len(required) != seat_count:
        raise FrontierRosterError(f"required_authors must have {seat_count} labs, got {len(required)}")

    eligible = [model for model in catalog if is_eligible(model, now=now)]
    by_author: dict[str, list[dict[str, Any]]] = {}
    for model in eligible:
        by_author.setdefault(author_of(str(model["id"])), []).append(model)

    seats: list[dict[str, Any]] = []
    missing: list[str] = []
    for author in required:
        seat = pick_for_author(by_author.get(author, []), author=author)
        if seat is None:
            missing.append(author)
        else:
            seats.append(seat)

    if missing and not allow_fill:
        raise FrontierRosterError(
            "No eligible OpenRouter flagship for required labs: " + ", ".join(missing)
        )

    if missing and allow_fill:
        used_authors = {seat["author"] for seat in seats}
        leftovers = [
            pick_for_author(models, author=author)
            for author, models in by_author.items()
            if author not in used_authors
        ]
        leftovers = [seat for seat in leftovers if seat is not None]
        leftovers.sort(key=lambda seat: (seat.get("intelligence_index") or -1), reverse=True)
        for seat in leftovers:
            if len(seats) >= seat_count:
                break
            seats.append(seat)

    if len(seats) != seat_count:
        raise FrontierRosterError(f"Could not fill {seat_count} frontier seats (got {len(seats)})")

    return {
        "locked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "openrouter",
        "sort": "intelligence-high-to-low",
        "seat_count": seat_count,
        "required_authors": required,
        "seats": seats,
    }


def pick_for_author(models: list[dict[str, Any]], *, author: str) -> dict[str, Any] | None:
    if not models:
        return None
    non_downranked = [model for model in models if not is_downranked(model)]
    pool = non_downranked or models
    pool = sorted(
        pool,
        key=lambda model: (
            intelligence_index(model) if intelligence_index(model) is not None else -1.0,
            1 if is_flagship_named(model) else 0,
            agentic_index(model) if agentic_index(model) is not None else -1.0,
            int(model.get("created") or 0),
        ),
        reverse=True,
    )
    winner = pool[0]
    rejected = [
        {
            "model_id": str(model.get("id")),
            "display_name": str(model.get("name") or model.get("id")),
            "intelligence_index": intelligence_index(model),
        }
        for model in models
        if str(model.get("id")) != str(winner.get("id"))
    ]
    winner_index = intelligence_index(winner)
    reason = f"highest intelligence_index for {author}"
    if winner_index is not None:
        reason += f" ({winner_index:g})"
    if rejected:
        best_rejected = max(
            rejected,
            key=lambda item: item["intelligence_index"] if item["intelligence_index"] is not None else -1.0,
        )
        if best_rejected["intelligence_index"] is not None:
            reason += f" ahead of {best_rejected['model_id']} ({best_rejected['intelligence_index']:g})"
        else:
            reason += f" ahead of {best_rejected['model_id']}"
    if is_downranked(winner) is False and any(is_downranked(model) for model in models):
        reason += "; downranked flash/mini/lite siblings"
    return {
        "author": author,
        "model_id": str(winner.get("canonical_slug") or winner.get("id")),
        "display_name": _display_name(winner),
        "intelligence_index": winner_index,
        "agentic_index": agentic_index(winner),
        "created": winner.get("created"),
        "context_length": winner.get("context_length"),
        "reason": reason,
        "rejected_siblings": rejected[:8],
    }


def verify_roster_against_catalog(
    snapshot: dict[str, Any],
    catalog: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    by_author: dict[str, list[dict[str, Any]]] = {}
    for model in catalog:
        if is_eligible(model, now=now):
            by_author.setdefault(author_of(str(model["id"])), []).append(model)
    for seat in snapshot.get("seats") or []:
        author = seat["author"]
        fresh = pick_for_author(by_author.get(author, []), author=author)
        if fresh is None:
            findings.append(
                {
                    "author": author,
                    "seated_model_id": seat.get("model_id"),
                    "fresh_model_id": None,
                    "reason": "seated lab has no eligible model in the live catalog",
                }
            )
            continue
        if fresh["model_id"] != seat.get("model_id"):
            findings.append(
                {
                    "author": author,
                    "seated_model_id": seat.get("model_id"),
                    "fresh_model_id": fresh["model_id"],
                    "seated_intelligence_index": seat.get("intelligence_index"),
                    "fresh_intelligence_index": fresh.get("intelligence_index"),
                    "reason": fresh["reason"],
                }
            )
    return findings


def snapshot_is_fresh(snapshot: dict[str, Any], *, max_age_hours: float = 24.0) -> bool:
    locked_at = snapshot.get("locked_at")
    if not locked_at:
        return False
    try:
        locked = datetime.strptime(str(locked_at), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    age = datetime.now(timezone.utc) - locked
    return age.total_seconds() <= max_age_hours * 3600


def load_frontier_snapshot(path: Path | None = None) -> dict[str, Any] | None:
    snapshot_path = path or SNAPSHOT_PATH
    if not snapshot_path.exists():
        return None
    return json.loads(snapshot_path.read_text(encoding="utf-8"))


def save_frontier_snapshot(snapshot: dict[str, Any], path: Path | None = None) -> Path:
    snapshot_path = path or SNAPSHOT_PATH
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return snapshot_path


def agents_from_snapshot(snapshot: dict[str, Any], base_agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seats = snapshot.get("seats") or []
    if len(seats) < 1:
        raise FrontierRosterError("frontier snapshot has no seats")
    from copy import deepcopy

    roster = deepcopy(base_agents[: len(seats)])
    for agent, seat in zip(roster, seats):
        agent["pseudonym"] = seat["display_name"]
        agent["model_id"] = seat["model_id"]
        agent["portrait_seed"] = seat["model_id"].replace("/", "-").replace(":", "-")
        agent["confessional_memory"] = "I am playing to win this social-strategy game."
    return roster


def chat_preflight(model_id: str, api_key: str) -> dict[str, Any]:
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": 'Return exactly: {"status":"ready"}'}],
        "temperature": 0,
        "max_tokens": int(os.environ.get("OPENROUTER_TEST_MAX_TOKENS", "80")),
    }
    request = urllib.request.Request(
        os.environ.get("OPENROUTER_CHAT_URL", "https://openrouter.ai/api/v1/chat/completions"),
        data=json.dumps(payload).encode("utf-8"),
        headers=_openrouter_headers(api_key),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=float(os.environ.get("OPENROUTER_TIMEOUT_SECONDS", "90"))) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "error": redact_secrets(exc.read().decode("utf-8", errors="replace"))[:1000],
        }
    except Exception as exc:  # noqa: BLE001 - preflight must report every model error
        return {"ok": False, "error": redact_secrets(str(exc))[:1000]}
    content = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    return {"ok": "status" in content.lower() and "ready" in content.lower(), "error": None}


def preflight_models(
    seats: list[dict[str, Any]],
    *,
    call_fn: Callable[[str], dict[str, Any]],
) -> list[dict[str, Any]]:
    results = []
    for seat in seats:
        result = call_fn(seat["model_id"])
        result = {
            "model_id": seat["model_id"],
            "display_name": seat["display_name"],
            "ok": bool(result.get("ok")),
            "error": result.get("error"),
        }
        results.append(result)
    failed = [result["model_id"] for result in results if not result["ok"]]
    if failed:
        raise FrontierRosterError("OpenRouter preflight failed for: " + ", ".join(failed))
    return results


def _display_name(model: dict[str, Any]) -> str:
    name = str(model.get("name") or "").strip()
    if not name:
        return str(model.get("id"))
    name = re.sub(r"^\s*[A-Za-z0-9._-]+\s*:\s*", "", name).strip()
    return name or str(model.get("id"))


def _openrouter_headers(api_key: str | None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "HTTP-Referer": os.environ.get("OPENROUTER_SITE_URL", "http://localhost:3001"),
        "X-Title": os.environ.get("OPENROUTER_APP_NAME", "LLM Survivor frontier roster"),
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers
