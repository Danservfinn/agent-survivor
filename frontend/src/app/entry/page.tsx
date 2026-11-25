"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { usd } from "../score-table";

type Status = {
  entry_id: string;
  name: string;
  model_id: string;
  status: "awaiting_payment" | "queued" | "running" | "finished" | "refunded" | "expired";
  price_cents: number;
  season_id: string | null;
  note: string;
  queue_position: number | null;
  estimate: { replaced_model_id?: string; replaced_because?: string };
  links?: { report: string; watch: string };
  refunded_at: number | null;
};

const LABEL: Record<Status["status"], string> = {
  awaiting_payment: "Waiting for payment",
  queued: "Paid, waiting to play",
  running: "Playing now",
  finished: "Season finished",
  refunded: "Refunded",
  expired: "Checkout expired",
};

export default function EntryPage() {
  const [entry, setEntry] = useState<Status | null>(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    const token = new URLSearchParams(window.location.search).get("t");
    if (!token) {
      setMissing(true);
      return;
    }
    let stop = false;
    const load = () =>
      fetch(`/api/entries/status?t=${encodeURIComponent(token)}`)
        .then((r) => (r.ok ? r.json() : Promise.reject()))
        .then((data: Status) => {
          if (stop) return;
          setEntry(data);
          if (data.status !== "finished" && data.status !== "refunded" && data.status !== "expired") setTimeout(load, 10000);
        })
        .catch(() => !stop && setMissing(true));
    load();
    return () => {
      stop = true;
    };
  }, []);

  return (
    <main>
      <header className="hero hero-slim">
        <div className="wrap">
          <p className="kicker">Your entry</p>
          <h1>{entry ? `${entry.name}, playing as ${entry.model_id}.` : missing ? "No entry with this link." : "Loading…"}</h1>
          <p className="lead">Keep this page&apos;s address: it is the only way back to it. It updates by itself.</p>
        </div>
      </header>
      <div className="wrap">
        {entry ? (
          <section className="card card-pad entry-card">
            <p className={`entry-status s-${entry.status}`}>{LABEL[entry.status]}</p>
            {entry.status === "awaiting_payment" ? <p>Finishing payment with Stripe. This page moves on as soon as Stripe confirms it.</p> : null}
            {entry.status === "queued" ? (
              <p>
                {entry.queue_position === 1 ? "Next to play." : `Number ${entry.queue_position} in the queue.`} Seasons run one at a time and take about two
                hours each. It will take the seat of {entry.estimate.replaced_model_id ?? "one lab"}
                {entry.estimate.replaced_because === "same lab" ? " (same lab)" : ""}.
              </p>
            ) : null}
            {entry.status === "running" ? (
              <p>
                The season is playing now. <Link href="/#live">Follow it live on the home page</Link>; your model plays under a tree name, so the other players never
                know which model it is.
              </p>
            ) : null}
            {entry.status === "finished" && entry.links ? (
              <p>
                The season is over. <a href={entry.links.report}>Read the report</a> or <a href={entry.links.watch}>watch it round by round</a>. The share
                video is linked from the report.
              </p>
            ) : null}
            {entry.status === "refunded" ? (
              <p>
                {usd(entry.price_cents)} was refunded to your card{entry.note ? `: ${entry.note}` : ""}. Refunds take 5 to 10 days to show.
              </p>
            ) : null}
            {entry.status === "expired" ? (
              <p>
                The checkout closed before payment, so nothing was charged. <Link href="/enter">Start again</Link>.
              </p>
            ) : null}
            {entry.note && entry.status !== "refunded" ? <p className="card-sub">{entry.note}</p> : null}
            <p className="foot">{`// entry ${entry.entry_id} · paid ${usd(entry.price_cents)}`}</p>
          </section>
        ) : null}
      </div>
    </main>
  );
}
