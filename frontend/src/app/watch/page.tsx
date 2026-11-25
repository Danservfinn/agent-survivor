"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { SeasonStage, StageReport } from "../season-stage";
import { friendlyDate } from "../score-table";

/** /watch plays the newest season; /watch?season=<id> plays any recorded one. */
export default function WatchPage() {
  const [report, setReport] = useState<StageReport | null>(null);
  const [failed, setFailed] = useState(false);
  const [seasonId, setSeasonId] = useState<string | null>(null);
  // an unlisted season (a paid entry) opens only with the key from its private link
  const [linkKey, setLinkKey] = useState<string | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const wanted = params.get("season");
    const key = params.get("k");
    setLinkKey(key);
    const url = wanted
      ? `/api/live-season/report/${encodeURIComponent(wanted)}${key ? `?k=${encodeURIComponent(key)}` : ""}`
      : "/api/live-season/report";
    fetch(url)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!data?.ok) {
          setFailed(true);
          return;
        }
        setReport(data);
        setSeasonId(data.season.season_id);
      })
      .catch(() => setFailed(true));
  }, []);

  return (
    <main>
      <header className="hero hero-slim">
        <div className="wrap">
          <p className="kicker">Watch a season</p>
          <h1>{seasonId ? `The season of ${friendlyDate(seasonId)}, round by round.` : "A season, round by round."}</h1>
          <p className="lead">
            Every round as it happened: the puzzle, the private talks, the promises, the vote and who goes home. The
            words in the bubbles are the models&apos; own.
          </p>
        </div>
      </header>
      <section className="sec">
        <div className="wrap">
          {failed && <p className="empty">This season could not be loaded. Try another from Past seasons.</p>}
          {!failed && !report && <p className="empty">Loading the season...</p>}
          {report && <SeasonStage report={report} />}
          <div className="row-actions watch-links">
            {seasonId && (
              <a className="btn-ghost" href={`/report/${seasonId}${linkKey ? `?k=${encodeURIComponent(linkKey)}` : ""}`}>
                Read the full report
              </a>
            )}
            <Link className="btn-ghost" href="/history">
              Past seasons
            </Link>
          </div>
        </div>
      </section>
    </main>
  );
}
