"""#477 (Voice-Pfad): Knoteninhalt vorlesen + Verbindungen auflisten.

Kundenfeedback eines Kunden: die Graph-Navigation per Sprache funktioniert,
aber (1) der Inhalt eines angesprungenen Punktes wird nicht vorgelesen und (2) „womit
hängt dieser Punkt zusammen?" wird nicht beantwortet. Für die personal-KB deckt das
``_wikilink_neighbours`` ab; der Voice-Agent arbeitet aber über die gemounteten
Second-Brain-VAULTS. Dort fehlten die Werkzeuge ``read_brain`` und ``brain_connections``.

Beide Voice-Handler bauen auf ``vault.build_graph`` (Titel/Pfad -> Knoten, Kanten aus
``[[wikilinks]]``/``.md``-Links) und ``vault.read_file`` auf. Dieser Test verifiziert
genau diese Kern-Ableitung über einen echten temporären Vault — ohne DB/FastAPI, damit
er überall läuft, wo der Vault gemountet ist (dieselbe Regel, die der Graph ZEICHNET).
"""

import os
import tempfile
import unittest

from app.core import vault


def _resolve(graph: dict, note: str):
    """Spiegelt die Titel/Pfad-Auflösung aus RealtimeVoiceSession._resolve_brain_note."""
    ql = (note or "").strip().lower().replace("\\", "/")
    qstem = ql.rsplit("/", 1)[-1]
    if qstem.endswith(".md"):
        qstem = qstem[:-3]
    best = None
    for node in graph.get("nodes") or []:
        name_l = str(node.get("name") or "").lower()
        path_l = str(node.get("path") or "").lower().replace("\\", "/")
        path_stem = path_l.rsplit("/", 1)[-1]
        if path_stem.endswith(".md"):
            path_stem = path_stem[:-3]
        if name_l == ql or path_stem == qstem or path_l == ql:
            rank = 0
        elif ql and (ql in name_l or ql in path_l):
            rank = 1
        elif qstem and (qstem in name_l or qstem in path_stem):
            rank = 2
        else:
            continue
        if best is None or rank < best[0]:
            best = (rank, node)
    return best[1] if best else None


def _connections(graph: dict, rel: str):
    """Spiegelt die Kanten-Ableitung aus RealtimeVoiceSession._brain_connections."""
    name_by_rel = {
        str(n.get("path")): str(n.get("name") or n.get("path"))
        for n in (graph.get("nodes") or [])
    }
    outgoing, incoming = [], []
    for e in graph.get("edges") or []:
        if str(e.get("source")) == rel:
            outgoing.append(name_by_rel.get(str(e.get("target")), str(e.get("target"))))
        elif str(e.get("target")) == rel:
            incoming.append(name_by_rel.get(str(e.get("source")), str(e.get("source"))))
    return sorted(set(outgoing)), sorted(set(incoming))


class VoiceBrainReadConnectionsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, "Kardiologie"), exist_ok=True)
        self._write("Sepsis.md", "# Sepsis\nSiehe [[Antibiotika]] und [[Blutkultur]].\n")
        self._write("Antibiotika.md", "# Antibiotika\nWird bei [[Sepsis]] eingesetzt.\n")
        self._write("Blutkultur.md", "# Blutkultur\nDiagnostik ohne Links.\n")
        self.graph = vault.build_graph(self.dir)

    def _write(self, rel, content):
        with open(os.path.join(self.dir, rel), "w", encoding="utf-8") as fh:
            fh.write(content)

    def test_resolve_by_title_case_insensitive(self):
        node = _resolve(self.graph, "sepsis")
        self.assertIsNotNone(node)
        self.assertEqual(node["path"], "Sepsis.md")

    def test_resolve_by_path_with_extension(self):
        node = _resolve(self.graph, "Antibiotika.md")
        self.assertEqual(node["name"], "Antibiotika")

    def test_read_full_content_not_snippet(self):
        node = _resolve(self.graph, "Sepsis")
        text = vault.read_file(self.dir, node["path"])
        # read_brain gibt den GANZEN Text, nicht nur einen Schnipsel wie search_brain.
        self.assertIn("Antibiotika", text)
        self.assertIn("Blutkultur", text)

    def test_connections_outgoing_and_incoming(self):
        node = _resolve(self.graph, "Sepsis")
        out, inc = _connections(self.graph, node["path"])
        self.assertEqual(out, ["Antibiotika", "Blutkultur"])  # verweist auf
        self.assertEqual(inc, ["Antibiotika"])                # wird erwähnt von

    def test_node_without_links_reports_no_connections(self):
        node = _resolve(self.graph, "Blutkultur")
        out, inc = _connections(self.graph, node["path"])
        self.assertEqual(out, [])
        self.assertEqual(inc, ["Sepsis"])  # nur eingehend, weil Sepsis darauf verweist

    def test_unknown_note_resolves_to_none(self):
        self.assertIsNone(_resolve(self.graph, "Nichtvorhanden"))


if __name__ == "__main__":
    unittest.main()
