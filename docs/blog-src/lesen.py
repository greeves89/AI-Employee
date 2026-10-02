"""Beitragsdateien (Kopf + Markdown) in die Argumente von blog_save_post umwandeln.

Kopf einer Datei in beitraege/:
  slug, title, description, keyword, cover   → gehen an den Blog
  rubrik, symbol, farbe, bildtitel            → nur für das Titelbild (bilder/render.mjs)
  tags: a, b, c
  faq:
  - Frage | Antwort
"""
import glob
import os

NUR_BILD = ("rubrik", "symbol", "farbe", "bildtitel")
HIER = os.path.dirname(os.path.abspath(__file__))


def lies(pfad):
    roh = open(pfad, encoding="utf-8").read()
    _, kopf, text = roh.split("---\n", 2)
    a, faq, in_faq = {}, [], False
    for zeile in kopf.splitlines():
        if zeile.startswith("faq:"):
            in_faq = True
            continue
        if in_faq and zeile.startswith("- "):
            frage, antwort = zeile[2:].split(" | ", 1)
            faq.append({"frage": frage.strip(), "antwort": antwort.strip()})
            continue
        k, v = zeile.split(": ", 1)
        a[k.strip()] = v.strip()
    for k in NUR_BILD:
        a.pop(k, None)
    a["tags"] = [t.strip() for t in a.get("tags", "").split(",") if t.strip()]
    a["faq"] = faq
    a["body_markdown"] = text.strip()
    return a


def alle():
    return [lies(p) for p in sorted(glob.glob(os.path.join(HIER, "beitraege", "*.md")))]
