# Rebrand: LLM Survivor becomes Agent Survivor

Danny, 2026-11-25: "after this is done, we need to do a complete rebrand from llm survivor to 'Agent Survivor'. update this everywhere and then check everything thoroughly to make sure there's no remaining trace of llm survivor branding."

| Decision | Choice |
|---|---|
| Address | New `agentsurvivor.kurult.ai`; every `llmsurvivor.kurult.ai` URL permanently redirects (301) to the same path on the new address. |
| Depth | Everything at once: visible text, docs, code identifiers, folders, the launchd job, secrets files, the GitHub repository, Stripe and GitHub app names, the brain and memory. |
| Old seasons | Start fresh: existing seasons are deleted from the site and from production. One compressed backup is kept outside the served tree unless Danny says otherwise. |

Starts when: Kublai's Stripe activation and GitHub OAuth app are done (both are renamed as part of this).

## Name map

| Old | New |
|---|---|
| LLM Survivor (and any casing: "LLM survivor", "llm survivor") | Agent Survivor |
| llm_survivor | agent_survivor |
| llm-survivor | agent-survivor |
| llmsurvivor | agentsurvivor |
| LLM_SURVIVOR_ (env vars: LLM_SURVIVOR_FAST_BENCHMARK, LLM_SURVIVOR_ENV_FILE, ...) | AGENT_SURVIVOR_ (with the old names still read for one release, then removed) |
| LLM SURVIVOR (statement descriptor) | AGENT SURVIVOR |

`SURVIVOR_*` env vars, "Survivor Index", and the word "survivor" alone stay.

## Steps, in order

0. **Quiet.**
   - Message the other sessions (inner-life on cap-fix, report-review on report-v2) with a start time; they commit and pause.
   - No season running; entries stay closed.
   - Take a backup tarball of production's artifacts and the git refs.
1. **Text and identifiers, one commit on production's line.**
   - Apply the name map across every tracked file except `docs/plans/` history, which gets a one-line note instead.
   - Rename files and paths that carry the name: `video/package.json` name, the secrets file names in code, `/api` docs.
   - Keep the old env var names as fallbacks for one release.
   - Full test suite with the network blocked; lint and build.
2. **Redirect in the app.** A middleware sends any request whose Host is `llmsurvivor.kurult.ai` a 301 to `https://agentsurvivor.kurult.ai` plus the same path and query. The Stripe webhook path is excepted: Stripe does not follow redirects, so it keeps working until step 5 moves it. Tests cover the redirect.
3. **New address.**
   - `cloudflared tunnel route dns <tunnel> agentsurvivor.kurult.ai`.
   - Add an ingress rule for `agentsurvivor.kurult.ai` to localhost:18800 in `~/.cloudflared/config.yml`, and the same in `~/.kublai/cloudflared/services.json`.
   - Keep the old rule, so the redirect can answer.
   - Back up the config first, then restart cloudflared.
4. **Folders, service and secrets** (a short outage, a few minutes):
   - Stop the launchd job.
   - `mv ~/projects/llm_survivor_live ~/projects/agent_survivor_live` (the main worktree), then `git worktree repair`. Move each linked worktree with `git worktree move` (`capfix`, `dev`, `report`, `reportpage`), and `llm_survivor_archive` to `agent_survivor_archive`.
   - Rebuild the production `.venv` (its scripts hold absolute paths): `python3.14 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt`.
   - `npm ci` is not needed (node_modules are relative), but check that `video/` renders.
   - Rename the secrets files `llm-survivor.env` to `agent-survivor.env`, and `llm-survivor-stripe.env` and `llm-survivor-github-oauth.env` likewise.
   - New plist `ai.kurult.agent-survivor-live` pointing at the new folder and `run_live.sh`; bootout the old label and bootstrap the new one.
5. **Outside services** (Kublai, in the dashboards):
   - **Stripe:** account name, public business name and website `https://agentsurvivor.kurult.ai`, statement descriptor `AGENT SURVIVOR`; the test and live webhook URLs move to `https://agentsurvivor.kurult.ai/api/entries/stripe-webhook`.
   - **GitHub OAuth app:** name "Agent Survivor", homepage and callback on the new address.
6. **Repository:** `gh repo rename agent-survivor` for Danservfinn/llm-survivor (GitHub redirects the old URL), then update `origin` in every worktree.
7. **Old seasons:**
   - Tar every season folder (production and archive) into one backup under `~/.kublai/backups/agent-survivor/seasons-before-rebrand-20261125.tar.gz`.
   - Delete them from `artifacts/seasons` and the archive folder.
   - Clear `runner_status.json`, `runner.log` and the public ledger.
   - The Methodology page then shows no reference season until the first new one.
8. **Knowledge:**
   - The brain: rename `projects/llm-survivor*` pages and rewrite the ~20 pages that mention it, adding a log entry.
   - Claude memory (`llm-survivor-dev-setup.md` and the MEMORY.md lines).
   - kurultai-memory facts: new facts that supersede the old ones.
   - Kublai's briefs and the Hermes plan, `~/reports/llm-survivor` to `~/reports/agent-survivor`, and the Hermes skill references.
9. **Sweep** (the check Danny asked for):
   - `grep -rniE "llm.?survivor|llmsurvivor"` across every worktree, the brain, `~/.claude`, `~/.hermes` (skills, plans, profiles), `~/.kublai` (bin, state, cloudflared), `~/Library/LaunchAgents`, `~/.cloudflared` and `~/reports`.
   - The live site: every page's HTML, every JS chunk, every API response (`/api/methodology`, `/api/entries/config`, `/api/live-season/*`, `/report`), the share images and a rendered video end card.
   - Stripe and GitHub settings, read back.
   - `curl -I https://llmsurvivor.kurult.ai/some/path` returns 301 to the same path on the new address.
   - Anything left is listed with a reason.

## Kept on purpose (historical records, not branding)

Logs (`~/.cloudflared/logs`, `~/.hermes/**/logs`, runner logs), the kurultai-memory turn and handoff logs, receipts, config backups (`*.bak-*`), Hermes task-workspace copies of old blog posts, and git history. Rewriting them would falsify what happened. The sweep report lists each, so Danny can say if any should go.

## Risks

- **Running sessions:** the folder moves change the other sessions' working directories; hence step 0.
- **Shared tunnel:** a bad edit to `~/.cloudflared/config.yml` takes down every Kurultai hostname. So: back up, validate with `cloudflared tunnel ingress validate`, restart once, and check all hostnames.
- **Stripe webhooks don't follow redirects;** step 2 exempts the webhook path until step 5 moves it.
- **Shared links:** links already posted keep working through the redirect.
