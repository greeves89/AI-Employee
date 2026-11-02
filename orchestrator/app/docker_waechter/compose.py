"""Agenten-Apps im Docker-Wächter: prüfen, dann mit dem Docker-Socket ausführen.

Der Orchestrator schickt das normalisierte, UNGEPRÜFTE Modell. Geprüft wird hier
mit derselben Positivliste (``compose_pruefung.pruefen``) — erst danach entsteht
der Runner mit dem Socket. So hilft es nichts, den Orchestrator zu übernehmen und
ein „schon geprüftes“ Modell mit ``privileged: true`` zu schicken.
"""

from __future__ import annotations

import logging
import posixpath
import re
from typing import Any

from app.core import compose_pruefung as cp

logger = logging.getLogger("docker_waechter")

_PRAEFIX = re.compile(r"^agent-[0-9a-z]{8}-$")
_PROJEKT = re.compile(r"^agent-[0-9a-z]{8}-[a-z0-9-]{1,120}$")
_VOLUME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
#: Ordner, unter denen der Runner ein Projekt einhängt (Workspace bzw. geteilter Ordner).
_SICHTEN = ("/workspace", "/shared")


def _pfad(wert: Any, *, absolut: bool) -> str | None:
    if not isinstance(wert, str) or "\x00" in wert:
        return None
    if absolut and not wert.startswith("/"):
        return None
    if ".." in wert.split("/"):
        return None
    return posixpath.normpath(wert) if wert else ""


def quelle_aus(daten: Any) -> cp.Quelle | str:
    """Die Quelle aus der Anfrage — oder der Grund, warum sie nicht taugt."""
    if not isinstance(daten, dict):
        return "Quelle fehlt"
    volume, praefix = daten.get("volume"), daten.get("praefix")
    sicht = _pfad(daten.get("sicht"), absolut=True)
    teilpfad = _pfad(daten.get("teilpfad") or "", absolut=False)
    bereich = _pfad(daten.get("bereich") or "", absolut=False) if not daten.get("bereich") else \
        _pfad(daten.get("bereich"), absolut=True)
    if not isinstance(volume, str) or not _VOLUME.fullmatch(volume):
        return "Volume ungültig"
    if not isinstance(praefix, str) or not _PRAEFIX.fullmatch(praefix):
        return "Präfix ungültig"
    # Das Volume gehört zum Agenten des Präfixes (oder ist der geteilte Ordner der Taskforce) —
    # sonst hinge der Runner fremde Daten ein, etwa das Volume der Datenbank.
    agent8 = praefix[len("agent-"):-1]
    if volume == "ai-employee-shared":
        if daten.get("sicht") != "/shared":
            return "Der geteilte Ordner nur für Taskforce-Projekte"
    elif not re.fullmatch(rf"workspace-{re.escape(agent8)}[0-9a-z]*", volume):
        return "Volume gehört nicht zu diesem Agenten"
    if sicht not in _SICHTEN:
        return "Sicht ungültig"
    if teilpfad is None or teilpfad.startswith("/") or bereich is None:
        return "Pfad ungültig"
    if bereich and not (bereich == sicht or bereich.startswith(sicht + "/")):
        return "Bereich liegt nicht in der Sicht"
    return cp.Quelle(volume=volume, sicht=sicht, praefix=praefix, teilpfad=teilpfad,
                     bereich=bereich, ordner_erlaubt=bool(daten.get("ordner_erlaubt", True)))


def bearbeiten(docker, daten: dict) -> tuple[int, dict]:
    """``POST /aie/compose`` — (HTTP-Status, Antwort)."""
    if not isinstance(daten, dict):
        return 400, {"message": "Anfrage ungültig"}
    quelle = quelle_aus(daten.get("quelle"))
    if isinstance(quelle, str):
        return 400, {"message": quelle}
    projekt, befehl, modell = daten.get("projekt"), daten.get("befehl"), daten.get("modell")
    if not isinstance(projekt, str) or not _PROJEKT.fullmatch(projekt) or not projekt.startswith(quelle.praefix):
        return 400, {"message": "Projektname ungültig"}
    if befehl not in cp.ERLAUBTE_BEFEHLE:
        return 400, {"message": "Befehl nicht erlaubt"}
    if modell is None:
        if befehl != ["down"]:
            return 400, {"message": "Ohne Modell nur „down“"}
    else:
        if not isinstance(modell, dict):
            return 400, {"message": "Modell ungültig"}
        try:
            modell = cp.pruefen(modell, quelle, projekt)[0]
        except cp.ComposeAbgelehnt as e:
            logger.warning("Compose abgelehnt (%s): %s", projekt, "; ".join(e.verstoesse)[:300])
            return 422, {"verstoesse": e.verstoesse}
    try:
        code, ausgabe = cp.ausfuehren_geprueft(docker, quelle, projekt, modell, befehl)
    except Exception as e:  # noqa: BLE001 — z. B. Runner-Abbild nicht verfügbar
        logger.warning("Compose %s gescheitert: %s", projekt, e)
        return 500, {"message": str(getattr(e, "detail", e))[:500]}
    return 200, {"code": code, "ausgabe": ausgabe}
