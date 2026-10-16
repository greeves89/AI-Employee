"""Zweiter Versuch fuer Zustellungen, deren Verbindung nie zustande kam.

Ein Namensaufloesungs-Aussetzer von zwei Sekunden hat auf einer Kundenanlage einen
Push an das Geraet des Nutzers verschluckt: ``APNsService.send`` faengt jede
Ausnahme, protokolliert eine Zeile und gibt ``False`` zurueck — ein zweiter Versuch
findet nicht statt. Die Meldung steht danach nur noch im Benachrichtigungszentrum,
das Telefon bleibt still. Bei einer Freigabe-Anfrage heisst das: der Agent wartet,
und niemand weiss davon. Web Push hat dieselbe Stelle.

**Wiederholt wird ausschliesslich die Klasse "Anfrage nie abgeschickt"**
(``httpx.ConnectError``, ``httpx.ConnectTimeout``) — dort ist ein zweiter Versuch
nachweislich ungefaehrlich, weil beim Push-Dienst garantiert nichts angekommen ist.
Ein ``ReadTimeout`` wird bewusst NICHT wiederholt: dort kann die Anfrage sehr wohl
angekommen sein, und die Wiederholung wuerde dieselbe Meldung zweimal auf dem Geraet
erscheinen lassen. Eine verlorene Meldung ist besser als zwei gleiche.

Dieselbe Regel steht ein zweites Mal in ``app/core/oauth_retry.py``
(``post_refresh_with_retry``, mit Jitter, fuer den Token-Austausch). Die beiden
gehoeren zusammengefuehrt — das beruehrt aber den OAuth-Pfad und gehoert deshalb
nicht in dieselbe Aenderung.

Auf dem gesunden Weg kostet das **nichts**: kein zusaetzlicher Aufruf, keine
zusaetzliche Wartezeit. Die Pausen entstehen nur, wenn die Verbindung schon
gescheitert ist. Das ist wichtig, weil zwei Aufrufer (``POST /notifications/`` und
das Anlegen einer Freigabe) im HTTP-Pfad einer Nutzeranfrage liegen — eine
Wartezeit, die auch bei gesunder Verbindung anfaellt, waere dort nicht vertretbar.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

logger = logging.getLogger(__name__)

# Genau diese Klasse: die Verbindung kam nicht zustande, die Anfrage ist also sicher
# nicht beim Dienst gelandet. Die DNS-Aussetzer der Anlage landen hier
# (ConnectError "[Errno -3] Temporary failure in name resolution").
VERBINDUNGSFEHLER: tuple[type[BaseException], ...] = (
    httpx.ConnectError,      # DNS/TCP: "[Errno -3] Temporary failure in name resolution"
    httpx.ConnectTimeout,    # Netz schluckt Pakete, Verbindung kam nie zustande
    httpx.PoolTimeout,       # keine Verbindung aus dem Pool bekommen
    httpx.ProxyError,        # CONNECT beim Proxy gescheitert
)
# Bewusst NICHT darin: ReadTimeout, ReadError, WriteError, RemoteProtocolError. Dort
# koennen die Bytes schon draussen sein. Keiner der vier Eintraege oben ist eine
# breite Basisklasse (alle liegen unter TransportError, aber als Geschwister), es
# wird also nichts versehentlich mitgefangen.

# Zwei Wiederholungen, zusammen 2,0 s Deckung. Gemessen auf der Anlage: die
# Aussetzer dauerten 2 bis 5 Sekunden; ein laengeres Nachhaken wuerde im HTTP-Pfad
# die Anfrage des Nutzers aufhalten, ohne die langen Faelle zuverlaessig zu retten.
PAUSEN_S: tuple[float, ...] = (0.4, 1.6)

# Fuer Laeufe im Hintergrund (Crawler, geplante Abgleiche) ist die Leiter oben zu
# kurz. Der Unterschied ist nicht Geschmack, sondern gemessen: das laengste
# Aussetzer-Fenster der Anlage dauerte 34 Sekunden (13.10., 04:00:57-04:01:31) und
# hat damit ZWEI vollstaendige Laeufe gekostet (Skill-Crawler ueber alle Quellen,
# Gesetz-Crawler EU und DE). Mit 2,0 s Deckung waere keiner der beiden gerettet
# worden - ein Fix in dieser Groesse haette also nur so ausgesehen.
#
# Hier wartet kein Mensch auf eine Antwort: der Skill-Crawler laeuft woechentlich,
# der Gesetz-Crawler taeglich, beide in einer Hintergrundschleife. 40 s Deckung
# kosten im gesunden Fall nichts (die Pausen entstehen nur nach einem
# Verbindungsfehler) und im kranken Fall hoechstens 40 s an einem Lauf, der sonst
# komplett verloren ist.
PAUSEN_HINTERGRUND_S: tuple[float, ...] = (2.0, 8.0, 30.0)

T = TypeVar("T")


async def mit_verbindungswiederholung(
    senden: Callable[[], Awaitable[T]],
    *,
    zweck: str,
    pausen: tuple[float, ...] | None = None,
) -> T:
    """``senden()`` erneut versuchen, solange nur die Verbindung scheiterte.

    Gibt den Rueckgabewert des ersten erfolgreichen Versuchs zurueck. Scheitert auch
    der letzte Versuch, wird dessen Ausnahme weitergegeben — der Aufrufer behandelt
    sie dann wie bisher. Jede andere Ausnahme (Lesefehler, Protokollfehler, Fehler im
    Aufbau der Anfrage) wird unverzuegert durchgelassen.
    """
    # Erst hier nachsehen, nicht als Standardargument: ein Standardwert wird zur
    # Definitionszeit gebunden, und ein Test, der PAUSEN_S setzt, waere wirkungslos.
    if pausen is None:
        pausen = PAUSEN_S
    versuche = len(pausen) + 1
    letzter: BaseException | None = None
    for nummer, pause in enumerate((None, *pausen), start=1):
        if pause is not None:
            await asyncio.sleep(pause)
        try:
            return await senden()
        except VERBINDUNGSFEHLER as e:
            letzter = e
            logger.warning(
                "%s: Verbindung nicht zustande gekommen (Versuch %d/%d): %s",
                zweck, nummer, versuche, e,
            )
    assert letzter is not None  # Die Schleife endet nur ueber diesen Zweig.
    raise letzter
