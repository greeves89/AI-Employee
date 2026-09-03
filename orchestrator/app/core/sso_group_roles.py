"""Gruppen eines IdP-Logins auf eine Rolle dieser Plattform aufloesen.

Ein Ort fuer beide Anmeldewege (SAML, Microsoft-OIDC) statt je einer eigenen
Zuordnungslogik — sonst laufen SAML- und Entra-Kunden irgendwann auseinander, nur
weil an der einen Stelle jemand eine Regel ergaenzt hat und an der anderen nicht.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sso_group_mapping import SsoGroupRoleMapping
from app.models.sso_observed_group import SsoObservedGroup

logger = logging.getLogger(__name__)

# Ohne Deckel waechst die Tabelle mit jedem je gesehenen, neuen Gruppennamen ueber
# alle Nutzer und Logins hinweg unbegrenzt — von Daten, die der Identitaetsanbieter
# liefert, also nicht von uns kontrolliert werden. Grosszuegig, aber endlich.
_MAX_OBSERVED_GROUPS_PER_PROVIDER = 2000


async def resolve_target(db: AsyncSession, provider: str, groups: list[str]) -> tuple[str, str] | None:
    """Welche Zuordnung greift, oder ``None`` wenn keine Gruppe passt.

    Trifft mehr als eine Zuordnung zu, gewinnt die mit der hoechsten ``priority``.
    Ohne Treffer wird NICHTS zurueckgegeben — der Aufrufer aendert dann nichts an der
    Rolle. Eine leere oder unpassende Zuordnung darf niemandem Rechte wegnehmen, die
    ein Mensch von Hand vergeben hat.
    """
    if not groups:
        return None
    lowered = {g.strip().lower() for g in groups if g and g.strip()}
    if not lowered:
        return None

    rows = (await db.execute(
        select(SsoGroupRoleMapping).where(SsoGroupRoleMapping.provider == provider)
    )).scalars().all()

    best: SsoGroupRoleMapping | None = None
    for row in rows:
        if row.group_name.strip().lower() in lowered:
            if best is None or row.priority > best.priority:
                best = row
    if best is None:
        return None
    return (best.target_kind, best.target_value)


async def record_observed_groups(db: AsyncSession, provider: str, groups: list[str]) -> None:
    """Gesehene Gruppennamen festhalten, damit die Verwaltung sie anklickbar macht.

    Fragt NUR die Zeilen ab, die zu den Gruppen DIESES Logins passen — nicht die
    ganze Tabelle. Bei jedem Login die komplette Historie zu laden haette die
    Login-Kosten mit der Tabellengroesse skalieren lassen, obwohl ein einzelner
    Login nur eine Handvoll Gruppen mitbringt (frueher tatsaechlich so gebaut,
    beim Security-Review als Verfuegbarkeitsrisiko auf dem Anmeldeweg gefunden).

    Bewusst fehlertolerant und ohne eigenen Commit: laeuft im selben Zug wie der
    Login und darf ihn unter keinen Umstaenden zum Scheitern bringen — wer sich
    anmeldet, will sich anmelden, nicht eine Beobachtungstabelle pflegen.
    """
    names = {g.strip() for g in (groups or []) if g and g.strip()}
    if not names:
        return
    try:
        existing = (await db.execute(
            select(SsoObservedGroup).where(
                SsoObservedGroup.provider == provider,
                SsoObservedGroup.group_name.in_(names),
            )
        )).scalars().all()
        by_name = {row.group_name: row for row in existing}
        now = datetime.now(timezone.utc)
        for row in existing:
            row.last_seen_at = now

        new_names = [n for n in names if n not in by_name]
        if not new_names:
            return
        # Nur beim Anlegen wirklich neuer Namen zaehlen — der haeufige Fall (schon
        # alles bekannt) bleibt bei der billigen gezielten Abfrage oben.
        total = await db.scalar(
            select(func.count()).select_from(SsoObservedGroup)
            .where(SsoObservedGroup.provider == provider)
        )
        room = max(0, _MAX_OBSERVED_GROUPS_PER_PROVIDER - (total or 0))
        for name in new_names[:room]:
            db.add(SsoObservedGroup(provider=provider, group_name=name, first_seen_at=now, last_seen_at=now))
        if len(new_names) > room:
            logger.warning(
                "sso_observed_groups (%s) am Limit von %d — %d neue Gruppennamen nicht gespeichert",
                provider, _MAX_OBSERVED_GROUPS_PER_PROVIDER, len(new_names) - room,
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("Konnte gesehene SSO-Gruppen nicht speichern: %s", e)
