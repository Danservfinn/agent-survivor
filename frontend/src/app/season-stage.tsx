"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { Avatar, Campfire, CirclePlayer, FIRE_CIRCLE, FIRE_FINALE, H, JURY_BOX, Point, Torch, W, juryLines, seatLayout } from "./season-circle";
import { orgColor } from "./score-table";

/* ---------------------------------------------------------------- data */

/** The parts of the season report (/api/live-season/report) the stage plays. */
export type StageReport = {
  season: { season_id: string; date: string | null; winner: string | null };
  names: Record<string, string>;
  aliases: Record<string, string>;
  models: { agent_id: string; model: string; model_id?: string | null; alias?: string | null }[];
  rounds: {
    round: number;
    challenge: {
      standings: { agent_id: string; correct: number | null; of: number }[];
      immunity_ids: string[];
    } | null;
    idol: { giver_id: string | null; recipient_id: string | null } | null;
    talks: { kind: string; participants: string[]; lines: { agent_id: string | null; text: string }[] }[];
    promises: { by_id: string; to_id: string; target_id: string; actual_id: string | null; status: string }[];
    votes: { voter_id: string; target_id: string; revote: boolean; played_idol: boolean; reason: string }[];
    tally: Record<string, number>;
    revote_tally: Record<string, number> | null;
    eliminated_id: string | null;
    idols_played: string[];
    exit_words: string;
  }[];
  finale: {
    finalist_ids: string[];
    jury: { juror_id: string; finalist_id: string | null; reason: string }[];
    tally: Record<string, number>;
    pitches: { agent_id: string; text: string }[];
    winner_id: string | null;
  } | null;
};

type Link = { from: string; to: string; kind: "talk" | "idol" | "vote" | "broken" | "jury"; color?: string };
type Quote = { who: string; context: string; to?: string; text: string };

/** One moment of the animation. */
type Beat = {
  round: number | null; // null for the finale
  kind: "plan" | "challenge" | "idol" | "talk" | "vote" | "revote" | "promises" | "out" | "jury" | "pitch" | "verdict";
  label: string;
  caption: string;
  quotes: Quote[];
  out: string[]; // voted out by the end of this beat
  focus?: string[]; // everyone else is dimmed
  links?: Link[];
  badges?: Record<string, string>;
  bubbles?: { id: string; text: string }[];
  immune?: string[];
  thinking?: boolean;
  finale?: boolean;
  winner?: string | null;
  ms: number; // how long the beat stays at normal speed
};

const BUBBLE_CHARS = 150;
const NO_BUBBLES: { id: string; text: string }[] = [];

/** How long one bubble stays up: a second to find it, then about 30 characters a second. */
export function readMs(text: string): number {
  return Math.max(2500, 1000 + text.length * 33);
}
const QUOTE_CHARS = 520;

function clip(text: string, n: number): string {
  const t = (text || "").replace(/\s+/g, " ").trim();
  return t.length <= n ? t : t.slice(0, n - 1).replace(/\s+\S*$/, "") + "…";
}

