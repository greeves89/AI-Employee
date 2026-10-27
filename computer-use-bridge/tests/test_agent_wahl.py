"""Welcher Agent mitsieht und auf diesem Rechner arbeitet — in der Bridge waehlbar.

Vorher liess sich das nur ueber die Weboberflaeche einstellen (und eine Weile gar
nicht); nach einem Server-Neustart mit neuer Sitzung war die Zuordnung still weg.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tray_app  # noqa: E402

CFG = {"url": "https://example.invalid", "token": "t", "session": "s1", "agent_id": "a1",
       "allowed_capabilities": ["screenshots", "observe"]}


class Zuordnen(unittest.TestCase):
    def test_ordnet_zu_wenn_anderer_agent(self):
        with patch.object(tray_app, "_api", return_value={"agent_id": None}), \
             patch.object(tray_app, "api_agent_zuordnen") as zu, \
             patch.object(tray_app, "api_update_capabilities") as rechte:
            self.assertTrue(tray_app.agent_zuordnen(dict(CFG)))
        zu.assert_called_once_with("https://example.invalid", "t", "s1", "a1")
        rechte.assert_called_once()   # Mitsehen nach dem Kuerzen wieder anfragen

    def test_nichts_zu_tun_wenn_schon_zugeordnet(self):
        with patch.object(tray_app, "_api", return_value={"agent_id": "a1"}), \
             patch.object(tray_app, "api_agent_zuordnen") as zu:
            self.assertTrue(tray_app.agent_zuordnen(dict(CFG)))
        zu.assert_not_called()

    def test_ohne_wahl_kein_aufruf(self):
        with patch.object(tray_app, "_api") as api:
            self.assertFalse(tray_app.agent_zuordnen({**CFG, "agent_id": ""}))
        api.assert_not_called()

    def test_wahl_wird_gemerkt(self):
        cfg = dict(CFG)
        with patch.object(tray_app, "save_config") as speichern, \
             patch.object(tray_app, "agent_zuordnen", return_value=True) as zu:
            self.assertTrue(tray_app.agent_waehlen(cfg, "a2"))
        self.assertEqual(cfg["agent_id"], "a2")
        speichern.assert_called_once()
        zu.assert_called_once()

    def test_neue_sitzung_bekommt_den_agenten_wieder(self):
        cfg = {**CFG, "session": ""}
        with patch.object(tray_app, "save_config"), \
             patch.object(tray_app, "token_frisch_halten"), \
             patch.object(tray_app, "pick_waiting_session", return_value=""), \
             patch.object(tray_app, "api_create_session", return_value=("s9", [])), \
             patch.object(tray_app, "agent_zuordnen") as zu:
            self.assertEqual(tray_app.ensure_session(cfg), tray_app.ENSURE_OK)
        zu.assert_called_once()
        self.assertEqual(cfg["session"], "s9")


class Anzeige(unittest.TestCase):
    def test_ohne_anmeldung_heisst_der_knopf_anmelden(self):
        i = tray_app.hauptfenster_inhalt({"url": "https://example.invalid"}, ("Nicht verbunden", "gray"), False, False)
        self.assertEqual(i["aktion"], "Anmelden")
        self.assertEqual(i["status"], "Nicht angemeldet")

    def test_doppelte_namen_bleiben_unterscheidbar(self):
        titel = tray_app.agenten_titel([("aaaaaa11", "DevBot"), ("bbbbbb22", "DevBot"), ("cccccc33", "")])
        self.assertEqual(len(set(titel)), 3)
        self.assertEqual(titel[0], "DevBot")


if __name__ == "__main__":
    unittest.main()
