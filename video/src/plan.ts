import { buildBeats, StageReport } from "../../frontend/src/app/season-stage";

export type ClipProps = {
  report: StageReport;
  /** X handle credited on the end card, e.g. "@Danieldoesx" */
  handle?: string | null;
  /** public address of the site, e.g. "https://llmsurvivor.kurult.ai" */
  site?: string | null;
};

export type Beat = ReturnType<typeof buildBeats>[number];

/** Seconds to walk over to a talk, and back to the seats after it. */
export const WALK = 1.0;
export const RETURN = 0.8;

/**
 * How long a speech bubble stays up: a second to find it, then about 30
 * characters a second (a relaxed reading pace for text over moving pictures),
 * never under 3 seconds or over 7.5.
 */
export function readSeconds(text: string): number {
  return Math.min(7.5, Math.max(3, 1 + text.length / 30));
}

/** The most of a line that reads in one bubble: whole sentences up to `max`
 *  characters when they make a fair share of it, else cut at a word. */
export function bubbleText(text: string, max = 200): string {
  const t = (text || "").replace(/\s+/g, " ").trim();
  if (t.length <= max) return t;
  const head = t.slice(0, max);
  const end = Math.max(head.lastIndexOf(". "), head.lastIndexOf("! "), head.lastIndexOf("? "));
  if (end >= max * 0.45) return head.slice(0, end + 1);
  return head.slice(0, max - 1).replace(/\s+\S*$/, "") + "…";
}

/** One line of the featured talk, and how long it stays on screen. */
export type Line = { id: string; text: string; seconds: number };

/** A group talking in private: the `visitors` walk over to the `host`. */
export type Group = { host: string; visitors: string[] };

export type Segment =
  | { kind: "title"; seconds: number }
  | { kind: "camp"; round: number; out: string[]; groups: Group[]; talks: number; featured: Beat | null; spoken: Line[]; seconds: number }
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
    // the featured pair says its lines one after the other, each up long enough to read
    const ids = featured?.focus ?? [];
    const pair = groups.find((g) => g.visitors.length === 1 && ids.includes(g.host) && ids.includes(g.visitors[0]));
    // the stage keeps each speaker's fuller line as a quote; the bubble shows more of it than the site does
    const spoken: Line[] = pair
      ? (featured?.quotes ?? [])
          .filter((q) => q.who === pair.host || q.who === pair.visitors[0])
          .map((q) => ({ id: q.who, text: bubbleText(q.text) }))
          .map((l) => ({ ...l, seconds: readSeconds(l.text) }))
      : [];
    const talking = spoken.length ? spoken.reduce((n, l) => n + l.seconds, 0) : 3.2;
    if (groups.length) plan.push({ kind: "camp", round: r.round, out: [...out], groups, talks: talkCount, featured, spoken, seconds: WALK + talking + RETURN });

    const vote = inRound.find((b) => b.kind === "vote");
    if (vote) plan.push({ kind: "beat", beat: vote, seconds: 2.3 });
    if (r.round === keyRound) {
      const promise = inRound.find((b) => b.kind === "promises");
      if (promise) plan.push({ kind: "beat", beat: promise, seconds: 4 });
    }
    const outBeat = inRound.find((b) => b.kind === "out");
    if (outBeat) {
      const full = r.round === keyRound;
      // the round shown in full keeps the leaving player's last words, up long enough to read
      const words = full ? (outBeat.bubbles ?? []).map((b) => b.text).join(" ") : "";
      const seconds = full ? Math.max(3.4, words ? 0.6 + readSeconds(words) : 0) : 1.9;
      plan.push({ kind: "beat", beat: full ? outBeat : { ...outBeat, bubbles: [], quotes: [] }, seconds });
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
