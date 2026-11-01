"""Fragen des Egress-Proxys (egress/aie_egress.py) — nur mit dessen Dienst-Schluessel.

* ``/egress/verbindung``: Ist das wirklich dieser Agent, und an welche Ziele duerfen
  seine Keys? Nur fuer diese Ziele bricht der Proxy TLS auf.
* ``/egress/aufloesen``: Die echten Werte hinter Platzhaltern — alle oder keiner,
  und nur, wenn jeder Key dem Agenten zusteht und an genau dieses Ziel darf.

Agenten erreichen beide Endpunkte zwar im Netz, haben aber den Dienst-Schluessel
nicht. Siehe core/egress.py.
"""

from __future__ import annotations

import hmac
import logging
from typing import Callable, NamedTuple

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import egress
from app.core.audit import protokolliere
from app.core.encryption import decrypt_token
from app.core.log_redaction import scrub_log
from app.core.secret_zugriff import nutzbare_secrets_des_besitzers, secrets_des_agenten
from app.db.session import get_db
from app.dependencies import make_agent_token
from app.models.agent import Agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/egress", tags=["egress"])


def _nur_proxy(request: Request) -> None:
    if not egress.ist_proxy_anfrage(request.headers.get("Authorization", "")):
        raise HTTPException(status_code=403, detail="Nur für den Egress-Proxy")


class Verbindung(BaseModel):
    agent_id: str = Field(min_length=1, max_length=32)
    agent_token: str = Field(min_length=1, max_length=128)


class Aufloesen(Verbindung):
    host: str = Field(min_length=1, max_length=253)
    platzhalter: list[str] = Field(min_length=1, max_length=20)


class _Freigabe(NamedTuple):
    """Was ein Agent ueber den Proxy hinausschicken darf: ein Key, das Token einer
    Anbindung oder der Zugang eines MCP-Servers. ``lesen`` liefert den Klartext."""
    art: str
    id: int
    name: str
    hosts: list[str]
    lesen: Callable[[], str]


async def _anbindungen(db: AsyncSession, agent: Agent) -> list[_Freigabe]:
    """Git-Anbindung der Plattform — nur fuer Agenten, denen sie zugewiesen ist
    (wie agent_manager._get_integration_env)."""
    if "github" not in ((agent.config or {}).get("integrations") or []):
        return []
    from app.core.githost.registry import get_git_host_provider
    from app.models.oauth_integration import OAuthIntegration, OAuthProvider
    integration = (await db.execute(select(OAuthIntegration).where(
        OAuthIntegration.provider == OAuthProvider.GITHUB, OAuthIntegration.user_id.is_(None),
    ))).scalar_one_or_none()
    if integration is None:
        return []
    provider = get_git_host_provider(integration.host_type or "github", integration.base_url)
    enc = integration.access_token_encrypted
    return [_Freigabe(egress.ANBINDUNG, integration.id, "Git-Anbindung", provider.egress_ziele(),
                      lambda: decrypt_token(enc))]


async def _mcp_zugaenge(db: AsyncSession, agent: Agent) -> list[_Freigabe]:
    """Token und Kopfzeilen externer MCP-Server — dieselbe Auswahl wie beim Einspielen
    (agent_manager._get_custom_mcp_env), nur am Host des jeweiligen Servers."""
    import json

    from app.core.agent_mcp_servers import servers_for_agent
    freigaben: list[_Freigabe] = []
    for s in await servers_for_agent(db, agent.id, agent.config):
        host = egress.mcp_ziel(s.url)
        if not host:
            continue
        if s.auth_token_encrypted:
            enc = s.auth_token_encrypted
            freigaben.append(_Freigabe(egress.MCP, s.id, f"MCP {s.name}", [host], lambda enc=enc: decrypt_token(enc)))
        if getattr(s, "headers_encrypted", None):
            try:
                koepfe = json.loads(decrypt_token(s.headers_encrypted))
            except Exception:  # noqa: BLE001 — wie beim Einspielen: kaputter Eintrag wird uebersprungen
                continue
            for i, kopf in enumerate(sorted(koepfe)):
                freigaben.append(_Freigabe(egress.MCP_KOPF, s.id * egress.KOPF_FAKTOR + i, f"MCP {s.name} {kopf}",
                                           [host], lambda w=str(koepfe[kopf]): w))
    return freigaben


