# Plan: a Methodology page that explains exactly how the models are tested

> This file holds two plans. The Methodology page (this section) is the plan under review. The paid agent-entry plan, approved on 2026-11-25 with Phase 0 deployed, is kept unchanged below under "Parked".

## Context

The site tells visitors the game in four short cards. Nobody can see what a model is actually asked, what it can see, how a promise is judged, how ties are settled or how the score is built. Three kinds of reader need that: people arriving from X who want to trust the results, researchers and lab people judging whether the benchmark is fair, and builders who will pay to enter an agent and need exact formats. The game's rules also change often (a dozen changes on 2026-11-24/25, and lies, spying, idol searches and an auction are in progress in another session), so a hand-written page would be wrong within days.

Decided with Danny on 2026-11-25:

| Decision | Choice |
|---|---|
| Readers | All three: curious followers, researchers, builders |
| Layout | One layered page: plain walk-through, fold-out panels for exact rules |
| Name and address | "Methodology" at `/methodology`, in the top menu |
| Backbone | One real round you can step through, then topic sections |
| Examples | Real moments from a season, diagrams in the game's own art, the real prompt and reply, a scoring calculator |
| Staying correct | Prose by hand; every number and switch read from the live game; a test fails when page and game disagree |
| Rule changes | Describe only live rules; show a rules version and date; end with a dated change log; each report links to the rules it was played under |
| Example source | A pinned reference season. Build now against the 24 November season (marked "earlier rules"); pin a fresh season once the new mechanics are deployed |
| Prompts | Record the exact prompt with every reply from now on |
| Builder detail | "For builders" fold-outs on this page; the future Enter page links to them |
| Limits | A plain section on what the benchmark does and does not show |

What exists to build on (checked):

- The game shows every player one numbered rules list: `GAME_RULES`, `_JURY_RULE_TEXT`, `_BALLOT_RULE`, assembled by `game_rules()` in `backend/llm_context.py`. Quoting it on the page means the page cannot contradict the game.
- Rule switches and defaults live in `backend/llm_config.py`; score weights and version in `backend/benchmark_scoring.py`; cost cap in `backend/public_gate.py`.
- The report already has a glossary and FAQ (`backend/report_template.html` `#method`); the README has the fullest prose rules ("How a season plays").
- Reusable drawing pieces: `Avatar`, `Torch`, `Campfire`, `seatLayout`, `juryLines` (`frontend/src/app/season-circle.tsx`), `buildBeats` and `SeasonStage` (`season-stage.tsx`), `ScoreTable` and helpers (`score-table.tsx`). Reusable styles: `.steps`, `.how`, `details.more`, `.terms` (a two-column glossary, defined but unused), `.bench-table`, `.card`.
- Prompts are **not** stored today; only replies are. The only season on disk predates the current rules.
- A new static page at `frontend/src/app/methodology/page.tsx` is served with no backend routing change.

More decisions (second and third rounds of questions):

| Decision | Choice |
|---|---|
| Mismatches between game, rule text and docs | Fix them before the page launches |
| Checking the results | Link the season's full data, its prompt log, and a download of its database |
| Voice | Plain and exact, the report's calm style |

State of the game on 2026-11-25 (production `0e1f17b`):

- A full rule-by-rule reading of the engine was done at `e160b6e`. Since then the other session shipped five phases of new mechanics (train of thought, a private lie record, forwarded quotes, spying, idol search, keep-or-hand immunity, an auction with an extra vote, an idol and a vote peek), behind `SURVIVOR_INNER_LIFE` and `SURVIVOR_ADVANTAGES` (both on). **The page's content must be written from the engine at build time, not from the earlier reading.**
- No season has been played under these rules, and OpenRouter credit is about $1.
- The live service does run the real step list (`run_live.sh` sets `AGENT_SURVIVOR_FAST_BENCHMARK=1`).

## The page

Address `/methodology`, title "Methodology", in the top menu between Watch and Latest report. A sticky contents list at the side; every section has an anchor; an "open all" switch for readers who want everything expanded.

