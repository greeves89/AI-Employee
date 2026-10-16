"""Git-Projekte: Issues und Pull Requests eines Repos auf dem Board.

Ein Projekt kann ein GitHub-Repo haben (``teams.repo`` = ``owner/name``). Der
Scheduler gleicht alle zehn Minuten ab (``alle_abgleichen``), auf Knopfdruck
auch sofort (``POST /teams/{id}/repo/abgleichen``):

* offene Issues → Karten im **Eingang** (neu) bzw. Titel/Text/Labels aktualisiert;
  der Platz auf dem Board (Spalte, Priorität) gehört dem Projekt und bleibt.
* geschlossenes Issue → Karte **erledigt** (außer sie ist gerade in Arbeit:
  dann eine Notiz; der Lauf entscheidet).
* offene PRs → verknüpft mit der Issue-Karte, die sie schließen („Fixes #12“),
  sonst eine eigene Karte in der **Prüfung** („Wartet auf Merge“); dazu der
  CI-Stand aus den Checks.
* PR gemergt/geschlossen → Stand nachgezogen; eine reine PR-Karte ist erledigt.

Zugang: die GitHub-Verbindung der Anlage (Admin, ``oauth_integrations`` ohne
Nutzer), sonst ``GITHUB_TOKEN`` aus der Umgebung, sonst ANONYM — dann gehen nur
öffentliche Repos, mit niedriger Abfragegrenze (60/Stunde); der Takt gleicht
anonyme Projekte deshalb nur alle 30 Minuten ab. Ein Repo an ein Projekt hängen
darf nur ein Administrator — sonst holte sich jeder über den Zugang der Anlage
fremde private Repos aufs Board.
Die Plattform LIEST nur; schreiben (Branches, PRs, Kommentare) tun die Agenten
mit ihrem eigenen GitHub-Werkzeug.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import projekte
from app.models.projekt_karte import ProjektKarte
from app.models.team import Team

log = logging.getLogger(__name__)

REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
SCHLIESST_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s*#(\d+)", re.I)
MAX_ISSUES = 300
MAX_PRS = 30
TEXT_ZEICHEN = 8000
TAKT_SEKUNDEN = 600
ANONYM_ABSTAND_SEKUNDEN = 1800


class GitFehler(RuntimeError):
    """Kein Zugang, Repo unbekannt, GitHub antwortet nicht — mit deutscher Meldung."""


def repo_pruefen(repo: str | None) -> str | None:
    repo = (repo or "").strip().removeprefix("https://github.com/").strip("/")
    if not repo:
        return None
    if not REPO_RE.match(repo) or ".." in repo:
        raise ValueError("Repo bitte als owner/name angeben, z. B. beispiel-org/beispiel-repo.")
    return repo


async def zugang(db: AsyncSession, redis):
    """(Token, Anbieter) — Token leer heisst anonym (nur öffentliche Repos)."""
    import os

    from app.core.githost.registry import get_git_host_provider
    from app.models.oauth_integration import OAuthIntegration, OAuthProvider
    from app.services.oauth_service import OAuthService

    try:
        token = await OAuthService(db, redis).get_valid_token("github")
    except Exception:  # noqa: BLE001 — keine Verbindung: Umgebung, sonst anonym
        token = os.environ.get("GITHUB_TOKEN", "")
    zeile = (await db.execute(
        select(OAuthIntegration).where(OAuthIntegration.provider == OAuthProvider.GITHUB,
                                       OAuthIntegration.user_id.is_(None))
    )).scalars().first()
    anbieter = get_git_host_provider(getattr(zeile, "host_type", None) or "github", getattr(zeile, "base_url", None))
    return token, anbieter


def _labels(issue: dict) -> list[str]:
    return [str(lbl.get("name")) for lbl in issue.get("labels") or [] if isinstance(lbl, dict) and lbl.get("name")][:20]


async def abgleichen(db: AsyncSession, team: Team, token: str, anbieter, client: httpx.AsyncClient) -> dict:
    """Ein Projekt mit seinem Repo abgleichen. Der Aufrufer committet."""
    repo = team.repo
    try:
        issues = await anbieter.list_open_issues(client, token, repo, MAX_ISSUES)
        pulls = await anbieter.list_open_pulls(client, token, repo, MAX_PRS)
    except LookupError as e:
        if token:
            raise GitFehler(f"Repo {repo} nicht gefunden — oder der GitHub-Zugang der Anlage darf es nicht lesen.") from e
        raise GitFehler(f"Repo {repo} nicht gefunden. Ist es privat, braucht die Anlage einen GitHub-Zugang "
                        "(Admin → Integrationen → GitHub).") from e
    except (RuntimeError, httpx.HTTPError) as e:
        raise GitFehler(f"GitHub antwortet nicht für {repo}: {e}") from e

    jetzt = datetime.now(timezone.utc)
    vorhanden = {
        (k.quelle, k.extern_nr): k
        for k in (await db.execute(
            select(ProjektKarte).where(ProjektKarte.team_id == team.id, ProjektKarte.quelle.in_(("issue", "pr")))
        )).scalars()
    }
    neu = aktualisiert = erledigt = 0

    # Issues
    offen_issues = set()
    for issue in issues:
        nr = int(issue["number"])
        offen_issues.add(nr)
        titel = str(issue.get("title") or f"Issue #{nr}")[:300]
        text = str(issue.get("body") or "")[:TEXT_ZEICHEN]
        karte = vorhanden.get(("issue", nr))
        if karte is None:
            karte = projekte.neue_karte(team.id, titel, text, spalte="eingang", quelle="issue", extern_nr=nr,
                                        url=issue.get("html_url"), labels=_labels(issue), von=f"github:{repo}")
            db.add(karte)
            vorhanden[("issue", nr)] = karte
            neu += 1
        else:
            if (karte.titel, karte.text, karte.labels) != (titel, text, _labels(issue)):
                karte.titel, karte.text, karte.labels = titel, text, _labels(issue)
                aktualisiert += 1
            if karte.spalte == "erledigt" and karte.notiz == "Issue geschlossen":
                # Wieder geöffnet: zurück in den Eingang.
                karte.spalte, karte.erledigt_at, karte.notiz = "eingang", None, "Issue wieder geöffnet"

    for (quelle, nr), karte in vorhanden.items():
        if quelle != "issue" or nr in offen_issues or karte.spalte == "erledigt":
            continue
        # Nicht mehr in der Liste: wirklich geschlossen? (Die Liste ist bei MAX_ISSUES gekappt.)
        if len(issues) >= MAX_ISSUES and await anbieter.issue_state(client, token, repo, nr) != "closed":
            continue
        if karte.spalte == "in_arbeit":
            karte.notiz = "Issue im Repo geschlossen — der Lauf läuft noch."
            continue
        karte.spalte, karte.erledigt_at, karte.notiz = "erledigt", jetzt, "Issue geschlossen"
        erledigt += 1

    # Pull Requests
    offen_prs = set()
    for pr in pulls:
        nr = int(pr["number"])
        offen_prs.add(nr)
        sha = (pr.get("head") or {}).get("sha")
        zustand = await anbieter.checks_state(client, token, repo, sha) if sha else "offen"
        verknuepft = [vorhanden.get(("issue", int(n))) for n in SCHLIESST_RE.findall(pr.get("body") or "")]
        verknuepft = [k for k in verknuepft if k is not None]
        if verknuepft:
            for karte in verknuepft:
                karte.pr_nr, karte.pr_url, karte.pr_zustand = nr, pr.get("html_url"), zustand
                if karte.spalte in ("eingang", "bereit"):
                    # Jemand arbeitet schon daran (außerhalb des Boards): ab in die Prüfung.
                    karte.spalte = "pruefung"
            continue
        karte = vorhanden.get(("pr", nr))
        if karte is None:
            karte = projekte.neue_karte(team.id, str(pr.get("title") or f"PR #{nr}")[:300],
                                        str(pr.get("body") or "")[:TEXT_ZEICHEN], spalte="eingang", quelle="pr",
                                        extern_nr=nr, url=pr.get("html_url"), labels=_labels(pr), von=f"github:{repo}")
            karte.spalte = "pruefung"
            db.add(karte)
            vorhanden[("pr", nr)] = karte
            neu += 1
        karte.pr_nr, karte.pr_url, karte.pr_zustand = nr, pr.get("html_url"), zustand

    # PRs, die nicht mehr offen sind: gemergt oder geschlossen?
    for karte in list(vorhanden.values()):
        if not karte.pr_nr or karte.pr_nr in offen_prs or karte.pr_zustand in ("gemergt", "geschlossen", None):
            continue
        zustand = await anbieter.pull_state(client, token, repo, karte.pr_nr)
        if zustand in ("gemergt", "geschlossen"):
            karte.pr_zustand = zustand
            if karte.quelle == "pr" and karte.spalte != "erledigt":
                karte.spalte, karte.erledigt_at = "erledigt", jetzt
                erledigt += 1

    team.repo_abgleich_at = jetzt
    team.repo_fehler = None
    return {"neu": neu, "aktualisiert": aktualisiert, "erledigt": erledigt,
            "offene_issues": len(issues), "offene_prs": len(pulls)}


async def projekt_abgleichen(db: AsyncSession, redis, team: Team) -> dict:
    """Ein Projekt abgleichen und den Ausgang am Projekt festhalten (auch Fehler)."""
    try:
        token, anbieter = await zugang(db, redis)
        async with httpx.AsyncClient(timeout=20) as client:
            ergebnis = await abgleichen(db, team, token, anbieter, client)
        await db.commit()
        return ergebnis
    except GitFehler as e:
        await db.rollback()
        t = await db.get(Team, team.id)
        if t is not None:
            t.repo_fehler = str(e)[:300]
            t.repo_abgleich_at = datetime.now(timezone.utc)
            await db.commit()
        raise


async def alle_abgleichen(redis) -> int:
    """Alle Projekte mit Repo — aus dem Scheduler-Takt. Liefert die Zahl der Projekte."""
    from app.db.session import resilient_session

    async with resilient_session() as db:
        teams = (await db.execute(
            select(Team).where(Team.is_active.is_(True), Team.repo.is_not(None))
        )).scalars().all()
        anonym = not (await zugang(db, redis))[0] if teams else False
        jetzt = datetime.now(timezone.utc)
        for team in teams:
            # Anonym nur alle 30 Minuten: die Abfragegrenze (60/h) teilen sich alle Projekte.
            zuletzt = team.repo_abgleich_at
            if anonym and zuletzt and (jetzt - (zuletzt if zuletzt.tzinfo else zuletzt.replace(tzinfo=timezone.utc))).total_seconds() < ANONYM_ABSTAND_SEKUNDEN:
                continue
            try:
                await projekt_abgleichen(db, redis, team)
            except GitFehler as e:
                log.info("Git-Abgleich %s: %s", team.id, e)
            except Exception as e:  # noqa: BLE001 — ein Projekt darf die anderen nicht aufhalten
                await db.rollback()
                log.warning("Git-Abgleich %s gescheitert: %s", team.id, e)
        return len(teams)
