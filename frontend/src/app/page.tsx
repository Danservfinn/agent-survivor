"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type ScoreRow = {
  agent_id: string;
  model: string;
  model_id: string;
  place: number;
  is_winner: boolean;
  challenge_accuracy: number;
  challenge_correct: number;
  challenge_attempts: number;
  median_response_ms: number | null;
  votes_cast: number;
  votes_received: number;
  jury_votes: number;
  mean_strategic_score: number;
  survivor_index: number;
};

type LiveScorecard = {
  ok: boolean;
  mode?: string;
  roster?: string;
  season_id?: string;
  finished?: boolean;
  elapsed_s?: number;
  generated_at?: string;
  game?: { rounds?: number; winner?: string | null };
  totals?: { attempts?: number; live_attempts?: number; votes?: number; jury_votes?: number };
  caps?: { estimated_live_calls?: number; estimated_cost_cents?: number; cap_reached?: string | null };
  rows?: ScoreRow[];
  weights?: Record<string, number>;
};

type Line = { text: string; kind: "info" | "result" | "error" };

type FeedEvent = {
  id: number;
  round: number;
  kind: string;
  title: string;
  dialogue: string;
  subtitle: string | null;
};

type Player = { agent_id: string; name: string; model: string; tribe: string | null };

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
  vote_call: "vote call",
  vote_booth: "vote",
  vote_reveal: "reveal",
  elimination: "elimination",
  exit_confessional: "exit",
  merge: "merge",
  winner_declared: "finale",
};

function fmt(n: number | null | undefined): string {
  return n === null || n === undefined ? "-" : String(n);
}

