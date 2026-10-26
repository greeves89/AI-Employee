"""Erzeugt die Symbole der Bridge — reproduzierbar, ohne Grafikprogramm.

    python assets/erzeugen.py      (aus computer-use-bridge/, braucht Pillow;
                                    .icns nur auf dem Mac mit iconutil)

App-Symbol: dieselbe Familie wie die iOS-App (blauer Verlauf, Glas-Kachel,
Cyan-Akzente), statt des Chips ein Bildschirm mit Auge — die Bridge ist das,
was auf deinem Rechner mitsieht. Linien nach den lucide-Symbolen „monitor“ und
„eye“ (24er-Raster).

Menueleiste (Mac, Template = folgt Hell/Dunkel) und Tray (Windows) zeigen den
Zustand: getrennt, verbindet, verbunden, sieht mit.
"""

from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HIER = Path(__file__).resolve().parent
UEBER = 4                       # Zeichnen in vierfacher Groesse, dann glatt verkleinern
CYAN = (78, 230, 230)
WEISS = (255, 255, 255)
ZUSTAENDE = ("getrennt", "verbindet", "verbunden", "sieht_mit")


def _verlauf(groesse: int) -> Image.Image:
    """Diagonaler Verlauf wie im iOS-Symbol: dunkles Navy oben links, Blau, Indigo unten rechts."""
    stopps = [(0.0, (6, 30, 52)), (0.45, (2, 112, 170)), (0.75, (16, 104, 190)), (1.0, (56, 72, 184))]
    klein = Image.new("RGB", (64, 64))
    px = klein.load()
    for y in range(64):
        for x in range(64):
            t = (x + y) / 126
            for (t0, c0), (t1, c1) in zip(stopps, stopps[1:]):
                if t0 <= t <= t1:
                    f = (t - t0) / (t1 - t0)
                    px[x, y] = tuple(round(a + (b - a) * f) for a, b in zip(c0, c1))
                    break
    return klein.resize((groesse, groesse), Image.BICUBIC)


def _p(x: float, y: float, s: float, ox: float, oy: float) -> tuple[float, float]:
    return ox + x * s, oy + y * s


def _monitor(d: ImageDraw.ImageDraw, s: float, ox: float, oy: float, farbe, breite: float) -> None:
    """lucide monitor: rect 2,3 20x14 r2 · Linie 8,21–16,21 · Linie 12,17–12,21."""
    d.rounded_rectangle((*_p(2, 3, s, ox, oy), *_p(22, 17, s, ox, oy)), radius=2 * s, outline=farbe,
                        width=round(breite))
    for a, b in (((8, 21), (16, 21)), ((12, 17), (12, 21))):
        d.line((*_p(*a, s, ox, oy), *_p(*b, s, ox, oy)), fill=farbe, width=round(breite))
        for punkt in (a, b):
            cx, cy = _p(*punkt, s, ox, oy)
            r = breite / 2
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=farbe)


def _mandel(cx: float, cy: float, w: float, h: float, s: float, ox: float, oy: float) -> list:
    punkte = []
    for i in range(121):
        t = i / 120
        punkte.append(_p(cx - w / 2 + w * t, cy - h / 2 * math.sin(math.pi * t), s, ox, oy))
    for i in range(1, 120):
        t = 1 - i / 120
        punkte.append(_p(cx - w / 2 + w * t, cy + h / 2 * math.sin(math.pi * t), s, ox, oy))
    return punkte


def _auge(bild: Image.Image, s: float, ox: float, oy: float, farbe, pupille, breite: float,
          cx: float = 12, cy: float = 10, w: float = 11, h: float = 5.6) -> None:
    """Mandelform (lucide eye) als Ring: aeussere minus innere Form — Pillow zeichnet
    dicke Polygonraender mit Rissen, eine Maske nicht."""
    b = breite / s
    maske = Image.new("L", bild.size, 0)
    md = ImageDraw.Draw(maske)
    md.polygon(_mandel(cx, cy, w + b, h + b, s, ox, oy), fill=255)
    md.polygon(_mandel(cx, cy, w - b, h - b * 0.9, s, ox, oy), fill=0)
    bild.paste(Image.new("RGBA", bild.size, farbe if len(farbe) == 4 else (*farbe, 255)), (0, 0), maske)
    d = ImageDraw.Draw(bild)
    px, py = _p(cx, cy, s, ox, oy)
    r = 1.55 * s
    d.ellipse((px - r, py - r, px + r, py + r), fill=pupille)


