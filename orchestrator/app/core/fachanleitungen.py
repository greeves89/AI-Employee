"""Zugewiesene Fach-Skills verlässlich anwenden — EINE Stelle für alle Laufzeiten.

Befund aus der Abnahme von v1.362.1: Ein Buchhaltungs-Agent mit zugewiesenem
Skill ``buchhaltung-vorkontieren`` schrieb einen DATEV-Buchungsstapel mit
Nettobetrag bei BU 9, obwohl der Skill die Brutto-Regel samt Prüfschritt
enthält — er hatte ihn nie geladen. Der Angebots-Agent erzeugte zuerst ein PDF
mit Platzhaltern und fragte danach. Beides, weil der Skill das Modell nicht
erreichte (im Chat gar nicht gelistet, in Aufträgen ohne ladbare ID) und weil
die Beschreibung nicht sagte, WANN er gilt.

Deshalb hier:

* Jeder mitgelieferte Skill nennt am Ende seiner Beschreibung seine Auslöser
  (``Auslöser: DATEV, Buchungsstapel, …``) — für das Modell lesbar UND für
  ``passende`` auswertbar.
* ``fuer_agent`` baut aus den zugewiesenen Skills eines Agenten den Block, den
  jede Laufzeit (Claude Code, Codex, Custom-LLM) gleich bekommt: eine harte
  Regel, die Liste mit ladbarer ID und Auslösern, und — wenn der Auftrag zu
  einem Auslöser passt — die Anleitung gleich vollständig. Das Modell muss sich
  dann nicht erst entscheiden, sie zu laden.

Der Endpunkt dazu ist ``POST /skills/agent/fachanleitungen``; der Agent ruft
ihn über ``runner_hooks.fachanleitungen`` auf (Aufträge und jede Chatnachricht).
"""

from __future__ import annotations

import re

#: Höchstens so viele Anleitungen werden je Auftrag vollständig mitgegeben —
#: sie sind je 6–12 KB groß, mehr würde den Auftrag selbst verdrängen.
MAX_ANLEITUNGEN = 2

#: Größere Skills werden nur gelistet, nicht eingebettet (Laden mit skill_install).
MAX_ANLEITUNG_ZEICHEN = 20000

_AUSLOESER = re.compile(r"Auslöser\s*:\s*(?P<liste>.+)$", re.S | re.I)
_UMLAUTE = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "ss"})


def ausloeser(beschreibung: str | None) -> list[str]:
    """Die Auslöser aus einer Skill-Beschreibung (``… Auslöser: a, b, c.``)."""
    treffer = _AUSLOESER.search(beschreibung or "")
    if not treffer:
        return []
    liste = treffer.group("liste").strip().rstrip(".")
    return [w.strip(" „“\"'") for w in liste.split(",") if w.strip(" „“\"'")]


def _falten(text: str) -> str:
    return (text or "").lower().translate(_UMLAUTE)


def _stamm(ausloeser_text: str) -> str:
    """Ausloeser ohne Flexionsendung am letzten Wort, damit „übersetzen“ auch
    „übersetze“ findet und „Belege“ auch „Beleg“. Verglichen wird am Wortanfang."""
    s = _falten(ausloeser_text).strip()
    if len(s) > 6 and s.endswith("en"):
        return s[:-2]
    if len(s) > 5 and s.endswith("e"):
        return s[:-1]
    return s


def _passt(stamm: str, text_gefaltet: str) -> bool:
    if not stamm:
        return False
    return re.search(r"(?<!\w)" + re.escape(stamm), text_gefaltet) is not None


def passende(skills: list[dict], auftrag: str) -> list[dict]:
    """Die zugewiesenen Skills, deren Auslöser (oder Name) im Auftrag vorkommt.

    Nur Skills MIT Auslösern kommen in Frage: Eine Beschreibung ohne sie ist
    nicht dafür geschrieben, automatisch zu greifen. Reihenfolge = Zahl der
    Treffer, dann Reihenfolge der Zuweisung.
    """
    text = _falten(auftrag)
    if not text.strip():
        return []
    bewertet: list[tuple[int, int, dict]] = []
    for nr, s in enumerate(skills):
        woerter = ausloeser(s.get("description"))
        if not woerter:
            continue
        stamme = {_stamm(w) for w in woerter} | {_falten(s.get("name") or "")}
        zahl = sum(1 for st in stamme if _passt(st, text))
        if zahl:
            bewertet.append((-zahl, nr, s))
    return [s for _, _, s in sorted(bewertet, key=lambda t: (t[0], t[1]))]