/** Turn a season report into the beats of the animation, in the order they happened. */
export function buildBeats(report: StageReport): Beat[] {
  const nm = (id: string | null | undefined) => (id ? report.names[id] ?? id : "nobody");
  const tagged = (id: string) => `${nm(id)}${report.aliases[id] ? ` (${report.aliases[id]})` : ""}`;
  const list = (ids: string[]) => {
    const names = ids.map(nm);
    return names.length <= 1 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
  };
  const beats: Beat[] = [];
  const out: string[] = [];

  for (const r of report.rounds) {
    const active = report.models.map((m) => m.agent_id).filter((id) => !out.includes(id));
    const immune = r.challenge?.immunity_ids ?? [];
    const base = { round: r.round, out: [...out] };

    beats.push({
      ...base,
      kind: "plan",
      label: "Plans",
      caption: `Round ${r.round}. ${active.length} players left. Each one writes a private plan: who to trust, and who should go.`,
      quotes: [],
      thinking: true,
      ms: 2800,
    });

    if (r.challenge) {
      const badges: Record<string, string> = {};
      r.challenge.standings.forEach((s) => {
        if (s.correct != null) badges[s.agent_id] = `${s.correct}/${s.of}`;
      });
      const caption = immune.length
        ? `The puzzle challenge. ${list(immune)} ${immune.length === 1 ? "scores" : "score"} best and can't be voted out this round.`
        : "The puzzle challenge. A tie at the top, so nobody is safe this round.";
      beats.push({ ...base, kind: "challenge", label: "Puzzle", caption, quotes: [], badges, immune, ms: 3600 });
    }

    if (r.idol?.giver_id && r.idol.recipient_id) {
      beats.push({
        ...base,
        kind: "idol",
        label: "Idol",
        caption: `${nm(r.idol.giver_id)} won the challenge and secretly hands a hidden idol to ${nm(
          r.idol.recipient_id,
        )}. Only the two of them know.`,
        quotes: [],
        focus: [r.idol.giver_id, r.idol.recipient_id],
        links: [{ from: r.idol.giver_id, to: r.idol.recipient_id, kind: "idol" }],
        immune,
        ms: 3800,
      });
    }

    // the private talks worth watching: broken promises first, then kept ones, then the longest
    const promiseScore = (ids: string[]) =>
      r.promises.reduce((score, p) => {
        if (!ids.includes(p.by_id) || !ids.includes(p.to_id)) return score;
        return score + (p.status === "broken" ? 6 : p.status === "kept" ? 2 : 0);
      }, 0);
    const talks = r.talks
      .map((t, i) => {
        const ids = Array.from(new Set([...(t.participants || []), ...t.lines.map((l) => l.agent_id || "")])).filter(
          (id) => id && active.includes(id),
        );
        const score = promiseScore(ids) + (t.kind === "conversation" ? 1 : 0) + Math.min(3, t.lines.length / 2);
        return { t, i, ids, score };
      })
      .filter((x) => x.ids.length >= 2)
      .sort((a, b) => b.score - a.score)
      .slice(0, 3)
      .sort((a, b) => a.i - b.i);
    for (const { t, ids } of talks) {
      // each speaker's longest line is what the bubble shows
      const best: Record<string, string> = {};
      t.lines.forEach((l) => {
        if (l.agent_id && l.text && (!best[l.agent_id] || l.text.length > best[l.agent_id].length)) best[l.agent_id] = l.text;
      });
      const speakers = ids.filter((id) => best[id]).slice(0, 2);
      // a broken promise is the one worth naming
      const between = r.promises.filter((p) => ids.includes(p.by_id) && ids.includes(p.to_id) && p.status !== "unclear");
      const promise = between.find((p) => p.status === "broken") ?? between[0];
      let caption = ids.length === 2 ? `${nm(ids[0])} and ${nm(ids[1])} talk in private.` : `${list(ids)} meet in private.`;
      if (promise) caption += ` ${nm(promise.by_id)} promises ${nm(promise.to_id)} a vote for ${nm(promise.target_id)}.`;
      beats.push({
        ...base,
        kind: "talk",
        label: "Talks",
        caption,
        quotes: speakers.map((id) => ({
          who: id,
          context: "in private to",
          to: ids.find((x) => x !== id),
          text: clip(best[id], QUOTE_CHARS),
        })),
        focus: ids,
        links: ids.slice(1).map((id) => ({ from: ids[0], to: id, kind: "talk" as const })),
        bubbles: speakers.map((id) => ({ id, text: clip(best[id], BUBBLE_CHARS) })),
        immune,
        // long enough for every speaker's bubble, one after another
        ms: Math.max(6200, speakers.reduce((n, id) => n + readMs(clip(best[id], BUBBLE_CHARS)), 0)),
      });
    }

    const firstBallot = r.votes.filter((v) => !v.revote && v.voter_id !== "host" && v.target_id);
    if (firstBallot.length) {
      const counts = Object.entries(r.tally).sort((a, b) => b[1] - a[1]);
      const [topId, topN] = counts[0] ?? ["", 0];
      const total = counts.reduce((n, [, c]) => n + c, 0);
      let caption = `The vote. ${nm(topId)} gets ${topN} of ${total} votes.`;
      if (r.idols_played.length) caption += ` ${list(r.idols_played)} plays the hidden idol, so votes against it don't count.`;
      const badges: Record<string, string> = {};
      counts.forEach(([id, n]) => (badges[id] = `${n} ${n === 1 ? "vote" : "votes"}`));
      const why = firstBallot.filter((v) => v.target_id === topId && v.reason).slice(0, 2);
      beats.push({
        ...base,
        kind: "vote",
        label: "Vote",
        caption,
        quotes: why.map((v) => ({ who: v.voter_id, context: "casting its vote", text: clip(v.reason, QUOTE_CHARS) })),
        links: firstBallot.map((v) => ({ from: v.voter_id, to: v.target_id, kind: "vote" as const, color: orgColor(modelOf(report, v.voter_id)) })),
        badges,
        immune,
        ms: 5200,
      });
    }

    if (r.revote_tally) {
      const second = r.votes.filter((v) => v.revote && v.target_id);
      const counts = Object.entries(r.revote_tally).sort((a, b) => b[1] - a[1]);
      const badges: Record<string, string> = {};
      counts.forEach(([id, n]) => (badges[id] = `${n} ${n === 1 ? "vote" : "votes"}`));
      beats.push({
        ...base,
        kind: "revote",
        label: "Revote",
        caption: `A tie, so they vote again. ${nm(counts[0]?.[0])} gets ${counts[0]?.[1] ?? 0}.`,
        quotes: [],
        links: second.map((v) => ({ from: v.voter_id, to: v.target_id, kind: "vote" as const, color: orgColor(modelOf(report, v.voter_id)) })),
        badges,
        immune,
        ms: 4400,
      });
    }

    const counted = r.promises.filter((p) => p.status === "kept" || p.status === "broken");
    const broken = counted.filter((p) => p.status === "broken");
    if (broken.length) {
      const first = broken[0];
      const reason = r.votes.find((v) => v.voter_id === first.by_id && !v.revote)?.reason;
      beats.push({
        ...base,
        kind: "promises",
        label: "Promises",
        caption: `Promises checked: ${counted.length - broken.length} kept, ${broken.length} broken. ${nm(first.by_id)} promised ${nm(
          first.to_id,
        )} a vote for ${nm(first.target_id)}, then voted for ${nm(first.actual_id)}.`,
        quotes: reason ? [{ who: first.by_id, context: "casting its vote", text: clip(reason, QUOTE_CHARS) }] : [],
        focus: Array.from(new Set(broken.flatMap((p) => [p.by_id, p.to_id]))),
        links: broken.map((p) => ({ from: p.by_id, to: p.to_id, kind: "broken" as const })),
        immune,
        ms: 5600,
      });
    }

    if (r.eliminated_id) {
      out.push(r.eliminated_id);
      const counts = Object.values(r.revote_tally ?? r.tally).sort((a, b) => b - a);
      beats.push({
        round: r.round,
        out: [...out],
        kind: "out",
        label: "Out",
        caption: `${tagged(r.eliminated_id)} is voted out${counts.length > 1 ? `, ${counts.join(" to ")}` : ""}.`,
        quotes: r.exit_words ? [{ who: r.eliminated_id, context: "last words", text: clip(r.exit_words, QUOTE_CHARS) }] : [],
        focus: [r.eliminated_id],
        bubbles: r.exit_words ? [{ id: r.eliminated_id, text: clip(r.exit_words, BUBBLE_CHARS) }] : [],
        ms: 5200,
      });
    }
  }

  const f = report.finale;
  if (f?.winner_id) {
    const jurors = f.jury.map((j) => j.juror_id);
    beats.push({
      round: null,
      out: [...out],
      kind: "jury",
      label: "Jury",
      caption: `The finale. The ${jurors.length || out.length} players voted out become the jury and question the final ${f.finalist_ids.length}: ${list(
        f.finalist_ids,
      )}.`,
      quotes: [],
      finale: true,
      ms: 4200,
    });
    for (const p of f.pitches) {
      beats.push({
        round: null,
        out: [...out],
        kind: "pitch",
        label: "Pitches",
        caption: `${nm(p.agent_id)} makes its case to the jury.`,
        quotes: [{ who: p.agent_id, context: "to the jury", text: clip(p.text, QUOTE_CHARS) }],
        focus: [p.agent_id],
        bubbles: [{ id: p.agent_id, text: clip(p.text, BUBBLE_CHARS) }],
        finale: true,
        ms: 6000,
      });
    }
    const votes = f.jury.filter((j) => j.finalist_id);
    const badges: Record<string, string> = {};
    f.finalist_ids.forEach((id) => (badges[id] = `${f.tally[id] ?? 0} ${(f.tally[id] ?? 0) === 1 ? "vote" : "votes"}`));
    const scores = [f.tally[f.winner_id] ?? 0, ...f.finalist_ids.filter((x) => x !== f.winner_id).map((x) => f.tally[x] ?? 0)];
    beats.push({
      round: null,
      out: [...out],
      kind: "verdict",
      label: "Winner",
      caption: `${nm(f.winner_id)} wins${votes.length ? `, ${scores.join(" to ")} on the jury vote` : ""}.`,
      quotes: votes
        .filter((j) => j.finalist_id === f.winner_id && j.reason)
        .slice(0, 2)
        .map((j) => ({ who: j.juror_id, context: "voting as a juror", text: clip(j.reason, QUOTE_CHARS) })),
      links: votes.map((j) => ({ from: j.juror_id, to: j.finalist_id as string, kind: "jury" as const, color: orgColor(modelOf(report, j.finalist_id)) })),
      badges,
      finale: true,
      winner: f.winner_id,
      ms: 7000,
    });
  }
  return beats;
}

