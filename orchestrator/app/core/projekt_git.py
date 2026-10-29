"""Git-Projekte: Issues und Pull Requests eines Repos auf dem Board.

Ein Projekt kann ein Repo haben: auf GitHub (``teams.repo`` = ``owner/name``) oder
auf einem eigenen Forgejo/Gitea-Server (``teams.repo`` = ``https://server/owner/name``,
siehe ``repo_ziel``; Anbieter ``githost/forgejo.py``). Der Scheduler gleicht alle zehn Minuten ab (``alle_abgleichen``), auf Knopfdruck
auch sofort (``POST /teams/{id}/repo/abgleichen``):

* offene Issues → Karten in der **Inbox** (neu) bzw. Titel/Text/Labels aktualisiert;
  Status-Tags ziehen die Spalte nach (``extern_nachziehen``: Review, Triage, In Arbeit, Bereit);
  der Platz auf dem Board (Spalte, Priorität) gehört dem Projekt und bleibt.
* geschlossenes Issue → Karte **erledigt**, als „not planned“ **verworfen** (außer sie ist gerade in Arbeit:
  dann eine Notiz; der Lauf entscheidet).
* offene PRs → verknüpft mit der Issue-Karte, die sie schließen („Fixes #12“),
  sonst eine eigene Karte im **Review** („Wartet auf Merge“); dazu der
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
from app.models.projekt_karte import ABGESCHLOSSEN, ProjektKarte
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
#: ``status/`` und ohne Groß-/Kleinschreibung. Agenten-Präfixe (``agent:``,
#: ``claude:`` …) gelten NUR für Triage: Ein Agent darf eine Rückfrage anzeigen,
#: aber nie freigeben (``agent:ready`` ist kein Bereit-Tag).
_STATUS_TAGS = {
    "in_arbeit": {"in-arbeit", "in arbeit", "in-bearbeitung", "in bearbeitung", "in-progress",
                  "in progress", "wip", "doing"},
    "pruefung": {"review", "in-review", "in review", "pruefung", "prüfung", "in-pruefung", "needs-review"},
    # Rückfrage offen, wartet auf einen Menschen. Ein bloßes ``blocked`` zählt
    # nicht (blockiert kann vieles sein) — nur mit Agenten-Präfix (``agent:blocked``).
    "triage": {"triage", "needs-triage", "needs triage", "needs-human", "needs human", "needs-info",
               "needs info", "question", "rückfrage", "rueckfrage"},
    # Freigabe durch einen Menschen: nur „ready“/„bereit“ (mit oder ohne status/).
    # Kein „todo“/„geplant“ — die stehen in vielen Repos an Ungesichtetem.
    "bereit": {"ready", "bereit"},
}
#: Präfixe, die vor einem Status-Tag stehen dürfen (``status/ready``, ``agent:blocked``).
_STATUS_PRAEFIX = re.compile(r"^(status|state|stand|agent|agents|claude|ai|ki|bot|codex)\s*[/:]\s*", re.I)
#: Präfixe von Agenten — nur für Triage (und ``blocked`` heißt nur mit ihnen „Rückfrage“).
_AGENT_PRAEFIXE = {"agent", "agents", "claude", "ai", "ki", "bot", "codex"}
#: Vorrang beim Abgleich: der erste gefundene Stand gewinnt.
_STAND_VORRANG = ("pruefung", "triage", "in_arbeit", "bereit")
#: Welches Tag das Board setzt, wenn das Repo noch keins dieser Art hat — sonst
#: das erste aus der Liste, das es im Repo schon gibt (``passendes_tag``).
STATUS_TAG_WAHL = {
    "triage": ("status/triage", "agent:blocked", "agent/blocked", "triage", "needs-triage", "status/needs-human",
               "claude:needs-human", "needs-human", "needs-info", "status/rückfrage", "rückfrage", "rueckfrage"),
    "bereit": ("status/ready", "ready", "status/bereit", "bereit"),
    "in_arbeit": ("status/in-arbeit", "status/in-progress", "in-progress", "in-arbeit", "status/wip", "wip"),
}
#: Branch-Namen wie ``feat/1044-deckel`` oder ``issue-91`` tragen die Issue-Nummer.
_BRANCH_NR = re.compile(r"(?:^|[/_-])(?:issue-?|gh-?)?(\d{1,7})(?=[-_/]|$)", re.I)


def tag_stand(roh: str) -> str | None:
    """Wofür ein einzelnes Tag steht: pruefung, triage, in_arbeit, bereit — oder None."""
    text = (roh or "").strip().lower()
    treffer = _STATUS_PRAEFIX.match(text)
    praefix = treffer.group(1) if treffer else ""
    name = text[treffer.end():] if treffer else text
    if praefix in _AGENT_PRAEFIXE:
        # Agenten zeigen nur Rückfragen an — nie Freigabe, Übernahme oder Review.
        return "triage" if name == "blocked" or name in _STATUS_TAGS["triage"] else None
    for stand, namen in _STATUS_TAGS.items():
        if name in namen:
            return stand
    return None


def _status_aus_tags(labels: list[str]) -> str | None:
    """Stand aus den Tags — Review vor Triage vor In Arbeit vor Bereit (``_STAND_VORRANG``)."""
    gefunden = {tag_stand(roh) for roh in labels or []}
    return next((stand for stand in _STAND_VORRANG if stand in gefunden), None)


def tags_der_art(labels: list[str], stand: str) -> list[str]:
    """Alle Tags aus ``labels``, die für ``stand`` stehen (erkannt, auch fremde)."""
    return [t for t in labels or [] if tag_stand(t) == stand]


def eigene_tags(labels: list[str], stand: str) -> list[str]:
    """Tags aus ``labels``, die das Board selbst für ``stand`` setzt (``STATUS_TAG_WAHL``).

    Nur diese nimmt das Board weg, wenn es aufräumt — ein Tag, das jemand anderes
    gesetzt hat (``wip``, ``claude:needs-human``), bleibt."""
    eigene = {t.lower() for t in STATUS_TAG_WAHL[stand]}
    return [t for t in labels or [] if t.strip().lower() in eigene]


#: Tags je Repo und Zugang, kurz gemerkt — das Board setzt das Tag, das es im Repo
#: schon gibt. Schlüssel mit Prüfsumme des Tokens (ein anderer Zugang sieht vielleicht
#: anderes); Fehler werden nicht gemerkt; höchstens ``REPO_TAGS_MAX`` Einträge.
_REPO_TAGS: dict[str, tuple[list[str], float]] = {}
REPO_TAGS_SEKUNDEN = 600
REPO_TAGS_MAX = 256


async def repo_tags(anbieter, client, token: str, repo: str) -> list[str]:
    """Die Tags (Labels) eines Repos, zehn Minuten gemerkt. Leer, wenn nicht lesbar."""
    import hashlib
    import time

    if not hasattr(anbieter, "list_labels"):
        return []
    zugang_kurz = hashlib.sha256((token or "").encode()).hexdigest()[:12]
    schluessel = f"{getattr(anbieter, 'api_base', '')}|{repo}|{zugang_kurz}".lower()
    gemerkt = _REPO_TAGS.get(schluessel)
    if gemerkt and gemerkt[1] > time.monotonic():
        return gemerkt[0]
    try:
        tags = [str(t) for t in await anbieter.list_labels(client, token, repo) if t][:500]
    except Exception as e:  # noqa: BLE001 — dann eben das Standard-Tag, nicht gemerkt
        log.info("[Projekt] Tags von %s nicht lesbar: %s", repo, e)
        return []
    _REPO_TAGS.pop(schluessel, None)
    while len(_REPO_TAGS) >= REPO_TAGS_MAX:
        _REPO_TAGS.pop(next(iter(_REPO_TAGS)))  # ältester Eintrag (Einfügereihenfolge)
    _REPO_TAGS[schluessel] = (tags, time.monotonic() + REPO_TAGS_SEKUNDEN)
    return tags


def passendes_tag(stand: str, vorhanden: list[str]) -> str:
    """Das Tag für ``stand``, das es im Repo schon gibt (in der Reihenfolge von
    ``STATUS_TAG_WAHL``) — sonst das erste der Liste (wird beim Setzen angelegt).
    Ein vorhandenes Tag behält seine Schreibweise."""
    je_name = {t.strip().lower(): t for t in vorhanden or []}
    for kandidat in STATUS_TAG_WAHL[stand]:
        if kandidat in je_name:
            return je_name[kandidat]
    return STATUS_TAG_WAHL[stand][0]


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
    """Ist eine fremde Übernahme liegen geblieben — also abgelaufen?

    Maßgeblich ist ``extern_bis`` (vom Abgleich: ``bis`` der Kopfzeile, verlängert
    durch Lebenszeichen, mit der Dauer des Projekts). Ohne ihn (vor dem ersten
    Abgleich mit dieser Logik) die letzte Regung plus ``EXTERN_STILL_AB``.
    """
    if not karte.extern_bearbeiter or karte.task_id or karte.spalte != "in_arbeit":
        return False
    jetzt = jetzt or datetime.now(timezone.utc)
    bis = _aware(getattr(karte, "extern_bis", None))
    if bis is not None:
        return jetzt > bis
    letzte = extern_regung(karte)
    return letzte is not None and jetzt - letzte > EXTERN_STILL_AB


def extern_ablauf(karte, dauer: timedelta = EXTERN_STILL_AB) -> datetime | None:
    """Bis wann die fremde Übernahme an der Karte gilt (für ``extern_bis``)."""
    if not karte.extern_bearbeiter or karte.task_id:
        return None
    u = uebernahme.Uebernahme.aus_dict(karte.uebernahme)
    regung = extern_regung(karte)
    if u is not None:
        return u.ablauf(dauer, regung)
    return regung + dauer if regung else None


def extern_nachziehen(karte, labels: list[str], zustaendige: list[str],
                      seit: datetime | None = None, jetzt: datetime | None = None,
                      labels_vorher: list[str] | None = None) -> bool:
    """Stand einer Issue-Karte an das anpassen, was AUSSERHALB des Boards passiert.

    Nimmt ein anderer Agent (eine andere AI-Employee-Anlage, Claude Code) oder ein
    Mensch ein Ticket, sieht man das im Repo an Zuständigen oder an Status-Tags.
    Vorher blieb die Karte in der Inbox — man sah nicht, dass jemand dran ist, und
    der Takt hätte sie einem eigenen Agenten noch einmal gegeben.

    WER dran ist, steht in der Übernahme-Kopfzeile (``core/uebernahme``), die der
    Abgleich vorher in ``karte.uebernahme`` gelesen hat: ein Agent dieser Anlage,
    Claude Code, eine andere Anlage, ein Mensch. Ohne Kopfzeile bleiben die
    Zuständigen („@login“, Art ``mensch``) bzw. „extern“.

    Die Spalte folgt den Tags mit diesem Vorrang (``_STAND_VORRANG``): Review →
    Triage (Rückfrage offen) → In Arbeit → Bereit; ohne Tag bleibt eine neue
    Karte in der Inbox. Verschwindet das Triage-Tag ohne Bereit-Tag, geht die
    Karte zurück in die Inbox — aber nur, wenn das Board das Triage-Tag vorher
    gesehen hat (``labels_vorher``): Eine Rückfrage, die gerade erst gestellt
    wurde, steht vielleicht noch nicht im Repo.

    Eine Karte mit eigenem Lauf (``task_id``) wird nicht verschoben, sie bekommt
    nur den Namen dazu: zwei arbeiten am selben Ticket, das soll man sehen.
    Ausnahme: eine Karte in Triage — die gibt ein Mensch im Repo frei.
    Gibt zurück, ob sich etwas geändert hat.
    """
    stand = _status_aus_tags(labels)
    # Ein In-Arbeit-Tag zählt auch neben einem Triage-Tag: Wer dran ist, ist dran.
    extern_dran = bool(zustaendige) or bool(tags_der_art(labels, "in_arbeit"))
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
    if karte.spalte in ABGESCHLOSSEN or (karte.task_id and karte.spalte != "triage"):
        return vorher != (karte.spalte, karte.extern_bearbeiter, karte.bearbeiter_art)
    if stand == "pruefung" and karte.spalte in ("eingang", "bereit", "in_arbeit", "triage"):
        karte.spalte = "pruefung"
    elif stand == "triage" and karte.spalte in ("eingang", "bereit", "in_arbeit"):
        karte.spalte = "triage"
    elif karte.spalte == "triage" and stand != "triage":
        # Die Rückfrage ist im Repo geklärt: Bereit-Tag → Bereit, sonst zurück in
        # die Inbox (oder In Arbeit, wenn jemand von außen übernommen hat).
        # Als „jemand ist dran“ zählt hier nur ein In-Arbeit-TAG — ein Zuständiger
        # ist oft nur der Mensch, der die Frage beantwortet hat.
        gesehen = bool(tags_der_art(labels_vorher or [], "triage"))
        if stand == "bereit":
            _triage_verlassen(karte, "bereit")
            _per_tag_freigegeben(karte, jetzt)
        elif tags_der_art(labels, "in_arbeit") and not karte.task_id:
            _triage_verlassen(karte, "in_arbeit")
        elif gesehen:
            _triage_verlassen(karte, "eingang")
    elif extern_dran and karte.spalte in ("eingang", "bereit"):
        karte.spalte = "in_arbeit"
    elif not extern_dran and karte.spalte == "in_arbeit":
        # Extern niemand mehr dran (Tag entfernt, Zuständigkeit gelöst): zurück.
        karte.spalte = "bereit" if stand == "bereit" else "eingang"
        if stand == "bereit":
            _per_tag_freigegeben(karte, jetzt)
    elif stand == "bereit" and karte.spalte == "eingang":
        karte.spalte = "bereit"
        _per_tag_freigegeben(karte, jetzt)
    return vorher != (karte.spalte, karte.extern_bearbeiter, karte.bearbeiter_art)


def _per_tag_freigegeben(karte, jetzt: datetime | None) -> None:
    """Audit-Spur: Die Freigabe kam über ein Tag im Repo, nicht über das Board.

    Wird an eine vorhandene Notiz angehängt (sie kann eine Antwort oder Frage
    tragen), gekürzt auf die 500 Zeichen der Spalte."""
    zeit = (jetzt or datetime.now(timezone.utc)).astimezone(projekte._zone()).strftime("%d.%m.%Y %H:%M")
    vermerk = f"Freigabe per Tag im Repo ({zeit})"
    bisher = (karte.notiz or "").strip()
    if bisher:
        vermerk = bisher[: max(0, 500 - len(vermerk) - 3)].rstrip() + " · " + vermerk
    karte.notiz = vermerk[:500]


def _triage_verlassen(karte, spalte: str) -> None:
    """Triage-Karte weiterstellen: der Lauf, der die Rückfrage gestellt hat, ist vorbei."""
    karte.spalte = spalte
    karte.task_id = karte.agent_id = karte.stufe = None


#: Login hinter einem Schlüssel, je Schlüssel eine Stunde gemerkt (Prüfsumme, nie der Schlüssel).
_KONTEN: dict[str, tuple[str | None, float]] = {}


async def schluessel_konto(anbieter, client, token: str) -> str | None:
    """Das Konto des Projekt-Schlüssels — seine Kommentare schreibt die Plattform selbst."""
    import hashlib
    import time

    if not token or not hasattr(anbieter, "eigenes_konto"):
        return None
    schluessel = hashlib.sha256(token.encode()).hexdigest()[:24]
    gemerkt = _KONTEN.get(schluessel)
    if gemerkt and gemerkt[1] > time.monotonic():
        return gemerkt[0]
    try:
        konto = await anbieter.eigenes_konto(client, token)
    except Exception as e:  # noqa: BLE001 — ohne Konto zählt nur die Rolle am Repo
        log.info("[Projekt] Konto des Schlüssels nicht lesbar: %s", e)
        konto = None
    _KONTEN[schluessel] = (konto, time.monotonic() + 3600)
    return konto


async def kommentare_holen(anbieter, client, token: str, repo: str, nummer: int) -> list[dict]:
    """Kommentare eines Issues; ``vertraut`` auch für das Konto des Projekt-Schlüssels.

    Der Anbieter setzt ``vertraut`` nach der Rolle am Repo (OWNER, MEMBER,
    COLLABORATOR); die Plattform schreibt mit dem Schlüssel des Projekts, und
    dessen Kommentare gelten immer. Ohne ``list_issue_comments``: keine.
    """
    if not hasattr(anbieter, "list_issue_comments"):
        return []
    kommentare = await anbieter.list_issue_comments(client, token, repo, nummer)
    konto = (await schluessel_konto(anbieter, client, token) or "").lower()
    if konto:
        for k in kommentare:
            if isinstance(k, dict) and (k.get("autor") or "").lower() == konto:
                k["vertraut"] = True
    return kommentare


def _uebernommen(labels: list[str], zustaendige: list[str]) -> bool:
    """Zeigt das Issue, dass jemand dran ist (Zuständige oder In-Arbeit-Tag)?"""
    return bool(zustaendige) or bool(tags_der_art(labels, "in_arbeit"))


#: Präfixe der Konflikt-Texte — ein neuer Befund derselben Quelle ersetzt den alten.
KONFLIKT_UEBERNAHMEN = "Zwei gültige Übernahmen"
KONFLIKT_COMMIT = "Fremder Commit"


async def _kommentare_lesen(anbieter, client, token: str, repo: str, karte, geaendert_am: datetime | None,
                             jetzt: datetime, dauer: timedelta = uebernahme.DAUER, *, uebernommen: bool = True,
                             ansprachen: list | None = None) -> bool:
    """Kommentare eines Issues lesen: Inhaber der Übernahme und neue Ansprachen (Rückkanal).

    Nur, wenn sich das Issue seit dem letzten Lesen geändert hat (ein neuer
    Kommentar ändert es). Gibt zurück, ob gelesen wurde (für die Obergrenze).
    ``uebernommen``: das Issue zeigt eine Übernahme — dann Inhaber und Konflikt
    (zwei gültige Übernahmen verschiedener Inhaber) an der Karte merken.
    ``ansprachen``: hierhin kommen neue „@ai-employee …“-Kommentare (``uebernahme.ansprachen``).
    """
    if not hasattr(anbieter, "list_issue_comments"):
        return False
    gelesen = _aware(karte.kommentare_gelesen_at)
    if gelesen is not None and geaendert_am is not None and geaendert_am <= gelesen:
        return False
    try:
        kommentare = await kommentare_holen(anbieter, client, token, repo, karte.extern_nr)
    except Exception as e:  # noqa: BLE001 — ohne Kommentare bleibt es bei Zuständigen/Tag
        log.info("[Projekt] Kommentare von #%s nicht lesbar: %s", karte.extern_nr, e)
        return True
    karte.kommentare_gelesen_at = jetzt
    if ansprachen is not None:
        ansprachen.extend((karte, a) for a in _neue_ansprachen(karte, kommentare))
    if not uebernommen:
        return True
    befund = uebernahme.auswerten(kommentare, jetzt=jetzt, dauer=dauer)
    karte.uebernahme = befund.inhaber.als_dict() if befund.inhaber else None
    if befund.konkurrent is not None:
        karte.konflikt = (f"{KONFLIKT_UEBERNAHMEN}: {einzeilig(befund.konkurrent.name, 60)} und "
                          f"{einzeilig(befund.inhaber.name, 60)} arbeiten am selben Ticket.")[:300]
    elif (karte.konflikt or "").startswith(KONFLIKT_UEBERNAHMEN):
        karte.konflikt = None
    return True


def _neue_ansprachen(karte, kommentare: list[dict]) -> list:
    """Ansprachen aus Kommentaren, die noch nicht weitergegeben wurden.

    Neu heißt: Kennung größer als die zuletzt weitergegebene (``rueckkanal_id``;
    GitHub und Forgejo vergeben aufsteigend) und jünger als die Karte — ältere
    Kommentare standen schon im Issue, bevor das Board es kannte.
    """
    seit = _aware(getattr(karte, "created_at", None))
    bisher = karte.rueckkanal_id or 0
    neu = []
    for k in kommentare or []:
        kid = k.get("id") if isinstance(k, dict) else None
        if not isinstance(kid, int) or kid <= bisher:
            continue
        erstellt = _zeit(k.get("erstellt"))
        if seit is not None and (erstellt is None or erstellt <= seit):
            continue
        a = uebernahme.ansprachen(k)
        if a is not None:
            neu.append(a)
    hoechste = max((k.get("id") for k in kommentare or [] if isinstance(k, dict) and isinstance(k.get("id"), int)),
                   default=bisher)
    karte.rueckkanal_id = max(bisher, hoechste)
    return neu


def rueckkanal_aktiv(team: Team, token: str | None) -> bool:
    """Ist der Rückkanal (``@ai-employee …`` im Issue weckt Agenten) für dieses Projekt an?

    Ausdrücklich gesetzt (``teams.rueckkanal``) gilt das. Sonst: an nur bei einem
    privaten Repo mit Schlüssel — bei öffentlichen Repos (jeder darf kommentieren)
    und ohne Schlüssel aus.
    """
    eingestellt = getattr(team, "rueckkanal", None)
    if eingestellt is not None:
        return bool(eingestellt)
    return bool(token) and getattr(team, "repo_oeffentlich", None) is False


async def _sichtbarkeit_lesen(team: Team, anbieter, client, token: str, repo: str) -> None:
    """Ob das Repo öffentlich ist — einmal je Abgleich (für den Standard des Rückkanals)."""
    if not hasattr(anbieter, "repo_oeffentlich"):
        return
    try:
        oeffentlich = await anbieter.repo_oeffentlich(client, token, repo)
    except Exception as e:  # noqa: BLE001 — unbekannt lassen; ohne Wissen bleibt der Rückkanal aus
        log.info("[Projekt] Sichtbarkeit von %s nicht lesbar: %s", repo, e)
        return
    if oeffentlich is not None:
        team.repo_oeffentlich = oeffentlich


#: Höchstens so viele geschlossene Issues je Abgleich, deren Abschlussgrund gelesen
#: wird (eine Abfrage je Issue, nur einmal: danach ist die Karte abgeschlossen).
MAX_ABSCHLUSS_ABFRAGEN = 20
#: Abschlussgründe (GitHub ``state_reason``), bei denen die Karte verworfen ist.
#: Forgejo/Gitea kennen keinen Grund — dort ist ein geschlossenes Issue erledigt.
VERWORFEN_GRUENDE = ("not_planned", "duplicate")
NOTIZ_GESCHLOSSEN = "Issue geschlossen"
NOTIZ_VERWORFEN = "Issue geschlossen: wird nicht umgesetzt"


async def _abschluss(anbieter, client, token: str, repo: str, nr: int) -> tuple[str | None, str | None]:
    """(Zustand, Abschlussgrund) eines Issues, das nicht mehr in der offenen Liste steht.

    Erst das ganze Issue (mit ``state_reason``), sonst nur der Zustand. Ist beides
    nicht lesbar: (None, None) — die Karte bleibt offen, der nächste Abgleich fragt
    erneut.
    """
    try:
        issue = await anbieter.get_issue(client, token, repo, nr) if hasattr(anbieter, "get_issue") else None
        if isinstance(issue, dict) and issue.get("state"):
            grund = issue.get("state_reason")
            return issue.get("state"), (grund if isinstance(grund, str) else None)
        return await anbieter.issue_state(client, token, repo, nr), None
    except Exception as e:  # noqa: BLE001 — nächster Abgleich versucht es wieder
        log.info("[Projekt] Abschluss von #%s nicht lesbar: %s", nr, e)
        return None, None


#: Höchstens so viele Ansprachen aus Issues je Abgleich und Projekt.
MAX_ANSPRACHEN = 10


async def _rueckkanal(db: AsyncSession, team: Team, ansprachen: list) -> list:
    """Ansprachen aus Issues ins Projektgespräch — als Nachricht von „extern (…)“ an den Agenten.

    Ohne Namen bzw. mit unbekanntem Namen geht sie an den Lead (oder das einzige
    Mitglied). Der Text ist fremder Text: entschärft gespeichert, im Weck-Auftrag
    gerahmt (``projekt_gespraech.gespraechs_auftrag``). Gibt die neuen Nachrichten
    zurück; geweckt wird nach dem Commit (``projekt_abgleichen``) mit den
    Weck-Grenzen des Gesprächs.
    """
    from app.core import projekt_gespraech

    if not ansprachen:
        return []
    namen = await projekt_gespraech.mitglieder_namen(db, team)
    je_name = {n.strip().lower(): aid for aid, n in namen.items()}
    standard = team.lead_agent_id or (team.member_agent_ids[0] if len(team.member_agent_ids or []) == 1 else None)
    nachrichten = []
    for karte, a in ansprachen[:MAX_ANSPRACHEN]:
        ziel = je_name.get((a.agent or "").strip().lower())
        hinweis = ""
        if a.agent and ziel is None:
            hinweis = f"(angesprochen: „{a.agent}“ — kein Agent dieses Namens im Projekt, darum an den Lead) "
        ziel = ziel or standard
        text = f"Zu Issue #{karte.extern_nr}{f' ({karte.url})' if karte.url else ''}: {hinweis}{a.text}"
        nachrichten.append(await projekt_gespraech.posten(
            db, team, autor_typ="extern", autor_id=None, autor_name=f"extern ({einzeilig(a.von, 60)})", text=text,
            art="frage",
            an=[ziel] if ziel else [], karte_id=karte.id))
    if len(ansprachen) > MAX_ANSPRACHEN:
        log.info("[Projekt] %s: %s Ansprachen aus Issues übersprungen (Grenze)", team.id,
                 len(ansprachen) - MAX_ANSPRACHEN)
    return nachrichten


async def _eigene_verlaengern(db: AsyncSession, team: Team, anbieter, client, token: str, repo: str,
                              karten: list, dauer: timedelta, jetzt: datetime) -> None:
    """Übernahmen eigener Agenten verlängern, solange ihr Lauf läuft — im Server, nicht im Agenten.

    Ist die Hälfte der Dauer um, schreibt das Board die Kopfzeile neu (``bis``
    rückt vor, ``seit`` bleibt). Höchstens zehn je Abgleich; ohne Schlüssel nicht.
    Geändert werden nur Kommentare, die die Plattform selbst geschrieben hat: Autor
    ist das Konto des Projekt-Schlüssels UND die Kopfzeile trägt diese Anlage —
    sonst überschriebe das Board den Kommentar eines anderen.
    """
    from app.core.projekte import AKTIV
    from app.models.agent import Agent
    from app.models.task import Task

    if not token or not hasattr(anbieter, "kommentar_aendern"):
        return
    konto = (await schluessel_konto(anbieter, client, token) or "").lower()
    if not konto:
        return
    kandidaten = []
    for karte in karten:
        u = uebernahme.Uebernahme.aus_dict(karte.uebernahme)
        if (karte.quelle != "issue" or not karte.task_id or u is None or not u.kopfzeile or not u.kommentar_id
                or u.art != uebernahme.PLATTFORM or u.anlage != uebernahme.anlage_kennung()
                or (u.autor or "").lower() != konto
                or not u.gehoert(agent_id=karte.agent_id, lauf=karte.task_id)):
            continue
        if u.bis is not None and u.bis - jetzt > dauer / 2:
            continue
        kandidaten.append((karte, u))
    if not kandidaten:
        return
    aktiv = set((await db.execute(select(Task.id).where(
        Task.id.in_([k.task_id for k, _ in kandidaten]), Task.status.in_(AKTIV)))).scalars().all())
    for karte, u in [(k, u) for k, u in kandidaten if k.task_id in aktiv][:10]:
        name = await db.scalar(select(Agent.name).where(Agent.id == karte.agent_id)) if karte.agent_id else None
        text = uebernahme.uebernahme_kommentar(agent_name=name or u.agent, agent_id=u.agent_id, lauf=u.lauf,
                                               nr=karte.extern_nr, projekt_name=team.name, jetzt=jetzt,
                                               dauer=dauer, seit=u.seit)
        try:
            if await anbieter.kommentar_aendern(client, token, repo, u.kommentar_id, text):
                u.bis, u.geaendert = jetzt + dauer, jetzt
                karte.uebernahme = u.als_dict()
        except Exception as e:  # noqa: BLE001 — Verlängern ist Beiwerk; der nächste Abgleich versucht es wieder
            log.info("[Projekt] Übernahme #%s nicht verlängert: %s", karte.extern_nr, e)


#: Höchstens eine Konflikt-Meldung je Issue in diesem Zeitraum — auch wenn die
#: Beteiligten wechseln (sonst ließe sich das Gespräch mit wechselnden Kopfzeilen fluten).
KONFLIKT_PAUSE = timedelta(hours=24)


async def konflikte_melden(db: AsyncSession, team: Team, karten: list, jetzt: datetime | None = None) -> int:
    """Neue Konflikte ins Projektgespräch und an den Lead — höchstens einer je Issue und Tag.

    Gibt die Zahl der neu gemeldeten zurück. Der Aufrufer committet.
    """
    from sqlalchemy import func

    from app.core import projekt_gespraech
    from app.models.notification import Notification
    from app.models.projekt_nachricht import ProjektNachricht

    jetzt = jetzt or datetime.now(timezone.utc)
    kandidaten = [k for k in karten if k.konflikt and k.konflikt != k.konflikt_gemeldet]
    neu = []
    for karte in kandidaten:
        schon = await db.scalar(select(func.count(ProjektNachricht.id)).where(
            ProjektNachricht.team_id == team.id, ProjektNachricht.karte_id == karte.id,
            ProjektNachricht.autor_typ == "system", ProjektNachricht.text.startswith("Konflikt an #"),
            ProjektNachricht.created_at > jetzt - KONFLIKT_PAUSE))
        karte.konflikt_gemeldet = karte.konflikt
        if not schon:
            neu.append(karte)
    for karte in neu[:10]:
        text = (f"Konflikt an #{karte.extern_nr}: {karte.konflikt} Bitte im Issue klären, wer weitermacht — "
                "die anderen hören auf und übergeben ihren Stand.")
        await projekt_gespraech.posten(db, team, autor_typ="system", autor_id=None, autor_name="Projekt",
                                       text=text, art="nachricht", karte_id=karte.id)
        if team.lead_agent_id:
            db.add(Notification(agent_id=team.lead_agent_id, type="warning", priority="high",
                                title=f"Projekt „{team.name}“: Konflikt an #{karte.extern_nr}", message=text,
                                action_url=f"/projekte/{team.id}"))
        karte.konflikt_gemeldet = karte.konflikt
    return len(neu[:10])


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
    dauer = uebernahme.dauer_fuer(team)
    await uebernahme.anlage_laden(db)
    vorhanden = {
        (k.quelle, k.extern_nr): k
        for k in (await db.execute(
            select(ProjektKarte).where(ProjektKarte.team_id == team.id, ProjektKarte.quelle.in_(("issue", "pr")))
        )).scalars()
    }
    neu = aktualisiert = erledigt = 0
    kommentar_abfragen = 0
    vorheriger_abgleich = _aware(team.repo_abgleich_at)
    ansprachen: list = []
    await _sichtbarkeit_lesen(team, anbieter, client, token, repo)
    rueckkanal_an = rueckkanal_aktiv(team, token)

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
        labels_vorher = list(karte.labels or []) if karte is not None else []
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
            if karte.spalte in ABGESCHLOSSEN and karte.notiz in (NOTIZ_GESCHLOSSEN, NOTIZ_VERWORFEN):
                # Wieder geöffnet: zurück in die Inbox.
                karte.spalte, karte.erledigt_at, karte.notiz = "eingang", None, "Issue wieder geöffnet"
        geaendert_am = _zeit(issue.get("updated_at"))
        uebernommen = _uebernommen(_labels(issue), _zustaendige(issue))
        # Rückkanal: nur Issues, die sich seit dem letzten Abgleich geändert haben und
        # Kommentare tragen — nicht beim ersten Abgleich und nicht bei neuen Karten
        # (deren Kommentare standen schon vor dem Board im Issue).
        rueckkanal = (rueckkanal_an and karte.created_at is not None and vorheriger_abgleich is not None
                      and geaendert_am is not None and geaendert_am > vorheriger_abgleich
                      and issue.get("comments") != 0)
        if (uebernommen or rueckkanal) and kommentar_abfragen < MAX_KOMMENTAR_ABFRAGEN:
            if await _kommentare_lesen(anbieter, client, token, repo, karte, geaendert_am, jetzt, dauer,
                                       uebernommen=uebernommen,
                                       ansprachen=ansprachen if karte.created_at is not None else None):
                kommentar_abfragen += 1
        if extern_nachziehen(karte, _labels(issue), _zustaendige(issue), seit=geaendert_am, jetzt=jetzt,
                             labels_vorher=labels_vorher) \
                and karte.extern_bearbeiter is not None:
            aktualisiert += 1
        if karte.extern_bearbeiter and geaendert_am and (
                karte.extern_regung_at is None or geaendert_am > _aware(karte.extern_regung_at)):
            karte.extern_regung_at = geaendert_am

    abschluss_abfragen = 0
    for (quelle, nr), karte in vorhanden.items():
        if quelle != "issue" or nr in offen_issues or karte.spalte in ABGESCHLOSSEN:
            continue
        # Nicht mehr in der Liste: wirklich geschlossen — und warum? (Die Liste ist
        # bei MAX_ISSUES gekappt; „not planned“ heißt verworfen, nicht erledigt.)
        # Über der Grenze oder nicht lesbar: Karte bleibt offen, der nächste Abgleich
        # fragt erneut — sonst wäre ein „not planned“ für immer „erledigt“.
        if abschluss_abfragen >= MAX_ABSCHLUSS_ABFRAGEN:
            continue
        abschluss_abfragen += 1
        zustand, grund = await _abschluss(anbieter, client, token, repo, nr)
        if zustand != "closed":
            continue
        if karte.spalte == "in_arbeit":
            karte.notiz = "Issue im Repo geschlossen — der Lauf läuft noch."
            continue
        if grund in VERWORFEN_GRUENDE:
            karte.spalte, karte.erledigt_at, karte.notiz = "verworfen", jetzt, NOTIZ_VERWORFEN
        else:
            karte.spalte, karte.erledigt_at, karte.notiz = "erledigt", jetzt, NOTIZ_GESCHLOSSEN
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
                if karte.spalte in ("eingang", "bereit", "triage") or (
                        karte.spalte == "in_arbeit" and not karte.task_id):
                    # Ein offener PR schließt das Issue: es wartet auf Review und Merge
                    # (Vorrang vor Triage, In Arbeit und Bereit).
                    karte.spalte = "pruefung"
                board_tags = eigene_tags(karte.labels, "in_arbeit")
                if karte.task_id and not karte.extern_bearbeiter and board_tags and token:
                    # Eigener Agent hat den PR gestellt: das Ticket ist nicht mehr „in Arbeit“,
                    # sondern wartet auf Review — wie es die Übernahmeregeln verlangen. Nur
                    # das eigene Tag, und nie, wenn zugleich jemand von außen übernommen hat.
                    for tag in board_tags:
                        try:
                            if await anbieter.remove_label(client, token, repo, karte.extern_nr, tag):
                                karte.labels = [x for x in karte.labels if x != tag]
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
            if karte.quelle == "pr" and karte.spalte not in ABGESCHLOSSEN:
                karte.spalte, karte.erledigt_at = "erledigt", jetzt
                erledigt += 1

    alle = list(vorhanden.values())
    await _extern_branches(anbieter, client, token, repo, alle)
    # Ablauf fremder Übernahmen (Kopfzeile, Lebenszeichen, Dauer des Projekts) und
    # die Verlängerung eigener, solange ihr Lauf läuft.
    for karte in alle:
        karte.extern_bis = extern_ablauf(karte, dauer)
        if not karte.extern_bearbeiter and not karte.task_id and karte.konflikt:
            karte.konflikt = None  # niemand mehr dran: kein Konflikt mehr
    await _eigene_verlaengern(db, team, anbieter, client, token, repo, alle, dauer, jetzt)
    await konflikte_melden(db, team, alle)
    nachrichten = await _rueckkanal(db, team, ansprachen)
    if nachrichten:
        await db.flush()  # Kennungen fürs Wecken nach dem Commit

    team.repo_abgleich_at = jetzt
    team.repo_fehler = None
    return {"neu": neu, "aktualisiert": aktualisiert, "erledigt": erledigt,
            "offene_issues": len(issues), "offene_prs": len(pulls),
            "rueckkanal": [n.id for n in nachrichten if n.an]}


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
        sha = str(b.get("sha") or "")[:64]
        u = uebernahme.Uebernahme.aus_dict(karte.uebernahme)
        # Neuer Commit auf dem Branch einer Übernahme mit Ausweis: Zeit UND Autor
        # (passt er zum Inhaber?) in einer Abfrage — je Commit nur einmal.
        if (u is not None and sha and sha != karte.extern_sha and abfragen < MAX_BRANCH_ZEITEN
                and hasattr(anbieter, "commit_info")):
            abfragen += 1
            try:
                info = await anbieter.commit_info(client, token, repo, sha) or {}
            except Exception:  # noqa: BLE001 — Beiwerk
                info = {}
            zeit = zeit or _zeit(info.get("zeit"))
            if info:
                karte.extern_sha = sha
                if not uebernahme.commit_passt(u, info.get("autor_mail")):
                    autor = einzeilig(str(info.get("autor_name") or info.get("autor_mail") or "?"), 60)
                    karte.konflikt = (f"{KONFLIKT_COMMIT}: {sha[:7]} auf {karte.extern_branch} ist von {autor}, "
                                      f"übernommen hat aber {einzeilig(u.name, 60)}.")[:300]
                elif (karte.konflikt or "").startswith(KONFLIKT_COMMIT):
                    karte.konflikt = None
        if zeit is None and sha and abfragen < MAX_BRANCH_ZEITEN and hasattr(anbieter, "commit_zeit"):
            abfragen += 1
            try:
                zeit = _zeit(await anbieter.commit_zeit(client, token, repo, sha))
            except Exception:  # noqa: BLE001
                zeit = None
        if zeit and (karte.extern_regung_at is None or zeit > _aware(karte.extern_regung_at)):
            karte.extern_regung_at = zeit


async def _rueckkanal_wecken(db: AsyncSession, redis, docker, team_id: str, nachricht_ids: list[int]) -> None:
    """Agenten wecken, die aus einem Issue angesprochen wurden — nach dem Commit, mit den
    Weck-Grenzen des Gesprächs (``projekt_gespraech.wecken``)."""
    if not nachricht_ids or redis is None:
        return
    from app.core import projekt_gespraech
    from app.core.load_balancer import LoadBalancer
    from app.core.task_router import TaskRouter
    from app.models.projekt_nachricht import ProjektNachricht

    try:
        if docker is None:
            # Ohne Docker startet der Router keinen gestoppten Agenten — die Ansprache
            # bliebe liegen. Den Dienst legt man an wie an anderen Stellen auch.
            from app.services.docker_service import DockerService

            try:
                docker = DockerService()
            except Exception:  # noqa: BLE001 — ohne Docker (Tests, Entwicklung): Lauf wartet in der Schlange
                docker = None
        router = TaskRouter(db, redis, LoadBalancer(redis), docker_service=docker)
        team = await db.get(Team, team_id)
        for nid in nachricht_ids:
            nachricht = await db.get(ProjektNachricht, nid)
            if team is not None and nachricht is not None:
                await projekt_gespraech.wecken(db, router, team, nachricht)
        await db.commit()
    except Exception as e:  # noqa: BLE001 — die Nachricht steht im Gespräch, nur das Wecken fehlt
        log.warning("[Projekt] %s: Ansprache aus dem Issue nicht geweckt: %s", team_id, e)


async def projekt_abgleichen(db: AsyncSession, redis, team: Team, docker=None) -> dict:
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
        await _rueckkanal_wecken(db, redis, docker, team_id, ergebnis.get("rueckkanal") or [])
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


async def beim_oeffnen(db: AsyncSession, redis, team: Team, jetzt: datetime | None = None,
                      docker=None) -> dict | None:
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
    return await projekt_abgleichen(db, redis, team, docker)


async def alle_abgleichen(redis, docker=None) -> int:
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
                await projekt_abgleichen(db, redis, team, docker)
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


async def uebernahme_live(db: AsyncSession, redis, team: Team,
                          karte: ProjektKarte) -> tuple[str | None, uebernahme.Uebernahme | None]:
    """Arbeitet schon jemand außerhalb des Boards an diesem Issue? Live im Repo gefragt.

    Der Abgleich läuft höchstens alle paar Minuten — in der Zwischenzeit hätte das
    Board ein Ticket vergeben, das gerade jemand anderes übernommen hat. Liefert
    (Name, Übernahme): den Namen aus der Übernahme-Kopfzeile, sonst ``@login`` oder
    „extern“, oder (None, None). Ist das Repo nicht erreichbar, entscheidet der
    letzte Abgleich.
    """
    if karte.quelle != "issue" or not karte.extern_nr or not team.repo:
        return None, None
    # Ein offener PR zum Ticket (aus dem letzten Abgleich) heißt: jemand ist dran.
    if karte.pr_nr and karte.pr_zustand not in ("gemergt", "geschlossen") and not karte.task_id:
        return f"PR #{karte.pr_nr}", None
    zuletzt = uebernahme.Uebernahme.aus_dict(karte.uebernahme)
    await uebernahme.anlage_laden(db)
    try:
        token, anbieter = await zugang(db, redis, team)
        repo = repo_ziel(team.repo).pfad
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0), follow_redirects=False) as client:
            issue = await anbieter.get_issue(client, token, repo, karte.extern_nr)
            if not isinstance(issue, dict):
                return karte.extern_bearbeiter, zuletzt
            if not _uebernommen(_labels(issue), _zustaendige(issue)):
                return None, None
            kommentare = await kommentare_holen(anbieter, client, token, repo, karte.extern_nr)
    except Exception as e:  # noqa: BLE001 — Repo nicht erreichbar: nicht blockieren
        log.info("[Projekt] Übernahme-Prüfung #%s übersprungen: %s", karte.extern_nr, e)
        return karte.extern_bearbeiter, zuletzt
    u = uebernahme.inhaber(kommentare)
    if u is not None:
        return u.name, u
    leute = _zustaendige(issue)
    return (", ".join(f"@{z}" for z in leute) if leute else "extern"), None


async def extern_dran(db: AsyncSession, redis, team: Team, karte: ProjektKarte) -> str | None:
    """Nur der Name aus ``uebernahme_live``."""
    return (await uebernahme_live(db, redis, team, karte))[0]


def abloesbar(team: Team, karte: ProjektKarte, u: uebernahme.Uebernahme | None,
              jetzt: datetime | None = None) -> bool:
    """Darf ein Plattform-Agent diese fremde Übernahme selbst übernehmen?

    Nur, wenn das Projekt es ausdrücklich erlaubt (``abgelaufene_uebernehmen``,
    Standard aus), die Übernahme einen Ausweis bzw. eine Zeit hat und seit ihrem
    Ablauf die Karenzzeit (``uebernahme.KARENZ``) vergangen ist. Ohne Erlaubnis
    bleibt es beim Knopf „Freigeben“ für Menschen.
    """
    if not getattr(team, "abgelaufene_uebernehmen", False) or u is None:
        return False
    ende = u.ablauf(uebernahme.dauer_fuer(team), extern_regung(karte) if karte.extern_bearbeiter else None)
    return ende is not None and (jetzt or datetime.now(timezone.utc)) > ende + uebernahme.KARENZ


async def offene_uebernahmen(db: AsyncSession, team_id: str, agent_id: str) -> int:
    """Wie viele Issues dieses Projekts hält der Agent gerade (In Arbeit)?"""
    from sqlalchemy import func

    return int(await db.scalar(select(func.count(ProjektKarte.id)).where(
        ProjektKarte.team_id == team_id, ProjektKarte.quelle == "issue", ProjektKarte.spalte == "in_arbeit",
        ProjektKarte.agent_id == agent_id)) or 0)


def erlaubte_hosts(team: Team) -> set[str]:
    """Hosts, auf die ein Kommentar der Plattform verlinken darf: der des Repos und der der Anlage."""
    from app.config import settings

    ziel = repo_ziel(team.repo or "")
    hosts = {"github.com"} if ziel.art == "github" else {urlsplit(ziel.basis or "").hostname or ""}
    try:
        hosts.add(urlsplit(getattr(settings, "public_app_url", "") or "").hostname or "")
    except ValueError:
        pass
    return {h.lower() for h in hosts if h}


async def ausgehend_pruefen(text: str, *, agent_id: str | None, kanal: str, team: Team) -> str:
    """Text, der in ein (vielleicht öffentliches) Repo geht: DLP, dann entschärfen.

    Die DLP-Regeln der Anlage (``core/dlp.evaluate_egress``) können maskieren oder
    sperren; gesperrt → GitFehler. Danach ``uebernahme.neutralisieren``.
    """
    from app.core.dlp import evaluate_egress

    urteil = await evaluate_egress(text or "", agent_id=agent_id, channel=kanal)
    if urteil.blocked:
        raise GitFehler("Der Text enthält geschützte Daten (DLP-Regel) und geht nicht ins Repo.")
    return uebernahme.neutralisieren(urteil.output, erlaubte_hosts(team))


async def uebergabe_schreiben(db: AsyncSession, redis, team: Team, karte: ProjektKarte, *, an_wer: str,
                              an_name: str, an_agent_id: str | None, von_name: str, stand: str,
                              naechster_schritt: str, agent_id: str | None = None) -> uebernahme.Uebernahme:
    """„Übergeben an …“: Kommentar mit Stand, nächstem Schritt und Kopfzeile des Empfängers.

    Die Kopfzeile trägt ``vorher`` = bisheriger Inhaber — damit endet dessen
    Übernahme (``uebernahme.auswerten``). Das Tag ``status/in-arbeit`` bleibt bzw.
    wird gesetzt. Liefert die neue Übernahme; der Aufrufer stellt die Karte um.
    """
    if karte.quelle != "issue" or not karte.extern_nr or not team.repo:
        raise GitFehler("Übergeben geht nur bei Karten aus Issues.")
    token, anbieter = await zugang(db, redis, team)
    if not token:
        raise GitFehler("Ohne Schlüssel kann das Board nichts ins Repo schreiben (Bearbeiten → Schlüssel).")
    from app.models.agent import Agent

    if karte.agent_id:
        bisher = await db.scalar(select(Agent.name).where(Agent.id == karte.agent_id))
        vorher_wer = uebernahme.wer_plattform(bisher or "Lead")
    else:
        vorher_wer = (karte.uebernahme or {}).get("wer") or "extern"
    jetzt = datetime.now(timezone.utc)
    stand = await ausgehend_pruefen(stand, agent_id=agent_id, kanal="issue_uebergabe", team=team)
    naechster_schritt = await ausgehend_pruefen(naechster_schritt, agent_id=agent_id, kanal="issue_uebergabe",
                                                team=team)
    text = uebernahme.uebergabe_kommentar(an_wer=an_wer, an_name=an_name, an_agent_id=an_agent_id,
                                          von_name=von_name, vorher_wer=vorher_wer, stand=stand,
                                          naechster_schritt=naechster_schritt, nr=karte.extern_nr, jetzt=jetzt,
                                          dauer=uebernahme.dauer_fuer(team))
    repo = repo_ziel(team.repo).pfad
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=False) as client:
        tag = passendes_tag("in_arbeit", await repo_tags(anbieter, client, token, repo))
        if not await anbieter.add_labels(client, token, repo, karte.extern_nr, [tag]):
            raise GitFehler(f"Tag {tag} ließ sich nicht setzen (Schreibrecht für Issues?).")
        # Übergeben heißt weiterarbeiten: keine Rückfrage mehr offen — sonst zöge der
        # Abgleich die Karte über das Triage-Tag gleich wieder nach Triage.
        for alt in tags_der_art(karte.labels, "triage"):
            if await anbieter.remove_label(client, token, repo, karte.extern_nr, alt):
                karte.labels = [x for x in karte.labels if x != alt]
        await anbieter.comment_issue(client, token, repo, karte.extern_nr, text)
    # Selbst geschrieben, mit dem Schlüssel des Projekts: vertraut.
    neu = uebernahme.lesen({"id": None, "body": text, "erstellt": jetzt.isoformat(), "vertraut": True})
    return neu


async def uebernahme_melden(team_id: str, karte_id: str, redis, *, agent_name: str | None,
                            uebernehmen: bool = True, agent_id: str | None = None,
                            lauf: str | None = None, vorgaenger: uebernahme.Uebernahme | None = None) -> None:
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
        await uebernahme.anlage_laden(db)
        try:
            token, anbieter = await zugang(db, redis, team)
            if not token:
                raise GitFehler("ohne Schlüssel kann das Board nichts ins Repo schreiben")
            repo = repo_ziel(team.repo).pfad
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=False) as client:
                if uebernehmen:
                    tag = passendes_tag("in_arbeit", await repo_tags(anbieter, client, token, repo))
                    ok = await anbieter.add_labels(client, token, repo, nr, [tag])
                    # Übernommen heißt: keine Rückfrage mehr offen.
                    for alt in tags_der_art(karte.labels, "triage"):
                        await anbieter.remove_label(client, token, repo, nr, alt)
                    await anbieter.comment_issue(client, token, repo, nr, uebernahme.uebernahme_kommentar(
                        agent_name=agent_name, agent_id=agent_id, lauf=lauf, nr=nr, projekt_name=team.name,
                        dauer=uebernahme.dauer_fuer(team), vorgaenger=vorgaenger))
                else:
                    tag = UEBERNAHME_TAG
                    ok = True
                    # Nur das Tag, das das Board selbst setzt — und keins, wenn zugleich
                    # jemand von außen übernommen hat (dessen Tag ist nicht unseres).
                    if not karte.extern_bearbeiter:
                        for alt in eigene_tags(karte.labels, "in_arbeit") or [UEBERNAHME_TAG]:
                            ok = await anbieter.remove_label(client, token, repo, nr, alt) and ok
                    await anbieter.comment_issue(client, token, repo, nr, uebernahme.rueckgabe_kommentar(
                        agent_name=agent_name, agent_id=agent_id, lauf=lauf))
            if not ok:
                raise GitFehler(f"Tag {tag} ließ sich nicht setzen (Schreibrecht für Issues?)")
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
            if tag_stand(tag) == "in_arbeit" and not await anbieter.remove_label(client, token, repo, nr, tag):
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
    karte.labels = [t for t in (karte.labels or []) if tag_stand(t) != "in_arbeit"]
    karte.spalte = "bereit"
    karte.notiz = (f"Fremde Übernahme gelöst — {seit.strip()} keine Bewegung." if seit
                   else "Fremde Übernahme gelöst.")


#: Spalten, die ``status_ins_repo`` ins Issue schreibt.
REPO_SPALTEN = ("triage", "bereit", "eingang", "verworfen")


async def status_ins_repo(db: AsyncSession, redis, team: Team, karte: ProjektKarte, stand: str, *,
                          agent_name: str | None = None, agent_id: str | None = None,
                          lauf: str | None = None, zugang_daten: tuple | None = None) -> list[str]:
    """Die neue Spalte einer Issue-Karte als Tag ins Issue — der Abgleich liest sie von dort.

    * ``triage`` (Rückfrage): Triage-Tag setzen. Nur mit Halter (``agent_id``: der
      Agent seiner eigenen laufenden Karte) kommen dessen In-Arbeit- und Bereit-Tags
      weg (nur die, die das Board selbst setzt) und ein Kommentar mit
      Rückgabe-Kopfzeile gibt die Übernahme frei. Ohne Halter fasst es keine
      anderen Tags an.
    * ``bereit`` (ein Mensch gibt frei): Bereit-Tag setzen, alle Triage-Tags weg.
    * ``eingang``/``verworfen`` (ein Mensch nimmt Bereit oder Triage zurück):
      Bereit- und Triage-Tags weg — sonst holte der Abgleich die Karte zurück.

    Triage-Tags entfernt das Board alle, die es als Triage liest — auch fremde wie
    ``claude:needs-human``: Ein verbliebenes zöge die Karte sonst gleich wieder
    nach Triage. Gesetzt wird das Tag, das es im Repo schon gibt (``passendes_tag``).
    Liefert die Tags des Issues danach (``karte.labels`` bleibt unverändert — der
    Aufrufer übernimmt sie unter seiner Sperre); GitFehler, wenn das Repo nicht
    erreichbar oder nicht beschreibbar ist. ``zugang_daten``: (Token, Anbieter),
    schon vorher bestimmt — dann fragt die Funktion die Datenbank nicht mehr und ist
    reiner Netzwerkteil (für eine Frist mit ``asyncio.wait_for``).
    """
    if stand not in REPO_SPALTEN:
        raise ValueError(stand)
    if karte.quelle != "issue" or not karte.extern_nr or not team.repo:
        return list(karte.labels or [])
    token, anbieter = zugang_daten if zugang_daten is not None else await zugang(db, redis, team)
    if not token:
        raise GitFehler("Ohne Schlüssel kann das Board nichts ins Repo schreiben (Bearbeiten → Schlüssel).")
    repo, nr = repo_ziel(team.repo).pfad, karte.extern_nr
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=False) as client:
        issue = await anbieter.get_issue(client, token, repo, nr) if hasattr(anbieter, "get_issue") else None
        aktuell = _labels(issue) if isinstance(issue, dict) else list(karte.labels or [])
        tag = None
        if stand in ("triage", "bereit"):
            tag = passendes_tag(stand, await repo_tags(anbieter, client, token, repo))
        if stand == "triage":
            # Nur mit Halter — und nie, wenn zugleich jemand von außen übernommen hat.
            weg = ((eigene_tags(aktuell, "in_arbeit") + eigene_tags(aktuell, "bereit"))
                   if agent_id and not karte.extern_bearbeiter else [])
        elif stand == "bereit":
            weg = tags_der_art(aktuell, "triage")
        else:
            weg = tags_der_art(aktuell, "bereit") + tags_der_art(aktuell, "triage")
        if tag and tag.lower() not in {t.lower() for t in aktuell}:
            if not await anbieter.add_labels(client, token, repo, nr, [tag]):
                raise GitFehler(f"Tag {tag} ließ sich nicht setzen (Schreibrecht für Issues?).")
            aktuell.append(tag)
        for alt in [t for t in weg if not tag or t.lower() != tag.lower()]:
            if not await anbieter.remove_label(client, token, repo, nr, alt):
                raise GitFehler(f"Tag {alt} ließ sich nicht entfernen (Schreibrecht für Issues?).")
            aktuell.remove(alt)
        if stand == "triage" and agent_id:
            bereit_tag = passendes_tag("bereit", await repo_tags(anbieter, client, token, repo))
            await anbieter.comment_issue(client, token, repo, nr, uebernahme.rueckfrage_kommentar(
                agent_name=agent_name, agent_id=agent_id, lauf=lauf, triage_tag=tag, bereit_tag=bereit_tag))
    return aktuell[:20]


#: Höchstens so viele Issue-Kommentare je Agent und Stunde — atomar in Redis gezählt,
#: VOR dem Schreiben.
ISSUE_KOMMENTARE_JE_STUNDE = 10


class ZuVieleKommentare(GitFehler):
    """Die Grenze der Issue-Kommentare je Stunde ist erreicht."""


async def _kommentar_platz(redis, agent_id: str) -> bool:
    """Einen Platz im Stundenkontingent nehmen — atomar (INCR), vor dem Schreiben.

    Ohne Redis (Tests, Ausfall) wird nicht gezählt — dann schützt nur der Takt der
    Läufe; ein Fehler im Zähler soll keinen Kommentar verhindern.
    """
    import time

    client = _redis_client(redis)
    if client is None:
        return True
    try:
        schluessel = f"issue_kommentar:{agent_id}:{int(time.time() // 3600)}"
        anzahl = await client.incr(schluessel)
        if anzahl == 1:
            await client.expire(schluessel, 3700)
        return anzahl <= ISSUE_KOMMENTARE_JE_STUNDE
    except Exception:  # noqa: BLE001
        return True


async def issue_kommentieren(db: AsyncSession, redis, team: Team, karte: ProjektKarte, *, agent_name: str,
                             agent_id: str, text: str) -> str:
    """Rückfrage eines Plattform-Agenten an Externe als Kommentar ins Issue (Rückkanal Agent → Issue).

    Die Kopfzeile ``ai-employee:nachricht`` weist ihn als Kommentar dieser Anlage
    aus; der Rückkanal liest ihn deshalb nicht als Ansprache.
    """
    if karte.quelle != "issue" or not karte.extern_nr or not team.repo:
        raise GitFehler("Ins Issue schreiben geht nur bei Karten aus Issues.")
    token, anbieter = await zugang(db, redis, team)
    if not token:
        raise GitFehler("Ohne Schlüssel kann das Board nichts ins Repo schreiben (Bearbeiten → Schlüssel).")
    await uebernahme.anlage_laden(db)
    # DLP und Entschärfen vor dem Zählen: ein gesperrter Text kostet keinen Platz.
    sauber = await ausgehend_pruefen(text, agent_id=agent_id, kanal="issue_kommentar", team=team)
    if not await _kommentar_platz(redis, agent_id):
        raise ZuVieleKommentare(f"Höchstens {ISSUE_KOMMENTARE_JE_STUNDE} Issue-Kommentare je Stunde — bitte "
                                "später weiter.")
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=False) as client:
        await anbieter.comment_issue(client, token, repo_ziel(team.repo).pfad, karte.extern_nr,
                                     uebernahme.nachricht_kommentar(agent_name=agent_name, agent_id=agent_id,
                                                                    text=sauber))
    return sauber


def uebernahme_im_hintergrund(team_id: str, karte_id: str, redis, *, agent_name: str | None,
                              uebernehmen: bool = True, agent_id: str | None = None,
                              lauf: str | None = None, vorgaenger: uebernahme.Uebernahme | None = None) -> None:
    """``uebernahme_melden`` ohne den Aufrufer warten zu lassen."""
    try:
        asyncio.get_running_loop().create_task(
            uebernahme_melden(team_id, karte_id, redis, agent_name=agent_name, uebernehmen=uebernehmen,
                              agent_id=agent_id, lauf=lauf, vorgaenger=vorgaenger))
    except RuntimeError:
        pass


# ── Push-Sperre: kein Push in den Branch einer fremden Übernahme ─────────────

#: Was der Hook einem Lauf des Arbeitstakts sagt, der pushen will.
TAKT_PUSH_GRUND = (
    "Push gesperrt: Dies ist ein Lauf des Arbeitstakts. Im Takt koordinierst du nur — Code entsteht in "
    "Karten-Läufen. Vergib die Karte mit `projekt_karte_vergeben` (bist du allein im Projekt, an dich selbst) "
    "und arbeite im Lauf der Karte weiter.")


#: Was ein Lauf aus dem Rückkanal (Frage aus einem Issue) nicht darf.
RUECKKANAL_GRUND = (
    "Gesperrt: Dieser Lauf beantwortet eine Frage aus einem Issue. Darin darfst du nur antworten "
    "(`projekt_issue_kommentar`, Projektgespräch) und Karten in der Inbox anlegen — nichts vergeben oder "
    "übergeben, keinen Code pushen, keine Mails senden.")


async def lauf_art(db: AsyncSession, agent_id: str, lauf: str | None) -> str | None:
    """Ist dieser Lauf beschränkt? "takt" (Arbeitstakt), "rueckkanal" (Frage aus einem Issue) oder None.

    Ist der Lauf unbekannt (ältere Hook-Einstellung, Chat) und laufen gerade NUR
    beschränkte Läufe dieses Agenten derselben Art, gehört der Aufruf zu einem davon.
    """
    from app.core import projekt_takt
    from app.models.task import Task, TaskStatus

    takt_plaene = set((await db.execute(
        select(Team.takt_schedule_id).where(Team.takt_schedule_id.is_not(None)))).scalars().all())

    def art_von(t) -> str | None:
        if (t.metadata_ or {}).get("rueckkanal"):
            return "rueckkanal"
        return "takt" if projekt_takt.ist_takt_lauf(t, takt_plaene) else None

    if lauf:
        task = await db.get(Task, lauf)
        return art_von(task) if task is not None and task.agent_id == agent_id else None
    laufend = (await db.execute(select(Task).where(
        Task.agent_id == agent_id, Task.status == TaskStatus.RUNNING).limit(10))).scalars().all()
    arten = {art_von(t) for t in laufend}
    return arten.pop() if len(arten) == 1 else None


#: Werkzeuge, die ein Lauf aus dem Rückkanal nicht benutzen darf (bloßer Name, ohne
#: ``mcp__<server>__``). ``projekt_karte_anlegen`` nur mit Spalte „eingang“.
RUECKKANAL_GESPERRT = frozenset({
    "projekt_karte_vergeben", "projekt_karte_uebergeben", "projekt_karte_verschieben",
    "create_task", "create_task_batch", "delegate_and_wait",
    "email_send", "email_reply", "send_email", "reply_email", "ms_send_email",
    "send_teams_message", "send_teams_chat_message",
})


def werkzeug_grund(art: str | None, werkzeug: str, eingabe: dict | None) -> str | None:
    """Sperrgrund für ein Werkzeug in einem beschränkten Lauf — None, wenn erlaubt."""
    if art != "rueckkanal":
        return None
    name = werkzeug.rsplit("__", 1)[-1] if werkzeug.startswith("mcp__") else werkzeug
    if name in RUECKKANAL_GESPERRT:
        return RUECKKANAL_GRUND
    if name == "projekt_karte_anlegen" and str((eingabe or {}).get("spalte") or "eingang") != "eingang":
        return RUECKKANAL_GRUND
    return None


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
    from app.models.agent import Agent

    agent = await db.get(Agent, agent_id)
    if agent is None:
        return {"erlaubt": True, "grund": None}
    # Die Grenze stoppt nur NEUE Abfragen beim Git-Server, nie die Entscheidung:
    # darüber zählt der gemerkte Stand, ohne ihn wird gesperrt. Sonst hebelte ein
    # Agent in einer Schleife die Sperre durch Hämmern aus.
    frei = await _push_fragen_frei(redis, agent_id)
    if not frei:
        log.warning("[Push-Sperre] Agent %s fragt öfter als %s-mal je Minute — nur noch gemerkte Stände",
                    agent_id, PUSH_FRAGEN_JE_MINUTE)
    await uebernahme.anlage_laden(db)
    art = await lauf_art(db, agent_id, lauf)
    if art == "takt":
        log.info("[Push-Sperre] Agent %s, Takt-Lauf %s: Push gesperrt", agent_id, lauf)
        return {"erlaubt": False, "grund": TAKT_PUSH_GRUND}
    if art == "rueckkanal":
        log.info("[Push-Sperre] Agent %s, Rückkanal-Lauf %s: Push gesperrt", agent_id, lauf)
        return {"erlaubt": False, "grund": RUECKKANAL_GRUND}

    projekte_des_agenten = await _projekte_mit_repo(db, agent_id)
    for ziel in ziele[:MAX_PUSH_ZIELE]:
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
            try:
                grund = await _fremde_uebernahme(db, redis, team, int(nummern[0]), branch, agent, lauf, frei)
            except _ZuVieleFragen:
                return {"erlaubt": False, "grund": ZU_VIELE_FRAGEN_GRUND}
            if grund:
                log.info("[Push-Sperre] Agent %s, Lauf %s, Branch %s: gesperrt", agent_id, lauf, branch)
                return {"erlaubt": False, "grund": grund}
    return {"erlaubt": True, "grund": None}


#: Höchstens so viele Ziel-Branches je Anfrage, so viele Anfragen je Agent und
#: Minute, und so lange gilt ein gelesener Stand je (Projekt, Issue).
MAX_PUSH_ZIELE = 3
PUSH_FRAGEN_JE_MINUTE = 30
PUSH_STAND_SEKUNDEN = 30


def _redis_client(redis):
    """Der aioredis-Client hinter dem RedisService — None ohne (Tests, Ausfall)."""
    return getattr(redis, "client", None)


ZU_VIELE_FRAGEN_GRUND = "Zu viele Push-Prüfungen in kurzer Zeit — in einer Minute erneut versuchen."


class _ZuVieleFragen(Exception):
    """Über der Grenze und kein gemerkter Stand: neu fragen geht nicht, also sperren."""


async def _push_fragen_frei(redis, agent_id: str) -> bool:
    """Zählt die Anfragen je Agent und Minute — darüber keine neuen Abfragen beim Git-Server.

    Ohne Grenze löste ein Agent in einer Schleife je Anfrage zwei Abfragen beim
    Git-Server aus. Ohne Redis (oder bei einer Störung) wird nicht gezählt.
    """
    import time

    client = _redis_client(redis)
    if client is None:
        return True
    try:
        schluessel = f"push_pruefen:anzahl:{agent_id}:{int(time.time() // 60)}"
        anzahl = await client.incr(schluessel)
        if anzahl == 1:
            await client.expire(schluessel, 70)
        return anzahl <= PUSH_FRAGEN_JE_MINUTE
    except Exception:  # noqa: BLE001 — Redis gestört: prüfen wie ohne
        return True


async def _projekte_mit_repo(db: AsyncSession, agent_id: str) -> list[Team]:
    """Aktive Projekte mit Repo, in denen der Agent Mitglied oder Lead ist — per Abfrage.

    ``member_agent_ids`` ist eine JSON-Liste; die Textsuche nach ``"<id>"`` grenzt
    in der Datenbank vor, die genaue Prüfung folgt hier (eine Kennung, die in einer
    anderen steckt, fällt dabei heraus).
    """
    from sqlalchemy import String, cast, or_

    kandidaten = (await db.execute(select(Team).where(
        Team.is_active.is_(True), Team.repo.is_not(None),
        or_(Team.lead_agent_id == agent_id, cast(Team.member_agent_ids, String).like(f'%"{agent_id}"%')),
    ).limit(50))).scalars().all()
    return [t for t in kandidaten
            if agent_id in set(t.member_agent_ids or []) | ({t.lead_agent_id} if t.lead_agent_id else set())]


async def _uebernahme_im_repo(db: AsyncSession, redis, team: Team, nr: int,
                              frei: bool = True) -> tuple[bool, uebernahme.Uebernahme | None]:
    """(übernommen?, Inhaber) live aus dem Repo — je (Projekt, Issue) 30 s gemerkt.

    Ein Push kommt selten allein (Wiederholung, mehrere Branches); ohne Merken
    kostete jeder zwei Abfragen beim Git-Server. Nicht erreichbar: (False, None),
    also nicht sperren.
    """
    import json

    client = _redis_client(redis)
    schluessel = f"push_pruefen:stand:{team.id}:{nr}"
    if client is not None:
        try:
            roh = await client.get(schluessel)
            if roh:
                stand = json.loads(roh)
                return bool(stand.get("uebernommen")), uebernahme.Uebernahme.aus_dict(stand.get("inhaber"))
        except Exception:  # noqa: BLE001 — Redis gestört: frisch fragen
            pass
    if not frei:
        raise _ZuVieleFragen()
    try:
        token, anbieter = await zugang(db, redis, team)
        repo = repo_ziel(team.repo).pfad
        # Knapp: der Hook wartet höchstens 15 s auf die ganze Entscheidung.
        async with httpx.AsyncClient(timeout=httpx.Timeout(4.0), follow_redirects=False) as http:
            issue = await anbieter.get_issue(http, token, repo, nr)
            if not isinstance(issue, dict):
                return False, None
            uebernommen = _uebernommen(_labels(issue), _zustaendige(issue))
            u = uebernahme.inhaber(await kommentare_holen(anbieter, http, token, repo, nr)) if uebernommen else None
    except Exception as e:  # noqa: BLE001 — nicht erreichbar: nicht sperren (siehe push_pruefen)
        log.info("[Push-Sperre] #%s in %s nicht prüfbar, Push erlaubt: %s", nr, team.id, e)
        return False, None
    if client is not None:
        try:
            await client.set(schluessel, json.dumps({"uebernommen": uebernommen,
                                                     "inhaber": u.als_dict() if u else None}),
                             ex=PUSH_STAND_SEKUNDEN)
        except Exception:  # noqa: BLE001
            pass
    return uebernommen, u


async def _fremde_uebernahme(db: AsyncSession, redis, team: Team, nr: int, branch: str, agent,
                             lauf: str | None, frei: bool = True) -> str | None:
    """Sperrgrund, wenn Issue ``nr`` im Repo des Projekts einem anderen gehört — sonst None."""
    karte = (await db.execute(select(ProjektKarte).where(
        ProjektKarte.team_id == team.id, ProjektKarte.quelle == "issue", ProjektKarte.extern_nr == nr)
    )).scalars().first()
    if karte is not None and lauf and karte.task_id == lauf:
        return None  # der Lauf, an den das Board die Karte vergeben hat
    uebernommen, u = await _uebernahme_im_repo(db, redis, team, nr, frei)
    if not uebernommen:
        return None
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
    await uebernahme.anlage_laden(db)
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
