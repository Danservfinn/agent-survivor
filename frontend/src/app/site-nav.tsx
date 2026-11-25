"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/** Top bar shared by every page; matches the season report's bar. */
export function SiteNav() {
  const path = usePathname() || "/";
  const here = (href: string) => (path === href || (href !== "/" && path.startsWith(href)) ? "page" : undefined);
  return (
    <div className="top">
      <div className="wrap top-in">
        <Link className="brand" href="/">
          <span className="pulse" aria-hidden="true" />
          LLM Survivor
        </Link>
        <nav aria-label="Site">
          <Link href="/" aria-current={here("/")}>
            Home
          </Link>
          <Link href="/watch" aria-current={here("/watch")}>
            Watch
          </Link>
          {/* the report is served by the backend, not by this static export */}
          <a href="/report">Latest report</a>
          <Link href="/history" aria-current={here("/history")}>
            Past seasons
          </Link>
        </nav>
      </div>
    </div>
  );
}
