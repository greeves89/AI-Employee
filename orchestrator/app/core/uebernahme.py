"""Übernahmen von Issues: wer arbeitet woran — maschinenlesbar im Issue selbst.

Am Repo eines Projekts arbeiten mehrere Seiten: Agenten dieser Anlage, Agenten
anderer AI-Employee-Anlagen, Claude-Code-Sitzungen außerhalb und Menschen. Oft
schreiben alle unter DEMSELBEN Git-Konto — wer ein Ticket übernommen hat, stand
bis hier nur als Freitext im Kommentar. Am 29.10. hat deshalb der Lead im Takt in
die Branches einer fremden Übernahme gepusht.

Grundsatz: Das Issue ist die einzige Wahrheit darüber, wer woran arbeitet; das
Board ist nur eine Ansicht davon. Eine Übernahme ist ein Kommentar, dessen ERSTE
Zeile eine Kopfzeile trägt::

    <!-- ai-employee:claim wer="ai-employee:agents.example.com/DEV" agent_id="…" lauf="…"
         branch="12-…" seit="2026-10-29T14:03:00+00:00" bis="2026-10-29T16:03:00+00:00" -->

darunter der Text für Menschen. Zurückgeben heißt ``ai-employee:release``.

``wer`` kennt vier Arten:

* ``ai-employee:<anlage>/<agent>`` — ein Agent einer AI-Employee-Anlage
  (``anlage_kennung``: diese Anlage oder eine andere),
* ``claude-code:<freitext>`` — eine Claude-Code-Sitzung,
* ``mensch:<login>`` — ein Mensch,
* ``extern`` — sonst jemand.

Gelesen wird robust: nur die erste Zeile eines Kommentars zählt (ein zitierter
Kommentar beginnt mit ``>`` und kann keine Übernahme vortäuschen), unbekannte
Felder fallen weg, Werte werden entschärft und gekürzt, unlesbare Zeiten sind
None. Ohne Kopfzeile erkennt ``lesen`` die bekannten Freitext-Übernahmen.

Das ist Zusammenstoß-Vermeidung unter Kollegen, keine Sicherheitsgrenze: wer
Kommentare schreiben darf, kann jede Kopfzeile schreiben.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.core.fremdtext import einzeilig, entschaerft

PLATTFORM = "plattform"
CLAUDE_CODE = "claude_code"
ANDERE_ANLAGE = "andere_anlage"
MENSCH = "mensch"
EXTERN = "extern"
ARTEN = (PLATTFORM, CLAUDE_CODE, ANDERE_ANLAGE, MENSCH, EXTERN)

#: Standard-Dauer einer Übernahme ab dem letzten Lebenszeichen; je Projekt
#: einstellbar (``teams.uebernahme_stunden``, ``dauer_fuer``).
DAUER = timedelta(hours=2)
STANDARD_STUNDEN = 2
#: Höchstens so viele offene Übernahmen je Bearbeiter (``teams.max_uebernahmen``).
STANDARD_OBERGRENZE = 2
#: So lange nach dem Ablauf einer fremden Übernahme wartet ein Plattform-Agent, bevor
#: er sie (mit ausdrücklicher Erlaubnis des Projekts) selbst übernimmt.
KARENZ = timedelta(minutes=30)


def dauer_fuer(team) -> timedelta:
    """Dauer einer Übernahme in diesem Projekt (1–24 Stunden, Standard 2)."""
    stunden = getattr(team, "uebernahme_stunden", None) or STANDARD_STUNDEN
    return timedelta(hours=min(24, max(1, int(stunden))))


def obergrenze_fuer(team) -> int:
    """Höchstens so viele offene Übernahmen je Bearbeiter in diesem Projekt (Standard 2)."""
    return min(20, max(1, int(getattr(team, "max_uebernahmen", None) or STANDARD_OBERGRENZE)))

_KOPF = re.compile(r"^<!--\s*ai-employee:(claim|release)\b(.*?)-->", re.S)
_FELD = re.compile(r'\b([a-z_]{2,12})="([^"\n]{0,400})"')
_KENNUNG = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_BRANCH = re.compile(r"^[A-Za-z0-9._/+*…-]{1,200}$")
_ANLAGE = re.compile(r"^[A-Za-z0-9.:_-]{1,100}$")
_LOGIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,38}$")

# Freitext-Übernahmen ohne Kopfzeile — so, wie sie bis hier geschrieben wurden.
_FREI_PLATTFORM = re.compile(r"übernommen\s+vom\s+ai-employee-agenten\s+[„\"»]?([^“\"«\n]{1,80})", re.I)
_FREI_UEBERNAHME = re.compile(r"^[\W_]*(?:übernommen|übernehme|ich\s+übernehme|claimed|claiming|i'?m\s+taking)\b", re.I)
_FREI_RUECKGABE = re.compile(r"^[\W_]*(?:zurückgegeben|übernahme\s+gelöst|freigegeben|released|unclaim)", re.I)
_CLAUDE_CODE = re.compile(r"claude[\s-]*code", re.I)


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


def _zeit(wert) -> datetime | None:
    if isinstance(wert, datetime):
        return wert if wert.tzinfo else wert.replace(tzinfo=timezone.utc)
    if not isinstance(wert, str) or not wert or len(wert) > 40:
        return None
    try:
        t = datetime.fromisoformat(wert.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _wert(text, hoechstens: int) -> str:
    """Wert für eine Kopfzeile: einzeilig, ohne Zeichen, die die Zeile zerlegen."""
    t = einzeilig(str(text or ""), hoechstens * 2)
    for z in ('"', "<", ">", "--"):
        t = t.replace(z, "")
    return t.strip()[:hoechstens]


def _kennung(wert) -> str | None:
    return wert if isinstance(wert, str) and _KENNUNG.match(wert) else None


#: Gespeicherte Kennung dieser Anlage (``anlage_laden``) — einmal je Prozess gelesen.
_ANLAGE_GELADEN: str | None = None
#: Schlüssel in ``platform_settings``. Bewusst NICHT in den Einstellungen der Oberfläche
#: (``settings_service.ALLOWED_KEYS``): ändert man ihn, erkennt die Anlage ihre
#: eigenen Übernahmen in den Repos nicht mehr.
ANLAGE_SCHLUESSEL = "anlage_kennung_uebernahmen"


def anlage_kennung() -> str:
    """Stabiler, nicht geheimer Name DIESER Anlage — für ``wer`` und das Board.

    Standard ist eine zufällige Kennung (``anlage-1a2b3c4d``), einmal erzeugt und in
    ``platform_settings`` gespeichert (``anlage_laden``): Sie verrät nichts über die
    Anlage — der Host der öffentlichen Adresse stünde sonst in jedem Repo, an dem
    sie arbeitet. ``ANLAGE_KENNUNG`` setzt einen sprechenden Namen.
    """
    from app.config import settings

    eigen = _wert(getattr(settings, "anlage_kennung", "") or "", 100)
    if eigen and _ANLAGE.match(eigen):
        return eigen
    return _ANLAGE_GELADEN or "anlage"


async def anlage_laden(db) -> str:
    """Die gespeicherte Kennung laden — beim ersten Mal erzeugen und speichern.

    Jeder Weg, der Übernahmen schreibt oder liest, ruft das vorher auf; nach dem
    ersten Mal ist es ein Blick in den Speicher des Prozesses.
    """
    global _ANLAGE_GELADEN
    if _ANLAGE_GELADEN:
        return anlage_kennung()
    import secrets

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.platform_settings import PlatformSettings

    try:
        # Eigene Sitzung auf derselben Verbindung: ein Rollback des Aufrufers darf
        # die neue Kennung nicht verwerfen, sonst entstünde beim nächsten Mal eine andere.
        async with AsyncSession(bind=db.bind, expire_on_commit=False) as eigene:
            zeile = (await eigene.execute(
                select(PlatformSettings).where(PlatformSettings.key == ANLAGE_SCHLUESSEL))).scalar_one_or_none()
            if zeile is not None and _ANLAGE.match(zeile.value or ""):
                _ANLAGE_GELADEN = zeile.value
            else:
                neu = f"anlage-{secrets.token_hex(4)}"
                eigene.add(PlatformSettings(key=ANLAGE_SCHLUESSEL, value=neu, is_secret=False))
                await eigene.commit()
                _ANLAGE_GELADEN = neu
    except Exception:  # noqa: BLE001 — ohne Speicher (Tests ohne Tabelle): Ersatzname, nächstes Mal neu
        return anlage_kennung()
    return anlage_kennung()


@dataclass
class Uebernahme:
    """Eine Übernahme (oder Rückgabe) aus einem Kommentar."""

    wer: str
    art: str
    name: str
    anlage: str | None = None
    agent: str | None = None
    agent_id: str | None = None
    lauf: str | None = None
    branch: str | None = None
    seit: datetime | None = None
    bis: datetime | None = None
    kommentar_id: int | None = None
    # Login, der den Kommentar geschrieben hat (``Verlängern`` nur eigener Kommentare).
    autor: str | None = None
    kopfzeile: bool = False
    freigabe: bool = False
    # Wann der Kommentar zuletzt geändert wurde — eine aktualisierte Kopfzeile ist
    # ein Lebenszeichen.
    geaendert: datetime | None = None
    # Übergabe oder Übernahme nach Ablauf: wessen Übernahme damit endet (``wer``).
    vorher: str | None = None
    extra: dict = field(default_factory=dict)

    def als_dict(self) -> dict:
        """Für die Karte (JSON-Spalte ``uebernahme``)."""
        return {
            "wer": self.wer, "art": self.art, "name": self.name, "anlage": self.anlage, "agent": self.agent,
            "agent_id": self.agent_id, "lauf": self.lauf, "branch": self.branch,
            "seit": self.seit.isoformat() if self.seit else None,
            "bis": self.bis.isoformat() if self.bis else None,
            "geaendert": self.geaendert.isoformat() if self.geaendert else None,
            "kommentar_id": self.kommentar_id, "kopfzeile": self.kopfzeile, "autor": self.autor,
        }

    @classmethod
    def aus_dict(cls, d) -> Uebernahme | None:
        if not isinstance(d, dict) or not isinstance(d.get("wer"), str):
            return None
        art = d.get("art") if d.get("art") in ARTEN else EXTERN
        kid = d.get("kommentar_id")
        return cls(wer=d["wer"][:200], art=art, name=str(d.get("name") or "extern")[:120],
                   anlage=d.get("anlage"), agent=d.get("agent"), agent_id=_kennung(d.get("agent_id")),
                   lauf=_kennung(d.get("lauf")), branch=d.get("branch"), seit=_zeit(d.get("seit")),
                   bis=_zeit(d.get("bis")), kommentar_id=kid if isinstance(kid, int) else None,
                   kopfzeile=bool(d.get("kopfzeile")), geaendert=_zeit(d.get("geaendert")),
                   autor=str(d["autor"])[:60] if isinstance(d.get("autor"), str) else None)

    def ablauf(self, dauer: timedelta = DAUER, regung: datetime | None = None) -> datetime | None:
        """Bis wann die Übernahme gilt.

        ``bis`` aus der Kopfzeile (sonst ``seit`` plus Dauer) — verlängert durch jedes
        Lebenszeichen: eine geänderte Kopfzeile oder ``regung`` (Commit auf dem
        Branch, Bewegung am Issue) plus Dauer.
        """
        kandidaten = [self.bis or (self.seit + dauer if self.seit else None)]
        if self.kopfzeile and self.geaendert:
            kandidaten.append(self.geaendert + dauer)
        if regung is not None:
            kandidaten.append(_zeit(regung) + dauer)
        kandidaten = [k for k in kandidaten if k is not None]
        return max(kandidaten) if kandidaten else None

    def abgelaufen(self, jetzt: datetime | None = None, dauer: timedelta = DAUER,
                   regung: datetime | None = None) -> bool:
        ende = self.ablauf(dauer, regung)
        return ende is not None and (jetzt or _jetzt()) > ende

    def gehoert(self, *, agent_id: str | None = None, lauf: str | None = None,
                agent_name: str | None = None) -> bool:
        """Gehört die Übernahme diesem Agenten (bzw. diesem Lauf) DIESER Anlage?"""
        if self.art != PLATTFORM:
            return False
        if lauf and self.lauf == lauf:
            return True
        if agent_id and self.agent_id == agent_id:
            return True
        # Alte Freitext-Übernahmen kennen nur den Namen des Agenten.
        return bool(not self.agent_id and agent_name and self.agent
                    and self.agent.strip().lower() == agent_name.strip().lower())


def wer_plattform(agent_name: str | None, anlage: str | None = None) -> str:
    return f"ai-employee:{anlage or anlage_kennung()}/{_wert(agent_name or 'Lead', 80) or 'Lead'}"


def _wer_lesen(roh: str, eigene_anlage: str) -> tuple[str, str, str, str | None, str | None]:
    """``wer`` → (wer, art, lesbarer Name, Anlage, Agent). Unbekanntes ist „extern“."""
    roh = _wert(roh, 200)
    if roh.startswith("ai-employee:") and "/" in roh:
        anlage, _, agent = roh.removeprefix("ai-employee:").partition("/")
        agent = agent.strip()[:80]
        if _ANLAGE.match(anlage) and agent:
            if anlage == eigene_anlage:
                return roh, PLATTFORM, agent, anlage, agent
            return roh, ANDERE_ANLAGE, f"Anlage {anlage}: {agent}", anlage, agent
    if roh == "claude-code" or roh.startswith("claude-code:"):
        frei = roh.partition(":")[2].strip()[:60]
        return roh, CLAUDE_CODE, f"Claude Code ({frei})" if frei else "Claude Code", None, None
    if roh.startswith("mensch:"):
        login = roh.partition(":")[2].strip().lstrip("@")
        if _LOGIN.match(login):
            return roh, MENSCH, f"@{login}", None, None
    return "extern", EXTERN, "extern", None, None


#: Ein ``bis`` reicht höchstens so weit in die Zukunft — sonst hielte eine Kopfzeile
#: mit ``bis="2099-…"`` ein Ticket für immer.
MAX_BIS = timedelta(hours=24)


def lesen(kommentar: dict, eigene_anlage: str | None = None) -> Uebernahme | None:
    """Übernahme oder Rückgabe aus einem Kommentar — None, wenn er keine ist.

    ``kommentar``: ``{"id", "body", "erstellt", "autor", "vertraut"}`` (so liefern es
    die Anbieter). ``vertraut`` heißt: der Autor darf ins Repo schreiben (OWNER,
    MEMBER, COLLABORATOR) oder ist das Konto des Projekt-Schlüssels.

    Nur von Vertrauten gelten Kopfzeile, Rückgabe und Übergabe. Von allen anderen
    — bei öffentlichen Repos kann jeder kommentieren — ist eine Übernahme „extern“
    ohne ``bis``, ohne Agent, ohne Lauf; eine Rückgabe gilt gar nicht. So kann ein
    Fremder weder eine Plattform-Identität vortäuschen noch eine Übernahme löschen.
    """
    u = _lesen(kommentar, eigene_anlage)
    if u is None:
        return None
    jetzt = _jetzt()
    if u.seit is not None and u.seit > jetzt:
        u.seit = jetzt
    if u.bis is not None and u.bis > jetzt + MAX_BIS:
        u.bis = jetzt + MAX_BIS
    autor = kommentar.get("autor")
    u.autor = str(autor)[:60] if isinstance(autor, str) and autor else None
    if kommentar.get("vertraut") is True:
        return u
    if u.freigabe:
        return None
    return Uebernahme(wer="extern", art=EXTERN, name="extern", seit=u.seit, kommentar_id=u.kommentar_id,
                      autor=u.autor)


def _lesen(kommentar: dict, eigene_anlage: str | None = None) -> Uebernahme | None:
    """Kopfzeile oder Freitext eines Kommentars lesen — ohne Blick auf den Autor (``lesen``)."""
    if not isinstance(kommentar, dict):
        return None
    eigene_anlage = eigene_anlage or anlage_kennung()
    text = str(kommentar.get("body") or "")[:20000].lstrip()
    kid = kommentar.get("id") if isinstance(kommentar.get("id"), int) else None
    erstellt = _zeit(kommentar.get("erstellt"))
    kopf = _KOPF.match(text)
    if kopf:
        felder = {}
        for name, wert in _FELD.findall(kopf.group(2)[:2000]):
            felder.setdefault(name, wert)
        wer, art, name, anlage, agent = _wer_lesen(felder.get("wer", ""), eigene_anlage)
        branch = _wert(felder.get("branch", ""), 200)
        vorher = _wer_lesen(felder["vorher"], eigene_anlage)[0] if felder.get("vorher") else None
        return Uebernahme(
            wer=wer, art=art, name=name, anlage=anlage, agent=agent,
            agent_id=_kennung(felder.get("agent_id")), lauf=_kennung(felder.get("lauf")),
            branch=branch if branch and _BRANCH.match(branch) else None,
            seit=_zeit(felder.get("seit")) or erstellt, bis=_zeit(felder.get("bis")),
            kommentar_id=kid, kopfzeile=True, freigabe=kopf.group(1) == "release",
            geaendert=_zeit(kommentar.get("geaendert")), vorher=vorher,
        )
    erste = next((z.strip() for z in text.splitlines() if z.strip()), "")[:400]
    if not erste or erste.startswith(">"):
        return None
    if _FREI_RUECKGABE.match(erste):
        return Uebernahme(wer="", art=EXTERN, name="", kommentar_id=kid, seit=erstellt, freigabe=True)
    treffer = _FREI_PLATTFORM.search(erste)
    if treffer:
        agent = _wert(treffer.group(1), 80)
        # Freitext nennt keine Anlage — geschrieben hat ihn bis hier diese.
        return Uebernahme(wer=f"ai-employee:?/{agent}", art=PLATTFORM, name=agent, agent=agent,
                          kommentar_id=kid, seit=erstellt)
    if _FREI_UEBERNAHME.match(erste):
        if _CLAUDE_CODE.search(erste):
            return Uebernahme(wer="claude-code", art=CLAUDE_CODE, name="Claude Code", kommentar_id=kid,
                              seit=erstellt)
        return Uebernahme(wer="extern", art=EXTERN, name="extern", kommentar_id=kid, seit=erstellt)
    return None


def _reihenfolge(k: dict):
    zeit = _zeit(k.get("erstellt")) if isinstance(k, dict) else None
    kid = k.get("id") if isinstance(k, dict) and isinstance(k.get("id"), int) else 0
    return (zeit or datetime.min.replace(tzinfo=timezone.utc), kid)


def _selbe(a: Uebernahme, b: Uebernahme) -> bool:
    """Derselbe Inhaber? (gleiches ``wer`` — oder gleicher Agent derselben Anlage)"""
    return a.wer == b.wer or bool(a.agent_id and a.agent_id == b.agent_id and a.anlage == b.anlage)


@dataclass
class Auswertung:
    """Was die Kommentare eines Issues über seine Übernahme sagen."""

    inhaber: Uebernahme | None = None
    # Eine zweite, noch gültige Übernahme eines ANDEREN — zwei arbeiten am selben Ticket.
    konkurrent: Uebernahme | None = None


def auswerten(kommentare: list[dict], eigene_anlage: str | None = None, *, jetzt: datetime | None = None,
              dauer: timedelta = DAUER) -> Auswertung:
    """Inhaber und Konkurrent aus den Kommentaren, in zeitlicher Reihenfolge gelesen.

    Offen bleibt jede Übernahme, bis sie zurückgegeben wird: Eine Rückgabe mit
    ``wer`` gibt nur die Übernahme DIESES Inhabers frei, eine ohne (Freitext,
    „Übernahme gelöst“ über das Board) jede. Eine Übergabe bzw. Übernahme nach
    Ablauf (Feld ``vorher``) beendet die Übernahme des Vorgängers. Inhaber ist die
    jüngste offene Übernahme; ist daneben eine ältere eines anderen noch gültig
    (nicht abgelaufen), ist das ein Konflikt.
    """
    eigene_anlage = eigene_anlage or anlage_kennung()
    jetzt = jetzt or _jetzt()
    offen: list[Uebernahme] = []
    for k in sorted((k for k in kommentare or [] if isinstance(k, dict)), key=_reihenfolge):
        u = lesen(k, eigene_anlage)
        if u is None:
            continue
        if u.freigabe:
            if not u.kopfzeile or u.wer == "extern":
                offen = []
            else:
                offen = [o for o in offen if not (_selbe(o, u) or (u.lauf and u.lauf == o.lauf))]
            continue
        if u.vorher:
            offen = [o for o in offen if o.wer != u.vorher]
        offen = [o for o in offen if not _selbe(o, u)] + [u]
    if not offen:
        return Auswertung()
    inhaber_ = offen[-1]
    konkurrent = next((o for o in reversed(offen[:-1]) if not o.abgelaufen(jetzt, dauer)), None)
    if konkurrent is not None and inhaber_.abgelaufen(jetzt, dauer):
        konkurrent = None
    return Auswertung(inhaber=inhaber_, konkurrent=konkurrent)


def inhaber(kommentare: list[dict], eigene_anlage: str | None = None) -> Uebernahme | None:
    """Die jüngste Übernahme, die nicht zurückgegeben wurde — oder None (``auswerten``)."""
    return auswerten(kommentare, eigene_anlage).inhaber


def kopfzeile(art: str, **felder) -> str:
    """``<!-- ai-employee:claim … -->`` — alle Werte entschärft, leere weggelassen."""
    teile = [f"ai-employee:{art if art in ('release', 'nachricht') else 'claim'}"]
    for name in ("wer", "agent_id", "lauf", "branch", "seit", "bis", "vorher"):
        wert = felder.get(name)
        if isinstance(wert, datetime):
            wert = wert.astimezone(timezone.utc).isoformat(timespec="seconds")
        wert = _wert(wert, 200) if wert else ""
        if wert:
            teile.append(f'{name}="{wert}"')
    return "<!-- " + " ".join(teile) + " -->"


def _wann(t: datetime | None) -> str:
    return t.astimezone(timezone.utc).strftime("%d.%m. %H:%M UTC") if t else "unbekannt"


def uebernahme_kommentar(*, agent_name: str | None, agent_id: str | None, lauf: str | None, nr: int,
                         projekt_name: str, jetzt: datetime | None = None, dauer: timedelta = DAUER,
                         seit: datetime | None = None, vorgaenger: Uebernahme | None = None) -> str:
    """Kommentar beim Übernehmen: Kopfzeile plus Text für Menschen.

    ``seit``: beim Verlängern bleibt der Beginn, nur ``bis`` rückt vor.
    ``vorgaenger``: Übernahme nach Ablauf einer fremden — der Kommentar nennt sie.
    """
    jetzt = jetzt or _jetzt()
    name = _wert(agent_name or "Lead", 80) or "Lead"
    kopf = kopfzeile("claim", wer=wer_plattform(name), agent_id=agent_id, lauf=lauf, branch=f"{nr}-…",
                     seit=seit or jetzt, bis=jetzt + dauer, vorher=vorgaenger.wer if vorgaenger else None)
    # Kein Projektname: Das Repo kann öffentlich sein, der Name ist interne Information.
    text = (f"{kopf}\nÜbernommen vom AI-Employee-Agenten „{name}“ (Projekt-Board). Branch `{nr}-…`, "
            f"Pull Request mit `Closes #{nr}`. "
            "Der Branch gehört dem Übernehmer: Bitte nicht hineinpushen, sondern hier nachfragen.")
    if vorgaenger is not None:
        text += (f"\n\nDie Übernahme von {_wert(vorgaenger.name, 120)} (seit {_wann(vorgaenger.seit)}) war ohne "
                 f"Lebenszeichen abgelaufen ({_wann(vorgaenger.ablauf(dauer))}); das Projekt erlaubt dann die "
                 "Übernahme. Wer noch daran arbeitet, meldet sich bitte hier.")
    return text


def uebergabe_kommentar(*, an_wer: str, an_name: str, an_agent_id: str | None = None, von_name: str,
                        vorher_wer: str | None, stand: str, naechster_schritt: str, nr: int,
                        jetzt: datetime | None = None, dauer: timedelta = DAUER) -> str:
    """Übergabe: neue Kopfzeile für den Empfänger, darunter Stand und nächster Schritt."""
    jetzt = jetzt or _jetzt()
    kopf = kopfzeile("claim", wer=an_wer, agent_id=an_agent_id, branch=f"{nr}-…", seit=jetzt, bis=jetzt + dauer,
                     vorher=vorher_wer or "extern")
    stand = einzeilig(stand, 1500) or "—"
    naechster = einzeilig(naechster_schritt, 800) or "—"
    return (f"{kopf}\nÜbergeben von {_wert(von_name, 120)} an {_wert(an_name, 120)}.\n\n"
            f"**Stand:** {stand}\n\n**Nächster Schritt:** {naechster}")


def rueckgabe_kommentar(*, agent_name: str | None, agent_id: str | None = None, lauf: str | None = None) -> str:
    kopf = kopfzeile("release", wer=wer_plattform(agent_name or "Lead"), agent_id=agent_id, lauf=lauf)
    return f"{kopf}\nZurückgegeben: Der AI-Employee-Agent arbeitet nicht mehr daran — das Ticket ist wieder frei."


def rueckfrage_kommentar(*, agent_name: str | None, agent_id: str | None = None, lauf: str | None = None,
                         triage_tag: str = "status/triage", bereit_tag: str = "status/ready") -> str:
    """Rückgabe mit offener Rückfrage: Der Agent hört auf, bis ein Mensch geklärt hat."""
    kopf = kopfzeile("release", wer=wer_plattform(agent_name or "Lead"), agent_id=agent_id, lauf=lauf)
    return (f"{kopf}\nRückfrage offen: Der AI-Employee-Agent arbeitet nicht weiter, bis sie beantwortet ist "
            f"(Frage im Kommentar dazu). Nach der Klärung bitte `{_wert(triage_tag, 60).replace('`', '')}` entfernen "
            f"und `{_wert(bereit_tag, 60).replace('`', '')}` setzen — dann geht das Ticket zurück nach Bereit.")


def anzeige(u: Uebernahme | None) -> str:
    """Kurz für Meldungen: „Claude Code (seit 29.10. 14:03)“."""
    if u is None:
        return "jemand außerhalb des Boards"
    seit = f" (seit {_wann(u.seit)})" if u.seit else ""
    return f"{u.name}{seit}"


#: So committen Plattform-Agenten (agent/app/main.py: ``<agent_id>@ai-employee.local``,
#: Name = Agentenname) — die Mail-Adresse ist der verlässliche Teil.
PLATTFORM_MAIL = "@ai-employee.local"


def commit_passt(u: Uebernahme, autor_mail: str | None) -> bool:
    """Passt der Autor eines Commits auf dem Übernahme-Branch zum Inhaber?

    Alle schreiben oft unter demselben Git-Konto; unterscheiden lassen sich nur
    Plattform-Agenten (eigene Mail-Adresse je Agent). Darum: Hat ein Agent der
    Plattform übernommen, muss ER committen. Hat jemand anderes übernommen (Claude
    Code, Mensch), darf kein Plattform-Agent auf seinem Branch committen — genau
    der Zusammenstoß vom 29.10.
    """
    mail = (autor_mail or "").strip().lower()
    if not mail:
        return True
    plattform = mail.endswith(PLATTFORM_MAIL)
    if u.agent_id:
        if plattform:
            return mail == f"{u.agent_id.lower()}{PLATTFORM_MAIL}"
        return u.art not in (PLATTFORM, ANDERE_ANLAGE)
    if u.art == PLATTFORM:
        return True  # alte Freitext-Übernahme ohne Kennung: nicht prüfbar
    return not plattform


# ── Text, den die Plattform in fremde Repos schreibt ─────────────────────────

_BILD = re.compile(r"!\[([^\]\n]*)\]\(([^)\n]*)\)")
#: Markdown-Link ``[Text](Adresse)`` ODER nackte Adresse — in EINEM Durchgang, damit
#: die Adresse eines Links nicht noch einmal als nackte Adresse gilt.
_LINK_ODER_URL = re.compile(r"\[([^\]\n]*)\]\(([^)\s]+)(?:\s+\"[^\"\n]*\")?\)|(?<!`)\bhttps?://[^\s<>`)\]]+",
                            re.I)


def _host(url: str) -> str:
    from urllib.parse import urlsplit

    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _als_text(url: str) -> str:
    """Eine Adresse als Code-Text: sichtbar, aber nicht anklickbar und ohne Vorschau."""
    return "`" + url.replace("`", "")[:200] + "`"


def neutralisieren(text: str | None, erlaubte_hosts: set[str] | frozenset[str] = frozenset()) -> str:
    """Text für einen Issue-Kommentar entschärfen — er stammt von einem Agenten.

    * Kein rohes HTML (``<`` wird ``&lt;``) — damit auch keine Kopfzeile ``<!-- … -->``.
    * Keine Bilder: ``![…](…)`` lädt beim Lesen eine fremde Adresse (Zählpixel,
      Datenabfluss über die Adresse). Es bleibt der Text, die Adresse als Code.
    * Links nur auf den Host des Repos und den der Anlage; alle anderen nur als Text.
    """
    erlaubt = {h.lower() for h in erlaubte_hosts if h}
    t = entschaerft(text or "").replace("<", "&lt;")
    t = _BILD.sub(lambda m: f"Bild „{m.group(1).strip() or 'ohne Titel'}“: {_als_text(m.group(2))}", t)
    t = t.replace("![", "!\\[")

    def ersetzen(m):
        if m.group(2) is not None:  # [Text](Adresse)
            return m.group(0) if _host(m.group(2)) in erlaubt else f"{m.group(1)} ({_als_text(m.group(2))})"
        return m.group(0) if _host(m.group(0)) in erlaubt else _als_text(m.group(0))

    return _LINK_ODER_URL.sub(ersetzen, t)


def wer_aus_eingabe(roh: str | None) -> tuple[str, str, str] | None:
    """Empfänger einer Übergabe außerhalb der Plattform → (wer, art, Name), sonst None.

    Erlaubt: ``claude-code[:<freitext>]``, ``mensch:<login>`` und Agenten anderer
    Anlagen ``ai-employee:<anlage>/<agent>``.
    """
    wer, art, name, _, _ = _wer_lesen(roh or "", anlage_kennung())
    if art == EXTERN:
        return None
    return wer, art, name


# ── Rückkanal: Issue-Kommentar → Agent, Agent → Issue ────────────────────────

#: ``@ai-employee <Agent>: <Text>`` bzw. ``@ai-employee <anlage>/<Agent>: …`` am
#: Zeilenanfang; ohne Namen (``@ai-employee: …``) an den Lead. Zitate (``>``) zählen nicht.
_ANSPRACHE = re.compile(r"^[ \t]*@ai-employee(?:[ \t]+(?P<ziel>[^:\n]{1,100}?))?[ \t]*:[ \t]*(?P<text>\S.*)$",
                        re.I | re.M)
#: Erste Zeile eines Kommentars der Plattform (Übernahme, Rückgabe, Nachricht).
_PLATTFORM_KOPF = re.compile(r"^<!--\s*ai-employee:[a-z]+\b(.*?)-->", re.S)
MAX_ANSPRACHE = 3000


@dataclass
class Ansprache:
    """Eine Nachricht aus einem Issue-Kommentar an einen Agenten dieser Anlage."""

    kommentar_id: int
    agent: str | None     # Name des angesprochenen Agenten; None = Lead
    text: str
    von: str              # lesbar: „@login“ oder „Anlage X: Agent“


def ansprachen(kommentar: dict, eigene_anlage: str | None = None) -> Ansprache | None:
    """Die Ansprache an DIESE Anlage in einem Kommentar — oder None.

    Kommentare, die diese Anlage selbst geschrieben hat (Kopfzeile mit ihrer
    Kennung), zählen nicht: sonst weckte eine Rückfrage, die eine Ansprache
    zitiert, den eigenen Agenten. Eine Ansprache mit fremder Anlage
    (``@ai-employee anlage-nord/DEV: …``) gilt der anderen Anlage. Der Text ist
    entschärft und gekürzt — er bleibt fremder Text.
    """
    if not isinstance(kommentar, dict) or not isinstance(kommentar.get("id"), int):
        return None
    if kommentar.get("vertraut") is not True:
        return None  # nur Konten mit Schreibrecht am Repo dürfen Agenten wecken
    eigene_anlage = eigene_anlage or anlage_kennung()
    text = str(kommentar.get("body") or "")[:20000].lstrip()
    von = f"@{kommentar['autor']}" if kommentar.get("autor") and _LOGIN.match(str(kommentar["autor"])) else "extern"
    kopf = _PLATTFORM_KOPF.match(text)
    if kopf:
        felder = dict(_FELD.findall(kopf.group(1)[:2000]))
        _, art, name, _, _ = _wer_lesen(felder.get("wer", ""), eigene_anlage)
        if art == PLATTFORM:
            return None  # von dieser Anlage selbst geschrieben
        if art != EXTERN:
            von = name
    treffer = _ANSPRACHE.search(text)
    if treffer is None:
        return None
    ziel = (treffer.group("ziel") or "").strip()
    if "/" in ziel:
        anlage, _, ziel = ziel.partition("/")
        if anlage.strip().lower() != eigene_anlage.lower():
            return None
    zeilen = [z for z in text[treffer.start("text"):].splitlines() if not z.lstrip().startswith(">")]
    inhalt = entschaerft("\n".join(zeilen)).strip()[:MAX_ANSPRACHE]
    if not inhalt:
        return None
    return Ansprache(kommentar_id=kommentar["id"], agent=einzeilig(ziel, 80) or None, text=inhalt,
                     von=einzeilig(von, 60))


def nachricht_kommentar(*, agent_name: str | None, agent_id: str | None, text: str) -> str:
    """Kommentar eines Plattform-Agenten ins Issue (Rückfrage an Externe).

    Die Kopfzeile weist ihn als Kommentar dieser Anlage aus — der Rückkanal
    liest ihn deshalb nicht als Ansprache, auch wenn er eine zitiert.
    """
    name = _wert(agent_name or "Agent", 80) or "Agent"
    kopf = kopfzeile("nachricht", wer=wer_plattform(name), agent_id=agent_id)
    inhalt = (text or "").strip()[:4000]
    return (f"{kopf}\n**{name}** (AI-Employee-Agent):\n\n{inhalt}\n\n"
            f"_Antwort an den Agenten: ein Kommentar mit `@ai-employee {name}: …`_")


# ── Abschnitt für die AGENTS.md ──────────────────────────────────────────────

AGENTS_MD_START = "<!-- ai-employee:zusammenarbeit:start -->"
AGENTS_MD_ENDE = "<!-- ai-employee:zusammenarbeit:ende -->"


def agents_md_abschnitt(anlage: str | None = None) -> str:
    """Der Abschnitt „Zusammenarbeit mit AI Employee“ für die ``AGENTS.md`` eines Repos.

    Er beschreibt genau das, was die Plattform liest und durchsetzt — damit
    Claude Code, andere Anlagen und Menschen dieselben Regeln haben.
    """
    anlage = anlage or anlage_kennung()
    beispiel = kopfzeile("claim", wer="claude-code:Sitzung am Laptop", branch="12-login-fehler",
                         seit="2026-10-29T14:00:00+00:00", bis="2026-10-29T16:00:00+00:00")
    return f"""{AGENTS_MD_START}
