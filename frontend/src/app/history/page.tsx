"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { ScoreRow, ScoreTable, seasonDate, usd } from "../score-table";

type HistoryRow = ScoreRow;

type Season = {
  season_id: string;
  recorded_at: string | null;
  roster: string | null;
  season_seed: number | null;
  elapsed_s: number | null;
  cost_cents: number | null;
  live_calls: number | null;
  cap_reached: string | null;
  // set when a model's provider stayed down: the season stopped and is not a result
  interrupted?: { step: string; model_id: string; reason: string } | null;
  index_version: number | null;
  game: { rounds: number | null; winner: string | null } | null;
  rows: HistoryRow[] | null;
  error?: string;
};

type Attempt = { who: string; correct: boolean; ms: number };

type LadderStanding = { who: string; correct: number; sudden_death: number | null; guess?: number | null };

type Challenge = {
  type: string;
  // ladder seasons (2026-11-24 on) carry standings; older seasons carry timed attempts
  standings?: LadderStanding[];
  // the tiebreak guessing game (seasons from 2026-11-24 on)
  guess_target?: number | null;
  ladder_level?: number | null;
  idol_given?: boolean;
  winner: string;
  tribe: string;
  solved: boolean;
  difficulty: string;
  puzzle_id: string;
  first_solver: string | null;
  first_solve_ms: number | null;
  solved_by: string[];
  immunity: string[];
  attempts: Attempt[];
};

type Conversation = {
  who: string;
  who_all?: string[];
  said: string;
  thinking?: string;
  lines?: { who: string; text: string }[];
};

type Narrative = {
  ok: boolean;
  season_id: string;
  rounds: {
    round: number;
    summary: string;
    immunity: string[];
    challenge: Challenge | null;
    conversations: Conversation[];
    votes: { who: string; target: string; reason: string }[];
    elimination: string | null;
    had_revote: boolean;
  }[];
  jury: { who: string; target: string; reason: string }[];
  pitches: { who: string; pitch: string }[];
  finale: { winner: string; line: string } | null;
};

const CLAMP = 220;

