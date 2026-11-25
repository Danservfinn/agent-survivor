"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { SeasonStage, StageReport } from "../season-stage";
import { friendlyDate, usd } from "../score-table";
import content from "./content.json";

/* ---------- the facts feed (GET /api/methodology) */

type ReplyField = { field: string; says: string; optional: boolean; needs: string | null };
type Change = { version: number; since: string; title: string; changes: string[] };
type Facts = {
  rules: { version: number; since: string; title: string; changes: Change[] };
  player_rules: string[];
  switches: Record<string, boolean | string>;
  roster: { name: string; players: number; models: { model: string; model_id: string }[]; aliases: string[] };
  challenge: { puzzles_per_round: number; tiers: string[]; levels: string[][]; rung_weights: number[]; first_level: number };
  strategy?: { every: number; first_round: number; highest_pick: { players: number; top: number }; top_per_player: number };
  idols: { gift_rounds: number; expires_at_players: number; search_from_round: number; hidden_chance: number };
  advantages: { spy_caught_chance: number; auction_tokens_per_round: number; auction_after_players_gone: number };
  talks: { per_round: number; notebook_max_chars: number };
  council: { host_questions: number };
  reply_fields: Record<string, ReplyField[]>;
  reply_common: { first: string[]; last: string[] };
  fairness: {
    reasoning_effort: string;
    reasoning_hidden: boolean;
    reasoning_headroom_tokens: number;
    provider_sort: string;
    precisions: string[];
    timeout_s: number;
    outage_hold_s: number;
    context_max_chars: number;
  };
  host_model: string;
  strategy_judges: string[];
  scoring: { index_version: number; weights: Record<string, number>; character_min_evidence: number };
  cost: { cap_cents: number; bill_factor: number };
  glossary: { term: string; text: string }[];
  sections: Record<string, { title: string; describes: string[]; fresh: boolean }>;
  reference: { season_id: string; pinned: boolean; rules_version: number | null; current_rules: boolean; prompt_examples: string[] } | null;
};

/* ---------- the reference season (GET /api/live-season/report/<id>) */

type Model = {
  agent_id: string;
  model: string;
  alias?: string | null;
  survivor_index: number | null;
  place?: number | null;
  components?: Record<string, number>;
  points?: Record<string, number>;
};
type Report = Omit<StageReport, "season" | "models" | "rounds"> & {
  season: StageReport["season"] & { weights?: Record<string, number>; index_version?: number };
  models: (StageReport["models"][number] & Model)[];
  rounds: (StageReport["rounds"][number] & { plans?: { agent_id: string; plan: string }[] })[];
};

type Step = {
  id: string;
  title: string;
  who: string;
  pin: string;
  family?: string;
  replyKind?: string;
  quote?: string;
  what: string[];
  rules?: string[];
  exact?: string;
};

/* every part an index version has used; a season shows the ones its weights name */
const ALL_PARTS: [string, string][] = [
  ["challenge_accuracy", "Challenge"],
  ["vote_participation", "Voting"],
  ["strategic_score", "Strategy"],
  ["placement", "Placement"],
  ["social", "Social"],
];
const NUMBER_WORDS = ["no", "one", "two", "three", "four", "five", "six"];
function partsFor(weights: Record<string, number> | undefined): [string, string][] {
  return ALL_PARTS.filter(([k]) => weights && weights[k] != null);
}

const TOC: [string, string][] = [
  ["minute", "The test in one minute"],
  ["round", "A round, step by step"],
  ["finale", "The finale"],
  ["score", "How the score is built"],
  ["fairness", "Fairness controls"],
  ["limits", "What this does and doesn't show"],
  ["check", "Check it yourself"],
  ["glossary", "Glossary"],
  ["changes", "Rule changes"],
];

const WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen"];
function word(n: number): string {
  return WORDS[n] ?? String(n);
}

function longDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("en-US", { day: "numeric", month: "long", year: "numeric" });
}

function nameOf(report: Report | null, id: string | null | undefined): string {
  if (!report || !id) return "";
  return report.names?.[id] ?? id;
}

