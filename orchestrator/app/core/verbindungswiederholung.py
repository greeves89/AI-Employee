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

class VerbindungNichtZustandeGekommen(OSError):
    """Verbindungsfehler aus einem Transport, der kein ``httpx`` ist.

    ``httpx`` bildet einen Namensaufloesungs-Aussetzer auf ``ConnectError`` ab, und
    darauf kann man pruefen. Der Skill-Crawler klont seine Git-Quellen aber ueber
    ``git clone`` als Unterprozess: dort kommt derselbe Ausfall als Rueckgabewert
    ungleich 0 mit "Could not resolve host" auf der Fehlerausgabe an und steckte
    bisher in einem allgemeinen ``RuntimeError``. Damit war er von einem echten
    Mangel an der Quelle (Repo weg, Zugangsdaten falsch) nicht zu unterscheiden —
    und genau diese Unterscheidung entscheidet, ob ein leeres Ergebnis den guten
    Katalog ueberschreiben darf.
    """


# Genau diese Klasse: die Verbindung kam nicht zustande, die Anfrage ist also sicher
# nicht beim Dienst gelandet. Die DNS-Aussetzer der Anlage landen hier
# (ConnectError "[Errno -3] Temporary failure in name resolution").
VERBINDUNGSFEHLER: tuple[type[BaseException], ...] = (
    VerbindungNichtZustandeGekommen,  # git clone & andere Transporte ohne httpx
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

# Nach so vielen Verbindungsfehlern HINTEREINANDER wird ein Hintergrundlauf
# abgebrochen. Grund: die Leiter oben deckt 40 s je Abruf, und der Gesetz-Crawler
# holt rund 6000 Normen sequentiell. Bleibt das Netz dauerhaft weg, waeren das
# 6000 x 40 s = rund 68 Stunden in EINEM Lauf — der nachgelagerte EU-Lauf kaeme
# tagelang nicht mehr dran. Vorher war ein solcher Lauf in Minuten durch und am
# naechsten Tag wieder faellig; diese Eigenschaft soll der Fix nicht kosten.
# 20 Fehler in Folge sind eindeutig: ein Aussetzer, der nur ein Fenster lang
# dauert, wird von der Leiter abgefangen und setzt die Serie zurueck.
HINTERGRUND_ABBRUCH_NACH = 20

# Fehlerausgaben von ``git``, die einen Netz-/Namensaufloesungsausfall bedeuten und
# nicht einen Mangel an der Quelle selbst. Kleingeschrieben verglichen.
GIT_NETZ_MARKER: tuple[str, ...] = (
    "could not resolve host",
    "temporary failure in name resolution",
    "connection timed out",
    "connection refused",
    "network is unreachable",
    "failed to connect to",
    "could not read from remote repository",
    "ssl connect error",
)


class NetzaussetzerAbbruch(RuntimeError):
    """Der Lauf ist am Netz gescheitert und hat nichts Brauchbares erzeugt.

    Wichtig fuer die Aufrufer AUS DER OBERFLAECHE: ein leeres Ergebnis ohne diese
    Ausnahme ist von "es gibt wirklich nichts" nicht zu unterscheiden. Ein
    Administrator, der "Aktualisieren" drueckt, bekam deshalb "ok, 0 importiert"
    gemeldet, obwohl ueberhaupt kein Lauf zustande kam (#746).
    """


def ist_git_netzfehler(fehlerausgabe: str) -> bool:
    """Steht in der Fehlerausgabe von ``git`` ein Netzausfall?"""
    return any(marker in fehlerausgabe.lower() for marker in GIT_NETZ_MARKER)


def lauf_unvollstaendig(*, erzeugt: int, bisher: int, verbindungsausfaelle: int) -> bool:
    """Darf dieser Lauf den Frischestempel NICHT weiterruecken?

    Zwei Bedingungen zusammen: am Netz gescheitert UND schlechter als der bisherige
    Stand. Die erste allein genuegt nicht (ein paar verlorene Normen von 6000 sind
    kein Grund, den Stempel einzufrieren), die zweite allein auch nicht (eine Quelle
    darf schrumpfen, wenn sie wirklich geschrumpft ist).

    Die Pruefung auf ``erzeugt == 0`` deckt den ersten Lauf ab: dort ist ``bisher``
    ebenfalls 0, und "gerade gecrawlt, 0 Treffer" ist genau die Falschaussage, um
    die es in #746 geht — ``/api/compliance`` zeigt Stempel und Zahl zusammen an.
    """
    if verbindungsausfaelle <= 0:
        return False
    return erzeugt == 0 or erzeugt < bisher


class Netzwache:
    """Zaehlt die Verbindungsfehler eines Hintergrundlaufs und begrenzt ihn.

    Zwei Zahlen, zwei verschiedene Fragen:

    * ``anzahl`` — wie viele Abrufe des Laufs sind an der VERBINDUNG gescheitert.
      Beantwortet: ist ein leeres Ergebnis ein Netzaussetzer oder ein echter
      Leerstand (siehe :func:`lauf_unvollstaendig`).
    * ``serie`` — wie viele davon unmittelbar hintereinander. Beantwortet: laeuft
      die Wiederholung ins Leere und sollte der Lauf aufgeben
      (:attr:`abbrechen`, siehe ``HINTERGRUND_ABBRUCH_NACH``).
    """

    def __init__(self, zweck: str, grenze: int | None = None) -> None:
        self.zweck = zweck
        # Erst hier nachsehen, nicht als Standardargument: ein Standardwert wird
        # zur Definitionszeit gebunden, und ein Test, der die Grenze setzt, waere
        # wirkungslos (dieselbe Falle wie bei ``PAUSEN_S`` weiter unten).
        self.grenze = HINTERGRUND_ABBRUCH_NACH if grenze is None else grenze
        self.anzahl = 0
        self.serie = 0

    def melde(self, fehler: BaseException) -> bool:
        """Fehler einsortieren. Gibt zurueck, ob es ein Verbindungsfehler war.

        Ein Fehler AM INHALT (kaputtes XML, Quelle weg) setzt die Serie zurueck: er
        beweist, dass die Verbindung stand.
        """
        if isinstance(fehler, VERBINDUNGSFEHLER):
            self.anzahl += 1
            self.serie += 1
            if self.abbrechen:
                logger.error(
                    "%s: %d Verbindungsfehler hintereinander — Lauf wird abgebrochen, "
                    "damit er nicht in der Wiederholung haengen bleibt.",
                    self.zweck, self.serie,
                )
            return True
        self.serie = 0
        return False

    def erfolg(self) -> None:
        """Ein Abruf hat geklappt — die Serie beginnt neu."""
        self.serie = 0

    @property
    def abbrechen(self) -> bool:
        return self.serie >= self.grenze


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
