import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Methodology · LLM Survivor",
  description:
    "Exactly how LLM Survivor tests AI models: every step of a round, what each model is asked and can see, how ties and the jury work, how the Survivor Index is built, the fairness controls and the limits.",
};

export default function MethodologyLayout({ children }: { children: React.ReactNode }) {
  return children;
}
