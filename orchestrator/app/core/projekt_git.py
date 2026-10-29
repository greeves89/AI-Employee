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
Die Plattform LIEST fast nur; schreiben (Branches, PRs, Kommentare) tun die Agenten
mit ihren eigenen Git-Werkzeugen. Ausnahmen: Übernahme und Rückgabe eines Issues
(Tag und Kommentar mit Kopfzeile, ``core/uebernahme``), das Lösen einer liegen
gebliebenen Übernahme und der Abschnitt für die ``AGENTS.md`` als Pull Request.
Vor jedem ``git push`` eines Agenten fragt der Hook ``push_pruefen``. Eigene Server werden nur über https und nie im
internen Netz abgefragt (``url_guard.check_outbound_url``).
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import NamedTuple
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import projekte, uebernahme
from app.core.fremdtext import einzeilig
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
#: So lange darf eine fremde Übernahme ohne jede Regung bleiben (keine Änderung am
#: Issue, kein Commit auf ihrem Branch, kein PR), bevor das Board sie als liegen
#: geblieben zeigt und das Projekt es meldet. Gemessen am 29.10.: eine Sitzung
#: übernahm zehn Tickets auf einmal und brachte danach stundenlang nichts ins Repo
#: — das Board zeigte sie als „in Arbeit“, und niemandem fiel es auf.
EXTERN_STILL_AB = timedelta(hours=2)
#: Höchstens so viele Commit-Abfragen je Abgleich (GitHub nennt am Branch keine Zeit).
MAX_BRANCH_ZEITEN = 10
#: Höchstens so viele Issues je Abgleich, deren Kommentare gelesen werden (wer hat
#: übernommen?). Nur übernommene Issues, und nur, wenn sie sich seit dem letzten
#: Lesen geändert haben — sonst kostete jeder Abgleich eine Abfrage je Ticket.
MAX_KOMMENTAR_ABFRAGEN = 20
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


#: Status-Tags, an denen ein Board außerhalb von AI Employee seinen Stand zeigt —
#: andere Instanzen, Claude Code, Menschen. Verglichen wird ohne ein Präfix wie
#: ``status/`` und ohne Groß-/Kleinschreibung.
_STATUS_TAGS = {
    "in_arbeit": {"in-arbeit", "in arbeit", "in-bearbeitung", "in bearbeitung", "in-progress",
                  "in progress", "wip", "doing"},
    "pruefung": {"review", "in-review", "in review", "pruefung", "prüfung", "in-pruefung", "needs-review"},
    "bereit": {"ready", "bereit", "todo", "to-do", "geplant"},
}
_STATUS_PRAEFIX = re.compile(r"^(?:status|state|stand)\s*[/:]\s*", re.I)
#: Branch-Namen wie ``feat/1044-deckel`` oder ``issue-91`` tragen die Issue-Nummer.
_BRANCH_NR = re.compile(r"(?:^|[/_-])(?:issue-?|gh-?)?(\d{1,7})(?=[-_/]|$)", re.I)


def _status_aus_tags(labels: list[str]) -> str | None:
    """Stand aus den Tags — der weiteste gewinnt (Prüfung vor In Arbeit vor Bereit)."""
    gefunden = set()
    for roh in labels:
        name = _STATUS_PRAEFIX.sub("", roh.strip()).lower()
        for stand, namen in _STATUS_TAGS.items():
            if name in namen:
                gefunden.add(stand)
    for stand in ("pruefung", "in_arbeit", "bereit"):
        if stand in gefunden:
            return stand
    return None


def _zustaendige(eintrag: dict) -> list[str]:
    """Logins der Zuständigen im Repo (GitHub und Forgejo nennen das Feld gleich)."""
    leute = eintrag.get("assignees") or ([eintrag["assignee"]] if eintrag.get("assignee") else [])
    return [str(p.get("login")) for p in leute if isinstance(p, dict) and p.get("login")][:5]


def _zeit(wert) -> datetime | None:
    """ISO-Zeit aus einer Antwort des Repos — None, wenn unlesbar."""
    if not isinstance(wert, str) or not wert:
        return None
    try:
        return _aware(datetime.fromisoformat(wert.replace("Z", "+00:00")))
    except ValueError:
        return None


def _aware(wert: datetime | None) -> datetime | None:
    if wert is None:
        return None
    return wert if wert.tzinfo else wert.replace(tzinfo=timezone.utc)


