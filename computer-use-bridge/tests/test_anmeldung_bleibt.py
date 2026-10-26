"""Die Bridge bleibt angemeldet: Erneuerungstoken statt Neuanmeldung alle 30 Minuten.

Bis 1.378.3 speicherte die Bridge nur das Zugangstoken (30 Minuten). Jeder
Neuaufbau danach — Server-Update, WLAN-Wechsel — endete mit „neu anmelden“.
"""

import base64
import json
import sys
import time
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tray_app  # noqa: E402


def _jwt(exp: float) -> str:
    teil = base64.urlsafe_b64encode(json.dumps({"exp": int(exp)}).encode()).decode().rstrip("=")
    return f"kopf.{teil}.signatur"


class _Antwort:
    def __init__(self, cookies):
        self.headers = Message()
        for c in cookies:
            self.headers["Set-Cookie"] = c


def _http_fehler(code):
    return urllib.error.HTTPError("https://example.invalid", code, "x", {}, None)


class CookieUndAblauf(unittest.TestCase):
    def test_erneuerung_aus_set_cookie(self):
        a = _Antwort(["access_token=aaa; HttpOnly; Path=/", "refresh_token=rrr; HttpOnly; Max-Age=604800"])
        self.assertEqual(tray_app._erneuerung_aus_antwort(a), "rrr")
        self.assertEqual(tray_app._erneuerung_aus_antwort(_Antwort([])), "")
        self.assertEqual(tray_app._erneuerung_aus_antwort(object()), "", "Ohne Kopfzeilen kein Absturz")

    def test_ablauf(self):
        self.assertFalse(tray_app._token_laeuft_ab(_jwt(time.time() + 3600)))
        self.assertTrue(tray_app._token_laeuft_ab(_jwt(time.time() + 60)), "fuenf Minuten Puffer")
        self.assertTrue(tray_app._token_laeuft_ab(_jwt(time.time() - 10)))
        self.assertTrue(tray_app._token_laeuft_ab("kein-jwt"))


class Erneuern(unittest.TestCase):
    def setUp(self):
        self.p_speichern = patch.object(tray_app, "save_config")
        self.speichern = self.p_speichern.start()

    def tearDown(self):
        self.p_speichern.stop()

    def test_erfolg_schreibt_beide_tokens(self):
        cfg = {"url": "https://example.invalid", "token": "alt", "refresh": "r1"}
        with patch.object(tray_app, "api_auffrischen", return_value=("neu", "r2")) as auf:
            self.assertTrue(tray_app.token_erneuern(cfg))
        auf.assert_called_once_with("https://example.invalid", "r1")
        self.assertEqual((cfg["token"], cfg["refresh"]), ("neu", "r2"))
        self.speichern.assert_called()

    def test_abgelehnt_loescht_erneuerung(self):
        cfg = {"url": "https://example.invalid", "token": "alt", "refresh": "r1"}
        with patch.object(tray_app, "api_auffrischen", side_effect=_http_fehler(401)):
            self.assertFalse(tray_app.token_erneuern(cfg))
        self.assertEqual(cfg["refresh"], "", "Ein widerrufenes Token wird nicht endlos probiert")

    def test_netz_weg_behaelt_erneuerung(self):
        cfg = {"url": "https://example.invalid", "token": "alt", "refresh": "r1"}
        with patch.object(tray_app, "api_auffrischen", side_effect=OSError("offline")):
            self.assertFalse(tray_app.token_erneuern(cfg))
        self.assertEqual(cfg["refresh"], "r1")

    def test_ohne_erneuerung_nichts(self):
        with patch.object(tray_app, "api_auffrischen") as auf:
            self.assertFalse(tray_app.token_erneuern({"url": "https://example.invalid", "token": "t"}))
        auf.assert_not_called()

    def test_frisch_halten_nur_kurz_vor_ablauf(self):
        with patch.object(tray_app, "token_erneuern") as ern:
            tray_app.token_frisch_halten({"token": _jwt(time.time() + 3600), "refresh": "r"})
            ern.assert_not_called()
            tray_app.token_frisch_halten({"token": _jwt(time.time() + 30), "refresh": "r"})
            ern.assert_called_once()


class Sitzung(unittest.TestCase):
    def test_401_erneuert_statt_neu_anmelden(self):
        cfg = {"url": "https://example.invalid", "token": _jwt(time.time() + 3600), "refresh": "r1",
               "session": "s1"}
        aufrufe = []

        def waehlen(url, token):
            aufrufe.append(token)
            if len(aufrufe) == 1:
                raise _http_fehler(401)
            return "s1"

        with patch.object(tray_app, "save_config"), \
             patch.object(tray_app, "pick_waiting_session", side_effect=waehlen), \
             patch.object(tray_app, "api_session_exists", return_value=True), \
             patch.object(tray_app, "api_update_capabilities"), \
             patch.object(tray_app, "api_auffrischen", return_value=(_jwt(time.time() + 1800), "r2")):
            self.assertEqual(tray_app.ensure_session(cfg), tray_app.ENSURE_OK)
        self.assertEqual(cfg["refresh"], "r2")
        self.assertEqual(cfg["session"], "s1", "Die Sitzung bleibt erhalten")

    def test_ohne_erneuerung_bleibt_es_bei_neu_anmelden(self):
        cfg = {"url": "https://example.invalid", "token": _jwt(time.time() + 3600), "session": "s1"}
        with patch.object(tray_app, "save_config"), \
             patch.object(tray_app, "pick_waiting_session", side_effect=_http_fehler(401)):
            self.assertEqual(tray_app.ensure_session(cfg), tray_app.ENSURE_NEEDS_LOGIN)

    def test_anmeldung_merkt_sich_die_erneuerung(self):
        cfg = {}
        with patch.object(tray_app, "api_login_tokens", return_value=("zugang", "r1")), \
             patch.object(tray_app, "api_create_session", return_value=("s9", [])):
            token, sid = tray_app.login_and_prepare("https://example.invalid", "a@example.invalid", "pw",
                                                    ["screenshots"], None, cfg)
        self.assertEqual((token, sid, cfg["refresh"]), ("zugang", "s9", "r1"))


if __name__ == "__main__":
    unittest.main()
