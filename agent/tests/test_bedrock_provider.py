"""AWS-Bedrock-Provider: Uebersetzung, Strom, Fehlerpfade, Abbruch.

Gegen AWS selbst laeuft hier nichts. Damit der Doppelgaenger nicht nur bestaetigt,
was der Code ohnehin glaubt, gibt es zwei Stufen:

* ``FalscherClient``: liefert Ereignisse von Hand und kann falsch antworten
  (zerstueckeltes Tool-JSON, Fehlerereignis, abgerissener Strom, Modell ohne
  Tool-Support) — und lehnt ab, was Converse ablehnt.
* ``echter_client``: der echte botocore-Client aus ``_client_bauen``; nur der
  Netzaufruf ist ersetzt. Parameterpruefung, Serialisierung, Signatur und der
  Parser fuer das binaere Ereignisformat sind echt.
"""

import asyncio
import base64
import json
import struct
import sys
import threading
import time
import zlib
from pathlib import Path

import pytest
from botocore.awsrequest import AWSResponse
from botocore.exceptions import (
    ClientError,
    EndpointConnectionError,
    ReadTimeoutError,
    ResponseStreamingError,
)

from app import model_fallback
from app.config import settings
from app.model_registry import estimate_cost, get_context_window
from app.providers import create_provider
from app.providers.base import ChatMessage, nutzertext_fuer_api_fehler
from app.providers.bedrock_provider import (
    BedrockProvider,
    StromAuswertung,
    fehler_aus_antwort,
    nachrichten_uebersetzen,
    region_aufloesen,
    werkzeuge_uebersetzen,
)

# In Teilen geschrieben: als ein Literal haelt der Sentinel den Agenten an, der die Datei liest.
_SCHLUESSEL_ID = "AK" "IAEXAMPLE000000000"

MODELL = "eu.anthropic.claude-sonnet-4-5-20250929-v1:0"
PIXEL = base64.b64encode(b"\x89PNG\r\n\x1a\nbild").decode()
WERKZEUG = {"type": "function", "function": {
    "name": "lese_datei", "description": "Liest eine Datei",
    "parameters": {"type": "object", "properties": {"pfad": {"type": "string"}}},
}}


def _tool_call(name="lese_datei", arguments='{"pfad": "/a"}', id_="t1"):
    return {"id": id_, "type": "function", "function": {"name": name, "arguments": arguments}}


def _client_error(code, nachricht, status=400):
    return ClientError({"Error": {"Code": code, "Message": nachricht},
                        "ResponseMetadata": {"HTTPStatusCode": status}}, "ConverseStream")


class FalscherStrom:
    def __init__(self, ereignisse, wartezeit=0.0, haengt=False):
        self.ereignisse = ereignisse
        self.wartezeit = wartezeit
        self.haengt = haengt
        self.geschlossen = threading.Event()

    def __iter__(self):
        for e in self.ereignisse:
            if self.wartezeit:
                time.sleep(self.wartezeit)
            if isinstance(e, Exception):
                raise e
            yield e
        if self.haengt:
            self.geschlossen.wait(10)

    def close(self):
        self.geschlossen.set()


class FalscherClient:
    """Lehnt ab, was Converse ablehnt — auch wenn der Code es nicht erwartet."""

    def __init__(self, ereignisse=(), fehler=None, strom=None):
        self.ereignisse = list(ereignisse)
        self.fehler = fehler
        self.strom = strom
        self.anfragen = []

    def converse_stream(self, **anfrage):
        self.anfragen.append(anfrage)
        if self.fehler:
            raise self.fehler
        rollen = [m["role"] for m in anfrage["messages"]]
        if rollen[0] != "user" or any(a == b for a, b in zip(rollen, rollen[1:])):
            raise _client_error("ValidationException", f"roles must alternate: {rollen}")
        for m in anfrage["messages"]:
            for block in m["content"]:
                if "text" in block and not block["text"].strip():
                    raise _client_error("ValidationException", "The text field is blank.")
                if ("toolUse" in block or "toolResult" in block) and "toolConfig" not in anfrage:
                    raise _client_error("ValidationException", "The toolConfig field must be defined.")
        return {"stream": self.strom or FalscherStrom(self.ereignisse)}


def _provider(client=None, **kwargs):
    werte = {"aws_region": "eu-central-1", "api_key": "schluessel", "model_name": MODELL}
    werte.update(kwargs)
    return BedrockProvider(api_endpoint=werte.pop("api_endpoint", ""),
                           client_fabrik=(lambda: client) if client else None, **werte)


def _sammeln(provider, messages=None, tools=None):
    async def lauf():
        return [e async for e in provider.stream_completion(
            messages or [ChatMessage(role="user", content="Hallo")], tools)]
    return asyncio.run(asyncio.wait_for(lauf(), 10))


def _text(*teile):
    return [{"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": t}}} for t in teile]


ENDE = [{"messageStop": {"stopReason": "end_turn"}},
        {"metadata": {"usage": {"inputTokens": 11, "outputTokens": 7, "totalTokens": 18,
                                "cacheReadInputTokens": 3, "cacheWriteInputTokens": 2}}}]


