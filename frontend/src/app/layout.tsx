import type { Metadata } from "next";
import "./globals.css";
import { SiteNav } from "./site-nav";

export const metadata: Metadata = {
  title: "Agent Survivor",
  description:
    "Nine AI models play Survivor: private talks, vote promises, puzzles for immunity and a jury of the players they voted out. Every line is a live model call.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>
        <SiteNav />
        {children}
        <footer className="site-foot">
          <div className="wrap">
            <span>Agent Survivor · a social strategy benchmark for language models</span>
            <span className="mono">every line is a live model call; nothing is scripted</span>
            <span>Logos belong to their owners. Agent Survivor is not affiliated with any lab.</span>
          </div>
        </footer>
      </body>
    </html>
  );
}
