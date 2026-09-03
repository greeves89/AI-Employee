"""Die Tray-App muss JEDE serverseitige Capability-Gruppe anbieten.

Befund 2026-08-18: ``input_capture`` und ``voice_capture`` existierten
serverseitig seit ihrer Einfuehrung, standen aber nicht in der ``CAPABILITY_META``
der Tray-App — der Nutzer konnte sie also gar nicht einschalten. Eine Gruppe, die
nur der Server kennt, ist tote Funktionalitaet; eine, die nur die Tray-App kennt,
laesst sich einschalten und wird beim naechsten Befehl mit 403 abgelehnt.

Beide Seiten werden aus der QUELLE gelesen — eine gepflegte Kopie haette denselben
Fehler nur an einer dritten Stelle wiederholt.
"""

import re
import unittest
from pathlib import Path

from app.api.computer_use import CAPABILITY_GROUPS

TRAY = Path(__file__).resolve().parents[2] / "computer-use-bridge/tray_app.py"


def _tray_capability_ids() -> set[str]:
    src = TRAY.read_text(encoding="utf-8")
    block = src.split("CAPABILITY_META = [", 1)[1].split("]", 1)[0]
    return set(re.findall(r'"id":\s*"([a-z_]+)"', block))


class TrayCapabilityParityTests(unittest.TestCase):
    def test_tray_offers_every_server_group(self):
        missing = sorted(set(CAPABILITY_GROUPS) - _tray_capability_ids())
        self.assertEqual(
            missing, [],
            "Diese Gruppen kennt der Server, aber die Tray-App bietet sie nicht "
            "an — der Nutzer kann sie nicht einschalten: " + ", ".join(missing),
        )

    def test_tray_offers_nothing_the_server_rejects(self):
        unknown = sorted(_tray_capability_ids() - set(CAPABILITY_GROUPS))
        self.assertEqual(
            unknown, [],
            "Diese Gruppen bietet die Tray-App an, der Server kennt sie nicht — "
            "Einschalten fuehrt zu 422/403: " + ", ".join(unknown),
        )

    def test_scope_lists_are_sent_to_the_server(self):
        """Der eigentliche Fehler hinter ``allowed_paths``: im Dialog gepflegt,
        lokal gespeichert, nie uebertragen. Fuer Anwendungen und Adressen muss
        der Weg zum Server nachweisbar existieren."""
        src = TRAY.read_text(encoding="utf-8")
        self.assertIn("allowed_apps", src)
        self.assertIn("allowed_domains", src)
        self.assertIn("_scope_payload", src,
                      "Freigabelisten muessen ueber _scope_payload an die "
                      "Capabilities-PATCH angehaengt werden")
        # Und zwar an der Stelle, die tatsaechlich sendet.
        patch_fn = src.split("def api_update_capabilities(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("_scope_payload", patch_fn)


if __name__ == "__main__":
    unittest.main()