def extern_regung(karte) -> datetime | None:
    """Letztes Lebenszeichen einer fremden Übernahme."""
    werte = [w for w in (_aware(karte.extern_regung_at), _aware(karte.extern_seit)) if w]
    return max(werte) if werte else None


def extern_stockt(karte, jetzt: datetime | None = None) -> bool:
    """Ist eine fremde Übernahme liegen geblieben? Ohne Regung seit ``EXTERN_STILL_AB``."""
    if not karte.extern_bearbeiter or karte.task_id or karte.spalte != "in_arbeit":
        return False
    letzte = extern_regung(karte)
    return letzte is not None and (jetzt or datetime.now(timezone.utc)) - letzte > EXTERN_STILL_AB


def extern_nachziehen(karte, labels: list[str], zustaendige: list[str],
                      seit: datetime | None = None, jetzt: datetime | None = None) -> bool:
    """Stand einer Issue-Karte an das anpassen, was AUSSERHALB des Boards passiert.

    Nimmt ein anderer Agent (eine andere AI-Employee-Anlage, Claude Code) oder ein
    Mensch ein Ticket, sieht man das im Repo an Zuständigen oder an Status-Tags.
    Vorher blieb die Karte im Eingang — man sah nicht, dass jemand dran ist, und
    der Takt hätte sie einem eigenen Agenten noch einmal gegeben.

    WER dran ist, steht in der Übernahme-Kopfzeile (``core/uebernahme``), die der
    Abgleich vorher in ``karte.uebernahme`` gelesen hat: ein Agent dieser Anlage,
    Claude Code, eine andere Anlage, ein Mensch. Ohne Kopfzeile bleiben die
    Zuständigen („@login“, Art ``mensch``) bzw. „extern“.

    Eine Karte mit eigenem Lauf (``task_id``) wird nicht verschoben, sie bekommt
    nur den Namen dazu: zwei arbeiten am selben Ticket, das soll man sehen.
    Gibt zurück, ob sich etwas geändert hat.
    """
    stand = _status_aus_tags(labels)
    extern_dran = bool(zustaendige) or stand == "in_arbeit"
    ueb = uebernahme.Uebernahme.aus_dict(karte.uebernahme) if extern_dran else None
    eigen = ueb is not None and ueb.gehoert(agent_id=karte.agent_id, lauf=karte.task_id)
    vorher = (karte.spalte, karte.extern_bearbeiter, karte.bearbeiter_art)
    if not extern_dran:
        bearbeiter = art = None
        karte.uebernahme = None
    elif ueb is not None and not eigen:
        bearbeiter, art = ueb.name, ueb.art
    elif karte.task_id and (eigen or not zustaendige):
        # Arbeitet ein eigener Agent daran, ist ein In-Arbeit-Tag unser eigenes
        # (``uebernahme_melden``) — kein Fremder.
        bearbeiter = art = None
    elif zustaendige:
        bearbeiter, art = ", ".join(f"@{z}" for z in zustaendige), uebernahme.MENSCH
    else:
        bearbeiter, art = "extern", uebernahme.EXTERN
    if bearbeiter is None:
        karte.extern_seit = karte.extern_regung_at = karte.extern_branch = None
    elif ueb is not None and not eigen and ueb.seit is not None:
        # Die Kopfzeile sagt genau, seit wann.
        karte.extern_seit = ueb.seit
    elif karte.extern_seit is None:
        # Wann übernommen wurde, sagt das Repo sonst nicht direkt. Die letzte Änderung
        # am Issue ist die beste Schätzung (das Tag zu setzen ändert es) — sonst jetzt.
        jetzt = jetzt or datetime.now(timezone.utc)
        karte.extern_seit = min(seit, jetzt) if seit else jetzt
    karte.extern_bearbeiter = bearbeiter[:200] if bearbeiter else None
    karte.bearbeiter_art = art
    if karte.task_id or karte.spalte == "erledigt":
        return vorher != (karte.spalte, karte.extern_bearbeiter, karte.bearbeiter_art)
    if stand == "pruefung" and karte.spalte in ("eingang", "bereit", "in_arbeit"):
        karte.spalte = "pruefung"
    elif extern_dran and karte.spalte in ("eingang", "bereit"):
        karte.spalte = "in_arbeit"
    elif not extern_dran and karte.spalte == "in_arbeit":
        # Extern niemand mehr dran (Tag entfernt, Zuständigkeit gelöst): zurück.
        karte.spalte = "bereit" if stand == "bereit" else "eingang"
    elif stand == "bereit" and karte.spalte == "eingang":
        karte.spalte = "bereit"
    return vorher != (karte.spalte, karte.extern_bearbeiter, karte.bearbeiter_art)


