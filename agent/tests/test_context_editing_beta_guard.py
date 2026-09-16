"""Eine abgelehnte Beta darf nicht den ganzen Chat-Weg mitreissen.

Context-Editing (#538) laesst Anthropic serverseitig alte Werkzeug-Ausgaben
wegraeumen. Es ist eine BETA: laeuft sie aus oder kennt ein Modell sie nicht,
antwortet die API mit 400. Wurde der Parameter bedingungslos mitgeschickt, waere
damit JEDE Anfrage ueber diesen Provider tot — wegen einer Bequemlichkeit, die
nur den Verlauf kleiner haelt und fuer die Funktion selbst entbehrlich ist.

Nach der ersten Ablehnung wird sie deshalb dauerhaft weggelassen.
"""

import ast
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.providers import anthropic_provider as ap  # noqa: E402


class DieAblehnungWirdErkanntTests(unittest.TestCase):
    def test_eine_meldung_ueber_context_management_zaehlt(self):
        self.assertTrue(ap._betrifft_context_editing(
            '{"error":{"message":"context_management: unsupported"}}'))

    def test_auch_die_beta_kennung_zaehlt(self):
        self.assertTrue(ap._betrifft_context_editing(
            "unsupported anthropic-beta: context-management-2025-06-27"))

    def test_und_der_strategiename(self):
        self.assertTrue(ap._betrifft_context_editing("clear_tool_uses_20250919 not allowed"))

    def test_ein_gewoehnlicher_eingabefehler_zaehlt_nicht(self):
        """Zu breit gefasst wuerde die Beta bei jedem Tippfehler abschalten."""
        self.assertFalse(ap._betrifft_context_editing(
            '{"error":{"message":"max_tokens: must be greater than 0"}}'))
        self.assertFalse(ap._betrifft_context_editing("model not found"))
        self.assertFalse(ap._betrifft_context_editing(""))
        self.assertFalse(ap._betrifft_context_editing(None))


class _Antwort:
    """Was die Messages-API zurueckgibt — hier ein 400 mit waehlbarem Text."""

    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self._text = text

    async def aread(self) -> bytes:
        return self._text.encode()

    async def aiter_lines(self):
        return
        yield  # pragma: no cover — ein leerer Strom

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Leitung:
    """Ersatz fuer httpx.AsyncClient: merkt sich, was ueber die Leitung ging."""

    is_closed = False

    def __init__(self, antwort: _Antwort):
        self.antwort = antwort
        self.anfragen: list[tuple[dict, dict]] = []

    def stream(self, method, url, json=None, headers=None):
        self.anfragen.append((json, headers))
        return self.antwort


def _fahre(antwort: _Antwort) -> tuple[list, list[tuple[dict, dict]]]:
    """Einen Zug ueber die Attrappe fahren; liefert (Ereignisse, Anfragen)."""
    provider = ap.AnthropicProvider(
        api_endpoint="https://example.invalid", api_key="x",
        model_name="claude-sonnet-4", max_tokens=100,
    )
    leitung = _Leitung(antwort)
    provider._http = leitung

    async def _lauf():
        return [e async for e in provider._stream_completion_impl(
            [ap.ChatMessage(role="user", content="hallo")])]

    return asyncio.run(_lauf()), leitung.anfragen


ABLEHNUNG = '{"type":"error","error":{"type":"invalid_request_error",' \
            '"message":"context_management: unsupported for this model"}}'
ANDERER_FEHLER = '{"type":"error","error":{"type":"invalid_request_error",' \
                 '"message":"max_tokens: must be greater than 0"}}'


