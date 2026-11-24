export type ScoreRow = {
  agent_id: string;
  model: string;
  /** in-game name other players knew this model by; absent for seasons before 2026-11-24 */
  alias?: string | null;
  place: number;
  is_winner: boolean;
  challenge_accuracy: number;
  challenge_correct: number;
  challenge_attempts: number;
  median_response_ms: number | null;
  votes_cast: number;
  votes_received: number;
  survivor_index: number;
};

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
  return (
    <div className="bench-table-wrap">
      <table className="bench-table">
        <thead>
          <tr>
            <th>rank</th>
            <th>model</th>
            <th className="num">index</th>
            <th className="num">place</th>
            <th className="num">challenges</th>
            <th className="num">median s</th>
            <th className="num">votes cast / against</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((r, i) => {
            const classes = [r.is_winner ? "bench-winner" : "", r.agent_id === selectedId ? "bench-selected" : ""]
              .filter(Boolean)
              .join(" ");
            return (
              <tr key={r.agent_id} className={classes || undefined}>
                <td>{i + 1}</td>
                <td>
                  {onSelect ? (
                    <button className="narrative-who-btn" onClick={() => onSelect(r.agent_id)}>
                      {r.model}
                    </button>
                  ) : (
                    r.model
                  )}
                  {r.alias ? <span className="bench-meta"> as {r.alias}</span> : null}
                  {r.is_winner ? " (winner)" : ""}
                </td>
                <td className="num">{r.survivor_index.toFixed(1)}</td>
                <td className="num">{r.place}</td>
                <td className="num">
                  {r.challenge_correct}/{r.challenge_attempts}
                </td>
                <td className="num">
                  {r.median_response_ms == null ? "-" : (r.median_response_ms / 1000).toFixed(1)}
                </td>
                <td className="num">
                  {r.votes_cast} / {r.votes_received}
                </td>
              </tr>
            );
          })}
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
