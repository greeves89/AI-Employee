import json
import logging
import os

import docker
from docker.errors import NotFound, APIError

from app.core.log_redaction import scrub_log

logger = logging.getLogger(__name__)

# Load seccomp profile once at import time.  The orchestrator container
# mounts the project root at /app, so the profile lives at
# /app/seccomp-profile.json.  In local development without Docker the
# file may also sit next to the orchestrator package.
_SECCOMP_PROFILE: str | None = None
for _candidate in (
    "/app/seccomp-profile.json",
    os.path.join(os.path.dirname(__file__), "..", "..", "seccomp-profile.json"),
):
    _candidate = os.path.normpath(_candidate)
    if os.path.isfile(_candidate):
        with open(_candidate) as _f:
            # Validate that it's legal JSON, then keep the compact string
            # representation – the Docker SDK passes it verbatim.
            _SECCOMP_PROFILE = json.dumps(json.load(_f), separators=(",", ":"))
        logger.info("Loaded seccomp profile from %s", _candidate)
        break

if _SECCOMP_PROFILE is None:
    logger.warning(
        "seccomp-profile.json not found – agent containers will run without a custom seccomp profile"
    )


def _session_bind_path(environment: dict | None) -> str:
    """Return the CLI session directory for the agent harness."""
    if (environment or {}).get("AGENT_MODE") == "codex_cli":
        return "/home/agent/.codex"
    return "/home/agent/.claude"


# Belegt einen guten Teil des Workspace-Kontingents oft nicht Caches, sondern
# liegengebliebene Git-Worktrees aus abgeschlossener Arbeit (Issue #830:
# 2.3 GB in 9 Worktrees, waehrend der eingebaute Aufraeumlauf nur data/cache,
# tmp und Logs kennt und die Belegung dabei nur von 99.8% auf 96.2% senkte —
# unter der 95%-Schwelle blieb der Agent trotzdem gestoppt).
#
# Sicherheitskriterium, identisch zu dem, das man von Hand pruefen wuerde:
# eine Worktree wird NUR entfernt, wenn sie (a) sauber ist (kein `git status
# --short`-Ausschlag) UND (b) ihr HEAD exakt dem HEAD der gleichnamigen
# Fernzweig-Branch entspricht (`git ls-remote origin <branch>`) — die Arbeit
# ist also vollstaendig gesichert, bevor der lokale Checkout verschwindet.
# Worktrees ohne gleichnamigen Fernzweig (z. B. Claude Codes eigene
# `.claude/worktrees/agent-*`-Sitzungen) werden bewusst NICHT angefasst, weil
# fuer sie kein Fernabgleich moeglich ist.
WORKTREE_PRUNE_SCRIPT = r"""
import os, subprocess

def sh(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)

# Ueberschreibbar fuers Testen (echte Git-Repos in einem Temp-Verzeichnis statt
# /workspace) -- der Helfer-Container selbst setzt sie nie, Default bleibt also
# im Betrieb unveraendert.
root_dir = os.environ.get("WORKTREE_PRUNE_ROOT", "/workspace")

pruned = []
for root, dirs, files in os.walk(root_dir):
    if ".git" in dirs:
        repo = root
        sh(["git", "config", "--global", "--add", "safe.directory", repo])
        listing = sh(["git", "worktree", "list", "--porcelain"], repo)
        entries, current = [], {}
        for line in listing.stdout.splitlines():
            if line.startswith("worktree "):
                if current:
                    entries.append(current)
                current = {"path": line[len("worktree "):]}
            elif line.startswith("branch "):
                current["branch"] = line[len("branch "):]
        if current:
            entries.append(current)
        for entry in entries:
            wt = entry["path"]
            if wt == repo:
                continue
            branch = entry.get("branch", "").replace("refs/heads/", "")
            if not branch:
                continue
            sh(["git", "config", "--global", "--add", "safe.directory", wt])
            status = sh(["git", "status", "--short"], wt)
            if status.returncode != 0 or status.stdout.strip():
                continue  # dirty or unreadable -- keep
            head = sh(["git", "rev-parse", "HEAD"], wt).stdout.strip()
            remote_line = sh(["git", "ls-remote", "origin", branch], repo).stdout
            remote_head = remote_line.split("\t")[0].strip() if remote_line else ""
            if remote_head and head and head == remote_head:
                result = sh(["git", "worktree", "remove", wt, "--force"], repo)
                if result.returncode == 0:
                    pruned.append(wt)
    dirs[:] = [d for d in dirs if d not in (".git", "node_modules", ".venv", "__pycache__")]

print(f"PRUNED {len(pruned)}: {pruned}")
"""


