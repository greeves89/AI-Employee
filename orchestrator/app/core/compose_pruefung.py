"""Prüfung der Compose-Dateien von Agenten-Apps (P0-1 aus dem Bedrohungsmodell).

Agenten-Apps starten über den Docker-Socket des Hosts. Die Compose-Datei schreibt
der Agent selbst. Ungeprüft reicht eine Zeile wie ``privileged: true`` oder
``volumes: ["/:/host"]``, und ein fehlgeleiteter Agent ist root auf dem Server.

Dieses Modul ist der EINE Weg, über den eine Compose-Datei aus einem Agenten-
oder Taskforce-Ordner ausgeführt wird (``api/docker_apps.py``,
``api/meeting_rooms.py``):

1. ``normalisieren``: ``docker compose config --format json`` in einem Runner
   OHNE Docker-Socket und ohne Netz; der Ordner nur lesend. Danach sind Variablen
   aufgelöst (``privileged: ${X}`` steht als ``true`` da), ``env_file``,
   ``extends`` und ``include`` eingemischt, Pfade absolut, Kurzformen in Langform.
   Geprüft wird also, was Docker wirklich ausführen würde.
2. ``pruefen``: Positivliste auf genau diesem Modell. Was nicht ausdrücklich
   erlaubt ist, lehnt die Prüfung ab. Dazu drei Umschreibungen:
   - Ports nur auf ``127.0.0.1`` (erreichbar ist die App über den App-Proxy),
   - relative Ordner (``./data``) werden zum Unterordner im eigenen Volume des
     Agenten. Vorher landeten sie auf dem HOST unter ``/workspace/…``, als root
     und geteilt mit jedem anderen Agenten, der denselben Pfad nutzte,
   - Container- und Netznamen bekommen das Präfix des Agenten.
3. ``ausfuehren``: Das geprüfte Modell geht über stdin an ``docker compose -f -``.
   Die Compose-Datei wird nach der Prüfung nicht wieder gelesen. Build-Kontext
   und Dockerfile schon — sie wirken aber nur im Build, nicht auf den Container.

``$`` bleibt dabei erhalten: ``config`` schreibt es als ``$$``, die zweite
Auflösung beim Start macht wieder ``$`` daraus (auf dem Pi geprüft).
"""

from __future__ import annotations

import asyncio
import json
import logging
import posixpath
import re
from dataclasses import dataclass
from typing import Any

from docker.errors import ImageNotFound
from docker.types import Mount
from fastapi import HTTPException

logger = logging.getLogger(__name__)

RUNNER_IMAGE = "docker:cli"
#: Unter diesem Schlüssel hängt das Volume des Agenten im geprüften Modell.
WORKSPACE_VOLUME = "aie_workspace"
AGENT_UID = "1000:1000"
RUNNER_NETZ = "ai-employee-network"
RUNNER_SPEICHER = "1g"
RUNNER_PROZESSE = 512
RUNNER_CPUS = 2_000_000_000
#: Bauen darf dauern (npm, pip, große Abbilder); alles andere ist schnell.
ZEITLIMIT_START = 1800

#: Ein Umgebungseintrag darf bei ``execve`` höchstens 128 KiB lang sein.
_MAX_MODELL_BYTES = 120_000

#: Die Fähigkeiten, die Docker jedem Container ohnehin gibt. ``cap_add`` mit
#: einer davon ändert nichts; alles darüber hinaus ist gesperrt.
_STANDARD_CAPS = frozenset({
    "CHOWN", "DAC_OVERRIDE", "FSETID", "FOWNER", "MKNOD", "NET_RAW", "SETGID", "SETUID",
    "SETFCAP", "SETPCAP", "NET_BIND_SERVICE", "SYS_CHROOT", "KILL", "AUDIT_WRITE",
})

#: Dienst-Schlüssel, die ohne weitere Prüfung durchgehen.
_DIENST_FREI = frozenset({
    "annotations", "attach", "blkio_config", "cap_drop", "command", "cpu_count",
    "cpu_percent", "cpu_period", "cpu_quota", "cpu_shares", "cpus", "cpuset",
    "depends_on", "develop", "dns", "dns_opt", "dns_search", "domainname", "entrypoint",
    "environment", "expose", "extra_hosts", "group_add", "healthcheck", "hostname",
    "image", "init", "links", "mac_address", "mem_limit", "mem_reservation",
    "mem_swappiness", "pids_limit", "platform", "profiles",
    "pull_policy", "pull_refresh_after", "read_only", "restart", "scale", "shm_size",
    "stdin_open", "stop_grace_period", "stop_signal", "sysctls", "tmpfs", "tty",
    "ulimits", "user", "working_dir",
})

#: Dienst-Schlüssel, die hier geprüft oder umgeschrieben werden.
#: ``memswap_limit`` fällt still weg (Docker nimmt dann das Doppelte von mem_limit).
_DIENST_GEPRUEFT = frozenset({
    "memswap_limit", "build", "cap_add", "container_name", "deploy", "env_file", "ipc", "labels",
    "logging", "network_mode", "networks", "oom_score_adj", "pid", "ports",
    "post_start", "pre_stop", "privileged", "security_opt", "volumes",
})

