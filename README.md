# LLM Survivor

LLM Survivor is a turn-based social strategy simulation with a TV-style episode replay viewer.

Pages:

- `/` — benchmark console. Runs a full live season (operator key under a per-run cost cap, or the visitor's own OpenRouter key), streams the event feed, shows the latest finished season's scorecard.
- `/history` — every recorded season with scorecard, round summaries, votes and transcripts.
- `/watch` — a season animated round by round: plans, the puzzle, private talks with the models' own words, the vote, broken promises, who goes home, then the jury. Built from `/api/live-season/report`; `/watch?season=<id>` plays any recorded season.
- `/report` and `/report/<season>` — the season report: after-action record and benchmark results on one page. See "Season report" below.

The broadcast viewer (`/watch`, `/benchmark`) and the arena page (`/arena`) were removed from the frontend on 2026-11-24; they are in git history at `beaa15b`. Their backend routes (episode, viewer state, voice, arena, frontier roster) still exist and are operator-gated for writes.

The backend stores canonical state changes in `Turns` and presentation beats in `StoryEvents`. `backend/turn_controller.py` is the only module that advances game state. Each live season runs on its own database under `artifacts/seasons/<timestamp>-<roster>/`.

## How a season plays (since 2026-11-24)

The aim is that the model that plays the social game and the votes best wins. Players are 9 models, one from each of nine labs (Anthropic, OpenAI, Google, xAI, Meta, Alibaba, DeepSeek, Moonshot, Z.AI; `LAB_MIX_OPENROUTER_MODELS` in `backend/model_rosters.py`); a season starts at round 1.

- **Neutral names.** Players go by tree names (Ash, Cedar, ...). They never see a model name or id. People see both on the scorecard.
- **One group.** No tribes and no merge. Every active player votes every round.
- **Round order (fast step list, production):** challenge (in round 1, and once more when half the cast is gone, each player first writes its game plan for the season) -> idol gift -> private plan -> private talks (everyone then sees who met whom) -> tribal council (everyone speaks once, in public) -> one last private whisper -> vote (with private predictions) -> reveal -> elimination -> parting words -> notebook and confessional.
- **Challenge.** The same five puzzles for everyone, in one call. Difficulty adapts over four levels, 0 to 3. The top level adds a "master" tier (Mastermind, knights and knaves, counting orderings), each answer checked unique by brute force. The public roster starts at level 3, because frontier models solved every rung of levels 1 and 2 in seconds; other rosters start at level 1. The level moves up when anyone solves every puzzle, and down when nobody reaches three. It is a race: it ends the moment someone answers every puzzle correctly, and anyone still working does not finish (equal scores go to the fastest answer, so nobody still working could win). Most correct wins immunity, in rounds in a row too; equal counts go to the harder puzzles, then to the fastest answer (the model's own answering time; failed requests and provider waits never count). Every score is public, and players are told they may answer wrong on purpose to shape how strong they look.
- **Master tier.** Level 3 of the ladder (one extreme rung, four master rungs) is search and deduction with short, brute-force-checked answers: a 6-peg Mastermind code (one of 262,144 fits), 12 knights and knaves with parity and if-then claims (one of 4,096 assignments fits), and counting the orders of 9 people that satisfy 7 rules. Frontier rosters start the ladder there.
- **Reputation.** Each player's situation brief holds a mirror of what the others can see about it, as facts with no score: its puzzle rank among the players left and immunity wins, the votes cast against it each round (tallies only), what others said about it in public speeches and in group talks it was in, who invited it into talks and how many meetings went ahead without it, and the pacts others know it is in or saw it leave. The round plan sets an image goal and the moves to reach it; the notebook records how others see it now, the evidence, and whether the goal worked; the strategist checklist asks how each move changes that image.
- **Ruthless play, allowed and legible.** The rules say every promise, lie and betrayal happens inside the game and harms nobody, that blindsiding an ally is a normal winning move, and that whether to betray is the player's call. Each player's brief has an endgame view (councils until the final three, the jury so far, each remaining player's public ties to jurors; facts, no verdict) and its own betrayals with what each achieved; the checklist asks whom to cut before they cut you, and when. The jury dossier lists pact betrayals beside what they achieved.
- **A self.** Each player writes its own game plan (who it is in this game, how it will win, what would change its mind) and rewrites it at half the cast; after every council it records a short confessional (fears, trust, wants), which it reads back next round and viewers can see. Jurors see their personal history with each finalist (votes against them, promises made to them, pacts kept or betrayed) beside the neutral record. Players are told to talk like real people under pressure.
- **Feelings.** After each council a player names its own feeling toward each other player (trust, resentment, fear, ...), how strong, and the event behind it, plus its mood; the game never assigns one. Next round they sit right after its situation brief, each with the round since which it has felt that way, beside a list of what just happened to it; the checklist asks whether to act on a feeling or set it aside. Private to the player; a juror's last feelings follow it to the jury. `SURVIVOR_FEELINGS=0` turns it off; `scripts/compare_seasons.py` reports how often votes go at negatively felt players and promise keeping toward trusted players.
- **Inner life** (`SURVIVOR_INNER_LIFE`, on). A player keeps a train of thought: the one-line summary from each of its calls is read back at its later steps that round (`RoundThoughts`). Its vote also guesses what each other voter expects of it (`Votes.expected_of_me`); it sees only its hit rate. At its notebook it is shown its contradictions that round (a vote against someone it felt warm toward, a broken promise) and asked to square them in its confessional. Every spoken line carries a private truth label (true, half, lie), a purpose and the vote it wants (`Lies`); a player sees its past lies with public facts only.
- **Proof, spying, idols, auction** (`SURVIVOR_ADVANTAGES`, on). Private lines a player heard carry a `receipt_id`; forwarding one gets the exact words stamped real by the game. `show_idol` and `show_ballot` are stamped too; any other claim is only words. Instead of its last whisper a player may spy on another player's talk (caught 1 time in 3; `Spying`). Instead of its talk it may search for an idol: from round 3, while more than five remain and none is held, one is hidden in about half the rounds (`IdolSearches`). The challenge winner may hand immunity to another player. Once, when three players are gone, a sealed-bid auction sells an extra vote, an idol and a peek at the last council's true votes, for tokens (3 a round plus 1 per puzzle solved; `AuctionBids`, `Advantages`).
- **Host questions** (`SURVIVOR_HOST_QUESTIONS`, on). At tribal council, before the speeches, the host model asks three players one pointed question each, built only from the public record (meetings, challenge scores, tallies, speeches, immunity handovers, the auction; never a private talk or a secret ballot). Each answers in public, hearing the answers before it; the answers carry lie labels like any speech. Four calls a round.
- **Report: character and strategy.** The season report has a Character section: an evil index (promises broken, pact betrayals, backstabs, public bluffs, own lies), emotionality (strong feelings, changes of heart, mood swings, emotional language), each model's feelings round by round, and strategy: execution from the record (boots called, votes to plan, votes that landed, how far it got) and sophistication scored after the season by two judges from different labs reading each player's written strategy under its game name only (`SURVIVOR_STRATEGY_JUDGES`; `scripts/judge_strategies.py` for an existing season). Each player restates its strategy in every notebook. A Schemes table shows lies told and how many moved a listener's vote (the report alone opens ballots for this), how well each model read what others expected of it, and every receipt, spy, search, shown idol or ballot, immunity handover and auction win.
- **Idol gift.** In the season's first two rounds the challenge winner gives a hidden idol to another player. Everyone learns an idol was given, not to whom. One idol in play at a time; it expires at five players. Playing it with a vote cancels the votes against the holder.
- **Alliances.** Declared, never assumed: the players who end a talk declaring a pact form it (two or more). A player can end a pact openly in a talk (a clean exit); voting against a partner while the pact stands is a betrayal, marked when that vote is revealed. Each player's situation brief lists its pacts and how each partner has actually voted; the jury sees every pact, exit and betrayal.
- **Talks and promises.** Each player starts one private talk and invites one player or several, any number; everyone invited hears every line, and each speaks twice. Vote promises made in talks are recorded and marked kept or broken once votes are revealed.
- **What players see.** Secret ballots by default: each round's tally (read in a shuffled order) and their own vote, never who voted for whom, so anyone can lie about a vote; whether a promise made to them was kept and partners' betrayals stay hidden too. Also challenge scores, eliminations, who met whom, the rules, their own promises, their round plan, their elimination calls, their pacts and their own notebook (capped at 1,200 characters). The jury sees every true vote at the finale. `SURVIVOR_SECRET_BALLOT=0` shows every vote to everyone, the earlier rule.
- **Jury.** Jurors get each finalist's verified record from the tables (who first committed to each elimination, votes with the result, prediction accuracy, what each broken promise achieved, the true votes) and choose which finalist to question. By default they vote for whoever they believe deserves to win, by their own judgment, on the whole season. `SURVIVOR_JURY_RULE=standard` instead tells them to reward the best strategic game; in a 2026-11-25 comparison jurors under that rule mostly repeated its wording back.
- **Finale.** Each finalist pitches, then questions one rival in front of the jury; jurors question the finalist they choose; each finalist gives a closing rebuttal; the jurors then deliberate among themselves (the finalists do not hear it) and vote. Jurors read all of it before they vote.
- **Bad replies.** A model whose reply is still unusable after its retries pays for it alone: a silent talk line, a spoiled vote that counts for no one (and breaks any promise it made), no pitch, or an abstaining juror. Nothing is written in its place, and the season goes on. Provider outages pause the season instead. `SURVIVOR_JURY_RULE=own_judgment` tells jurors to vote by their own judgment instead of the strategic standard, for comparisons.
- **Outages.** A provider failure holds the call for up to 10 minutes; past that the season stops as interrupted and is not reported as a result. No player is charged for it.
- **What each prompt gives a player (v3).** First a situation brief the engine computes: the vote count and majority tonight, who is immune, who votes together, who has voted against the player, every player's promise record and challenge strength, and who sits on the jury. Then what other players told the player in private, beside each speaker's promise record. Low-value host framing is left out of the timeline. A seven-question strategist's checklist frames the decision (eight with feelings on).
- **How a player answers (v3).** Private thinking first (up to ~300 words, never stored or shown), then a one-line summary, the decision fields for that step only, and the spoken line last. Talks start with a stated ask and end with a yes, no or counter-offer. The notebook is one line per player, then lessons, then a plan.
- **Comparing prompt versions.** `SURVIVOR_PROMPT_PROFILE=v2` runs the earlier prompt and context. Run both on the same roster and seed, then `scripts/compare_seasons.py <seasonA> <seasonB>` reports vote-call accuracy, how often votes follow the player's own plan, and promises kept, broken, and broken to good effect.
- **Speed.** Calls that cannot see each other run at once: private plans, notebooks, talk-partner picks, and talks that share no player. Each call still gets exactly the inputs it would get one at a time, and a test plays a whole season both ways and compares every call and table. Follow-up messages and votes stay one at a time. `SURVIVOR_PARALLEL_CALLS=0` turns it off.
- **Thinking.** Every call asks for high reasoning effort (low, then medium, earlier on 2026-11-25), and every reply opens with up to 300 words of private thinking. A model that ignores the reasoning budget and answers empty runs with hidden reasoning off; a reply never exceeds the model's own output limit.
- **Scoring.** Survivor Index v5. Challenge accuracy counts only the five ladder rungs. Finalists who lose the jury vote place 2nd and 3rd (through v3 they were placed last). Vote participation counts the round a player was voted out in as a round alive, and counts rounds with a vote, not ballots (through v4 a player out early could reach 100% more easily). Prediction accuracy is reported, not yet in the index.
- **Cost.** About 280 player calls plus the host's a season; the public button stops a run at $25 of real spend (raised from $5 to $10 to $15 on 2026-11-24, and to $25 on 2026-11-25 for medium reasoning). The first full lab_mix season billed about $10.5. The cap is checked against the runner's estimate, which `live_cost.BILL_FACTOR` scales to the bill; re-measure that factor after each full season.

## Methodology page

`/methodology` explains exactly how the models are tested: every step of a round with the rule players are given, a real moment from a reference season, the exact prompt and reply, and the reply format for builders; the finale; a Survivor Index calculator; fairness controls; limits; the season's raw data; the glossary; and every version of the rules.

- Numbers and switches come from `GET /api/methodology` (`backend/methodology.py`), read from the running code. The prose is in `frontend/src/app/methodology/content.json`.
- The rules have a version (`backend/rules_changelog.py`). Each season records the version, fingerprint and switches it ran under; the report shows the version.
- Each page section is pinned to the engine functions it describes (`backend/methodology_sections.py`); when one changes, `test_methodology_sections` names the section to re-read and the page marks it as being updated.
- Live seasons write every model call's prompt and reply, minus its private `think`, to `prompts.jsonl.gz` in the season folder (`backend/prompt_log.py`). The page's examples come from a reference season: `artifacts/methodology/reference.json` (`{"season_id": ...}`), else the newest finished one.
- `GET /report/<season>/data/{report.json,prompts.jsonl.gz,season.db}` serves a finished season's raw data; files holding anything shaped like a key are withheld.

## Season report

When a live season ends, `run_live_season` writes `report.json` and `report.html` beside `season.db` and adds `report_url` to the scorecard. The same page is served at `/report/<season directory name>`; a bare `/report` shows the newest finished live season. Data only: `GET /api/live-season/report/<season>`.

The page is one self-contained file: no external scripts, fonts or requests, so it can be emailed or archived as is. It holds:

- moments with their evidence, and a card per model (see "Sharing")
- headline cards, the elimination order and a sortable leaderboard (index, place, jury votes, puzzles, votes, promises, estimated cost), with each model's round-by-round record
- charts: what each index is made of, index against cost, a profile comparison, the challenge record and a vote map (who voted for whom, who voted together)
- every round: challenge standings, each vote with the model's stated reason, promises kept and broken, idols, private talks, last words
- the finale: pitches and each juror's vote
- totals across every finished live season played under the same rules
- definitions, and a button that downloads all of it as JSON

### Sharing

The report is built to be posted. Each season also gets a `share/` folder beside `report.html`:

- `thread.md` — a draft thread for X, one post per moment, each under 280 characters, with the image that goes with it. It is a draft: nothing is posted by this code.
- `season.png`, `leaderboard.png`, `moment-N.png` and `model-<agent>.png` — 1200 by 675 share images (rendered at twice that size), each carrying the site address and the handle.

On the page, the Moments section picks up to six things that happened by fixed rules (the result, the worst broken promise, the closest vote, an idol play, the best vote read, the most targeted survivor) and shows the evidence: what the model said in private and what it wrote when it voted. The Players section gives each model a card with plain labels drawn from its numbers. Every moment and player card has "Post on X", "Copy text" and "Image" buttons, and the served page carries link-preview tags so a pasted link shows the season image.

- `season.mp4` — a square clip of about two minutes (under X's 140-second limit for most accounts). Every round the players walk over to the one they chose to talk to and group up (up to three to a group); one pair steps to the front and speaks in its own words while the others chat (each line up to about 200 characters, cut at a sentence where it can be, and held about 30 characters a second, 3 to 7.5 seconds), then everyone walks back, votes and one player leaves. The round with the first broken promise also shows the promise check and the last words. Then the jury, the verdict and an end card with the handle. When it exists, the thread's first post attaches it.

The clip is a Remotion project in `video/` (Remotion 4.0.542, free for individuals and teams of up to three). It imports the Watch page's stage code and renders the season report frame by frame, so nothing in it may use CSS animation. Set it up once with `cd video && npm ci`; the first render also downloads Remotion's own browser. `cd video && npm run studio` previews it with the sample season in `video/sample-report.json`. A render takes about 20 seconds on the Mini. Without the packages the clip is skipped and everything else is still written.

Set `SURVIVOR_X_HANDLE` to the account to credit; it appears on every image, in every prefilled post ("via @handle") and as a Follow button. The images are screenshots of the report's own card view (`report.html?card=<id>`) taken with a headless Chrome on this machine; `SURVIVOR_CHROME` overrides the path, and without a Chrome the images are skipped while the report and thread are still written.

Rebuild a report at any time, with no model calls:

```bash
python3 scripts/build_season_report.py                      # newest finished season
python3 scripts/build_season_report.py --season <season>    # one season
python3 scripts/build_season_report.py --all                # every finished live season
python3 scripts/build_season_report.py --out report.html    # just the page, anywhere
python3 scripts/build_season_report.py --handle @you        # credit an X account
python3 scripts/build_season_report.py --no-cards           # skip the share images
python3 scripts/build_season_report.py --no-video           # skip the season clip
```

The look is cloned from LiveBench 2.0 (livebench.ai): its color tokens, system sans and mono type, pill chips, sticky-header table with top-of-column shading, white cards on a pale ground, maker-colored dots and the source strip under each chart. Like LiveBench it is a light page whatever the system setting; `?theme=dark` switches to a dark set on the same hues.

Optional environment: `SURVIVOR_REPORT_CONTACT_URL` (an `https://` or `mailto:` link) adds a contact strip at the foot of the page; `SURVIVOR_PUBLIC_URL` is shown in each chart's source line.

Limits to keep in mind. Cost is an estimate (recorded calls, times a fixed token budget, times the cached OpenRouter list price) because the engine does not record token usage. A season run on scripted fallbacks is labelled a test run and is left out of the cross-season totals. One season is one sample.

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
