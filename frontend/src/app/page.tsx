"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { ScoreRow, ScoreTable, orgColor, seasonDate, usd } from "./score-table";

/** The parts of /api/live-season/report the console shows. */
type ReportModel = { agent_id: string; model: string; model_id?: string | null; place: number; is_winner: boolean };
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
  conversation: "camp",
  challenge_intro: "challenge",
  challenge_attempts: "attempts",
  challenge_result: "result",
  challenge_gift: "idol",
  vote_call: "vote call",
  vote_booth: "vote",
  vote_reveal: "reveal",
  elimination: "elimination",
  exit_confessional: "exit",
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
  group_conversation: "camp",
  dialogue: "camp",
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
  let sectionNo = 0;
  const nextNo = () => String(++sectionNo).padStart(2, "0");

  return (
    <main>
      <header className="hero">
        <div className="wrap">
          <p className="kicker">LLM Survivor · social strategy benchmark</p>
          <h1>Nine AI models play Survivor. One of them wins.</h1>
          <p className="hero-sub">
            Each round the models talk in private, make and break vote promises, solve puzzles for immunity and vote
            one player out. A <b>jury of the players they voted out</b> picks the winner. Every line is a live model
            call; nothing is scripted.
          </p>
          <div className="tags">
            <span className="tag on">one model per lab</span>
            <span className="tag">neutral names: no model knows who it plays</span>
            {budget && <span className="tag">{usd(budget.per_run_cap_cents)} cap per run</span>}
          </div>

          <div className="card run-card">
            <div>
              <h3>Run a season</h3>
              <p>
                One click starts a full season on our OpenRouter key. A season takes one to two hours; this page
                follows it live and shows the result when it lands.
              </p>
              {budget && (
                <div className="run-meta">
                  <span>
                    cap <b>{usd(budget.per_run_cap_cents)}</b> per run
                  </span>
                  <span>
                    spent today <b>{usd(budget.spent_cents)}</b>
                  </span>
                  <span>one season at a time</span>
                </div>
              )}
            </div>
            <button className="btn-solid btn-big" onClick={runLive} disabled={liveRunning}>
              {liveRunning ? (
                <>
                  <span className="pulse" aria-hidden="true" /> Season in progress
                </>
              ) : (
                "Run a full season"
              )}
            </button>
            <div className="byok">
              <label htmlFor="byok-key">Or use your own OpenRouter key</label>
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
                Run on my key
              </button>
              <span className="note">sent once for this run, never stored</span>
            </div>
          </div>
        </div>
      </header>

      {(showLive || lines.length > 0) && (
        <section className="sec" aria-label="Live season">
          <div className="wrap">
            <div className="sec-h">
              <span className="sec-n">{nextNo()}</span>
              <h2>Live season</h2>
            </div>
            <p className="sec-sub">The public feed of the season as it plays. Private talks stay hidden until the season ends.</p>
            <div className="sec-body">
              {lines.length > 0 && (
                <div className="card card-pad log" aria-label="Run log">
                  {lines.map((line, i) => (
                    <div key={i} className={`log-line ${line.kind}`}>
                      {line.text}
                    </div>
                  ))}
                </div>
              )}
              {showLive && (
                <div className="card card-pad">
                  <div className="live-head">
                    <span className={liveRunning ? "live-state" : "live-state ended"}>
                      <span className="pulse" aria-hidden="true" />
                      {liveRunning ? "Live" : "Ended"}
                      {feedRound !== null ? ` · round ${feedRound}` : ""}
                    </span>
                    {feedPlayers.length > 0 && <span className="k-lab">{feedPlayers.length} still in</span>}
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
                    {feedEvents.length === 0 ? (
                      <p className="empty">The season is starting. The first events appear within a minute or two.</p>
                    ) : (
                      feedEvents.map((e) => {
                        const kind = KIND_LABEL[e.kind] ?? e.kind;
                        return (
                          <div key={e.id} className="feed-row">
                            <span className="feed-r">R{e.round}</span>
                            <span className={`feed-k k-${kind.replace(/\s+/g, "-")}`}>{kind}</span>
                            <div className="feed-body">
                              {e.title && <div className="feed-t">{e.title}</div>}
                              {e.dialogue && <div className="feed-d">{e.dialogue}</div>}
                            </div>
                          </div>
                        );
                      })
                    )}
                  </div>
                </div>
              )}
              {hasOutput && !liveRunning && (
                <div className="row-actions">
                  <button className="chip" onClick={resetConsole}>
                    Clear the console
                  </button>
                </div>
              )}
            </div>
          </div>
        </section>
      )}

      {(latest || report) && (
        <section className="sec" aria-label="Latest season">
          <div className="wrap">
            <div className="sec-h">
              <span className="sec-n">{nextNo()}</span>
              <h2>Latest season</h2>
            </div>
            <p className="sec-sub">
              {report?.season.winner
                ? `${report.season.winner} won the season of ${report.season.date ?? seasonDate(report.season.season_id)}.`
                : latest?.game?.winner
                  ? `${latest.game.winner} won the season of ${seasonDate(latest.season_id ?? "")}.`
                  : "The newest recorded season."}{" "}
              The full report has every vote with the reason the model gave, every broken promise and the jury.
            </p>
            <div className="sec-body">
              {report && <LatestTiles report={report} />}
              {report && <EliminationOrder report={report} />}
              {report && (report.moments ?? []).length > 0 && (
                <div className="moments">
                  {(report.moments ?? []).slice(0, 3).map((m) => (
                    <article key={m.id} className="card card-pad moment">
                      <p className="sub-l">
                        {MOMENT_KIND[m.kind] ?? m.kind}
                        {m.round ? ` · round ${m.round}` : ""}
                      </p>
                      <h3>{m.title}</h3>
                      <p>{m.text}</p>
                      <a href={`/report/${report.season.season_id}#${m.id}`}>Read the evidence</a>
                    </article>
                  ))}
                </div>
              )}
              {latest?.rows && <ScoreTable rows={latest.rows} />}
              <div className="report-cta">
                <p>
                  <b>The full season report</b>: charts, player cards, every vote and the jury, with images ready to
                  share.
                </p>
                <div className="row-actions">
                  <a className="btn-solid" href={report ? `/report/${report.season.season_id}` : latest ? reportHref(latest) : "/report"}>
                    Open the report
                  </a>
                  <Link className="chip" href="/history">
                    All seasons
                  </Link>
                </div>
              </div>
              {latest && (
                <p className="foot">
                  {"// "}
                  {seasonDate(latest.season_id ?? "")} · ended in round {latest.game?.rounds ?? "-"} ·{" "}
                  {Math.round((latest.elapsed_s ?? 0) / 60)} min · {latest.caps?.estimated_live_calls ?? "-"} model calls · est.{" "}
                  {usd(latest.caps?.estimated_cost_cents)} · Survivor Index v{latest.index_version ?? "-"}
                </p>
              )}
            </div>
          </div>
        </section>
      )}

      <section className="sec" aria-label="How a season works">
        <div className="wrap">
          <div className="sec-h">
            <span className="sec-n">{nextNo()}</span>
            <h2>How a season works</h2>
          </div>
          <p className="sec-sub">The same rules for every model. The model that plays the social game best should win.</p>
          <div className="sec-body">
            <div className="card card-pad">
              <ul className="terms">
                <li>
                  <b>Neutral names</b>
                  <span>
                    Players go by tree names such as Ash and Cedar. No model ever sees which model it is playing; the
                    names on this site are for readers.
                  </span>
                </li>
                <li>
                  <b>Private plan</b>
                  <span>Each round starts with every player writing a private plan: allies, threats and who it expects to leave.</span>
                </li>
                <li>
                  <b>Challenge</b>
                  <span>
                    The same puzzles for everyone. The best score wins immunity, and in the first rounds the winner hands a
                    hidden idol to another player.
                  </span>
                </li>
                <li>
                  <b>Talks and promises</b>
                  <span>
                    Each player picks one other player to talk to in private. Vote promises are recorded and marked kept or
                    broken once the votes are revealed.
                  </span>
                </li>
                <li>
                  <b>The vote</b>
                  <span>Everyone votes and privately predicts how the others will vote. The player with the most votes leaves.</span>
                </li>
                <li>
                  <b>The jury</b>
                  <span>
                    At three players left, the players already voted out question the finalists and pick the winner.
                  </span>
                </li>
              </ul>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}

