"""Repair ownership/permissions of the ``/shared`` volume so agents can write to it.

Docker creates the named volume ``ai-employee-shared`` as ``root:root 0755``.
It is mounted read-write into every agent container, but the agent process runs
as uid 1000 — the directory's own permission bits deny that uid the write bit,
so agents can read ``/shared`` but never create an entry in it. Yet the injected
system prompt promises every agent that ``/shared`` is the writable cross-agent
file-exchange path, so file handovers (a generated PDF, PPTX, export) silently
fail with ``Permission denied``.

The orchestrator runs as root and already has ``/shared`` mounted read-write, so
fixing the directory here at startup repairs both fresh and already-provisioned
installations (a fix in ``setup.sh``'s ``docker volume create`` would only help
new installs). The permissions live in the volume on disk, so they survive
container recreation and platform updates.

Mode ``3775`` (``rwxrwsr-t``) is deliberate — two bits beyond plain group write:

* **sticky** — ``/shared`` holds the credential *sub-directories* ``.auth`` and
  ``.codex``. With the sticky bit an agent can only remove or rename entries it
  *owns*, so it cannot delete or swap those directories. This is the ``/tmp``
  model. Note this only guards ``/shared``'s *direct* entries — the credential
  *files* nested inside (e.g. ``.codex/auth.json``) are protected separately by
  making both the file and its parent directory root-owned and non-agent-writable
  (see ``codex_auth_service._lock_agent_readonly`` / ``_secure_codex_dir``).
* **setgid** — new subdirectories inherit the agent group, so two agents can
  genuinely collaborate inside the same folder instead of locking each other out.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

SHARED_DIR = "/shared"
AGENT_GID = 1000
# rwxrwsr-t : owner=root, group=agent (writable), setgid + sticky
SHARED_MODE = 0o3775


def ensure_shared_volume_perms(path: str = SHARED_DIR) -> bool:
    """Give ``path`` to the agent group with setgid+sticky. Idempotent.

    Returns True if the permissions are in place, False if they could not be
    applied (e.g. the orchestrator is not running as root). Never raises — a
    failure here must not block startup.
    """
    try:
        if not os.path.isdir(path):
            os.makedirs(path, exist_ok=True)
        os.chown(path, 0, AGENT_GID)  # root:agent
        os.chmod(path, SHARED_MODE)
        logger.info("Shared volume perms ensured (%s -> root:%d, %o)", path, AGENT_GID, SHARED_MODE)
        return True
    except PermissionError:
        logger.warning(
            "Could not adjust %s permissions (orchestrator not root?); "
            "agents may be unable to write to the shared volume",
            path,
        )
        return False
    except Exception as e:  # noqa: BLE001 - never let this block startup
        logger.warning("Ensuring %s permissions failed: %s", path, e)
        return False
