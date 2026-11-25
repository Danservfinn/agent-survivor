"use client";

import { useEffect, useRef, useState } from "react";

import { orgColor } from "./score-table";

export type CirclePlayer = {
  id: string;
  model: string;
  modelId?: string | null;
  alias?: string | null;
};

type Point = { x: number; y: number };

/* drawing units: the SVG's viewBox */
const W = 620;
const H = 600;
const CIRCLE = { cx: W / 2, cy: 288, r: 212 };
const JURY_BOX = { x: 34, y: 18, w: W - 68, h: 290 };
const FIRE_CIRCLE: Point = { x: W / 2, y: 288 };
const FIRE_FINALE: Point = { x: W / 2, y: 346 };
const FINALIST_Y = 484;

/**
 * A season replayed as a picture. Players sit round a fire with a torch each;
 * every step one is voted out (torch dark, faded). For the finale the players
 * voted out walk to a jury box and the final three stand together by the fire.
 * The last step draws each juror's vote to the finalist it chose and lights up
 * the winner. Seats keep a fixed order, so nothing gives the result away early.
 */
export function SeasonCircle({
  players,
  eliminations,
  winnerId,
  juryVotes = [],
  title,
}: {
  players: CirclePlayer[];
  eliminations: { round: number; id: string }[];
  winnerId: string | null;
  /** one entry per juror: who it voted for */
  juryVotes?: { jurorId: string; finalistId: string }[];
  title?: string;
}) {
  const E = eliminations.length;
  const hasFinale = winnerId != null;
  const juryStep = hasFinale ? E + 1 : -1;
  const finalStep = hasFinale ? E + 2 : E;
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(false);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const started = useRef(false);

  // play once the picture is on screen; reduced motion opens on the finished season instead
  useEffect(() => {
    const reduce = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      setStep(finalStep);
      return;
    }
    const box = boxRef.current;
    if (!box || typeof IntersectionObserver === "undefined") {
      setPlaying(true);
      return;
    }
    const seen = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting) && !started.current) {
          started.current = true;
          setPlaying(true);
        }
      },
      { threshold: 0.4 },
    );
    seen.observe(box);
    // already on screen when the page loads (it sits in the hero): start without waiting for a scroll
    const rect = box.getBoundingClientRect();
    if (rect.top < window.innerHeight && rect.bottom > 0) {
      started.current = true;
      setPlaying(true);
    }
    return () => seen.disconnect();
  }, [finalStep]);

  useEffect(() => {
    if (!playing) return;
    if (step >= finalStep) {
      setPlaying(false);
      return;
    }
    // the move into the jury takes longer to watch than a vote
    const wait = step === 0 ? 1400 : step === juryStep ? 3400 : 2200;
    const t = setTimeout(() => setStep((s) => s + 1), wait);
    return () => clearTimeout(t);
  }, [playing, step, finalStep, juryStep]);

  const goTo = (s: number) => {
    setPlaying(false);
    setStep(s);
  };

  const byId: Record<string, CirclePlayer> = {};
  players.forEach((p) => (byId[p.id] = p));
  const outAt: Record<string, number> = {};
  eliminations.forEach((e, i) => (outAt[e.id] = i + 1));
  const outRound = (id: string) => (outAt[id] != null ? eliminations[outAt[id] - 1].round : null);
  const isOut = (id: string) => outAt[id] != null && outAt[id] <= step;

  const finale = hasFinale && step >= juryStep;
  const verdict = hasFinale && step >= finalStep;
  const justOut = step >= 1 && step <= E ? eliminations[step - 1].id : null;

  // the jury is whoever cast a jury vote; seasons without that record use everyone voted out
  const jurorSet = new Set(juryVotes.length ? juryVotes.map((v) => v.jurorId) : eliminations.map((e) => e.id));
  const jurors = eliminations.map((e) => e.id).filter((id) => jurorSet.has(id));
  const finalists = players.filter((p) => outAt[p.id] == null).map((p) => p.id);
  const tally: Record<string, number> = {};
  finalists.forEach((f) => (tally[f] = 0));
  juryVotes.forEach((v) => (tally[v.finalistId] = (tally[v.finalistId] ?? 0) + 1));
  const voteOf: Record<string, string> = {};
  juryVotes.forEach((v) => (voteOf[v.jurorId] = v.finalistId));

  // seat positions: round the fire, or (finale) jury box and the final three
  const circlePos: Record<string, Point> = {};
  players.forEach((p, i) => {
    const a = -Math.PI / 2 + (i * 2 * Math.PI) / players.length;
    circlePos[p.id] = { x: CIRCLE.cx + CIRCLE.r * Math.cos(a), y: CIRCLE.cy + CIRCLE.r * Math.sin(a) };
  });
  const finalePos: Record<string, Point> = {};
  const benched = eliminations.map((e) => e.id); // everyone out sits on the bench; non-jurors stay faded
  const cols = benched.length <= 6 ? 3 : 4;
  const colW = JURY_BOX.w / cols;
  benched.forEach((id, i) => {
    const row = Math.floor(i / cols);
    const inRow = Math.min(cols, benched.length - row * cols);
    const rowOffset = ((cols - inRow) * colW) / 2;
    finalePos[id] = { x: JURY_BOX.x + rowOffset + colW * ((i % cols) + 0.5), y: JURY_BOX.y + 82 + row * 122 };
  });
  const gap = Math.min(170, (W - 80) / Math.max(1, finalists.length));
  finalists.forEach((id, i) => {
    finalePos[id] = { x: W / 2 + (i - (finalists.length - 1) / 2) * gap, y: FINALIST_Y };
  });
  const pos = (id: string) => (finale ? finalePos[id] ?? circlePos[id] : circlePos[id]);
  const fire = finale ? FIRE_FINALE : FIRE_CIRCLE;

  const left = players.length - Math.min(step, E);
  const names = (ids: string[]) => {
    const list = ids.map((id) => byId[id]?.model ?? id);
    return list.length <= 1 ? list.join("") : `${list.slice(0, -1).join(", ")} and ${list[list.length - 1]}`;
  };
  let caption: React.ReactNode;
  if (step === 0) caption = <>Round 1 begins. {players.length} AI models, each under a secret tree name.</>;
  else if (justOut) {
    const p = byId[justOut];
    caption = (
      <>
        <b>Round {eliminations[step - 1].round}:</b> {p?.model ?? justOut}
        {p?.alias ? ` (${p.alias})` : ""} is voted out. {left} left.
      </>
    );
  } else if (finale && !verdict) {
    caption = (
      <>
        <b>The finale.</b> The {jurors.length} players voted out become the jury and question the final{" "}
        {finalists.length}: {names(finalists)}.
      </>
    );
  } else if (verdict && winnerId) {
    const votes = tally[winnerId] ?? 0;
    const others = finalists.filter((f) => f !== winnerId).map((f) => tally[f] ?? 0);
    caption = (
      <>
        <b>{byId[winnerId]?.model ?? winnerId} wins</b>
        {juryVotes.length ? `, ${[votes, ...others].join(" to ")} on the jury vote.` : ", chosen by the jury."}
      </>
    );
  }

  const stepLabel = (s: number) =>
    s === 0 ? "Start" : s <= E ? `Round ${eliminations[s - 1].round}` : s === juryStep ? "The jury" : "The winner";

  return (
    <figure className="circle" ref={boxRef}>
      {title && <figcaption className="circle-title">{title}</figcaption>}
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`The season as ${players.length} players around a fire.`}>
        <circle cx={CIRCLE.cx} cy={CIRCLE.cy} r={CIRCLE.r} className={`circle-ring${finale ? " gone" : ""}`} />
        <g className={`jury-box${finale ? " on" : ""}`}>
          <rect x={JURY_BOX.x} y={JURY_BOX.y} width={JURY_BOX.w} height={JURY_BOX.h} rx={18} />
          <text x={JURY_BOX.x + 18} y={JURY_BOX.y + 24} className="jury-label">
            THE JURY · players voted out
          </text>
          <text x={W / 2} y={FINALIST_Y - 76} className={`final-label${verdict ? " hide" : ""}`}>
            THE FINAL {finalists.length}
          </text>
        </g>
        <Campfire at={fire} winner={verdict} small={finale} />

        {/* each juror's vote, drawn at the verdict */}
        <g className={`votes${verdict ? " on" : ""}`}>
          {juryVotes.map((v) => {
            const a = finalePos[v.jurorId];
            const b = finalePos[v.finalistId];
            if (!a || !b) return null;
            // from the foot of the jury box under the juror, so no line crosses a name
            return (
              <line
                key={v.jurorId}
                x1={a.x}
                y1={JURY_BOX.y + JURY_BOX.h}
                x2={b.x}
                y2={b.y - 44}
                stroke={orgColor(byId[v.finalistId]?.modelId)}
                className="vote-line"
              />
            );
          })}
        </g>

        {players.map((p) => {
          const at = pos(p.id);
          const out = isOut(p.id);
          const juror = finale && jurorSet.has(p.id);
          const won = verdict && p.id === winnerId;
          const fresh = p.id === justOut;
          const round = outRound(p.id);
          const opacity = out ? (juror ? 0.92 : 0.28) : 1;
          let label = p.alias ? `as ${p.alias}` : "";
          if (won) label = "winner";
          else if (juror && verdict && voteOf[p.id]) label = `voted ${byId[voteOf[p.id]]?.model ?? voteOf[p.id]}`;
          else if (juror) label = `juror · out round ${round}`;
          else if (out && round) label = `out in round ${round}`;
          else if (finale) label = verdict ? `${tally[p.id] ?? 0} jury votes` : p.alias ? `as ${p.alias}` : "finalist";
          return (
            <g
              key={p.id}
              className={`seat${out ? " out" : ""}${juror ? " juror" : ""}${won ? " won" : ""}${fresh ? " fresh" : ""}`}
              style={{ transform: `translate(${at.x}px, ${at.y}px)` }}
            >
              <title>
                {p.model}
                {p.alias ? `, playing as ${p.alias}` : ""}
                {round && out ? `. Voted out in round ${round}.` : won ? ". Won the season." : ""}
              </title>
              <Torch lit={!out} />
              <g className="seat-body">
                {/* the rings are always there and only switch on, so the avatar is never rebuilt mid-replay */}
                <circle r={40} className={`won-ring${won ? " on" : ""}`} />
                <circle r={38} className={`out-ring${fresh ? " on" : ""}`} />
                <g className="avatar-wrap" opacity={opacity}>
                  <Avatar seed={p.modelId || p.model} color={orgColor(p.modelId)} alias={p.alias || ""} />
                </g>
              </g>
              <text y={58} className="seat-name">
                {p.model}
              </text>
              <text y={73} className="seat-alias">
                {label}
              </text>
              {verdict && finalists.includes(p.id) && juryVotes.length > 0 && (
                <g className="vote-badge" transform="translate(0 -62)">
                  <rect x={-34} y={-12} width={68} height={22} rx={11} />
                  <text y={4}>
                    {tally[p.id] ?? 0} {(tally[p.id] ?? 0) === 1 ? "vote" : "votes"}
                  </text>
                </g>
              )}
            </g>
          );
        })}
      </svg>
      <p className="circle-caption" aria-live="polite">
        {caption}
      </p>
      <div className="circle-controls">
        <button
          className="chip"
          onClick={() => {
            if (step >= finalStep) {
              setStep(0);
              setPlaying(true);
            } else setPlaying((p) => !p);
          }}
        >
          {step >= finalStep ? "Replay" : playing ? "Pause" : "Play"}
        </button>
        <div className="circle-dots" role="group" aria-label="Jump to a step">
          {Array.from({ length: finalStep + 1 }, (_, s) => (
            <button
              key={s}
              className={`dot${s === step ? " on" : ""}${s < step ? " past" : ""}${s > E ? " finale" : ""}`}
              onClick={() => goTo(s)}
              aria-label={stepLabel(s)}
              title={stepLabel(s)}
              aria-current={s === step ? "step" : undefined}
            />
          ))}
        </div>
      </div>
    </figure>
  );
}

