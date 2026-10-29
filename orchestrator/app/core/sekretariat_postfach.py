"""Postfach und Kalender für die Seite „Heute“ und das Tagesbriefing (core/sekretariat.py).

Liest mit dem Microsoft-Zugang des NUTZERS (nicht dem eines Agenten) — Seite und
Briefing sehen so dieselben Daten. Alles hier ist einfach und nachvollziehbar:

* **Fehlergrund** (``fehler_aus``): je Quelle ein Code und ein kurzer deutscher Grund
  statt „nicht erreichbar“.
* **Gruppen** (``einordnen``): „Braucht dich“, „Weitere“, „Automatisch“ — nach Absender
  und Kopfzeilen, ohne Modellaufruf. Jede Mail trägt die Gründe ihrer Einordnung.
* **Fristen** (``frist_aus``): ein Datum in Betreff oder Vorschau, wenn ein Fristwort
  dabeisteht („bis 15.11.“, „Frist: 3. Dezember“).

Die Microsoft-Aufrufe laufen über ``msgraph_mcp._graph`` (dort auch für die Tests
nachgestellt).
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

ANZAHL_UNGELESEN = 15
MAIL_FELDER = ("id,subject,from,sender,toRecipients,ccRecipients,receivedDateTime,importance,flag,"
               "bodyPreview,webLink")

# ─── Fehlergrund ────────────────────────────────────────────────

NICHT_VERBUNDEN = "nicht_verbunden"
ABGELAUFEN = "abgelaufen"
BERECHTIGUNG = "berechtigung"
STOERUNG = "stoerung"
ZEITUEBERSCHREITUNG = "zeitueberschreitung"

GRUENDE = {
    NICHT_VERBUNDEN: "Dein Microsoft-Konto ist nicht verbunden.",
    ABGELAUFEN: "Die Anmeldung bei Microsoft ist abgelaufen — bitte neu verbinden.",
    BERECHTIGUNG: "Microsoft verweigert den Zugriff: Beim Verbinden fehlt eine Berechtigung. Neu verbinden "
                  "oder den Administrator fragen.",
    STOERUNG: "Microsoft antwortet gerade mit einem Fehler (Störung bei Microsoft).",
    ZEITUEBERSCHREITUNG: "Microsoft hat nicht rechtzeitig geantwortet (Zeitüberschreitung).",
}


def fehler(code: str, grund: str | None = None) -> dict:
    return {"code": code, "grund": grund or GRUENDE[code]}


def fehler_aus(e: BaseException) -> dict:
    """Ausnahme beim Lesen (Graph oder Anmeldung) → Code und Grund für Seite und Briefing."""
    import httpx

    from app.core.msgraph_mcp import GraphError

    if isinstance(e, GraphError):
        if e.status_code == 401:
            return fehler(ABGELAUFEN)
        if e.status_code == 403:
            return fehler(BERECHTIGUNG)
        if e.status_code in (408, 504):
            return fehler(ZEITUEBERSCHREITUNG)
        if e.status_code == 429:
            return fehler(STOERUNG, "Microsoft drosselt gerade die Anfragen (zu viele in kurzer Zeit).")
        if e.status_code < 500:
            return fehler(STOERUNG, f"Microsoft hat die Anfrage abgelehnt (HTTP {e.status_code}).")
        return fehler(STOERUNG)
    if isinstance(e, (httpx.TimeoutException, asyncio.TimeoutError)):
        return fehler(ZEITUEBERSCHREITUNG)
    if isinstance(e, httpx.HTTPError):
        return fehler(STOERUNG)
    text = str(e)
    # Meldungen aus services/oauth_service.get_valid_token
    if text.startswith("No integration found"):
        return fehler(NICHT_VERBUNDEN)
    if "expired" in text.lower():
        return fehler(ABGELAUFEN)
    treffer = re.search(r"refresh failed: (\d{3})", text)
    if treffer:
        return fehler(STOERUNG) if int(treffer.group(1)) >= 500 else fehler(ABGELAUFEN)
    return fehler(STOERUNG)


# ─── Kalender ───────────────────────────────────────────────────


def _utc(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def kalender_pfad(beginn: datetime, ende: datetime, top: int = 60) -> str:
    """Pfad für ``calendarView`` — Zeitpunkte in UTC mit ``Z`` und kodiert.

    Bis hier standen ``isoformat()``-Zeiten roh in der Adresse: das ``+01:00`` kam bei
    Microsoft als Leerzeichen an, und jede Abfrage endete mit HTTP 400."""
    params = {
        "startDateTime": _utc(beginn), "endDateTime": _utc(ende), "$orderby": "start/dateTime", "$top": top,
        "$select": "subject,start,end,location,isOnlineMeeting,isAllDay,isCancelled,webLink",
    }
    return "/me/calendarView?" + urlencode(params, quote_via=quote, safe="$/,")


async def termine(token: str, zone: str) -> tuple[list[dict], list[dict]]:
    """(heute, morgen) — beide aus EINER Abfrage, Zeiten in der Zone der Anlage."""
    from app.core.msgraph_mcp import _graph

    tz = ZoneInfo(zone)
    beginn = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    morgen = (beginn + timedelta(days=1)).date().isoformat()
    daten = await _graph("GET", kalender_pfad(beginn, beginn + timedelta(days=2)), token,
                         headers={"Prefer": f'outlook.timezone="{zone}"'})
    heute_l, morgen_l = [], []
    for t in daten.get("value") or []:
        if t.get("isCancelled"):
            continue
        start = (t.get("start") or {}).get("dateTime") or ""
        eintrag = {
            "betreff": t.get("subject") or "(ohne Titel)", "beginn": start or None,
            "ende": (t.get("end") or {}).get("dateTime"), "ort": ((t.get("location") or {}).get("displayName") or ""),
            "online": bool(t.get("isOnlineMeeting")), "ganztags": bool(t.get("isAllDay")),
            "link": _https(t.get("webLink")),
        }
        (morgen_l if start[:10] == morgen else heute_l).append(eintrag)
    return heute_l, morgen_l


# ─── Posteingang ────────────────────────────────────────────────


def _https(link) -> str | None:
    """Nur echte https-Adressen werden zum Link (kein ``javascript:``)."""
    link = str(link or "")
    return link if link.startswith("https://") else None


def _adresse(feld) -> str:
    return str(((feld or {}).get("emailAddress") or {}).get("address") or "").strip().lower()


_NOREPLY = re.compile(r"^(no[-_.]?reply|do[-_.]?not[-_.]?reply|donotreply|mailer-daemon|postmaster|bounce[s]?)"
                      r"([-_.+].*)?@", re.I)
_ATLASSIAN = re.compile(r"(@|\.)(atlassian\.(net|com)|jira[^@]*)$|jira@", re.I)
_FRAGE = re.compile(r"\?|\b(kannst du|könnten sie|können sie|bitte um|rückmeldung|could you|can you)\b", re.I)
_OHNE_LINKS = re.compile(r"\S+://\S+|<[^>]*>")
_TERMIN_TYPEN = ("eventMessageRequest", "eventMessage")


def einordnen(m: dict, eigene: set[str]) -> tuple[str, list[str]]:
    """Gruppe einer Mail und warum: ``braucht_dich`` | ``weitere`` | ``automatisch``."""
    von = _adresse(m.get("from")) or _adresse(m.get("sender"))
    betreff = (m.get("subject") or "").lower()
    kopf = {str(h.get("name") or "").lower(): str(h.get("value") or "").lower()
            for h in (m.get("internetMessageHeaders") or []) if isinstance(h, dict)}
    automatisch = []
    if _NOREPLY.search(von):
        automatisch.append("Absender antwortet nicht (no-reply)")
    if _ATLASSIAN.search(von):
        automatisch.append("Benachrichtigung von Atlassian/Jira")
    if "quarantine" in von or ("quarant" in betreff and von.endswith("microsoft.com")):
        automatisch.append("Quarantäne-Hinweis von Microsoft")
    if "list-unsubscribe" in kopf or "list-id" in kopf:
        automatisch.append("Newsletter oder Verteiler (Abmeldelink im Kopf)")
    if kopf.get("precedence") in ("bulk", "list", "junk"):
        automatisch.append("Massenversand")
    if kopf.get("auto-submitted", "no") not in ("", "no"):
        automatisch.append("automatisch erzeugt")
    if automatisch:
        return "automatisch", automatisch

    gruende = []
    an = {_adresse(r) for r in (m.get("toRecipients") or [])}
    if eigene & an:
        gruende.append("direkt an dich")
    if any(t in str(m.get("@odata.type") or "") for t in _TERMIN_TYPEN):
        gruende.append("Terminanfrage")
    # Ohne Links: ein „?“ in einer Adresse ist keine Frage.
    if _FRAGE.search(_OHNE_LINKS.sub(" ", f"{m.get('subject') or ''} {m.get('bodyPreview') or ''}")):
        gruende.append("enthält eine Frage")
    if m.get("importance") == "high" or (m.get("flag") or {}).get("flagStatus") == "flagged":
        gruende.append("als wichtig markiert")
    if gruende:
        return "braucht_dich", gruende
    return "weitere", ["nur in Kopie oder an einen Verteiler" if eigene and not (eigene & an) else "keine Frage, kein Termin"]


# ─── Fristen ────────────────────────────────────────────────────

_MONATE = {"jan": 1, "jän": 1, "feb": 2, "mär": 3, "mar": 3, "apr": 4, "mai": 5, "may": 5, "jun": 6, "jul": 7,
           "aug": 8, "sep": 9, "okt": 10, "oct": 10, "nov": 11, "dez": 12, "dec": 12}
_FRISTWORT = re.compile(r"\b(bis|frist|fällig|faellig|spätestens|spaetestens|deadline|due|until|abgabe|einreich\w*|"
                        r"termin|läuft|ablauf|zum)\b", re.I)
_DATUM_PUNKT = re.compile(r"(?<![\w.])(\d{1,2})\.\s?(\d{1,2})\.(?:\s?(\d{4}|\d{2})(?!\d))?(?!\d)")
_DATUM_ISO = re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)")
_DATUM_WORT = re.compile(r"(?<![\w.])(\d{1,2})\.?\s+(jan|jän|feb|mär|mar|apr|mai|may|jun|jul|aug|sep|okt|oct|nov|dez|dec)"
                         r"[a-zä]*\.?(?:\s+(\d{4}))?", re.I)


def frist_aus(text: str, heute: date, *, horizont_tage: int = 180) -> date | None:
    """Das erste plausible Datum ab heute, wenn ein Fristwort im Text steht."""
    if not text or not _FRISTWORT.search(text):
        return None
    kandidaten: list[tuple[int, int, int | None]] = []
    for t in _DATUM_ISO.finditer(text):
        kandidaten.append((int(t.group(3)), int(t.group(2)), int(t.group(1))))
    for t in _DATUM_PUNKT.finditer(text):
        jahr = t.group(3)
        kandidaten.append((int(t.group(1)), int(t.group(2)),
                           (int(jahr) + 2000 if len(jahr) == 2 else int(jahr)) if jahr else None))
    for t in _DATUM_WORT.finditer(text):
        kandidaten.append((int(t.group(1)), _MONATE[t.group(2).lower()[:3]], int(t.group(3)) if t.group(3) else None))
    for tag, monat, jahr in kandidaten:
        for j in ([jahr] if jahr else [heute.year, heute.year + 1]):
            try:
                d = date(j, monat, tag)
            except ValueError:
                continue
            if heute <= d <= heute + timedelta(days=horizont_tage):
                return d
    return None


# ─── Lesen ──────────────────────────────────────────────────────


async def _nachrichten(token: str) -> list[dict]:
    """Die ungelesenen — erst mit Kopfzeilen (Newsletter erkennen); lehnt Microsoft
    das in der Liste ab, ohne sie."""
    from app.core.msgraph_mcp import GraphError, _graph

    params = {"$filter": "isRead eq false", "$orderby": "receivedDateTime desc", "$top": ANZAHL_UNGELESEN,
              "$select": f"{MAIL_FELDER},internetMessageHeaders"}
    try:
        return (await _graph("GET", "/me/mailFolders/inbox/messages", token, params=params)).get("value") or []
    except GraphError as e:
        if e.status_code != 400:
            raise
    params["$select"] = MAIL_FELDER
    return (await _graph("GET", "/me/mailFolders/inbox/messages", token, params=params)).get("value") or []


async def _gesamt_ungelesen(token: str) -> int | None:
    from app.core.msgraph_mcp import _graph

    try:
        ordner = await _graph("GET", "/me/mailFolders/inbox", token, params={"$select": "unreadItemCount"})
    except Exception as e:  # noqa: BLE001 — ohne Zahl zeigt die Seite die Liste
        log.info("Heute: Zahl der ungelesenen nicht gelesen: %s", e)
        return None
    zahl = ordner.get("unreadItemCount")
    return int(zahl) if isinstance(zahl, int) else None


async def _eigene_adressen(token: str, bekannt: set[str]) -> set[str]:
    from app.core.msgraph_mcp import _graph

    adressen = {a.lower() for a in bekannt if "@" in a}
    try:
        ich = await _graph("GET", "/me", token, params={"$select": "mail,userPrincipalName"})
        adressen |= {str(ich.get(k) or "").lower() for k in ("mail", "userPrincipalName") if ich.get(k)}
    except Exception as e:  # noqa: BLE001 — dann nur die Anmeldeadresse
        log.info("Heute: eigene Adresse nicht gelesen: %s", e)
    return adressen


def mail_dict(m: dict, eigene: set[str], heute: date) -> dict:
    gruppe, gruende = einordnen(m, eigene)
    von = (m.get("from") or m.get("sender") or {}).get("emailAddress") or {}
    vorschau = (m.get("bodyPreview") or "")[:160]
    frist = frist_aus(f"{m.get('subject') or ''} {m.get('bodyPreview') or ''}", heute)
    return {
        "id": m.get("id"), "betreff": m.get("subject") or "(ohne Betreff)", "von": von.get("name") or von.get("address") or "",
        "von_adresse": str(von.get("address") or ""), "eingang": m.get("receivedDateTime"), "vorschau": vorschau,
        "wichtig": m.get("importance") == "high" or ((m.get("flag") or {}).get("flagStatus") == "flagged"),
        "link": _https(m.get("webLink")), "gruppe": gruppe, "gruende": gruende,
        "frist": frist.isoformat() if frist else None,
    }


async def ungelesen(token: str, bekannte_adressen: set[str], zone: str) -> tuple[list[dict], int | None]:
    """(die ersten 15 ungelesenen, eingeordnet; die echte Zahl der ungelesenen im Posteingang)."""
    nachrichten, gesamt, eigene = await asyncio.gather(
        _nachrichten(token), _gesamt_ungelesen(token), _eigene_adressen(token, bekannte_adressen))
    heute = datetime.now(ZoneInfo(zone)).date()
    return [mail_dict(m, eigene, heute) for m in nachrichten], gesamt


async def mail_lesen(token: str, mail_id: str) -> dict:
    """Eine Mail des Nutzers (für Aktionen auf der Seite): Betreff, Absender, Vorschau, Link."""
    from app.core.msgraph_mcp import _gid, _graph

    m = await _graph("GET", f"/me/messages/{_gid(mail_id)}", token,
                     params={"$select": "id,subject,from,bodyPreview,webLink,receivedDateTime"})
    von = (m.get("from") or {}).get("emailAddress") or {}
    return {"id": m.get("id") or mail_id, "betreff": m.get("subject") or "(ohne Betreff)",
            "von": von.get("name") or von.get("address") or "", "von_adresse": str(von.get("address") or ""),
            "vorschau": (m.get("bodyPreview") or "")[:600], "link": _https(m.get("webLink"))}


async def ordner(token: str) -> list[dict]:
    """Ordner des Postfachs (eine Ebene Unterordner) als Ziel für „Ablegen“."""
    from app.core.msgraph_mcp import _graph

    daten = await _graph("GET", "/me/mailFolders", token, params={
        "$top": 100, "$select": "id,displayName",
        "$expand": "childFolders($select=id,displayName)"})
    erg = []
    for f in daten.get("value") or []:
        erg.append({"id": f.get("id"), "name": f.get("displayName") or ""})
        for c in f.get("childFolders") or []:
            erg.append({"id": c.get("id"), "name": f"{f.get('displayName') or ''} / {c.get('displayName') or ''}"})
    return [o for o in erg if o["id"]]


# ─── Regel-Vorschläge aus dem Posteingang ───────────────────────


def regel_vorschlaege(mails: list[dict]) -> list[dict]:
    """2–3 Regeln, die der Posteingang nahelegt — mit dem Anlass. Nichts erfunden: nur,
    wofür es Mails gibt; sonst zwei allgemeine Vorschläge."""
    def zahl(stichwort: str) -> int:
        return sum(1 for m in mails if any(stichwort in g for g in m.get("gruende") or []))

    erg = []
    for stichwort, text, anlass in (
        ("Atlassian/Jira", "Merke: Benachrichtigungen von Atlassian/Jira nur gesammelt im Tagesbriefing nennen, "
                           "nicht einzeln melden.", "Jira-Benachrichtigungen"),
        ("Newsletter", "Merke: Newsletter als gelesen markieren und in den Ordner „Newsletter“ verschieben.",
         "Newsletter"),
        ("no-reply", "Merke: Mails von no-reply-Absendern nie beantworten, nur ablegen.", "Mails von no-reply-Absendern"),
        ("Quarantäne", "Merke: Quarantäne-Hinweise von Microsoft nur im Tagesbriefing erwähnen.", "Quarantäne-Hinweise"),
        ("direkt an dich", "Merke: Mails direkt an mich mit einer Frage immer unter „Top 3 heute“ im Briefing nennen.",
         "direkte Mails"),
    ):
        n = zahl(stichwort)
        if n:
            erg.append({"text": text, "anlass": f"{n} {anlass} im Posteingang"})
    if not erg:
        erg = [{"text": "Merke: Newsletter und Werbung ohne Rückfrage in den Ordner „Später lesen“ verschieben.",
                "anlass": "allgemeiner Vorschlag"},
               {"text": "Merke: Rechnungen immer als Erinnerung zum Fälligkeitstag anlegen.",
                "anlass": "allgemeiner Vorschlag"}]
    return erg[:3]
