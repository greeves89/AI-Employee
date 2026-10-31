#!/usr/bin/env python3
"""Netzwächter: Agenten und Agenten-Apps erreichen weder Heimnetz noch Router noch Host.

Läuft auf dem HOST (systemd, root), nicht in einem Container: Er braucht iptables
und darf dafür kein privilegiertes Abbild mitbringen. Installation:
``sudo ./scripts/install-netzwaechter.sh``.

Eingeschränkt sind Container mit dem Label ``ai-employee.type=agent`` (setzt die
Plattform beim Anlegen) und alle Container eines Compose-Projekts ``agent-…``
(Agenten-Apps; den Projektnamen vergibt die Plattform über ``-p``). Ein gefälschtes
Label kann einen Container damit nur EINSCHRÄNKEN, nie freischalten. Der
Orchestrator und die übrigen Dienste der Plattform bleiben unberührt — der
Orchestrator muss etwa ein Exchange oder einen SSO-Dienst im Firmennetz erreichen.

Für einen eingeschränkten Container gilt (Kette ``AIE-NETZ`` aus ``DOCKER-USER``,
``AIE-NETZ-HOST`` aus ``INPUT``):

1. Antworten auf bestehende Verbindungen gehen immer.
2. Kein Verkehr zu eingeschränkten Containern eines ANDEREN Besitzers — im
   Plattformnetz erreichten Agenten sonst die Agenten anderer Nutzer (etwa deren
   Prüf-Hook auf Port 8080).
3. Ausnahmen aus ``/etc/ai-employee/netz-ausnahmen`` (ein Netz je Zeile, CIDR).
4. Verkehr in Docker-Netze, in denen der Container selbst hängt: Plattform,
   eigene Apps.
5. Alles andere in private Netze (RFC 1918, Link-Local, CGNAT) wird verworfen.
   Das Internet bleibt erreichbar.
6. Den Host selbst (SSH, Dienste auf dem Pi) erreicht er gar nicht.

Dazu das Standard-Bridge von Docker (``bridge``): Dort laufen die ``RUN``-Schritte
von Builds — ein Dockerfile erreichte sonst den Router. Die Plattform selbst nutzt
dieses Netz nicht. Erlaubt bleibt nur DNS zu den Nameservern aus
``/etc/resolv.conf`` (Builds fragen sie direkt, nicht über Dockers DNS).

Die Ketten werden bei jeder Änderung vollständig neu geschrieben (``iptables-restore
--noflush``), ausgelöst durch Docker-Ereignisse und spätestens alle fünf Minuten.
``--zeigen`` gibt die Regeln nur aus, ``--entfernen`` nimmt alles zurück.
"""

from __future__ import annotations

import ipaddress
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

KETTE = "AIE-NETZ"
KETTE_HOST = "AIE-NETZ-HOST"
AUSNAHMEN = Path("/etc/ai-employee/netz-ausnahmen")
PRIVAT = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "100.64.0.0/10"]
INTERVALL = 300


def besitzer(labels: dict, umgebung: list[str]) -> str | None:
    """Schlüssel des Besitzers (``agent-<id8>``) oder None = nicht eingeschränkt."""
    projekt = str(labels.get("com.docker.compose.project", ""))
    if projekt.startswith("agent-") and len(projekt) >= 14:
        return projekt[:14]
    if labels.get("ai-employee.type") == "agent":
        aid = next((e.split("=", 1)[1] for e in umgebung if e.startswith("AGENT_ID=")), "")
        return f"agent-{aid[:8]}" if aid else "agent-unbekannt"
    return None


