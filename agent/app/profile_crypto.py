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

**Schluessel:** dieselbe Reihenfolge wie ``orchestrator/app/core/encryption.py``
(dort die "uebliche Regel"): Umgebungsvariable, wenn gueltig -- sonst eine
einmal angelegte, auf der Platte persistierte Datei (0600) -- sonst neu
erzeugen und dort ablegen. Agent und Orchestrator sind getrennte Prozesse
ohne gemeinsamen Import, deshalb hier dieselbe Logik noch einmal, bewusst
nicht als Kopie der Orchestrator-Datei.
"""

from __future__ import annotations

import io
import logging
import os
import tarfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

#: Wo der Schluessel persistiert, wenn er nicht ueber die Umgebung kommt.
_SCHLUESSEL_DATEI = Path(
    os.environ.get("BROWSER_PROFIL_KEY_DATEI", "/workspace/.browser-profil.key")
)


def _ist_gueltiger_schluessel(wert: str | None) -> bool:
    if not wert:
        return False
    try:
        Fernet(wert.encode())
        return True
    except Exception:
        return False


_zwischengespeicherter_schluessel: str | None = None


def _schluessel() -> bytes:
    """Fernet-Schluessel aufloesen: Umgebung > Datei > neu erzeugen."""
    global _zwischengespeicherter_schluessel
    if _zwischengespeicherter_schluessel:
        return _zwischengespeicherter_schluessel.encode()

    umgebung = os.environ.get("BROWSER_PROFIL_KEY")
    if umgebung:
        if _ist_gueltiger_schluessel(umgebung):
            _zwischengespeicherter_schluessel = umgebung
            return umgebung.encode()
        logger.error(
            "[ProfilCrypto] BROWSER_PROFIL_KEY ist UNGUELTIG (muss ein "
            "Fernet.generate_key() sein) -- weiche auf persistierten/neu "
            "erzeugten Schluessel aus."
        )

    if _SCHLUESSEL_DATEI.exists():
        wert = _SCHLUESSEL_DATEI.read_text().strip()
        if _ist_gueltiger_schluessel(wert):
            _zwischengespeicherter_schluessel = wert
            return wert.encode()
        logger.warning("[ProfilCrypto] Persistierter Schluessel %s ist ungueltig -- "
                       "wird neu erzeugt.", _SCHLUESSEL_DATEI)

    neu = Fernet.generate_key()
    _SCHLUESSEL_DATEI.parent.mkdir(parents=True, exist_ok=True)
    _SCHLUESSEL_DATEI.write_text(neu.decode())
    _SCHLUESSEL_DATEI.chmod(0o600)
    logger.warning("[ProfilCrypto] Neuen Schluessel erzeugt und unter %s abgelegt.",
                   _SCHLUESSEL_DATEI)
    _zwischengespeicherter_schluessel = neu.decode()
    return neu


def _fernet() -> Fernet:
    return Fernet(_schluessel())


def verschluesseln(klartext_dir: str, archiv_datei: str) -> None:
    """Verzeichnis einpacken, verschluesseln und atomar an ``archiv_datei`` ablegen.

    Atomar (temp-Datei + ``os.replace``), damit ein Abbruch waehrend des
    Schreibens nie ein halbes, unlesbares Archiv zurueck laesst -- der letzte
    gueltige Stand bliebe sonst verloren statt nur nicht aktualisiert.
    """
    if not os.path.isdir(klartext_dir):
        logger.debug("[ProfilCrypto] %s existiert nicht -- nichts zu sichern", klartext_dir)
        return

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
    except InvalidToken:
        logger.error(
            "[ProfilCrypto] Archiv %s liess sich nicht entschluesseln (Schluessel "
            "geaendert oder Datei beschaedigt). Profil startet leer statt den Agenten "
            "dauerhaft zu blockieren -- die Anmeldung ist dann weg.",
            archiv_datei,
        )
        return

    with tarfile.open(fileobj=io.BytesIO(entschluesselt), mode="r") as tar:
        tar.extractall(klartext_dir, filter="data")
    logger.info("[ProfilCrypto] Profil aus Archiv entschluesselt (%s)", archiv_datei)
