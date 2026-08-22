"""Sentinel core service skeleton (issue #590, part of Sentinel epic #588).

Event-driven counterpart to watchdog.py's poll-driven checks: instead of
scanning the DB on a timer like SchedulerService.run() does, SentinelService
consumes the per-agent event streams `agent:*:logs` and `agent:*:chat:response`.

Die Zuordnung eines Ereignisses zu einem Agenten stammt AUSSCHLIESSLICH aus dem
KANALNAMEN, den der Orchestrator selbst aufloest — nie aus dem Feld `agent_id`
in der Nutzlast. Das ist der Kern der Vertrauensgrenze (#590) und der Grund fuer
die Kanalwahl:

* `agents:logs:all` wird hier NICHT mehr gelesen. Dieser globale Kanal ist per
  ACL absichtlich fuer jeden Agenten beschreibbar (`redis_service.py`) — er
  speist die Live-Ansicht der Oberflaeche. Wer darauf hoert und dem mitgelieferten
  `agent_id` glaubt, laesst Agent A ein Ereignis im Namen von Agent B erfinden
  und B daraufhin anhalten: eine Waffe von Agent gegen Agent.
* `agent:{id}:*` liegt dagegen im Namensraum GENAU EINES Agenten. Mit aktiver
  ACL kann kein Agent in den Namensraum eines anderen schreiben; die Zuordnung
  ueber den Kanalnamen ist damit eine echte Grenze statt einer Selbstauskunft.

Sichtbarkeit geht dabei nicht verloren: `log_publisher.publish()` schreibt
dieselbe Nutzlast auf beide Kanaele, und die orchestrator-seitigen Melder
(`agent_manager._publish_event`, `task_router`, `ws.py`) schreiben ohnehin auf
den Kanal je Agent.

Eine ehrliche Grenze der Phase 1: sie schliesst die FAELSCHUNG der Zuordnung.
Ein kompromittierter Agent, der einfach aufhoert zu senden oder bereinigten Text
sendet, bleibt unsichtbar — dagegen hilft nur orchestrator-seitige Beobachtung
(Docker-Logs). Das ist bewusst nicht Teil dieser Aenderung.

Die drei Haken sind inzwischen ausgefuehrt, nicht mehr leer — wer hier etwas
aendert, aendert scharfe Wirkung:
  - `_scan`       erkennt Geheimnisse in der Ausgabe und Prompt-Injektion (#592).
  - `_stop_agent` haelt den Container wirklich an (#590 Punkt 4) — aber NUR,
                  wenn `settings.redis_acl_enabled` an ist. Ohne aktive ACL ist
                  die Kanalnamen-Zuordnung (siehe oben) keine echte Grenze, dann
                  meldet dieser Haken den Fund nur noch, statt zuzuschlagen.
  - `_notify`     schreibt Benachrichtigung und Pruefspur in die Datenbank.
Einziger Schalter davor ist `sentinel_enabled` (Standard: aus).
"""

import asyncio
import json
import time
import logging

from app.config import settings
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)

#: Die Werkzeug- und Lebenszyklus-Ereignisse je Agent.
_AGENT_LOG_PATTERN = "agent:*:logs"

#: Der Gespraechsverkehr laeuft ueber einen eigenen Kanal je Agent.
_AGENT_CHAT_PATTERN = "agent:*:chat:response"

#: Beide Muster liegen im Namensraum genau eines Agenten — siehe Modul-Kopf.
_AGENT_PATTERNS = (_AGENT_LOG_PATTERN, _AGENT_CHAT_PATTERN)
# Pubsub reconnect backoff after an unexpected error (Redis restart, network
# blip). Short enough that a real incident isn't missed for long, long enough
# not to hot-loop against a Redis that is still down.
_RECONNECT_DELAY_SECONDS = 2

#: Groesse, ab der ein Ereignis fuer die Pruefung abgeschnitten wird. Ein
#: einzelnes Dateilesen kann Megabyte gross sein; ungebremst wuerde es alle
#: folgenden Ereignisse hinter sich aufstauen.
_MAX_SCAN_CHARS = 20_000

#: Wie lange derselbe Vorfall desselben Agenten nicht erneut ausgeloest wird.
_VORFALL_SPERRE_SEKUNDEN = 60.0

