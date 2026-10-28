"""Git-Projekte: Issues und Pull Requests eines Repos auf dem Board.

Ein Projekt kann ein Repo haben: auf GitHub (``teams.repo`` = ``owner/name``) oder
auf einem eigenen Forgejo/Gitea-Server (``teams.repo`` = ``https://server/owner/name``,
siehe ``repo_ziel``; Anbieter ``githost/forgejo.py``). Der Scheduler gleicht alle zehn Minuten ab (``alle_abgleichen``), auf Knopfdruck
auch sofort (``POST /teams/{id}/repo/abgleichen``):

* offene Issues → Karten im **Eingang** (neu) bzw. Titel/Text/Labels aktualisiert;
  der Platz auf dem Board (Spalte, Priorität) gehört dem Projekt und bleibt.
* geschlossenes Issue → Karte **erledigt** (außer sie ist gerade in Arbeit:
  dann eine Notiz; der Lauf entscheidet).
* offene PRs → verknüpft mit der Issue-Karte, die sie schließen („Fixes #12“),
  sonst eine eigene Karte in der **Prüfung** („Wartet auf Merge“); dazu der
  CI-Stand aus den Checks (GitHub) bzw. dem Commit-Status (Forgejo/Gitea).
* PR gemergt/geschlossen → Stand nachgezogen; eine reine PR-Karte ist erledigt.

Zugang je Projekt (``zugang``), in dieser Reihenfolge:
1. ein Schlüssel aus der Schlüsselverwaltung (``teams.repo_secret_id``) — nur,
   solange der Ersteller des Projekts ihn nutzen darf;
2. die GitHub-Verbindung der Anlage bzw. ``GITHUB_TOKEN`` — NUR für GitHub-Repos von
   Projekten, die ein Administrator angelegt hat (sonst läse jeder über den
   Anlagen-Zugang fremde private Repos);
3. anonym — nur öffentliche Repos, niedrige Abfragegrenze (60/Stunde); der Takt
   gleicht anonyme Projekte deshalb nur alle 30 Minuten ab.

Beim Öffnen eines Projekts gleicht die Oberfläche zusätzlich ab (``beim_oeffnen``),
mit Schlüssel höchstens einmal je Minute, anonym höchstens alle fünf Minuten.
Die Plattform LIEST nur; schreiben (Branches, PRs, Kommentare) tun die Agenten
mit ihren eigenen Git-Werkzeugen. Eigene Server werden nur über https und nie im
internen Netz abgefragt (``url_guard.check_outbound_url``).
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import NamedTuple
from urllib.parse import urlsplit

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
#: Hoechstdauer eines Abgleichs je Projekt (Scheduler, Oeffnen, Knopf).
ABGLEICH_FRIST_SEKUNDEN = 60
OEFFNEN_ABSTAND_SEKUNDEN = 60
OEFFNEN_ABSTAND_ANONYM_SEKUNDEN = 300


class GitFehler(RuntimeError):
    """Kein Zugang, Repo unbekannt, GitHub antwortet nicht — mit deutscher Meldung."""


_GITHUB_HOSTS = {"github.com", "www.github.com"}


class RepoZiel(NamedTuple):
    """Wo ein Projekt-Repo liegt. ``art``: "github" oder "forgejo" (auch Gitea)."""

    art: str
    basis: str | None   # https://server (nur Forgejo/Gitea)
    pfad: str           # owner/name


def repo_ziel(repo: str) -> RepoZiel:
    """GitHub-Repos stehen als ``owner/name``, Forgejo/Gitea als volle Adresse.

    Eine nicht lesbare Adresse (Altbestand, Handarbeit in der Datenbank) wird
    ``ungueltig`` — sie darf den Abgleich der anderen Projekte nicht abbrechen.
    """
    if repo.startswith("https://"):
        try:
            teile = urlsplit(repo)
            port = f":{teile.port}" if teile.port else ""
        except ValueError:
            return RepoZiel("ungueltig", None, "")
        if not teile.hostname or ":" in teile.hostname:
            return RepoZiel("ungueltig", None, "")
        return RepoZiel("forgejo", f"https://{teile.hostname}{port}", teile.path.strip("/"))
    return RepoZiel("github", None, repo)


def repo_pruefen(repo: str | None) -> str | None:
    """Eingabe → gespeicherte Form: ``owner/name`` (GitHub) oder
    ``https://server/owner/name`` (Forgejo/Gitea, nur https)."""
    roh = (repo or "").strip()
    if not roh:
        return None
    if roh.lower().startswith(("http://", "https://")):
        from app.core.url_guard import eindeutiger_host

        teile = urlsplit(roh)
        host = eindeutiger_host(roh)
        pfad = teile.path.strip("/").removesuffix(".git")
        if host in _GITHUB_HOSTS:
            roh = pfad
        else:
            if teile.scheme.lower() != "https":
                raise ValueError("Eigene Git-Server bitte über https angeben.")
            if not host or teile.username or teile.password or teile.query or teile.fragment:
                raise ValueError("Bitte die Adresse des Repos angeben, z. B. https://git.example.com/team/repo.")
            # Keine IPv6-Literale: als ``https://::1/…`` gespeichert waere die
            # Adresse danach nicht mehr lesbar.
            if ":" in host or "[" in teile.netloc:
                raise ValueError("Bitte einen Servernamen angeben, keine IP-Adresse in eckigen Klammern.")
            if not REPO_RE.match(pfad) or ".." in pfad:
                raise ValueError("Die Adresse muss auf owner/name enden, z. B. https://git.example.com/team/repo.")
            port = f":{teile.port}" if teile.port else ""
            return f"https://{host}{port}/{pfad}"
    roh = roh.strip("/")
    if not REPO_RE.match(roh) or ".." in roh:
        raise ValueError("Repo bitte als owner/name (GitHub) oder als Adresse "
                         "(Forgejo/Gitea, z. B. https://git.example.com/team/repo) angeben.")
    return roh


