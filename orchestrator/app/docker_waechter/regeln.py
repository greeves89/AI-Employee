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


def klein(wert: Any) -> Any:
    """Schlüssel klein, rekursiv — Docker (Go ``encoding/json``) liest Feldnamen ohne
    Rücksicht auf Groß-/Kleinschreibung. ``{"hostconfig": {"privileged": true}}``
    wirkt dort wie ``Privileged``; geprüft wird deshalb auf genau dieser Form."""
    if isinstance(wert, dict):
        for k in wert:
            if not str(k).isascii():
                # Go faltet auch „K“ (Kelvin) zu k und „ſ“ zu s: „Hoſtconfig“ wäre dort HostConfig.
                raise ValueError(f"Schlüssel mit Nicht-ASCII-Zeichen {k!r}")
        return {str(k).lower(): klein(v) for k, v in wert.items()}
    if isinstance(wert, list):
        return [klein(v) for v in wert]
    return wert


def ohne_dubletten(paare: list) -> dict:
    """``object_pairs_hook``: zwei Schlüssel, die sich nur in der Schreibung
    unterscheiden, sind mehrdeutig — welcher gilt, entschiede Docker anders als wir."""
    gesehen = set()
    for k, _ in paare:
        if not str(k).isascii():
            raise ValueError(f"Schlüssel mit Nicht-ASCII-Zeichen {k!r}")
        if str(k).lower() in gesehen:
            raise ValueError(f"doppelter Schlüssel {k!r}")
        gesehen.add(str(k).lower())
    return dict(paare)


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
        art = str(m.get("type") or "volume").lower()
        if art == "bind":
            grund = host_pfad_pruefen(str(m.get("source") or ""), str(m.get("target") or ""),
                                      erlaubte_host_pfade)
            if grund:
                return grund
        elif art == "volume":
            treiber = (m.get("volumeoptions") or {}).get("driverconfig") or {}
            if treiber.get("name") not in (None, "", "local") or treiber.get("options"):
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
    koerper = klein(koerper)
    hc = koerper.get("hostconfig") or {}
    if not isinstance(hc, dict):
        return "HostConfig in unbekannter Form"
    if hc.get("privileged"):
        return "Privileged ist gesperrt"
    fremde = sorted({_cap(c) for c in _liste(hc.get("capadd"))} - ERLAUBTE_CAPS)
    if fremde:
        return f"Fähigkeiten {', '.join(fremde)} sind gesperrt"
    for feld in ("devices", "devicerequests", "devicecgrouprules"):
        if _liste(hc.get(feld)):
            return f"{feld} ist gesperrt"
    netz = str(hc.get("networkmode") or "")
    if netz in _GESPERRTE_NETZE or netz.startswith("container:"):
        return f"NetworkMode {netz!r} ist gesperrt"
    for feld in ("pidmode", "ipcmode", "utsmode", "usernsmode", "cgroupnsmode"):
        modus = str(hc.get(feld) or "").lower()
        if modus not in _EIGENER_NAMESPACE:
            return f"{feld} {modus!r} ist gesperrt"
    if hc.get("sysctls"):
        return "Sysctls sind gesperrt"
    if hc.get("cgroupparent"):
        return "CgroupParent ist gesperrt"
    if hc.get("volumedriver"):
        return "VolumeDriver ist gesperrt"
    if str(hc.get("runtime") or "runc") not in ("runc", ""):
        return f"Runtime {hc.get('Runtime')!r} ist gesperrt"
    if hc.get("volumesfrom"):
        return "VolumesFrom ist gesperrt"
    # Leere Listen hier heben die Abschirmung von /proc und /sys auf (systempaths=unconfined).
    for feld in ("maskedpaths", "readonlypaths"):
        if feld in hc and hc.get(feld) is not None:
            return f"{feld} setzt nur Docker selbst"
    return (_security_opt_pruefen(_liste(hc.get("securityopt")))
            or _binds_pruefen(_liste(hc.get("binds")), erlaubte_host_pfade)
            or _mounts_pruefen(_liste(hc.get("mounts")), erlaubte_host_pfade))


def exec_anlegen(koerper: dict) -> str | None:
    """``POST /containers/{id}/exec`` — kein privilegierter Befehl."""
    if not isinstance(koerper, dict):
        return "Anfrage ist kein JSON-Objekt"
    if klein(koerper).get("privileged"):
        return "Privilegiertes exec ist gesperrt"
    return None


def volume_anlegen(koerper: dict) -> str | None:
    """``POST /volumes/create`` — nur lokale Volumes ohne Optionen.

    Mit ``driver_opts`` (type=none, o=bind, device=/) wäre ein benanntes Volume
    ein Host-Mount durch die Hintertür.
    """
    if not isinstance(koerper, dict):
        return "Anfrage ist kein JSON-Objekt"
    koerper = klein(koerper)
    if str(koerper.get("driver") or "local") != "local":
        return f"Volume-Treiber {koerper.get('driver')!r} ist gesperrt"
    if koerper.get("driveropts"):
        return "Volume-Optionen sind gesperrt"
    return None


def container_starten(koerper: dict) -> str | None:
    """``POST /containers/{id}/start`` — ältere API-Versionen nahmen hier noch eine
    HostConfig an; damit liessen sich Rechte am Anlegen vorbei nachreichen."""
    if koerper in (None, {}, b"", ""):
        return None
    if not isinstance(koerper, dict):
        return "Anfrage ist kein JSON-Objekt"
    return "HostConfig beim Start ist gesperrt" if koerper else None


def zielcontainer(inspect: dict) -> str | None:
    """Darf der Orchestrator in diesen Container hinein (exec, Dateien, attach, update, rename)?

    Nur, was die Plattform ausdrücklich als ihres kennzeichnet: Agenten
    (``ai-employee.type=agent``) und Apps der Agenten (Compose-Projekt ``agent-…``).
    Und auch dann nie, wenn der Container privilegiert ist, den Docker-Socket hat
    oder Namespaces des Hosts teilt — wer dort einen Befehl ausführt, wäre root auf
    dem Server. Alles andere (Socket-Proxy, autoheal, Datenbank, der Wächter …) ist zu.
    """
    try:
        d = klein(inspect or {})
    except ValueError as e:
        return f"Ziel nicht prüfbar ({e})"
    labels = (d.get("config") or {}).get("labels") or {}
    projekt = str(labels.get("com.docker.compose.project") or "")
    if labels.get("ai-employee.type") != "agent" and not projekt.startswith("agent-"):
        return "Ziel gehört nicht zu den Agenten"
    hc = d.get("hostconfig") or {}
    if hc.get("privileged"):
        return "Ziel ist privilegiert"
    for feld in ("pidmode", "ipcmode", "utsmode", "usernsmode", "networkmode"):
        modus = str(hc.get(feld) or "").lower()
        if modus == "host" or modus.startswith("container:"):
            return f"Ziel teilt Namespaces ({feld})"
    quellen = [str(b).split(":")[0] for b in _liste(hc.get("binds"))]
    quellen += [str((m or {}).get("source") or "") for m in _liste(d.get("mounts"))]
    if any("docker.sock" in q or "containerd.sock" in q for q in quellen):
        return "Ziel hat den Docker-Socket"
    return None