def _uebernommen(labels: list[str], zustaendige: list[str]) -> bool:
    """Zeigt das Issue, dass jemand dran ist (Zuständige oder In-Arbeit-Tag)?"""
    return bool(zustaendige) or _status_aus_tags(labels) == "in_arbeit"


async def _uebernahme_lesen(anbieter, client, token: str, repo: str, karte, geaendert_am: datetime | None,
                            jetzt: datetime) -> bool:
    """Kommentare eines übernommenen Issues lesen und den Inhaber an der Karte merken.

    Nur, wenn sich das Issue seit dem letzten Lesen geändert hat (ein neuer
    Kommentar ändert es). Gibt zurück, ob gelesen wurde (für die Obergrenze).
    """
    if not hasattr(anbieter, "list_issue_comments"):
        return False
    gelesen = _aware(karte.kommentare_gelesen_at)
    if gelesen is not None and geaendert_am is not None and geaendert_am <= gelesen:
        return False
    try:
        kommentare = await anbieter.list_issue_comments(client, token, repo, karte.extern_nr)
    except Exception as e:  # noqa: BLE001 — ohne Kommentare bleibt es bei Zuständigen/Tag
        log.info("[Projekt] Kommentare von #%s nicht lesbar: %s", karte.extern_nr, e)
        return True
    u = uebernahme.inhaber(kommentare)
    karte.uebernahme = u.als_dict() if u else None
    karte.kommentare_gelesen_at = jetzt
    return True


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
    except httpx.HTTPError as e:
        raise GitFehler(f"{server} antwortet nicht für {repo}: {e}") from e
    except RuntimeError as e:
        # Der Server hat geantwortet, aber abgelehnt oder Unbrauchbares geliefert —
        # die Meldung des Anbieters sagt, was fehlt.
        raise GitFehler(f"{server}, {repo}: {e}") from e

    jetzt = datetime.now(timezone.utc)
    vorhanden = {
        (k.quelle, k.extern_nr): k
        for k in (await db.execute(
            select(ProjektKarte).where(ProjektKarte.team_id == team.id, ProjektKarte.quelle.in_(("issue", "pr")))
        )).scalars()
    }
    neu = aktualisiert = erledigt = 0
    kommentar_abfragen = 0

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
        geaendert_am = _zeit(issue.get("updated_at"))
        if _uebernommen(_labels(issue), _zustaendige(issue)) and kommentar_abfragen < MAX_KOMMENTAR_ABFRAGEN:
            if await _uebernahme_lesen(anbieter, client, token, repo, karte, geaendert_am, jetzt):
                kommentar_abfragen += 1
        if extern_nachziehen(karte, _labels(issue), _zustaendige(issue), seit=geaendert_am, jetzt=jetzt) \
                and karte.extern_bearbeiter is not None:
            aktualisiert += 1
        if karte.extern_bearbeiter and geaendert_am and (
                karte.extern_regung_at is None or geaendert_am > _aware(karte.extern_regung_at)):
            karte.extern_regung_at = geaendert_am

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
        # „Closes #N“ im Text — oder die Nummer im Branch-Namen (``feat/1044-…``):
        # viele PRs nennen das Issue nur dort.
        nummern = set(SCHLIESST_RE.findall(pr.get("body") or ""))
        nummern.update(_BRANCH_NR.findall(str((pr.get("head") or {}).get("ref") or "")))
        verknuepft = [vorhanden.get(("issue", int(n))) for n in nummern]
        verknuepft = [k for k in verknuepft if k is not None]
        if verknuepft:
            for karte in verknuepft:
                karte.pr_nr, karte.pr_url, karte.pr_zustand = nr, _sichere_url(pr.get("html_url"), anbieter), zustand
                karte.stufe = projekte.hoehere_stufe(karte.stufe, "ci" if zustand.startswith("ci_") else "pr")
                if karte.spalte in ("eingang", "bereit") or (karte.spalte == "in_arbeit" and not karte.task_id):
                    # Jemand arbeitet schon daran (außerhalb des Boards): ab in die Prüfung.
                    karte.spalte = "pruefung"
                if karte.task_id and UEBERNAHME_TAG in (karte.labels or []) and token:
                    # Eigener Agent hat den PR gestellt: das Ticket ist nicht mehr „in Arbeit“,
                    # sondern wartet auf Prüfung — wie es die Übernahmeregeln verlangen.
                    try:
                        if await anbieter.remove_label(client, token, repo, karte.extern_nr, UEBERNAHME_TAG):
                            karte.labels = [x for x in karte.labels if x != UEBERNAHME_TAG]
                    except Exception as e:  # noqa: BLE001 — Beiwerk
                        log.info("[Projekt] Tag an #%s nicht entfernt: %s", karte.extern_nr, e)
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

    await _extern_branches(anbieter, client, token, repo, list(vorhanden.values()))

    team.repo_abgleich_at = jetzt
    team.repo_fehler = None
    return {"neu": neu, "aktualisiert": aktualisiert, "erledigt": erledigt,
            "offene_issues": len(issues), "offene_prs": len(pulls)}