async def _ersteller(db: AsyncSession, team: Team):
    from app.models.user import User

    if not team.created_by:
        return None
    return (await db.execute(
        select(User).where((User.id == team.created_by) | (User.email == team.created_by))
    )).scalars().first()


async def schluessel_token(db: AsyncSession, team: Team, *, nur_eigener: bool = False) -> str | None:
    """Token aus dem Schlüssel des Projekts — None ohne Schlüssel, GitFehler ohne Zugriff.

    ``nur_eigener``: nur ein Schlüssel, der dem Ersteller GEHÖRT. Für eigene
    Git-Server: dort bestimmt der Ersteller den Zielserver selbst — ein ihm nur
    zur Nutzung geteilter Schlüssel ginge sonst an einen Server seiner Wahl und
    wäre abgegriffen. Bei GitHub geht ein Schlüssel ohnehin nur an GitHub.
    """
    if not team.repo_secret_id:
        return None
    from app.core import secret_zugriff
    from app.core.encryption import decrypt_token
    from app.models.agent_secret import AgentSecret

    secret = await db.get(AgentSecret, team.repo_secret_id)
    ersteller = await _ersteller(db, team)
    if secret is None or not secret.is_active or ersteller is None \
            or await secret_zugriff.zugang(db, ersteller, secret) is None:
        raise GitFehler("Der Schlüssel des Projekts fehlt oder ist für den Ersteller nicht mehr freigegeben.")
    if nur_eigener and str(secret.owner_id or "") != str(ersteller.id):
        raise GitFehler("Für eigene Git-Server nur einen eigenen Schlüssel wählen — ein geteilter Schlüssel "
                        "wird nicht an einen frei eingetragenen Server geschickt.")
    return decrypt_token(secret.value_encrypted).strip()


