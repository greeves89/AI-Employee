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

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device_token import DeviceToken
from app.models.push_subscription import PushSubscription

logger = logging.getLogger(__name__)


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
    await _push_apns(db, user_id, title, body, data)
    await _push_web(db, user_id, title, body, data)


async def _push_apns(db, user_id, title, body, data) -> None:
    try:
        from app.services.apns_service import APNsService

        if not APNsService.configured():
            return
        rows = await db.execute(select(DeviceToken).where(DeviceToken.user_id == user_id))
        for dt in rows.scalars().all():
            await APNsService.send(dt.token, title, body, data=data)
    except Exception as e:  # noqa: BLE001
        logger.warning("APNs-Zustellung an %s fehlgeschlagen: %s", user_id, e)


async def _push_web(db, user_id, title, body, data) -> None:
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
        for sub in subs:
            try:
                status = await webpush.send(
                    endpoint=sub.endpoint, p256dh=sub.p256dh, auth=sub.auth,
                    payload=payload, keys=keys, subject=subject,
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