#: Warum ein Schlüssel gesperrt ist. Steht so in der Ablehnung, damit der Agent
#: weiß, was er stattdessen tun kann.
_DIENST_GESPERRT = {
    "cgroup": "eigene cgroup des Hosts",
    "cgroup_parent": "eigene cgroup des Hosts",
    "configs": "liest Dateien vom Server – Inhalte über Umgebungsvariablen oder Dateien im Projekt übergeben",
    "cpu_rt_period": "Echtzeit-Planung am Host",
    "cpu_rt_runtime": "Echtzeit-Planung am Host",
    "credential_spec": "Zugangsdaten des Hosts",
    "device_cgroup_rules": "Zugriff auf Geräte des Servers",
    "devices": "Zugriff auf Geräte des Servers",
    "external_links": "Verbindung zu fremden Containern",
    "gpus": "Zugriff auf Geräte des Servers",
    "isolation": "eigene Isolationsart",
    "models": "nicht unterstützt",
    "oom_kill_disable": "kann den Server lahmlegen",
    "provider": "führt ein Programm im Orchestrator aus",
    "runtime": "eigene Container-Laufzeit",
    "secrets": "liest Dateien vom Server – Inhalte über Umgebungsvariablen oder Dateien im Projekt übergeben",
    "storage_opt": "Speichertreiber des Hosts",
    "use_api_socket": "Zugriff auf Docker des Servers",
    "userns_mode": "Benutzer-Namensraum des Hosts",
    "uts": "Namensraum des Hosts",
    "volumes_from": "Ordner fremder Container",
}

_BUILD_FREI = frozenset({
    "args", "cache_from", "dockerfile_inline", "extra_hosts", "labels", "no_cache",
    "platforms", "provenance", "pull", "sbom", "shm_size", "target", "ulimits",
})
#: „tags“ fällt weg: Ein Zusatzname könnte ein Abbild der Plattform überschreiben.
_BUILD_GEPRUEFT = frozenset({"context", "dockerfile", "additional_contexts", "network", "privileged", "tags"})

_NETZ_FREI = frozenset({"name", "driver", "external", "internal", "attachable", "labels", "ipam", "enable_ipv6", "enable_ipv4"})
_VOLUME_FREI = frozenset({"name", "external", "driver", "labels"})
_DIENST_NETZ_FREI = frozenset({"aliases", "priority", "gw_priority", "mac_address", "interface_name"})
_LOG_TREIBER = frozenset({None, "json-file", "local", "none"})
_DEPLOY_FREI = frozenset({
    "mode", "replicas", "labels", "update_config", "rollback_config", "restart_policy",
    "placement", "endpoint_mode", "resources",
})
_RESSOURCEN_FREI = frozenset({"cpus", "memory", "pids"})
#: Standardgrenzen je Dienst, wenn die App selbst keine setzt.
APP_PROZESSE = 4096
APP_PROZESSE_MAX = 8192
APP_SPEICHER = "4g"
APP_SPEICHER_MAX = 8 * 1024**3
APP_KOPIEN_MAX = 5

_NO_NEW_PRIVILEGES = re.compile(r"^no-new-privileges([:=]true)?$")
_NAME_UNSICHER = re.compile(r"[^a-zA-Z0-9_.-]")


class ComposeAbgelehnt(Exception):
    """Die Compose-Datei enthält Gesperrtes. ``verstoesse`` sagt was, je Zeile."""

    def __init__(self, verstoesse: list[str]):
        super().__init__("; ".join(verstoesse))
        self.verstoesse = verstoesse


@dataclass(frozen=True)
class Quelle:
    """Wo das Projekt liegt und wie der Runner es sieht.

    ``volume`` + ``teilpfad`` sind der Ordner im Docker-Volume, ``sicht`` ist der
    Pfad, unter dem der Runner ihn einbindet (und unter dem ``config`` alle Pfade
    absolut schreibt). ``praefix`` ist ``agent-<id8>-``: Jeder Name, den die App
    am Host anlegt, beginnt damit.
    """

    volume: str
    sicht: str
    praefix: str
    teilpfad: str = ""
    #: Nur Pfade darunter sind erlaubt (Standard: alles unter ``sicht``).
    bereich: str = ""
    #: Ordner als Mount in die App? Nein im geteilten Volume (siehe meeting_rooms).
    ordner_erlaubt: bool = True

    def mount(self, *, nur_lesen: bool) -> Mount:
        m = Mount(target=self.sicht, source=self.volume, type="volume", read_only=nur_lesen)
        if self.teilpfad:
            m["VolumeOptions"] = {"Subpath": self.teilpfad}
        return m

    def im_volume(self, pfad: str) -> str | None:
        """Runner-Pfad → Unterordner im Volume, oder None, wenn er nicht darin liegt."""
        pfad = posixpath.normpath(pfad)
        bereich = self.bereich or self.sicht
        if pfad != bereich and not pfad.startswith(bereich.rstrip("/") + "/"):
            return None
        if pfad == self.sicht:
            rest = ""
        elif pfad.startswith(self.sicht + "/"):
            rest = pfad[len(self.sicht) + 1:]
        else:
            return None
        if not self.teilpfad:
            return rest
        return posixpath.join(self.teilpfad, rest) if rest else self.teilpfad


