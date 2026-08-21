"""API endpoints for command approval workflow.

When agents need to execute risky commands, they request approval from users.
Approvals are persisted in the DB (CommandApproval model) and survive restarts.
A Notification is created on each new request so the user sees it in the bell + Telegram.
"""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log_redaction import scrub_log
from app.db.session import get_db
from app.dependencies import require_agent_access, require_auth, verify_agent_token
from app.models.agent import Agent
from app.models.audit_log import AuditLog, AuditEventType
from app.models.command_approval import ApprovalStatus, CommandApproval
from app.models.notification import Notification
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/approvals", tags=["approvals"])

# Sentinel epic #588, issue #591: feed this endpoint's already-server-side
# decision points (a command was submitted for approval; a human approved,
# denied or cancelled it) into the Sentinel's event pipeline (#590) as a
# second, orchestrator-generated telemetry source alongside `agent:*:logs`.
#
# Der oben beschriebene Vorbehalt ist mit #590 aufgeloest: frueher ging dieses
# Ereignis auf `agents:logs:all`, den global beschreibbaren Kanal. Der Sentinel
# liest den nicht mehr — er hoert je Agent auf `agent:{id}:logs` und leitet die
# Zuordnung aus dem Kanalnamen ab statt aus dem Feld `agent_id`. Deshalb muss
# auch dieses Ereignis in den Namensraum des betroffenen Agenten, sonst erreicht
# es den Sentinel nicht mehr.
def _sentinel_pipeline_channel(agent_id: str) -> str:
    return f"agent:{agent_id}:logs"