## Zusammenarbeit mit AI Employee

An diesem Repo arbeiten Agenten der AI-Employee-Anlage `{anlage}`, andere Agenten
(z. B. Claude-Code-Sitzungen) und Menschen — oft unter demselben Git-Konto. Das
Issue ist die einzige Wahrheit darüber, wer woran arbeitet.

### Übernehmen
1. Vor dem Start prüfen: Trägt das Issue das Tag `status/in-arbeit`, oder steht
   in den Kommentaren eine Übernahme ohne Rückgabe? Dann gehört es jemand anderem.
2. Tag `status/in-arbeit` setzen und einen Kommentar schreiben, dessen **erste
   Zeile** die Übernahme-Kopfzeile ist, darunter Text für Menschen:

   ```
   {beispiel}
   Übernommen von Claude Code. Branch `12-login-fehler`.
   ```

   `wer` ist eines von: `ai-employee:<anlage>/<agent>`, `claude-code:<freitext>`,
   `mensch:<login>` oder `extern`. `seit`/`bis` in ISO-8601 mit Zeitzone.
3. Ein Issue, ein Branch: `<nr>-<kurzer-name>` (z. B. `12-login-fehler`).
   Pull Request mit `Closes #<nr>`.

### Lebenszeichen und Ablauf
Eine Übernahme gilt bis `bis` (üblich: zwei Stunden). Jedes Lebenszeichen
verlängert sie: ein Push auf den eigenen Branch oder eine aktualisierte
Kopfzeile (neues `bis`). Ohne Lebenszeichen gilt sie als liegen geblieben; ein
Mensch kann sie dann freigeben. Höchstens zwei offene Übernahmen je Bearbeiter.

