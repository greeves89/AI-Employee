"""Agent mit Stimme im Teams-Termin — über Graph Communications (service-hosted media).

Microsoft bietet zwei Medien-Modi, und die Unterscheidung entscheidet über den ganzen
Aufwand:

* **application-hosted media** — der Bot bekommt den rohen Audiostrom. Voller Duplex,
  aber Microsofts Echtzeit-Medien-SDK gibt es nur für .NET, dazu offene Medienports
  und die Berechtigung ``Calls.AccessMedia.All``. Das ist ein eigener Dienst.
* **service-hosted media** — Microsoft hält die Medien. Der Bot spricht über
  ``playPrompt`` in den Termin und hört über ``recordResponse`` eine Äußerung ab.
  Reines HTTPS plus ein Webhook. **Das ist der Weg, den dieses Modul geht.**

Was damit geht: Der Agent tritt einem Termin bei, sagt etwas, hört eine Antwort,
sagt wieder etwas — abwechselnd, wie am Telefon. Was NICHT geht: durchgehend
mithören und dazwischenreden. Dafür bräuchte es den .NET-Dienst oben.

Der Administrator muss in Azure genau **eine Adresse** eintragen (die Rückruf-Adresse
unten) und drei Angaben hierher zurückkopieren. Alles Weitere steht in
``docs/TEAMS_CALLING_SETUP.md``.
"""

import logging
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
LOGIN_BASE = "https://login.microsoftonline.com"

# Einstellungsschlüssel. Das Geheimnis steht zusätzlich in SECRET_KEYS.
APP_ID = "teams_calling_app_id"
APP_SECRET = "teams_calling_app_secret"
TENANT_ID = "teams_calling_tenant_id"
ENABLED = "teams_calling_enabled"

# Die Berechtigungen, die der Administrator in Azure freigeben muss. Als
# ANWENDUNGSberechtigungen mit Mandanten-Zustimmung — ein Bot tritt Terminen ohne
# angemeldeten Nutzer bei, delegierte Rechte reichen dafür nicht.
REQUIRED_PERMISSIONS = [
    ("Calls.JoinGroupCall.All", "Terminen beitreten"),
    ("Calls.JoinGroupCallAsGuest.All", "Als Gast beitreten (Termine anderer Organisationen)"),
    ("Calls.InitiateGroupCall.All", "Anrufe starten"),
    ("OnlineMeetings.Read.All", "Termin zur Einladung auflösen"),
]

# Bewusst NICHT dabei: Calls.AccessMedia.All. Die Berechtigung braucht nur der rohe
# Audiostrom (application-hosted media) — sie hier zu verlangen hiesse, vom Kunden
# ein weitreichendes Recht einzufordern, das dieser Weg gar nicht nutzt.


def callback_url(base_url: str) -> str:
    """Die EINE Adresse, die der Administrator in Azure einträgt."""
    return f"{(base_url or '').rstrip('/')}/api/v1/teams/calling/callback"


async def load_settings(db) -> dict:
    from app.services.settings_service import SettingsService

    svc = SettingsService(db)
    return {k: (await svc.get(k)) or "" for k in (APP_ID, APP_SECRET, TENANT_ID, ENABLED)}


def is_configured(cfg: dict) -> bool:
    """Vollständig genug, um einem Termin beizutreten?

    Ohne Geheimnis gibt es kein Token, ohne Mandanten-ID keinen Token-Endpunkt —
    dann darf der Knopf gar nicht erst erscheinen.
    """
    return bool(cfg.get(APP_ID) and cfg.get(APP_SECRET) and cfg.get(TENANT_ID))


def is_enabled(cfg: dict) -> bool:
    return is_configured(cfg) and (cfg.get(ENABLED) or "").lower() in ("true", "1", "yes")


def public_base_is_https(base_url: str) -> bool:
    """Microsoft ruft nur HTTPS zurück.

    Steht die Anlage nur unter http zur Verfügung, kommt keine einzige
    Benachrichtigung an — und zwar ohne Fehlermeldung auf unserer Seite. Deshalb
    wird das in der Oberfläche vorab gemeldet statt später gerätselt.
    """
    return (urlparse(base_url or "").scheme or "") == "https"


async def app_token(cfg: dict) -> str | None:
    """Anwendungs-Token (client_credentials) für die Communications-API."""
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{LOGIN_BASE}/{cfg[TENANT_ID]}/oauth2/v2.0/token",
                data={
                    "client_id": cfg[APP_ID],
                    "client_secret": cfg[APP_SECRET],
                    "scope": "https://graph.microsoft.com/.default",
                    "grant_type": "client_credentials",
                },
            )
        resp.raise_for_status()
        return resp.json().get("access_token")
    except Exception as e:  # noqa: BLE001
        logger.warning("[Teams-Anruf] Token nicht erhaltbar: %s", e)
        return None


