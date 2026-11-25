"""The season clip: a short square MP4 of the season's highlights, for sharing.

The clip is a Remotion project in ../video. It reuses the Watch page's stage code
and renders the season report into share/season.mp4. Rendering needs Node and the
project's packages (`cd video && npm ci`); when either is missing the clip is
skipped and the rest of the report is unaffected. Nothing here posts anywhere.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

VIDEO_DIR = Path(__file__).resolve().parent.parent / "video"
CLIP_NAME = "season.mp4"
RENDER_TIMEOUT_S = 900  # the first render also downloads Remotion's own browser


def _remotion_cli() -> Path:
    return VIDEO_DIR / "node_modules" / "@remotion" / "cli" / "remotion-cli.js"


def find_node() -> str | None:
    override = os.environ.get("SURVIVOR_NODE")
    if override:
        return override if Path(override).exists() else None
    found = shutil.which("node")
    if found:
        return found
    # launchd jobs start with a short PATH; Homebrew's node lives here on the Mini
    for candidate in ("/opt/homebrew/bin/node", "/usr/local/bin/node"):
        if Path(candidate).exists():
            return candidate
    return None


def video_ready() -> str | None:
    """None when a clip can be rendered, else the reason it cannot."""
    if not (VIDEO_DIR / "src" / "index.ts").exists():
        return "no video project"
    if not _remotion_cli().exists():
        return "video packages not installed; run `cd video && npm ci`"
    if not find_node():
        return "no Node found; set SURVIVOR_NODE"
    return None


def render_clip(
    report: dict[str, Any],
    out_path: Path,
    *,
    handle: str | None = None,
    site: str | None = None,
    timeout: int = RENDER_TIMEOUT_S,
) -> dict[str, Any]:
    """Render the season clip to `out_path`. Returns {"written": bool, "reason": str}."""
    reason = video_ready()
    if reason:
        return {"written": False, "reason": reason}
    node = find_node()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="survivor-clip-") as tmp:
        props_path = Path(tmp) / "props.json"
        props_path.write_text(json.dumps({"report": report, "handle": handle, "site": site}, ensure_ascii=False))
        command = [
            str(node),
            str(_remotion_cli()),
            "render",
            "src/index.ts",
            "SeasonClip",
            str(out_path),
            f"--props={props_path}",
            "--log=error",
        ]
        env = dict(os.environ)
        # remotion starts helper processes by name; give them the same node
        env["PATH"] = f"{Path(str(node)).parent}{os.pathsep}{env.get('PATH', '')}"
        try:
            done = subprocess.run(command, cwd=VIDEO_DIR, env=env, capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return {"written": False, "reason": f"render took longer than {timeout}s"}
        except OSError as exc:
            return {"written": False, "reason": f"render could not start: {exc}"}
    if done.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        tail = (done.stderr or done.stdout or "").strip().splitlines()[-3:]
        return {"written": False, "reason": "render failed: " + " | ".join(tail)[:400]}
    return {"written": True, "reason": ""}
