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
from urllib.parse import urlsplit

from app.core.fremdtext import einzeilig

PLATTFORM = "plattform"
CLAUDE_CODE = "claude_code"
ANDERE_ANLAGE = "andere_anlage"
MENSCH = "mensch"
EXTERN = "extern"
ARTEN = (PLATTFORM, CLAUDE_CODE, ANDERE_ANLAGE, MENSCH, EXTERN)

#: Standard-Dauer einer Übernahme ab dem letzten Lebenszeichen (Stufe 2 macht sie wirksam).
DAUER = timedelta(hours=2)

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


def anlage_kennung() -> str:
    """Stabiler, nicht geheimer Name DIESER Anlage — für ``wer`` und das Board.

    Eine eigene Kennung gibt es nicht (Lizenz und Lebenszeichen tragen keine);
    der Host der öffentlichen Adresse ist stabil und ohnehin bekannt. Wer ihn
    nicht preisgeben will oder mehrere Anlagen hinter einem Host betreibt,
    setzt ``ANLAGE_KENNUNG``.
    """
    from app.config import settings

    eigen = _wert(getattr(settings, "anlage_kennung", "") or "", 100)
    if eigen and _ANLAGE.match(eigen):
        return eigen
    for adresse in (getattr(settings, "public_app_url", ""), getattr(settings, "oauth_redirect_base_url", "")):
        try:
            host = (urlsplit(adresse or "").hostname or "").lower()
        except ValueError:
            host = ""
        if host and host not in ("localhost", "127.0.0.1", "::1") and _ANLAGE.match(host):
            return host
    return "anlage"


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
    kopfzeile: bool = False
    freigabe: bool = False
    extra: dict = field(default_factory=dict)

    def als_dict(self) -> dict:
        """Für die Karte (JSON-Spalte ``uebernahme``)."""
        return {
            "wer": self.wer, "art": self.art, "name": self.name, "anlage": self.anlage, "agent": self.agent,
            "agent_id": self.agent_id, "lauf": self.lauf, "branch": self.branch,
            "seit": self.seit.isoformat() if self.seit else None,
            "bis": self.bis.isoformat() if self.bis else None,
            "kommentar_id": self.kommentar_id, "kopfzeile": self.kopfzeile,
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
                   kopfzeile=bool(d.get("kopfzeile")))

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


def lesen(kommentar: dict, eigene_anlage: str | None = None) -> Uebernahme | None:
    """Übernahme oder Rückgabe aus einem Kommentar — None, wenn er keine ist.

    ``kommentar``: ``{"id", "body", "erstellt"}`` (so liefern es die Anbieter).
    """
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
        return Uebernahme(
            wer=wer, art=art, name=name, anlage=anlage, agent=agent,
            agent_id=_kennung(felder.get("agent_id")), lauf=_kennung(felder.get("lauf")),
            branch=branch if branch and _BRANCH.match(branch) else None,
            seit=_zeit(felder.get("seit")) or erstellt, bis=_zeit(felder.get("bis")),
            kommentar_id=kid, kopfzeile=True, freigabe=kopf.group(1) == "release",
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


def inhaber(kommentare: list[dict], eigene_anlage: str | None = None) -> Uebernahme | None:
    """Die jüngste Übernahme, die nicht zurückgegeben wurde — oder None.

    Eine Rückgabe mit ``wer`` gibt nur die Übernahme DIESES Inhabers frei; eine
    Rückgabe ohne (Freitext, „Übernahme gelöst“ über das Board) jede.
    """
    eigene_anlage = eigene_anlage or anlage_kennung()
    aktuell: Uebernahme | None = None
    for k in sorted((k for k in kommentare or [] if isinstance(k, dict)), key=_reihenfolge):
        u = lesen(k, eigene_anlage)
        if u is None:
            continue
        if u.freigabe:
            if aktuell is not None and (not u.kopfzeile or u.wer in (aktuell.wer, "extern")
                                        or (u.lauf and u.lauf == aktuell.lauf)):
                aktuell = None
            continue
        aktuell = u
    return aktuell


def kopfzeile(art: str, **felder) -> str:
    """``<!-- ai-employee:claim … -->`` — alle Werte entschärft, leere weggelassen."""
    teile = [f"ai-employee:{'release' if art == 'release' else 'claim'}"]
    for name in ("wer", "agent_id", "lauf", "branch", "seit", "bis"):
        wert = felder.get(name)
        if isinstance(wert, datetime):
            wert = wert.astimezone(timezone.utc).isoformat(timespec="seconds")
        wert = _wert(wert, 200) if wert else ""
        if wert:
            teile.append(f'{name}="{wert}"')
    return "<!-- " + " ".join(teile) + " -->"


def uebernahme_kommentar(*, agent_name: str | None, agent_id: str | None, lauf: str | None, nr: int,
                         projekt_name: str, jetzt: datetime | None = None,
                         dauer: timedelta = DAUER) -> str:
    """Kommentar beim Übernehmen: Kopfzeile plus Text für Menschen."""
    jetzt = jetzt or _jetzt()
    name = _wert(agent_name or "Lead", 80) or "Lead"
    kopf = kopfzeile("claim", wer=wer_plattform(name), agent_id=agent_id, lauf=lauf, branch=f"{nr}-…",
                     seit=jetzt, bis=jetzt + dauer)
    return (f"{kopf}\nÜbernommen vom AI-Employee-Agenten „{name}“ (Projekt-Board, Projekt "
            f"„{_wert(projekt_name, 120)}“). Branch `{nr}-…`, Pull Request mit `Closes #{nr}`. "
            "Der Branch gehört dem Übernehmer: Bitte nicht hineinpushen, sondern hier nachfragen.")


def rueckgabe_kommentar(*, agent_name: str | None, agent_id: str | None = None, lauf: str | None = None) -> str:
    kopf = kopfzeile("release", wer=wer_plattform(agent_name or "Lead"), agent_id=agent_id, lauf=lauf)
    return f"{kopf}\nZurückgegeben: Der AI-Employee-Agent arbeitet nicht mehr daran — das Ticket ist wieder frei."


def anzeige(u: Uebernahme | None) -> str:
    """Kurz für Meldungen: „Claude Code (seit 29.10. 14:03)“."""
    if u is None:
        return "jemand außerhalb des Boards"
    seit = f" (seit {u.seit.astimezone(timezone.utc).strftime('%d.%m. %H:%M')} UTC)" if u.seit else ""
    return f"{u.name}{seit}"


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

### Lebenszeichen
Wer arbeitet, zeigt es: ein Push auf den eigenen Branch oder eine aktualisierte
Kopfzeile. Eine Übernahme ohne Bewegung zeigt das Board nach zwei Stunden als
liegen geblieben; ein Mensch kann sie dann freigeben.

### Der Branch gehört dem Übernehmer
Niemand sonst pusht in den Branch einer fremden Übernahme. Agenten von AI
Employee werden dabei technisch gesperrt. Stattdessen: im Issue nachfragen,
einen eigenen Branch anlegen und einen Pull Request **in den fremden Branch**
stellen, oder den Menschen bitten, die Übernahme freizugeben.

### Übergabe und Rückgabe
Wer aufhört, schreibt einen Kommentar mit dem Stand und dem nächsten Schritt,
dessen erste Zeile `<!-- ai-employee:release wer="…" -->` ist, und nimmt das
Tag `status/in-arbeit` weg.

### Rückkanal
Fragen an die Agenten von AI Employee stellt man im Issue; sie lesen die
Kommentare zu ihren Übernahmen.
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
