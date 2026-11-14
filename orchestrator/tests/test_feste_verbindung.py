"""Eine Stelle gegen DNS-Rebinding (Plan Sicherheit 2026-11, Punkt 8).

``core/feste_verbindung.FesteAdresse`` löst je Anfrage einmal auf, prüft und verbindet
per IP. Genutzt von Git-Servern der Projekte (mit Firmennetz-Freigabe) und vom
Schlüsselabruf beim Identitätsanbieter (nur öffentlich). Vorher löste httpx den Namen
beim IdP nach der SSRF-Prüfung ein zweites Mal auf.
"""

import inspect
import socket
import unittest

import httpx

from app.core.feste_verbindung import FesteAdresse


def _aufloesen(ip):
    return lambda host, port, type=None: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]


class _Innen(httpx.AsyncBaseTransport):
    def __init__(self):
        self.anfragen = []

    async def handle_async_request(self, request):
        self.anfragen.append((request.url.host, request.extensions.get("sni_hostname"),
                              request.headers.get("host")))
        return httpx.Response(200, json={"keys": []})


class FesteAdresseTests(unittest.IsolatedAsyncioTestCase):
    async def _holen(self, ip, frei=None):
        innen = _Innen()
        async with httpx.AsyncClient(transport=FesteAdresse(innen=innen, aufloesen=_aufloesen(ip),
                                                           firmennetz_frei=frei)) as c:
            r = await c.get("https://idp.example.com/keys")
        return r, innen.anfragen

    async def test_oeffentlich_per_ip_mit_name_fuer_zertifikat(self):
        r, anfragen = await self._holen("93.184.216.34")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(anfragen, [("93.184.216.34", "idp.example.com", "idp.example.com")])

    async def test_intern_abgelehnt_ohne_freigabe(self):
        for ip in ("10.0.0.5", "127.0.0.1", "169.254.169.254", "172.19.0.10"):
            with self.assertRaises(httpx.ConnectError, msg=ip):
                await self._holen(ip)

    async def test_firmennetz_nur_mit_freigabe(self):
        r, _ = await self._holen("10.0.0.5", frei=lambda h, p: h == "idp.example.com")
        self.assertEqual(r.status_code, 200)
        with self.assertRaises(httpx.ConnectError):
            await self._holen("127.0.0.1", frei=lambda h, p: True)   # Loopback nie


class NutzerTests(unittest.TestCase):
    def test_idp_abruf_nutzt_feste_adresse_ohne_firmennetz(self):
        from app.core import mcp_idp_auth

        quelle = inspect.getsource(mcp_idp_auth._hole_json)
        self.assertIn("transport=FesteAdresse(True", quelle)
        self.assertNotIn("firmennetz_frei", quelle)

    def test_projekt_git_nutzt_dieselbe_klasse(self):
        from app.core import projekt_git

        self.assertIsInstance(projekt_git._FesteAdresse(True), FesteAdresse)


if __name__ == "__main__":
    unittest.main()
