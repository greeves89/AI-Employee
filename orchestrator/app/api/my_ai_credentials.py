"""Der eigene Claude-/Codex-Zugang eines Nutzers.

Bis hierher kam der Abo-Zugang aus **einer** Einstellung für die ganze
Installation, und pflegen konnte sie nur ein Administrator. Wer die Plattform
nutzt, arbeitete damit zwangsläufig auf fremde Rechnung — oder gar nicht.

Hier legt jeder seinen eigenen ab. Der Administrator entscheidet über
``allow_team_license``, ob es daneben noch einen gemeinsamen Zugang gibt.

**Jeder sieht und ändert ausschliesslich seinen eigenen.** Kein Administrator-Weg
auf fremde Zugänge, auch nicht lesend: ein Abo-Token ist der Zugang zu einem
bezahlten Konto einer anderen Person. Wer es einsehen könnte, könnte es benutzen.

Das Geheimnis geht **nie** wieder heraus — weder an den Besitzer. Zurück kommt nur,
ob eines hinterlegt ist, wie es heisst und wann es zuletzt funktioniert hat.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import agent_credentials as creds
from app.core.encryption import encrypt_token
from app.db.session import get_db
from app.dependencies import require_auth
from app.models.user_ai_credential import CREDENTIAL_HARNESSES, UserAiCredential

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/me/ai-credentials", tags=["me"])


class CredentialUpsert(BaseModel):
    harness: str = Field(description="claude_code | codex")
    #: Bei Claude Code das OAuth-Token bzw. ein `sk-ant-api…`-Schlüssel, bei
    #: Codex der vollständige Inhalt der `auth.json`.
    secret: str = Field(min_length=8)
    label: str | None = Field(default=None, max_length=120)


def _to_response(row: UserAiCredential) -> dict:
    """Alles ausser dem Geheimnis."""
    return {
        "harness": row.harness,
        "label": row.label,
        "last_status": row.last_status,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("")
async def list_my_credentials(user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Was ich hinterlegt habe — und ob daneben eine Teamlizenz zur Verfügung steht."""
    rows = (await db.execute(
        select(UserAiCredential).where(UserAiCredential.user_id == user.id)
    )).scalars().all()
    have = {r.harness: _to_response(r) for r in rows}
    return {
        "credentials": [
            have.get(h, {"harness": h, "label": None, "last_status": None,
                         "last_used_at": None, "created_at": None})
            for h in CREDENTIAL_HARNESSES
        ],
        # Ohne diese Angabe kann ein Nutzer nicht einschaetzen, ob seine Agenten
        # ohne eigenen Zugang ueberhaupt laufen.
        "team_license_allowed": creds.team_license_allowed(),
    }


@router.put("")
async def upsert_my_credential(
    body: CredentialUpsert,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Eigenen Zugang hinterlegen oder ersetzen.

    Wirkt erst, wenn die Agenten des Nutzers **neu erstellt** werden — der Zugang
    geht als Umgebungsvariable in den Container und wird beim Start gelesen. Das
    steht auch in der Antwort, damit niemand auf eine sofortige Wirkung wartet.
    """
    harness = (body.harness or "").strip()
    if harness not in CREDENTIAL_HARNESSES:
        raise HTTPException(status_code=400,
                            detail=f"Unbekannte Laufzeit: {body.harness}")
    secret = body.secret.strip()
    if not secret:
        raise HTTPException(status_code=400, detail="Zugang ist leer")

    row = (await db.execute(
        select(UserAiCredential).where(
            UserAiCredential.user_id == user.id,
            UserAiCredential.harness == harness,
        )
    )).scalar_one_or_none()

    if row is None:
        row = UserAiCredential(user_id=user.id, harness=harness)
        db.add(row)
    row.secret_encrypted = encrypt_token(secret)
    row.label = (body.label or "").strip() or None
    # Ein neuer Zugang ist ungeprueft. „ok" zu behaupten, waere geraten — und beim
    # naechsten Fehlschlag suchte jemand an der falschen Stelle.
    row.last_status = "unknown"
    row.last_used_at = None
    await db.commit()
    await db.refresh(row)

    logger.info("[Zugang] %s hat eigenen %s-Zugang hinterlegt", user.id, harness)
    return {
        **_to_response(row),
        "hint": "Wirkt, sobald deine Agenten neu erstellt werden.",
    }


@router.delete("/{harness}")
async def delete_my_credential(
    harness: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Eigenen Zugang entfernen.

    Danach greift wieder die Teamlizenz — falls der Administrator sie erlaubt.
    Sonst laufen die eigenen Agenten dieser Laufzeit ohne Zugang, und das sagt
    die Antwort ausdrücklich, statt es dem Nutzer später als Fehler zu zeigen.
    """
    row = (await db.execute(
        select(UserAiCredential).where(
            UserAiCredential.user_id == user.id,
            UserAiCredential.harness == harness,
        )
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Kein eigener Zugang hinterlegt")
    await db.delete(row)
    await db.commit()

    allowed = creds.team_license_allowed()
    return {
        "harness": harness,
        "deleted": True,
        "falls_back_to_team_license": allowed,
        "hint": ("Es gilt wieder die Teamlizenz." if allowed else
                 "Ohne eigenen Zugang laufen deine Agenten dieser Laufzeit "
                 "ohne Anmeldung — die Teamlizenz ist gesperrt."),
    }


async def mark_status(db: AsyncSession, user_id: str, harness: str, status: str) -> None:
    """Vom echten Lauf gemeldet, nicht geraten.

    Wird aufgerufen, wenn ein Agent mit diesem Zugang gestartet ist bzw. an einer
    Anmeldung gescheitert ist. Damit sieht der Nutzer in seiner Übersicht, ob sein
    Abo noch trägt — die häufigste Ursache für „mein Agent tut nichts" ist ein
    abgelaufenes Token.
    """
    row = (await db.execute(
        select(UserAiCredential).where(
            UserAiCredential.user_id == user_id,
            UserAiCredential.harness == harness,
        )
    )).scalar_one_or_none()
    if row is None:
        return
    row.last_status = status
    row.last_used_at = datetime.now(timezone.utc)
    await db.commit()
