"""Darf die Plattform diese Adresse von sich aus anfragen?

Sobald ein Aufrufer eine Rückruf-Adresse mitgeben kann, entscheidet er, wohin dieser
Server eine Anfrage schickt. Ohne Prüfung ist das eine klassische serverseitige
Anfragefälschung (SSRF): jemand trägt ``http://169.254.169.254/`` oder
``http://postgres:5432`` ein und lässt den Orchestrator aus dem internen Netz heraus
Dinge abrufen, an die er von außen nie herankäme.

Zwei Sperren, in dieser Reihenfolge:

1. **Nur HTTPS.** Ein Rückruf über HTTP trägt das Ergebnis im Klartext durchs Netz.
2. **Kein internes Ziel.** Loopback, private Netze, link-local und die Adressen der
   Cloud-Metadatendienste sind ausgeschlossen — aufgelöst wird dafür der Hostname,
   denn ein öffentlicher Name kann auf ``127.0.0.1`` zeigen.
"""

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Der Metadatendienst von AWS/Azure/GCP. Steht zusaetzlich zu den privaten Netzen
# hier, weil er das lohnendste Ziel einer SSRF ist: dort liegen Zugangsdaten.
_METADATA_HOSTS = {"169.254.169.254", "metadata.google.internal", "100.100.100.200"}


def _is_internal(host: str) -> bool:
    """Zeigt dieser Name oder diese Adresse ins eigene Netz?

    Aufgeloest wird bewusst: ``evil.example`` kann auf ``127.0.0.1`` zeigen, und die
    reine Namenspruefung waere damit wirkungslos. Laesst sich der Name nicht
    aufloesen, gilt er als intern — im Zweifel nicht anfragen.
    """
    if host.lower() in _METADATA_HOSTS:
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        logger.debug("Host %s nicht aufloesbar — als intern behandelt", host)
        return True

    for info in infos:
        raw = info[4][0]
        try:
            addr = ipaddress.ip_address(raw)
        except ValueError:
            return True
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
            return True
    return False


def check_outbound_url(url: str) -> tuple[bool, str]:
    """``(erlaubt, Begruendung)`` für eine ausgehende Adresse."""
    if not url or len(url) > 2000:
        return False, "leer oder zu lang"
    try:
        parsed = urlparse(url)
    except ValueError:
        return False, "nicht lesbar"
    if parsed.scheme != "https":
        return False, "nur https erlaubt"
    if not parsed.hostname:
        return False, "kein Hostname"
    if _is_internal(parsed.hostname):
        return False, "zeigt auf ein internes Ziel"
    return True, ""


async def is_allowed_callback(url: str, db=None, agent_id: str | None = None) -> bool:
    """Darf für diesen Agenten ein Rückruf an diese Adresse gehen?

    Erst die harte Prüfung oben; danach — falls für den Agenten überhaupt eine
    Freigabeliste gepflegt ist — muss der Host auch dort stehen. Ohne gepflegte Liste
    bleibt es bei der harten Prüfung, sonst wäre der Rückruf für alle bestehenden
    Agenten stillschweigend abgeschaltet.
    """
    allowed, reason = check_outbound_url(url)
    if not allowed:
        logger.info("Rueckruf abgelehnt (%s)", reason)
        return False
    if db is None or agent_id is None:
        return True

    try:
        from sqlalchemy import select

        from app.models.url_allowlist import AgentUrlAllowlist

        entries = (await db.execute(
            select(AgentUrlAllowlist).where(AgentUrlAllowlist.agent_id == agent_id)
        )).scalars().all()
        if not entries:
            return True
        host = (urlparse(url).hostname or "").lower()
        for entry in entries:
            pattern = (getattr(entry, "url_pattern", "") or "").lower()
            cleaned = pattern.replace("https://", "").replace("http://", "").strip("/*")
            if cleaned and (host == cleaned or host.endswith("." + cleaned)):
                return True
        logger.info("Rueckruf-Host %s steht nicht auf der Freigabeliste von %s",
                    host, agent_id)
        return False
    except Exception:  # noqa: BLE001 — die harte Pruefung hat bereits bestanden
        logger.debug("Freigabeliste nicht auswertbar", exc_info=True)
        return True
