"""Ein Foundry-Projektendpunkt, so wie Azure ihn zum Kopieren anbietet, funktioniert.

Azure AI Foundry zeigt in der Oberflaeche einen **Projekt**-Endpunkt an
(``https://<res>.services.ai.azure.com/api/projects/<name>``) mit einem
Kopieren-Knopf daneben. Genau den traegt jeder ein — er steht ja da.

Haengt man daran den klassischen Deployment-Pfad, antwortet Azure mit 400: der
Pfad gehoert an die RESSOURCE, nicht ans Projekt. Am 15.08.2026 am lebenden
Dienst nachgemessen — Projektpfad 400, Ressourcen-Wurzel 200.

Statt den Nutzer den Pfad von Hand kuerzen zu lassen, schneidet ihn der Code ab.
"""

import unittest

from app.providers.openai_provider import OpenAIProvider


def _url(endpoint: str, model: str = "gpt-5.6-sol") -> str:
    return OpenAIProvider(
        api_endpoint=endpoint, api_key="x", model_name=model, is_azure=True
    )._resolve_url()[0]


BASIS = "https://res.services.ai.azure.com"


class TheProjectPathIsStrippedTests(unittest.TestCase):
    def test_a_project_endpoint_still_hits_the_resource(self):
        self.assertEqual(
            _url(f"{BASIS}/api/projects/meinprojekt"),
            _url(BASIS),
        )

    def test_the_deployment_path_sits_at_the_resource_root(self):
        self.assertIn("/openai/deployments/gpt-5.6-sol/chat/completions",
                      _url(f"{BASIS}/api/projects/meinprojekt"))

    def test_the_project_segment_is_gone(self):
        self.assertNotIn("/api/projects", _url(f"{BASIS}/api/projects/meinprojekt"))

    def test_a_classic_azure_endpoint_is_unchanged(self):
        """Die bisherige Form darf sich nicht mitaendern."""
        klassisch = "https://meine.openai.azure.com"
        self.assertIn("/openai/deployments/gpt-5.6-sol/chat/completions", _url(klassisch))
        self.assertTrue(_url(klassisch).startswith(klassisch))

    def test_a_trailing_slash_does_not_break_it(self):
        self.assertEqual(_url(f"{BASIS}/api/projects/meinprojekt/"), _url(BASIS))

    def test_the_v1_surface_is_untouched(self):
        """``…/openai/v1`` hat einen eigenen Zweig und darf nicht in den
        Deployment-Pfad umgebogen werden."""
        self.assertIn("/openai/v1/", _url(f"{BASIS}/openai/v1"))


class CodexModelleAufKlassischemAzure(unittest.TestCase):
    """``gpt-5.3-codex`` über ein klassisches Azure-Konto: Codex-Modelle kann
    Azure NUR über die Responses-Schnittstelle bedienen. Der Deployment-Pfad
    (…/chat/completions) antwortete mit 400 „OperationNotSupported — The
    chatCompletion operation does not work with the specified model". Der
    Responses-Weg liegt an der Ressource (``/openai/v1/responses``, Modell =
    Bereitstellungsname im Rumpf, keine api-version nötig)."""

    def _aufloesen(self, endpoint, model):
        return OpenAIProvider(api_endpoint=endpoint, api_key="x", model_name=model,
                              is_azure=True)._resolve_url()

    def test_codex_geht_auf_den_responses_weg(self):
        for ep in ("https://meine.openai.azure.com", f"{BASIS}/api/projects/meinprojekt",
                   "https://meine.cognitiveservices.azure.com/"):
            with self.subTest(ep=ep):
                url, fmt = self._aufloesen(ep, "gpt-5.3-codex")
                self.assertEqual(fmt, "responses")
                self.assertTrue(url.endswith("/openai/v1/responses"), url)
                self.assertNotIn("/api/projects", url)
                self.assertNotIn("/deployments/", url)

    def test_andere_modelle_bleiben_auf_dem_deployment_pfad(self):
        url, fmt = self._aufloesen("https://meine.openai.azure.com", "gpt-4o")
        self.assertEqual(fmt, "chat")
        self.assertIn("/openai/deployments/gpt-4o/chat/completions", url)

    def test_die_azure_meldung_leitet_um(self):
        from app.providers import openai_provider as op

        meldung = ('{"error":{"code":"OperationNotSupported","message":"The chatCompletion '
                   'operation does not work with the specified model, gpt-5.9-neu. Please '
                   'choose different model and try again."}}')
        self.assertTrue(op._verlangt_responses_weg(meldung))
        op._BRAUCHT_RESPONSES.add("gpt-5.9-neu")
        self.addCleanup(op._BRAUCHT_RESPONSES.discard, "gpt-5.9-neu")
        url, fmt = self._aufloesen("https://meine.openai.azure.com", "gpt-5.9-neu")
        self.assertEqual(fmt, "responses")
        self.assertTrue(url.endswith("/openai/v1/responses"))


if __name__ == "__main__":
    unittest.main()
