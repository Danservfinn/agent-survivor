"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { orgColor } from "./score-table";

export type CirclePlayer = {
  id: string;
  model: string;
  modelId?: string | null;
  alias?: string | null;
};

/**
 * A season replayed as a picture: every player sits round a fire with a torch.
 * Each step one player is voted out (their torch goes dark and they fade), and
 * at the end the winner lights up. Players keep their seat order, so the picture
 * never gives away who wins before the replay gets there.
 */
export function SeasonCircle({
  players,
  eliminations,
  winnerId,
  title,
}: {
  players: CirclePlayer[];
  eliminations: { round: number; id: string }[];
  winnerId: string | null;
  title?: string;
}) {
  const finalStep = eliminations.length + (winnerId ? 1 : 0);
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(false);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const started = useRef(false);

  // play once the picture scrolls into view; reduced motion starts on the finished season instead
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
    const t = setTimeout(() => setStep((s) => s + 1), step === 0 ? 1400 : 2200);
    return () => clearTimeout(t);
  }, [playing, step, finalStep]);

  const goTo = useCallback((s: number) => {
    setPlaying(false);
    setStep(s);
  }, []);

  const outAt: Record<string, number> = {};
  eliminations.forEach((e, i) => (outAt[e.id] = i + 1));
  const showWinner = winnerId != null && step >= finalStep;
  const justOut = step >= 1 && step <= eliminations.length ? eliminations[step - 1].id : null;
  const byId: Record<string, CirclePlayer> = {};
  players.forEach((p) => (byId[p.id] = p));
  const left = players.filter((p) => !(outAt[p.id] && outAt[p.id] <= step)).length;

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
  } else if (showWinner && winnerId) {
    const p = byId[winnerId];
    caption = (
      <>
        <b>{p?.model ?? winnerId} wins.</b> The players voted out chose it.
      </>
    );
  }

  // seat geometry, in the SVG's own units
  const W = 620;
  const H = 600;
  const cx = W / 2;
  const cy = 288;
  const R = 212;

  return (
    <figure className="circle" ref={boxRef}>
      {title && <figcaption className="circle-title">{title}</figcaption>}
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`The season as a circle of ${players.length} players around a fire.`}>
        <circle cx={cx} cy={cy} r={R} className="circle-ring" />
        <Campfire x={cx} y={cy} winner={showWinner} />
        {players.map((p, i) => {
          const a = -Math.PI / 2 + (i * 2 * Math.PI) / players.length;
          const x = cx + R * Math.cos(a);
          const y = cy + R * Math.sin(a);
          const out = outAt[p.id] != null && outAt[p.id] <= step;
          const won = showWinner && p.id === winnerId;
          const fresh = p.id === justOut;
          const outRound = outAt[p.id] != null ? eliminations[outAt[p.id] - 1].round : null;
          return (
            <g
              key={p.id}
              className={`seat${out ? " out" : ""}${won ? " won" : ""}${fresh ? " fresh" : ""}`}
              transform={`translate(${x} ${y})`}
            >
              <title>
                {p.model}
                {p.alias ? `, playing as ${p.alias}` : ""}
                {out && outRound ? `. Voted out in round ${outRound}.` : won ? ". Won the season." : ""}
              </title>
              <Torch lit={!out} />
              <g className="seat-body">
                {/* the rings are always there and only switch on, so the avatar is never rebuilt mid-replay */}
                <circle r={40} className={`won-ring${won ? " on" : ""}`} />
                <circle r={38} className={`out-ring${fresh ? " on" : ""}`} />
                <g className="avatar-wrap" opacity={out ? 0.28 : 1}>
                  <Avatar seed={p.modelId || p.model} color={orgColor(p.modelId)} alias={p.alias || ""} />
                </g>
              </g>
              <text y={58} className="seat-name">
                {p.model}
              </text>
              <text y={73} className="seat-alias">
                {won ? "winner" : out && outRound ? `out in round ${outRound}` : p.alias ? `as ${p.alias}` : ""}
              </text>
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
        <div className="circle-dots" role="group" aria-label="Jump to a round">
          {Array.from({ length: finalStep + 1 }, (_, s) => (
            <button
              key={s}
              className={`dot${s === step ? " on" : ""}${s < step ? " past" : ""}`}
              onClick={() => goTo(s)}
              aria-label={
                s === 0 ? "Start" : s <= eliminations.length ? `Round ${eliminations[s - 1].round}` : "The winner"
              }
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
      {/* shoulders */}
      <path d="M -26 40 Q -26 18 0 18 Q 26 18 26 40 Z" fill={color} className="av-body" />
      {/* antenna */}
      {antenna === 0 && (
        <g className="av-ink">
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
      {/* head */}
      {head === 0 && <circle r={25} cy={-4} fill={color} />}
      {head === 1 && <rect x={-25} y={-28} width={50} height={46} rx={13} fill={color} />}
      {head === 2 && <path d="M 0 -30 L 25 -17 L 25 9 L 0 22 L -25 9 L -25 -17 Z" fill={color} strokeLinejoin="round" />}
      {/* face plate */}
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
      {/* leaf badge with the secret name's first letter */}
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

function Campfire({ x, y, winner }: { x: number; y: number; winner: boolean }) {
  return (
    <g className={`fire${winner ? " big" : ""}`} transform={`translate(${x} ${y})`}>
      <ellipse cx={0} cy={30} rx={62} ry={14} className="fire-glow" />
      <line x1={-34} y1={34} x2={30} y2={18} className="fire-log" />
      <line x1={34} y1={34} x2={-30} y2={18} className="fire-log" />
      <g className="fire-flame">
        <path d="M 0 -42 Q 26 -10 16 14 Q 10 26 0 26 Q -10 26 -16 14 Q -26 -10 0 -42 Z" className="flame-outer" />
        <path d="M 0 -18 Q 13 0 8 14 Q 4 20 0 20 Q -4 20 -8 14 Q -13 0 0 -18 Z" className="flame-inner" />
      </g>
    </g>
  );
}
