"""Anonymisierer vor dem Modell — reine Funktionen (egress/anonym.py).

Der heikle Teil ist der Rückweg: Das Modell schreibt token­weise, ein Platzhalter kommt
über mehrere Ereignisse zerteilt an. Geprüft wird mit nachgebauten Ereignisströmen von
Anthropic und OpenAI (Codex), in zufälligen Stückgrenzen, auch mit leeren Stücken.
"""

import json
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "egress"))
import anonym  # noqa: E402

K = "schluessel-agent-a1"


class ErkennungTests(unittest.TestCase):

    def _ersetzt(self, text, begriffe=()):
        a = anonym.Anonymisierer(K, list(begriffe))
        return a.text(text), a.zuordnung

    def test_email(self):
        neu, z = self._ersetzt("Schreib an max.mustermann@example.com bitte.")
        self.assertNotIn("max.mustermann@example.com", neu)
        self.assertEqual(list(z.values()), ["max.mustermann@example.com"])
        self.assertRegex(neu, r"Schreib an \[EMAIL-[0-9a-f]{8}\] bitte\.")

    def test_iban_nur_mit_gueltiger_pruefziffer(self):
        neu, _ = self._ersetzt("IBAN DE89 3704 0044 0532 0130 00 und DE89370400440532013000")
        self.assertEqual(neu.count("[IBAN-"), 2)
        neu, z = self._ersetzt("Kennung DE12 3456 7890 1234 5678 90")      # Prüfziffer falsch
        self.assertEqual(z, {})

    def test_karte_nur_mit_luhn(self):
        neu, z = self._ersetzt("Karte 4111 1111 1111 1111, Bestellung 1234 5678 9012 3456")
        self.assertEqual(list(z.values()), ["4111 1111 1111 1111"])

    def test_telefon(self):
        for nummer in ("+49 30 1234567", "0171/1234567", "0049 89 12345678", "(030) 123 45 67"):
            with self.subTest(nummer=nummer):
                neu, z = self._ersetzt(f"Ruf an: {nummer}.")
                self.assertIn("[TELEFON-", neu)
        for kein in ("Version 1.443.0", "Raum 0815", "Rechnung 2026-11-09", "Port 8000"):
            with self.subTest(kein=kein):
                self.assertEqual(self._ersetzt(kein)[1], {})

    def test_namen_aus_der_liste(self):
        neu, z = self._ersetzt("Herr Müller und HERR MÜLLER, nicht Müllerei.", ["Herr Müller", "IN"])
        self.assertEqual(neu.count("[NAME-"), 2, neu)
        self.assertIn("Müllerei", neu)
        self.assertEqual(len(set(p for p in z)), 1, "Groß/Klein: derselbe Platzhalter")

    def test_unsichere_zeichen_und_blobs_bleiben(self):
        self.assertEqual(self._ersetzt('Name "Max" \\ x', ['Name "Max"'])[1], {})
        blob = "A" * 300 + "max@example.com"
        self.assertEqual(self._ersetzt("A" * 400)[1], {})
        self.assertIn("[EMAIL-", self._ersetzt(blob[:300] + " max@example.com")[0])

    def test_platzhalter_wird_nicht_nochmal_ersetzt(self):
        neu, _ = self._ersetzt("max@example.com")
        self.assertEqual(self._ersetzt(neu)[0], neu)

    def test_deterministisch_je_agent(self):
        a = anonym.platzhalter(K, "EMAIL", "Max@Example.com")
        self.assertEqual(a, anonym.platzhalter(K, "EMAIL", "max@example.com"))
        self.assertNotEqual(a, anonym.platzhalter("anderer", "EMAIL", "max@example.com"))