def fuer_agent(agent_id: str, volume: str) -> Quelle:
    """Der Workspace eines Agenten, wie ihn auch der Agent selbst sieht."""
    return Quelle(volume=volume, sicht="/workspace", praefix=f"agent-{agent_id[:8]}-")


# ── Runner ────────────────────────────────────────────────────────────────────

def _runner_abbild(docker) -> str:
    """Kennung des Runner-Abbilds — nur eines, das aus der Registry kommt.

    Ein lokal gebautes Abbild hat keine ``RepoDigests``. Hiesse es ``docker:cli``,
    liefe sonst Code eines Agenten mit dem Docker-Socket. Die Prüfung schreibt
    Namen gebauter Abbilder zwar auf das Präfix des Agenten um; das hier ist die
    zweite Linie, falls ein solches Abbild schon vorher da war.
    """
    try:
        abbild = docker.client.images.get(RUNNER_IMAGE)
        if abbild.attrs.get("RepoDigests"):
            return abbild.id
        logger.warning("Runner-Abbild %s ist lokal gebaut — wird neu aus der Registry geholt", RUNNER_IMAGE)
    except ImageNotFound:
        pass
    try:
        name, tag = RUNNER_IMAGE.split(":", 1)
        return docker.client.images.pull(name, tag=tag).id
    except Exception as e:  # noqa: BLE001 — ohne sauberes Abbild läuft gar nichts
        raise HTTPException(status_code=503, detail=f"Das Abbild '{RUNNER_IMAGE}' ist nicht verfügbar: {e}") from None


def _runner(docker, befehl: list[str], *, mounts: list, socket: bool, netz: str | None,
            user: str | None = None, umgebung: dict | None = None,
            nur_stdout: bool = False, zeitlimit: int = 120) -> tuple[int, str]:
    """Einen kurzlebigen ``docker:cli``-Container ausführen. (Exit-Code, Ausgabe).

    Mit Speicher-, Prozess- und Zeitgrenze: Eine Compose-Datei voller YAML-Anker
    oder ein hängender Build darf weder den Server noch den Aufrufer blockieren.
    ``nur_stdout``: Warnungen auf stderr bleiben aus der Ausgabe (``config`` liefert
    dort JSON). Im Fehlerfall kommt stderr trotzdem mit."""
    volumes = {"/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"}} if socket else {}
    kwargs: dict[str, Any] = {}
    if netz:
        kwargs["network"] = netz
    else:
        kwargs["network_mode"] = "none"
    if user:
        kwargs["user"] = user
    c = docker.client.containers.run(
        image=_runner_abbild(docker), command=befehl, volumes=volumes, mounts=mounts,
        environment=umgebung or {}, detach=True, mem_limit=RUNNER_SPEICHER,
        pids_limit=RUNNER_PROZESSE, nano_cpus=RUNNER_CPUS, **kwargs,
    )
    try:
        try:
            code = int((c.wait(timeout=zeitlimit) or {}).get("StatusCode", 1))
        except Exception:  # noqa: BLE001 — Zeitüberschreitung (requests) oder Verbindungsabbruch
            try:
                c.kill()
            except Exception:
                logger.debug("Runner nach Zeitüberschreitung schon beendet", exc_info=True)
            return 124, f"Zeitüberschreitung nach {zeitlimit} s"
        roh = c.logs(stdout=True, stderr=(code != 0 or not nur_stdout))
        return code, roh.decode("utf-8", "replace") if isinstance(roh, bytes) else str(roh)
    finally:
        try:
            c.remove(force=True)
        except Exception:
            logger.debug("Runner nicht entfernt", exc_info=True)


def normalisieren(docker, quelle: Quelle, projekt: str, compose_datei: str) -> dict:
    """``docker compose config`` ohne Socket, ohne Netz, als uid 1000, Ordner nur lesend."""
    verzeichnis = posixpath.dirname(compose_datei)
    code, ausgabe = _runner(
        docker,
        ["docker", "compose", "-p", projekt, "--project-directory", verzeichnis,
         "-f", compose_datei, "config", "--format", "json"],
        mounts=[quelle.mount(nur_lesen=True)], socket=False, netz=None, user=AGENT_UID,
        umgebung={"HOME": "/tmp"}, nur_stdout=True,
    )
    if code != 0:
        raise HTTPException(status_code=422, detail=f"Die Compose-Datei ist ungültig: {ausgabe[-1500:]}")
    try:
        modell = json.loads(ausgabe)
    except ValueError:
        modell = None
    if not isinstance(modell, dict):
        raise HTTPException(status_code=422, detail="Die Compose-Datei ließ sich nicht auswerten.")
    return modell


