"""Zielliste am Ausgang — die erlaubten Adressen eines Agenten verbindlich machen.

Die Liste „Erlaubte Adressen“ (``api/url_allowlist``, je Agent; dazu die Host-Muster der
Rolle seines Besitzers) prüfte bisher nur der Werkzeug-Ausführer IM Agenten. Claude Code und
Codex gingen mit ihren eigenen Werkzeugen (WebFetch, curl in der Shell) daran vorbei.

Opt-in je Agent (``agent.config["zielliste"]``, Entscheidung vom 09.11.2026). Eingeschaltet
setzen zwei Stellen dieselbe Liste durch, an denen der Agent nicht vorbeikommt:

* **Egress-Proxy** (``egress/aie_egress.py``): lässt Verbindungen nur zu Zielen der Liste zu,
  HTTPS (CONNECT) und Klartext-HTTP. Er bekommt die Liste über ``/api/v1/egress/verbindung``
  (``fuer_proxy``).
* **Netzwächter** (``scripts/netzwaechter.py``, Host): Für den Agenten geht dann nichts mehr
  direkt hinaus, auf keinem Port, nur über den Proxy. Er erkennt das an ``AIE_ZIELLISTE=1`` in
  der Umgebung, mit der der Behälter entstand (der Agent kann sie nicht nachträglich ändern).

Immer erlaubt, sonst arbeitet der Agent nicht: die Modellanbieter, der eigene
Modell-Endpunkt und die Ziele, für die die Plattform selbst Zugänge einsetzt (Schlüssel, Git,
MCP). Voraussetzung ist der eingeschaltete Egress-Proxy.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from sqlalchemy import select

SCHLUESSEL = "zielliste"
UMGEBUNG = "AIE_ZIELLISTE"


def an(config: dict | None) -> bool:
    return bool(((config or {}).get(SCHLUESSEL) or {}).get("aktiv"))


def mit(config: dict | None, aktiv: bool) -> dict:
    """Neues Dict (SQLAlchemy erkennt die Änderung an der Zuweisung)."""
    neu = dict(config or {})
    neu[SCHLUESSEL] = {"aktiv": bool(aktiv)}
    return neu


def wirksam(config: dict | None) -> bool:
    from app.core import egress

    return an(config) and egress.aktiv()


def _host(adresse: str | None) -> str | None:
    if not adresse:
        return None
    teil = str(adresse)
    host = urlsplit(teil if "://" in teil else f"https://{teil}").hostname
    return host.lower() if host else None


async def _modell_hosts(db, agent) -> set[str]:
    """Modellanbieter und — bei eigenem Modell — der Endpunkt des KI-Kontos (vom Admin angelegt)."""
    from app.core.anonymisierer import ZIELE
    from app.models.ai_account import AIAccount

    hosts = set(ZIELE)
    if getattr(agent, "ai_account_id", None):
        konto = await db.get(AIAccount, agent.ai_account_id)
        h = _host(getattr(konto, "api_endpoint", None)) if konto is not None else None
        if h:
            hosts.add(h)
    # NICHT der Endpunkt aus ``agent.llm_config``: den darf auch ändern, wem der Agent nur
    # freigegeben ist — er schöbe sonst ein beliebiges Ziel in „immer erlaubt“. KI-Konten legt
    # nur ein Admin an. Wer ein Modell über ``llm_config`` anbindet, trägt dessen Adresse in die
    # Liste ein (die ändern nur Besitzer und Admin).
    return hosts


async def _rollen_muster(db, agent) -> list[str] | None:
    """Host-Muster der Rolle des Besitzers — None = keine Einschränkung durch die Rolle."""
    from app.api.url_allowlist import host_muster
    from app.core.permissions import get_effective_permissions
    from app.models.user import User

    besitzer = await db.get(User, agent.user_id) if getattr(agent, "user_id", None) else None
    if besitzer is None:
        return None
    muster = (await get_effective_permissions(besitzer, db)).get("url_host_patterns")
    if muster is None:
        return None
    return [m for m in (host_muster(x) for x in muster) if m]


async def eintraege(db, agent_id: str) -> list[str]:
    from app.api.url_allowlist import host_muster
    from app.models.url_allowlist import AgentUrlAllowlist

    zeilen = (await db.execute(select(AgentUrlAllowlist.url_pattern).where(
        AgentUrlAllowlist.agent_id == agent_id, AgentUrlAllowlist.is_active.is_(True)))).scalars().all()
    return sorted({m for m in (host_muster(z) for z in zeilen) if m})


async def fuer_proxy(db, agent, plattform_hosts: set[str]) -> dict | None:
    """Was der Proxy durchsetzt — None, wenn die Zielliste aus ist.

    ``muster``: erlaubte Adressen des Agenten (Host-Muster). ``rolle``: Muster der Rolle des
    Besitzers (zusätzlich zu erfüllen) oder None. ``immer``: Ziele, ohne die der Agent nicht
    arbeitet; sie gelten unabhängig von Liste und Rolle.
    """
    if agent is None or not wirksam(agent.config):
        return None
    return {
        "muster": await eintraege(db, agent.id),
        "rolle": await _rollen_muster(db, agent),
        "immer": sorted({h.lower() for h in plattform_hosts if h} | await _modell_hosts(db, agent)),
    }


def umgebung(config: dict | None) -> dict[str, str]:
    """Marke für den Netzwächter (alle Ports nur über den Proxy). Klartext-HTTP über den Proxy
    und die Kennung im Anmeldenamen setzt ``egress.agent_umgebung`` dazu."""
    return {UMGEBUNG: "1"} if wirksam(config) else {}
