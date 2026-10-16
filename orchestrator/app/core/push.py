"""EIN Verteilpunkt fuer Benachrichtigungen an die Geraete eines Nutzers.

Bisher hiess diese Funktion ``apns_service.push_to_user`` und konnte nur iOS. Sie
wurde von vier Stellen aufgerufen (Aufgabe fertig, Freigabe noetig, Meldung erstellt,
Watchdog). Web Push als zweiten Weg danebenzustellen haette geheissen, alle vier
Stellen zu erweitern — und die fuenfte, die morgen dazukommt, waere garantiert
vergessen worden.

Deshalb faechert diese Funktion auf: sie schickt an jedes registrierte iOS-Geraet UND
an jede Browser-Anmeldung. Die Aufrufer wissen davon nichts. Kommt ein dritter Kanal
dazu, aendert sich genau hier etwas.
"""

import asyncio
import json
import logging
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device_token import DeviceToken
from app.models.push_subscription import PushSubscription

logger = logging.getLogger(__name__)

# Deckel fuer die GESAMTE Auffaecherung. Zwei Aufrufer (``POST /notifications/`` und
# das Anlegen einer Freigabe) warten im HTTP-Pfad einer Nutzeranfrage darauf, und die
# Geraete werden der Reihe nach bedient. Ohne Deckel addieren sich bei einer
# Netzstoerung die Zeitlimits ALLER Geraete des Nutzers: gemessen wurden fuer zwei
# iPhones und drei Browser-Anmeldungen bei verworfenen Paketen ueber drei Minuten —
# der Nutzer bekommt dann eine Zeitueberschreitung auf eine Anfrage, deren eigentliche
# Arbeit laengst erledigt ist.
#
# Der Deckel umfasst ausschliesslich die NETZZUSTELLUNG, nicht die Datenbankzugriffe
# dieser Datei. Ein mitten im Datenbankaufruf abgebrochener Vorgang wuerde die Sitzung
# des Aufrufers beschaedigen und aus einer verlorenen Meldung einen gescheiterten
# Vorgang machen — das waere schlimmer als das Problem.
ZUSTELLBUDGET_S = 10.0


class _Budget:
    """Wie viel Zeit die Zustellung insgesamt noch hat."""

    def __init__(self, sekunden: float) -> None:
        self._ende = time.monotonic() + sekunden

    def rest(self) -> float:
        return self._ende - time.monotonic()

    def erschoepft(self) -> bool:
        return self.rest() <= 0


async def push_to_user(
    db: AsyncSession,
    user_id: str,
    title: str,
    body: str,
    data: dict | None = None,
) -> None:
    """An alle Geraete des Nutzers zustellen. Best effort — wirft nie.

    Ein fehlgeschlagener Push darf niemals den Vorgang kippen, aus dem er stammt:
    eine Aufgabe gilt als fertig, auch wenn die Meldung darueber nicht ankam.
    """
    if not user_id:
        return
    budget = _Budget(ZUSTELLBUDGET_S)
    await _push_apns(db, user_id, title, body, data, budget)
    await _push_web(db, user_id, title, body, data, budget)


async def _push_apns(db, user_id, title, body, data, budget: "_Budget") -> None:
    try:
        from app.services.apns_service import APNsService

        if not APNsService.configured():
            return
        rows = await db.execute(select(DeviceToken).where(DeviceToken.user_id == user_id))
        geraete = rows.scalars().all()
        for nummer, dt in enumerate(geraete):
            if budget.erschoepft():
                logger.warning(
                    "Zustellbudget aufgebraucht: %d von %d iOS-Geraeten von %s nicht "
                    "mehr versucht", len(geraete) - nummer, len(geraete), user_id,
                )
                return
            try:
                await asyncio.wait_for(
                    APNsService.send(dt.token, title, body, data=data),
                    timeout=budget.rest(),
                )
            except (TimeoutError, asyncio.TimeoutError):
                logger.warning("APNs-Zustellung an ein Geraet von %s abgebrochen "
                               "(Zustellbudget)", user_id)
                return
    except Exception as e:  # noqa: BLE001
        logger.warning("APNs-Zustellung an %s fehlgeschlagen: %s", user_id, e)


async def _push_web(db, user_id, title, body, data, budget: "_Budget") -> None:
    """Browser-Zustellung, inklusive Aufraeumen erloschener Anmeldungen.

    Ohne das Aufraeumen sammeln sich Karteileichen (deinstallierter Browser, entzogene
    Rechte), die bei JEDER Meldung erneut angefragt werden — das kostet bei jedem
    Versand eine HTTP-Runde pro Leiche.
    """
    try:
        from app.core import webpush
        from app.core.push_config import get_vapid_keys, vapid_subject

        keys = await get_vapid_keys(db)
        if keys is None:
            return
        subs = (await db.execute(
            select(PushSubscription).where(PushSubscription.user_id == user_id)
        )).scalars().all()
        if not subs:
            return

        payload = json.dumps({"title": title, "body": body, "data": data or {}}).encode()
        subject = await vapid_subject(db)
        gone: list[PushSubscription] = []
        for nummer, sub in enumerate(subs):
            if budget.erschoepft():
                logger.warning(
                    "Zustellbudget aufgebraucht: %d von %d Browser-Anmeldungen von %s "
                    "nicht mehr versucht", len(subs) - nummer, len(subs), user_id,
                )
                break
            try:
                status = await asyncio.wait_for(
                    webpush.send(
                        endpoint=sub.endpoint, p256dh=sub.p256dh, auth=sub.auth,
                        payload=payload, keys=keys, subject=subject,
                    ),
                    timeout=budget.rest(),
                )
                if webpush.is_gone(status):
                    gone.append(sub)
                elif status >= 400:
                    logger.warning("Web Push %s abgelehnt (%s)", sub.endpoint[:60], status)
            except Exception as e:  # noqa: BLE001 — eine tote Anmeldung stoppt nicht die anderen
                logger.warning("Web Push an %s fehlgeschlagen: %s", sub.endpoint[:60], e)

        for sub in gone:
            await db.delete(sub)
        if gone:
            await db.commit()
            logger.info("%d erloschene Web-Push-Anmeldung(en) entfernt", len(gone))
    except Exception as e:  # noqa: BLE001
        logger.warning("Web-Push-Zustellung an %s fehlgeschlagen: %s", user_id, e)
