"""Einen Unterprozess begleiten und nur bei STILLSTAND abbrechen.

Die eine Stelle, an der diese Regel lebt. Feste Gesamtdauern haben arbeitende
Agenten mitten im Auftrag abgeschossen — im Chat nach 600s, bei Agent-zu-Agent-
Nachrichten nach 300s. Beides ist die falsche Groesse: Es zaehlt nicht, wie lange
etwas dauert, sondern ob es noch laeuft.

Jede Regung des Prozesses — ein Block auf stdout, eine Zeile auf stderr — setzt
die Uhr zurueck. Abgebrochen wird, wer wirklich verstummt. Ein Gesamtlimit bleibt
als Notbremse gegen einen Prozess, der endlos Ausgabe erzeugt und nie fertig wird.
"""

from __future__ import annotations

import asyncio
import time


class ProcessIdleTimeout(Exception):
    """Der Prozess hat `idle_limit` Sekunden lang nichts mehr von sich gegeben."""


async def _drain(stream, sink: bytearray, tick) -> None:
    """Einen Stream leerlesen und dabei das Lebenszeichen setzen."""
    if stream is None:
        return
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            return
        sink.extend(chunk)
        tick()


async def communicate_with_idle_timeout(
    process,
    idle_limit: float,
    *,
    stdin_input: bytes | None = None,
    hard_cap: float | None = None,
    on_activity=None,
) -> tuple[bytes, bytes]:
    """Wie ``process.communicate()``, aber mit Stillstands- statt Gesamtlimit.

    Gibt ``(stdout, stderr)`` zurueck. Wirft ``ProcessIdleTimeout``, wenn
    ``idle_limit`` Sekunden lang keinerlei Ausgabe kam (oder ``hard_cap``
    ueberschritten wurde) — der Prozess wird dabei beendet.

    ``on_activity`` wird bei jeder Regung aufgerufen; darueber meldet der Aufrufer
    das Lebenszeichen nach aussen weiter (z.B. an den LogPublisher).
    """
    last = time.monotonic()
    started = last

    def tick() -> None:
        nonlocal last
        last = time.monotonic()
        if on_activity is not None:
            on_activity()

    if stdin_input is not None and process.stdin is not None:
        process.stdin.write(stdin_input)
        try:
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        process.stdin.close()

    out, err = bytearray(), bytearray()
    readers = asyncio.gather(
        _drain(process.stdout, out, tick),
        _drain(process.stderr, err, tick),
    )
    waiter = asyncio.ensure_future(process.wait())

    try:
        while True:
            done, _ = await asyncio.wait({waiter}, timeout=2.0)
            if done:
                await readers          # Reste einsammeln
                return bytes(out), bytes(err)
            now = time.monotonic()
            if now - last > idle_limit or (hard_cap and now - started > hard_cap):
                raise ProcessIdleTimeout(
                    f"keine Ausgabe seit {int(now - last)}s (Grenze {int(idle_limit)}s)"
                )
    except ProcessIdleTimeout:
        readers.cancel()
        waiter.cancel()
        try:
            process.kill()
        except ProcessLookupError:
            pass
        raise
    except BaseException:
        readers.cancel()
        waiter.cancel()
        raise


async def prozessgruppe_beenden(proc, wartezeit: float = 5.0) -> None:
    """Einen abgebrochenen Lauf samt ALLEN Kindprozessen beenden (#926).

    Scheiterte die Auswertung eines Laufs (z. B. kurze Redis-Stoerung), meldete der
    Runner „gescheitert“, liess den CLI-Prozess aber weiterlaufen — und mit ihm
    Render-, Browser- und ffmpeg-Prozesse, die weiter in den Arbeitsordner
    schrieben, waehrend der Neuversuch dort schon arbeitete. Voraussetzung: der
    Prozess wurde mit ``start_new_session=True`` gestartet, dann ist seine PID
    zugleich die Kennung seiner Prozessgruppe.
    """
    import os
    import signal

    if proc is None or proc.returncode is not None:
        return
    def _signal(sig) -> None:
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        except OSError:
            try:
                proc.send_signal(sig)
            except ProcessLookupError:
                pass

    _signal(signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), timeout=wartezeit)
    except asyncio.TimeoutError:
        pass
    # Auch wenn der Hauptprozess schon weg ist: verwaiste Kinder der Gruppe mitnehmen.
    _signal(signal.SIGKILL)
    try:
        await asyncio.wait_for(proc.wait(), timeout=wartezeit)
    except asyncio.TimeoutError:
        pass
