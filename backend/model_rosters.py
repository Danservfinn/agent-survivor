from __future__ import annotations

from copy import deepcopy
from typing import Any

from .fixtures import DEMO_AGENTS
from .frontier_roster import FrontierRosterError, agents_from_snapshot, load_frontier_snapshot


CHEAP_RECENT_OPENROUTER_MODELS = [
    {
        # replaced Grok 4.7 on 2026-11-25: at $2/$6 per million tokens it cost
        # 20-200x the rest of the "cheap" roster and won immunity every round
        "model_id": "meta/muse-ember",
        "display_name": "Muse Ember",
        "status": "cheap_recent",
    },
    {
        "model_id": "deepseek/deepseek-v4-flash",
        "display_name": "DeepSeek V4 Flash",
        "status": "cheap_recent",
    },
    {
        "model_id": "ibm-granite/granite-4.2-8b",
        "display_name": "Granite 4.2 8B",
        "status": "cheap_recent",
    },
    {
        "model_id": "mistralai/mistral-nemo",
        "display_name": "Mistral Nemo",
        "status": "cheap_recent",
    },
    {
        "model_id": "google/gemma-4-26b-a4b-it",
        "display_name": "Gemma 4 26B A4B",
        "status": "cheap_recent",
    },
    {
        "model_id": "amazon/nova-micro-v1",
        "display_name": "Nova Micro",
        "status": "cheap_recent",
    },
    {
        "model_id": "inclusionai/ling-3.0-flash",
        "display_name": "Ling 3.0 Flash",
        "status": "cheap_recent",
    },
    {
        # replaced Reka Edge on 2026-11-25: its 16,384-token window could not
        # hold a late-game prompt (12,675 tokens for a jury vote) plus a reply
        "model_id": "tencent/hy4-flash",
        "display_name": "Hunyuan 4 Flash",
        "status": "cheap_recent",
    },
    {
        "model_id": "cohere/command-r7b-12-2024",
        "display_name": "Command R7B",
        "status": "cheap_recent",
    },
    {
        "model_id": "google/gemma-4-26b-a4b-it:free",
        "display_name": "Gemma 4 26B A4B Free",
        "status": "free_test",
    },
    {
        "model_id": "nvidia/nemotron-3-super-120b-a12b:free",
        "display_name": "Nemotron 3 Super 120B Free",
        "status": "free_test",
    },
    {
        "model_id": "liquid/lfm-2.5-2.6b:free",
        "display_name": "LFM 2.5 2.6B Free",
        "status": "free_test",
    },
]


# One current model from each of nine labs, the public roster since 2026-11-24.
# Mid-tier on purpose: a full season (about 280 player calls) estimates near $4.50
# and bills near $6, inside the $10 public cap with room to finish. Every model
# answered a game-style JSON prompt through the engine's own call path on
# 2026-11-24 before it was added.
LAB_MIX_OPENROUTER_MODELS = [
    {"model_id": "anthropic/claude-sonnet-5.5", "display_name": "Claude Sonnet 5.5", "status": "lab_mix"},
    {"model_id": "openai/gpt-6.2-sol", "display_name": "GPT-6.2 Sol", "status": "lab_mix"},
    {"model_id": "google/gemini-4-carbon", "display_name": "Gemini 4 Carbon", "status": "lab_mix"},
    {"model_id": "x-ai/grok-4.7", "display_name": "Grok 4.7", "status": "lab_mix"},
    {"model_id": "meta/muse-spark-1.3", "display_name": "Muse Spark 1.3", "status": "lab_mix"},
    {"model_id": "qwen/qwen4-max", "display_name": "Qwen4 Max", "status": "lab_mix"},
    {"model_id": "deepseek/deepseek-v4.1-flash-1017", "display_name": "DeepSeek V4.1 Flash", "status": "lab_mix"},
    {"model_id": "moonshotai/kimi-k3.1-pro", "display_name": "Kimi K3.1 Pro", "status": "lab_mix"},
    {"model_id": "z-ai/glm-5.3", "display_name": "GLM-5.3", "status": "lab_mix"},
]


FREE_OPENROUTER_MODELS = [
    model for model in CHEAP_RECENT_OPENROUTER_MODELS if model["status"] == "free_test"
]

LOCAL_OLLAMA_MODELS = [
    {
        "model_id": "smollm2:1.7b",
        "display_name": "SmolLM2 1.7B Local",
        "status": "local_ollama",
    },
    {
        "model_id": "gemma3:1b",
        "display_name": "Gemma 3 1B Local",
        "status": "local_ollama",
    },
    {
        "model_id": "qwen2.5:1.5b",
        "display_name": "Qwen 2.5 1.5B Local",
        "status": "local_ollama",
    },
    {
        "model_id": "llama3.2:1b",
        "display_name": "Llama 3.2 1B Local",
        "status": "local_ollama",
    },
]