/** The round the examples come from: the first with a broken promise, else round 1. */
function exampleRound(report: Report | null) {
  if (!report?.rounds?.length) return null;
  return report.rounds.find((r) => r.promises?.some((p) => p.status === "broken")) ?? report.rounds[0];
}

/** One real moment from the reference season for a step, or null. */
function quoteFor(kind: string | undefined, report: Report | null): { who: string; text: string; note?: string } | null {
  const round = exampleRound(report);
  if (!kind || !report || !round) return null;
  const n = (id: string | null | undefined) => nameOf(report, id);
  switch (kind) {
    case "challenge": {
      const rows = [...(round.challenge?.standings ?? [])].sort((a, b) => (b.correct ?? 0) - (a.correct ?? 0));
      if (!rows.length) return null;
      const line = rows.map((r) => `${n(r.agent_id)} ${r.correct ?? 0}/${r.of}`).join(" · ");
      return { who: `Round ${round.round} scores`, text: line };
    }
    case "plan": {
      const p = round.plans?.[0];
      return p ? { who: `${n(p.agent_id)}, private plan, round ${round.round}`, text: p.plan } : null;
    }
    case "talk": {
      const t = round.talks?.find((x) => x.lines?.length);
      const line = t?.lines.find((l) => l.agent_id && l.text);
      return line ? { who: `${n(line.agent_id)}, in a private talk with ${t!.participants.filter((p) => p !== line.agent_id).map(n).join(", ")}`, text: line.text } : null;
    }
    case "promise": {
      const p = round.promises?.find((x) => x.status === "broken") ?? round.promises?.[0];
      if (!p) return null;
      const kept = p.status === "kept";
      return {
        who: `Round ${round.round}, from the record`,
        text: `${n(p.by_id)} promised ${n(p.to_id)} a vote for ${n(p.target_id)}, then voted for ${p.actual_id ? n(p.actual_id) : "nobody"}.`,
        note: kept ? "Kept." : "Broken.",
      };
    }
    case "vote": {
      const v = round.votes?.find((x) => x.reason);
      return v ? { who: `${n(v.voter_id)}'s vote for ${n(v.target_id)}, round ${round.round}`, text: v.reason } : null;
    }
    case "exit": {
      const r = report.rounds.find((x) => x.exit_words);
      return r ? { who: `${n(r.eliminated_id)}, voted out in round ${r.round}`, text: r.exit_words } : null;
    }
    case "pitch": {
      const p = report.finale?.pitches?.[0];
      return p ? { who: `${n(p.agent_id)}'s pitch`, text: p.text } : null;
    }
    case "jury": {
      const j = report.finale?.jury?.find((x) => x.reason);
      return j ? { who: `${n(j.juror_id)}, juror, voting for ${n(j.finalist_id)}`, text: j.reason } : null;
    }
  }
  return null;
}

/* ---------- small pieces */

function Stale({ facts, pin }: { facts: Facts | null; pin: string }) {
  const s = facts?.sections?.[pin];
  if (!s || s.fresh) return null;
  return (
    <p className="meth-stale" role="note">
      The game changed after this part was written. It is being re-checked against the code; the numbers on this page
      are current, but the description may lag.
    </p>
  );
}

function Rules({ facts, phrases }: { facts: Facts | null; phrases?: string[] }) {
  if (!facts || !phrases?.length) return null;
  const found = phrases.map((ph) => facts.player_rules.find((r) => r.includes(ph))).filter((r): r is string => !!r);
  const unique = Array.from(new Set(found));
  if (!unique.length) return null;
  return (
    <details className="more meth-fold">
      <summary>The exact rule players are given</summary>
      {unique.map((r) => (
        <blockquote key={r} className="meth-rule">
          {r}
        </blockquote>
      ))}
    </details>
  );
}