#: Schluessel, unter dem der Sentinel sein Lebenszeichen ablegt.
SENTINEL_HEARTBEAT_KEY = "sentinel:heartbeat"

#: Wie oft das Lebenszeichen erneuert wird. Deutlich kleiner als die
#: Schwelle im Wachhund, damit ein einzelner verpasster Schlag keinen
#: Fehlalarm ausloest.
_HERZSCHLAG_INTERVALL_SEKUNDEN = 15.0


def agent_id_aus_kanal(kanal: bytes | str) -> str | None:
    """Die Agenten-Zuordnung aus dem Kanalnamen lesen — nie aus der Nutzlast.

    Der Kanalname kommt von Redis selbst und beschreibt, WO eine Nachricht
    ankam. Das Feld `agent_id` in der Nutzlast beschreibt dagegen nur, was der
    Absender ueber sich BEHAUPTET. Genau in dieser Verwechslung lag die Luecke
    (#590): sie machte aus einer Selbstauskunft eine Berechtigung, einen
    fremden Agenten anhalten zu lassen.

    Gibt `None` zurueck, wenn der Kanal nicht in den Namensraum eines Agenten
    gehoert — dann wird das Ereignis verworfen statt geraten.
    """
    if isinstance(kanal, bytes):
        kanal = kanal.decode("utf-8", "replace")
    teile = kanal.split(":")
    # "agent:{id}:logs" bzw. "agent:{id}:chat:response"
    if len(teile) < 3 or teile[0] != "agent":
        return None
    return teile[1] or None


class SentinelVerdict:
    """Result of scanning one agent event.

    `triggered=False` is the only outcome this skeleton ever produces — real
    rules (secret/PII leakage, destructive commands, policy violations, ...)
    are #592's job. The shape exists now so #592 can fill in `_scan` without
    touching the dispatch path in `_handle_event`.
    """

    def __init__(self, triggered: bool, reason: str | None = None, excerpt: str | None = None):
        self.triggered = triggered
        self.reason = reason
        self.excerpt = excerpt


