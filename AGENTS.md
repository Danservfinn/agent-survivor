# Agent Survivor

Workspace: `/Users/kublai/projects/agent_survivor_live`. Production runs from this directory (launchd `ai.kurult.agent-survivor-live`, port 18800, public at `agentsurvivor.kurult.ai`): rebuilding `frontend/out` or restarting the job is a production deploy.

## Direction

- The simulation is turn-based. `backend/turn_controller.py` is the only module that advances game state.
- The frontend is a replay surface. It reads `StoryEvents` and may request a new turn, but it must not mutate state locally.
- The first runnable proof is a deterministic Tribal Conference episode, designed as a TV-broadcast replay rather than the old pixel-first viewer.
- The frontend is four pages: `/` (home: what the game is, last season, start a season), `/watch` (a season animated round by round from its report data; `?season=<id>` picks one), `/history`, and `/methodology` (exactly how the models are tested; not in the menu until Danny has read it).
- Paid entries (`/enter`, `/entry`) are closed until an operator opens them, and test-mode payments never start real seasons unless allowed; see README "Paid entries". Live Stripe keys and opening entries need Danny's yes.
- `/methodology` keeps itself true: its numbers come from `GET /api/methodology` (`backend/methodology.py`), its prose lives in `frontend/src/app/methodology/content.json`, and three tests fail when the game changes under it. `test_rules_changelog` wants a new entry in `backend/rules_changelog.py` when any rule changes; `test_methodology_sections` names a page section whose engine code changed (re-read it, then `python3 -m backend.methodology_sections --update <section>`); `test_rules_agree` and `test_methodology_page` hold the rules text and the page's quotes to the engine. Live seasons record every prompt and reply (never `think`) to `prompts.jsonl.gz`. The broadcast viewer and arena pages were removed on 2026-11-24 (git `beaa15b` has them); their backend routes remain.
- Mutations (reset, turns, viewer-state writes, frontier lock, arena POSTs) require `OPERATOR_TOKEN` as `Authorization: Bearer …`. If the token is unset, those POSTs return 503.
- Public shows lock a live OpenRouter frontier roster (`scripts/select_frontier_roster.py --lock`) before producing an episode. Cheap/free/Ollama/Z.ai rosters are local smoke only.
- The public season button (`/api/live-season/public-run`) runs `lab_mix_openrouter`, one model per lab, under a $25 per-run cap on real spend (`backend/public_gate.py`; estimates are scaled to the bill by `live_cost.BILL_FACTOR`). Each model there was checked with a live call before it was added; check a replacement the same way.
- Paid arena work remains local/closed-beta only unless explicitly authorized. Real x402, escrow, payout, Railway, and production mutations require explicit approval.
- CPU/default players are platform-filled entries with curated default `soul.md` profiles. They do not pay, vote to start, or receive payouts.

- `video/` is a Remotion project that renders a season report into `share/season.mp4` (`backend/season_video.py` runs it after each live season). It imports the Watch stage from `frontend/src/app`; Remotion renders by seeking frames, so drive motion with `useCurrentFrame()`/`interpolate()`, never CSS animation. Deploying it means `cd video && npm ci` in the production tree. Remotion's own agent skills are in `video/.agents/skills`.
- Model calls that cannot see each other run in parallel (plans, notebooks, partner picks, talks with no shared player). Every call must get exactly the inputs it would get one at a time; `backend/tests/test_parallel_equivalence.py` plays a season both ways and checks every call and table. `SURVIVOR_PARALLEL_CALLS=0` turns it off.

## Local Commands

- Backend tests: `python3 -m unittest discover -s backend/tests -v`
- Backend API: `python3 -m uvicorn backend.api:app --reload --port 8000`
- Frontend build: `cd frontend && npm run build` (this overwrites the `frontend/out` that production serves)

## Safety

- Do not use CBS footage, logos, music, or exact branded assets.
- Do not clone, imitate, or use a real person's voice without explicit rights. ElevenLabs keys must stay in the backend runtime environment only; never write them to repo files, docs, frontend code, API payloads, logs, or generated artifacts.
- Do not touch Railway or production services from this workspace without explicit approval.