/** FNV-1a: the same model always gets the same face. */
function hash(text: string): number {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

/** A small robot in the maker's color, wearing its secret name on a leaf badge. */
function Avatar({ seed, color, alias }: { seed: string; color: string; alias: string }) {
  const h = hash(seed);
  const head = h % 3;
  const eyes = (h >>> 3) % 3;
  const antenna = (h >>> 6) % 3;
  const mouth = (h >>> 9) % 2;
  return (
    <g className="avatar">
      <path d="M -26 40 Q -26 18 0 18 Q 26 18 26 40 Z" fill={color} className="av-body" />
      {antenna === 0 && (
        <g>
          <line x1={0} y1={-26} x2={0} y2={-36} stroke={color} strokeWidth={3} strokeLinecap="round" />
          <circle cx={0} cy={-38} r={4} fill={color} />
        </g>
      )}
      {antenna === 1 && (
        <g>
          <line x1={-8} y1={-25} x2={-13} y2={-35} stroke={color} strokeWidth={3} strokeLinecap="round" />
          <line x1={8} y1={-25} x2={13} y2={-35} stroke={color} strokeWidth={3} strokeLinecap="round" />
          <circle cx={-13} cy={-36} r={3} fill={color} />
          <circle cx={13} cy={-36} r={3} fill={color} />
        </g>
      )}
      {head === 0 && <circle r={25} cy={-4} fill={color} />}
      {head === 1 && <rect x={-25} y={-28} width={50} height={46} rx={13} fill={color} />}
      {head === 2 && <path d="M 0 -30 L 25 -17 L 25 9 L 0 22 L -25 9 L -25 -17 Z" fill={color} strokeLinejoin="round" />}
      <rect x={-17} y={-17} width={34} height={24} rx={9} className="av-face" />
      {eyes === 0 && (
        <g className="av-eyes">
          <circle cx={-7} cy={-6} r={3.2} />
          <circle cx={7} cy={-6} r={3.2} />
        </g>
      )}
      {eyes === 1 && (
        <g className="av-eyes">
          <rect x={-11} y={-8} width={8} height={4} rx={2} />
          <rect x={3} y={-8} width={8} height={4} rx={2} />
        </g>
      )}
      {eyes === 2 && (
        <g className="av-eyes">
          <ellipse cx={-7} cy={-6} rx={2.4} ry={4} />
          <ellipse cx={7} cy={-6} rx={2.4} ry={4} />
        </g>
      )}
      {mouth === 0 ? (
        <path d="M -5 1 Q 0 4.5 5 1" className="av-mouth" />
      ) : (
        <line x1={-4} y1={2} x2={4} y2={2} className="av-mouth" />
      )}
      <g transform="translate(13 29)">
        <path d="M 0 -9 Q 9 -9 9 0 Q 9 9 0 9 Q -9 9 -9 0 Q -9 -9 0 -9 Z" className="av-leaf" />
        <text y={3.5} className="av-leaf-t">
          {(alias || "?").slice(0, 1).toUpperCase()}
        </text>
      </g>
    </g>
  );
}

function Torch({ lit }: { lit: boolean }) {
  return (
    <g className={`torch${lit ? "" : " dark"}`} transform="translate(-40 -12)">
      <line x1={0} y1={-6} x2={0} y2={34} className="torch-stick" />
      <path d="M -5 -6 L 5 -6 L 3 0 L -3 0 Z" className="torch-cup" />
      <g className="torch-flame">
        <path d="M 0 -24 Q 8 -14 4 -8 Q 2 -5 0 -5 Q -2 -5 -4 -8 Q -8 -14 0 -24 Z" className="flame-outer" />
        <path d="M 0 -17 Q 4 -11 2 -8 Q 1 -7 0 -7 Q -1 -7 -2 -8 Q -4 -11 0 -17 Z" className="flame-inner" />
      </g>
    </g>
  );
}

function Campfire({ at, winner, small }: { at: Point; winner: boolean; small: boolean }) {
  return (
    <g className="fire-pos" style={{ transform: `translate(${at.x}px, ${at.y}px) scale(${small ? 0.72 : 1})` }}>
      <g className={`fire${winner ? " big" : ""}`}>
        <ellipse cx={0} cy={30} rx={62} ry={14} className="fire-glow" />
        <line x1={-34} y1={34} x2={30} y2={18} className="fire-log" />
        <line x1={34} y1={34} x2={-30} y2={18} className="fire-log" />
        <g className="fire-flame">
          <path d="M 0 -42 Q 26 -10 16 14 Q 10 26 0 26 Q -10 26 -16 14 Q -26 -10 0 -42 Z" className="flame-outer" />
          <path d="M 0 -18 Q 13 0 8 14 Q 4 20 0 20 Q -4 20 -8 14 Q -13 0 0 -18 Z" className="flame-inner" />
        </g>
      </g>
    </g>
  );
}
