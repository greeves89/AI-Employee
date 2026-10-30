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

Mode ``3770`` (``rwxrws--T``) is deliberate — two bits beyond plain group write,
and no access at all for *others*:

* **sticky** — ``/shared`` holds the credential *sub-directories* ``.auth`` and
  ``.codex``. With the sticky bit an agent can only remove or rename entries it
  *owns*, so it cannot delete or swap those directories. This is the ``/tmp``
  model. Note this only guards ``/shared``'s *direct* entries — the credential
  *files* nested inside (e.g. ``.codex/auth.json``) are protected separately by
  making both the file and its parent directory root-owned and non-agent-writable
  (see ``codex_auth_service._lock_agent_readonly`` / ``_secure_codex_dir``).
* **setgid** — new subdirectories inherit the agent group, so two agents can
  genuinely collaborate inside the same folder instead of locking each other out.

The *others* bits are stripped (``0`` instead of ``5``): the only processes that
touch ``/shared`` are the orchestrator (root, via the owner bits) and the agent
containers (uid 1000 / gid ``AGENT_GID``, via the group bits). Nothing legitimate
reads ``/shared`` as "other", so world-read/execute is unnecessary exposure of a
directory that carries credential sub-dirs — dropping it keeps every real actor's
full access while removing the surface CodeQL flags as world-readable.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re

logger = logging.getLogger(__name__)

SHARED_DIR = "/shared"
SHARED_VOLUME = "ai-employee-shared"
AGENT_GID = 1000
# rwxrws--T : owner=root, group=agent (writable), setgid + sticky, no world access
SHARED_MODE = 0o3770

# --- Trennung je Nutzer -------------------------------------------------------
# Früher hing das ganze Volume in JEDEM Agenten. Darin lagen Austauschdateien
# aller Nutzer, das Token der Teamlizenz, der gemeinsame Codex-Zugang und das
# Fehlerprotokoll der Plattform — ein Agent sah, was die Agenten anderer Nutzer
# ablegten. Jetzt bekommt ein Agent als ``/shared`` nur den Unterordner seines
# Besitzers; Zugangsdaten und Protokoll hängen nur dort und nur lesend, wo sie
# gebraucht werden (``geteilt_plan``). Der Orchestrator sieht weiter das ganze
# Volume (Skill-Dateien, Taskforce, Alt-Inhalt).
NUTZER_DIR = "nutzer"
OHNE_BESITZER = "ohne-besitzer"
AUTH_DIR = ".auth"            # Token der Teamlizenz (claude_token_service)
CODEX_DIR = ".codex"          # gemeinsamer Codex-Zugang (codex_auth_service)
PLATTFORM_DIR = ".plattform"  # Fehlerprotokoll der Plattform (platform_error_log)
#: Wird vom DockerService gesetzt, sobald feststeht, ob die Engine Unterordner
#: eines Volumes einhängen kann (Docker-API >= 1.45). Ohne das gilt das alte
#: Verhalten — alle Agenten sehen das ganze Volume.
JE_NUTZER_AKTIV = False

_SICHER = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def nutzer_teilpfad(user_id: str | None) -> str:
    """Unterordner des Volumes, den die Agenten eines Besitzers als ``/shared`` sehen."""
    if not user_id:
        name = OHNE_BESITZER
    elif _SICHER.fullmatch(str(user_id)):
        name = str(user_id)
    else:
        name = hashlib.sha256(str(user_id).encode()).hexdigest()[:32]
    return f"{NUTZER_DIR}/{name}"


def agent_sicht(user_id: str | None, root: str = SHARED_DIR) -> str:
    """Wo der Orchestrator findet, was die Agenten dieses Besitzers unter ``/shared`` sehen."""
    if not JE_NUTZER_AKTIV:
        return root
    return os.path.join(root, nutzer_teilpfad(user_id))


def _ordner(path: str, mode: int) -> None:
    os.makedirs(path, exist_ok=True)
    os.chown(path, 0, AGENT_GID)
    os.chmod(path, mode)


def nutzer_ordner_anlegen(user_id: str | None, root: str = SHARED_DIR) -> str:
    """Legt den Unterordner eines Besitzers an (root:agent, setgid+sticky) und die
    Einhängepunkte der Zugangsdaten darin. Docker braucht beides vor dem Start."""
    pfad = os.path.join(root, nutzer_teilpfad(user_id))
    _ordner(os.path.join(root, NUTZER_DIR), 0o0750)
    _ordner(pfad, SHARED_MODE)
    # Leere, nicht beschreibbare Einhängepunkte: ohne Zugang bleibt der Ordner leer.
    for sub in (AUTH_DIR, CODEX_DIR, PLATTFORM_DIR):
        _ordner(os.path.join(pfad, sub), 0o0750)
    return pfad


def geteilt_plan(user_id: str | None, *, mode: str, env: dict, ist_admin: bool) -> dict:
    """Was ein Agent von ``/shared`` sieht — für ``DockerService.create_container``.

    - ``/shared``: Unterordner seines Besitzers, schreibbar.
    - ``/shared/.auth`` (Token der Teamlizenz), nur lesend: nur ohne eigenes
      Claude-Abo des Besitzers und nicht im Codex-Modus.
    - ``/shared/.codex`` (gemeinsamer Codex-Zugang), nur lesend: nur Codex ohne
      eigenen Zugang und ohne API-Schlüssel.
    - ``/shared/.plattform`` (Fehlerprotokoll), nur lesend: nur Agenten von Admins
      (und ohne Besitzer, die gehören den Admins) — das Protokoll nennt Agenten
      und Fehler aller Nutzer.
    """
    nur_lesen: list[tuple[str, str]] = []
    if mode != "codex_cli" and env.get("CLAUDE_CREDENTIAL_SOURCE") != "personal":
        nur_lesen.append((AUTH_DIR, f"{SHARED_DIR}/{AUTH_DIR}"))
    if mode == "codex_cli" and not env.get("CODEX_AUTH_JSON") and not env.get("OPENAI_API_KEY"):
        nur_lesen.append((CODEX_DIR, f"{SHARED_DIR}/{CODEX_DIR}"))
    if ist_admin or not user_id:
        nur_lesen.append((PLATTFORM_DIR, f"{SHARED_DIR}/{PLATTFORM_DIR}"))
    return {"volume": SHARED_VOLUME, "nutzer": nutzer_teilpfad(user_id), "nur_lesen": nur_lesen}


def ensure_shared_volume_perms(path: str = SHARED_DIR) -> bool:
    """Give ``path`` to the agent group with setgid+sticky. Idempotent.

    Returns True if the permissions are in place, False if they could not be
    applied (e.g. the orchestrator is not running as root). Never raises — a
    failure here must not block startup.
    """
    try:
        if not os.path.isdir(path):
            os.makedirs(path, exist_ok=True)
        # Quellen der nur lesend eingehängten Unterordner (Zugangsdaten, Protokoll).
        for sub in (AUTH_DIR, CODEX_DIR, PLATTFORM_DIR):
            _ordner(os.path.join(path, sub), 0o0750)
        token = os.path.join(path, AUTH_DIR, "token.json")
        if os.path.exists(token):
            # Früher 0644 — für jeden lesbar, der das Volume sah.
            os.chown(token, 0, AGENT_GID)
            os.chmod(token, 0o0640)
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
