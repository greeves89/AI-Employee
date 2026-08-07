"""Ein Schreib-Schalter fuer ALLE Microsoft-Wege (Graph/M365 + on-prem Exchange).

Es gibt zwei Ebenen, und die obere gewinnt immer:

  1. ``msgraph_read_only`` — plattformweite Admin-Einstellung. Standard AN.
     Solange sie aktiv ist, sieht KEIN Agent ein Schreib-Werkzeug und kann auch
     keines aufrufen, egal was in seiner Konfiguration steht.
  2. ``msgraph_access`` / ``exchange_access`` pro Agent ("read" | "write") —
     greift nur, wenn der globale Schalter ausgeschaltet wurde.

Beide MCP-Transporte (``api/mcp_msgraph.py``, ``api/mcp_exchange.py``) fragen
ausschliesslich hier, damit es keinen zweiten Weg an der Regel vorbei gibt. Der
externe Transport fuer OpenWebUI (``api/mcp_msgraph_external.py``) ist ohnehin
fest lesend und ruft deshalb nur ``read_only_enabled`` fuer die Anzeige nicht auf.
"""

from app.config import settings

# Werte, die in einer Agenten-Konfiguration "darf schreiben" bedeuten.
_WRITE_VALUES = ("write", "read_write", "rw")


def read_only_enabled() -> bool:
    """Ist der plattformweite Nur-Lesen-Zwang fuer Microsoft aktiv? Standard: ja."""
    return bool(getattr(settings, "msgraph_read_only", True))


def write_enabled(agent_config: dict | None, access_key: str) -> bool:
    """Darf dieser Agent ueber den genannten Weg schreiben?

    ``access_key`` ist ``"msgraph_access"`` (M365/Graph) oder ``"exchange_access"``
    (on-prem Exchange). Ohne Konfiguration gilt "read" — lesend war schon immer der
    Standard, der globale Schalter macht ihn nur unumgehbar.
    """
    if read_only_enabled():
        return False
    return (agent_config or {}).get(access_key, "read") in _WRITE_VALUES