function modelOf(report: StageReport, id: string | null | undefined): string | null {
  return report.models.find((m) => m.agent_id === id)?.model_id ?? null;
}

/* ---------------------------------------------------------------- stage */

export function SeasonStage({ report }: { report: StageReport }) {
  const beats = useMemo(() => buildBeats(report), [report]);
  const [i, setI] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [fast, setFast] = useState(false);
  const started = useRef(false);
  const beat = beats[Math.min(i, beats.length - 1)];
  // speakers take turns: one bubble at a time, so two never overlap
  const [turnAt, setTurnAt] = useState({ beat: 0, n: 0 });
  const turn = turnAt.beat === i ? turnAt.n : 0;
  const speaking = beat?.bubbles ?? NO_BUBBLES;

  useEffect(() => {
    if (turn >= speaking.length - 1) return;
    const share = readMs(speaking[turn].text);
    const t = setTimeout(() => setTurnAt({ beat: i, n: turn + 1 }), fast ? share / 2 : share);
    return () => clearTimeout(t);
  }, [i, turn, speaking, fast]);

  // start on its own unless the viewer asked for less motion
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (!reduce) setPlaying(true);
  }, []);

  useEffect(() => {
    if (!playing || !beat) return;
    if (i >= beats.length - 1) {
      setPlaying(false);
      return;
    }
    const t = setTimeout(() => setI((n) => n + 1), fast ? beat.ms / 2 : beat.ms);
    return () => clearTimeout(t);
  }, [playing, i, beat, beats.length, fast]);

  if (!beat) return <p className="empty">This season has no rounds to show.</p>;

  const players: CirclePlayer[] = [...report.models]
    .sort((a, b) => a.agent_id.localeCompare(b.agent_id))
    .map((m) => ({ id: m.agent_id, model: m.model, modelId: m.model_id, alias: m.alias }));
  const elimOrder = report.rounds.map((r) => r.eliminated_id).filter((x): x is string => !!x);
  const finalists = report.finale?.finalist_ids?.length
    ? report.finale.finalist_ids
    : players.map((p) => p.id).filter((id) => !elimOrder.includes(id));
  const layout = seatLayout(players, elimOrder, finalists);
  const at = (id: string): Point => (beat.finale ? layout.finale[id] : layout.circle[id]) ?? layout.circle[id];
  const verdictLines =
    beat.kind === "verdict" ? juryLines((beat.links ?? []).map((l) => ({ jurorId: l.from, finalistId: l.to })), layout.finale) : {};
  const jurors = new Set((report.finale?.jury ?? []).map((j) => j.juror_id));
  const nm = (id: string | null | undefined) => (id ? report.names[id] ?? id : "");
  const rounds = report.rounds.map((r) => r.round);
  const roundStart = (round: number | null) => beats.findIndex((b) => b.round === round);
  const go = (n: number) => {
    setPlaying(false);
    setI(Math.max(0, Math.min(beats.length - 1, n)));
  };

  return (
    <div className="stage">
      <div className="stage-view">
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={beat.caption}>
          <circle cx={W / 2} cy={288} r={212} className={`circle-ring${beat.finale ? " gone" : ""}`} />
          <g className={`jury-box${beat.finale ? " on" : ""}`}>
            <rect x={JURY_BOX.x} y={JURY_BOX.y} width={JURY_BOX.w} height={JURY_BOX.h} rx={18} />
            <text x={JURY_BOX.x + 18} y={JURY_BOX.y + 24} className="jury-label">
              THE JURY · players voted out
            </text>
          </g>
          <Campfire at={beat.finale ? FIRE_FINALE : FIRE_CIRCLE} winner={beat.kind === "verdict"} small={!!beat.finale} />

          {/* key on the beat so lines draw again each time */}
          <g key={`links-${i}`} className="links">
            {(beat.links ?? []).map((l, n) => {
              const a = at(l.from);
              const b = at(l.to);
              if (!a || !b) return null;
              const exact = beat.kind === "verdict" ? verdictLines[l.from] : undefined;
              return <LinkPath key={n} a={a} b={b} kind={l.kind} color={l.color} n={n} fromBox={beat.kind === "verdict"} exact={exact} />;
            })}
          </g>

          {players.map((p) => {
            const pos = at(p.id);
            const out = beat.out.includes(p.id);
            const juror = !!beat.finale && out && (jurors.size === 0 || jurors.has(p.id));
            const dim = beat.focus ? !beat.focus.includes(p.id) : false;
            const won = beat.winner === p.id;
            const immune = (beat.immune ?? []).includes(p.id) && !out;
            const leaving = beat.kind === "out" && beat.focus?.includes(p.id);
            const opacity = out && !juror ? 0.25 : dim ? 0.38 : 1;
            const badge = beat.badges?.[p.id];
            return (
              <g
                key={p.id}
                className={`seat${out ? " out" : ""}${juror ? " juror" : ""}${won ? " won" : ""}`}
                style={{ transform: `translate(${pos.x}px, ${pos.y}px)` }}
              >
                <title>{`${p.model}${p.alias ? `, playing as ${p.alias}` : ""}`}</title>
                <Torch lit={!out} />
                <g className="seat-body">
                  <circle r={40} className={`won-ring${won ? " on" : ""}`} />
                  <circle r={38} className={`out-ring${leaving ? " on" : ""}`} />
                  <circle r={36} className={`safe-ring${immune ? " on" : ""}`} />
                  <g className="avatar-wrap" opacity={opacity}>
                    <Avatar seed={p.modelId || p.model} color={orgColor(p.modelId)} alias={p.alias || ""} />
                  </g>
                </g>
                {beat.thinking && !out && (
                  <g className="thinking" transform="translate(30 -38)">
                    <circle cx={0} cy={0} r={3} />
                    <circle cx={9} cy={-6} r={4} />
                    <circle cx={21} cy={-12} r={5.5} />
                  </g>
                )}
                <text y={58} className="seat-name" opacity={dim && !out ? 0.5 : 1}>
                  {p.model}
                </text>
                <text y={73} className="seat-alias">
                  {won ? "winner" : immune ? "safe this round" : out && beat.finale ? "juror" : out ? "voted out" : p.alias ? `as ${p.alias}` : ""}
                </text>
                {badge && (
                  // beside the head, not above it, so it never covers the name of the seat above
                  <g key={`badge-${i}`} className={`stage-badge${beat.kind === "challenge" ? " score" : ""}`} transform="translate(62 -18)">
                    <rect x={-30} y={-12} width={60} height={22} rx={11} />
                    <text y={4}>{badge}</text>
                  </g>
                )}
              </g>
            );
          })}
        </svg>

        {/* speech bubbles are HTML so long lines wrap */}
        <div className="bubbles" key={`bubbles-${i}`}>
          {speaking.slice(turn, turn + 1).map((b) => {
            const pos = at(b.id);
            if (!pos) return null;
            const above = pos.y > H * 0.45;
            const left = (pos.x / W) * 100;
            const shift = left < 24 ? "-12%" : left > 76 ? "-88%" : "-50%";
            const style: React.CSSProperties = above
              ? { left: `${left}%`, bottom: `${((H - pos.y + 50) / H) * 100}%`, transform: `translateX(${shift})` }
              : { left: `${left}%`, top: `${((pos.y + 84) / H) * 100}%`, transform: `translateX(${shift})` };
            return (
              <div key={b.id} className={`bubble${above ? " above" : " below"}`} style={style}>
                <span className="bubble-who">{nm(b.id)}</span>
                {b.text}
              </div>
            );
          })}
        </div>
      </div>

      <div className="stage-side">
        <p className="eyebrow">
          {beat.round != null ? `Round ${beat.round}` : "Finale"} · {beat.label}
        </p>
        <p className="stage-caption" aria-live="polite">
          {beat.caption}
        </p>
        {beat.quotes.length > 0 && (
          <div className="stage-quotes">
            {beat.quotes.map((q, n) => (
              <figure key={n} className="stage-quote">
                <figcaption>
                  <span className="mdot" style={{ background: orgColor(modelOf(report, q.who)) }} />
                  <b>{nm(q.who)}</b>
                  {report.aliases[q.who] ? <span className="muted"> ({report.aliases[q.who]})</span> : null}
                  <span className="muted">
                    {" "}
                    · {q.context}
                    {q.to ? ` ${nm(q.to)}` : ""}
                  </span>
                </figcaption>
                <blockquote>{q.text}</blockquote>
              </figure>
            ))}
          </div>
        )}

        <div className="stage-controls">
          <button className="chip" onClick={() => go(i - 1)} disabled={i === 0} aria-label="Back one step">
            ◀
          </button>
          <button
            className="btn-solid"
            onClick={() => {
              if (i >= beats.length - 1) {
                setI(0);
                setPlaying(true);
              } else setPlaying((p) => !p);
            }}
          >
            {i >= beats.length - 1 ? "Watch again" : playing ? "Pause" : "Play"}
          </button>
          <button className="chip" onClick={() => go(i + 1)} disabled={i >= beats.length - 1} aria-label="Forward one step">
            ▶
          </button>
          <button className="chip" onClick={() => setFast((f) => !f)} aria-pressed={fast}>
            {fast ? "2× speed" : "1× speed"}
          </button>
        </div>
        <div className="stage-progress" aria-hidden="true">
          <span style={{ width: `${((i + 1) / beats.length) * 100}%` }} />
        </div>
        <div className="stage-rounds" role="group" aria-label="Jump to a round">
          {rounds.map((r) => (
            <button
              key={r}
              className={`chip${beat.round === r ? " on" : ""}`}
              aria-pressed={beat.round === r}
              onClick={() => go(roundStart(r))}
            >
              Round {r}
            </button>
          ))}
          {roundStart(null) >= 0 && (
            <button
              className={`chip${beat.round === null ? " on" : ""}`}
              aria-pressed={beat.round === null}
              onClick={() => go(roundStart(null))}
            >
              Finale
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/** A line between two seats: straight for talks, a gentle curve with a dot at the end for votes. */
function LinkPath({
  a,
  b,
  kind,
  color,
  n,
  fromBox,
  exact,
}: {
  a: Point;
  b: Point;
  kind: Link["kind"];
  color?: string;
  n: number;
  fromBox: boolean;
  // a jury vote's own start and end, so votes never share a line
  exact?: { from: Point; to: Point };
}) {
  const start = exact ? exact.from : fromBox ? { x: a.x, y: JURY_BOX.y + JURY_BOX.h } : a;
  const target = exact ? exact.to : b;
  const dx = target.x - start.x;
  const dy = target.y - start.y;
  const len = Math.hypot(dx, dy) || 1;
  // stop short of the avatar so the dot lands on its edge
  const end = exact ? target : { x: b.x - (dx / len) * 42, y: b.y - (dy / len) * 42 };
  const from = { x: start.x + (dx / len) * (fromBox ? 0 : 36), y: start.y + (dy / len) * (fromBox ? 0 : 36) };
  const bend = kind === "vote" ? 26 : 0;
  const mid = { x: (from.x + end.x) / 2 - (dy / len) * bend, y: (from.y + end.y) / 2 + (dx / len) * bend };
  const d = `M ${from.x} ${from.y} Q ${mid.x} ${mid.y} ${end.x} ${end.y}`;
  return (
    <g className={`link link-${kind}`} style={{ animationDelay: `${n * 0.12}s` }}>
      <path d={d} stroke={color} pathLength={1} style={{ animationDelay: `${n * 0.12}s` }} />
      {(kind === "vote" || kind === "jury" || kind === "idol") && (
        <circle cx={end.x} cy={end.y} r={5} fill={kind === "idol" ? "#d4a017" : color} className="link-end" />
      )}
    </g>
  );
}
