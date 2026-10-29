"""Sekretariat und Seite „Heute“ (core/sekretariat.py).

Jeder Endpunkt handelt nur für den angemeldeten Nutzer: sein Sekretariat
(``von_nutzer``), seine Agenten darin (``_eigene_mitglieder``), sein Postfach (sein
Microsoft-Zugang) und seine Erinnerungen.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.agents import _get_agent_manager, update_agent_integrations
from app.api.tasks import _get_task_router
from app.core.agent_manager import AgentManager
from app.db.session import get_db
from app.dependencies import is_agent_principal, require_auth, require_auth_or_agent
from app.core.task_router import TaskRouter

router = APIRouter(prefix="/sekretariat", tags=["sekretariat"])


def _fehler(e: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(e))


def _graph_fehler(e: Exception) -> HTTPException:
    """Fehler bei Microsoft → 502 mit dem kurzen Grund (core/sekretariat_postfach)."""
    from app.core import sekretariat_postfach

    return HTTPException(status_code=502, detail=sekretariat_postfach.fehler_aus(e)["grund"])


async def _freigabe(db, user, approval_id: str, status: str | None = None):
    from app.core import sekretariat

    try:
        return await sekretariat.freigabe_des_nutzers(db, user, approval_id, status=status)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except sekretariat.SekretariatFehler as e:
        raise HTTPException(status_code=409, detail=str(e))


async def _team(db, user):
    from app.core import sekretariat

    team = await sekretariat.von_nutzer(db, user)
    if team is None:
        raise HTTPException(status_code=404, detail="Noch kein Sekretariat eingerichtet.")
    return team


@router.get("/heute")
async def heute(user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    from app.core import sekretariat

    return await sekretariat.heute(db, user)


class Einrichten(BaseModel):
    agent_ids: list[str] = Field(min_length=1, max_length=4)
    sichten_minuten: int = 30
    briefing: str = Field(default="07:30", max_length=5)


@router.post("/einrichten", status_code=201)
async def einrichten(body: Einrichten, user=Depends(require_auth), db: AsyncSession = Depends(get_db),
                     manager: AgentManager = Depends(_get_agent_manager)):
    """Sekretariat anlegen. Microsoft stellt der gewohnte Integrations-Weg ein (Rollenrechte,
    Nur-Lesen-Sperre, Neustart) — im Modus „Entwurf“: senden erst nach Freigabe."""
    from app.api.teams import _kein_betrachter, _wissen_sicherstellen
    from app.core import ms_access, sekretariat
    from app.models.agent import Agent

    _kein_betrachter(user)
    try:
        team = await sekretariat.einrichten(db, user, body.agent_ids, sichten_minuten=body.sichten_minuten,
                                            briefing=body.briefing)
    except sekretariat.SekretariatFehler as e:
        raise _fehler(e)
    await db.commit()
    await _wissen_sicherstellen(db, team, user)

    hinweise = []
    zugang = "read" if ms_access.read_only_enabled() else ms_access.ENTWURF
    if zugang == "read":
        hinweise.append("Microsoft ist auf dieser Anlage gesperrt (nur lesend): das Sekretariat liest Postfach und "
                        "Kalender, entwirft aber nichts. Aufheben kann das nur ein Administrator.")
    for agent_id in body.agent_ids:
        agent = await db.get(Agent, agent_id)
        cfg = dict(agent.config or {}) if agent else {}
        integrationen = list(dict.fromkeys([*(cfg.get("integrations") or []), "microsoft"]))
        # Immer herabstufen — auch „Lesen + Schreiben“: im Sekretariat sendet niemand ohne Freigabe.
        aenderung = {"integrations": integrationen, "msgraph_access": zugang}
        try:
            await update_agent_integrations(agent_id, aenderung, user=user, db=db, manager=manager)
        except HTTPException as e:
            hinweise.append(f"{agent.name if agent else agent_id}: Microsoft nicht eingestellt — {e.detail}")
            if agent is not None and cfg.get("msgraph_access") == "write":
                # Der gewohnte Weg scheiterte (z. B. Rollenrecht) — die Herabstufung gilt trotzdem.
                from sqlalchemy.orm.attributes import flag_modified

                cfg["msgraph_access"] = zugang
                agent.config = cfg
                flag_modified(agent, "config")
                await db.commit()
    return {"id": team.id, "hinweise": hinweise}


class Merke(BaseModel):
    text: str = Field(min_length=1, max_length=600)


@router.post("/regeln", status_code=201)
async def regel_anlegen(body: Merke, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    from app.core import sekretariat

    team = await _team(db, user)
    try:
        schluessel = await sekretariat.merken(db, team, body.text)
    except sekretariat.SekretariatFehler as e:
        raise _fehler(e)
    await db.commit()
    return {"schluessel": schluessel, "regeln": await sekretariat.regeln(db, team)}


@router.put("/regeln/{schluessel}")
async def regel_aendern(schluessel: str, body: Merke, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    from app.core import sekretariat

    team = await _team(db, user)
    try:
        neu = await sekretariat.regel_aendern(db, team, schluessel, body.text)
    except sekretariat.SekretariatFehler as e:
        raise _fehler(e)
    await db.commit()
    return {"schluessel": neu, "regeln": await sekretariat.regeln(db, team)}


class VorschlagEntscheid(BaseModel):
    uebernehmen: bool


@router.post("/regel-vorschlaege/{approval_id}")
async def regel_vorschlag(approval_id: str, body: VorschlagEntscheid, user=Depends(require_auth),
                          db: AsyncSession = Depends(get_db)):
    """Regel-Vorschlag aus einer Ablehnung übernehmen oder verwerfen — erst hier wird er Regel."""
    from app.core import sekretariat

    team = await _team(db, user)
    a = await _freigabe(db, user, approval_id)
    try:
        await sekretariat.regel_vorschlag_erledigen(db, team, a, body.uebernehmen)
    except sekretariat.SekretariatFehler as e:
        raise _fehler(e)
    await db.commit()
    return {"regeln": await sekretariat.regeln(db, team)}


@router.delete("/regeln/{schluessel}")
async def regel_loeschen(schluessel: str, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    from app.core import sekretariat

    team = await _team(db, user)
    try:
        await sekretariat.regel_loeschen(db, team, schluessel)
    except sekretariat.SekretariatFehler as e:
        raise _fehler(e)
    await db.commit()
    return {"regeln": await sekretariat.regeln(db, team)}


class NeueErinnerung(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    # ISO-Zeitpunkt; ohne Zeitzone gilt die der Anlage.
    faellig: datetime
    # Bezug auf eine Mail des eigenen Postfachs — Betreff und Link liest der Server selbst.
    mail_id: str | None = Field(default=None, max_length=512)


async def _besitzer_des_rufers(user, db) -> tuple[str, str]:
    """(Nutzer, für den die Erinnerung ist; wer sie anlegt). Ein Agent legt sie für seinen
    Besitzer an — nur, wenn er zu dessen Sekretariat gehört."""
    from app.core import sekretariat
    from app.models.agent import Agent
    from app.models.user import User

    if not is_agent_principal(user):
        return str(user.id), "nutzer"
    agent = await db.get(Agent, str(user.id))
    besitzer = await db.get(User, agent.user_id) if agent and agent.user_id else None
    team = await sekretariat.von_nutzer(db, besitzer) if besitzer is not None else None
    if team is None or str(user.id) not in set(team.member_agent_ids or []) | {team.lead_agent_id}:
        raise HTTPException(status_code=403, detail="Erinnerungen legen nur die Agenten des Sekretariats an.")
    return str(besitzer.id), f"agent:{agent.id}"


@router.post("/erinnerungen", status_code=201)
async def erinnerung_anlegen(body: NeueErinnerung, user=Depends(require_auth_or_agent),
                             db: AsyncSession = Depends(get_db)):
    from app.core import sekretariat

    fuer, von = await _besitzer_des_rufers(user, db)
    bezug = None
    if body.mail_id:
        if von != "nutzer":
            raise HTTPException(status_code=422, detail="Den Bezug auf eine Mail setzt nur der Nutzer.")
        try:
            bezug = await sekretariat.mail_bezug(db, user, body.mail_id)
        except sekretariat.SekretariatFehler as e:
            raise HTTPException(status_code=409, detail=str(e))
        except Exception as e:  # noqa: BLE001 — Microsoft
            raise _graph_fehler(e)
    try:
        e = await sekretariat.erinnerung_anlegen(db, fuer, body.text, body.faellig, von=von, bezug=bezug)
    except sekretariat.SekretariatFehler as e:
        raise _fehler(e)
    await db.commit()
    await db.refresh(e)
    return sekretariat.erinnerung_dict(e)


async def _eigene_erinnerung(db, user, erinnerung_id: int):
    from app.models.erinnerung import Erinnerung

    e = await db.get(Erinnerung, erinnerung_id)
    if e is None or e.user_id != str(user.id):
        raise HTTPException(status_code=404, detail="Erinnerung nicht gefunden.")
    return e


@router.post("/erinnerungen/{erinnerung_id}/erledigt")
async def erinnerung_erledigt(erinnerung_id: int, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    from datetime import timezone

    from app.core import sekretariat

    e = await _eigene_erinnerung(db, user, erinnerung_id)
    e.erledigt_at = datetime.now(timezone.utc)
    await db.commit()
    return sekretariat.erinnerung_dict(e)


class Spaeter(BaseModel):
    wann: str = Field(pattern="^(1h|morgen|woche)$")


@router.post("/erinnerungen/{erinnerung_id}/spaeter")
async def erinnerung_spaeter(erinnerung_id: int, body: Spaeter, user=Depends(require_auth),
                             db: AsyncSession = Depends(get_db)):
    """„Später“: in 1 Std., morgen 9:00 oder nächste Woche — und dann erneut melden."""
    from app.core import sekretariat

    e = await _eigene_erinnerung(db, user, erinnerung_id)
    if e.erledigt_at is not None:
        raise HTTPException(status_code=409, detail="Die Erinnerung ist schon erledigt.")
    sekretariat.erinnerung_spaeter(e, body.wann)
    await db.commit()
    return sekretariat.erinnerung_dict(e)


# ─── Freigaben: bearbeiten, ablehnen mit Begründung, nachfassen ──


class EntwurfAenderung(BaseModel):
    an: list[str] = Field(min_length=1, max_length=30)
    cc: list[str] = Field(default_factory=list, max_length=30)
    betreff: str = Field(default="", max_length=300)
    text: str = Field(default="", max_length=20000)


@router.put("/freigaben/{approval_id}/entwurf")
async def entwurf_aendern(approval_id: str, body: EntwurfAenderung, user=Depends(require_auth),
                          db: AsyncSession = Depends(get_db)):
    """Den Entwurf vor dem Freigeben ändern. Freigeben sendet danach GENAU diesen Stand."""
    from app.core import sekretariat

    a = await _freigabe(db, user, approval_id, status="pending")
    try:
        v = await sekretariat.entwurf_aendern(db, user, a, an=body.an, cc=body.cc, betreff=body.betreff,
                                              text=body.text)
    except sekretariat.SekretariatFehler as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:  # noqa: BLE001 — Microsoft
        raise _graph_fehler(e)
    await db.commit()
    return {"view": {"name": "mail", "data": v}}


class Ablehnung(BaseModel):
    grund: str | None = Field(default=None, max_length=1000)


@router.post("/freigaben/{approval_id}/ablehnen")
async def freigabe_ablehnen(approval_id: str, body: Ablehnung, user=Depends(require_auth),
                            db: AsyncSession = Depends(get_db), router_: TaskRouter = Depends(_get_task_router)):
    """Ablehnen über den gewohnten Weg (api/approvals.deny_request). Mit Begründung:
    sie geht an den Agenten zurück, und daraus entsteht ein Regel-VORSCHLAG."""
    from app.api.approvals import ApprovalDecision, deny_request
    from app.core import projekt_gespraech, sekretariat

    team = await _team(db, user)
    a = await _freigabe(db, user, approval_id, status="pending")
    grund = (body.grund or "").strip() or None
    await deny_request(str(a.id), ApprovalDecision(decision="deny", reason=grund), user=user, db=db)
    vorschlag, nachricht = await sekretariat.ablehnung_vermerken(db, team, a, grund or "")
    await db.commit()
    if nachricht is not None:
        projekt_gespraech.im_hintergrund_wecken(router_, team.id, nachricht.id)
    return {"status": "denied", "regel_vorschlag": vorschlag}


class Nachfassen(BaseModel):
    faellig: datetime


@router.post("/freigaben/{approval_id}/nachfassen", status_code=201)
async def freigabe_nachfassen(approval_id: str, body: Nachfassen, user=Depends(require_auth),
                              db: AsyncSession = Depends(get_db)):
    """„Nachfassen, wenn keine Antwort bis …“ — eine Erinnerung aus einer gesendeten Mail."""
    from app.core import sekretariat

    a = await _freigabe(db, user, approval_id)
    try:
        e = await sekretariat.nachfassen(db, user, a, body.faellig)
    except sekretariat.SekretariatFehler as err:
        raise _fehler(err)
    await db.commit()
    await db.refresh(e)
    return sekretariat.erinnerung_dict(e)


# ─── Tagesbriefing ──────────────────────────────────────────────


@router.post("/briefing", status_code=202)
async def briefing_neu(user=Depends(require_auth), db: AsyncSession = Depends(get_db),
                       router_: TaskRouter = Depends(_get_task_router)):
    """Briefing sofort neu erstellen. Läuft schon eins: 409 (Doppelstart-Sperre)."""
    from app.core import sekretariat

    team = await _team(db, user)
    try:
        task = await sekretariat.briefing_neu(db, router_, team)
    except sekretariat.SekretariatFehler as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"task_id": getattr(task, "id", None)}


# ─── Posteingang ────────────────────────────────────────────────


class MailErledigt(BaseModel):
    mail_id: str = Field(min_length=1, max_length=512)
    # Standardordner (z. B. "archive") oder die Kennung eines eigenen Ordners; leer = nur gelesen.
    ordner: str | None = Field(default=None, max_length=512)
    ordner_name: str | None = Field(default=None, max_length=200)


@router.post("/mails/erledigt")
async def mail_erledigt(body: MailErledigt, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    from app.core import sekretariat

    await _team(db, user)
    try:
        erg = await sekretariat.mail_erledigt(db, user, body.mail_id, body.ordner, body.ordner_name)
    except sekretariat.SekretariatFehler as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:  # noqa: BLE001 — Microsoft
        raise _graph_fehler(e)
    await db.commit()
    return erg


@router.get("/mails/ordner")
async def mail_ordner(user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    from app.core import sekretariat, sekretariat_postfach

    await _team(db, user)
    try:
        return {"ordner": await sekretariat_postfach.ordner(await sekretariat.zugang(db, user))}
    except sekretariat.SekretariatFehler as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:  # noqa: BLE001 — Microsoft
        raise _graph_fehler(e)


class AntwortEntwerfen(BaseModel):
    mail_id: str = Field(min_length=1, max_length=512)
    hinweis: str | None = Field(default=None, max_length=2000)


@router.post("/mails/antwort", status_code=201)
async def mail_antwort(body: AntwortEntwerfen, request: Request, user=Depends(require_auth),
                       db: AsyncSession = Depends(get_db), router_: TaskRouter = Depends(_get_task_router)):
    """„Antwort entwerfen“: ein Auftrag ans Sekretariat (derselbe Weg wie die Eingabezeile).
    Der Entwurf kommt über die Mail-Freigabe zurück nach „Wartet auf dich“."""
    from app.api.teams import ProjektAuftrag, projekt_auftrag
    from app.core import sekretariat, sekretariat_postfach

    team = await _team(db, user)
    if await sekretariat.antwort_schon_beauftragt(db, str(user.id), body.mail_id):
        raise HTTPException(status_code=409, detail=(
            "Für diese Mail ist gerade schon eine Antwort beauftragt — der Entwurf kommt nach „Wartet auf dich“."))
    try:
        mail = await sekretariat_postfach.mail_lesen(await sekretariat.zugang(db, user), body.mail_id)
    except sekretariat.SekretariatFehler as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:  # noqa: BLE001 — Microsoft
        raise _graph_fehler(e)
    await sekretariat.protokollieren(db, str(user.id), "antwort_beauftragt",
                                     f"Antwort auf „{mail['betreff'][:200]}“ beauftragt",
                                     bezug={"mail_id": body.mail_id})
    return await projekt_auftrag(team.id, ProjektAuftrag(text=sekretariat.antwort_auftrag(mail, body.hinweis),
                                                         titel="Antwort auf eine Mail entwerfen"),
                                 request, router_=router_, user=user, db=db)
