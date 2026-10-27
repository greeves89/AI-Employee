"""„Trennen“ trennt wirklich — und „Verbunden“ erscheint, sobald die Verbindung steht.

Bis 1.378.12 setzte der Stopp nur ein Attribut, das niemand las: der Socket lief
weiter, und die App zeigte nach „Trennen“ für immer „Verbinde…“.
"""

import asyncio
import json
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge  # noqa: E402
import tray_app  # noqa: E402

try:
    from websockets.asyncio.server import serve
except ImportError:  # websockets < 14
    serve = None


def _warten(bedingung, sekunden=5.0):
    ende = time.time() + sekunden
    while time.time() < ende:
        if bedingung():
            return True
        time.sleep(0.05)
    return False


@unittest.skipIf(serve is None, "braucht websockets >= 14")
class Trennen(unittest.TestCase):
    def setUp(self):
        self.bereit = threading.Event()
        self.ende = threading.Event()
        self.port = None
        threading.Thread(target=self._server, daemon=True).start()
        self.assertTrue(self.bereit.wait(5))
        for p in (patch.object(bridge.Bridge, "_ensure_dispatcher"),
                  patch.object(tray_app, "_bridge_module", return_value=bridge),
                  patch.object(tray_app, "token_frisch_halten")):
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        tray_app.stop_bridge()
        _warten(lambda: not tray_app.is_running())
        self.ende.set()

    def _server(self):
        async def handler(ws):
            await ws.recv()                                   # hello
            await ws.send(json.dumps({"type": "session_info", "session_id": "s1"}))
            await ws.wait_closed()

        async def main():
            async with serve(handler, "127.0.0.1", 0) as srv:
                self.port = srv.sockets[0].getsockname()[1]
                self.bereit.set()
                while not self.ende.is_set():
                    await asyncio.sleep(0.05)

        asyncio.run(main())

    def test_verbunden_dann_getrennt(self):
        cfg = {"url": f"http://127.0.0.1:{self.port}", "token": "t", "session": "s1"}
        self.assertTrue(tray_app.start_bridge(cfg))
        self.assertTrue(_warten(lambda: tray_app._main_status_parts()[0] == "Verbunden"),
                        tray_app._main_status_parts())

        tray_app.stop_bridge()
        self.assertTrue(_warten(lambda: not tray_app.is_running(), 3),
                        "Nach „Trennen“ muss die Verbindung wirklich enden")
        self.assertEqual(tray_app._main_status_parts()[0], "Nicht verbunden")

    def test_wieder_verbinden_nach_trennen(self):
        cfg = {"url": f"http://127.0.0.1:{self.port}", "token": "t", "session": "s1"}
        tray_app.start_bridge(cfg)
        self.assertTrue(_warten(lambda: tray_app._status == "connected"))
        tray_app.stop_bridge()
        self.assertTrue(_warten(lambda: not tray_app.is_running(), 3))
        tray_app.start_bridge(cfg)
        self.assertTrue(_warten(lambda: tray_app._main_status_parts()[0] == "Verbunden"))


if __name__ == "__main__":
    unittest.main()
