"""Den MCP-Modus fuer einen Test FESTNAGELN, statt ihn zu erben.

``max_concurrent_runs`` liest ``MCP_HTTP_PORT``, ``PIDS_RESERVE`` und
``PIDS_COST_PER_RUN`` selbst aus der Umgebung — genau das war die Reparatur zu
#326, denn nur so benutzen Produktion und Test dieselbe Aufrufform. Der Preis:
jeder Test, der ueber die Rechnung urteilt, haengt jetzt an diesen drei
Variablen. Im Agent-Container sind sie gesetzt, auf dem Bau-Rechner nicht.

Ein Test, dessen Erwartung davon abhaengt, WO er laeuft, beweist nichts — und
genau diese Sorte hat den Fehler jahrelang verdeckt. Deshalb liegt das Pinnen
hier an EINER Stelle und nicht als Kopie in jeder Testdatei.

``gemeinsam=True`` stellt einen ECHTEN ``/health``-Dienst hin, nicht nur die
Variable: seit ``_gemeinsamer_mcp_modus`` die Anlage fragt statt der Absicht zu
glauben, waere eine blosse Variable der Einzelprozess-Modus. Ein Helfer, der das
verschwiege, wuerde genau den fail-open-Fall zum Soll-Verhalten erklaeren.
"""

import contextlib
import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

#: Alles, was die Rechnung von aussen beeinflussen kann.
MCP_UMGEBUNGSVARIABLEN = ("MCP_HTTP_PORT", "PIDS_RESERVE", "PIDS_COST_PER_RUN")

#: Die Routen eines vollstaendig hochgefahrenen Sammelprozesses, wie
#: ``agent/mcp/_all.mjs`` sie meldet.
ALLE_ROUTEN = [
    "bash-approval", "memory", "notifications", "orchestrator", "skills",
    "desktop", "hyperframes", "email", "brain", "read-logs", "msgraph",
]


class Sammelprozess:
    """Ein echter ``/health``-Dienst — dieselbe Antwort wie ``_all.mjs``.

    Kein Doppel fuer die Sonde, sondern ein Gegenueber: die Sonde soll genau
    den Weg gehen, den sie in der Produktion geht.
    """

    def __init__(self, routen=None, kaputt: bool = False):
        self.routen = list(ALLE_ROUTEN) if routen is None else list(routen)
        self.kaputt = kaputt

    def __enter__(self) -> int:
        routen, kaputt = self.routen, self.kaputt

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if kaputt:
                    self.send_response(500)
                    self.end_headers()
                    return
                koerper = json.dumps({"ok": True, "servers": routen}).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(koerper)))
                self.end_headers()
                self.wfile.write(koerper)

            def log_message(self, *a):
                pass

        self._srv = HTTPServer(("127.0.0.1", 0), Handler)
        self._t = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._t.start()
        return self._srv.server_address[1]

    def __exit__(self, *_):
        self._srv.shutdown()
        self._srv.server_close()
        self._t.join(timeout=5)


class StummerPort:
    """Offen, aber niemand spricht HTTP — der Fall, den ein blosses ``connect``
    nicht von einem gesunden Dienst unterscheiden kann."""

    def __enter__(self) -> int:
        self._s = socket.socket()
        self._s.bind(("127.0.0.1", 0))
        self._s.listen(1)
        return self._s.getsockname()[1]

    def __exit__(self, *_):
        self._s.close()


def toter_port() -> int:
    """Eine Portnummer, auf der sicher niemand lauscht."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def mcp_modus(gemeinsam: bool, routen=None):
    """``gemeinsam=True``: ein Sammelprozess laeuft WIRKLICH und meldet Routen."""
    with contextlib.ExitStack() as stapel:
        stapel.enter_context(mock.patch.dict(os.environ, {}, clear=False))
        for name in MCP_UMGEBUNGSVARIABLEN:
            os.environ.pop(name, None)
        if gemeinsam:
            port = stapel.enter_context(Sammelprozess(routen))
            os.environ["MCP_HTTP_PORT"] = str(port)
        yield