async def zugang(db: AsyncSession, redis, team: Team | None = None):
    """(Token, Anbieter) für ``team`` — Token leer heisst anonym (nur öffentliche Repos)."""
    import os

    from app.core.githost.registry import get_git_host_provider
    from app.models.oauth_integration import OAuthIntegration, OAuthProvider
    from app.models.user import UserRole
    from app.services.oauth_service import OAuthService

    ziel = repo_ziel(team.repo) if team is not None and team.repo else None
    if ziel is not None and ziel.art == "ungueltig":
        raise GitFehler("Die Repo-Adresse ist nicht lesbar — bitte im Projekt neu eintragen.")
    if ziel is not None and ziel.art == "forgejo":
        # Eigener Git-Server: nur der Schluessel des Projekts oder anonym — der
        # GitHub-Zugang der Anlage gilt dort nicht. Vor jeder Abfrage pruefen,
        # dass die Adresse nicht ins interne Netz zeigt.
        from app.core.githost.forgejo import ForgejoHostProvider
        from app.core.url_guard import check_outbound_url

        # Namensaufloesung im Thread: sie blockiert sonst die Ereignisschleife.
        erlaubt, grund = await asyncio.to_thread(check_outbound_url, ziel.basis or "")
        if not erlaubt:
            raise GitFehler(f"Server {ziel.basis} wird nicht abgefragt: {grund}.")
        return (await schluessel_token(db, team, nur_eigener=True)) or "", ForgejoHostProvider(ziel.basis or "")

    token = await schluessel_token(db, team) if team is not None else None
    if token is None:
        ersteller = await _ersteller(db, team) if team is not None else None
        token = ""
        if team is None or (ersteller is not None and ersteller.role == UserRole.ADMIN):
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


def _nummer(eintrag: dict) -> int | None:
    """Issue-/PR-Nummer — None, wenn der Server etwas anderes liefert."""
    nr = eintrag.get("number")
    return nr if isinstance(nr, int) and not isinstance(nr, bool) and 0 < nr < 10**9 else None


def _sichere_url(wert, anbieter) -> str | None:
    """Link aus der Antwort des Servers — nur https, ohne Leerraum, kurz, und bei
    eigenen Servern nur auf denselben Server. Er landet im Board und im Auftrag
    an den Agenten; ein fremder Server darf dort nichts unterbringen."""
    if not isinstance(wert, str) or len(wert) > 300 or not wert.startswith("https://") \
            or any(z.isspace() for z in wert):
        return None
    basis = getattr(anbieter, "base_url", None)
    if basis and not wert.startswith(basis + "/"):
        return None
    return wert


def _labels(issue: dict) -> list[str]:
    return [str(lbl.get("name")) for lbl in issue.get("labels") or [] if isinstance(lbl, dict) and lbl.get("name")][:20]


