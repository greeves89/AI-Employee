"""Der kuratierte Codex-Katalog darf kein totes Modell als Standard tragen.

Gemeldet 08.09.2026: DevAgent haengte in Wiederholungsversuchen mit einer
Meldung, die wie ein Verbindungs-/Anmeldeproblem aussah. Ursache: der
Standardwert "gpt-5.5" existiert fuer ChatGPT-Konto-authentifizierten Codex
nicht mehr — am echten Endpunkt geprueft, 400 "not supported when using Codex
with a ChatGPT account". Codex meldet einen ungueltigen Modellnamen als
Verbindungsabbruch, nicht als klaren Konfigurationsfehler; deshalb war eine
erneute Anmeldung wirkungslos, und die Ursache war leicht zu verwechseln.

Dieser Test kann die Gueltigkeit eines Modellnamens nicht pruefen — das geht
nur mit einem echten, ChatGPT-authentifizierten Aufruf, den ein automatischer
Test nicht auf Dauer wiederholen sollte. Er haelt stattdessen fest, WELCHER
Wert zuletzt verifiziert wurde, und dass die beiden bekannt toten Werte nicht
zurueckkehren.
"""

import unittest

from app.core.model_catalog import MODEL_CATALOG


class KeinTotesStandardmodellTests(unittest.TestCase):
    def test_der_codex_standard_ist_nicht_das_bekannt_tote_modell(self):
        standard = MODEL_CATALOG["codex_cli"]["default_model"]
        self.assertNotIn(standard, {"gpt-5.5", "gpt-5.4"},
                         f"{standard!r} ist am 08.09.2026 fuer ChatGPT-Konto-Codex tot")

    def test_der_zuletzt_verifizierte_wert_ist_der_standard(self):
        """Am 08.09.2026 gegen den echten Endpunkt geprueft: nur dieser eine
        Wert wurde angenommen, jede andere Variante (gpt-5.x, gpt-6-sol,
        gpt-6.1, o4-mini) wurde mit 400 abgelehnt."""
        self.assertEqual("gpt-6-astra", MODEL_CATALOG["codex_cli"]["default_model"])

    def test_die_liste_enthaelt_kein_totes_modell(self):
        werte = {m["value"] for m in MODEL_CATALOG["codex_cli"]["providers"]["codex"]}
        self.assertFalse(werte & {"gpt-5.5", "gpt-5.4"})


if __name__ == "__main__":
    unittest.main()