async def _publish_sentinel_event(
    redis: "RedisService | None", agent_id: str, event_type: str, data: dict
) -> None:
    """Emit one orchestrator-generated event for the Sentinel pipeline (#591).

    Best-effort like `_publish_notification` — a missing/unavailable Redis
    client must never break the approval flow itself.
    """
    if not redis or not redis.client or not agent_id:
        return
    try:
        event = json.dumps({
            "agent_id": agent_id,
            "task_id": "",
            "type": event_type,
            "data": {**data, "source": "orchestrator"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        await redis.client.publish(_sentinel_pipeline_channel(agent_id), event)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[Sentinel] Failed to publish {event_type} event for {scrub_log(agent_id)}: {e}")


def _get_redis() -> RedisService | None:
    from app.api.ws import _redis
    return _redis


# ══════════════════════════════════════════════════════════════════════════════
# SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class ApprovalRequest(BaseModel):
    tool: str | None = None
    input: dict | None = None
    reasoning: str | None = None
    risk_level: str = "medium"  # "low", "medium", "high", "blocked"
    question: str | None = None
    options: list[str] | None = None
    context: str | None = None
    target_channel: str = "all"
    # Zu welchem Auftrag die Rueckfrage gehoert. Die Spalte gab es im Modell schon,
    # nur hat sie nie jemand gefuellt — in der Ablage stand die Frage damit ohne
    # den Auftrag, um den es ging.
    task_id: str | None = None
    # Wofuer die Freigabe steht: gewoehnliche Rueckfrage, Eskalation wegen
    # Unsicherheit (#389), erschoepfte Selbstheilung (#390), Reflexionsvorschlag.
    kind: str | None = None
    # Eine Ansicht statt einer Liste von Textoptionen: {"name": ..., "data": {...}}.
    #
    # Der Name zeigt auf eine Komponente, die IM FRONTEND liegt — der Agent
    # liefert bewusst kein Markup. Ein Modell, das HTML in die Oberflaeche
    # schreiben darf, ist ein Einfallstor mit Zwischenschritt.
    #
    # Bewusst hier und nicht als eigener Endpunkt: eine Ansicht ist eine
    # Rueckfrage, die anders aussieht. Sie braucht dasselbe Anhalten des Agenten,
    # denselben Rueckweg (``user_response``), dieselbe Ablage und dieselbe
    # Zustellung nach Telegram und iOS. Ein zweiter Weg daneben muesste das alles
    # noch einmal koennen — und wuerde beim ersten Kanal auseinanderlaufen.
    view: dict | None = None


class ConfidenceCheck(BaseModel):
    """Ein Agent meldet, wie sicher er sich ist — und fragt, ob das reicht."""

    confidence: float | str
    question: str
    context: str | None = None
    options: list[str] | None = None
    task_id: str | None = None
    target_channel: str = "all"


class ApprovalDecision(BaseModel):
    decision: str  # "approve" or "deny"
    reason: str | None = None


class ApprovalAnswer(BaseModel):
    """Die Antwort auf eine Rueckfrage — die gewaehlte Option oder freier Text.

    Fragt ein Agent per ``request_approval`` mit Antwortmoeglichkeiten nach, dann
    ist „genehmigt" keine Antwort: er will wissen, WELCHE. Ueber Telegram ging
    das schon immer (die Wahl landet in ``user_response``), in der Weboberflaeche
    nicht — dort standen die Optionen nur als Text da, und wer wirklich antworten
    wollte, musste ABLEHNEN und die Antwort ins Begruendungsfeld tippen.

    Optional, damit die bisherigen Aufrufer ohne Rumpf unveraendert
    weiterfunktionieren.
    """

    answer: str | None = None


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

#: Was eine Ansicht sein darf. Der Name zeigt auf eine Komponente im Frontend;
#: was hier nicht steht, gibt es nicht. Bewusst eine Liste und keine Pruefung auf
#: „sieht harmlos aus": ein Name, den die Oberflaeche nicht kennt, wuerde dort zu
#: einer leeren Flaeche — der Agent haette angehalten und niemand koennte
#: antworten.
ERLAUBTE_ANSICHTEN = {
    # Der kleinste Beweis: aus mehreren Bildern eines waehlen. Deckt den
    # haeufigsten Fall ab (Marketing-Agent erzeugt Varianten) und braucht keine
    # Bearbeitung im Browser.
    "image_choice",
}

#: Obergrenze fuer die Nutzlast einer Ansicht. Sie liegt in ``meta`` derselben
#: Zeile wie die Freigabe und geht ueber denselben Redis-Kanal — ein Agent, der
#: dort ein eingebettetes Bild ablegt, wuerde beides sprengen. Bilder gehoeren
#: als Pfad hinein, nicht als Inhalt.
ANSICHT_MAX_ZEICHEN = 8000


def _saubere_ansicht(roh: dict) -> dict | None:
    """Nimmt nur, was die Oberflaeche auch zeichnen kann."""
    name = (roh or {}).get("name")
    if name not in ERLAUBTE_ANSICHTEN:
        logger.warning("Unbekannte Ansicht %r angefordert — wird ignoriert, die "
                       "Rueckfrage laeuft ueber die Textoptionen", scrub_log(str(name)))
        return None
    daten = (roh or {}).get("data") or {}
    if len(json.dumps(daten, default=str)) > ANSICHT_MAX_ZEICHEN:
        logger.warning("Ansicht %r zu gross — wird ignoriert", scrub_log(str(name)))
        return None
    return {"name": name, "data": daten}


def _approval_to_dict(a: CommandApproval) -> dict:
    meta = a.meta or {}
    return {
        "approval_id": str(a.id),
        "agent_id": a.agent_id,
        "tool": a.command,
        "reasoning": a.description,
        "risk_level": a.risk_level,
        "status": a.status,
        "input": meta.get("input") or {},
        "question": meta.get("question"),
        "options": meta.get("options"),
        "view": meta.get("view"),
        "context": meta.get("context"),
        "target_channel": meta.get("target_channel"),
        "meta": meta,
        "created_at": a.created_at.isoformat(),
        "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
        "user_response": a.user_response,
    }


async def _publish_notification(redis: RedisService | None, notif: Notification) -> None:
    if not redis or not redis.client:
        return
    event = json.dumps({
        "type": "notification",
        "data": {
            "id": notif.id,
            "agent_id": notif.agent_id,
            "type": notif.type,
            "title": notif.title,
            "message": notif.message,
            "priority": notif.priority,
            "read": notif.read,
            "action_url": notif.action_url,
            "meta": notif.meta,
            "created_at": notif.created_at.isoformat(),
        },
    })
    await redis.client.publish("notifications:live", event)


async def _push_ios_for_agent(
    db: AsyncSession,
    agent_id: str,
    title: str,
    message: str,
    data: dict | None = None,
) -> None:
    try:
        from app.core.push import push_to_user

        agent = (await db.execute(
            select(Agent).where(Agent.id == agent_id)
        )).scalar_one_or_none()
        if agent and agent.user_id:
            await push_to_user(db, agent.user_id, title, message or title, data=data)
    except Exception:  # noqa: BLE001
        logger.exception("APNs push failed for approval")


# ══════════════════════════════════════════════════════════════════════════════
# AGENT ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/request")
async def request_approval(
    body: ApprovalRequest,
    agent_auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    agent_id = agent_auth["agent_id"]

    if body.risk_level == "blocked":
        logger.warning(f"Agent {scrub_log(agent_id)} attempted blocked command: {scrub_log(body.tool)}")
        raise HTTPException(status_code=403, detail="This command is forbidden and cannot be executed even with approval.")

    is_question = bool(body.question and not body.tool)
    approval_tool = body.tool or "user_decision"
    reasoning = body.reasoning or body.context or body.question or ""

    # Dieselbe Frage steht schon offen? Dann keine zweite Zeile.
    #
    # Der Anlass: ein Agent, dessen Einrichtung unvollständig ist, fragt bei jedem
    # proaktiven Lauf, ob er seinen Laufstatus notieren darf. Niemand antwortet, die
    # Frage läuft in ihre Zeitgrenze, eine Stunde später fragt er erneut — auf einer
    # Anlage waren so 570 nahezu gleichlautende Anfragen aufgelaufen. Aus Sicht des
    # Menschen ist das EINE Entscheidung, nicht 570.
    # Verglichen wird Werkzeug UND Begründung UND die Frage selbst. Die Frage steht
    # in ``meta`` und ist in SQL über die Datenbanken hinweg schlecht vergleichbar —
    # deshalb die Vorauswahl im Query, der Rest hier. Die Vorauswahl ist klein: im
    # Normalfall hat ein Agent keine, im Flutfall genau eine offene Frage.
    candidates = (await db.execute(
        select(CommandApproval).where(
            CommandApproval.agent_id == agent_id,
            CommandApproval.status == ApprovalStatus.PENDING,
            CommandApproval.command == approval_tool,
            CommandApproval.description == reasoning,
        ).order_by(CommandApproval.created_at.desc()).limit(50)
    )).scalars().all()
    existing = next(
        (c for c in candidates if (c.meta or {}).get("question") == body.question),
        None,
    )
    if existing is not None:
        meta = dict(existing.meta or {})
        meta["repeats"] = int(meta.get("repeats") or 1) + 1
        meta["last_asked_at"] = datetime.now(timezone.utc).isoformat()
        existing.meta = meta
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(existing, "meta")
        await db.commit()
        logger.info(
            "[Freigaben] Agent %s fragt dasselbe zum %s. Mal — Anfrage %s wiederverwendet",
            scrub_log(agent_id), meta["repeats"], existing.id,
        )
        # Der Agent wartet auf DIESE Kennung — er poll't sie wie beim ersten Mal.
        return {
            "approval_id": str(existing.id),
            "status": "pending",
            "message": "Diese Frage steht bereits offen und wartet auf eine Entscheidung.",
            "repeats": meta["repeats"],
        }
    meta = {
        "input": body.input or {},
        "question": body.question,
        "options": body.options or (["Approve", "Deny"] if is_question else None),
        "context": body.context,
        "target_channel": body.target_channel,
    }
    if body.kind:
        meta["kind"] = body.kind
    if body.task_id:
        meta["task_id"] = body.task_id
    if body.view:
        meta["view"] = _saubere_ansicht(body.view)

    # Persist to DB
    approval = CommandApproval(
        agent_id=agent_id,
        task_id=body.task_id,
        command=approval_tool,
        description=reasoning,
        risk_level=body.risk_level,
        status=ApprovalStatus.PENDING,
        meta=meta,
    )
    db.add(approval)
    await db.commit()
    await db.refresh(approval)

    # Create notification so user sees it in bell + Telegram
    notif = Notification(
        agent_id=agent_id,
        type="approval",
        title=f"Approval required: {approval_tool}",
        message=reasoning,
        priority="high",
        action_url="/approvals",
        meta={
            "approval_id": approval.id,
            "risk_level": body.risk_level,
            "input": body.input or {},
            "question": body.question,
            "options": meta["options"],
            "context": body.context,
            "target_channel": body.target_channel,
        },
    )
    db.add(notif)
    await db.commit()
    await db.refresh(notif)

    redis = _get_redis()
    await _publish_notification(redis, notif)

    # Fetch agent name for richer notification text
    agent_name = agent_id
    try:
        _agent = await db.scalar(select(Agent).where(Agent.id == agent_id))
        if _agent:
            agent_name = _agent.name or agent_id
    except Exception:
        pass

    # Build a human-readable approval message
    risk_emoji = {"low": "🟡", "medium": "🟠", "high": "🔴", "critical": "🚨"}.get(body.risk_level, "⚠️")
    tg_lines = [f"{risk_emoji} *Approval nötig — {agent_name}*"]
    if body.question:
        tg_lines.append(f"\n❓ {body.question}")
    if body.tool and body.tool != "user_decision":
        tg_lines.append(f"🔧 Tool: `{body.tool}`")
    if reasoning:
        tg_lines.append(f"\n{reasoning}")
    tg_lines.append(f"\n_Risiko: {body.risk_level}_")
    tg_message = "\n".join(tg_lines)

    # Always notify Telegram — every approval is time-sensitive
    try:
        from app.api.notifications import _send_telegram, NotificationCreate
        await _send_telegram(
            NotificationCreate(
                agent_id=agent_id,
                type="approval",
                title=notif.title,
                message=tg_message,
                priority="high",
                action_url="/approvals",
                meta=notif.meta,
            ),
            redis,
            notif_id=notif.id,
        )
    except Exception as e:
        logger.warning(f"Telegram notification failed for approval {approval.id}: {e}")

    # Always notify iOS via APNs — every approval needs immediate attention
    await _push_ios_for_agent(
        db,
        agent_id,
        f"{risk_emoji} {agent_name}: Approval nötig",
        body.question or reasoning or notif.title,
        data={
            "notification_id": str(notif.id),
            "agent_id": agent_id,
            "type": "approval",
            "action_url": "/approvals",
            "approval_id": str(approval.id),
            "approval_screen": "true",
            "meta": notif.meta or {},
        },
    )

    # Publish to agent's chat WS so approval appears inline in the chat
    if redis and redis.client:
        try:
            ws_event = json.dumps({
                "type": "approval_request",
                "data": {
                    "approval_id": str(approval.id),
                    "notification_id": str(notif.id),
                    "question": body.question or reasoning,
                    "tool": body.tool,
                    "risk_level": body.risk_level,
                    "options": meta.get("options") or ["Approve", "Deny"],
                    "reasoning": reasoning,
                }
            })
            await redis.client.publish(f"agent:{agent_id}:chat:response", ws_event)
        except Exception as e:
            logger.warning(f"Failed to publish approval to chat WS: {e}")

    audit_entry = AuditLog(
        agent_id=agent_id,
        approval_id=str(approval.id),
        event_type=AuditEventType.APPROVAL_REQUESTED,
        command=approval_tool,
        outcome="pending",
        meta={"risk_level": body.risk_level, "reasoning": reasoning, **meta},
    )
    db.add(audit_entry)
    await db.commit()

    # Sentinel event (#591): command submitted + policy verdict + approval
    # requested collapse into one server-observed moment here — `risk_level`
    # already IS the policy verdict (computed client-side against the policies
    # `command_policies.py` served, then reported back in this same request).
    #
    # `reasoning` is agent-supplied free text and can carry secrets or real
    # names (see PR #596 review) — `scrub_log()` only strips control chars for
    # log-injection safety, it does not redact content. Rather than trust a
    # regex-based redactor to catch every credential/PII shape, the Sentinel
    # payload is kept to structured, non-sensitive fields only; a full-text
    # audit trail of `reasoning` already exists in AuditLog above for anyone
    # with DB access, which is a narrower trust boundary than this pub/sub
    # channel.
    await _publish_sentinel_event(
        redis, agent_id, "approval_requested",
        {
            "approval_id": str(approval.id),
            "tool": approval_tool,
            "risk_level": body.risk_level,
        },
    )

    logger.info(
        f"Approval {approval.id} created for agent {scrub_log(agent_id)} - "
        f"{scrub_log(body.tool)} (risk: {scrub_log(body.risk_level)})"
    )

    return {
        "approval_id": str(approval.id),
        "status": "pending",
        "message": "Approval request created. Waiting for user decision.",
    }


@router.post("/confidence")
async def confidence_gate(
    body: ConfidenceCheck,
    agent_auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Konfidenz melden — der Server entscheidet, ob das reicht (#389).

    Die Schwelle liegt bewusst **hier** und nicht im Agenten: würde er selbst
    beurteilen, ob seine 40 % genügen, wäre diese Beurteilung genauso unsicher wie
    die Antwort. Er meldet eine Zahl, die Regel gehört dem Betreiber.

    Reicht die Sicherheit, kostet der Aufruf **nichts** — kein Mensch wird
    behelligt, keine Freigabe entsteht. Genau deshalb kann der Agent ihn
    grosszügig verwenden, ohne dass die Ablage volläuft.
    """
    from app.core.confidence import (
        build_question,
        is_enabled,
        normalize_confidence,
        should_escalate,
        threshold_for,
    )
    from app.models.task import Task

    agent_id = agent_auth["agent_id"]
    try:
        confidence = normalize_confidence(body.confidence)
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"Konfidenz unlesbar: {e}")

    agent = (await db.execute(
        select(Agent).where(Agent.id == agent_id)
    )).scalar_one_or_none()
    agent_config = getattr(agent, "config", None)

    task_meta = None
    if body.task_id:
        task = (await db.execute(
            # Nur der eigene Auftrag: sonst koennte ein Agent die Schwelle eines
            # fremden Auftrags erben.
            select(Task).where(Task.id == body.task_id, Task.agent_id == agent_id)
        )).scalar_one_or_none()
        task_meta = getattr(task, "metadata_", None)

    threshold = threshold_for(agent_config, task_meta)
    escalate = is_enabled(agent_config) and should_escalate(confidence, threshold)

    if not escalate:
        return {
            "escalated": False,
            "proceed": True,
            "confidence": confidence,
            "threshold": threshold,
            "message": (
                f"Sicherheit {confidence}% ≥ Schwelle {threshold}% — mach weiter."
            ),
        }

    created = await request_approval(
        ApprovalRequest(
            question=build_question(body.question, confidence, threshold),
            options=body.options or ["Ich entscheide", "Mach weiter wie geplant"],
            context=body.context,
            reasoning=body.context or body.question,
            risk_level="medium",
            target_channel=body.target_channel,
            task_id=body.task_id,
            kind="low_confidence",
        ),
        agent_auth,
        db,
    )
    logger.info(
        "[Konfidenz] Agent %s eskaliert bei %s%% (Schwelle %s%%) → Freigabe %s",
        scrub_log(agent_id), confidence, threshold, created.get("approval_id"),
    )
    return {
        "escalated": True,
        "proceed": False,
        "confidence": confidence,
        "threshold": threshold,
        "approval_id": created.get("approval_id"),
        "message": (
            f"Sicherheit {confidence}% < Schwelle {threshold}% — an einen Menschen "
            "weitergereicht. Führe NICHT aus, warte auf die Entscheidung."
        ),
    }


@router.get("/check/{approval_id}")
async def check_approval_status(
    approval_id: str,
    agent_auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(CommandApproval).where(CommandApproval.id == int(approval_id)))
    approval = result.scalar_one_or_none()

    if not approval:
        raise HTTPException(status_code=404, detail="Approval request not found")
    if approval.agent_id != agent_auth["agent_id"]:
        raise HTTPException(status_code=403, detail="Not authorized for this request")

    return {
        "approval_id": str(approval.id),
        "status": approval.status,
        "user_response": approval.user_response,
        "resolved_at": approval.resolved_at.isoformat() if approval.resolved_at else None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# USER ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/pending")
async def list_pending_approvals(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    from app.models.user import UserRole

    query = select(CommandApproval).where(CommandApproval.status == ApprovalStatus.PENDING)

    if user.role not in (UserRole.ADMIN, UserRole.MANAGER):
        agent_result = await db.execute(select(Agent.id).where(Agent.user_id == user.id))
        allowed_ids = list(agent_result.scalars().all())
        query = query.where(CommandApproval.agent_id.in_(allowed_ids))

    query = query.order_by(CommandApproval.created_at.desc())
    result = await db.execute(query)
    approvals = result.scalars().all()

    return {"approvals": [_approval_to_dict(a) for a in approvals], "count": len(approvals)}


async def _visible_agent_ids(user, db: AsyncSession) -> list[str] | None:
    """Agenten, deren Freigaben dieser Nutzer sehen darf. ``None`` = alle.

    Dieselbe Regel wie in ``list_pending_approvals`` — an EINER Stelle, damit die
    Zählung im Menü nicht eine andere Menge meint als die Liste darunter.
    """
    from app.models.user import UserRole

    if user.role in (UserRole.ADMIN, UserRole.MANAGER):
        return None
    return list((await db.execute(
        select(Agent.id).where(Agent.user_id == user.id)
    )).scalars().all())


@router.get("/pending/count")
async def count_pending_approvals(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Nur die Zahl — für das Abzeichen im Menü.

    Eigener Endpunkt statt ``/pending``: das Menü fragt im Takt, und die volle
    Liste kann Hunderte Einträge samt Begründungstexten haben. Für eine Zahl über
    einem Menüpunkt ist das die falsche Datenmenge.
    """
    query = select(func.count(CommandApproval.id)).where(
        CommandApproval.status == ApprovalStatus.PENDING
    )
    allowed = await _visible_agent_ids(user, db)
    if allowed is not None:
        query = query.where(CommandApproval.agent_id.in_(allowed))
    return {"count": int((await db.execute(query)).scalar() or 0)}


@router.delete("/pending")
async def clear_pending_approvals(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Alle offenen Freigaben verwerfen.

    Für den Fall, dass sich Hunderte gleichartiger Anfragen angesammelt haben —
    ein wartender Agent hat seine Frage längst abgebrochen, die Zeile blieb aber
    stehen. Sie einzeln wegzuklicken ist keine Arbeit, die ein Mensch tun sollte.

    Verworfen wird als **abgelehnt**, nicht gelöscht: die Prüfspur muss erhalten
    bleiben — es gab eine Anfrage, und sie wurde nicht genehmigt.
    """
    query = select(CommandApproval).where(CommandApproval.status == ApprovalStatus.PENDING)
    allowed = await _visible_agent_ids(user, db)
    if allowed is not None:
        query = query.where(CommandApproval.agent_id.in_(allowed))

    rows = (await db.execute(query)).scalars().all()
    now = datetime.now(timezone.utc)
    for approval in rows:
        approval.status = ApprovalStatus.DENIED
        approval.resolved_at = now
        approval.user_response = f"Sammelverwerfung durch {user.email}"
    await db.commit()

    # Wartende Agenten aufwecken, statt sie in ihre Zeitgrenze laufen zu lassen.
    redis = _get_redis()
    if redis and redis.client:
        for approval in rows:
            try:
                await redis.client.publish(
                    f"approval:{approval.id}",
                    json.dumps({"status": "denied", "approval_id": str(approval.id)}),
                )
            except Exception:  # noqa: BLE001
                pass

    logger.info("[Freigaben] %s offene Anfragen von %s verworfen", len(rows), scrub_log(user.email))
    return {"cleared": len(rows)}


@router.get("/all")
async def list_all_approvals(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    from app.models.user import UserRole
    query = select(CommandApproval).order_by(CommandApproval.created_at.desc()).limit(100)
    if user.role not in (UserRole.ADMIN, UserRole.MANAGER):
        agent_result = await db.execute(select(Agent.id).where(Agent.user_id == user.id))
        allowed_ids = list(agent_result.scalars().all())
        query = select(CommandApproval).where(CommandApproval.agent_id.in_(allowed_ids)).order_by(CommandApproval.created_at.desc()).limit(100)
    result = await db.execute(query)
    approvals = result.scalars().all()
    return {"approvals": [_approval_to_dict(a) for a in approvals], "count": len(approvals)}


@router.post("/{approval_id}/approve")
async def approve_request(
    approval_id: str,
    body: ApprovalAnswer | None = None,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(CommandApproval).where(CommandApproval.id == int(approval_id)))
    approval = result.scalar_one_or_none()

    if not approval:
        raise HTTPException(status_code=404, detail="Approval request not found")

    await require_agent_access(approval.agent_id, user, db)

    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=400, detail=f"Request already {approval.status}")

    # Reflection proposals: applying the change IS the approval effect (no agent
    # container is waiting on Redis). Apply FIRST — if it fails, the approval
    # stays pending so the admin can retry.
    applied = None
    if (approval.meta or {}).get("kind") == "reflection":
        from app.services.reflection_service import apply_reflection_approval
        try:
            applied = await apply_reflection_approval(db, approval)
        except Exception as e:  # noqa: BLE001
            logger.exception("Reflection approval %s apply failed", approval.id)
            raise HTTPException(status_code=500, detail=f"Anwendung fehlgeschlagen: {e}")

    approval.status = ApprovalStatus.APPROVED
    approval.resolved_at = datetime.now(timezone.utc)
    # Hat der Nutzer eine Option gewaehlt oder etwas geschrieben, ist DAS die
    # Antwort. Vorher stand hier immer „Approved by <mail>" — bei einer
    # Rueckfrage mit vier Antwortmoeglichkeiten erfuhr der Agent also nie,
    # welche gemeint war, und fragte im naechsten Zug erneut.
    antwort = (body.answer or "").strip() if body else ""
    approval.user_response = antwort or f"Approved by {user.email}"

    audit_entry = AuditLog(
        agent_id=approval.agent_id,
        approval_id=approval_id,
        event_type=AuditEventType.COMMAND_APPROVED,
        command=f"{approval.command} {(approval.meta or {}).get('input', {})}",
        outcome="success",
        user_id=str(user.id),
        meta={"risk_level": approval.risk_level, "reasoning": approval.description,
              **({"applied": applied} if applied else {})},
    )
    db.add(audit_entry)
    await db.commit()

    # Notify agent via Redis so it can stop polling
    redis = _get_redis()
    if redis and redis.client:
        await redis.client.publish(
            f"approval:{approval.id}",
            # ``reason`` ist derselbe Schluessel, den der Telegram-Weg seit jeher
            # mit der gewaehlten Option fuellt — die Agentenseite braucht dadurch
            # keine Aenderung.
            json.dumps({"status": "approved", "approval_id": str(approval.id),
                        **({"reason": antwort} if antwort else {})}),
        )

    # Sentinel event (#591): approval result. Emitted here — the single moment
    # the decision is actually written — rather than at the agent-facing
    # `/check/{id}` poll endpoint the issue originally pointed at, which fires
    # every ~2s while pending and would otherwise re-publish the same result
    # on every poll after resolution.
    await _publish_sentinel_event(
        redis, approval.agent_id, "approval_resolved",
        {"approval_id": approval_id, "status": "approved", "risk_level": approval.risk_level},
    )

    logger.info(f"Approval {approval.id} approved by user {user.id}")
    return {
        "approval_id": approval_id,
        "status": "approved",
        "applied": applied,
        "message": "Übernommen." if applied else "Command approved. Agent will proceed.",
    }


@router.post("/{approval_id}/deny")
async def deny_request(
    approval_id: str,
    decision: ApprovalDecision,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(CommandApproval).where(CommandApproval.id == int(approval_id)))
    approval = result.scalar_one_or_none()

    if not approval:
        raise HTTPException(status_code=404, detail="Approval request not found")

    await require_agent_access(approval.agent_id, user, db)

    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=400, detail=f"Request already {approval.status}")

    approval.status = ApprovalStatus.DENIED
    approval.resolved_at = datetime.now(timezone.utc)
    approval.user_response = decision.reason or "Denied by user"

    audit_entry = AuditLog(
        agent_id=approval.agent_id,
        approval_id=approval_id,
        event_type=AuditEventType.COMMAND_DENIED,
        command=f"{approval.command} {(approval.meta or {}).get('input', {})}",
        outcome="blocked",
        user_id=str(user.id),
        meta={"risk_level": approval.risk_level, "deny_reason": decision.reason},
    )
    db.add(audit_entry)
    await db.commit()

    redis = _get_redis()
    if redis and redis.client:
        await redis.client.publish(
            f"approval:{approval.id}",
            json.dumps({"status": "denied", "approval_id": str(approval.id), "reason": decision.reason}),
        )

    # Sentinel event (#591): approval result, see approve_request for why this
    # is emitted at the resolution endpoint rather than the polling one.
    # `decision.reason` is human-supplied free text with the same secret/PII
    # risk as `reasoning` above (PR #596 review) — deliberately left out of
    # the Sentinel payload for the same reason; the full reason is still
    # recorded in `approval.user_response` / AuditLog.
    await _publish_sentinel_event(
        redis, approval.agent_id, "approval_resolved",
        {"approval_id": approval_id, "status": "denied", "risk_level": approval.risk_level},
    )

    logger.info(f"Approval {approval.id} denied by user {user.id}")
    return {"approval_id": approval_id, "status": "denied", "reason": decision.reason, "message": "Command denied."}


@router.delete("/{approval_id}")
async def cancel_approval_request(
    approval_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(CommandApproval).where(CommandApproval.id == int(approval_id)))
    approval = result.scalar_one_or_none()

    if not approval:
        raise HTTPException(status_code=404, detail="Approval request not found")
    # Ownership: only the agent's owner/admin may cancel its pending approval
    # (mirrors approve_request/deny_request).
    await require_agent_access(approval.agent_id, user, db)

    approval.status = ApprovalStatus.DENIED
    approval.resolved_at = datetime.now(timezone.utc)
    approval.user_response = "Cancelled by user"
    await db.commit()

    # Sentinel event (#591): a cancel is also a resolution outcome — a Sentinel
    # rule tracking "was this risky command ever actually approved?" needs to
    # see it too, not just approve/deny.
    await _publish_sentinel_event(
        _get_redis(), approval.agent_id, "approval_resolved",
        {"approval_id": approval_id, "status": "cancelled", "risk_level": approval.risk_level},
    )

    return {"message": "Approval request cancelled"}
