# Blog der Landingpage

Die Landingpage (`docs/ios-app/`) ist statisch. Ihr Blog ist — wie das
Kontaktformular — eine Funktion des Orchestrators, die nur auf Anlagen läuft,
die sie einschalten. Ohne `BLOG_ENABLED` antworten alle Adressen des Blogs mit
404.

| Was | Wo |
|---|---|
| Öffentliche Seiten | `/blog`, `/blog/<adresse>`, `/blog/feed.xml`, `/sitemap.xml`, `/robots.txt` |
| Schreiben | MCP-Dienst `POST /api/v1/mcp/blog` (Bearer-Schlüssel) |
| Code | `orchestrator/app/core/blog.py`, `api/blog_public.py`, `api/blog_mcp.py`, `blog_site/` |
| Daten | Tabelle `blog_posts` |

## Einschalten

1. Schlüssel erzeugen und in die `.env` der Anlage eintragen (nie ins Repo):

   ```bash
   echo "BLOG_MCP_TOKEN=$(openssl rand -base64 48 | tr -d '/+=$' | head -c 48)" >> .env
   ```

2. Weitere Angaben in der `.env`:

   ```bash
   BLOG_ENABLED=true
   BLOG_BASE_URL=https://example.com      # öffentliche Adresse, ohne Schrägstrich am Ende
   BLOG_AUTHOR=Vorname Nachname           # Name unter den Beiträgen
   # optional: Besucherzählung des Betreibers (nur https)
   BLOG_ANALYTICS_SRC=https://stats.example.com/script.js
   BLOG_ANALYTICS_ID=<kennung>
   ```

3. Dem Reverse-Proxy die Adressen geben. Mit dem mitgelieferten Caddy als
   Datei `conf.d/site/blog.caddy`:

   ```caddy
   handle /blog* {
   	reverse_proxy ai-employee-orchestrator:8000 {
   		import forwarded_https
   	}
   }
   handle /sitemap.xml {
   	reverse_proxy ai-employee-orchestrator:8000
   }
   handle /robots.txt {
   	reverse_proxy ai-employee-orchestrator:8000
   }
   ```

   Danach `docker restart ai-employee-caddy`.

4. Orchestrator neu starten. Die Tabelle legt die Migration an.

Ohne öffentliche Adresse (`BLOG_BASE_URL`, ersatzweise `PUBLIC_APP_URL`) bleibt
der Blog aus: kanonische Adresse, Sitemap und Feed brauchen sie.

## Beiträge pflegen (Oberfläche)

Administratoren pflegen die Beiträge in der **Admin-Konsole → System → Blog**:
Liste, Editor, Prüfung, Vorschau, Veröffentlichen, Zurückziehen. Die Anleitung
steht im Benutzerhandbuch, Abschnitt 22.9.

## Beiträge schreiben (MCP)

Der Dienst spricht MCP über Streamable HTTP. In Claude Code:

```bash
claude mcp add --transport http blog https://example.com/api/v1/mcp/blog \
  --header "Authorization: Bearer <BLOG_MCP_TOKEN>"
```

Für die Agenten der Plattform trägt ein Administrator dieselbe Adresse unter
**Integrationen → MCP-Server** ein (Anmeldung: Token). Danach haben alle
Laufzeiten die Werkzeuge.

| Werkzeug | Zweck |
|---|---|
| `blog_writing_guide` | Arbeitsweise: Themenkarte, Erfahrung erfragen, Aufbau, Verlinkung |
| `blog_list_posts` | Alle Beiträge mit Status und Hauptbegriff |
| `blog_get_post` | Einen Beitrag vollständig lesen |
| `blog_save_post` | Anlegen oder ändern; neue Beiträge sind Entwürfe |
| `blog_seo_check` | Prüfung: Längen, Hauptbegriff, Gliederung, Umfang, Verweise |
| `blog_find_mentions` | Beiträge, die einen Begriff nennen — für die Verlinkung |
| `blog_publish` / `blog_unpublish` | Online stellen und zurückziehen |
| `blog_delete_post` | Löschen (veröffentlichte nur mit Bestätigung) |

Ein veröffentlichter Beitrag zeigt jede Änderung sofort. Eine Änderung, die ihn
unter die Schwelle der Prüfung drückt, wird deshalb abgelehnt — erst
zurückziehen, dann umbauen.

`blog_save_post` antwortet mit einer Vorschau-Adresse. Über sie lässt sich ein
Entwurf im Browser ansehen; Suchmaschinen sehen ihn nicht.

## Arbeitsweise

Die Werkzeuge bilden eine einfache Methode ab:

1. **Themenkarte.** Zu einem Thema alle Fragen sammeln, die Menschen dazu
   stellen, und je Frage einen Beitrag schreiben.
2. **Erfahrung erfragen.** Vor dem Schreiben dem Menschen, in dessen Namen der
   Beitrag erscheint, höchstens zehn Fragen stellen — eigene Erfahrung,
   Fallbeispiele, Meinung. Nichts erfinden.
3. **Verlinken.** `blog_find_mentions` zeigt, wo der Hauptbegriff schon steht.
   An etwa drei dieser Stellen auf den neuen Beitrag verweisen.

## Sicherheit

- Markdown wird mit abgeschaltetem HTML aufbereitet; HTML im Text erscheint als
  Text. Verweise mit `javascript:` und ähnlichen Zielen werden nicht zu Links.
- Die Seiten setzen eine eigene Content-Security-Policy ohne Skripte (Ausnahme:
  die eingetragene Besucherzählung).
- Der MCP-Schlüssel braucht mindestens 32 Zeichen; ein kürzerer Wert schaltet
  den Dienst nicht frei. Er steht nie in einer Adresse — die Vorschau nutzt
  einen eigenen zufälligen Schlüssel je Beitrag, der beim Zurückziehen neu
  vergeben wird. Vorschau-Seiten laden keine Besucherzählung.
- Wer den MCP-Schlüssel hat, kann veröffentlichen. Er gehört nur in Hände, die
  im Namen der Seite schreiben dürfen.
