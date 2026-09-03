"""Ein EINZIGER Deckel fuer alle drei Consumer-Pools (Issue #628 Phase 2).

Der Deckel aus Phase 1 (``pids_budget.max_concurrent_runs``) sitzt nur in
``task_consumer._max_parallel_tasks()``. Es gibt aber drei Stellen, die je
einen vollen Lauf samt MCP-Satz starten koennen: Aufgaben, Chat und
Agent-Nachrichten — alle drei laufen laut ``main.py`` per ``asyncio.gather``
im selben Prozess. Jeder Pool fuer sich gegen dasselbe Container-Budget
gerechnet ergibt zusammen eine falsche Summe: Aufgaben 4 + Chat 4 +
Nachrichten 4 = 12 Laeufe x 88 Threads > 512, mit jedem Pool einzeln
"korrekt" gedeckelt.

Die einzelnen ``MAX_PARALLEL_*``-Obergrenzen bleiben als Wunsch pro Pool
erhalten (sie begrenzen, wie viele eigene Laeufe ein Pool ueberhaupt
versucht) — die harte Grenze ist ab jetzt dieser EINE prozessweite
``asyncio.Semaphore``.

Ein Platz bleibt exklusiv dem Chat vorbehalten: haelt eine lange Aufgabe
alle gemeinsamen Plaetze, darf ein Chat nicht beliebig warten, sonst wird
der Agent im Gespraech scheinbar taub.
"""

import asyncio
import contextlib
import logging
import os

from app.pids_budget import DEFAULT_COST_PER_RUN, DEFAULT_RESERVE, max_concurrent_runs

logger = logging.getLogger(__name__)

#: Wie viele Plaetze exklusiv dem Chat vorbehalten bleiben — mindestens einer,
#: solange das Gesamtbudget das hergibt.
CHAT_RESERVED_SLOTS = 1


class RunBudget:
    """Teilt ``total`` Plaetze auf einen gemeinsamen Topf und einen fuer Chat
    reservierten Rest auf. Aufgaben und Nachrichten nehmen ausschliesslich aus
    dem gemeinsamen Topf; Chat nimmt bevorzugt aus dem gemeinsamen Topf und
    faellt nur auf den reservierten Platz zurueck, wenn der gemeinsame Topf
    leer ist.
    """

    def __init__(self, total: int, chat_reserved: int = CHAT_RESERVED_SLOTS):
        total = max(1, total)
        chat_reserved = min(max(0, chat_reserved), max(0, total - 1))
        self.total = total
        self.chat_reserved = chat_reserved
        self._shared = asyncio.Semaphore(total - chat_reserved)
        self._chat_only = asyncio.Semaphore(chat_reserved)
        logger.info(
            "RunBudget: %d Plaetze gesamt (%d gemeinsam, %d exklusiv fuer Chat)",
            total, total - chat_reserved, chat_reserved,
        )

    @contextlib.asynccontextmanager
    async def slot_for_task(self):
        """Fuer Aufgaben und Agent-Nachrichten — ausschliesslich der
        gemeinsame Topf, nie der fuer Chat reservierte Platz."""
        async with self._shared:
            yield

    @contextlib.asynccontextmanager
    async def slot_for_chat(self):
        """Fuer Chat — nimmt, was zuerst frei wird. Race-sicher per
        ``asyncio.wait`` auf beide Semaphoren gleichzeitig: eine vorherige
        Pruefung wie ``if not self._shared.locked()`` waere ein Wettlauf, bei
        dem der gemeinsame Topf zwischen Pruefung und Zugriff wieder belegt
        sein kann — dann haette Chat auf den vollen Topf gewartet, waehrend
        der eigens fuer ihn reservierte Platz die ganze Zeit frei war."""
        if self.chat_reserved <= 0:
            async with self._shared:
                yield
            return

        shared_acquire = asyncio.ensure_future(self._shared.acquire())
        chat_acquire = asyncio.ensure_future(self._chat_only.acquire())
        done, pending = await asyncio.wait(
            {shared_acquire, chat_acquire}, return_when=asyncio.FIRST_COMPLETED
        )
        for p in pending:
            p.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await p

        used_shared = shared_acquire in done
        if used_shared and chat_acquire in done:
            # Beide gleichzeitig frei geworden — den ungenutzten reservierten
            # Platz sofort wieder freigeben, statt ihn liegen zu lassen.
            self._chat_only.release()

        try:
            yield
        finally:
            if used_shared:
                self._shared.release()
            else:
                self._chat_only.release()


_budget: RunBudget | None = None


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def get_run_budget() -> RunBudget:
    """Der EINE prozessweite ``RunBudget`` — beim ersten Zugriff gebaut,
    danach von allen drei Consumern wiederverwendet."""
    global _budget
    if _budget is None:
        total = max_concurrent_runs(
            reserve=_env_int("PIDS_RESERVE", DEFAULT_RESERVE),
            cost_per_run=_env_int("PIDS_COST_PER_RUN", DEFAULT_COST_PER_RUN),
        )
        _budget = RunBudget(total)
    return _budget


def reset_run_budget() -> None:
    """Nur fuer Tests: den Singleton verwerfen, damit der naechste Zugriff
    ``get_run_budget()`` neu baut (z.B. nach einem gepatchten Budget)."""
    global _budget
    _budget = None
