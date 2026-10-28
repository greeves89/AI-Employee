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

# Wie viel von ``AUSZUG_SEITE`` in einen Projektauftrag geht. Die Grenze zählt
# ZEICHEN, nicht Zeilen — wer sie in einer anderen Einheit weitergibt, schneidet
# dem Agenten still den Seitenschluss ab (Issue #979).
AUSZUG_ZEICHEN = 1500
AUSZUG_SEITE = "index.md"
AUSZUG_MARKER = "[… gekürzt — weiter mit projekt_wissen_lesen]"

INDEX_VORLAGE = """# {name}

Projektwissen — was jeder im Projekt wissen muss. Nur die ersten {grenze} Zeichen
dieser Seite gehen bei jedem Projektauftrag an den Agenten mit, alles danach
nicht. Also das Wichtigste zuerst, Ausführliches auf eigene Seiten.

## Ziel

{ziel}

## Stand

- Woran gerade gearbeitet wird

## Wichtige Orte

- Repository, Server, Zugänge (keine Passwörter hier ablegen)

## Vereinbarungen

- Wie wir arbeiten, was gilt, was zu vermeiden ist

## Ansprechpartner

- Wer entscheidet, wer zuständig ist

## Weitere Seiten

Ausführliches gehört in eigene Seiten (z. B. `entscheidungen.md`, `architektur.md`).
"""


def index_vorlage(name: str, ziel: str) -> str:
    """Die Startseite — mit der echten Grenze darin.

    Als Funktion und nicht als ``.format``-Aufruf beim Anleger, damit kein
    Aufrufer ``grenze`` vergessen kann: die Zahl soll nie wieder von
    ``AUSZUG_ZEICHEN`` abweichen können.
    """
    return INDEX_VORLAGE.format(name=name, ziel=ziel, grenze=AUSZUG_ZEICHEN)


def auftrag_index_zeile() -> str:
    """Was ein Auftrag über ``AUSZUG_SEITE`` sagen muss — Einheit und Zahl aus einer Hand.

    Stand in ``api/teams.py`` als Fließtext mit eigener Zahl („höchstens 40
    Zeilen") und lief damit von der durchgesetzten Grenze weg: 40 Zeilen deutscher
    Prosa sind rund 2500 Zeichen, von denen nur 1500 ankamen — der Seitenschluss
    (Ansprechpartner, Vereinbarungen) fiel still weg (Issue #979).
    """
    return (f"- `{AUSZUG_SEITE}`: Ziel, Stand, wichtige Orte, Vereinbarungen, Ansprechpartner. "
            f"Von dieser Seite gehen die ersten {AUSZUG_ZEICHEN} ZEICHEN in jeden Projektauftrag "
            "ein, alles darüber hinaus erreicht keinen Agenten. Also das Wichtigste zuerst und "
            "Ausführliches auf eigene Seiten — die Grenze zählt Zeichen, nicht Zeilen.")


def auszug_text(text: str) -> tuple[str, bool]:
    """Was von einer Seite in einen Projektauftrag geht — und ob dabei gekürzt wurde.

    EINE Stelle für Auszug und Rückmeldung. Ein zweiter Längenvergleich an
    anderer Stelle liefe beim nächsten Eingriff (``strip``, Marker, Grenze)
    auseinander und stellte die stille Lücke aus Issue #979 wieder her.
    """
    text = text.strip()
    if len(text) <= AUSZUG_ZEICHEN:
        return text, False
    return text[:AUSZUG_ZEICHEN].rstrip() + "\n" + AUSZUG_MARKER, True


def _auszug_warnung(host_path: str, pfad: str, inhalt: str) -> str | None:
    """Rückmeldung an den Schreibenden, wenn sein Text den Auszug sprengt.

    Ohne sie ist der Schnitt für ihn unsichtbar: die Seite ist vollständig
    gespeichert, in den Projektauftrag geht aber nur ihr Anfang (Issue #979).
    Betrifft nur ``AUSZUG_SEITE`` — jede andere Seite wird nie gekürzt.
    """
    try:
        if vault.resolve_path(host_path, pfad) != vault.resolve_path(host_path, AUSZUG_SEITE):
            return None
    except ValueError:
        return None
    _, gekuerzt = auszug_text(inhalt)
    if not gekuerzt:
        return None
    return (f"Hinweis: Gespeichert ist die ganze Seite, aber nur die ersten {AUSZUG_ZEICHEN} "
            f"Zeichen von {AUSZUG_SEITE} gehen in einen Projektauftrag ein — deine "
            f"{len(inhalt.strip())} Zeichen werden dafür gekürzt. Das Wichtigste nach vorn, "
            "Ausführliches auf eine eigene Seite.")


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
            index_vorlage(team.name, (team.description or "(noch nicht beschrieben)").strip()),
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
    return auszug_text(text)[0]


async def suchen(db: AsyncSession, brain: SecondBrain, frage: str, limit: int = 8) -> list[dict]:
    from app.services import vault_search

    return await vault_search.hybrid_search(db, brain.label, brain.host_path, frage, limit)