def regeln(container: list[dict], subnetze: dict[str, str], ausnahmen: list[str],
           standard: str | None = None, nameserver: list[str] | None = None) -> tuple[list[str], list[str]]:
    """(Regeln für AIE-NETZ, Regeln für AIE-NETZ-HOST) — ohne Seiteneffekte.

    ``container``: je Container ``{"name", "besitzer", "ips": {netz: ip}}``.
    ``subnetze``: Docker-Netz → Subnetz. ``standard``: Subnetz des Standard-Bridge
    (Builds). ``nameserver``: DNS-Server, die Builds erreichen müssen.
    """
    vorwaerts = ["-A AIE-NETZ -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN"]
    host = ["-A AIE-NETZ-HOST -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN"]
    eingeschraenkt = [c for c in container if c["besitzer"]]
    for c in eingeschraenkt:
        for ip in sorted(set(c["ips"].values())):
            # 2. Andere Besitzer im selben Netz
            for anderer in eingeschraenkt:
                if anderer["besitzer"] == c["besitzer"]:
                    continue
                gemeinsam = set(c["ips"]) & set(anderer["ips"])
                for netz in sorted(gemeinsam):
                    vorwaerts.append(f"-A AIE-NETZ -s {ip}/32 -d {anderer['ips'][netz]}/32 -j DROP")
            # 3. Ausnahmen
            for ziel in ausnahmen:
                vorwaerts.append(f"-A AIE-NETZ -s {ip}/32 -d {ziel} -j RETURN")
            # 4. Eigene Docker-Netze
            for netz in sorted(c["ips"]):
                if subnetze.get(netz):
                    vorwaerts.append(f"-A AIE-NETZ -s {ip}/32 -d {subnetze[netz]} -j RETURN")
            # 5. Private Netze
            for privat in PRIVAT:
                vorwaerts.append(f"-A AIE-NETZ -s {ip}/32 -d {privat} -j DROP")
            # 5b. HTTPS nur ueber den Egress-Proxy (liegt im eigenen Docker-Netz, Regel 4).
            # Abweisen statt Verwerfen: Werkzeuge ohne Proxy scheitern sofort statt
            # nach einer Minute. UDP 443 (QUIC) gaebe es am Proxy vorbei ebenso.
            if c.get("nur_proxy"):
                vorwaerts.append(f"-A AIE-NETZ -s {ip}/32 -p tcp --dport 443 -j REJECT --reject-with tcp-reset")
                vorwaerts.append(f"-A AIE-NETZ -s {ip}/32 -p udp --dport 443 -j DROP")
            # 6. Host
            host.append(f"-A AIE-NETZ-HOST -s {ip}/32 -j DROP")
    if standard:
        for ns in nameserver or []:
            for proto in ("udp", "tcp"):
                vorwaerts.append(f"-A AIE-NETZ -s {standard} -d {ns}/32 -p {proto} --dport 53 -j RETURN")
                host.append(f"-A AIE-NETZ-HOST -s {standard} -d {ns}/32 -p {proto} --dport 53 -j RETURN")
        for ziel in ausnahmen:
            vorwaerts.append(f"-A AIE-NETZ -s {standard} -d {ziel} -j RETURN")
        vorwaerts.append(f"-A AIE-NETZ -s {standard} -d {standard} -j RETURN")
        for privat in PRIVAT:
            vorwaerts.append(f"-A AIE-NETZ -s {standard} -d {privat} -j DROP")
        host.append(f"-A AIE-NETZ-HOST -s {standard} -j DROP")
    return vorwaerts, host


def nameserver_lesen(pfad: Path = Path("/etc/resolv.conf")) -> list[str]:
    """IPv4-Nameserver des Hosts (die Builds direkt fragen)."""
    gefunden = []
    for zeile in pfad.read_text().splitlines() if pfad.exists() else []:
        teile = zeile.split()
        if len(teile) >= 2 and teile[0] == "nameserver":
            try:
                adresse = ipaddress.ip_address(teile[1])
            except ValueError:
                continue
            if adresse.version == 4 and not adresse.is_loopback:
                gefunden.append(str(adresse))
    return gefunden


def ausnahmen_lesen(pfad: Path = AUSNAHMEN) -> list[str]:
    if not pfad.exists():
        return []
    gueltig = []
    for zeile in pfad.read_text().splitlines():
        zeile = zeile.split("#", 1)[0].strip()
        if not zeile:
            continue
        try:
            gueltig.append(str(ipaddress.ip_network(zeile, strict=False)))
        except ValueError:
            print(f"[Netzwächter] Ausnahme ignoriert (kein Netz): {zeile}", flush=True)
    return gueltig


class Verschwunden(Exception):
    """Container oder Netz gibt es nicht mehr — überspringen ist richtig."""


def _docker_json(*argumente: str) -> list:
    r = subprocess.run(["docker", *argumente], capture_output=True, text=True)
    if r.returncode:
        if "No such" in r.stderr:
            raise Verschwunden(r.stderr.strip())
        # Jeder andere Fehler (Daemon hängt, keine Rechte): den Lauf abbrechen. Die
        # alten Regeln bleiben dann stehen — besser als neue, in denen Container fehlen.
        raise RuntimeError(f"docker {' '.join(argumente[:2])}: {r.stderr.strip()}")
    return json.loads(r.stdout) if r.stdout.strip() else []


def nur_ueber_proxy(labels: dict, umgebung: list[str]) -> bool:
    """Agent, der mit dem Egress-Proxy entstand (``HTTPS_PROXY`` in seiner Umgebung).

    Fuer ihn geht HTTPS nur ueber den Proxy hinaus. Die Umgebung des Behaelters ist
    die Quelle — so passt die Regel immer zu dem, was der Agent selbst kennt, und
    Apps (ohne Proxy) bleiben unberuehrt.
    """
    return (labels.get("ai-employee.type") == "agent"
            and any(e.startswith("HTTPS_PROXY=") and len(e) > len("HTTPS_PROXY=") for e in umgebung))