class SentinelService:
    """Central, privileged supervisor over all agents (Sentinel epic #588).

    Runs exactly once, inside the orchestrator process — never inside an
    agent container, unlike a per-agent self-check, which an agent under an
    injection attack could simply skip. See SchedulerService for the sibling
    "one background service per orchestrator process" pattern; the difference
    here is event-driven consumption instead of a 30s poll loop, because a
    harmful action needs to be caught as it happens, not up to 30s later.
    """

    def __init__(self, redis: RedisService, docker=None):
        self.redis = redis
        # Ohne Docker-Dienst kann der Sentinel nichts stoppen. Optional, damit
        # Tests ihn weglassen koennen; im Betrieb reicht main.py ihn durch.
        self.docker = docker
        self._running = False
        # (agent_id, grund) -> Zeitpunkt des letzten Ausloesens, gegen Sturmfeuer.
        self._letzter_vorfall: dict[tuple[str, str], float] = {}
        self._letzter_schlag: float = 0.0
        # The Sentinel-exclusive credential (#590 scope point 3, dependencies.py
        # require_sentinel/get_sentinel_token). Held here so _stop_agent can present
        # it once it is wired to a real privileged call (#590 scope point 4) — not
        # used yet, computing it eagerly just proves the derivation succeeds at
        # startup (a misconfigured/missing api_secret_key would fail loudly here
        # instead of silently at the first real stop attempt).
        from app.dependencies import get_sentinel_token
        self._sentinel_token = get_sentinel_token()

    async def run(self) -> None:
        """Main loop: (re)subscribe to the per-agent streams and react to each event.

        Never lets a pubsub error kill the loop — same resilience contract as
        StreamManager.stream_all_logs(): log, back off, reconnect. A Sentinel
        that silently stops watching is worse than one that logs a warning and
        keeps trying; watchdog.py is expected to grow a liveness check on this
        service itself (#590 scope point 6) so a stuck Sentinel is its own alert
        rather than a silent gap.
        """
        logger.info("[Sentinel] Service started")
        self._running = True
        while self._running:
            try:
                await self._consume()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(
                    "[Sentinel] pubsub consume error, reconnecting in %ss: %s",
                    _RECONNECT_DELAY_SECONDS, e,
                )
                await asyncio.sleep(_RECONNECT_DELAY_SECONDS)

    def stop(self) -> None:
        """Signal run() to exit after the current message (graceful shutdown)."""
        self._running = False

    async def _consume(self) -> None:
        """Hold one pubsub subscription open and hand each message to _handle_message."""
        if not self.redis.client:
            await asyncio.sleep(_RECONNECT_DELAY_SECONDS)
            return
        # Beide Stroeme per Muster, beide je Agent. Der Chat laeuft ueber einen
        # eigenen Kanal (`publish_chat`) — ohne ihn waere der Sentinel blind fuer
        # den gesamten Gespraechsverkehr, und genau dort arbeitet ein interaktiv
        # genutzter Agent hauptsaechlich.
        #
        # Per Muster statt per Aenderung am Veroeffentlicher: so bleiben alle
        # bestehenden Lauscher (channel_gateway, StreamManager) unberuehrt.
        pubsub = self.redis.client.pubsub()
        await pubsub.psubscribe(*_AGENT_PATTERNS)
        try:
            while self._running:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                await self._herzschlag()
                if message and message["type"] in ("message", "pmessage"):
                    await self._handle_message(message["data"], message.get("channel"))
                else:
                    await asyncio.sleep(0.01)
        finally:
            await pubsub.punsubscribe(*_AGENT_PATTERNS)
            await pubsub.aclose()

    async def _herzschlag(self) -> None:
        """Ein Lebenszeichen nach Redis — hoechstens einmal pro Intervall.

        Ein Waechter, der unbemerkt stehenbleibt, ist schlimmer als keiner: die
        Anlage sieht dann ueberwacht aus und ist es nicht. Der Wachhund im
        Zeitplaner liest diesen Zeitstempel und meldet sich, wenn er altert
        (#590 Punkt 6).

        Bewusst in der Warteschleife und nicht nur beim Ereignis: ein Sentinel,
        der stundenlang nichts zu tun hat, ist gesund — einer, der haengt, nicht.
        Beide sehen ohne diesen Schlag gleich aus.

        Fehler beim Schreiben werden verschluckt: ein Waechter, der wegen seines
        eigenen Lebenszeichens abstuerzt, waere absurd.
        """
        jetzt = time.time()
        if jetzt - self._letzter_schlag < _HERZSCHLAG_INTERVALL_SEKUNDEN:
            return
        self._letzter_schlag = jetzt
        try:
            if self.redis.client:
                await self.redis.client.set(SENTINEL_HEARTBEAT_KEY, str(jetzt))
        except Exception:  # noqa: BLE001
            logger.debug("[Sentinel] Herzschlag konnte nicht geschrieben werden", exc_info=True)

    async def _handle_message(self, raw: bytes | str, kanal: bytes | str | None = None) -> None:
        """Decode one raw pubsub payload and route it, tolerating malformed events.

        `kanal` ist der Kanal, auf dem die Nachricht ankam — die einzige
        vertrauenswuerdige Quelle fuer die Agenten-Zuordnung (siehe Modul-Kopf).
        """
        agent_id = agent_id_aus_kanal(kanal) if kanal is not None else None
        if not agent_id:
            # Ohne belegbare Herkunft wird nichts geprueft: ein Stopp gegen einen
            # geratenen Agenten waere schlimmer als ein verpasstes Ereignis.
            logger.debug("[Sentinel] Ereignis ohne zuordenbaren Kanal verworfen")
            return
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            event = json.loads(raw)
        except (ValueError, TypeError) as e:
            logger.warning("[Sentinel] Could not decode event, skipping: %s", e)
            return
        if not isinstance(event, dict):
            return
        await self._handle_event(agent_id, event)

    async def _handle_event(self, agent_id: str, event: dict) -> None:
        """Scan one decoded event; on a trigger, stop the agent and notify in parallel.

        `agent_id` kommt vom Aufrufer aus dem Kanalnamen. Ein etwaiges Feld
        `agent_id` in `event` wird bewusst NICHT gelesen — es ist die
        Selbstauskunft des Absenders und war der Kern der Luecke (#590).

        `asyncio.gather` (not sequential awaits) per #590 scope point 4: a
        blocking stop_agent() call must never delay the human-facing alert.
        """
        if not agent_id:
            return
        verdict = await self._scan(agent_id, event)
        if verdict is None or not verdict.triggered:
            return
        reason = verdict.reason or "sentinel_triggered"
        if self._bereits_gemeldet(agent_id, reason):
            logger.info("[Sentinel] %s/%s erneut erkannt — Sperre laeuft noch", agent_id, reason)
            return
        await asyncio.gather(
            self._stop_agent(agent_id, reason),
            self._notify(agent_id, reason, verdict.excerpt),
            return_exceptions=True,
        )

    def _text_of(self, event: dict) -> str:
        """Den pruefbaren Text aus einem Ereignis holen — begrenzt.

        ``data`` ist mal ein String, mal ein Verschachtelungsknaeuel (Werkzeug-
        Eingaben, Ergebnisse). Ein Ereignis kann sehr gross werden; ohne Grenze
        wuerde ein einzelnes Dateilesen den Sentinel minutenlang beschaeftigen und
        damit alle folgenden Ereignisse verzoegern.
        """
        data = event.get("data")
        if isinstance(data, str):
            text = data
        elif isinstance(data, dict):
            try:
                text = json.dumps(data, ensure_ascii=False)
            except (TypeError, ValueError):
                text = str(data)
        else:
            text = str(data or "")
        return text[:_MAX_SCAN_CHARS]

    async def _scan(self, agent_id: str, event: dict) -> SentinelVerdict | None:
        """Die Erkennung (#592) — deterministisch, ohne zusaetzlichen Modellaufruf.

        **Warum kein Reviewer-Modell hier:** dieser Pfad sieht JEDES Ereignis
        JEDES Agenten. Ein Modellaufruf pro Ereignis waere im Betrieb weder
        bezahlbar noch schnell genug, um eine schaedliche Handlung waehrend sie
        geschieht zu erwischen — und genau das ist der Zweck (#588). Die teure
        Inhaltspruefung („ist die Antwort korrekt") gehoert in die vorhandene
        Egress-Pipeline, wo sie EINMAL pro ausgehender Nachricht laeuft.

        Hier stehen deshalb die zwei Signale, die scharf, billig und
        eindeutig sind — beide aus vorhandenen, bereits erprobten Bausteinen:

        1. **Geheimnis in der Ausgabe** (``core.dlp``). Der Egress-Filter sieht
           nur, was NACH DRAUSSEN geht; der Sentinel sieht auch Werkzeugaufrufe
           und -ergebnisse. Ein Zugangsschluessel, der dort auftaucht, ist ein
           Vorfall, unabhaengig davon, ob er je verschickt wird.
        2. **Prompt-Injektion** (``security.agent_guard``). Fremde Inhalte, die
           dem Agenten Anweisungen unterschieben wollen — das ist der Fall, den
           ein Agent per Selbstpruefung nicht abfangen kann, weil die Injektion
           genau diese Selbstpruefung mit angreift.

        Fail-open ist Pflicht: ein Fehler in der Erkennung darf nie einen Agenten
        anhalten. Im Zweifel laufen lassen und nichts melden.
        """
        try:
            text = self._text_of(event)
            if not text.strip():
                return None

            # Frueher stand hier eine Ausnahme fuer eigene Meldungen: Ereignisse
            # mit `type == "system"` und "[Sentinel]" im Text wurden uebersprungen.
            # Beides steht dem Agenten frei — die Ausnahme war damit ein
            # Freifahrtschein: Text mit "[Sentinel]" versehen, Typ auf "system"
            # setzen, und die Pruefung fiel aus. Entfernt, weil sie nichts
            # schuetzte: der Sentinel meldet ueber die Datenbank (`_notify`),
            # nicht ueber Redis, und kein Melder im Orchestrator schreibt
            # "[Sentinel]" in eine Ereignis-Nutzlast. Es gab also nie eine
            # eigene Meldung, die hier haette zurueckkommen koennen.

            from app.core import dlp
            from app.security.agent_guard import detect_injection

            verdaechtig, muster = detect_injection(text)
            if verdaechtig:
                return SentinelVerdict(
                    True, "prompt_injection",
                    # Das Muster selbst, nicht der umgebende Text: der koennte
                    # beliebige Inhalte enthalten.
                    excerpt=muster[:200],
                )

            # Bewusst NICHT `scan_matches`: das ist die Maskier-Schwelle. Ein
            # Stopp braucht die engere — siehe find_high_confidence_secrets.
            treffer = {"secret": dlp.find_high_confidence_secrets(text)}
            if treffer.get("secret"):
                return SentinelVerdict(
                    True, "secret_in_output",
                    # NIEMALS der Klartext. `mask_sample` laesst nur Anfang und
                    # Ende stehen — genug zum Wiedererkennen, zu wenig zum
                    # Benutzen. Ein Vorfallbericht, der das Geheimnis erneut
                    # ausschreibt, ist selbst ein Leck.
                    excerpt=", ".join(dlp.mask_sample(m) for m in treffer["secret"][:3]),
                )
            return None
        except Exception:  # noqa: BLE001
            logger.exception("[Sentinel] Fehler in der Erkennung — Ereignis wird durchgelassen")
            return None

    def _bereits_gemeldet(self, agent_id: str, reason: str) -> bool:
        """Denselben Agenten nicht im Sekundentakt erneut anhalten.

        Ein Agent, der ein Geheimnis ausgibt, tut das oft in mehreren Ereignissen
        kurz hintereinander. Ohne diese Sperre entstuenden daraus ein Dutzend
        Stopp-Versuche und ein Dutzend Meldungen fuer denselben Vorfall.
        """
        schluessel = (agent_id, reason)
        jetzt = time.monotonic()
        letzter = self._letzter_vorfall.get(schluessel, 0.0)
        if jetzt - letzter < _VORFALL_SPERRE_SEKUNDEN:
            return True
        self._letzter_vorfall[schluessel] = jetzt
        return False

    async def _stop_agent(self, agent_id: str, reason: str) -> None:
        """Den Agenten wirklich anhalten — in-process, wie der Zeitplaner es tut.

        **Warum kein HTTP mit dem Sentinel-Token:** der Dienst laeuft im selben
        Prozess wie der Orchestrator. Ein Aufruf ueber das Netz an die eigene
        Anwendung waere ein Umweg mit zwei zusaetzlichen Fehlerquellen (Netz,
        Serialisierung) und einer Sperrgefahr, wenn der Ereignisstrom gerade den
        einzigen Arbeiter belegt. ``scheduler_service`` haelt Agenten seit jeher
        direkt an; derselbe Weg, dieselbe Erwartung. Das eigene Zugangsschema
        bleibt trotzdem noetig — fuer den Fall, dass der Sentinel spaeter in einen
        eigenen Prozess wandert (#588).

        Faellt das Anhalten aus, wird das TROTZDEM vermerkt: ein
        Sicherheitsvorfall ohne Spur ist schlimmer als einer ohne Reaktion.

        **Vertrauensgrenze vor Wirkung (#590 Review-Auflage):** die Herleitung
        der Zuordnung aus dem Kanalnamen ist nur dann eine echte Grenze, wenn
        die Redis-ACL aktiv ist — sonst teilen sich alle Agent-Container
        weiterhin ein gemeinsames `requirepass` und koennten den Kanal eines
        anderen Agenten beschreiben (siehe Modul-Kopf). Ohne aktive ACL bliebe
        `_stop_agent` also exakt die Waffe, die #590 schliessen sollte, nur
        unter neuem Namen. Deshalb haelt dieser Pfad nur an, wenn
        `settings.redis_acl_enabled` an ist; ohne sie wird der Fund trotzdem
        vermerkt und gemeldet (wie beim Fehlschlag unten), nur eben nicht
        durchgesetzt. Erkennen darf der Sentinel immer — anhalten nur, wenn
        der Zuordnung tatsaechlich zu trauen ist.
        """
        from app.db.session import async_session_factory
        from app.models.audit_log import AuditEventType, AuditLog

        gestoppt = False
        uebersprungen = not settings.redis_acl_enabled
        fehler: str | None = None
        if uebersprungen:
            fehler = (
                "Redis-ACL ist nicht aktiv (redis_acl_enabled=False) — die "
                "Kanalnamen-Zuordnung ist ohne sie keine echte Vertrauensgrenze, "
                "daher kein automatisches Anhalten."
            )
            logger.warning(
                "[Sentinel] Anhalten von %s uebersprungen (Grund: %s) — Redis-ACL aus",
                agent_id, reason,
            )
        else:
            try:
                if self.docker is None:
                    raise RuntimeError("kein Docker-Dienst — Sentinel kann nicht stoppen")
                from app.core.agent_manager import AgentManager

                async with async_session_factory() as db:
                    await AgentManager(db, self.docker, self.redis).stop_agent(agent_id)
                gestoppt = True
                logger.warning("[Sentinel] Agent %s angehalten (Grund: %s)", agent_id, reason)
            except Exception as e:  # noqa: BLE001 — der Vermerk unten ist wichtiger
                fehler = str(e)[:300]
                logger.error("[Sentinel] Agent %s konnte NICHT angehalten werden: %s", agent_id, fehler)

        try:
            async with async_session_factory() as db:
                db.add(AuditLog(
                    agent_id=agent_id,
                    event_type=AuditEventType.AGENT_STOPPED.value,
                    outcome="success" if gestoppt else ("skipped" if uebersprungen else "failure"),
                    meta={"by": "sentinel", "reason": reason, **({"error": fehler} if fehler else {})},
                ))
                await db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("[Sentinel] Vermerk im Pruefprotokoll fehlgeschlagen")

        if not gestoppt:
            # Der gefaehrlichere Fall: erkannt, aber nicht gestoppt. Ohne diese
            # zweite Meldung stuende in der Oberflaeche nur „Vorfall erkannt",
            # und der Betreiber wuerde annehmen, die Sache sei erledigt.
            try:
                from app.models.notification import Notification

                titel = (
                    "Sentinel hat einen Vorfall erkannt, aber nicht angehalten"
                    if uebersprungen else
                    "Sentinel konnte den Agenten NICHT anhalten"
                )
                async with async_session_factory() as db:
                    db.add(Notification(
                        agent_id=agent_id,
                        type="error",
                        title=titel,
                        message=(
                            f"Vorfall: {reason}. Der Agent laeuft weiter. "
                            f"{'Grund: ' if uebersprungen else 'Fehler beim Anhalten: '}"
                            f"{fehler or 'unbekannt'}. "
                            "Bitte von Hand stoppen."
                        )[:2000],
                        priority="urgent",
                        action_url=f"/agents/{agent_id}",
                    ))
                    await db.commit()
            except Exception:  # noqa: BLE001
                logger.exception("[Sentinel] Zweite Meldung fehlgeschlagen")

    async def _notify(self, agent_id: str, reason: str, excerpt: str | None) -> None:
        """Den Menschen erreichen — und zwar so, dass er es nicht uebersieht.

        Der Auszug wird auf 500 Zeichen begrenzt und ungefiltert uebernommen: er
        stammt aus einer Agentenausgabe und kann alles enthalten, was diese
        enthaelt. Er landet in einer Benachrichtigung, die nur der Betreiber
        sieht — nicht in einem Kanal nach draussen.
        """
        from app.db.session import async_session_factory
        from app.models.notification import Notification

        try:
            async with async_session_factory() as db:
                db.add(Notification(
                    agent_id=agent_id,
                    type="error",
                    title="Sentinel: Vorfall erkannt",
                    message=(
                        f"Grund: {reason}."
                        + (f" Auszug: {excerpt[:500]}" if excerpt else "")
                        + " Der Agent wird angehalten. Ob das gelungen ist, steht "
                          "im Pruefprotokoll — scheitert es, kommt eine zweite, "
                          "dringendere Meldung."
                    )[:2000],
                    priority="urgent",
                    action_url=f"/agents/{agent_id}",
                ))
                await db.commit()
            logger.warning("[Sentinel] Betreiber benachrichtigt (%s, %s)", agent_id, reason)
        except Exception:  # noqa: BLE001 — eine Meldung darf den Stopp nie verhindern
            logger.exception("[Sentinel] Benachrichtigung fehlgeschlagen")
