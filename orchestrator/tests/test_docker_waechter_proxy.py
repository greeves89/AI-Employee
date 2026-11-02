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
            if erste.startswith("GET /networks/"):
                name = {"apps": "ai-employee-apps-12345678", "dockerapi": "ai-employee_docker-api"}.get(
                    erste.split(" ")[1].split("/")[2])
                text = json.dumps({"Name": name}).encode()
                writer.write((b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(text)).encode() + b"\r\n\r\n" + text)
                             if name else b"HTTP/1.1 404 Not Found\r\nContent-Length: 2\r\n\r\n{}")
                await writer.drain()
                writer.close()
                return
            if erste.startswith("GET /containers/") and erste.split(" ")[1].endswith("/json"):
                name = erste.split(" ")[1].split("/")[2]
                inspect = {
                    "abc": {"Id": "a" * 64, "HostConfig": {}, "Config": {"Labels": {"ai-employee.type": "agent"}}},
                    "app1": {"Id": "b" * 64, "HostConfig": {}, "Config": {"Labels": {"com.docker.compose.project": "agent-12345678-x"}}},
                    "fremd": {"Id": "c" * 64, "HostConfig": {}, "Config": {"Labels": {}}},
                    "runner1": {"Id": "f" * 64, "HostConfig": {}, "Config": {"Labels": {"ai-employee.type": "runner"}}},
                    "hostpid": {"Id": "d" * 64, "HostConfig": {"PidMode": "host"}, "Config": {"Labels": {"ai-employee.type": "agent"}}},
                    "proxy": {"HostConfig": {"Binds": ["/var/run/docker.sock:/var/run/docker.sock:ro"]}, "Config": {}},
                    "heal": {"HostConfig": {}, "Mounts": [{"Source": "/var/run/docker.sock"}], "Config": {}},
                    "postgres": {"HostConfig": {}, "Config": {"Labels": {"com.docker.compose.project": "ai-employee"}}},
                    "cadvisor": {"HostConfig": {"Privileged": True}, "Config": {}},
                    "ai-employee-orchestrator": {"Id": "e" * 64, "Name": "/ai-employee-orchestrator",
                                                 "HostConfig": {}, "Config": {"Labels": {"com.docker.compose.project": "ai-employee"}}},
                }.get(name)
                if inspect is None:
                    writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 2\r\n\r\n{}")
                else:
                    text = json.dumps(inspect).encode()
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(text)).encode() + b"\r\n\r\n" + text)
                await writer.drain()
                writer.close()
                return
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

    async def test_docker_schliesst_ruhende_verbindung(self):
        """Der Socket-Proxy schliesst seine Seite nach einer Antwort (Ruhezeit abgelaufen).
        Die nächste Anfrage über dieselbe Client-Verbindung muss trotzdem eine Antwort
        bekommen — vorher schloss der Wächter dann wortlos (RemoteDisconnected im Client)."""
        async def einmal(reader, writer):
            kopf = await reader.readuntil(b"\r\n\r\n")
            text = json.dumps({"zeile": kopf.split(b"\r\n")[0].decode()}).encode()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(text)).encode() + b"\r\n\r\n" + text)
            await writer.drain()
            writer.close()
        docker = await asyncio.start_server(einmal, "127.0.0.1", 0)
        w = proxy.Waechter(("127.0.0.1", docker.sockets[0].getsockname()[1]), [], dienst_schluessel="s" * 40)
        server = await w.starten("127.0.0.1", 0)
        try:
            antworten = await _senden(
                server.sockets[0].getsockname()[1],
                b"GET /v1.45/_ping HTTP/1.1\r\nHost: docker\r\n\r\n",
                b"GET /v1.45/containers/json HTTP/1.1\r\nHost: docker\r\n\r\n",
            )
        finally:
            server.close()
            docker.close()
        self.assertEqual(len(antworten), 2)
        self.assertIn(b"/containers/json", antworten[1][1])

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

    async def test_rahmung_und_version_streng(self):
        koerper = b'{"HostConfig": {}}'
        faelle = [
            b"POST /containers/create HTTP/1.0\r\nContent-Length: 18\r\n\r\n" + koerper,
            b"POST /containers/create HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n12\n" + koerper + b"\r\n0\r\n\r\n",
            b"POST /containers/create HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n0x12\r\n" + koerper + b"\r\n0\r\n\r\n",
            b"POST /containers/abc/start HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n{}XX0\r\n\r\n",
        ]
        for anfrage in faelle:
            with self.subTest(anfrage[:60]):
                (kopf, _), = await _senden(self.port, anfrage)
                self.assertTrue(kopf.startswith(b"HTTP/1.1 400"), kopf)

    async def test_ungepruefter_chunked_koerper_kommt_mit_fester_laenge_an(self):
        anfrage = (b"POST /v1.45/containers/abc/kill HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n"
                   b"2\r\n{}\r\n0\r\n\r\n")
        (kopf, inhalt), = await _senden(self.port, anfrage)
        self.assertTrue(kopf.startswith(b"HTTP/1.1 201"), kopf)
        self.assertEqual(json.loads(inhalt)["koerper"], "{}")

    async def test_nicht_ascii_schluessel(self):
        for koerper in ('{"Ho\u017ftConfig": {"Privileged": true}}', '{"HostConfig": {"Privileged": true, "\u212aey": 1}}'):
            with self.subTest(koerper):
                text = koerper.encode()
                anfrage = b"POST /containers/create HTTP/1.1\r\nContent-Length: " + str(len(text)).encode() + b"\r\n\r\n" + text
                (kopf, _), = await _senden(self.port, anfrage)
                self.assertTrue(kopf.startswith(b"HTTP/1.1 403"), kopf)

    async def test_start_mit_hostconfig(self):
        (k1, _), = await _senden(self.port, _post("/v1.24/containers/abc/start", {"HostConfig": {"Privileged": True}}))
        self.assertTrue(k1.startswith(b"HTTP/1.1 403"))
        (k2, _), = await _senden(self.port, b"POST /v1.45/containers/abc/start HTTP/1.1\r\nHost: d\r\nContent-Length: 0\r\n\r\n")
        self.assertTrue(k2.startswith(b"HTTP/1.1 201"))

    async def test_zielcontainer(self):
        """Hinein nur in Agenten und ihre Apps — nie in Container mit Socket oder in Dienste der Anlage."""
        for name in ("proxy", "heal", "postgres", "cadvisor", "unbekannt", "fremd", "hostpid"):
            for anfrage in (_post(f"/v1.45/containers/{name}/exec", {"Cmd": ["sh"]}),
                            f"PUT /v1.45/containers/{name}/archive?path=/ HTTP/1.1\r\nContent-Length: 0\r\n\r\n".encode(),
                            f"GET /v1.45/containers/{name}/export HTTP/1.1\r\n\r\n".encode(),
                            _post(f"/v1.45/containers/{name}/update", {"Memory": 1})):
                with self.subTest(name=name, anfrage=anfrage[:40]):
                    (kopf, _), = await _senden(self.port, anfrage)
                    self.assertTrue(kopf.startswith(b"HTTP/1.1 403"), kopf)
        for name, kennung in (("abc", "a" * 64), ("app1", "b" * 64)):
            with self.subTest(name):
                (kopf, inhalt), = await _senden(self.port, _post(f"/v1.45/containers/{name}/exec?x=1", {"Cmd": ["ls"]}))
                self.assertTrue(kopf.startswith(b"HTTP/1.1 201"), kopf)
                self.assertEqual(json.loads(inhalt)["zeile"], f"POST /v1.45/containers/{kennung}/exec?x=1 HTTP/1.1",
                                 "weitergereicht an die geprüfte ID, nicht an den Namen")
        (kopf, _), = await _senden(self.port, _post("/v1.45/containers/proxy/rename?name=ai-agent-x", {}))
        self.assertTrue(kopf.startswith(b"HTTP/1.1 403"), "umbenennen nur bei Agenten")

    async def test_schreibweise_und_doppelte_schluessel(self):
        """Docker liest Feldnamen ohne Rücksicht auf die Schreibung."""
        for koerper in ({"hostconfig": {"privileged": True}}, {"HOSTCONFIG": {"PRIVILEGED": True}},
                        {"HostConfig": {"binds": ["/:/host"]}}):
            with self.subTest(koerper):
                (kopf, _), = await _senden(self.port, _post("/v1.45/containers/create", koerper))
                self.assertTrue(kopf.startswith(b"HTTP/1.1 403"), kopf)
        doppelt = b'{"HostConfig": {}, "hostconfig": {"Privileged": true}}'
        anfrage = b"POST /containers/create HTTP/1.1\r\nContent-Length: " + str(len(doppelt)).encode() + b"\r\n\r\n" + doppelt
        (kopf, inhalt), = await _senden(self.port, anfrage)
        self.assertTrue(kopf.startswith(b"HTTP/1.1 403"), kopf)
        self.assertIn("eindeutig", json.loads(inhalt)["message"])

    async def test_netz_verbinden(self):
        faelle = [("apps", "abc", 201), ("apps", "ai-employee-orchestrator", 201), ("dockerapi", "abc", 403),
                  ("apps", "proxy", 403), ("apps", "fremd", 403), ("unbekannt", "abc", 403)]
        for netz, container, erwartet in faelle:
            with self.subTest(netz=netz, container=container):
                (kopf, inhalt), = await _senden(self.port, _post(f"/v1.45/networks/{netz}/connect", {"Container": container}))
                self.assertTrue(kopf.startswith(f"HTTP/1.1 {erwartet}".encode()), kopf)
                if erwartet == 201:
                    self.assertNotIn(container, json.loads(json.loads(inhalt)["koerper"])["Container"],
                                     "weitergereicht an die ID")
        (kopf, _), = await _senden(self.port, _post("/v1.45/networks/apps/connect",
                                                    {"Container": "abc", "EndpointConfig": {"Aliases": ["redis"]}}))
        self.assertTrue(kopf.startswith(b"HTTP/1.1 403"))

    async def test_lebenszyklus_nur_fuer_agenten(self):
        def anfrage(methode, pfad):
            return f"{methode} {pfad} HTTP/1.1\r\nContent-Length: 0\r\n\r\n".encode()
        for name in ("postgres", "proxy", "heal", "fremd", "ai-employee-orchestrator"):
            for methode, pfad in (("DELETE", f"/v1.45/containers/{name}?force=1"), ("POST", f"/v1.45/containers/{name}/stop"),
                                  ("POST", f"/v1.45/containers/{name}/kill"), ("POST", f"/v1.45/containers/{name}/restart")):
                with self.subTest(name=name, pfad=pfad):
                    (kopf, _), = await _senden(self.port, anfrage(methode, pfad))
                    self.assertTrue(kopf.startswith(b"HTTP/1.1 403"), kopf)
        for name, kennung in (("abc", "a" * 64), ("app1", "b" * 64), ("runner1", "f" * 64)):
            with self.subTest(name):
                (kopf, inhalt), = await _senden(self.port, anfrage("DELETE", f"/v1.45/containers/{name}?force=1"))
                self.assertTrue(kopf.startswith(b"HTTP/1.1 201"), kopf)
                self.assertEqual(json.loads(inhalt)["zeile"], f"DELETE /v1.45/containers/{kennung}?force=1 HTTP/1.1")

    async def test_gesperrte_zugriffe(self):
        for methode, pfad in (("POST", "/v1.45/images/load"), ("POST", "/v1.45/images/ghcr.io/x/push"),
                              ("POST", "/v1.45/containers/prune"), ("POST", "/v1.45/volumes/prune"),
                              ("POST", "/v1.45/images/prune"), ("POST", "/v1.45/images/create?fromSrc=http://x/a.tar"),
                              ("POST", "/v1.45/images/create?FROMSRC=-"),
                              ("DELETE", "/v1.45/volumes/ai-employee_postgres_data"), ("DELETE", "/v1.45/volumes/egress_ca")):
            with self.subTest(pfad):
                (kopf, _), = await _senden(self.port, f"{methode} {pfad} HTTP/1.1\r\nContent-Length: 0\r\n\r\n".encode())
                self.assertTrue(kopf.startswith(b"HTTP/1.1 403"), kopf)
        for pfad in ("/v1.45/images/create?fromImage=alpine&tag=3.20", "/v1.45/volumes/workspace-2ad91565"):
            methode = "DELETE" if "volumes" in pfad else "POST"
            with self.subTest(pfad):
                (kopf, _), = await _senden(self.port, f"{methode} {pfad} HTTP/1.1\r\nContent-Length: 0\r\n\r\n".encode())
                self.assertTrue(kopf.startswith(b"HTTP/1.1 201"), kopf)

    async def test_feinschliff(self):
        def roh(methode, pfad, koerper=b""):
            return (f"{methode} {pfad} HTTP/1.1\r\nContent-Type: application/x-www-form-urlencoded\r\n"
                    f"Content-Length: {len(koerper)}\r\n\r\n").encode() + koerper
        faelle = [
            (roh("POST", "/v1.45/images/create", b"fromSrc=http://x/a.tar"), 403),
            (roh("DELETE", "/v1.45/images/ai-employee-agent:latest"), 403),
            (roh("DELETE", "/v1.45/networks/dockerapi"), 403),
            (roh("DELETE", "/v1.45/networks/apps"), 201),
            (roh("DELETE", "/v1.45/volumes/moderator-workspace-r1"), 201),
            (_post("/v1.45/networks/dockerapi/disconnect", {"Container": "abc"}), 403),
            (_post("/v1.45/networks/apps/disconnect", {"Container": "postgres"}), 403),
            (_post("/v1.45/networks/apps/disconnect", {"Container": "app1", "Force": True}), 201),
        ]
        for anfrage, erwartet in faelle:
            with self.subTest(anfrage[:60]):
                (kopf, _), = await _senden(self.port, anfrage)
                self.assertTrue(kopf.startswith(f"HTTP/1.1 {erwartet}".encode()), kopf)

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
