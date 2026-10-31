"""Netzwächter (scripts/netzwaechter.py): Regeln für Agenten und Agenten-Apps (P1-6).

Live auf dem Pi bestätigt (31.10.): Ein Agent erreichte Router, SSH des Pi und die
Agenten anderer Nutzer, ein Build den Router. Geprüft wird hier die Regelbildung —
eine reine Funktion, ohne Docker und ohne iptables.
"""

import importlib.util
import unittest
from pathlib import Path

_PFAD = Path(__file__).resolve().parents[2] / "scripts" / "netzwaechter.py"
_spec = importlib.util.spec_from_file_location("netzwaechter", _PFAD)
nw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nw)

SUBNETZE = {"ai-employee-network": "172.18.0.0/16", "ai-employee-apps-aaaaaaaa": "10.210.1.0/24",
            "agent-aaaaaaaa-todo_default": "10.210.2.0/24"}


ORCHESTRATOR = {"name": "ai-employee-orchestrator", "besitzer": None,
                "ips": {"ai-employee-network": "172.18.0.2", "ai-employee-apps-aaaaaaaa": "10.210.1.2"}}
AGENT_A = {"name": "ai-agent-a", "besitzer": "agent-aaaaaaaa",
           "ips": {"ai-employee-network": "172.18.0.3", "ai-employee-apps-aaaaaaaa": "10.210.1.3"}}
AGENT_B = {"name": "ai-agent-b", "besitzer": "agent-bbbbbbbb", "ips": {"ai-employee-network": "172.18.0.4"}}
APP_A = {"name": "agent-aaaaaaaa-todo-web-1", "besitzer": "agent-aaaaaaaa",
         "ips": {"ai-employee-apps-aaaaaaaa": "10.210.1.5", "agent-aaaaaaaa-todo_default": "10.210.2.2"}}


class BesitzerTests(unittest.TestCase):
    def test_app_ueber_den_projektnamen(self):
        self.assertEqual(nw.besitzer({"com.docker.compose.project": "agent-aaaaaaaa-projects-todo"}, []),
                         "agent-aaaaaaaa")

    def test_agent_ueber_label_und_kennung(self):
        self.assertEqual(nw.besitzer({"ai-employee.type": "agent"}, ["AGENT_ID=bbbbbbbb-1"]), "agent-bbbbbbbb")

    def test_plattform_ist_nicht_eingeschraenkt(self):
        for labels in ({}, {"com.docker.compose.project": "ai-employee"},
                       {"com.docker.compose.project": "future-app-hub"}):
            self.assertIsNone(nw.besitzer(labels, []))


