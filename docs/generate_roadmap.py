#!/usr/bin/env python3
"""Die beiden Roadmap-Bilder für die README erzeugen.

Bis 1.169 gab es dieses Skript nicht: die Bilder wurden bei jedem Release von Hand
neu gebaut. Das ging jedes Mal etwas anders aus — Abstände, Schriftgrößen,
Reihenfolge — und wer den Stand nur aktualisieren wollte, musste erst die Machart
rekonstruieren. Der Inhalt steht deshalb jetzt oben als Daten, das Zeichnen darunter
als Code.

    python3 docs/generate_roadmap.py

Schreibt ``docs/assets/roadmap.png`` und ``docs/assets/vision-roadmap.png``.
Braucht Pillow und die SF-Pro-Schriften von macOS; fehlen sie, wird auf Arial
ausgewichen (die Bilder sehen dann etwas anders aus, entstehen aber).
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs/assets"

VERSION = (ROOT / "VERSION").read_text().strip()

BG = (10, 10, 12)
CARD = (22, 22, 26)
CARD_LINE = (38, 38, 44)
TEXT = (238, 238, 242)
MUTED = (150, 150, 160)
GREEN = (52, 211, 153)
AMBER = (251, 191, 36)
BLUE = (96, 165, 250)
VIOLET = (167, 139, 250)

# ── Inhalt ───────────────────────────────────────────────────────────────────
# Ein Eintrag ist (Titel, eine Zeile Erklaerung). Die Erklaerung sagt, WARUM es
# jemanden interessiert — eine Liste aus Substantiven liest niemand.

SHIPPED = [
    ("Selbstheilung gescheiterter Aufgaben",
     "Zeitablauf wird wiederholt, falsches Kennwort nie. Danach der Mensch, mit Verlauf."),
    ("Nachfragen statt raten",
     "Der Agent meldet seine Sicherheit; die Schwelle gehört dem Betreiber, nicht ihm."),
    ("Golden-Tests als Update-Gatter",
     "Ein Rückschritt in der Rolle blockiert das Update — reproduzierbar, ohne Modell-Schiedsrichter."),
    ("Eskalations-Posteingang",
     "Zu unsicher und endgültig gescheitert landen an EINER Stelle."),
    ("Jedes Symbol, jede Farbe, ein Schlagwort",
     "Statt 18 Symbolen der ganze lucide-Satz; Übersicht mit Suche, Filter, Sortierung."),
    ("Composer im Claude-Code-Zuschnitt",
     "Eingabe oben, Bedienung darunter, Kontextring neben dem Absenden. Befehle mit „/“."),
    ("SAML 2.0 + IdP-Gruppen-Zuordnung",
     "Neben OIDC. Signaturprüfung über xmlsec — XML-DSig wird nicht von Hand geprüft."),
    ("Teams · Slack · WhatsApp",
     "Ein Eingang für alle Kanäle. Teams in drei Richtungen, inklusive Agent im Termin."),
    ("Agent mit Stimme im Teams-Termin",
     "Beitreten, sprechen, antworten — service-hosted media, ohne .NET-Modul."),
    ("PWA + Browser-Meldungen",
     "Installierbar; Push für den Empfänger verschlüsselt, ohne neue Abhängigkeit."),
    ("Vertretungskette repariert",
     "Die Team-Lead-Stufe hatte über ihre gesamte Lebensdauer nie ausgelöst."),
    ("Autonomiestufe → Container-sudo",
     "Ein L1-Agent bekam Paket-Rechte, die er nie haben sollte."),
]

IN_PROGRESS = [
    ("Datei-Übergabe zwischen Agenten",
     "Die Nachricht gibt es; Dateien laufen noch über das geteilte Volume."),
    ("Vertretungs-Übung am Livesystem",
     "Ende-zu-Ende gegen echtes SQL getestet — der Probelauf auf der Anlage fehlt."),
    ("Kontext von Hand beschneiden",
     "Aus #538. Was genau editierbar sein soll, ist noch nicht entschieden."),
]

NEXT = [
    ("DATEV / Lexware-Export",
     "DACH-Steuer-Workflows. Vom Nutzer ausdrücklich zurückgestellt."),
    ("Schema nur über Migrationen",
     "41 CREATE-TABLE-Anweisungen laufen noch beim Start, neben der Alembic-Historie."),
    ("Lokales Test-Setup dokumentieren",
     "21 Testdateien lassen sich ohne Zusatzpakete nicht einlesen."),
]

FOOTER = (
    "Testlage: 1804 Tests grün, 1 vorbestehend rot (Nova Sonic, fehlendes Bedrock-Modul "
    "lokal). Neu in diesem Stand: Selbstheilung, Konfidenz-Routing, Golden-Tests, "
    "Symbol-/Schlagwortwahl, Composer."
)

SUBTITLE = (
    "Was zuletzt ausgeliefert wurde, was gerade läuft und was als Nächstes kommt. "
    "Die drei großen Punkte dieses Stands beantworten dieselbe Frage: Wann darf ein "
    "Agent allein weitermachen — und wann muss ein Mensch ran?"
)

# Vision-Roadmap: die vier Säulen, je Punkt mit Zustand.
PILLARS = [
    ("Vertrauen & Kontrolle", VIOLET, [
        ("Decision-Trace / Zeitreise", True),
        ("DLP-Ausgangsfilter", True),
        ("Budgets & Kostendeckel", True),
        ("Konfidenz-Routing", True),
        ("Eskalations-Posteingang", True),
    ]),
    ("Zuverlässigkeit", GREEN, [
        ("Selbstheilung mit Strategie", True),
        ("Golden-Tests als Gatter", True),
        ("Vertretungskette", True),
        ("Live-Probelauf der Vertretung", False),
    ]),
    ("Reichweite", BLUE, [
        ("Teams · Slack · WhatsApp", True),
        ("Agent mit Stimme im Termin", True),
        ("Mobile PWA + Push", True),
        ("SAML 2.0 + Gruppen", True),
        ("DATEV / Lexware", False),
    ]),
    ("Zeit bis zum Nutzen", AMBER, [
        ("Workflow-Baukasten", True),
        ("Skill-Marktplatz", True),
        ("Branchen-Pakete", True),
        ("Admin-Concierge", True),
    ]),
]


# ── Zeichnen ─────────────────────────────────────────────────────────────────

def _font(name: str, size: int):
    for path in (f"/Library/Fonts/{name}.ttf",
                 f"/System/Library/Fonts/Supplemental/{name}.ttf"):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    for fallback in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                     "/System/Library/Fonts/Supplemental/Arial.ttf"):
        if Path(fallback).exists():
            return ImageFont.truetype(fallback, size)
    return ImageFont.load_default()


def _wrap(draw, text, font, width):
    words, lines, current = text.split(), [], ""
    for word in words:
        probe = f"{current} {word}".strip()
        if draw.textlength(probe, font=font) <= width:
            current = probe
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _card(draw, box, radius=18):
    draw.rounded_rectangle(box, radius=radius, fill=CARD, outline=CARD_LINE, width=1)


def build_roadmap(path: Path) -> None:
    W = 2592

    f_title = _font("SF-Pro", 58)
    f_sub = _font("SF-Pro", 25)
    f_col = _font("SF-Pro", 32)
    f_item = _font("SF-Pro", 27)
    f_note = _font("SF-Pro", 24)

    columns = [
        ("Zuletzt ausgeliefert", GREEN, "Steht jetzt live.", SHIPPED),
        ("In Arbeit", AMBER, "Angefangen, noch nicht fertig.", IN_PROGRESS),
        ("Als Nächstes", BLUE, "Bewusst benannt statt stillschweigend offen.", NEXT),
    ]
    col_w = (W - 60 * 4) // 3

    # Erst messen, dann die Leinwand aufspannen. Eine feste Hoehe hiess bisher: ein
    # Eintrag mehr, und die laengste Spalte laeuft unten aus dem Bild — sichtbar
    # erst, wenn jemand das fertige Bild ansieht.
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    def column_height(entries):
        height = 34 + 44 + 42
        for _title, desc in entries:
            height += 36 + 30 * len(_wrap(probe, desc, f_item, col_w - 60)) + 22
        return height

    head_lines = _wrap(probe, SUBTITLE, f_sub, W - 130)
    top = 145 + 34 * len(head_lines) + 40
    footer_lines = _wrap(probe, FOOTER, f_note, W - 130)
    H = top + max(column_height(c[3]) for c in columns) + 50 + 32 * len(footer_lines) + 40

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    d.text((60, 55), f"Roadmap — Stand {VERSION}", font=f_title, fill=TEXT)
    y = 145
    for line in head_lines:
        d.text((60, y), line, font=f_sub, fill=MUTED)
        y += 34

    for index, (heading, color, note, entries) in enumerate(columns):
        x = 60 + index * (col_w + 60)
        cy = top + 34
        _card(d, (x, top, x + col_w, top + column_height(entries)))

        d.ellipse((x + 30, cy + 12, x + 42, cy + 24), fill=color)
        d.text((x + 58, cy), heading, font=f_col, fill=color)
        cy += 44
        d.text((x + 30, cy), note, font=f_note, fill=MUTED)
        cy += 42

        for title, desc in entries:
            d.text((x + 30, cy), title, font=f_item, fill=TEXT)
            cy += 36
            for line in _wrap(d, desc, f_item, col_w - 60):
                d.text((x + 30, cy), line, font=f_item, fill=MUTED)
                cy += 30
            cy += 8
            d.line((x + 30, cy, x + col_w - 30, cy), fill=CARD_LINE, width=1)
            cy += 14

    fy = H - 32 * len(footer_lines) - 40
    for line in footer_lines:
        d.text((60, fy), line, font=f_note, fill=MUTED)
        fy += 32

    img.save(path)
    print(f"geschrieben: {path}")


def build_vision(path: Path) -> None:
    W = 2592

    f_title = _font("SF-Pro", 58)
    f_sub = _font("SF-Pro", 25)
    f_col = _font("SF-Pro", 31)
    f_item = _font("SF-Pro", 26)

    sub = (
        "Die vertrauenswürdige autonome KI-Belegschaft für den deutschen Mittelstand — "
        "selbst gehostet, DSGVO, isolierte Agenten, die unbeaufsichtigt laufen dürfen. "
        f"Stand {VERSION}: ausgefüllt = live, offen = noch nicht."
    )
    note = (
        "Offen sind noch zwei Punkte: der Probelauf der Vertretungskette am Livesystem "
        "und der DATEV/Lexware-Export — letzterer auf ausdrücklichen Wunsch zurückgestellt."
    )
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    col_w = (W - 60 * 5) // 4
    head_lines = _wrap(probe, sub, f_sub, W - 130)
    note_lines = _wrap(probe, note, f_sub, W - 130)
    top = 145 + 34 * len(head_lines) + 40
    tallest = max(34 + 56 + len(p[2]) * 46 + 24 for p in PILLARS)
    H = top + tallest + 60 + 32 * len(note_lines) + 40

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    d.text((60, 55), "Vision — die vier Säulen", font=f_title, fill=TEXT)
    y = 145
    for line in head_lines:
        d.text((60, y), line, font=f_sub, fill=MUTED)
        y += 34
    for index, (name, color, points) in enumerate(PILLARS):
        x = 60 + index * (col_w + 60)
        height = 34 + 56 + len(points) * 46 + 24
        _card(d, (x, top, x + col_w, top + height))
        cy = top + 34
        d.text((x + 30, cy), name, font=f_col, fill=color)
        cy += 56
        for label, done in points:
            box = (x + 30, cy + 6, x + 30 + 18, cy + 24)
            if done:
                d.ellipse(box, fill=color)
            else:
                d.ellipse(box, outline=MUTED, width=2)
            for line_index, line in enumerate(_wrap(d, label, f_item, col_w - 90)):
                d.text((x + 62, cy + line_index * 30), line, font=f_item,
                       fill=TEXT if done else MUTED)
            cy += 46

    ny = H - 32 * len(note_lines) - 40
    for line in note_lines:
        d.text((60, ny), line, font=f_sub, fill=MUTED)
        ny += 32

    img.save(path)
    print(f"geschrieben: {path}")


if __name__ == "__main__":
    ASSETS.mkdir(parents=True, exist_ok=True)
    build_roadmap(ASSETS / "roadmap.png")
    build_vision(ASSETS / "vision-roadmap.png")