def _funkeln(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float, farbe) -> None:
    """Vierzackiger Stern wie im iOS-Symbol."""
    punkte = []
    for i in range(8):
        winkel = math.pi / 2 * (i // 2) + (math.pi / 4 if i % 2 else 0)
        rr = r if i % 2 == 0 else r * 0.28
        punkte.append((cx + rr * math.cos(winkel - math.pi / 2), cy + rr * math.sin(winkel - math.pi / 2)))
    d.polygon(punkte, fill=farbe)


def app_symbol(groesse: int = 1024) -> Image.Image:
    G = groesse * UEBER
    bild = _verlauf(G).convert("RGBA")
    ebene = Image.new("RGBA", (G, G), (0, 0, 0, 0))
    d = ImageDraw.Draw(ebene)
    # Feine Ringe wie im iOS-Symbol
    for i, rand in enumerate((0.035, 0.12)):
        a = round(G * rand)
        d.rounded_rectangle((a, a, G - a, G - a), radius=G * (0.2 - i * 0.04), outline=(255, 255, 255, 26),
                            width=max(1, G // 400))
    # Glas-Kachel
    k0, k1 = G * 0.175, G * 0.825
    d.rounded_rectangle((k0, k0, k1, k1), radius=G * 0.17, fill=(255, 255, 255, 34),
                        outline=(255, 255, 255, 92), width=max(2, G // 300))
    bild = Image.alpha_composite(bild, ebene)
    d = ImageDraw.Draw(bild)
    s = G * 0.0215                      # Einheit des 24er-Rasters
    ox, oy = G / 2 - 12 * s, G / 2 - 12.4 * s
    strich = 1.75 * s
    _monitor(d, s, ox, oy, WEISS, strich)
    _auge(bild, s, ox, oy, WEISS, CYAN, strich * 0.9)
    _funkeln(d, G * 0.255, G * 0.29, G * 0.03, (220, 230, 240))
    _funkeln(d, G * 0.73, G * 0.735, G * 0.048, CYAN)
    r = G * 0.03
    d.ellipse((G * 0.675 - r, G * 0.64 - r, G * 0.675 + r, G * 0.64 + r), fill=CYAN)
    return bild.resize((groesse, groesse), Image.LANCZOS)


def menueleiste(zustand: str, groesse: int = 44) -> Image.Image:
    """Template-Bild (schwarz + Alpha): macOS faerbt es selbst fuer Hell/Dunkel."""
    G = groesse * UEBER
    bild = Image.new("RGBA", (G, G), (0, 0, 0, 0))
    d = ImageDraw.Draw(bild)
    s = G / 24
    farbe = (0, 0, 0, 255 if zustand != "getrennt" else 110)
    _monitor(d, s, 0, 0, farbe, 1.9 * s)
    if zustand == "verbindet":
        for x in (8.5, 12, 15.5):
            d.ellipse((*_p(x - 1.1, 8.9, s, 0, 0), *_p(x + 1.1, 11.1, s, 0, 0)), fill=farbe)
    elif zustand == "verbunden":
        d.ellipse((*_p(9.2, 7.2, s, 0, 0), *_p(14.8, 12.8, s, 0, 0)), fill=farbe)
    elif zustand == "sieht_mit":
        _auge(bild, s, 0, 0, farbe, farbe, 1.6 * s, w=12, h=6.4)
    return bild.resize((groesse, groesse), Image.LANCZOS)


def tray(zustand: str, groesse: int = 64) -> Image.Image:
    """Windows-Tray: farbige Kachel je Zustand, weisser Bildschirm."""
    G = groesse * UEBER
    kachel = {"getrennt": (110, 116, 128), "verbindet": (217, 119, 6),
              "verbunden": (2, 112, 170), "sieht_mit": (2, 112, 170)}[zustand]
    bild = Image.new("RGBA", (G, G), (0, 0, 0, 0))
    d = ImageDraw.Draw(bild)
    d.rounded_rectangle((0, 0, G - 1, G - 1), radius=G * 0.22, fill=kachel)
    s = G / 30
    ox, oy = G / 2 - 12 * s, G / 2 - 12 * s
    _monitor(d, s, ox, oy, WEISS, 2.3 * s)
    if zustand == "sieht_mit":
        _auge(bild, s, ox, oy, WEISS, CYAN, 1.9 * s, w=12, h=6.4)
    elif zustand == "verbunden":
        d.ellipse((*_p(9.6, 7.6, s, ox, oy), *_p(14.4, 12.4, s, ox, oy)), fill=WEISS)
    return bild.resize((groesse, groesse), Image.LANCZOS)


def main() -> None:
    gross = app_symbol(1024)
    gross.save(HIER / "app-symbol-1024.png")
    gross.save(HIER / "app-symbol.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                                               (128, 128), (256, 256)])
    for z in ZUSTAENDE:
        menueleiste(z).save(HIER / f"menueleiste-{z}.png")
        tray(z).save(HIER / f"tray-{z}.png")
    if sys.platform == "darwin":
        satz = HIER / "app-symbol.iconset"
        satz.mkdir(exist_ok=True)
        for g in (16, 32, 128, 256, 512):
            gross.resize((g, g), Image.LANCZOS).save(satz / f"icon_{g}x{g}.png")
            gross.resize((g * 2, g * 2), Image.LANCZOS).save(satz / f"icon_{g}x{g}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(satz), "-o", str(HIER / "app-symbol.icns")], check=True)
        for f in satz.iterdir():
            f.unlink()
        satz.rmdir()
    print("Symbole erzeugt in", HIER)


if __name__ == "__main__":
    main()
