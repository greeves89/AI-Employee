"""Bedrock als Custom-LLM: Region, Ziel-Host, Umgebung hinter dem Proxy (#161).

Die Werte hier sind erfundene Platzhalter ohne Schluesselform.
"""

import unittest
from unittest.mock import patch

from app.core import egress
from app.core.realtime_catalog import sprachmodelle_des_kontos


def _cfg(**kw):
    return {"provider_type": "bedrock", "aws_region": "eu-central-1", "api_key": "geheim-wert", **kw}


class RegionUndZielTest(unittest.TestCase):
    def test_region_aus_eintrag(self):
        self.assertEqual(egress.bedrock_region({"aws_region": " EU-West-1 "}), "eu-west-1")

    def test_region_aus_endpunkt_wenn_eintrag_leer(self):
        cfg = {"api_endpoint": "https://bedrock-runtime.us-east-1.amazonaws.com"}
        self.assertEqual(egress.bedrock_region(cfg), "us-east-1")

    def test_ungueltige_region_gibt_leer_nie_einen_standard(self):
        for roh in ("evil.example.com", "eu-central-1/../x", "x", "eu_central_1"):
            self.assertEqual(egress.bedrock_region({"aws_region": roh}), "", roh)

    def test_ziel_kommt_aus_der_region_nie_aus_dem_endpunkt(self):
        cfg = _cfg(api_endpoint="https://evil.example.com")
        self.assertEqual(egress.bedrock_ziel(cfg), "bedrock-runtime.eu-central-1.amazonaws.com")

    def test_endpunkt_auf_fremden_host_ergibt_ohne_region_kein_ziel(self):
        cfg = {"provider_type": "bedrock", "api_endpoint": "https://evil.example.com"}
        self.assertIsNone(egress.bedrock_ziel(cfg))

    def test_iam_hat_kein_ziel(self):
        self.assertIsNone(egress.bedrock_ziel(_cfg(aws_access_key_id="kennung")))

    def test_anderer_anbieter_hat_kein_ziel(self):
        self.assertIsNone(egress.bedrock_ziel({**_cfg(), "provider_type": "openai"}))


class UmgebungTest(unittest.TestCase):
    def test_anderer_anbieter_bekommt_nichts(self):
        self.assertEqual(egress.bedrock_umgebung({"provider_type": "openai"}), {})

    def test_ohne_proxy_geht_iam_in_den_agenten(self):
        with patch.object(egress, "aktiv", return_value=False):
            env = egress.bedrock_umgebung(_cfg(aws_access_key_id="kennung", aws_session_token=" tok "))
        self.assertEqual(env["LLM_AWS_REGION"], "eu-central-1")
        self.assertEqual(env["LLM_AWS_ACCESS_KEY_ID"], "kennung")
        self.assertEqual(env["LLM_AWS_SESSION_TOKEN"], "tok")
        self.assertNotIn("LLM_API_KEY", env)

    def test_mit_proxy_wird_aus_dem_bearer_schluessel_ein_platzhalter(self):
        with patch.object(egress, "aktiv", return_value=True):
            env = egress.bedrock_umgebung(_cfg())
        self.assertNotIn("geheim-wert", env.values())
        # Ueber den Wert gesucht, nicht ueber den Variablennamen: CodeQL haelt alles
        # unter "..._KEY" fuer ein Passwort und meldet sonst die HMAC-Pruefung in
        # platzhalter_aus als Passwort-Hash (py/weak-sensitive-data-hashing).
        gefunden = [n for n, w in env.items() if w.startswith("aie-geheim-")]
        self.assertEqual(gefunden, ["LLM_API_KEY"])
        self.assertEqual(egress.platzhalter_aus(env[gefunden[0]]), (egress.KI, egress.KI_BEDROCK))

    def test_mit_proxy_sperrt_iam_und_laesst_das_secret_draussen(self):
        with patch.object(egress, "aktiv", return_value=True):
            env = egress.bedrock_umgebung(_cfg(aws_access_key_id="kennung"))
        self.assertEqual(env["LLM_API_KEY"], "")
        self.assertEqual(env["LLM_BEDROCK_IAM_GESPERRT"], "true")
        self.assertNotIn("LLM_AWS_ACCESS_KEY_ID", env)

    def test_mit_proxy_ohne_gueltige_region_kein_klartext_und_kein_platzhalter(self):
        with patch.object(egress, "aktiv", return_value=True):
            env = egress.bedrock_umgebung(_cfg(aws_region="unsinn"))
        self.assertEqual(env["LLM_API_KEY"], "")


class SprachmodelleTest(unittest.TestCase):
    MODELLE = [{"id": "amazon.nova-sonic-v1:0"}, {"id": "amazon.nova-pro-v1:0"}]

    def test_andere_anbieter_bleiben_unveraendert(self):
        self.assertIs(sprachmodelle_des_kontos("openai", {}, self.MODELLE), self.MODELLE)

    def test_bearer_konto_gehoert_nicht_ins_sprach_setup(self):
        self.assertIsNone(sprachmodelle_des_kontos("bedrock", {}, self.MODELLE))

    def test_iam_konto_behaelt_nur_sonic(self):
        r = sprachmodelle_des_kontos("bedrock", {"aws_access_key_id": "k"}, self.MODELLE)
        self.assertEqual([m["id"] for m in r], ["amazon.nova-sonic-v1:0"])

    def test_iam_konto_nur_mit_textmodellen_faellt_ganz_raus(self):
        r = sprachmodelle_des_kontos("bedrock", {"aws_access_key_id": "k"}, [self.MODELLE[1]])
        self.assertIsNone(r)

    def test_iam_konto_ohne_eigene_modellliste_nutzt_den_katalog(self):
        self.assertEqual(sprachmodelle_des_kontos("bedrock", {"aws_access_key_id": "k"}, []), [])


if __name__ == "__main__":
    unittest.main()


class FipsTest(unittest.TestCase):
    def test_fips_endpunkt_liefert_die_region_wie_im_agenten(self):
        cfg = {"api_endpoint": "https://bedrock-runtime-fips.us-east-1.amazonaws.com"}
        self.assertEqual(egress.bedrock_region(cfg), "us-east-1")
