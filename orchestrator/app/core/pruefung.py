"""Blinder Prüfer für Projekt-Karten.

Ist ein Lauf fertig und die Karte im Review, prüft ein zweiter Lauf das Ergebnis — und
zwar „blind“: Er bekommt die Story der Karte, den Pull Request, Branch und Repo, aber
NICHT den Bericht des Umsetzers. So prüft er, was gebaut ist, nicht, was behauptet wird.

* Eingeschaltet je Projekt (``teams.blinder_pruefer``).
* Prüfer ist ein anderer Agent des Projekts; gibt es keinen, derselbe Agent in einem
  neuen Lauf (eigene Sitzung) — schwächer blind, aber besser als keiner.
* Befunde im Format von ``core/beleg`` (``Befund: Ort — was passiert — wie gesehen``),
  Schlusszeile ``PRÜFUNG: BESTANDEN`` oder ``PRÜFUNG: NACHARBEIT``.
* Das Ergebnis steht am Lauf des Umsetzers (``metadata["pruefung"]``); das Board zeigt es im
  Review. Entscheiden — abnehmen oder mit den Befunden zurückgeben — tut der Mensch.

Aufgerufen aus ``TaskRouter.handle_task_completion`` (``nach_lauf``) — dort, wo ohnehin
jeder Lauf endet; keine eigene Schleife, kein eigener Takt.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.core.beleg import BEFUND_REGEL, befunde, vermutungen

logger = logging.getLogger(__name__)

ROLLE = "pruefer"
BESTANDEN, NACHARBEIT = "bestanden", "nacharbeit"
LAEUFT, OHNE_URTEIL, GESCHEITERT = "laeuft", "ohne_urteil", "gescheitert"
_SCHLUSS = {"PRÜFUNG: BESTANDEN": BESTANDEN, "PRUEFUNG: BESTANDEN": BESTANDEN,
            "PRÜFUNG: NACHARBEIT": NACHARBEIT, "PRUEFUNG: NACHARBEIT": NACHARBEIT}


def urteil(text: str | None) -> str | None:
    """Schlusszeile → ``bestanden`` | ``nacharbeit`` | None (nur die letzten Zeilen zählen)."""
    zeilen = [z.strip().strip("*_`> ").strip().upper() for z in (text or "").splitlines() if z.strip()]
    for z in reversed(zeilen[-3:]):
        for marke, wert in _SCHLUSS.items():
            if z.startswith(marke):
                return wert
    return None


def pruefer_waehlen(member_ids: list[str] | None, lead_id: str | None, umsetzer: str | None) -> str | None:
    """Ein anderer Agent des Projekts als der Umsetzer — sonst der Umsetzer selbst."""
    andere = sorted(a for a in (member_ids or []) if a and a != umsetzer)
    if andere:
        return andere[0]
    if lead_id and lead_id != umsetzer:
        return lead_id
    return umsetzer


def pruefauftrag(*, titel: str, story: str, projekt: str, repo: str | None, pr_url: str | None,
                 branch: str | None, karte_id: str) -> str:
    """Der Auftrag an den Prüfer — bewusst OHNE das Ergebnis des Umsetzers."""
    stand = []
    if repo:
        stand.append(f"- Repo: {repo}")
    if pr_url:
        stand.append(f"- Pull Request: {pr_url}")
    if branch:
        stand.append(f"- Branch: {branch}")
    if not stand:
        stand.append("- Kein PR/Branch gemeldet — prüfe den Stand, den die Story beschreibt "
                     "(Dateien im Projekt, Ergebnis im Arbeitsordner).")
    return (
        f"Prüfauftrag (blinder Prüfer) im Projekt „{projekt}“ — Karte „{titel}“ (karte_id={karte_id}).\n\n"
        "Ein anderer Lauf hat diese Karte umgesetzt. Du siehst absichtlich NICHT seinen Bericht: "
        "Prüfe, was tatsächlich gebaut ist, gegen die Story — nicht, was jemand behauptet.\n\n"
        "[EXTERNAL-DATA source=\"karte\"]\n"
        f"{story.strip()[:8000] or '(keine Beschreibung)'}\n"
        "[/EXTERNAL-DATA]\n\n"
        "## Stand zum Prüfen\n" + "\n".join(stand) + "\n\n"
        "## So prüfst du\n"
        f"- Arbeite in einem frischen Verzeichnis (/workspace/pruefung-{karte_id}, frischer Klon "
        "oder git worktree) — nicht in einem Arbeitsordner, in dem du die Karte schon bearbeitet hast.\n"
        "- Stütze dich nicht auf frühere Notizen oder Erinnerungen zu dieser Karte.\n"
        "- Führe die Tests aus und stelle das in der Story verlangte Verhalten selbst nach.\n"
        "- Ändere nichts am Stand des Umsetzers (kein Push, kein Merge, keine Kommentare im PR).\n"
        f"- {BEFUND_REGEL}\n"
        "- Wurde ein Test leichter gemacht oder eine Prüfung abgeschaltet, ist das ein Befund.\n\n"
        "Beende deine Antwort mit genau einer Zeile:\n"
        "PRÜFUNG: BESTANDEN — kein Befund, das verlangte Verhalten selbst nachgestellt.\n"
        "PRÜFUNG: NACHARBEIT — mindestens ein Befund."
    )


async def nach_lauf(router, task) -> None:
    """Ende eines Laufs: Prüfung starten (Umsetzer) oder ihr Ergebnis festhalten (Prüfer).

    ``router``: der ``TaskRouter`` (Datenbank-Sitzung und ``create_and_route_task``).
    Fehler hier dürfen das Abschließen nie stoppen — der Aufrufer fängt sie ab.
    """
    meta = task.metadata_ or {}
    if meta.get("rolle") == ROLLE:
        await _ergebnis_festhalten(router.db, task)
    elif meta.get("karte_id"):
        await _pruefung_starten(router, task)


async def _pruefung_starten(router, task) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.models.agent import Agent
    from app.models.projekt_karte import ProjektKarte
    from app.models.task import TaskStatus
    from app.models.team import Team

    if task.status != TaskStatus.COMPLETED:
        return
    db = router.db
    meta = task.metadata_ or {}
    karte = await db.get(ProjektKarte, meta["karte_id"])
    if karte is None or karte.task_id != task.id or karte.spalte != "pruefung":
        return
    team = await db.get(Team, karte.team_id)
    if team is None or not team.blinder_pruefer:
        return
    if (meta.get("pruefung") or {}).get("task_id"):
        return                                          # schon geprüft bzw. Prüfung läuft
    pruefer = pruefer_waehlen(team.member_agent_ids, team.lead_agent_id, task.agent_id)
    if not pruefer or await db.get(Agent, pruefer) is None:
        return
    auftrag = pruefauftrag(
        titel=karte.titel, story=karte.text or karte.titel, projekt=team.name, repo=team.repo,
        pr_url=karte.pr_url, branch=karte.extern_branch, karte_id=karte.id,
    )
    pruef_task = await router.create_and_route_task(
        title=f"Prüfung: {karte.titel}"[:200], prompt=auftrag, priority=1, agent_id=pruefer,
        metadata={"team_id": team.id, "rolle": ROLLE, "pruefung_karte_id": karte.id,
                  "pruefung_fuer": task.id},
    )
    task.metadata_ = {**(task.metadata_ or {}), "pruefung": {
        "status": LAEUFT, "task_id": pruef_task.id, "agent_id": pruefer,
        "blind": pruefer != task.agent_id,
        "seit": datetime.now(timezone.utc).isoformat(),
    }}
    flag_modified(task, "metadata_")
    logger.info("[Prüfung] Karte %s: Prüfung %s an %s (blind=%s)", karte.id, pruef_task.id,
                pruefer, pruefer != task.agent_id)


async def _ergebnis_festhalten(db, pruef_task) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.models.task import Task, TaskStatus

    meta = pruef_task.metadata_ or {}
    umsetzer = await db.get(Task, meta.get("pruefung_fuer"))
    if umsetzer is None:
        return
    bisher = (umsetzer.metadata_ or {}).get("pruefung") or {}
    if bisher.get("task_id") != pruef_task.id:
        return                                          # eine ältere Prüfung zählt nicht
    if pruef_task.status != TaskStatus.COMPLETED:
        status = GESCHEITERT
    else:
        status = urteil(pruef_task.result) or OHNE_URTEIL
    gefunden = befunde(pruef_task.result)
    if status == BESTANDEN and gefunden:
        status = NACHARBEIT                              # Befunde und „bestanden“ widersprechen sich
    umsetzer.metadata_ = {**(umsetzer.metadata_ or {}), "pruefung": {
        **bisher, "status": status, "befunde": gefunden[:20],
        "vermutungen": vermutungen(pruef_task.result)[:10],
        "fertig": datetime.now(timezone.utc).isoformat(),
    }}
    flag_modified(umsetzer, "metadata_")
    logger.info("[Prüfung] Lauf %s: %s (%d Befunde)", umsetzer.id, status, len(gefunden))


def fuer_karte(task) -> dict | None:
    """Was das Board über die Prüfung zeigt (aus dem Lauf des Umsetzers)."""
    p = (getattr(task, "metadata_", None) or {}).get("pruefung") if task is not None else None
    if not p:
        return None
    return {k: p.get(k) for k in ("status", "agent_id", "blind", "befunde", "vermutungen", "task_id")}


def rueckgabe_text(p: dict) -> str:
    """Hinweis für „Zurückgeben“: die Befunde des Prüfers als Liste."""
    zeilen = [f"- {b}" for b in (p.get("befunde") or [])]
    return ("Der blinde Prüfer hat Nacharbeit gefunden:\n" + "\n".join(zeilen)) if zeilen else ""
