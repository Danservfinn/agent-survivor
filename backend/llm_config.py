from __future__ import annotations

import os
import json
import urllib.error
import urllib.request
from dataclasses import dataclass


PROVIDER_OPENROUTER = "openrouter"
PROVIDER_OLLAMA = "ollama"
PROVIDER_ZAI = "zai"
PROVIDER_DETERMINISTIC = "deterministic"
SUPPORTED_PROVIDERS = {PROVIDER_OPENROUTER, PROVIDER_OLLAMA, PROVIDER_ZAI, PROVIDER_DETERMINISTIC}


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    openrouter_configured: bool
    ollama_configured: bool
    zai_configured: bool
    default_model_id: str
    zai_base_url: str
    zai_default_model_id: str
    timeout_seconds: float
    site_url: str
    app_name: str
    ollama_base_url: str
    ollama_host_model_id: str
    ollama_keep_alive: str
    ollama_num_ctx: int
    ollama_timeout_seconds: float
    ollama_available_models: list[str]
    ollama_required_models: list[str]
    ollama_missing_models: list[str]


def get_llm_settings() -> LLMSettings:
    provider = os.environ.get("LLM_PROVIDER", PROVIDER_OPENROUTER).strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        provider = PROVIDER_OPENROUTER
    ollama_base_url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    ollama_host_model_id = os.environ.get("OLLAMA_HOST_MODEL", "qwen2.5:1.5b")
    zai_base_url = os.environ.get("ZAI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
    # only probe Ollama when it is the active provider: this runs on every model call
    ollama_available_models = _available_ollama_models(ollama_base_url) if provider == PROVIDER_OLLAMA else []
    ollama_required_models = _required_ollama_models(ollama_host_model_id)
    ollama_missing_models = [
        model_id for model_id in ollama_required_models if model_id not in ollama_available_models
    ]
    return LLMSettings(
        provider=provider,
        openrouter_configured=bool(os.environ.get("OPENROUTER_API_KEY")),
        ollama_configured=bool(ollama_base_url) and not ollama_missing_models,
        zai_configured=bool(os.environ.get("ZAI_API_KEY") or os.environ.get("GLM_API_KEY")),
        default_model_id=os.environ.get("OPENROUTER_DEFAULT_MODEL", "openai/gpt-4.1-mini"),
        zai_base_url=zai_base_url,
        zai_default_model_id=os.environ.get("ZAI_HOST_MODEL", "glm-4.5-flash"),
        # Replies are not streamed, so the connection is silent until the model
        # finishes. At high reasoning a frontier model on hard puzzles can think
        # for minutes; at 90 s Gemini 4 timed out on every try and each retry
        # was billed again (2026-11-25). Ten minutes covers it.
        timeout_seconds=float(os.environ.get("OPENROUTER_TIMEOUT_SECONDS", "600")),
        site_url=os.environ.get("OPENROUTER_SITE_URL", "http://localhost:3001"),
        app_name=os.environ.get("OPENROUTER_APP_NAME", "LLM Survivor Local"),
        ollama_base_url=ollama_base_url,
        ollama_host_model_id=ollama_host_model_id,
        ollama_keep_alive=os.environ.get("OLLAMA_KEEP_ALIVE_PER_CALL", "0"),
        ollama_num_ctx=int(os.environ.get("OLLAMA_NUM_CTX", "8192")),
        ollama_timeout_seconds=float(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "180")),
        ollama_available_models=ollama_available_models,
        ollama_required_models=ollama_required_models,
        ollama_missing_models=ollama_missing_models,
    )


# Contestant prompt version. "v3" (default since 2026-11-24): a computed
# situation brief and a ledger of what each player was told, a strategist's
# checklist, private thinking before the decision, fewer reply fields and a
# structured notebook. "v2": the earlier prompt and context, kept so the two
# can be compared on the same seed (scripts/compare_seasons.py).
PROMPT_PROFILES = ("v2", "v3")


# How jurors are told to vote. "own_judgment" (default since 2026-11-25):
# whoever the juror believes deserves to win, by their own standard, with the
# verified record in front of them. "standard": the best strategic game, judged
# on whether moves worked. In the 2026-11-25 comparison five of six jurors under
# "standard" repeated its wording ("broken promises that delivered results")
# and crowned the player who fit the checklist; kept as a switch to compare.
JURY_RULES = ("standard", "own_judgment")


