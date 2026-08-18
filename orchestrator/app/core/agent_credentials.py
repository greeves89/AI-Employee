"""Welcher Zugang gilt für diesen Agenten — eine Reihenfolge, eine Stelle.

Bis hierher kam der Claude-Code- bzw. Codex-Zugang aus **einer** Einstellung für
die ganze Installation. Jetzt gibt es drei Möglichkeiten, und die Reihenfolge
zwischen ihnen ist die eigentliche Entscheidung:

1. **Der eigene Zugang des Besitzers.** Wer sein Abo hinterlegt hat, arbeitet
   damit. Nicht der gerade Eingeloggte zählt, sondern der **Besitzer des
   Agenten** — ein Agent arbeitet auch nachts weiter, wenn niemand angemeldet ist.
2. **Die Teamlizenz** — aber nur, wenn der Administrator sie freigegeben hat.
   Der Schalter ist der Punkt, an dem eine Firma entscheidet: „unser Konto ist
   für alle da" oder „jeder bringt sein eigenes mit".
3. **Nichts.** Dann bekommt der Container keinen Zugang, und der Agent sagt das
   auch — statt mit einer Fehlermeldung zu sterben, die niemand deuten kann.

Warum getrennte Zugänge mehr sind als eine Bequemlichkeit: alle Codex-Agenten
teilten sich einen rotierenden Refresh-Token. Erneuert ihn einer, sind die
anderen tot — deshalb muss das Neuerstellen bis heute serialisiert werden.
Getrennte Zugänge sind getrennte Token-Familien; der Ausfall eines Abos trifft
dann genau einen Agenten.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user_ai_credential import UserAiCredential

logger = logging.getLogger(__name__)

#: Woher der Zugang kam. Wird an den Aufrufer zurückgegeben, damit die Oberfläche
#: es anzeigen und die Kostenrechnung es unterscheiden kann.
SOURCE_PERSONAL = "personal"
SOURCE_TEAM = "team"
SOURCE_NONE = "none"


def harness_of(mode: str | None, model_provider: str | None) -> str | None:
    """Welche Laufzeit braucht hier einen Abo-Zugang?

    Dieselbe Umschreibung wie im Agent-Manager: ein „claude_code"-Agent auf dem
    Codex-Anbieter IST ein Codex-Agent. Stünde sie hier anders, bekäme er den
    falschen Zugang untergeschoben.
    """
    if (model_provider or settings.model_provider) == "codex":
        return "codex"
    if (mode or "claude_code") == "claude_code":
        return "claude_code"
    return None  # custom_llm: der Zugang haengt am AI-Konto, nicht an einem Abo


async def personal_credential(db: AsyncSession, user_id: str | None,
                              harness: str) -> str | None:
    """Der eigene Zugang dieses Nutzers — entschlüsselt — oder ``None``."""
    if not user_id or not harness:
        return None
    row = (await db.execute(
        select(UserAiCredential)
        .where(UserAiCredential.user_id == str(user_id))
        .where(UserAiCredential.harness == harness)
    )).scalar_one_or_none()
    if not row or not row.secret_encrypted:
        return None
    try:
        from app.core.encryption import decrypt_token
        return decrypt_token(row.secret_encrypted)
    except Exception:  # noqa: BLE001
        # Schlüssel gewechselt oder Datensatz beschädigt. Auf die Teamlizenz
        # zurückzufallen wäre hier falsch: der Nutzer hat sich bewusst für sein
        # eigenes Abo entschieden, und still auf ein fremdes Konto zu wechseln
        # kostet Geld, das ihm niemand angekündigt hat.
        logger.warning("[Zugang] Eigener %s-Zugang von %s nicht entschluesselbar",
                       harness, user_id)
        return None


def personal_credentials_allowed(user=None) -> bool:
    """Darf DIESER Mitarbeiter sein EIGENES Abo einbinden?

    Woertlich vom Kunden am 18.08.2026:

        „Fuer uns als Unternehmen moechte ich NICHT, dass Mitarbeiter ihre
        privaten Accounts hier hinterlegen und dann mit Firmendaten arbeiten.
        Das muss man quasi global als Admin einstellen koennen." … „Und dass
        man dann fuer User manuell freischalten kann … aber dass man das
        generell unterbinden kann."

    Also zwei Ebenen: ein globaler Schalter mit Vorgabe **AUS**, und die
    Freigabe einzelner Nutzer.

    Die erste Fassung (v1.227.0) hatte die Vorgabe auf AN, mit der Begruendung,
    eine bestehende Anlage duerfe nach einem Update nicht ohne Zugang
    dastehen. Das traegt hier NICHT: private Abos waren vorher gar nicht
    moeglich, es kann also nichts wegbrechen. Fuer eine Sicherheitszusage ist
    „standardmaessig offen" die falsche Richtung.

    ``user=None`` fragt nur den globalen Schalter ab.
    """
    if bool(getattr(settings, "allow_personal_credentials", False)):
        return True
    return bool(getattr(user, "allow_personal_credentials", False))


def team_license_allowed() -> bool:
    """Darf die Teamlizenz benutzt werden?

    Vorgabe **an** — sonst stünde jede bestehende Installation nach dem Update
    ohne Zugang da. Wer es abschalten will, tut das bewusst.
    """
    return bool(getattr(settings, "allow_team_license", True))


async def resolve(db: AsyncSession, *, owner_id: str | None, mode: str | None,
                  model_provider: str | None) -> tuple[str, str | None, str | None]:
    """``(quelle, harness, geheimnis)`` für diesen Agenten.

    ``geheimnis`` ist bei ``claude_code`` das OAuth-Token, bei ``codex`` der
    Inhalt der ``auth.json``. ``None`` heisst: kein Zugang — und der Aufrufer
    soll das sichtbar machen, nicht überspielen.
    """
    harness = harness_of(mode, model_provider)
    if harness is None:
        return SOURCE_NONE, None, None

    # Der Schalter wirkt HIER und nicht nur in der Oberflaeche: sonst waere das
    # Abschalten kosmetisch — bereits hinterlegte Zugaenge liefen weiter, und
    # genau die sollen ja aufhoeren zu wirken.
    # Steht der globale Schalter auf AN, gilt es fuer alle — dann braucht es
    # die Einzelfreigabe gar nicht nachzuschlagen. Der Blick in die Datenbank
    # ist zusaetzlich abgesichert: manche Aufrufer reichen einen schlanken
    # Sitzungs-Ersatz herein, und daran darf die Zugangsaufloesung nicht
    # scheitern.
    besitzer = None
    if not personal_credentials_allowed() and owner_id:
        try:
            from app.models.user import User as _User
            besitzer = await db.get(_User, owner_id)
        except Exception:  # noqa: BLE001
            besitzer = None
    if personal_credentials_allowed(besitzer):
        own = await personal_credential(db, owner_id, harness)
        if own:
            return SOURCE_PERSONAL, harness, own
    else:
        logger.info("[Zugang] %s: eigene Zugaenge sind gesperrt", harness)

    if not team_license_allowed():
        logger.info("[Zugang] %s: kein eigener Zugang, Teamlizenz ist gesperrt", harness)
        return SOURCE_NONE, harness, None

    shared = await team_secret(db, harness)
    return (SOURCE_TEAM, harness, shared) if shared else (SOURCE_NONE, harness, None)


async def team_secret(db: AsyncSession, harness: str) -> str | None:
    """Der Zugang der Installation — dieselbe Quelle, aus der er bisher schon kam.

    Bei Claude Code eine Einstellung, bei Codex die abgelegte ``auth.json`` aus
    der OAuth-Integration. Bewusst KEINE zweite Ablage: es gibt genau einen
    Teamzugang, und er soll auch genau einmal gepflegt werden.
    """
    if harness == "claude_code":
        return settings.anthropic_api_key or settings.claude_code_oauth_token or None
    try:
        from app.core.encryption import decrypt_token
        from app.models.oauth_integration import OAuthIntegration, OAuthProvider

        row = (await db.execute(
            select(OAuthIntegration).where(OAuthIntegration.provider == OAuthProvider.CODEX)
        )).scalar_one_or_none()
        if row and row.access_token_encrypted:
            return decrypt_token(row.access_token_encrypted)
    except Exception:  # noqa: BLE001
        logger.warning("[Zugang] Teamzugang fuer Codex nicht lesbar", exc_info=True)
    return None


def env_for(harness: str, secret: str) -> dict[str, str]:
    """Wie der Zugang im Container ankommt.

    Über Umgebungsvariablen, wie die Vertex-Zugangsdaten auch: der Container
    schreibt sie beim Start in die Datei, die seine CLI erwartet. Das ist kein
    neuer Mechanismus, sondern der vorhandene — und es ist der einzige, der PRO
    Container etwas anderes liefern kann. Die gemeinsame Datei im geteilten
    Verzeichnis kann das per Definition nicht.
    """
    if harness == "claude_code":
        # Ein API-Schluessel und ein OAuth-Token sehen unterschiedlich aus und
        # gehoeren in unterschiedliche Variablen; die CLI waehlt danach.
        key = "ANTHROPIC_API_KEY" if secret.startswith("sk-ant-api") else "CLAUDE_CODE_OAUTH_TOKEN"
        return {key: secret}
    return {"CODEX_AUTH_JSON": secret}
