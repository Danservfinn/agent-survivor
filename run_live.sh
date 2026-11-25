#!/usr/bin/env bash
set -euo pipefail
# run from wherever this checkout lives
cd "$(dirname "$0")"
APP_DIR="$PWD"
ENV_FILE="${AGENT_SURVIVOR_ENV_FILE:-/Users/kublai/.kublai/secrets/agent-survivor.env}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi
# Production-local live mode: real model calls only. Ollama serves the local benchmark roster.
export PYTHONNOUSERSITE=1
export PORT=18800
export FRONTEND_DIST="$APP_DIR/frontend/out"
export LLM_PROVIDER="${LLM_PROVIDER:-ollama}"
# Accelerated benchmark mode keeps live model decisions/challenges/votes but skips long narrative filler.
export AGENT_SURVIVOR_FAST_BENCHMARK=1
export OLLAMA_BASE_URL=http://127.0.0.1:11434
export OLLAMA_HOST_MODEL=qwen2.5:1.5b
export OLLAMA_TIMEOUT_SECONDS=240
export OLLAMA_NUM_CTX=8192
export VOICE_PROVIDER=${VOICE_PROVIDER:-fake}
exec "$APP_DIR/.venv/bin/python" -m uvicorn backend.api:app --host 127.0.0.1 --port 18800
