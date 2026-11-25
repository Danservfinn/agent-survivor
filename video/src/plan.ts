import { buildBeats, StageReport } from "../../frontend/src/app/season-stage";

export type ClipProps = {
  report: StageReport;
  /** X handle credited on the end card, e.g. "@Danieldoesx" */
  handle?: string | null;
  /** public address of the site, e.g. "https://llmsurvivor.kurult.ai" */
  site?: string | null;
};

type Beat = ReturnType<typeof buildBeats>[number];

export type Segment =
  | { kind: "title"; seconds: number }
  | { kind: "beat"; beat: Beat; seconds: number }
  | { kind: "end"; beat: Beat | null; seconds: number };

/**
 * The clip is the season's highlights, not every step: the opening, one round's
 * betrayal in full (the private talk, the vote, the broken promise, who left),
 * every other vote-out in quick succession, the jury and the verdict.
 */
export function clipPlan(report: StageReport): Segment[] {
  const beats = buildBeats(report);
  const plan: Segment[] = [{ kind: "title", seconds: 3 }];

  // the round to show in full: the first with a broken promise, else the first with a talk
  const keyRound =
    beats.find((b) => b.kind === "promises")?.round ?? beats.find((b) => b.kind === "talk")?.round ?? null;
  const promise = beats.find((b) => b.kind === "promises" && b.round === keyRound);
  const traitor = promise?.focus?.[0];

  for (const round of report.rounds.map((r) => r.round)) {
    const inRound = beats.filter((b) => b.round === round);
    if (round === keyRound) {
      const talks = inRound.filter((b) => b.kind === "talk");
      const talk = talks.find((b) => traitor && b.focus?.includes(traitor)) ?? talks[0];
      if (talk) plan.push({ kind: "beat", beat: talk, seconds: 5 });
      const vote = inRound.find((b) => b.kind === "vote");
      if (vote) plan.push({ kind: "beat", beat: vote, seconds: 3.4 });
      if (promise) plan.push({ kind: "beat", beat: promise, seconds: 4.2 });
      const out = inRound.find((b) => b.kind === "out");
      if (out) plan.push({ kind: "beat", beat: out, seconds: 3.4 });
    } else {
      const out = inRound.find((b) => b.kind === "out");
      if (out) plan.push({ kind: "beat", beat: { ...out, bubbles: [], quotes: [] }, seconds: 1.7 });
    }
  }

  const jury = beats.find((b) => b.kind === "jury");
  const verdict = beats.find((b) => b.kind === "verdict");
  if (jury) plan.push({ kind: "beat", beat: jury, seconds: 2.8 });
  if (verdict) plan.push({ kind: "beat", beat: verdict, seconds: 4.6 });
  plan.push({ kind: "end", beat: verdict ?? beats[beats.length - 1] ?? null, seconds: 4 });
  return plan;
}

export function planFrames(plan: Segment[], fps: number): number {
  return plan.reduce((n, s) => n + Math.round(s.seconds * fps), 0);
}
