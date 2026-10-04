"""Ein Weg ins Prüfprotokoll (#908).

Bis hierhin schrieb jede Stelle ihre ``AuditLog``-Zeile selbst — mit eigener
Vorstellung davon, was ``agent_id`` bei Verwaltungsschritten heißt, und ohne
Namen. Das Ergebnis im Markttest: eine mit „Abbrechen“ beantwortete Rückfrage
stand als „command approved · user_decision {} · success“ im Protokoll, ohne
Person, mit Agentenkennung statt Namen. Ein Prüfer kann damit nichts anfangen.

Hier liegen deshalb
- ``protokolliere``: der eine Helfer, über den neue Einträge entstehen. Er
  merkt sich den Agentennamen im Eintrag selbst, damit der Name auch nach dem
  Löschen des Agenten noch lesbar bleibt, und wirft nie — ein Protokollfehler
  darf die eigentliche Handlung nicht verhindern.
- ``freigabe_entschieden``: die EINE Stelle, an der eine Freigabe ihren
  Entscheider bekommt (``resolved_by_user_id``, #897) und ihren Protokolleintrag.
  Alle Wege — Weboberfläche, Benachrichtigung, Telegram, Sammelverwerfung,
  Ablauf — rufen sie auf. Stünde das an jeder Stelle einzeln, liefe der
  nächste neue Weg wieder am Protokoll vorbei.
"""

from __future__ import annotations

import logging
from typing import Any

from app.models.audit_log import AuditEventType, AuditLog

logger = logging.getLogger(__name__)

#: Pseudo-Kennungen in ``agent_id`` für Einträge ohne Agentenbezug. Sie kollidieren
#: nie mit echten Agenten-IDs (8 Hex-Zeichen) und landen damit nie in der
#: Sichtbarkeitsmenge eines Mitglieds: Anmelde- und Verwaltungsereignisse sehen
#: nur Administratoren.
AKTEUR_ANMELDUNG = "auth"
AKTEUR_VERWALTUNG = "admin"
AKTEUR_SYSTEM = "system"
AKTEURE_OHNE_AGENT = frozenset({AKTEUR_ANMELDUNG, AKTEUR_VERWALTUNG, AKTEUR_SYSTEM, "global", "user"})

#: Was ein Mensch oder die Plattform entscheidet, darf ein Agent nicht über
#: ``POST /audit/log`` selbst eintragen — sonst könnte er sich eine Freigabe oder
#: eine Anmeldung ins Protokoll schreiben, die es nie gab.
NUR_SERVERSEITIG = frozenset({
    AuditEventType.COMMAND_APPROVED.value,
    AuditEventType.COMMAND_DENIED.value,
    AuditEventType.QUESTION_ANSWERED.value,
    AuditEventType.APPROVAL_CANCELLED.value,
    AuditEventType.APPROVAL_EXPIRED.value,
    AuditEventType.LOGIN_SUCCEEDED.value,
    AuditEventType.LOGIN_FAILED.value,
    AuditEventType.LOGOUT.value,
    AuditEventType.USER_CREATED.value,
    AuditEventType.USER_UPDATED.value,
    AuditEventType.USER_DELETED.value,
    AuditEventType.PASSWORD_RESET.value,
    AuditEventType.SETTINGS_CHANGED.value,
    AuditEventType.ROLE_CHANGED.value,
    AuditEventType.MCP_SERVER_CHANGED.value,
    AuditEventType.AUDIT_EXPORTED.value,
    AuditEventType.MFA_ENABLED.value,
    AuditEventType.MFA_DISABLED.value,
    AuditEventType.MFA_RESET.value,
    AuditEventType.MFA_CODE_FAILED.value,
    AuditEventType.RETENTION_PURGED.value,
})

#: Werkzeugname, unter dem eine reine Rückfrage (ohne Befehl) abgelegt wird —
#: siehe ``api/approvals.request_approval``.
RUECKFRAGE_WERKZEUG = "user_decision"


def _wert(ereignis: AuditEventType | str) -> str:
    return ereignis.value if isinstance(ereignis, AuditEventType) else str(ereignis)


async def _agentenname(db, agent_id: str | None) -> str | None:
    if not agent_id or agent_id in AKTEURE_OHNE_AGENT:
        return None
    try:
        from app.models.agent import Agent

        agent = await db.get(Agent, agent_id)
        return (getattr(agent, "name", None) or None) if agent else None
    except Exception:  # noqa: BLE001 — Name ist Komfort, kein Grund zu scheitern
        return None


