"""Selbsttest nach dem Rückspielen einer Sicherung (#892): ist ein Geheimnis lesbar?

Eine Wiederherstellung ohne den passenden ``ENCRYPTION_KEY`` sieht erst einmal
gelungen aus — die Datenbank ist da, die Agenten sind da. Unlesbar sind nur die
verschlüsselten Geheimnisse (KI-Konten, Keys, OAuth-Zugänge), und das fällt erst
auf, wenn ein Agent damit arbeiten soll. ``scripts/restore.sh`` ruft deshalb
diesen Test im Orchestrator auf:

    docker exec ai-employee-orchestrator python -m app.core.schluessel_selbsttest

Exit-Code 0 = lesbar (oder nichts zu prüfen), 1 = mit dem vorhandenen Schlüssel
nicht lesbar. Geprüft werden einige wenige Einträge je Tabelle, mehr braucht es
nicht: mit dem falschen Schlüssel ist ohnehin keiner lesbar.
"""

import asyncio
import json
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import decrypt_token

#: Je Tabelle so viele Einträge — reicht, um einen falschen Schlüssel zu erkennen.
STICHPROBE = 3


def _quellen():
    from app.models.agent_secret import AgentSecret
    from app.models.ai_account import AIAccount
    from app.models.oauth_integration import OAuthIntegration

    return (
        AIAccount.api_key_encrypted,
        AgentSecret.value_encrypted,
        OAuthIntegration.access_token_encrypted,
    )


async def pruefe(db: AsyncSession) -> dict:
    geprueft = lesbar = 0
    for spalte in _quellen():
        try:
            werte = (await db.execute(
                select(spalte).where(spalte.isnot(None), spalte != "").limit(STICHPROBE)
            )).scalars().all()
        except Exception:  # noqa: BLE001 — fehlende Tabelle auf alter Anlage
            await db.rollback()
            continue
        for wert in werte:
            geprueft += 1
            try:
                decrypt_token(wert)
                lesbar += 1
            except ValueError:
                pass
    # Teilweise lesbar ist kein Schlüsselproblem (eher ein alter Einzelfall) —
    # mit dem falschen Schlüssel ist KEIN Eintrag lesbar.
    return {"geprueft": geprueft, "lesbar": lesbar, "ok": geprueft == 0 or lesbar > 0}


async def _main() -> int:
    from app.db.session import async_session_factory

    async with async_session_factory() as db:
        ergebnis = await pruefe(db)
    print(json.dumps(ergebnis))
    return 0 if ergebnis["ok"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