async def _extern_branches(anbieter, client: httpx.AsyncClient, token: str, repo: str, karten: list) -> None:
    """Branch und letzten Commit fremder Übernahmen nachziehen — ein Lebenszeichen.

    Wer nach dem Übernehmen pusht, arbeitet; das sieht man am Issue nicht. Eine
    Abfrage der Branches je Abgleich, Commit-Zeiten nur für die betroffenen.
    """
    offen = [k for k in karten if k.quelle == "issue" and k.extern_bearbeiter and not k.task_id
             and k.spalte == "in_arbeit" and not k.pr_nr]
    if not offen or not hasattr(anbieter, "list_branches"):
        return
    try:
        branches = await anbieter.list_branches(client, token, repo)
    except Exception as e:  # noqa: BLE001 — Beiwerk: ohne Branches zählt die Issue-Zeit
        log.info("[Projekt] Branches von %s nicht lesbar: %s", repo, e)
        return
    je_nr: dict[int, dict] = {}
    for b in branches:
        for n in _BRANCH_NR.findall(str(b.get("name") or "")):
            je_nr.setdefault(int(n), b)
    abfragen = 0
    for karte in offen:
        b = je_nr.get(karte.extern_nr)
        karte.extern_branch = str(b["name"])[:200] if b else None
        if not b:
            continue
        zeit = _zeit(b.get("zeit"))
        if zeit is None and b.get("sha") and abfragen < MAX_BRANCH_ZEITEN and hasattr(anbieter, "commit_zeit"):
            abfragen += 1
            try:
                zeit = _zeit(await anbieter.commit_zeit(client, token, repo, b["sha"]))
            except Exception:  # noqa: BLE001
                zeit = None
        if zeit and (karte.extern_regung_at is None or zeit > _aware(karte.extern_regung_at)):
            karte.extern_regung_at = zeit


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


# ── Übernahme ins Repo zurückmelden ───────────────────────────────────────────
#: Tag, mit dem ein Board außerhalb (andere Anlage, Claude Code, Mensch) sieht, dass
#: jemand an einem Issue arbeitet. Dieselbe Vereinbarung, die ``_STATUS_TAGS`` liest.
UEBERNAHME_TAG = "status/in-arbeit"


async def extern_dran(db: AsyncSession, redis, team: Team, karte: ProjektKarte) -> str | None:
    """Arbeitet schon jemand außerhalb des Boards an diesem Issue? Live im Repo gefragt.

    Der Abgleich läuft höchstens alle paar Minuten — in der Zwischenzeit hätte das
    Board ein Ticket vergeben, das gerade jemand anderes übernommen hat. Liefert
    den Namen (aus der Übernahme-Kopfzeile, sonst ``@login`` oder „extern“) oder
    None. Ist das Repo nicht erreichbar, entscheidet der letzte Abgleich.
    """
    if karte.quelle != "issue" or not karte.extern_nr or not team.repo:
        return None
    # Ein offener PR zum Ticket (aus dem letzten Abgleich) heißt: jemand ist dran.
    if karte.pr_nr and karte.pr_zustand not in ("gemergt", "geschlossen") and not karte.task_id:
        return f"PR #{karte.pr_nr}"
    try:
        token, anbieter = await zugang(db, redis, team)
        repo = repo_ziel(team.repo).pfad
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0), follow_redirects=False) as client:
            issue = await anbieter.get_issue(client, token, repo, karte.extern_nr)
            if not isinstance(issue, dict):
                return karte.extern_bearbeiter
            if not _uebernommen(_labels(issue), _zustaendige(issue)):
                return None
            kommentare = (await anbieter.list_issue_comments(client, token, repo, karte.extern_nr)
                          if hasattr(anbieter, "list_issue_comments") else [])
    except Exception as e:  # noqa: BLE001 — Repo nicht erreichbar: nicht blockieren
        log.info("[Projekt] Übernahme-Prüfung #%s übersprungen: %s", karte.extern_nr, e)
        return karte.extern_bearbeiter
    u = uebernahme.inhaber(kommentare)
    if u is not None:
        return u.name
    leute = _zustaendige(issue)
    return ", ".join(f"@{z}" for z in leute) if leute else "extern"


