"""Mails gehen erst nach Freigabe raus — und dann sendet der Server, nicht der Agent.

Agenten im Microsoft-Zugriffsmodus „Entwurf“ (``ms_access.entwurf_enabled``) können
``ms_send_email``/``ms_reply_email`` aufrufen, bekommen aber nur einen Entwurf im
Postfach ihres Besitzers. Den reicht ``anfragen`` als Freigabe ein (Art
``mail_senden``, Ansicht ``mail``):

* Die Vorschau (An, Cc, Betreff, Text) liest der Server aus dem Postfach — nicht aus
  dem, was der Agent behauptet.
* Ein Fingerabdruck über Empfänger, Betreff und Text wird mit abgelegt. Beim
  Freigeben liest ``senden`` den Entwurf erneut; hat er sich seitdem geändert (etwa
  ein anderer Empfänger), wird NICHT gesendet.
* Freigeben darf nur der Besitzer des Agenten — es ist sein Postfach.

Diese Art legt nur der Server an (``api/approvals.SERVER_ARTEN``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re

from fastapi import HTTPException

log = logging.getLogger(__name__)

ART = "mail_senden"
FELDER = "subject,toRecipients,ccRecipients,bccRecipients,body,isDraft"
MAX_TEXT = 4000


async def _token(db, user_id: str | None) -> str | None:
    from app.services.oauth_service import OAuthService

    if not user_id:
        return None
    try:
        return await OAuthService(db, None).get_valid_token("microsoft", user_id)
    except Exception as e:  # noqa: BLE001 — nicht verbunden / Microsoft gestört
        log.warning("Kein Microsoft-Zugang für Mail-Freigabe: %s", e)
        return None


async def _entwurf(token: str, message_id: str) -> dict:
    from app.core.msgraph_mcp import _gid, _graph

    return await _graph("GET", f"/me/messages/{_gid(message_id)}?$select={FELDER}", token)


def _adressen(m: dict, feld: str) -> list[str]:
    return [str(((r or {}).get("emailAddress") or {}).get("address") or "") for r in (m.get(feld) or [])]


def fingerabdruck(m: dict) -> str:
    teile = {
        "an": sorted(_adressen(m, "toRecipients")), "cc": sorted(_adressen(m, "ccRecipients")),
        "bcc": sorted(_adressen(m, "bccRecipients")), "betreff": m.get("subject") or "",
        "text": ((m.get("body") or {}).get("content") or ""),
    }
    return hashlib.sha256(json.dumps(teile, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _klartext(m: dict) -> str:
    body = m.get("body") or {}
    text = body.get("content") or ""
    if (body.get("contentType") or "").lower() == "html":
        text = re.sub(r"(?is)<(script|style).*?</\1>", "", text)
        text = re.sub(r"(?i)<br\s*/?>|</p>", "\n", text)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:MAX_TEXT] + (" …" if len(text) > MAX_TEXT else "")


def vorschau(m: dict) -> dict:
    return {"an": _adressen(m, "toRecipients"), "cc": _adressen(m, "ccRecipients"),
            "bcc": _adressen(m, "bccRecipients"), "betreff": m.get("subject") or "(ohne Betreff)",
            "text": _klartext(m)}


async def anfragen(agent_id: str, message_id: str, betreff: str) -> str:
    """Einen Entwurf zur Freigabe stellen. Liefert den Hinweis für den Agenten."""
    from app.api.approvals import ApprovalRequest, anfrage_anlegen
    from app.db.session import async_session_factory
    from app.models.agent import Agent

    async with async_session_factory() as db:
        agent = await db.get(Agent, agent_id)
        token = await _token(db, agent.user_id if agent else None)
        if not token:
            return "Der Entwurf liegt im Postfach, konnte aber nicht zur Freigabe gestellt werden (kein Microsoft-Zugang)."
        m = await _entwurf(token, message_id)
        if not m.get("isDraft"):
            return "Das ist kein Entwurf — nichts zur Freigabe gestellt."
        v = vorschau(m)
        an = ", ".join(v["an"]) or "—"
        erg = await anfrage_anlegen(db, agent_id, ApprovalRequest(
            tool="ms_send_email", kind=ART, risk_level="medium",
            reasoning=f"Mail „{v['betreff']}“ an {an} senden",
            input={"message_id": message_id, "fingerabdruck": fingerabdruck(m)},
            view={"name": "mail", "data": v},
        ))
    return (f"Zur Freigabe gestellt (Freigabe {erg.get('approval_id')}): Gesendet wird erst, wenn dein Mensch "
            "freigibt — das erledigt der Server. Du musst nicht warten und nichts erneut senden.")


async def senden(db, approval, user) -> dict:
    """Beim Freigeben: Entwurf prüfen und aus dem Postfach des Besitzers senden.

    Wirft ``HTTPException`` — die Freigabe bleibt dann offen."""
    from app.core.msgraph_mcp import GraphError, _gid, _graph
    from app.models.agent import Agent

    agent = await db.get(Agent, approval.agent_id)
    if agent is None or str(agent.user_id or "") != str(getattr(user, "id", "")):
        raise HTTPException(status_code=403, detail="Diese Mail gibt nur frei, wem das Postfach gehört.")
    eingabe = ((approval.meta or {}).get("input") or {})
    message_id = str(eingabe.get("message_id") or "")
    token = await _token(db, agent.user_id)
    if not message_id or not token:
        raise HTTPException(status_code=409, detail="Microsoft-Zugang fehlt — bitte unter Integrationen neu verbinden.")
    try:
        m = await _entwurf(token, message_id)
    except GraphError as e:
        if e.status_code == 404:
            raise HTTPException(status_code=409, detail="Den Entwurf gibt es nicht mehr (gelöscht oder schon gesendet).")
        raise HTTPException(status_code=502, detail="Microsoft ist gerade nicht erreichbar — bitte später erneut.")
    if not m.get("isDraft"):
        raise HTTPException(status_code=409, detail="Die Mail ist schon gesendet.")
    if fingerabdruck(m) != eingabe.get("fingerabdruck"):
        raise HTTPException(status_code=409, detail=(
            "Der Entwurf wurde seit der Anfrage geändert — es wird nichts gesendet. Bitte im Postfach prüfen "
            "und dort selbst senden oder die Freigabe ablehnen."))
    try:
        await _graph("POST", f"/me/messages/{_gid(message_id)}/send", token)
    except GraphError:
        raise HTTPException(status_code=502, detail="Senden fehlgeschlagen — der Entwurf liegt weiter im Postfach.")
    v = vorschau(m)
    log.info("Mail-Freigabe %s: Entwurf gesendet", approval.id)
    return {"gesendet": True, "an": v["an"], "betreff": v["betreff"]}
