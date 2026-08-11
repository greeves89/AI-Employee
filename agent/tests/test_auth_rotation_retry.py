"""Eine Token-Erneuerung darf keinen Lauf kosten.

Der Anlass steht im Betrieb, auf die Minute nachvollziehbar:

    10:51  Agenten-Zug laeuft → 401 „access token has been revoked"
    10:52  Plattform erneuert den OAuth-Token
    10:53  neuer Token liegt im gemeinsamen Verzeichnis

Anthropic **rotiert**: sobald der neue Zugangstoken ausgestellt ist, ist der alte
tot. Faellt das in einen laufenden Zug, stirbt er — ohne dass jemand etwas falsch
gemacht hat. Der Nutzer sieht rot „Failed to authenticate".

Der Chat-Pfad hatte dafuer schon eine Wiederholung, aber sie wartete **pauschal
zehn Sekunden**. Die Plattform schreibt den neuen Token erst im naechsten
30-Sekunden-Takt, und wenn sie dafuer erst bei Anthropic anfragen muss, dauert es
laenger. Der Versuch lief also verlaesslich ins Leere.

Der Aufgaben-Pfad hatte **gar keine** Wiederholung — und dort waere sie noetiger:
eine Aufgabe laeuft unbeaufsichtigt, es sitzt niemand davor, der es noch einmal
versucht.
"""

import asyncio
import unittest
from unittest.mock import patch


class WaitForNewTokenTests(unittest.IsolatedAsyncioTestCase):
    """Gewartet wird auf den TOKENWECHSEL, nicht auf die Uhr."""

    async def test_it_returns_as_soon_as_the_token_changes(self):
        from app.config import wait_for_new_oauth_token

        seen = {"n": 0}

        def fake_token():
            seen["n"] += 1
            return "alt" if seen["n"] < 3 else "neu"

        with patch("app.config.get_oauth_token", fake_token):
            got = await wait_for_new_oauth_token("alt", timeout=5, interval=0.01)
        self.assertEqual(got, "neu")

    async def test_it_gives_up_instead_of_hanging(self):
        """Kein Warten ohne Ende: irgendwann wird trotzdem versucht."""
        from app.config import wait_for_new_oauth_token

        with patch("app.config.get_oauth_token", lambda: "alt"):
            got = await wait_for_new_oauth_token("alt", timeout=0.05, interval=0.01)
        self.assertIsNone(got)

    async def test_an_empty_token_does_not_count_as_a_change(self):
        """Waehrend des Schreibens kann die Datei kurz leer sein — das ist kein
        neuer Token, sondern ein Zwischenzustand."""
        from app.config import wait_for_new_oauth_token

        with patch("app.config.get_oauth_token", lambda: ""):
            got = await wait_for_new_oauth_token("alt", timeout=0.05, interval=0.01)
        self.assertIsNone(got)

    async def test_it_does_not_busy_wait(self):
        """Der Abstand zwischen zwei Blicken wird eingehalten — sonst wird aus dem
        Warten eine Schleife, die eine CPU frisst."""
        from app.config import wait_for_new_oauth_token

        calls = []

        async def fake_sleep(d):
            calls.append(d)
            if len(calls) > 3:
                raise asyncio.CancelledError

        with patch("app.config.get_oauth_token", lambda: "alt"), \
             patch("asyncio.sleep", fake_sleep):
            with self.assertRaises(asyncio.CancelledError):
                await wait_for_new_oauth_token("alt", timeout=99, interval=2.0)
        self.assertTrue(all(d == 2.0 for d in calls), calls)


class TaskPathParityTests(unittest.TestCase):
    """Der Aufgaben-Pfad muss dieselbe Erholung haben wie der Chat.

    Quelltext-Test, weil beide Pfade eine CLI starten: hier zaehlt, dass die
    Faehigkeit in BEIDEN steht und nicht wieder auseinanderlaeuft.
    """

    @staticmethod
    def _src(name):
        from pathlib import Path
        return (Path(__file__).resolve().parents[1] / "app" / name).read_text()

    def test_the_chat_path_waits_for_the_new_token(self):
        src = self._src("chat_handler.py")
        self.assertIn("wait_for_new_oauth_token", src)
        self.assertNotIn("await asyncio.sleep(10)  # Wait for token sync", src,
                         "Pauschales Warten war genau der Fehler")

    def test_the_task_path_retries_at_all(self):
        src = self._src("agent_runner.py")
        self.assertIn("wait_for_new_oauth_token", src)
        self.assertIn("_execute_task_once", src)

    def test_both_recognise_a_revoked_token(self):
        """„revoked" stand in keiner der beiden Listen — ausgerechnet das Wort,
        das im Betrieb kam."""
        for name in ("chat_handler.py", "agent_runner.py"):
            with self.subTest(name):
                self.assertIn("revoked", self._src(name))

    def test_the_retry_is_bounded(self):
        """Einmal wiederholen, nicht endlos: ein dauerhaft kaputter Zugang muss
        sichtbar werden, statt im Kreis zu laufen."""
        src = self._src("agent_runner.py")
        block = src.split("async def execute_task")[1].split("async def _execute_task_once")[0]
        self.assertEqual(block.count("_execute_task_once("), 2)


class RecreateOrderTests(unittest.TestCase):
    """Beim Neuerstellen zuerst den Token erneuern, dann den Container starten."""

    def test_update_agent_refreshes_before_it_recreates(self):
        from pathlib import Path

        src = (Path(__file__).resolve().parents[2]
               / "orchestrator/app/core/agent_manager.py").read_text()
        block = src.split("async def update_agent")[1][:3000]
        refresh = block.find("refresh_access_token")
        stop = block.find("stop_container")
        self.assertGreater(refresh, -1, "Kein Token-Refresh vor dem Neuerstellen")
        self.assertLess(refresh, stop,
                        "Der Refresh muss VOR dem Stoppen des alten Containers stehen")


if __name__ == "__main__":
    unittest.main()
