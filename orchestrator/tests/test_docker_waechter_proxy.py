"""Docker-Wächter als Durchreicher: prüft, was er prüfen soll, und reicht alles
andere unverändert durch (Ströme, Umschaltung auf rohe Bytes, mehrere Anfragen
je Verbindung). Gegen einen nachgebauten Docker-Server."""

import asyncio
import json
import unittest

from app.docker_waechter import proxy


async def _docker_attrappe(reader, writer):
    """Antwortet je nach Pfad wie Docker: JSON, chunked Strom, 101 + Echo."""
    try:
        while True:
            kopf = await reader.readuntil(b"\r\n\r\n")
            erste = kopf.split(b"\r\n")[0].decode()
            laenge = 0
            for zeile in kopf.split(b"\r\n")[1:]:
                if zeile.lower().startswith(b"content-length:"):
                    laenge = int(zeile.split(b":")[1])
            koerper = await reader.readexactly(laenge) if laenge else b""
            if "/exec/x/start" in erste:
                writer.write(b"HTTP/1.1 101 UPGRADED\r\nConnection: Upgrade\r\nUpgrade: tcp\r\n\r\n")
                await writer.drain()
                while True:
                    d = await reader.read(100)
                    if not d:
                        break
                    writer.write(b"echo:" + d)
                    await writer.drain()
                writer.close()
                return
            if "/events" in erste:
                writer.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n")
                for i in range(3):
                    stueck = json.dumps({"n": i}).encode()
                    writer.write(f"{len(stueck):x}\r\n".encode() + stueck + b"\r\n")
                writer.write(b"0\r\n\r\n")
                await writer.drain()
                continue
            antwort = json.dumps({"zeile": erste, "koerper": koerper.decode()}).encode()
            writer.write(b"HTTP/1.1 201 Created\r\nContent-Type: application/json\r\nContent-Length: "
                         + str(len(antwort)).encode() + b"\r\n\r\n" + antwort)
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError):
        writer.close()


async def _senden(port, *anfragen, roh=None):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    antworten = []
    for a in anfragen:
        writer.write(a)
        await writer.drain()
        kopf = await reader.readuntil(b"\r\n\r\n")
        laenge = 0
        for zeile in kopf.split(b"\r\n"):
            if zeile.lower().startswith(b"content-length:"):
                laenge = int(zeile.split(b":")[1])
        if b"chunked" in kopf.lower():
            daten = b""
            while True:
                zeile = await reader.readuntil(b"\r\n")
                n = int(zeile.strip(), 16)
                if n == 0:
                    await reader.readuntil(b"\r\n")
                    break
                daten += (await reader.readexactly(n + 2))[:-2]
            antworten.append((kopf, daten))
        elif kopf.startswith(b"HTTP/1.1 101"):
            writer.write(roh or b"")
            await writer.drain()
            writer.write_eof()
            antworten.append((kopf, await reader.read()))
        else:
            antworten.append((kopf, await reader.readexactly(laenge) if laenge else b""))
    writer.close()
    return antworten


def _post(pfad, koerper):
    text = json.dumps(koerper).encode()
    return (f"POST {pfad} HTTP/1.1\r\nHost: docker\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(text)}\r\n\r\n").encode() + text


