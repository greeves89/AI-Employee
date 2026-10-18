"""Das eigene Claude-Abo im laufenden Container frisch halten (#999).

Läuft ein Agent mit dem EIGENEN Claude-Abo seines Besitzers, bekam er bisher nur
die Abschrift des Access-Tokens aus seiner Erstellung. Die lief nach wenigen
Stunden ab, und danach endete jeder Zug mit „401 OAuth access token has expired".

Diese Schleife fragt den Orchestrator jede Minute nach dem aktuellen Token. Hat
ein anderer Agent desselben Besitzers gerade erneuert, ist das alte Token sofort
tot — deshalb der kurze Takt, und deshalb fragt auch der 401-Wiederholversuch
(``config.wait_for_new_oauth_token``) sofort selbst nach.
Erneuert wird dort, der Refresh-Token kommt nie in den Container. Gesetzt wird die
Schleife nur, wenn der Orchestrator den Container mit
``CLAUDE_CREDENTIAL_SOURCE=personal`` erstellt hat.
"""

from __future__ import annotations

import asyncio
import os

from app import config

INTERVAL_SECONDS = 60


def personal_mode() -> bool:
    return os.environ.get("CLAUDE_CREDENTIAL_SOURCE", "") == "personal"


def apply_response(status_code: int, data: dict | None) -> None:
    """Eine Antwort von ``GET /agents/{id}/claude-token`` übernehmen.

    Nur eine eindeutige Antwort ändert den Zustand: ein Netzfehler oder ein 5xx
    lässt das zuletzt bekannte Token stehen — es kann noch gelten, und ohne Token
    wäre der Agent sicher tot.
    """
    if status_code != 200 or not isinstance(data, dict):
        return
    token = data.get("access_token")
    if data.get("source") == "personal" and isinstance(token, str) and token:
        config.set_personal_oauth_token(token)
    else:
        # Der Besitzer hat sein Abo entfernt oder eigene Zugänge sind gesperrt:
        # zurück zum bisherigen Weg (Teamlizenz), statt an einem Token zu kleben,
        # das niemand mehr erneuert.
        config.set_personal_oauth_token(None)


async def fetch_once(agent_id: str) -> None:
    import httpx

    url = f"{config.settings.orchestrator_url}/api/v1/agents/{agent_id}/claude-token"
    headers = {"Authorization": f"Bearer {config.settings.agent_token}", "X-Agent-ID": agent_id}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers)
        data = resp.json() if resp.status_code == 200 else None
    except Exception as exc:  # noqa: BLE001 — beim nächsten Durchlauf erneut
        print(f"[Agent] Claude-Token-Abruf gescheitert: {type(exc).__name__}")
        return
    if resp.status_code != 200:
        print(f"[Agent] Claude-Token-Abruf: HTTP {resp.status_code}")
    apply_response(resp.status_code, data)


async def sync_loop(agent_id: str, interval_seconds: int = INTERVAL_SECONDS) -> None:
    while True:
        await fetch_once(agent_id)
        await asyncio.sleep(interval_seconds)