async def uebernahme_melden(team_id: str, karte_id: str, redis, *, agent_name: str | None,
                            uebernehmen: bool = True, agent_id: str | None = None,
                            lauf: str | None = None) -> None:
    """Übernahme (oder Rückgabe) eines Issues ins Repo schreiben — im Hintergrund.

    Übernehmen: Tag ``status/in-arbeit`` und ein Kommentar mit Übernahme-Kopfzeile
    (``core/uebernahme``: Anlage, Agent, Lauf, seit/bis) und der Vereinbarung für
    Menschen (Branch ``<nr>-…``, PR mit ``Closes #<nr>``). Zurückgeben: Tag weg und
    ein Kommentar mit Rückgabe-Kopfzeile. Scheitert es (etwa ein Schlüssel ohne
    Schreibrecht), steht das als Notiz an der Karte — die Arbeit selbst läuft trotzdem.
    """
    from app.db.session import async_session_factory

    async with async_session_factory() as db:
        team = await db.get(Team, team_id)
        karte = await db.get(ProjektKarte, karte_id)
        if team is None or karte is None or karte.quelle != "issue" or not karte.extern_nr or not team.repo:
            return
        nr = karte.extern_nr
        try:
            token, anbieter = await zugang(db, redis, team)
            if not token:
                raise GitFehler("ohne Schlüssel kann das Board nichts ins Repo schreiben")
            repo = repo_ziel(team.repo).pfad
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=False) as client:
                if uebernehmen:
                    ok = await anbieter.add_labels(client, token, repo, nr, [UEBERNAHME_TAG])
                    await anbieter.comment_issue(client, token, repo, nr, uebernahme.uebernahme_kommentar(
                        agent_name=agent_name, agent_id=agent_id, lauf=lauf, nr=nr, projekt_name=team.name))
                else:
                    ok = await anbieter.remove_label(client, token, repo, nr, UEBERNAHME_TAG)
                    await anbieter.comment_issue(client, token, repo, nr, uebernahme.rueckgabe_kommentar(
                        agent_name=agent_name, agent_id=agent_id, lauf=lauf))
            if not ok:
                raise GitFehler(f"Tag {UEBERNAHME_TAG} ließ sich nicht setzen (Schreibrecht für Issues?)")
        except Exception as e:  # noqa: BLE001 — Rückmeldung ist Beiwerk, die Arbeit läuft
            log.warning("[Projekt] Übernahme #%s nicht ins Repo gemeldet: %s", nr, e)
            karte.notiz = f"Übernahme nicht ins Repo gemeldet: {str(e)[:200]}"[:500]
            await db.commit()