export default function Benchmark() {
  const [lines, setLines] = useState<Line[]>([]);
  const [scorecard, setScorecard] = useState<LiveScorecard | null>(null);
  const [liveRunning, setLiveRunning] = useState(false);
  const [budget, setBudget] = useState<{ remaining_cents: number; spent_cents: number } | null>(null);
  const [feedEvents, setFeedEvents] = useState<FeedEvent[]>([]);
  const [feedPlayers, setFeedPlayers] = useState<Player[]>([]);
  const [feedRound, setFeedRound] = useState<number | null>(null);
  const lastEventId = useRef(0);
  const feedSeasonId = useRef<string | null>(null);
  const feedBoxRef = useRef<HTMLDivElement | null>(null);
  // true while the user has scrolled away from the bottom of the feed
  const feedPinned = useRef(true);

  const log = useCallback((text: string, kind: Line["kind"] = "info") => {
    setLines((prev) => [...prev, { text, kind }]);
  }, []);

  useEffect(() => {
    fetch("/api/live-season/scorecard")
      .then((res) => (res.ok ? res.json() : null))
      .then((data: LiveScorecard | null) => setScorecard(data))
      .catch(() => setScorecard(null));
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
        feedPinned.current = true;
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
      if (rep?.ok) {
        log(
          `live season complete: ${rep.game?.rounds ?? "?"} rounds, winner ${rep.game?.winner ?? "?"}, ${(rep.elapsed_s ?? 0).toFixed(0)}s`,
          "result",
        );
        const cost = ((rep.caps?.estimated_cost_cents ?? 0) / 100).toFixed(2);
        log(`spend: $${cost} of $1.00 cap`, "result");
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
    setLiveRunning(false);
  }, [log]);

  const resetConsole = useCallback(() => {
    setLines([]);
    setFeedEvents([]);
    setFeedPlayers([]);
    setFeedRound(null);
    lastEventId.current = 0;
    feedSeasonId.current = null;
    feedPinned.current = true;
    setLiveRunning(false);
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
      log("launching live season on YOUR OpenRouter key: 8 models, real API calls");
      log("your key spends your credits (typically well under $1); it is never stored or logged");
    } else {
      log("launching live season: 8 models, real API calls");
      log("server caps: $1/run, $3/day site-wide");
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
      log("a season is already running, attaching to it (up to 45 min)");
      await pollLiveSeason();
      return;
    }
    if (res.status === 429) {
      log("today's spend tracking reset at midnight UTC, try again in a moment", "info");
      setLiveRunning(false);
      return;
    }
    if (!res.ok || !data?.ok) {
      log(data?.detail || data?.error || "the launch was refused by the server, nothing was charged", "info");
      setLiveRunning(false);
      return;
    }
    log(data?.byok
      ? "season launched on your key. full seasons take 10-45 min, this page will update when it lands"
      : "season launched. full seasons take 10-45 min, this page will update when it lands");
    await pollLiveSeason();
  }, [log, pollLiveSeason, byok]);

  return (
    <main className="bench-shell">
      <header className="bench-header">
        <h1>LLM Survivor Benchmark</h1>
        <p className="bench-sub">
          Social-strategy benchmark for language models: challenges, alliances, tribal votes, jury.
          One button runs a complete season with real model API calls from start to finish.
          No sign-in required.
        </p>
      </header>

      <section className="bench-controls">
        <button className="bench-run bench-run-live" onClick={runLive} disabled={liveRunning}>
          {liveRunning ? "SEASON IN PROGRESS..." : "RUN FULL SEASON"}
        </button>
        <a className="bench-run" href="/history">
          HISTORY
        </a>
        <button className="bench-run bench-reset" onClick={resetConsole} disabled={liveRunning}>
          RESET
        </button>
        {budget && (
          <span className="bench-meta">
            spent today: ${(budget.spent_cents / 100).toFixed(2)}
          </span>
        )}
      </section>

      <section className="bench-byok">
        <label htmlFor="byok-key">or run it on your own OpenRouter key:</label>
        <input
          id="byok-key"
          type="password"
          autoComplete="off"
          placeholder="sk-or-..."
          value={byok}
          onChange={(e) => setByok(e.target.value)}
          disabled={liveRunning}
        />
        <button className="bench-run bench-byok-run" onClick={runLive} disabled={liveRunning || byok.trim().length < 8}>
          RUN ON MY KEY
        </button>
        <span className="bench-meta">key stays in this browser tab; server uses it once, never stores it</span>
      </section>

      {lines.length > 0 && (
        <section className="bench-log" aria-label="Run log">
          {lines.map((line, i) => (
            <div key={i} className={`bench-line bench-${line.kind}`}>
              {line.text}
            </div>
          ))}
        </section>
      )}

      {(liveRunning || feedEvents.length > 0) && (
        <section className="bench-section" aria-label="Live season console">
          <h2>
            Live season console
            {feedRound !== null && <span className="bench-meta"> round {feedRound}</span>}
            {!liveRunning && feedEvents.length > 0 && (
              <span className="bench-meta"> season ended, last events below</span>
            )}
          </h2>
          {feedPlayers.length > 0 && (
            <div className="bench-table-wrap">
              <table className="bench-table">
                <thead>
                  <tr>
                    <th>still in ({feedPlayers.length})</th>
                    <th>model</th>
                    <th>tribe</th>
                  </tr>
                </thead>
                <tbody>
                  {feedPlayers.map((p) => (
                    <tr key={p.agent_id}>
                      <td>{p.name}</td>
                      <td>{p.model}</td>
                      <td>{p.tribe ?? "merged"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="bench-log bench-log-feed" ref={feedBoxRef} onScroll={onFeedScroll}>
            {feedEvents.length === 0 ? (
              <div className="bench-line bench-info">
                season is running, waiting for the first events to land...
              </div>
            ) : (
              feedEvents.map((e) => (
                <div key={e.id} className="bench-line bench-feed">
                  <span className="feed-round">R{e.round}</span>{" "}
                  <span className="feed-kind">{KIND_LABEL[e.kind] ?? e.kind}</span>
                  {e.title && <span className="feed-title"> | {e.title}</span>}
                  {e.dialogue && <div className="feed-dialogue">{e.dialogue}</div>}
                </div>
              ))
            )}
          </div>
        </section>
      )}

      {scorecard && scorecard.ok && scorecard.rows && scorecard.rows.length > 0 && (
        <section className="bench-section" aria-label="Latest season scorecard">
          <h2>Latest season scorecard</h2>
          <div className="bench-table-wrap">
            <pre className="bench-pre">
              {[
                `season ${scorecard.season_id ?? "?"} | rounds ${scorecard.game?.rounds ?? "-"} | winner ${scorecard.game?.winner ?? "-"}`,
                `runtime ${scorecard.elapsed_s ?? "?"}s | live calls ${scorecard.caps?.estimated_live_calls ?? "-"} | est cost ${(
                  (scorecard.caps?.estimated_cost_cents ?? 0) / 100
                ).toFixed(2)} USD`,
              ].join("\n")}
            </pre>
            <table className="bench-table">
              <thead>
                <tr>
                  <th>rank</th>
                  <th>model</th>
                  <th>index</th>
                  <th>place</th>
                  <th>challenge</th>
                  <th>med ms</th>
                  <th>votes for/against</th>
                  <th>strategic</th>
                </tr>
              </thead>
              <tbody>
                {scorecard.rows.map((r, i) => (
                  <tr key={r.agent_id} className={r.is_winner ? "bench-winner" : undefined}>
                    <td>{i + 1}</td>
                    <td>{r.model}</td>
                    <td>{r.survivor_index.toFixed(1)}</td>
                    <td>{r.place}</td>
                    <td>
                      {r.challenge_correct}/{r.challenge_attempts} ({(r.challenge_accuracy * 100).toFixed(0)}%)
                    </td>
                    <td>{r.median_response_ms ?? "-"}</td>
                    <td>
                      {r.votes_cast}/{r.votes_received}
                    </td>
                    <td>{r.mean_strategic_score.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="bench-note">
            Weights v2: accuracy 30%, participation 20%, ground-truth strategy 20%, placement 15%, social 15%.
            Live runs use real model API calls under a $1/run and $3/day site cap.{" "}
            <a href="/history">Full run history</a>.
          </p>
        </section>
      )}
    </main>
  );
}