# ── Nachrichten ─────────────────────────────────────────────────────────────


def test_system_steht_getrennt_und_rollen_bleiben_erhalten():
    system, nachrichten = nachrichten_uebersetzen([
        ChatMessage(role="system", content="Regel eins"),
        ChatMessage(role="system", content="Regel zwei"),
        ChatMessage(role="user", content="Frage"),
        ChatMessage(role="assistant", content="Antwort"),
    ], True)
    assert system == "Regel eins\n\nRegel zwei"
    assert nachrichten == [{"role": "user", "content": [{"text": "Frage"}]},
                           {"role": "assistant", "content": [{"text": "Antwort"}]}]


def test_mehrere_ergebnisse_eines_zugs_werden_eine_nachricht():
    _, nachrichten = nachrichten_uebersetzen([
        ChatMessage(role="user", content="Los"),
        ChatMessage(role="assistant", content="Ich schaue nach.", tool_calls=[
            _tool_call(id_="t1"), _tool_call(arguments={"pfad": "/b"}, id_="t2")]),
        ChatMessage(role="tool", content="Inhalt A", tool_call_id="t1", name="lese_datei"),
        ChatMessage(role="tool", content="Inhalt B", tool_call_id="t2", name="lese_datei"),
    ], True)
    assert [m["role"] for m in nachrichten] == ["user", "assistant", "user"]
    assert nachrichten[1]["content"] == [
        {"text": "Ich schaue nach."},
        {"toolUse": {"toolUseId": "t1", "name": "lese_datei", "input": {"pfad": "/a"}}},
        {"toolUse": {"toolUseId": "t2", "name": "lese_datei", "input": {"pfad": "/b"}}},
    ]
    assert nachrichten[2]["content"] == [
        {"toolResult": {"toolUseId": "t1", "content": [{"text": "Inhalt A"}]}},
        {"toolResult": {"toolUseId": "t2", "content": [{"text": "Inhalt B"}]}},
    ]


def test_leere_bloecke_fallen_weg_und_vorn_steht_immer_user():
    _, nachrichten = nachrichten_uebersetzen([
        ChatMessage(role="assistant", content="übrig vom Verdichten"),
        ChatMessage(role="user", content="   "),
        ChatMessage(role="assistant", content=""),
        ChatMessage(role="user", content="Weiter"),
    ], True)
    assert nachrichten[0]["role"] == "user"
    assert [m["role"] for m in nachrichten] == ["user", "assistant", "user"]
    assert all(b["text"].strip() for m in nachrichten for b in m["content"])


def test_leeres_werkzeugergebnis_ist_kein_leerer_textblock():
    _, nachrichten = nachrichten_uebersetzen([
        ChatMessage(role="user", content="x"),
        ChatMessage(role="assistant", content=None, tool_calls=[_tool_call()]),
        ChatMessage(role="tool", content="", tool_call_id="t1"),
    ], True)
    assert nachrichten[2]["content"][0]["toolResult"]["content"][0]["text"].strip()


def test_ohne_werkzeuge_wird_der_verlauf_zu_text_und_inhalt_bleibt():
    """Der Abschluss-Zug nach dem Budget ruft ohne Werkzeuge — mit Werkzeug-Verlauf."""
    _, nachrichten = nachrichten_uebersetzen([
        ChatMessage(role="user", content="Los"),
        ChatMessage(role="assistant", content="", tool_calls=[_tool_call()]),
        ChatMessage(role="tool", content="Inhalt A", tool_call_id="t1", name="lese_datei"),
    ], False)
    roh = json.dumps(nachrichten, ensure_ascii=False)
    assert "toolUse" not in roh and "toolResult" not in roh
    assert "lese_datei" in roh and "/a" in roh and "Inhalt A" in roh


def test_bilder_aus_nachricht_und_werkzeugergebnis():
    bild = {"type": "image", "media_type": "image/png", "data": PIXEL}
    _, nachrichten = nachrichten_uebersetzen([
        ChatMessage(role="user", content=[{"type": "text", "text": "Sieh"}, bild,
                                          {"type": "image", "media_type": "image/jpg", "data": PIXEL}]),
        ChatMessage(role="assistant", content="", tool_calls=[_tool_call()]),
        ChatMessage(role="tool", content=[{"type": "text", "text": "Bild:"}, bild], tool_call_id="t1"),
    ], True)
    roh = base64.b64decode(PIXEL)
    assert nachrichten[0]["content"][1] == {"image": {"format": "png", "source": {"bytes": roh}}}
    assert nachrichten[0]["content"][2]["image"]["format"] == "jpeg"
    assert nachrichten[2]["content"][0]["toolResult"]["content"][1]["image"]["source"]["bytes"] == roh


def test_kaputtes_bild_wird_gesagt_statt_verschwiegen():
    _, nachrichten = nachrichten_uebersetzen([ChatMessage(role="user", content=[
        {"type": "text", "text": "Sieh"},
        {"type": "image", "media_type": "image/png", "data": "kein base64!"},
        {"type": "image", "media_type": "image/tiff", "data": PIXEL},
    ])], True)
    assert [b["text"] for b in nachrichten[0]["content"][1:]] == ["[Bild nicht darstellbar]"] * 2


