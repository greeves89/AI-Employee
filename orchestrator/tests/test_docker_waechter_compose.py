"""Agenten-Apps im Docker-Wächter: Er prüft das Modell selbst, bevor ein Runner
mit Docker-Socket entsteht — ein „schon geprüftes“ Modell vom Orchestrator zählt nicht."""

import unittest
from dataclasses import asdict
from unittest.mock import patch

from app.core import compose_pruefung as cp
from app.docker_waechter import compose

QUELLE = asdict(cp.fuer_agent("2ad91565abcdef00", "workspace-2ad91565"))
PROJEKT = "agent-2ad91565-meineapp"
GUT = {"name": "meineapp", "services": {"web": {"image": "nginx:1.27", "networks": {"default": None}}},
       "networks": {"default": {"name": "meineapp_default"}}}


def _anfrage(**ueber):
    daten = {"quelle": QUELLE, "projekt": PROJEKT, "modell": GUT, "befehl": ["up", "-d", "--build"]}
    daten.update(ueber)
    return daten


class ComposeImWaechter(unittest.TestCase):
    def setUp(self):
        self.lauf = patch.object(cp, "ausfuehren_geprueft", return_value=(0, "gestartet"))
        self.ausgefuehrt = self.lauf.start()

    def tearDown(self):
        self.lauf.stop()

    def test_gutes_modell_wird_geprueft_und_ausgefuehrt(self):
        status, antwort = compose.bearbeiten(None, _anfrage())
        self.assertEqual((status, antwort), (200, {"code": 0, "ausgabe": "gestartet"}))
        _, quelle, projekt, modell, befehl = self.ausgefuehrt.call_args.args
        self.assertEqual(projekt, PROJEKT)
        self.assertEqual(modell, cp.pruefen(GUT, quelle, PROJEKT)[0], "ausgeführt wird das GEPRÜFTE Modell")

    def test_gefaehrliches_modell_kommt_nie_zum_runner(self):
        for dienst in ({"image": "x", "privileged": True},
                       {"image": "x", "volumes": [{"type": "bind", "source": "/", "target": "/host"}]},
                       {"image": "x", "network_mode": "host"},
                       {"image": "x", "cap_add": ["SYS_ADMIN"]},
                       {"image": "x", "pid": "host"}):
            with self.subTest(dienst):
                modell = {"name": "x", "services": {"boese": dienst}}
                status, antwort = compose.bearbeiten(None, _anfrage(modell=modell))
                self.assertEqual(status, 422)
                self.assertTrue(antwort["verstoesse"])
        self.ausgefuehrt.assert_not_called()

    def test_quelle_projekt_und_befehl_werden_geprueft(self):
        faelle = [
            dict(quelle={**QUELLE, "praefix": "x-"}),
            dict(quelle={**QUELLE, "sicht": "/"}),
            dict(quelle={**QUELLE, "sicht": "/etc"}),
            dict(quelle={**QUELLE, "teilpfad": "../../etc"}),
            dict(quelle={**QUELLE, "volume": "../x"}),
            dict(projekt="agent-ffffffff-fremd"),
            dict(projekt="ai-employee"),
            dict(befehl=["run", "--rm", "x"]),
            dict(befehl=["up", "-d", "--build", "--privileged"]),
            dict(modell=None),
            dict(modell="kein dict"),
        ]
        for fall in faelle:
            with self.subTest(fall):
                status, _ = compose.bearbeiten(None, _anfrage(**fall))
                self.assertEqual(status, 400)
        self.ausgefuehrt.assert_not_called()

    def test_down_ohne_modell(self):
        status, _ = compose.bearbeiten(None, _anfrage(modell=None, befehl=["down"]))
        self.assertEqual(status, 200)

    def test_taskforce_quelle(self):
        quelle = asdict(cp.Quelle(volume="ai-employee-shared", sicht="/shared", praefix="agent-2ad91565-",
                                  teilpfad="nutzer/u1", bereich="/shared/taskforce/r1", ordner_erlaubt=False))
        self.assertIsInstance(compose.quelle_aus(quelle), cp.Quelle)
        self.assertIsInstance(compose.quelle_aus({**quelle, "bereich": "/workspace/x"}), str)


class OrchestratorSchicktUngeprueft(unittest.TestCase):
    """Mit Wächter geht das UNgeprüfte Modell hin; ohne läuft dieselbe Prüfung lokal."""

    def test_ohne_waechter_lokal_geprueft(self):
        q = cp.fuer_agent("2ad91565abcdef00", "workspace-2ad91565")
        with patch.dict("os.environ", {}, clear=False), \
                patch.object(cp, "ausfuehren_geprueft", return_value=(0, "")) as lauf:
            import os
            os.environ.pop("DOCKER_WAECHTER_TOKEN", None)
            cp.ausfuehren(None, q, PROJEKT, GUT, ["up", "-d", "--build"])
            self.assertEqual(lauf.call_args.args[3], cp.pruefen(GUT, q, PROJEKT)[0])
            with self.assertRaises(cp.ComposeAbgelehnt):
                cp.ausfuehren(None, q, PROJEKT, {"name": "x", "services": {"b": {"image": "x", "privileged": True}}},
                              ["up", "-d", "--build"])

    def test_mit_waechter_geht_es_dorthin(self):
        q = cp.fuer_agent("2ad91565abcdef00", "workspace-2ad91565")

        class Antwort:
            status_code = 422
            headers = {"content-type": "application/json"}
            text = ""

            @staticmethod
            def json():
                return {"verstoesse": ["Dienst „b“: privileged ist gesperrt."]}

        with patch.dict("os.environ", {"DOCKER_WAECHTER_TOKEN": "t" * 40, "DOCKER_HOST": "tcp://docker-waechter:2375"}), \
                patch("httpx.post", return_value=Antwort()) as post, \
                patch.object(cp, "ausfuehren_geprueft") as lokal:
            code, text = cp.ausfuehren(None, q, PROJEKT, GUT, ["up", "-d", "--build"])
        lokal.assert_not_called()
        self.assertEqual(post.call_args.args[0], "http://docker-waechter:2375/aie/compose")
        self.assertEqual(post.call_args.kwargs["json"]["modell"], GUT, "ungeprüft übergeben")
        self.assertEqual(code, 1)
        self.assertIn("privileged", text)


if __name__ == "__main__":
    unittest.main()