function Builders({ facts, kind }: { facts: Facts | null; kind?: string }) {
  if (!facts || !kind || !facts.reply_fields[kind]) return null;
  const fields = facts.reply_fields[kind];
  const common = kind === "auction" ? null : facts.reply_common;
  return (
    <details className="more meth-fold">
      <summary>For builders: the reply</summary>
      <p className="card-sub">
        One JSON object.{" "}
        {common ? (
          <>
            It opens with <code>{common.first.join(", ")}</code> and closes with <code>{common.last.join(", ")}</code>;
            these are this step&apos;s own fields, in order:
          </>
        ) : (
          "Its fields:"
        )}
      </p>
      <div className="bench-table-wrap">
        <table className="bench-table meth-fields">
          <thead>
            <tr>
              <th>field</th>
              <th>what the player is told</th>
            </tr>
          </thead>
          <tbody>
            {fields.map((f) => (
              <tr key={f.field}>
                <td>
                  <code>{f.field}</code>
                  {f.optional ? <span className="meth-opt">optional</span> : null}
                </td>
                <td>{f.says}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="card-sub">
        A reply that cannot be used after its retries costs only that player: a silent line, a spoiled vote, no pitch
        or an abstaining juror. Optional fields that are malformed are ignored. Each call may take up to{" "}
        {Math.round(facts.fairness.timeout_s / 60)} minutes.
      </p>
    </details>
  );
}

type PromptExample = {
  model: string;
  player: string | null;
  step: string | null;
  messages: { role: string; content: string }[];
  reply: Record<string, unknown> | null;
  model_ms?: number | null;
};

function PromptFold({ family, facts, label = "The prompt and a real reply" }: { family?: string; facts: Facts | null; label?: string }) {
  const [example, setExample] = useState<PromptExample | null | "none">(null);
  if (!family) return null;
  const available = facts?.reference?.prompt_examples?.includes(family);
  const load = (open: boolean) => {
    if (!open || example !== null || !available) return;
    fetch(`/api/methodology/prompt/${family}`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => setExample(data ?? "none"))
      .catch(() => setExample("none"));
  };
  return (
    <details className="more meth-fold" onToggle={(e) => load((e.currentTarget as HTMLDetailsElement).open)}>
      <summary>{label}</summary>
      {!available ? (
        <p className="card-sub">
          Prompts are recorded from the first season played after 25 November 2026. The reference season below this
          page&apos;s examples predates that, so this step&apos;s exact prompt will appear here once a recorded season
          is pinned.
        </p>
      ) : example === null ? (
        <p className="card-sub">Loading…</p>
      ) : example === "none" ? (
        <p className="card-sub">No recorded example of this step yet.</p>
      ) : (
        <div className="meth-prompt">
          <p className="card-sub">
            Sent to <b>{example.model}</b>
            {example.player ? (
              <>
                {" "}
                playing as <b>{example.player}</b>
              </>
            ) : null}
            . The model saw only this text. Its private <code>think</code> field is removed from the reply, as players
            are told it is never kept.
          </p>
          {example.messages.map((m, i) => (
            <div key={i}>
              <p className="meth-role">{m.role}</p>
              <pre className="meth-pre">{m.content}</pre>
            </div>
          ))}
          <p className="meth-role">reply</p>
          <pre className="meth-pre">{JSON.stringify(example.reply, null, 2)}</pre>
        </div>
      )}
    </details>
  );
}

function StepCard({ step, facts, report, n }: { step: Step; facts: Facts | null; report: Report | null; n: number }) {
  const quote = quoteFor(step.quote, report);
  return (
    <li className="card meth-step" id={`step-${step.id}`}>
      <div className="meth-step-head">
        <span className="step-n">{n}</span>
        <h3>{step.title}</h3>
        <span className={`meth-who who-${step.who}`}>{content.who[step.who as keyof typeof content.who] ?? step.who}</span>
      </div>
      <Stale facts={facts} pin={step.pin} />
      {step.what.map((p, i) => (
        <p key={i}>{p}</p>
      ))}
      {step.exact === "challenge" && facts ? <ChallengeFacts facts={facts} /> : null}
      {quote ? (
        <figure className="meth-quote">
          <blockquote>{quote.text}</blockquote>
          <figcaption>
            {quote.who}
            {quote.note ? <b> {quote.note}</b> : null}
          </figcaption>
        </figure>
      ) : null}
      <Rules facts={facts} phrases={step.rules} />
      <PromptFold family={step.family} facts={facts} label={step.id === "challenge" ? "The puzzle prompt and a real reply" : undefined} />
      {step.id === "challenge" ? <PromptFold family="strategy_pick" facts={facts} label="The strategy-game prompt and a real pick" /> : null}
      <Builders facts={facts} kind={step.replyKind} />
    </li>
  );
}

function ChallengeFacts({ facts }: { facts: Facts }) {
  const c = facts.challenge;
  return (
    <details className="more meth-fold">
      <summary>The puzzle ladder and the strategy game, in numbers</summary>
      <p className="card-sub">
        Four levels; each round&apos;s level moves up one when any player solved every puzzle the round before, and
        down one when nobody reached three. The public season starts at level {c.first_level}. Harder puzzles count
        more in a tie: puzzle 1 to 5 weigh {c.rung_weights.join(", ")}.
      </p>
      <div className="bench-table-wrap">
        <table className="bench-table">
          <thead>
            <tr>
              <th>level</th>
              {c.levels[0].map((_, i) => (
                <th key={i}>puzzle {i + 1}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {c.levels.map((row, i) => (
              <tr key={i} className={i === c.first_level ? "bench-selected" : undefined}>
                <td>{i}</td>
                {row.map((tier, j) => (
                  <td key={j}>{tier}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="card-sub">
        Grid puzzles infer a transformation from examples; &quot;extreme&quot; grids hide a rule that two examples
        reveal. &quot;Master&quot; puzzles are six-peg Mastermind, knights and knaves with twelve speakers, and
        counting the orderings of nine people under seven rules; each answer is checked unique by brute force.
      </p>
      {facts.strategy && facts.switches.strategy_challenges !== false ? (
        <p className="card-sub">
          The strategy game comes every {facts.strategy.every === 2 ? "second" : `${facts.strategy.every}th`} round,
          starting with round {facts.strategy.first_round}; the ladder&apos;s level carries over it unchanged. With{" "}
          {facts.strategy.highest_pick.players} players the picks run from 1 to {facts.strategy.highest_pick.top}. The
          picks are sealed: each player makes its pick in a private call, all at once, and none sees another&apos;s
          until the result.
        </p>
      ) : null}
    </details>
  );
}

/* ---------- who sees what */

const SEES: [string, string, string, string, string][] = [
  // what, the player itself, other players, the jury at the finale, viewers
  ["Words of a private talk", "if in it", "no (unless spying)", "no", "yes"],
  ["Who met whom", "yes", "yes", "yes", "yes"],
  ["Its own vote", "yes", "no (unless shown)", "yes", "yes"],
  ["The tally", "yes", "yes", "yes", "yes"],
  ["Who voted for whom", "no", "no", "yes", "yes"],
  ["Plans, notebook, feelings", "yes", "no", "no", "yes"],
  ["Its lie labels", "yes", "no", "no", "yes"],
  ["Who holds an idol", "if holder or giver", "no (unless shown)", "yes", "yes"],
  ["Which model anyone is", "no", "no", "no", "yes"],
  ["Its private reasoning", "never kept", "no", "no", "no"],
];

function SeesTable() {
  const mark = (v: string) => (v === "yes" ? <span className="meth-yes">●</span> : v === "no" ? <span className="meth-no">○</span> : <span className="meth-some">{v}</span>);
  return (
    <div className="bench-table-wrap">
      <table className="bench-table meth-sees">
        <thead>
          <tr>
            <th>what</th>
            <th>the player</th>
            <th>other players</th>
            <th>the jury, at the end</th>
            <th>viewers</th>
          </tr>
        </thead>
        <tbody>
          {SEES.map(([what, ...cells]) => (
            <tr key={what}>
              <td>{what}</td>
              {cells.map((c, i) => (
                <td key={i}>{mark(c)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ---------- scoring calculator */

function Calculator({ facts, report }: { facts: Facts; report: Report | null }) {
  const models = useMemo(() => [...(report?.models ?? [])].filter((m) => m.components).sort((a, b) => (b.survivor_index ?? 0) - (a.survivor_index ?? 0)), [report]);
  const [pick, setPick] = useState<string | null>(null);
  const chosen = models.find((m) => m.agent_id === pick) ?? models[0];
  const weights = facts.scoring.weights;
  if (!chosen?.components) {
    return <p className="card-sub">The calculator needs a finished season.</p>;
  }
  const rows = partsFor(weights).map(([key, label]) => {
    const value = chosen.components?.[key] ?? 0;
    return { key, label, value, weight: weights[key] ?? 0, points: 100 * (weights[key] ?? 0) * value };
  });
  const total = rows.reduce((s, r) => s + r.points, 0);
  return (
    <div className="card card-pad meth-calc">
      <label className="meth-pick">
        <span>Player</span>
        <select value={chosen.agent_id} onChange={(e) => setPick(e.target.value)}>
          {models.map((m) => (
            <option key={m.agent_id} value={m.agent_id}>
              {m.model}
              {m.alias ? ` (${m.alias})` : ""}
            </option>
          ))}
        </select>
      </label>
      <div className="bench-table-wrap">
        <table className="bench-table">
          <thead>
            <tr>
              <th>part</th>
              <th className="num">its value</th>
              <th className="num">× weight</th>
              <th className="num">= points</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key}>
                <td>{r.label}</td>
                <td className="num">{r.value.toFixed(3)}</td>
                <td className="num">{Math.round(r.weight * 100)}</td>
                <td className="num">{r.points.toFixed(2)}</td>
              </tr>
            ))}
            <tr className="meth-total">
              <td>Survivor Index</td>
              <td />
              <td />
              <td className="num">{total.toFixed(2)}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p className="foot">
        {"// the season report gives "}
        {chosen.model} {chosen.survivor_index?.toFixed(2) ?? "–"}
        {chosen.survivor_index != null && Math.abs(chosen.survivor_index - total) > 0.05 ? " (the report was scored with another index version)" : ""}
      </p>
    </div>
  );
}

/* ---------- the page */

export default function MethodologyPage() {
  const [facts, setFacts] = useState<Facts | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [failed, setFailed] = useState(false);
  const [allOpen, setAllOpen] = useState(false);
  const body = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch("/api/methodology")
      .then((res) => (res.ok ? res.json() : Promise.reject()))
      .then((data: Facts) => {
        setFacts(data);
        if (data.reference) {
          fetch(`/api/live-season/report/${encodeURIComponent(data.reference.season_id)}`)
            .then((res) => (res.ok ? res.json() : null))
            .then((r) => r?.ok && setReport(r))
            .catch(() => {});
        }
      })
      .catch(() => setFailed(true));
  }, []);

  const toggleAll = () => {
    const next = !allOpen;
    body.current?.querySelectorAll("details").forEach((d) => {
      d.open = next;
      d.dispatchEvent(new Event("toggle"));
    });
    setAllOpen(next);
  };

  const ref = facts?.reference ?? null;
  const w = facts?.scoring.weights;

  return (
    <main className="meth">
      <header className="hero hero-slim">
        <div className="wrap">
          <p className="kicker">Methodology</p>
          <h1>How the models are tested.</h1>
          <p className="lead">
            Agent Survivor puts AI models in a game of alliances, deals and votes, and records everything they do. This
            page describes every step of the game: what each model is asked, what it can see, how its moves are
            checked and how its score is built. Every number on it is read from the game&apos;s code.
          </p>
          {facts ? (
            <div className="tags">
              <span className="tag on">rules version {facts.rules.version}</span>
              <span className="tag">since {longDate(facts.rules.since)}</span>
              <span className="tag">Survivor Index v{facts.scoring.index_version}</span>
              <span className="tag">{facts.roster.players} players</span>
            </div>
          ) : failed ? (
            <p className="bench-error">The rules could not be loaded. Try again in a moment.</p>
          ) : null}
          {ref && !ref.current_rules ? (
            <p className="meth-banner">
              The examples on this page come from the season of {friendlyDate(ref.season_id)}, played under rules
              version {ref.rules_version ?? "?"}. Several steps described here did not exist yet, so they show no
              example. A season played under the current rules will replace it.
            </p>
          ) : null}
        </div>
      </header>

      <div className="wrap meth-grid">
        <nav className="meth-toc" aria-label="On this page">
          <p className="eyebrow">On this page</p>
          <ol>
            {TOC.map(([id, label]) => (
              <li key={id}>
                <a href={`#${id}`}>{label}</a>
              </li>
            ))}
          </ol>
          <button type="button" className="chip" onClick={toggleAll}>
            {allOpen ? "Close all details" : "Open all details"}
          </button>
        </nav>

        <div className="meth-body" ref={body}>
          <section className="sec meth-sec" id="minute">
            <p className="eyebrow">The test in one minute</p>
            <h2 className="big">
              {facts ? `${word(facts.roster.players)[0].toUpperCase()}${word(facts.roster.players).slice(1)} models` : "Frontier models"}, one game, no
              script.
            </h2>
            <ul className="meth-facts">
              <li>
                <b>{facts?.roster.players ?? 9} models from different labs</b> play one season together, as one group.
                Each round one player is voted out.
              </li>
              <li>
                <b>Identities are hidden.</b> Players go by tree names such as {facts?.roster.aliases.slice(0, 3).join(", ") ?? "Ash, Birch, Cedar"}
                , redrawn every season. No prompt ever contains a model&apos;s name.
              </li>
              <li>
                <b>Every word is a live model call.</b> Nothing a player says or does is written by anyone else; a
                model that fails to answer stays silent.
              </li>
              <li>
                <b>Moves are checked against the record.</b> Promises are compared with the votes cast, puzzle answers
                are graded by code, and the jury reads a record taken from the game&apos;s tables.
              </li>
              <li>
                <b>A jury of the players voted out picks the winner</b> from the final three.
              </li>
            </ul>
            <h3>Who sees what</h3>
            <p className="card-sub">Ballots are secret, talks are private, and each player keeps records only it can read.</p>
            <SeesTable />
            {facts ? (
              <details className="more meth-fold">
                <summary>All {facts.player_rules.length} rules every player is given</summary>
                <ol className="meth-rules">
                  {facts.player_rules.map((r) => (
                    <li key={r}>{r}</li>
                  ))}
                </ol>
              </details>
            ) : null}
          </section>

          <section className="sec meth-sec" id="round">
            <p className="eyebrow">A round, step by step</p>
            <h2 className="big">{content.round.length} steps, in the order the game runs them.</h2>
            <Stale facts={facts} pin="round" />
            <p className="card-sub">
              Each card says what happens, who sees it, and a real moment from the reference season. Open the details
              for the exact rule the players are given, the full prompt and a real reply, and the reply format for
              anyone building an agent.
            </p>
            {report ? (
              <div className="meth-stage">
                <SeasonStage report={report} />
              </div>
            ) : null}
            <ol className="meth-steps">
              {(content.round as Step[]).map((s, i) => (
                <StepCard key={s.id} step={s} facts={facts} report={report} n={i + 1} />
              ))}
            </ol>
          </section>

          <section className="sec meth-sec" id="finale">
            <p className="eyebrow">The finale</p>
            <h2 className="big">When three remain, the voted-out decide.</h2>
            <Stale facts={facts} pin="finale" />
            <p className="card-sub">
              Every player voted out sits on the jury
              {facts ? `, so a ${word(facts.roster.players)}-player season has ${word(facts.roster.players - 3)} jurors` : ""}.
            </p>
            <ol className="meth-steps">
              {(content.finale as Step[]).map((s, i) => (
                <StepCard key={s.id} step={s} facts={facts} report={report} n={i + 1} />
              ))}
            </ol>
            <Rules facts={facts} phrases={["Jurors are told", "Jurors receive", "When three players remain"]} />
          </section>

          <section className="sec meth-sec" id="score">
            <p className="eyebrow">How the score is built</p>
            <h2 className="big">The jury picks the winner. The index scores everyone.</h2>
            <Stale facts={facts} pin="scoring" />
            {facts && w ? (
              <>
                <p>
                  The Survivor Index (version {facts.scoring.index_version}) scores each model&apos;s whole season from 0
                  to 100, including models voted out early. It adds {NUMBER_WORDS[partsFor(w).length] ?? partsFor(w).length} parts, each a share between 0 and 1 times its
                  weight.{w.placement == null ? " Final place is reported beside it, not counted in it." : ""}
                </p>
                <p className="meth-formula">
                  {partsFor(w).map(([k, label], i) => (
                    <span key={k}>
                      {i ? " + " : ""}
                      {Math.round(w[k] * 100)} × {label.toLowerCase()}
                    </span>
                  ))}
                </p>
                <ul className="terms">
                  {facts.glossary
                    .filter((g) => partsFor(w).some(([, label]) => label === g.term) || g.term === "Place")
                    .map((g) => (
                      <li key={g.term}>
                        <b>{g.term}</b>
                        <span>{g.text}</span>
                      </li>
                    ))}
                </ul>
                <h3>Try it on a real player</h3>
                <Calculator facts={facts} report={report} />
                <h3>Reported beside the index, never inside it</h3>
                <p>
                  Vote predictions, promises kept, a deception index (lies, broken promises, betrayals, backstabs, public
                  bluffs) and an emotionality index are computed from the record; each character index is shown only
                  with at least {facts.scoring.character_min_evidence} events behind it. A sophistication score comes
                  from judge models reading every player&apos;s written strategy under its game name only:{" "}
                  {facts.strategy_judges.join(" and ")}. It is a model opinion, not a measurement, and a judge&apos;s
                  score of a player from its own lab is shown but not counted.
                </p>
              </>
            ) : null}
          </section>

          <section className="sec meth-sec" id="fairness">
            <p className="eyebrow">Fairness controls</p>
            <h2 className="big">The same game for every model.</h2>
            <Stale facts={facts} pin="fairness" />
            {facts ? (
              <ul className="terms">
                <li>
                  <b>Same prompt</b>
                  <span>
                    Every player gets the same prompt template and the same rules; only its own situation differs. An
                    entered agent&apos;s prompts also carry its owner&apos;s private playbook, placed after the game
                    record and before the step&apos;s task, and marked as advice that the game rules always override;
                    house models never get one, so an agent&apos;s results measure its model and playbook together. A
                    test checks that no prompt contains any model&apos;s name or id.
                  </span>
                </li>
                <li>
                  <b>Same thinking</b>
                  <span>
                    Every call asks for {facts.fairness.reasoning_effort} reasoning effort, with{" "}
                    {facts.fairness.reasoning_headroom_tokens.toLocaleString()} extra tokens of room for it, and the
                    reasoning stays hidden. Each model&apos;s room is capped only by its own output limit.
                  </span>
                </li>
                <li>
                  <b>Same serving</b>
                  <span>
                    Calls go through OpenRouter to each model&apos;s fastest providers ({facts.fairness.provider_sort}
                    ), at full or 8-bit precision only ({facts.fairness.precisions.filter((p) => p !== "unknown").join(", ")}
                    ), because speed breaks puzzle ties and lower-precision versions can reason worse.
                  </span>
                </li>
                <li>
                  <b>A model&apos;s failure is its own</b>
                  <span>
                    A reply that is still unusable after its retries costs only that player: a silent line, a spoiled
                    vote, no pitch or an abstaining juror. Nothing is ever written in its place.
                  </span>
                </li>
                <li>
                  <b>An outage is nobody&apos;s</b>
                  <span>
                    If a provider is down, the call waits up to {Math.round(facts.fairness.outage_hold_s / 60)} minutes.
                    Past that the season stops, is marked interrupted and is not reported as a result.
                  </span>
                </li>
                <li>
                  <b>Memory has the same limit</b>
                  <span>
                    Each prompt holds up to {facts.fairness.context_max_chars.toLocaleString()} characters of game
                    context. When it is full the oldest timeline goes first; the rules, a player&apos;s own notebook and
                    the verified ledgers are never cut.
                  </span>
                </li>
                <li>
                  <b>Parallel only when blind</b>
                  <span>
                    Calls that cannot see each other run at the same time; a test plays a whole season both ways and
                    checks every call and table match.
                  </span>
                </li>
                <li>
                  <b>Seeded chance</b>
                  <span>
                    Puzzles, tree names, seats, the order votes are read and speeches given, hidden idols, caught spies
                    and the rock draw all follow the season&apos;s seed. Model sampling and provider speed do not.
                  </span>
                </li>
                <li>
                  <b>The host</b>
                  <span>
                    The host is {facts.host_model}. It narrates and questions up to {facts.council.host_questions} players
                    per council from the public record only, and never votes.
                  </span>
                </li>
              </ul>
            ) : null}
          </section>

          <section className="sec meth-sec" id="limits">
            <p className="eyebrow">What this does and doesn&apos;t show</p>
            <h2 className="big">Read the results with these in mind.</h2>
            <ul className="terms">
              {content.limits.map(([head, text]) => (
                <li key={head}>
                  <b>{head}</b>
                  <span>{text}</span>
                </li>
              ))}
            </ul>
          </section>

          <section className="sec meth-sec" id="check">
            <p className="eyebrow">Check it yourself</p>
            <h2 className="big">The full record of the reference season.</h2>
            {ref ? (
              <ul className="meth-links">
                <li>
                  <a href={`/report/${ref.season_id}`}>The season report</a>: every vote with its reason, every promise,
                  the finale and the scores.
                </li>
                <li>
                  <a href={`/watch?season=${encodeURIComponent(ref.season_id)}`}>The replay</a>, round by round.
                </li>
                <li>
                  <a href={`/report/${ref.season_id}/data/report.json`}>report.json</a>: everything the report shows, as
                  data.
                </li>
                <li>
                  <a href={`/report/${ref.season_id}/data/season.db`}>season.db</a>: the season&apos;s SQLite database,
                  with every table the game wrote, including private plans, notebooks and feelings.
                </li>
                {ref.prompt_examples.length ? (
                  <li>
                    <a href={`/report/${ref.season_id}/data/prompts.jsonl.gz`}>prompts.jsonl.gz</a>: the exact prompt
                    and reply of every model call (private reasoning removed).
                  </li>
                ) : (
                  <li>The prompt log starts with the next season; this one predates it.</li>
                )}
              </ul>
            ) : (
              <p className="card-sub">No finished season yet.</p>
            )}
            {facts ? (
              <p className="foot">
                {"// a public season is capped at "}
                {usd(facts.cost.cap_cents)} of model spend · roster {facts.roster.name}:{" "}
                {facts.roster.models.map((m) => m.model).join(", ")}
              </p>
            ) : null}
          </section>

          <section className="sec meth-sec" id="glossary">
            <p className="eyebrow">Glossary</p>
            <h2 className="big">Every number, defined.</h2>
            {facts ? (
              <ul className="terms">
                {facts.glossary.map((g) => (
                  <li key={g.term}>
                    <b>{g.term}</b>
                    <span>{g.text}</span>
                  </li>
                ))}
              </ul>
            ) : null}
          </section>

          <section className="sec meth-sec" id="changes">
            <p className="eyebrow">Rule changes</p>
            <h2 className="big">Every version of the rules.</h2>
            <p className="card-sub">
              A season is comparable only with seasons played under the same rules. Each season&apos;s report names the
              version it was played under.
            </p>
            {facts ? (
              <ol className="meth-changes">
                {facts.rules.changes.map((c) => (
                  <li key={c.version} id={`rules-${c.version}`}>
                    <p className="meth-change-h">
                      <b>Version {c.version}</b> · {longDate(c.since)} · {c.title}
                    </p>
                    <ul>
                      {c.changes.map((line) => (
                        <li key={line}>{line}</li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ol>
            ) : null}
          </section>
        </div>
      </div>
    </main>
  );
}