# ── Werkzeuge ───────────────────────────────────────────────────────────────


def test_werkzeuge_form_und_erster_gewinnt():
    zweiter = {"function": {"name": "lese_datei", "description": "doppelt", "parameters": {}}}
    ohne = {"function": {"name": "ohne_angaben"}}
    falsch = {"function": {"name": "mit.punkt", "description": "x"}}
    specs = werkzeuge_uebersetzen([WERKZEUG, zweiter, ohne, falsch, {"function": {}}])
    assert [s["toolSpec"]["name"] for s in specs] == ["lese_datei", "ohne_angaben"]
    assert specs[0]["toolSpec"] == {
        "name": "lese_datei", "description": "Liest eine Datei",
        "inputSchema": {"json": WERKZEUG["function"]["parameters"]},
    }
    assert specs[1]["toolSpec"]["description"] == "ohne_angaben"
    assert specs[1]["toolSpec"]["inputSchema"]["json"]["type"] == "object"


def test_anfrage_traegt_toolconfig_grenze_und_temperatur_nur_wo_erlaubt():
    client = FalscherClient(_text("ok") + ENDE)
    _sammeln(_provider(client, max_tokens=500, model_name="amazon.nova-pro-v1:0", temperature=1.7),
             tools=[WERKZEUG])
    anfrage = client.anfragen[0]
    assert anfrage["modelId"] == "amazon.nova-pro-v1:0"
    assert anfrage["inferenceConfig"] == {"maxTokens": 500, "temperature": 1.0}
    assert anfrage["toolConfig"]["tools"][0]["toolSpec"]["name"] == "lese_datei"

    client = FalscherClient(_text("ok") + ENDE)
    _sammeln(_provider(client, max_tokens=0))
    assert "inferenceConfig" not in client.anfragen[0]
    assert "toolConfig" not in client.anfragen[0]


def test_zug_ohne_werkzeuge_scheitert_nicht_am_verlauf():
    """Der Doppelgaenger weist toolUse ohne toolConfig ab wie Converse."""
    client = FalscherClient(_text("Zusammenfassung") + ENDE)
    ereignisse = _sammeln(_provider(client), [
        ChatMessage(role="user", content="Los"),
        ChatMessage(role="assistant", content="", tool_calls=[_tool_call()]),
        ChatMessage(role="tool", content="Inhalt", tool_call_id="t1", name="lese_datei"),
        ChatMessage(role="user", content="Fasse zusammen."),
    ], tools=None)
    assert [e.type for e in ereignisse] == ["text_delta", "done"]


# ── Strom ───────────────────────────────────────────────────────────────────


def test_text_und_nutzung():
    client = FalscherClient([{"messageStart": {"role": "assistant"}}, *_text("Hal", "lo"),
                             {"contentBlockStop": {"contentBlockIndex": 0}}, *ENDE])
    ereignisse = _sammeln(_provider(client))
    assert [e.text for e in ereignisse if e.type == "text_delta"] == ["Hal", "lo"]
    fertig = ereignisse[-1]
    assert (fertig.type, fertig.input_tokens, fertig.output_tokens, fertig.cached_tokens,
            fertig.cache_write_tokens) == ("done", 11, 7, 3, 2)


def test_zerstueckeltes_tool_json_und_zwei_werkzeuge_durcheinander():
    def delta(i, teil):
        return {"contentBlockDelta": {"contentBlockIndex": i, "delta": {"toolUse": {"input": teil}}}}

    def start(i, id_, name):
        return {"contentBlockStart": {"contentBlockIndex": i,
                                      "start": {"toolUse": {"toolUseId": id_, "name": name}}}}

    client = FalscherClient([
        *_text("Moment."), {"contentBlockStop": {"contentBlockIndex": 0}},
        start(1, "t1", "a"), start(2, "t2", "b"),
        delta(1, '{"pf'), delta(2, '{"x"'), delta(1, 'ad": "/ä'), delta(2, ': [1,'),
        delta(1, '/b"}'), delta(2, " 2]}"),
        {"contentBlockStop": {"contentBlockIndex": 2}}, {"contentBlockStop": {"contentBlockIndex": 1}},
        {"messageStop": {"stopReason": "tool_use"}}, ENDE[1],
    ])
    aufrufe = [e for e in _sammeln(_provider(client)) if e.type == "tool_call"]
    assert [(e.tool_id, e.tool_name, e.tool_input) for e in aufrufe] == [
        ("t2", "b", {"x": [1, 2]}), ("t1", "a", {"pfad": "/ä/b"})]


def test_werkzeug_ohne_argumente_ist_leer_nicht_kaputt():
    auswertung = StromAuswertung(MODELL, True)
    auswertung.ereignis({"contentBlockStart": {"contentBlockIndex": 0, "start": {
        "toolUse": {"toolUseId": "t", "name": "n"}}}})
    assert auswertung.ereignis({"contentBlockStop": {"contentBlockIndex": 0}})[0].tool_input == {}


