"""Eigenes Microsoft-365-Konto eines Agenten (Plan docs/plaene/agenten-identitaet-m365.md, Phase 1).

Ein Agent kann ein eigenes, lizenziertes Microsoft-Konto bekommen. Der Besitzer meldet sich
einmal mit den Zugangsdaten des Agenten-Nutzers an. Danach liest und schreibt der Agent als
er selbst (delegiertes Token, ``/me`` ist dann der Agent). Es ist eine Zeile in
``oauth_integrations`` mit ``agent_id``; ``user_id`` ist der Besitzer (Spur, Meldungen beim
Erneuern).

Hier liegt die EINE Stelle, die entscheidet, mit welchem Microsoft-Token ein Agent arbeitet
(``token_fuer``). Hat er ein eigenes Konto, dann nur damit — scheitert dessen Erneuerung,
gibt es KEINEN stillen Rückfall auf das Konto des Besitzers (der Agent handelte sonst
plötzlich als sein Besitzer). Ohne eigenes Konto wie bisher: das des Besitzers.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

logger = logging.getLogger(__name__)

ANBIETER = "microsoft"


async def _zeile(db, agent_id: str):
    from app.models.oauth_integration import OAuthIntegration, OAuthProvider

    return (await db.execute(select(OAuthIntegration).where(
        OAuthIntegration.provider == OAuthProvider(ANBIETER),
        OAuthIntegration.agent_id == agent_id))).scalar_one_or_none()


async def darf_verbinden(db, agent, user) -> bool:
    """Nur Besitzer oder Admin — wie Anonymisierer und Browser-Freigabeliste."""
    from app.models.user import UserRole

    return getattr(user, "role", None) == UserRole.ADMIN or (agent is not None and agent.user_id == user.id)


async def speichern(svc, provider_name: str, user_id: str | None, agent_id: str, token_data: dict):
    """Rückruf nach der Anmeldung: Konto am Agenten speichern — nach erneuter Prüfung.

    * Nur Microsoft, nur ein Agent, den der Anmeldende besitzt (oder ein Admin).
    * Hat sich der Besitzer versehentlich mit SEINEM Konto angemeldet (statt mit dem des
      Agenten), wird abgelehnt: Der Agent handelte sonst unbemerkt als sein Besitzer.
    """
    from app.models.agent import Agent
    from app.models.user import User

    if provider_name != ANBIETER:
        raise ValueError("Ein eigenes Konto gibt es nur für Microsoft 365")
    db = svc.db
    agent = await db.get(Agent, agent_id)
    nutzer = await db.get(User, user_id) if user_id else None
    if agent is None or nutzer is None or not await darf_verbinden(db, agent, nutzer):
        raise ValueError("Nur der Besitzer des Agenten darf sein Konto verbinden")
    if not agent.user_id:
        raise ValueError("Der Agent hat keinen Besitzer — bitte zuerst einen Besitzer zuweisen")
    # ERST prüfen, dann speichern: Eine abgelehnte Anmeldung darf ein bestehendes,
    # funktionierendes Konto des Agenten nicht überschreiben.
    konto = await svc.kontoname(provider_name, token_data.get("access_token", ""))
    grund = await _eigenes_konto(db, konto, token_data, {agent.user_id, nutzer.id})
    if grund:
        raise ValueError(grund)
    zeile = await svc.persist_tokens(provider_name, agent.user_id, token_data, agent_id=agent.id,
                                     account_label=konto)
    await _protokollieren(db, nutzer.id, agent.id, f"Microsoft-Konto des Agenten verbunden ({zeile.account_label or '?'})")
    return zeile


def _sub(token_data: dict) -> str | None:
    """``sub`` aus dem id_token. Ohne Signaturprüfung: Es kam eben direkt vom Token-Endpunkt
    (TLS), und es dient nur einem zusätzlichen Abgleich, nie der Anmeldung."""
    import base64
    import json

    teile = str(token_data.get("id_token") or "").split(".")
    if len(teile) != 3:
        return None
    try:
        nutzlast = json.loads(base64.urlsafe_b64decode(teile[1] + "=" * (-len(teile[1]) % 4)))
    except Exception:  # noqa: BLE001
        return None
    sub = nutzlast.get("sub") if isinstance(nutzlast, dict) else None
    return sub if isinstance(sub, str) and sub else None


async def _eigenes_konto(db, konto: str | None, token_data: dict, menschen: set) -> str | None:
    """Grund zur Ablehnung, falls das angemeldete Konto keins eines Agenten sein kann.

    * Nicht feststellbar, wer sich angemeldet hat → ablehnen (sonst ließe sich der Abgleich
      mit einem gestörten ``/me`` umgehen).
    * Gleiche Adresse wie das Microsoft-Konto oder die Anmelde-Adresse von Besitzer oder
      Anmeldendem → ablehnen.
    * Gleiches ``sub`` wie deren Anmeldung über Microsoft (nur gleich, wenn dieselbe
      Entra-App) → ablehnen.
    """
    from app.models.oauth_integration import OAuthIntegration, OAuthProvider
    from app.models.user import User

    if not (konto or "").strip():
        return "Das angemeldete Microsoft-Konto ließ sich nicht feststellen. Bitte erneut verbinden."
    menschen = {m for m in menschen if m}
    adressen = set((await db.execute(select(OAuthIntegration.account_label).where(
        OAuthIntegration.provider == OAuthProvider(ANBIETER),
        OAuthIntegration.agent_id.is_(None),
        OAuthIntegration.user_id.in_(menschen)))).scalars().all())
    nutzer = (await db.execute(select(User.email, User.sso_subject).where(User.id.in_(menschen)))).all()
    adressen |= {e for e, _ in nutzer}
    eigen = "Das ist dein eigenes Microsoft-Konto. Bitte mit dem Konto des Agenten anmelden."
    if konto.strip().casefold() in {(a or "").strip().casefold() for a in adressen if a}:
        return eigen
    sub = _sub(token_data)
    if sub and sub in {s for _, s in nutzer if s}:
        return eigen
    return None


async def trennen(db, redis, agent, user) -> bool:
    from app.services.oauth_service import OAuthService

    if await _zeile(db, agent.id) is None:
        return False
    from app.core import graph_abos

    await graph_abos.entfernen(db, agent.id)   # Abo bei Microsoft löschen, solange das Token noch gilt
    try:   # gemerkte Teams-Kennung des alten Kontos vergessen (services/teams_gateway)
        if redis is not None and getattr(redis, "client", None) is not None:
            await redis.client.delete(f"teams:ich:{agent.id}")
    except Exception:  # noqa: BLE001
        pass
    if graph_abos.an(agent.config):
        # Aus, nicht nur ruhend: Ein späteres Verbinden soll nicht still wieder Läufe auslösen.
        agent.config = graph_abos.mit_einstellung(agent.config, False)
        await db.commit()
    await OAuthService(db, redis).disconnect(ANBIETER, agent.user_id, agent_id=agent.id)
    await _protokollieren(db, user.id, agent.id, "Microsoft-Konto des Agenten getrennt")
    return True


async def stand(db, agent, redis=None) -> dict:
    """Für die Oberfläche: verbunden? mit welchem Konto? trägt die Anmeldung noch?

    ``status`` wie bei den Integrationen (core/integration_health): ``connected``,
    ``refresh_failing`` (Erneuerung scheitert, läuft noch) oder ``expired`` (neu anmelden).
    """
    from app.core import integration_health

    z = await _zeile(db, agent.id)
    if z is None:
        return {"verbunden": False, "konto": None, "status": integration_health.STATUS_DISCONNECTED}
    fehler = await integration_health.get_refresh_failure(redis, z.id) if redis is not None else None
    return {"verbunden": True, "konto": z.account_label,
            "status": integration_health.integration_status(True, z.expires_at, fehler)}


async def uebersicht(db) -> list[dict]:
    """Alle Agenten mit eigenem Konto — für den Sicherheitsstatus (nur Zeitprüfung, ohne Redis)."""
    from app.core import integration_health
    from app.models.agent import Agent
    from app.models.oauth_integration import OAuthIntegration, OAuthProvider

    zeilen = (await db.execute(
        select(Agent.id, Agent.name, OAuthIntegration.account_label, OAuthIntegration.expires_at)
        .join(OAuthIntegration, OAuthIntegration.agent_id == Agent.id)
        .where(OAuthIntegration.provider == OAuthProvider(ANBIETER)))).all()
    return [{"agent_id": aid, "name": name, "konto": konto,
             "status": integration_health.integration_status(True, ablauf, None)}
            for aid, name, konto, ablauf in zeilen]


async def token_fuer(db, agent) -> str | None:
    """DAS Microsoft-Token, mit dem dieser Agent arbeitet — ``None``, wenn keins geht.

    Eigenes Konto vorhanden → nur dieses (kein Rückfall). Sonst das des Besitzers.
    """
    from app.services.oauth_service import OAuthService

    if agent is None or not agent.user_id:
        return None
    svc = OAuthService(db, None)
    if await _zeile(db, agent.id) is not None:
        try:
            return await svc.get_valid_token(ANBIETER, agent.user_id, agent_id=agent.id)
        except Exception as e:  # noqa: BLE001 — sichtbar machen, nicht auf den Besitzer ausweichen
            logger.warning("Microsoft-Konto des Agenten %s nicht nutzbar: %s", agent.id, e)
            return None
    try:
        return await svc.get_valid_token(ANBIETER, agent.user_id)
    except Exception as e:  # noqa: BLE001
        logger.debug("Kein Microsoft-Zugang für Agent %s: %s", agent.id, e)
        return None


async def _protokollieren(db, user_id: str, agent_id: str, text: str) -> None:
    try:
        from app.core.audit import protokolliere
        from app.models.audit_log import AuditEventType

        await protokolliere(db, AuditEventType.SETTINGS_CHANGED, user_id=user_id, agent_id=agent_id,
                            command=text, meta={"aktion": "agenten_konto"})
        await db.commit()   # protokolliere merkt nur vor
    except Exception:  # noqa: BLE001 — das Protokoll darf das Verbinden nicht verhindern
        logger.warning("Agenten-Konto: Prüfprotokoll nicht geschrieben", exc_info=True)
