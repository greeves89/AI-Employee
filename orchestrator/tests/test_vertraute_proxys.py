"""Weiterleitungsangaben nur vom Eingangs-Proxy (core/vertraute_proxys.py).

Bis 1.439 übernahm uvicorn ``X-Forwarded-For`` von jedem Absender
(``--forwarded-allow-ips "*"``): Ein Agent im Docker-Netz konnte eine beliebige
Absenderadresse vorgeben — für Rate-Limit, Anmelde-Bremse und Protokoll.

MC/DC: ``übernehmen = Gegenstelle ist vertrauter Proxy ∧ Kopfzeile vorhanden``

| Gegenstelle vertraut | XFF da | Adresse | Test |
|---|---|---|---|
| ja   | ja   | aus XFF         | test_vom_eingang_uebernommen |
| nein | ja   | echte Gegenstelle | test_vom_agenten_nicht |
| ja   | nein | echte Gegenstelle | test_eingang_ohne_kopf |
"""

import ast
import asyncio
import ipaddress
import pathlib
import socket
import unittest
from unittest.mock import AsyncMock, patch

from app.core import vertraute_proxys as vp

ROOT = pathlib.Path(__file__).resolve().parents[1]


async def _echo(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    body = f'{scope["client"][0]}|{scope["scheme"]}'.encode()
    await send({"type": "http.response.body", "body": body})


async def _rufen(mw, peer: str, **koepfe) -> str:
    scope = {"type": "http", "method": "GET", "path": "/", "scheme": "http", "query_string": b"",
             "client": (peer, 40000), "server": ("orchestrator", 8000),
             "headers": [(k.lower().replace("_", "-").encode(), v.encode()) for k, v in koepfe.items()]}
    teile = []

    async def send(nachricht):
        if nachricht["type"] == "http.response.body":
            teile.append(nachricht["body"])

    async def receive():
        return {"type": "http.request", "body": b""}

    await mw(scope, receive, send)
    return b"".join(teile).decode()


class MiddlewareTests(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.mw = vp.VertrauterProxy(_echo)
        self.mw.netze = AsyncMock(return_value=(ipaddress.ip_network("172.19.0.10/32"),))

    async def test_vom_eingang_uebernommen(self):
        self.assertEqual(await _rufen(self.mw, "172.19.0.10", X_Forwarded_For="198.51.100.7",
                                      X_Forwarded_Proto="https"), "198.51.100.7|https")

    async def test_vom_agenten_nicht(self):
        self.assertEqual(await _rufen(self.mw, "172.18.0.6", X_Forwarded_For="198.51.100.7",
                                      X_Forwarded_Proto="https"), "172.18.0.6|http")

    async def test_eingang_ohne_kopf(self):
        self.assertEqual(await _rufen(self.mw, "172.19.0.10"), "172.19.0.10|http")

    async def test_rechtester_unvertrauter_eintrag(self):
        """Hängt ein Proxy an (Caddy mit trusted_proxies), ist der linke Teil vom Client."""
        self.assertEqual(await _rufen(self.mw, "172.19.0.10",
                                      X_Forwarded_For="6.6.6.6, 198.51.100.7, 172.19.0.10"),
                         "198.51.100.7|http")

    async def test_unsinn_in_xff_wird_uebergangen(self):
        self.assertEqual(await _rufen(self.mw, "172.19.0.10", X_Forwarded_For="198.51.100.7, unsinn"),
                         "198.51.100.7|http")
        self.assertEqual(await _rufen(self.mw, "172.19.0.10", X_Forwarded_For="unsinn"), "172.19.0.10|http")

    async def test_websocket_schema(self):
        mw = vp.VertrauterProxy(_echo)
        neu = mw._umschreiben({"type": "websocket", "scheme": "ws", "client": ("172.19.0.10", 1),
                               "headers": [(b"x-forwarded-proto", b"https")]}, ())
        self.assertEqual(neu["scheme"], "wss")

    async def test_veroeffentlichter_port_bleibt_gateway(self):
        """Host → 127.0.0.1:8000 kommt vom Gateway — darf keine Adresse vorgeben (und die
        Agenten-Token-Prüfung erkennt ihn weiter als Eingang)."""
        self.assertEqual(await _rufen(self.mw, "172.18.0.1", X_Forwarded_For="10.0.0.5"), "172.18.0.1|http")


class AufloesungTests(unittest.IsolatedAsyncioTestCase):
    GAI = "asyncio.unix_events._UnixSelectorEventLoop.getaddrinfo"

    @staticmethod
    def _antwort(ip):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    async def test_namen_adressen_und_netze(self):
        mw = vp.VertrauterProxy(_echo)
        with patch.dict("os.environ", {"TRUSTED_PROXY_HOSTS": "ai-employee-caddy, 172.18.0.1, 10.9.0.0/16"}), \
                patch(self.GAI, new=AsyncMock(return_value=self._antwort("172.19.0.10"))) as gai:
            netze = await mw.netze()
            await mw.netze()
        self.assertEqual(gai.await_count, 1, "nur der Name wird aufgelöst, und nur einmal je Minute")
        for ip, erwartet in (("172.19.0.10", True), ("172.18.0.1", True), ("10.9.4.2", True),
                             ("172.18.0.6", False)):
            self.assertEqual(mw._vertraut(ipaddress.ip_address(ip), netze), erwartet, ip)

    async def test_unaufloesbar_heisst_niemandem_vertrauen_und_gilt_nur_kurz(self):
        mw = vp.VertrauterProxy(_echo)
        with patch.dict("os.environ", {"TRUSTED_PROXY_HOSTS": "ai-employee-caddy"}), \
                patch(self.GAI, new=AsyncMock(side_effect=socket.gaierror("nicht da"))):
            self.assertEqual(await mw.netze(), ())
            self.assertEqual(await _rufen(mw, "172.19.0.10", X_Forwarded_For="1.2.3.4"), "172.19.0.10|http")
        self.assertEqual(mw._gueltig, vp.FEHLSCHLAG_S)

    async def test_dns_haengt_anfrage_wartet_nicht(self):
        """Nach dem ersten Mal wartet keine Anfrage auf DNS: alter Stand, Auffrischen im Hintergrund."""
        mw = vp.VertrauterProxy(_echo)
        with patch.dict("os.environ", {"TRUSTED_PROXY_HOSTS": "ai-employee-caddy"}), \
                patch(self.GAI, new=AsyncMock(return_value=self._antwort("172.19.0.10"))):
            await mw.netze()
        mw._stand -= vp.GUELTIG_S + 1                     # abgelaufen

        async def haengt(*a, **k):
            await asyncio.sleep(30)

        with patch.dict("os.environ", {"TRUSTED_PROXY_HOSTS": "ai-employee-caddy"}), \
                patch(self.GAI, new=haengt):
            netze = await asyncio.wait_for(mw.netze(), 0.5)
            self.assertEqual(netze, (ipaddress.ip_network("172.19.0.10/32"),))
            mw._aufgabe.cancel()

    async def test_dns_zeitgrenze(self):
        mw = vp.VertrauterProxy(_echo)

        async def haengt(*a, **k):
            await asyncio.sleep(30)

        with patch.dict("os.environ", {"TRUSTED_PROXY_HOSTS": "ai-employee-caddy"}), \
                patch(self.GAI, new=haengt), patch.object(vp, "DNS_ZEIT_S", 0.05):
            self.assertEqual(await asyncio.wait_for(mw.netze(), 1), ())

    def test_vorgabe_ist_caddy(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(vp._eintraege(), ["ai-employee-caddy"])


class VerdrahtungTests(unittest.TestCase):

    def test_uvicorn_vertraut_selbst_niemandem(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        cmds = [z for z in dockerfile.splitlines() if z.startswith('CMD ["uvicorn"')]
        self.assertEqual(len(cmds), 2)
        for z in cmds:
            self.assertIn('"--no-proxy-headers"', z)
            self.assertNotIn("forwarded-allow-ips", z)

    def test_middleware_ist_die_aeusserste(self):
        """Zuletzt hinzugefügt = äußerste Schicht; Rate-Limit sieht die richtige Adresse."""
        baum = ast.parse((ROOT / "app" / "main.py").read_text(encoding="utf-8"))
        hinzu = [ast.unparse(k.args[0]) for k in ast.walk(baum)
                 if isinstance(k, ast.Call) and getattr(k.func, "attr", "") == "add_middleware"]
        zeilen = sorted((k.lineno, ast.unparse(k.args[0])) for k in ast.walk(baum)
                        if isinstance(k, ast.Call) and getattr(k.func, "attr", "") == "add_middleware")
        self.assertIn("VertrauterProxy", hinzu)
        self.assertEqual(zeilen[-1][1], "VertrauterProxy")


class ZusammenspielTests(unittest.TestCase):
    """Mit der echten App: Agent mit gefälschter Absenderadresse, Agenten-Token über Caddy."""

    def test_echte_app(self):
        import httpx
        from fastapi import Depends, FastAPI, Request

        from app.dependencies import make_agent_token, verify_agent_token

        app = FastAPI()

        @app.get("/wer")
        async def wer(request: Request):
            return {"ip": request.client.host, "schema": request.url.scheme}

        @app.get("/agent")
        async def agent(a=Depends(verify_agent_token)):
            return a

        app.add_middleware(vp.VertrauterProxy)
        mw_netze = AsyncMock(return_value=(ipaddress.ip_network("172.19.0.10/32"),))

        async def ablauf():
            with patch.object(vp.VertrauterProxy, "netze", mw_netze), \
                    patch("app.dependencies._abweisung_merken"):
                for peer, erwartet in (("172.19.0.10", {"ip": "198.51.100.7", "schema": "https"}),
                                       ("172.18.0.6", {"ip": "172.18.0.6", "schema": "http"})):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(peer, 1)),
                                                 base_url="http://orchestrator") as c:
                        r = await c.get("/wer", headers={"X-Forwarded-For": "198.51.100.7",
                                                         "X-Forwarded-Proto": "https"})
                        self.assertEqual(r.json(), erwartet, peer)
                koepfe = {"Authorization": f"Bearer {make_agent_token('e225cbe0')}", "X-Agent-ID": "e225cbe0"}
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("172.19.0.10", 1)),
                                             base_url="http://orchestrator") as c:
                    self.assertEqual((await c.get("/agent", headers={**koepfe, "X-Forwarded-For": "1.2.3.4"})).status_code, 401)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("172.18.0.6", 1)),
                                             base_url="http://orchestrator") as c:
                    self.assertEqual((await c.get("/agent", headers=koepfe)).status_code, 200)

        import asyncio
        asyncio.run(ablauf())



