import { AbsoluteFill, Easing, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";

import "../../frontend/src/app/globals.css";
import "./clip.css";

import {
  Avatar,
  CIRCLE,
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
import { Beat, ClipProps, Segment, clipPlan } from "./plan";

type Camp = Extract<Segment, { kind: "camp" }>;

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
const ease = Easing.bezier(0.45, 0, 0.2, 1);

// a private talk: walk over, the first speaker, the reply, walk back (seconds)
const WALK = 1.0;
const RETURN = 0.8;

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

/**
 * Where everyone stands during the private talks. The featured pair steps to the
 * front, below the fire, so its words can sit above it. Every other group, and
 * anyone left on their own, takes one of the places spread evenly round the rest
 * of the circle, in the order of their seats, so no one walks through another.
 */
function campSpots(camp: Camp, seats: Record<string, Point>, active: string[]): { spots: Record<string, Point>; meet: Record<string, Point> } {
  const spots: Record<string, Point> = {};
  // where a group's chat dots go: just outside the circle, clear of every head
  const meet: Record<string, Point> = {};
  const { cx, cy, r } = CIRCLE;
  const featuredIds = camp.featured?.focus ?? [];
  const isFeatured = (g: { host: string; visitors: string[] }) =>
    g.visitors.length === 1 && featuredIds.includes(g.host) && featuredIds.includes(g.visitors[0]);
  const front = camp.groups.find(isFeatured);
  const clusters: string[][] = camp.groups.filter((g) => g !== front).map((g) => [g.host, ...g.visitors]);
  const grouped = new Set(camp.groups.flatMap((g) => [g.host, ...g.visitors]));
  for (const id of active) if (!grouped.has(id) && seats[id]) clusters.push([id]);

  const place = (members: string[], at: Point, along: Point, step: number, dots: Point) => {
    const side = (id: string) => (seats[id].x - at.x) * along.x + (seats[id].y - at.y) * along.y;
    [...members].sort((a, b) => side(a) - side(b)).forEach((id, i) => {
      const off = (i - (members.length - 1) / 2) * step;
      spots[id] = { x: at.x + along.x * off, y: at.y + along.y * off };
      meet[id] = dots;
    });
  };

  if (front) {
    const at = { x: cx, y: cy + r - 12 };
    place([front.host, ...front.visitors], at, { x: 1, y: 0 }, 150, { x: at.x, y: at.y - 66 });
  }

  // the free arc runs clockwise from just past the front (145°) round to 35°
  const FROM = 145;
  const ARC = 250;
  const angleOf = (ids: string[]) => {
    const a = ids.reduce((n, id) => n + Math.atan2(seats[id].y - cy, seats[id].x - cx), 0) / ids.length;
    return ((((a * 180) / Math.PI - FROM) % 360) + 360) % 360;
  };
  clusters
    .map((ids) => ({ ids, a: angleOf(ids) }))
    .sort((x, y) => x.a - y.a)
    .forEach(({ ids }, i) => {
      const deg = FROM + (ARC * (i + 0.5)) / clusters.length;
      const rad = (deg * Math.PI) / 180;
      const at = { x: cx + Math.cos(rad) * r, y: cy + Math.sin(rad) * r };
      // a column on the side of the circle needs more room than a row on top
      const step = 76 + 14 * Math.abs(Math.cos(rad));
      const out = 74 + 10 * Math.abs(Math.cos(rad));
      place(ids, at, { x: -Math.sin(rad), y: Math.cos(rad) }, step, { x: at.x + Math.cos(rad) * out, y: at.y + Math.sin(rad) * out - 6 });
    });
  return { spots, meet };
}

function campBeat(camp: Camp): Beat {
  const n = camp.talks;
  const lead = `${n} private ${n === 1 ? "talk" : "talks"} before the vote.`;
  return {
    round: camp.round,
    kind: "talk",
    label: "Private talks",
    caption: camp.featured ? `${lead} ${camp.featured.caption}` : `${lead} Each player picks someone to talk to alone.`,
    quotes: [],
    out: camp.out,
    links: [],
    bubbles: [],
    ms: 0,
  };
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

  const camp = seg.kind === "camp" ? seg : null;
  const beat: Beat | null = seg.kind === "title" ? null : seg.kind === "camp" ? campBeat(seg) : seg.beat;
  const prevBeat = prev && (prev.kind === "beat" || prev.kind === "end") ? prev.beat : null;
  const finale = !!beat?.finale;

  // the move into the jury box happens over the first second of the first finale beat
  const toFinale = finale && !prevBeat?.finale ? interpolate(t, [0, fps], [0, 1], { ...clamp, easing: ease }) : finale ? 1 : 0;
  const seat = (id: string): Point => {
    const a = layout.circle[id];
    const b = layout.finale[id] ?? a;
    return { x: a.x + (b.x - a.x) * toFinale, y: a.y + (b.y - a.y) * toFinale };
  };

  // private talks: walk over, talk, walk back
  const { spots, meet } = camp
    ? campSpots(camp, layout.circle, players.map((p) => p.id).filter((id) => !camp.out.includes(id)))
    : { spots: {}, meet: {} };
  const arrive = camp ? interpolate(t, [0, WALK * fps], [0, 1], { ...clamp, easing: ease }) : 0;
  const leave = camp ? interpolate(t, [len - RETURN * fps, len], [0, 1], { ...clamp, easing: ease }) : 0;
  const together = Math.min(arrive, 1 - leave);
  const walking = camp && together > 0.02 && together < 0.98;
  const at = (id: string): Point => {
    const s = seat(id);
    const c = spots[id];
    return c ? { x: s.x + (c.x - s.x) * together, y: s.y + (c.y - s.y) * together } : s;
  };

  // the featured pair: one speaks, then the other answers
  const talkStart = WALK * fps;
  const talkEnd = len - RETURN * fps;
  const turnAt = (talkStart + talkEnd) / 2;
  const featuredIds = camp?.featured?.focus ?? [];
  const featuredPair =
    camp?.groups.find((g) => g.visitors.length === 1 && featuredIds.includes(g.host) && featuredIds.includes(g.visitors[0])) ?? null;
  const spoken = featuredPair ? (camp?.featured?.bubbles ?? []).filter((b) => b.id === featuredPair.host || featuredPair.visitors.includes(b.id)) : [];
  const speaker = spoken.length ? (t < turnAt || spoken.length === 1 ? spoken[0].id : spoken[1].id) : null;

  const fire = { x: FIRE_CIRCLE.x + (FIRE_FINALE.x - FIRE_CIRCLE.x) * toFinale, y: FIRE_CIRCLE.y + (FIRE_FINALE.y - FIRE_CIRCLE.y) * toFinale };
  const appear = (delay: number) => interpolate(t, [delay, delay + 0.4 * fps], [0, 1], { ...clamp, easing: Easing.bezier(0.16, 1, 0.3, 1) });
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
          <circle cx={CIRCLE.cx} cy={CIRCLE.cy} r={CIRCLE.r} className="circle-ring" style={{ opacity: 1 - toFinale }} />
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

          {/* each torch stays at its seat while its player walks */}
          {players.map((p) => {
            const out = beat ? beat.out.includes(p.id) : false;
            const leaving = beat?.kind === "out" && beat.focus?.includes(p.id);
            const lit = !(out && !leaving) && !(leaving && t > 0.5 * fps);
            const s = seat(p.id);
            return (
              <g key={p.id} style={{ transform: `translate(${s.x}px, ${s.y}px)` }}>
                <Torch lit={lit} />
              </g>
            );
          })}

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
            const inPair = camp ? camp.groups.some((g) => g.host === p.id || g.visitors.includes(p.id)) : false;
            const inFeatured = !!featuredPair && (featuredPair.host === p.id || featuredPair.visitors.includes(p.id));
            let opacity = 1;
            if (juror) opacity = dim ? 0.4 : 0.95;
            else if (leaving) opacity = leavingFade;
            else if (out || outBefore.has(p.id)) opacity = camp ? 0.25 * (1 - 0.85 * together) : 0.25; // the gone step back while the rest talk
            else if (dim) opacity = 0.38;
            else if (camp && !inPair) opacity = 1 - 0.55 * together; // left alone at the fire
            // on the title card the players arrive one by one
            const enter = seg.kind === "title" ? spring({ frame: t - idx * 3, fps, config: { damping: 14 } }) : 1;
            // a small hop in step while walking, and a lean in while speaking
            const hop = walking && inPair ? -Math.abs(Math.sin(t * 0.55 + idx)) * 7 : 0;
            const speaking = speaker === p.id && t > talkStart && t < talkEnd;
            const lean = speaking ? 1 + 0.06 * Math.abs(Math.sin(t * 0.35)) : 1;
            // during the talks only the featured pair keeps its name, so labels never pile up
            const nameFade = camp && !inFeatured ? 1 - together : 1;
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
              <g key={p.id} style={{ transform: `translate(${pos.x}px, ${pos.y + hop}px) scale(${enter})` }}>
                <circle r={40} className="won-ring" style={{ opacity: wonRing }} />
                {speaking && <circle r={44} className="clip-speak-ring" opacity={interpolate(t % 30, [0, 30], [0.9, 0.2])} />}
                {leaving && (
                  <circle
                    r={interpolate(t, [0, 0.8 * fps], [30, 54], clamp)}
                    className="clip-out-ring"
                    opacity={interpolate(t, [0, 0.8 * fps], [1, 0], clamp)}
                  />
                )}
                <g opacity={opacity} style={{ scale: won ? `${1 + 0.16 * wonRing}` : `${lean}` }}>
                  <Avatar seed={p.modelId || p.model} color={orgColor(p.modelId)} alias={p.alias || ""} />
                </g>
                <g opacity={nameFade}>
                  <text y={60} className="seat-name" opacity={dim && !juror ? 0.5 : out && !juror ? 0.55 : 1}>
                    {p.model}
                  </text>
                  <text y={77} className={`seat-alias${won ? " won" : out && !juror ? " out" : ""}`}>
                    {label}
                  </text>
                </g>
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

          {/* the other pairs: chat dots above their heads */}
          {camp?.groups
            .filter((p) => p !== featuredPair || spoken.length === 0)
            .map((p, n) => {
              const m = meet[p.host];
              if (!m) return null;
              const show = interpolate(t, [talkStart - 4 + n * 3, talkStart + 6 + n * 3], [0, 1], clamp) * (1 - leave);
              return (
                <g key={p.host} transform={`translate(${m.x} ${m.y})`} opacity={show}>
                  <rect x={-26} y={-13} width={52} height={26} rx={13} className="clip-chat" />
                  {[0, 1, 2].map((i) => (
                    <circle key={i} cx={-12 + i * 12} cy={-Math.max(0, Math.sin(frame / 4 - i * 0.9)) * 3.5} r={3.6} className="clip-chat-dot" />
                  ))}
                </g>
              );
            })}
        </svg>

        {/* the featured talk: the models' own words, one speaker at a time */}
        {camp &&
          featuredPair &&
          spoken.map((b, n) => {
            const m = meet[featuredPair.host];
            const from = n === 0 ? talkStart : turnAt;
            const to = n === 0 && spoken.length > 1 ? turnAt : talkEnd;
            const show = interpolate(t, [from, from + 0.35 * fps], [0, 1], clamp) * interpolate(t, [to - 0.25 * fps, to], [1, 0], clamp);
            if (show <= 0) return null;
            return <Bubble key={b.id} at={m} who={nm(b.id)} text={b.text} show={show} />;
          })}

        {/* other beats: the bubbles the stage chose */}
        {!camp &&
          (beat?.bubbles ?? []).map((b, n) => (
            <Bubble key={b.id} at={at(b.id)} who={nm(b.id)} text={b.text} show={appear(0.35 * fps + n * 0.5 * fps)} />
          ))}
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

/** A speech bubble over the stage, above the speaker when it is low on the stage and below it when high. */
function Bubble({ at, who, text, show }: { at: Point; who: string; text: string; show: number }) {
  const above = at.y > H * 0.42;
  const left = (at.x / W) * 100;
  const shift = left < 24 ? "-12%" : left > 76 ? "-88%" : "-50%";
  return (
    <div
      className="clip-bubble"
      style={{
        left: `${left}%`,
        ...(above ? { bottom: `${((H - at.y + 50) / H) * 100}%` } : { top: `${((at.y + 86) / H) * 100}%` }),
        translate: `${shift} ${interpolate(show, [0, 1], [10, 0])}px`,
        opacity: show,
      }}
    >
      <span className="clip-bubble-who">{who}</span>
      {text}
    </div>
  );
}

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
