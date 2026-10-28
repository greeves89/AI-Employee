"""DLP egress filter (#388) — scan agent-generated text before it leaves the platform.

Two layers:
  * a **pure scanner** (``classify`` / ``mask``) — deterministic, no I/O, fully
    unit-testable. Reuses the credential patterns from ``log_redaction`` for the
    ``secret`` class and adds PII classes (IBAN, credit card w/ Luhn, e-mail,
    11-digit German tax-id style number, BIP-39 crypto-wallet seed phrase).
  * a **DB-aware evaluator** (``evaluate_egress``) — resolves the per-class action
    from ``DlpRule`` (agent-specific > global > built-in default), applies it
    (allow/log/mask/block), and writes an ``AuditLog`` record that records the
    matched *classes and counts only* — never the sensitive value.

Opt-in: passthrough (allow) unless the ``dlp_enabled`` setting is true, so existing
deployments are unaffected until an admin turns it on. Fail-open on internal errors
(logs a warning) so a bug can never wedge all outbound messaging.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from app.core.bip39_wordlist import BIP39_ENGLISH_WORDS
from app.core.log_redaction import _PATTERNS as _SECRET_PATTERNS
from app.core.log_redaction import ZUSATZ_PATTERNS as _FORMAT_PATTERNS
from app.core.log_redaction import redact_logs

logger = logging.getLogger(__name__)

CLASSES = ("secret", "secret_format", "iban", "credit_card", "email", "de_tax_id", "seed_phrase")

# Built-in default action per class when no DB rule overrides it.
DEFAULT_ACTIONS: dict[str, str] = {
    "secret": "block",       # never leak credentials
    "iban": "mask",
    "credit_card": "mask",
    "de_tax_id": "log",      # 11-digit numbers are false-positive prone -> log only
    "email": "allow",        # e-mails are usually legitimate to send
    # #703: weitere Anbieter-Formate. Schwaerzen statt sperren, solange die
    # Fehlalarmrate im Betrieb unbekannt ist -- ``secret`` (block) wuerde bei
    # jedem Treffer die ganze Antwort unterdruecken.
    "secret_format": "mask",
    # Eine Seed-Phrase ist selten ein Fehlalarm; im Klartext hinausgehen
    # lassen ("log") hiesse, sie zu erkennen und trotzdem zu verschenken.
    "seed_phrase": "mask",
}
ACTION_SEVERITY = {"allow": 0, "log": 1, "mask": 2, "block": 3}
VALID_ACTIONS = set(ACTION_SEVERITY)

_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Za-z0-9]){11,30}\b")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_CC = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_DE_TAX = re.compile(r"\b\d{11}\b")
# Buchstaben samt unsichtbarer Zeichen, damit ein Zero-Width-Zeichen ein Wort
# nicht in zwei zerlegt; beim Vergleich mit der Liste fallen sie heraus.
_WORD_TOKEN = re.compile(r"(?:[^\W\d_]|[\u200b-\u200d\u2060\ufeff])+")
_UNSICHTBAR = re.compile(r"[\u200b-\u200d\u2060\ufeff]")
# Zwischen zwei Woertern einer Phrase steht nur Leerraum oder ein Komma,
# hoechstens mit einer Nummerierung ("3. ", "3) "). ``margin_left + margin_right``
# oder ``green.apple(`` ist Code, keine Phrase.
_SEED_TRENNER = re.compile(r"[\s,]*(?:\d{1,2}[.):]?\s+)?[\s,]*")
# BIP-39 mnemonics are always 12/15/18/21/24 words -- 12 is the real-world floor.
_SEED_FENSTER = 12
# Ein Fuellwort je sechs Woerter reicht nicht, um eine Phrase zu verstecken.
_SEED_MIN_TREFFER = 10


def _im_wortschatz(wort: str) -> bool:
    import unicodedata

    return unicodedata.normalize("NFKC", _UNSICHTBAR.sub("", wort)).lower() in BIP39_ENGLISH_WORDS


def _bip39_spans(text: str) -> list[tuple[int, int]]:
    """Textstellen, die wie eine BIP-39-Seed-Phrase aussehen (#703).

    Gleitendes Fenster: 12 aufeinanderfolgende Woerter, davon mindestens 10
    aus der festen Liste mit 2048 Woertern. Keine Pruefsumme -- ein
    Format-Signal wie die anderen Muster, kein Validator. Gedacht gegen das
    versehentliche Weitergeben (Phrase in Chat, Ticket oder Protokoll), nicht
    gegen jemanden, der sie absichtlich verstecken will (kyrillische
    Doppelgaenger, Base64, Aufteilen auf zwei Nachrichten erkennt es nicht).
    """
    tokens = list(_WORD_TOKEN.finditer(text))
    spans: list[tuple[int, int]] = []
    # In Abschnitte zerlegen, in denen nur erlaubte Trenner zwischen den Woertern stehen.
    abschnitt: list[re.Match] = []

    def auswerten(teil: list[re.Match]) -> None:
        if len(teil) < _SEED_FENSTER:
            return
        treffer = [_im_wortschatz(t.group()) for t in teil]
        # Jedes Fenster mit genug Treffern ganz markieren -- auch Fuellwoerter
        # darin, sonst bliebe die Phrase um sie herum in Stuecken stehen.
        markiert = [False] * len(teil)
        for i in range(len(teil) - _SEED_FENSTER + 1):
            if sum(treffer[i:i + _SEED_FENSTER]) >= _SEED_MIN_TREFFER:
                markiert[i:i + _SEED_FENSTER] = [True] * _SEED_FENSTER
        k = 0
        while k < len(teil):
            if not markiert[k]:
                k += 1
                continue
            ende = k
            while ende + 1 < len(teil) and markiert[ende + 1]:
                ende += 1
            # Raender auf Listenwoerter kuerzen: umgebender Text bleibt stehen.
            von, bis = k, ende
            while not treffer[von]:
                von += 1
            while not treffer[bis]:
                bis -= 1
            spans.append((teil[von].start(), teil[bis].end()))
            k = ende + 1

    for t in tokens:
        if abschnitt and not _SEED_TRENNER.fullmatch(text, abschnitt[-1].end(), t.start()):
            auswerten(abschnitt)
            abschnitt = []
        abschnitt.append(t)
    auswerten(abschnitt)
    return spans


def _bip39_matches(text: str) -> list[str]:
    return [text[a:b] for a, b in _bip39_spans(text)]


def _luhn_ok(s: str) -> bool:
    ds = [int(c) for c in s if c.isdigit()]
    if not (13 <= len(ds) <= 19):
        return False
    total, alt = 0, False
    for d in reversed(ds):
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _finds(pat: re.Pattern, text: str) -> list[str]:
    """Full matched substrings (group 0), so grouped secret patterns don't yield tuples."""
    return [m.group(0) for m in pat.finditer(text)]


def scan_matches(text: str) -> dict[str, list[str]]:
    """Return ``{class: [raw matched substrings]}`` for every DLP class in ``text``."""
    if not text:
        return {}
    out: dict[str, list[str]] = {}
    secret: list[str] = []
    for pat, _ in _SECRET_PATTERNS:
        secret += _finds(pat, text)
    if secret:
        out["secret"] = secret
    formate: list[str] = []
    for pat, _ in _FORMAT_PATTERNS:
        formate += _finds(pat, text)
    if formate:
        out["secret_format"] = formate
    iban = _finds(_IBAN, text)
    if iban:
        out["iban"] = iban
    cc = [m for m in _finds(_CC, text) if _luhn_ok(m)]
    if cc:
        out["credit_card"] = cc
    email = _finds(_EMAIL, text)
    if email:
        out["email"] = email
    tax = _finds(_DE_TAX, text)
    if tax:
        out["de_tax_id"] = tax
    seed = _bip39_matches(text)
    if seed:
        out["seed_phrase"] = seed
    return out


#: Ersetzungsmarken der Muster, die ein Geheimnis am FORMAT erkennen — ein
#: GitHub-Token faengt mit ``ghp_`` an, ein JWT hat drei base64-Teile, ein
#: PEM-Block hat eine Kopfzeile. Wer so etwas findet, hat ein Geheimnis gefunden.
#:
#: Nicht dabei: die ``KEY=VALUE``-Heuristik („alles, was TOKEN/SECRET/PASSWORD
#: heisst, gefolgt von vier Zeichen"). Die ist fuer das MASKIEREN goldrichtig —
#: im Zweifel schwaerzen kostet nichts. Als Ausloeser fuer einen STOPP ist sie
#: falsch: ``GH_TOKEN=nicht-gesetzt`` in einer Protokollzeile oder ein
#: ``API_KEY=<dein-schluessel>`` in einer Anleitung reichen ihr schon.
#:
#: Genau das ist am 2026-08-15 passiert: drei Minuten nach dem Einschalten hielt
#: der Sentinel den Hauptagenten an — wegen einer Zeichenkette, die mit „GH"
#: anfing. Maskieren und Anhalten sind verschiedene Eingriffe und brauchen
#: verschiedene Schwellen.
#:
#: Die #703-Ergaenzungen (``ZUSATZ_PATTERNS`` in log_redaction.py, Klasse
#: ``secret_format``, und ``seed_phrase``) stehen deshalb ABSICHTLICH NICHT in
#: dieser Menge: sie sind frisch und ihre Fehlalarmrate in diesem Betrieb ist
#: unbestaetigt. Sie schwaerzen ab sofort, loesen aber KEINEN Agenten-Stopp
#: aus, bis jemand die Fehlalarmrate gemessen und sie hier bewusst ergaenzt hat.
HIGH_CONFIDENCE_SECRET_MARKERS = frozenset({
    "[REDACTED_KEY_BLOCK]",
    "[REDACTED_API_KEY]",
    "[REDACTED_GH_TOKEN]",
    "[REDACTED_SLACK_TOKEN]",
    "[REDACTED_AWS_KEY]",
    "[REDACTED_JWT]",
    "[REDACTED_TELEGRAM_TOKEN]",
})


def find_high_confidence_secrets(text: str) -> list[str]:
    """Nur Geheimnisse, die am Format erkennbar sind — fuer Eingriffe, die wehtun.

    ``scan_matches`` bleibt unveraendert die richtige Wahl fuers Maskieren: dort
    ist ein Fehlalarm harmlos. Hier geht es um Faelle, in denen eine Fehldeutung
    Arbeit zerstoert, und da zaehlt nur, was sich nicht wegdiskutieren laesst.
    """
    treffer: list[str] = []
    for pat, repl in _SECRET_PATTERNS:
        if repl in HIGH_CONFIDENCE_SECRET_MARKERS:
            treffer += _finds(pat, text)
    return treffer


def classify(text: str) -> dict[str, int]:
    """Return ``{class: match_count}`` for every DLP class present in ``text``."""
    return {c: len(v) for c, v in scan_matches(text).items()}


def mask_sample(s: str) -> str:
    """A recognisable-but-safe excerpt of a matched value: first 2 + *** + last 2 chars.

    Short values reveal less. Used so the audit shows *what* triggered a hit
    (format ``df***as``) without storing the full sensitive value.
    """
    s = (s or "").strip()
    if len(s) <= 4:
        return (s[0] + "***") if s else "***"
    return f"{s[:2]}***{s[-2:]}"


def samples_of(matches: dict[str, list[str]], per_class: int = 3) -> dict[str, list[str]]:
    """Masked excerpts (max ``per_class`` per class) for audit display."""
    return {c: [mask_sample(x) for x in v[:per_class]] for c, v in matches.items()}


def mask(text: str, classes: set[str]) -> str:
    """Return ``text`` with the given classes redacted (keeps a ``[REDACTED_*]`` hint)."""
    out = text
    if "secret" in classes:
        out = redact_logs(out)
    if "secret_format" in classes:
        for pat, repl in _FORMAT_PATTERNS:
            out = pat.sub(repl, out)
    if "iban" in classes:
        out = _IBAN.sub("[REDACTED_IBAN]", out)
    if "credit_card" in classes:
        out = _CC.sub(lambda m: "[REDACTED_CC]" if _luhn_ok(m.group()) else m.group(), out)
    if "email" in classes:
        out = _EMAIL.sub("[REDACTED_EMAIL]", out)
    if "de_tax_id" in classes:
        out = _DE_TAX.sub("[REDACTED_ID]", out)
    if "seed_phrase" in classes:
        # In einem Durchgang ueber die Fundstellen neu aufbauen: ``replace`` je
        # Fund liess bei sich wiederholenden Phrasen die Haelfte stehen und war
        # quadratisch langsam.
        teile, pos = [], 0
        for a, b in _bip39_spans(out):
            teile += [out[pos:a], "[REDACTED_SEED_PHRASE]"]
            pos = b
        out = "".join(teile) + out[pos:]
    return out


# ── Werkzeugaufrufe: Eingaben und Ausgaben ohne Geheimnisse (#911) ──────────
#
# Ein ``curl -H "Authorization: Bearer …"`` stand im Klartext im Verlauf
# (``chat_messages.tool_calls``), damit bei geteilten Nutzern und in jeder
# Sicherung — und vorher schon im Live-Strom im Browser. Maskiert wird NUR die
# Klasse ``secret``: E-Mail-Adressen, IBANs und Nummern sind in Werkzeugaufrufen
# meist der Gegenstand der Arbeit (Mail senden, Buchung anlegen); sie zu
# schwaerzen machte den Verlauf unbrauchbar, ohne ein Geheimnis zu schuetzen.
#
# Maskiert wird je WERT, nicht auf dem JSON-Text: ``Bearer \S+`` frisst sonst ein
# schliessendes ``\"`` mit, und die Oberflaeche bekommt kaputtes JSON (``IN {}``).


def _werte_maskieren(wert, schluessel: str | None = None):
    """Kopie von ``wert`` mit maskierten Strings; die Struktur bleibt gleich.

    Steht ein String unter einem sprechenden Schluessel (``password``,
    ``API_KEY`` …), greift dieselbe ``KEY=VALUE``-Regel wie im Text — sonst sieht
    sie den Schluessel nie, weil der Wert allein harmlos aussieht.
    """
    if isinstance(wert, str):
        neu = redact_logs(wert)
        if neu == wert and schluessel and wert:
            probe = f"{schluessel}={wert}"
            if redact_logs(probe) != probe:
                return "[REDACTED]"
        return neu
    if isinstance(wert, list):
        return [_werte_maskieren(w) for w in wert]
    if isinstance(wert, dict):
        return {k: _werte_maskieren(v, str(k)) for k, v in wert.items()}
    return wert


def werkzeug_eingabe_maskieren(eingabe):
    """Werkzeug-Eingabe ohne Geheimnisse — als JSON-Text oder als Objekt.

    Ein JSON-Text bleibt gueltiges JSON (und zeichengleich, wenn nichts zu
    maskieren war). Text, der kein JSON ist (Altbestand, abgeschnitten), wird als
    Text maskiert.
    """
    if isinstance(eingabe, str):
        try:
            objekt = json.loads(eingabe)
        except ValueError:
            return redact_logs(eingabe)
        neu = _werte_maskieren(objekt)
        return eingabe if neu == objekt else json.dumps(neu, ensure_ascii=False)
    return _werte_maskieren(eingabe)


def werkzeug_liste_maskieren(werkzeuge):
    """Eine Liste von Werkzeug-Eintraegen (``{"tool","input","output",…}``)."""
    if not isinstance(werkzeuge, list):
        return werkzeuge
    aus = []
    for eintrag in werkzeuge:
        if isinstance(eintrag, dict):
            eintrag = dict(eintrag)
            if "input" in eintrag:
                eintrag["input"] = werkzeug_eingabe_maskieren(eintrag["input"])
            if eintrag.get("output"):
                eintrag["output"] = _werte_maskieren(eintrag["output"])
        aus.append(eintrag)
    return aus


def werkzeug_ereignis_maskieren(ereignis):
    """Ein Strom-Ereignis (``{"type","data",…}``) fuer den Browser bzw. die Ablage.

    ``tool_call`` → Eingabe, ``tool_result`` → Ausgabe, ``done``/``result`` →
    die mitgelieferte Werkzeugliste. Alles andere kommt unveraendert (dasselbe
    Objekt) zurueck.
    """
    if not isinstance(ereignis, dict):
        return ereignis
    typ = ereignis.get("type")
    daten = ereignis.get("data")
    if not isinstance(daten, dict):
        return ereignis
    if typ == "tool_call" and "input" in daten:
        neu = {**daten, "input": werkzeug_eingabe_maskieren(daten["input"])}
    elif typ == "tool_result" and "content" in daten:
        neu = {**daten, "content": _werte_maskieren(daten["content"])}
    elif typ in ("done", "result") and isinstance(daten.get("tool_calls"), list):
        neu = {**daten, "tool_calls": werkzeug_liste_maskieren(daten["tool_calls"])}
    else:
        return ereignis
    return {**ereignis, "data": neu}


def werkzeug_ereignis_text_maskieren(roh: str) -> str:
    """Wie ``werkzeug_ereignis_maskieren``, fuer ein Ereignis als JSON-Text.

    Zeichengleich zurueck, wenn nichts zu maskieren war oder es kein Ereignis ist.
    """
    try:
        ereignis = json.loads(roh)
    except (TypeError, ValueError):
        return roh
    neu = werkzeug_ereignis_maskieren(ereignis)
    return roh if neu == ereignis else json.dumps(neu)


@dataclass
class DlpVerdict:
    allowed: bool
    output: str
    classes: dict[str, int]           # matched class -> count
    actions: dict[str, str]           # matched class -> resolved action
    effective: str                    # overall (highest-severity) action

    @property
    def blocked(self) -> bool:
        return not self.allowed


def resolve_actions(classes: dict[str, int], rules: list) -> dict[str, str]:
    """Resolve the effective action per matched class from DlpRule rows.

    Precedence: agent-specific rule (already pre-filtered by caller to this agent or
    global) > global rule > built-in default. ``rules`` is a list of DlpRule with
    attributes pii_class, agent_id, action, enabled.
    """
    by_agent: dict[str, str] = {}
    by_global: dict[str, str] = {}
    for r in rules:
        if not getattr(r, "enabled", True):
            continue
        if r.action not in VALID_ACTIONS:
            continue
        if r.agent_id is None:
            by_global[r.pii_class] = r.action
        else:
            by_agent[r.pii_class] = r.action
    out: dict[str, str] = {}
    for cls in classes:
        out[cls] = by_agent.get(cls) or by_global.get(cls) or DEFAULT_ACTIONS.get(cls, "log")
    return out


def decide(text: str, classes: dict[str, int], actions: dict[str, str]) -> DlpVerdict:
    """Apply resolved actions to produce the final verdict (pure)."""
    if not classes:
        return DlpVerdict(True, text, {}, {}, "allow")
    effective = max(actions.values(), key=lambda a: ACTION_SEVERITY.get(a, 0))
    if effective == "block":
        return DlpVerdict(False, "", classes, actions, "block")
    to_mask = {c for c, a in actions.items() if a == "mask"}
    output = mask(text, to_mask) if to_mask else text
    return DlpVerdict(True, output, classes, actions, effective)


_EVENT_BY_ACTION = {"block": "dlp_blocked", "mask": "dlp_masked", "log": "dlp_flagged"}


async def evaluate_egress(text: str, *, agent_id: str | None = None, channel: str = "unknown") -> DlpVerdict:
    """Scan one outbound message and enforce the configured DLP policy.

    Opens its own DB session (safe to call from Redis listeners). Passthrough when
    ``dlp_enabled`` is not true. Fail-open on any internal error.
    """
    if not text:
        return DlpVerdict(True, text, {}, {}, "allow")
    try:
        matches = scan_matches(text)
        classes = {c: len(v) for c, v in matches.items()}
        if not classes:
            return DlpVerdict(True, text, {}, {}, "allow")

        from sqlalchemy import select

        from app.db.session import async_session_factory
        from app.models.audit_log import AuditLog
        from app.models.dlp_rule import DlpRule
        from app.services.settings_service import SettingsService

        async with async_session_factory() as db:
            svc = SettingsService(db)
            enabled = (await svc.get("dlp_enabled")) in ("true", "1", True)
            if not enabled:
                return DlpVerdict(True, text, classes, {}, "allow")

            rules = (await db.execute(
                select(DlpRule).where(
                    (DlpRule.agent_id == agent_id) | (DlpRule.agent_id.is_(None))
                )
            )).scalars().all()
            actions = resolve_actions(classes, rules)
            verdict = decide(text, classes, actions)

            if verdict.effective in _EVENT_BY_ACTION:
                db.add(AuditLog(
                    agent_id=agent_id or "system",
                    event_type=_EVENT_BY_ACTION[verdict.effective],
                    command=channel,
                    outcome="blocked" if verdict.blocked else "success",
                    meta={
                        "classes": classes,
                        "actions": actions,
                        "channel": channel,
                        # masked excerpts (df***as) so the audit shows WHAT triggered
                        # the hit, without ever storing the full sensitive value.
                        "samples": samples_of(matches),
                    },
                ))
                await db.commit()
            return verdict
    except Exception as e:  # noqa: BLE001 — never wedge outbound messaging
        logger.warning("DLP evaluate_egress failed (fail-open): %s", e)
        return DlpVerdict(True, text, {}, {}, "allow")
