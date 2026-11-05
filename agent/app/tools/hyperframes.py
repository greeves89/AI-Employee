"""Videos rendern fuer Custom-LLM — dieselben Werkzeuge wie ``hyperframes-server.mjs``.

Die anderen Laufzeiten erreichen den stdio-Server; dieser Weg nicht (#161). Der
gemeinsame MCP-Prozess hilft hier nicht, er ist in der Standardinstallation aus.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from app.config import settings

_DOCS = (Path("/opt/mcp/hyperframes-docs.md"),
         Path(__file__).resolve().parents[2] / "mcp" / "hyperframes-docs.md")
_SICHERER_NAME = re.compile(r"[a-zA-Z0-9_-]{1,64}")
RENDER_FRIST_S = 180


def _videoordner() -> Path:
    nutzer = os.environ.get("COMPUTER_USE_USER_ID") or "anonymous"
    return Path(settings.workspace_dir) / "videos" / nutzer


def docs() -> str:
    for pfad in _DOCS:
        if pfad.is_file():
            return pfad.read_text(encoding="utf-8")
    return "Error: Hyperframes guide not found in this image"


def list_videos() -> str:
    ordner = _videoordner()
    videos = sorted(ordner.glob("*.mp4")) if ordner.is_dir() else []
    if not videos:
        return "No videos found."
    return "Videos:\n" + "\n".join(f"- {v}" for v in videos)


async def _hyperframes(ordner: str, *argumente: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "hyperframes", *argumente, cwd=ordner,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, fehler = await asyncio.wait_for(proc.communicate(), RENDER_FRIST_S)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 1, f"timed out after {RENDER_FRIST_S}s"
    return proc.returncode or 0, fehler.decode("utf-8", "replace")


async def render_video(html: str, dateiname: str | None = None) -> str:
    if not html.strip():
        return "Error: html_content is required"
    roh = dateiname or f"video-{int(time.time() * 1000)}"
    name = re.sub(r"\.mp4$", "", os.path.basename(str(roh)), flags=re.I)
    if not _SICHERER_NAME.fullmatch(name):
        return (f'Error: Invalid filename "{dateiname}". Use only letters, numbers, '
                "hyphens, and underscores (max 64 chars).")

    ordner = _videoordner()
    ordner.mkdir(parents=True, exist_ok=True)
    ziel = ordner / f"{name}.mp4"
    arbeit = tempfile.mkdtemp(prefix="hf-")
    try:
        # Eine Datei gleichen Namens aus frueherer Zeit darf einen gescheiterten
        # Render nicht als Erfolg ausgeben.
        ziel.unlink(missing_ok=True)
        # Das Grundgeruest ist optional; manche Versionen ueberspringen diesen Schritt.
        await _hyperframes(arbeit, "init", ".")
        Path(arbeit, "index.html").write_text(html, encoding="utf-8")
        code, fehler = await _hyperframes(
            arbeit, "render", "--output", str(ziel), "--workers", "1")
        # hyperframes endet bei Warnungen ungleich null und liefert trotzdem die Datei.
        if code and not ziel.is_file():
            return f"Render failed: {fehler.strip()[-1000:]}"
    except OSError as e:
        return f"Render failed: {e}"
    finally:
        shutil.rmtree(arbeit, ignore_errors=True)
    return f"Video rendered successfully.\nPath: {ziel}\nSize: {ziel.stat().st_size / 1024:.1f} KB"
