"""Build the after-action report for a recorded season.

    python3 scripts/build_season_report.py                  # newest finished season
    python3 scripts/build_season_report.py --season 20261124-164353-cheap_recent_openrouter
    python3 scripts/build_season_report.py --all            # every season with a result
    python3 scripts/build_season_report.py --out ~/Desktop/report.html
    python3 scripts/build_season_report.py --rescore        # bring every stored season to the current index

--rescore rewrites each season's scorecard.json under the current Survivor
Index (the old one is kept as scorecard.v<old>.json), in the seasons folder and
in --archive-dir, then rebuilds the stored report, thread and images of every
season that had them. The season clip is kept as it is (it shows no index).

Writes report.json and report.html beside the season database (or the page to
--out), plus a share folder: a draft thread (share/thread.md) and one image per
moment, per model, the season and the leaderboard. Nothing is posted anywhere.
No model calls and no network: it reads only the season's own files. The images
need a Chrome or Chromium on this machine (SURVIVOR_CHROME overrides the path).
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
        rescore_scorecard,
        write_report_files,
    )

    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--season", help="season directory name; default is the newest finished season")
    parser.add_argument("--seasons-dir", default=str(SEASONS_DIR), help="directory that holds the season folders")
    parser.add_argument("--all", action="store_true", help="rebuild the report of every finished live season")
    parser.add_argument("--out", help="write only the HTML page to this path")
    parser.add_argument("--contact-url", default=os.environ.get("SURVIVOR_REPORT_CONTACT_URL"))
    parser.add_argument("--site-url", default=os.environ.get("SURVIVOR_PUBLIC_URL"))
    parser.add_argument("--handle", default=os.environ.get("SURVIVOR_X_HANDLE"), help="X handle credited on the page and the share images")
    parser.add_argument("--no-cards", action="store_true", help="skip the share images (they need a local Chrome)")
    parser.add_argument("--no-video", action="store_true", help="skip the season clip (it needs `cd video && npm ci`)")
    parser.add_argument("--rescore", action="store_true", help="rescore every stored season under the current index and rebuild its stored files")
    parser.add_argument("--archive-dir", action="append", default=None, help="another folder of seasons to rescore (repeatable)")
    args = parser.parse_args()

    seasons_dir = Path(args.seasons_dir).expanduser()
    if args.rescore:
        return rescore_all(seasons_dir, [Path(d).expanduser() for d in (args.archive_dir or [])], args, rescore_scorecard, write_report_files)
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
            out.write_text(render_report_html(report, contact_url=args.contact_url, site_url=args.site_url, handle=args.handle))
            print(out)
        else:
            paths = write_report_files(
                season_dir,
                seasons_dir=seasons_dir,
                contact_url=args.contact_url,
                site_url=args.site_url,
                handle=args.handle,
                cards=not args.no_cards,
                video=not args.no_video,
            )
            print(paths["html"])
            print(paths["thread"])
            cards = paths["cards"]
            print(f"share images: {len(cards['written'])} written" + (f", {len(cards['skipped'])} skipped ({cards['reason']})" if cards["skipped"] or cards["reason"] else ""))
            clip = paths["video"]
            print("season clip: " + ("share/season.mp4 written" if clip["written"] else f"skipped ({clip['reason']})"))
    return 0


def rescore_all(seasons_dir: Path, archive_dirs: list[Path], args, rescore_scorecard, write_report_files) -> int:
    import json

    changed = 0
    for folder in [seasons_dir, *archive_dirs]:
        if not folder.is_dir():
            print(f"skipped {folder}: not a folder", file=sys.stderr)
            continue
        for season_dir in sorted(p for p in folder.iterdir() if p.is_dir() and (p / "season.db").exists()):
            if not rescore_scorecard(season_dir):
                continue
            changed += 1
            line = f"rescored {season_dir.name}"
            if (season_dir / "report.json").exists():
                paths = write_report_files(
                    season_dir, seasons_dir=folder, contact_url=args.contact_url, site_url=args.site_url, handle=args.handle,
                    cards=not args.no_cards, video=False,
                )
                line += f"; report rebuilt, {len(paths['cards']['written'])} images"
            print(line)
        # the runner's flag file carries a copy of the last season's scorecard for the home page
        flag = folder / "runner_status.json"
        if flag.exists():
            status = json.loads(flag.read_text())
            report = status.get("report") or {}
            season = report.get("report_url", "").rsplit("/", 1)[-1] if report.get("report_url") else None
            card = folder / season / "scorecard.json" if season else None
            if status.get("status") == "done" and card and card.exists():
                fresh = json.loads(card.read_text())
                fresh.pop("db", None)
                status["report"] = {**fresh, "report_url": report.get("report_url")}
                flag.write_text(json.dumps(status))
                print(f"updated {flag.name} to the rescored scorecard of {season}")
    print(f"{changed} season(s) rescored")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