async def extern_freigeben(db: AsyncSession, redis, team: Team, karte: ProjektKarte) -> None:
    """Eine liegen gebliebene fremde Übernahme lösen — auf Wunsch des Menschen.

    Im Repo: In-Arbeit-Tags und Zuständige weg, ein Kommentar mit Rückgabe-Kopfzeile
    dazu. Auf dem Board: die Karte zurück nach „Bereit“, der Lead kann sie vergeben.
    Der Aufrufer committet. Scheitert das Schreiben ins Repo, bleibt alles, wie es
    war — sonst holte der nächste Abgleich die Übernahme gleich wieder zurück.
    """
    if karte.quelle != "issue" or not karte.extern_nr or not team.repo:
        raise GitFehler("Nur Karten aus Issues haben eine Übernahme im Repo.")
    if not karte.extern_bearbeiter or karte.task_id or karte.spalte != "in_arbeit":
        raise GitFehler("An dieser Karte arbeitet niemand von außen.")
    token, anbieter = await zugang(db, redis, team)
    if not token:
        raise GitFehler("Ohne Schlüssel kann das Board nichts ins Repo schreiben (Bearbeiten → Schlüssel).")
    repo, nr = repo_ziel(team.repo).pfad, karte.extern_nr
    letzte = extern_regung(karte)
    seit = ""
    if letzte is not None:
        stunden = max(1, int((datetime.now(timezone.utc) - letzte).total_seconds() // 3600))
        seit = f" seit {stunden} Std."
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=False) as client:
        issue = await anbieter.get_issue(client, token, repo, nr)
        if not isinstance(issue, dict):
            raise GitFehler(f"Issue #{nr} ist nicht lesbar.")
        for tag in _labels(issue):
            if _status_aus_tags([tag]) == "in_arbeit" and not await anbieter.remove_label(client, token, repo, nr, tag):
                raise GitFehler(f"Tag {tag} ließ sich nicht entfernen (Schreibrecht für Issues?).")
        leute = _zustaendige(issue)
        if leute and not await anbieter.remove_assignees(client, token, repo, nr, leute):
            raise GitFehler("Die Zuständigen ließen sich nicht austragen (Schreibrecht für Issues?).")
        # Ohne Namen: das Repo kann öffentlich sein. Eine Rückgabe-Kopfzeile ohne
        # ``wer`` gibt jede Übernahme frei.
        await anbieter.comment_issue(
            client, token, repo, nr,
            uebernahme.kopfzeile("release") + "\n"
            f"Übernahme gelöst über das Projekt-Board: {seit.strip() or 'lange'} keine Bewegung "
            "(kein Commit, kein Pull Request). Das Ticket ist wieder frei. Wer weiterarbeitet, setzt bitte "
            f"erneut `{UEBERNAHME_TAG}`.")
    karte.extern_bearbeiter = karte.bearbeiter_art = karte.uebernahme = None
    karte.extern_seit = karte.extern_regung_at = karte.extern_branch = None
    karte.labels = [t for t in (karte.labels or []) if _status_aus_tags([t]) != "in_arbeit"]
    karte.spalte = "bereit"
    karte.notiz = (f"Fremde Übernahme gelöst — {seit.strip()} keine Bewegung." if seit
                   else "Fremde Übernahme gelöst.")


def uebernahme_im_hintergrund(team_id: str, karte_id: str, redis, *, agent_name: str | None,
                              uebernehmen: bool = True, agent_id: str | None = None,
                              lauf: str | None = None) -> None:
    """``uebernahme_melden`` ohne den Aufrufer warten zu lassen."""
    try:
        asyncio.get_running_loop().create_task(
            uebernahme_melden(team_id, karte_id, redis, agent_name=agent_name, uebernehmen=uebernehmen,
                              agent_id=agent_id, lauf=lauf))
    except RuntimeError:
        pass


# ── Push-Sperre: kein Push in den Branch einer fremden Übernahme ─────────────

#: Was der Hook einem Lauf des Arbeitstakts sagt, der pushen will.
TAKT_PUSH_GRUND = (
    "Push gesperrt: Dies ist ein Lauf des Arbeitstakts. Im Takt koordinierst du nur — Code entsteht in "
    "Karten-Läufen. Vergib die Karte mit `projekt_karte_vergeben` (bist du allein im Projekt, an dich selbst) "
    "und arbeite im Lauf der Karte weiter.")


def remote_ziel(remote: str | None) -> tuple[str, str] | None:
    """(Host, owner/name) aus einer Remote-Adresse — https, ssh oder ``git@host:owner/name``."""
    roh = (remote or "").strip()
    if not roh or len(roh) > 500 or any(z.isspace() for z in roh):
        return None
    scp = re.match(r"^[A-Za-z0-9._-]+@([A-Za-z0-9.-]+):(?!//)(.+)$", roh)
    if scp:
        host, pfad = scp.group(1), scp.group(2)
    else:
        try:
            host = urlsplit(roh).hostname or ""
            pfad = urlsplit(roh).path
        except ValueError:
            return None
    pfad = pfad.strip("/").removesuffix(".git")
    if not host or not REPO_RE.match(pfad):
        return None
    return host.lower(), pfad.lower()


def repo_passt(repo: str, remote: str | None) -> bool:
    """Gehört die Remote-Adresse zum Repo des Projekts?"""
    ziel = remote_ziel(remote)
    if ziel is None:
        return False
    host, pfad = ziel
    projekt = repo_ziel(repo)
    if projekt.pfad.lower() != pfad:
        return False
    if projekt.art == "forgejo":
        return (urlsplit(projekt.basis or "").hostname or "").lower() == host
    # GitHub (auch Enterprise): der Pfad genügt — ein eigener Forgejo-Server mit
    # demselben owner/name stünde im Projekt als volle Adresse.
    return True


async def push_pruefen(db: AsyncSession, redis, agent_id: str, *, lauf: str | None,
                       ziele: list[dict]) -> dict:
    """Darf dieser Agent (in diesem Lauf) auf diese Branches pushen?

    Gefragt vom PreToolUse-Hook (Claude Code, Codex) und vom Custom-LLM-Executor
    vor jedem ``git push``. Zwei Regeln:

    1. Läufe des Arbeitstakts pushen gar nicht — der Lead koordiniert dort nur.
    2. Trägt der Ziel-Branch eine Issue-Nummer (``_BRANCH_NR``) und hat ein ANDERER
       das Issue übernommen (Übernahme-Kopfzeile, sonst Tag/Zuständige), ist der
       Push gesperrt. Der eigene Lauf und der eigene Agent dürfen.

    Bewusst fail-open: Ist das Repo nicht erreichbar, gehört der Branch zu keinem
    Projekt des Agenten oder ist unklar, welches Repo gemeint ist, wird nicht
    gesperrt, nur protokolliert. Ziel ist, Zusammenstöße unter Kollegen zu
    vermeiden — keine Sicherheitsgrenze (wer Kommentare schreiben kann, kann jede
    Übernahme schreiben; ein Agent mit Shell kann am Hook vorbei pushen).
    """
    from app.core import projekt_takt
    from app.models.agent import Agent
    from app.models.task import Task, TaskStatus

    agent = await db.get(Agent, agent_id)
    if agent is None:
        return {"erlaubt": True, "grund": None}
    task = await db.get(Task, lauf) if lauf else None
    if task is not None and task.agent_id != agent_id:
        task = None
    takt_plaene = set((await db.execute(
        select(Team.takt_schedule_id).where(Team.takt_schedule_id.is_not(None)))).scalars().all())
    if task is None and not lauf:
        # Lauf unbekannt (ältere Hook-Einstellung, Chat): laufen gerade NUR Takt-Läufe
        # dieses Agenten, gehört der Push zu einem davon.
        laufend = (await db.execute(select(Task).where(
            Task.agent_id == agent_id, Task.status == TaskStatus.RUNNING).limit(10))).scalars().all()
        if laufend and all(projekt_takt.ist_takt_lauf(t, takt_plaene) for t in laufend):
            task = laufend[0]
    if task is not None and projekt_takt.ist_takt_lauf(task, takt_plaene):
        log.info("[Push-Sperre] Agent %s, Takt-Lauf %s: Push gesperrt", agent_id, task.id)
        return {"erlaubt": False, "grund": TAKT_PUSH_GRUND}

    projekte_des_agenten = [
        t for t in (await db.execute(
            select(Team).where(Team.is_active.is_(True), Team.repo.is_not(None)))).scalars().all()
        if agent_id in set(t.member_agent_ids or []) | ({t.lead_agent_id} if t.lead_agent_id else set())
    ]
    for ziel in ziele[:10]:
        branch = einzeilig(str(ziel.get("branch") or ""), 200)
        nummern = _BRANCH_NR.findall(branch)
        if not nummern:
            continue
        remote = ziel.get("remote")
        if remote:
            kandidaten = [t for t in projekte_des_agenten if repo_passt(t.repo, remote)]
        else:
            kandidaten = projekte_des_agenten if len(projekte_des_agenten) == 1 else []
        if not kandidaten:
            log.info("[Push-Sperre] Agent %s, Branch %s: kein Projekt-Repo zugeordnet — nicht geprüft",
                     agent_id, branch)
        for team in kandidaten[:3]:
            grund = await _fremde_uebernahme(db, redis, team, int(nummern[0]), branch, agent, lauf)
            if grund:
                log.info("[Push-Sperre] Agent %s, Lauf %s, Branch %s: gesperrt", agent_id, lauf, branch)
                return {"erlaubt": False, "grund": grund}
    return {"erlaubt": True, "grund": None}


async def _fremde_uebernahme(db: AsyncSession, redis, team: Team, nr: int, branch: str, agent,
                             lauf: str | None) -> str | None:
    """Sperrgrund, wenn Issue ``nr`` im Repo des Projekts einem anderen gehört — sonst None."""
    karte = (await db.execute(select(ProjektKarte).where(
        ProjektKarte.team_id == team.id, ProjektKarte.quelle == "issue", ProjektKarte.extern_nr == nr)
    )).scalars().first()
    if karte is not None and lauf and karte.task_id == lauf:
        return None  # der Lauf, an den das Board die Karte vergeben hat
    try:
        token, anbieter = await zugang(db, redis, team)
        repo = repo_ziel(team.repo).pfad
        # Knapp: der Hook wartet höchstens 15 s auf die ganze Entscheidung.
        async with httpx.AsyncClient(timeout=httpx.Timeout(4.0), follow_redirects=False) as client:
            issue = await anbieter.get_issue(client, token, repo, nr)
            if not isinstance(issue, dict) or not _uebernommen(_labels(issue), _zustaendige(issue)):
                return None
            kommentare = (await anbieter.list_issue_comments(client, token, repo, nr)
                          if hasattr(anbieter, "list_issue_comments") else [])
    except Exception as e:  # noqa: BLE001 — nicht erreichbar: nicht sperren (siehe push_pruefen)
        log.info("[Push-Sperre] #%s in %s nicht prüfbar, Push erlaubt: %s", nr, team.id, e)
        return None
    u = uebernahme.inhaber(kommentare)
    if u is not None and u.gehoert(agent_id=agent.id, lauf=lauf, agent_name=agent.name):
        return None
    if u is None and karte is not None and karte.agent_id == agent.id:
        return None  # eigene Karte, nur der Übernahme-Kommentar fehlt
    return (f"Push auf `{branch}` gesperrt: Issue #{nr} hat {uebernahme.anzeige(u)} übernommen — der Branch "
            "gehört dem Übernehmer. Stattdessen: im Issue nachfragen, einen eigenen Branch anlegen und einen "
            f"Pull Request in `{branch}` stellen, oder den Menschen bitten, die Übernahme im Projekt-Board "
            "freizugeben.")


# ── Abschnitt „Zusammenarbeit mit AI Employee“ für die AGENTS.md ─────────────

AGENTS_MD_BRANCH = "ai-employee/agents-md-zusammenarbeit"


async def agents_md_pr(db: AsyncSession, redis, team: Team) -> dict:
    """Den Abschnitt als Pull Request ins Repo stellen — nie direkt auf den Standard-Branch.

    Liefert ``{"url": …}`` oder ``{"url": None, "aktuell": True}``, wenn der
    Abschnitt schon so im Repo steht. GitFehler mit klarer Meldung, wenn der
    Schlüssel nicht schreiben darf.
    """
    if not team.repo:
        raise GitFehler("Das Projekt hat kein Repo.")
    token, anbieter = await zugang(db, redis, team)
    if not token:
        raise GitFehler("Ohne Schlüssel kann das Board nichts ins Repo schreiben (Bearbeiten → Schlüssel).")
    if not hasattr(anbieter, "pull_anlegen"):
        raise GitFehler("Dieser Git-Server kann hier keine Pull Requests anlegen — bitte den Text kopieren.")
    repo = repo_ziel(team.repo).pfad
    abschnitt = uebernahme.agents_md_abschnitt()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0), follow_redirects=False) as client:
            basis = await anbieter.standard_branch(client, token, repo) or "main"
            auf_basis, _ = await anbieter.datei_holen(client, token, repo, "AGENTS.md", basis)
            if auf_basis is not None and uebernahme.agents_md_einfuegen(auf_basis, abschnitt) == auf_basis:
                return {"url": None, "aktuell": True}
            await anbieter.branch_anlegen(client, token, repo, AGENTS_MD_BRANCH, basis)
            bisher, sha = await anbieter.datei_holen(client, token, repo, "AGENTS.md", AGENTS_MD_BRANCH)
            neu = uebernahme.agents_md_einfuegen(bisher if bisher is not None else auf_basis, abschnitt)
            if neu != bisher:
                await anbieter.datei_schreiben(client, token, repo, "AGENTS.md", neu, AGENTS_MD_BRANCH,
                                               "docs: Abschnitt „Zusammenarbeit mit AI Employee“ in der AGENTS.md",
                                               sha)
            url = await anbieter.pull_anlegen(
                client, token, repo, AGENTS_MD_BRANCH, basis, "AGENTS.md: Zusammenarbeit mit AI Employee",
                "Regeln für Agenten an diesem Repo: Übernahme-Kopfzeile, Lebenszeichen, der Branch gehört dem "
                "Übernehmer, Übergabe und Rückkanal. Erzeugt über das Projekt-Board von AI Employee.")
    except RuntimeError as e:
        raise GitFehler(f"Kein Pull Request angelegt: {e}") from e
    except httpx.HTTPError as e:
        raise GitFehler(f"Kein Pull Request angelegt: der Server antwortet nicht ({type(e).__name__}).") from e
    return {"url": _sichere_url(url, anbieter), "aktuell": False}
