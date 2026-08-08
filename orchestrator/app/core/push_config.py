"""VAPID-Schluessel: einmal erzeugen, danach unveraendert aufbewahren.

Der oeffentliche Schluessel geht an jeden Browser, der sich anmeldet, und wird dort
in der Anmeldung festgehalten. Wird der Schluessel getauscht, sind SAEMTLICHE
bestehenden Anmeldungen wertlos und jeder Nutzer muesste sich neu anmelden — ohne
es zu merken, denn Meldungen bleiben dann einfach aus.

Deshalb: einmal erzeugen, verschluesselt in den Einstellungen ablegen, nie
automatisch erneuern. Genau die Ueberlegung, die auch fuer den ENCRYPTION_KEY gilt.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.webpush import VapidKeys

logger = logging.getLogger(__name__)

_PRIVATE_KEY_SETTING = "webpush_vapid_private_key"
_PUBLIC_KEY_SETTING = "webpush_vapid_public_key"
_SUBJECT_SETTING = "webpush_vapid_subject"

# Der Push-Dienst will eine Kontaktmoeglichkeit, falls etwas schiefgeht. Eine
# mailto-Adresse reicht; sie wird nicht geprueft, muss aber vorhanden sein.
_DEFAULT_SUBJECT = "mailto:admin@ai-employee.local"


async def get_vapid_keys(db: AsyncSession, *, create: bool = True) -> VapidKeys | None:
    """Das Schluesselpaar dieses Servers, bei Bedarf einmalig erzeugt.

    ``create=False`` fragt nur ab, ohne anzulegen — dafuer, dass ein Lesezugriff
    nicht unbemerkt Schluessel erzeugt.
    """
    from app.services.settings_service import SettingsService

    svc = SettingsService(db)
    private_pem = await svc.get(_PRIVATE_KEY_SETTING)
    public_b64 = await svc.get(_PUBLIC_KEY_SETTING)
    if private_pem and public_b64:
        return VapidKeys(private_pem=private_pem, public_b64=public_b64)
    if not create:
        return None

    keys = VapidKeys.generate()
    await svc.set(_PRIVATE_KEY_SETTING, keys.private_pem)
    await svc.set(_PUBLIC_KEY_SETTING, keys.public_b64)
    await db.commit()
    logger.info("VAPID-Schluesselpaar erzeugt (einmalig, bleibt bestehen)")
    return keys


async def vapid_subject(db: AsyncSession) -> str:
    from app.services.settings_service import SettingsService

    return (await SettingsService(db).get(_SUBJECT_SETTING)) or _DEFAULT_SUBJECT