async def abgleichen(db: AsyncSession, team: Team, token: str, anbieter, client: httpx.AsyncClient) -> dict:
    """Ein Projekt mit seinem Repo abgleichen. Der Aufrufer committet."""
    repo = repo_ziel(team.repo).pfad
    server = getattr(anbieter, "anzeigename", "GitHub")
    herkunft = "github" if server == "GitHub" else server
    try:
        issues = await anbieter.list_open_issues(client, token, repo, MAX_ISSUES)
        pulls = await anbieter.list_open_pulls(client, token, repo, MAX_PRS)
    except LookupError as e:
        if token:
            raise GitFehler(f"Repo {repo} nicht gefunden — oder der Schlüssel darf es nicht lesen.") from e
        raise GitFehler(f"Repo {repo} nicht gefunden. Ist es privat, braucht das Projekt einen Schlüssel "
                        "(Bearbeiten → Schlüssel für das Repo).") from e
    except (RuntimeError, httpx.HTTPError) as e:
        raise GitFehler(f"{server} antwortet nicht für {repo}: {e}") from e

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
        nr = _nummer(issue)
        if nr is None:
            continue
        offen_issues.add(nr)
        titel = str(issue.get("title") or f"Issue #{nr}")[:300]
        text = str(issue.get("body") or "")[:TEXT_ZEICHEN]
        karte = vorhanden.get(("issue", nr))
        if karte is None:
            karte = projekte.neue_karte(team.id, titel, text, spalte="eingang", quelle="issue", extern_nr=nr,
                                        url=_sichere_url(issue.get("html_url"), anbieter), labels=_labels(issue),
                                        von=f"{herkunft}:{repo}")
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
        nr = _nummer(pr)
        if nr is None:
            continue
        offen_prs.add(nr)
        sha = (pr.get("head") or {}).get("sha")
        zustand = await anbieter.checks_state(client, token, repo, sha) if sha else "offen"
        verknuepft = [vorhanden.get(("issue", int(n))) for n in SCHLIESST_RE.findall(pr.get("body") or "")]
        verknuepft = [k for k in verknuepft if k is not None]
        if verknuepft:
            for karte in verknuepft:
                karte.pr_nr, karte.pr_url, karte.pr_zustand = nr, _sichere_url(pr.get("html_url"), anbieter), zustand
                karte.stufe = projekte.hoehere_stufe(karte.stufe, "ci" if zustand.startswith("ci_") else "pr")
                if karte.spalte in ("eingang", "bereit"):
                    # Jemand arbeitet schon daran (außerhalb des Boards): ab in die Prüfung.
                    karte.spalte = "pruefung"
            continue
        karte = vorhanden.get(("pr", nr))
        if karte is None:
            karte = projekte.neue_karte(team.id, str(pr.get("title") or f"PR #{nr}")[:300],
                                        str(pr.get("body") or "")[:TEXT_ZEICHEN], spalte="eingang", quelle="pr",
                                        extern_nr=nr, url=_sichere_url(pr.get("html_url"), anbieter), labels=_labels(pr),
                                        von=f"{herkunft}:{repo}")
            karte.spalte = "pruefung"
            db.add(karte)
            vorhanden[("pr", nr)] = karte
            neu += 1
        karte.pr_nr, karte.pr_url, karte.pr_zustand = nr, _sichere_url(pr.get("html_url"), anbieter), zustand

    # PRs, die nicht mehr offen sind: gemergt oder geschlossen?
    for karte in list(vorhanden.values()):
        if not karte.pr_nr or karte.pr_nr in offen_prs or karte.pr_zustand in ("gemergt", "geschlossen", None):
            continue
        zustand = await anbieter.pull_state(client, token, repo, karte.pr_nr)
        if zustand in ("gemergt", "geschlossen"):
            karte.pr_zustand = zustand
            if zustand == "gemergt":
                karte.stufe = "merge"
            if karte.quelle == "pr" and karte.spalte != "erledigt":
                karte.spalte, karte.erledigt_at = "erledigt", jetzt
                erledigt += 1

    team.repo_abgleich_at = jetzt
    team.repo_fehler = None
    return {"neu": neu, "aktualisiert": aktualisiert, "erledigt": erledigt,
            "offene_issues": len(issues), "offene_prs": len(pulls)}


async def projekt_abgleichen(db: AsyncSession, redis, team: Team) -> dict:
    """Ein Projekt abgleichen und den Ausgang am Projekt festhalten (auch Fehler)."""
    # Kennung VOR dem Abgleich merken: nach einem Rollback ist ``team`` verfallen,
    # ein Zugriff darauf lüde nach — in der asynchronen Sitzung ein 500er.
    team_id = team.id
    try:
        token, anbieter = await zugang(db, redis, team)
        # Frist fuer den GANZEN Abgleich: Die Zeitgrenze von httpx gilt je
        # Lesevorgang — ein Server, der alle paar Sekunden ein Byte schickt, hielte
        # sonst den Scheduler-Takt aller Projekte fest.
        async with asyncio.timeout(ABGLEICH_FRIST_SEKUNDEN):
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=False) as client:
                ergebnis = await abgleichen(db, team, token, anbieter, client)
        await db.commit()
        return ergebnis
    except TimeoutError as e:
        fehler = GitFehler(f"Der Server antwortet zu langsam (Abgleich nach {ABGLEICH_FRIST_SEKUNDEN} s abgebrochen).")
        await _fehler_vermerken(db, team_id, fehler)
        raise fehler from e
    except GitFehler as e:
        await db.rollback()
        t = await db.get(Team, team_id)
        if t is not None:
            t.repo_fehler = str(e)[:300]
            t.repo_abgleich_at = datetime.now(timezone.utc)
            await db.commit()
        raise
    except Exception as e:  # noqa: BLE001 — unerwartete Antwort eines Servers: vermerken statt 500
        log.warning("Git-Abgleich %s: unerwarteter Fehler", team_id, exc_info=True)
        fehler = GitFehler(f"Abgleich fehlgeschlagen ({type(e).__name__}).")
        await _fehler_vermerken(db, team_id, fehler)
        raise fehler from e


