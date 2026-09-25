"""call2home — periodically reports usage to the license server.

Opt-in: does nothing unless the admin has set `license_server_url` under
Settings. Never blocks or degrades anything locally on failure/mismatch —
enforcement of the agent limit happens at agent-creation time
(api/agents.py, against the locally cached License), independent of whether
this heartbeat ever reaches the license server. This is purely a usage/
renewal signal for the operator, not a local kill switch — see
core/license.py's docstring and the "Produktionsdaten sind heilig" principle:
a network hiccup or a customer's air-gapped network must never stop agents
that are already running.
"""

import asyncio
import logging
import random
import uuid

import httpx
from sqlalchemy import func, select

from app.config import _read_version
from app.models.agent import Agent

logger = logging.getLogger(__name__)

_INTERVAL = 6 * 3600  # 6 hours — frequent enough to catch revocations within any reasonable grace period, not chatty
_STARTUP_DELAY = 30

# --- Lebenszeichen ohne Lizenz ---------------------------------------------
#
# Der Heartbeat oben verlangt eine eingetragene Serveradresse UND einen
# Lizenzschluessel. Damit meldet sich nur, wer ohnehin schon Kunde ist — die
# Installationen, um die es eigentlich geht, sieht der Betreiber nie.
#
# Deshalb zusaetzlich ein schmales Lebenszeichen, das ohne beides auskommt.
# Es ist bewusst harmlos:
#
# * Es sendet eine zufaellige, lokal erzeugte Kennung und die Version — mehr
#   nicht. Kein Inhalt, keine Namen, keine Agentendaten.
# * Die Antwort kann einen Hinweistext enthalten, den die Oberflaeche als
#   Streifen zeigt. Sie sperrt NICHTS: Eine laufende Anlage darf nie von
#   aussen gestoppt werden — derselbe Grundsatz wie oben.
# * Ein Administrator kann es abschalten (``usage_ping_enabled`` = "false").
#
#: Einmal am Tag. Begruendung fuer genau diesen Takt:
#:
#: * Die Frage lautet "wer setzt das ein", nicht "was tut er gerade" — dafuer
#:   reicht Tagesaufloesung.
#: * Haeufiger waere schwer zu rechtfertigen: Es ist keine Funktion, von der
#:   der Betreiber der Anlage etwas hat.
#: * Seltener wuerde kurzlebige Installationen verpassen. Deshalb kommt der
#:   erste Ping schon kurz nach dem Start — wer die Plattform nur einen
#:   Nachmittag ausprobiert, taucht trotzdem auf.
PING_INTERVALL = 24 * 3600
#: Nicht sofort: Beim Hochfahren hat die Anlage Wichtigeres zu tun, und ein
#: Ping aus einem halb gestarteten Zustand sagt wenig.
PING_START_VERZUG = 120
#: Streuung, damit nicht alle Anlagen gleichzeitig anklopfen. Ohne die
#: schlagen tausend Installationen im selben Moment auf, sobald sie einmal
#: gemeinsam neu gestartet wurden (Stromausfall, Update-Welle).
PING_STREUUNG = 0.1
#: Voreinstellung — ueberschreibbar ueber ``usage_ping_url``.
PING_STANDARD_URL = "https://lizenzen.future-app.de"


def _mit_streuung(sekunden: float) -> float:
    """Wartezeit um bis zu PING_STREUUNG nach oben oder unten verschieben."""
    spanne = sekunden * PING_STREUUNG
    return max(1.0, sekunden + random.uniform(-spanne, spanne))


