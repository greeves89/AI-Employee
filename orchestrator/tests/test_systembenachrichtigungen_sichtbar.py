"""Systembenachrichtigungen muessen bei den Administratoren ankommen.

Neun Stellen im Orchestrator legen Benachrichtigungen mit dem Absender
``agent_id="system"`` an: Selbsttest, Sentinel-Ausfall, abgelaufene
OAuth-Tokens, Datenbank-Stoerung der Leerlauf-Ueberwachung und andere.

Seit v1.68.3 (Juni 2026) sieht ein Nutzer nur Benachrichtigungen seiner
eigenen Agenten — richtig, vorher sah jeder alles. Einen Agenten namens
``system`` gibt es aber nicht. Damit sah seitdem NIEMAND eine dieser
Meldungen: auf dem Pi lagen 640 ungelesene, darunter fuenfmal dringend
"Sentinel antwortet nicht mehr".

Systemmeldungen betreffen den Betrieb der Plattform, nicht einen einzelnen
Nutzer. Sie gehen deshalb an die Administratoren — und nur an die.
"""

import unittest

from app.api import notifications as benachrichtigungen
from app.models.user import UserRole


class _Ergebnis:
    def __init__(self, werte): self._werte = werte
    def scalars(self): return self
    def all(self): return list(self._werte)


class _Db:
    """Beantwortet die zwei Abfragen: eigene Agenten, dann geteilte."""

    def __init__(self, eigene, geteilte=()):
        self._antworten = [eigene, geteilte]

    async def execute(self, _anfrage):
        return _Ergebnis(self._antworten.pop(0))


class _Nutzer:
    def __init__(self, rolle):
        self.id = "nutzer-1"
        self.role = rolle


class SichtbarkeitTest(unittest.IsolatedAsyncioTestCase):

    async def test_administrator_sieht_systemmeldungen(self):
        sichtbar = await benachrichtigungen._visible_agent_ids(
            _Nutzer(UserRole.ADMIN), _Db(["agent-a"]))
        self.assertIn(benachrichtigungen.SYSTEM_ABSENDER, sichtbar)

    async def test_administrator_sieht_weiter_seine_agenten(self):
        sichtbar = await benachrichtigungen._visible_agent_ids(
            _Nutzer(UserRole.ADMIN), _Db(["agent-a"], ["agent-b"]))
        self.assertIn("agent-a", sichtbar)
        self.assertIn("agent-b", sichtbar)

    async def test_normaler_nutzer_sieht_keine_systemmeldungen(self):
        """Betriebsinterna gehen nicht an jeden, der ein Konto hat."""
        for rolle in [r for r in UserRole if r != UserRole.ADMIN]:
            with self.subTest(rolle=rolle):
                sichtbar = await benachrichtigungen._visible_agent_ids(
                    _Nutzer(rolle), _Db(["agent-a"]))
                self.assertNotIn(benachrichtigungen.SYSTEM_ABSENDER, sichtbar)
                self.assertIn("agent-a", sichtbar)

    def test_der_absender_ist_der_den_die_dienste_benutzen(self):
        """Stimmt der Name nicht mit dem der Dienste ueberein, ist alles umsonst."""
        self.assertEqual(benachrichtigungen.SYSTEM_ABSENDER, "system")


if __name__ == "__main__":
    unittest.main()