async def _fehler_vermerken(db: AsyncSession, team_id: str, fehler: GitFehler) -> None:
    await db.rollback()
    t = await db.get(Team, team_id)
    if t is not None:
        t.repo_fehler = str(fehler)[:300]
        t.repo_abgleich_at = datetime.now(timezone.utc)
        await db.commit()


def _sekunden_seit(zuletzt: datetime | None, jetzt: datetime) -> float | None:
    if zuletzt is None:
        return None
    return (jetzt - (zuletzt if zuletzt.tzinfo else zuletzt.replace(tzinfo=timezone.utc))).total_seconds()


async def beim_oeffnen(db: AsyncSession, redis, team: Team, jetzt: datetime | None = None) -> dict | None:
    """Abgleich beim Öffnen des Projekts — None, wenn der letzte noch frisch genug ist.

    Ohne diese Bremse kostete jeder Seitenaufruf mehrere GitHub-Abfragen; anonym
    teilen sich alle Projekte 60 je Stunde.
    """
    jetzt = jetzt or datetime.now(timezone.utc)
    try:
        anonym = not (await zugang(db, redis, team))[0]
    except GitFehler:
        return None  # Schlüssel kaputt: steht schon am Projekt (repo_fehler)
    abstand = OEFFNEN_ABSTAND_ANONYM_SEKUNDEN if anonym else OEFFNEN_ABSTAND_SEKUNDEN
    seit = _sekunden_seit(team.repo_abgleich_at, jetzt)
    if seit is not None and seit < abstand:
        return None
    return await projekt_abgleichen(db, redis, team)


async def alle_abgleichen(redis) -> int:
    """Alle Projekte mit Repo — aus dem Scheduler-Takt. Liefert die Zahl der Projekte."""
    from app.db.session import resilient_session

    async with resilient_session() as db:
        ids = (await db.execute(
            select(Team.id).where(Team.is_active.is_(True), Team.repo.is_not(None))
        )).scalars().all()
        jetzt = datetime.now(timezone.utc)
        for team_id in ids:
            # Je Projekt frisch laden: ein Rollback beim vorigen lässt geladene Objekte verfallen.
            team = await db.get(Team, team_id)
            if team is None:
                continue
            try:
                anonym = not (await zugang(db, redis, team))[0]
            except Exception:  # noqa: BLE001 — Schlüssel kaputt, Adresse unlesbar: der Abgleich meldet es am Projekt
                anonym = False
            # Anonym nur alle 30 Minuten: die Abfragegrenze (60/h) teilen sich alle Projekte.
            seit = _sekunden_seit(team.repo_abgleich_at, jetzt)
            if anonym and seit is not None and seit < ANONYM_ABSTAND_SEKUNDEN:
                continue
            try:
                await projekt_abgleichen(db, redis, team)
            except GitFehler as e:
                log.info("Git-Abgleich %s: %s", team_id, e)
            except Exception as e:  # noqa: BLE001 — ein Projekt darf die anderen nicht aufhalten
                await db.rollback()
                log.warning("Git-Abgleich %s gescheitert: %s", team_id, e)
        return len(ids)