class LicenseHeartbeatService:
    def __init__(self, session_factory) -> None:
        self._sf = session_factory
        self._running = True

    async def run(self) -> None:
        await asyncio.sleep(_STARTUP_DELAY)
        while self._running:
            try:
                await self._beat()
            except Exception as exc:
                logger.warning("License heartbeat cycle failed (non-fatal): %s", exc)
            await asyncio.sleep(_INTERVAL)

    async def run_ping(self) -> None:
        """Eigener Takt fuer das Lebenszeichen ohne Lizenz (taeglich)."""
        await asyncio.sleep(_mit_streuung(PING_START_VERZUG))
        while self._running:
            try:
                await self._ping()
            except Exception as exc:
                # Niemals laut: Das hier ist kein Dienst, auf den sich jemand
                # verlaesst — es darf im Protokoll nicht wie ein Ausfall wirken.
                logger.debug("Lebenszeichen fehlgeschlagen (folgenlos): %s", exc)
            await asyncio.sleep(_mit_streuung(PING_INTERVALL))

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    async def _ping(self) -> None:
        """Ein Lebenszeichen ohne Lizenz — und die Antwort darauf merken."""
        from app.db.session import resilient_session
        from app.services.settings_service import SettingsService

        async with resilient_session(session_factory=self._sf) as db:
            svc = SettingsService(db)
            if (await svc.get("usage_ping_enabled") or "true").strip().lower() == "false":
                return  # vom Betreiber abgeschaltet

            ziel = (await svc.get("usage_ping_url") or PING_STANDARD_URL).strip()
            if not ziel:
                return

            kennung = await svc.get("license_instance_id")
            if not kennung:
                kennung = uuid.uuid4().hex
                await svc.set("license_instance_id", kennung)
                await db.commit()

        url = ziel.rstrip("/") + "/api/v1/call2home/ping"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    url, json={"instance_id": kennung, "version": _read_version()})
            if resp.status_code != 200:
                return
            antwort = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.debug("Lebenszeichen nicht zugestellt: %s", exc)
            return

        # Der Hinweis wird gemerkt, damit die Oberflaeche ihn zeigen kann.
        # Leerer Hinweis loescht den alten — sonst bliebe ein einmal gesetzter
        # Streifen fuer immer stehen, auch nachdem der Betreiber ihn
        # zurueckgenommen hat.
        async with resilient_session(session_factory=self._sf) as db:
            svc = SettingsService(db)
            await svc.set("usage_ping_hinweis", str(antwort.get("hinweis") or ""))
            await svc.set("usage_ping_bewertung", str(antwort.get("bewertung") or "unbekannt"))
            await db.commit()
        logger.debug("Lebenszeichen gesendet (%s)", antwort.get("bewertung"))

    # ------------------------------------------------------------------
    async def _beat(self) -> None:
        from app.db.session import resilient_session
        from app.services.settings_service import SettingsService

        async with resilient_session(session_factory=self._sf) as db:
            svc = SettingsService(db)
            server_url = (await svc.get("license_server_url") or "").strip()
            if not server_url:
                return  # opt-in — no server configured, stay silent

            license_key = (await svc.get("license_key") or "").strip()
            if not license_key:
                return  # nothing to authenticate the heartbeat with

            instance_id = await svc.get("license_instance_id")
            if not instance_id:
                instance_id = uuid.uuid4().hex
                await svc.set("license_instance_id", instance_id)
                await db.commit()

            from app.core.license import get_current_license
            lic = get_current_license()
            if not lic.license_id or lic.license_id == "community-default":
                return  # community tier has nothing to report home about

            agent_count = (await db.execute(select(func.count(Agent.id)))).scalar() or 0

        url = server_url.rstrip("/") + "/v1/call2home/heartbeat"
        body = {
            "instance_id": instance_id,
            "license_id": lic.license_id,
            "version": _read_version(),
            "active_agent_count": agent_count,
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=body, headers={"Authorization": f"Bearer {license_key}"})
            if resp.status_code == 200:
                logger.info("License heartbeat ok: %s", resp.json().get("license_status"))
            else:
                logger.warning("License heartbeat rejected: HTTP %s", resp.status_code)
        except httpx.HTTPError as exc:
            logger.info("License heartbeat unreachable (offline grace applies): %s", exc)
