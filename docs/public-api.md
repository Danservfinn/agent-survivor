# Public vs operator API

## Public GET

- `/api/live-season/status`, `/feed`, `/budget`, `/scorecard`, `/history`, `/narrative/{season_id}`
- `/health`
- `/api/state?view=public`
- `/api/episode/current?view=public`
- `/api/story-events?view=public`
- `/api/game/summary`
- `/api/viewer-state` (GET)
- `/api/model-rosters`
- `/media/voice/*`

## Public POST

- `/api/live-season/public-run` (one season at a time; per-run cost cap in `backend/public_gate.py`)

## Operator POST (Bearer `OPERATOR_TOKEN`)

- `/api/live-season/run`
- `/api/turns/advance`
- `/api/turns/auto-run`
- `/api/game/auto-run-to-end`
- `/api/rounds/next`
- `/api/rounds/preload-next`
- `/api/dev/reset`
- `/api/llm/settings`
- `/api/viewer-state` (POST)
- `/api/voice/build-episode`
- `/api/operator/frontier-roster/preview`
- `/api/operator/frontier-roster/lock`
- `/api/arena/**` POSTs including `/api/arena/dev/reset`

If `OPERATOR_TOKEN` is unset, those POSTs return 503.