class DerParameterHaengtAmMerkzeichenTests(unittest.TestCase):
    """Der Stream wird ueber eine Leitungs-Attrappe gefahren: gemessen wird,
    was WIRKLICH ueber die Leitung geht und was danach im Merkzeichen steht."""

    QUELLE = (Path(__file__).resolve().parents[1]
              / "app" / "providers" / "anthropic_provider.py").read_text()

    def setUp(self):
        self._merkzeichen = ap._CONTEXT_EDITING_AUS
        ap._CONTEXT_EDITING_AUS = False

    def tearDown(self):
        ap._CONTEXT_EDITING_AUS = self._merkzeichen

    def test_er_wird_nur_gesetzt_solange_nichts_abgelehnt_wurde(self):
        _ereignisse, anfragen = _fahre(_Antwort(400, ANDERER_FEHLER))
        (body, headers), = anfragen
        self.assertEqual(body.get("context_management"),
                         {"edits": [{"type": "clear_tool_uses_20250919"}]})
        self.assertEqual(headers.get("anthropic-beta"), "context-management-2025-06-27")

        ap._CONTEXT_EDITING_AUS = True
        _ereignisse, anfragen = _fahre(_Antwort(400, ANDERER_FEHLER))
        (body, headers), = anfragen
        self.assertNotIn("context_management", body)
        self.assertNotIn("anthropic-beta", headers)

    def test_beides_haengt_zusammen(self):
        """Der Beta-Kopf ohne den Parameter waere sinnlos, der Parameter ohne
        den Kopf ein garantierter 400. Gezaehlt werden ZUWEISUNGS-Knoten im
        Syntaxbaum — ein Kommentar oder der gleichnamige Eintrag in der
        Erkennungsliste ist kein Setzort."""
        baum = ast.parse(self.QUELLE)
        zuweisungen = [
            k for k in ast.walk(baum) if isinstance(k, ast.Assign)
            and any(ast.get_source_segment(self.QUELLE, z) == 'headers["anthropic-beta"]'
                    for z in k.targets)
        ]
        self.assertEqual(len(zuweisungen), 1,
                         "Der Beta-Kopf darf nur an EINER Stelle gesetzt werden")
        # ... und nicht zusaetzlich unbedingt als Schluessel im headers-Woerterbuch.
        schluessel = [
            k for k in ast.walk(baum) if isinstance(k, ast.Dict)
            and any(isinstance(s, ast.Constant) and s.value == "anthropic-beta" for s in k.keys)
        ]
        self.assertEqual(schluessel, [],
                         "Der Kopf darf nicht zusaetzlich unbedingt im headers-Wörterbuch stehen")

    def test_die_ablehnung_schaltet_dauerhaft_ab(self):
        ereignisse, _anfragen = _fahre(_Antwort(400, ABLEHNUNG))
        self.assertTrue(ap._CONTEXT_EDITING_AUS)
        self.assertEqual([e.type for e in ereignisse], ["error"])
        self.assertIn("schick die Nachricht einfach nochmal", ereignisse[0].text)

        # Der naechste Zug — auch ueber eine NEUE Instanz — geht ohne die Beta raus.
        _ereignisse, anfragen = _fahre(_Antwort(400, ANDERER_FEHLER))
        (body, headers), = anfragen
        self.assertNotIn("context_management", body)
        self.assertNotIn("anthropic-beta", headers)

    def test_ein_anderer_eingabefehler_schaltet_nicht_ab(self):
        """Sonst wuerde jeder Tippfehler die Beta fuer immer abschalten — und
        der Nutzer bekaeme eine Meldung, die mit seinem Fehler nichts zu tun hat."""
        ereignisse, _anfragen = _fahre(_Antwort(400, ANDERER_FEHLER))
        self.assertFalse(ap._CONTEXT_EDITING_AUS)
        self.assertEqual([e.type for e in ereignisse], ["error"])
        self.assertIn("API error 400", ereignisse[0].text)
        self.assertNotIn("schick die Nachricht einfach nochmal", ereignisse[0].text)

    def test_das_merkzeichen_gilt_ueber_instanzen_hinweg(self):
        """Je Instanz gemerkt, liefe der naechste Provider erneut hinein."""
        self.assertIn("global _CONTEXT_EDITING_AUS", self.QUELLE)
        self.assertTrue(hasattr(ap, "_CONTEXT_EDITING_AUS"))

    def test_der_nutzer_erfaehrt_was_zu_tun_ist(self):
        """Ein roher API-Fehler waere hier eine Sackgasse."""
        ereignisse, _anfragen = _fahre(_Antwort(400, ABLEHNUNG))
        self.assertEqual(ereignisse[0].type, "error")
        self.assertIn("schick die Nachricht einfach nochmal", ereignisse[0].text)
        self.assertNotIn("API error 400", ereignisse[0].text)

    def test_der_grund_wird_im_wortlaut_protokolliert(self):
        """Wer spaeter nachsieht, warum die Beta aus ist, findet die Antwort der
        API im Protokoll — nicht nur „abgelehnt"."""
        with self.assertLogs(ap.logger, level="WARNING") as protokoll:
            _fahre(_Antwort(400, ABLEHNUNG))
        zeile = "\n".join(protokoll.output)
        self.assertIn("Grund im Wortlaut", zeile)
        self.assertIn("context_management: unsupported for this model", zeile)


if __name__ == "__main__":
    unittest.main()
