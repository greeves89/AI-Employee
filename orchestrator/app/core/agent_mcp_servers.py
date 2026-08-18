"""Die MCP-Server EINES Agenten — auswaehlen und aufrufen.

Warum es diese Datei gibt: die Sprachfront konnte die MCP-Server eines Agenten
nicht benutzen. Ihre Werkzeugliste stand von Hand im Quelltext (47 Konstanten),
und nichts darin holte jemals ein ``tools/list``. Ein Nutzer band am 18.08.2026
einen Server mit 32 Werkzeugen an seinen Agenten — die Sprachfront sah ihn
nicht, reichte den Auftrag per ``ask_agent`` weiter und der Nutzer musste ihr am
Ende selbst sagen, dass es das Werkzeug gibt.

Die Auswahl „welche Server gehoeren zu diesem Agenten" GAB es bereits, aber
eingebacken in ``AgentManager._get_custom_mcp_env``, wo sie Umgebungsvariablen
fuer den Container baut. Sie steht jetzt hier, damit beide Wege dieselbe
Auswahl treffen — inklusive der Gruppenrechte. Eine zweite Auswahl daneben
waere genau die Luecke, durch die ein gesperrter Server doch erreichbar wird.
"""

import json
import logging
from dataclasses import dataclass

from app.core.log_redaction import scrub_log
from app.models.mcp_server import McpServer
from app.core.encryption import decrypt_token
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

#: Wie viele Werkzeuge eine Sprachsitzung insgesamt deklarieren darf.
#:
#: „ALLES was unter MCP Tools drin ist MUSS der agent voice auch kennen"
#: (Nutzer, 18.08.2026) — und im selben Atemzug der richtige Einwand, dass die
#: Engine bei etwa 128 Werkzeugen dichtmacht.
#:
#: Beides zusammen geht nur so: was ins Budget passt, wird direkt deklariert;
#: alles Weitere bleibt ueber ``mcp_search_tools`` + ``mcp_call_tool``
#: erreichbar. Damit ist NICHTS unerreichbar, und die Grenze wird trotzdem
#: eingehalten. Eine stille Kuerzung waere genau das Verhalten, ueber das er
#: sich beschwert hat — nur an anderer Stelle.
WERKZEUG_BUDGET = 128


async def servers_for_agent(
    db: AsyncSession, agent_id: str | None, agent_config: dict | None = None
) -> list[McpServer]:
    """Die aktiven MCP-Server, die dieser Agent benutzen darf.

    Drei Filter, in dieser Reihenfolge: aktiviert, dem Agenten zugewiesen
    (``config["mcp_servers"]``, sonst alle), und was die Gruppenrechte des
    Besitzers zulassen (``mcp_server_ids``; ``None`` = alle, Administratoren
    ohne Schranke).
    """
    result = await db.execute(select(McpServer).where(McpServer.enabled == True))  # noqa: E712
    servers = list(result.scalars().all())

    if agent_config and "mcp_servers" in agent_config:
        zugewiesen = set(agent_config["mcp_servers"])
        servers = [s for s in servers if s.id in zugewiesen]

    if agent_id:
        try:
            from app.core.permissions import get_effective_permissions
            from app.models.agent import Agent
            from app.models.user import User

            agent = await db.get(Agent, agent_id)
            besitzer = await db.get(User, agent.user_id) if (agent and agent.user_id) else None
            if besitzer:
                rechte = await get_effective_permissions(besitzer, db)
                erlaubt = rechte.get("mcp_server_ids")
                if erlaubt is not None:
                    erlaubt_set = set(erlaubt)
                    servers = [s for s in servers if s.id in erlaubt_set]
        except Exception as e:  # noqa: BLE001 — ein Rechtefehler darf den Start nicht kippen
            logger.warning("MCP-Rechtefilter fuer Agent %s fehlgeschlagen: %s",
                           scrub_log(agent_id), scrub_log(e))

    return servers


