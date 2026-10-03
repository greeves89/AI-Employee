"""Minimale Nutzerliste für Freigabe-Dialoge (Apps, Keys, Workflows).

EINE Stelle, damit die Regel nicht je Endpunkt auseinanderläuft: ohne den
Aufrufer; Nicht-Admins bekommen nur id + Anzeigename — die E-Mail-Adressen der
anderen Nutzer der Anlage gehen sie nichts an; nur Admins sehen sie.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserRole


async def verzeichnis(db: AsyncSession, user) -> list[dict]:
    ist_admin = getattr(user, "role", None) == UserRole.ADMIN
    rows = (await db.execute(select(User.id, User.name, User.email).order_by(User.name))).all()
    users = []
    for uid, name, email in rows:
        if uid == str(user.id):
            continue
        eintrag = {"id": uid, "name": name}
        if ist_admin:
            eintrag["email"] = email
        users.append(eintrag)
    return users
