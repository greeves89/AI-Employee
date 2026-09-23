"""#838: 'haengt' muss fuer die SPERRE und fuer das AUFRAEUMEN dasselbe heissen.

Vorher galt ein Agent ab 30 Minuten ohne Lebenszeichen als ``blocked`` --
jeder faellige Zeitplan-Lauf wurde ab da uebersprungen und NICHT nachgeholt --,
waehrend der StaleTaskWatchdog die haengende Aufgabe erst nach
``watchdog_stale_task_minutes`` (Standard 180) beendete. In dem Fenster
dazwischen sperrte eine Aufgabe, die niemand abraeumte: gemessen acht
ersatzlos ausgefallene Laeufe an einem Tag.

Geprueft wird deshalb die GLEICHHEIT der beiden Schwellen -- an der Wirkung,
nicht am Quelltext: eine Aufgabe, die noch nicht abgeraeumt wuerde, darf auch
noch nicht sperren.
"""

import asyncio
import unittest
from datetime import datetime, timedelta, timezone

from app.models.task import TaskStatus
from app.services.scheduler_service import SchedulerService
from app.services.watchdog import _STALE_TASK_THRESHOLD, is_task_stale


class _Aufgabe:
    def __init__(self, agent_id, updated_at):
        self.agent_id = agent_id
        self.updated_at = updated_at
        # RUNNING, sonst prueft is_task_stale gar nicht erst die Zeit -- eine
        # Attrappe, die nie "haengt", koennte den Test nie rot machen.
        self.status = TaskStatus.RUNNING


class _DB:
    """Gibt genau die Aufgaben zurueck, die die Abfrage finden wuerde."""

    def __init__(self, aufgaben):
        self._aufgaben = aufgaben

    async def execute(self, _anweisung):
        aufgaben = self._aufgaben

        class _Ergebnis:
            def scalars(self):
                class _S:
                    def all(self_inner):
                        return aufgaben
                return _S()

        return _Ergebnis()


def _dienst() -> SchedulerService:
    return SchedulerService.__new__(SchedulerService)


class DieSchwelleIstDieselbeTests(unittest.TestCase):
    def test_die_konfigurierte_schwelle_gilt_auch_fuer_die_sperre(self):
        from app.config import settings

        erwartet = timedelta(minutes=max(1, int(settings.watchdog_stale_task_minutes)))
        self.assertEqual(_dienst()._stale_schwelle(), erwartet)

    def test_vorbedingung_die_beiden_werte_sind_wirklich_verschieden(self):
        """Ohne diesen Unterschied koennte der Test unten nie fehlschlagen --
        er wuerde die Luecke auch dann fuer geschlossen halten, wenn sie offen
        ist."""
        self.assertNotEqual(_STALE_TASK_THRESHOLD,
                            _dienst()._stale_schwelle())

    def test_eine_aufgabe_sperrt_erst_wenn_der_watchdog_sie_auch_beenden_wuerde(self):
        now = datetime.now(timezone.utc)
        schwelle = _dienst()._stale_schwelle()
        # Mitten im frueheren Loch: alter als der alte 30-Minuten-Rueckfall,
        # juenger als die Schwelle, ab der wirklich abgeraeumt wird.
        dazwischen = _STALE_TASK_THRESHOLD + (schwelle - _STALE_TASK_THRESHOLD) / 2
        aufgabe = _Aufgabe("a1", now - dazwischen)

        # Der Watchdog wuerde sie nicht anfassen ...
        self.assertFalse(is_task_stale(aufgabe, now, schwelle))
        # ... also darf sie auch nicht sperren.
        gezaehlt = asyncio.run(
            _dienst()._stale_task_count(_DB([aufgabe]), "a1", now)
        )
        self.assertEqual(gezaehlt, 0)

    def test_jenseits_der_schwelle_sperrt_sie_weiterhin(self):
        """Gegenprobe: die Sperre darf nicht einfach abgeschaltet sein."""
        now = datetime.now(timezone.utc)
        alt = _dienst()._stale_schwelle() + timedelta(minutes=5)
        aufgabe = _Aufgabe("a1", now - alt)

        self.assertTrue(is_task_stale(aufgabe, now, _dienst()._stale_schwelle()))
        gezaehlt = asyncio.run(
            _dienst()._stale_task_count(_DB([aufgabe]), "a1", now)
        )
        self.assertEqual(gezaehlt, 1)

    def test_fremde_aufgaben_zaehlen_nicht(self):
        now = datetime.now(timezone.utc)
        alt = _dienst()._stale_schwelle() + timedelta(minutes=5)
        gezaehlt = asyncio.run(
            _dienst()._stale_task_count(_DB([_Aufgabe("b2", now - alt)]), "a1", now)
        )
        self.assertEqual(gezaehlt, 0)


if __name__ == "__main__":
    unittest.main()