| # | Section | What it holds |
|---|---|---|
| 0 | Header | One paragraph on what is tested. Rules version and date. The reference season, with a banner while it predates the current rules. |
| 1 | The test in one minute | Five facts (nine models, hidden identities, one group, a jury of the players voted out, every word a live model call). A diagram of **who can see what**: a player, the jury, the viewers. |
| 2 | A round, step by step | The backbone: a stepper through one real round of the reference season, drawn with the game's art. One card per step, in the order the engine runs them. |
| 3 | The finale | Pitches, crossfire, jury questions, rebuttal, private deliberation, the vote; the verified record each juror reads; the jury's instruction; ties. |
| 4 | How the score is built | The Survivor Index formula with live weights; each part defined; a **calculator**: pick a player from the reference season and watch the parts add up. Why the winner can differ from the top score. The other reported numbers, the character indexes, and the strategy judges (named). |
| 5 | Fairness controls | Same prompt, same thinking budget, same limits, provider routing, what happens when a model fails (silence or a spoiled vote, never a scripted line), outages (the season is void), what is seeded and what is not. |
| 6 | What this does and doesn't show | One season is one sample. Puzzle ties go to answering speed, which depends on the provider. The prompt tells players lying is legitimate. The host is a separate, cheaper model. Viewers see more than any player. Part of the score is judged by other models. Compare only within one rules version. |
| 7 | Check it yourself | The reference season's report and replay, its full record (JSON), its prompt log, a download of its database. |
| 8 | Glossary | The report's definitions, from one shared source. |
| 9 | Change log | Dated rule changes, newest first, each with its rules version. |

Each step card in section 2 has:

- what happens, in two or three plain sentences;
- a badge for who sees it: only the player / the players in the talk / every player / viewers;
- a frame of the stage for that moment, and a real quote from the reference season;
- three fold-outs: **The exact rule** (the wording players are given, plus edge cases), **The prompt and a real reply** (from the prompt log), **For builders** (reply fields, limits, what a bad answer costs).

Diagrams reuse `Avatar`, `Torch`, `Campfire`, `seatLayout`, `juryLines` and `buildBeats`; fold-outs use `details.more`; the glossary uses `.terms`.

## Keeping it true

Three mechanisms, so the page cannot drift silently:

1. **A facts feed.** `GET /api/methodology` (new `backend/methodology.py`) returns everything numeric or switchable, read from the code at request time: the players' rules list (`llm_context.game_rules()`), switches (`llm_config`), reply fields per step (`openrouter_client.V3_REPLY_FIELDS`), ladder levels and weights (`challenge_bank`), idol and auction constants, token limits, timeout, thinking and routing settings, score weights and version (`benchmark_scoring`), cost cap, host model, strategy judges, roster, rules version, reference season, change log, glossary. The page prints numbers only from this feed. New optional reply fields appear in the builders' fold-outs with no page change.
2. **Prose pinned to code.** Each hand-written section lists the functions it describes (for example the ties section: `_maybe_run_revote`, `_deadlock`) with a hash of their source, in `backend/methodology_sections.json`. A test recomputes the hashes; when a described function changes, the test fails and names the section to re-read. The feed also reports stale sections, so a live mismatch shows "this section is being updated" instead of a wrong rule.
3. **A rules version.** `backend/rules_changelog.py` holds dated entries (version, date, plain summary) and a fingerprint of the rules list, switch defaults, score weights, reply fields and step lists. A test fails if the fingerprint changes without a new entry. Each season stores the version it was played under (`scorecard.json`); the report shows it and links to the change log.

## Engine changes this needs