class RegelTests(unittest.TestCase):

    def setUp(self):
        self.vor, self.host = nw.regeln([ORCHESTRATOR, AGENT_A, AGENT_B, APP_A], SUBNETZE, [])
        self.alles = "\n".join(self.vor + self.host)

    def test_orchestrator_bleibt_frei(self):
        """Er muss Exchange oder SSO im Firmennetz erreichen."""
        self.assertNotIn("172.18.0.2/32", self.alles)
        self.assertNotIn("10.210.1.2/32", self.alles)

    def test_privates_netz_und_host_gesperrt(self):
        for ip in ("172.18.0.3", "172.18.0.4", "10.210.1.5"):
            self.assertIn(f"-A AIE-NETZ -s {ip}/32 -d 192.168.0.0/16 -j DROP", self.vor)
            self.assertIn(f"-A AIE-NETZ-HOST -s {ip}/32 -j DROP", self.host)

    def test_eigene_netze_vor_der_sperre(self):
        """Plattform und eigene Apps liegen selbst in privaten Netzen — sie müssen
        VOR der Sperre erlaubt sein."""
        erlaubt = self.vor.index("-A AIE-NETZ -s 172.18.0.3/32 -d 172.18.0.0/16 -j RETURN")
        gesperrt = self.vor.index("-A AIE-NETZ -s 172.18.0.3/32 -d 172.16.0.0/12 -j DROP")
        self.assertLess(erlaubt, gesperrt)
        self.assertIn("-A AIE-NETZ -s 10.210.1.3/32 -d 10.210.1.0/24 -j RETURN", self.vor)

    def test_agenten_verschiedener_besitzer_sehen_sich_nicht(self):
        """Im Plattformnetz erreichte ein Agent sonst den Prüf-Hook eines fremden."""
        drop = "-A AIE-NETZ -s 172.18.0.3/32 -d 172.18.0.4/32 -j DROP"
        self.assertIn(drop, self.vor)
        self.assertIn("-A AIE-NETZ -s 172.18.0.4/32 -d 172.18.0.3/32 -j DROP", self.vor)
        # und zwar VOR der Freigabe des gemeinsamen Netzes
        self.assertLess(self.vor.index(drop),
                        self.vor.index("-A AIE-NETZ -s 172.18.0.3/32 -d 172.18.0.0/16 -j RETURN"))

    def test_agent_und_eigene_app_sehen_sich(self):
        self.assertNotIn("-s 10.210.1.3/32 -d 10.210.1.5/32 -j DROP", self.alles)
        self.assertNotIn("-s 10.210.1.5/32 -d 10.210.1.3/32 -j DROP", self.alles)

    def test_antworten_immer_zuerst(self):
        self.assertIn("ESTABLISHED,RELATED", self.vor[0])
        self.assertIn("ESTABLISHED,RELATED", self.host[0])

    def test_ausnahmen_vor_der_sperre(self):
        vor, _ = nw.regeln([AGENT_A], SUBNETZE, ["192.168.178.50/32"])
        erlaubt = vor.index("-A AIE-NETZ -s 172.18.0.3/32 -d 192.168.178.50/32 -j RETURN")
        self.assertLess(erlaubt, vor.index("-A AIE-NETZ -s 172.18.0.3/32 -d 192.168.0.0/16 -j DROP"))

    def test_ohne_eingeschraenkte_nur_die_antwortregel(self):
        vor, host = nw.regeln([ORCHESTRATOR], SUBNETZE, [])
        self.assertEqual(len(vor), 1)
        self.assertEqual(len(host), 1)


class StandardBridgeTests(unittest.TestCase):
    """Builds laufen im Standard-Bridge — ein Dockerfile erreichte sonst den Router
    (auf dem Pi gemessen: RUN wget 192.168.178.1 ging durch)."""

    def setUp(self):
        self.vor, self.host = nw.regeln([], SUBNETZE, [], standard="172.17.0.0/16", nameserver=["192.168.178.1"])

    def test_privates_netz_und_host_gesperrt(self):
        self.assertIn("-A AIE-NETZ -s 172.17.0.0/16 -d 192.168.0.0/16 -j DROP", self.vor)
        self.assertIn("-A AIE-NETZ-HOST -s 172.17.0.0/16 -j DROP", self.host)

    def test_dns_zum_router_bleibt_frei(self):
        """Builds fragen den Nameserver aus resolv.conf direkt — ohne DNS kein apt, kein npm."""
        dns = "-A AIE-NETZ -s 172.17.0.0/16 -d 192.168.178.1/32 -p udp --dport 53 -j RETURN"
        self.assertIn(dns, self.vor)
        self.assertLess(self.vor.index(dns), self.vor.index("-A AIE-NETZ -s 172.17.0.0/16 -d 192.168.0.0/16 -j DROP"))
        self.assertNotIn("-d 192.168.178.1/32 -j RETURN", "\n".join(self.vor), "nur Port 53, nicht der ganze Router")

    def test_nameserver_ohne_loopback(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("# kommentar\nnameserver 192.168.178.1\nnameserver 127.0.0.53\nnameserver fd00::1\nsearch fritz.box\n")
        self.assertEqual(nw.nameserver_lesen(Path(f.name)), ["192.168.178.1"])


class AusnahmenTests(unittest.TestCase):
    def test_nur_gueltige_netze(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("# SAP beim Kunden\n192.168.178.50\n10.0.5.0/24  # Drucker\nkein-netz\n\n")
        self.assertEqual(nw.ausnahmen_lesen(Path(f.name)), ["192.168.178.50/32", "10.0.5.0/24"])

    def test_ohne_datei_keine(self):
        self.assertEqual(nw.ausnahmen_lesen(Path("/gibt/es/nicht")), [])


if __name__ == "__main__":
    unittest.main()