def ausfuehren(docker, quelle: Quelle, projekt: str, modell: dict | None,
               befehl: list[str]) -> tuple[int, str]:
    """Compose mit dem GEPRÜFTEN Modell ausführen (über stdin, nicht aus einer Datei).

    ``modell=None`` nur für Befehle, die ohne Datei auskommen (``down``): Compose
    findet die Container dann über die Projektbezeichnung.
    """
    if modell is None:
        return _runner(docker, ["docker", "compose", "-p", projekt, *befehl],
                       mounts=[], socket=True, netz=RUNNER_NETZ, zeitlimit=300)
    text = json.dumps(modell)
    if len(text.encode()) > _MAX_MODELL_BYTES:
        raise HTTPException(status_code=422, detail="Die Compose-Datei ist zu groß (mehr als 120 KB nach dem Auflösen).")
    # Der Ordner hängt nur lesend: Compose braucht ihn für die Build-Kontexte.
    return _runner(
        docker,
        ["sh", "-c", 'printf %s "$AIE_COMPOSE" | docker compose -p "$AIE_PROJEKT" -f - "$@"', "sh", *befehl],
        mounts=[quelle.mount(nur_lesen=True)], socket=True, netz=RUNNER_NETZ,
        umgebung={"AIE_COMPOSE": text, "AIE_PROJEKT": projekt}, zeitlimit=ZEITLIMIT_START,
    )


def ordner_anlegen(docker, quelle: Quelle, pfade: list[str]) -> None:
    """Fehlende Ordner für umgeschriebene Mounts anlegen, als uid 1000 und ohne Socket.

    Docker bindet einen Unterordner nur ein, wenn es ihn gibt; eine Datei, die
    fehlt, wird wie bei Compose ein Ordner.
    """
    if not pfade:
        return
    code, ausgabe = _runner(
        docker,
        ["sh", "-c", 'for p; do [ -e "$p" ] || [ -L "$p" ] || mkdir -p -- "$p"; done', "sh", *pfade],
        mounts=[quelle.mount(nur_lesen=False)], socket=False, netz=None, user=AGENT_UID,
    )
    if code != 0:
        logger.warning("Ordner für App-Mounts nicht angelegt: %s", ausgabe[-300:])


# ── Prüfung ───────────────────────────────────────────────────────────────────

def _praefixiert(quelle: Quelle, name: str) -> str:
    return name if name.startswith(quelle.praefix) else quelle.praefix + _NAME_UNSICHER.sub("-", name)


def _cap(name: Any) -> str:
    s = str(name).upper()
    return s.removeprefix("CAP_")


def pruefen(modell: dict, quelle: Quelle, projekt: str) -> tuple[dict, list[str], list[str]]:
    """(geprüftes Modell, Hinweise zu Umschreibungen, anzulegende Ordner).

    Wirft ``ComposeAbgelehnt`` mit ALLEN Verstößen auf einmal: Der Agent soll die
    Datei in einem Durchgang reparieren können.
    """
    fehler: list[str] = []
    hinweise: list[str] = []
    ordner: list[str] = []
    neu: dict[str, Any] = {"name": projekt}

    for schluessel in modell:
        if schluessel in ("name", "services", "networks", "volumes") or str(schluessel).startswith("x-"):
            continue
        if schluessel in ("version", "include"):
            continue
        fehler.append(f"„{schluessel}“ ist für Agenten-Apps gesperrt (liest Dateien vom Server).")

    netze = _netze_pruefen(modell.get("networks") or {}, quelle, fehler, hinweise)
    if netze:
        neu["networks"] = netze
    volumes = _volumes_pruefen(modell.get("volumes") or {}, quelle, fehler)
    workspace_genutzt = False

    dienste = modell.get("services") or {}
    if not isinstance(dienste, dict) or not dienste:
        fehler.append("Die Compose-Datei enthält keine Dienste.")
        dienste = {}
    # Ein gebautes Abbild bekommt den Namen aus „image“. Hiesse es „docker:cli“
    # oder „ai-employee-agent:latest“, ueberschriebe der Build ein Abbild, das die
    # Plattform selbst startet — mit Docker-Socket bzw. als jeder neue Agent.
    gebaut = {str(d["image"]) for d in dienste.values()
              if isinstance(d, dict) and d.get("build") is not None and d.get("image")}
    abbilder = {alt: _abbildname(quelle, alt) for alt in gebaut if not alt.startswith(quelle.praefix)}
    neu_dienste: dict[str, Any] = {}
    for name, dienst in dienste.items():
        if not isinstance(dienst, dict):
            fehler.append(f"Dienst „{name}“: ungültig.")
            continue
        d, nutzt_ws = _dienst_pruefen(name, dienst, quelle, set(dienste), set(volumes),
                                      netze, fehler, hinweise, ordner, abbilder)
        workspace_genutzt = workspace_genutzt or nutzt_ws
        neu_dienste[name] = d
    neu["services"] = neu_dienste

    if workspace_genutzt:
        volumes[WORKSPACE_VOLUME] = {"external": True, "name": quelle.volume}
    if volumes:
        neu["volumes"] = volumes

    if fehler:
        raise ComposeAbgelehnt(fehler)
    return neu, hinweise, ordner