### Der Branch gehört dem Übernehmer
Niemand sonst pusht in den Branch einer fremden Übernahme. Agenten von AI
Employee werden dabei gebremst (Zusammenstoß-Schutz, keine Sicherheitsgrenze). Stattdessen: im Issue nachfragen,
einen eigenen Branch anlegen und einen Pull Request **in den fremden Branch**
stellen, oder den Menschen bitten, die Übernahme freizugeben.

### Übergabe und Rückgabe
Wer an jemand anderen übergibt, schreibt einen Kommentar mit Stand und nächstem
Schritt, dessen erste Zeile die Kopfzeile des Empfängers ist, mit
`vorher="<wer bisher>"`. Wer einfach aufhört, schreibt einen Kommentar, dessen
erste Zeile `<!-- ai-employee:release wer="…" -->` ist, und nimmt das Tag
`status/in-arbeit` weg.

### Ablauf und Status-Tags
Neue Issues landen in der Inbox. Nur ein Mensch gibt frei: Tag `status/ready`
(→ Bereit); Agenten nehmen nur freigegebene Issues. Wer übernimmt, setzt
`status/in-arbeit`. Bei einer Rückfrage: die Frage als Kommentar ins Issue,
`status/triage` setzen, `status/in-arbeit` entfernen und nicht weiterarbeiten.
Der Mensch antwortet, entfernt `status/triage` und setzt wieder `status/ready`.
Ein offener PR mit `Closes #<nr>` heißt Review; gemergt bzw. geschlossen heißt
erledigt, als „not planned“ geschlossen heißt verworfen. Hat das Repo schon
eigene Tags dafür (z. B. `agent:blocked` für Rückfragen), gelten diese.

