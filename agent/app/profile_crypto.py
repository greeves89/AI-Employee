"""Browserprofil verschluesselt ablegen (#828, Schritt 5).

Das persistente Chromium-Profil (``agent/app/tools/browser.py``) enthaelt
Sitzungsmerkmale -- nach einer Anmeldung also faktisch Zugangsdaten. Chromium
selbst kann ein Profil nicht verschluesselt benutzen (es liest/schreibt
SQLite- und LevelDB-Dateien direkt), deshalb der Schnappschuss-Ansatz:

* Auf der Platte liegt NUR das verschluesselte Archiv (``PROFIL_ARCHIV``).
* Beim Start wird daraus ein Klartext-Arbeitsverzeichnis befuellt, das
  Chromium normal benutzt.
* Nach sicherheitsrelevanten Schritten (Anmeldung, Navigation) und beim
  geordneten Beenden wird das Arbeitsverzeichnis wieder eingepackt und
  verschluesselt zurückgeschrieben.

**Bekannte Grenze:** Ein harter Container-Abbruch (OOM-Kill, ``docker kill``)
zwischen zwei Schnappschuessen geht nicht in das Archiv ein -- dasselbe
Zugestaendnis, das diese Codebase schon beim Zwischenspeicher unter ``/tmp``
macht. Ein geordnetes Beenden (SIGTERM/SIGINT, ``shutdown()`` in
``main.py``) sichert immer, ein Navigationsschritt zusaetzlich zwischendurch.

**Schluessel:** kommt ausschliesslich vom Orchestrator
(``BROWSER_PROFIL_KEY``, je Agent aus ``ENCRYPTION_KEY`` abgeleitet) und liegt
nie im Arbeitsbereich. Fehlt er, wird nichts gesichert und kein Archiv
angeruehrt.

**Was das schuetzt:** das ruhende Archiv im Arbeitsbereich (Sicherungen,
Plattenabzug, ein anderer Container mit dem Volume). Es schuetzt NICHT vor dem
Agenten selbst: Waehrend er laeuft, liegt das Profil entschluesselt unter
``/tmp`` und der Schluessel in seiner Umgebung. Beim geordneten Beenden wird
das Klartextprofil geloescht.
"""

from __future__ import annotations

import io
import logging
import os
import tarfile
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

class KeinSchluessel(RuntimeError):
    """``BROWSER_PROFIL_KEY`` fehlt oder ist ungueltig."""


def _schluessel() -> bytes:
    wert = os.environ.get("BROWSER_PROFIL_KEY", "")
    try:
        Fernet(wert.encode())
    except Exception as e:  # noqa: BLE001 — leer oder kein Fernet-Schluessel
        raise KeinSchluessel(
            "BROWSER_PROFIL_KEY fehlt oder ist ungueltig -- das Browserprofil wird "
            "nicht gesichert. Agent ueber die Oberflaeche aktualisieren."
        ) from e
    return wert.encode()


def _fernet() -> Fernet:
    return Fernet(_schluessel())


def verschluesseln(klartext_dir: str, archiv_datei: str) -> bool:
    """Verzeichnis einpacken, verschluesseln und atomar an ``archiv_datei`` ablegen.

    Atomar (temp-Datei + ``os.replace``), damit ein Abbruch waehrend des
    Schreibens nie ein halbes, unlesbares Archiv zurueck laesst -- der letzte
    gueltige Stand bliebe sonst verloren statt nur nicht aktualisiert.
    """
    if not os.path.isdir(klartext_dir):
        logger.debug("[ProfilCrypto] %s existiert nicht -- nichts zu sichern", klartext_dir)
        return False

    puffer = io.BytesIO()
    with tarfile.open(fileobj=puffer, mode="w") as tar:
        tar.add(klartext_dir, arcname=".")
    verschluesselt = _fernet().encrypt(puffer.getvalue())

    ziel = Path(archiv_datei)
    ziel.parent.mkdir(parents=True, exist_ok=True)
    temp = ziel.with_suffix(ziel.suffix + ".tmp")
    temp.write_bytes(verschluesselt)
    temp.chmod(0o600)
    os.replace(temp, ziel)
    logger.info("[ProfilCrypto] Profil gesichert (%d Bytes verschluesselt)", len(verschluesselt))
    return True


def entschluesseln(archiv_datei: str, klartext_dir: str) -> None:
    """Archiv entschluesseln und nach ``klartext_dir`` auspacken.

    Idempotent und sicher gegen Doppelaufruf: Ist ``klartext_dir`` schon
    befuellt, wird NICHT erneut ausgepackt -- sonst koennte ein zweiter
    Aufruf (Python-Werkzeug UND Playwright-MCP greifen beide auf denselben
    Pfad zu) ein bereits laufendes Chromium ueberschreiben.
    """
    ziel = Path(klartext_dir)
    if ziel.is_dir() and any(ziel.iterdir()):
        logger.debug("[ProfilCrypto] %s ist schon befuellt -- kein erneutes Auspacken",
                     klartext_dir)
        return

    ziel.mkdir(parents=True, exist_ok=True)

    if not os.path.exists(archiv_datei):
        logger.debug("[ProfilCrypto] Kein Archiv unter %s -- Profil startet leer", archiv_datei)
        return

    try:
        roh = Path(archiv_datei).read_bytes()
        entschluesselt = _fernet().decrypt(roh)
    except KeinSchluessel as e:
        # Archiv NICHT anfassen: mit dem richtigen Schluessel ist es noch lesbar.
        logger.error("[ProfilCrypto] %s Profil startet leer, das Archiv bleibt liegen.", e)
        return
    except InvalidToken:
        # Beiseitelegen statt beim naechsten Sichern ueberschreiben: Kommt der
        # richtige Schluessel zurueck, ist die Anmeldung noch zu retten.
        beiseite = f"{archiv_datei}.nicht-lesbar-{int(time.time())}"
        os.replace(archiv_datei, beiseite)
        logger.error(
            "[ProfilCrypto] Archiv %s liess sich nicht entschluesseln (Schluessel "
            "geaendert oder Datei beschaedigt). Liegt jetzt unter %s; das Profil "
            "startet leer.", archiv_datei, beiseite,
        )
        return

    with tarfile.open(fileobj=io.BytesIO(entschluesselt), mode="r") as tar:
        tar.extractall(klartext_dir, filter="data")
    logger.info("[ProfilCrypto] Profil aus Archiv entschluesselt (%s)", archiv_datei)