- **Prompt recording** (new `backend/prompt_log.py`): `_post_chat_completion` appends each call's exact messages and the reply to `prompts.jsonl` in the season folder, labelled through a private `_log` key in the payload (player, step, kind), which is stripped before sending like the existing `_` keys. A file append under a lock, not a database write, so parallel calls stay equivalent. **The private `think` field is not recorded**, keeping the game's promise that it is never stored. Keys are scrubbed with `redact_secrets`.
- **Data downloads**: `GET /report/{season_id}/data/{name}` for `report.json`, `prompts.jsonl.gz` and a copy of `season.db`, only for finished seasons, with a test that scans each file for key-shaped strings before it is published.
- **Reference season pin**: `artifacts/methodology/reference.json`; defaults to the newest finished house season.

`openrouter_client.py` and `live_season.py` are the other session's working files; these edits are small and are agreed with it first.

## Mismatches to fix before launch

Found at `e160b6e`; re-check each at the current head, then send the list to the other session (it owns the engine) and fix the doc-only ones here.

- Rules list says difficulty rises when "two or more" solve everything; the engine raises it when one does (`llm_context.GAME_RULES` vs `turn_controller._ladder_level`).
- Strategy score: the weighting for how decisive a majority was is never applied (`benchmark_scoring.py:196`); the module docstring describes the retired definition.
- Promise status: the report has "unclear" for "I'll vote for you"; the engine marks the same promise kept or broken.
- One seat is flagged immune from seeding until the first challenge (`fixtures.DEMO_AGENTS[0]`).
- Rounds-alive is counted differently for eliminated and surviving players (`benchmark_scoring.py:232-241`).
- Stale text: README (ladder levels, checklist length, jury default), `challenge_bank.py` docstring (guessing game, "speed is never scored"), `public_gate.py` ($15), `live_cost.py` (medium effort), `api.py:247` ("cheap roster").
- Dead code to remove or mark: `archetype_setup` / `request_agent_archetype` (would raise), the canary seat, sudden death and the guessing game.

Launch gate: a test (`test_rules_agree.py`) asserts the fixable ones (rule text vs engine constants).

## Phases

| # | Phase | Main files |
|---|---|---|
| 1 | Agree the facts: re-read the engine at head, send the mismatch list, fix doc-only items | `README.md`, docstrings; message to the other session |
| 2 | Facts feed, rules version, change log, section pins, tests; seasons record their rules version; report shows it | `backend/methodology.py`, `rules_changelog.py`, `methodology_sections.json` (new), `api.py`, `live_season.py`, `season_report.py`, `report_template.html` |
| 3 | Prompt recording and data downloads | `backend/prompt_log.py` (new), `openrouter_client.py`, `live_season.py`, `api.py` |
| 4 | The page, built against the 24 November season with the "earlier rules" banner; prompt fold-outs show "recorded from the next season" | `frontend/src/app/methodology/page.tsx` (+ small components beside it), `globals.css`, `site-nav.tsx`, `page.tsx` (link from the How it works cards), `layout.tsx` |
| 5 | Review: deploy the page unlisted (no menu link) for Danny to read | — |
| 6 | Reference season: top up credit, play one season with recording on, pin it, re-check every example and the mismatch gate, add the menu link | `artifacts/methodology/reference.json` |

All work on a branch in `/Users/kublai/projects/agent_survivor_report`, rebased on production before each deploy. Every deploy needs Danny's yes.

## Verification

- Tests (live venv): `test_methodology_facts.py` (feed builds; every fact the page uses exists, read from a manifest beside the page), `test_methodology_sections.py` (pinned hashes), `test_rules_changelog.py` (fingerprint), `test_rules_agree.py` (launch gate), `test_prompt_log.py` (one line per call, no `think`, no keys, parallel run equals serial), `test_data_downloads.py`; the full suite stays green, including `test_parallel_equivalence.py`.
- Page: `cd frontend && npm run lint && npm run build`; screenshots at desktop and phone width; step through every card; open every fold-out; check the calculator's total equals the report's index for three players; change a weight locally and see the page follow the feed; break a pinned hash and see the "being updated" note.
- Prompt log: play a short season on the cheap roster locally and compare one logged prompt with the text the engine builds for that step.
- After the reference season: every quote on the page opens to its place in the report.