async def check_setup(cfg: dict) -> dict:
    """Ist die Einrichtung in Azure wirklich fertig?

    Der Administrator soll das hier prüfen können, statt es beim ersten Termin
    herauszufinden. Geprüft wird, was ohne Nebenwirkung prüfbar ist: ob ein Token
    ausgestellt wird und ob die Zustimmung erteilt wurde.
    """
    if not is_configured(cfg):
        return {"ok": False, "reason": "Angaben unvollständig"}
    token = await app_token(cfg)
    if not token:
        return {"ok": False, "reason": "Kein Token — App-ID, Geheimnis oder Mandanten-ID stimmen nicht"}

    # Ein harmloser Aufruf, der die erteilten Rechte spiegelt.
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                f"{GRAPH_BASE}/communications/calls",
                headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code == 403:
            return {"ok": False, "reason": "Token da, aber die Zustimmung des Administrators fehlt"}
        return {"ok": True, "reason": ""}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"Graph nicht erreichbar: {e}"}


async def join_meeting(cfg: dict, *, join_url: str, base_url: str,
                       display_name: str = "AI Employee") -> dict | None:
    """Einem Teams-Termin über den Beitrittslink beitreten.

    ``serviceHostedMediaConfig`` ist der Kern: damit hält Microsoft die Medien und
    wir brauchen weder Medienports noch .NET.
    """
    token = await app_token(cfg)
    if not token:
        return None

    payload = {
        "@odata.type": "#microsoft.graph.call",
        "callbackUri": callback_url(base_url),
        "requestedModalities": ["audio"],
        "mediaConfig": {"@odata.type": "#microsoft.graph.serviceHostedMediaConfig"},
        "meetingInfo": {
            "@odata.type": "#microsoft.graph.joinMeetingIdMeetingInfo",
            "joinWebUrl": join_url,
        },
        "tenantId": cfg[TENANT_ID],
        "callOptions": {
            "@odata.type": "#microsoft.graph.outgoingCallOptions",
            "isContentSharingNotificationEnabled": False,
        },
        "source": {
            "@odata.type": "#microsoft.graph.participantInfo",
            "identity": {
                "@odata.type": "#microsoft.graph.identitySet",
                "application": {
                    "@odata.type": "#microsoft.graph.identity",
                    "displayName": display_name,
                    "id": cfg[APP_ID],
                },
            },
        },
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{GRAPH_BASE}/communications/calls",
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"},
                json=payload,
            )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("[Teams-Anruf] Beitritt fehlgeschlagen: %s", e)
        return None


async def speak(cfg: dict, call_id: str, audio_url: str) -> bool:
    """Etwas in den Termin sagen.

    Graph erwartet eine erreichbare Adresse auf eine WAV-Datei (16 kHz, mono,
    16 Bit). Unsere Sprachschicht liefert PCM in 24 kHz — die Umwandlung passiert
    beim Ablegen der Datei, nicht hier.
    """
    token = await app_token(cfg)
    if not token:
        return False
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{GRAPH_BASE}/communications/calls/{call_id}/playPrompt",
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"},
                json={
                    "prompts": [{
                        "@odata.type": "#microsoft.graph.mediaPrompt",
                        "mediaInfo": {
                            "@odata.type": "#microsoft.graph.mediaInfo",
                            "uri": audio_url,
                            "resourceId": call_id,
                        },
                    }],
                },
            )
        resp.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("[Teams-Anruf] Sprechen fehlgeschlagen: %s", e)
        return False


async def listen(cfg: dict, call_id: str, *, max_seconds: int = 30) -> bool:
    """Eine Äußerung aufnehmen.

    Abwechselnd, nicht durchgehend: Graph nimmt EINE Äusserung auf und meldet sie
    über den Rückruf. Durchgehendes Mithören ginge nur mit rohem Medienstrom.
    """
    token = await app_token(cfg)
    if not token:
        return False
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{GRAPH_BASE}/communications/calls/{call_id}/recordResponse",
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"},
                json={
                    "maxRecordDurationInSeconds": max_seconds,
                    "initialSilenceTimeoutInSeconds": 10,
                    "maxSilenceTimeoutInSeconds": 3,
                    "playBeep": False,
                },
            )
        resp.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("[Teams-Anruf] Zuhoeren fehlgeschlagen: %s", e)
        return False


async def hang_up(cfg: dict, call_id: str) -> bool:
    """Den Termin verlassen. Ohne das bleibt der Bot bis zum Ende drin."""
    token = await app_token(cfg)
    if not token:
        return False
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.delete(
                f"{GRAPH_BASE}/communications/calls/{call_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
        return resp.status_code in (200, 202, 204, 404)
    except Exception as e:  # noqa: BLE001
        logger.warning("[Teams-Anruf] Verlassen fehlgeschlagen: %s", e)
        return False