async def protokolliere(
    db,
    ereignis: AuditEventType | str,
    *,
    agent_id: str | None = None,
    user_id: str | None = None,
    command: str | None = None,
    outcome: str = "success",
    approval_id: str | int | None = None,
    task_id: str | None = None,
    meta: dict[str, Any] | None = None,
) -> AuditLog | None:
    """Einen Eintrag vormerken (``db.add``), NICHT festschreiben.

    Der Aufrufer schreibt fest — so landet der Eintrag in derselben Transaktion
    wie die Handlung selbst: entweder beides oder keins.

    ``agent_id=None`` heißt Verwaltungsschritt (``admin``). Bei echten Agenten
    wird der Name mitgeschrieben (``meta.agent_name``), weil Agenten hart gelöscht
    werden — ohne ihn stünde nach dem Löschen nur noch eine Kennung da.
    """
    try:
        aid = str(agent_id) if agent_id else AKTEUR_VERWALTUNG
        daten = dict(meta or {})
        if "agent_name" not in daten:
            name = await _agentenname(db, aid)
            if name:
                daten["agent_name"] = name
        eintrag = AuditLog(
            agent_id=aid,
            task_id=task_id,
            approval_id=str(approval_id) if approval_id is not None else None,
            event_type=_wert(ereignis),
            command=command,
            outcome=outcome,
            user_id=str(user_id) if user_id else None,
            meta=daten or None,
        )
        db.add(eintrag)
        return eintrag
    except Exception:  # noqa: BLE001 — Protokoll darf die Handlung nie verhindern
        logger.warning("Prüfprotokoll: Eintrag %s konnte nicht vorgemerkt werden",
                       _wert(ereignis), exc_info=True)
        return None


def ist_rueckfrage(approval) -> bool:
    """Eine Rückfrage will eine ANTWORT, keine Erlaubnis.

    Sie entsteht ohne Werkzeug (``request_approval`` mit ``question`` und ohne
    ``tool``) und wird unter ``user_decision`` abgelegt. „Genehmigt“ ist bei ihr
    keine sinnvolle Aussage — wer „Abbrechen“ wählt, hat nichts genehmigt.
    """
    return (getattr(approval, "command", None) or "") == RUECKFRAGE_WERKZEUG


#: Ergebnis einer Entscheidung → Ereignistyp, wenn es KEINE Rückfrage ist.
_EREIGNIS = {
    "approved": (AuditEventType.COMMAND_APPROVED, "success"),
    "denied": (AuditEventType.COMMAND_DENIED, "blocked"),
    "cancelled": (AuditEventType.APPROVAL_CANCELLED, "cancelled"),
    "expired": (AuditEventType.APPROVAL_EXPIRED, "expired"),
}


async def freigabe_entschieden(
    db,
    approval,
    *,
    ergebnis: str,
    user=None,
    antwort: str | None = None,
    grund: str | None = None,
    kanal: str = "web",
    sammelverwerfung: bool = False,
    zusatz: dict[str, Any] | None = None,
) -> AuditLog | None:
    """Entscheider setzen und protokollieren — an EINER Stelle (#897, #908).

    ``ergebnis``: ``approved`` | ``denied`` | ``cancelled`` | ``expired``.
    Den Status der Freigabe setzt der Aufrufer (die Wege unterscheiden sich in
    der Antwort, die der Agent bekommt); hier entsteht, was nachprüfbar bleiben
    muss: wer, wann, was.
    """
    if ergebnis not in _EREIGNIS:
        raise ValueError(f"Unbekanntes Ergebnis: {ergebnis}")

    user_id = str(getattr(user, "id", "") or "") or None
    if user_id == "__anonymous__":
        user_id = None
    # Entscheider in der Freigabe selbst — nur wenn ein Mensch entschieden hat.
    # Ein Ablauf hat keinen Entscheider; ein Telegram-Knopf keinen bekannten.
    try:
        approval.resolved_by_user_id = user_id
    except Exception:  # noqa: BLE001
        pass

    meta_f = getattr(approval, "meta", None) or {}
    basis: dict[str, Any] = {"risk_level": getattr(approval, "risk_level", None), "kanal": kanal}
    if zusatz:
        basis.update(zusatz)

    if ist_rueckfrage(approval) and ergebnis in ("approved", "denied"):
        optionen = meta_f.get("options") or []
        if ergebnis == "denied":
            text = (grund or "").strip() or "Abgelehnt"
        else:
            text = (antwort or "").strip() or "Bestätigt"
        meta = {
            **basis,
            "question": meta_f.get("question"),
            "answer": text,
            "option": text if text in optionen else None,
            "options": optionen or None,
            "decision": ergebnis,
        }
        return await protokolliere(
            db, AuditEventType.QUESTION_ANSWERED,
            agent_id=approval.agent_id, user_id=user_id,
            command=f"Rückfrage beantwortet: {text}",
            outcome="success", approval_id=approval.id,
            task_id=getattr(approval, "task_id", None), meta=meta,
        )

    ereignis, outcome = _EREIGNIS[ergebnis]
    befehl = getattr(approval, "command", None) or ""
    if ergebnis in ("approved", "denied"):
        befehl = f"{befehl} {meta_f.get('input', {})}"
        meta = {**basis, "reasoning": getattr(approval, "description", None)}
        if ergebnis == "denied":
            meta["deny_reason"] = grund
    else:
        if ist_rueckfrage(approval) and meta_f.get("question"):
            befehl = f"Rückfrage: {meta_f.get('question')}"
        meta = {**basis}
        if sammelverwerfung:
            meta["sammelverwerfung"] = True
    return await protokolliere(
        db, ereignis,
        agent_id=approval.agent_id, user_id=user_id,
        command=befehl.strip() or None, outcome=outcome,
        approval_id=approval.id, task_id=getattr(approval, "task_id", None), meta=meta,
    )