class SicherheitsstatusTests(unittest.TestCase):
    """Eine Override-Datei kann das Abbild überschreiben — live auf der Testanlage gefunden.

    Der Statuspunkt liest die Startargumente des LAUFENDEN Servers, nicht das Dockerfile.
    """

    BASIS = ["/usr/local/bin/python3.12", "/usr/local/bin/uvicorn", "app.main:app", "--host", "0.0.0.0"]

    def _stand(self, args, env=None):
        from app.core import sicherheitsstatus as ss
        with patch.dict("os.environ", env or {}, clear=False):
            punkte = ss._absender(args)
        return punkte[0]["stand"] if punkte else None

    def test_abbild_ok(self):
        self.assertEqual(self._stand(self.BASIS + ["--no-proxy-headers"]), "ok")

    def test_override_mit_stern_warnt(self):
        for args in (self.BASIS + ["--forwarded-allow-ips", "*"],
                     self.BASIS + ["--forwarded-allow-ips=*"],
                     self.BASIS + ["--forwarded-allow-ips", "10.0.0.1, *"]):
            with self.subTest(args=args[-1]):
                self.assertEqual(self._stand(args), "warnung")

    def test_umgebungsvariable_mit_stern_warnt(self):
        self.assertEqual(self._stand(self.BASIS, {"FORWARDED_ALLOW_IPS": "*"}), "warnung")

    def test_bestimmte_adressen_ok(self):
        self.assertEqual(self._stand(self.BASIS + ["--forwarded-allow-ips", "172.19.0.10"]), "ok")

    def test_ohne_uvicorn_kein_punkt(self):
        self.assertIsNone(self._stand(["python", "-m", "pytest"]))
        from app.core import sicherheitsstatus as ss
        self.assertEqual(ss._uvicorn_argumente(("/gibt/es/nicht",)), [])

    def test_warnung_sagt_wie_beheben(self):
        from app.core import sicherheitsstatus as ss
        p = ss._absender(self.BASIS + ["--forwarded-allow-ips", "*"])[0]
        self.assertIn("--no-proxy-headers", p["beheben"])
        self.assertIn("docker-compose.override.yml", p["beheben"])

    def test_steht_in_der_gesamtliste(self):
        import inspect
        from app.core import sicherheitsstatus as ss
        self.assertIn("_absender()", inspect.getsource(ss.stand))


if __name__ == "__main__":
    unittest.main()
