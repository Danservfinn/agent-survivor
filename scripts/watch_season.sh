#!/bin/zsh
cd /Users/kublai/projects/llm_survivor_live
for i in $(seq 1 240); do
  sleep 30
  ST=$(curl -s -m 10 'https://llmsurvivor.kurult.ai/api/live-season/status' | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status"))' 2>/dev/null)
  if [ "$ST" = "done" ]; then
    echo "DONE"
    curl -s -m 10 'https://llmsurvivor.kurult.ai/api/live-season/status' | python3 -c '
import json,sys
d=json.load(sys.stdin); r=d.get("report") or {}
g=r.get("game") or {}; c=r.get("caps") or {}
print("winner:", g.get("winner"))
print("rounds:", g.get("rounds"))
print("cost_cents:", c.get("estimated_cost_cents"))
print("elapsed_s:", round(r.get("elapsed_s") or 0))
rows=r.get("rows") or []
for row in rows[:5]:
    print("  place", row.get("place"), row.get("model"), "idx", row.get("survivor_index"))
'
    exit 0
  fi
done
echo "watch timeout"