class ReviewTests(unittest.TestCase):
    """Befunde aus dem Sicherheitsreview vom 09.11."""

    def test_kein_quadratischer_aufwand(self):
        """H1: Aufwand wächst linear (doppelte Eingabe ≈ doppelte Zeit, nie viermal) und bleibt
        absolut klein. Vorher: 40 000 ``%`` ohne ``@`` = 1,7 s, quadratisch."""
        import time

        def dauer(text):
            a = anonym.Anonymisierer(K, ["Herr Müller"])
            t = time.perf_counter()
            a.text(text)
            return time.perf_counter() - t

        for name, teil in {"prozent": "%", "prozent20": "%20", "punkte": "a.", "ziffern": "0 ",
                           "iban": "DE89 ", "ohne_at": "x" * 63 + ".", "at_ohne_punkt": "a@" }.items():
            with self.subTest(name):
                klein = dauer(teil * (250_000 // len(teil)))
                gross = dauer(teil * (1_000_000 // len(teil)))
                self.assertLess(gross, max(klein * 6, 0.05), f"{name}: {klein:.2f}s → {gross:.2f}s")
                self.assertLess(gross, 8.0, name)

    def test_karte_hinter_anderen_ziffern(self):
        """M2: Eine Bestellnummer davor verdeckte die Karte."""
        a = anonym.Anonymisierer(K, [])
        neu = a.text("Bestellung 12345 67890 4111 1111 1111 1111 erledigt")
        self.assertNotIn("4111 1111 1111 1111", neu)
        self.assertIn("Bestellung 12345 67890 [KARTE-", neu)

    def test_kollision_bekommt_laengeren_platzhalter(self):
        """M3: zwei Werte, eine kurze Kennung → der zweite bekommt eine lange, nichts vertauscht."""
        from unittest.mock import patch
        echt = anonym.platzhalter
        with patch.object(anonym, "platzhalter",
                          lambda k, art, w, laenge=8: "[EMAIL-aaaaaaaa]" if laenge == 8 else echt(k, art, w, laenge)):
            a = anonym.Anonymisierer(K, [])
            neu = a.text("a@example.com und b@example.com")
        self.assertEqual(len(a.zuordnung), 2)
        self.assertEqual(sorted(a.zuordnung.values()), ["a@example.com", "b@example.com"])
        self.assertEqual(anonym.einsetzen(neu, a.zuordnung), "a@example.com und b@example.com")

    def test_denken_bleibt_in_beide_richtungen_unangetastet(self):
        """M4: signierte Denk-Blöcke müssen byte­gleich zurückkommen."""
        anfrage = {"messages": [{"role": "assistant", "content": [
            {"type": "thinking", "thinking": "Ich rufe 0171 1234567 an, Platzhalter [EMAIL-3f9a1c2b]",
             "signature": "sig"},
            {"type": "text", "text": "Ruf 0171 1234567 an"}]}]}
        neu, z = anonym.anfrage_anonymisieren(json.dumps(anfrage).encode(), K, [])
        daten = json.loads(neu)
        self.assertEqual(daten["messages"][0]["content"][0], anfrage["messages"][0]["content"][0])
        self.assertNotIn("0171 1234567", daten["messages"][0]["content"][1]["text"])
        zurueck = anonym.einsetzen_json({"content": [{"type": "thinking", "thinking": "[EMAIL-3f9a1c2b]"}]},
                                        {"[EMAIL-3f9a1c2b]": "x@example.com"})
        self.assertEqual(zurueck["content"][0]["thinking"], "[EMAIL-3f9a1c2b]")
        strom = anonym.SseEinsetzer({"[EMAIL-3f9a1c2b]": "x@example.com"})
        aus = strom(_sse([{"type": "content_block_delta", "index": 0,
                           "delta": {"type": "thinking_delta", "thinking": "[EMAIL-3f9a1c2b]"}},
                          {"type": "content_block_stop", "index": 0}]))
        self.assertIn("[EMAIL-3f9a1c2b]", aus.decode())

    def test_rundlauf_stabil(self):
        """Einsetzen und erneut anonymisieren ergibt dieselben Platzhalter (Prompt-Cache, Verlauf)."""
        text = "Herr Müller (max@example.com, +49 30 1234567, DE89370400440532013000)"
        a = anonym.Anonymisierer(K, ["Herr Müller"])
        einmal = a.text(text)
        b = anonym.Anonymisierer(K, ["Herr Müller"])
        self.assertEqual(b.text(anonym.einsetzen(einmal, a.zuordnung)), einmal)


class AnfrageTests(unittest.TestCase):

    ANFRAGE = {
        "model": "claude-sonnet-5", "stream": True, "max_tokens": 1000,
        "system": [{"type": "text", "text": "Du bist Assistent von Herr Müller."}],
        "messages": [
            {"role": "user", "content": "Mail an anna.schmidt@example.com: Zahlung auf DE89370400440532013000."},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Read",
                                               "input": {"file_path": "/work/kunden.csv"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                          "content": "Name;Tel\nHerr Müller;+49 30 1234567\n"}]},
        ],
        "tools": [{"name": "Read", "input_schema": {"type": "object", "properties": {"email": {"type": "string"}}}}],
    }

    def test_alles_ersetzt_schluessel_und_struktur_bleiben(self):
        roh = json.dumps(self.ANFRAGE).encode()
        neu, z = anonym.anfrage_anonymisieren(roh, K, ["Herr Müller"])
        text = neu.decode()
        for wert in ("anna.schmidt@example.com", "DE89370400440532013000", "Herr Müller", "+49 30 1234567"):
            self.assertNotIn(wert, text)
        daten = json.loads(text)
        self.assertEqual(daten["messages"][1]["content"][0]["input"]["file_path"], "/work/kunden.csv")
        self.assertIn("email", daten["tools"][0]["input_schema"]["properties"], "Schlüssel bleiben")
        self.assertEqual(anonym.einsetzen_json(daten, z), self.ANFRAGE)

    def test_ohne_treffer_unveraendert(self):
        roh = b'{"model":"x","messages":[{"role":"user","content":"Hallo"}]}'
        self.assertEqual(anonym.anfrage_anonymisieren(roh, K, []), (roh, {}))

    def test_kein_json_wird_abgelehnt(self):
        with self.assertRaises(ValueError):
            anonym.anfrage_anonymisieren(b"kein json", K, [])


class TextStromTests(unittest.TestCase):

    def test_zerteilter_platzhalter(self):
        z = {"[EMAIL-3f9a1c2b]": "max@example.com"}
        text = "Schick es an [EMAIL-3f9a1c2b], danke. [Hinweis] und [EMAIL-ffffffff]."
        for groesse in (1, 2, 3, 5, 7, 50):
            with self.subTest(groesse=groesse):
                s = anonym.TextStrom(z)
                aus = "".join(s.weiter(text[i:i + groesse]) for i in range(0, len(text), groesse)) + s.ende()
                self.assertEqual(aus, "Schick es an max@example.com, danke. [Hinweis] und [EMAIL-ffffffff].")

    def test_haelt_nie_mehr_als_einen_platzhalter_zurueck(self):
        s = anonym.TextStrom({"[EMAIL-3f9a1c2b]": "x"})
        self.assertEqual(s.weiter("Liste [1] [2] [EMAIL-3f"), "Liste [1] [2] ")
        self.assertEqual(s.rest, "[EMAIL-3f")


def _sse(ereignisse, mit_namen=True) -> bytes:
    teile = []
    for e in ereignisse:
        kopf = f"event: {e['type']}\n" if mit_namen else ""
        teile.append(f"{kopf}data: {json.dumps(e, ensure_ascii=False)}\n\n")
    return "".join(teile).encode()


def _lesen(roh: bytes) -> list[dict]:
    out = []
    for block in roh.decode().split("\n\n"):
        daten = [z[5:].strip() for z in block.split("\n") if z.startswith("data:")]
        if daten:
            out.append(json.loads("\n".join(daten)))
    return out


def _zerteilt(text: str, rng: random.Random) -> list[str]:
    stuecke, i = [], 0
    while i < len(text):
        n = rng.randint(1, 4)
        stuecke.append(text[i:i + n])
        i += n
    return stuecke


def _durch(einsetzer, roh: bytes, rng: random.Random) -> bytes:
    aus, i = [], 0
    while i < len(roh):
        n = rng.randint(0, 40)            # 0 = leeres Stück mitten im Strom
        aus.append(einsetzer(roh[i:i + n]))
        i += n
    aus.append(einsetzer(b""))
    return b"".join(aus)


class SseAnthropicTests(unittest.TestCase):
    Z = {"[EMAIL-3f9a1c2b]": "anna.schmidt@example.com", "[NAME-0a1b2c3d]": "Herr Müller"}
    TEXT = "Ich schreibe [NAME-0a1b2c3d] an [EMAIL-3f9a1c2b] und [Hinweis]."
    ARGS = '{"to": "[EMAIL-3f9a1c2b]", "subject": "Gruß an [NAME-0a1b2c3d]"}'

    def _strom(self, rng):
        ev = [{"type": "message_start", "message": {"id": "m1", "content": []}},
              {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}]
        ev += [{"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": t}}
               for t in _zerteilt(self.TEXT, rng)]
        ev += [{"type": "content_block_stop", "index": 0},
               {"type": "content_block_start", "index": 1,
                "content_block": {"type": "tool_use", "id": "t9", "name": "send_mail", "input": {}}}]
        ev += [{"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": t}}
               for t in _zerteilt(self.ARGS, rng)]
        ev += [{"type": "content_block_stop", "index": 1}, {"type": "message_stop"}]
        return _sse(ev)

    def test_viele_zerlegungen(self):
        for seed in range(60):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                aus = _lesen(_durch(anonym.SseEinsetzer(self.Z), self._strom(rng), rng))
                text = "".join(e["delta"]["text"] for e in aus
                               if e["type"] == "content_block_delta" and e["index"] == 0)
                args = "".join(e["delta"]["partial_json"] for e in aus
                               if e["type"] == "content_block_delta" and e["index"] == 1)
                self.assertEqual(text, "Ich schreibe Herr Müller an anna.schmidt@example.com und [Hinweis].")
                self.assertEqual(json.loads(args), {"to": "anna.schmidt@example.com", "subject": "Gruß an Herr Müller"})
                typen = [e["type"] for e in aus]
                self.assertEqual(typen[-1], "message_stop")
                # Jeder Block: alle Teilstücke VOR seinem Ende
                for idx in (0, 1):
                    stop = max(i for i, e in enumerate(aus) if e["type"] == "content_block_stop" and e["index"] == idx)
                    teile = [i for i, e in enumerate(aus) if e["type"] == "content_block_delta" and e["index"] == idx]
                    self.assertLess(max(teile), stop)

    def test_ereignisnamen_bleiben(self):
        roh = _durch(anonym.SseEinsetzer(self.Z), self._strom(random.Random(1)), random.Random(2))
        for block in roh.decode().strip().split("\n\n"):
            name = block.split("\n")[0]
            self.assertTrue(name.startswith("event: "), block[:60])
            self.assertEqual(name[7:], json.loads(block.split("data: ", 1)[1])["type"])

    def test_crlf(self):
        roh = self._strom(random.Random(3)).replace(b"\n", b"\r\n")
        aus = _lesen(anonym.SseEinsetzer(self.Z)(roh))
        text = "".join(e["delta"]["text"] for e in aus if e["type"] == "content_block_delta" and e["index"] == 0)
        self.assertIn("anna.schmidt@example.com", text)


