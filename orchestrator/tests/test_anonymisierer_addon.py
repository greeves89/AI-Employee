"""Anonymisierer im echten mitmproxy-Addon (egress/aie_egress.py).

Braucht mitmproxy — läuft in der CI nicht (dort fehlt es) und lokal nicht, aber im Abbild
des Proxys (gleicher Digest wie in der Compose-Datei):

    docker run --rm -v "$PWD":/w -w /w/orchestrator/tests mitmproxy/mitmproxy:12.3.1@sha256:… \\
        python3 -m unittest test_anonymisierer_addon -v
"""

import asyncio
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "egress"))
try:
    from mitmproxy.test import tflow, tutils
    import aie_egress
except ImportError:  # pragma: no cover — ohne mitmproxy (CI, lokal)
    aie_egress = None

CFG = {"schluessel": "k-a1", "begriffe": ["Herr Müller"], "ziele": ["api.anthropic.com", "chatgpt.com"]}
AGENT = ("a1", "tok")


class _Orch:
    def __init__(self, cfg=CFG):
        self.cfg = cfg
        self.frisch = False  # einmal frisch gefragt?

    def anonym(self, agent):
        return self.cfg

    def hosts(self, agent, frisch=False):
        self.frisch = self.frisch or frisch
        return list(self.cfg["ziele"]) if self.cfg else []

    def werte(self, *a):
        raise AssertionError("keine Schlüssel-Platzhalter im Test")

    #: Zielliste am Ausgang (orchestrator/app/core/zielliste) — None = aus.
    ziele = None

    def zielliste(self, agent):
        return self.ziele