async def _ki_aktuell(db: AsyncSession, agent: Agent) -> dict[str, str] | None:
    """Der Claude-Zugang, den dieser Agent JETZT bekaeme (eigenes Abo → Teamlizenz).

    Dieselbe Aufloesung wie beim Anlegen (``agent_credentials.resolve``). OAuth-Token
    werden erneuert; die Abschrift aus der Erstellung war nach der ersten Erneuerung
    widerrufen, und jeder Aufruf mit dem Platzhalter scheiterte. ``{}`` heisst: kein
    Zugang (z. B. vom Admin gesperrt) — das gilt auch hinter dem Proxy. ``None``
    heisst: nicht ermittelbar, dann bleibt es bei der Abschrift.
    """
    from app.core import agent_credentials as creds

    try:
        _quelle, harness, geheimnis = await creds.resolve(
            db, owner_id=agent.user_id, mode=agent.mode or "claude_code",
            model_provider=(agent.config or {}).get("model_provider") or "anthropic")
    except Exception:  # noqa: BLE001
        logger.warning("Egress: Claude-Zugang von Agent %s nicht aufloesbar", scrub_log(agent.id))
        return None
    return creds.env_for(harness, geheimnis) if harness == "claude_code" and geheimnis else {}


async def _ki_zugang(db: AsyncSession, agent: Agent) -> list[_Freigabe]:
    """Claude-Zugang, den ``ki_umstellen`` beim Anlegen hinter den Proxy gelegt hat.

    Eingesetzt wird der aktuelle Wert. Die Abschrift aus der Erstellung nur, wenn sich
    der Zugang nicht ermitteln laesst; hat der Agent heute keinen (oder einen anderer
    Art), bekommt er auch keinen — sonst wirkte das Sperren hinter dem Proxy nicht.
    """
    gespeichert = {var: enc for var, enc in ((agent.config or {}).get("egress_ki") or {}).items()
                   if var in egress.KI_VARIABLEN}
    if not gespeichert:
        return []
    aktuell = await _ki_aktuell(db, agent)
    if aktuell is None:
        return [_Freigabe(egress.KI, egress.KI_VARIABLEN[var], var, egress.KI_ZIELE,
                          lambda enc=enc: decrypt_token(enc))
                for var, enc in gespeichert.items()]
    return [_Freigabe(egress.KI, egress.KI_VARIABLEN[var], var, egress.KI_ZIELE, lambda w=aktuell[var]: w)
            for var in gespeichert if aktuell.get(var)]


async def _freigaben(db: AsyncSession, body: Verbindung) -> list[_Freigabe]:
    if not hmac.compare_digest(body.agent_token.encode(), make_agent_token(body.agent_id).encode()):
        raise HTTPException(status_code=401, detail="Agent unbekannt")
    agent = await db.get(Agent, body.agent_id)
    if agent is None:
        raise HTTPException(status_code=401, detail="Agent unbekannt")
    secrets = await secrets_des_agenten(db, body.agent_id, await nutzbare_secrets_des_besitzers(db, body.agent_id))
    return ([_Freigabe("", s.id, s.key_name, list(s.erlaubte_hosts or []),
                       lambda enc=s.value_encrypted: decrypt_token(enc))
             for s in secrets if s.erlaubte_hosts]
            + await _anbindungen(db, agent)
            + await _mcp_zugaenge(db, agent)
            + await _ki_zugang(db, agent))


@router.post("/verbindung", dependencies=[Depends(_nur_proxy)])
async def verbindung(body: Verbindung, db: AsyncSession = Depends(get_db)):
    return {"hosts": sorted({h for f in await _freigaben(db, body) for h in f.hosts})}


@router.post("/aufloesen", dependencies=[Depends(_nur_proxy)])
async def aufloesen(body: Aufloesen, db: AsyncSession = Depends(get_db)):
    nach_id = {(f.art, f.id): f for f in await _freigaben(db, body)}
    werte: dict[str, str] = {}
    abgelehnt: list[dict] = []
    for p in dict.fromkeys(body.platzhalter):
        teile = egress.platzhalter_aus(p)
        freigabe = nach_id.get(teile) if teile else None
        if freigabe is None:
            abgelehnt.append({"platzhalter": p, "grund": "Key steht dem Agenten nicht zu"})
        elif not egress.host_passt(body.host, freigabe.hosts):
            abgelehnt.append({"platzhalter": p, "key": freigabe.name, "grund": "Ziel nicht erlaubt"})
        else:
            try:
                werte[p] = freigabe.lesen()
            except Exception:  # noqa: BLE001
                abgelehnt.append({"platzhalter": p, "key": freigabe.name, "grund": "Key nicht lesbar"})
    if abgelehnt:
        # Ein Agent wollte einen Key an ein fremdes Ziel schicken: ins Protokoll,
        # damit Admins es sehen — der Wert verlaesst die Plattform nicht.
        await protokolliere(
            db, "egress_key_verweigert", agent_id=body.agent_id, outcome="blocked",
            command=f"HTTPS {body.host[:253]}",
            meta={"ziel": body.host[:253], "abgelehnt": abgelehnt},
        )
        await db.commit()
        logger.warning("Egress: Key-Nutzung verweigert (Agent %s, Ziel %s)",
                       scrub_log(body.agent_id), scrub_log(body.host[:253]))
        return JSONResponse(status_code=403, content={"detail": "Key für dieses Ziel nicht freigegeben",
                                                      "abgelehnt": abgelehnt})
    return {"werte": werte}