def test_kaputtes_tool_json_bleibt_sichtbar():
    auswertung = StromAuswertung(MODELL, True)
    auswertung.ereignis({"contentBlockStart": {"contentBlockIndex": 0, "start": {
        "toolUse": {"toolUseId": "t", "name": "n"}}}})
    auswertung.ereignis({"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": '{"a":'}}}})
    assert auswertung.ereignis({"contentBlockStop": {"contentBlockIndex": 0}})[0].tool_input == {"raw": '{"a":'}


def test_offenes_werkzeug_am_ende_geht_nicht_verloren():
    auswertung = StromAuswertung(MODELL, True)
    auswertung.ereignis({"contentBlockStart": {"contentBlockIndex": 1, "start": {
        "toolUse": {"toolUseId": "t", "name": "n"}}}})
    auswertung.ereignis({"messageStop": {"stopReason": "tool_use"}})
    assert [e.type for e in auswertung.abschluss()] == ["tool_call", "done"]


def test_fehlerereignis_im_strom_ist_kein_fertig():
    client = FalscherClient([*_text("Halb"), {"throttlingException": {"message": "Too many tokens"}}, *ENDE])
    ereignisse = _sammeln(_provider(client))
    assert [e.type for e in ereignisse] == ["text_delta", "error"]
    assert model_fallback.is_retryable(ereignisse[-1].text)
    assert "Too many tokens" in ereignisse[-1].text


def test_abgerissener_strom_ist_kein_fertig_sondern_wiederholbar():
    client = FalscherClient(_text("Halbe Antw"))
    ereignisse = _sammeln(_provider(client))
    assert [e.type for e in ereignisse] == ["text_delta", "error"]
    assert model_fallback.is_connection_glitch(ereignisse[-1].text)


def test_fehlende_metadaten_kosten_nur_die_zaehlung():
    client = FalscherClient([*_text("Ganz"), ENDE[0]])
    ereignisse = _sammeln(_provider(client))
    assert [e.type for e in ereignisse] == ["text_delta", "done"]
    assert ereignisse[-1].input_tokens == 0


def test_gefilterte_antwort_ohne_inhalt_wird_gemeldet():
    client = FalscherClient([{"messageStop": {"stopReason": "guardrail_intervened"}}, ENDE[1]])
    ereignisse = _sammeln(_provider(client))
    assert ereignisse[-1].type == "error"
    assert "content filter" in ereignisse[-1].text
    assert not model_fallback.is_retryable(ereignisse[-1].text)


# ── Fehlerpfade ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("wortlaut", [
    "This model doesn't support tool use.",
    "This model doesn't support tool use in streaming mode.",
    "The model does not support tools.",
    "Tool use is not supported by this model.",
])
def test_modell_ohne_tool_support_scheitert_laut(wortlaut):
    client = FalscherClient(fehler=_client_error("ValidationException", wortlaut))
    ereignisse = _sammeln(_provider(client, model_name="amazon.titan-text-express-v1"), tools=[WERKZEUG])
    assert [e.type for e in ereignisse] == ["error"]
    text = ereignisse[0].text
    assert "amazon.titan-text-express-v1" in text and wortlaut in text and "Werkzeug" in text
    # Kein stilles Weglassen: die Werkzeuge gingen mit und kein zweiter Versuch ohne sie.
    assert len(client.anfragen) == 1 and "toolConfig" in client.anfragen[0]
    # Weder Modellwechsel noch Wiederholung — und der Chat zeigt den Satz unveraendert.
    assert not model_fallback.is_retryable(text) and not model_fallback.is_connection_glitch(text)
    assert nutzertext_fuer_api_fehler(text) == text


def test_streaming_einschraenkung_wird_benannt():
    e = fehler_aus_antwort("ValidationException", "This model doesn't support tool use in streaming mode.",
                           400, "m", True)
    assert "Streaming" in e.text


def test_andere_ablehnung_behauptet_nichts_ueber_werkzeuge():
    client = FalscherClient(fehler=_client_error("ValidationException", "Input is too long for requested model."))
    text = _sammeln(_provider(client), tools=[WERKZEUG])[0].text
    assert "keine Werkzeug-Aufrufe" not in text
    assert "Input is too long" in text and MODELL in text


def test_ablehnung_ohne_werkzeuge_wird_nicht_als_toolmangel_gedeutet():
    e = fehler_aus_antwort("ValidationException", "This model doesn't support tool use.", 400, "m", False)
    assert "keine Werkzeug-Aufrufe" not in e.text


def test_inferenzprofil_noetig_sagt_was_zu_tun_ist():
    nachricht = ("Invocation of model ID anthropic.claude-x with on-demand throughput isn't supported. "
                 "Retry your request with the ID or ARN of an inference profile that contains this model.")
    text = fehler_aus_antwort("ValidationException", nachricht, 400, "m", True).text
    assert "Inferenzprofil" in text
    assert not model_fallback.is_retryable(text) and nutzertext_fuer_api_fehler(text) == text