def _netze_pruefen(netze: dict, quelle: Quelle, fehler: list[str], hinweise: list[str]) -> dict:
    neu: dict[str, Any] = {}
    for key, n in netze.items():
        n = dict(n or {})
        for k in list(n):
            if k not in _NETZ_FREI:
                fehler.append(f"Netz „{key}“: „{k}“ ist gesperrt.")
        if n.get("driver") not in (None, "bridge"):
            fehler.append(f"Netz „{key}“: Treiber „{n.get('driver')}“ ist gesperrt, erlaubt ist nur „bridge“.")
        name = str(n.get("name") or key)
        if not name.startswith(quelle.praefix):
            # Auch bei „external“: Zwei Projekte desselben Agenten teilen sich ein
            # Netz weiter, ein fremdes Netz (Plattform, andere Nutzer) ist so nie gemeint.
            n["name"] = _praefixiert(quelle, name)
            if n.get("external"):
                hinweise.append(f"Netz „{key}“: „{name}“ heißt am Server „{n['name']}“; fremde Netze sind nicht erreichbar.")
        ipam = n.get("ipam") or {}
        if ipam.get("config") or ipam.get("driver") not in (None, "default"):
            # Eigene Adressbereiche können sich mit dem Firmennetz überschneiden.
            n["ipam"] = {}
            hinweise.append(f"Netz „{key}“: eigener Adressbereich entfernt, Docker vergibt ihn selbst.")
        neu[key] = n
    return neu


def _volumes_pruefen(volumes: dict, quelle: Quelle, fehler: list[str]) -> dict:
    neu: dict[str, Any] = {}
    for key, v in volumes.items():
        if key == WORKSPACE_VOLUME:
            fehler.append(f"Volume „{key}“: Der Name ist für die Plattform reserviert.")
            continue
        v = dict(v or {})
        for k in v:
            if k not in _VOLUME_FREI:
                fehler.append(f"Volume „{key}“: „{k}“ ist gesperrt (kann Ordner des Servers einbinden).")
        if v.get("driver") not in (None, "local"):
            fehler.append(f"Volume „{key}“: Treiber „{v.get('driver')}“ ist gesperrt.")
        name = str(v.get("name") or key)
        if not name.startswith(quelle.praefix):
            fehler.append(
                f"Volume „{key}“: Der Name „{name}“ muss mit „{quelle.praefix}“ beginnen "
                "(fremde Volumes sind gesperrt). Ohne „name:“ vergibt Compose ihn selbst."
            )
        neu[key] = v
    return neu


