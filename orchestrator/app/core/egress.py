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

PLATZHALTER_RE = re.compile(r"aie-geheim-(\d{1,10})-([0-9a-f]{12})")
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
_HOST_RE = re.compile(rf"^(\*\.)?{_LABEL}(?:\.{_LABEL})+$")

def aktiv() -> bool:
    """Eingeschaltet UND mit einem Dienst-Schluessel, den der Proxy vorzeigen muss."""
    return bool(settings.egress_proxy_enabled
                and len(settings.egress_proxy_token or "") >= DIENST_TOKEN_MINDESTLAENGE)


def ist_proxy_anfrage(authorization: str) -> bool:
    """Traegt die Anfrage den Dienst-Schluessel des Proxys? (Vergleich in konstanter Zeit)"""
    token = (authorization or "").removeprefix("Bearer ").strip()
    return aktiv() and bool(token) and hmac.compare_digest(token, settings.egress_proxy_token)


def veraltet(agent_config: dict | None) -> bool:
    """Entstand der Behaelter mit einem anderen Ausgang, als jetzt gilt?

    Dann zeigt der Agent "Aktualisieren" — sonst bliebe ein Agent nach dem
    Einschalten des Proxys mit Klartext-Keys stehen, ohne dass es jemand merkt.
    Agenten aus der Zeit vor dem Proxy haben keinen Eintrag = ohne Proxy.
    """
    return bool((agent_config or {}).get("egress")) != aktiv()


def platzhalter(secret_id: int) -> str:
    sig = hmac.new(settings.api_secret_key.encode(), _PLATZHALTER_DOMAIN + str(secret_id).encode(),
                   hashlib.sha256).hexdigest()[:12]
    return f"aie-geheim-{secret_id}-{sig}"


def secret_id_aus(wert: str) -> int | None:
    """Die Key-ID hinter einem Platzhalter — nur, wenn die Signatur stimmt."""
    m = PLATZHALTER_RE.fullmatch(wert or "")
    if not m:
        return None
    sid = int(m.group(1))
    return sid if hmac.compare_digest(platzhalter(sid), wert) else None


def hosts_pruefen(werte) -> list[str] | None:
    """Erlaubte Ziele eines Keys bereinigen. Leer → None. Ungueltig → ValueError.

    Erlaubt sind Hostnamen (``api.example.com``) und Unterdomaenen-Muster
    (``*.example.com``, trifft NICHT ``example.com`` selbst). Keine Adressen mit
    Schema, Pfad oder Port, keine Muster auf eine ganze Endung (``*.com``).
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
        if not _HOST_RE.match(h) or len(h) > 253:
            raise ValueError(f"Kein gültiger Hostname: {str(roh)[:80]}")
        if h.startswith("*.") and h.count(".") < 2:
            raise ValueError(f"Muster zu weit: {h} (mindestens *.name.endung)")
        if h not in ergebnis:
            ergebnis.append(h)
    if len(ergebnis) > MAX_HOSTS:
        raise ValueError(f"Höchstens {MAX_HOSTS} Ziele je Key")
    return ergebnis or None


def host_passt(host: str, muster: list[str] | None) -> bool:
    h = (host or "").strip().lower().rstrip(".")
    if not h:
        return False
    for m in muster or []:
        if m.startswith("*."):
            if h.endswith(m[1:]) and len(h) > len(m) - 1:
                return True
        elif h == m:
            return True
    return False


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


async def agent_umgebung(agent_id: str) -> dict[str, str]:
    """Umgebung, mit der ein Agent ueber den Proxy geht. Leer, wenn der Proxy aus ist.

    Wird ZULETZT auf die Umgebung gelegt: Kein zugewiesener Key darf den Weg nach
    draussen umbiegen. Nur HTTPS geht ueber den Proxy — Keys gehen ueber HTTPS, und
    die eigenen Apps des Agenten (http, Namen ohne Punkt) bleiben direkt erreichbar.
    """
    if not aktiv():
        return {}
    from app.dependencies import make_agent_token
    ca = await ca_zertifikat()
    teile = urlsplit(settings.egress_proxy_url)
    proxy = f"{teile.scheme}://{agent_id}:{make_agent_token(agent_id)}@{teile.netloc}"
    no_proxy = ",".join(NO_PROXY)
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