def secret_ballot() -> bool:
    """Secret ballots (default since 2026-11-25), as in Survivor: players learn
    each round's tally and their own vote, never who voted for whom. The jury
    sees the true votes at the finale. SURVIVOR_SECRET_BALLOT=0 shows every
    vote to every player, the earlier rule, for comparisons."""
    return os.environ.get("SURVIVOR_SECRET_BALLOT", "1").strip() != "0"


def inner_life_enabled() -> bool:
    """A player's train of thought within a round, its read of what others
    expect of it, its contradictions, and its private lie ledger (2026-11-25).
    SURVIVOR_INNER_LIFE=0 turns them off, to compare."""
    return os.environ.get("SURVIVOR_INNER_LIFE", "1").strip() != "0"


def advantages_enabled() -> bool:
    """Receipts, spying, idol search, shown idols and ballots, giving away
    immunity, and the auction (2026-11-25). SURVIVOR_ADVANTAGES=0 turns them
    off, to compare."""
    return os.environ.get("SURVIVOR_ADVANTAGES", "1").strip() != "0"


def host_questions_enabled() -> bool:
    """At tribal council the host questions a few players in front of everyone,
    from the public record only (2026-11-25). SURVIVOR_HOST_QUESTIONS=0 turns
    it off."""
    return os.environ.get("SURVIVOR_HOST_QUESTIONS", "1").strip() != "0"


def feelings_enabled() -> bool:
    """Players record their own feelings after each council and carry them
    forward (default since 2026-11-25). SURVIVOR_FEELINGS=0 turns it off, to
    compare seasons with and without feelings."""
    return os.environ.get("SURVIVOR_FEELINGS", "1").strip() != "0"


def jury_rule() -> str:
    value = os.environ.get("SURVIVOR_JURY_RULE", "own_judgment").strip().lower()
    return value if value in JURY_RULES else "own_judgment"


def prompt_profile() -> str:
    value = os.environ.get("SURVIVOR_PROMPT_PROFILE", "v3").strip().lower()
    return value if value in PROMPT_PROFILES else "v3"


def should_use_openrouter() -> bool:
    settings = get_llm_settings()
    return settings.provider == PROVIDER_OPENROUTER and settings.openrouter_configured


def should_use_ollama() -> bool:
    settings = get_llm_settings()
    return settings.provider == PROVIDER_OLLAMA and settings.ollama_configured


def should_use_zai() -> bool:
    settings = get_llm_settings()
    return settings.provider == PROVIDER_ZAI and settings.zai_configured


def should_use_live_llm() -> bool:
    return should_use_openrouter() or should_use_ollama() or should_use_zai()


def live_llm_provider() -> str:
    settings = get_llm_settings()
    if settings.provider == PROVIDER_OLLAMA and settings.ollama_configured:
        return PROVIDER_OLLAMA
    if settings.provider == PROVIDER_OPENROUTER and settings.openrouter_configured:
        return PROVIDER_OPENROUTER
    if settings.provider == PROVIDER_ZAI and settings.zai_configured:
        return PROVIDER_ZAI
    return PROVIDER_DETERMINISTIC


def failed_llm_provider() -> str:
    provider = live_llm_provider()
    if provider == PROVIDER_DETERMINISTIC:
        return PROVIDER_DETERMINISTIC
    return f"{provider}_failed"


def redact_secrets(exc: BaseException) -> str:
    message = str(exc)
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if api_key:
        message = message.replace(api_key, "[redacted]")
    for secret_name in ("ZAI_API_KEY", "GLM_API_KEY"):
        secret = os.environ.get(secret_name)
        if secret:
            message = message.replace(secret, "[redacted]")
    return message


def _required_ollama_models(host_model_id: str) -> list[str]:
    try:
        from .model_rosters import LOCAL_OLLAMA_MODELS
    except Exception:
        LOCAL_OLLAMA_MODELS = []
    model_ids = [host_model_id, *(model["model_id"] for model in LOCAL_OLLAMA_MODELS)]
    return sorted({model_id for model_id in model_ids if model_id})


def _available_ollama_models(base_url: str) -> list[str]:
    if not base_url:
        return []
    request = urllib.request.Request(f"{base_url}/api/tags", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=2.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return []
    return sorted(
        model["name"]
        for model in payload.get("models", [])
        if isinstance(model, dict) and isinstance(model.get("name"), str)
    )
