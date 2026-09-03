"""APNs push notifications.

Signs a provider JWT (ES256) with the APNs auth key and delivers alerts
over HTTP/2. Config comes from PlatformSettings (loaded into `settings`):
apns_auth_key (.p8 contents), apns_key_id, apns_team_id, apns_bundle_id,
apns_sandbox.
"""

from __future__ import annotations

import logging
import time

import httpx
import jwt
from app.config import settings

logger = logging.getLogger(__name__)


class APNsService:
    _token: str | None = None
    _token_at: float = 0.0

    @classmethod
    def configured(cls) -> bool:
        return bool(
            settings.apns_auth_key and settings.apns_key_id and settings.apns_team_id
        )

    @staticmethod
    def _auth_key() -> str:
        """Den Schluessel aus der Konfiguration in ein brauchbares PEM bringen.

        Der Schluessel ist ein mehrzeiliger PEM-Block, die Konfiguration eine
        Zeile pro Wert. In der Praxis wird er deshalb mit \n statt echter
        Zeilenumbrueche eingetragen — dann kaeme hier die Zeichenfolge
        Backslash-n an, und das Signieren scheitert mit einer Meldung ueber ein
        ungueltiges Schluesselformat, die auf alles Moegliche hindeutet, nur
        nicht auf die Ursache. Beide Schreibweisen sind daher zugelassen.
        """
        key = settings.apns_auth_key.strip()
        if "\\n" in key and "\n" not in key:
            key = key.replace("\\n", "\n")
        return key

    @classmethod
    def _provider_token(cls) -> str:
        # Apple wants the JWT refreshed periodically; 30 min is safely under
        # the 1 h limit and over the 20 min minimum.
        now = time.time()
        if cls._token and (now - cls._token_at) < 1800:
            return cls._token
        cls._token = jwt.encode(
            {"iss": settings.apns_team_id, "iat": int(now)},
            cls._auth_key(),
            algorithm="ES256",
            headers={"kid": settings.apns_key_id},
        )
        cls._token_at = now
        return cls._token

    @classmethod
    async def send(
        cls,
        device_token: str,
        title: str,
        body: str,
        data: dict | None = None,
    ) -> bool:
        if not cls.configured():
            return False
        # Reihenfolge: erst die eingestellte Umgebung, dann die andere. Apple
        # trennt Test- und Verkaufsversionen strikt; ein Geraete-Schluessel aus
        # einem Xcode-Build ist an der Verkaufsadresse ungueltig und umgekehrt.
        # Beide Arten sind gleichzeitig im Umlauf (Entwicklung am Schreibtisch,
        # Testflug beim Kunden), und der Unterschied ist von aussen nicht
        # erkennbar: Apple antwortet in beiden Faellen mit BadDeviceToken. Wer
        # den Schalter falsch stellt, sucht den Fehler deshalb ueberall, nur
        # nicht dort. Der zweite Versuch kostet nur bei Geraeten etwas, deren
        # Zustellung ohnehin scheitert.
        hosts = ["api.sandbox.push.apple.com", "api.push.apple.com"]
        if not settings.apns_sandbox:
            hosts.reverse()
        payload = {
            "aps": {
                "alert": {"title": title, "body": body},
                "sound": "default",
            }
        }
        if data:
            payload.update(data)
            # Freigabe-Pushes bekommen eine Kategorie, damit iOS die
            # Genehmigen/Ablehnen-Knoepfe direkt auf der Mitteilung anzeigen
            # kann (App 1.2.3+ registriert die Kategorie "APPROVAL").
            if data.get("type") == "approval":
                payload["aps"]["category"] = "APPROVAL"
        headers = {
            "authorization": f"bearer {cls._provider_token()}",
            "apns-topic": settings.apns_bundle_id,
            "apns-push-type": "alert",
        }
        try:
            async with httpx.AsyncClient(http2=True, timeout=10.0) as client:
                for versuch, host in enumerate(hosts):
                    r = await client.post(
                        f"https://{host}/3/device/{device_token}",
                        headers=headers, json=payload,
                    )
                    if r.status_code == 200:
                        if versuch:
                            # Nicht die eingestellte Umgebung — der Schalter
                            # passt nicht zu den Geraeten, die sich anmelden.
                            logger.info(
                                "APNs: Zustellung gelang erst ueber %s. "
                                "APNS_SANDBOX passt nicht zu den angemeldeten "
                                "Geraeten.", host,
                            )
                        return True
                    if r.status_code != 400 or "BadDeviceToken" not in r.text:
                        break   # Anderer Fehler — der zweite Anlauf hilft nicht
                logger.warning("APNs %s for %s…: %s",
                               r.status_code, device_token[:8], r.text)
            return False
        except Exception:  # noqa: BLE001
            logger.exception("APNs send failed")
            return False


# Der Verteilpunkt an die Geraete eines Nutzers liegt jetzt in ``core.push`` und
# faechert auf iOS UND Browser auf. Hier bleibt nur der APNs-Transport. Wer eine
# Meldung verschicken will, ruft ``core.push.push_to_user`` — sonst erreicht sie
# ausschliesslich iPhones, und niemand merkt es.
