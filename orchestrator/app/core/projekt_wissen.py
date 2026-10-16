"""Projektwissen: ein eigenes Second Brain je Projekt.

Das Wissen liegt als Markdown-Ablage (Second Brain, ``models/second_brain.py``)
unter dem Slug ``projekt-<team>``. Agenten erreichen es NICHT über einen Mount —
der braucht einen Neustart des Containers und bräche laufende Chats ab —,
sondern über Werkzeuge, die bei jedem Aufruf die Mitgliedschaft im Projekt
prüfen (``api/teams.py``, ``/teams/{id}/wissen``). Wer neu ins Projekt kommt,
hat damit sofort Zugriff; wer geht, verliert ihn sofort.

Der Kern (``index.md``) geht zusätzlich als Auszug in jeden Projektauftrag
(``core/zielkette.vorspann``) — so kennt jeder Agent die Grundlagen, ohne erst
suchen zu müssen.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import vault
from app.models.second_brain import SecondBrain
from app.models.team import Team

log = logging.getLogger(__name__)

AUSZUG_ZEICHEN = 1500

INDEX_VORLAGE = """# {name}

Projektwissen — was jeder im Projekt wissen muss. Kurz halten: Dieser Text geht
bei jedem Projektauftrag an den Agenten mit.

## Ziel

{ziel}

## Wichtige Orte

- Repository, Server, Zugänge (keine Passwörter hier ablegen)

## Vereinbarungen

- Wie wir arbeiten, was gilt, was zu vermeiden ist

## Weitere Seiten

