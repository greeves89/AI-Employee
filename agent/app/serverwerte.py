"""Werte nur für den Agenten-Server — nicht für die Werkzeuge, die er startet.

Der Orchestrator gibt dem Container Zugänge mit, die nur dieser Python-Prozess braucht:
die Redis-Anmeldung, den Schlüssel des Browserprofils, die Zugänge zu eigenen MCP-Servern
und den eigenen Codex-Zugang. Jeder Kindprozess erbt aber die Umgebung — die CLI, jeder
Bash-Befehl des Modells, ``npm install`` samt Paket-Skripten, der gemeinsame MCP-Prozess.
Dort tauchten die Werte in ``env``-Ausgaben, Absturzberichten und Build-Logs auf, die im
Chat, in Commits oder in Tickets landen.

``uebernehmen()`` läuft als Erstes beim Start: Es holt die Werte aus ``os.environ`` in
diesen Prozess. Danach erbt JEDER Kindprozess eine Umgebung ohne sie — eine Stelle statt
einer Bereinigung an jedem Startpunkt. Den Prozess selbst schirmt
``richtlinie.pruefserver_abschirmen`` ab (nicht „dumpbar“): Seine ``/proc/<pid>/environ``
und sein Speicher sind für den Agenten-Nutzer nicht lesbar (live geprüft). Das ``pop``
allein reicht dafür nicht — die ursprüngliche Umgebung bleibt im Prozessabbild stehen.

Nicht abgedeckt: Zugänge, die eine CLI selbst liest (``~/.claude.json`` für die Kopfzeilen
eigener MCP-Server, ``MCP_TOKEN_*`` für Codex, ``~/.codex/auth.json``). Hinter dem
Egress-Proxy sind das Platzhalter; siehe docs/SICHERHEITSMODELL.md.

Bewusst NICHT darunter: ``AGENT_TOKEN``. Werkzeuge brauchen es (``ai-team``, die
Proxy-Adresse in ``HTTPS_PROXY``), und seit 1.439.0 gilt es nur aus dem Agentennetz.
Ebenso Schlüssel aus der Schlüsselverwaltung (``GITHUB_TOKEN`` …): die sind für die
Werkzeuge gedacht und liegen hinter dem Egress-Proxy nur als Platzhalter vor.
"""

from __future__ import annotations

import os
import threading

#: Nur der Server-Prozess liest sie (``settings.redis_url``, ``profile_crypto``,
#: ``mcp_client``, Codex-/Claude-MCP-Konfiguration, ``setup_codex_auth``).
NUR_SERVER = (
    "REDIS_URL",
    "REDIS_PASSWORD",
    "BROWSER_PROFIL_KEY",
    "CUSTOM_MCP_AUTH",
    "CUSTOM_MCP_HEADERS",
    "CODEX_AUTH_JSON",
    "BROWSER_ZUGANG",      # Ausweis des Orchestrators am Bildstrom (browser_stream)
)

_werte: dict[str, str] = {}
_sperre = threading.Lock()


def uebernehmen() -> list[str]:
    """Die Server-Werte aus der Umgebung holen. Gibt die Namen zurück, die da waren.

    Muss NACH dem Laden von ``app.config.settings`` laufen (das liest ``REDIS_URL``
    beim Import) und VOR dem ersten Kindprozess.
    """
    genommen = []
    with _sperre:
        for name in NUR_SERVER:
            wert = os.environ.pop(name, None)
            if wert is not None:
                _werte[name] = wert
                genommen.append(name)
    return genommen


def get(name: str, vorgabe: str = "") -> str:
    """Der Wert — aus dem Speicher, sonst (noch nicht übernommen, Tests) aus der Umgebung."""
    with _sperre:
        if name in _werte:
            return _werte[name]
    return os.environ.get(name, vorgabe)


def setzen(name: str, wert: str) -> None:
    """Einen Server-Wert erneuern (etwa rotierte MCP-Zugänge) — nie in die Umgebung."""
    if name not in NUR_SERVER:
        raise ValueError(f"{name} ist kein reiner Server-Wert")
    with _sperre:
        _werte[name] = wert
    os.environ.pop(name, None)


def entfernen(name: str) -> None:
    with _sperre:
        _werte.pop(name, None)
    os.environ.pop(name, None)