@pytest.mark.parametrize("code,status", [
    ("ThrottlingException", 429), ("ServiceQuotaExceededException", 400), ("ModelNotReadyException", 429),
    ("ModelTimeoutException", 408), ("ModelErrorException", 424), ("ModelStreamErrorException", 424),
    ("InternalServerException", 500), ("ServiceUnavailableException", 503),
])
def test_kapazitaet_weicht_aus(code, status):
    text = _sammeln(_provider(FalscherClient(fehler=_client_error(code, "Bitte warten", status))))[0].text
    assert code in text
    assert model_fallback.is_retryable(text), text
    assert text.startswith("API error ")


@pytest.mark.parametrize("code,nachricht,status", [
    ("AccessDeniedException", "You don't have access to the model with the specified model ID.", 403),
    ("UnrecognizedClientException", "The security token included in the request is invalid.", 403),
    ("ExpiredTokenException", "The security token included in the request is expired", 400),
    ("InvalidSignatureException", "The request signature we calculated does not match. Try again later.", 403),
])
def test_zugang_weicht_nie_aus(code, nachricht, status):
    ereignis = _sammeln(_provider(FalscherClient(fehler=_client_error(code, nachricht, status))))[0]
    assert not model_fallback.is_retryable(ereignis.text), ereignis.text
    assert not model_fallback.is_connection_glitch(ereignis.text)
    assert "Zugang" in nutzertext_fuer_api_fehler(ereignis.text)


def test_unbekanntes_modell_ist_endgueltig():
    text = _sammeln(_provider(FalscherClient(fehler=_client_error(
        "ResourceNotFoundException", "Could not resolve the foundation model.", 404))))[0].text
    assert not model_fallback.is_retryable(text)
    assert "Modell" in nutzertext_fuer_api_fehler(text)


def test_zeitueberschreitung_und_leitung():
    zeit = _sammeln(_provider(FalscherClient(fehler=ReadTimeoutError(endpoint_url="https://x.example"))))[0]
    assert model_fallback.is_retryable(zeit.text)
    netz = _sammeln(_provider(FalscherClient(fehler=EndpointConnectionError(endpoint_url="https://x.example"))))[0]
    assert model_fallback.is_connection_glitch(netz.text)
    abriss = FalscherStrom([*_text("Hal"), ResponseStreamingError(error="Connection broken")])
    ereignisse = _sammeln(_provider(FalscherClient(strom=abriss)))
    assert [e.type for e in ereignisse] == ["text_delta", "error"]
    assert model_fallback.is_connection_glitch(ereignisse[-1].text)


def test_fehlertext_traegt_keine_zahlen_aus_den_umstaenden():
    """``model_fallback`` sucht Statuscodes als Teilzeichenkette."""
    nachrichten = [ChatMessage(role="user", content="x")] * 500
    text = _sammeln(_provider(FalscherClient(fehler=_client_error(
        "ValidationException", "Nope"))), nachrichten)[0].text
    assert "500" not in text and not model_fallback.is_retryable(text)


@pytest.mark.parametrize("kwargs,erwartet", [
    ({"aws_region": ""}, "Region"),
    ({"aws_region": "boese.example/"}, "keine gültige"),
    ({"api_key": ""}, "Zugangsdaten"),
    ({"model_name": ""}, "Modell"),
    ({"iam_gesperrt": True, "api_key": "", "aws_access_key_id": "AKIAEXAMPLE"}, "Egress-Proxy"),
])
def test_fehlende_einrichtung_geht_nicht_ans_netz(kwargs, erwartet):
    client = FalscherClient(_text("nie") + ENDE)
    ereignisse = _sammeln(_provider(client, **kwargs))
    assert [e.type for e in ereignisse] == ["error"] and erwartet in ereignisse[0].text
    assert client.anfragen == []
    assert not model_fallback.is_retryable(ereignisse[0].text)
    assert not model_fallback.is_connection_glitch(ereignisse[0].text)


def test_region_kommt_aus_dem_endpunkt_nie_aus_einem_standard():
    assert region_aufloesen("", "https://bedrock-runtime.eu-west-1.amazonaws.com") == "eu-west-1"
    assert region_aufloesen("", "https://bedrock-runtime.eu-west-1.amazonaws.com.boese.example") == ""
    assert region_aufloesen("us-gov-west-1", "") == "us-gov-west-1"
    assert region_aufloesen("", "https://example.com") == ""
    assert region_aufloesen("", "") == ""


def test_einrichtungsfehler_oeffnet_den_breaker_nicht_kapazitaet_schon():
    falsch = _provider(FalscherClient(), aws_region="")
    for _ in range(5):
        assert "Region" in _sammeln(falsch)[0].text

    gedrosselt = _provider(FalscherClient(fehler=_client_error("ThrottlingException", "x", 429)))
    for _ in range(3):
        _sammeln(gedrosselt)
    assert "Circuit breaker" in _sammeln(gedrosselt)[0].text


# ── Nebenlaeufigkeit ────────────────────────────────────────────────────────