async def schreiben(db: AsyncSession, brain: SecondBrain, pfad: str, inhalt: str, *, wer: str = "Projekt",
                    versionieren: bool = True) -> dict:
    """Seite anlegen oder überschreiben, als Version festhalten und für die Suche neu einlesen.

    ``versionieren=False`` für Seiten, deren Quelle woanders liegt (ausgelesener
    Text hochgeladener Dateien): sonst bliebe er nach dem Löschen im Verlauf lesbar.
    """
    ergebnis = vault.write_file(brain.host_path, pfad, inhalt)
    if versionieren:
        _version_festhalten(brain.host_path, pfad, wer)
    try:
        from app.services import vault_indexer

        await vault_indexer.index_file(db, brain.label, brain.host_path, pfad)
    except Exception as e:  # noqa: BLE001 — Einlesen darf das Schreiben nie scheitern lassen
        log.warning("Projektwissen nicht neu eingelesen (%s): %s", vault.safe_log(pfad), vault.safe_log(e))
    hinweis = _auszug_warnung(brain.host_path, pfad, inhalt)
    return {**ergebnis, "hinweis": hinweis} if hinweis else ergebnis


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


# Die Ablage enthält, was Agenten schreiben — git läuft deshalb über
# ``vault.git_sicher`` (dort steht, wogegen das schützt und wogegen nicht).
_git = vault.git_sicher


def _pfadspek(pfad: str) -> str:
    """Ein Dateiname für git, und zwar als Name und nicht als Muster.

    ``--`` schaltet die Options-Erkennung ab, **nicht** die Pathspec-Magie: ein
    ``pfad`` wie ``:(glob)**/*.md`` passiert ``vault.resolve_path`` und die
    Suffixprüfung und nimmt im anschließenden Commit dann *jede* Datei der Ablage
    auf — der Commit lautet aber auf eine einzelne Datei und einen einzelnen
    Autor. ``:(literal)`` nimmt der Zeichenkette genau diese Deutung (Issue #964).

    Ein LEERER ``pfad`` muss dabei abgefangen werden: ``":(literal)"`` allein ist
    für git „alles", wo das nackte ``""`` noch ein ``fatal`` war.
    """
    if not pfad or not pfad.strip():
        raise ValueError("Leerer Pfad.")
    return ":(literal)" + pfad


def _version_festhalten(host_path: str, pfad: str, wer: str) -> None:
    """Jede Änderung als Commit — Grundlage für Verlauf und Zurückholen. Best effort."""
    if not pfad or not pfad.strip():
        return
    try:
        if not os.path.isdir(os.path.join(host_path, ".git")):
            _git(host_path, "init", "-q")
        _git(host_path, "add", "--", _pfadspek(pfad))
        name = re.sub(r"[^\w .:@-]", "", wer)[:80] or "Projekt"
        _git(host_path, "-c", f"user.name={name}", "-c", "user.email=projekt@ai-employee.local",
             "commit", "-q", "-m", f"{pfad}: {name}", "--", _pfadspek(pfad))
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("Version von %s nicht festgehalten: %s", vault.safe_log(pfad), vault.safe_log(e))


def verlauf(brain: SecondBrain, pfad: str, limit: int = 30) -> list[dict]:
    if not pfad or not pfad.strip():
        return []
    vault.resolve_path(brain.host_path, pfad)  # Pfad prüfen (kein Ausbruch)
    # ``limit`` wird zu ``-<n>`` und landet damit an der Stelle einer git-Option:
    # heute ruft nur ein Aufrufer ohne Argument (Default 30), ein künftiger mit
    # einem Abfrageparameter hätte dort eine offene Option (Issue #964).
    try:
        anzahl = max(1, min(int(limit), 1000))
    except (TypeError, ValueError):
        anzahl = 30
    try:
        r = _git(brain.host_path, "log", f"-{anzahl}", "--format=%H%x1f%an%x1f%aI",
                 "--", _pfadspek(pfad))
    except (OSError, subprocess.SubprocessError):
        return []
    eintraege = []
    for zeile in r.stdout.splitlines():
        teile = zeile.split("\x1f")
        if len(teile) == 3:
            eintraege.append({"version": teile[0], "wer": teile[1], "wann": teile[2]})
    return eintraege


def version_lesen(brain: SecondBrain, pfad: str, version: str) -> str:
    """Eine frühere Fassung GENAU EINER Datei.

    ``git show <rev>:<pfad>`` ist dafür das falsche Werkzeug, weil es drei Fälle
    mit Rückgabewert 0 beantwortet, die keine Datei sind (alle nachgemessen):

    * ``pfad`` enthält ein Musterzeichen (``*``, ``?``, ``[``) → rc=0, LEERE
      Ausgabe — nicht unterscheidbar von einer legitim leeren ``.md``,
    * ``pfad`` ist leer → rc=0 und die BAUMAUFLISTUNG der ganzen Ablage,
    * ``pfad`` zeigt auf einen Ordner → rc=0 und dessen Baumauflistung.

    Geprüft wurde nur ``returncode != 0``, also ging das als Dateiinhalt weiter:
    die Oberfläche zeigte eine leere oder erfundene alte Fassung, und
    ``wissen_wiederherstellen`` hätte sie genau so zurückgeschrieben.
    ``cat-file blob`` verlangt ein Blob und meldet alle drei Fälle als Fehler,
    lässt eine wirklich leere ``.md`` aber unverändert durch (Issue #968).
    """
    vault.resolve_path(brain.host_path, pfad)
    if not re.fullmatch(r"[0-9a-f]{7,40}", version or ""):
        raise ValueError("Unbekannte Version.")
    r = _git(brain.host_path, "cat-file", "blob", f"{version}:{pfad}")
    if r.returncode != 0:
        raise FileNotFoundError(pfad)
    return r.stdout