def _kurz(beschreibung: str, laenge: int = 220) -> str:
    text = " ".join((beschreibung or "").split())
    return text if len(text) <= laenge else text[: laenge - 1].rstrip() + "…"


def _listenzeile(s: dict) -> str:
    woerter = ausloeser(s.get("description"))
    if woerter:
        wann = "Auslöser: " + ", ".join(woerter)
        # Das WANN zählt hier, nicht das WAS: steht ein „Nutzen, wenn …“-Satz in der
        # Beschreibung, nur ihn — sonst die gekürzte Beschreibung ohne Auslöser.
        ohne = _AUSLOESER.sub("", s.get("description") or "")
        wenn = re.search(r"Nutzen, wenn[^.]*\.", ohne)
        was = _kurz(wenn.group(0) if wenn else ohne, 220)
        zeile = f"{was} {wann}" if was else wann
    else:
        zeile = _kurz(s.get("description") or "")
    return f"  • {s['name']} — skill_install(skill_id={s['id']}) — {zeile}"


REGEL = (
    "Du hast geprüfte Fachanleitungen (Skills) für deine Aufgaben. PFLICHT: Passt ein "
    "Auftrag zu einer Anleitung unten (Auslöser oder Thema), arbeitest du NICHT aus dem "
    "Gedächtnis. Du lädst sie ZUERST mit `skill_install(skill_id=…)` und befolgst sie "
    "Schritt für Schritt — auch ihre Rückfragen (z. B. fehlende Firmendaten EINMAL "
    "erfragen, bevor ein Kundendokument entsteht) und ihre Prüfschritte, BEVOR du ein "
    "Ergebnis (Datei, Export, Kundendokument) erzeugst. Steht eine Anleitung weiter unten "
    "unter „FACHANLEITUNG FÜR DIESEN AUFTRAG“, ist sie bereits geladen. Nach der Arbeit: "
    "`skill_rate(skill_id=…)`."
)


def _liste(skills: list[dict]) -> str:
    zeilen = ["", "=== DEINE FACHANLEITUNGEN (zugewiesene Skills) ===", REGEL, ""]
    zeilen += [_listenzeile(s) for s in skills]
    zeilen += ["=== ENDE FACHANLEITUNGEN ===", ""]
    return "\n".join(zeilen)


def _anleitung(s: dict) -> str:
    return (
        f"\n=== FACHANLEITUNG FÜR DIESEN AUFTRAG: {s['name']} (skill_id={s['id']}) ===\n"
        "Dein Auftrag passt zu dieser zugewiesenen Fachanleitung; sie ist hiermit geladen. "
        "Befolge sie vollständig — Rückfragen, Reihenfolge und Prüfschritte —, bevor du "
        "ein Ergebnis ausgibst. Weicht ein Ergebnis von ihr ab, korrigiere es, statt es "
        f"auszuliefern. Nach der Arbeit: skill_rate(skill_id={s['id']}, …).\n\n"
        f"{(s.get('content') or '').strip()}\n"
        "=== ENDE FACHANLEITUNG ===\n"
    )


def fuer_agent(skills: list[dict], auftrag: str = "") -> dict:
    """Block für die zugewiesenen Skills eines Agenten und einen Auftrag.

    ``skills``: dicts mit ``id``, ``name``, ``description``, ``content``.
    Rückgabe: ``prompt`` (Regel + Liste; leer ohne Skills) und ``anleitungen``
    (je ``id``, ``name``, ``text``) für die zum Auftrag passenden Skills.
    """
    if not skills:
        return {"prompt": "", "anleitungen": []}
    treffer = [
        s for s in passende(skills, auftrag)
        if len(s.get("content") or "") <= MAX_ANLEITUNG_ZEICHEN
    ][:MAX_ANLEITUNGEN]
    return {
        "prompt": _liste(skills),
        "anleitungen": [{"id": s["id"], "name": s["name"], "text": _anleitung(s)} for s in treffer],
    }
