"""Das Agenten-Startskript darf nie am CLI-Update haengenbleiben.

Am 21.09.2026 stand ein Agent still: Der Container war "Up", aber
``python -m app.main`` lief nie an, Port 8080 antwortete nicht, die
Gesundheitspruefung schlug fehl und der Agent reagierte im Chat nicht mehr.
Im Prozessbaum hing das Startskript in ``npm install -g @openai/codex@latest``
-- seit drei Minuten, obwohl ein ``timeout 60`` davorstand.

Zwei Ursachen, beide hier festgehalten:

1. **Node bevorzugt IPv6.** Loest die Registry nur zu IPv6-Adressen auf und
   fehlt die IPv6-Route, laeuft npm nicht in einen Fehler, sondern in einen
   Haenger. Im betroffenen Container gemessen: IPv6 scheitert, IPv4 antwortet
   in 0,6 s. Gegenmittel: ``--dns-result-order=ipv4first``.

2. **Der Deckel hielt nicht.** ``timeout`` schickt nur SIGTERM, und npm stirbt
   daran nicht. Nachgemessen an einem Prozess, der SIGTERM ignoriert: mit
   schlichtem ``timeout 5`` dauerte es 61 Sekunden, mit ``timeout -k 3 5``
   acht. Gegenmittel: ``-k``, damit SIGKILL folgt.

Der zweite Test prueft das Verhalten wirklich nach -- er laesst einen
stoerrischen Prozess laufen und misst. Der erste haelt fest, dass das
Startskript die beiden Gegenmittel auch benutzt; ein Verhaltenstest des
ganzen Skripts wuerde eine echte Registry brauchen.
"""

import os
import subprocess
import time
import unittest

_ENTRYPOINT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "entrypoint.sh"
)


class StartskriptTest(unittest.TestCase):
    def setUp(self):
        with open(_ENTRYPOINT) as f:
            self.skript = f.read()

    def test_npm_laeuft_nicht_in_einen_ipv6_haenger(self):
        self.assertIn(
            "--dns-result-order=ipv4first", self.skript,
            "Ohne IPv4-Vorrang haengt npm in Containern ohne IPv6-Route, "
            "statt mit einem Fehler zurueckzukommen.",
        )

    def test_der_deckel_eskaliert_zu_sigkill(self):
        self.assertRegex(
            self.skript, r"timeout\s+-k\s+\d+\s+\d+\s+npm",
            "timeout ohne -k schickt nur SIGTERM; npm ueberlebt das und der "
            "Agent startet nie.",
        )

    def test_bash_syntax(self):
        r = subprocess.run(["bash", "-n", _ENTRYPOINT], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


class DeckelVerhaltenTest(unittest.TestCase):
    """Warum ``-k`` noetig ist -- nachgemessen statt behauptet."""

    #: Ein Prozess, der SIGTERM abfaengt und einfach weiterlaeuft.
    STOERRISCH = [
        "python3", "-c",
        "import signal, time; signal.signal(signal.SIGTERM, lambda *a: None); time.sleep(30)",
    ]

    def _dauer(self, befehl) -> float:
        start = time.monotonic()
        subprocess.run(befehl, capture_output=True)
        return time.monotonic() - start

    @unittest.skipUnless(
        subprocess.run(["which", "timeout"], capture_output=True).returncode == 0,
        "timeout nicht verfuegbar (macOS ohne coreutils)",
    )
    def test_ohne_k_haelt_der_deckel_nicht(self):
        dauer = self._dauer(["timeout", "2", *self.STOERRISCH])
        self.assertGreater(
            dauer, 5,
            "Erwartet war, dass der Prozess SIGTERM ueberlebt und weiterlaeuft. "
            "Tut er das nicht, ist die Annahme dieses Tests hinfaellig.",
        )

    @unittest.skipUnless(
        subprocess.run(["which", "timeout"], capture_output=True).returncode == 0,
        "timeout nicht verfuegbar (macOS ohne coreutils)",
    )
    def test_mit_k_greift_der_deckel(self):
        dauer = self._dauer(["timeout", "-k", "1", "2", *self.STOERRISCH])
        self.assertLess(
            dauer, 8,
            "Mit -k muss nach der Frist SIGKILL folgen und der Aufruf enden.",
        )


if __name__ == "__main__":
    unittest.main()
