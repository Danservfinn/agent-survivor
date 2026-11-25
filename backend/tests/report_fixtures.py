"""Shared fixtures for the report page tests: one scripted season per process,
and a headless-Chrome render of a report page to its final DOM."""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from backend import database, season_story, turn_controller

SEASON = "20261124-000002-cheap_recent_openrouter"
_PLAYED: Path | None = None


def played_season() -> Path:
    """A season folder holding a full scripted season (no model calls). Built once;
    callers copy it before changing anything."""
    global _PLAYED
    if _PLAYED is not None:
        return _PLAYED
    root = Path(tempfile.mkdtemp(prefix="report-fixture-"))
    atexit.register(shutil.rmtree, root, True)
    season_dir = root / "seasons" / SEASON
    season_dir.mkdir(parents=True)
    db = season_dir / "season.db"
    original = database.DATABASE_PATH
    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "", "LLM_PROVIDER": "openrouter"}, clear=False):
        database.DATABASE_PATH = db
        try:
            database.seed_demo(reset=True, path=db, roster_preset="cheap_recent_openrouter")
            turn_controller.auto_run_to_end(max_rounds=20, max_turns=2000, max_live_calls=0, max_estimated_cost_cents=0)
        finally:
            database.DATABASE_PATH = original
    _PLAYED = season_dir
    return season_dir


def copy_season(dest_root: Path) -> Path:
    """A private copy of the played season under `dest_root/seasons/`."""
    source = played_season()
    dest = Path(dest_root) / "seasons" / source.name
    shutil.copytree(source, dest)
    return dest


def rendered_dom(html: str, query: str = "", width: int = 1280) -> str | None:
    """The page's DOM after its script has run, from headless Chrome; None when
    no Chrome is installed (the caller skips)."""
    chrome = season_story.find_chrome()
    if not chrome:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "report.html"
        page.write_text(html)
        result = subprocess.run(
            [chrome, "--headless=new", "--disable-gpu", "--no-first-run", f"--user-data-dir={tmp}/profile",
             f"--window-size={width},900", "--virtual-time-budget=3000", "--dump-dom", page.as_uri() + query],
            capture_output=True, text=True, timeout=60,
        )
    return result.stdout
