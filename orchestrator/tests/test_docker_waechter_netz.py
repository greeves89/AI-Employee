"""Netztrennung des Docker-Wächters in docker-compose.yml (P1-13): Den Socket-Proxy
erreicht nur der Wächter, der Orchestrator spricht nur mit dem Wächter, und der
Wächter selbst hat weder Docker-Socket noch Code vom Host."""

import unittest
from pathlib import Path

import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"


class Netztrennung(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dienste = yaml.safe_load(COMPOSE.read_text())["services"]
        cls.netze = yaml.safe_load(COMPOSE.read_text())["networks"]

    def test_socket_proxy_nur_im_docker_netz(self):
        self.assertEqual(self.dienste["docker-socket-proxy"]["networks"], ["docker-api"])
        self.assertTrue(self.netze["docker-api"].get("internal"))
        mitglieder = [n for n, d in self.dienste.items() if "docker-api" in (d.get("networks") or [])]
        self.assertEqual(sorted(mitglieder), ["docker-socket-proxy", "docker-waechter"])

    def test_orchestrator_spricht_nur_mit_dem_waechter(self):
        env = self.dienste["orchestrator"]["environment"]
        self.assertEqual(env["DOCKER_HOST"], "tcp://docker-waechter:2375")
        self.assertIn("DOCKER_WAECHTER_TOKEN", env)
        self.assertNotIn("docker-socket-proxy", self.dienste["orchestrator"].get("depends_on", {}))

    def test_waechter_ohne_socket_ohne_code_vom_host_ohne_rechte(self):
        w = self.dienste["docker-waechter"]
        self.assertNotIn("volumes", w)
        self.assertEqual(w["image"], self.dienste["orchestrator"]["image"], "dasselbe signierte Abbild")
        self.assertTrue(w.get("read_only"))
        self.assertEqual(w.get("cap_drop"), ["ALL"])
        self.assertIn("no-new-privileges:true", w.get("security_opt", []))
        self.assertNotIn("ports", w)

    def test_niemand_sonst_haengt_den_socket_ein(self):
        mit_socket = sorted(n for n, d in self.dienste.items()
                            if any("docker.sock" in str(v) for v in d.get("volumes") or []))
        self.assertEqual(mit_socket, ["autoheal", "docker-socket-proxy"])


if __name__ == "__main__":
    unittest.main()
