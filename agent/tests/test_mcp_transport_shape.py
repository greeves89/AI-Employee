"""Regressionstests fuer Issue #638: die eingebauten MCP-Server werden schrittweise
von einer Modul-Instanz auf eine Fabrik umgestellt, damit ein Prozess mehrere
gleichzeitige Laeufe bedienen kann.

Hintergrund: ein `Server` des MCP-SDK laesst sich nur an genau einen Transport
binden — ein zweites `connect()` wirft "Already connected to a transport". Ein
geteilter HTTP-Server braucht deshalb je Sitzung eine eigene Instanz.

Diese Tests pruefen die *Bauform* der Dateien, nicht ihr Laufzeitverhalten (dafuer
waere node noetig). Sie sollen verhindern, dass ein bereits umgestellter Server
unbemerkt auf die Singleton-Form zurueckfaellt.
"""
import re
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parents[1] / "mcp"

# Server, die bereits auf die Fabrik-Bauform umgestellt sind. Beim Umstellen
# weiterer Server gehoert der Name hier hinein.
#
# Schluessel = der Name, unter dem der Server registriert wird (main.py) und der
# im HTTP-Modus den Pfad `/mcp/<name>` bildet. Wert = der Dateiname ohne
# `-server.mjs`. Beides faellt auseinander, sobald ein Server anders heisst als
# seine Datei — `desktop` liegt in `computer-use-server.mjs`.
MIGRATED = {
    "read-logs": "read-logs",
    "desktop": "computer-use",
}


def _source(name: str) -> str:
    return (MCP_DIR / f"{MIGRATED[name]}-server.mjs").read_text(encoding="utf-8")


def test_transport_bootstrap_exists():
    assert (MCP_DIR / "_transport.mjs").is_file()


def test_bootstrap_defaults_to_stdio_without_port():
    """Ohne MCP_HTTP_PORT muss alles bleiben wie bisher — der Umbau ist abschaltbar."""
    src = (MCP_DIR / "_transport.mjs").read_text(encoding="utf-8")
    assert "MCP_HTTP_PORT" in src
    assert "StdioServerTransport" in src


def test_http_binds_loopback_only():
    """Die Server tragen die Container-Identitaet (AGENT_ID/AGENT_TOKEN); ueber
    Containergrenzen hinweg duerfte sie nicht geteilt werden."""
    src = (MCP_DIR / "_transport.mjs").read_text(encoding="utf-8")
    assert '"127.0.0.1"' in src
    assert "0.0.0.0" not in src


def test_migrated_servers_export_a_factory():
    for name in MIGRATED:
        src = _source(name)
        assert re.search(r"export function buildServer\(", src), (
            f"{name}: erwartet 'export function buildServer()'"
        )


def test_migrated_servers_have_no_module_level_instance():
    """Genau der Rueckfall, den dieser Test verhindern soll: eine Instanz auf
    Modulebene laesst sich nur einmal verbinden."""
    for name in MIGRATED:
        src = _source(name)
        assert not re.search(r"^const server = new Server\(", src, re.MULTILINE), (
            f"{name}: Modul-Instanz statt Fabrik"
        )


def test_migrated_servers_delegate_transport_choice():
    for name in MIGRATED:
        src = _source(name)
        assert "startServer(" in src, f"{name}: ruft startServer() nicht auf"
        assert "StdioServerTransport" not in src, (
            f"{name}: waehlt den Transport selbst statt ueber _transport.mjs"
        )


def test_migrated_servers_register_under_their_own_name():
    """Der Name in startServer() bildet den HTTP-Pfad `/mcp/<name>`. Weicht er vom
    Registrierungsnamen ab, laeuft der Server zwar an, ist aber unter dem Pfad,
    den der Agent aufruft, nicht erreichbar — ein stiller Fehlschlag."""
    for name in MIGRATED:
        src = _source(name)
        assert re.search(rf'startServer\(\s*"{re.escape(name)}"', src), (
            f"{name}: startServer() wird nicht mit genau diesem Namen aufgerufen"
        )


def test_migrated_servers_keep_no_mutable_state_on_module_level():
    """DIE Fehlerklasse, die der Umbau erzeugt — und die einzige, die stumm bleibt.

    Die Fabrik trennt nur, was IN ihr entsteht. Ein `let`/`var` auf Modulebene
    bleibt prozessweit geteilt, und `process lifetime` bedeutet nach dem Umbau
    nicht mehr "ein Lauf", sondern "der ganze Container".

    Konkret der Befund, der zu diesem Test fuehrte: `let pinnedSessionId` in
    computer-use-server.mjs haette Lauf A erlaubt, per `computer_use_session` die
    Klicks von Lauf B auf den Bildschirm des Nutzers umzulenken. Ein Test auf
    genau diese Variable haette nur diesen einen Fall gedeckt — geprueft wird
    deshalb die Form, damit der naechste umgestellte Server nicht dieselbe Falle
    neu aufstellt.

    `const` ist erlaubt: unveraenderliche Konstanten und Kataloge duerfen geteilt
    werden, genau dafuer liegen sie auf Modulebene.
    """
    for name in MIGRATED:
        src = _source(name)
        offenders = re.findall(r"^(?:let|var)\s+(\w+)", src, re.MULTILINE)
        assert offenders == [], (
            f"{name}: veraenderlicher Zustand auf Modulebene "
            f"({', '.join(offenders)}) — gehoert in den Abschluss von buildServer(), "
            "sonst teilen ihn alle gleichzeitigen Laeufe dieses Prozesses"
        )