class SseOpenAITests(unittest.TestCase):
    Z = {"[EMAIL-3f9a1c2b]": "anna.schmidt@example.com"}

    def test_responses_strom(self):
        for seed in range(40):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                text = "Antwort an [EMAIL-3f9a1c2b] gesendet."
                args = '{"empfaenger":"[EMAIL-3f9a1c2b]"}'
                ev = [{"type": "response.created", "response": {"id": "r1"}}]
                ev += [{"type": "response.output_text.delta", "item_id": "msg_1", "output_index": 0,
                        "content_index": 0, "delta": t} for t in _zerteilt(text, rng)]
                ev += [{"type": "response.output_text.done", "item_id": "msg_1", "output_index": 0,
                        "content_index": 0, "text": text}]
                ev += [{"type": "response.function_call_arguments.delta", "item_id": "fc_1", "output_index": 1,
                        "delta": t} for t in _zerteilt(args, rng)]
                ev += [{"type": "response.function_call_arguments.done", "item_id": "fc_1", "output_index": 1,
                        "arguments": args},
                       {"type": "response.completed", "response": {"output": [{"content": [{"text": text}]}]}}]
                aus = _lesen(_durch(anonym.SseEinsetzer(self.Z), _sse(ev, mit_namen=False), rng))
                deltas = "".join(e["delta"] for e in aus if e["type"] == "response.output_text.delta")
                self.assertEqual(deltas, "Antwort an anna.schmidt@example.com gesendet.")
                fertig = next(e for e in aus if e["type"] == "response.output_text.done")
                self.assertEqual(fertig["text"], "Antwort an anna.schmidt@example.com gesendet.")
                fargs = "".join(e["delta"] for e in aus if e["type"] == "response.function_call_arguments.delta")
                self.assertEqual(json.loads(fargs), {"empfaenger": "anna.schmidt@example.com"})
                self.assertIn("anna.schmidt@example.com", json.dumps(aus[-1]))

    def test_unbekannter_platzhalter_bleibt(self):
        aus = anonym.SseEinsetzer(self.Z)(_sse([{"type": "response.output_text.delta", "item_id": "m", "delta": "[EMAIL-00000000] x"},
                                                 {"type": "response.output_text.done", "item_id": "m", "text": "x"}]))
        self.assertIn("[EMAIL-00000000]", aus.decode())


if __name__ == "__main__":
    unittest.main()
