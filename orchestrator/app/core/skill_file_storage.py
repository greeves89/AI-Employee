"""Skill file storage — manages files attached to marketplace skills.

Files live at {skill_files_root}/{skill_id}/{filename} on the shared Docker volume
so all containers can access them without copying.
"""

import hashlib
import logging
import os
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {
    ".py", ".js", ".ts", ".sh", ".bash", ".yaml", ".yml", ".json",
    ".toml", ".txt", ".md", ".csv", ".xml", ".html", ".css",
    ".env.example", ".conf", ".cfg", ".ini",
}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


def skill_dir(skill_id: int) -> Path:
    return Path(settings.skill_files_root) / str(skill_id)


def storage_path(skill_id: int, filename: str) -> Path:
    return skill_dir(skill_id) / filename


def validate_filename(filename: str) -> str:
    """Sanitize filename and enforce allowed extensions. Returns safe filename."""
    name = Path(filename).name  # strip any directory components
    if not name:
        raise ValueError("Empty filename")
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"File type '{ext}' not allowed. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}")
    # Replace dangerous characters
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
    return safe


def save_file(skill_id: int, filename: str, data: bytes) -> Path:
    """Write file bytes to skill storage directory. Returns the path."""
    if len(data) > MAX_FILE_SIZE:
        raise ValueError(f"File exceeds maximum size of {MAX_FILE_SIZE // 1024 // 1024} MB")
    path = storage_path(skill_id, filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def read_file(skill_id: int, filename: str) -> bytes:
    """Read file bytes from skill storage."""
    path = storage_path(skill_id, filename)
    if not path.exists():
        raise FileNotFoundError(f"File '{filename}' not found for skill {skill_id}")
    return path.read_bytes()


def delete_file(skill_id: int, filename: str) -> None:
    """Delete a file from skill storage."""
    path = storage_path(skill_id, filename)
    if path.exists():
        path.unlink()
    # Clean up empty skill dir
    try:
        path.parent.rmdir()
    except OSError:
        pass


def list_skill_files(skill_id: int) -> list[str]:
    """List filenames stored for a skill."""
    d = skill_dir(skill_id)
    if not d.exists():
        return []
    return sorted(f.name for f in d.iterdir() if f.is_file())


def get_all_files_for_agent(skill_id: int) -> list[tuple[str, bytes]]:
    """Return (filename, bytes) tuples for all files of a skill — used when pushing to agent."""
    result = []
    for name in list_skill_files(skill_id):
        try:
            result.append((name, read_file(skill_id, name)))
        except Exception as e:
            logger.warning(f"Could not read skill file {name} for skill {skill_id}: {e}")
    return result
