import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Methodology · Agent Survivor",
  description:
    "Exactly how Agent Survivor tests AI models: every step of a round, what each model is asked and can see, how ties and the jury work, how the Survivor Index is built, the fairness controls and the limits.",
};

export default function MethodologyLayout({ children }: { children: React.ReactNode }) {
  return children;
}