@unittest.skipIf(aie_egress is None, "mitmproxy fehlt")
class AddonTests(unittest.TestCase):

    def _flow(self, koerper: dict | bytes, host="api.anthropic.com", cfg=CFG, **kopf):
        addon = aie_egress.AieEgress()
        addon.orch = _Orch(cfg)
        roh = koerper if isinstance(koerper, bytes) else json.dumps(koerper).encode()
        f = tflow.tflow(req=tutils.treq(method=b"POST", host=host, port=443, scheme=b"https",
                                        path=b"/v1/messages", content=roh))
        for k, v in kopf.items():
            f.request.headers[k.replace("_", "-")] = v
        addon.agenten[f.client_conn.id] = AGENT
        addon.ziele[f.client_conn.id] = (host, 443)
        return addon, f

    def _anfrage(self, addon, f):
        asyncio.run(addon.requestheaders(f))
        if f.response is None:
            asyncio.run(addon.request(f))

    ANFRAGE = {"model": "m", "stream": True, "messages": [
        {"role": "user", "content": "Mail an anna@example.com, Gruß an Herr Müller, Tel +49 30 1234567"}]}

    def test_anfrage_wird_anonymisiert(self):
        addon, f = self._flow(self.ANFRAGE)
        self._anfrage(addon, f)
        text = f.request.get_text()
        for wert in ("anna@example.com", "Herr Müller", "+49 30 1234567"):
            self.assertNotIn(wert, text)
        self.assertEqual(len(f.metadata["aie_anonym"]), 3)
        self.assertFalse(f.request.stream, "Körper wird gepuffert, nicht ungesehen gestreamt")

    def test_anderer_host_und_ausgeschaltet_unveraendert(self):
        for host, cfg in (("api.github.com", CFG), ("api.anthropic.com", None)):
            with self.subTest(host=host, an=bool(cfg)):
                addon, f = self._flow(self.ANFRAGE, host=host, cfg=cfg)
                self._anfrage(addon, f)
                self.assertIn("anna@example.com", f.request.get_text())

    def test_nicht_pruefbar_wird_abgewiesen(self):
        faelle = {
            "zu groß angekündigt": dict(content_length=str(64 * 1024 * 1024)),
        }
        for name, kopf in faelle.items():
            with self.subTest(name):
                addon, f = self._flow(self.ANFRAGE, **kopf)
                self._anfrage(addon, f)
                self.assertIsNotNone(f.response, name)
                self.assertIn(f.response.status_code, (413, 415))
        addon, f = self._flow(b"kein json")
        self._anfrage(addon, f)
        self.assertEqual(f.response.status_code, 400)

    @staticmethod
    def _zstd(roh: bytes) -> bytes:
        try:
            from compression import zstd   # mitmproxy ≥ 12.3 (Python 3.14)
            return zstd.compress(roh)
        except ImportError:
            import zstandard               # mitmproxy ≤ 12.2
            return zstandard.ZstdCompressor().compress(roh)

    def test_komprimierte_anfrage_wird_entpackt_und_anonymisiert(self):
        import gzip
        roh = json.dumps(self.ANFRAGE).encode()
        for art, gepackt in (("gzip", gzip.compress(roh)), ("zstd", self._zstd(roh))):
            with self.subTest(art=art):
                addon, f = self._flow(gepackt, content_encoding=art)
                self._anfrage(addon, f)
                self.assertIsNone(f.response)
                self.assertNotIn("content-encoding", f.request.headers)
                self.assertNotIn("anna@example.com", f.request.get_text())
        addon, f = self._flow(b"\x00\x01", content_encoding="br")
        self._anfrage(addon, f)
        self.assertEqual(f.response.status_code, 415)

    def test_zstd_bombe_wird_abgelehnt(self):
        """Begrenzt auf beiden Wegen (stdlib-zstd ab 12.3, zstandard davor)."""
        bombe = self._zstd(b"0" * (40 * 1024 * 1024))
        addon, f = self._flow(bombe, content_encoding="zstd")
        self._anfrage(addon, f)
        self.assertEqual(f.response.status_code, 413)

    def test_kaputte_kompression_wird_nicht_gesendet(self):
        """Nachprüfung: kaputte zstd-/gzip-Daten warfen eine Ausnahme — mitmproxy ließ die Anfrage
        danach ungeprüft durch. Jetzt: 415, nichts geht hinaus."""
        for art, roh in (("zstd", b"\x28\xb5\x2f\xfd" + b"kaputt" * 10), ("gzip", b"\x1f\x8bkaputt")):
            with self.subTest(art=art):
                addon, f = self._flow(roh, content_encoding=art)
                self._anfrage(addon, f)
                self.assertIsNotNone(f.response, art)
                self.assertEqual(f.response.status_code, 415)

    def test_unerwarteter_fehler_sperrt_statt_durchzulassen(self):
        """Jeder Hook ist fail closed: Anfrage → 502, Antwort → Abbruch."""
        from unittest.mock import patch

        import aie_egress

        addon, f = self._flow(self.ANFRAGE)
        with patch.object(aie_egress.anonym, "anfrage_anonymisieren", side_effect=RuntimeError("unerwartet")):
            self._anfrage(addon, f)
        self.assertIsNotNone(f.response)
        self.assertEqual(f.response.status_code, 502)
        addon, f = self._flow(self.ANFRAGE)
        with patch.object(type(addon), "_anonym_einstellung", side_effect=RuntimeError("unerwartet")):
            asyncio.run(addon.requestheaders(f))
        self.assertEqual(f.response.status_code, 502)
        getoetet = []
        f.kill = lambda: getoetet.append(True)
        with patch.object(type(addon), "_maske", side_effect=RuntimeError("unerwartet")):
            f.response = __import__("mitmproxy").http.Response.make(200, b"{}")
            addon.responseheaders(f)
        self.assertEqual(getoetet, [True])

    def _connect(self, host: str, ziele, port=443):
        import base64

        addon = aie_egress.AieEgress()
        addon.orch = _Orch(None)
        addon.orch.ziele = ziele
        f = tflow.tflow(req=tutils.treq(method=b"CONNECT", host=host, port=port, path=b"", scheme=b"", authority=b""))
        f.request.headers["Proxy-Authorization"] = "Basic " + base64.b64encode(b"a1:tok").decode()
        asyncio.run(addon.http_connect(f))
        return f

    def test_zielliste_sperrt_was_nicht_auf_der_liste_steht(self):
        """Zielliste am Ausgang: CONNECT nur zu erlaubten Zielen, Modellanbieter immer."""
        ziele = {"muster": ["*.example.com"], "rolle": None, "immer": ["api.anthropic.com"]}
        self.assertEqual(self._connect("wikipedia.org", ziele).response.status_code, 403)
        self.assertIsNone(self._connect("api.example.com", ziele).response)
        self.assertIsNone(self._connect("api.anthropic.com", ziele).response)
        self.assertIsNone(self._connect("wikipedia.org", None).response, "Zielliste aus: wie bisher")

    def test_zielliste_gilt_auch_fuer_klartext_http(self):
        import base64

        addon = aie_egress.AieEgress()
        addon.orch = _Orch(None)
        addon.orch.ziele = {"muster": ["erlaubt.example.com"], "rolle": None, "immer": []}
        for host, gesperrt in (("anders.example.org", True), ("erlaubt.example.com", False)):
            f = tflow.tflow(req=tutils.treq(method=b"GET", host=host, port=80, scheme=b"http", path=b"/"))
            f.request.headers["Proxy-Authorization"] = "Basic " + base64.b64encode(b"a1:tok").decode()
            asyncio.run(addon._ohne_tunnel(f))
            self.assertEqual(f.response is not None and f.response.status_code == 403, gesperrt, host)

    def test_zielliste_bricht_tls_auf_und_haelt_den_servernamen_fest(self):
        """Domain-Fronting: anderer SNI im Tunnel zum erlaubten Ziel geht nach außen nicht mit."""
        from types import SimpleNamespace
        addon, f = self._flow(self.ANFRAGE, host="erlaubt.example.com", cfg=None)
        addon.orch.ziele = {"muster": ["erlaubt.example.com"], "rolle": None, "immer": []}
        server = SimpleNamespace(sni=None)
        data = SimpleNamespace(context=SimpleNamespace(client=SimpleNamespace(id=f.client_conn.id), server=server),
                               client_hello=SimpleNamespace(sni="fremd.example.org"), ignore_connection=False)
        asyncio.run(addon.tls_clienthello(data))
        self.assertFalse(data.ignore_connection, "mit Zielliste immer aufbrechen")
        self.assertEqual(server.sni, "erlaubt.example.com")

    def test_zielliste_prueft_den_host_kopf_jeder_anfrage(self):
        ziele = {"muster": ["erlaubt.example.com"], "rolle": None, "immer": []}
        for host_kopf, gesperrt in (("fremd.example.org", True), ("erlaubt.example.com", False)):
            addon, f = self._flow({"x": 1}, host="erlaubt.example.com", cfg=None)
            addon.orch.ziele = ziele
            f.request.headers["host"] = host_kopf
            asyncio.run(addon.requestheaders(f))  # vor dem Senden, auch bei gestreamtem Körper
            self.assertEqual(f.response is not None and f.response.status_code == 421, gesperrt, host_kopf)

    def test_zielliste_klartext_host_kopf_muss_passen(self):
        import base64

        addon = aie_egress.AieEgress()
        addon.orch = _Orch(None)
        addon.orch.ziele = {"muster": ["erlaubt.example.com"], "rolle": None, "immer": []}
        f = tflow.tflow(req=tutils.treq(method=b"GET", host="erlaubt.example.com", port=80, scheme=b"http", path=b"/"))
        f.request.headers["Proxy-Authorization"] = "Basic " + base64.b64encode(b"a1:tok").decode()
        f.request.headers["host"] = "fremd.example.org"
        asyncio.run(addon._ohne_tunnel(f))
        self.assertEqual(f.response.status_code, 421)

    def test_kennung_erzwingt_frische_liste_und_sperrt_ohne(self):
        """``zl.<id>``: Der Proxy verlässt sich nicht auf einen gemerkten Stand „aus“."""
        import base64

        for ziele, gesperrt in ((None, True),
                                ({"muster": ["erlaubt.example.com"], "rolle": None, "immer": []}, False)):
            addon = aie_egress.AieEgress()
            addon.orch = _Orch(None)
            addon.orch.ziele = ziele
            f = tflow.tflow(req=tutils.treq(method=b"CONNECT", host="erlaubt.example.com", port=443,
                                            path=b"", scheme=b"", authority=b""))
            f.request.headers["Proxy-Authorization"] = "Basic " + base64.b64encode(b"zl.a1:tok").decode()
            asyncio.run(addon.http_connect(f))
            self.assertTrue(addon.orch.frisch, "mit Kennung immer frisch, solange nichts gemerkt ist")
            self.assertEqual(f.response is not None and f.response.status_code == 403, gesperrt, ziele)
            if not gesperrt:
                self.assertEqual(addon.agenten[f.client_conn.id], ("a1", "tok"), "Kennung abgestreift")

    def test_rohes_tcp_mit_zielliste_beendet(self):
        from types import SimpleNamespace
        addon, f = self._flow(self.ANFRAGE, cfg=None)
        beendet = []
        tcp = SimpleNamespace(client_conn=f.client_conn, kill=lambda: beendet.append(True))
        addon.tcp_start(tcp)
        self.assertEqual(beendet, [], "ohne Zielliste unberührt")
        addon.orch.ziele = {"muster": [], "rolle": None, "immer": []}
        addon.tcp_start(tcp)
        self.assertEqual(beendet, [True])

    def test_tls_zum_anbieter_fragt_frisch(self):
        from types import SimpleNamespace
        addon, f = self._flow(self.ANFRAGE)
        cid = f.client_conn.id
        data = SimpleNamespace(context=SimpleNamespace(client=SimpleNamespace(id=cid)),
                               client_hello=SimpleNamespace(sni="api.anthropic.com"), ignore_connection=False)
        asyncio.run(addon.tls_clienthello(data))
        self.assertTrue(addon.orch.frisch)
        self.assertFalse(data.ignore_connection, "Anbieter wird aufgebrochen, wenn der Anonymisierer an ist")

    def test_grosse_anfrage_im_eigenen_prozess(self):
        """Ab 64 KB erkennt ein eigener Prozess (re hält den GIL) — Ergebnis gleich."""
        gross = {"model": "m", "messages": [{"role": "user", "content": ("Fülltext " * 25_000) + " anna@example.com"}]}
        addon, f = self._flow(gross)
        self._anfrage(addon, f)
        self.assertIsNone(f.response)
        self.assertGreater(len(f.request.content), aie_egress._PROZESS_AB)
        self.assertNotIn("anna@example.com", f.request.get_text())
        self.assertIsNotNone(aie_egress._pool, "über den Prozess-Pool gelaufen")

    def test_gestreamter_koerper_wird_abgewiesen(self):
        addon, f = self._flow(self.ANFRAGE)
        f.request.raw_content = None
        f.request.headers["content-length"] = "1234"
        asyncio.run(addon.request(f))
        self.assertEqual(f.response.status_code, 413)

    def _antwort_strom(self, addon, f, ereignisse: list[dict]) -> str:
        f.response = tutils.tresp(status_code=200, headers=((b"content-type", b"text/event-stream"),), content=None)
        f.response.headers.pop("content-length", None)
        addon.responseheaders(f)
        strom = f.response.stream
        self.assertTrue(callable(strom))
        roh = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in ereignisse).encode()
        return b"".join(strom(roh[i:i + 7]) for i in range(0, len(roh), 7)).decode()

    def test_antwortstrom_setzt_werte_ein(self):
        addon, f = self._flow(self.ANFRAGE)
        self._anfrage(addon, f)
        z = {w: p for p, w in f.metadata["aie_anonym"].items()}
        text = f"Ich schreibe {z['anna@example.com']} und {z['Herr Müller']}."
        ev = [{"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}]
        ev += [{"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text[i:i + 3]}}
               for i in range(0, len(text), 3)]
        ev += [{"type": "content_block_stop", "index": 0}, {"type": "message_stop"}]
        aus = self._antwort_strom(addon, f, ev)
        teile = "".join(json.loads(b.split("data: ", 1)[1])["delta"]["text"]
                        for b in aus.strip().split("\n\n") if "text_delta" in b)
        self.assertEqual(teile, "Ich schreibe anna@example.com und Herr Müller.")

    def test_antwort_am_stueck(self):
        addon, f = self._flow(self.ANFRAGE)
        self._anfrage(addon, f)
        p = next(iter(f.metadata["aie_anonym"]))
        koerper = json.dumps({"content": [{"type": "text", "text": f"Wert: {p}"}]}).encode()
        f.response = tutils.tresp(status_code=200, headers=((b"content-type", b"application/json"),), content=koerper)
        addon.responseheaders(f)
        self.assertIs(f.response.stream, False)
        addon.response(f)
        self.assertIn(f.metadata["aie_anonym"][p], f.response.get_text())

    def test_websocket_zum_anbieter_wird_geschlossen(self):
        from unittest.mock import Mock
        for host, erwartet in (("api.anthropic.com", True), ("api.github.com", False)):
            with self.subTest(host=host):
                addon, f = self._flow(self.ANFRAGE, host=host)
                f.websocket = tflow.twebsocketflow().websocket
                f.kill = Mock()
                addon.websocket_message(f)
                self.assertEqual(f.kill.called, erwartet)


if __name__ == "__main__":
    unittest.main()