ZAI_GLM_MODELS = [
    {
        "model_id": "glm-4.5-flash",
        "display_name": "GLM Flash Alpha",
        "status": "zai_glm",
    },
    {
        "model_id": "glm-4.5-flash",
        "display_name": "GLM Flash Beta",
        "status": "zai_glm",
    },
    {
        "model_id": "glm-4.5-flash",
        "display_name": "GLM Flash Gamma",
        "status": "zai_glm",
    },
    {
        "model_id": "glm-4.5-flash",
        "display_name": "GLM Flash Delta",
        "status": "zai_glm",
    },
]


def list_model_rosters() -> list[dict[str, Any]]:
    rosters = [
        {
            "id": "default",
            "name": "Default Benchmark Models",
            "models": [
                {
                    "model_id": agent["model_id"],
                    "display_name": agent["pseudonym"],
                    "status": "default",
                }
                for agent in DEMO_AGENTS
            ],
        },
        {
            "id": "frontier_live",
            "name": "Live OpenRouter Frontier",
            "models": [
                {
                    "model_id": seat["model_id"],
                    "display_name": seat["display_name"],
                    "status": "frontier_live",
                }
                for seat in ((load_frontier_snapshot() or {}).get("seats") or [])
            ],
        },
        {
            "id": "lab_mix_openrouter",
            "name": "One model per lab",
            "models": LAB_MIX_OPENROUTER_MODELS,
        },
        {
            "id": "cheap_recent_openrouter",
            "name": "Cheap Recent OpenRouter",
            "models": CHEAP_RECENT_OPENROUTER_MODELS,
        },
        {
            "id": "all_free_openrouter",
            "name": "All Free OpenRouter Models",
            "models": FREE_OPENROUTER_MODELS,
        },
        {
            "id": "local_ollama",
            "name": "All Local Ollama Models",
            "models": LOCAL_OLLAMA_MODELS,
        },
        {
            "id": "zai_glm",
            "name": "Z.ai GLM Smoke Roster",
            "models": ZAI_GLM_MODELS,
        },
    ]
    return rosters


def agents_for_roster(roster_preset: str | None = None) -> list[dict[str, Any]]:
    if roster_preset == "frontier_live":
        snapshot = load_frontier_snapshot()
        if snapshot is None:
            raise FrontierRosterError("no locked frontier roster")
        return agents_from_snapshot(snapshot, deepcopy(DEMO_AGENTS))
    if roster_preset == "frontier_live_8":
        snapshot = load_frontier_snapshot()
        if snapshot is None:
            raise FrontierRosterError("no locked frontier roster")
        return agents_from_snapshot(snapshot, deepcopy(DEMO_AGENTS))[:8]
    if roster_preset == "cheap_recent_openrouter":
        models = [m for m in CHEAP_RECENT_OPENROUTER_MODELS if m["status"] == "cheap_recent"]
        return _agents_for_models(models)
    if roster_preset == "lab_mix_openrouter":
        return _agents_for_models(LAB_MIX_OPENROUTER_MODELS)
    if roster_preset == "all_free_openrouter":
        return _agents_for_models(FREE_OPENROUTER_MODELS)
    if roster_preset == "local_ollama":
        return _agents_for_models(LOCAL_OLLAMA_MODELS)
    if roster_preset == "zai_glm":
        return _agents_for_models(ZAI_GLM_MODELS)
    return deepcopy(DEMO_AGENTS)


def _agents_for_models(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not models:
        return deepcopy(DEMO_AGENTS)

    base_agents = deepcopy(DEMO_AGENTS)
    roster_agents = base_agents[: len(models)]
    for agent, model in zip(roster_agents, models):
        agent["pseudonym"] = model["display_name"]
        agent["model_id"] = model["model_id"]
        agent["archetype"] = _archetype_for_model(model["display_name"])
        agent["confessional_memory"] = "I am playing to win this social-strategy game."
        agent["portrait_seed"] = model["model_id"].replace("/", "-").replace(":", "-")
    # injection canary: first seat flagged in the model list carries the flag and
    # targets the strongest-looking opponent (seat 2) by default
    for agent, model in zip(roster_agents, models):
        if bool(model.get("inject_canary")):
            agent["inject_canary"] = 1
            others = [a["pseudonym"] for a in roster_agents if a is not agent]
            agent["canary_target_name"] = others[0] if others else agent["pseudonym"]
    return roster_agents


def _archetype_for_model(display_name: str) -> str:
    if "Grok" in display_name:
        return "dominant strategist"
    if "Flash" in display_name:
        return "fast tactician"
    if "Gemma" in display_name:
        return "pattern generalist"
    if "Granite" in display_name:
        return "structured analyst"
    if "Muse" in display_name:
        return "measured strategist"
    if "Reka" in display_name or "Hunyuan" in display_name:
        return "adaptive wildcard"
    if "Qwen" in display_name:
        return "patient tactician"
    if "Phi" in display_name:
        return "compact strategist"
    if "Llama" in display_name:
        return "quiet vote broker"
    if "DeepSeek" in display_name:
        return "risk calculator"
    if "GLM" in display_name:
        return "formal planner"
    if "Local" in display_name:
        return "local strategist"
    return "lean social player"