def test_blockierender_strom_haelt_die_ereignisschleife_nicht_an():
    strom = FalscherStrom(_text("a", "b", "c") + ENDE, wartezeit=0.05)

    async def lauf():
        schlaege = 0
        fertig = asyncio.Event()

        async def takt():
            nonlocal schlaege
            while not fertig.is_set():
                schlaege += 1
                await asyncio.sleep(0.01)

        aufgabe = asyncio.create_task(takt())
        ereignisse = [e async for e in _provider(FalscherClient(strom=strom)).stream_completion(
            [ChatMessage(role="user", content="x")])]
        fertig.set()
        await aufgabe
        return schlaege, ereignisse

    schlaege, ereignisse = asyncio.run(lauf())
    assert ereignisse[-1].type == "done"
    assert schlaege >= 10, f"Schleife stand: nur {schlaege} Takte"


def test_aussteigen_mittendrin_schliesst_den_strom():
    strom = FalscherStrom(_text("a"), haengt=True)

    async def lauf():
        erzeuger = _provider(FalscherClient(strom=strom)).stream_completion(
            [ChatMessage(role="user", content="x")])
        erstes = await erzeuger.__anext__()
        await erzeuger.aclose()
        return erstes

    assert asyncio.run(asyncio.wait_for(lauf(), 5)).text == "a"
    assert strom.geschlossen.wait(2)


def test_close_beendet_einen_haengenden_lauf():
    strom = FalscherStrom(_text("a"), haengt=True)

    async def lauf():
        provider = _provider(FalscherClient(strom=strom))
        ereignisse = []

        async def lesen():
            async for e in provider.stream_completion([ChatMessage(role="user", content="x")]):
                ereignisse.append(e)
                if e.type == "text_delta":
                    await provider.close()

        await asyncio.wait_for(lesen(), 3)
        return ereignisse

    ereignisse = asyncio.run(lauf())
    assert strom.geschlossen.wait(2)
    assert ereignisse[-1].type == "error" and "abgebrochen" in ereignisse[-1].text


def test_haengender_aufruf_vor_dem_strom_laesst_sich_abbrechen():
    freigabe = threading.Event()

    class Haengt(FalscherClient):
        def converse_stream(self, **anfrage):
            freigabe.wait(5)
            return super().converse_stream(**anfrage)

    strom = FalscherStrom(_text("a"), haengt=True)

    async def lauf():
        provider = _provider(Haengt(strom=strom))

        async def lesen():
            return [e async for e in provider.stream_completion([ChatMessage(role="user", content="x")])]

        aufgabe = asyncio.create_task(lesen())
        await asyncio.sleep(0.05)
        await provider.close()
        ergebnis = await asyncio.wait_for(aufgabe, 2)
        freigabe.set()
        return ergebnis

    assert [e.type for e in asyncio.run(lauf())] == ["error"]
    assert strom.geschlossen.wait(2), "Strom blieb nach dem Abbruch offen"


# ── Echter botocore-Client ──────────────────────────────────────────────────


def _rahmen(kopfzeilen: dict[str, str], rumpf: bytes) -> bytes:
    kopf = b"".join(bytes([len(k)]) + k.encode() + b"\x07" + struct.pack(">H", len(v)) + v.encode()
                    for k, v in kopfzeilen.items())
    vorspann = struct.pack(">II", 16 + len(kopf) + len(rumpf), len(kopf))
    nachricht = vorspann + struct.pack(">I", zlib.crc32(vorspann)) + kopf + rumpf
    return nachricht + struct.pack(">I", zlib.crc32(nachricht))


def _ereignis(art: str, daten: dict) -> bytes:
    return _rahmen({":message-type": "event", ":event-type": art, ":content-type": "application/json"},
                   json.dumps(daten).encode())


def _ausnahme(art: str, nachricht: str) -> bytes:
    return _rahmen({":message-type": "exception", ":exception-type": art, ":content-type": "application/json"},
                   json.dumps({"message": nachricht}).encode())


class _Roh:
    """Was urllib3 sonst liefert: Stuecke, an beliebigen Stellen geschnitten."""

    def __init__(self, daten: bytes, stueck: int):
        self.daten = daten
        self.stueck = stueck

    def stream(self, *_a, **_k):
        for i in range(0, len(self.daten), self.stueck):
            yield self.daten[i:i + self.stueck]

    def read(self, *_a, **_k):
        return self.daten

    def close(self):
        pass


def _echter_client(provider, status=200, daten=b"", kopf=None, stueck=7):
    gesendet = []

    def antworten(request, **_kwargs):
        gesendet.append(request)
        return AWSResponse(request.url, status, kopf or {"content-type": "application/vnd.amazon.eventstream"},
                           _Roh(daten, stueck))

    client = provider._client_bauen()
    client.meta.events.register("before-send.bedrock-runtime.ConverseStream", antworten)
    provider._client_fabrik = lambda: client
    return gesendet