### Selbst mergen
Erlaubt (Squash), sofern dieses Repo nichts anderes sagt, wenn alles erfüllt
ist: Branch enthält das aktuelle main (danach erneut geprüft); Tests grün, bei
Oberflächen-Änderungen auch ein Browser-Test; CI grün auf dem letzten Commit
(Lauf zum headSha prüfen); Review durch jemand anderen als den Autor (bei
Login, Rollen, Personendaten, Uploads oder neuen öffentlichen Endpunkten
zusätzlich ein Security-Review); niemand sonst arbeitet am PR. Fremde Branches
nie überschreiben (kein `--force`).

### Konflikte
Zwei gültige Übernahmen am selben Issue oder ein Commit eines anderen auf dem
Branch einer Übernahme markiert das Board rot und meldet es den Menschen.

### Rückkanal
Fragen an die Agenten von AI Employee stellt man im Issue, mit einer Zeile, die
so beginnt:

```
@ai-employee <Agent>: <Frage>
```

Ohne Namen (`@ai-employee: …`) geht sie an den Lead des Projekts; arbeiten
mehrere Anlagen am Repo, nennt `@ai-employee <anlage>/<Agent>: …` die Anlage.
Die Frage landet im Projektgespräch und weckt den Agenten. Seine Rückfragen
schreibt er ins Issue.
Kopfzeilen, Rückgaben und Fragen zählen nur von Konten mit Schreibrecht am
Repo. Der Rückkanal ist bei privaten Repos mit Schlüssel an, sonst aus
(Projekteinstellung).
{AGENTS_MD_ENDE}
"""


def agents_md_einfuegen(bisher: str | None, abschnitt: str) -> str:
    """Abschnitt in eine bestehende ``AGENTS.md`` setzen — einen alten ersetzen, sonst anhängen."""
    bisher = bisher or ""
    start, ende = bisher.find(AGENTS_MD_START), bisher.find(AGENTS_MD_ENDE)
    if start != -1 and ende > start:
        return bisher[:start] + abschnitt.strip() + bisher[ende + len(AGENTS_MD_ENDE):]
    if not bisher.strip():
        return "# AGENTS.md\n\n" + abschnitt
    return bisher.rstrip() + "\n\n" + abschnitt
