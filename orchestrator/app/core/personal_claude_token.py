"""Das eigene Claude-Abo eines Nutzers frisch halten (#999).

Bis hierher lag im eigenen Zugang nur eine **Abschrift** des Access-Tokens, ohne
Refresh-Token und ohne Ablaufzeit. Anthropic lässt dieses Token nach wenigen
Stunden ablaufen. Danach antwortete jeder Agent des Besitzers nur noch mit
„401 OAuth access token has expired", und auch Neuerstellen half nicht: es
spielte dieselbe tote Abschrift wieder ein.

Jetzt liegen Access-Token, Refresh-Token und Ablaufzeit zusammen im Zugang, und
diese Stelle erneuert kurz vor dem Ablauf. Gefragt wird sie von zwei Seiten:

* beim **Erstellen** eines Containers (``agent_manager._owner_credential_env``),
* vom **laufenden** Agenten über ``GET /agents/{id}/claude-token`` — er fragt
  regelmäßig nach, statt bis zum nächsten Neuerstellen auf der Abschrift zu sitzen.

Erneuert wird nur hier, im Orchestrator. Der Agent bekommt nie den Refresh-Token:
rotiert ihn Anthropic beim Erneuern, ist der alte tot, und zwei Erneuerer derselben
Familie würden sich gegenseitig aussperren. Aus demselben Grund sperrt die Abfrage
die Zeile (``FOR UPDATE``), bevor sie erneuert.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import decrypt_token, encrypt_token
from app.models.user_ai_credential import UserAiCredential

logger = logging.getLogger(__name__)

#: So lange vor dem Ablauf wird erneuert. Der Agent fragt jede Minute nach; mit
#: 15 Minuten Vorlauf sieht er das neue Token lange bevor das alte abläuft.
REFRESH_MARGIN = timedelta(minutes=15)

#: Antworten, bei denen der Refresh-Token selbst tot ist. Hier hilft nur eine neue
#: Anmeldung. Alles andere (5xx, Netz) ist vorübergehend und wird beim nächsten
#: Abruf erneut versucht.
_DEAD_GRANT_STATUS = {400, 401, 403}

#: Angenommene Lebensdauer, wenn der Anbieter kein ``expires_in`` mitschickt.
DEFAULT_LIFETIME_SECONDS = 3600

RefreshCall = Callable[[str], Awaitable[tuple[int, dict]]]


class RefreshRejected(Exception):
    """Der Anbieter hat die Erneuerung abgelehnt; ``status`` ist der HTTP-Status."""

    def __init__(self, status: int):
        super().__init__(f"Erneuerung abgelehnt: HTTP {status}")
        self.status = status


def needs_refresh(expires_at: datetime | None, now: datetime,
                  margin: timedelta = REFRESH_MARGIN) -> bool:
    """Ist es Zeit zu erneuern? Ohne bekannte Ablaufzeit: nein.

    Ein eingefügtes Token (``claude setup-token``, ein Jahr gültig) oder ein
    API-Schlüssel hat keine Ablaufzeit im Zugang. Für die gibt es auch nichts zu
    erneuern, und blind zu erneuern hiesse, ohne Refresh-Token anzufragen.
    """
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at - now <= margin


def apply_token_data(row: UserAiCredential, token_data: dict, now: datetime) -> None:
    """Eine Token-Antwort des Anbieters in den Zugang übernehmen.

    Ohne ``access_token`` wird nichts angefasst: eine kaputte Antwort darf einen
    noch gültigen Zugang nicht still überschreiben. Ohne neuen Refresh-Token
    bleibt der alte stehen (nicht jeder Anbieter rotiert bei jeder Erneuerung).
    """
    access = token_data.get("access_token")
    if not access or not isinstance(access, str):
        raise ValueError("Antwort ohne access_token")
    expires_in = token_data.get("expires_in")
    # Erst alles ausrechnen, dann zuweisen: scheitert ``int()`` an einer kaputten
    # Antwort, darf der Zugang nicht halb überschrieben zurückbleiben.
    # Ohne Angabe eine vorsichtige Stunde annehmen: ``None`` hiesse „läuft nie
    # ab" — dann würde nie wieder erneuert, und der Fehler aus #999 wäre zurück.
    expires_at = now + timedelta(seconds=int(expires_in) if expires_in else DEFAULT_LIFETIME_SECONDS)
    refresh = token_data.get("refresh_token")
    row.secret_encrypted = encrypt_token(access)
    if refresh:
        row.refresh_token_encrypted = encrypt_token(refresh)
    row.expires_at = expires_at
    row.last_status = "ok"


async def anthropic_refresh_call(refresh_token: str) -> tuple[int, dict]:
    """Die echte Erneuerung bei Anthropic — derselbe Aufruf wie für die Teamlizenz."""
    import httpx

    from app.core.oauth_providers import apply_tenant, get_provider, get_provider_client_id
    from app.core.oauth_retry import post_refresh_with_retry

    provider = get_provider("anthropic")
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await post_refresh_with_retry(
            client,
            apply_tenant(provider.token_url),
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": get_provider_client_id(provider),
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
    try:
        body = response.json()
    except ValueError:
        body = {}
    return response.status_code, body if isinstance(body, dict) else {}


async def current_claude_token(
    db: AsyncSession,
    user_id: str | None,
    *,
    now: datetime | None = None,
    refresh_call: RefreshCall | None = None,
) -> str | None:
    """Das aktuell gültige Token aus dem eigenen Claude-Zugang — bei Bedarf erneuert.

    ``None``, wenn der Nutzer keinen eigenen Claude-Zugang hat. Scheitert die
    Erneuerung, kommt das bisherige Token zurück: es kann noch Minuten gelten, und
    ein Agent ohne jedes Token wäre nicht besser dran. Ist der Refresh-Token tot,
    steht danach ``last_status = "auth_failed"`` im Zugang — das sieht der Besitzer
    in seinen Einstellungen.
    """
    if not user_id:
        return None
    now = now or datetime.now(timezone.utc)
    refresh_call = refresh_call or anthropic_refresh_call

    stmt = (
        select(UserAiCredential)
        .where(UserAiCredential.user_id == str(user_id))
        .where(UserAiCredential.harness == "claude_code")
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None or not row.secret_encrypted:
        return None

    if row.refresh_token_encrypted and needs_refresh(row.expires_at, now):
        # Erst jetzt sperren und neu lesen: ein zweiter Abruf, der gleichzeitig
        # hier ankam, hat vielleicht schon erneuert — dann ist der alte
        # Refresh-Token verbraucht, und ein zweiter Versuch damit würde den
        # Zugang als tot markieren, obwohl er gerade frisch ist.
        row = (await db.execute(
            stmt.with_for_update().execution_options(populate_existing=True)
        )).scalar_one_or_none()
        if row is not None and row.refresh_token_encrypted and needs_refresh(row.expires_at, now):
            await _refresh(db, row, now, refresh_call)
        if row is None or not row.secret_encrypted:
            return None

    try:
        return decrypt_token(row.secret_encrypted)
    except Exception:  # noqa: BLE001 — Schlüssel gewechselt oder Datensatz kaputt
        logger.warning("[Zugang] Eigener Claude-Zugang von %s nicht entschluesselbar", user_id)
        return None


async def _refresh(db: AsyncSession, row: UserAiCredential, now: datetime,
                   refresh_call: RefreshCall) -> None:
    try:
        status, body = await refresh_call(decrypt_token(row.refresh_token_encrypted))
        if status != 200:
            raise RefreshRejected(status)
        apply_token_data(row, body, now)
    except RefreshRejected as exc:
        if exc.status in _DEAD_GRANT_STATUS:
            row.last_status = "auth_failed"
            logger.warning("[Zugang] Eigener Claude-Zugang von %s abgelehnt (HTTP %s) — "
                           "neue Anmeldung noetig", row.user_id, exc.status)
        else:
            logger.warning("[Zugang] Erneuerung fuer %s voruebergehend gescheitert (HTTP %s)",
                           row.user_id, exc.status)
        await db.commit()
        return
    except Exception as exc:  # noqa: BLE001 — Netz, kaputte Antwort: beim naechsten Abruf erneut
        logger.warning("[Zugang] Erneuerung fuer %s gescheitert: %s", row.user_id,
                       type(exc).__name__)
        # Kein rollback: der Zugang ist unverändert (apply_token_data weist erst
        # zu, wenn alles gerechnet ist), und ein rollback liesse die Attribute
        # verfallen — das anschliessende Lesen des Tokens wäre dann ein Lazy-Load
        # ausserhalb des Greenlets. commit gibt nur die Zeilensperre frei.
        await db.commit()
        return
    await db.commit()
    logger.info("[Zugang] Eigener Claude-Zugang von %s erneuert", row.user_id)
