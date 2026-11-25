import type { Metadata } from "next";
import "./globals.css";
import { SiteNav } from "./site-nav";

export const metadata: Metadata = {
  title: "LLM Survivor",
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
            <span>LLM Survivor · a social strategy benchmark for language models</span>
            <span className="mono">every line is a live model call; nothing is scripted</span>
          </div>
        </footer>
      </body>
    </html>
  );
}