def _directory_entries_for(names) -> list[str]:
    """Every intermediate directory of the given relative file names, parents
    first, each exactly once: ["a", "a/b"] for "a/b/c.txt"."""
    seen: list[str] = []
    for name in names:
        parts = name.strip("/").split("/")[:-1]
        for depth in range(1, len(parts) + 1):
            directory = "/".join(parts[:depth])
            if directory and directory not in seen:
                seen.append(directory)
    return seen


class ZielordnerNichtVorbereitbar(ValueError):
    """Der Zielordner eines Schreibvorgangs liess sich nicht sicher
    anlegen/uebergeben — ein Glied der Kette ist ein Symlink oder eine Datei,
    oder mkdir/chown schlugen fehl. Es wurde NICHTS geschrieben."""


class ZielordnerKompromittiert(ZielordnerNichtVorbereitbar):
    """Die Nachpruefung NACH put_archive hat ein Kettenglied als Symlink
    vorgefunden, das vor dem Schreiben noch ein echtes Verzeichnis war (#843).
    Im Unterschied zu ZielordnerNichtVorbereitbar wurde hier bereits
    geschrieben — der Aufrufer darf den Vorgang trotzdem nicht als Erfolg
    werten, denn der Zielpfad kann im TOCTOU-Fenster zwischen
    prepare_target_dir und put_archive umgelenkt worden sein."""


_WORKSPACE_ROOT = "/workspace"


# Laeuft IM Agenten-Container (python:3.12-slim, python3 ist immer da) als root.
# Argumente: <wurzel> <uid> <gid> <glied>... — legt die Kette wurzel/glied1/glied2/...
# an und uebergibt jedes Glied dem Agenten. Jeder Schritt oeffnet das naechste
# Glied RELATIV zum vorigen Verzeichnis-Deskriptor mit O_NOFOLLOW|O_DIRECTORY und
# chown't den Deskriptor (fchown): ein Symlink an irgendeiner Stelle der Kette
# (auch ein spaeter eingetauschter) liefert ELOOP statt einer Eigentumsaenderung
# ausserhalb der Wurzel. Ein ``chown`` auf Pfade — auch mit ``-h`` — koennte das
# fuer Zwischenglieder nicht garantieren. Meldungen gehen nach stdout, weil
# exec_in_container nur stdout zurueckgibt.
_PREPARE_TARGET_DIR_SCRIPT = """
import errno, os, stat, sys
root, uid, gid, *parts = sys.argv[1:]
uid, gid = int(uid), int(gid)
# Eigene Wache, unabhaengig vom Aufrufer: ein Glied ist genau EIN Ordnername.
# "..", "" oder ein Slash wuerden dir_fd aushebeln ("/x" ignoriert dir_fd).
for part in parts:
    if part in ("", ".", "..") or "/" in part:
        print(f"unzulaessiges Kettenglied {part!r} — Upload-Ziel abgelehnt")
        sys.exit(1)
flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
try:
    fd = os.open(root, flags)
except OSError as e:
    print(f"Wurzel {root!r} nicht als Verzeichnis zu oeffnen: {e.strerror}")
    sys.exit(2)
try:
    for part in parts:
        try:
            os.mkdir(part, 0o755, dir_fd=fd)
        except FileExistsError:
            pass
        except OSError as e:
            print(f"mkdir {part!r} fehlgeschlagen: {e.strerror}")
            sys.exit(3)
        try:
            nfd = os.open(part, flags, dir_fd=fd)
        except OSError as e:
            # Linux meldet fuer einen Symlink bei O_NOFOLLOW|O_DIRECTORY ENOTDIR,
            # nicht ELOOP — fuer die Meldung nachsehen, was es wirklich ist.
            try:
                ist_link = stat.S_ISLNK(os.lstat(part, dir_fd=fd).st_mode)
            except OSError:
                ist_link = False
            if ist_link or e.errno == errno.ELOOP:
                print(f"{part!r} ist ein Symlink — Upload-Ziel abgelehnt")
            elif e.errno == errno.ENOTDIR:
                print(f"{part!r} ist kein Verzeichnis — Upload-Ziel abgelehnt")
            else:
                print(f"{part!r} nicht zu oeffnen: {e.strerror}")
            sys.exit(4)
        try:
            os.fchown(nfd, uid, gid)
        except OSError as e:
            print(f"chown {part!r} fehlgeschlagen: {e.strerror}")
            sys.exit(5)
        os.close(fd)
        fd = nfd
finally:
    os.close(fd)
"""


