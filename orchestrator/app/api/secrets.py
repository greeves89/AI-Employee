"""Key Management System API — encrypted secrets for agents.

Secrets (API keys, SSO profiles, OAuth tokens) are stored Fernet-encrypted.
They are assigned to agents and injected as env vars at task runtime.
The plaintext value is only visible at creation time — reads return masked values.
"""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_manager import AgentManager
from app.core.encryption import decrypt_token, encrypt_token
from app.core.log_redaction import scrub_log
from app.db.session import get_db
from app.dependencies import get_docker_service, get_redis_service, require_auth
from app.core import secret_zugriff
from app.models.agent_secret import AgentSecret, AgentSecretAssignment, AgentSecretShare, SecretType
from app.services.docker_service import DockerService
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/secrets", tags=["secrets"])


def _get_agent_manager(
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
    redis: RedisService = Depends(get_redis_service),
) -> AgentManager:
    return AgentManager(db, docker, redis)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SecretCreate(BaseModel):
    name: str
    key_name: str
    value: str
    secret_type: SecretType = SecretType.API_KEY
    description: str = ""


class SecretUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    value: str | None = None
    is_active: bool | None = None


def _mask(value_encrypted: str) -> str:
    try:
        plain = decrypt_token(value_encrypted)
        if len(plain) <= 8:
            return "****"
        return plain[:4] + "****" + plain[-4:]
    except Exception:
        return "****"


def _serialize(s: AgentSecret, include_mask: bool = True, user=None, *,
               zugang: str | None = None, besitzer: str | None = None, freigaben: int | None = None) -> dict:
    eigen = bool(user is not None and s.owner_id and s.owner_id == getattr(user, "id", None))
    verwaltbar = eigen or (user is not None and _ist_admin(user))
    return {
        # Warum der Aufrufer ihn nutzen darf: admin | eigen | rolle | person.
        "zugang": zugang or ("eigen" if eigen else None),
        # Bei an mich freigegebenen Keys: von wem (Name), sonst None.
        "owner_name": besitzer,
        # Nur fuer Besitzer/Admin: an wie viele Personen freigegeben.
        "shared_with_count": freigaben if verwaltbar else None,
        # Fuer die Oberflaeche: eigene Secrets kann man bearbeiten, freigegebene nur nutzen.
        "owned": eigen,
        "manageable": verwaltbar,
        "id": s.id,
        "name": s.name,
        "key_name": s.key_name,
        "secret_type": s.secret_type,
        "description": s.description,
        "is_active": s.is_active,
        # Nur wer den Key verwaltet, sieht eine Andeutung des Werts (erste/letzte 4
        # Zeichen) — Empfaenger einer Freigabe nicht (Sicherheitspruefung 03.10.2026).
        "masked_value": _mask(s.value_encrypted) if (include_mask and verwaltbar) else None,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "assigned_agent_ids": [a.agent_id for a in s.assignments],
    }


async def _refresh_agents_for_secret(
    db: AsyncSession,
    manager: AgentManager,
    secret_id: int,
    agent_ids: list[str] | None = None,
) -> dict:
    if agent_ids is None:
        result = await db.execute(
            select(AgentSecretAssignment.agent_id).where(
                AgentSecretAssignment.secret_id == secret_id
            )
        )
        agent_ids = list(result.scalars().all())

    refreshed: list[str] = []
    warnings: list[str] = []
    for agent_id in sorted(set(agent_ids)):
        try:
            await manager.update_agent(agent_id)
            refreshed.append(agent_id)
        except Exception as exc:
            logger.warning("Could not refresh agent %s after secret change: %s", scrub_log(agent_id), exc)
            warnings.append(f"{agent_id}: {exc}")

    return {"refreshed_agent_ids": refreshed, "warnings": warnings}


# ---------------------------------------------------------------------------
# Secret CRUD
# ---------------------------------------------------------------------------

