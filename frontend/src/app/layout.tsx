import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "LLM Survivor Benchmark",
  description: "One-click full-season social-strategy benchmark for language models. Text-only results console.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
