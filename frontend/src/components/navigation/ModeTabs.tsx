"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { FlaskConical, Radio } from "lucide-react";

const tabs = [
  {
    href: "/watch",
    label: "Watch",
    description: "Live exhibition",
    icon: Radio,
  },
  {
    href: "/benchmark",
    label: "Benchmarking",
    description: "LLM replay lab",
    icon: FlaskConical,
  },
];

export function ModeTabs() {
  const pathname = usePathname();

  return (
    <nav className="mode-tabs" aria-label="LLM Survivor modes">
      <div className="mode-tabs-inner">
        <Link className="mode-brand" href="/" aria-label="LLM Survivor public home">
          <span>LLM Survivor</span>
        </Link>
        <div className="mode-tab-list">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            const isActive = pathname === tab.href || pathname.startsWith(`${tab.href}/`);
            return (
              <Link
                key={tab.href}
                href={tab.href}
                className={isActive ? "mode-tab active" : "mode-tab"}
                aria-current={isActive ? "page" : undefined}
              >
                <Icon size={17} />
                <span>
                  <strong>{tab.label}</strong>
                  <small>{tab.description}</small>
                </span>
              </Link>
            );
          })}
        </div>
      </div>
    </nav>
  );
}