def _dienst_pruefen(name: str, dienst: dict, quelle: Quelle, dienste: set, volumes: set,
                    netze: dict, fehler: list[str], hinweise: list[str],
                    ordner: list[str], abbilder: dict[str, str]) -> tuple[dict, bool]:
    wo = f"Dienst „{name}“"
    d: dict[str, Any] = {}
    workspace_genutzt = False

    for k, v in dienst.items():
        if k in _DIENST_FREI:
            d[k] = v
        elif k in _DIENST_GESPERRT:
            if v not in (None, False, [], {}, ""):
                fehler.append(f"{wo}: „{k}“ ist gesperrt ({_DIENST_GESPERRT[k]}).")
        elif k in _DIENST_GEPRUEFT or k.startswith("x-"):
            continue
        else:
            fehler.append(f"{wo}: „{k}“ ist für Agenten-Apps nicht freigegeben.")

    if dienst.get("privileged"):
        fehler.append(f"{wo}: „privileged“ ist gesperrt (volle Rechte auf dem Server).")
    for hook in ("post_start", "pre_stop"):
        for h in dienst.get(hook) or []:
            if isinstance(h, dict) and h.get("privileged"):
                fehler.append(f"{wo}: „{hook}“ mit „privileged“ ist gesperrt.")
        if dienst.get(hook):
            d[hook] = dienst[hook]

    caps = [_cap(c) for c in dienst.get("cap_add") or []]
    zu_viel = [c for c in caps if c not in _STANDARD_CAPS]
    if zu_viel:
        fehler.append(f"{wo}: „cap_add: {', '.join(zu_viel)}“ ist gesperrt (zusätzliche Rechte am Server).")
    elif caps:
        d["cap_add"] = caps

    for opt in dienst.get("security_opt") or []:
        if not _NO_NEW_PRIVILEGES.match(str(opt)):
            fehler.append(f"{wo}: „security_opt: {opt}“ ist gesperrt, erlaubt ist nur „no-new-privileges“.")
    if dienst.get("security_opt"):
        d["security_opt"] = dienst["security_opt"]

    if (dienst.get("oom_score_adj") or 0) < 0:
        fehler.append(f"{wo}: negatives „oom_score_adj“ ist gesperrt.")
    elif "oom_score_adj" in dienst:
        d["oom_score_adj"] = dienst["oom_score_adj"]

    for ns in ("pid", "ipc"):
        wert = dienst.get(ns)
        if wert in (None, "") or (ns == "ipc" and wert in ("private", "shareable", "none")):
            if wert:
                d[ns] = wert
        elif str(wert).startswith("service:") and str(wert)[8:] in dienste:
            d[ns] = wert
        else:
            fehler.append(f"{wo}: „{ns}: {wert}“ ist gesperrt (Namensraum des Servers oder fremder Container).")

    nm = dienst.get("network_mode")
    if nm == "bridge":
        # Das Standardnetz von Docker teilen sich alle Container ohne eigenes Netz.
        # Ohne Angabe haengt Compose den Dienst beim Start an das Netz des Projekts.
        hinweise.append(f"{wo}: „network_mode: bridge“ entfernt, die App bekommt ihr eigenes Netz.")
    elif nm not in (None, ""):
        if nm == "none" or (str(nm).startswith("service:") and str(nm)[8:] in dienste):
            d["network_mode"] = nm
        else:
            fehler.append(f"{wo}: „network_mode: {nm}“ ist gesperrt. Ohne „network_mode“ bekommt die App ihr eigenes Netz.")

    labels = dienst.get("labels") or {}
    if isinstance(labels, dict):
        fremde = [k for k in labels if str(k).startswith("com.docker.")]
        if fremde:
            fehler.append(f"{wo}: Labels mit „com.docker.“ setzt nur Docker selbst ({', '.join(fremde)}).")
        if labels:
            d["labels"] = labels

    log = dienst.get("logging")
    if log:
        if log.get("driver") not in _LOG_TREIBER:
            fehler.append(f"{wo}: Log-Treiber „{log.get('driver')}“ ist gesperrt, erlaubt sind json-file und local.")
        d["logging"] = log

    deploy = dienst.get("deploy")
    if deploy:
        _deploy_pruefen(wo, deploy, fehler)
        d["deploy"] = deploy

    if dienst.get("build") is not None:
        d["build"] = _build_pruefen(wo, dienst["build"], quelle, fehler)

    for datei in dienst.get("env_file") or []:
        pfad = datei.get("path") if isinstance(datei, dict) else datei
        if quelle.im_volume(str(pfad)) is None:
            fehler.append(f"{wo}: „env_file: {pfad}“ liegt nicht im Projekt.")
    if dienst.get("env_file"):
        d["env_file"] = dienst["env_file"]

    if dienst.get("ports"):
        d["ports"] = _ports_umschreiben(dienst["ports"])

    if dienst.get("volumes"):
        d["volumes"], workspace_genutzt = _mounts_pruefen(wo, dienst["volumes"], quelle, volumes,
                                                          fehler, hinweise, ordner)

    if "networks" in dienst and "network_mode" not in d:
        d["networks"] = _dienst_netze(wo, dienst["networks"] or {}, netze, fehler, hinweise)

    if dienst.get("container_name"):
        alt = str(dienst["container_name"])
        neu_name = _praefixiert(quelle, alt)
        d["container_name"] = neu_name
        if neu_name != alt:
            # Andere Dienste erreichen ihn weiter unter dem alten Namen.
            for netz, cfg in (d.get("networks") or {}).items():
                cfg = dict(cfg or {})
                cfg["aliases"] = sorted(set(cfg.get("aliases") or []) | {alt})
                d["networks"][netz] = cfg
            hinweise.append(f"{wo}: Containername „{alt}“ heißt am Server „{neu_name}“, im App-Netz weiter „{alt}“.")

    abbild = str(dienst.get("image") or "")
    if abbild in abbilder:
        d["image"] = abbilder[abbild]
        hinweise.append(f"{wo}: Das gebaute Abbild heißt „{abbilder[abbild]}“ statt „{abbild}“.")

    _grenzen(wo, d, fehler, hinweise)
    return d, workspace_genutzt


def _bytes(wert: Any) -> int:
    """„512m“, „1G“, 536870912 → Bytes. Unlesbar zählt als unbegrenzt (wird gekappt)."""
    if isinstance(wert, (int, float)):
        return int(wert)
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([bkmgt]?)i?b?\s*", str(wert).lower())
    if not m:
        return APP_SPEICHER_MAX + 1
    return int(float(m.group(1)) * 1024 ** "bkmgt".index(m.group(2) or "b"))


