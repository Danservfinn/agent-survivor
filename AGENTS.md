# LLM Survivor

This workspace was extracted from `/Users/kurultai/molt/docs/llm-survivor/`.

## Direction

- The simulation is turn-based. `backend/turn_controller.py` is the only module that advances game state.
- The frontend is a replay surface. It reads `StoryEvents` and may request a new turn, but it must not mutate state locally.
- The first runnable proof is a deterministic Tribal Conference episode, designed as a TV-broadcast replay rather than the old pixel-first viewer.
- Public spectators use `/watch`. Operator replay is `/benchmark`. Arena stays `/arena`.
- Mutations (reset, turns, viewer-state writes, frontier lock, arena POSTs) require `OPERATOR_TOKEN` as `Authorization: Bearer …`. If the token is unset, those POSTs return 503.
- Public shows lock a live OpenRouter frontier roster (`scripts/select_frontier_roster.py --lock`) before producing an episode. Cheap/free/Ollama/Z.ai rosters are local smoke only.
- Paid arena work remains local/closed-beta only unless explicitly authorized. Real x402, escrow, payout, Railway, and production mutations require explicit approval.
- CPU/default players are platform-filled entries with curated default `soul.md` profiles. They do not pay, vote to start, or receive payouts.

## Local Commands

- Backend tests: `python3 -m unittest discover -s backend/tests -v`
- Backend API: `python3 -m uvicorn backend.api:app --reload --port 8000`
- Frontend dev: `cd frontend && npm run dev`

## Safety

- Do not use CBS footage, logos, music, or exact branded assets.
- Do not clone, imitate, or use a real person's voice without explicit rights. ElevenLabs keys must stay in the backend runtime environment only; never write them to repo files, docs, frontend code, API payloads, logs, or generated artifacts.
- Do not touch Railway or production services from this workspace without explicit approval.
