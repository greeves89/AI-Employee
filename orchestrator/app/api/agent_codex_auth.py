"""Rueckweg fuer den rotierten Codex-Zugang eines Agenten.

Der ChatGPT-Refresh-Token ist einmalig: die Codex-CLI tauscht ihn bei jeder
Erneuerung gegen einen neuen und schreibt den neuen in ihre ``auth.json``. Der
Zugang, mit dem ein Container gestartet wurde, ist aber nur eine **Abschrift**
aus der Datenbank. Ohne diesen Rueckweg stirbt die Erneuerung mit dem Container,
und der naechste Start spielt die bereits verbrauchte Fassung erneut ein — der
Anbieter antwortet darauf mit ``refresh_token_reused`` und die Laufzeit ist
dauerhaft tot (Issue #646).

Vertrauensgrenze: **wer** schreibt, steht im Agenten-Token, nicht in der
Nutzlast. Der Besitzer wird serverseitig aus dem Agenten aufgeloest, und
geschrieben wird ausschliesslich ein **bereits vorhandener** eigener
Codex-Zugang dieses Besitzers. Ein Agent kann damit weder einen Zugang anlegen,
wo keiner war, noch den gemeinsamen Zugang der Anlage anfassen.
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import decrypt_token, encrypt_token
from app.db.session import get_db
from app.dependencies import verify_agent_token
from app.models.agent import Agent
from app.models.user_ai_credential import UserAiCredential
from app.services.codex_auth_service import _access_token_exp

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent-codex-auth", tags=["agents"])


class CodexAuthRotation(BaseModel):
    #: Der vollstaendige Inhalt der ``auth.json``, wie die CLI sie nach der
    #: Erneuerung hinterlassen hat.
    auth_json: dict = Field(description="Inhalt der auth.json nach der Rotation")


def rotation_rejection(stored: dict, incoming: dict) -> str | None:
    """Warum die eingehende Fassung NICHT uebernommen werden darf — sonst ``None``.

    Reine Funktion, damit die Grenzfaelle ohne Datenbank pruefbar sind. Geprueft
    wird nur, was den Zugang kaputtmachen koennte: eine unbrauchbare Form, ein
    **fremdes Konto** und ein **aelterer** Token. Alles drei wuerde den Zugang
    des Besitzers ueberschreiben, ohne dass er es merkt.
    """
    # Eine echte auth.json ist wenige Kilobyte gross. Alles darueber landete
    # verschluesselt und dauerhaft in der Zugangszeile des Nutzers.
    if len(json.dumps(incoming)) > 64 * 1024:
        return "auth.json unplausibel gross"

    tokens = incoming.get("tokens")
    if not isinstance(tokens, dict):
        return "auth.json ohne tokens-Abschnitt"
    for field in ("access_token", "refresh_token"):
        value = tokens.get(field)
        if not isinstance(value, str) or not value.strip():
            return f"auth.json ohne {field}"

    stored_tokens = stored.get("tokens") if isinstance(stored.get("tokens"), dict) else {}
    stored_account = stored_tokens.get("account_id")
    if stored_account and tokens.get("account_id") != stored_account:
        return "Zugang gehoert zu einem anderen Konto"

    stored_exp = _access_token_exp(stored)
    incoming_exp = _access_token_exp(incoming)
    if stored_exp and incoming_exp and incoming_exp < stored_exp:
        return "eingehender Token ist aelter als der gespeicherte"
    return None


@router.post("")
async def store_rotated_codex_auth(
    body: CodexAuthRotation,
    auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Den erneuerten Codex-Zugang des aufrufenden Agenten zurueckschreiben."""
    agent_id = auth["agent_id"]
    agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if agent is None or not agent.user_id:
        raise HTTPException(status_code=404, detail="Agent ohne Besitzer")

    row = (await db.execute(
        select(UserAiCredential).where(
            UserAiCredential.user_id == agent.user_id,
            UserAiCredential.harness == "codex",
        )
    )).scalar_one_or_none()
    if row is None:
        # Der Agent laeuft auf dem gemeinsamen Zugang der Anlage. Den pflegt der
        # Orchestrator zentral (CodexAuthService.ensure_fresh) — ein Agent darf
        # ihn nicht ueberschreiben, sonst koennte einer die ganze Anlage kippen.
        raise HTTPException(status_code=404, detail="Kein eigener Codex-Zugang hinterlegt")

    try:
        stored = json.loads(decrypt_token(row.secret_encrypted))
    except Exception:  # noqa: BLE001 — unlesbar gespeichert: dann ist jede Erneuerung besser
        stored = {}

    reason = rotation_rejection(stored, body.auth_json)
    if reason:
        logger.warning("[Zugang] Codex-Rotation von %s abgelehnt: %s", agent_id, reason)
        raise HTTPException(status_code=400, detail=reason)

    stored_refresh = (stored.get("tokens") or {}).get("refresh_token")
    if stored_refresh and stored_refresh == body.auth_json["tokens"]["refresh_token"]:
        return {"stored": False, "reason": "unveraendert"}

    row.secret_encrypted = encrypt_token(json.dumps(body.auth_json))
    row.last_status = "ok"
    await db.commit()
    logger.info("[Zugang] Codex-Rotation von %s uebernommen", agent_id)
    return {"stored": True}