def zustand() -> tuple[list[dict], dict[str, str]]:
    ids = subprocess.run(["docker", "ps", "-q"], check=True, capture_output=True, text=True).stdout.split()
    container = []
    netznamen: set[str] = {"bridge"}
    gesehen = []
    for cid in ids:
        # Einzeln: Ein Container, der zwischen ``ps`` und ``inspect`` endet, darf
        # nicht den ganzen Durchlauf kosten — sonst blieben neue Container ohne Regeln.
        try:
            gesehen += _docker_json("inspect", cid)
        except Verschwunden:
            continue
    for c in gesehen:
        netze = (c.get("NetworkSettings") or {}).get("Networks") or {}
        ips = {n: v["IPAddress"] for n, v in netze.items() if v.get("IPAddress")}
        netznamen |= set(ips)
        labels = (c.get("Config") or {}).get("Labels") or {}
        umgebung = (c.get("Config") or {}).get("Env") or []
        container.append({
            "name": c.get("Name", "").lstrip("/"),
            "besitzer": besitzer(labels, umgebung),
            "ips": ips,
            "nur_proxy": nur_ueber_proxy(labels, umgebung),
        })
    subnetze = {}
    netze = []
    for name in sorted(netznamen):
        try:
            netze += _docker_json("network", "inspect", name)
        except Verschwunden:
            if name == "bridge":
                raise RuntimeError("Standard-Bridge nicht lesbar — Regeln für Builds fehlten") from None
            continue
    for n in netze:
        konfig = ((n.get("IPAM") or {}).get("Config") or [])
        v4 = next((k["Subnet"] for k in konfig if k.get("Subnet") and ":" not in k["Subnet"]), None)
        if v4:
            subnetze[n["Name"]] = v4
    return container, subnetze


def _iptables(*argumente: str, pruefen: bool = False) -> bool:
    r = subprocess.run(["iptables", "-w", *argumente], capture_output=True, text=True)
    if r.returncode and not pruefen:
        raise RuntimeError(r.stderr.strip())
    return r.returncode == 0


def anwenden(vorwaerts: list[str], host: list[str]) -> None:
    """Beide Ketten in EINEM Schritt ersetzen, Sprünge einmalig setzen."""
    text = "\n".join(["*filter", f":{KETTE} - [0:0]", f":{KETTE_HOST} - [0:0]", *vorwaerts, *host, "COMMIT", ""])
    subprocess.run(["iptables-restore", "-w", "--noflush"], input=text, text=True, check=True)
    for kette, ziel in (("DOCKER-USER", KETTE), ("INPUT", KETTE_HOST)):
        if not _iptables("-C", kette, "-j", ziel, pruefen=True):
            _iptables("-I", kette, "1", "-j", ziel)


def entfernen() -> None:
    for kette, ziel in (("DOCKER-USER", KETTE), ("INPUT", KETTE_HOST)):
        while _iptables("-C", kette, "-j", ziel, pruefen=True):
            _iptables("-D", kette, "-j", ziel)
        _iptables("-F", ziel, pruefen=True)
        _iptables("-X", ziel, pruefen=True)


def bridge_netfilter() -> None:
    """Verkehr INNERHALB einer Docker-Bridge läuft nur mit br_netfilter durch
    iptables — ohne ihn erreichten sich Agenten verschiedener Besitzer im
    Plattformnetz trotz Regel (auf dem Pi gemessen)."""
    subprocess.run(["modprobe", "br_netfilter"], capture_output=True)
    schalter = Path("/proc/sys/net/bridge/bridge-nf-call-iptables")
    if schalter.exists() and schalter.read_text().strip() != "1":
        schalter.write_text("1")


def einmal(zeigen: bool = False) -> int:
    container, subnetze = zustand()
    vorwaerts, host = regeln(container, subnetze, ausnahmen_lesen(),
                             standard=subnetze.get("bridge"), nameserver=nameserver_lesen())
    if zeigen:
        print("\n".join(vorwaerts + host))
    else:
        bridge_netfilter()
        anwenden(vorwaerts, host)
    return sum(1 for c in container if c["besitzer"])


def dauerlauf() -> None:
    ereignis = threading.Event()

    def lauschen():
        while True:
            proc = subprocess.Popen(
                # connect/disconnect: Die Plattform haengt Apps und Agenten selbst in
                # App-Netze — jede neue IP braucht ihre Regeln sofort.
                ["docker", "events", "--filter", "type=container", "--filter", "event=start",
                 "--filter", "event=die", "--filter", "type=network", "--filter", "event=connect",
                 "--filter", "event=disconnect", "--format", "{{.Type}}"],
                stdout=subprocess.PIPE, text=True)
            for _ in proc.stdout:
                ereignis.set()
            time.sleep(5)    # Docker neu gestartet: wieder anmelden

    threading.Thread(target=lauschen, daemon=True).start()
    while True:
        warten = INTERVALL
        try:
            n = einmal()
            print(f"[Netzwächter] Regeln für {n} Container gesetzt", flush=True)
        except Exception as e:  # noqa: BLE001 — gleich wieder versuchen, nicht erst in 5 Minuten
            print(f"[Netzwächter] Fehler: {e}", flush=True)
            warten = 10
        ereignis.wait(warten)
        ereignis.clear()
        time.sleep(2)        # mehrere Ereignisse eines Starts zusammenfassen


if __name__ == "__main__":
    if "--entfernen" in sys.argv:
        entfernen()
        print("[Netzwächter] Regeln entfernt")
    elif "--zeigen" in sys.argv:
        einmal(zeigen=True)
    elif "--einmal" in sys.argv:
        print(f"[Netzwächter] Regeln für {einmal()} Container gesetzt")
    else:
        dauerlauf()
