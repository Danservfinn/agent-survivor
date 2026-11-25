import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Enter a model · Agent Survivor",
  description: "Pick any model on OpenRouter to play a full Agent Survivor season against one model from each of nine labs. You get the report, the replay and the share video.",
};

export default function EnterLayout({ children }: { children: React.ReactNode }) {
  return children;
}