# Laeuft NACH put_archive als root: geht dieselbe Kette (Wurzel + Basis-
# Ordner) NOCHMAL mit O_NOFOLLOW ab, diesmal rein lesend (kein mkdir, kein
# chown). Schliesst das TOCTOU-Fenster zwischen prepare_target_dir
# (Exec-Aufruf) und put_archive (getrennter Docker-API-Roundtrip, #843): ein
# Kettenglied, das im Fenster gegen einen Symlink getauscht wurde, liefert
# bei der erneuten O_NOFOLLOW-Oeffnung ELOOP statt eines Erfolgs — unabhaengig
# davon, welchen Owner der eingetauschte Symlink traegt. Ein Kettenglied kann
# nicht unbemerkt durch ein ANDERES echtes Verzeichnis ersetzt werden, ohne
# die Wurzel zu verlassen (mkdir legt nur innerhalb desselben Elternordners
# an) — die einzige Fluchtmoeglichkeit aus der Kette ist ein Symlink, und
# genau den faengt O_NOFOLLOW ab. Erkennt einen im Fenster erfolgten Angriff
# NACHTRAEGLICH, verhindert ihn nicht: das Schreiben ist zu diesem Zeitpunkt
# schon passiert, der Aufrufer soll es dann aber nicht als Erfolg werten.
_VERIFY_TARGET_DIR_SCRIPT = """
import errno, os, stat, sys
root, *parts = sys.argv[1:]
for part in parts:
    if part in ("", ".", "..") or "/" in part:
        print(f"unzulaessiges Kettenglied {part!r} — Nachpruefung abgelehnt")
        sys.exit(1)
flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
try:
    fd = os.open(root, flags)
except OSError as e:
    print(f"Wurzel {root!r} nicht mehr als Verzeichnis zu oeffnen: {e.strerror}")
    sys.exit(2)
try:
    for part in parts:
        try:
            nfd = os.open(part, flags, dir_fd=fd)
        except OSError as e:
            try:
                ist_link = stat.S_ISLNK(os.lstat(part, dir_fd=fd).st_mode)
            except OSError:
                ist_link = False
            if ist_link or e.errno == errno.ELOOP:
                print(f"{part!r} wurde waehrend des Schreibens gegen einen Symlink getauscht")
            elif e.errno == errno.ENOTDIR:
                print(f"{part!r} ist kein Verzeichnis mehr")
            else:
                print(f"{part!r} nicht mehr zu oeffnen: {e.strerror}")
            sys.exit(3)
        os.close(fd)
        fd = nfd
finally:
    os.close(fd)
"""


