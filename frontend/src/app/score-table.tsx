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

/** Season scorecard, best index first. Used by the console and by run history. */
export function ScoreTable({
  rows,
  selectedId,
  onSelect,
}: {
  rows: ScoreRow[];
  selectedId?: string | null;
  onSelect?: (agentId: string) => void;
}) {
  const sorted = [...rows].sort((a, b) => b.survivor_index - a.survivor_index);
  const hasPredictions = rows.some((r) => r.prediction_accuracy != null);
  const accuracy = (r: ScoreRow) => (r.challenge_attempts ? r.challenge_accuracy : null);
  const shadeIndex = shader(rows.map((r) => r.survivor_index));
  const shadePlace = shader(rows.map((r) => r.place), true);
  const shadeAcc = shader(rows.map(accuracy));
  const shadePred = shader(rows.map((r) => r.prediction_accuracy ?? null));
  return (
    <div className="bench-table-wrap">
      <table className="bench-table">
        <thead>
          <tr>
            <th>Model</th>
            <th className="num">Survivor index</th>
            <th className="num">Place</th>
            <th className="num">Puzzles right</th>
            <th className="num">Answer s</th>
            <th className="num">Votes cast / against</th>
            {hasPredictions && <th className="num">Votes predicted</th>}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r, i) => (
            <tr key={r.agent_id} className={r.agent_id === selectedId ? "bench-selected" : undefined}>
              <td>
                <span className="mdl">
                  <span className="rk">{i + 1}</span>
                  <span className="mdot" style={{ background: orgColor(r.model_id) }} />
                  {onSelect ? (
                    <button className="narrative-who-btn nm" onClick={() => onSelect(r.agent_id)}>
                      {r.model}
                    </button>
                  ) : (
                    <span className="nm">{r.model}</span>
                  )}
                  {r.alias ? <span className="al">({r.alias})</span> : null}
                  {r.is_winner ? <span className="badge">winner</span> : null}
                </span>
              </td>
              <td className={`num idx ${shadeIndex(r.survivor_index) ?? ""}`}>{r.survivor_index.toFixed(1)}</td>
              <td className={`num ${shadePlace(r.place) ?? ""}`}>{r.place}</td>
              <td className={`num ${shadeAcc(accuracy(r)) ?? ""}`}>
                {r.challenge_attempts ? `${r.challenge_correct}/${r.challenge_attempts}` : "–"}
              </td>
              <td className="num">{r.median_response_ms == null ? "–" : (r.median_response_ms / 1000).toFixed(1)}</td>
              <td className="num">
                {r.votes_cast} / {r.votes_received}
              </td>
              {hasPredictions && (
                <td className={`num ${shadePred(r.prediction_accuracy ?? null) ?? ""}`}>
                  {r.prediction_accuracy == null ? "–" : `${Math.round(r.prediction_accuracy * 100)}%`}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** "20261003-092152-cheap_recent_openrouter" -> "2026-10-03 09:21". Falls back to the id. */
export function seasonDate(seasonId: string): string {
  const m = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/.exec(seasonId);
  return m ? `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}` : seasonId;
}

export function usd(cents: number | null | undefined): string {
  return `$${((cents ?? 0) / 100).toFixed(2)}`;
}
