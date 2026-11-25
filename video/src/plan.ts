import { buildBeats, StageReport } from "../../frontend/src/app/season-stage";

export type ClipProps = {
  report: StageReport;
  /** X handle credited on the end card, e.g. "@Danieldoesx" */
  handle?: string | null;
  /** public address of the site, e.g. "https://llmsurvivor.kurult.ai" */
  site?: string | null;
};

export type Beat = ReturnType<typeof buildBeats>[number];

/** A group talking in private: the `visitors` walk over to the `host`. */
export type Group = { host: string; visitors: string[] };

export type Segment =
  | { kind: "title"; seconds: number }
  | { kind: "camp"; round: number; out: string[]; groups: Group[]; talks: number; featured: Beat | null; seconds: number }
  | { kind: "beat"; beat: Beat; seconds: number }
  | { kind: "end"; beat: Beat | null; seconds: number };

/**
 * Every round plays the same way: the players pair off and walk over to talk
 * (one pair speaks in its own words, the rest chat), then everyone goes back to
 * vote and one player leaves. The round with the season's first broken promise
 * also shows the promise check and the last words. Then the jury and the verdict.
 */
export function clipPlan(report: StageReport): Segment[] {
  const beats = buildBeats(report);
  const plan: Segment[] = [{ kind: "title", seconds: 3 }];

  const keyRound = beats.find((b) => b.kind === "promises")?.round ?? null;
  const out: string[] = [];

  for (const r of report.rounds) {
    const inRound = beats.filter((b) => b.round === r.round);
    const active = report.models.map((m) => m.agent_id).filter((id) => !out.includes(id));

    // the talk to hear: one with a broken promise between its players, else the round's first
    const broken = r.promises.filter((p) => p.status === "broken");
    const talks = inRound.filter((b) => b.kind === "talk");
    const featured =
      talks.find((b) => broken.some((p) => b.focus?.includes(p.by_id) && b.focus?.includes(p.to_id))) ?? talks[0] ?? null;

    const groups = talkGroups(r.talks, active, featured);
    const talkCount = r.talks.filter((t) => t.kind === "conversation").length || r.talks.length;
    if (groups.length) plan.push({ kind: "camp", round: r.round, out: [...out], groups, talks: talkCount, featured, seconds: r.round === keyRound ? 7 : 6 });

    const vote = inRound.find((b) => b.kind === "vote");
    if (vote) plan.push({ kind: "beat", beat: vote, seconds: 2.3 });
    if (r.round === keyRound) {
      const promise = inRound.find((b) => b.kind === "promises");
      if (promise) plan.push({ kind: "beat", beat: promise, seconds: 4 });
    }
    const outBeat = inRound.find((b) => b.kind === "out");
    if (outBeat) {
      const full = r.round === keyRound;
      plan.push({ kind: "beat", beat: full ? outBeat : { ...outBeat, bubbles: [], quotes: [] }, seconds: full ? 3.4 : 1.9 });
    }
    if (r.eliminated_id) out.push(r.eliminated_id);
  }

  const jury = beats.find((b) => b.kind === "jury");
  const verdict = beats.find((b) => b.kind === "verdict");
  if (jury) plan.push({ kind: "beat", beat: jury, seconds: 2.8 });
  if (verdict) plan.push({ kind: "beat", beat: verdict, seconds: 4.6 });
  plan.push({ kind: "end", beat: verdict ?? beats[beats.length - 1] ?? null, seconds: 4 });
  return plan;
}

/**
 * Who walks over to whom this round. The first player named in a talk chose it,
 * so it walks to the other. Each player stands in one place: a talk whose host is
 * already in a group adds the visitor to that group (up to three, and never to the
 * featured pair, whose words are shown); a talk whose players are both taken is
 * left out of the picture.
 */
export function talkGroups(
  talks: StageReport["rounds"][number]["talks"],
  active: string[],
  featured: Beat | null,
): Group[] {
  const conversations = talks.filter((t) => t.kind === "conversation");
  const pool = (conversations.length ? conversations : talks)
    .map((t) => (t.participants || []).filter((id) => active.includes(id)))
    .filter((ids) => ids.length >= 2);
  const featuredIds = featured?.focus ?? [];
  pool.sort((a, b) => {
    const fa = a.every((id) => featuredIds.includes(id)) ? 0 : 1;
    const fb = b.every((id) => featuredIds.includes(id)) ? 0 : 1;
    return fa - fb;
  });
  const groupOf = new Map<string, Group>();
  const groups: Group[] = [];
  for (const ids of pool) {
    const [visitor, host] = ids;
    if (groupOf.has(visitor)) continue;
    const there = groupOf.get(host);
    if (!there) {
      const g = { host, visitors: [visitor] };
      groups.push(g);
      groupOf.set(host, g);
      groupOf.set(visitor, g);
    } else if (there.visitors.length < 2 && !isFeatured(there)) {
      there.visitors.push(visitor);
      groupOf.set(visitor, there);
    }
  }
  return groups;

  function isFeatured(g: Group): boolean {
    return featuredIds.length > 0 && featuredIds.includes(g.host) && g.visitors.every((id) => featuredIds.includes(id));
  }
}

export function planFrames(plan: Segment[], fps: number): number {
  return plan.reduce((n, s) => n + Math.round(s.seconds * fps), 0);
}
