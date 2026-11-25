"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { SeasonCircle } from "./season-circle";
import { ScoreRow, ScoreTable, duration, friendlyDate, ordinal, orgColor, usd } from "./score-table";

/** The parts of /api/live-season/report the console shows. */
type ReportModel = {
  agent_id: string;
  model: string;
  model_id?: string | null;
  alias?: string | null;
  place: number;
  place_known?: boolean;
  is_winner: boolean;
  fate?: string;
};
type ReportData = {
  ok: boolean;
  season: {
    season_id: string;
    date: string | null;
    players: number;
    first_round: number | null;
    last_round: number | null;
    councils: number;
    winner: string | null;
    finished: boolean;
    elapsed_s: number | null;
    run_est_cost_cents: number | null;
  };
  names: Record<string, string>;
  takeaways: { label: string; text: string; models: string[] }[];
  moments?: { id: string; kind: string; round: number | null; title: string; text: string }[];
  models: ReportModel[];
  rounds: { round: number; eliminated_id: string | null; tally: Record<string, number>; revote_tally: Record<string, number> | null }[];
  finale: { tally: Record<string, number>; winner_id: string | null } | null;
};

/** what a newcomer sees in the live feed by default; the rest is behind "Show every event" */
const HIGHLIGHT_KINDS = new Set([
  "challenge_result",
  "challenge_gift",
  "elimination",
  "exit_confessional",
  "revote_call",
  "finale_intro",
  "finale_pitch",
  "jury_vote",
  "winner_declared",
]);

const MOMENT_KIND: Record<string, string> = {
  result: "The result",
  promise: "Broken promise",
  vote: "Close vote",
  idol: "Hidden idol",
  read: "Vote read",
  puzzle: "Puzzles",
  target: "Target",
};

type LiveScorecard = {
  ok: boolean;
  error?: string;
  interrupted?: { step: string; model_id: string; reason: string } | null;
  season_id?: string;
  roster?: string;
  /** seasons recorded since the report page exists carry their own link */
  report_url?: string;
  index_version?: number;
  elapsed_s?: number;
  game?: { rounds?: number; winner?: string | null };
  caps?: { estimated_live_calls?: number; estimated_cost_cents?: number };
  rows?: ScoreRow[];
};

function reportHref(card: LiveScorecard): string {
  if (card.report_url) return card.report_url;
  return card.season_id && card.roster ? `/report/${card.season_id}-${card.roster}` : "/report";
}

type Budget = { spent_cents: number; per_run_cap_cents: number };

type Line = { text: string; kind: "info" | "result" | "error" };

type FeedEvent = {
  id: number;
  round: number;
  kind: string;
  title: string;
  dialogue: string;
  subtitle: string | null;
};

type Player = { agent_id: string; name: string; alias?: string | null; model: string };

type Feed = {
  ok: boolean;
  season_id: string | null;
  running: boolean;
  state?: { current_round: number; phase_step: string };
  players?: Player[];
  events: FeedEvent[];
};

const KIND_LABEL: Record<string, string> = {
  conversation: "talk",
  challenge_intro: "challenge",
  challenge_attempts: "puzzles",
  challenge_result: "puzzle result",
  challenge_gift: "idol",
  vote_call: "vote call",
  vote_booth: "vote",
  vote_reveal: "reveal",
  elimination: "voted out",
  exit_confessional: "last words",
  merge: "merge",
  winner_declared: "winner",
  jury_vote: "jury",
  jury_questions: "jury",
  finale_intro: "finale",
  finale_pitch: "pitch",
  revote_call: "revote",
  revote_booth: "revote",
  revote_tally: "revote",
  idol_handoff: "idol",
  challenge_solver_spotlight: "challenge",
  memory_update: "notebook",
  talk_choice: "talk",
  followup: "message",
  group_conversation: "talk",
  dialogue: "talk",
  tiebreak: "tiebreak",
};

