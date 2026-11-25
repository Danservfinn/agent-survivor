# LLM Survivor

Workspace: `/Users/kublai/projects/llm_survivor_live`. Production runs from this directory (launchd `ai.kurult.llm-survivor-live`, port 18800, public at `llmsurvivor.kurult.ai`): rebuilding `frontend/out` or restarting the job is a production deploy.

## Direction

- The simulation is turn-based. `backend/turn_controller.py` is the only module that advances game state.
- The frontend is a replay surface. It reads `StoryEvents` and may request a new turn, but it must not mutate state locally.
- The first runnable proof is a deterministic Tribal Conference episode, designed as a TV-broadcast replay rather than the old pixel-first viewer.
- The frontend is two pages: `/` (benchmark console) and `/history`. The broadcast viewer and arena pages were removed on 2026-11-24 (git `beaa15b` has them); their backend routes remain.
- Mutations (reset, turns, viewer-state writes, frontier lock, arena POSTs) require `OPERATOR_TOKEN` as `Authorization: Bearer …`. If the token is unset, those POSTs return 503.
- Public shows lock a live OpenRouter frontier roster (`scripts/select_frontier_roster.py --lock`) before producing an episode. Cheap/free/Ollama/Z.ai rosters are local smoke only.
- The public season button (`/api/live-season/public-run`) runs `lab_mix_openrouter`, one model per lab, under a $10 per-run cap (`backend/public_gate.py`, since 2026-11-24). Each model there was checked with a live call before it was added; check a replacement the same way.
- Paid arena work remains local/closed-beta only unless explicitly authorized. Real x402, escrow, payout, Railway, and production mutations require explicit approval.
- CPU/default players are platform-filled entries with curated default `soul.md` profiles. They do not pay, vote to start, or receive payouts.

## Local Commands

- Backend tests: `python3 -m unittest discover -s backend/tests -v`
- Backend API: `python3 -m uvicorn backend.api:app --reload --port 8000`
- Frontend build: `cd frontend && npm run build` (this overwrites the `frontend/out` that production serves)

## Safety

- Do not use CBS footage, logos, music, or exact branded assets.
- Do not clone, imitate, or use a real person's voice without explicit rights. ElevenLabs keys must stay in the backend runtime environment only; never write them to repo files, docs, frontend code, API payloads, logs, or generated artifacts.
- Do not touch Railway or production services from this workspace without explicit approval.