## Needs Danny

- OpenRouter credit for the reference season (up to $25).
- A read of the unlisted page before it gets a menu link.
- A yes for each deploy.

## Choices made on your behalf (say if you disagree)

- The models' private `think` text is not recorded or shown, because the game tells them it is never stored.
- Old versions of the page are not kept; the change log says what changed since any season's rules version.
- The page describes only the public game; operator-only rosters and local test modes are left out.

---

# Entries: two kinds, the model pick first (decided 2026-11-25)

Danny's answers, through the engine session and to Claude Code:

| Decision | Choice |
|---|---|
| Kinds | Both. **Model pick first**: the buyer picks any OpenRouter model to play the frontier (lab-mix) roster. The **$29 own-endpoint entry** (below) follows on the same store, queue, Stripe and /enter. |
| Model pick price | **2 x the estimated season cost, rounded up to the next $5, never below $50.** A hard cap stops a season at 85% of what was paid. The first billed frontier season replaces the estimate's baseline. |
| Price level (confirmed) | Kept at 2x after the estimate came in at $24-28 for a house season: picks cost about $60-80 (DeepSeek V4.1 Flash $60, Sonnet 5.5 $65, Opus 5.5 $80). |
| Routers | **Refused**: models whose catalog price is variable (openrouter/auto, routers) cannot be entered. |
| Which models | **Any OpenRouter model that passes a live check** before payment: one real call in the game's format, and a catalog price (no price, refused). Re-checked at dispatch; refunded if it vanished or got dearer than paid for. |
| Stripe | A separate "Agent Survivor" account in the same organization as Parse's, being set up by Kublai (credentials: `~/.kublai/secrets/agent-survivor-stripe.env`). |
| Ownership | Claude Code owns the entry flow (store, queue, Stripe, /enter, status page, leaderboard bucket). The engine session builds `live_cost.estimate_entry_season_cost(model_id, roster)` and `run_live_season(roster, entry={model_id, name, entry_id})`. |

Model-pick differences from the endpoint plan below: no endpoint client, SSRF rules or circuit breaker; no embargo (we make every call, so live viewing is safe); entry seasons stay off the home page's "latest" and get their own leaderboard bucket. If the picked model's lab is in the roster, the entry replaces that lab's seat; `entry.json` names the seat's agent_id.

---

# Parked: let anyone enter their own agent in a season (and show lab logos)

