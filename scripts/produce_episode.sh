#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
API="${API:-http://127.0.0.1:8000}"

if [[ -z "${OPERATOR_TOKEN:-}" ]]; then
  echo "OPERATOR_TOKEN is required" >&2
  exit 1
fi

python3 scripts/select_frontier_roster.py --lock
python3 scripts/verify_frontier_roster.py

auth=(-H "Authorization: Bearer ${OPERATOR_TOKEN}" -H "Content-Type: application/json")
curl -sS -X POST "$API/api/dev/reset" "${auth[@]}" -d '{"roster_preset":"frontier_live"}'
curl -sS -X POST "$API/api/turns/auto-run" "${auth[@]}" -d '{"max_turns":90}'
echo
echo "episode produced against locked frontier roster"
