"""Egress-Proxy: Keys verlassen die Plattform erst am Ausgang.

Bisher bekam ein Agent jeden zugewiesenen Key als Klartext in seine Umgebung.
Ein fehlgeleiteter Agent konnte ihn ausgeben, in eine Datei schreiben oder an ein
beliebiges Ziel schicken.

Mit eingeschaltetem Proxy (``settings.egress_proxy_enabled``):

* Ein Key mit ``erlaubte_hosts`` kommt nur als **Platzhalter** in den Agenten
  (``platzhalter``). Ohne erlaubte Ziele bleibt es beim Klartext — den Rest
  zeigt der Sicherheitsstatus.
* Der Agent geht fuer HTTPS ueber den Proxy (``agent_umgebung``), angemeldet mit
  seiner Kennung und seinem Agenten-Token.
* Der Proxy (``egress/aie_egress.py``) bricht TLS nur fuer Ziele auf, an die einer
  der Keys dieses Agenten gehen darf; alles andere laeuft als Tunnel durch.
* Findet er einen Platzhalter, fragt er hier nach (``api/egress.py``). Der Wert
  kommt nur zurueck, wenn der Key dem Agenten zusteht UND das Ziel erlaubt ist —
  dieselbe Auswahl wie beim Einspielen (``secret_zugriff.secrets_des_agenten``).

Der Platzhalter traegt eine Signatur aus ``api_secret_key``: Ein Agent kann sich
keinen fuer einen fremden Key ausdenken, und der Proxy erkennt ihn ohne Liste.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
from urllib.parse import urlsplit

from app.config import settings

logger = logging.getLogger(__name__)

#: Arten: ``aie-geheim-<id>-…`` = Key aus der Schluesselverwaltung, ``g`` = Token
#: einer Git-Anbindung der Plattform, ``m`` = Bearer-Token eines MCP-Servers,
#: ``h`` = eigene Kopfzeile eines MCP-Servers (id = Server-ID * KOPF_FAKTOR + Nummer),
#: ``k`` = Claude-Zugang des Agenten (id = Nummer der Variable in ``KI_VARIABLEN``).
PLATZHALTER_RE = re.compile(r"aie-geheim-([gmhk]?)(\d{1,10})-([0-9a-f]{12})")
ANBINDUNG = "g"
KI = "k"
MCP = "m"
MCP_KOPF = "h"
KOPF_FAKTOR = 1000
_PLATZHALTER_DOMAIN = b"egress-platzhalter:v1:"

#: Unter dieser Adresse gibt der Proxy sein oeffentliches CA-Zertifikat heraus —
#: nie den privaten Schluessel. Der Name loest nirgends auf, der Proxy antwortet selbst.
CA_ADRESSE = "http://aie-egress.internal/ca.pem"

#: Interne Ziele gehen nie ueber den Proxy.
NO_PROXY = (
    "localhost", "127.0.0.1", "::1",
    "ai-employee-orchestrator", "orchestrator", "ai-employee-redis", "redis",
    "host.docker.internal", ".internal", ".local",
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
)

DIENST_TOKEN_MINDESTLAENGE = 32
MAX_HOSTS = 20

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOST_RE = re.compile(rf"^{_LABEL}(?:\.{_LABEL})+$")

def aktiv() -> bool:
    """Eingeschaltet UND mit einem Dienst-Schluessel, den der Proxy vorzeigen muss."""
    return bool(settings.egress_proxy_enabled
                and len(settings.egress_proxy_token or "") >= DIENST_TOKEN_MINDESTLAENGE)


def ist_proxy_anfrage(authorization: str) -> bool:
    """Traegt die Anfrage den Dienst-Schluessel des Proxys? (Vergleich in konstanter Zeit)"""
    token = (authorization or "").removeprefix("Bearer ").strip()
    return aktiv() and bool(token) and hmac.compare_digest(token.encode(), settings.egress_proxy_token.encode())


def veraltet(agent_config: dict | None, agent_id: str | None = None) -> bool:
    """Entstand der Behaelter mit einem anderen Ausgang, als jetzt gilt?

    Dann zeigt der Agent "Aktualisieren" — sonst bliebe ein Agent nach dem
    Einschalten des Proxys mit Klartext-Keys stehen, ohne dass es jemand merkt.
    Agenten aus der Zeit vor dem Proxy haben keinen Eintrag = ohne Proxy.
    Mit ``agent_id`` zaehlt auch die Stufe von ``EGRESS_KI_ZUGANG``.
    """
    cfg = agent_config or {}
    if bool(cfg.get("egress")) != aktiv():
        return True
    return agent_id is not None and bool(cfg.get("egress_ki_stufe")) != ki_ueber_proxy(agent_id)


def platzhalter(secret_id: int, art: str = "") -> str:
    sig = hmac.new(settings.api_secret_key.encode(), _PLATZHALTER_DOMAIN + f"{art}{secret_id}".encode(),
                   hashlib.sha256).hexdigest()[:12]
    return f"aie-geheim-{art}{secret_id}-{sig}"


def platzhalter_aus(wert: str) -> tuple[str, int] | None:
    """(Art, ID) hinter einem Platzhalter — nur, wenn die Signatur stimmt."""
    m = PLATZHALTER_RE.fullmatch(wert or "")
    if not m:
        return None
    art, sid = m.group(1), int(m.group(2))
    return (art, sid) if hmac.compare_digest(platzhalter(sid, art).encode(), wert.encode()) else None


def secret_id_aus(wert: str) -> int | None:
    """Die Key-ID hinter einem Platzhalter der Schluesselverwaltung."""
    teile = platzhalter_aus(wert)
    return teile[1] if teile and teile[0] == "" else None


def hosts_pruefen(werte) -> list[str] | None:
    """Erlaubte Ziele eines Keys bereinigen. Leer → None. Ungueltig → ValueError.

    Erlaubt sind nur genaue Hostnamen (``api.example.com``). Keine Muster: ``*.github.io``
    oder ``*.amazonaws.com`` haetten den Key an jede dort angemeldete Fremdadresse
    gegeben. Keine Adressen mit Schema, Pfad oder Port.
    """
    if werte is None:
        return None
    if isinstance(werte, str):
        werte = re.split(r"[\s,]+", werte)
    ergebnis: list[str] = []
    for roh in werte:
        h = str(roh or "").strip().lower().rstrip(".")
        if not h:
            continue
        if h.startswith("*"):
            raise ValueError(f"Keine Muster, nur genaue Hostnamen: {h[:80]}")
        if not _HOST_RE.match(h) or len(h) > 253:
            raise ValueError(f"Kein gültiger Hostname: {str(roh)[:80]}")
        if h not in ergebnis:
            ergebnis.append(h)
    if len(ergebnis) > MAX_HOSTS:
        raise ValueError(f"Höchstens {MAX_HOSTS} Ziele je Key")
    return ergebnis or None


def host_passt(host: str, muster: list[str] | None) -> bool:
    h = (host or "").strip().lower().rstrip(".")
    return bool(h) and h in (muster or [])


async def ca_zertifikat() -> str:
    """Oeffentliches CA-Zertifikat des Proxys.

    Bei jedem Anlegen frisch geholt, nicht zwischengespeichert: Entsteht die CA
    neu (Volume ersetzt), bekaeme ein neuer Agent sonst die alte und traute dem
    Proxy nicht.
    """
    import httpx
    letzter: Exception | None = None
    for versuch in range(3):
        try:
            async with httpx.AsyncClient(proxy=settings.egress_proxy_url, timeout=5) as client:
                r = await client.get(CA_ADRESSE)
            text = r.text.strip()
            if r.status_code == 200 and text.startswith("-----BEGIN CERTIFICATE-----") and "PRIVATE" not in text:
                return text + "\n"
            letzter = RuntimeError(f"Antwort {r.status_code}")
        except Exception as e:  # noqa: BLE001 — unten als eine Meldung
            letzter = e
        await asyncio.sleep(1 + versuch)
    raise RuntimeError(f"Egress-Proxy liefert kein CA-Zertifikat ({type(letzter).__name__}). "
                       "Ohne ihn kann der Agent nicht starten, solange der Proxy eingeschaltet ist.")


def _intern(host: str) -> bool:
    import ipaddress
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        return "." not in host or host.endswith((".internal", ".local"))


def mcp_ziel(url: str) -> str | None:
    """Host eines MCP-Servers, dessen Zugang ueber den Proxy laufen kann — sonst None.

    Nur HTTPS auf Port 443 ins Internet: Dort bricht der Proxy auf. Interne Server
    (Orchestrator, eigene Apps) gehen am Proxy vorbei und behalten ihren Zugang.
    """
    teile = urlsplit(url or "")
    host = (teile.hostname or "").lower()
    if teile.scheme != "https" or not host or _intern(host):
        return None
    try:
        if teile.port not in (None, 443):
            return None
    except ValueError:
        return None
    return host


#: Claude-Zugang: welche Variable welche Nummer traegt, und wohin er darf.
KI_VARIABLEN = {"CLAUDE_CODE_OAUTH_TOKEN": 0, "ANTHROPIC_API_KEY": 1}
KI_ZIELE = ["api.anthropic.com"]
_KI_ANDERER_WEG = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
                   "ANTHROPIC_BASE_URL")


def ki_ueber_proxy(agent_id: str) -> bool:
    """Geht der Claude-Zugang dieses Agenten ueber den Proxy? (``EGRESS_KI_ZUGANG``)"""
    wert = (settings.egress_ki_zugang or "").strip().lower()
    if not aktiv() or not wert:
        return False
    return wert == "alle" or (agent_id or "").lower() in {x.strip() for x in wert.split(",") if x.strip()}


def ki_umstellen(agent_id: str, env: dict[str, str]) -> dict[str, str]:
    """Claude-Zugang in ``env`` durch Platzhalter ersetzen.

    Liefert den Zugang verschluesselt fuer ``agent.config["egress_ki"]`` — dort liest
    ihn die Aufloesung (api/egress.py) wieder, genau den Wert, den der Agent sonst
    bekommen haette, egal aus welcher Quelle (Anlage, KI-Konto, eigener Zugang).
    Nur Anthropic direkt: Bedrock, Vertex, Foundry und eigene Adressen bleiben, wie sie sind.
    """
    if not ki_ueber_proxy(agent_id) or any(env.get(k) for k in _KI_ANDERER_WEG):
        return {}
    from app.core.encryption import encrypt_token
    gespeichert: dict[str, str] = {}
    for variable, nummer in KI_VARIABLEN.items():
        wert = env.get(variable)
        if wert and not PLATZHALTER_RE.fullmatch(wert):
            gespeichert[variable] = encrypt_token(wert)
            env[variable] = platzhalter(nummer, KI)
    return gespeichert


def interne_hosts(mcp_server_json: str | None) -> list[str]:
    """Hosts der MCP-Server eines Agenten, die nicht ins Internet gehoeren.

    Namen ohne Punkt (eigene Apps im App-Netz, etwa ``test-mcp-server``), private
    Adressen, ``.internal``/``.local``. Claude Code schickt auch ``http://``-Adressen
    an ``HTTPS_PROXY``; der Proxy kennt die App-Netze nicht und antwortete mit 502.
    ``NO_PROXY`` kennt kein Muster fuer „Name ohne Punkt“ — also stehen sie einzeln da.
    """
    import json
    try:
        server = json.loads(mcp_server_json or "{}")
    except ValueError:
        return []
    gefunden: list[str] = []
    for url in (server.values() if isinstance(server, dict) else []):
        host = (urlsplit(str(url)).hostname or "").lower()
        if host and _intern(host) and host not in gefunden:
            gefunden.append(host)
    return gefunden


async def agent_umgebung(agent_id: str, mcp_server_json: str | None = None) -> dict[str, str]:
    """Umgebung, mit der ein Agent ueber den Proxy geht. Leer, wenn der Proxy aus ist.

    Wird ZULETZT auf die Umgebung gelegt: Kein zugewiesener Key darf den Weg nach
    draussen umbiegen. Nur HTTPS geht ueber den Proxy — Keys gehen ueber HTTPS, und
    die eigenen Apps des Agenten (http, Namen ohne Punkt) bleiben direkt erreichbar.
    ``mcp_server_json``: ``CUSTOM_MCP_SERVERS`` des Agenten (interne bleiben direkt).
    """
    if not aktiv():
        return {}
    from app.dependencies import make_agent_token
    ca = await ca_zertifikat()
    teile = urlsplit(settings.egress_proxy_url)
    proxy = f"{teile.scheme}://{agent_id}:{make_agent_token(agent_id)}@{teile.netloc}"
    no_proxy = ",".join(NO_PROXY + tuple(interne_hosts(mcp_server_json)))
    return {
        "HTTPS_PROXY": proxy, "https_proxy": proxy,
        "NO_PROXY": no_proxy, "no_proxy": no_proxy,
        # Node (fetch/undici) folgt der Umgebung nur damit.
        "NODE_USE_ENV_PROXY": "1",
        # Das Startskript (agent/entrypoint.sh) legt das Zertifikat in den
        # Systemspeicher; Python und Node bringen eigene Listen mit und brauchen
        # den Verweis.
        "AIE_EGRESS_CA": ca,
        "NODE_EXTRA_CA_CERTS": "/usr/local/share/ca-certificates/aie-egress.crt",
        "REQUESTS_CA_BUNDLE": "/etc/ssl/certs/ca-certificates.crt",
        "SSL_CERT_FILE": "/etc/ssl/certs/ca-certificates.crt",
    }
