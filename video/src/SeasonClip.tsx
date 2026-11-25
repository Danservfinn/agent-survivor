import { AbsoluteFill, Easing, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";

import "../../frontend/src/app/globals.css";
import "./clip.css";

import {
  Avatar,
  Campfire,
  CirclePlayer,
  FIRE_CIRCLE,
  FIRE_FINALE,
  H,
  JURY_BOX,
  Point,
  Torch,
  W,
  seatLayout,
} from "../../frontend/src/app/season-circle";
import { friendlyDate, orgColor } from "../../frontend/src/app/score-table";
import { ClipProps, Segment, clipPlan } from "./plan";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;

/** Which segment the frame is in, how far into it, and the one before. */
function locate(plan: Segment[], frame: number, fps: number) {
  let start = 0;
  for (let n = 0; n < plan.length; n++) {
    const len = Math.round(plan[n].seconds * fps);
    if (frame < start + len || n === plan.length - 1) return { seg: plan[n], prev: plan[n - 1] ?? null, t: frame - start, len };
    start += len;
  }
  return { seg: plan[0], prev: null, t: 0, len: 1 };
}

export const SeasonClip = ({ report, handle, site }: ClipProps) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const plan = clipPlan(report);
  const { seg, prev, t, len } = locate(plan, frame, fps);

  const players: CirclePlayer[] = [...report.models]
    .sort((a, b) => a.agent_id.localeCompare(b.agent_id))
    .map((m) => ({ id: m.agent_id, model: m.model, modelId: m.model_id, alias: m.alias }));
  const elimOrder = report.rounds.map((r) => r.eliminated_id).filter((x): x is string => !!x);
  const finalists = report.finale?.finalist_ids?.length
    ? report.finale.finalist_ids
    : players.map((p) => p.id).filter((id) => !elimOrder.includes(id));
  const layout = seatLayout(players, elimOrder, finalists);
  const jurors = new Set((report.finale?.jury ?? []).map((j) => j.juror_id));
  const nm = (id: string) => report.names[id] ?? id;

  const beat = seg.kind === "title" ? null : seg.beat;
  const prevBeat = prev && prev.kind !== "title" ? prev.beat : null;
  const finale = !!beat?.finale;

  // the move into the jury box happens over the first second of the first finale beat
  const toFinale = finale && !prevBeat?.finale ? interpolate(t, [0, fps], [0, 1], { ...clamp, easing: Easing.bezier(0.45, 0, 0.2, 1) }) : finale ? 1 : 0;
  const at = (id: string): Point => {
    const a = layout.circle[id];
    const b = layout.finale[id] ?? a;
    return { x: a.x + (b.x - a.x) * toFinale, y: a.y + (b.y - a.y) * toFinale };
  };
  const fire = { x: FIRE_CIRCLE.x + (FIRE_FINALE.x - FIRE_CIRCLE.x) * toFinale, y: FIRE_CIRCLE.y + (FIRE_FINALE.y - FIRE_CIRCLE.y) * toFinale };

  const appear = (delay: number) => interpolate(t, [delay, delay + 0.4 * fps], [0, 1], { ...clamp, easing: Easing.bezier(0.16, 1, 0.3, 1) });
  const draw = interpolate(t, [0.25 * fps, 1.1 * fps], [0, 1], { ...clamp, easing: Easing.bezier(0.33, 1, 0.68, 1) });
  const leavingFade = interpolate(t, [0.3 * fps, 1.1 * fps], [1, 0.25], clamp);

  const outBefore = new Set(beat ? beat.out.filter((id) => !(beat.kind === "out" && beat.focus?.includes(id))) : []);
  const stageScale = 744 / W; // the stage is drawn 744px wide inside the 1080 frame

  return (
    <AbsoluteFill className="clip">
      <div className="clip-top">
        <span className="clip-brand">
          <span className="clip-dot" />
          LLM Survivor
        </span>
        <span className="clip-season">{report.season.season_id ? friendlyDate(report.season.season_id) : ""}</span>
      </div>

      <div className="clip-stage" style={{ width: W * stageScale, height: H * stageScale }}>
        <svg viewBox={`0 0 ${W} ${H}`} width={W * stageScale} height={H * stageScale}>
          <circle cx={W / 2} cy={288} r={212} className="circle-ring" style={{ opacity: 1 - toFinale }} />
          <g className="jury-box" style={{ opacity: toFinale }}>
            <rect x={JURY_BOX.x} y={JURY_BOX.y} width={JURY_BOX.w} height={JURY_BOX.h} rx={18} />
            <text x={JURY_BOX.x + 18} y={JURY_BOX.y + 24} className="jury-label">
              THE JURY · players voted out
            </text>
          </g>
          <g style={{ transform: `translate(${fire.x}px, ${fire.y}px) scale(${1 - 0.28 * toFinale})` }}>
            <g style={{ scale: `${1 + 0.04 * Math.sin(frame / 3)} ${1 - 0.04 * Math.sin(frame / 3)}`, transformOrigin: "0 24px" }}>
              <Campfire at={{ x: 0, y: 0 }} winner={beat?.kind === "verdict" && t > fps} small={false} />
            </g>
          </g>

          {/* links draw in over the first second of their beat */}
          <g className="clip-links">
            {(beat?.links ?? []).map((l, n) => {
              const a = at(l.from);
              const b = at(l.to);
              if (!a || !b) return null;
              const start = beat?.kind === "verdict" ? { x: a.x, y: JURY_BOX.y + JURY_BOX.h } : a;
              const dx = b.x - start.x;
              const dy = b.y - start.y;
              const L = Math.hypot(dx, dy) || 1;
              const end = { x: b.x - (dx / L) * 42, y: b.y - (dy / L) * 42 };
              const from = beat?.kind === "verdict" ? start : { x: start.x + (dx / L) * 36, y: start.y + (dy / L) * 36 };
              const bend = l.kind === "vote" ? 26 : 0;
              const mid = { x: (from.x + end.x) / 2 - (dy / L) * bend, y: (from.y + end.y) / 2 + (dx / L) * bend };
              const stroke = l.color ?? (l.kind === "broken" ? "#c0392b" : l.kind === "idol" ? "#d4a017" : "#2f54eb");
              const own = interpolate(t, [0.25 * fps + n * 2, 1.1 * fps + n * 2], [0, 1], clamp);
              return (
                <g key={n}>
                  <path
                    d={`M ${from.x} ${from.y} Q ${mid.x} ${mid.y} ${end.x} ${end.y}`}
                    pathLength={1}
                    strokeDasharray={1}
                    strokeDashoffset={1 - own}
                    stroke={stroke}
                    className={`clip-link clip-link-${l.kind}`}
                  />
                  {(l.kind === "vote" || l.kind === "jury") && <circle cx={end.x} cy={end.y} r={5} fill={stroke} opacity={own >= 1 ? 1 : 0} />}
                </g>
              );
            })}
          </g>

          {players.map((p, idx) => {
            const pos = at(p.id);
            const out = beat ? beat.out.includes(p.id) : false;
            const leaving = beat?.kind === "out" && beat.focus?.includes(p.id);
            const juror = finale && out && (jurors.size === 0 || jurors.has(p.id));
            const dim = beat?.focus ? !beat.focus.includes(p.id) : false;
            const won = beat?.kind === "verdict" && beat.winner === p.id;
            let opacity = 1;
            if (juror) opacity = dim ? 0.4 : 0.95;
            else if (leaving) opacity = leavingFade;
            else if (out || outBefore.has(p.id)) opacity = 0.25;
            else if (dim) opacity = 0.38;
            // on the title card the players arrive one by one
            const enter = seg.kind === "title" ? spring({ frame: t - idx * 3, fps, config: { damping: 14 } }) : 1;
            const torchLit = !(out && !leaving) && !(leaving && t > 0.5 * fps);
            const badge = beat?.badges?.[p.id];
            const wonRing = won ? interpolate(t, [0.8 * fps, 1.3 * fps], [0, 1], clamp) : 0;
            const label = won
              ? "winner"
              : juror
                ? "juror"
                : out || leaving
                  ? leaving && t < 0.5 * fps
                    ? p.alias ? `as ${p.alias}` : ""
                    : "voted out"
                  : p.alias
                    ? `as ${p.alias}`
                    : "";
            return (
              <g key={p.id} style={{ transform: `translate(${pos.x}px, ${pos.y}px) scale(${enter})` }}>
                <Torch lit={torchLit} />
                <circle r={40} className="won-ring" style={{ opacity: wonRing }} />
                {leaving && (
                  <circle
                    r={interpolate(t, [0, 0.8 * fps], [30, 54], clamp)}
                    className="clip-out-ring"
                    opacity={interpolate(t, [0, 0.8 * fps], [1, 0], clamp)}
                  />
                )}
                <g opacity={opacity} style={{ scale: won ? `${1 + 0.16 * wonRing}` : "1" }}>
                  <Avatar seed={p.modelId || p.model} color={orgColor(p.modelId)} alias={p.alias || ""} />
                </g>
                <text y={60} className="seat-name" opacity={dim && !juror ? 0.5 : out && !juror ? 0.55 : 1}>
                  {p.model}
                </text>
                <text y={77} className={`seat-alias${won ? " won" : out && !juror ? " out" : ""}`}>
                  {label}
                </text>
                {badge && (
                  <g transform="translate(62 -18)" opacity={appear(0.5 * fps)}>
                    <rect x={-32} y={-13} width={64} height={24} rx={12} className={`clip-badge${won ? " won" : ""}`} />
                    <text y={4.5} className="clip-badge-t">
                      {badge}
                    </text>
                  </g>
                )}
              </g>
            );
          })}
        </svg>

        {/* speech bubbles: the models' own words */}
        {(beat?.bubbles ?? []).map((b, n) => {
          const pos = at(b.id);
          const above = pos.y > H * 0.45;
          const left = (pos.x / W) * 100;
          const shift = left < 24 ? "-12%" : left > 76 ? "-88%" : "-50%";
          const show = appear(0.35 * fps + n * 0.5 * fps);
          return (
            <div
              key={b.id}
              className="clip-bubble"
              style={{
                left: `${left}%`,
                ...(above ? { bottom: `${((H - pos.y + 50) / H) * 100}%` } : { top: `${((pos.y + 86) / H) * 100}%` }),
                translate: `${shift} ${interpolate(show, [0, 1], [10, 0])}px`,
                opacity: show,
              }}
            >
              <span className="clip-bubble-who">{nm(b.id)}</span>
              {b.text}
            </div>
          );
        })}
      </div>

      <div className="clip-caption" style={{ opacity: seg.kind === "end" ? 0 : interpolate(t, [0, 0.3 * fps], [0, 1], clamp) }}>
        {seg.kind === "title" ? (
          <>
            <p className="clip-eyebrow">A game for AI models</p>
            <p className="clip-text">
              {players.length} AI models play Survivor under secret names. They make deals, break promises and vote each other
              out.
            </p>
          </>
        ) : beat ? (
          <>
            <p className="clip-eyebrow">{beat.round != null ? `Round ${beat.round} · ${beat.label}` : `Finale · ${beat.label}`}</p>
            <p className="clip-text">{beat.caption}</p>
          </>
        ) : null}
      </div>

      {seg.kind === "end" && (
        <EndCard
          t={t}
          len={len}
          winner={report.season.winner}
          // the post carries the full link; the card shows only the address, which reads at a glance
          reportUrl={site ? site.replace(/^https?:\/\//, "").replace(/\/$/, "") : null}
          handle={handle ?? null}
        />
      )}
    </AbsoluteFill>
  );
};

function EndCard({ t, len, winner, reportUrl, handle }: { t: number; len: number; winner: string | null; reportUrl: string | null; handle: string | null }) {
  const { fps } = useVideoConfig();
  const show = interpolate(t, [0, 0.5 * fps], [0, 1], { ...clamp, easing: Easing.bezier(0.16, 1, 0.3, 1) });
  const fadeOut = interpolate(t, [len - 0.4 * fps, len], [1, 0.98], clamp);
  return (
    <AbsoluteFill className="clip-end" style={{ opacity: show * fadeOut, justifyContent: "center", alignItems: "center" }}>
      <div className="clip-end-card" style={{ translate: `0 ${interpolate(show, [0, 1], [24, 0])}px` }}>
        <p className="clip-eyebrow">LLM Survivor</p>
        <p className="clip-end-title">{winner ? `${winner} won.` : "Season over."}</p>
        <p className="clip-end-text">Every vote, every reason and every broken promise is in the full report.</p>
        {reportUrl && <p className="clip-end-url">{reportUrl}</p>}
        {handle && <p className="clip-end-follow">Follow {handle} for the next season</p>}
      </div>
    </AbsoluteFill>
  );
}
