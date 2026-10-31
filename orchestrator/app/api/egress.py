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

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import egress
from app.core.audit import protokolliere
from app.core.encryption import decrypt_token
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


async def _secrets(db: AsyncSession, body: Verbindung):
    if not hmac.compare_digest(body.agent_token, make_agent_token(body.agent_id)):
        raise HTTPException(status_code=401, detail="Agent unbekannt")
    if await db.get(Agent, body.agent_id) is None:
        raise HTTPException(status_code=401, detail="Agent unbekannt")
    return await secrets_des_agenten(db, body.agent_id, await nutzbare_secrets_des_besitzers(db, body.agent_id))


@router.post("/verbindung", dependencies=[Depends(_nur_proxy)])
async def verbindung(body: Verbindung, db: AsyncSession = Depends(get_db)):
    secrets = await _secrets(db, body)
    return {"hosts": sorted({h for s in secrets for h in (s.erlaubte_hosts or [])})}


@router.post("/aufloesen", dependencies=[Depends(_nur_proxy)])
async def aufloesen(body: Aufloesen, db: AsyncSession = Depends(get_db)):
    nach_id = {s.id: s for s in await _secrets(db, body)}
    werte: dict[str, str] = {}
    abgelehnt: list[dict] = []
    for p in dict.fromkeys(body.platzhalter):
        sid = egress.secret_id_aus(p)
        secret = nach_id.get(sid) if sid is not None else None
        if secret is None or not secret.erlaubte_hosts:
            abgelehnt.append({"platzhalter": p, "grund": "Key steht dem Agenten nicht zu"})
        elif not egress.host_passt(body.host, secret.erlaubte_hosts):
            abgelehnt.append({"platzhalter": p, "key": secret.key_name, "grund": "Ziel nicht erlaubt"})
        else:
            try:
                werte[p] = decrypt_token(secret.value_encrypted)
            except Exception:  # noqa: BLE001
                abgelehnt.append({"platzhalter": p, "key": secret.key_name, "grund": "Key nicht lesbar"})
    if abgelehnt:
        # Ein Agent wollte einen Key an ein fremdes Ziel schicken: ins Protokoll,
        # damit Admins es sehen — der Wert verlaesst die Plattform nicht.
        await protokolliere(
            db, "egress_key_verweigert", agent_id=body.agent_id, outcome="blocked",
            command=f"HTTPS {body.host[:253]}",
            meta={"ziel": body.host[:253], "abgelehnt": abgelehnt},
        )
        await db.commit()
        logger.warning("Egress: Key-Nutzung verweigert (Agent %s, Ziel %s)", body.agent_id, body.host[:253])
        return JSONResponse(status_code=403, content={"detail": "Key für dieses Ziel nicht freigegeben",
                                                      "abgelehnt": abgelehnt})
    return {"werte": werte}
