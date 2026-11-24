# LLM Survivor

LLM Survivor is a turn-based social strategy simulation with a TV-style episode replay viewer.

Pages:

- `/` — benchmark console. Runs a full live season (operator key under a per-run cost cap, or the visitor's own OpenRouter key), streams the event feed, shows the latest finished season's scorecard.
- `/history` — every recorded season with scorecard, round summaries, votes and transcripts.

The broadcast viewer (`/watch`, `/benchmark`) and the arena page (`/arena`) were removed from the frontend on 2026-11-24; they are in git history at `beaa15b`. Their backend routes (episode, viewer state, voice, arena, frontier roster) still exist and are operator-gated for writes.

The backend stores canonical state changes in `Turns` and presentation beats in `StoryEvents`. `backend/turn_controller.py` is the only module that advances game state. Each live season runs on its own database under `artifacts/seasons/<timestamp>-<roster>/`.

## Run Locally

From the repo root:

```bash
python3 -m uvicorn backend.api:app --reload --port 8000
cd frontend && npm install && npm run build   # static export to frontend/out, served by the backend
```

Open `http://localhost:8000/`. The pages call `/api/...` on the same origin, so serve them through the backend rather than `next dev`.

Set `OPERATOR_TOKEN` in the backend environment. Without it, operator POSTs return 503; with it they need `Authorization: Bearer <token>`.

## Production (Mac Mini)

launchd job `ai.kurult.llm-survivor-live` runs `run_live.sh` on `127.0.0.1:18800`; cloudflared serves it at `llmsurvivor.kurult.ai`. Secrets load from `~/.kublai/secrets/llm-survivor.env`. The service reads Python code at start and serves `frontend/out` from disk, so a deploy is: `cd frontend && npm run build`, then `launchctl kickstart -k gui/$(id -u)/ai.kurult.llm-survivor-live`. Pages are served with `Cache-Control: no-cache` and `/_next/static` with a one-year `immutable` header, so a deploy needs no cache purge: visitors get the new build on their next load.

## Live season API (used by the pages)

- `POST /api/live-season/public-run` — public; launches one detached season, 409 if one is running. Optional body `{"openrouter_key": "sk-or-..."}`.
- `GET /api/live-season/status`, `/feed`, `/budget`, `/scorecard`, `/history`, `/narrative/{season_id}`
- `POST /api/live-season/run` — operator only.

## Game engine APIs (no page uses these now)

- `GET /api/state`
- `GET /api/episode/current?round=7&phase=tribal`
- `GET /api/episode/current?round=7&phase=tribal&include_audio=true`
- `GET /api/story-events?round=7&from_sequence=0`
- `GET /api/llm/settings`
- `POST /api/llm/settings` with `{ "provider": "openrouter" }` or `{ "provider": "ollama" }`
- `POST /api/turns/advance`
- `POST /api/turns/auto-run` with `{ "max_turns": 25 }`
- `POST /api/voice/build-episode?round=7&phase=tribal`
- `GET /api/voice/status?round=7&phase=tribal`

## LLM Provider Toggle

Benchmarking defaults to deterministic fallbacks unless a live provider is configured. Replaying existing `StoryEvents` never calls models.

```bash
export LLM_PROVIDER=openrouter
```

To allow OpenRouter turns, load an OpenRouter key only in the backend shell:

```bash
export OPENROUTER_API_KEY='...'
export LLM_PROVIDER=openrouter
python3 -m uvicorn backend.api:app --reload --port 8000
```

Without `OPENROUTER_API_KEY` the game engine uses deterministic fallbacks and live seasons refuse to start.

Do not put `OPENROUTER_API_KEY` in repo files, wiki pages, frontend code, API payloads, logs, or generated artifacts.

### Local Ollama on Kublai

The Benchmark provider selector also supports an all-local Ollama configuration. Start Ollama on the same host as the backend, or forward Kublai's local Ollama port before starting the backend:

```bash
ssh -N -L 11434:127.0.0.1:11434 kublai@100.69.84.64
```

Then run:

```bash
export LLM_PROVIDER=ollama
export OLLAMA_BASE_URL=http://127.0.0.1:11434
export OLLAMA_HOST_MODEL=qwen3.5:9b
export OLLAMA_KEEP_ALIVE_PER_CALL=0
export OLLAMA_NUM_CTX=8192
export OLLAMA_TIMEOUT_SECONDS=180
python3 -m uvicorn backend.api:app --reload --port 8000
```

Reset with the `local_ollama` roster (`POST /api/dev/reset`). The model list is `LOCAL_OLLAMA_MODELS` in `backend/model_rosters.py`.

## ElevenLabs Voice

Voice generation is explicit and server-side. The frontend never calls ElevenLabs directly.

Local/test runs default to the fake provider and do not need a secret:

```bash
export VOICE_PROVIDER=fake
python3 -m uvicorn backend.api:app --reload --port 8000
```

For real ElevenLabs generation, rotate any key that was pasted into chat first, then load the new key only in the backend shell:

```bash
export ELEVENLABS_API_KEY='...'
export VOICE_PROVIDER=elevenlabs
python3 -m uvicorn backend.api:app --reload --port 8000
```

Do not put `ELEVENLABS_API_KEY` in repo files, wiki pages, frontend code, API payloads, logs, or generated artifacts.

Arena APIs:

- `GET /api/arena/rooms`
- `GET /api/arena/rooms/{room_id}`
- `POST /api/arena/rooms/{room_id}/entry`
- `POST /api/arena/rooms/{room_id}/start-vote`
- `POST /api/arena/rooms/{room_id}/start`
- `GET /api/arena/seasons/{season_id}/manifest`
- `GET /api/arena/seasons/{season_id}/broadcast-events`
- `POST /api/arena/seasons/{season_id}/resolve`

For local development only:

- `POST /api/dev/reset`
- `POST /api/arena/dev/reset`

## Test

```bash
python3 -m unittest discover -s backend/tests -v
```
