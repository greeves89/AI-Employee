"""Anonymisierer vor dem Modell — reine Funktionen, ohne mitmproxy (Entscheidung 09.11.2026).

Für Agenten, bei denen er eingeschaltet ist, ersetzt der Egress-Proxy Personendaten in
jeder Anfrage an den Modellanbieter durch Platzhalter und setzt sie in der Antwort wieder
ein. Der Anbieter sieht die Werte nie; Agent und Werkzeuge arbeiten mit den echten.

Grundsätze:

* **Platzhalter nur aus ASCII** (``[EMAIL-3f9a1c2b]``) und **Werte nur aus „sicheren“
  Zeichen** (keine Anführungszeichen, Backslashes, Steuerzeichen). Dann sieht ein Wert in
  jeder JSON-Ebene gleich aus — auch in Werkzeug-Argumenten (``partial_json`` ist JSON in
  einem JSON-String) — und das Einsetzen braucht kein Escaping.
* **Deterministisch:** gleicher Wert → gleicher Platzhalter (HMAC je Agent). Der ganze
  Verlauf geht bei jedem Zug mit; der Prompt-Cache des Anbieters bleibt wirksam.
* **Keine Ablage:** Die Zuordnung Platzhalter → Wert entsteht aus der jeweiligen Anfrage.
  Das Modell kann nur Platzhalter nennen, die es in dieser Anfrage gesehen hat.
* **Strom:** Das Modell schreibt token­weise. Ein Platzhalter kommt zerteilt über mehrere
  Ereignisse an. ``SseEinsetzer`` versteht die Ereignisströme (Anthropic, OpenAI/Codex),
  hält je Textblock einen möglichen Platzhalter-Anfang zurück und gibt ihn am Blockende aus.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re

# ── Erkennung ──────────────────────────────────────────────────────────────
#: Zeichen, aus denen ein ersetzbarer Wert bestehen darf (siehe oben). Kein ``"``/``\\``.
_SICHER = re.compile(r"[^\"\\\x00-\x1f\x7f]+")

# Alle Wiederholungen begrenzt (RFC: Lokalteil ≤ 64, Label ≤ 63): unbegrenzt war das Muster
# quadratisch — 40 000 ``%`` ohne ``@`` brauchten 1,7 s, 1 MB Minuten (Sicherheitsreview).
_EMAIL = re.compile(r"(?<![\w.+%-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){0,8}"
                    r"\.[A-Za-z]{2,24}(?![\w-])")
# IBAN: Länderkennung, Prüfziffern, 11–30 Zeichen, mit oder ohne Vierergruppen
_IBAN = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?(?![A-Za-z0-9])")
# Kreditkarte: Folgen von Zifferngruppen (Leerzeichen/Bindestrich); darin wird jedes Fenster
# aus ganzen Gruppen mit 13–19 Ziffern und gültiger Luhn-Summe gesucht (``_karten``) — sonst
# verdeckte eine Bestellnummer davor die Karte („12345 67890 4111 1111 1111 1111“).
_ZIFFERNFOLGE = re.compile(r"(?<![\d-])\d{1,19}(?:[ -]\d{1,19}){0,9}(?![\d-])")
# Telefon: international (+49 …, 0049 …) oder national mit führender 0, mind. 7 Ziffern
_TELEFON = re.compile(r"(?<![\w+])(?:\+\d{1,3}|00\d{1,3}|0)(?:[ /()-]*\d){6,14}(?!\d)")


def _iban_ok(roh: str) -> bool:
    s = roh.replace(" ", "")
    if not 15 <= len(s) <= 34:
        return False
    zahl = "".join(str(int(c, 36)) for c in s[4:] + s[:4])
    return int(zahl) % 97 == 1


def _luhn_ok(roh: str) -> bool:
    ziffern = [int(c) for c in roh if c.isdigit()]
    if not 13 <= len(ziffern) <= 19:
        return False
    summe = 0
    for i, z in enumerate(reversed(ziffern)):
        if i % 2:
            z *= 2
            if z > 9:
                z -= 9
        summe += z
    return summe % 10 == 0


def _karten(s: str):
    """(Anfang, Ende) jeder Kartennummer: längstes Fenster aus ganzen Gruppen je Folge."""
    for m in _ZIFFERNFOLGE.finditer(s):
        gruppen = [(g.start() + m.start(), g.end() + m.start()) for g in re.finditer(r"\d+", m.group(0))]
        # Ziffern bis einschließlich Gruppe j (Präfixsummen): nur Fenster mit 13–19 Ziffern prüfen
        bis = [0]
        for a, e in gruppen:
            bis.append(bis[-1] + e - a)
        i = 0
        while i < len(gruppen):
            treffer = None
            for j in range(len(gruppen) - 1, i - 1, -1):
                ziffern = bis[j + 1] - bis[i]
                if ziffern < 13:
                    break
                if ziffern > 19:
                    continue
                a, e = gruppen[i][0], gruppen[j][1]
                if _luhn_ok(s[a:e]):
                    treffer = (a, e, j)
                    break
            if treffer:
                yield treffer[0], treffer[1]
                i = treffer[2] + 1
            else:
                i += 1


#: (Art, Muster, Zusatzprüfung) — Reihenfolge = Vorrang bei Überlappung. Karten: ``_karten``.
_ARTEN = (
    ("EMAIL", _EMAIL, None),
    ("IBAN", _IBAN, _iban_ok),
    ("TELEFON", _TELEFON, lambda s: sum(c.isdigit() for c in s) >= 7),
)

PLATZHALTER = re.compile(r"\[(?:EMAIL|IBAN|KARTE|TELEFON|NAME)-(?:[0-9a-f]{12}|[0-9a-f]{8})\]")
#: Längster möglicher Platzhalter — mehr hält der Strom nie zurück.
_MAX_PLATZHALTER = len("[TELEFON-000000000000]")
#: Kennung: 8 Hex (32 Bit); trifft ein ANDERER Wert dieselbe, 12 Hex (Sicherheitsreview:
#: mit 6 Hex vertauschten Kollisionen still zwei Werte — eine Mail ginge an den falschen).
_KURZ, _LANG = 8, 12


def _norm(wert: str) -> str:
    return " ".join(wert.split()).casefold()


def platzhalter(schluessel: str, art: str, wert: str, laenge: int = _KURZ) -> str:
    """Deterministisch je Agent: gleicher Wert (Groß/Klein egal) → gleicher Platzhalter."""
    kennung = hmac.new(schluessel.encode(), f"{art}:{_norm(wert)}".encode(), hashlib.sha256).hexdigest()[:laenge]
    return f"[{art}-{kennung}]"


def _begriffs_muster(begriffe: list[str]) -> re.Pattern | None:
    sauber = sorted({b.strip() for b in begriffe if b and len(b.strip()) >= 3
                     and _SICHER.fullmatch(b.strip())}, key=len, reverse=True)
    if not sauber:
        return None
    return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(b) for b in sauber) + r")(?!\w)", re.IGNORECASE)


#: Blöcke, die der Anbieter signiert oder verschlüsselt (Denken): in beide Richtungen
#: byte­gleich lassen — sonst lehnt er sie im nächsten Zug ab. Das Modell hat nur Platzhalter
#: gesehen; echte Werte stehen darin nicht.
_UNANGETASTET = ("thinking", "thinking_delta", "redacted_thinking", "reasoning")


def _unangetastet(d: dict) -> bool:
    return d.get("type") in _UNANGETASTET


#: Lange Zeichenketten ohne Leerzeichen aus dem base64-Alphabet (Bilder, Dateien) nie anfassen.
_BLOB = re.compile(r"[A-Za-z0-9+/=_-]{256,}")


class Anonymisierer:
    """Ersetzt in Texten erkannte Werte und merkt sich die Zuordnung Platzhalter → Wert."""

    def __init__(self, schluessel: str, begriffe: list[str] | None = None):
        self.schluessel = schluessel
        self.begriffe = _begriffs_muster(begriffe or [])
        self.zuordnung: dict[str, str] = {}

    def text(self, s: str) -> str:
        if not s or _BLOB.fullmatch(s):
            return s
        treffer: list[tuple[int, int, str]] = []          # (Anfang, Ende, Art)
        if self.begriffe:
            treffer += [(m.start(), m.end(), "NAME") for m in self.begriffe.finditer(s)]
        hat_at = "@" in s
        hat_ziffer = any(c.isdigit() for c in s)
        for art, muster, pruefung in _ARTEN:
            # Schnelle Vorprüfung: ohne „@“ keine Adresse, ohne Ziffer keine Nummer
            if (art == "EMAIL" and not hat_at) or (art != "EMAIL" and not hat_ziffer):
                continue
            for m in muster.finditer(s):
                wert = m.group(0)
                if pruefung and not pruefung(wert):
                    continue
                treffer.append((m.start(), m.end(), art))
        if hat_ziffer:
            treffer += [(a, e, "KARTE") for a, e in _karten(s)]
        if not treffer:
            return s
        # Überlappungen: früher Beginn, dann längster Treffer gewinnt
        treffer.sort(key=lambda t: (t[0], -(t[1] - t[0])))
        teile, pos = [], 0
        for anfang, ende, art in treffer:
            if anfang < pos:
                continue
            wert = s[anfang:ende]
            if not _SICHER.fullmatch(wert) or PLATZHALTER.fullmatch(wert):
                continue
            p = platzhalter(self.schluessel, art, wert)
            if p in self.zuordnung and _norm(self.zuordnung[p]) != _norm(wert):
                p = platzhalter(self.schluessel, art, wert, _LANG)
            self.zuordnung.setdefault(p, wert)
            teile += [s[pos:anfang], p]
            pos = ende
        teile.append(s[pos:])
        return "".join(teile)

    def json(self, daten):
        """Alle Zeichenketten-WERTE einer JSON-Struktur (Schlüssel bleiben)."""
        if isinstance(daten, str):
            return self.text(daten)
        if isinstance(daten, list):
            return [self.json(x) for x in daten]
        if isinstance(daten, dict):
            if _unangetastet(daten):
                return daten
            return {k: self.json(v) for k, v in daten.items()}
        return daten


def anfrage_anonymisieren(koerper: bytes, schluessel: str, begriffe: list[str]) -> tuple[bytes, dict[str, str]]:
    """JSON-Körper einer Anfrage an den Modellanbieter → (neuer Körper, Zuordnung).

    Kein gültiges JSON → ``ValueError`` (der Proxy sperrt dann: lieber keine Anfrage als
    eine mit Personendaten).
    """
    daten = json.loads(koerper.decode("utf-8"))
    a = Anonymisierer(schluessel, begriffe)
    neu = a.json(daten)
    if not a.zuordnung:
        return koerper, {}
    return json.dumps(neu, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), a.zuordnung


def einsetzen(text: str, zuordnung: dict[str, str]) -> str:
    if not zuordnung or "[" not in text:
        return text
    return PLATZHALTER.sub(lambda m: zuordnung.get(m.group(0), m.group(0)), text)


def einsetzen_json(daten, zuordnung: dict[str, str]):
    if isinstance(daten, str):
        return einsetzen(daten, zuordnung)
    if isinstance(daten, list):
        return [einsetzen_json(x, zuordnung) for x in daten]
    if isinstance(daten, dict):
        if _unangetastet(daten):
            return daten
        return {k: einsetzen_json(v, zuordnung) for k, v in daten.items()}
    return daten


def _anfang_offen(s: str) -> int:
    """Länge des Endstücks von ``s``, das der Anfang eines Platzhalters sein kann."""
    start = s.rfind("[", max(0, len(s) - _MAX_PLATZHALTER))
    if start < 0:
        return 0
    rest = s[start:]
    if "]" in rest:
        return 0
    if re.fullmatch(r"\[(?:[A-Z]{0,7}|(?:EMAIL|IBAN|KARTE|TELEFON|NAME)-[0-9a-f]{0,12})", rest):
        return len(rest)
    return 0


class TextStrom:
    """Setzt Platzhalter in einem token­weise ankommenden Text ein (ein Block)."""

    def __init__(self, zuordnung: dict[str, str]):
        self.zuordnung = zuordnung
        self.rest = ""

    def weiter(self, stueck: str) -> str:
        daten = einsetzen(self.rest + stueck, self.zuordnung)
        k = _anfang_offen(daten)
        self.rest = daten[len(daten) - k:] if k else ""
        return daten[:len(daten) - k]

    def ende(self) -> str:
        r, self.rest = self.rest, ""
        return r


# ── Ereignisströme (text/event-stream) ────────────────────────────────────
#: Felder, in denen Text token­weise kommt — je Anbieter.
_DELTA_FELDER = ("text", "partial_json", "thinking", "delta", "arguments")


def _block_schluessel(ereignis: dict) -> str | None:
    """Wozu ein Teilstück gehört: Anthropic ``index``, OpenAI ``item_id``/``output_index``."""
    for k in ("item_id", "output_index", "index"):
        if k in ereignis:
            teil = ereignis.get("content_index", ereignis.get("summary_index", ""))
            return f"{k}:{ereignis[k]}:{teil}"
    return None


def _ist_teilstueck(ereignis: dict) -> tuple[str, str] | None:
    """(Pfad, Feld) eines token­weisen Textstücks — sonst None."""
    typ = str(ereignis.get("type") or "")
    if typ == "content_block_delta" and isinstance(ereignis.get("delta"), dict):   # Anthropic
        # „thinking“ bleibt mit Platzhaltern: Der Block ist signiert und kommt im nächsten
        # Zug zurück — nur byte­gleich gilt die Signatur (Sicherheitsreview). Sichtbar
        # wird Denken ohnehin nur als Zusammenfassung.
        for feld in ("text", "partial_json"):
            if isinstance(ereignis["delta"].get(feld), str):
                return ("delta", feld)
    if typ.endswith(".delta") and isinstance(ereignis.get("delta"), str):           # OpenAI Responses
        return ("", "delta")
    return None


def _ist_blockende(ereignis: dict) -> bool:
    typ = str(ereignis.get("type") or "")
    return typ == "content_block_stop" or typ.endswith(".done")


class SseEinsetzer:
    """Setzt Platzhalter in einem Server-Sent-Events-Strom ein, über Ereignisgrenzen hinweg.

    Als ``flow.response.stream`` nutzbar: bekommt Bytes, gibt Bytes zurück. Zerlegt den
    Strom in Ereignisse (Leerzeile), behandelt ``data:``-JSON:

    * Teilstücke (``content_block_delta``, ``*.delta``): je Block ein ``TextStrom``.
    * Blockende (``content_block_stop``, ``*.done``): vorher den zurückgehaltenen Rest als
      eigenes Teilstück ausgeben; ``*.done`` mit vollem Text wird vollständig ersetzt.
    * Alles andere: alle Zeichenketten vollständig ersetzen.
    """

    def __init__(self, zuordnung: dict[str, str]):
        self.zuordnung = zuordnung
        self.puffer = b""
        self.stroeme: dict[str, TextStrom] = {}
        self.letztes_teilstueck: dict[str, dict] = {}

    def _rest_als_teilstueck(self, schluessel: str) -> str:
        strom = self.stroeme.pop(schluessel, None)
        vorlage = self.letztes_teilstueck.pop(schluessel, None)
        if not strom or vorlage is None:
            return ""
        rest = strom.ende()
        if not rest:
            return ""
        e = json.loads(json.dumps(vorlage))
        pfad, feld = _ist_teilstueck(e)
        (e[pfad] if pfad else e)[feld] = rest
        return self._ereignis_text(e, vorlage.get("__sse_event__"))

    @staticmethod
    def _ereignis_text(e: dict, name: str | None) -> str:
        e = {k: v for k, v in e.items() if k != "__sse_event__"}
        kopf = f"event: {name}\n" if name else ""
        return f"{kopf}data: {json.dumps(e, ensure_ascii=False, separators=(',', ':'))}\n\n"

    def _ereignis(self, roh: str) -> str:
        zeilen = roh.split("\n")
        daten_zeilen = [z[5:].lstrip() for z in zeilen if z.startswith("data:")]
        name = next((z[6:].strip() for z in zeilen if z.startswith("event:")), None)
        if not daten_zeilen:
            return roh + "\n\n"
        try:
            e = json.loads("\n".join(daten_zeilen))
        except ValueError:
            return einsetzen(roh, self.zuordnung) + "\n\n"
        if not isinstance(e, dict):
            return einsetzen(roh, self.zuordnung) + "\n\n"
        schluessel = _block_schluessel(e)
        teil = _ist_teilstueck(e)
        if teil and schluessel:
            pfad, feld = teil
            ziel = e[pfad] if pfad else e
            strom = self.stroeme.setdefault(schluessel, TextStrom(self.zuordnung))
            ziel[feld] = strom.weiter(ziel[feld])
            self.letztes_teilstueck[schluessel] = {**json.loads(json.dumps(e)), "__sse_event__": name}
            return self._ereignis_text(e, name)
        vorweg = ""
        if _ist_blockende(e):
            if schluessel:
                vorweg = self._rest_als_teilstueck(schluessel)
            # OpenAI: zu einem „.done“ gehören alle Teilstücke desselben Elements (genauer
            # Vergleich bis zum Trenner — „index:1“ darf „index:10“ nicht treffen).
            if schluessel:
                basis = schluessel.rsplit(":", 1)[0] + ":"
                for k in [k for k in self.stroeme if k.startswith(basis)]:
                    vorweg += self._rest_als_teilstueck(k)
        return vorweg + self._ereignis_text(einsetzen_json(e, self.zuordnung), name)

    def __call__(self, stueck: bytes) -> bytes:
        """Ein Stück des Stroms. Ein leeres Stück ist KEIN sicheres Ende (mitmproxy ruft auch
        mitten im Strom mit ``b""`` auf, siehe ``egress_logik.StromMaske``) — zurückgehalten
        wird nur bis zum Blockende, und das kommt in jedem vollständigen Strom."""
        self.puffer += stueck
        roh = self.puffer.replace(b"\r\n", b"\n")
        *fertig, offen = roh.split(b"\n\n")
        self.puffer = offen
        return "".join(self._ereignis(f.decode("utf-8")) for f in fertig).encode("utf-8")
