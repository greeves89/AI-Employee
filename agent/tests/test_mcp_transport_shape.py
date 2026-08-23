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
MIGRATED = ["read-logs"]


def _source(name: str) -> str:
    return (MCP_DIR / f"{name}-server.mjs").read_text(encoding="utf-8")


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
