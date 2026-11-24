"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { ScoreRow, ScoreTable, seasonDate, usd } from "./score-table";

type LiveScorecard = {
  ok: boolean;
  error?: string;
  interrupted?: { step: string; model_id: string; reason: string } | null;
  season_id?: string;
  elapsed_s?: number;
  game?: { rounds?: number; winner?: string | null };
  caps?: { estimated_live_calls?: number; estimated_cost_cents?: number };
  rows?: ScoreRow[];
};

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
  winner_declared: "finale",
};

export default function Benchmark() {
  const [lines, setLines] = useState<Line[]>([]);
  const [scorecard, setScorecard] = useState<LiveScorecard | null>(null);
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
      log("a season is already running, attaching to it (up to 45 min)");
      await pollLiveSeason();
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
  }, [log, pollLiveSeason, byok, budget]);

  const hasOutput = lines.length > 0 || feedEvents.length > 0;

  return (
    <main className="bench-shell">
      <header className="bench-header">
        <h1>LLM Survivor Benchmark</h1>
        <p className="bench-sub">
          Social-strategy benchmark for language models: challenges, alliances, tribal votes, jury.
          One button runs a complete season with real model API calls. No sign-in required.
        </p>
      </header>

      <section className="bench-controls">
        <button className="bench-run" onClick={runLive} disabled={liveRunning}>
          {liveRunning ? "SEASON IN PROGRESS..." : "RUN FULL SEASON"}
        </button>
        <Link href="/history">run history</Link>
        {budget && (
          <span className="bench-meta">
            cap {usd(budget.per_run_cap_cents)} per run | spent today {usd(budget.spent_cents)}
          </span>
        )}
      </section>

      <section className="bench-byok">
        <label htmlFor="byok-key">or use your own OpenRouter key</label>
        <input
          id="byok-key"
          type="password"
          autoComplete="off"
          placeholder="sk-or-..."
          value={byok}
          onChange={(e) => setByok(e.target.value)}
          disabled={liveRunning}
        />
        <button className="bench-run bench-run-quiet" onClick={runLive} disabled={liveRunning || byok.trim().length < 8}>
          run on my key
        </button>
        <span className="bench-meta">the key is sent once for this run and never stored</span>
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
                    <th>plays as</th>
                    <th>model</th>
                  </tr>
                </thead>
                <tbody>
                  {feedPlayers.map((p) => (
                    <tr key={p.agent_id}>
                      <td>{p.name}</td>
                      <td>{p.alias ?? "-"}</td>
                      <td>{p.model}</td>
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

      {hasOutput && !liveRunning && (
        <p className="bench-meta">
          <button className="narrative-toggle" onClick={resetConsole}>
            clear log
          </button>
        </p>
      )}

      {scorecard && scorecard.ok && scorecard.rows && scorecard.rows.length > 0 && (
        <section className="bench-section" aria-label="Latest finished season">
          <h2>Latest finished season</h2>
          <p className="bench-result-line">
            {scorecard.game?.winner ? `${scorecard.game.winner} won` : "no winner"}
            <span className="bench-meta">
              {" "}
              {seasonDate(scorecard.season_id ?? "")} | ended in round {scorecard.game?.rounds ?? "-"} |{" "}
              {Math.round((scorecard.elapsed_s ?? 0) / 60)} min | {scorecard.caps?.estimated_live_calls ?? "-"} live
              calls | {usd(scorecard.caps?.estimated_cost_cents)}
            </span>
          </p>
          <ScoreTable rows={scorecard.rows} />
          <p className="bench-note">
            Index v2 weights: challenge accuracy 30%, vote participation 20%, strategy 20%, placement 15%, social 15%.{" "}
            <Link href="/history">Every season, with transcripts</Link>.
          </p>
        </section>
      )}
    </main>
  );
}
