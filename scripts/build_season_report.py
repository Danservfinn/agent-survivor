"""Build the after-action report for a recorded season.

    python3 scripts/build_season_report.py                  # newest finished season
    python3 scripts/build_season_report.py --season 20261124-164353-cheap_recent_openrouter
    python3 scripts/build_season_report.py --all            # every season with a result
    python3 scripts/build_season_report.py --out ~/Desktop/report.html

Writes report.json and report.html beside the season database (or the page to
--out). No model calls and no network: it reads only the season's own files.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    from backend.live_season import SEASONS_DIR
    from backend.season_report import (
        _season_brief,
        build_season_report,
        render_report_html,
        reportable_season_dir,
        write_report_files,
    )

    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--season", help="season directory name; default is the newest finished season")
    parser.add_argument("--seasons-dir", default=str(SEASONS_DIR), help="directory that holds the season folders")
    parser.add_argument("--all", action="store_true", help="rebuild the report of every finished live season")
    parser.add_argument("--out", help="write only the HTML page to this path")
    parser.add_argument("--contact-url", default=os.environ.get("SURVIVOR_REPORT_CONTACT_URL"))
    parser.add_argument("--site-url", default=os.environ.get("SURVIVOR_PUBLIC_URL"))
    args = parser.parse_args()

    seasons_dir = Path(args.seasons_dir).expanduser()
    if args.all:
        targets = [p for p in sorted(seasons_dir.iterdir()) if p.is_dir() and _season_brief(p) is not None]
    elif args.season:
        targets = [seasons_dir / args.season]
    else:
        newest = reportable_season_dir(seasons_dir)
        targets = [newest] if newest else []
    if not targets:
        print(f"no season with recorded events under {seasons_dir}", file=sys.stderr)
        return 1

    for season_dir in targets:
        if not (season_dir / "season.db").exists():
            print(f"no season database in {season_dir}", file=sys.stderr)
            return 1
        if args.out and len(targets) == 1:
            report = build_season_report(season_dir, seasons_dir=seasons_dir)
            out = Path(args.out).expanduser()
            out.write_text(render_report_html(report, contact_url=args.contact_url, site_url=args.site_url))
            print(out)
        else:
            paths = write_report_files(season_dir, seasons_dir=seasons_dir, contact_url=args.contact_url, site_url=args.site_url)
            print(paths["html"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
