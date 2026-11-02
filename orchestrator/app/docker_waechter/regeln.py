"""Was der Docker-Wächter durchlässt (P1-13 aus dem Bedrohungsmodell).

Der Orchestrator spricht Docker nur noch über den Wächter. Der Socket-Proxy davor
filtert nur nach Adressen; wer den Orchestrator übernimmt, konnte darüber einen
Container mit ``Privileged`` oder ``/`` als Mount anlegen und war root auf dem
Server. Hier steht, was ein Container, ein ``exec`` und ein Volume dürfen — genau
das, was die Plattform selbst braucht (Agenten, Hilfs- und Runner-Container),
nichts darüber hinaus.

Reine Funktionen ohne Ein- und Ausgabe: ``None`` heißt erlaubt, sonst der Grund.
"""

from __future__ import annotations

import posixpath
from typing import Any, Iterable

#: Fähigkeiten, die ein Agent-Container zusätzlich bekommt (docker_service.create_container).
ERLAUBTE_CAPS = frozenset({"CHOWN", "SETUID", "SETGID", "DAC_OVERRIDE", "FOWNER", "KILL"})
#: Namespace-Modi, die keine fremden Namespaces teilen.
_EIGENER_NAMESPACE = ("", "private", "shareable", "default")
#: Netze, die kein Host- oder Fremd-Netz sind; benannte Netze sind ebenso erlaubt.
_GESPERRTE_NETZE = ("host",)


def _liste(wert: Any) -> list:
    return list(wert) if isinstance(wert, (list, tuple)) else []


def _cap(name: Any) -> str:
    name = str(name or "").strip().upper()
    return name[4:] if name.startswith("CAP_") else name


def _unter(pfad: str, basen: Iterable[str]) -> bool:
    return any(pfad == b or pfad.startswith(b.rstrip("/") + "/") for b in basen if b)


def host_pfad_pruefen(quelle: str, ziel: str, erlaubte_host_pfade: Iterable[str]) -> str | None:
    """Ein Host-Pfad als Mount: nur aus dem Katalog bzw. unter den Second Brains,
    und nie gegen die Sperrliste (System, Docker-Socket, Ziele über Prüf-Hook)."""
    from app.core.mounts import mount_gesperrt

    roh = str(quelle or "")
    if not roh.startswith("/"):
        return f"Host-Pfad {roh!r} ist nicht absolut"
    pfad = posixpath.normpath(roh)
    grund = mount_gesperrt(pfad, ziel or "/mnt")
    if grund:
        return grund
    if not _unter(pfad, [posixpath.normpath(p) for p in erlaubte_host_pfade]):
        return f"Host-Pfad {pfad} ist nicht freigegeben"
    return None


def _binds_pruefen(binds: list, erlaubte_host_pfade: Iterable[str]) -> str | None:
    for eintrag in binds:
        teile = str(eintrag).split(":")
        quelle = teile[0]
        ziel = teile[1] if len(teile) > 1 else ""
        if quelle.startswith("/"):
            grund = host_pfad_pruefen(quelle, ziel, erlaubte_host_pfade)
            if grund:
                return grund
    return None


def _mounts_pruefen(mounts: list, erlaubte_host_pfade: Iterable[str]) -> str | None:
    for m in mounts:
        if not isinstance(m, dict):
            return "Mount in unbekannter Form"
        art = str(m.get("Type") or "volume").lower()
        if art == "bind":
            grund = host_pfad_pruefen(str(m.get("Source") or ""), str(m.get("Target") or ""),
                                      erlaubte_host_pfade)
            if grund:
                return grund
        elif art == "volume":
            treiber = (m.get("VolumeOptions") or {}).get("DriverConfig") or {}
            if treiber.get("Name") not in (None, "", "local") or treiber.get("Options"):
                return "Volume mit eigenem Treiber oder Treiber-Optionen ist gesperrt"
        elif art != "tmpfs":
            return f"Mount-Art {art!r} ist gesperrt"
    return None


def _security_opt_pruefen(optionen: list) -> str | None:
    for opt in optionen:
        text = str(opt).strip()
        schluessel, _, wert = text.replace(":", "=", 1).partition("=")
        schluessel = schluessel.lower()
        if schluessel == "no-new-privileges":
            continue
        if schluessel == "seccomp" and wert.strip().lower() not in ("unconfined", ""):
            continue
        return f"security_opt {text[:40]!r} ist gesperrt"
    return None


def container_anlegen(koerper: dict, erlaubte_host_pfade: Iterable[str]) -> str | None:
    """``POST /containers/create`` — erlaubt nur, was die Plattform selbst anlegt."""
    if not isinstance(koerper, dict):
        return "Anfrage ist kein JSON-Objekt"
    hc = koerper.get("HostConfig") or {}
    if not isinstance(hc, dict):
        return "HostConfig in unbekannter Form"
    if hc.get("Privileged"):
        return "Privileged ist gesperrt"
    fremde = sorted({_cap(c) for c in _liste(hc.get("CapAdd"))} - ERLAUBTE_CAPS)
    if fremde:
        return f"Fähigkeiten {', '.join(fremde)} sind gesperrt"
    for feld in ("Devices", "DeviceRequests", "DeviceCgroupRules"):
        if _liste(hc.get(feld)):
            return f"{feld} ist gesperrt"
    netz = str(hc.get("NetworkMode") or "")
    if netz in _GESPERRTE_NETZE or netz.startswith("container:"):
        return f"NetworkMode {netz!r} ist gesperrt"
    for feld in ("PidMode", "IpcMode", "UTSMode", "UsernsMode", "CgroupnsMode"):
        modus = str(hc.get(feld) or "").lower()
        if modus not in _EIGENER_NAMESPACE:
            return f"{feld} {modus!r} ist gesperrt"
    if hc.get("Sysctls"):
        return "Sysctls sind gesperrt"
    if hc.get("CgroupParent"):
        return "CgroupParent ist gesperrt"
    if hc.get("VolumeDriver"):
        return "VolumeDriver ist gesperrt"
    if str(hc.get("Runtime") or "runc") not in ("runc", ""):
        return f"Runtime {hc.get('Runtime')!r} ist gesperrt"
    if hc.get("VolumesFrom"):
        return "VolumesFrom ist gesperrt"
    return (_security_opt_pruefen(_liste(hc.get("SecurityOpt")))
            or _binds_pruefen(_liste(hc.get("Binds")), erlaubte_host_pfade)
            or _mounts_pruefen(_liste(hc.get("Mounts")), erlaubte_host_pfade))


def exec_anlegen(koerper: dict) -> str | None:
    """``POST /containers/{id}/exec`` — kein privilegierter Befehl."""
    if not isinstance(koerper, dict):
        return "Anfrage ist kein JSON-Objekt"
    if koerper.get("Privileged"):
        return "Privilegiertes exec ist gesperrt"
    return None


def volume_anlegen(koerper: dict) -> str | None:
    """``POST /volumes/create`` — nur lokale Volumes ohne Optionen.

    Mit ``driver_opts`` (type=none, o=bind, device=/) wäre ein benanntes Volume
    ein Host-Mount durch die Hintertür.
    """
    if not isinstance(koerper, dict):
        return "Anfrage ist kein JSON-Objekt"
    if str(koerper.get("Driver") or "local") != "local":
        return f"Volume-Treiber {koerper.get('Driver')!r} ist gesperrt"
    if koerper.get("DriverOpts"):
        return "Volume-Optionen sind gesperrt"
    return None
