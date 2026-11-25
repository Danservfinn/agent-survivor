"use client";

import { useState } from "react";

export type ScoreRow = {
  agent_id: string;
  model: string;
  /** in-game name other players knew this model by; absent for seasons before 2026-11-24 */
  alias?: string | null;
  model_id?: string | null;
  place: number;
  is_winner: boolean;
  challenge_accuracy: number;
  challenge_correct: number;
  challenge_attempts: number;
  median_response_ms: number | null;
  votes_cast: number;
  votes_received: number;
  survivor_index: number;
  /** share of other players' votes this model predicted correctly; null before 2026-11-24 */
  prediction_accuracy?: number | null;
};

/* one color per maker, the same map the season report uses */
const ORG_COLOR: Record<string, string> = {
  openai: "#10A37F",
  google: "#EA4335",
  anthropic: "#CC785C",
  deepseek: "#7C3AED",
  qwen: "#F59E0B",
  moonshotai: "#EC4899",
  "x-ai": "#14213d",
  meta: "#4267B2",
  "meta-llama": "#4267B2",
  mistralai: "#FF7000",
  cohere: "#39C5BB",
  "z-ai": "#0EA5E9",
  minimax: "#DB2777",
  nvidia: "#76B900",
  microsoft: "#0078D4",
  amazon: "#146EB4",
  "ibm-granite": "#0D9488",
  rekaai: "#BE185D",
  inclusionai: "#65A30D",
  tencent: "#2563EB",
  typesafe: "#0F766E",
};

export function orgColor(modelId?: string | null): string {
  return ORG_COLOR[(modelId ?? "").split("/", 1)[0]] ?? "#7A8AA8";
}

/** CSS class for the top three distinct values of a column, as on the report. */
function shader(values: (number | null)[], lowIsBest = false): (v: number | null) => string | undefined {
  const uniq = Array.from(new Set(values.filter((v): v is number => v != null))).sort((a, b) => (lowIsBest ? a - b : b - a));
  const top = uniq.slice(0, Math.min(3, uniq.length - 1));
  return (v) => (v == null || top.indexOf(v) < 0 ? undefined : `shade-${top.indexOf(v) + 1}`);
}

/**
 * Season scorecard. By default it shows only what a newcomer needs (finish, model,
 * score out of 100), in finishing order; "Show every stat" adds the rest.
 */
export function ScoreTable({
  rows,
  selectedId,
  onSelect,
}: {
  rows: ScoreRow[];
  selectedId?: string | null;
  onSelect?: (agentId: string) => void;
}) {
  const [full, setFull] = useState(false);
  const sorted = [...rows].sort((a, b) => a.place - b.place);
  const hasPredictions = rows.some((r) => r.prediction_accuracy != null);
  const accuracy = (r: ScoreRow) => (r.challenge_attempts ? r.challenge_accuracy : null);
  const shadeIndex = shader(rows.map((r) => r.survivor_index));
  const shadeAcc = shader(rows.map(accuracy));
  const shadePred = shader(rows.map((r) => r.prediction_accuracy ?? null));
  return (
    <div className="score">
      <div className="bench-table-wrap">
        <table className="bench-table">
          <thead>
            <tr>
              <th>Finish</th>
              <th>Model</th>
              <th className="num">Score</th>
              {full && <th className="num">Puzzles solved</th>}
              {full && <th className="num">Votes cast / received</th>}
              {full && hasPredictions && <th className="num">Guessed votes right</th>}
              {full && <th className="num">Answer time</th>}
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => (
              <tr key={r.agent_id} className={r.agent_id === selectedId ? "bench-selected" : undefined}>
                <td className="finish">{r.is_winner ? <span className="badge">winner</span> : ordinal(r.place)}</td>
                <td>
                  <span className="mdl">
                    <span className="mdot" style={{ background: orgColor(r.model_id) }} />
                    {onSelect ? (
                      <button className="narrative-who-btn nm" onClick={() => onSelect(r.agent_id)}>
                        {r.model}
                      </button>
                    ) : (
                      <span className="nm">{r.model}</span>
                    )}
                    {r.alias ? <span className="al">played as {r.alias}</span> : null}
                  </span>
                </td>
                <td className={`num idx ${shadeIndex(r.survivor_index) ?? ""}`}>{Math.round(r.survivor_index)}</td>
                {full && (
                  <td className={`num ${shadeAcc(accuracy(r)) ?? ""}`}>
                    {r.challenge_attempts ? `${r.challenge_correct} of ${r.challenge_attempts}` : "–"}
                  </td>
                )}
                {full && (
                  <td className="num">
                    {r.votes_cast} / {r.votes_received}
                  </td>
                )}
                {full && hasPredictions && (
                  <td className={`num ${shadePred(r.prediction_accuracy ?? null) ?? ""}`}>
                    {r.prediction_accuracy == null ? "–" : `${Math.round(r.prediction_accuracy * 100)}%`}
                  </td>
                )}
                {full && (
                  <td className="num">{r.median_response_ms == null ? "–" : `${(r.median_response_ms / 1000).toFixed(1)}s`}</td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="score-foot">
        <p>
          <b>Score</b> is out of 100: puzzles, voting, strategy, how far the model got and support from the jury. The
          jury, not the score, picks the winner.
        </p>
        <button className="chip" onClick={() => setFull((f) => !f)} aria-expanded={full}>
          {full ? "Show fewer stats" : "Show every stat"}
        </button>
      </div>
    </div>
  );
}

export function ordinal(n: number): string {
  const t = n % 100;
  const suffix = t >= 10 && t <= 20 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[n % 10] ?? "th";
  return `${n}${suffix}`;
}

/** "20261124-175223-..." -> "Nov 24, 2026 at 5:52 PM" (the server's local time). */
export function friendlyDate(seasonId: string): string {
  const m = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/.exec(seasonId);
  if (!m) return seasonId;
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const hour = Number(m[4]);
  const h12 = hour % 12 === 0 ? 12 : hour % 12;
  return `${months[Number(m[2]) - 1]} ${Number(m[3])}, ${m[1]} at ${h12}:${m[5]} ${hour < 12 ? "AM" : "PM"}`;
}

/** 6405 -> "1 h 47 min" */
export function duration(seconds: number | null | undefined): string | null {
  if (!seconds || seconds < 60) return null;
  const total = Math.round(seconds / 60);
  const h = Math.floor(total / 60);
  const m = total % 60;
  return h ? `${h} h ${m} min` : `${m} min`;
}

/** "20261003-092152-cheap_recent_openrouter" -> "2026-10-03 09:21". Falls back to the id. */
export function seasonDate(seasonId: string): string {
  const m = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/.exec(seasonId);
  return m ? `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}` : seasonId;
}

export function usd(cents: number | null | undefined): string {
  return `$${((cents ?? 0) / 100).toFixed(2)}`;
}
