"""Oeffentliches Kontaktformular der Landingpage.

Die Landingpage ist statisch (Caddy) — dieser Endpunkt ist ihr einziges
Backend: er nimmt Name, E-Mail und Nachricht entgegen und stellt sie dem
Betreiber per SMTP zu. Opt-in per Konfiguration: solange keine SMTP-Zugangs-
daten gesetzt sind (CONTACT_SMTP_USER/CONTACT_SMTP_PASSWORD), antwortet er
mit 503 — auf Installationen ohne Landingpage existiert damit faktisch kein
Formularversand.

Schutz vor Missbrauch (der Endpunkt ist bewusst ohne Anmeldung erreichbar):
- Honeypot-Feld ``website`` — Bots fuellen es, Menschen sehen es nicht.
  Gefuellt heisst: freundlich "ok" sagen und NICHTS senden.
- Redis-Zaehler je Absender-IP (5 Nachrichten pro Stunde), zusaetzlich zum
  globalen API-Rate-Limit.
- Feste Empfaengeradresse aus der Konfiguration — der Client bestimmt nie,
  wohin gesendet wird.
"""

import asyncio
import logging
import re
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/contact", tags=["contact"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
_MAX_PER_HOUR = 5


class ContactRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    message: str = Field(min_length=10, max_length=5000)
    # Honeypot: bleibt bei Menschen leer (das Feld ist im Formular unsichtbar).
    website: str = ""


def _send_mail(body: ContactRequest) -> None:
    """Blockierender SMTP-Versand — laeuft via asyncio.to_thread."""
    msg = EmailMessage()
    msg["From"] = formataddr(("AI Employee Landingpage", settings.contact_from))
    msg["To"] = settings.contact_to
    msg["Subject"] = f"Landingpage-Anfrage von {body.name}"
    # Antworten gehen direkt an die Person, die angefragt hat.
    msg["Reply-To"] = body.email
    msg.set_content(
        f"Neue Anfrage ueber das Kontaktformular der Landingpage:\n\n"
        f"Name:    {body.name}\n"
        f"E-Mail:  {body.email}\n\n"
        f"Nachricht:\n{body.message}\n"
    )
    with smtplib.SMTP(settings.contact_smtp_host, settings.contact_smtp_port, timeout=25) as smtp:
        smtp.starttls()
        smtp.login(settings.contact_smtp_user, settings.contact_smtp_password)
        smtp.send_message(msg)


@router.post("")
@router.post("/")
async def submit_contact(body: ContactRequest, request: Request):
    if body.website.strip():
        # Bot im Honeypot: nicht verraten, nichts senden.
        return {"status": "ok"}

    if not _EMAIL_RE.match(body.email.strip()):
        raise HTTPException(status_code=422, detail="Bitte eine gueltige E-Mail-Adresse angeben.")

    if not (settings.contact_smtp_user and settings.contact_smtp_password and settings.contact_to):
        raise HTTPException(status_code=503, detail="Kontaktformular ist auf dieser Installation nicht eingerichtet.")

    # 5 Nachrichten pro IP und Stunde — genug fuer Menschen, zu wenig fuer Spam.
    redis = getattr(request.app.state, "redis", None)
    client = getattr(redis, "client", None)
    if client is not None:
        try:
            ip = request.client.host if request.client else "unknown"
            key = f"contact:rate:{ip}"
            count = int(await client.incr(key))
            if count == 1:
                await client.expire(key, 3600)
            if count > _MAX_PER_HOUR:
                raise HTTPException(status_code=429, detail="Zu viele Anfragen — bitte spaeter erneut versuchen.")
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001
            logger.debug("[Contact] Rate-Limit nicht verfuegbar", exc_info=True)

    try:
        await asyncio.to_thread(_send_mail, body)
    except Exception as e:  # noqa: BLE001
        # Kein Stacktrace an den Client, aber eine Spur im Log.
        logger.warning("[Contact] Mailversand fehlgeschlagen: %s", e)
        raise HTTPException(status_code=502, detail="Nachricht konnte gerade nicht zugestellt werden — bitte spaeter erneut versuchen.")

    logger.info("[Contact] Anfrage zugestellt (Absender-Domain: %s)", body.email.split("@")[-1])
    return {"status": "sent"}
