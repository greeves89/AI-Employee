"""Die eigene OAuth-Rueckkehr-Adresse muss in der Oberflaeche erreichbar sein.

Der Server-Teil (Spalte, Validierung, PATCH-Semantik, DCR-Reset) kam mit #665,
aber ohne Formularfeld: die Einstellung existierte und war ausschliesslich per
API zu setzen. Ein Administrator haette sie in der Oberflaeche nicht gefunden —
und genau deshalb weiter die globale Adresse umgebogen, was Anmeldung und
Kalender-Anbindung mitverstellt (der Fehler, den #665 abstellen wollte).
"""

import unittest
from pathlib import Path

_WURZEL = Path(__file__).resolve().parents[2]
_SEITE = (_WURZEL / "frontend" / "src" / "app" / "integrations" / "page.tsx").read_text()
_API_TS = (_WURZEL / "frontend" / "src" / "lib" / "api.ts").read_text()
_API_PY = (_WURZEL / "orchestrator" / "app" / "api" / "mcp_servers.py").read_text()


class DasFeldIstBedienbarTests(unittest.TestCase):
    def test_es_gibt_einen_zustand_dafuer(self):
        self.assertIn("const [addCallbackBase, setAddCallbackBase]", _SEITE)

    def test_der_gespeicherte_wert_wird_vorbefuellt(self):
        """Ohne das steht beim Bearbeiten ein leeres Feld ueber einem gesetzten
        Wert — und Speichern wuerde ihn stillschweigend loeschen."""
        self.assertIn('setAddCallbackBase(server.oauth_callback_base_url || "")', _SEITE)

    def test_es_gibt_ein_eingabefeld_mit_beschriftung(self):
        self.assertIn("Eigene OAuth-Rückkehr-Adresse", _SEITE)
        self.assertIn("value={addCallbackBase}", _SEITE)

    def test_der_wert_wird_beim_speichern_mitgeschickt(self):
        self.assertIn("data.oauth_callback_base_url = addCallbackBase.trim()", _SEITE)

    def test_unveraendert_bleibt_unveraendert(self):
        """PATCH-Semantik: nur senden, wenn sich etwas geaendert hat — sonst
        wuerde jedes Speichern die Anbieter-Registrierung verwerfen."""
        self.assertIn("addCallbackBase.trim() !== basisVorher", _SEITE)

    def test_der_typ_kennt_das_feld(self):
        self.assertIn("oauth_callback_base_url?: string | null;", _API_TS)


class DerNutzerWirdVorDerFolgeGewarntTests(unittest.TestCase):
    def test_der_verlust_der_registrierung_wird_angesagt(self):
        """Der Server verwirft beim Wechsel die automatische Registrierung —
        wer das nicht weiss, haelt den naechsten Fehlschlag fuer einen Bug."""
        self.assertIn("Registrierung verworfen", _SEITE)

    def test_und_nur_wenn_es_ueberhaupt_eine_gibt(self):
        self.assertIn("editingServer?.oauth_client_id && (", _SEITE)

    def test_der_server_verwirft_sie_wirklich(self):
        """Der Hinweis darf nichts behaupten, was der Server nicht tut."""
        block = _API_PY.split("if body.oauth_callback_base_url is not None:", 1)[1][:600]
        self.assertIn("server.oauth_client_id = None", block)
        self.assertIn("server.oauth_client_secret_encrypted = None", block)


if __name__ == "__main__":
    unittest.main()