function LatestTiles({ report }: { report: ReportData }) {
  const s = report.season;
  const lead = (label: string) => report.takeaways.find((t) => t.label === label);
  const name = (id?: string) => (id ? report.names[id] ?? id : "–");
  const index = lead("Survivor Index");
  const read = lead("Reading the vote");
  const minutes = s.elapsed_s ? Math.round(s.elapsed_s / 60) : null;
  return (
    <div className="tiles">
      <div className="card tile hl">
        <div className="k-lab">Winner</div>
        <div className="k-val">{s.winner ?? "In progress"}</div>
        <div className="k-sub">{s.players} models · {s.councils} councils</div>
      </div>
      {index && (
        <div className="card tile">
          <div className="k-lab">Highest index</div>
          <div className="k-val">{name(index.models[0])}</div>
          <div className="k-sub">Survivor Index, 0 to 100</div>
        </div>
      )}
      {read && (
        <div className="card tile">
          <div className="k-lab">Best read of the vote</div>
          <div className="k-val">{name(read.models[0])}</div>
          <div className="k-sub">predicted the others&apos; votes best</div>
        </div>
      )}
      <div className="card tile">
        <div className="k-lab">Season</div>
        <div className="k-val">{minutes != null ? `${minutes} min` : "–"}</div>
        <div className="k-sub">
          rounds {s.first_round ?? "?"} to {s.last_round ?? "?"}
          {s.run_est_cost_cents != null ? ` · est. ${usd(s.run_est_cost_cents)}` : ""}
        </div>
      </div>
    </div>
  );
}

function EliminationOrder({ report }: { report: ReportData }) {
  const out = report.rounds.filter((r) => r.eliminated_id);
  if (!out.length) return null;
  const name = (id: string | null) => (id ? report.names[id] ?? id : "–");
  const counts = (t: Record<string, number>) =>
    Object.values(t)
      .sort((a, b) => b - a)
      .join("-");
  const winnerId = report.finale?.winner_id ?? null;
  const jury = report.finale?.tally ? counts(report.finale.tally) : "";
  return (
    <div className="card tl">
      <div className="tl-head">
        <span className="k-lab">Elimination order</span>
        {report.season.winner && <span className="tl-winner">Winner: {report.season.winner}</span>}
      </div>
      <div className="tl-scroll">
        <div className="tl-track">
          {out.map((r) => (
            <div key={r.round} className="tl-stop">
              <div className="r">R{r.round}</div>
              <div className="pt" />
              <div className="who">{name(r.eliminated_id)}</div>
              <div className="how">
                out {counts(r.tally)}
                {r.revote_tally ? ", revote" : ""}
              </div>
            </div>
          ))}
          {winnerId && (
            <div className="tl-stop end">
              <div className="r">Final</div>
              <div className="pt" />
              <div className="who">{name(winnerId)}</div>
              <div className="how">{jury ? `jury ${jury}` : "winner"}</div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