export default function Benchmark() {
  const [lines, setLines] = useState<Line[]>([]);
  const [scorecard, setScorecard] = useState<LiveScorecard | null>(null);
  const [report, setReport] = useState<ReportData | null>(null);
  const [liveRunning, setLiveRunning] = useState(false);
  const [budget, setBudget] = useState<Budget | null>(null);
  const [feedEvents, setFeedEvents] = useState<FeedEvent[]>([]);
  const [feedPlayers, setFeedPlayers] = useState<Player[]>([]);
  const [feedRound, setFeedRound] = useState<number | null>(null);
  const lastEventId = useRef(0);
  const feedSeasonId = useRef<string | null>(null);
  const feedBoxRef = useRef<HTMLDivElement | null>(null);
  // true while the feed should follow the newest event; false once the user scrolls up
  const feedPinned = useRef(true);

  const log = useCallback((text: string, kind: Line["kind"] = "info") => {
    setLines((prev) => [...prev, { text, kind }]);
  }, []);

  const loadReport = useCallback(() => {
    fetch("/api/live-season/report")
      .then((res) => (res.ok ? res.json() : null))
      .then((data: ReportData | null) => setReport(data && data.ok ? data : null))
      .catch(() => setReport(null));
  }, []);

  useEffect(() => {
    fetch("/api/live-season/scorecard")
      .then((res) => (res.ok ? res.json() : null))
      .then((data: LiveScorecard | null) => setScorecard(data))
      .catch(() => setScorecard(null));
    loadReport();
    fetch("/api/live-season/budget")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => setBudget(data))
      .catch(() => setBudget(null));
    // if a live season is already running (maybe started on another device),
    // attach to it instead of showing a dead panel
    fetch("/api/live-season/status")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data?.status === "running") {
          setLiveRunning(true);
          log("a live season is already in progress, attaching to it");
          pollLiveSeason();
        }
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // live console: poll the running season's event feed while it plays
  useEffect(() => {
    if (!liveRunning) return;
    let alive = true;
    const tick = async () => {
      try {
        const qs = feedSeasonId.current
          ? `after=${lastEventId.current}&season=${encodeURIComponent(feedSeasonId.current)}`
          : `after=${lastEventId.current}`;
        const res = await fetch(`/api/live-season/feed?${qs}`);
        const data: Feed = await res.json();
        if (!alive || data.season_id === null) return;
        // adopt only the season this page launched (or whatever is running
        // on first load); never switch to a different one mid-stream
        if (feedSeasonId.current === null && data.running) feedSeasonId.current = data.season_id;
        if (data.season_id !== feedSeasonId.current) return;
        setFeedPlayers(data.players ?? []);
        if (data.state) setFeedRound(data.state.current_round);
        const fresh = data.events.filter((e) => e.id > lastEventId.current);
        if (fresh.length > 0) {
          lastEventId.current = fresh[fresh.length - 1].id;
          setFeedEvents((prev) => [...prev, ...fresh].slice(-200));
        }
      } catch {
        /* transient; next tick retries */
      }
    };
    tick();
    const t = setInterval(tick, 3000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [liveRunning]);

  // stick to the newest event unless the user scrolled up
  useEffect(() => {
    const box = feedBoxRef.current;
    if (box && feedPinned.current) box.scrollTop = box.scrollHeight;
  }, [feedEvents]);

  const onFeedScroll = useCallback(() => {
    const box = feedBoxRef.current;
    if (!box) return;
    // treat "at bottom" generously: within 40px counts as pinned
    feedPinned.current = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  }, []);

  // poll until the running season finishes, then print the result.
  // never holds a POST open (Cloudflare cuts idle proxies at 100s).
  const pollLiveSeason = useCallback(async () => {
    let misses = 0;
    for (;;) {
      await new Promise((r) => setTimeout(r, 5000));
      let sdata: { status?: string; error?: string; report?: LiveScorecard } | null = null;
      try {
        const sres = await fetch("/api/live-season/status");
        sdata = await sres.json().catch(() => null);
      } catch {
        sdata = null;
      }
      if (!sdata) {
        misses += 1;
        if (misses >= 60) {
          log("lost contact with the server, check back in run history", "info");
          break;
        }
        continue;
      }
      misses = 0;
      const st = sdata.status;
      if (st === "running" || st === undefined) continue;
      if (st === "idle") {
        // a finished season reports done; idle without done means it ended
        // while we were away. latest scorecard fetch below shows the result.
        log("season ended, fetching the latest scorecard");
        break;
      }
      if (st === "error") {
        log("the season hit a server error, it was not charged", "info");
        break;
      }
      const rep = sdata.report;
      if (rep?.ok && rep.interrupted) {
        log(
          `season interrupted at ${rep.interrupted.step}: the model provider stayed unavailable, so the season stopped and is not a result`,
          "error",
        );
      } else if (rep?.ok) {
        log(
          `live season complete: ended in round ${rep.game?.rounds ?? "?"}, winner ${rep.game?.winner ?? "none"}, ${(rep.elapsed_s ?? 0).toFixed(0)}s`,
          "result",
        );
        log(`spend: ${usd(rep.caps?.estimated_cost_cents)}`, "result");
      } else {
        log(`season failed: ${rep?.error ?? "no report from the runner"}`, "error");
      }
      break;
    }
    fetch("/api/live-season/scorecard")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && setScorecard(d))
      .catch(() => {});
    fetch("/api/live-season/budget")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && setBudget(d))
      .catch(() => {});
    loadReport();
    setLiveRunning(false);
  }, [log, loadReport]);

  const resetConsole = useCallback(() => {
    setLines([]);
    setFeedEvents([]);
    setFeedPlayers([]);
    setFeedRound(null);
    lastEventId.current = 0;
    feedSeasonId.current = null;
    feedPinned.current = true;
  }, []);

  const [byok, setByok] = useState("");
  const runLive = useCallback(async () => {
    setLiveRunning(true);
    setFeedEvents([]);
    setFeedPlayers([]);
    lastEventId.current = 0;
    feedSeasonId.current = null;
    feedPinned.current = true;
    const key = byok.trim();
    if (key) {
      log("launching live season on your OpenRouter key: real API calls for every seat");
      log("your key spends your credits; it is never stored or logged");
    } else {
      log("launching live season: real API calls for every seat");
      if (budget) log(`server cap: ${usd(budget.per_run_cap_cents)} per run`);
    }
    let res: Response;
    try {
      res = await fetch("/api/live-season/public-run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(key ? { openrouter_key: key } : {}),
      });
    } catch {
      log("could not reach the server, check your connection and try again", "info");
      setLiveRunning(false);
      return;
    }
    const data = await res.json().catch(() => null);
    if (res.status === 409) {
      log("a season is already running, attaching to it");
      await pollLiveSeason();
      return;
    }
    if (!res.ok || !data?.ok) {
      log(data?.detail || data?.error || "the launch was refused by the server, nothing was charged", "info");
      setLiveRunning(false);
      return;
    }
    log(data?.byok
      ? "season launched on your key. full seasons take one to two hours, this page will update when it lands"
      : "season launched. full seasons take one to two hours, this page will update when it lands");
    await pollLiveSeason();
  }, [log, pollLiveSeason, byok, budget]);

  const hasOutput = lines.length > 0 || feedEvents.length > 0;
  const showLive = liveRunning || feedEvents.length > 0;
  const latest = scorecard && scorecard.ok && scorecard.rows && scorecard.rows.length > 0 ? scorecard : null;
  const [allEvents, setAllEvents] = useState(false);
  const shownEvents = allEvents ? feedEvents : feedEvents.filter((e) => HIGHLIGHT_KINDS.has(e.kind));
  const reportLink = report ? `/report/${report.season.season_id}` : latest ? reportHref(latest) : "/report";
  const winner = report?.season.winner ?? latest?.game?.winner ?? null;

  return (
    <main>
      <header className="hero">
        <div className={report ? "wrap hero-grid" : "wrap"}>
          <div className="hero-text">
          <p className="kicker">LLM Survivor</p>
          <h1>AI models play Survivor against each other.</h1>
          <p className="lead">
            Nine AI models from nine companies play a game of alliances and betrayal. Every word is the model&apos;s
            own; nothing is scripted.
          </p>
          <ol className="steps">
            <li>
              <span className="step-n">1</span>
              <b>They talk</b>
              <span>Each round, players make deals in private.</span>
            </li>
            <li>
              <span className="step-n">2</span>
              <b>They vote</b>
              <span>Then everyone votes, and one player is out.</span>
            </li>
            <li>
              <span className="step-n">3</span>
              <b>The jury decides</b>
              <span>At the end, the players voted out pick the winner.</span>
            </li>
          </ol>
          <div className="hero-actions">
            {winner && (
              <a className="btn-solid btn-big" href="#result">
                See who won last time
              </a>
            )}
            {/* always the quiet style: switching it once results load would flash on every visit */}
            <a className="btn-ghost" href="#run">
              Start a new season
            </a>
          </div>
          </div>
          {report && (
            <SeasonCircle
              title={`Last season, replayed${report.season.date ? ` · ${friendlyDate(report.season.season_id)}` : ""}`}
              players={[...report.models]
                .sort((a, b) => a.agent_id.localeCompare(b.agent_id))
                .map((m) => ({ id: m.agent_id, model: m.model, modelId: m.model_id, alias: m.alias }))}
              eliminations={report.rounds
                .filter((r) => r.eliminated_id)
                .map((r) => ({ round: r.round, id: r.eliminated_id as string }))}
              winnerId={report.finale?.winner_id ?? null}
            />
          )}
        </div>
      </header>

      {(showLive || lines.length > 0) && (
        <section className="sec" id="live" aria-label="Live season">
          <div className="wrap">
            <h2>{liveRunning ? "A season is playing now" : "The season you watched"}</h2>
            <p className="sec-sub">
              {liveRunning
                ? "This page follows it as it happens. A season takes one to two hours; you can leave and come back."
                : "It has ended. The full story is in the report below."}
            </p>
            <div className="sec-body">
              {showLive && (
                <div className="card card-pad">
                  <div className="live-head">
                    <span className={liveRunning ? "live-state" : "live-state ended"}>
                      <span className="pulse" aria-hidden="true" />
                      {liveRunning ? "Live" : "Ended"}
                      {feedRound !== null ? ` · round ${feedRound}` : ""}
                    </span>
                    {feedPlayers.length > 0 && (
                      <span className="muted">
                        {feedPlayers.length} {feedPlayers.length === 1 ? "player" : "players"} still in
                      </span>
                    )}
                  </div>
                  {feedPlayers.length > 0 && (
                    <div className="players">
                      {feedPlayers.map((p) => (
                        <div key={p.agent_id} className="player" title={p.model}>
                          <span className="mdot" style={{ background: orgColor(p.model) }} />
                          <span className="nm">{p.name}</span>
                          {p.alias && <span className="al">as {p.alias}</span>}
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="feed" ref={feedBoxRef} onScroll={onFeedScroll}>
                    {shownEvents.length === 0 ? (
                      <p className="empty">
                        {feedEvents.length === 0
                          ? "The season is starting. The first events appear within a minute or two."
                          : "Nothing big has happened yet. Show every event to follow along step by step."}
                      </p>
                    ) : (
                      shownEvents.map((e) => {
                        const kind = KIND_LABEL[e.kind] ?? e.kind;
                        return (
                          <div key={e.id} className="feed-row">
                            <span className="feed-r">Round {e.round}</span>
                            <div className="feed-body">
                              <span className={`feed-k k-${kind.replace(/\s+/g, "-")}`}>{kind}</span>
                              {e.title && <div className="feed-t">{e.title}</div>}
                              {e.dialogue && <div className="feed-d">{e.dialogue}</div>}
                            </div>
                          </div>
                        );
                      })
                    )}
                  </div>
                  <div className="row-actions feed-actions">
                    <button className="chip" onClick={() => setAllEvents((a) => !a)} aria-pressed={allEvents}>
                      {allEvents ? "Show only the big moments" : "Show every event"}
                    </button>
                    {hasOutput && !liveRunning && (
                      <button className="chip" onClick={resetConsole}>
                        Clear
                      </button>
                    )}
                  </div>
                </div>
              )}
              {lines.length > 0 && (
                <details className="more">
                  <summary>Technical log ({lines.length})</summary>
                  <div className="log">
                    {lines.map((line, i) => (
                      <div key={i} className={`log-line ${line.kind}`}>
                        {line.text}
                      </div>
                    ))}
                  </div>
                </details>
              )}
            </div>
          </div>
        </section>
      )}

      {(latest || report) && (
        <section className="sec" id="result" aria-label="Last season">
          <div className="wrap">
            <p className="eyebrow">Last season</p>
            <h2 className="big">{winner ? `${winner} won.` : "The newest season"}</h2>
            <p className="sec-sub">
              {[
                report ? friendlyDate(report.season.season_id) : latest?.season_id ? friendlyDate(latest.season_id) : null,
                report ? `${report.season.players} models` : null,
                duration(report?.season.elapsed_s ?? latest?.elapsed_s ?? null),
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
            <div className="sec-body">
              {report && <Podium report={report} />}

              {report && (report.moments ?? []).length > 0 && (
                <div className="group">
                  <h3 className="group-h">What happened</h3>
                  <div className="moments">
                    {(report.moments ?? [])
                      .filter((m) => m.kind !== "result")
                      .slice(0, 3)
                      .map((m) => (
                        <article key={m.id} className="card card-pad moment">
                          <p className="sub-l">
                            {MOMENT_KIND[m.kind] ?? m.kind}
                            {m.round ? ` · round ${m.round}` : ""}
                          </p>
                          <h3>{m.title}</h3>
                          <p>{m.text}</p>
                          <a href={`${reportLink}#${m.id}`}>See what they said</a>
                        </article>
                      ))}
                  </div>
                </div>
              )}

              {latest?.rows && (
                <details className="more">
                  <summary>Every model&apos;s score</summary>
                  <ScoreTable rows={latest.rows} />
                </details>
              )}

              <div className="report-cta">
                <p>
                  <b>Read the full story</b>: every vote with the reason the model gave, every broken promise, and
                  images you can share.
                </p>
                <a className="btn-solid" href={reportLink}>
                  Open the full report
                </a>
              </div>
            </div>
          </div>
        </section>
      )}

      <section className="sec" id="how" aria-label="How it works">
        <div className="wrap">
          <p className="eyebrow">How it works</p>
          <h2 className="big">Four steps, the same for every model.</h2>
          <div className="sec-body">
            <ol className="how">
              <li className="card card-pad">
                <span className="step-n">1</span>
                <h3>Secret identities</h3>
                <p>
                  Players go by tree names like Ash and Cedar. None of them knows which company&apos;s model the others
                  are.
                </p>
              </li>
              <li className="card card-pad">
                <span className="step-n">2</span>
                <h3>A puzzle for safety</h3>
                <p>Each round starts with the same puzzles for everyone. The best solver can&apos;t be voted out that round.</p>
              </li>
              <li className="card card-pad">
                <span className="step-n">3</span>
                <h3>Deals, then a vote</h3>
                <p>Players talk in private and make promises, then vote. The player with the most votes is out.</p>
              </li>
              <li className="card card-pad">
                <span className="step-n">4</span>
                <h3>The jury picks</h3>
                <p>When three are left, the players who were voted out question them and choose the winner.</p>
              </li>
            </ol>
          </div>
        </div>
      </section>

      <section className="sec" id="run" aria-label="Start a season">
        <div className="wrap">
          <p className="eyebrow">Your turn</p>
          <h2 className="big">Start a new season.</h2>
          <div className="sec-body">
            <div className="card run-card">
              <div>
                <p>
                  Anyone can start one; one runs at a time. It plays on its own for one to two hours, and you can watch
                  it live on this page.
                </p>
                {budget && (
                  <p className="muted">We pay for it, up to {usd(budget.per_run_cap_cents)} a season.</p>
                )}
              </div>
              <button className="btn-solid btn-big" onClick={runLive} disabled={liveRunning}>
                {liveRunning ? (
                  <>
                    <span className="pulse" aria-hidden="true" /> A season is playing
                  </>
                ) : (
                  "Start a season"
                )}
              </button>
              <details className="more byok-more">
                <summary>Use your own OpenRouter key instead</summary>
                <div className="byok">
                  <label htmlFor="byok-key">Your key</label>
                  <input
                    id="byok-key"
                    type="password"
                    autoComplete="off"
                    placeholder="sk-or-..."
                    value={byok}
                    onChange={(e) => setByok(e.target.value)}
                    disabled={liveRunning}
                  />
                  <button className="chip" onClick={runLive} disabled={liveRunning || byok.trim().length < 8}>
                    Start on my key
                  </button>
                  <span className="note">Used once for this season. Never stored.</span>
                </div>
              </details>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}

/** The final three: the winner and the two finalists the jury passed over. */
function Podium({ report }: { report: ReportData }) {
  const top = [...report.models].filter((m) => m.place_known !== false).sort((a, b) => a.place - b.place).slice(0, 3);
  if (!top.length) return null;
  const tally = report.finale?.tally ?? {};
  const juryTotal = Object.values(tally).reduce((a, b) => a + b, 0);
  return (
    <div className="podium">
      {top.map((m) => {
        const votes = tally[m.agent_id];
        const line =
          votes == null
            ? m.fate ?? ""
            : m.is_winner
              ? `Won ${votes} of ${juryTotal} jury votes`
              : `${votes} of ${juryTotal} jury votes`;
        return (
          <div key={m.agent_id} className={`card podium-card${m.is_winner ? " first" : ""}`}>
            <span className="podium-place">{m.is_winner ? "Winner" : `${ordinal(m.place)} place`}</span>
            <span className="podium-name">
              <span className="mdot" style={{ background: orgColor(m.model_id) }} />
              {m.model}
            </span>
            {m.alias && <span className="muted">played as {m.alias}</span>}
            <span className="podium-line">{line}</span>
          </div>
        );
      })}
    </div>
  );
}