class Durchreicher(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.docker = await asyncio.start_server(_docker_attrappe, "127.0.0.1", 0)
        port = self.docker.sockets[0].getsockname()[1]
        aufrufe = []
        self.compose_aufrufe = aufrufe
        self.w = proxy.Waechter(("127.0.0.1", port), ["/srv/secondbrain"], dienst_schluessel="s" * 40,
                                compose=lambda d: (aufrufe.append(d) or 200, {"code": 0, "ausgabe": "ok"}))
        self.server = await self.w.starten("127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close()
        self.docker.close()

    async def test_erlaubtes_anlegen_und_mehrere_anfragen_je_verbindung(self):
        gut = {"Image": "x", "HostConfig": {"Binds": ["workspace-a:/workspace:rw"]}}
        antworten = await _senden(
            self.port,
            _post("/v1.45/containers/create?name=a", gut),
            b"GET /v1.45/containers/json HTTP/1.1\r\nHost: docker\r\n\r\n",
            b"GET /v1.45/events HTTP/1.1\r\nHost: docker\r\n\r\n",
        )
        self.assertTrue(antworten[0][0].startswith(b"HTTP/1.1 201"))
        self.assertEqual(json.loads(json.loads(antworten[0][1])["koerper"]), gut, "Körper unverändert")
        self.assertIn(b"/containers/json", antworten[1][1])
        self.assertEqual(antworten[2][1], b'{"n": 0}{"n": 1}{"n": 2}', "chunked Strom durchgereicht")

    async def test_gesperrtes_anlegen_kommt_nie_bei_docker_an(self):
        for pfad in ("/v1.45/containers/create", "/containers/create", "//containers/create",
                     "/v1.45/containers/%63reate", "/v1.45/x/../containers/create"):
            with self.subTest(pfad):
                (kopf, inhalt), = await _senden(self.port, _post(pfad, {"HostConfig": {"Privileged": True}}))
                self.assertTrue(kopf.startswith(b"HTTP/1.1 403"), kopf)
                self.assertIn("Privileged", json.loads(inhalt)["message"])

    async def test_exec_und_volume(self):
        (k1, _), = await _senden(self.port, _post("/v1.45/containers/abc/exec", {"Cmd": ["sh"], "Privileged": True}))
        (k2, _), = await _senden(self.port, _post("/v1.45/volumes/create",
                                                 {"Name": "x", "DriverOpts": {"device": "/", "o": "bind"}}))
        (k3, _), = await _senden(self.port, _post("/v1.45/containers/abc/exec", {"Cmd": ["ls"]}))
        self.assertTrue(k1.startswith(b"HTTP/1.1 403"))
        self.assertTrue(k2.startswith(b"HTTP/1.1 403"))
        self.assertTrue(k3.startswith(b"HTTP/1.1 201"))

    async def test_umschaltung_auf_rohe_bytes(self):
        anfrage = _post("/v1.45/exec/x/start", {"Detach": False}).replace(
            b"Content-Type", b"Connection: Upgrade\r\nUpgrade: tcp\r\nContent-Type")
        (kopf, daten), = await _senden(self.port, anfrage, roh=b"hallo")
        self.assertTrue(kopf.startswith(b"HTTP/1.1 101"))
        self.assertEqual(daten, b"echo:hallo")

    async def test_chunked_anfrage_wird_geprueft(self):
        koerper = json.dumps({"HostConfig": {"Privileged": True}}).encode()
        anfrage = (b"POST /v1.45/containers/create HTTP/1.1\r\nHost: d\r\nTransfer-Encoding: chunked\r\n\r\n"
                   + f"{len(koerper):x}\r\n".encode() + koerper + b"\r\n0\r\n\r\n")
        (kopf, _), = await _senden(self.port, anfrage)
        self.assertTrue(kopf.startswith(b"HTTP/1.1 403"))

    async def test_mehrdeutige_anfragen_werden_abgelehnt(self):
        koerper = b'{"HostConfig": {"Privileged": true}}'
        faelle = [
            b"POST /containers/create HTTP/1.1\r\nContent-Length: 2\r\nContent-Length: 40\r\n\r\n" + koerper,
            b"POST /containers/create HTTP/1.1\r\nContent-Length: 40\r\nTransfer-Encoding: chunked\r\n\r\n" + koerper,
            b"POST /containers/create HTTP/1.1\r\nTransfer-Encoding: gzip, chunked\r\n\r\n" + koerper,
            b"POST /containers/create HTTP/1.1\r\nX: a\r\n Content-Length: 5\r\n\r\n" + koerper,
            b"POST /containers/create HTTP/1.1\r\nContent-Length : 40\r\n\r\n" + koerper,
            b"POST /containers/create HTTP/1.1\r\nContent-Length: +40\r\n\r\n" + koerper,
            b"POST /containers/create HTTP/1.1\r\nX: a\nContent-Length: 40\r\n\r\n" + koerper,
        ]
        for anfrage in faelle:
            with self.subTest(anfrage[:70]):
                (kopf, _), = await _senden(self.port, anfrage)
                self.assertTrue(kopf.startswith(b"HTTP/1.1 400"), kopf)

    async def test_start_mit_hostconfig(self):
        (k1, _), = await _senden(self.port, _post("/v1.24/containers/abc/start", {"HostConfig": {"Privileged": True}}))
        self.assertTrue(k1.startswith(b"HTTP/1.1 403"))
        (k2, _), = await _senden(self.port, b"POST /v1.45/containers/abc/start HTTP/1.1\r\nHost: d\r\nContent-Length: 0\r\n\r\n")
        self.assertTrue(k2.startswith(b"HTTP/1.1 201"))

    async def test_compose_nur_mit_schluessel(self):
        (k1, _), = await _senden(self.port, _post("/aie/compose", {"projekt": "agent-12345678-x"}))
        self.assertTrue(k1.startswith(b"HTTP/1.1 401"))
        anfrage = _post("/aie/compose", {"projekt": "agent-12345678-x"}).replace(
            b"Content-Type", b"X-Waechter-Token: " + b"s" * 40 + b"\r\nContent-Type")
        (k2, inhalt), = await _senden(self.port, anfrage)
        self.assertTrue(k2.startswith(b"HTTP/1.1 200"))
        self.assertEqual(json.loads(inhalt), {"code": 0, "ausgabe": "ok"})
        self.assertEqual(self.compose_aufrufe, [{"projekt": "agent-12345678-x"}])

    def test_pfad_wie_docker_ihn_sieht(self):
        self.assertEqual(proxy.art_der_anfrage("post", "/v1.45//containers/create?x=1"), "container")
        self.assertEqual(proxy.art_der_anfrage("POST", "/containers/abc/exec"), "exec")
        self.assertIsNone(proxy.art_der_anfrage("GET", "/containers/create"))
        self.assertEqual(proxy.art_der_anfrage("POST", "/containers/abc/start"), "start")
        self.assertIsNone(proxy.art_der_anfrage("POST", "/containers/abc/stop"))
        self.assertEqual(proxy.art_der_anfrage("POST", "http://docker/v1.45/volumes/create"), "volume")
        self.assertEqual(proxy.art_der_anfrage("POST", "/v1.45/containers/create%3Fx#y"), None)


if __name__ == "__main__":
    unittest.main()
