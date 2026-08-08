"""MCP-Bruecke: Rueckruf statt Nachfragen, und Aufgaben abbrechbar machen.

Bisher blieb einer Bruecke (SKAI, VoiceBot) nur, ``get_task_status`` im Takt
abzufragen. Bei einem Lauf ueber zwanzig Minuten sind das hunderte Anfragen fuer eine
einzige Antwort. Und eine einmal gestartete Aufgabe liess sich gar nicht mehr stoppen —
sie lief weiter und kostete Token, auch wenn niemand das Ergebnis noch brauchte.

Der heikle Teil ist die Rueckruf-Adresse: sobald ein Aufrufer bestimmt, wohin dieser
Server eine Anfrage schickt, ist das eine serverseitige Anfragefaelschung (SSRF),
wenn es niemand prueft.
"""

import unittest
from pathlib import Path

from app.core import url_guard

REPO = Path(__file__).resolve().parents[2]
ORCH = REPO / "orchestrator"


class OutboundGuardTests(unittest.TestCase):
    def test_https_only(self):
        """Ein Rueckruf ueber HTTP traegt das Ergebnis im Klartext durchs Netz."""
        ok, reason = url_guard.check_outbound_url("http://example.com/hook")
        self.assertFalse(ok)
        self.assertIn("https", reason)

    def test_public_https_is_allowed(self):
        self.assertTrue(url_guard.check_outbound_url("https://example.com/hook")[0])

    def test_loopback_is_blocked(self):
        for url in ("https://127.0.0.1/x", "https://localhost/x", "https://[::1]/x"):
            with self.subTest(url=url):
                self.assertFalse(url_guard.check_outbound_url(url)[0])

    def test_cloud_metadata_is_blocked(self):
        """Das lohnendste Ziel einer SSRF — dort liegen Zugangsdaten."""
        for host in ("169.254.169.254", "metadata.google.internal"):
            with self.subTest(host=host):
                self.assertFalse(url_guard.check_outbound_url(f"https://{host}/")[0])

    def test_unresolvable_host_fails_closed(self):
        """Im Zweifel nicht anfragen — ein Name kann auf 127.0.0.1 zeigen."""
        ok, _ = url_guard.check_outbound_url("https://gibt-es-nicht.invalid/x")
        self.assertFalse(ok)

    def test_garbage_is_rejected(self):
        for url in ("", "nicht mal eine url", "https://", "x" * 3000):
            with self.subTest(url=url[:20]):
                self.assertFalse(url_guard.check_outbound_url(url)[0])


class BridgeToolTests(unittest.TestCase):
    SRC = ORCH / "app/api/mcp_agent.py"

    def test_cancel_task_is_offered(self):
        self.assertIn('"name": "cancel_task"', self.SRC.read_text())

    def test_cancel_is_scoped_to_the_own_agent(self):
        """Sonst koennte eine Bruecke fremde Aufgaben stoppen."""
        block = self.SRC.read_text().split('if name == "cancel_task"')[1]
        self.assertIn("Task.agent_id == agent.id", block)

    def test_cancel_does_not_leak_foreign_task_ids(self):
        """Ein anderer Fehlertext fuer 'fremd' verriete, welche IDs existieren."""
        block = self.SRC.read_text().split('if name == "cancel_task"')[1]
        self.assertIn("Task not found.", block)

    def test_cancel_signals_a_running_task(self):
        """Der Datenbankeintrag allein stoppt keinen laufenden Prozess."""
        block = self.SRC.read_text().split('if name == "cancel_task"')[1]
        self.assertIn("task:cancel", block)

    def test_already_finished_is_not_an_error(self):
        block = self.SRC.read_text().split('if name == "cancel_task"')[1]
        self.assertIn("already", block)

    def test_callback_url_is_checked_before_it_is_stored(self):
        block = self.SRC.read_text().split('if name == "send_task"')[1]
        self.assertIn("is_allowed_callback", block)
        self.assertLess(block.index("is_allowed_callback"), block.index("db.add"))


class CallbackDeliveryTests(unittest.TestCase):
    SRC = ORCH / "app/core/task_router.py"

    def test_callback_is_delivered_on_completion(self):
        self.assertIn("_deliver_task_callback", self.SRC.read_text())

    def test_url_is_rechecked_at_delivery_time(self):
        """Zwischen Anlegen und Fertigwerden koennen Stunden liegen — ein Name kann
        inzwischen woanders hinzeigen."""
        block = self.SRC.read_text().split("async def _deliver_task_callback")[1]
        self.assertIn("check_outbound_url", block)

    def test_a_failed_callback_never_breaks_the_run(self):
        block = self.SRC.read_text().split("async def _deliver_task_callback")[1]
        self.assertIn("except Exception", block)

    def test_result_is_bounded(self):
        block = self.SRC.read_text().split("async def _deliver_task_callback")[1]
        self.assertIn("[:20000]", block)


if __name__ == "__main__":
    unittest.main()
