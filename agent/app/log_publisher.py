import asyncio
import json
import time
from datetime import datetime, timezone

import redis.asyncio as aioredis

#: Gemeinsamer kleiner Puffer fuer Chat-Text (#900). Mit Live-Streaming kommen
#: Teilstuecke von wenigen Zeichen; jedes einzeln waeren hunderte Redis- und
#: WebSocket-Nachrichten je Antwort. Gesammelt wird hoechstens so lange bzw. so
#: viel — das Auge merkt die Verzoegerung nicht, die Leitung schon.
PUFFER_SEKUNDEN = 0.08
PUFFER_ZEICHEN = 200
_TEXT_FELDER = {"text", "neuer_block"}


class LogPublisher:
    """Publishes agent events to Redis PubSub for real-time streaming."""

    def __init__(self, redis: aioredis.Redis, agent_id: str):
        self.redis = redis
        self.agent_id = agent_id
        self.last_activity_at = time.monotonic()
        #: Letzter Fortschritt JE AUFGABE. Absichtlich kein einzelner Wert wie
        #: ``last_activity_at``: bei ``MAX_PARALLEL_TASKS>1`` teilen sich alle
        #: Aufgaben EINEN LogPublisher. Ein gemeinsamer Zeitstempel hiesse, dass
        #: eine fleissige Aufgabe den Herzschlag einer laengst klemmenden am
        #: Leben haelt — genau die Blindstelle, die #730 beseitigen soll.
        self.last_activity_by_task: dict[str, float] = {}
        #: Noch nicht gesendeter Chat-Text je Nachricht (siehe ``publish_chat``).
        self._puffer: dict[str, dict] = {}
        self._puffer_uhr: dict[str, asyncio.Task] = {}
        #: Haelt die Reihenfolge: gepufferter Text geht IMMER vor dem naechsten
        #: Ereignis derselben Nachricht raus, auch wenn die Uhr gerade sendet.
        self._sende_sperre = asyncio.Lock()

    def notiere_fortschritt(self, task_id: str | None) -> None:
        """Haelt fest, dass an dieser Aufgabe gerade wirklich etwas passiert ist."""
        if task_id:
            self.last_activity_by_task[task_id] = time.monotonic()

    def vergiss_aufgabe(self, task_id: str | None) -> None:
        """Nach Ende der Aufgabe aufraeumen — sonst waechst die Zuordnung endlos."""
        if task_id:
            self.last_activity_by_task.pop(task_id, None)

    def stillstand_seit(self, task_id: str | None) -> float:
        """Sekunden seit dem letzten echten Fortschritt dieser Aufgabe."""
        if not task_id or task_id not in self.last_activity_by_task:
            return 0.0
        return time.monotonic() - self.last_activity_by_task[task_id]

    async def publish(self, task_id: str, event_type: str, data: dict | str) -> None:
        # Jedes Ereignis einer Aufgabe ist ein Fortschrittsbeleg: der Runner
        # veroeffentlicht hier jeden Textblock, jeden Werkzeugaufruf und jedes
        # Werkzeugergebnis. Bleibt das aus, arbeitet niemand mehr.
        self.notiere_fortschritt(task_id)
        message = json.dumps(
            {
                "agent_id": self.agent_id,
                "task_id": task_id,
                "type": event_type,
                "data": data,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        channel = f"agent:{self.agent_id}:logs"
        await self.redis.publish(channel, message)
        await self.redis.publish("agents:logs:all", message)
        # Store in activity history (keep last 200 events)
        history_key = f"agent:{self.agent_id}:activity"
        await self.redis.rpush(history_key, message)
        await self.redis.ltrim(history_key, -200, -1)

    async def publish_chat(self, message_id: str, event_type: str, data: dict | str) -> None:
        """Publish chat events to a dedicated chat channel.

        Nebenbei das LEBENSZEICHEN des laufenden Turns: jedes Ereignis — ein
        Werkzeugaufruf, ein Zwischenstand — setzt die Stillstandsuhr zurueck. Der
        Wachhund im ChatConsumer bricht nur ab, wenn wirklich nichts mehr kommt,
        statt nach einer festen Gesamtdauer mitten in die Arbeit zu schneiden.

        Text wird kurz gepuffert (``PUFFER_SEKUNDEN``/``PUFFER_ZEICHEN``) und als
        EIN ``text``-Ereignis derselben Form gesendet. Ein neuer Block
        (``neuer_block``) beginnt ein neues Ereignis, jedes andere Ereignis
        derselben Nachricht schiebt den gepufferten Text vorher hinaus. Gilt fuer
        alle Laufzeiten, die hier veroeffentlichen (Claude Code, Codex, Custom-LLM).
        """
        self.last_activity_at = time.monotonic()
        async with self._sende_sperre:
            if event_type == "text" and isinstance(data, dict) and set(data) <= _TEXT_FELDER:
                puffer = self._puffer.get(message_id)
                if puffer is not None and data.get("neuer_block"):
                    await self._puffer_leeren(message_id)
                    puffer = None
                if puffer is None:
                    self._puffer[message_id] = dict(data, text=str(data.get("text", "")))
                    self._uhr_stellen(message_id)
                else:
                    puffer["text"] += str(data.get("text", ""))
                if len(self._puffer[message_id]["text"]) >= PUFFER_ZEICHEN:
                    await self._puffer_leeren(message_id)
                return
            await self._puffer_leeren(message_id)
            await self._senden(message_id, event_type, data)

    def _uhr_stellen(self, message_id: str) -> None:
        async def uhr() -> None:
            await asyncio.sleep(PUFFER_SEKUNDEN)
            async with self._sende_sperre:
                if self._puffer_uhr.get(message_id) is asyncio.current_task():
                    self._puffer_uhr.pop(message_id, None)
                await self._puffer_leeren(message_id)

        alt = self._puffer_uhr.pop(message_id, None)
        if alt is not None:
            alt.cancel()
        self._puffer_uhr[message_id] = asyncio.create_task(uhr())

    async def _puffer_leeren(self, message_id: str) -> None:
        """Gepufferten Text dieser Nachricht senden (nur unter ``_sende_sperre``)."""
        uhr = self._puffer_uhr.pop(message_id, None)
        if uhr is not None and uhr is not asyncio.current_task():
            uhr.cancel()
        puffer = self._puffer.pop(message_id, None)
        if puffer is not None and puffer.get("text"):
            await self._senden(message_id, "text", puffer)

    async def _senden(self, message_id: str, event_type: str, data: dict | str) -> None:
        message = json.dumps(
            {
                "agent_id": self.agent_id,
                "message_id": message_id,
                "type": event_type,
                "data": data,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        await self.redis.publish(f"agent:{self.agent_id}:chat:response", message)
        # Publish "done" events to global channel for persistence independent of WebSocket
        if event_type == "done":
            await self.redis.publish("chat:completions", message)
            # Pub/Sub ist fluechtig: War der Orchestrator gerade nicht verbunden
            # (Neustart), ging die Schlussantwort verloren — im Chat blieb nur ein
            # Teilstueck (03.10.2026). Zusaetzlich dauerhaft ablegen; der
            # Orchestrator raeumt die Liste ab (main._drain_chat_completions).
            try:
                schluessel = f"agent:{self.agent_id}:chat:done"
                await self.redis.rpush(schluessel, message)
                await self.redis.ltrim(schluessel, -200, -1)
            except Exception:  # noqa: BLE001 — das Live-Ereignis ist schon raus
                pass

    async def publish_status(self, state: str, current_task: str = "", active_sessions: list[str] | None = None) -> None:
        # active_sessions: ALL source keys currently processing (e.g. ["chat:abc", "chat:def"])
        # so the UI can mark every busy conversation, not just one. JSON-encoded for the hash.
        import json as _json
        await self.redis.hset(
            f"agent:{self.agent_id}:status",
            mapping={
                "state": state,
                "current_task": current_task,
                "active_sessions": _json.dumps(active_sessions or ([current_task] if current_task else [])),
                "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            },
        )