def _abbildname(quelle: Quelle, name: str) -> str:
    """„meine/app:1.0“ → „agent-<id8>-meine-app:1.0“ (ein Name ohne Registry)."""
    name = name.lower()
    repo, _, tag = name.rpartition(":") if ":" in name.rsplit("/", 1)[-1] else (name, "", "latest")
    repo = re.sub(r"[^a-z0-9._-]+", "-", repo).strip("-.") or "app"
    tag = re.sub(r"[^a-z0-9_.-]", "-", tag)[:128] or "latest"
    return f"{quelle.praefix}{repo}:{tag}"


def _grenzen(wo: str, d: dict, fehler: list[str], hinweise: list[str]) -> None:
    """Obergrenzen, damit eine App den Server nicht lahmlegt (Prozesse, Speicher, Kopien)."""
    pids = d.get("pids_limit")
    if pids in (None, "") or int(pids) <= 0 or int(pids) > APP_PROZESSE_MAX:
        if pids not in (None, ""):
            hinweise.append(f"{wo}: „pids_limit“ auf {APP_PROZESSE} begrenzt.")
        d["pids_limit"] = APP_PROZESSE
    limits = (((d.get("deploy") or {}).get("resources") or {}).get("limits") or {})
    if not d.get("mem_limit") and not limits.get("memory"):
        d["mem_limit"] = APP_SPEICHER
    for wo_wert, halter, schluessel in ((d.get("mem_limit"), d, "mem_limit"), (limits.get("memory"), limits, "memory")):
        if wo_wert not in (None, "") and _bytes(wo_wert) > APP_SPEICHER_MAX:
            halter[schluessel] = APP_SPEICHER_MAX
            hinweise.append(f"{wo}: Speicher auf {APP_SPEICHER_MAX // 1024**3} GB begrenzt.")

    for wert, schluessel in ((d.get("scale"), "scale"), ((d.get("deploy") or {}).get("replicas"), "deploy.replicas")):
        if wert not in (None, "") and int(wert) > APP_KOPIEN_MAX:
            fehler.append(f"{wo}: „{schluessel}: {wert}“ ist zu viel, erlaubt sind höchstens {APP_KOPIEN_MAX}.")


def _deploy_pruefen(wo: str, deploy: dict, fehler: list[str]) -> None:
    for k in deploy:
        if k not in _DEPLOY_FREI:
            fehler.append(f"{wo}: „deploy.{k}“ ist gesperrt.")
    for art, res in ((deploy.get("resources") or {}).items()):
        for k in (res or {}):
            if k not in _RESSOURCEN_FREI:
                fehler.append(f"{wo}: „deploy.resources.{art}.{k}“ ist gesperrt (Geräte des Servers).")


def _build_pruefen(wo: str, build: Any, quelle: Quelle, fehler: list[str]) -> Any:
    if not isinstance(build, dict):
        fehler.append(f"{wo}: „build“ ist ungültig.")
        return build
    for k, v in build.items():
        if k in _BUILD_FREI or k in _BUILD_GEPRUEFT:
            continue
        if v not in (None, False, [], {}, ""):
            fehler.append(f"{wo}: „build.{k}“ ist gesperrt (Zugriff auf Server, Schlüssel oder Netz des Hosts).")
    ctx = str(build.get("context") or "")
    if quelle.im_volume(ctx) is None:
        fehler.append(f"{wo}: Der Build-Kontext „{ctx}“ liegt nicht im Projekt.")
    df = build.get("dockerfile")
    if df and str(df).startswith("/") and quelle.im_volume(str(df)) is None:
        fehler.append(f"{wo}: Das Dockerfile „{df}“ liegt nicht im Projekt.")
    for key, ziel in (build.get("additional_contexts") or {}).items():
        ziel = str(ziel)
        if not (ziel.startswith(("docker-image://", "service:")) or quelle.im_volume(ziel) is not None):
            fehler.append(f"{wo}: Der Build-Kontext „{key}“ liegt nicht im Projekt.")
    if build.get("network") not in (None, "default", "none"):
        fehler.append(f"{wo}: „build.network: {build.get('network')}“ ist gesperrt.")
    if build.get("privileged"):
        fehler.append(f"{wo}: „build.privileged“ ist gesperrt.")
    return {k: v for k, v in build.items() if (k in _BUILD_FREI or k in _BUILD_GEPRUEFT) and k != "tags"}


def _ports_umschreiben(ports: list) -> list:
    """Jeder Port nur auf 127.0.0.1 und mit freiem Host-Port. Die App ist über den
    App-Proxy erreichbar; ein fester Host-Port würde beim zweiten Start kollidieren
    und wäre aus dem ganzen Netz erreichbar."""
    neu = []
    for p in ports:
        if not isinstance(p, dict) or p.get("target") in (None, ""):
            continue
        eintrag = {"target": p["target"], "host_ip": "127.0.0.1", "protocol": p.get("protocol") or "tcp"}
        for k in ("app_protocol", "name"):
            if p.get(k):
                eintrag[k] = p[k]
        neu.append(eintrag)
    return neu