class DockerService:
    """Wraps Docker SDK for container management.

    Connects to a docker-socket-proxy when DOCKER_HOST is set (production),
    otherwise falls back to the local Docker socket (development).
    """

    def __init__(self):
        import os
        docker_host = os.environ.get("DOCKER_HOST")
        if docker_host:
            self.client = docker.DockerClient(base_url=docker_host)
        else:
            self.client = docker.from_env()

    def create_container(
        self,
        image: str,
        name: str,
        environment: dict,
        volume_name: str,
        network: str,
        memory_limit: str = "2g",
        cpu_quota: int = 100000,
        session_volume_name: str | None = None,
        shared_volume_name: str | None = None,
        needs_sudo: bool = False,
        bind_mounts: dict[str, dict] | None = None,
    ) -> docker.models.containers.Container:
        # Ensure named volumes exist (bind-mount host paths are not managed here)
        for vol in [volume_name, session_volume_name, shared_volume_name]:
            if vol:
                try:
                    self.client.volumes.get(vol)
                except NotFound:
                    self.client.volumes.create(name=vol)

        volumes = {volume_name: {"bind": "/workspace", "mode": "rw"}}
        if session_volume_name:
            volumes[session_volume_name] = {
                "bind": _session_bind_path(environment),
                "mode": "rw",
            }
        if shared_volume_name:
            volumes[shared_volume_name] = {"bind": "/shared", "mode": "rw"}
        # Admin-defined bind mounts (host_path → {bind: container_path, mode: ro|rw})
        if bind_mounts:
            volumes.update(bind_mounts)

        # Security: no-new-privileges blocks sudo. Only enable it when
        # no sudo permissions are configured. The container sandbox is
        # the primary isolation layer regardless.
        security_opts = [] if needs_sudo else ["no-new-privileges:true"]

        # Apply seccomp profile to restrict dangerous syscalls (mount,
        # reboot, module loading, etc.) while still allowing everything
        # the agent toolchain needs (bash, git, npm, python, networking).
        if _SECCOMP_PROFILE is not None:
            security_opts.append(f"seccomp={_SECCOMP_PROFILE}")

        container = self.client.containers.run(
            image=image,
            name=name,
            detach=True,
            environment=environment,
            volumes=volumes,
            network=network,
            mem_limit=memory_limit,
            cpu_quota=cpu_quota,
            # Headless Chrome (HyperFrames video rendering) needs >=256 MB of
            # shared memory; Docker's 64 MB default makes Chrome crash mid-render.
            shm_size="512m",
            restart_policy={"Name": "unless-stopped"},
            labels={"ai-employee.type": "agent"},
            # Zombie reaping: tini as PID 1 cleans up defunct child processes
            init=True,
            # Security hardening
            security_opt=security_opts,
            cap_drop=["ALL"],
            # KILL: Die Signalkette hat hier ZWEI Init-Schichten. init=True
            # oben setzt Dockers eigenes docker-init als PID 1; das ENTRYPOINT
            # des Images ("tini -- /entrypoint.sh") ist die zweite Schicht
            # darunter -- daher meldet sich tini im Fehlerfall als PID 7, nicht
            # als PID 1. Die erste Weitergabe (PID 1 -> tini) ist harmlos, beide
            # laufen als root. Die ZWEITE ist der Knackpunkt: entrypoint.sh
            # schliesst mit "exec gosu agent", der Agent laeuft also unter einer
            # anderen UID. Ein Signal ueber die UID-Grenze verlangt CAP_KILL --
            # ohne sie scheitert tini mit EPERM ("Unexpected error when
            # forwarding signal: 'Operation not permitted'"), der Agent sieht
            # SIGTERM nie, und der Container endet mit Exit 1 statt 143
            # (Issue #835).
            cap_add=["CHOWN", "SETUID", "SETGID", "DAC_OVERRIDE", "FOWNER", "KILL"],
            pids_limit=512,
        )
        return container

    def get_container(self, container_id: str):
        return self.client.containers.get(container_id)

    def get_container_logs(self, container_id: str, tail: int = 200,
                           since_seconds: int | None = None) -> str:
        """Return the tail of a container's combined stdout/stderr.

        ``tail`` is hard-capped at 1000 lines so an agent can't drain the host.
        Raw secrets are NOT stripped here — callers that expose logs to agents
        must run the output through app.core.log_redaction.redact_logs first.
        """
        import time
        container = self.client.containers.get(container_id)
        kwargs: dict = {
            "tail": max(1, min(int(tail), 1000)),
            "timestamps": True,
            "stdout": True,
            "stderr": True,
        }
        if since_seconds:
            kwargs["since"] = int(time.time()) - int(since_seconds)
        raw = container.logs(**kwargs)
        if isinstance(raw, (bytes, bytearray)):
            return raw.decode("utf-8", errors="replace")
        return str(raw)

    def stop_container(self, container_id: str) -> None:
        container = self.client.containers.get(container_id)
        container.stop(timeout=30)

    def start_container(self, container_id: str) -> None:
        container = self.client.containers.get(container_id)
        container.start()

    def restart_container(self, container_id: str, timeout: int = 10) -> None:
        """Restart a container by name/id. The daemon (via the socket proxy)
        performs stop+start server-side, so this completes even for the
        orchestrator's own container once the request has been dispatched."""
        container = self.client.containers.get(container_id)
        container.restart(timeout=timeout)

    def remove_container(self, container_id: str, force: bool = True) -> None:
        container = self.client.containers.get(container_id)
        container.remove(force=force)

    def copy_workspace_volume(self, src_volume: str, dst_volume: str,
                              image: str = "ai-employee-agent:latest") -> None:
        """Clone the entire contents of one workspace volume into another.

        Used to give a distributed agent copy the *trained brain* of its source:
        knowledge.md, installed skills (/workspace/.claude/skills), CLAUDE.md and
        any docs the source built up. Excludes ``.git`` and resets the per-agent
        task log (``.agent_state.md``) so each copy starts its own history fresh.
        Runs a short-lived helper container that mounts both volumes.
        """
        script = (
            "cp -a /from/. /to/ 2>/dev/null || true; "
            "rm -rf /to/.git /to/.agent_state.md; "
            "chmod -R u+rwX /to 2>/dev/null || true"
        )
        self.client.containers.run(
            image=image,
            entrypoint="",
            command=["sh", "-c", script],
            volumes={
                src_volume: {"bind": "/from", "mode": "ro"},
                dst_volume: {"bind": "/to", "mode": "rw"},
            },
            remove=True,
            detach=False,
            labels={"ai-employee.type": "clone-helper"},
        )

    def cleanup_workspace_volume(self, volume_name: str,
                                  image: str = "ai-employee-agent:latest") -> int | None:
        """Clear caches/tmp/logs/stale worktrees directly in a workspace
        VOLUME, without the agent's own container running (issue #714,
        Punkt 1; worktree-Anteil issue #830).

        A disk-quota stop leaves an agent permanently stuck: cleanup normally
        needs a live container, and a live container is exactly what the
        quota-stop state prevents. The commands here mirror what the disk
        warning already tells a still-running agent to do itself
        (``disk_monitor._write_warning``); running them via a short-lived
        helper container against the volume (same pattern as
        ``copy_workspace_volume``) works whether or not the agent's own
        container is running. Returns the workspace size in MB AFTER cleanup,
        or ``None`` if the helper run itself failed.
        """
        try:
            prune_output = self.client.containers.run(
                image=image,
                entrypoint="",
                command=["python3", "-c", WORKTREE_PRUNE_SCRIPT],
                volumes={volume_name: {"bind": "/workspace", "mode": "rw"}},
                remove=True,
                detach=False,
                labels={"ai-employee.type": "disk-cleanup-helper"},
            )
        except Exception:
            # Best effort — verwaiste Worktrees sind ein Bonus, kein Muss. Der
            # naechste Schritt (Caches/Logs) soll trotzdem laufen.
            logger.warning("Worktree-Aufraeumlauf fuer Volume %s fehlgeschlagen", volume_name, exc_info=True)
        else:
            text = prune_output.decode("utf-8", errors="replace") if isinstance(prune_output, (bytes, bytearray)) else str(prune_output)
            for line in text.strip().splitlines():
                if line.startswith("PRUNED"):
                    logger.info("Workspace-Aufraeumlauf Volume %s: %s", volume_name, line)

        script = (
            "rm -rf /workspace/data/cache /workspace/tmp /workspace/.cache 2>/dev/null; "
            "find /workspace -name '*.log' -delete 2>/dev/null; "
            "du -sm --exclude=.cache /workspace | cut -f1"
        )
        try:
            output = self.client.containers.run(
                image=image,
                entrypoint="",
                command=["sh", "-c", script],
                volumes={volume_name: {"bind": "/workspace", "mode": "rw"}},
                remove=True,
                detach=False,
                labels={"ai-employee.type": "disk-cleanup-helper"},
            )
        except Exception:
            logger.warning("Workspace-Aufraeumlauf fuer Volume %s fehlgeschlagen", volume_name, exc_info=True)
            return None
        text = output.decode("utf-8", errors="replace") if isinstance(output, (bytes, bytearray)) else str(output)
        line = text.strip().splitlines()[-1] if text.strip() else ""
        return int(line) if line.isdigit() else None

    def remove_volume(self, volume_name: str) -> None:
        try:
            volume = self.client.volumes.get(volume_name)
            volume.remove(force=True)
        except NotFound:
            pass

    def get_container_stats(self, container_id: str) -> dict:
        container = self.client.containers.get(container_id)
        stats = container.stats(stream=False)

        cpu_percent = 0.0
        cpu_delta = (
            stats["cpu_stats"]["cpu_usage"]["total_usage"]
            - stats["precpu_stats"]["cpu_usage"]["total_usage"]
        )
        system_delta = (
            stats["cpu_stats"]["system_cpu_usage"]
            - stats["precpu_stats"]["system_cpu_usage"]
        )
        if system_delta > 0 and cpu_delta > 0:
            num_cpus = len(
                stats["cpu_stats"]["cpu_usage"].get("percpu_usage", [1])
            )
            cpu_percent = (cpu_delta / system_delta) * num_cpus * 100

        mem_usage = stats.get("memory_stats", {}).get("usage", 0)
        mem_limit = stats.get("memory_stats", {}).get("limit", 1)

        return {
            "cpu_percent": round(cpu_percent, 2),
            "memory_usage_mb": round(mem_usage / (1024 * 1024), 2),
            "memory_limit_mb": round(mem_limit / (1024 * 1024), 2),
            "memory_percent": round((mem_usage / mem_limit) * 100, 2) if mem_limit > 0 else 0,
        }

    def get_workspace_disk_usage(self, container_id: str, limit_gb: float) -> dict | None:
        """Return /workspace disk usage stats for a container.

        Uses `du -sm /workspace` so the number reflects ONLY the workspace
        directory's actual size (excluding bind-mounts and other filesystems).
        Percent is calculated against the configured quota limit, capped at 100%.
        """
        try:
            container = self.client.containers.get(container_id)
            exit_code, output = container.exec_run(
                ["du", "-sm", "--exclude=.cache", "/workspace"],
                demux=True,
            )
            stdout = output[0].decode("utf-8", errors="replace") if output[0] else ""
            line = stdout.strip().splitlines()[0] if stdout.strip() else ""
            if not line:
                return None
            used_mb = float(line.split()[0])
            limit_mb = max(limit_gb * 1024, 1)
            disk_percent = round(min(used_mb / limit_mb * 100, 100), 2)
            return {
                "disk_usage_mb": round(used_mb, 2),
                "disk_limit_mb": round(limit_mb, 2),
                "disk_percent": disk_percent,
                "disk_available_mb": round(max(limit_mb - used_mb, 0), 2),
            }
        except Exception:
            return None

    def get_image_id(self, image_name: str) -> str | None:
        """Get the current image ID for a given image name."""
        try:
            image = self.client.images.get(image_name)
            return image.id
        except Exception:
            return None

    def get_container_image_id(self, container_id: str) -> str | None:
        """Get the image ID that a container was built from."""
        try:
            container = self.client.containers.get(container_id)
            return container.image.id
        except Exception:
            return None

    def is_container_image_outdated(
        self,
        container_id: str,
        image_name: str = "ai-employee-agent:latest",
        current_image_id: str | None = None,
    ) -> bool:
        """True if the container runs an older image than the current image_name tag.

        Agent containers are launched from ai-employee-agent:latest but are NOT
        recreated when that tag is rebuilt (issue #433), so a running agent can
        silently stay on a stale image. Compares the container's build image id
        against the id the tag currently points to. Fail-closed to False when
        either id is unknown, so an unresolvable state never shows a false alarm.

        Pass current_image_id (from a single prior get_image_id call) when checking
        many containers against the same tag, to avoid one images.get() per container
        (issue #449).
        """
        current = current_image_id if current_image_id is not None else self.get_image_id(image_name)
        running = self.get_container_image_id(container_id)
        if current is None or running is None:
            return False
        return current != running

    def get_container_status(self, container_id: str) -> str:
        try:
            container = self.client.containers.get(container_id)
            return container.status
        except (NotFound, APIError):
            return "unknown"

    def list_agent_containers(self) -> list:
        containers = self.client.containers.list(
            all=True, filters={"label": "ai-employee.type=agent"}
        )
        return [
            {
                "id": c.id,
                "name": c.name,
                "status": c.status,
                "labels": c.labels,
            }
            for c in containers
        ]

    def exec_in_container(self, container_id: str, cmd: str | list, user: str | None = None) -> tuple[int, str]:
        container = self.client.containers.get(container_id)
        kwargs = {"demux": True}
        if user:
            kwargs["user"] = user
        exit_code, output = container.exec_run(cmd, **kwargs)
        stdout = output[0].decode("utf-8", errors="replace") if output[0] else ""
        return exit_code, stdout

    def write_file_in_container(
        self,
        container_id: str,
        path: str,
        content: str,
        uid: int = 1000,
        gid: int = 1000,
        root: str = _WORKSPACE_ROOT,
    ) -> None:
        """Write a file into a running container using tar archive.

        Files are owned by the agent user (uid/gid 1000) by default so the agent
        process can modify them afterwards (e.g. /workspace/knowledge.md). A freshly
        constructed TarInfo defaults to uid/gid 0, which put_archive honours and would
        otherwise leave the file root-owned and unwritable for the agent.

        Der Zielordner wird VORHER symlink-sicher angelegt und dem Agenten
        uebergeben (:meth:`prepare_target_dir`) — derselbe Schutz wie bei
        :meth:`write_files_in_container`, siehe dort fuer die Begruendung
        (#821, #840). ``root`` ist normalerweise ``/workspace``; die zwei
        Aufrufer, die legitim ausserhalb schreiben (Sudoers-Datei, geteilte
        Team-Registrierung), geben ihre eigene Wurzel UND passende uid/gid
        bewusst mit, statt sie stillschweigend zu umgehen (#841).

        Nach ``put_archive`` wird die Kette per :meth:`_assert_target_dir_still_safe`
        nochmal nachgeprueft (#843) — schliesst das TOCTOU-Fenster zwischen
        Vorbereitung und Schreiben, siehe dort.
        """
        import io
        import tarfile

        filename = path.split("/")[-1]
        dir_path = "/".join(path.split("/")[:-1]) or "/"
        safe_dir = self.prepare_target_dir(container_id, dir_path, uid, gid, root=root)

        container = self.client.containers.get(container_id)

        # Create a tar archive with the file
        data = content.encode("utf-8")

        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            info = tarfile.TarInfo(name=filename)
            info.size = len(data)
            info.uid = uid
            info.gid = gid
            tar.addfile(info, io.BytesIO(data))
        tar_stream.seek(0)

        container.put_archive(safe_dir, tar_stream)
        self._assert_target_dir_still_safe(container_id, dir_path, root=root)

    def prepare_target_dir(
        self, container_id: str, target_dir: str, uid: int = 1000, gid: int = 1000,
        root: str = _WORKSPACE_ROOT,
    ) -> str:
        """Die Ordnerkette unterhalb von ``root`` (Vorgabe ``/workspace``)
        symlink-sicher anlegen und dem Agenten uebergeben. Gibt den
        normalisierten Zielpfad zurueck.

        mkdir laeuft als root, ein frisch angelegtes Ziel gehoerte damit root —
        der Agent koennte danach keine Datei daneben legen. Deshalb wird jedes
        Glied angelegt UND uebergeben, in EINEM Schritt im Behaelter, jeweils
        relativ zum vorigen Verzeichnis-Deskriptor mit O_NOFOLLOW (siehe
        :data:`_PREPARE_TARGET_DIR_SCRIPT`). Ein Exitcode != 0 bricht ab,
        BEVOR irgendetwas geschrieben wurde.

        ``root`` ist eine explizite Entscheidung des Aufrufers, keine
        Umgehung: die beiden Stellen, die legitim ausserhalb von
        ``/workspace`` schreiben (Sudoers-Datei unter ``/etc``, geteilte
        Team-Registrierung unter ``/shared``), geben ihre Wurzel bewusst mit
        UND — wichtig fuer ``/etc`` — passende uid/gid, sonst chownt dieser
        Helfer ein Systemverzeichnis auf den Agenten (#841).

        Was das NICHT ist: eine Garantie gegen einen Tausch zwischen dieser
        Pruefung und dem Schreiben. Die Kette gehoert danach dem/der
        uebergebenen uid/gid, ein Aufrufer mit Agenten-uid kann ein Glied in
        der Zwischenzeit ersetzen. Abgedeckt ist der Zustand VOR dem
        Schreiben — das ist genau der Weg, ueber den der Zielpfad heute
        umgelenkt wuerde. Das Fenster NACH dieser Pruefung schliesst
        :meth:`_assert_target_dir_still_safe`, die beide Schreib-Helfer nach
        ``put_archive`` aufrufen (#843): eine Nachpruefung erkennt einen im
        Fenster eingetauschten Symlink zwar erst NACHTRAEGLICH, markiert den
        Vorgang dann aber als kompromittiert statt ihn als Erfolg zu melden.

        Braucht einen LAUFENDEN Behaelter (exec). ``put_archive`` allein kaeme
        auch an einen gestoppten heran; deshalb wird dieser Fall hier in eine
        verstaendliche Ablehnung uebersetzt statt in einen Docker-Fehler.

        Raises:
            ValueError: Ziel liegt nicht unterhalb von ``root``.
            ZielordnerNichtVorbereitbar: Kette nicht sicher herstellbar, oder
                der Behaelter laeuft nicht.
        """
        if "\x00" in target_dir:
            raise ValueError("Null bytes not allowed in path")
        safe_root = os.path.normpath(root)
        safe_dir = os.path.normpath(target_dir)
        if safe_dir != safe_root and not safe_dir.startswith(safe_root + "/"):
            # Kein Aufrufer schreibt heute ausserhalb seiner erklaerten
            # Wurzel; wer es kuenftig tut, soll das bewusst entscheiden und
            # nicht hier hineinstolpern.
            raise ValueError(f"Write target must be within {safe_root}")

        parts = [p for p in safe_dir[len(safe_root):].split("/") if p]
        try:
            exit_code, output = self.exec_in_container(
                container_id,
                ["python3", "-c", _PREPARE_TARGET_DIR_SCRIPT,
                 safe_root, str(uid), str(gid), *parts],
                user="root",
            )
        except APIError as e:
            # Haeufigster Fall: der Behaelter laeuft nicht (409). Ohne diese
            # Uebersetzung meldet der Import-Endpunkt ein nacktes 500.
            raise ZielordnerNichtVorbereitbar(
                "Zielordner konnte nicht vorbereitet werden: der Agent-Behaelter "
                f"antwortet nicht (laeuft er?) — {e}"
            ) from e
        if exit_code != 0:
            grund = (output or "").strip().splitlines()[-1:] or ["unbekannter Fehler"]
            logger.warning(
                "[Dateien] Ziel %s nicht vorbereitbar (rc=%s): %s",
                scrub_log(safe_dir), exit_code, scrub_log(grund[0]),
            )
            raise ZielordnerNichtVorbereitbar(
                f"Zielordner konnte nicht vorbereitet werden: {grund[0]}"
            )
        return safe_dir

    def _assert_target_dir_still_safe(
        self, container_id: str, target_dir: str, root: str = _WORKSPACE_ROOT,
    ) -> None:
        """Nachpruefung NACH ``put_archive`` (#843): geht dieselbe Kette wie
        :meth:`prepare_target_dir` erneut mit O_NOFOLLOW ab, diesmal rein
        lesend. ``target_dir`` hat zu diesem Zeitpunkt bereits
        :meth:`prepare_target_dir` erfolgreich durchlaufen — hier wird nur
        noch geprueft, ob sich seitdem etwas veraendert hat, nicht erneut
        gegen ``root`` validiert.

        Wirft :class:`ZielordnerKompromittiert`, wenn ein Kettenglied im
        Fenster zwischen Vorbereitung und Schreiben gegen einen Symlink
        getauscht wurde. Das Schreiben ist dann bereits passiert — der
        Aufrufer soll es trotzdem nicht als Erfolg werten.
        """
        safe_root = os.path.normpath(root)
        safe_dir = os.path.normpath(target_dir)
        parts = [p for p in safe_dir[len(safe_root):].split("/") if p] if safe_dir != safe_root else []
        try:
            exit_code, output = self.exec_in_container(
                container_id,
                ["python3", "-c", _VERIFY_TARGET_DIR_SCRIPT, safe_root, *parts],
                user="root",
            )
        except APIError as e:
            raise ZielordnerKompromittiert(
                "Zielordner nach dem Schreiben nicht mehr zu pruefen: der "
                f"Agent-Behaelter antwortet nicht (laeuft er noch?) — {e}"
            ) from e
        if exit_code != 0:
            grund = (output or "").strip().splitlines()[-1:] or ["unbekannter Fehler"]
            logger.warning(
                "[Dateien] Ziel %s nach dem Schreiben kompromittiert (rc=%s): %s",
                scrub_log(safe_dir), exit_code, scrub_log(grund[0]),
            )
            raise ZielordnerKompromittiert(
                f"Zielordner wurde waehrend des Schreibens veraendert: {grund[0]}"
            )

    def write_files_in_container(
        self,
        container_id: str,
        target_dir: str,
        files: list[tuple[str, bytes]],
        uid: int = 1000,
        gid: int = 1000,
    ) -> None:
        """Write multiple files into a container directory using a single tar archive.

        Files are owned by the agent user (uid/gid 1000) by default; see
        write_file_in_container for why.

        Nested names ("meine-app/src/index.js", as the ZIP import produces them)
        need the same care for their directories: put_archive creates every
        directory that is missing from the archive itself — as root. The agent
        could then overwrite the imported files but not add a single new one
        next to them (Permission denied on mkdir/create). So every intermediate
        directory gets an explicit entry with the agent's uid/gid first.

        Deliberate semantics for directories that already exist: Docker applies
        owner AND mode of a directory entry to an existing directory as well
        (moby pkg/archive createTarFile: Mkdir only if missing, then Lchown +
        Chmod unconditionally). An import therefore normalises the directories
        it writes INTO to agent-owned 0755 — the same treatment the files get
        (overwritten, agent-owned, 0644). Only directories on the path of a
        written file are touched, never siblings, never ``target_dir`` itself.
        That is what makes a re-import of an app fix a root-owned tree from an
        older import instead of preserving the broken state.

        Der Zielordner wird VORHER symlink-sicher angelegt und dem Agenten
        uebergeben (:meth:`prepare_target_dir`). Das steht bewusst hier und
        nicht bei den Aufrufern: ``put_archive`` loest den Zielpfad im
        Behaelter mit ``filepath.EvalSymlinks`` auf — ausdruecklich
        einschliesslich des letzten Pfadglieds (moby ``daemon/archive_unix.go``:
        "so that you can extract an archive to a symlink that points to a
        directory"). Ein vom Agenten eingetauschter Symlink lenkt den
        Schreibvorgang also wirklich um. Den Schutz bei jedem Aufrufer einzeln
        zu wiederholen hat zweimal nicht getragen (#821, #840).

        Nach ``put_archive`` wird die Kette per :meth:`_assert_target_dir_still_safe`
        nochmal nachgeprueft (#843) — schliesst das TOCTOU-Fenster zwischen
        Vorbereitung und Schreiben, siehe dort.
        """
        import io
        import tarfile

        safe_dir = self.prepare_target_dir(container_id, target_dir, uid, gid)

        container = self.client.containers.get(container_id)

        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            for directory in _directory_entries_for(name for name, _ in files):
                info = tarfile.TarInfo(name=directory)
                info.type = tarfile.DIRTYPE
                info.mode = 0o755  # normalises existing dirs too, see docstring
                info.uid = uid
                info.gid = gid
                tar.addfile(info)
            for filename, data in files:
                info = tarfile.TarInfo(name=filename)
                info.size = len(data)
                info.uid = uid
                info.gid = gid
                tar.addfile(info, io.BytesIO(data))
        tar_stream.seek(0)

        container.put_archive(safe_dir, tar_stream)
        self._assert_target_dir_still_safe(container_id, target_dir)

    def get_file_from_container(self, container_id: str, path: str) -> bytes:
        import io
        import tarfile

        container = self.client.containers.get(container_id)
        bits, _ = container.get_archive(path)
        stream = io.BytesIO(b"".join(bits))
        with tarfile.open(fileobj=stream) as tar:
            member = tar.getmembers()[0]
            f = tar.extractfile(member)
            if f is None:
                raise FileNotFoundError(f"Cannot read {path}")
            return f.read()