Ausführliches gehört in eigene Seiten (z. B. `entscheidungen.md`, `architektur.md`).
"""


def _slug(team: Team) -> str:
    return f"projekt-{team.id[:12]}"


async def brain_des_projekts(db: AsyncSession, team: Team) -> SecondBrain | None:
    slug = getattr(team, "wissen_slug", None)
    if not slug:
        return None
    return (await db.execute(
        select(SecondBrain).where(SecondBrain.slug == slug, SecondBrain.is_active.is_(True))
    )).scalar_one_or_none()


async def sicherstellen(db: AsyncSession, team: Team, angelegt_von: str | None = None) -> SecondBrain:
    """Das Brain des Projekts — beim ersten Mal angelegt und mit index.md bestückt."""
    brain = await brain_des_projekts(db, team)
    if brain is not None:
        return brain
    from app.api.brains import _provision_vault

    slug = _slug(team)
    brain = (await db.execute(select(SecondBrain).where(SecondBrain.slug == slug))).scalar_one_or_none()
    if brain is None:
        brain = SecondBrain(
            label=f"brain-{slug}", name=f"Projekt: {team.name}"[:200], slug=slug,
            host_path=f"{settings.secondbrain_base.rstrip('/')}/{slug}",
            container_path=f"{settings.secondbrain_container_base.rstrip('/')}/{slug}",
            default_mode="rw", standard="freeform",
            description=f"Projektwissen von „{team.name}“ — gepflegt über die Projektseite und die Projekt-Werkzeuge der Agenten.",
            created_by=angelegt_von,
        )
        db.add(brain)
    brain.is_active = True
    team.wissen_slug = slug
    await db.commit()
    await db.refresh(brain)
    # Erst die Projekt-index.md, dann die Ablage einrichten: _provision_vault legt
    # nur fehlende Dateien an und ließe sonst seine allgemeine Vorlage stehen.
    try:
        vault.write_file(
            brain.host_path, "index.md",
            INDEX_VORLAGE.format(name=team.name, ziel=(team.description or "(noch nicht beschrieben)").strip()),
            overwrite=False,
        )
    except FileExistsError:
        pass
    except (ValueError, OSError) as e:
        log.warning("index.md für Projekt %s nicht angelegt: %s", team.id, vault.safe_log(e))
    _provision_vault(brain.host_path, brain.name, "freeform")
    return brain


async def auszug(db: AsyncSession, team: Team) -> str:
    """Anfang von index.md für den Vorspann eines Projektauftrags — leer, wenn es keins gibt."""
    brain = await brain_des_projekts(db, team)
    if brain is None:
        return ""
    try:
        text = vault.read_file(brain.host_path, "index.md").strip()
    except (FileNotFoundError, ValueError, OSError):
        return ""
    if len(text) > AUSZUG_ZEICHEN:
        text = text[:AUSZUG_ZEICHEN].rstrip() + "\n[… gekürzt — weiter mit projekt_wissen_lesen]"
    return text


async def suchen(db: AsyncSession, brain: SecondBrain, frage: str, limit: int = 8) -> list[dict]:
    from app.services import vault_search

    return await vault_search.hybrid_search(db, brain.label, brain.host_path, frage, limit)


async def schreiben(db: AsyncSession, brain: SecondBrain, pfad: str, inhalt: str, *, wer: str = "Projekt") -> dict:
    """Seite anlegen oder überschreiben, als Version festhalten und für die Suche neu einlesen."""
    ergebnis = vault.write_file(brain.host_path, pfad, inhalt)
    _version_festhalten(brain.host_path, pfad, wer)
    try:
        from app.services import vault_indexer

        await vault_indexer.index_file(db, brain.label, brain.host_path, pfad)
    except Exception as e:  # noqa: BLE001 — Einlesen darf das Schreiben nie scheitern lassen
        log.warning("Projektwissen nicht neu eingelesen (%s): %s", vault.safe_log(pfad), vault.safe_log(e))
    return ergebnis


async def anhaengen(db: AsyncSession, brain: SecondBrain, pfad: str, abschnitt: str, *, wer: str = "Projekt",
                    kopf: str | None = None) -> dict:
    """Abschnitt an eine Seite anhängen (legt sie an) — nie etwas Bestehendes überschreiben.

    So landen Ergebnisse, Entscheidungen und Protokolle zuverlässig im Wissen, ohne
    dass ein Agent die Seite erst lesen und vollständig neu schreiben muss.
    """
    try:
        bisher = vault.read_file(brain.host_path, pfad)
    except FileNotFoundError:
        bisher = f"# {kopf}\n" if kopf else ""
    neu = (bisher.rstrip() + "\n\n" + abschnitt.strip() + "\n").lstrip()
    return await schreiben(db, brain, pfad, neu, wer=wer)


# ─── Verlauf (Git der Ablage) ───────────────────────────────────


# Git läuft hier über Inhalte, die Agenten schreiben. ``vault.resolve_path`` sperrt
# ``.git`` für jeden Schreibweg; zusätzlich führt Git hier nichts aus der Ablage aus:
# keine Hooks, kein fsmonitor, keine System-/Benutzer-Konfiguration, schlanke Umgebung.
_GIT_SICHER = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "core.pager=cat",
               "-c", "core.sshCommand=false", "-c", "protocol.allow=never")


def _git(cwd: str, *args: str, timeout: int = 20) -> subprocess.CompletedProcess:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/nonexistent", "LANG": "C.UTF-8",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(["git", *_GIT_SICHER, *args], cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, env=env)


def _version_festhalten(host_path: str, pfad: str, wer: str) -> None:
    """Jede Änderung als Commit — Grundlage für Verlauf und Zurückholen. Best effort."""
    try:
        if not os.path.isdir(os.path.join(host_path, ".git")):
            _git(host_path, "init", "-q")
        _git(host_path, "add", "--", pfad)
        name = re.sub(r"[^\w .:@-]", "", wer)[:80] or "Projekt"
        _git(host_path, "-c", f"user.name={name}", "-c", "user.email=projekt@ai-employee.local",
             "commit", "-q", "-m", f"{pfad}: {name}", "--", pfad)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("Version von %s nicht festgehalten: %s", vault.safe_log(pfad), vault.safe_log(e))


def verlauf(brain: SecondBrain, pfad: str, limit: int = 30) -> list[dict]:
    vault.resolve_path(brain.host_path, pfad)  # Pfad prüfen (kein Ausbruch)
    try:
        r = _git(brain.host_path, "log", f"-{limit}", "--format=%H%x1f%an%x1f%aI", "--", pfad)
    except (OSError, subprocess.SubprocessError):
        return []
    eintraege = []
    for zeile in r.stdout.splitlines():
        teile = zeile.split("\x1f")
        if len(teile) == 3:
            eintraege.append({"version": teile[0], "wer": teile[1], "wann": teile[2]})
    return eintraege


def version_lesen(brain: SecondBrain, pfad: str, version: str) -> str:
    vault.resolve_path(brain.host_path, pfad)
    if not re.fullmatch(r"[0-9a-f]{7,40}", version or ""):
        raise ValueError("Unbekannte Version.")
    r = _git(brain.host_path, "show", f"{version}:{pfad}")
    if r.returncode != 0:
        raise FileNotFoundError(pfad)
    return r.stdout