@router.get("")
async def list_secrets(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Alle Keys, die der Aufrufer nutzen darf (core/secret_zugriff.py): Admin alle,
    sonst eigene, per Rolle und per Person freigegebene. Default-deny."""
    from app.models.user import User
    result = await db.execute(select(AgentSecret).order_by(AgentSecret.name))
    secrets = result.scalars().all()
    erlaubt = await secret_zugriff.nutzbare_secret_ids(db, user)
    if erlaubt is not None:
        secrets = [s for s in secrets if s.id in erlaubt]
    uid = str(getattr(user, "id", "") or "")
    rolle = await secret_zugriff._rollen_ids(db, user) if erlaubt is not None else set()
    person = await secret_zugriff.personen_ids(db, uid) if erlaubt is not None else set()
    anzahl: dict[int, int] = {}
    sichtbar = [s.id for s in secrets]
    for sid in (await db.execute(
        select(AgentSecretShare.secret_id).where(AgentSecretShare.secret_id.in_(sichtbar))
    )).scalars().all() if sichtbar else []:
        anzahl[sid] = anzahl.get(sid, 0) + 1
    besitzer_ids = {s.owner_id for s in secrets if s.owner_id and s.owner_id != uid}
    namen = {}
    if besitzer_ids:
        for r in (await db.execute(select(User.id, User.name).where(User.id.in_(besitzer_ids)))).all():
            namen[r[0]] = r[1]

    def art(s: AgentSecret) -> str:
        if erlaubt is None:
            return "admin"
        if s.owner_id == uid:
            return "eigen"
        return "rolle" if s.id in rolle else "person" if s.id in person else ""

    return {"secrets": [
        _serialize(s, user=user, zugang=art(s), besitzer=namen.get(s.owner_id or ""), freigaben=anzahl.get(s.id, 0))
        for s in secrets
    ]}


@router.post("", status_code=201)
async def create_secret(
    body: SecretCreate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    secret = AgentSecret(
        name=body.name,
        key_name=body.key_name.upper().replace(" ", "_"),
        value_encrypted=encrypt_token(body.value),
        secret_type=body.secret_type,
        description=body.description,
        created_by=getattr(user, "email", None),
        owner_id=None if _ist_admin(user) else user.id,
    )
    db.add(secret)
    await db.commit()
    await db.refresh(secret)
    return _serialize(secret, user=user)


async def _assert_agent_owned(agent_id: str, user, db) -> None:
    """404 unless the caller owns/shares the agent (admin bypass). Prevents attaching/
    reading/stripping secrets on a foreign tenant's agent."""
    from app.core.ownership import visible_agent_ids
    vids = await visible_agent_ids(user, db)
    if vids is not None and agent_id not in vids:
        raise HTTPException(status_code=404, detail="Agent nicht gefunden")


async def _agent_besitzer(agent_id: str, db) -> str | None:
    """Wem der Agent GEHOERT — nicht nur, wer ihn sehen darf (geteilte Agenten)."""
    from app.models.agent import Agent
    return (await db.execute(select(Agent.user_id).where(Agent.id == agent_id))).scalar_one_or_none()


async def _freigegebene_secret_ids(user, db) -> set[int]:
    """Was der Aufrufer nutzen darf (eigene, Rolle, Person) — fuer Nicht-Admins."""
    return await secret_zugriff.nutzbare_secret_ids(db, user) or set()


def _ist_admin(user) -> bool:
    from app.models.user import UserRole
    return getattr(user, "role", None) == UserRole.ADMIN


async def _assert_secret_allowed(secret_id: int, user, db) -> None:
    """403 unless the caller may USE this secret (assign it to an own agent):
    Admin, Besitzer, Rollenfreigabe oder Freigabe an diese Person (core/secret_zugriff.py)."""
    secret = await db.get(AgentSecret, secret_id)
    if secret is None:
        raise HTTPException(status_code=404, detail="Secret not found")
    if await secret_zugriff.zugang(db, user, secret) is None:
        raise HTTPException(
            status_code=403,
            detail="Dieser Key/Secret ist für dich nicht freigegeben.",
        )


def _assert_secret_managed(secret: AgentSecret, user) -> None:
    """403 unless the caller may CHANGE or DELETE this secret: admin or owner.

    Nutzen ist nicht verwalten: Wem ein Firmen-Secret per Rolle nur zur Nutzung
    freigegeben ist, durfte es bis v1.343 auch aendern und loeschen."""
    if _ist_admin(user):
        return
    if secret.owner_id and secret.owner_id == getattr(user, "id", None):
        return
    raise HTTPException(status_code=403, detail="Dieses Secret kann nur sein Besitzer oder ein Admin ändern.")


@router.patch("/{secret_id}")
async def update_secret(
    secret_id: int,
    body: SecretUpdate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    secret = await db.get(AgentSecret, secret_id)
    if not secret:
        raise HTTPException(status_code=404, detail="Secret not found")
    _assert_secret_managed(secret, user)

    should_refresh = body.value is not None or body.is_active is not None
    if body.name is not None:
        secret.name = body.name
    if body.description is not None:
        secret.description = body.description
    if body.is_active is not None:
        secret.is_active = body.is_active
    if body.value is not None:
        secret.value_encrypted = encrypt_token(body.value)

    await db.commit()
    await db.refresh(secret)
    response = _serialize(secret, user=user)
    if should_refresh:
        response["refresh"] = await _refresh_agents_for_secret(db, manager, secret_id)
    return response


@router.delete("/{secret_id}", status_code=204)
async def delete_secret(
    secret_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    secret = await db.get(AgentSecret, secret_id)
    if not secret:
        raise HTTPException(status_code=404, detail="Secret not found")
    _assert_secret_managed(secret, user)
    result = await db.execute(
        select(AgentSecretAssignment.agent_id).where(
            AgentSecretAssignment.secret_id == secret_id
        )
    )
    agent_ids = list(result.scalars().all())
    await db.delete(secret)
    await db.commit()
    if agent_ids:
        await _refresh_agents_for_secret(db, manager, secret_id, agent_ids)


# ---------------------------------------------------------------------------
# Agent assignment
# ---------------------------------------------------------------------------

@router.get("/agent/{agent_id}")
async def get_agent_secrets(
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    await _assert_agent_owned(agent_id, user, db)
    result = await db.execute(
        select(AgentSecretAssignment).where(AgentSecretAssignment.agent_id == agent_id)
    )
    assignments = result.scalars().all()
    secret_ids = [a.secret_id for a in assignments]

    secrets = []
    if secret_ids:
        s_result = await db.execute(select(AgentSecret).where(AgentSecret.id.in_(secret_ids)))
        secrets = s_result.scalars().all()

    # Wem der Agent nur GETEILT ist, der sah hier bis v1.344 alle Secrets des
    # Agenten (Name, Variable, Teile des Werts) — auch ohne Freigabe. Sichtbar ist
    # jetzt, was der Aufrufer auch in /secrets saehe; der Besitzer des Agenten
    # sieht alles, was auf seinem Agenten liegt (es steckt in seinem Container).
    if not _ist_admin(user) and await _agent_besitzer(agent_id, db) != user.id:
        freigegeben = await _freigegebene_secret_ids(user, db)
        secrets = [s for s in secrets if s.id in freigegeben or s.owner_id == user.id]

    return {"agent_id": agent_id, "secrets": [_serialize(s, user=user) for s in secrets]}


@router.post("/agent/{agent_id}/{secret_id}", status_code=201)
async def assign_secret(
    agent_id: str,
    secret_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    secret = await db.get(AgentSecret, secret_id)
    if not secret:
        raise HTTPException(status_code=404, detail="Secret not found")

    # The caller must own the target agent AND be allowed to use the secret (admin bypasses both).
    await _assert_agent_owned(agent_id, user, db)
    await _assert_secret_allowed(secret_id, user, db)
    # Nicht-Admins weisen JEDEN Key nur EIGENEN Agenten zu — nicht einem, der nur
    # geteilt ist. Frueher galt das nur fuer private Keys; ein per Person
    # freigegebener Firmen-Key liess sich so an den geteilten Agenten eines Admins
    # haengen, wo er ungefiltert ankam und ausgelesen werden konnte (03.10.2026).
    if not _ist_admin(user) and await _agent_besitzer(agent_id, db) != user.id:
        raise HTTPException(status_code=403, detail="Schlüssel nur an eigene Agenten.")

    existing = await db.execute(
        select(AgentSecretAssignment).where(
            AgentSecretAssignment.agent_id == agent_id,
            AgentSecretAssignment.secret_id == secret_id,
        )
    )
    if existing.scalar_one_or_none():
        refresh = await _refresh_agents_for_secret(db, manager, secret_id, [agent_id])
        return {"ok": True, "message": "Already assigned", "refresh": refresh}

    db.add(AgentSecretAssignment(agent_id=agent_id, secret_id=secret_id))
    await db.commit()
    refresh = await _refresh_agents_for_secret(db, manager, secret_id, [agent_id])
    return {"ok": True, "refresh": refresh}


@router.delete("/agent/{agent_id}/{secret_id}", status_code=204)
async def unassign_secret(
    agent_id: str,
    secret_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    await _assert_agent_owned(agent_id, user, db)
    # Abhaengen darf der Besitzer des Agenten immer — auch einen Key, dessen
    # Freigabe er inzwischen verloren hat (sonst bliebe er haengen).
    if not _ist_admin(user) and await _agent_besitzer(agent_id, db) != user.id:
        await _assert_secret_allowed(secret_id, user, db)
    await db.execute(
        delete(AgentSecretAssignment).where(
            AgentSecretAssignment.agent_id == agent_id,
            AgentSecretAssignment.secret_id == secret_id,
        )
    )
    await db.commit()
    await _refresh_agents_for_secret(db, manager, secret_id, [agent_id])


# ---------------------------------------------------------------------------
# Freigabe an Personen
# ---------------------------------------------------------------------------

class SecretShares(BaseModel):
    user_ids: list[str]


async def _freigaben(db: AsyncSession, secret_id: int) -> list[dict]:
    from app.models.user import User
    zeilen = (await db.execute(
        select(AgentSecretShare.user_id, User.name, User.email)
        .join(User, User.id == AgentSecretShare.user_id, isouter=True)
        .where(AgentSecretShare.secret_id == secret_id)
        .order_by(User.name)
    )).all()
    return [{"user_id": r[0], "name": r[1], "email": r[2]} for r in zeilen]


@router.get("/{secret_id}/shares")
async def list_secret_shares(
    secret_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """An welche Personen der Key freigegeben ist — nur fuer Besitzer und Admin."""
    secret = await db.get(AgentSecret, secret_id)
    if not secret:
        raise HTTPException(status_code=404, detail="Secret not found")
    _assert_secret_managed(secret, user)
    return {"secret_id": secret_id, "shares": await _freigaben(db, secret_id)}


@router.put("/{secret_id}/shares")
async def set_secret_shares(
    secret_id: int,
    body: SecretShares,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Key an genau diese Personen freigeben (ersetzt die bisherige Liste).

    Nur Besitzer und Admin. Freigegeben wird an Personen, nie an Rollen — das macht
    ein Admin in den Rollenrechten. Wer die Freigabe verliert, verliert den Key auch
    in seinen Agenten: Die werden neu gestartet und bekommen ihn nicht mehr
    (agent_manager._get_secrets_env prueft beim Einspielen).
    """
    from app.models.agent import Agent
    from app.models.user import User
    secret = await db.get(AgentSecret, secret_id)
    if not secret:
        raise HTTPException(status_code=404, detail="Secret not found")
    _assert_secret_managed(secret, user)

    gewuenscht = {str(u).strip() for u in body.user_ids if str(u).strip()}
    if gewuenscht and secret_zugriff.variable_reserviert(secret.key_name):
        raise HTTPException(
            status_code=422,
            detail=f"Keys mit der Variable {secret.key_name} lassen sich nicht an Personen freigeben "
                   "(sie steuern Laufzeit, Netzwerk oder die Plattform).",
        )
    gewuenscht.discard(str(secret.owner_id or ""))   # an sich selbst freigeben ist sinnlos
    gewuenscht.discard(str(user.id))
    if gewuenscht:
        vorhanden = set((await db.execute(select(User.id).where(User.id.in_(gewuenscht)))).scalars().all())
        if gewuenscht - vorhanden:
            raise HTTPException(status_code=422, detail="Freigabe nicht möglich — Person prüfen.")

    bisher = set((await db.execute(
        select(AgentSecretShare.user_id).where(AgentSecretShare.secret_id == secret_id)
    )).scalars().all())
    entzogen = bisher - gewuenscht
    if entzogen:
        await db.execute(delete(AgentSecretShare).where(
            AgentSecretShare.secret_id == secret_id, AgentSecretShare.user_id.in_(entzogen),
        ))
    for uid in sorted(gewuenscht - bisher):
        db.add(AgentSecretShare(secret_id=secret_id, user_id=uid, created_by=str(user.id)))
    await db.commit()

    refresh = None
    if entzogen:
        betroffen = list((await db.execute(
            select(AgentSecretAssignment.agent_id)
            .join(Agent, Agent.id == AgentSecretAssignment.agent_id)
            .where(AgentSecretAssignment.secret_id == secret_id, Agent.user_id.in_(entzogen))
        )).scalars().all())
        if betroffen:
            # Zuweisung loeschen, nicht nur ausblenden: sonst floesse der Key bei
            # einer erneuten Freigabe still zurueck.
            await db.execute(delete(AgentSecretAssignment).where(
                AgentSecretAssignment.secret_id == secret_id,
                AgentSecretAssignment.agent_id.in_(betroffen),
            ))
            await db.commit()
            refresh = await _refresh_agents_for_secret(db, manager, secret_id, betroffen)
    freigaben = await _freigaben(db, secret_id)
    if not _ist_admin(user):
        freigaben = [{k: v for k, v in f.items() if k != "email"} for f in freigaben]
    return {"secret_id": secret_id, "shares": freigaben, "refresh": refresh}