Approved 2026-11-25. Phase 0 (lab logos, one line per jury vote) is deployed. Phases 1 to 5 are pending and unchanged by the Methodology plan, except that the Methodology page's "For builders" fold-outs become the reference the Enter page links to, and the engine's new optional reply fields (lies, receipts, spying, idol search, auction; other session's work on `cap-fix`) reach an entry's endpoint with no protocol change because the entry gets the same prompts as a house model.

## Context

Agent Survivor seasons are played only by nine house models today. The goal is revenue and reach: let anyone bring their own AI agent, pay an entry fee, and get a full season of it against the house models, with the report, the round-by-round replay and the share video that already exist. Danny also asked for the animation to show each lab's logo in place of the generated robot.

Decided with Danny on 2026-11-25:

| Decision | Choice |
|---|---|
| What an agent is | The submitter's own HTTP endpoint. We send it each game prompt; it answers. |
| Who pays | The submitter, by Stripe Checkout, before the season runs (default $29, set by env). No prizes or payouts: it is a paid service. |
| Who may enter | Anyone. No accounts, no approval. Automatic checks and payment are the gate. |
| Season make-up | One entry plays the house models. Each submitter gets their own season. |
| Player pictures | Lab logos in the animation, not robots. |

What the code has today (checked against the repo at `/Users/kublai/projects/agent_survivor_report`):

- Every model call goes through `_post_chat_completion(payload, api_key)` (`backend/openrouter_client.py:1519`). `payload["model"]` is the seat's model id at every call site, so a call can be routed by an `entry/` prefix with no signature or schema change.
- A season starts from `/api/live-season/public-run` → `scripts/run_public_season.py` → `live_season.run_live_season(roster=…)`. There is no way to pass a custom player. One season runs at a time (process-global `database.DATABASE_PATH`).
- `backend/arena.py` is bookkeeping only (it never ran a game; its payment is faked). Only its name trim and `_looks_like_secret` check are worth copying.
- There is no rate limit, sign-in or private link anywhere. `/api/live-season/report/{id}` serves a *running* season in full: every vote and private talk.
- The cross-season leaderboard merges players by model id (`season_report._cross_season`).

## How it works for the submitter

1. **Describe.** On a new `/enter` page: agent name, endpoint address (https), optional access token, optional model name (so a hosted OpenAI-compatible provider can be entered with no code).
2. **Test (free).** We send three sample game prompts (a private talk, a vote, a puzzle round) and show pass or fail for each. Payment is offered only after a pass.
3. **Pay.** Stripe Checkout. Stripe holds the email; we do not store it.
4. **Wait.** A private status page (`/entry?t=<token>`) shows queue position, then the round and players left. The link is also in the Stripe receipt.
5. **Results.** When the season ends the page links the report, the replay and the video. Results are public.

**No live viewing of an entry season, for anyone.** The entry's endpoint is on the internet, and our own site shows every vote and private talk of a running season. If the season were visible while it runs, the entry could read the other players' secret ballots. So an entry season is hidden from every public page and API until it ends.

## Design

### A. Protocol: OpenAI-compatible chat completions

We `POST {base}/chat/completions` with the same messages a house model gets and read `choices[0].message.content`, which must hold the JSON object the prompt asks for. Headers name the season, the player's alias and the step, so an agent can keep its own memory. A starter (`examples/entry_agent.py`, FastAPI wrapping any model) and `docs/entry-protocol.md` go with it. Tools, memory and bigger models are allowed; entries score on their own board.

### B. A seat that calls an endpoint

- The entry's model id is `entry/<slug>`. `_post_chat_completion` routes on that prefix to a new adapter, `_post_entry_chat(payload)`, before the provider switch. No new `Agents` columns (`/api/state` returns raw rows, so new columns would be public).
- New `backend/entry_endpoint.py` (standard library only): `validate_base_url`, `register` / `unregister` (a registry keyed by model id, loaded before the season; jobs stay database-free), `post_chat`, `stats`, the circuit breaker, its own exception. The adapter converts every failure to `InvalidModelReply` and returns the normal shape with `_model_ms`.
- The address and token never enter `season.db`, reports, logs, argv, env or API replies. Error text carries a category and HTTP status only.

### C. Calling a stranger's address safely

Production is a Mac Mini on a home network. The client refuses anything that is not a public web server:

- https, port 443, host names only (no IP literals), IPv4 only in v1;
- every resolved address must be `is_global and not is_multicast` (plain `is_private` misses 100.64.0.0/10, the carrier-NAT and Tailscale range);
- connect to the checked address with the right SNI, so a DNS change cannot redirect the call;
- no redirects, no proxy settings, a total deadline of 90 seconds, reply capped in bytes and at about 6 characters per allowed token.

`127.0.0.1` is allowed only through an in-process flag set by tests and the operator script.

### D. When the endpoint fails

- Every endpoint failure is the entry's own failure (`InvalidModelReply`): a silent line or a spoiled vote. It must never be any other error type: `_camp_talks_parallel` re-raises anything else (`turn_controller.py:2018-2020`) and the season would crash.
- **Circuit breaker (required).** Without it one dead endpoint costs over 20 minutes per vote step (retries × 90 seconds). Three failures in a row open it for 120 seconds; three trips, or 15 minutes of failed-call time in total, open it for the rest of the season. The report says how often the entry failed to answer.

### E. The season

- Nine seats: the entry replaces one lab seat, chosen by the season seed so it rotates. Nine keeps the placement scale, jury size, length and cost of a house season. Maker: "Community".
- `run_live_season(roster="lab_mix_openrouter", entry=…)`; no new roster name (the roster tuple and API pattern are pinned by tests). The season folder is `<timestamp>-lab_mix_entry` with an `entry.json` (public id, name, replaced lab; no secrets).
- Agent names: at most 20 characters, plain characters, not equal to a tree alias or a house model name (`test_llm_context.py:697-703`), and screened with `_looks_like_secret`.
- The entry seat costs us nothing in model fees: price `entry/` ids at 0 in `live_cost.estimate_call_cost_usd` and `season_report._call_cost_usd`, and leave them out of the host-step median in `turn_controller._estimated_step_cost_cents`.
- Puzzle ids, seats and aliases derive from the seed. Add a per-season secret salt (`SURVIVOR_SEED_SALT`) to the shuffles for entry seasons so an entrant cannot work out the answers or who is who.

### F. Entries store, queue and dispatcher

- `backend/entries_store.py`: its own SQLite file at `~/.kublai/state/agent-survivor/entries.db` (mode 0600), never through `DATABASE_PATH`. Tables `Entries`, `StripeEvents`, `Settings`. The endpoint token is wiped at any end state and when a draft expires.
- Statuses: `draft → tested → awaiting_payment → paid → queued → running → finished | failed | refunded | expired`.
- `backend/entry_queue.py`: a dispatcher thread in the API process. It starts the next paid entry when no season runs, by launching the **existing** `scripts/run_public_season.py --entry-spec <0600 file>` (the one-at-a-time check greps for that script name). The runner reads and deletes the spec file and reports only through `scorecard.json` and `runner_status.json`; the dispatcher reconciles, refunds, and rebuilds its state after an API restart.
- Paid entries go before the free public button (which returns 409 while one is queued). Queue cap about 8, counting unpaid checkouts. An operator kill switch pauses entries.

### G. Free test

`backend/entry_preflight.py` runs as a background job that the page polls (three 90-second calls would exceed Cloudflare's timeout). It builds the three prompts with the real prompt builders from a canned fixture and checks replies with the real parsers. It returns a category and status only, never the endpoint's reply body. Limits (`backend/rate_limit.py`): per visitor (`CF-Connecting-IP`), per target host, per day overall, and three jobs at once.

### H. Stripe

- **Account: the existing Kurultai Stripe account** (Danny, 2026-11-25). On this Mac it is the account Parse uses: Stripe CLI login named "Parse", keys in Parse's production `.env`. Confirm with Danny that this is the one before Phase 3. Do not reuse Parse's secret key: create a restricted key for Agent Survivor only (Checkout Sessions write, Refunds write, Events read), its own webhook endpoint and signing secret, and a product "Agent Survivor season entry". Set a statement descriptor suffix (`AGENT SURVIVOR`) so buyers recognise the charge. The Stripe CLI login expired 2026-11-07; `stripe login` again before testing webhooks.
- **What Kublai found (2026-11-25):**
  - The account is acct_1Snq0B8LghiREdMS, the LLC's only known Stripe account, shared by Parse Agents and Parse Media.
  - Restricted keys can only be made in the dashboard, so Danny makes them. Don't create webhooks with Parse's key. The CLI login needs Danny to approve the pairing. No working test key exists on the Mac.
  - Every webhook endpoint on the account receives every checkout event. Ours must ignore sessions without our own metadata marker, still return 2xx, and never set the metadata keys `apiKeyId` or `tier`, which Parse's handler acts on.
  - Checkout shows "Parse" unless the session sets `branding_settings.display_name`. The statement descriptor becomes "PARSE* AGENT SURVIVOR". The business profile URL is parsethis.ai. Stripe Tax is on, with no registrations.
  - Code to copy: `~/parse-for-agents-live/src/stripe.ts` and `src/routes/billing.ts`. Test-mode steps are in `docs/staging-checkout.md`. Kublai's Stripe skill is read-mostly.
  - Pin the API version on both the endpoint and the client. Keep the secrets in one 0600 file with no `.bak` copies. After any key roll, confirm a real webhook delivery.
- `backend/entry_payments.py`, standard library only: `create_checkout_session`, `verify_webhook`, `refund`. A restricted key, a sandbox first, a pinned API version, idempotency keys from the entry id, sessions that expire in 30 minutes.
- Webhook `POST /api/entries/stripe-webhook` verifies the raw body, is safe to receive twice, and fulfils on `checkout.session.completed` and `checkout.session.async_payment_succeeded` when paid. The status page also confirms with Stripe as a fallback.
- Stripe keys live in a **separate** secrets file read only by the payments module, so the process that calls strangers' endpoints never holds them.
- **Refund in full:** provider outage, cost cap reached, runner crash after one retry, endpoint unreachable at the check just before the start (after three tries), or queued over 48 hours. **No refund:** the endpoint failed during the season; the report is still delivered.

### I. Embargo, reports and leaderboards

- `_embargoed(season_dir)` (has `entry.json`, no `scorecard.json`) blocks the feed, report, narrative, history and `/report/{id}` for a running entry season.
- `latest_report`, `latest_season_dir` and `reportable_season_dir` skip entry seasons, so a stranger's season never becomes the home page.
- `_rules_of` gets a third bucket for seasons with an `entry/` player, so the house leaderboard is unchanged. Rows carry `entry: true`; a community board ranks entries. Add "Community" to `ORG_NAMES` and both colour maps.
- An operator "unpublish" switch hides a finished entry season.

### J. Frontend

New static pages `frontend/src/app/enter/page.tsx` (describe → test → pay, protocol notes, starter, terms line) and `frontend/src/app/entry/page.tsx` (status; no-referrer). Nav item, home and report-footer calls to action, an "entry" badge and filter on `/history`, new form styles in `globals.css`.

### K. Lab logos in place of the robot avatars

- **Where:** the three places that draw players with `Avatar` from `frontend/src/app/season-circle.tsx`: the home replay, `/watch` (`season-stage.tsx`) and the video (`video/src/SeasonClip.tsx`). One change to the shared drawing covers all three.
- **How:** the logo sits in a white round tile with a ring in the lab's colour. The tile keeps the dimming, speaking ring, winner ring, voted-out state and the alias letter badge (which also tells two players from one lab apart).
- **Which logo:** from the model id's prefix, the key `orgColor` already uses (`score-table.tsx:25`). The nine public-roster labs first, then the other rosters.
- **Source:** inline SVG in a new `frontend/src/app/lab-logos.tsx` (no network fetch, so it works in the static site and the Remotion render). Each mark comes unaltered from the lab's brand kit or the `lobe-icons` set, with source and licence recorded in `frontend/src/app/LOGOS.md`. Verify each file when building; never draw one from memory.
- **Fallback:** a lab with no logo on file keeps the robot. A submitted entry keeps the robot with a "community" ring, so entries stand out and no uploaded image needs moderating.
- **Trademarks:** the marks are used only to say whose model is playing. Add to the footer and the video end card: "Logos belong to their owners. Agent Survivor is not affiliated with any lab." Removing a lab from the module puts its robot back.
- **Not in scope:** the report page's dots and the share cards.

## Phases

Each phase ships and is checked on its own. Every production deploy needs Danny's yes.

| # | Phase | Main files |
|---|---|---|
| 0 | Lab logos, plus the pending "one line per jury vote" fix | `lab-logos.tsx`, `LOGOS.md`, `season-circle.tsx`, `season-stage.tsx`, `video/src/SeasonClip.tsx`, `layout.tsx` |
| 1 | Engine: an operator runs an entry season against a local stub | `entry_endpoint.py` (new), `openrouter_client.py`, `model_rosters.py`, `database.py`, `live_cost.py`, `turn_controller.py`, `live_season.py`, `scripts/run_public_season.py`, `examples/entry_agent.py`, `docs/entry-protocol.md` |
| 2 | Store, free test, queue, embargo, report labels | `entries_store.py`, `entry_preflight.py`, `entry_fixtures.py`, `entry_queue.py`, `rate_limit.py` (all new), `api.py`, `season_report.py`, `benchmark_scoring.py`, `season_story.py`, `report_template.html`, `AGENTS.md` |
| 3 | Stripe sandbox: checkout, webhook, refunds | `entry_payments.py` (new), `api.py`, `entry_queue.py` |
| 4 | Frontend | `enter/page.tsx`, `entry/page.tsx` (new), `site-nav.tsx`, `page.tsx`, `history/page.tsx`, `score-table.tsx`, `globals.css` |
| 5 | Rollout: sandbox keys end to end on production, then live keys | secrets files, Stripe dashboard |

All backend paths are under `backend/`; all frontend paths under `frontend/src/app/`.

Reuse, do not rebuild: `_ssl_context()` and `_parse_json_content` (`openrouter_client.py`), the launch block in `api.py:276-299` (factor into `_launch_runner(extra_args)`), `_looks_like_secret` (`arena.py:620`), `redact_secrets` (`llm_config.py:146`), `_ensure_column` is **not** needed.

Tests that must stay green unchanged: `test_live_season.py` (roster tuple), `test_public_gate.py` ($25 cap, nine labs), `test_parallel_equivalence.py`, `test_model_failures.py`, `test_llm_context.py`.

## Verification

Run everything in the report worktree with the live venv: `/Users/kublai/projects/agent_survivor_live/.venv/bin/python -m unittest discover -s backend/tests`.

- **Phase 0:** `cd frontend && npm run lint && npm run build`; render stills of the video (`npx remotion render … --frames=…`) and screenshots of `/` and `/watch`; check nine logos, six jury lines, and the robot fallback with an unknown model id.
- **Phase 1:** new `test_entry_endpoint.py` (every blocked address range, redirect refusal, DNS change, size and time limits, breaker) and `test_entry_season.py` (a full season with a local stub, serial and parallel; a dead endpoint and the season still ends; the token and host appear in no file under the season folder nor in captured output). By hand: run the starter, run the runner with a spec file on the cheap roster, kill the stub mid-season.
- **Phase 2:** tests for the state machine, rate limits and embargo. By hand: create, test and force-queue an entry on a local API; while it runs the feed and report return the embargo reply; afterwards the house leaderboard is unchanged.
- **Phase 3:** tests for the webhook signature (good, bad, stale, repeated event) and every refund rule. By hand: `stripe listen --forward-to` the webhook, pay with a test card, force an interrupted season and see the refund.
- **Phase 4:** lint and build, then walk `/enter` to `/entry?t=` against the local API.
- **Phase 5:** one sandbox entry through the public hostname (also confirms Cloudflare lets Stripe's webhook through), then one live $29 entry by Danny, refunded by hand.

## Needs Danny

- Stripe: use the existing Kurultai account (decided). Confirm it is the account Parse uses, then create the Agent Survivor restricted keys (sandbox and live) and register the webhook address.
- Terms wording on `/enter`: sell "a benchmark run, report and video" (never "win", "prize" or "bet"); the refund rules above; a licence to publish the agent's output; acceptable use.
- Whether to collect sales tax or VAT (Stripe collects none without a registration).
- Confirm whether the repo is public; if it is, the seed salt in section E matters more.
- A yes for each production deploy and for the new secrets files.

## Risks kept in view

- A season takes about 1 hour 45 minutes, so eight queued entries is a 14-hour wait; the page must show the expected wait before payment.
- The entrant's tunnel may die between payment and start: checked just before the start, retried three times, then refunded.
- A Mini reboot mid-season: one automatic retry, then a refund.
- Offensive agent names on our domain and in the video: name rules, the unpublish switch, and entry seasons never featured on the home page.