@dataclass(frozen=True)
class MCPZiel:
    """Ein aufrufbarer MCP-Server, losgeloest von der Datenbanksitzung.

    Die Sprachsitzung laeuft minutenlang; die Sitzung, aus der die Server
    kamen, ist da laengst zu. Ein ORM-Objekt mitzuschleppen waere ein
    Detached-Fehler beim ersten Aufruf. Also alles, was gebraucht wird, einmal
    ausgelesen — Zugangsdaten inklusive, damit spaeter nichts mehr nachgeladen
    werden muss.
    """
    name: str
    url: str
    bearer: str | None
    kopfzeilen: dict[str, str]
    allow_private: bool


def _ziel(server: McpServer) -> MCPZiel:
    kopf = _kopfzeilen(server)
    bearer = kopf.pop("Authorization", "").removeprefix("Bearer ").strip() or None
    return MCPZiel(
        name=server.name,
        url=server.url,
        bearer=bearer,
        kopfzeilen=kopf,
        allow_private=bool(getattr(server, "allow_private_host", False)),
    )


def _kopfzeilen(server: McpServer) -> dict[str, str]:
    """Anmeldung fuer diesen Server — Bearer und/oder eigene Kopfzeilen."""
    kopf: dict[str, str] = {}
    if server.auth_token_encrypted:
        kopf["Authorization"] = f"Bearer {decrypt_token(server.auth_token_encrypted)}"
    if getattr(server, "headers_encrypted", None):
        try:
            kopf.update(json.loads(decrypt_token(server.headers_encrypted)))
        except Exception:  # noqa: BLE001 — ein kaputter Eintrag darf den Rest nicht mitnehmen
            logger.warning("Eigene Kopfzeilen von MCP-Server %s nicht lesbar", scrub_log(server.name))
    return kopf


def voice_toolspecs(
    servers: list[McpServer], budget: int | None = None
) -> tuple[list[dict], dict[str, tuple[MCPZiel, str]], list[dict]]:
    """Die Werkzeuge der Server als Nova-Sonic-Werkzeuge, plus Zustellplan.

    Die Beschreibungen liegen schon in ``McpServer.tools`` — beim Anbinden
    einmal per ``tools/list`` geholt und gespeichert. Die Sprachfront muss also
    beim Sitzungsstart nichts uebers Netz holen; sie liest, was ohnehin da ist.

    Rueckgabe, drei Stuecke:

    * ``werkzeuge`` — was direkt deklariert wird (bis ``budget``)
    * ``plan`` — ``Name -> (Ziel, Originalname)`` fuer **alle** Werkzeuge, auch
      die nicht deklarierten; darueber laeuft ``mcp_call_tool``
    * ``katalog`` — Name, Beschreibung und Dienst fuer **alle**, damit
      ``mcp_search_tools`` etwas zu durchsuchen hat

    Der Name wird nur dann umbenannt, wenn zwei Server denselben vergeben —
    sonst hoerte das Modell einen Namen und wir riefen den falschen Server.
    """
    werkzeuge: list[dict] = []
    plan: dict[str, tuple[MCPZiel, str]] = {}
    katalog: list[dict] = []
    vergeben: set[str] = set()
    platz = WERKZEUG_BUDGET if budget is None else max(0, budget)

    for server in servers:
        ziel = _ziel(server)
        for werkzeug in (server.tools or []):
            if not isinstance(werkzeug, dict):
                continue
            original = str(werkzeug.get("name") or "").strip()
            if not original:
                continue

            name = original
            if name in vergeben:
                # Zwei Server mit gleichem Werkzeugnamen: der zweite bekommt den
                # Servernamen davor. Bewusst NUR im Konfliktfall — sonst muesste
                # der Nutzer „projectplannerpro_list_projects" sagen.
                name = f"{_kurzname(server.name)}_{original}"[:64]
                if name in vergeben:
                    continue

            beschreibung = str(werkzeug.get("description") or original)[:900]
            schema = werkzeug.get("inputSchema") or {"type": "object", "properties": {}}

            # Aufrufbar ist ALLES — deklariert nur, was ins Budget passt.
            plan[name] = (ziel, original)
            katalog.append({"name": name, "dienst": server.name, "beschreibung": beschreibung})
            vergeben.add(name)

            if len(werkzeuge) < platz:
                werkzeuge.append({
                    "toolSpec": {
                        "name": name,
                        "description": f"[{server.name}] {beschreibung}",
                        "inputSchema": {"json": json.dumps(schema)},
                    }
                })

    if len(plan) > len(werkzeuge):
        logger.info(
            "[Sprache] %d von %d MCP-Werkzeugen direkt deklariert — der Rest bleibt "
            "ueber mcp_search_tools/mcp_call_tool erreichbar",
            len(werkzeuge), len(plan),
        )
    return werkzeuge, plan, katalog