function Line({
  who,
  children,
  thinking,
  onWho,
  onRoundView,
}: {
  who: string;
  children: string;
  thinking?: string;
  onWho?: () => void;
  onRoundView?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const text = children;
  const long = text.length > CLAMP || (thinking && thinking.length > CLAMP);
  const shown = open ? text : text.length > CLAMP ? text.slice(0, CLAMP - 3) + "..." : text;
  return (
    <div className="narrative-line">
      {onWho ? (
        <button
          className="narrative-who narrative-who-btn"
          onClick={() => {
            if (onRoundView) onRoundView();
            else if (onWho) onWho();
          }}
        >
          {who}
        </button>
      ) : (
        <span className="narrative-who">{who}</span>
      )}
      <span className="narrative-said">
        {shown}
        {open && thinking ? (
          <span className="narrative-thinking">thinking: {thinking}</span>
        ) : undefined}
        {long ? (
          <button className="narrative-toggle" onClick={() => setOpen((o) => !o)}>
            {open ? "less" : "more"}
          </button>
        ) : undefined}
      </span>
    </div>
  );
}

function LadderBlock({ challenge, onName }: { challenge: Challenge; onName?: (n: string) => void }) {
  const standings = challenge.standings ?? [];
  const suddenDeath = standings.some((row) => row.sudden_death != null);
  const guessed = standings.some((row) => row.guess != null);
  return (
    <div className="narrative-block">
      <h4 className="narrative-label">challenge: five-puzzle ladder</h4>
      <p className="narrative-summary">
        {challenge.winner ? (
          <>
            {onName ? (
              <button className="narrative-who-btn" onClick={() => onName(challenge.winner as string)}>
                <strong>{challenge.winner}</strong>
              </button>
            ) : (
              <strong>{challenge.winner}</strong>
            )}{" "}
            won immunity{suddenDeath ? " after sudden death" : ""}.
          </>
        ) : (
          "Nobody won immunity."
        )}
        {challenge.idol_given ? " The winner gave a hidden idol to another player." : ""}
        {guessed && challenge.guess_target != null
          ? ` Ties went to the number closest to two-thirds of the average guess (${challenge.guess_target}).`
          : ""}
        {challenge.ladder_level ? ` Difficulty level ${challenge.ladder_level} of 2.` : ""}
      </p>
      <table className="bench-table">
        <thead>
          <tr>
            <th>player</th>
            <th className="num">correct of 5</th>
            {suddenDeath && <th className="num">sudden death</th>}
            {guessed && <th className="num">guess</th>}
          </tr>
        </thead>
        <tbody>
          {standings.map((row) => (
            <tr key={row.who} className={row.who === challenge.winner ? "bench-winner" : undefined}>
              <td>{row.who}</td>
              <td className="num">{row.correct}</td>
              {suddenDeath && <td className="num">{row.sudden_death ?? "-"}</td>}
              {guessed && <td className="num">{row.guess ?? "-"}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ChallengeBlock({ challenge, onName }: { challenge: Challenge; onName?: (n: string) => void }) {
  const [open, setOpen] = useState(false);
  if (challenge.standings) return <LadderBlock challenge={challenge} onName={onName} />;
  return (
    <div className="narrative-block">
      <h4 className="narrative-label">challenge result</h4>
      <p className="narrative-summary">
        {challenge.type} challenge ({challenge.difficulty || "?"}). First solved by{" "}
        {challenge.first_solver ? (
          <>
            {onName ? (
              <button className="narrative-who-btn" onClick={() => onName(challenge.first_solver as string)}>
                <strong>{challenge.first_solver}</strong>
              </button>
            ) : (
              <strong>{challenge.first_solver}</strong>
            )}
            {challenge.first_solve_ms != null ? ` in ${(challenge.first_solve_ms / 1000).toFixed(1)}s` : ""}
          </>
        ) : (
          "nobody"
        )}
        {challenge.first_solver && !challenge.solved ? " (no exact solve; fastest attempt won)" : ""}
        {challenge.solved_by.length > 1 ? `. ${challenge.solved_by.length} models solved it` : ""}
        {challenge.immunity.length > 0 ? `. Immunity: ${challenge.immunity.join(", ")}` : ""}
      </p>
      <button className="narrative-toggle" onClick={() => setOpen((o) => !o)}>
        {open ? "hide attempts" : "all attempts"}
      </button>
      {open && (
        <table className="bench-table">
          <thead>
            <tr>
              <th>model</th>
              <th>solved</th>
              <th>time</th>
            </tr>
          </thead>
          <tbody>
            {challenge.attempts.map((a, i) => (
              <tr key={i}>
                <td>{a.who}</td>
                <td>{a.correct ? "yes" : "no"}</td>
                <td>{(a.ms / 1000).toFixed(1)}s</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}


function Collapsed({ label, children }: { label: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="narrative-block">
      <h4 className="narrative-label">
        {label}{" "}
        <button className="narrative-toggle" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          {open ? "hide" : "show"}
        </button>
      </h4>
      {open && children}
    </div>
  );
}

function ModelDetail({ narrative, row, onClose }: { narrative: Narrative; row: HistoryRow; onClose: () => void }) {
  const name = row.model;
  const lines: { round: number; kind: string; text: string; thinking?: string }[] = [];
  const votes: { round: number; target: string; reason: string }[] = [];
  // result reads "3/5" for ladder seasons and "yes, 2.1s" for older timed seasons
  const attempts: { round: number; result: string; winner: string | null }[] = [];

  for (const r of narrative.rounds) {
    for (const c of r.conversations) {
      if (c.who === name) lines.push({ round: r.round, kind: "camp", text: c.said, thinking: c.thinking });
    }
    for (const v of r.votes) {
      if (v.who === name) votes.push({ round: r.round, target: v.target, reason: v.reason });
    }
    if (r.challenge?.standings) {
      const mine = r.challenge.standings.find((s) => s.who === name);
      if (mine) attempts.push({ round: r.round, result: `${mine.correct}/5`, winner: r.challenge.winner });
    } else if (r.challenge) {
      const mine = (r.challenge.attempts ?? []).find((a) => a.who === name);
      if (mine) {
        attempts.push({
          round: r.round,
          result: `${mine.correct ? "yes" : "no"}, ${(mine.ms / 1000).toFixed(1)}s`,
          winner: r.challenge.first_solver,
        });
      }
    }
  }
  const juryVote = narrative.jury.find((j) => j.who === name);
  const pitch = narrative.pitches.find((p) => p.who === name);

  return (
    <div className="model-detail" id="model-detail">
      <button className="narrative-toggle" onClick={onClose}>close</button>
      <h3 className="narrative-round-title">{name} this season</h3>
      <pre className="bench-pre">
        {[
          `place ${row.place}${row.is_winner ? " (winner)" : ""} | index ${row.survivor_index.toFixed(1)}`,
          `challenge ${row.challenge_correct}/${row.challenge_attempts} (${(row.challenge_accuracy * 100).toFixed(0)}%)`,
          `votes cast ${row.votes_cast} | votes against ${row.votes_received}`,
        ].join("\n")}
      </pre>

      {pitch && (
        <div className="narrative-block">
          <h4 className="narrative-label">final pitch</h4>
          <Line who="">{pitch.pitch}</Line>
        </div>
      )}
      {juryVote && (
        <div className="narrative-block">
          <h4 className="narrative-label">jury vote</h4>
          <Line who="">{`voted ${juryVote.target}${juryVote.reason ? ` — ${juryVote.reason}` : ""}`}</Line>
        </div>
      )}
      {attempts.length > 0 && (
        <div className="narrative-block">
          <h4 className="narrative-label">challenges</h4>
          <table className="bench-table">
            <thead>
              <tr><th>round</th><th>result</th><th>round winner</th></tr>
            </thead>
            <tbody>
              {attempts.map((a, i) => (
                <tr key={i}>
                  <td>{a.round}</td>
                  <td>{a.result}</td>
                  <td>{a.winner ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {votes.length > 0 && (
        <div className="narrative-block">
          <h4 className="narrative-label">votes cast</h4>
          {votes.map((v, i) => (
            <Line key={i} who={`R${v.round}`}>{`voted ${v.target}${v.reason ? ` — ${v.reason}` : ""}`}</Line>
          ))}
        </div>
      )}
      {lines.length > 0 && (
        <div className="narrative-block">
          <h4 className="narrative-label">everything they said</h4>
          {lines.map((l, i) => (
            <Line key={i} who={`R${l.round} ${l.kind}`} thinking={l.thinking}>{l.text}</Line>
          ))}
        </div>
      )}
    </div>
  );
}

export default function HistoryPage() {
  const [seasons, setSeasons] = useState<Season[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [narratives, setNarratives] = useState<Record<string, Narrative | "loading" | "error">>({});
  const [openModel, setOpenModel] = useState<string | null>(null);
  const [roundModelFilter, setRoundModelFilter] = useState<Record<string, string | null>>({});

  const loadNarrative = useCallback((id: string) => {
    setNarratives((prev) => (prev[id] ? prev : { ...prev, [id]: "loading" }));
    fetch(`/api/live-season/narrative/${id}`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        setNarratives((prev) => ({ ...prev, [id]: data && data.ok ? data : "error" }));
      })
      .catch(() => setNarratives((prev) => ({ ...prev, [id]: "error" })));
  }, []);

  useEffect(() => {
    fetch("/api/live-season/history")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!data?.seasons) {
          setFailed(true);
          return;
        }
        setSeasons(data.seasons);
        const first = (data.seasons as Season[]).find((x) => x.game?.winner) ?? data.seasons[0];
        if (first) {
          setOpenId(first.season_id);
          loadNarrative(first.season_id);
        }
      })
      .catch(() => setFailed(true));
  }, [loadNarrative]);

  const toggle = (id: string) => {
    const next = openId === id ? null : id;
    setOpenId(next);
    setOpenModel(null);
    if (next && !narratives[next]) loadNarrative(next);
  };

  const openByName = useCallback((name: string, rows: HistoryRow[] | null | undefined) => {
    const row = (rows ?? []).find((r) => r.model === name);
    if (!row) return;
    setOpenModel(row.agent_id);
    setTimeout(
      () => document.getElementById("model-detail")?.scrollIntoView({ behavior: "smooth", block: "start" }),
      80
    );
  }, []);

  return (
    <main className="bench-shell">
      <header className="bench-header">
        <h1>Run History</h1>
        <p className="bench-sub">
          Every recorded live season, newest first, rescored to the current index version. Open a
          season for its scorecard and round-by-round summary; click a model for everything it did.
        </p>
        <p className="bench-meta">
          <Link href="/">back to benchmark console</Link>
        </p>
      </header>

      {failed && <p className="bench-error">history unavailable</p>}
      {!failed && seasons.length === 0 && <p className="bench-meta">no live seasons recorded yet</p>}

      {seasons.map((s) => {
        const open = openId === s.season_id;
        const finished = Boolean(s.game?.winner);
        const narrative = narratives[s.season_id];
        return (
          <section key={s.season_id} className="bench-section">
            <button
              className={finished ? "season-head" : "season-head season-head-unfinished"}
              onClick={() => toggle(s.season_id)}
              aria-expanded={open}
            >
              <span>{open ? "v" : ">"}</span>
              <span>{seasonDate(s.season_id)}</span>
              <strong>
                {s.error ? "error" : finished ? `${s.game?.winner} won` : s.interrupted ? "interrupted" : "unfinished"}
              </strong>
              <span className="season-head-meta">
                {finished ? "ended in" : "stopped in"} round {s.game?.rounds ?? "-"}
                {s.cost_cents != null ? ` | ${usd(s.cost_cents)}` : ""}
                {s.live_calls != null ? ` | ${s.live_calls} calls` : ""}
              </span>
            </button>

            {open && !s.error && (
              <>
                <p className="bench-meta">
                  {[
                    s.roster ? `roster ${s.roster}` : null,
                    s.season_seed != null ? `seed ${s.season_seed}` : null,
                    s.index_version != null ? `index v${s.index_version}` : null,
                    s.elapsed_s != null ? `${Math.round(s.elapsed_s / 60)} min` : null,
                    s.cap_reached ? `cap reached: ${s.cap_reached}` : null,
                    s.interrupted
                      ? `interrupted at ${s.interrupted.step}: provider outage, not a result`
                      : finished
                        ? null
                        : "run stopped before a winner was declared",
                  ]
                    .filter(Boolean)
                    .join(" | ")}
                  {" | "}
                  <a href={`/report/${s.season_id}`}>full report</a>
                </p>
                <ScoreTable
                  rows={s.rows ?? []}
                  selectedId={openModel}
                  onSelect={(id) => setOpenModel((m) => (m === id ? null : id))}
                />

                {openModel && (narratives[s.season_id] && narratives[s.season_id] !== "loading" && narratives[s.season_id] !== "error") &&
                  (() => {
                    const n = narratives[s.season_id] as Narrative;
                    const row = (s.rows ?? []).find((r) => r.agent_id === openModel);
                    if (!row) return null;
                    return <ModelDetail narrative={n} row={row} onClose={() => setOpenModel(null)} />;
                  })()}

                {narrative === "loading" && <p className="bench-meta">loading season summary...</p>}
                {narrative === "error" && <p className="bench-meta">no written summary for this season</p>}

                {narrative && narrative !== "loading" && narrative !== "error" && (
                  <div className="narrative">
                    {narrative.rounds.map((r) => (
                      <div key={r.round} className="narrative-round">
                        <h3 className="narrative-round-title">Round {r.round}</h3>
                        <p className="narrative-summary">{r.summary}</p>

                        {r.challenge && <ChallengeBlock challenge={r.challenge} onName={(n) => openByName(n, s.rows)} />}

                        {r.conversations.length > 0 && (
                          <Collapsed label={`strategy at camp (${r.conversations.length} conversations)`}>
                            {(roundModelFilter[s.season_id] ?? null) && (
                              <p className="bench-meta">
                                round {r.round} view: {roundModelFilter[s.season_id]}{" "}
                                <button
                                  className="narrative-toggle"
                                  onClick={() => setRoundModelFilter({ ...roundModelFilter, [s.season_id]: null })}
                                >
                                  clear filter
                                </button>
                              </p>
                            )}
                            {r.conversations
                              .filter((c) => {
                                const f = roundModelFilter[s.season_id];
                                if (!f) return true;
                                const inWhoAll = (c.who_all ?? [c.who]).includes(f);
                                const inLines = (c.lines ?? []).some((l) => l.who === f);
                                return inWhoAll || inLines;
                              })
                              .map((c, i) => (
                                <div key={i} className="narrative-convo">
                                  <div className="narrative-convo-head">
                                    {(c.who_all ?? [c.who]).map((w, j) => (
                                      <span key={j}>
                                        {j > 0 ? " + " : ""}
                                        <button className="narrative-who-btn" onClick={() => openByName(w, s.rows)}>
                                          {w}
                                        </button>
                                      </span>
                                    ))}
                                  </div>
                                  {(c.lines ?? [{ who: c.who, text: c.said }]).map((l, j) => (
                                    <Line
                                      key={j}
                                      who={l.who}
                                      onWho={() => openByName(l.who, s.rows)}
                                      onRoundView={() =>
                                        setRoundModelFilter({
                                          ...roundModelFilter,
                                          [s.season_id]: roundModelFilter[s.season_id] === l.who ? null : l.who,
                                        })
                                      }
                                    >
                                      {l.text}
                                    </Line>
                                  ))}
                                  {c.thinking && <Line who={`${c.who} (private)`}>{c.thinking}</Line>}
                                </div>
                              ))}
                          </Collapsed>
                        )}

                        {r.votes.length > 0 && (
                          <div className="narrative-block">
                            <h4 className="narrative-label">the vote</h4>
                            {r.votes.map((v, i) => (
                              <Line key={i} who={v.who} onWho={() => openByName(v.who, s.rows)}>
                                {`voted ${v.target}${v.reason ? ` — ${v.reason}` : ""}`}
                              </Line>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}

                    {narrative.pitches.length > 0 && (
                      <div className="narrative-round">
                        <h3 className="narrative-round-title">Final pitches</h3>
                        {narrative.pitches.map((p, i) => (
                          <Line key={i} who={p.who} onWho={() => openByName(p.who, s.rows)}>
                            {p.pitch}
                          </Line>
                        ))}
                      </div>
                    )}

                    {narrative.jury.length > 0 && (
                      <div className="narrative-round">
                        <h3 className="narrative-round-title">Jury votes</h3>
                        {narrative.jury.map((j, i) => (
                          <Line key={i} who={j.who} onWho={() => openByName(j.who, s.rows)}>
                            {`voted ${j.target}${j.reason ? ` — ${j.reason}` : ""}`}
                          </Line>
                        ))}
                        {narrative.finale && (
                          <p className="narrative-summary">
                            {narrative.finale.winner} wins the season. {narrative.finale.line}
                          </p>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </>
            )}
            {open && s.error && <p className="bench-error">rescore failed: {s.error}</p>}
          </section>
        );
      })}
    </main>
  );
}
