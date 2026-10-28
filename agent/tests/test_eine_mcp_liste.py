"""Die eingebauten MCP-Server duerfen nicht in zwei Listen auseinanderlaufen.

Vorgeschichte: Am 22.09.2026 meldete ein Agent dem Nutzer, Orchestrator,
Skills und Memory seien "nicht verbunden". Sie waren es -- alle zehn
antworteten. Gelesen hatte er die Warnungen von ``claude mcp list``, und die
entstanden, weil dieselben Server unter zwei verschiedenen Endpunkten bekannt
waren: Die ``.mcp.json`` schrieb noch die Einzelprozess-Variante, waehrend die
Anmeldung laengst ueber den gemeinsamen Prozess lief (#638).

Dahinter lag ein aelterer Befund: Die Liste der eingebauten Server wurde
MEHRFACH getippt, und eine Kopie hing vier Server hinterher (``hyperframes``,
``email``, ``brain``, ``read-logs``). Auffallen konnte das nicht, weil jede
Liste fuer sich funktionierte.

#822 (v1.328.4) hat die Inhalte in Ordnung gebracht. Zwei getippte Listen
bleiben es aber: ``_COMBINED_MCP_NAMES`` fuer die Anmeldung ueber den
gemeinsamen Prozess und ``builtin_servers`` fuer die Einzelprozesse. Dieser
Test haelt sie zusammen -- er repariert nichts, er laesst das naechste
Auseinanderlaufen sofort auffliegen, statt es Monate spaeter beim Nutzer
ankommen zu lassen.
"""

import unittest
from unittest import mock

from app import main


def _stdio_namen() -> set[str]:
    """Die Namen aus dem Einzelprozess-Zweig -- gebaut, nicht abgelesen.

    ``builtin_servers`` steht innerhalb von ``register_mcp_servers``. Statt den
    Quelltext zu durchsuchen (was nur Zeichenketten zaehlt), wird die Funktion
    wirklich ausgefuehrt und mitgeschrieben, was sie anzumelden versucht.
    """
    gesehen: set[str] = set()

    def _mitschreiben(args) -> bool:
        # Einzelprozess-Aufrufe sehen aus wie: [..., "--", name, "node", pfad]
        if "--" in args:
            gesehen.add(args[args.index("--") + 1])
        return True

    with mock.patch.object(main, "_run_mcp_add", _mitschreiben), \
         mock.patch.object(main, "_start_combined_mcp", lambda *_: False), \
         mock.patch.object(main, "_write_mcp_json_fallback", lambda *a, **k: None), \
         mock.patch.dict("os.environ", {"MCP_HTTP_PORT": "", "MSGRAPH_ENABLED": "",
                                        "COMPUTER_USE_BROWSER": "", "CUSTOM_MCP_SERVERS": ""},
                         clear=False):
        main.register_mcp_servers()
    return gesehen


def _all_mjs_namen() -> set[str]:
    """Die DRITTE Liste: ``agent/mcp/_all.mjs`` (gemeinsamer Prozess).

    Sie hing bis #727 frei. Steht ein Name in ``_COMBINED_MCP_NAMES``, fehlt
    aber hier, dann meldet Claude Code eine Adresse an, die niemand bedient —
    genau die Vorgeschichte, die dieser Test oben beschreibt, nur eine Ebene
    tiefer.
    """
    import re
    from pathlib import Path

    quelle = (Path(__file__).resolve().parents[1] / "mcp/_all.mjs").read_text()
    block = quelle[quelle.index("const kandidaten"):quelle.index("];", quelle.index("const kandidaten"))]
    namen = set(re.findall(r'\[\s*"([a-z0-9-]+)"', block))
    # msgraph wird im Modul nur unter einer Bedingung angehaengt.
    if 'kandidaten.push(["msgraph"' in quelle:
        namen.add("msgraph")
    return namen


class EineListeTest(unittest.TestCase):

    def test_beide_listen_sind_nicht_leer(self):
        """Sonst vergleicht der eigentliche Test zwei leere Mengen."""
        self.assertGreaterEqual(len(main._COMBINED_MCP_NAMES), 6)
        self.assertGreaterEqual(len(_stdio_namen()), 6)

    def test_der_gemeinsame_prozess_bedient_jeden_angemeldeten_namen(self):
        """``_COMBINED_MCP_NAMES`` ist die ANMELDUNG, ``_all.mjs`` die
        BEDIENUNG. Laufen sie auseinander, zeigt der Agent ein Werkzeug an, das
        ins Leere greift."""
        angemeldet = set(main._COMBINED_MCP_NAMES)
        bedient = _all_mjs_namen()
        self.assertGreaterEqual(len(bedient), 6, "Parser von _all.mjs liefert nichts")
        fehlt = angemeldet - bedient
        self.assertEqual(
            fehlt, set(),
            "Diese Server werden angemeldet, aber im gemeinsamen Prozess nicht "
            "geladen: " + ", ".join(sorted(fehlt)),
        )

    def test_beide_wege_kennen_dieselben_server(self):
        kombi = set(main._COMBINED_MCP_NAMES)
        einzeln = _stdio_namen()

        nur_kombi = kombi - einzeln
        nur_einzeln = einzeln - kombi
        self.assertEqual(
            (nur_kombi, nur_einzeln), (set(), set()),
            "Die beiden Listen der eingebauten MCP-Server laufen auseinander.\n"
            f"  nur im gemeinsamen Prozess: {sorted(nur_kombi)}\n"
            f"  nur als Einzelprozess:      {sorted(nur_einzeln)}\n"
            "Genau so hing die Liste schon einmal vier Server hinterher — und der "
            "Agent meldete dem Nutzer daraufhin einen Ausfall, den es nicht gab.",
        )


if __name__ == "__main__":
    unittest.main()