def suche_im_katalog(katalog: list[dict], frage: str, grenze: int = 25) -> str:
    """Werkzeuge nach Name, Dienst und Beschreibung durchsuchen.

    Damit kommt das Modell auch an das heran, was nicht deklariert werden
    konnte — es fragt nach „Projekte", bekommt ``list_projects`` genannt und
    ruft es dann ueber ``mcp_call_tool`` auf.
    """
    begriffe = [t for t in (frage or "").lower().split() if t]
    if not begriffe:
        treffer = katalog[:grenze]
    else:
        def punkte(e: dict) -> int:
            heu = f"{e['name']} {e['dienst']} {e['beschreibung']}".lower()
            return sum(1 for t in begriffe if t in heu)
        bewertet = [(punkte(e), e) for e in katalog]
        treffer = [e for p, e in sorted(bewertet, key=lambda x: -x[0]) if p > 0][:grenze]

    if not treffer:
        return f"Kein Werkzeug gefunden zu: {frage}"
    return "\n".join(f"{e['name']} [{e['dienst']}]: {e['beschreibung'][:160]}" for e in treffer)


def _kurzname(servername: str) -> str:
    """Servername als Werkzeug-Namensteil: nur, was Nova als Namen akzeptiert."""
    sauber = "".join(c if (c.isalnum() or c == "_") else "_" for c in servername.lower())
    return sauber.strip("_")[:24] or "mcp"


async def call_agent_tool(ziel: MCPZiel, tool_name: str, arguments: dict) -> str:
    """Ein Werkzeug auf einem Fremdserver aufrufen und den Text zurueckgeben.

    Nutzt denselben Client wie die Administrator-Oberflaeche — samt
    URL-Pruefung gegen private Adressen. Ein eigener zweiter Aufrufweg haette
    diese Pruefung frueher oder spaeter nicht mehr gehabt.
    """
    from app.api.mcp_servers import _call_tool

    rpc = await _call_tool(
        ziel.url, tool_name, arguments, ziel.bearer, ziel.kopfzeilen or None,
        allow_private=ziel.allow_private,
    )
    return _text_aus_antwort(rpc)


def _text_aus_antwort(rpc: dict) -> str:
    """Aus der JSON-RPC-Antwort das machen, was man vorlesen kann."""
    if not isinstance(rpc, dict):
        return str(rpc)
    if "error" in rpc:
        fehler = rpc["error"]
        return f"Fehler vom MCP-Server: {fehler.get('message') if isinstance(fehler, dict) else fehler}"

    ergebnis = rpc.get("result")
    if not isinstance(ergebnis, dict):
        return json.dumps(ergebnis, ensure_ascii=False)[:4000]

    stuecke = []
    for teil in ergebnis.get("content") or []:
        if isinstance(teil, dict) and teil.get("type") == "text":
            stuecke.append(str(teil.get("text") or ""))
    if stuecke:
        return "\n".join(stuecke)[:4000]
    return json.dumps(ergebnis, ensure_ascii=False)[:4000]
