# Quellen der Blogbeiträge

Die Beiträge des Blogs liegen in der Datenbank der Anlage. Hier liegen ihre
Quellen, damit sie versioniert sind und sich nach einer Änderung wieder
einspielen lassen.

| Ordner / Datei | Inhalt |
|---|---|
| `beitraege/*.md` | Je Beitrag eine Datei: Kopf (Adresse, Titel, Beschreibung, Hauptbegriff, Themen, Fragen) und Markdown |
| `bilder/grafik-*.html` | Quellen der Grafiken im Text |
| `bilder/render.mjs` | Erzeugt Titelbilder (aus dem Kopf der Beiträge) und Grafiken nach `bilder/out/` |
| `einspielen.py` | Spielt Bilder und Beiträge über den MCP-Dienst des Blogs ein |

Ablauf nach einer Änderung:

```bash
node docs/blog-src/bilder/render.mjs
export BLOG_MCP_URL=https://example.com/api/v1/mcp/blog BLOG_MCP_TOKEN=...
python3 docs/blog-src/einspielen.py bilder
python3 docs/blog-src/einspielen.py entwurf     # speichert; veröffentlichte Beiträge bleiben online
python3 docs/blog-src/einspielen.py online      # nur für neue Beiträge nötig
```

Die Arbeitsweise (Themenkarte, Erfahrung des Autors erfragen, Bilder,
Verlinkung) steht in [../BLOG.md](../BLOG.md).
