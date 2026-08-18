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
from app.dependencies import get_redis_service, require_auth
from app.services.oauth_service import OAuthService
from app.services.redis_service import RedisService
from app.models.user_ai_credential import CREDENTIAL_HARNESSES, UserAiCredential

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/me/ai-credentials", tags=["me"])



def _oauth_service(
    db: AsyncSession = Depends(get_db),
    redis: RedisService = Depends(get_redis_service),
) -> OAuthService:
    """Dieselbe Konstruktion wie in ``integrations.py``.

    Der erste Anlauf baute Redis von Hand und uebergab nur ihn — ``OAuthService``
    erwartet aber ``(db, redis)``. Ergebnis war ein 500 beim Klick auf
    „Mit Claude anmelden". Die vorhandene Abhaengigkeit zu benutzen ist nicht nur
    kuerzer, sie kann auch nicht in dieser Weise falsch sein.
    """
    return OAuthService(db, redis)


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


def _eigene_zugaenge_erlaubt() -> None:
    """Sperrt alles, was einen persoenlichen Zugang ANLEGT.

    Der Kunde hat am 18.08.2026 zur Bedingung gemacht, dass das zentral
    steuerbar ist. Der Schalter wirkt bereits im Zugangs-Pfad
    (``agent_credentials.resolve``) — hier kommt er ein zweites Mal, damit man
    gar nicht erst etwas hinterlegt, das anschliessend wirkungslos waere. Ein
    Anmeldevorgang, der scheinbar klappt und dann nichts bewirkt, ist
    schlimmer als eine klare Absage.

    Lesen und LOESCHEN bleiben immer erlaubt: wer seinen Zugang loswerden will,
    darf daran nicht gehindert werden, nur weil ein Administrator die Funktion
    inzwischen zugemacht hat.
    """
    from app.core.agent_credentials import personal_credentials_allowed

    if not personal_credentials_allowed():
        raise HTTPException(
            status_code=403,
            detail=("Eigene KI-Zugaenge sind in dieser Anlage nicht freigegeben. "
                    "Dein Administrator kann das unter Einstellungen aendern."),
        )


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
        # Damit die Oberflaeche den Bereich ausblenden kann, statt Knoepfe
        # anzubieten, die anschliessend mit 403 abgewiesen werden.
        "personal_allowed": creds.personal_credentials_allowed(),
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
    _eigene_zugaenge_erlaubt()
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

# ── Anmeldung wie beim Administrator, nur landet sie beim Nutzer ─────────────
#
# Bis 2026-08-15 konnte ein Nutzer seinen Zugang nur als Text einfuegen: bei
# Claude ein Token aus ``claude setup-token``, bei Codex der Inhalt der
# ``auth.json``. Der Administrator hatte laengst den bequemen Weg — Knopf,
# Browser-Anmeldung, fertig. Zwei verschiedene Verfahren fuer dieselbe Sache,
# und das umstaendlichere fuer den, der sich am wenigsten auskennt.
#
# Der Ablauf ist derselbe wie unter Einstellungen → Modelle. Der Unterschied
# sitzt am Ende: das Ergebnis wird NICHT als plattformweite Integration
# gespeichert, sondern als persoenlicher Zugang dieses Nutzers — genau die
# Ablage, aus der ``agent_credentials`` liest.


class OAuthExchange(BaseModel):
    code: str = Field(description="Code von der Anthropic-Seite (oder die ganze Callback-URL)")
    state: str = Field(default="", description="Falls im eingefuegten Text enthalten")
    label: str | None = Field(default=None, max_length=120)


@router.post("/anthropic/start")
async def start_anthropic_login(
    user=Depends(require_auth),
    service: "OAuthService" = Depends(_oauth_service),
):
    """Anmeldung starten — liefert die Adresse, die im Browser geoeffnet wird."""
    _eigene_zugaenge_erlaubt()
    try:
        auth_url = await service.generate_auth_url("anthropic", user_id=user.id)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"auth_url": auth_url}


