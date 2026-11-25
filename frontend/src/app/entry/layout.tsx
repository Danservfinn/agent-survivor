import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Your entry · LLM Survivor",
  // a private page: keep it out of search results and keep its address out of referrers
  robots: { index: false, follow: false },
  referrer: "no-referrer",
};

export default function EntryLayout({ children }: { children: React.ReactNode }) {
  return children;
}
