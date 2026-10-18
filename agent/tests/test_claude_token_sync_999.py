"""Ein Agent mit eigenem Claude-Abo nutzt dessen erneuertes Token (#999).

Vorher las ``get_oauth_token`` immer zuerst die gemeinsame Token-Datei der
Teamlizenz. Das eigene Abo wirkte damit gar nicht, und wo keine Teamlizenz lief,
blieb nur die Abschrift aus der Erstellung, die nach Stunden ablief.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import claude_token_sync as sync
from app import config


class PersonalTokenWins(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.shared = Path(self.tmp.name) / "token.json"
        self.shared.write_text(json.dumps({"access_token": "team-tok"}))
        self.patches = [
            patch.object(config, "SHARED_TOKEN_PATH", str(self.shared)),
            patch.object(config.settings, "claude_code_oauth_token", "abschrift-tok"),
        ]
        for p in self.patches:
            p.start()
        config.set_personal_oauth_token(None)
        config._personal_oauth_cleared = False

    def tearDown(self):
        for p in self.patches:
            p.stop()
        config.set_personal_oauth_token(None)
        config._personal_oauth_cleared = False
        self.tmp.cleanup()

    def test_ohne_markierung_wie_bisher_die_gemeinsame_datei(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_CREDENTIAL_SOURCE", None)
            self.assertEqual(config.get_oauth_token(), "team-tok")

    def test_eigenes_abo_ignoriert_die_teamdatei(self):
        with patch.dict(os.environ, {"CLAUDE_CREDENTIAL_SOURCE": "personal"}):
            # Vor dem ersten Abruf: die Abschrift aus der Erstellung, nie die Teamlizenz.
            self.assertEqual(config.get_oauth_token(), "abschrift-tok")
            sync.apply_response(200, {"source": "personal", "access_token": "frisch-tok"})
            self.assertEqual(config.get_oauth_token(), "frisch-tok")

    def test_fehler_beim_abruf_laesst_letztes_token_stehen(self):
        with patch.dict(os.environ, {"CLAUDE_CREDENTIAL_SOURCE": "personal"}):
            sync.apply_response(200, {"source": "personal", "access_token": "frisch-tok"})
            sync.apply_response(503, None)
            sync.apply_response(500, {"detail": "Interner Fehler"})
            sync.apply_response(200, "kaputt")
            self.assertEqual(config.get_oauth_token(), "frisch-tok")

    def test_abo_entfernt_zurueck_zur_teamlizenz(self):
        with patch.dict(os.environ, {"CLAUDE_CREDENTIAL_SOURCE": "personal"}):
            sync.apply_response(200, {"source": "personal", "access_token": "frisch-tok"})
            sync.apply_response(200, {"source": "team", "access_token": None})
            self.assertEqual(config.get_oauth_token(), "team-tok")


class RetryAsksActively(unittest.IsolatedAsyncioTestCase):
    async def test_401_wiederholung_fragt_im_eigenen_abo_sofort_nach(self):
        """Erneuert ein anderer Agent desselben Besitzers, ist das alte Token
        sofort tot. Die Wiederholung darf nicht bis zum nächsten Takt warten."""
        abrufe = []

        async def fetch(agent_id):
            abrufe.append(agent_id)
            sync.apply_response(200, {"source": "personal", "access_token": "frisch-tok"})

        config.set_personal_oauth_token("alt-tok")
        try:
            with patch.dict(os.environ, {"CLAUDE_CREDENTIAL_SOURCE": "personal"}), \
                    patch.object(sync, "fetch_once", fetch):
                neu = await config.wait_for_new_oauth_token("alt-tok", timeout=1.0, interval=0.01)
        finally:
            config.set_personal_oauth_token(None)
            config._personal_oauth_cleared = False
        self.assertEqual(neu, "frisch-tok")
        self.assertEqual(len(abrufe), 1)


class LoopIsWired(unittest.TestCase):
    def test_main_startet_die_schleife_nur_im_eigenen_abo(self):
        src = (Path(__file__).resolve().parents[1] / "app/main.py").read_text()
        self.assertIn("claude_token_sync.personal_mode()", src)
        self.assertIn("asyncio.create_task(claude_token_sync.sync_loop(agent_id))", src)


if __name__ == "__main__":
    unittest.main()