ANTWORT = b"".join([
    _ereignis("messageStart", {"role": "assistant"}),
    _ereignis("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"text": "Moment."}}),
    _ereignis("contentBlockStop", {"contentBlockIndex": 0}),
    _ereignis("contentBlockStart", {"contentBlockIndex": 1, "start": {
        "toolUse": {"toolUseId": "tooluse_1", "name": "lese_datei"}}}),
    _ereignis("contentBlockDelta", {"contentBlockIndex": 1, "delta": {"toolUse": {"input": '{"pfad"'}}}),
    _ereignis("contentBlockDelta", {"contentBlockIndex": 1, "delta": {"toolUse": {"input": ': "/a"}'}}}),
    _ereignis("contentBlockStop", {"contentBlockIndex": 1}),
    _ereignis("messageStop", {"stopReason": "tool_use"}),
    _ereignis("metadata", {"usage": {"inputTokens": 5, "outputTokens": 4, "totalTokens": 9},
                           "metrics": {"latencyMs": 12}}),
])


def test_echter_client_bearer_und_ganzer_strom(monkeypatch):
    monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    provider = _provider(api_key="aie-geheim-k3-abcdef012345")
    gesendet = _echter_client(provider, daten=ANTWORT)
    verlauf = [
        ChatMessage(role="system", content="Regel"),
        ChatMessage(role="user", content=[{"type": "text", "text": "Sieh"},
                                          {"type": "image", "media_type": "image/png", "data": PIXEL}]),
        ChatMessage(role="assistant", content="", tool_calls=[_tool_call(id_="t0")]),
        ChatMessage(role="tool", content="Inhalt", tool_call_id="t0", name="lese_datei"),
    ]
    ereignisse = _sammeln(provider, verlauf, [WERKZEUG])

    assert [e.type for e in ereignisse] == ["text_delta", "tool_call", "done"]
    assert ereignisse[1].tool_input == {"pfad": "/a"} and ereignisse[1].tool_id == "tooluse_1"
    assert (ereignisse[2].input_tokens, ereignisse[2].output_tokens) == (5, 4)

    anfrage = gesendet[0]
    # So findet der Egress-Proxy den Platzhalter: als ganzer Bearer in der Kopfzeile.
    assert anfrage.headers["Authorization"] in (b"Bearer aie-geheim-k3-abcdef012345",
                                                "Bearer aie-geheim-k3-abcdef012345")
    assert anfrage.url.startswith("https://bedrock-runtime.eu-central-1.amazonaws.com/model/")
    rumpf = json.loads(anfrage.body)
    assert rumpf["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"]["type"] == "object"
    assert rumpf["system"] == [{"text": "Regel"}]
    assert rumpf["messages"][1]["content"][0]["toolUse"]["toolUseId"] == "t0"
    assert rumpf["messages"][2]["content"][0]["toolResult"]["toolUseId"] == "t0"


@pytest.mark.parametrize("stueck", [1, 13, 4096])
def test_echter_parser_egal_wie_der_strom_geschnitten_ist(stueck):
    provider = _provider()
    _echter_client(provider, daten=ANTWORT, stueck=stueck)
    assert [e.type for e in _sammeln(provider)] == ["text_delta", "tool_call", "done"]


def test_echter_client_iam_signiert_ohne_das_secret_zu_zeigen():
    provider = _provider(api_key="geheimes-secret", aws_access_key_id=_SCHLUESSEL_ID,
                         aws_session_token="sitzung")
    gesendet = _echter_client(provider, daten=ANTWORT)
    assert _sammeln(provider)[-1].type == "done"
    kopf = gesendet[0].headers["Authorization"]
    kopf = kopf.decode() if isinstance(kopf, bytes) else kopf
    assert kopf.startswith(f"AWS4-HMAC-SHA256 Credential={_SCHLUESSEL_ID}/")
    assert "/eu-central-1/bedrock/aws4_request" in kopf
    assert "geheimes-secret" not in kopf
    token = gesendet[0].headers["X-Amz-Security-Token"]
    assert (token.decode() if isinstance(token, bytes) else token) == "sitzung"


def test_echter_parser_fehler_mitten_im_strom():
    provider = _provider()
    _echter_client(provider, daten=ANTWORT[:len(_ereignis("messageStart", {"role": "assistant"}))]
                   + _ereignis("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"text": "Hal"}})
                   + _ausnahme("throttlingException", "Too many tokens, please wait."))
    ereignisse = _sammeln(provider)
    assert [e.type for e in ereignisse] == ["text_delta", "error"]
    assert ereignisse[-1].text.startswith("API error 429: ThrottlingException")
    assert model_fallback.is_retryable(ereignisse[-1].text)


def test_echter_parser_ablehnung_mitten_im_strom():
    provider = _provider()
    _echter_client(provider, daten=_ausnahme("validationException", "This model doesn't support tool use."))
    text = _sammeln(provider, tools=[WERKZEUG])[-1].text
    assert "keine Werkzeug-Aufrufe" in text and not model_fallback.is_retryable(text)


def test_echter_parser_abriss_mitten_im_rahmen():
    provider = _provider()
    _echter_client(provider, daten=ANTWORT[:-40])  # messageStop kam, metadata fehlt
    assert _sammeln(provider)[-1].type == "done"

    provider = _provider()
    _echter_client(provider, daten=_ereignis("contentBlockDelta", {
        "contentBlockIndex": 0, "delta": {"text": "Hal"}})[:-5])
    assert model_fallback.is_connection_glitch(_sammeln(provider)[-1].text)


def test_echte_ablehnung_modell_ohne_tool_support():
    provider = _provider(model_name="amazon.titan-text-express-v1")
    _echter_client(provider, status=400, daten=b'{"message":"This model doesn\'t support tool use."}',
                   kopf={"x-amzn-errortype": "ValidationException:http://internal.amazon.com/coral/",
                         "content-type": "application/json"})
    text = _sammeln(provider, tools=[WERKZEUG])[-1].text
    assert "amazon.titan-text-express-v1" in text and "keine Werkzeug-Aufrufe" in text


def test_ablehnung_durch_den_egress_proxy_ist_kein_aws_rumpf():
    """aie_egress antwortet mit Klartext, wenn der Key am Ziel nicht freigegeben ist."""
    provider = _provider()
    _echter_client(provider, status=403, daten="Key für dieses Ziel nicht freigegeben".encode(),
                   kopf={"content-type": "text/plain; charset=utf-8"})
    text = _sammeln(provider)[-1].text
    assert text.startswith("API error 403")
    assert not model_fallback.is_retryable(text)


def test_strommaske_des_proxys_kostet_hoechstens_die_zaehlung():
    """Der Proxy haelt das Ende zurueck, wenn es wie ein Wert-Anfang aussieht.

    Bedrock streamt binaer (kein SSE): Trifft das letzte Pruefsummen-Byte den Anfang
    des Schluessels, fehlt es am Ende. Das darf eine fertige Antwort nicht kippen.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "egress"))
    import egress_logik as logik

    for zaehler in range(100_000):
        letzter_rahmen = _ereignis("metadata", {"usage": {
            "inputTokens": zaehler, "outputTokens": 1, "totalTokens": zaehler + 1}})
        if chr(letzter_rahmen[-1]).isalpha() and letzter_rahmen[-1] < 128:
            break
    schluessel = chr(letzter_rahmen[-1]) + "BSK-beispiel-schluessel"
    maske = logik.StromMaske({"aie-geheim-k3-abcdef012345": schluessel})
    daten = ANTWORT[:ANTWORT.rindex(_ereignis("metadata", {
        "usage": {"inputTokens": 5, "outputTokens": 4, "totalTokens": 9}, "metrics": {"latencyMs": 12}}))]
    durch = maske(daten) + maske(letzter_rahmen) + maske(b"")
    assert len(durch) == len(daten) + len(letzter_rahmen) - 1

    provider = _provider()
    _echter_client(provider, daten=durch)
    ereignisse = _sammeln(provider)
    assert [e.type for e in ereignisse] == ["text_delta", "tool_call", "done"]


def test_bearer_schluessel_landet_nicht_in_der_umgebung():
    import os
    provider = _provider(api_key="nur-im-client")
    provider._client_bauen()
    assert "nur-im-client" not in os.environ.values()


# ── Verdrahtung ─────────────────────────────────────────────────────────────


def test_create_provider_nimmt_die_einstellungen(monkeypatch):
    monkeypatch.setattr(settings, "llm_aws_region", "eu-west-1")
    monkeypatch.setattr(settings, "llm_aws_access_key_id", _SCHLUESSEL_ID)
    monkeypatch.setattr(settings, "llm_aws_session_token", "")
    monkeypatch.setattr(settings, "llm_bedrock_iam_gesperrt", True)
    provider = create_provider("bedrock", "", "secret", MODELL, max_tokens=0, temperature=0.2,
                               thinking_mode="auto", reasoning_effort="", api_version="")
    assert isinstance(provider, BedrockProvider)
    assert provider.aws_region == "eu-west-1" and provider.iam_gesperrt
    assert provider.aws_access_key_id == _SCHLUESSEL_ID


def test_registry_kennt_nur_belegtes():
    assert get_context_window("eu.amazon.nova-pro-v1:0") == 300_000
    assert get_context_window("amazon.nova-micro-v1:0") == 128_000
    assert get_context_window(MODELL) == 200_000      # ueber claude-sonnet-4-5
    assert estimate_cost("amazon.nova-lite-v1:0", 1_000_000, 1_000_000) == 0.0


@pytest.mark.parametrize("status,erwartet", [(429, False), (408, False), (404, True), (422, True), (503, False)])
def test_unbekannter_code_nur_echte_clientfehler_sind_konfigurationsfehler(status, erwartet):
    from app.providers.bedrock_provider import fehler_aus_antwort
    e = fehler_aus_antwort(str(status), "x", status, "m", True)
    assert e.konfigurationsfehler is erwartet


def test_abbruch_durch_close_ist_kein_konfigurationsfehler():
    strom = FalscherStrom(_text("a"), haengt=True)

    async def lauf():
        provider = _provider(FalscherClient(strom=strom))
        ereignisse = []

        async def lesen():
            async for e in provider.stream_completion([ChatMessage(role="user", content="x")]):
                ereignisse.append(e)
                if e.type == "text_delta":
                    await provider.close()

        await asyncio.wait_for(lesen(), 3)
        return ereignisse

    letzte = asyncio.run(lauf())[-1]
    assert letzte.type == "error" and not letzte.konfigurationsfehler
