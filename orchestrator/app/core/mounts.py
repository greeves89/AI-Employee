"""
Mount catalog: parse AGENT_MOUNT_CATALOG env var and resolve per-agent mounts.

Catalog format (one entry per line, lines starting with # are comments):
    label:host_path:container_path:mode

Example:
    nas-documents:/mnt/nas/documents:/mnt/documents:ro
    project-repo:/srv/repos/myproject:/workspace/repo:rw
    shared-output:/data/output:/output:rw

Security invariants:
  - Only labels from the admin-defined catalog are allowed (strict allowlist)
  - host_path and mode are set server-side — never taken from user input
  - container_path for a new mount must not exactly equal /workspace (that volume
    is always mounted separately and must not be replaced)
  - UI never receives the host_path — only label, container_path, and mode
"""
import re
from dataclasses import dataclass


_LABEL_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


@dataclass(frozen=True)
class MountEntry:
    label: str
    host_path: str
    container_path: str
    mode: str  # "ro" or "rw"


def parse_mount_catalog(raw: str) -> dict[str, MountEntry]:
    """Parse the AGENT_MOUNT_CATALOG string into a label → MountEntry dict.

    Invalid lines are skipped with a warning rather than failing startup.
    """
    import logging
    log = logging.getLogger(__name__)

    catalog: dict[str, MountEntry] = {}
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":", 3)
        if len(parts) != 4:
            log.warning("Skipping malformed mount catalog entry (expected label:host:container:mode): %r", line)
            continue
        label, host_path, container_path, mode = (p.strip() for p in parts)
        if not _LABEL_RE.match(label):
            log.warning("Skipping mount catalog entry with invalid label %r", label)
            continue
        if not host_path.startswith("/"):
            log.warning("Skipping mount catalog entry %r: host_path must be absolute", label)
            continue
        if not container_path.startswith("/"):
            log.warning("Skipping mount catalog entry %r: container_path must be absolute", label)
            continue
        if container_path == "/workspace":
            log.warning("Skipping mount catalog entry %r: container_path=/workspace is reserved", label)
            continue
        if mode not in ("ro", "rw"):
            log.warning("Skipping mount catalog entry %r: mode must be 'ro' or 'rw'", label)
            continue
        catalog[label] = MountEntry(label=label, host_path=host_path, container_path=container_path, mode=mode)

    return catalog


async def get_effective_catalog(db) -> dict[str, MountEntry]:
    """Mount catalog = static env catalog (AGENT_MOUNT_CATALOG) merged with the
    DB-managed Second Brain vaults.

    Resolved fresh on every agent start / catalog read, so a brain created in the
    UI is usable immediately — no orchestrator restart. Imports are local to keep
    this module free of DB/app import cycles at load time.
    """
    from app.config import settings
    from sqlalchemy import select
    from app.models.second_brain import SecondBrain

    catalog = parse_mount_catalog(settings.agent_mount_catalog)
    rows = (await db.execute(select(SecondBrain).where(SecondBrain.is_active.is_(True)))).scalars().all()
    for b in rows:
        if not _LABEL_RE.match(b.label):
            continue
        catalog[b.label] = MountEntry(
            label=b.label,
            host_path=b.host_path,
            container_path=b.container_path,
            mode="ro" if b.default_mode == "ro" else "rw",
        )
    return catalog


def _stricter_mode(catalog_mode: str, requested_mode: str | None) -> str:
    """Return the effective Docker mount mode. read-only always wins over read-write."""
    if catalog_mode == "ro" or requested_mode == "ro":
        return "ro"
    return "rw"


def resolve_agent_mounts(
    mount_labels: list[str],
    catalog: dict[str, MountEntry],
    mount_modes: dict[str, str] | None = None,
) -> list[MountEntry]:
    """Return MountEntry list for labels, applying optional per-agent mode caps."""
    out: list[MountEntry] = []
    mount_modes = mount_modes or {}
    for label in mount_labels:
        if label not in catalog:
            continue
        entry = catalog[label]
        mode = _stricter_mode(entry.mode, mount_modes.get(label))
        out.append(MountEntry(
            label=entry.label,
            host_path=entry.host_path,
            container_path=entry.container_path,
            mode=mode,
        ))
    return out


def mounts_to_docker_volumes(mounts: list[MountEntry]) -> dict[str, dict]:
    """Convert MountEntry list to the volumes dict format expected by docker SDK."""
    return {
        m.host_path: {"bind": m.container_path, "mode": m.mode}
        for m in mounts
    }