def _mounts_pruefen(wo: str, mounts: list, quelle: Quelle, volumes: set, fehler: list[str],
                    hinweise: list[str], ordner: list[str]) -> tuple[list, bool]:
    neu = []
    workspace_genutzt = False
    for m in mounts:
        if not isinstance(m, dict):
            fehler.append(f"{wo}: Mount „{m}“ ist ungültig.")
            continue
        art = m.get("type")
        ziel = m.get("target")
        if art == "tmpfs":
            neu.append(m)
        elif art == "volume":
            quelle_name = m.get("source")
            sub = (m.get("volume") or {}).get("subpath") or ""
            if quelle_name and quelle_name not in volumes:
                fehler.append(f"{wo}: Volume „{quelle_name}“ ist nicht in dieser Datei angelegt.")
            elif ".." in str(sub).split("/"):
                fehler.append(f"{wo}: Unterordner „{sub}“ ist ungültig.")
            else:
                neu.append(m)
        elif art == "bind":
            src = str(m.get("source") or "")
            teil = quelle.im_volume(src)
            if teil is not None and not quelle.ordner_erlaubt:
                fehler.append(f"{wo}: Ordner aus dem Projekt („{src}“) sind hier nicht möglich, nur benannte Volumes.")
                continue
            if teil is None:
                fehler.append(
                    f"{wo}: Der Ordner „{src}“ vom Server ist gesperrt. Erlaubt sind Ordner im Projekt "
                    "(z. B. „./data:/data“) und benannte Volumes."
                )
                continue
            eintrag: dict[str, Any] = {"type": "volume", "source": WORKSPACE_VOLUME, "target": ziel}
            if teil:
                eintrag["volume"] = {"subpath": teil}
            if m.get("read_only"):
                eintrag["read_only"] = True
            neu.append(eintrag)
            ordner.append(posixpath.normpath(src))
            workspace_genutzt = True
            hinweise.append(f"{wo}: „{src}“ liegt im eigenen Workspace, nicht auf dem Server.")
        else:
            fehler.append(f"{wo}: Mount-Art „{art}“ ist gesperrt.")
    return neu, workspace_genutzt


def _dienst_netze(wo: str, netze_dienst: Any, netze: dict, fehler: list[str],
                  hinweise: list[str]) -> dict:
    if isinstance(netze_dienst, list):
        netze_dienst = {n: None for n in netze_dienst}
    neu: dict[str, Any] = {}
    for netz, cfg in netze_dienst.items():
        if netz not in netze:
            fehler.append(f"{wo}: Netz „{netz}“ ist nicht in dieser Datei angelegt.")
            continue
        cfg = dict(cfg or {})
        for k in list(cfg):
            if k in ("ipv4_address", "ipv6_address"):
                cfg.pop(k)
                hinweise.append(f"{wo}: feste Adresse im Netz „{netz}“ entfernt.")
            elif k not in _DIENST_NETZ_FREI:
                fehler.append(f"{wo}: „networks.{netz}.{k}“ ist gesperrt.")
        neu[netz] = cfg or None
    return neu


# ── Ein Aufruf für die Aufrufer ───────────────────────────────────────────────

async def vorbereiten(docker, quelle: Quelle, projekt: str, compose_datei: str, *,
                      agent_id: str | None) -> tuple[dict, list[str]]:
    """Normalisieren, prüfen, Ordner anlegen. Ablehnung: HTTP 422 + Prüfprotokoll."""
    modell = await asyncio.to_thread(normalisieren, docker, quelle, projekt, compose_datei)
    try:
        geprueft, hinweise, ordner = pruefen(modell, quelle, projekt)
    except ComposeAbgelehnt as e:
        await _ablehnung_protokollieren(agent_id, compose_datei, e.verstoesse)
        raise HTTPException(status_code=422, detail=ablehnungstext(e.verstoesse)) from None
    await asyncio.to_thread(ordner_anlegen, docker, quelle, ordner)
    return geprueft, hinweise


def ablehnungstext(verstoesse: list[str]) -> str:
    return (
        "Die App wurde nicht gestartet. Diese Einstellungen sind für Agenten-Apps gesperrt, "
        "weil sie Zugriff auf den Server geben würden:\n- " + "\n- ".join(verstoesse)
        + "\nErlaubt sind Ordner im Projekt (./data), benannte Volumes, Ports und Umgebungsvariablen."
    )


async def _ablehnung_protokollieren(agent_id: str | None, compose_datei: str, verstoesse: list[str]) -> None:
    """Ins Prüfprotokoll. Ein Fehler hier darf die Ablehnung nie verhindern."""
    try:
        from app.core.audit import protokolliere
        from app.db.session import async_session_factory
        from app.models.audit_log import AuditEventType
        async with async_session_factory() as db:
            await protokolliere(
                db, AuditEventType.APP_BLOCKED, agent_id=agent_id,
                command=f"Compose abgelehnt: {compose_datei}", outcome="blocked",
                meta={"verstoesse": verstoesse[:20]},
            )
            await db.commit()
    except Exception:
        logger.warning("Ablehnung einer Compose-Datei nicht protokolliert", exc_info=True)