@router.post("/anthropic/exchange")
async def exchange_anthropic_login(
    body: OAuthExchange,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    service: "OAuthService" = Depends(_oauth_service),
):
    """Den eingefuegten Code eintauschen und als EIGENEN Zugang hinterlegen.

    Der Austausch selbst ist derselbe wie beim Administrator — er legt dabei
    zusaetzlich die uebliche Integration an. Entscheidend ist der Schritt
    danach: das Zugangstoken wandert in ``user_ai_credentials``, denn nur von
    dort liest ``agent_credentials`` beim Bau eines Containers. Ohne diesen
    Schritt haette der Nutzer sich erfolgreich angemeldet — und seine Agenten
    liefen trotzdem ohne seinen Zugang.
    """
    _eigene_zugaenge_erlaubt()
    from app.core.encryption import decrypt_token

    code = (body.code or "").split("#")[0].strip()
    if not code:
        raise HTTPException(status_code=400, detail="Kein Code eingefuegt")

    try:
        integration = await service.exchange_code("anthropic", code, body.state)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    token = decrypt_token(integration.access_token_encrypted)
    row = (await db.execute(
        select(UserAiCredential).where(
            UserAiCredential.user_id == user.id,
            UserAiCredential.harness == "claude_code",
        )
    )).scalar_one_or_none()
    if row is None:
        row = UserAiCredential(user_id=user.id, harness="claude_code")
        db.add(row)
    row.secret_encrypted = encrypt_token(token)
    row.label = (body.label or "").strip() or integration.account_label or "Claude-Abo"
    row.last_status = "ok"
    await db.commit()
    return {"status": "connected", "label": row.label,
            "hint": "Wirkt, sobald deine Agenten neu erstellt werden."}


@router.post("/codex/start")
async def start_codex_login(user=Depends(require_auth)):
    """Geraeteanmeldung bei ChatGPT starten — liefert Code und Adresse.

    Den Abschluss macht der Nutzer wie bisher, indem er den Inhalt seiner
    ``auth.json`` einfuegt (PUT oben). Anders als bei Anthropic gibt es hier
    keinen Code zum Eintauschen: Codex legt die Datei lokal an.
    """
    _eigene_zugaenge_erlaubt()
    from app.services.codex_device_auth_service import codex_device_auth_service

    try:
        # Fuer DIESEN Nutzer — der Dienst legt das Ergebnis dann in seinem
        # persoenlichen Zugang ab statt als Zugang der ganzen Anlage.
        session = await codex_device_auth_service.start(for_user_id=user.id)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "session_id": session.id,
        "verification_uri": session.verification_uri,
        "user_code": session.code,
        "expires_at": session.expires_at.isoformat(),
        "status": session.status,
    }

@router.get("/codex/status/{session_id}")
async def codex_login_status(session_id: str, user=Depends(require_auth)):
    """Laeuft die Anmeldung noch, ist sie durch, oder ist sie gescheitert?

    Der Nutzer bekommt bei dieser Anmeldung nie eine Datei zu sehen: Codex legt
    sie im Container an, der Dienst liest sie und raeumt sie weg. Die Oberflaeche
    fragt deshalb hier nach, statt den Nutzer nach etwas zu fragen, das er nicht
    hat — genau daran scheiterte der erste Anlauf.
    """
    from app.services.codex_device_auth_service import codex_device_auth_service

    session = await codex_device_auth_service.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Anmeldung nicht gefunden")
    # Fremde Anmeldungen gehen niemanden etwas an — auch nicht ihr Zustand.
    if session.for_user_id != user.id:
        raise HTTPException(status_code=404, detail="Anmeldung nicht gefunden")
    return {
        "status": session.status,
        "account_label": session.account_label,
        "error": session.error,
        "user_code": session.code if session.status == "pending" else None,
        "verification_uri": session.verification_uri if session.status == "pending" else None,
    }
