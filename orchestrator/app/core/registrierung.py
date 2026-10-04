"""Registrierungsschalter beim Start festschreiben (#914).

Mit #914 ist die Passwort-Selbstregistrierung für NEUE Anlagen standardmäßig
geschlossen (``registration_open=False``) und die SSO-Kontoanlage hat einen
eigenen Schalter (``sso_auto_provisioning``). Eine Anlage, die schon läuft, darf
sich durch das Update aber nicht anders verhalten als vorher:

- Bestandsanlage (es gibt Nutzer), Schalter nie gespeichert → der BISHERIGE
  Wert wird gespeichert: ``registration_open`` wie bisher (Umgebungsvariable,
  sonst die alte Vorgabe ``True``), ``sso_auto_provisioning`` gleich diesem Wert
  — denn bisher steuerte ``registration_open`` auch die SSO-Anlage.
- Neue Anlage (noch keine Nutzer) → die neuen Vorgaben werden gespeichert. Das
  ist nötig: sonst hielte der nächste Start nach Anlage des ersten Admins die
  Anlage für einen Bestand und öffnete die Registrierung.

Was einmal gespeichert ist, fasst diese Funktion nie wieder an — die Schalter
gehören dann dem Administrator.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select

from app.config import settings

logger = logging.getLogger(__name__)

#: Vorgabe von ``registration_open`` bis einschließlich v1.361.
ALTE_VORGABE_REGISTRIERUNG = True


def _als_bool(wert: str | None) -> bool | None:
    if wert is None or wert == "":
        return None
    return wert.strip().lower() in ("true", "1", "yes")


async def registrierung_festschreiben(db) -> dict[str, bool]:
    """Fehlende Schalter speichern. Gibt zurück, was gespeichert wurde."""
    from app.models.user import User
    from app.services.settings_service import SettingsService

    svc = SettingsService(db)
    reg_gespeichert = _als_bool(await svc.get("registration_open"))
    sso_gespeichert = _als_bool(await svc.get("sso_auto_provisioning"))
    if reg_gespeichert is not None and sso_gespeichert is not None:
        return {}

    nutzer = int(await db.scalar(select(func.count()).select_from(User)) or 0)
    bestand = nutzer > 0
    neu: dict[str, bool] = {}

    if reg_gespeichert is None:
        if bestand and "registration_open" not in settings.model_fields_set:
            reg = ALTE_VORGABE_REGISTRIERUNG
        else:
            # Neue Anlage → neue Vorgabe; oder ausdrücklich per Umgebung gesetzt.
            reg = bool(settings.registration_open)
        neu["registration_open"] = reg
        reg_gespeichert = reg

    if sso_gespeichert is None:
        if bestand and "sso_auto_provisioning" not in settings.model_fields_set:
            # Bisher entschied registration_open auch über die SSO-Anlage.
            sso = bool(reg_gespeichert)
        else:
            sso = bool(settings.sso_auto_provisioning)
        neu["sso_auto_provisioning"] = sso

    for schluessel, wert in neu.items():
        await svc.set(schluessel, "true" if wert else "false")
        setattr(settings, schluessel, wert)
    await db.commit()
    logger.info("Registrierungsschalter festgeschrieben (%s): %s",
                "Bestandsanlage" if bestand else "neue Anlage", neu)
    return neu
