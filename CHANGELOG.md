# Changelog

All notable changes to AI-Employee are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) · Versioning: [SemVer](https://semver.org/)

---

## [1.276.2] - 2026-08-31

### Behoben
- **CI war seit dem 27.08. dauerhaft rot — jeder offene Pull Request zeigte einen Fehlschlag, den er gar nicht verursacht hatte.** Ein Kalender-Test verglich das Ergebnis in der Zeitzone des ausfuehrenden Rechners statt in der angefragten. Auf einem Entwicklerrechner mit Europe/Berlin lief er gruen, auf dem UTC-Runner der CI rot. Die Anwendung selbst war immer korrekt; nur die Pruefung war falsch. Wichtiger als der Test: weil rot vier Tage lang der Normalzustand war, fiel zwei Tage spaeter ein **echter** Fehlschlag nicht mehr auf (siehe naechster Punkt).
- **Ein Personenname und eine Kundenkennung standen im oeffentlichen Repository** — in einem Code-Kommentar und in einem Test-Docstring. Der vorhandene Schutz-Test hatte das korrekt gemeldet, ging aber im Dauer-Rot unter. Beide Stellen sind jetzt durch neutrale Formulierungen ersetzt; der Sachverhalt bleibt vollstaendig nachvollziehbar. Hinweis fuer Betreiber: die Git-Historie enthaelt die Namen weiterhin.

## [1.276.1] - 2026-08-29

### Behoben
- **Chat brach mit `'tuple' object has no attribute 'get'` ab** (Kundenmeldung). `_build_responses_body` rief `.get()` auf jedem `tool_calls`-Eintrag ohne Typ-Pruefung auf; ein nicht-dict-Eintrag riss die gesamte Runde ab, noch bevor der Provider-eigene Fehlerpfad greift. Ueberspringt jetzt nicht-dict-Eintraege. Chat-/Task-Fehlermeldungen zeigen zusaetzlich kuenftig den Exception-Typ statt einer blossen Nachricht, damit ein naechstes Auftreten sofort erkennbar ist, auch ohne Container-Log (der nach jedem Agent-Neubau weg ist).
- **Badge-Zeile auf den Agent-Karten wurde bei zu vielen gleichzeitig aktiven Badges lautlos abgeschnitten** (Kundenmeldung: "Buttons sehen komisch aus"). Bricht jetzt in eine zweite Zeile um statt vom Kartenrand verschluckt zu werden.

## [1.276.0] - 2026-08-28

### Neu
- **Agent kann seinen eigenen Container per Chat neu bauen** (`restart_own_container`): rebuildet den Container aus dem aktuellen Agent-Image/Konfig, der Workspace bleibt vollstaendig erhalten. Ueber einen agent-token-authentifizierten Endpunkt (`POST /agent-apps/restart-self`), respektiert denselben Eval-Gate wie das Admin-"Update"-Feature. In allen drei Laufzeiten verdrahtet (Claude Code, Custom-LLM, Codex) sowie in der Sprachfront-Werkzeug-Parity eingeordnet (delegiert, wie `rebuild_app`).

## [1.275.4] - 2026-08-28

### Behoben
- **Agent empfahl unaufgefordert "ego (lite) auf deinem Mac installieren"**, ohne das Betriebssystem des Nutzers zu kennen — live in einem Kundengespraech gefunden. Die automatische Ein-Zeilen-Installation gibt es nur fuer macOS; auf anderen Systemen (oder wenn das Betriebssystem unbekannt ist) soll der Agent jetzt zuerst fragen und sonst auf https://lite.ego.app/ verweisen statt Mac anzunehmen.

## [1.275.3] - 2026-08-28

### Behoben
- **SSO-Login mit Microsoft endete fuer neue Nutzer mit 500** — live per Log-Traceback bestaetigt: `invalid input value for enum userrole: "UNASSIGNED"`. Die Rolle `UNASSIGNED` (fuer frisch per SSO angemeldete Nutzer ohne Zuteilung) wurde als Python-Enum-Member ergaenzt, die noetige `ALTER TYPE`-Migration fuer den Postgres-Enum-Typ fehlte aber — betraf nicht nur den gemeldeten Kunden, sondern auch die eigene Plattform (dort bisher nur nicht ausgeloest). Nachzieh-Migration ergaenzt (gleiches Muster wie die GitHub-OAuth-Provider-Ergaenzung).

## [1.275.2] - 2026-08-27

### Behoben
- **Feedback-Detail-Modal zeigte den Volltext technisch-roh** (sichtbare `---`-Frontmatter-Zeilen, `**fett**`, `# Ueberschrift`) statt formatiert. Rendert den Markdown-Volltext jetzt ueber die bestehende `MarkdownContent`-Komponente (dieselbe wie im Agent-Chat); YAML-Frontmatter und die eingebettete Screenshot-Referenz werden rausgeschnitten, weil beides in der Modal schon als eigene Badges/Sektion angezeigt wird.

## [1.275.1] - 2026-08-27

### Behoben
- **Der Modal-Fix aus 1.275.0 hat live nicht funktioniert — Klick daneben schloss die Modal weiterhin nicht.** Falsche Ursache angenommen: nicht Radix' eingebaute Aussenklick-Erkennung war das Problem, sondern dass Radix' `Dialog.Content` selbst per Inline-Style `pointer-events: auto` erzwingt und damit die `pointer-events-none`-Tailwind-Klasse auf dem umschliessenden Wrapper-Element unwirksam macht — der Wrapper deckt dadurch den GESAMTEN Viewport ab und faengt jeden Klick ab, bevor er das dahinterliegende Overlay je erreicht. Live mit `elementFromPoint`/`getComputedStyle` bestaetigt. Der Klick-Handler sitzt jetzt auf dem Content-Wrapper selbst (`target === currentTarget` unterscheidet Klick-auf-Hintergrund von Klick-auf-Karte), nicht mehr auf dem nie erreichten Overlay.

## [1.275.0] - 2026-08-27

### Behoben
- **Modals schlossen sich nicht beim Klick daneben.** Live gemeldet an der Feedback-Detail-Modal: der Klick-außerhalb-Mechanismus war strukturell kaputt — das unsichtbare `pointer-events-none`-Wrapper-Element ließ Radix' eingebaute Aussenklick-Erkennung ins Leere laufen. Alle betroffenen Dialoge (Feedback-Detail, Mount-Rechte, Datei-Upload, Changelog, Team/Agent anlegen, Delegieren, Freigabe-Anfrage, Analytics-Agentendetail) bekommen jetzt einen expliziten Klick-Handler auf dem Hintergrund.
- **Element-Label im Feedback-Widget verklebte zwei Textteile ohne Trenner** (z.B. "...erhaltenFeedback wird..." statt "...erhalten Feedback wird..."), weil `textContent` den Text mehrerer Geschwister-Elemente roh aneinanderhängt. Liest jetzt `innerText` (respektiert das gerenderte Layout). Ausserdem schnitt die 80-Zeichen-Grenze mitten im Wort ohne Auslassungszeichen ab — jetzt an der letzten Wortgrenze mit "…".

## [1.274.2] - 2026-08-27

### Behoben
- **CSV-/Formel-Injection-Fix aus 1.274.1 war unvollstaendig** — `sentiment` (frei befuellbar ueber das Feedback-Widget) wurde beim Absichern der Export-Spalten uebersehen. Jetzt geht jede exportierte Freitextspalte durch dieselbe Absicherung; ein neuer Test deckt alle betroffenen Spalten gemeinsam ab, nicht nur eine Stichprobe.

## [1.274.1] - 2026-08-27

### Behoben
- **CSV-/Formel-Injection im Feedback-Export.** Titel, Name und Admin-Notizen kommen aus Nutzer-Feedback und landeten roh in der exportierten CSV — ein Wert wie `=HYPERLINK(...)` haette beim Oeffnen in Excel/Sheets als Formel laufen koennen. Felder, die mit `=`/`+`/`-`/`@`/Tab/CR beginnen, bekommen jetzt ein fuehrendes Anfuehrungszeichen, das jede gaengige Tabellenkalkulation zwingt, sie als reinen Text zu lesen.

## [1.274.0] - 2026-08-27

### Hinzugefuegt
- **Feedback als ZIP exportieren** (Admin -> Feedback) — eine CSV-Uebersicht ueber alle Eintraege plus, je Widget-Feedback, dessen Markdown-Datei und Screenshot. Neuer Endpoint `GET /feedback/export` (optionaler `status`-Filter, admin-only).

## [1.273.0] - 2026-08-27

### Hinzugefuegt
- **Admin-konfigurierbare Websuche** (Admin -> Websuche, Vorbild OpenWebUI) — DuckDuckGo bleibt schluessellos die Vorgabe; wahlweise echte Brave-Search-API- oder SerpApi-Anbindung, Provider + Key global umschaltbar. Vorher gab es ZWEI unabhaengige, sich widersprechende DuckDuckGo-Kopien (Sprachfront, Agent-Container) und der bestehende „brave-search"-Eintrag bei den AI-Accounts war nachweislich nur ein Stub ohne echten Aufruf. Jetzt EIN gemeinsames Modul (`core/web_search.py`), das Agent-Container (ueber einen neuen `/agent-search/web`-Endpunkt), Sprachfront UND — als neuer MCP-Tool-Eintrag `web_search` — auch Claude Code direkt nutzen. Schliesst zugleich eine Harness-Luecke: Claude Code hatte bisher ueberhaupt keine Websuche.

### Behoben
- **"Eigene KI-Zugaenge erlauben"-Schalter liess sich nicht deaktivieren.** Der Schalter fehlte in der PATCH-Feldliste der Einstellungen-API — ein Admin-Klick wurde lautlos verworfen, ohne Fehlermeldung, und `GET /settings/` zeigte deshalb immer "an", unabhaengig vom tatsaechlichen Zustand. Live per DB-Abfrage bestaetigt: der Wert wurde noch nie gespeichert.

## [1.272.2] - 2026-08-27

### Behoben
- **"Öffne YouTube und such nach X" öffnete nur die Startseite, keine Ergebnisse.** Die `ego`-Beschreibung hatte als einziges Beispiel "öffnen + lesen", kein Suchbeispiel — die Sprachfront rief `openOrReuseTab` auf die Startseite auf und hielt die Aufgabe fuer erledigt, sobald ein Tab offen war. Neues, live verifiziertes Beispiel: bei einer Suche direkt die Ergebnis-URL ansteuern (`?search_query=`/`?q=`) statt eine leere Startseite zu oeffnen und dort stehen zu bleiben. Beschreibung nennt jetzt auch den vollen Helfer-Umfang von ego lite (Task Spaces, Drag, Upload, CDP, Netzwerk-Warten, …), nicht nur eine Kurzliste — dieselbe Rohzugriff-Breite, die Claude Code selbst über die ego-browser-Skill hat.

## [1.272.1] - 2026-08-27

### Behoben
- **ego lite arbeitete unsichtbar im Hintergrund — die Automatisierung lief korrekt, der Nutzer sah nur nichts davon und hielt sie fuer kaputt.** Startet die `ego-browser`-CLI ego lite selbst (kein laufender Prozess vorhanden), laeuft es als Hintergrunddienst (`--startup-ego-browser-service`) ohne Fenster im Vordergrund. Live verifiziert: die Suche hatte tatsaechlich stattgefunden (echter Tab, echte Ergebnisse), nur unsichtbar. Die Bridge holt ego lite nach jedem erfolgreichen Aufruf jetzt automatisch in den Vordergrund.
- **"Browser starten" + danach suchen oeffnete zwei verschiedene Fenster.** Sagte der Nutzer "starte den Browser und such nach X" als einen Satz, rief die Sprachfront `open` (Standardbrowser) UND separat `ego` (ego lite) auf — zwei unabhaengige Fenster, der Nutzer sah nur das leere erste. Beschreibung praezisiert: jede Interaktion im Satz macht die GANZE Aufgabe zu einem einzigen `ego`-Aufruf.

## [1.272.0] - 2026-08-27

### Hinzugefuegt
- **Diskrete ego-lite-Aktionen** — `ego_navigate`/`ego_snapshot`/`ego_click`/`ego_fill`/`ego_wait`/`ego_capture`/`ego_tabs`/`ego_close`, das Gegenstueck zu `browser_*` fuer die echte, eingeloggte Sitzung — kein eigenes JS-Skript mehr noetig fuer die gaengigsten Schritte (`ego_run` bleibt fuer alles Komplexere). Alle acht live gegen ein echtes ego lite verifiziert, bevor sie in die Bridge kamen. Ueber Bridge, MCP-Server, Codex/Custom-LLM-Katalog UND die Sprachfront-Rohdurchreiche erreichbar — keine Extra-Arbeit fuer letztere noetig, das war genau der Sinn der Rohdurchreiche aus 1.271.0.
- **M365-Mail-Suche fuer die Sprachfront** — `m365_mail_recent` kannte bisher nur "letzte N Mails"; live gemeldet, dass eine Themensuche ("Deutsche Bahn", "Reisekosten") dadurch leerlief. Neu: `search`/`sender`/`subject` werden jetzt an den bestehenden Graph-Suchparameter durchgereicht (der Agent hatte das laengst).
- **Personensuche + Teams-Nachrichten fuer die Sprachfront** — `m365_search_people` (dieselbe Verzeichnissuche, die der Agent schon hat) und `m365_teams_message` (schreibt in einen bestehenden 1:1-Chat, gefunden ueber die Mitgliedernamen; kein Chat gefunden wird ehrlich gesagt statt geraten). Vorher hatte die Sprachfront fuer Teams ueberhaupt kein Werkzeug.

### Behoben
- **Kalender "morgen" zeigte ueberwiegend den Rest von heute.** `days_ahead` war ein rollierendes Fenster ab dem exakten Aufrufzeitpunkt, kein Kalendertag — `days_ahead=1` erreicht Mitternacht erst kurz vor Tagesende. Neuer Parameter `date` (`today`/`tomorrow`/ISO-Datum) auf dem gemeinsamen Graph-Werkzeug `ms_list_calendar_events` liefert jetzt echte Mitternacht-zu-Mitternacht-Grenzen; die Sprachfront nutzt ihn ueber ein neues `when`-Feld.
- **Buchstabierte E-Mail-Adressen wurden mehrfach falsch verstanden und trotzdem verwendet** (live: `alisch@mindsquare.de` wurde nacheinander zu drei falschen Adressen). Die Sprachfront liest eine buchstabierte Adresse jetzt Buchstabe fuer Buchstabe zurueck, bevor sie sie in einer Mail verwendet.
- **`ego` galt nur fuer Login-Aufgaben** — "Google oeffnen"/"YouTube durchsuchen" liefen ueber den unzuverlaessigen Bedienungshilfen-Weg statt ueber `ego`. Jetzt der Standardweg fuer jede Aufgabe mit Browser-Inhalten, inkl. eines konkreten Beispiels fuer interne Tools (Perk/Concur/SAP), die die Sprachfront vorher faelschlich als "kein Zugriff" abgewiesen hat.

## [1.271.1] - 2026-08-27

### Behoben
- **`ego` wurde zu eng auf Login-Aufgaben beschraenkt — die Sprachfront nutzte "einfach oeffnen" (Standardbrowser) + Bedienungshilfen-Suche fuer Websites, was fuer Web-Inhalte nicht zuverlaessig funktioniert.** Live gemeldet: "Google oeffnen" + "auf YouTube nach Pokemon Karten suchen" liefen ueber `open_url` + `find_element` + `type` (Standardbrowser, kein DOM-Zugriff) und scheiterten; erst nach expliziter Nutzer-Nachfrage griff `ego` — und funktionierte sofort korrekt (echte Seiten-Snapshots, echtes Navigieren zur Video-URL). Beschreibung praezisiert: `ego` ist jetzt der Standardweg fuer JEDE Aufgabe mit Browser-Inhalten (nicht nur Login), `open`+`find`+`click` ausdruecklich nur noch fuer native Apps. Zusaetzlich: die Sprachfront behauptete vorab faelschlich, `ego` sei "nicht aktiviert" (obwohl in der Bridge laengst freigegeben) — Beschreibung untersagt jetzt ausdruecklich, das ohne echten Fehlertext zu vermuten.

## [1.271.0] - 2026-08-27

### Behoben
- **Die Sprachfront kannte nur 8 handverdrahtete Bridge-Aktionen — jede neue
  Faehigkeit (zuletzt `ego_run`) fehlte ihr automatisch, bis sie hier von
  Hand nachgetragen wurde.** Live gemeldet: "1:1 die gleichen Tools wie der
  Agent" — der Agent selbst erreicht ueber die MCP-Server der Bridge JEDE
  Aktion, die Sprachfront kannte nur `open/screenshot/find/click/type/key/
  wait/scroll` (jetzt neu: `ego`). Die tiefere Ursache: ein drittes, unabhaengig
  gepflegtes Werkzeug-Schema (`DESKTOP_TOOL`) neben dem MCP-Server und den
  Codex/Custom-LLM-Definitionen — genau das Muster, vor dem die Harness-
  Paritaet-Regel warnt. Neu: eine Rohdurchreiche — jeder ECHTE Bridge-
  Aktionsname (`shell_run`, `browser_navigate`, `ego_run`, `get_clipboard`, …)
  geht mit einem `params`-Objekt direkt an dieselbe `dispatch_bridge_command`-
  Funktion, dieselbe Faehigkeits-/Besitzpruefung wie ueberall sonst. Neue
  Bridge-Aktionen erreichen die Sprachfront damit automatisch — keine
  manuelle Nachpflege mehr noetig.

## [1.270.1] - 2026-08-27

### Behoben
- **`ego_run` wurde vom Agenten uebersehen — er versuchte erst `open_app`.**
  Live im Sprachmodus getestet: auf "oeffne ego lite" probierte der Agent
  zunaechst verschiedene App-Namen ueber `open_app`, bevor er (nur zufaellig)
  auf `ego_run` kam. Ursache: die Werkzeug-Beschreibung sagte nicht, dass
  `ego_run` ego lite bei Bedarf SELBST startet (verifiziert: ein Aufruf mit
  komplett geschlossener App startet sie automatisch im Hintergrund) —
  `open_app` davor ist ueberfluessig. Beschreibung in beiden Werkzeug-Quellen
  (MCP-Server, Codex/Custom-LLM) praezisiert.

## [1.270.0] - 2026-08-27

### Hinzugefuegt
- **Gespeicherte Meetings — vorher gab es dafuer gar keine Persistenz.** Der
  Meeting-Recorder in der iOS-App hielt das Transkript nur im Arbeitsspeicher;
  verlassen der Ansicht oder "Verwerfen" loeschte alles, kein Verlauf, kein
  Umbenennen, keine Teilnehmerliste. Neue userbased CRUD-Flaeche `/meetings`
  (`POST` speichern, `GET` Liste + Einzelabruf, `PATCH` umbenennen/Teilnehmer
  bearbeiten, `DELETE`) — strikt auf den anfragenden Nutzer beschraenkt wie
  jeder andere neue Endpunkt. Sprecher-Erkennung ist bewusst NICHT enthalten
  (V1): die Pi-STT (`faster-whisper small`, CPU) ist fuer echte Diarisierung
  zu ressourcenknapp; Teilnehmer werden manuell eingetragen.

## [1.269.0] - 2026-08-27

### Hinzugefuegt
- **Agenten koennen jetzt ego lite bedienen — die echte, eingeloggte
  Browsersitzung des Nutzers, nicht nur ein isoliertes Profil.** Bisher
  konnte die Desktop-Bridge einen Browser nur im eigenen, separaten Profil
  steuern (`browser_navigate` & Co.) — bewusst getrennt vom echten Profil des
  Nutzers, damit kein Login-Diebstahl moeglich ist. Fuer Aufgaben, bei denen
  der Nutzer bereits angemeldet ist (Mail, interne Tools), war das jedes Mal
  eine erneute Anmeldung. Neue Faehigkeitsgruppe `ego_browser` (wie `shell`
  standardmaessig AUS, bewusstes Freischalten durch den Nutzer): eine neue
  Aktion `ego_run` schickt ein JS-Snippet an das lokale `ego-browser`-CLI
  (Voraussetzung: die ego-lite-App ist auf dem Rechner installiert) und
  liefert die Ausgabe zurueck — dieselben Helfer (Task Spaces, Klicken,
  Formulare fuellen, Seiteninhalt lesen), die auch die `ego-browser`-Skill
  fuer Claude Code lokal nutzt. Ueber alle drei Laufzeiten hinweg verdrahtet
  (MCP-Server fuer Claude Code, `computer_use`-Werkzeug fuer Codex/Custom-LLM)
  und im Berechtigungs-Dialog der Bridge sichtbar.

## [1.268.4] - 2026-08-24

### Behoben
- **Die Namens-Wache schlug bei eingebetteten Bildern falschen Alarm — und
  hielt damit die gesamte Auslieferung an.** Die Pruefung, die Kunden- und
  Personennamen aus dem oeffentlichen Repo fernhaelt, verglich jede Zeile als
  reinen Text. In einer Seite steckt ein Foto als 140 KB grosse Base64-Zeile;
  in so viel Zeichensalat taucht ein vierstelliger Name irgendwann zufaellig
  auf. Folge: der Testlauf auf `main` war rot, und alle vier offenen Pull
  Requests standen auf "instabil" — obwohl an keiner Stelle ein Name stand und
  an keinem der vier Pull Requests etwas fehlte.
  Eingebettete Binaerdaten werden jetzt vor dem Abgleich entfernt. Der Text
  rund um so ein Bild wird weiter geprueft, und ein Name im Fliesstext faellt
  unveraendert auf — beides ist mit eigenen Tests festgehalten, damit die
  Wache nicht im Stillen blind wird.
- **Die Fundmeldung zeigt wieder, was gefunden wurde.** Meldete die Wache eine
  Zeile mit eingebettetem Bild, bestand die auf 90 Zeichen gekuerzte Ausgabe
  nur aus Bilddaten — der Name, um den es ging, fiel hinten heraus. Wer die
  Meldung las, sah Datei und Zeile, aber nicht den Anlass. Gemeldet wird jetzt
  die geschnittene Zeile.

## [1.268.3] - 2026-08-24

### Geaendert
- **Landingpage: Rechtslinks fuehren auf die eigenstaendigen Seiten** (/impressum,
  /datenschutz) statt Overlays aufzupoppen — Formular-Hinweis, Hinweis-Leiste
  und Footer. Die Overlays bleiben als Fallback der Einzeldatei erhalten;
  ihr Schliessen springt nicht mehr zum Seitenanfang.

## [1.268.2] - 2026-08-24

### Behoben
- **Landingpage: Impressum und Datenschutz sind wieder erreichbar, solange die
  Hinweis-Leiste offen ist.** Die fixe Leiste verdeckte genau die Footer-Zeile
  mit den Rechtslinks. Jetzt stehen beide Links auch in der Leiste selbst, und
  der Footer bekommt Luft nach unten, bis der Hinweis bestaetigt wurde.

## [1.268.1] - 2026-08-24

### Behoben
- **Foundry-Discovery findet jetzt auch Projekt-Ressourcen** — live gegen eine
  echte Azure-AI-Foundry-Projekt-URL verifiziert: dort liegen die deployten
  Modelle unter `/deployments?api-version=2025-05-01` (ein `/v1/models` gibt
  es nicht). Neuer Kandidat fuer Foundry und fuer als azure-openai angelegte
  Projekt-URLs; der Parser liest das Deployments-Format, nimmt den
  DEPLOYMENT-Namen als Modell-Id und laesst Embedding-Deployments draussen.

## [1.268.0] - 2026-08-24

### Hinzugefuegt
- **Foundry-Modelle werden endlich gefunden (Kundenbefund).** Fuer den
  Provider "foundry" gab es keinerlei Discovery-Pfad — deployte Modelle einer
  Azure-AI-Foundry-Ressource tauchten nie auf. Jetzt probiert die Discovery
  je Provider mehrere Kandidaten-Pfade (Foundry: native Anthropic-Route,
  OpenAI-v1-Flaeche, Modell-Katalog; Azure OpenAI zusaetzlich den
  Deployments-Katalog) und nimmt die erste Antwort; scheitern alle, wird der
  aussagekraeftigste Fehler gemeldet (Auth vor 404). Der Modell-Katalog
  fragt Foundry ueber Ressource + Key aus den Provider-Einstellungen ab.
- **Modell-Freigabe je AI-Account (Kundenwunsch).** Der Administrator kann
  einzelne Modelle eines AI-Accounts sperren/freigeben (Schalter je Modell in
  der AI-Accounts-Seite). Agent-Erstellung und Umverbinden zeigen nur
  freigegebene Modelle und erzwingen das serverseitig; Bestandsdaten ohne
  Flag gelten als freigegeben.

## [1.267.3] - 2026-08-24

### Behoben
- **Chat-Selbstheilung bei zu langem Verlauf jetzt in ALLEN Laufzeiten (#623).**
  Codex verwirft bei einem Kontextlaengen-Fehler die Sitzung, informiert den
  Nutzer und beantwortet die Nachricht frisch (wie der Claude-Pfad seit #613);
  Custom-LLM komprimiert den Verlauf im Notfall sofort, statt dass jede weitere
  Nachricht identisch scheitert. Und eine EINZELNE zu grosse Nachricht bekommt
  ueberall eine verstaendliche Erklaerung (kuerzer fassen oder als Datei in den
  Workspace) statt des rohen CLI-Fehlers. Die Fehlererkennung deckt jetzt auch
  die OpenAI-Formulierungen ab (maximum context length, exceeds the context).

## [1.267.2] - 2026-08-24

### Hinzugefuegt
- **Skill „writing-agent-ready-issues" (PR #648).** Neuer Quellordner
  `marketplace-skills/` fuer SKILL.md-basierte Marktplatz-Skills; der Skill
  fasst zusammen, wie ein Product Owner Backlog-Items agent-tauglich
  aufbereitet (Triage-Zustaende, Definition of Ready, Epic-Schnitt, Templates).

### Geaendert
- **Landingpage: iPhone-Rahmen liegt jetzt additiv um das Bild** statt es an
  den Raendern zu beschneiden (object-fit:cover entfernt — das Bild traegt
  die Groesse, der Rahmen kommt aussen dazu).
- Abhaengigkeiten: uvicorn 0.52.4 (PR #650), npm-Minor-Gruppe im Frontend
  (PR #651). sentence-transformers 6.0 (PR #649) bleibt offen, bis der
  Embedding-Pfad gegen die Major-Version getestet ist.

## [1.267.1] - 2026-08-24

### Behoben
- **Echte Umlaute in allen nutzersichtbaren Texten des Kontaktformulars.**
  Formular-Validierung, Fehlermeldungen des Endpunkts, Mailtext und
  Datenschutz-Absatz nutzten teilweise ae/oe/ue-Ersatzschreibweise.

## [1.267.0] - 2026-08-24

### Hinzugefuegt
- **Kontaktformular auf der Landingpage.** Neuer oeffentlicher Endpunkt
  `POST /api/v1/contact` stellt Name, E-Mail und Nachricht per SMTP an den
  Betreiber zu (Reply-To = Absender). Opt-in per Konfiguration
  (`CONTACT_SMTP_USER`/`CONTACT_SMTP_PASSWORD`/`CONTACT_TO` in der .env der
  Installation — ohne sie antwortet der Endpunkt mit 503, Zugangsdaten liegen
  nie im Repo). Schutz: Honeypot-Feld, 5 Nachrichten pro IP und Stunde,
  feste Empfaengeradresse. Die Datenschutzerklaerung (Seite und Overlay)
  beschreibt die Verarbeitung inkl. Brevo als Auftragsverarbeiter.

## [1.266.7] - 2026-08-24

### Behoben
- **Landingpage: Dashboard-Screenshot im Browser-Mockup nicht mehr gequetscht.**
  Das height-Attribut des Bildes gewann gegen das CSS (nur width gesetzt) —
  jetzt `height:auto` am Mockup-Bild plus generelles Sicherheitsnetz
  `img{max-width:100%;height:auto}`.

## [1.266.6] - 2026-08-24

### Geaendert
- **Landingpage auf Stand der Technik gebracht.** Scroll-Fortschrittsleiste
  (CSS scroll-driven), Maus-Spotlight und Gradient-Hover auf allen Karten,
  Bento-Raster mit breiten Schwerpunkt-Kacheln, Integrations-Marquee (M365 bis
  Redis), animierte Terminal-Demo eines echten Agenten-Laufs (tippt sich,
  respektiert prefers-reduced-motion), Akzent-Schimmer im Hero, Feinkorn-
  Textur, gestaffelte Reveals. Alles vanilla und self-contained — keine
  externen Bibliotheken, keine externen Requests.

## [1.266.5] - 2026-08-24

### Geaendert
- **Landingpage erzaehlt jetzt Plattform zuerst.** Hero mit Browser-Mockup des
  echten Dashboards („Dein KI-Team. Auf deiner Infrastruktur."), Kennzahlen-
  leiste mit Laufzeiten/M365+MCP/Kanaelen/Self-Hosted, Plattform-Sektion direkt
  danach; die iOS-App folgt als eigener Bereich („Und alles davon in deiner
  Tasche") mit allen bisherigen App-Sektionen. Navigation entsprechend
  vereinfacht (Plattform · iOS-App · Rollen · Beta).

## [1.266.4] - 2026-08-24

### Geaendert
- **Landingpage zeigt jetzt die Plattform dahinter.** Neue Sektion „Die
  Plattform dahinter" mit zwei Schwerpunkt-Karten (Microsoft 365 direkt:
  Outlook/Exchange, Graph, Teams inkl. Stimme im Termin; MCP: 12 eingebaute
  Server mit 75+ Werkzeugen, externe MCP-Server mit OAuth, Second Brain als
  MCP-Server, eigene Docker-Apps) und zwoelf Feature-Kacheln (drei Laufzeiten,
  alle Kanaele, Echtzeit-Sprache, Second Brains, Gedaechtnis, Vertrauen &
  Kontrolle, Automatisierung, Teamarbeit, Skills, PC & Browser, Modelle,
  Ticketsysteme). Hero und Kennzahlenleiste entsprechend geschaerft.

## [1.266.3] - 2026-08-24

### Behoben
- **Telegram-Sprachnachrichten an einen schlafenden Agenten gehen nicht mehr
  verloren (#645).** Zwei Luecken: (1) Der Medien-Pfad (Sprache, Fotos,
  Dokumente) weckte einen gestoppten Agenten nicht — nur Textnachrichten taten
  das. Die Nachricht lag damit in einer Warteschlange, die niemand mehr las.
  Jetzt gilt queue-first + wecken, wie beim Text. (2) Stirbt der Container
  mitten in der Verarbeitung (OOM, Neustart-Schleife beim Aufwecken), nahm
  brpop die Nachricht mit ins Grab. Jede Chat-Nachricht liegt jetzt waehrend
  der Verarbeitung in einer Inflight-Liste und wird beim naechsten Start in
  alter Reihenfolge zurueckgelegt.

## [1.266.2] - 2026-08-24

### Behoben
- **Ein Tagesplan-Block mit vergangener Startzeit feuert nicht mehr sofort (#642).**
  Beim Neuschreiben des Plans bekam JEDER Block mit Uhrzeit einen aktivierten
  Einmal-Zeitplan — auch wenn die Uhrzeit laengst vorbei war. Erledigte, erneut
  eingereichte Bloecke wurden so noch einmal beauftragt ("Arbeite ihn JETZT ab"
  auf einen ERLEDIGT-Titel), und ein um 14:00 nachgetragener 13:30-Block feuerte
  sofort. Jetzt gilt mit 5 Minuten Karenz: Vergangenheit loest nichts aus — der
  Block bleibt als Notiz sichtbar, der naechste proaktive Lauf greift ihn auf,
  falls noch offen. Dasselbe beim Verschieben eines Blocks auf eine vergangene
  Uhrzeit (Ausloeser wird deaktiviert statt sofort zu feuern).

## [1.266.1] - 2026-08-24

### Behoben
- **`.gitignore`-Muster `core` praezisiert (Wurzel + `core.[PID]`).** Das in
  1.266.0 uebernommene Muster gegen Core-Dumps deckte auch das Verzeichnis
  `orchestrator/app/core/` ab — getrackte Dateien blieben unberuehrt, aber
  jede NEUE Datei dort waere von `git add` still verschluckt worden.

## [1.266.0] - 2026-08-24

### Hinzugefuegt
- **Standard-Denktiefe pro Agent (Kundenwunsch).** In den Agenten-Einstellungen
  (Web und iOS-App, direkt bei Modell/AI-Account) laesst sich je Agent eine
  Standard-Denktiefe festlegen (Auto/Minimal/Low/Medium/High/Extra High,
  `PATCH /agents/{id}/default-reasoning`). Sie gilt ueberall dort, wo am
  einzelnen Lauf keine Stufe haengt — Aufgaben, Zeitplaene, delegierte
  Auftraege, Agent-zu-Agent-Nachrichten und Chats ohne gewaehlte Stufe — und
  zwar in ALLEN Laufzeiten (Claude Code via Denk-Budget, Codex via
  model_reasoning_effort, Custom-LLM via reasoning_effort). Eine im Chat
  gewaehlte Stufe gewinnt weiterhin; vollstaendig wirksam ab dem naechsten
  Neuerstellen des Agenten.

### Behoben
- **Ein Zeitplan, der seinen Termin verliert, meldet sich jetzt (#631, PR #633).**
  Wurde ein faelliger Lauf uebersprungen (Agent ausser Dienst, beschaeftigt,
  ueberlastet, Dispatch-Sperre), versuchte der Scheduler es eine Weile erneut
  und gab den Termin danach stillschweigend auf — Zaehler und Waechter waren
  fuer diesen Fall blind (success_rate blieb 1.0). Ein verworfener Termin wird
  jetzt als fehlgeschlagener Lauf gezaehlt und einmalig gemeldet (Name,
  Soll-Zeitpunkt, Grund); Wiederholungs-Budgets werden nach einem geglueckten
  Lauf zurueckgesetzt, und Plan-Bloecke haken sich nur noch ueber last_run_at
  ab, nicht ueber total_runs.
- **Telegram-Chat faehrt sich bei zu langem Verlauf nicht mehr fest (#613, PR #620).**
  Der Laengenfehler wird erkannt, die Sitzung neu begonnen und die Nachricht
  sofort erneut beantwortet (mit Hinweis an den Nutzer). Ausserdem schleppt
  nicht mehr jede Nachricht die volle Telegram-API-Referenz und die
  Autonomie-Regeln mit (~1.950 Token weniger pro Folgenachricht); geaenderte
  Autonomie-Regeln erreichen den Agenten weiterhin sofort, und ein
  Laengenfehler laeuft nicht mehr faelschlich in die Token-Warteschleife.
- **Meldungen von Agenten ohne eigenen Telegram-Bot kommen wieder an (#637, PR #641).**
  Der Ersatzweg ueber den globalen Bot schickte auf einen Kanal ohne Abhoerer
  und ohne das text-Feld, das der Bot vorliest — die Meldung verschwand
  spurlos. Jetzt: richtiger Kanal (telegram:notification), richtiges Format,
  auch fuer die Warnung nach einem abgestuerzten Job; stille Zustellfehler
  landen im Protokoll, und ein Form-Test haelt alle Melde-Wege gleich.

### Sicherheit
- **Sentinel: Agenten-Zuordnung kommt aus dem Kanalnamen, nicht aus der
  Selbstauskunft (#590, PR #627).** Ein Agent konnte bisher im Namen eines
  anderen einen Vorfall erfinden und ihn stoppen lassen; die Aufsicht liest
  jetzt nur noch die Kanaele je Agent, die Freifahrtschein-Ausnahme ist
  entfernt, und das harte Anhalten greift nur bei aktiver Redis-ACL (ohne ACL
  wird gemeldet statt gestoppt). Ohne Wirkung, solange der Sentinel aus ist
  (Standard).

### Hinzugefuegt
- **Ein gemeinsamer Nebenlaeufigkeitsdeckel statt drei getrennter (#628 Phase 2, PR #634).**
  Aufgaben, Chat und Agent-zu-Agent-Nachrichten teilen sich jetzt EIN
  prozessweites Budget (`agent/app/run_budget.py`) statt dreier Einzelgrenzen,
  deren Summe das Container-Prozesslimit sprengen konnte; ein Platz bleibt
  exklusiv fuer Chat reserviert, damit Gespraeche nicht hinter langlaufenden
  Aufgaben verhungern.
- **MCP-Server koennen HTTP sprechen — Schritt 1 (#638, PR #639).** Gemeinsamer
  Transport-Bootstrap `agent/mcp/_transport.mjs` (stdio wie bisher; mit
  `MCP_HTTP_PORT` bedient EIN Prozess mehrere Sitzungen unter `/mcp/<name>`,
  nur Loopback, 8-MB-Body-Deckel) plus Pilot `read-logs`. Ohne gesetzten Port
  aendert sich nichts.

## [1.265.9] - 2026-08-23

### Hinzugefuegt
- **Eigenstaendige Rechtsseiten fuer die App-Store-Pruefung.** `docs/ios-app/`
  enthaelt jetzt `datenschutz.html` und `impressum.html` als direkt verlinkbare
  Seiten (Apple verlangt eine Datenschutz-URL); die Datenschutzerklaerung
  deckt zusaetzlich die App selbst ab (keine Datenerhebung durch den Anbieter,
  Anmeldedaten nur zur eigenen Instanz, Push-Token, Mikrofon).

## [1.265.8] - 2026-08-23

### Geaendert
- **Landingpage: Impressum vervollstaendigt.** Die Platzhalter fuer
  Betreiber-Angaben und Kontakt sind durch die echten Angaben ersetzt —
  damit sind Impressumspflicht und Datenschutzerklaerung vollstaendig.

## [1.265.7] - 2026-08-23

### Geaendert
- **Landingpage: Siri-Kachel nennt jetzt die echten Kommandos** („Schreibe
  <Agent> in AI Employee Hub", Aufgabe anlegen) statt einer Phrase, die die
  App nicht registriert.

## [1.265.6] - 2026-08-23

### Behoben
- **Landingpage: „Alles klar" schliesst die Hinweis-Leiste jetzt wirklich.**
  Das `display:flex` der Leiste hatte das `hidden`-Attribut ueberstimmt — der
  Klick wurde gespeichert, die Leiste blieb aber sichtbar.

## [1.265.5] - 2026-08-23

### Geaendert
- **Landingpage-Mockups sehen jetzt nach iPhone aus:** duenner dunkler Rahmen
  wie beim Geraet, dazu ein schmaler schwarzer Glasrand zwischen Screen-Inhalt
  und Rahmen (statt des breiten Bezels aus 1.265.4).

## [1.265.4] - 2026-08-23

### Geaendert
- **Landingpage: Geraete-Mockups mit echtem Bezel.** Die App-Screens sitzen
  jetzt mit sichtbarem Rand im Rahmen statt randlos; letzter
  Umlaut-Nachzuegler (Team-Zugehoerigkeit) korrigiert.

## [1.265.3] - 2026-08-23

### Geaendert
- **Landingpage rechtssicher und rund.** Impressum und Datenschutzerklaerung
  (als Overlay, im Footer verlinkt; Betreiber-Angaben als auszufuellende
  Platzhalter), Hinweis-Leiste unten (kein Tracking, nur lokale Bestaetigung),
  Schriften lokal eingebettet statt von Google Fonts geladen (kein externer
  Request), echte Umlaute im gesamten Text, Engines-Angabe korrigiert (Claude
  Code, Codex UND Custom-LLM) und drei fehlende Funktionen ergaenzt
  (Aufgaben & Zeitplaene, Task-Timeline, Integrationen).

## [1.265.2] - 2026-08-23

### Geaendert
- **Landingpage ohne Zugaenge zur Web-Oberflaeche.** „Anmelden" im Menue,
  „Web-Oberflaeche oeffnen" im Hero und der Anmelde-Knopf im Beta-Abschnitt
  sind entfernt — die Seite nennt keinerlei Einstieg in die Plattform mehr.

## [1.265.1] - 2026-08-23

### Geaendert
- **Die Landingpage der iOS-App wird NICHT mehr standardmaessig ausgeliefert.**
  Route und Mount aus 1.265.0 sind aus `Caddyfile` und `docker-compose.yml`
  wieder entfernt — Installationen zeigen unveraendert die Web-Oberflaeche.
  Die Seite liegt nur noch als Datei unter `docs/ios-app/`; wer sie auf einer
  Installation zeigen will, verdrahtet sie deployment-spezifisch ueber
  `docker-compose.override.yml` (Mount) und ein `conf.d/site/`-Snippet
  (Anleitung in `conf.d/README.md`).

## [1.265.0] - 2026-08-23

### Hinzugefuegt
- **Landingpage der iOS-Begleit-App wird von jeder Installation ausgeliefert.**
  Unter `/app/` liefert Caddy eine statische Seite (`docs/ios-app/index.html`),
  die die App vorstellt: Agenten, Chat, Sprache, Wissen in 3D, Mein PC, Analyse,
  Rollen und der Einstieg ueber TestFlight. Die Seite ist eigenstaendig (Bilder
  eingebettet) und verlinkt auf `/login` der jeweiligen Installation. Soll sie
  auf einer Installation die Startseite sein, reicht ein Snippet in
  `conf.d/site/` (Beispiel in `conf.d/README.md`); die Web-Oberflaeche bleibt
  sonst unveraendert. Nach dem Update `docker compose up -d caddy`, damit der
  neue Mount greift.

## [1.264.5] - 2026-08-23

### Behoben
- **Ein Zeitplan, der wegen eines gestoppten Agenten ausfaellt, ist jetzt
  auffindbar.** War der Agent zur faelligen Uhrzeit nicht ansprechbar, brach der
  Zeitplan ab, *bevor* ueberhaupt ein Auftrag entstand: der Lauf hinterliess
  weder einen fehlgeschlagenen noch einen offenen Eintrag — in keiner Liste, fuer
  niemanden. Aus Sicht des Betreibers hatte er nie stattgefunden, und das
  fehlende Ergebnis war der einzige Hinweis. Ein taeglicher Job konnte so an
  einem Drittel der Tage ausfallen, ohne dass es jemandem auffiel. Ein
  ausgefallener Lauf wird jetzt als fehlgeschlagener Auftrag verbucht — mit
  Zeitplan, Soll-Uhrzeit und Grund — und genau einmal pro verpasstem Termin,
  nicht einmal pro Pruefung.
- **Die Ausfallmeldung sagt nicht mehr das Gegenteil dessen, was passiert ist.**
  Hatte der Agent gerade keine offene Aufgabe, meldete das System woertlich „es
  geht also nichts verloren" — obwohl der faellige Lauf genau in diesem Moment
  verloren ging. Die Meldung nennt jetzt den betroffenen Zeitplan und ist hoch
  genug eingestuft, um auch per Telegram anzukommen.
- **Der Ausfall eines taeglichen Jobs wird nicht mehr verschluckt.** Die
  Ausfallmeldung hing an einer Sperre, die pro Agent zwoelf Stunden lang alles
  Weitere unterdrueckte: hatte derselbe Agent aus irgendeinem anderen Grund schon
  gemeldet, blieb der verpasste Lauf still. Sie zaehlt jetzt pro Zeitplan.

---

## [1.264.4] - 2026-08-23

### Behoben
- **Ein gestoppter Agent wird fuer faellige Zeitplaene und Kalender-Bloecke
  geweckt.** Agenten schlafen nach Nutzer-Inaktivitaet ein (UserLifecycle,
  Standard 30 Minuten). Stand danach ein Zeitplan an, galt der Agent als
  ausgefallen: kein Lauf, keine Verschiebung, keine brauchbare Meldung — und
  jeder 30-Sekunden-Takt meldete denselben Ausfall neu. Der taegliche
  Podcast fehlte so an rund jedem dritten Tag (#632). Jetzt startet der
  Scheduler den Agenten zuerst (derselbe Weg wie bei Nachrichten zwischen
  Agenten) und fuehrt den Lauf aus; gelingt das Wecken nicht, wird eskaliert
  und der Lauf kurz nachgesetzt statt fuer immer zu haengen.
- **Kein Schlafenlegen kurz vor einem Termin.** Das Auto-Stoppen laesst
  Agenten wach, wenn binnen der Ruhefrist ein aktiver Zeitplan ansteht —
  spart den Kaltstart genau zur Feuerzeit.

---

## [1.264.3] - 2026-08-22

### Hinzugefuegt
- **Freigabe-Pushes tragen jetzt eine Aktions-Kategorie.** Die iOS-App (ab
  1.2.3) zeigt damit Genehmigen/Ablehnen direkt auf der Push-Mitteilung an —
  eine Freigabe laesst sich vom Sperrbildschirm beantworten, ohne die App zu
  oeffnen (Geraet muss entsperrt werden, bewusste Sicherheitsentscheidung).

## [1.264.2] - 2026-08-22

### Behoben
- **Sprachsteuerung: Ein Content-Filter-Block sagt jetzt, was hilft.** Blockt
  der Modell-Anbieter (AWS) den beim Verbindungsaufbau geladenen
  Gespraechsverlauf, kam bisher nur die rohe Fehlermeldung samt RequestId an —
  und jeder Neuversuch lief in denselben Block. Die Meldung erklaert jetzt,
  dass der geladene Verlauf betroffen ist (nicht die eigene Frage) und ein
  neues Gespraech die Sprachsteuerung wieder freigibt; sie ist ausdruecklich
  als nicht-wiederholbar markiert, damit Clients nicht im Kreis neu verbinden.

---

## [1.264.1] - 2026-08-22

### Behoben
- **Sprachsitzungen mit Nova Sonic starten wieder.** Das AWS-SDK für den
  Sprachkanal (`aws-sdk-bedrock-runtime`) hat in Version 0.10 seine Klassen
  umbenannt und verbietet den bisherigen Aufbauweg — auf einer Anlage mit
  neuem Container-Image brach damit JEDE Sprachsitzung sofort ab
  („cannot import name 'Config'"). Der Sprachkanal kann jetzt mit beiden
  SDK-Generationen umgehen; die Microsoft-Realtime-Anbindung (Azure) war
  nicht betroffen und bleibt unverändert. Ein neuer Test fährt den echten
  Aufbauweg gegen die tatsächlich installierte SDK-Version, damit die
  nächste Umbenennung auffällt, bevor sie eine Anlage trifft.

---

## [1.264.0] - 2026-08-21

### Behoben
- **Eine Aufgabe, der die Prozesse ausgehen, gilt nicht mehr als erledigt.**
  Der Container hat eine harte Obergrenze an gleichzeitigen Prozessen. War sie
  erreicht, ließ sich kein Werkzeug mehr starten — `git`, `gh`, Tests, alles
  scheiterte still. Der Lauf merkte davon nichts und meldete Erfolg. So kamen
  Aufträge als „erledigt" zurück, zu denen es weder einen Pull Request noch
  eine Datei gab; das sah nach einem Modell- oder Prompt-Problem aus und war
  keines. Solche Läufe scheitern jetzt sichtbar, mit dem gemessenen
  Prozessstand und der Zeile, an der es riss.

### Geändert
- **Wie viele Aufträge ein Agent gleichzeitig annimmt, richtet sich jetzt nach
  dem, was der Container hergibt** — vorher war es eine geratene Zahl. Wer mehr
  einstellt, als hineinpasst, bekommt die passende Zahl und einen Hinweis im
  Protokoll; überzählige Aufträge warten in der Schlange, statt den Container
  zu erdrosseln. Feinjustierung über `PIDS_RESERVE` und `PIDS_COST_PER_RUN`.

Damit ist der erste Teil von #628 erledigt. Das Anheben der Obergrenze selbst
und ein gemeinsamer Satz Hintergrunddienste stehen noch aus.

---

## [1.263.1] - 2026-08-21

### Behoben
- **Der Hinweis „Veraltete Oberfläche — neu laden" konnte nie erscheinen.** Er
  vergleicht die im Bundle eingebackene Version mit der des Backends; wird das
  Frontend-Image ohne `APP_VERSION` gebaut, steht dort „dev" und der Abgleich
  fällt still aus. Folge: nach einem Deploy blieb die geöffnete Seite auf dem
  alten Stand, ohne dass irgendetwas darauf hinwies. Ein Produktions-Bundle
  ohne Version meldet sich jetzt in der Konsole statt stumm zu bleiben.
- **Der Abgleich lief nur alle 30 Minuten.** Wer die Seite lange offen hält,
  erfuhr von einem Deploy entsprechend spät. Beim Zurückkommen auf den Tab wird
  jetzt sofort nachgefragt.

---

## [1.263.0] - 2026-08-21

### Behoben
- **Mehrere Bilder lagen übereinander statt nebeneinander.** Die Bühne der
  Sprachansicht holte sich genau EIN Element — das neueste. Jedes weitere Bild
  lag zwar vor, aber unsichtbar darunter: wer beide Bildschirme aufnahm, sah
  trotzdem nur einen, ohne jede Meldung. Jetzt stehen bis zu vier Anzeigen
  nebeneinander, jede einzeln schliessbar, und die Bühne macht dafür von selbst
  auf. Sind mehr da, wird das gesagt statt stillschweigend abgeschnitten.
- **Screenshots hiessen alle gleich.** Beide Bildschirme trugen die Beschriftung
  „Bildschirm des Nutzers" — nebeneinander nicht auseinanderzuhalten. Jetzt
  stehen Nummer und Bildgrösse darunter.

---

## [1.262.0] - 2026-08-21

### Neu
- **Gespräch und Aufgaben lassen sich wegklappen — die Anzeige in der Mitte wird
  gross.** In der Sprachansicht liegt der Screenshot des Nutzer-Bildschirms
  zwischen zwei Spalten und war auf 28rem Breite festgenagelt; erkennen liess
  sich darauf wenig. Beide Seitenspalten haben jetzt einen Knopf zum Einklappen,
  übrig bleibt eine schmale Leiste mit Beschriftung und Zähler. Die Mitte nimmt
  den frei gewordenen Platz, die Bühne löst dabei ihre Breiten- und
  Höhenbegrenzung. Die Einstellung wird gemerkt.

---

## [1.261.0] - 2026-08-21

### Behoben
- **Klicks auf dem zweiten Monitor landeten auf dem ersten.** Die Bildschirm-
  Auswahl galt bisher nur für die Aufnahme; ein Klick benutzte den Versatz des
  zuletzt aufgenommenen Bildschirms. Wer also nach dem Screenshot von Nummer 2
  klickte, traf richtig — dazwischen ein Blick auf Nummer 1, und derselbe Klick
  ging daneben. `click`, `move`, `scroll` und `drag` nehmen die Bildschirmnummer
  jetzt selbst entgegen und rechnen mit deren Ursprung.
- **Geratene Koordinaten.** Das Modell setzte Klicks auf Beispielwerte (x=123,
  y=456), ohne vorher hingesehen zu haben. Die Werkzeugbeschreibung sagt jetzt
  ausdrücklich: Koordinaten nur aus `find` oder aus einem unmittelbar davor
  gemachten Screenshot — niemals raten.
- **„Die Auswertung kam nicht zurück" verschwieg den Grund.** Die Bildauswertung
  scheiterte in Wahrheit an einem erschöpften Modell-Kontingent; die Sprachfront
  warf diese Begründung weg und liess den Nutzer beim Bild suchen. Der Grund wird
  jetzt im Wortlaut weitergereicht und vorgelesen.

---

## [1.260.0] - 2026-08-21

### Neu
- **Screenshots von jedem Bildschirm, nicht nur vom ersten.** Die Bridge nahm
  ausschliesslich den Hauptbildschirm auf — ein zweiter Monitor war unerreichbar.
  Jetzt lässt sich sagen „geh auf Bildschirm 2"; Nummer 1 ist immer der
  Hauptbildschirm, also die Zählweise, die man am Telefon benutzt.
- **Der Agent weiss jetzt, wie gross das Bild ist.** Die Bridge rechnete
  `image_size` seit jeher aus und gab es zurück — im Orchestrator und im Agenten
  kam es **nirgends** vor. Das Modell nannte Klickkoordinaten, ohne die Bildgrösse
  zu kennen. Jede Antwort auf einen Screenshot nennt sie jetzt, samt „(0,0) ist
  oben links" und, bei mehreren Monitoren, welche es gibt.
- In **allen drei Laufzeiten** — Sprachfront, MCP (Claude Code/Codex) und
  Custom-LLM. Die Bildschirmliste erscheint nur, wenn es wirklich mehrere gibt;
  bei einem Monitor wäre sie nur Rauschen im Kontext.

### Behoben
- **Klicks auf dem zweiten Monitor landeten auf dem ersten.** Ein Nebenbildschirm
  beginnt nicht bei 0/0 — geklickt wird aber über alle Monitore hinweg in einem
  gemeinsamen Raum. Der Ursprung des aufgenommenen Bildschirms wird jetzt
  mitgeführt und auf jede Klick-, Bewegungs- und Scroll-Koordinate gerechnet;
  der Rückweg zieht ihn wieder ab, damit beide Richtungen Umkehrungen bleiben.

---

## [1.259.0] - 2026-08-21

### Behoben
- **„Abbrechen" brach nichts ab — und meldete trotzdem Erfolg.** Gemeldet mit
  vollständigem Gesprächsprotokoll: dreimal „abbrechen", dreimal „Beide Aufgaben
  wurden gestoppt" — und die Aufgabe lief Stunden später immer noch.
- Vier Schichten desselben Problems, alle nachgewiesen:
  1. Die Sprachfront meldete Erfolg, sobald ein Redis-`publish` ohne Fehler
     zurückkam. Ein publish gelingt aber auch, wenn **niemand zuhört**.
  2. Sie kannte nur die Aufgaben aus **dieser** Sitzung. Das Gespräch war
     fortgesetzt, die Menge also leer.
  3. Der Abbruch wies **laufende** Aufgaben grundsätzlich mit einem Fehler ab —
     es gab keinen Weg, eine laufende Aufgabe zu stoppen.
  4. Der Kanal `task:cancel` wurde seit jeher besendet und hatte **keinen
     einzigen Zuhörer**.
- Der Agent hört jetzt auf diesem Kanal und bricht die benannte Aufgabe wirklich
  ab. Der Zuhörer läuft neben der Warteschlange — in derselben Schleife käme er
  erst dran, wenn gerade nichts verarbeitet wird, also genau dann nicht, wenn man
  ihn braucht.
- Die Sprachfront holt jetzt **alle** offenen Aufgaben des Agenten, bricht sie ab
  und **sieht danach noch einmal nach**. Überlebt etwas, sagt sie das — mit Namen
  — statt Erfolg zu behaupten.

### Neu
- **Manueller Stopp für laufende Aufgaben.** In der Aufgabenliste hat eine
  laufende Aufgabe jetzt einen roten „Stoppen"-Knopf, der **ohne Überfahren**
  sichtbar ist — wer etwas anhalten will, sucht den Knopf sofort. Wartende
  Aufgaben behalten ihr dezentes „Abbrechen": eine wartende nimmt man aus der
  Schlange, eine laufende unterbricht man.

---

## [1.258.0] - 2026-08-21

### Neu
- **Ordner lassen sich als ZIP herunterladen** — an beiden Stellen, an denen es
  gewünscht war: als **Export**-Knopf auf jeder App-Karte (nimmt das
  Verzeichnis dieser App mit) und als Download-Symbol an jedem Ordner im
  Dateibaum (in beiden Dateibäumen).
- Entpackt entsteht wieder ein Ordner, keine Dateiwolke im Download-Verzeichnis.
- **`node_modules`, `.git`, `__pycache__`, `.venv` und Ähnliches bleiben
  draußen.** In einem Projektordner machen die leicht das Tausendfache des
  eigentlichen Codes aus, und alles darin ist aus dem Rest wiederherstellbar.
  Der Knopf sagt das auch dazu. Ist ein Ordner trotzdem zu groß, kommt eine
  lesbare Meldung statt eines Zeitablaufs.
- Der Pfad wird gepackt, ohne im Container ein `zip` vorauszusetzen — ein
  Export, der je nach Abbild funktioniert oder nicht, ist keiner.
- Beide Oberflächen benutzen **denselben Endpunkt** mit denselben Wachen wie die
  übrigen Dateiwege (Anmeldung, Eigentümer, Pfad-Riegel).

### Behoben
- **Laufende Apps ohne bekannten Pfad.** Wurde eine App gestartet, ohne dass ihre
  compose-Datei beim Durchsuchen gefunden wurde, stand kein Pfad an ihr — damit
  ließ sich das Verzeichnis weder anzeigen noch exportieren. Docker trägt den
  Arbeitspfad als Label mit; genau der wird jetzt als Rückfallebene benutzt.

---

## [1.257.0] - 2026-08-21

### Behoben
- **Die Stimme las Markdown mit vor** — „Punkte zur Performance: n n- Echtzeit-Fähigkeit\*\*:"
  — und liess angekündigte Aufzählungen manchmal ganz weg („Hier sind die
  Hauptoptionen und was du beachten solltest:" … und dann nichts).
- **Es war kein Escape-Fehler in unserem Code.** Zwei Vermutungen wurden geprüft
  und verworfen: die Textsäuberung entfernt keine Backslashes, und weder
  Erinnerungen noch Gesprächsverlauf enthalten literale `\n`-Folgen. Der
  gespeicherte Text enthält weder Backslash noch Zeilenumbruch — das „n" steht
  schon so in dem, was die Engine zurückgibt.
- Es ist eine Formatierungsgewohnheit des Modells: fürs Auge geschrieben, obwohl
  es fürs Ohr ist. Die Anweisung dagegen steht jetzt als **erste Regel** im
  Sprach-Systemprompt, nennt die verbotenen Zeichen beim Namen, sagt warum
  („werden LAUT MITGESPROCHEN") und bietet den Ersatz an („erstens … zweitens …").
- Der abgebrochene Doppelpunkt ist dieselbe Ursache und ausdrücklich mit geregelt.

### Hinweis
- Bei einem Sprache-zu-Sprache-Modell gibt es dagegen **keinen mechanischen
  Hebel** — gesprochen ist gesprochen, die Audioausgabe lässt sich nicht
  nachträglich säubern. Das Transkript bleibt deshalb bewusst ungeschönt: es
  soll zeigen, was wirklich gesagt wurde, statt den Fehler unsichtbar zu machen.

---

## [1.256.0] - 2026-08-21

### Behoben
- **Im Sprachmodus wurde mitten im Satz ein „n" vorgelesen.** Gemeldet mit
  Bildschirmfoto: „Hier sind ein paar Treffer zu Inside AI auf YouTube: **n n1.**
  InsideAI\*\* – …". Zwei Fehler in einem Satz — ein zerbrochener Zeilenumbruch
  und übrig gebliebenes Markdown.
- Der Hergang ist lehrreich, weil die Reparatur schon einmal da war: eine
  Säuberung wandelt literale `\n`-Folgen in echte Umbrüche zurück (ihr Kommentar
  beschreibt genau dieses Symptom) — aber das Werkzeug-Ergebnis wird danach ein
  **zweites Mal** kodiert, und dabei wird aus dem echten Umbruch wieder das
  sichtbare Zeichenpaar. Die Sprach-Engine reicht es wörtlich ans Modell, das den
  Backslash nicht sprechen kann; übrig bleibt das „n".
- Für gesprochenen Text trägt ein Umbruch ohnehin keine Bedeutung: ein Absatz
  wird jetzt zur Sprechpause, eine Zeile zum Leerzeichen. Steht davor schon ein
  Satzzeichen, kommt kein zweiter Punkt dazu.
- **Markdown wird nicht mehr mitgesprochen** — Sternchen, Backticks,
  Überschriften-Rauten verschwinden, der Text bleibt. Aus `[Doku](…)` wird
  „Doku", nicht die Adresse.
- Der Eingriff bleibt auf den Weg beschränkt, der tatsächlich doppelt kodiert:
  eingespielte Zwischenmeldungen gehen direkt an die Engine, dort gab es das
  Problem nie.

---

## [1.255.0] - 2026-08-19

### Behoben
- **Ein delegierter Auftrag lief unter dem Modell des Auftraggebers, nicht des
  Zielagenten.** Jedes Delegier-Werkzeug hängte `model: DEFAULT_MODEL` an — das
  Modell dessen, der delegiert. Ein Kollege arbeitete damit unter einem Modell,
  das er sich nie ausgesucht hat.
- Zweite, unsichtbare Folge: der **Model-Router des Zielagenten kam nie zum
  Zug**. Der Orchestrator fragt ihn ausdrücklich nur, wenn *kein* Modell mitkam.
  Bei Delegation war er damit strukturell wirkungslos — unabhängig davon, ob er
  eingeschaltet war.
- Jetzt wird bei `create_task`, `create_task_batch`, `delegate_and_wait` und
  `create_schedule` **kein Modell mehr mitgeschickt**. Der Orchestrator fällt
  damit auf genau das zurück, was dokumentiert ist: „we leave it None so the
  agent falls back to its own default". Ein ausdrücklich gewähltes Modell (etwa
  beim Trigger) geht unverändert durch.
- Der Custom-LLM-Weg machte es ohnehin schon richtig — die Lücke bestand nur im
  MCP-Weg, also bei Claude Code und Codex. Wieder eine Paritätslücke zwischen den
  Laufzeiten.

---

## [1.254.0] - 2026-08-19

### Behoben
- **Der Model-Router war eingeschaltet und wirkungslos.** Auf die Frage „funktioniert
  eigentlich der model router?" nachgesehen: er war bei genau einem Agenten aktiv, und
  seine Regeln waren drei leere Zeichenketten.
- Ursache war die Oberfläche: die Vorgaben standen nur als **Platzhalter** in den
  Feldern. Wer den Router einschaltete, sah Modellnamen und durfte annehmen, sie seien
  gesetzt — gespeichert wurde der leere String.
- `route_model` gab diese leeren Regeln zurück, obwohl der eigene Docstring „or None if
  the resolved tier has no rule configured" verspricht. Folge: **146 Aufträge mit leerem
  Modellnamen** in sieben Tagen. Aufgefallen ist es nie, weil der Agent am Ende auf seine
  Vorgabe zurückfiel — der Router entschied, und die Entscheidung war jedes Mal nichts.
- Leere Regeln zählen jetzt als „nicht konfiguriert" und fallen auf die eingebauten
  Vorgaben zurück.

- **„Tendenz: schlechter" stand auf vier Aufgaben.** In der Entwicklungs-Karte wurden
  212 junge gegen **vier** alte Aufgaben verglichen — die alle gescheitert waren und
  deshalb gar nicht erst nachgearbeitet werden konnten. Die Fehlerquote war von 100 % auf
  7,1 % gefallen, das Urteil lautete trotzdem „schlechter".
- Der Vergleich verlangt jetzt eine Mindestmenge **je Hälfte**; sonst steht dort „zu
  wenig Daten" statt eines Urteils.

### Geändert
- **Der Model-Router wird per Auswahlliste gepflegt statt per Freitext.** Angeboten
  werden nur vom Administrator freigegebene Modelle — dieselbe Quelle wie beim Anlegen
  eines Agenten. Die Vorgaben sind echte Werte, keine Platzhalter mehr, und eine Auswahl
  wird sofort gespeichert.
- Ein bereits hinterlegtes, inzwischen gesperrtes Modell bleibt sichtbar und als „nicht
  freigegeben" markiert, statt die Auswahl stumm auf etwas anderes springen zu lassen.

---

## [1.253.0] - 2026-08-19

### Geändert
- **Agenten kennen ihr Arbeitsbudget und enden würdig, statt abgeschnitten zu
  werden.** Anlass: „die agents machen nicht mehr sauber mit". Die eigenen
  Selbstbewertungen der Anlage zeigen das Muster — Note 5 bei 1–10 Schritten,
  Note 3 bei 27–75. Je länger ein Lauf ohne Korrektur, desto schlechter das
  Ergebnis.
- Naheliegend wäre, die maximale Schrittzahl zu senken. Beide Anbieter raten
  davon ab: Anthropic unterscheidet einen Deckel, „the model is not aware of",
  von einem Budget, mit dem es sich einteilt und „gracefully" endet; OpenAI fängt
  das Limit ab und bittet um Eingrenzung. Ein kleinerer Deckel schneidet früher
  ab, statt besser zu werden.
- Der Custom-LLM-Harness sagt dem Agenten jetzt gegen Ende, wie viele Schritte
  ihm bleiben. Ist das Budget auf, endete die Schleife bisher **still** — jetzt
  folgt ein letzter Schritt ohne Werkzeuge, in dem der Agent zusammenfasst, was
  fertig ist, was offen bleibt und was der nächste Schritt wäre.
- **Rückfragen hängen nicht mehr am Bauchgefühl, sondern an der
  Umkehrbarkeit.** Anthropics Kriterium wörtlich übernommen: leicht rückgängig
  zu machen → weitermachen mit einer vernünftigen Annahme; schwer rückgängig
  (an Kollegen delegieren, Nachricht senden, Termin anlegen, veröffentlichen,
  löschen) → vorher fragen, wenn der Bezug nicht in diesem Gespräch genannt oder
  frisch nachgeschlagen wurde.
- Der Agent ist ein schlechter Richter über seine eigene Sicherheit: am Vortag
  schrieb er „am wahrscheinlichsten" und schickte trotzdem drei Kollegen auf das
  falsche Projekt.

### Neu
- **Jede Laufzeit bekommt den Rat ihres eigenen Anbieters.** Der übrige
  Anleitungstext bleibt bewusst für alle gleich; unterschiedlich ist nur, wie die
  Laufzeit ihre Schleife führt:
  - **Claude Code** — Selbstprüfung auf Takt statt erst am Ende (Anthropic
    empfiehlt genau das für lange Läufe)
  - **Codex/OpenAI** — bei zu grosser Aufgabe sauber schneiden und vorschlagen,
    wie man sie teilt, statt mittendrin abzubrechen
  - **Custom-LLM** — die Budget-Meldungen unseres Harness sind angekündigt,
    damit sie nicht aus dem Nichts kommen

---

## [1.252.1] - 2026-08-19

### Behoben
- **Das Reglerfeld im Sprachmodus war zu schmal.** Der Griff des
  Lautstärkereglers stand rechts über den Rahmen hinaus. Das Feld nutzt jetzt die
  verfügbare Breite (bis zu einer sinnvollen Obergrenze), und die Regler dürfen
  unter ihre Inhaltsbreite schrumpfen — genau das fehlte und liess den Griff
  herausragen.

---

## [1.252.0] - 2026-08-19

### Neu
- **„Neu verbinden" statt nur „Auflegen".** Bei einem Sprachfehler blieb bisher
  nur das Auflegen — auch dann, wenn ein zweiter Anlauf gereicht hätte. Jetzt
  steht ein Knopf unter der Meldung, und darunter der Hinweis, dass das Gespräch
  fortgesetzt und nicht neu begonnen wird.
- Der Wiederaufbau ist derselbe wie beim automatischen: er lädt das bisherige
  Gespräch nach, der Agent redet weiter statt neu zu begrüßen.
- Bewusst **kein** automatischer Neuversuch bei Eingabefehlern der Engine
  (`Invalid input request`): eine Eingabeprüfung zu wiederholen bringt denselben
  Fehler und verdeckt die eigentliche Ursache. Zeitüberschreitungen und
  Drosselungen bauen weiterhin von selbst neu auf.

### Geändert
- **Lautstärke- und Mikrofonregler sind jetzt beschriftet.** Vorher standen zwei
  gleich aussehende graue Regler untereinander, unterschieden nur durch ein
  kleines Symbol. Jetzt in einem gemeinsamen Feld mit Namen, farbigen Symbolen
  und einem Satz, was die aktuelle Mikrofon-Einstellung bewirkt.

### Behoben
- **Diagnose für „Invalid input request".** Der Fehler trat zweimal auf, und im
  Log stand nur die Meldung — nicht, worauf sie folgte. Das zuletzt gesendete
  Ereignis (Art und Grösse, Audio ausgenommen) wird jetzt mitgeschrieben und in
  der Fehlermeldung genannt. Die Ursache selbst ist damit noch nicht gefunden,
  aber beim nächsten Mal nachvollziehbar.

---

## [1.251.0] - 2026-08-19

### Neu
- **Mikrofon-Empfindlichkeit im Sprachmodus, einstellbar mitten im Gespräch.**
  Gemeldet: „speech reagiert zu schnell auf Töne" — man konnte es nicht
  einstellen.
- Ursache: die Tonschleife schickte **jeden** Frame an die Engine, egal wie
  leise. Die Sprecherwechsel-Erkennung sitzt bei Nova Sonic im Modell; sie bekam
  also jedes Umgebungsgeräusch zu hören und entschied selbst, darauf zu
  reagieren. Die beiden Schwellen standen zudem fest im Quelltext.
- Neuer Regler in der Gesprächsansicht. Er wirkt **sofort, ohne Neuaufbau der
  Sitzung** — das Rauschtor ist unser Code in der Tonkette, kein Parameter der
  Engine. Ein Neuaufbau wäre hier eine Unterbrechung ohne Gegenwert.
- Unterhalb der Schwelle wird **Stille gesendet statt gar nichts**: der Tonstrom
  muss lückenlos bleiben, sonst gerät die Erkennung der Engine aus dem Takt.
- Ein Nachlauf verhindert, dass leise Endsilben abgeschnitten werden. Auf 0
  gestellt ist das Tor aus und alles verhält sich wie vorher.
- Das Unterbrechen (Barge-in) folgt derselben Einstellung — sonst hätte der
  Regler nur das Zuhören beeinflusst.
- Der Wert liegt **lokal am Gerät**, nicht am Agenten: Mikrofon und Raum gehören
  zum Arbeitsplatz, derselbe Agent braucht anderswo einen anderen Wert.

---

## [1.250.2] - 2026-08-19

### Geändert
- **Reasoning-Menü mit ChatGPT-naher Benennung.** Die Denktiefe-Stufen heißen
  jetzt Auto / Minimal / Low / Medium / High / Extra High statt der deutschen
  Prosa-Labels. Die internen Werte (off/low/medium/high/max) bleiben unverändert;
  „Extra High" wird serverseitig zu `xhigh` bzw. auf `high` geclampt.

---

## [1.250.0] - 2026-08-19

### Hinzugefügt
- **Token-Verbrauch pro Nachricht im Chat sichtbar — inkl. Feinaufschlüsselung.**
  Die Meta-Zeile unter einer Antwort zeigt jetzt neben Dauer/Kosten/Ein-/Ausgabe-
  Tokens zusätzlich `reasoning`-Tokens (das „Nachdenken"), `cached`-Tokens
  (Prompt-Cache-Treffer) und `cache-write`-Tokens, sofern der Provider sie
  meldet. Damit lässt sich direkt ablesen, ob eine höhere Reasoning-Stufe den
  Verbrauch verändert. Erfasst über **alle Harnesse**: Custom LLM / OpenAI
  (Responses- und Chat-Completions-API), Claude (Anthropic-Cache-Tokens),
  Codex und GPT. Felder werden nur angezeigt, wenn sie tatsächlich gemeldet
  wurden (keine leeren Nullwerte). Kein Schema-Change — die Werte reisen im
  vorhandenen Nachrichten-Meta.

---

## [1.249.0] - 2026-08-19

### Hinzugefügt (M365)
- **Transkript-zu-Aufgaben (Meeting-Aufgaben-Agent).** Liest pro User die
  Teams-Transkripte der eigenen Meetings über Microsoft Graph (delegiert,
  nicht App-only), extrahiert die Aufgaben des angemeldeten Users und legt sie
  als To-dos unter „Meine Aufgaben" an — bewusst keinem Plan zugeordnet, der
  User sortiert selbst (Human in the loop). Zwei neue delegierte Graph-Scopes
  optional wählbar (`OnlineMeetings.Read`, `OnlineMeetingTranscript.Read.All`,
  Admin-Consent erforderlich); ohne Freigabe ändert sich nichts. Der Agent
  liest nur Transkripte, die der eigene User in Teams selbst öffnen könnte;
  fremde Meetings sind serverseitig unerreichbar, Aufgaben anderer Personen
  werden nie angelegt, Duplikatschutz über die transcript_id.

---

## [1.248.0] - 2026-08-19

### Hinzugefügt (M365-Mail)
- **Mail-Ordner und Posteingangsregeln nativ über MS Graph.** Bisher konnte ein
  Agent Mail nur in feste Standardordner (inbox/sent/…) verschieben — ein
  eigener Ordner fiel still auf „inbox" zurück; Ordner auflisten/anlegen oder
  Regeln erstellen war nur umständlich über die Desktop-Bridge möglich. Neu:
  - `ms_list_mail_folders` (read-only) — listet alle Ordner inkl. eigener
    Unterordner mit ID und Ungelesen-Zähler.
  - `ms_create_mail_folder` (write) — legt einen Ordner an, optional als
    Unterordner.
  - `ms_create_mail_rule` / `ms_list_mail_rules` — Posteingangsregeln, die
    eintreffende Mail automatisch einsortieren (Bedingung Betreff/Absender →
    in Ordner verschieben, optional als gelesen markieren).
  - `ms_move_email` akzeptiert jetzt zusätzlich eine Ordner-ID (aus
    `ms_list_mail_folders`) als Ziel, nicht nur die festen Standardordner.

  Alles read/write-korrekt gegatet und mit dem bereits vorhandenen
  `Mail.ReadWrite`-Scope — keine neue Zustimmung nötig.

---

## [1.247.0] - 2026-08-19

### Geändert
- **Gespräche werden vom Modell zuverlässiger benannt.** Die Anweisung zum
  automatischen Umbenennen war weich formuliert („sobald klar ist, worum es
  geht"), sodass viele Sprachgespräche beim Standardnamen blieben. Sie ist jetzt
  verbindlich: spätestens nach der ersten inhaltlichen Nutzeräußerung wird genau
  einmal ein kurzer thematischer Titel gesetzt; der Standardname bleibt nicht
  stehen.
- **Speech-Ansicht: Feinschliff an Verlauf-Ausblendung und Button-Dock** —
  weicherer, höher ansetzender Fade und ruhigeres Dock, damit der Übergang vom
  Verlauf zum „Gespräch weiterführen"-Button gleichmäßiger wirkt.

---

## [1.246.0] - 2026-08-19

### Neu
- **Die gewählte Denktiefe sprang bei jedem Chat-Wechsel auf „Auto" zurück.** Der
  Selector im Chat lebte nur im Oberflächen-Zustand – Seite neu geladen, Gespräch
  gewechselt oder Chat aus der Übersicht geöffnet, und die Wahl war weg. Jetzt
  merkt sich **jedes Gespräch seine eigene Denktiefe** (gespeichert wie Titel und
  Pin), der Knopf zeigt das aktive Level auch nach einem Neuladen. Ein neuer Chat
  übernimmt die zuletzt gewählte Stufe; „Standard" wählen stellt bewusst auf Auto
  zurück. Abzweig und Fortsetzung erben die Stufe des Ursprungsgesprächs.
- **Neue Stufe „Maximal nachdenken".** Bei GPT-/Codex-Modellen wird sie als
  `xhigh` durchgereicht; Modelle, die das nicht kennen (o-Serie, Fremd-Endpunkte,
  Claude), erhalten sicher die höchste Stufe, die sie akzeptieren – statt eines
  API-Fehlers bei jeder Nachricht.

### Behoben
- Ein frisch gewähltes Level wurde ohne weiteren Tastendruck **veraltet
  mitgesendet** – die Sende-Funktion sah den neuen Wert nicht (fehlende
  React-Abhängigkeit). Die Wahl gilt jetzt sofort für die nächste Nachricht.
- Die Level-Whitelist im Sende-Pfad war ein eigenes, hartkodiertes Tuple und
  konnte von der Oberfläche abweichen. Beide Prüfstellen (Senden + Speichern)
  nutzen jetzt dieselbe Konstante.

---

## [1.245.0] - 2026-08-19

### Geändert (Speech-Ansicht)
- **Gesprächsverlauf in der Speech-Ansicht sichtbar.** Bisher zeigte die
  Speech-Ansicht nur einen Startknopf, aber nicht den bisherigen Verlauf des
  ausgewählten Gesprächs. Der Verlauf (dieselbe Quelle wie der Text-Chat) wird
  jetzt oben angezeigt und blendet nach unten aus; darüber liegt das Button-Dock
  mit „Gespräch beginnen" (neu) bzw. „Gespräch weiterführen" (ausgewähltes
  Gespräch) / „Gespräch öffnen" (aktive Sitzung) sowie Status, Aufklappen und
  Beenden.
- **Gesprächstitel wird angezeigt und folgt dem Modell.** Der Titel des
  ausgewählten Gesprächs steht jetzt über dem Verlauf; die Gesprächsliste in der
  Speech-Ansicht aktualisiert sich bei aktiver Sitzung periodisch, sodass ein per
  `rename_conversation` vom Modell gesetzter Name dort erscheint.

---

## [1.244.0] - 2026-08-19

### Behoben
- **`get_delegated_tasks` (Voice) war auf die aktuelle Sitzung beschränkt.** Das
  Werkzeug las nur den In-Memory-Zustand der laufenden Sprachsitzung; eine neue
  Sitzung startete leer und konnte weder eine zuvor delegierte Aufgabe noch
  deren Ergebnis anzeigen — die Rückmeldung einer abgeschlossenen Delegation
  ging so verloren. Es fragt jetzt zusätzlich die zuletzt für diesen Agenten
  gelaufenen, vom Nutzer angestoßenen Aufgaben aus der Datenbank ab (sitzungs-
  übergreifend, inkl. Ergebnis). Automatische Läufe (Zeitplan/Proaktiv, Titel in
  eckigen Klammern) sind ausgeblendet; die Abfrage ist auf den Agenten der
  Sitzung beschränkt (Mandantentrennung).

---

## [1.243.1] - 2026-08-19

### Behoben
- **DB-Verbindungs-Timeouts unter schreibintensiver Hintergrundlast.** Der
  Connection-Pool war unbegrenzt (`max_overflow=-1`), wodurch Lastspitzen sehr
  viele neue Verbindungen gleichzeitig aufbauen konnten; auf ressourcen-
  begrenzten Hosts lief der Aufbau neuer Verbindungen dann in ein Timeout, was
  Hintergrund-Jobs und einzelne Requests scheitern ließ. Der Pool ist jetzt
  begrenzt (`pool_size=10`, `max_overflow=20`), sodass Spitzen kurz in der
  Pool-Queue warten statt die Datenbank mit Verbindungsaufbauten zu überlasten;
  Verbindungen werden seltener recycelt (5 → 15 min), was die Anzahl neu
  aufzubauender Verbindungen weiter senkt.
- **Verschluckte Fehlermeldung im nächtlichen Reflection-Job.** Der Fehler wurde
  mit `%s` geloggt; bei einem `TimeoutError` (leerer `str()`) stand nur
  „Reflection error: " ohne Ursache im Log. Jetzt mit Typ (`%r`) und Traceback,
  damit solche Fälle diagnostizierbar sind.

---

## [1.243.0] - 2026-08-18

### Geändert (UI/UX — Redesign, Iteration 1)
- **Moderne Buttons statt grauer System-Knöpfe.** Neuer gefüllter Pill-Button
  (Primäraktion in Akzentblau, Sekundär dezent) — der Hauptgrund, warum die App
  „alt" wirkte. Angewendet im Hauptfenster und Status-Fenster.
- **Hauptfenster**: Höhe an den Inhalt angepasst (560 → 480) — die große leere
  Fläche unten ist weg.
- **Status-Fenster**: Überlauf behoben — mit allen Fähigkeiten + Ordnerpfad lief
  die unterste Zeile aus der Karte; Karte höher, lange Pfade in der Mitte
  gekürzt.
- **Voice-Bar**: Text lief unter die Buttons — Bar verbreitert (760 → 880) und
  Textbreite begrenzt, keine Überlappung mehr.

### Verbessert
- **Agenten finden lokale Dateien jetzt über die Shell.** Die Beschreibung von
  `computer_shell` stellt jetzt klar, dass der Befehl auf dem Rechner des
  Nutzers in den freigegebenen Ordnern läuft — bei Fragen nach lokalen
  Dateien/Ordnern nutzt der Agent `ls`/`find`/`cat` statt Screenshot/Finder und
  weist bei fehlender Freigabe auf die Fähigkeit „Shell-Befehle" und den
  Ordner-Zugriff hin.

---

## [1.242.0] - 2026-08-18

### Hinzugefügt
- **Per Sprache eine Demonstration aufzeichnen und daraus ein Skill bauen.**
  Neues Voice-Werkzeug `learn_skill` (action=start/finish): Bei `start` zeichnet
  die Bridge Klicks, Tastatureingaben und Screenshots des Nutzers auf, bei
  `finish` wird die Aufzeichnung beendet und aus den Schritten und Bildern
  automatisch ein Skill erzeugt (als Entwurf, aktiv erst nach Freigabe). Nutzt
  denselben Aufzeichnungs- und Skill-Bau-Weg wie die HTTP-Oberfläche
  (`replay_skill_service`) — kein zweiter, abweichender Pfad. Setzt die
  Bridge-Berechtigung „Eingaben mitschneiden" voraus. Schließt die Lücke, dass
  der Replay-Modus bisher nur über die Web-UI, nicht per Voice erreichbar war
  (Harness-Parität).

---

## [1.241.0] - 2026-08-18

### Hinzugefügt
- **Browser im Hintergrund betreiben (wie ego lite).** Die Browser-Steuerung
  startet jetzt optional headless — ohne sichtbares Fenster, der Agent bedient
  die Seite über den DOM (Navigieren, Klicken per Element/Text, Formulare),
  ohne den Vordergrund des Nutzers zu kapern. Neuer Schalter „Browser
  unsichtbar im Hintergrund betreiben" im Berechtigungs-Dialog (macOS +
  Windows), opt-in. **Standard bleibt sichtbar** — eine eingeloggte Sitzung
  unsichtbar laufen zu lassen soll eine bewusste Entscheidung sein. Greift beim
  nächsten Browser-Start; das eigene, private Profil (kein Cookie-Zugriff aufs
  Nutzerprofil) bleibt unverändert.

---

## [1.240.3] - 2026-08-18

### Behoben
- **Klicks landeten auf Retina-Displays systematisch daneben.** Der Screenshot
  wird auf 1280 px Breite herunterskaliert (damit das Modell keine Koordinaten
  >1280 halluziniert), aber der Klick ging mit denselben Koordinaten an
  pyautogui, das in logischen Punkten (z. B. 1440) arbeitet — also lag jeder
  Klick um den Faktor logisch/1280 daneben. Der Dispatcher merkt sich jetzt den
  Maßstab des letzten Screenshots und rechnet jede Klick-, Scroll-, Bewegungs-
  und Ziehkoordinate aus dem Bildraum in den Klickraum zurück; `find_element`
  liefert seine Treffer im selben Bildraum, damit beide Klickquellen konsistent
  sind. Ohne vorherigen Screenshot bleibt es beim alten 1:1-Verhalten.

---

## [1.240.2] - 2026-08-18

### Behoben (Build/Signierung)
- **Notarisierung weiterhin „invalid" trotz korrekter Signatur — Ursache: der
  DMG-Staging-Schritt.** `cp -r` zerstörte die Code-Signatur beim Kopieren der
  fertig signierten App: BSD-`cp` mangelt die Symlink-Struktur von
  `Python.framework`, wodurch die gesiegelte Signatur ungültig wird —
  `codesign --verify` bestand VOR dem Kopieren noch. Ersetzt durch `ditto`
  (Apples Werkzeug für signierte Bundles) plus eine erneute Verify-Prüfung
  NACH dem Kopieren, damit ein Signaturbruch sofort im Build auffällt statt
  erst Minuten später bei Apple.

---

## [1.240.1] - 2026-08-18

### Behoben (Build/Signierung)
- **macOS-Notarisierung schlug fehl: „signature of the binary is invalid".**
  Der Build signierte die App mit `codesign --deep` — das signiert von außen
  nach innen und lässt die eingebetteten Python-Binaries (Framework, dylibs,
  `.so`) ungültig bzw. ohne Hardened Runtime zurück, was Apples Notardienst
  ablehnt. Jetzt wird **jede eingebettete Mach-O-Datei einzeln von innen nach
  außen** signiert, jeweils mit `--options runtime` und sicherem Zeitstempel
  (`--timestamp`); das Bundle zuletzt mit den Entitlements. Damit wird die App
  signiert **und** notarisiert ausgeliefert — kein Rechtsklick→Öffnen mehr beim
  ersten Start, und die TCC-Freigaben bleiben über Updates stabil.

---

## [1.240.0] - 2026-08-18

### Geändert
- **Die Bridge ist jetzt eine App mit Hauptfenster, kein Tray-Anhängsel.**
  Beim Start öffnet sich ein Hauptfenster mit Live-Verbindungsstatus, Server-,
  Session- und Freigabe-Übersicht, Verbinden/Trennen und Schnellzugriff auf
  Voice, Berechtigungen, Einstellungen und Web-UI. macOS: natives Fenster im
  Stil der bestehenden Karten-Dialoge, alle 3 s live aktualisiert; Windows:
  Hauptfenster mit Tabs (Übersicht/Voice), Doppelklick aufs Tray-Symbol
  öffnet es. Das Tray bleibt für den Hintergrundbetrieb — Fenster schließen
  beendet die Verbindung nicht. Verbinden/Anmelden laufen über dieselben
  Callbacks wie die Tray-Menüpunkte (eine Verbindungslogik, keine zweite).

---

## [1.239.0] - 2026-08-18

### Sicherheit
- **TLS-Verifikation der Bridge ist jetzt AN — mit Zertifikats-Pinning wie bei
  SSH.** Bisher lief jede Verbindung (Login samt Passwort, Token, alle Befehle)
  mit `CERT_NONE` und war gegen Mitleser ungeschützt. Jetzt: öffentliche
  Zertifikate über die System-CA; selbstsignierte werden beim Erstkontakt
  gepinnt und danach im Handshake verlangt, BEVOR ein Byte Nutzdaten fließt
  (`cadata` + `VERIFY_X509_PARTIAL_CHAIN`, deckt auch Firmen-CA-Blätter ab).
  Ein geändertes Zertifikat ist ein harter Fehler mit beiden Fingerabdrücken;
  neu vertraut wird nur bei ausdrücklicher Neu-Anmeldung. Notausgang für
  Sonderfälle: `"tls": {"mode": "insecure"}` von Hand in der Config — nie
  Voreinstellung. Gilt für Tray-HTTP, Bridge-WebSocket UND Voice-WebSocket,
  auf macOS und Windows.
- **`~/.ai_employee_bridge.json` (enthält das JWT) ist jetzt 0600.** Vorher
  konnte jeder andere lokale Account das Token lesen; der nächste
  Speichervorgang repariert auch Bestandsdateien.

### Hinzugefügt
- **`shell_run` existiert jetzt wirklich — fail-closed über die Ordnerliste.**
  Die Fähigkeit `shell` stand seit jeher in Server-Gruppen und
  Berechtigungs-Dialog („auf diese Ordner beschränkt"), implementiert war
  NICHTS. Jetzt: ohne freigegebenen Ordner gesperrt (auch bei aktivierter
  Fähigkeit), Arbeitsverzeichnis muss in einem freigegebenen Ordner liegen
  (realpath gegen `..`/Symlink-Ausbruch), Timeout max. 300 s. Der
  Dialog-Text sagt jetzt ehrlich „Startordner“ statt „beschränkt auf“. Neues
  MCP-Werkzeug `computer_shell` für Claude-Code-Agenten; Codex konnte die
  Aktion schon über das generische Werkzeug.
- **Interaction Bar spricht DIREKT mit dem Voice Layer des Agenten.** Vorher:
  Datei aufnehmen → am Stück senden → Antworttext lokal per Edge-TTS vorlesen.
  Jetzt: Mikrofon streamt live als 16-kHz-PCM in die Realtime-Session
  (Nova Sonic), Antwort-Audio (24-kHz-PCM) spielt beim Eintreffen über einen
  Streaming-Player. Edge-TTS samt Abhängigkeit entfernt.
- **Agenten-Auswahl statt ID-Feld in der Interaction Bar** — Dropdown mit den
  eigenen Agenten (Namen), zuletzt genutzter vorausgewählt.
- **Interaction Bar jetzt auch unter Windows** (vorher stiller Abbruch):
  gleiche Fähigkeit, customtkinter-Fenster, Audio-Wiedergabe über
  winmm/MCI ohne Zusatzfenster.

### Behoben
- **Endgültige Server-Ablehnung (1008) beendet die Verbindungsschleife.**
  Vorher wählte die Bridge eine tote Session alle 5 s ewig neu an und das
  Tray-Symbol blieb auf „verbunden“ — eine abgelaufene Session war von einer
  gesunden Verbindung nicht unterscheidbar. Unter Windows kommt zusätzlich
  eine Benachrichtigung mit dem Grund.
- **Interaction Bar erschien nicht beim Klick** (macOS): die Hintergrund-App
  wurde nie aktiviert; das Fenster tauchte erst auf, wenn irgendein anderer
  Dialog die App aktivierte.
- **Mehrzeiliger Text war untippbar:** ein roher Zeilenumbruch im
  AppleScript-Literal ist ein Syntaxfehler; der stille Rückfall tippte
  layout-falsch weiter. Umbrüche werden jetzt als Return-Taste gesendet.
- **Zwei Tray-Dialoge gleichzeitig crashten die App** (Windows): zwei
  tkinter-Mainloops in parallelen Threads. Solange ein Dialog offen ist,
  öffnet kein zweiter.
- `get_clipboard` meldete bei fehlgeschlagenem Lesen einen leeren Text statt
  eines Fehlers; `ax_tree`-Fehler steckten als Baum verkleidet im Ergebnis;
  der Agent-Browser wird beim Beenden der App mitgeschlossen (verwaiste
  Profil-Sperre); Tipp-Mitschnitt-Puffer ist jetzt threadsicher.

---

## [1.238.5] - 2026-08-18

### Behoben (Sicherheit)
- **Befehls-Einschleusung in der Zwischenablage unter Windows.** Der Text kam
  aus dem Netz und wurde direkt in einen PowerShell-Befehl gesetzt
  (`Set-Clipboard '{text}'`). Ein Apostroph darin beendete das Literal — der
  Rest lief als eigener Befehl mit den Rechten des angemeldeten Nutzers. Der
  Text geht jetzt über die Standardeingabe, wie der macOS-Zweig es seit jeher
  richtig macht.

### Behoben
- **Klicken, Tasten, Scrollen und Ziehen meldeten Erfolg, ohne etwas zu tun.**
  Ohne Bedienungshilfen-Freigabe verwirft macOS synthetische Eingaben lautlos —
  die Bridge meldete trotzdem „erledigt", der Agent baute darauf auf. Jetzt
  prüft ein gemeinsamer Riegel vor jeder Eingabe.
- **Die Bridge fragte nie nach Freigaben.** Sie rief nur die stillen Prüfungen
  (`CGPreflightScreenCaptureAccess`, `AXIsProcessTrusted`) auf, nie die
  fragenden Varianten. Wer die Freigabe nie erteilt oder zurückgesetzt hatte,
  bekam **nie wieder** einen Dialog und musste die App von Hand eintragen.
- **Jede Neuanmeldung löschte die Freigabelisten** für Anwendungen und
  Adressen serverseitig — die drei Anmeldedialoge reichten die Konfiguration
  nicht durch, wodurch „alles freigeben" gesendet wurde.
- **Auf macOS gingen beim Speichern der Einstellungen Freigaben verloren:**
  die Konfiguration wurde ersetzt statt zusammengeführt (Windows machte es
  richtig).
- **`browser_close` meldete Erfolg, auch wenn der Browser noch lief** — der
  nächste Start scheiterte dann an der Sperrdatei des Profils.
- **Drei Fehlermeldungen in den Windows-Dialogen verschwanden spurlos**
  (`NameError` durch späte Auswertung in `lambda`): Bei falscher Server-Adresse
  blieb „Verbinde…" für immer stehen, ohne Hinweis.
- Ein hängender Browser-Start meldet jetzt „startet noch" statt nach einer
  Minute „antwortet nicht".

---

## [1.238.4] - 2026-08-18

### Behoben
- **Die Sprachfront startete gar nicht mehr** — jede Sitzung brach sofort mit
  `ValidationException: Input is invalid` ab. Ursache war kein Limit und keine
  kaputte Aufnahme: **Bedrock lehnt doppelte Werkzeugnamen ab.** Ein
  angebundener Dienst brachte ein `list_todos` mit, das die Sprachfront als
  eingebautes Werkzeug bereits selbst vergibt — beide gingen in denselben
  Sitzungsstart. Die Deduplizierung kannte bis dahin nur die MCP-Werkzeuge
  untereinander, nie die eingebauten Namen. Folge: nicht ein Werkzeug fiel aus,
  sondern die komplette Sitzung kam nicht zustande. Der Dienst-Name wird jetzt
  wie bei einer Kollision zwischen zwei Diensten vorangestellt, das Werkzeug
  bleibt also erreichbar.
- **Die Bridge meldete ihre neuen Fähigkeiten nicht.** Fenster- und
  Browser-Steuerung liefen, wurden beim Verbinden aber nie angekündigt — die
  Ankündigung war eine zweite, handgetippte Liste. Sie kommt jetzt aus einer
  einzigen Quelle, ein Test hält sie mit dem Dispatcher zusammen.
- **Die Bridge merkt sich die E-Mail-Adresse.** Sie wurde nie gespeichert und
  musste bei jeder Anmeldung neu getippt werden. Das Passwort wird weiterhin
  nirgends abgelegt.

---

## [1.238.3] - 2026-08-18

### Behoben
- **Der Bridge-Download führte auf eine 404-Seite.** Der Link zeigte auf
  `https://github.com//releases/download/…` — doppelter Schrägstrich, kein
  Repository. Ursache war das Zusammenspiel zweier für sich harmloser Zeilen:
  `docker-compose.yml` reicht `GITHUB_REPO` als `${GITHUB_REPO:-}` weiter,
  setzt die Variable also auf **leer**, wenn der Host sie nicht kennt — und
  `os.getenv(name, default)` greift nur, wenn eine Variable **gar nicht**
  existiert. Eine leere Variable schlägt den Standard.
  Der Download hatte seit Mai funktioniert; die compose-Zeile kam erst am
  13.08. dazu, und zwar für die **Feedback-Issue-Spiegelung** — ein ganz
  anderes Feature, das zufällig denselben Variablennamen braucht.
  Beide Seiten sind jetzt abgesichert: der Code fällt auch bei leerem Wert auf
  das echte Repository zurück, und compose reicht einen sinnvollen Standard
  weiter statt einer leeren Zeichenkette.

---

## [1.238.2] - 2026-08-18

### Behoben
- **Der Bridge-Build lieferte mehrere Fähigkeiten gar nicht aus.** Der
  Workflow installierte eine von Hand gepflegte Paketliste, die von
  `requirements.txt` abgewichen war. Im fertigen Programm fehlten dadurch:
  - **`uiautomation` unter Windows** — die Bridge konnte dort **keine Elemente
    finden**, nur blind auf Koordinaten klicken. Ausgerechnet in der
    PyInstaller-Spec stand das Modul längst; installiert wurde es nie.
  - `pynput` — Replay-Modus (Eingaben mitschneiden) tot.
  - `sounddevice`/`numpy` — Mikrofon tot.
  - `pyobjc-framework-Quartz` unter macOS — Bildschirmfoto im eigenen Prozess
    tot, Rückfall auf einen Fremdprozess, der bei **jedem** Foto neu nach der
    Freigabe fragt (genau das, was der Weg im eigenen Prozess vermeiden soll).

  Auffallen konnte das nicht: Die Bridge fängt fehlende Importe ab und gibt
  eine freundliche Meldung zurück — die Fähigkeit war nicht kaputt, sie war
  still nicht da. Beide Builds installieren jetzt aus `requirements.txt`, ein
  Test lehnt eine zweite handgepflegte Liste ab.
- **Playwright wird jetzt wirklich mitgeliefert.** Der Node-Treiber gehört zu
  den Daten, nicht zu den Modulen — ein Eintrag unter `hiddenimports` hätte
  ihn nicht eingepackt und die neue Browser-Steuerung wäre im gebauten
  Programm tot geblieben. Beide Specs nutzen dafür `collect_all`.
- `customtkinter` und `pyobjc-framework-AVFoundation` standen nur in der
  CI-Liste und fehlten in `requirements.txt` — die Drift ging in beide
  Richtungen.

---

## [1.238.1] - 2026-08-18

### Behoben
- **Login und Nutzerverwaltung waren nach dem letzten Update teils nicht mehr
  erreichbar (`UndefinedColumnError` auf `users.allow_personal_credentials`).**
  Die Datenbank-Migration zur neuen Spalte aus v1.238.0 hatte gefehlt — das
  Modell kannte die Spalte, die Datenbank nicht. Jede Anfrage, die einen
  Nutzer laed, schlug deshalb fehl. Rein nachholende, additive Migration
  (IF NOT EXISTS), kein Verhaltensunterschied fuer bereits funktionierende
  Installationen.

## [1.238.0] - 2026-08-18

### Sicherheit
- **Eigene KI-Abos sind jetzt standardmässig gesperrt — und einzeln freigebbar.**
  Korrektur an v1.227.0: dort war der Schalter global mit Vorgabe **an** gebaut.
  Gefordert war das Gegenteil.
- Wörtlich aus dem Kundentermin: „Für uns als Unternehmen möchte ich **nicht**,
  dass Mitarbeiter ihre privaten Accounts hier hinterlegen und dann mit
  Firmendaten arbeiten. Das muss man quasi global als Admin einstellen können."
  Und: „dass man dann **für User manuell freischalten** kann … aber dass man das
  **generell unterbinden** kann."
- Zwei Ebenen: globaler Schalter (Vorgabe **aus**) plus Einzelfreigabe je Nutzer,
  die ein Administrator über die Nutzerverwaltung setzt. Steht der globale
  Schalter an, gilt es für alle — dann muss niemand einzeln angehakt werden.
- Meine ursprüngliche Begründung („eine bestehende Anlage darf nach einem Update
  nicht ohne Zugang dastehen") trug hier nicht: private Abos waren vorher gar
  nicht möglich, es konnte nichts wegbrechen. Für eine Sicherheitszusage ist
  „standardmässig offen" die falsche Richtung.
- Der Schalter greift weiterhin an der Stelle, wo Zugänge aufgelöst werden, nicht
  nur in der Oberfläche — ein bereits hinterlegter privater Zugang hört damit
  wirklich auf zu wirken.

---

## [1.237.1] - 2026-08-18

### Behoben
- **Das Browser-Profil der Bridge wurde weltlesbar angelegt** (0755 nach umask).
  Dort liegen nach der Einmal-Anmeldung die Sitzungs-Cookies und Anmeldedaten —
  jeder andere lokale Account auf demselben Rechner hätte sie mitlesen können.
  Das ist genau der Diebstahl, gegen den die Chrome/Edge-136-Härtung gebaut
  wurde, nur eine Ebene tiefer, und hätte die Begründung des Entwurfs („kein
  Cookie-Import aus dem privaten Profil") ausgehebelt. Das Verzeichnis wird
  jetzt mit `0700` angelegt **und** nachträglich verengt — `mode=` allein
  genügt nicht, weil die umask es beschneidet und ein bereits vorhandenes
  Verzeichnis seine alten Rechte behalten hätte. Gefunden von der
  Sicherheitsprüfung des vorherigen Commits.

---

## [1.237.0] - 2026-08-18

### Behoben
- **Der Agent durfte auf dem Rechner des Nutzers gar nicht klicken.** Sieben
  Befehle, die der Claude-Code-Weg sendet — `click`, `move`, `scroll`,
  `find_element`, `wait_for_element`, `get_clipboard`, `set_clipboard` —
  standen nicht in der serverseitigen Freigabeliste. Die Prüfung ist
  fail-closed, also wurden sie mit **403 abgewiesen, bevor sie den Rechner
  erreichten**. Über Codex liefen dieselben Fähigkeiten (dort heißen sie
  `mouse_click` …), deshalb fiel es nie auf. Das ist die tatsächliche Ursache
  hinter „Navigieren und Formulare ausfüllen ist nicht verlässlich" — nicht das
  Modell, das „zu anderen Mitteln greift", sondern die API. Ein Test liest die
  Namen jetzt aus beiden Quellen und hält sie zusammen.
- **`open_app` war unter Windows kaputt** — der Code rief unbedingt `open -a`
  auf, ein reiner macOS-Befehl. Windows nutzt jetzt denselben Weg wie bei
  `open_url` (Shell-API ohne Kommandozeilen-Interpretation).
- **Zwei Berechtigungsgruppen waren unerreichbar:** `input_capture` und
  `voice_capture` existierten serverseitig, standen aber nicht in der Liste der
  Tray-App — niemand konnte sie einschalten. Ein Test vergleicht beide Seiten.

### Hinzugefügt
- **Browser-Steuerung in der Bridge** (Gruppe `browser`, standardmäßig **aus**):
  Seiten strukturiert lesen, Formulare ausfüllen, klicken, warten, Tabs
  wechseln, Bildschirmfoto — im **eigenen Browser-Profil** des Agenten.
  Genutzt wird der installierte Edge bzw. Chrome, kein mitgelieferter Browser.
  Hintergrund: Seit Chrome/Edge 136 lässt sich das Standardprofil nicht mehr
  fernsteuern (Härtung gegen Cookie-Diebstahl), ein eigenes Profil ist der
  vorgesehene Weg. Der Mensch meldet sich einmal an, danach bleibt die
  Anmeldung erhalten. Cookies aus dem privaten Profil zu kopieren wäre genau
  das, wogegen die Härtung gebaut wurde — das tun wir bewusst nicht.
- **Fenster-Steuerung:** `list_windows` und `focus_window`. Tippen und Klicken
  gehen immer an das Fenster im Vordergrund; ohne diesen Schritt landete
  Eingabe in der zuletzt benutzten Anwendung statt in der gemeinten.
- **Freigabelisten pro Sitzung:** **welche** Anwendungen und **welche**
  Adressen der Agent anfassen darf — durchgesetzt **serverseitig**, nicht nur
  in der Oberfläche. Leer heißt „nicht einschränken". Beim Adressvergleich
  zählt der Host, nicht die Zeichenkette: `example.com` erlaubt
  `a.example.com`, aber nicht `example.com.fremde-domain.tld`.
  (Das vorhandene Feld `allowed_paths` bleibt davon unberührt — es wird bis
  heute nur lokal gespeichert und nirgends durchgesetzt.)
- Alle neuen Fähigkeiten in **allen Laufzeiten**: MCP (Claude Code), Codex und
  Custom-LLM.

---

## [1.236.0] - 2026-08-18

### Neu
- **Import und Export sind jetzt bedienbar.** In der Second-Brain-Liste zwei neue
  Knoepfe je Vault: herunterladen (ZIP) und einspielen. Der Einspiel-Dialog fragt,
  ob zusammengefuehrt oder ersetzt werden soll, und warnt beim Ersetzen
  ausdruecklich, dass nicht enthaltene Dateien verschwinden.
- Nach dem Einspielen nennt die Meldung Zahlen statt nur "fertig": wie viele
  Dateien geschrieben, entfernt und uebersprungen wurden — bei einem krummen
  Archiv waere sonst nicht zu sehen, dass etwas fehlt.
- Gegengeprueft mit einem echten Obsidian-artigen Vault (verschachtelte Ordner,
  `.obsidian`, Wikilinks, Frontmatter, Umlaute): Rundlauf Export in Import ohne
  Verlust.

---

## [1.235.0] - 2026-08-18

### Neu
- **Second Brains: Import und Export als ZIP.** Ein ganzer Vault lässt sich
  herunterladen und wieder einspielen — Ordnerstruktur inklusive, direkt in
  Obsidian zu öffnen.
- Zwei Betriebsarten beim Import: **zusammenführen** (Vorgabe, ergänzt und
  überschreibt, löscht nichts) oder **ersetzen** (der Vault wird zum Abbild des
  Archivs).
- **Die Einbettungen werden danach nachgezogen.** Ohne diesen Schritt lägen die
  Notizen zwar auf der Platte, wären aber semantisch unauffindbar — für die
  Agenten praktisch unsichtbar. Der Indexlauf ist inkrementell und entfernt auch
  die Einträge gelöschter Dateien, passt also zu beiden Betriebsarten. Schlägt er
  fehl, bleibt der Import trotzdem erhalten und es wird darauf hingewiesen.

### Sicherheit
- Ein hochgeladenes Archiv ist Fremdeingabe: jeder Eintrag geht durch denselben
  Pfad-Riegel wie der Dateibrowser (kein zweiter, eigener Weg). Aufsteigende
  Pfade (`../`) werden abgewiesen, absolute Pfade in den Vault hinein
  normalisiert, Symlinks und Sonderdateien übersprungen, gesperrte Dateiendungen
  bleiben gesperrt.
- Zip-Bomben werden **vor** dem Entpacken abgewiesen (geprüft wird die entpackte
  Größe, nicht die des Archivs). Grenzen: 200 MB Archiv, 1 GB entpackt, 50.000
  Einträge.
- Eine einzelne krumme Datei stoppt den Import nicht — sie wird übersprungen und
  im Bericht genannt.

### Hinweis
- **Obsidian Sync selbst lässt sich nicht anbinden**: geschlossener,
  Ende-zu-Ende-verschlüsselter Bezahldienst ohne öffentliche Schnittstelle. Der
  Weg führt über die Ordnerstruktur — ein Vault ist nur Markdown in Ordnern.
  Echtes Mitlaufen über Git ist der nächste Schritt.

---

## [1.234.1] - 2026-08-18

### Behoben
- **Die Seite „Master-Regeln" blieb leer.** Der Reiter war da, der Inhalt nicht:
  der Inhaltsblock hängt an einer eigenen Liste eingebetteter Reiter, und dort
  fehlte der neue Eintrag. Weder Typprüfung noch Build konnten das sehen — der
  Test prüft es jetzt.

---

## [1.234.0] - 2026-08-18

### Neu
- **Master-Regeln: Verhaltensvorgaben für alle Agenten aller Nutzer.** Aus einer
  Kundenanfrage: eine globale Vorgabe, was Agenten dürfen und was nicht — „ich
  will aber nicht bei jedem agenten das einzeln vorgeben".
- Neuer Reiter unter **Sicherheit**. Der Text landet in der Anleitung jedes
  Agenten und im Sprachmodus, steht über jedem Auftrag und ist für normale
  Nutzer nicht abwählbar.
- Die Regeln stehen bewusst **ganz oben** in der Anleitung: die Agenten-Laufzeit
  kürzt eine zu lange Anleitung von hinten — angehängt wären ausgerechnet sie
  als Erstes weg.
- Alle vier Laufzeiten bekommen sie: Claude Code über `CLAUDE.md`, Codex über
  `AGENT.md`, Custom-LLM liest dieselbe Datei, und die Sprachfront hat einen
  eigenen Anschluss. Ein Test prüft, dass keine Stelle vergessen wird — genau das
  ist am selben Tag zweimal passiert.
- **Globale Befehlssperren sind endlich einstellbar.** Das Datenmodell konnte sie
  seit jeher (`scope: global`), es gab nur nirgends eine Oberfläche dafür. Jetzt
  auf derselben Seite: zwei Ebenen desselben Gedankens.

### Hinweis
- Master-Regeln sind eine **Anweisung, keine Sperre** — das steht auch so auf der
  Seite. Sprachmodelle halten sich meistens, aber nicht immer daran. Was
  technisch unmöglich sein muss, gehört in die Befehlssperren.
- Wirksam wird der Regeltext bei einem Agenten erst, wenn er aktualisiert wurde.

---

## [1.233.0] - 2026-08-18

### Behoben
- **Ein Chat verlor seinen Verlauf, sobald der Agent neu startete.** Gemeldet mit
  Bildschirmfoto: der Agent sprach in einer Unterhaltung über ein ganz anderes
  Projekt und stieß vier Reviews bei drei Kollegen an. Die Frage war, ob er zwei
  Chats vermischt.
- **Tut er nicht.** Auf der Anlage nachgesehen: alle Nachrichten des Wortwechsels
  lagen in einer Sitzung, die Anzeige stimmte, kein gemeinsamer Sitzungsschlüssel.
- Die echte Lücke: in der Custom-LLM-Laufzeit lebt der Verlauf **ausschließlich
  im Arbeitsspeicher** und wurde nie aus der Datenbank zurückgeholt — anders als
  bei den CLI-Laufzeiten, die ihre Sitzung wiederfinden. Nach Neustart, Update
  oder Container-Tausch stand der Agent in einem Chat mit Dutzenden gespeicherten
  Nachrichten vor einem leeren Blatt und reimte sich aus semantisch gesuchten
  Erinnerungen zusammen, worum es geht. Er schrieb selbst „am wahrscheinlichsten"
  — und handelte trotzdem.
- Der Verlauf dieser Unterhaltung wird jetzt beim ersten Zug zurückgeholt
  (begrenzt, nur echte Wortmeldungen, ohne Oberflächen-Kacheln). Schlägt das
  fehl, redet der Agent wie bisher ohne Vorgeschichte weiter.
- Die Erinnerungen bleiben unverändert — sie tragen Wissen über Unterhaltungen
  hinweg. Sie sind nur nicht mehr die einzige Quelle.

### Sicherheit
- `/chat/history` hing an einem reinen **Nutzer**-Login, der Agent kam an seinen
  eigenen Verlauf nicht heran (derselbe Fehlertyp wie beim Löschen eigener
  Erinnerungen am selben Tag). Der Agent darf jetzt seinen **eigenen** Verlauf
  lesen; fremde Agenten werden weiterhin mit 403 abgewiesen.

---

## [1.232.0] - 2026-08-18

### Neu
- **Die Sprachfront benutzt die MCP-Dienste des Agenten jetzt selbst.** Gemeldet:
  ein Dienst mit 32 Werkzeugen war unter Einstellungen → Integrationen angehakt,
  aber die Stimme zählte auf Nachfrage nur ihre eingebauten auf und reichte jeden
  Auftrag an den Agenten weiter — bis der Nutzer ihr selbst sagte, welches
  Werkzeug es gibt.
- Ursache: ihre Werkzeugliste stand vollständig von Hand im Quelltext (47
  Konstanten) und holte nirgends ein `tools/list`. Sie **konnte** von den
  angebundenen Diensten nichts wissen.
- Jetzt werden die Werkzeuge der angehakten Dienste zu echten Werkzeugen der
  Sprachfront und direkt dort aufgerufen. Die Auswahl kommt aus derselben Stelle
  wie die des Agenten-Containers, Gruppenrechte inklusive — zwei Auswahlen
  nebeneinander wären die Lücke, durch die ein gesperrter Dienst doch erreichbar
  wird.
- **Nichts wird still weggelassen.** Die Engine verträgt nur eine begrenzte Zahl
  Werkzeuge; was ins Budget passt, wird direkt deklariert, alles Weitere bleibt
  über `mcp_search_tools` und `mcp_call_tool` erreichbar.
- Antwortet ein Dienst nicht, sagt die Stimme das — statt still auf den Agenten
  auszuweichen und so zu tun, als gäbe es das Werkzeug nicht.
- Vergeben zwei Dienste denselben Werkzeugnamen, bekommt der zweite den
  Dienstnamen davor; beide bleiben erreichbar und der Aufruf landet beim
  richtigen.

---

## [1.231.0] - 2026-08-18

### Neu
- **Textdateien im Arbeitsbereich lassen sich direkt bearbeiten.** Aus dem
  Kundentermin: `.env`-Dateien ließen sich ansehen, aber nicht ändern — wer eine
  Zeile korrigieren wollte, musste herunterladen, lokal bearbeiten und wieder
  hochladen. Die Ansicht war rein lesend, es gab keinen Schreibweg.
- „Bearbeiten" in der Dateivorschau, Textfeld an Stelle der Anzeige, Speichern
  oder Abbrechen. Bei HTML wird bewusst die Quelle bearbeitet, nicht die
  gerenderte Fassung. Ein Wechsel der Datei verwirft den Entwurf; schlägt das
  Speichern fehl, bleibt der Text stehen und der Fehler wird angezeigt.
- Gilt in beiden Dateibäumen (Arbeitsbereich eines Agenten und die
  agentenübergreifende Ansicht).

### Sicherheit
- Der Schreibweg prüft an derselben Stelle wie Lesen und Hochladen: Pfade müssen
  im Arbeitsbereich bleiben (`..` wird **nach** dem Normalisieren geprüft),
  Symlinks und Verzeichnisse werden abgelehnt, gesperrte Dateiendungen bleiben
  gesperrt — sonst wäre die Sperre beim Hochladen über den Umweg „Bearbeiten"
  zu umgehen. Obergrenze 1 MB. Der Endpunkt prüft Anmeldung und Eigentümer.

---

## [1.230.0] - 2026-08-18

### Geändert
- **Zeitpläne sind nach Agent gruppiert und starten eingeklappt.** Vorher eine
  flache Liste über alle Agenten hinweg — bei sieben Agenten mit je mehreren
  Plänen war nicht mehr zu sehen, wessen Plan wann läuft.
- Jede Gruppe zeigt schon zugeklappt, wie viele Pläne sie hat, wie viele davon
  aktiv sind und wann der nächste läuft. Innerhalb einer Gruppe steht oben, was
  als Nächstes dran ist. Dazu „Alle aufklappen" / „Alle zuklappen".
- Pläne ohne festen Agenten (die über die Lastverteilung laufen) bekommen eine
  eigene Gruppe, statt aus der Liste zu fallen.

---

## [1.229.0] - 2026-08-18

### Behoben
- **Eine Datei riss die ganze Agentenseite mit.** Gemeldet mit Bildschirmfoto:
  statt der Vorschau nur noch „This page couldn't load", der Agent weg, an dem
  gerade gearbeitet wurde.
- Zwei Ursachen, die zusammenkamen. Erstens holte der PDF-Betrachter seinen
  Arbeiter von einem fremden CDN — ein Browser darf einen Worker aber nicht von
  einem fremden Ursprung starten. Schlägt das fehl, wirft pdf.js beim ersten
  Zugriff (`this.messageHandler` ist dann leer).
- Zweitens gab es im **gesamten** Frontend keine einzige Fehlergrenze. Also
  kippte ein Fehler in der Vorschau den kompletten Seitenbaum statt nur das
  eine Feld.
- Beides behoben: Arbeiter, Zeichensatztabellen und Schriften kommen jetzt vom
  eigenen Ursprung und werden beim Bauen mitgeliefert. Die Vorschau sitzt in
  einer Fehlergrenze — geht sie kaputt, läuft der Rest der Seite weiter und man
  kann die Datei stattdessen herunterladen.
- Der CDN-Bezug war auch inhaltlich falsch: die Anlage läuft selbst gehostet
  (auch abgeschottet ohne Weg nach draußen), und jedes geöffnete PDF hätte einem
  Fremdanbieter verraten, dass es geöffnet wurde.

- **Ein Agent ließ sich nicht mehr starten (500).** Im selben Bericht, aus der
  Browserkonsole. Die gemerkte Container-Kennung war veraltet; beim Neuaufbau
  kam „the container name is already in use" zurück, weil zwischen Abräumen und
  Anlegen ein zweiter Weg denselben Agenten aufgebaut hatte.
- Der fertige Container ist genau der, der gebaut werden sollte — er wird jetzt
  **übernommen** statt mit einem Fehler quittiert. Ihn zu löschen wäre falsch:
  er kann bereits arbeiten. Andere Docker-Fehler (voller Datenträger, fehlendes
  Abbild) kommen unverändert durch und werden nicht als „schon da" verschluckt.
- Beide Wege, die einen Container neu aufbauen, benutzen dieselbe Stelle.

---

## [1.228.0] - 2026-08-18

### Behoben
- **Ein Agent konnte sein eigenes veraltetes Wissen nicht löschen.** Gemeldet mit
  Bildschirmfoto: der Agent merkte, dass sein gespeicherter Team-Zettel einen
  Kollegen nennt, den es nicht mehr gibt, wollte die vier Notizen wegräumen — und
  bekam vier Mal `401 Invalid or expired token`. Im Log der Anlage stand es
  genauso.
- Ursache: der Löschweg hing an einer reinen **Nutzer**-Anmeldung. Speichern und
  Auflisten ließen den Agenten längst durch, nur Löschen nicht. Das Werkzeug
  `memory_delete` stand damit in allen vier Laufzeiten im Katalog und hat nie
  funktioniert.
- Der Besitz-Schild kannte den Agenten-Fall die ganze Zeit, wurde aber nie
  erreicht. Die Mandantentrennung bleibt unverändert: ein Agent kommt nur an
  seine eigenen Notizen, fremde werden weiterhin mit 403 abgewiesen.

### Geändert
- **Agenten sehen ihre Kollegenliste jetzt von selbst nach, statt sie zu
  erinnern.** Rückmeldung: „wieso hat der das erst nach ansprache gesichtet...
  wieso KOMMT DER NICHT ALLEIN AUF DEN GEDANKEN MAL ZU SCHAUEN".
- Die Regel gab es, sie hing aber am Gefragtwerden („wenn dich jemand nach deinem
  Team fragt"). Jetzt hängt sie am Handeln: vor dem Delegieren, vor dem
  Anschreiben, vor dem Nennen eines Kollegen und bevor etwas über das Team ins
  Gedächtnis geschrieben wird.
- Neu ist auch, was zu tun ist, wenn jemand fehlt: es sagen und neu planen,
  statt Arbeit für einen Namen einzustellen, den es nicht mehr gibt — und die
  falsche Notiz löschen, damit man ihr morgen nicht wieder glaubt.
- Nachgesehen auf der Anlage: an die tote Kennung ging **kein einziger** Auftrag.
  Die vorhandene Sicherung beim Zustellen konnte also gar nicht greifen — der
  Agent hat nie falsch delegiert, sondern falsch geglaubt.

### Neu
- **Dateien lassen sich auf einen Ordner im Dateibaum ziehen.** Sie landen direkt
  dort, der Ordner klappt auf und liest sich neu ein. Wer knapp danebentrifft und
  auf einer Datei landet, meint deren Ordner — das gilt jetzt auch so. Der leere
  Bereich unter dem Baum nimmt Dateien für den Wurzelordner an.
- Gilt für beide Dateibäume (Arbeitsbereich eines Agenten und die
  agentenübergreifende Ansicht); beide benutzen dieselbe Mechanik, damit sie
  nicht auseinanderlaufen.
- Ganze Ordner kann die Schnittstelle nicht — das wird jetzt gesagt, statt mit
  einer leeren Datei fehlzuschlagen.

---

## [1.227.0] - 2026-08-18

### Sicherheit
- **Eigene KI-Abos der Mitarbeiter sind jetzt zentral steuerbar.** Aus dem
  Kundentermin vom selben Tag: es fehle die „globale Freigabe, damit Mitarbeiter
  eigene Abo-Accounts einbinden dürfen — Sicherheitsrisiko sonst, muss zentral
  steuerbar sein."
- Der persönliche Weg („Meine KI-Zugänge") entstand am selben Tag **ohne** diesen
  Schalter: jeder eingeloggte Nutzer konnte sein privates Abo einbinden, ein
  Administrator konnte es weder sehen noch unterbinden. Genau das Risiko, das
  vorbeugend benannt worden war.
- Neuer Schalter unter Einstellungen. Vorgabe **an**, wie bei der Teamlizenz —
  eine bestehende Anlage darf nach einem Update nicht plötzlich ohne Zugang
  dastehen.

### Geändert
- Der Schalter wirkt an **zwei** Stellen, sonst wäre er Fassade: im Zugangs-Pfad
  (`agent_credentials.resolve`), damit bereits hinterlegte Zugänge aufhören zu
  wirken — und in der Schnittstelle, damit niemand etwas anlegt, das
  anschließend wirkungslos ist. Eine Anmeldung, die scheinbar klappt und dann
  nichts bewirkt, ist schlimmer als eine klare Absage.
- **Lesen und Löschen bleiben immer erlaubt:** wer seinen Zugang loswerden will,
  darf daran nicht gehindert werden, nur weil die Funktion inzwischen zu ist.
- Die Übersicht meldet den Zustand mit, damit die Oberfläche den Bereich
  ausblendet statt Knöpfe anzubieten, die mit 403 abgewiesen werden.

---

## [1.226.1] - 2026-08-18

### Behoben
- **Ein Schluckauf der Sprach-Engine beendete das Gespräch.** „Model has timed
  out in processing the request. Try your request again." — und das
  Live-Gespräch stand. Der Browser zeigte den Fehler und blieb stehen; neu
  starten musste man von Hand.
- Dabei gibt es das Neuverbinden längst: reißt der Stream ab, verbindet die
  Oberfläche still neu und setzt **dasselbe** Gespräch fort. Nur ein Fehler der
  Engine lief in einen anderen Zweig, obwohl es dasselbe ist — „Try your request
  again" steht sogar in der Meldung.
- Vorübergehende Fehler (Zeitüberschreitung, Drosselung, 5xx, Verbindungsabbruch)
  gehen jetzt denselben Weg: neu verbinden, Gespräch fortsetzen, Sitzungskennung
  behalten. Die Obergrenze für Neuversuche bleibt.

### Sicherheit
- Bewusst eine **Positivliste** statt einer Faustregel: was nicht darauf steht,
  wird dem Nutzer gezeigt. Ein falscher Zugangsschlüssel würde sonst achtmal
  hintereinander scheitern, ohne dass jemand erfährt, warum — und am Ende stünde
  dieselbe Meldung, nur acht Versuche später.

---

## [1.226.0] - 2026-08-18

### Neu
- **Die Sprachfront kann jetzt eskalieren statt zu raten** (`escalate_if_unsure`).
  Sie war die einzige der vier Laufzeiten ohne das Konfidenz-Gate — sie hat also
  geraten, wo der Agent gefragt hätte. Am Telefon wiegt das schwerer als im
  Geschriebenen: ein falscher Name klingt genauso sicher wie ein richtiger, und
  niemand kann zurückblättern.
- Sie ruft dabei **dieselbe** Serverfunktion auf wie der Agent. Die Schwelle
  gehört dem Betreiber und steht pro Agent in der Konfiguration; zwei Schwellen
  wären zwei Regeln, von denen eine irgendwann die falsche ist.
- Die Rückfrage erscheint im Cockpit mit den Antwortmöglichkeiten als Knöpfe;
  die Antwort wird dem Modell in einer Sprechpause zugetragen. Eine Ablehnung
  liest sich nicht als Erlaubnis, und solange keine Antwort da ist, arbeitet der
  Agent an der Sache nicht weiter.

### Geändert
- **Vorgabe: Sprachinteraktion läuft über MCP-Dienste.** Grundlage dafür ist eine
  Einordnung aller 79 Agenten-Werkzeuge (`core/voice_tool_parity.py`): 57 haben
  den Orchestrator als Gegenstelle und gehören der Sprachfront direkt, 23 laufen
  im Agenten-Container und werden delegiert. `bash` und `write_file` in der
  Sprachfront zu spiegeln hieße, eine zweite Ausführung danebenzustellen.
- Ein Test lässt **kein Werkzeug uneingeordnet** und führt die verbleibende
  Lücke als Zahl, die nur fallen darf. Vorher war die Differenz unsichtbar: beide
  Seiten funktionierten für sich, gemessen waren es 42 gegen 79.
- Offen bleiben 38 Einträge, angeführt von `notify_user` und `send_telegram` —
  die Sprachfront kann den Nutzer noch nicht benachrichtigen.

---

## [1.225.0] - 2026-08-18

### Neu
- **Die Sprachfront weiß jetzt, welche API-Zugänge ein Agent hat.** Auf „hast du
  Zugang zu diesem Key?" antwortete sie bisher „keine Umgebungsvariablen
  gefunden" und zählte stattdessen ihre eigenen Einstellungen auf. Sie hatte
  darauf schlicht keinen Blick: die zugewiesenen Schlüssel legt der Orchestrator
  als Umgebungsvariablen in den **Agenten**-Container, die Sprachfront läuft
  woanders.
- Neues Werkzeug `list_agent_secrets`: nennt Name und Variablennamen der
  zugewiesenen, aktiven Zugänge. Ist keiner zugewiesen, sagt es auch, wo man das
  ändert — statt nur „nichts gefunden".

### Sicherheit
- **Die Werte bleiben draußen.** Der gesprochene Verlauf wird als Nachricht
  gespeichert und geht Wort für Wort an einen fremden Dienst; ein Schlüssel, der
  dort einmal landet, müsste gedreht werden. Das Werkzeug entschlüsselt nichts
  und liest die verschlüsselte Spalte nicht einmal an — Tests wachen über beides.
- Für einen echten API-Aufruf delegiert die Sprachfront per `ask_agent` an den
  Agenten, der die Variable ohnehin hat. Die Werkzeugbeschreibung sagt das
  ausdrücklich und verbietet, den Nutzer nach einem Schlüssel zu fragen.
- Abgeschaltete Zugänge werden nicht gemeldet — sie landen auch nicht im
  Container, und sie zu nennen wäre ein falsches Versprechen.

---

## [1.224.0] - 2026-08-18

### Behoben
- **Ein neu gestarteter Sprachchat machte am alten Thema weiter.** Er begrüßte
  mit „Willkommen zurück — wir waren gerade dabei …" und arbeitete das vorige
  Gespräch fort.
- Das war Absicht, nur zu weit gefasst: ist eine Sprachsitzung leer, lädt der
  Server das letzte Gespräch des Agenten nach — „ein Kollege, den man zweimal
  anruft, erinnert sich an das erste Telefonat". Für einen Verbindungsabbruch
  und für den zweiten Anruf ist das genau richtig; für ein **ausdrücklich neu
  gestartetes** Gespräch nicht.
- Es fehlte die Unterscheidung: hat der Nutzer neu angefangen, oder ist die
  Sitzung nur leer? Beides sah gleich aus. Der Browser sagt es jetzt (`fresh`),
  und nur dann wird nichts nachgeladen. Das Nachladen selbst bleibt — es wird
  nicht abgeschafft, sondern eingegrenzt.

---

## [1.223.1] - 2026-08-18

### Behoben
- **Beim Hochladen ließ sich der Zielordner nicht auswählen.** Das Fenster
  zeigte fest „to /workspace"; wer woanders hin wollte, musste hochladen und
  danach von Hand verschieben. Jetzt ein Feld für den Zielordner, vorbelegt mit
  dem bisherigen Pfad, dazu die gängigen Ordner auf einen Klick.
- Die Auswahl zieht dieselbe Grenze wie der Server (`/workspace`) und sagt es
  **vor** dem Hochladen statt hinterher als Fehler. `/shared` wird bewusst nicht
  angeboten — der Server weist es ab, ein Vorschlag dorthin würde also sicher
  fehlschlagen.

### Geändert
- Sprachmodus: die Anweisung „keine Aufzählungen" führte dazu, dass der Agent
  zur Aufzählung ansetzte und mitten im Satz aufhörte — ein Doppelpunkt ins
  Leere. Jetzt: mehrere Punkte gehören in Fließtext, und was angekündigt wird,
  wird auch gesagt.

---

## [1.223.0] - 2026-08-18

### Neu
- **SSO-Gruppen werden jetzt auch beim Microsoft/Entra-ID-Login gelesen und auf
  Rollen abgebildet.** Bisher galt das nur für SAML — der normale Entra-OIDC-Login
  (der übliche Weg) ignorierte Gruppen komplett; jeder neue Nutzer landete als
  "unassigned" und musste von Hand freigeschaltet werden. Jetzt: `GET
  /me/memberOf` (mit dem ohnehin vorhandenen `User.Read`-Scope, keine
  zusätzliche Admin-Freigabe in Entra nötig) liefert die Gruppen, eine neue
  Zuordnungstabelle entscheidet die Rolle — bei jedem Login neu, nicht nur beim
  ersten, sodass ein Abteilungswechsel im IdP automatisch nachzieht.
- **Zuordnungsziel kann jetzt auch eine eigene Rolle (CustomRole) sein**, nicht
  nur admin/manager/member — eine Entra-Gruppe kann direkt auf die volle,
  granulare Rechtekonfiguration (Templates, Modelle, Agent-Limit, ...) zeigen.
- **Neue Verwaltungsseite (Admin → Nutzer & Rollen → SSO-Gruppen)** löst die
  alte freie JSON-Textbox ab: tatsächlich beim Login gesehene Gruppennamen
  werden zum Anklicken angeboten statt blind abgetippt werden zu müssen.
- SAML und Microsoft-OIDC teilen sich jetzt dieselbe Zuordnungslogik
  (`app/core/sso_group_roles.py`) statt zweier unabhängiger Wege.

### Sicherheit
- Der "letzter Administrator wird nicht herabgestuft"-Schutz war unter
  gleichzeitigen Logins nicht race-sicher (TOCTOU) und zählte deaktivierte
  Admin-Konten fälschlich als Schutz mit — beides beim internen Security-Review
  vor dem Merge gefunden und mit einer Zeilensperre (`FOR UPDATE`) plus
  `is_active`-Filter behoben, bevor der Code live ging.
- Beobachtete Gruppennamen (`sso_observed_groups`) sind jetzt pro Anbieter
  gedeckelt und werden nicht mehr komplett bei jedem Login geladen — beides
  ebenfalls vor dem Merge gefunden, nicht danach.

---

## [1.222.4] - 2026-08-18

### Geändert
- **Feedback und Concierge sind in den Sidebar-Kopf gezogen – unten rechts
  schwebt nichts mehr.** Die beiden schwebenden Knöpfe lagen über Eingabe-
  feldern und Bedienelementen der Seiten. Beide Einstiege sitzen jetzt als
  Paar oben neben dem Logo: der Concierge (nur Administratoren) als
  Lucide-Icon im selben Stil wie der Feedback-Knopf daneben. Die Panels
  selbst öffnen unverändert unten rechts – sie sind flüchtig und überdecken
  nichts dauerhaft. Der Test zur FAB-Paar-Geometrie
  (`test_feedback_button_matches_concierge.py`) ist mit seiner Prämisse
  entfallen und durch `test_feedback_and_concierge_sit_in_the_sidebar.py`
  ersetzt.

---

## [1.222.3] - 2026-08-18

### Behoben
- **Nach einer fertigen Aufgabe kam im Sprachmodus keine Meldung.** Aufgabe per
  Sprache erteilt, Fokus-Modus an („ich melde mich, wenn etwas fertig ist"),
  Aufgabe lief durch und stand in der Oberfläche auf ERLEDIGT — gesprochen
  wurde nichts.
- Die Meldung wartet auf eine Sprechpause, weil sie sonst an den laufenden Satz
  angehängt und nie ausgesprochen wird. Sie brach aber nach 25 Sekunden ab und
  spielte sich **trotzdem** ein — also genau in die laufende Ausgabe, die sie
  vermeiden sollte.
- Das ist kein Randfall: der Zeitstempel „spricht gerade" wird bei **jedem**
  Audioschnipsel erneuert. Redet das Modell durchgehend, wird es nie still. Im
  Protokoll der gemeldeten Sitzung reihte sich die Sprachausgabe von 11:49:26
  bis 11:50:04 fast lückenlos aneinander — 38 Sekunden am Stück.
- Jetzt wird bis zu drei Minuten auf eine echte Pause gewartet, statt die
  Meldung zu verheizen. Erst danach wird sie notfalls doch eingespielt — lieber
  riskieren, dass sie verschluckt wird, als eine fertige Aufgabe gar nicht zu
  melden.

### Geändert
- Jeder Ausgang steht jetzt im Protokoll: eingespielt mit Pause, eingespielt
  ohne Pause (Warnung), verworfen weil die Sitzung endete. Vorher schwieg die
  Funktion und meldete nur Fehler auf Debug-Ebene — deshalb war der Ausfall
  nicht nachvollziehbar.

---

## [1.222.2] - 2026-08-18

### Behoben
- **Im Chat kam eine Rückfrage des Agenten praktisch nicht an.** Ein Agent
  zeigte per `present_view` drei Bilder zur Auswahl — im Chat erschien nur ein
  Balken „Freigabe erforderlich" mit dem Werkzeugnamen (bei einer Rückfrage
  leer) und zwei festen Knöpfen. Weder die Frage noch die Antwortmöglichkeiten
  noch die Ansicht wurden gezeigt, und „Freigeben" schickte dem Agenten eine
  leere Bestätigung, aus der er nicht ablesen konnte, was gemeint war.
- Ursache: die Rückfrage-Anzeige gab es **dreimal** — Freigabe-Fenster,
  Sprachcockpit und Chat. Als die Antwortmöglichkeiten in 1.221.0 anklickbar
  wurden, bekamen zwei davon die Änderung; die dritte wurde übersehen.

### Geändert
- Die drei Fassungen sind zu **einer** zusammengelegt
  (`components/agents/approval-prompt.tsx`): Frage, Ansicht, Optionen, freie
  Antwort und Ablehnen — überall gleich. Drei Fassungen derselben Sache laufen
  immer auseinander; es ist nur eine Frage, welche zuerst vergessen wird.
- Der Chat deklarierte die Nutzlast nur zur Hälfte (`tool`, `reasoning`) und
  zeichnete sie deshalb auch nur zur Hälfte — Frage, Optionen und Ansicht
  standen in der Antwort der Schnittstelle längst drin.
- Der Chat rief die Schnittstelle mit rohem `fetch` und ohne Antwort auf; er
  geht jetzt denselben Weg wie alle anderen. Ein Test prüft, dass alle drei
  Flächen dieselbe Komponente benutzen.

---

## [1.222.1] - 2026-08-18

### Geändert
- **Die Activity-Ansicht (`/activity`) gruppiert Agenten jetzt nach Team,
  initial eingeklappt.** Bisher stand jeder Agent als eigene Zeile
  untereinander — bei vielen Agenten musste man an ihnen vorbeiscrollen, um
  den Kalender eines bestimmten Teams zu finden. Jede Team-Gruppe lässt sich
  einzeln aufklappen; Agenten ohne Team landen in einer eigenen
  „Ohne Team"-Gruppe.

---

## [1.221.1] - 2026-08-18

### Behoben
- **Eine einzelne Kollision genau im Cron-Takt kostete einer täglichen Aufgabe
  den ganzen Tag.** Betroffen war jede geplante Aufgabe (u. a. der tägliche
  Rhythmus „Abendplanung"/„Morgencheck"): war der Agent im exakten Moment des
  Zeitplans kurz beschäftigt, außerhalb seiner Dienstzeit oder ein
  Dispatch-Lock gerade belegt, sprang `next_run_at` sofort auf den nächsten
  Tag — keine zweite Chance. Nur der Überlast-Fall hatte seit v1.220.4 einen
  kurzen Wiederholungsversuch. Jetzt bekommen auch „außerhalb der
  Dienstzeit", „gerade beschäftigt" und „Dispatch-Lock belegt" bis zu zwei
  bzw. drei kurze Wiederholungsversuche, bevor der Slot für heute aufgegeben
  wird — Lock-Kollisionen (Sekunden) mit deutlich kürzerem Abstand als
  Dienstzeit-/Beschäftigt-Kollisionen (Minuten).
- **Ein altes, zweites Namensschema für Tagesplanungs-Zeitpläne
  (`[Plan] Morgencheck: …`/`[Plan] Abendplanung: …`, mit Datum im Titel)
  wurde vom Aufräum-Abgleich nicht erkannt** und feuerte für einen gestoppten
  Agenten seit Wochen alle 30 Sekunden ergebnislos. Der Abgleich erkennt jetzt
  beide alten Namensschemata, ohne echte, gleichnamig beginnende
  Plan-Blöcke (`[Plan] <eigener Titel>`) mit zu löschen.

---

## [1.222.0] - 2026-08-18

### Neu
- **Agenten können Ansichten einblenden statt nur Wortlisten** — `present_view`,
  in allen drei Laufzeiten. Der Agent zeigt etwas, hält an und bekommt die Wahl
  des Nutzers zurück. Erste Ansicht: `image_choice` — mehrere Bilder
  nebeneinander, ein Klick wählt. Der häufigste Fall: ein Marketing-Agent
  erzeugt Varianten und will wissen, welche.
- Gebaut als **Erweiterung der Rückfrage**, nicht als zweiter Weg daneben:
  dieselbe Zeile, dasselbe Anhalten des Agenten, derselbe Rückweg über
  `user_response`. Dadurch funktioniert eine Ansicht ohne weiteres Zutun in der
  Ablage, auf Telegram und auf dem Telefon — dort als Wortoptionen.
- Sichtbar **im Chat und im Sprachmodus**. Im Sprachcockpit zahlt sie sich am
  meisten aus: „das dritte, mit dem größeren Schriftzug" ist gesprochen mühsam
  und als Klick eine Sekunde.

### Sicherheit
- Der Agent liefert **kein Markup**, sondern einen Namen aus einer Liste. Ein
  Modell, das HTML in die Oberfläche schreiben darf, ist ein Einfallstor mit
  Zwischenschritt — zumal sein Rohstoff (Webseiten, Dateien, Mails) von außen
  kommt. Server und Frontend führen dieselbe Liste, ein Test hält sie zusammen.
- Unbekannte Namen und übergroße Nutzlasten werden verworfen, **die Rückfrage
  bleibt bestehen** — sonst stünde der Agent still und der Nutzer sähe nichts.
- Bilder werden als **Pfad** übergeben, nie als Inhalt: die Nutzlast liegt in
  derselben Zeile wie die Freigabe und geht über denselben Redis-Kanal.

### Behoben
- **Im Sprachmodus gingen Antwortmöglichkeiten verloren.** Dort standen fest
  `options[0]` und `options[1]` — bei einer Frage mit vier Antworten kamen zwei
  gar nicht an, und der Nutzer konnte die richtige nicht geben. Im Sprachmodus
  wiegt das besonders schwer: dieses Feld ist dort die einzige Stelle zum
  Antworten. Jetzt werden alle Optionen gezeigt, und die Wahl wird
  weitergereicht statt nur „genehmigt".

---

## [1.221.0] - 2026-08-18

### Behoben
- **Die Antwortmöglichkeiten einer Agenten-Rückfrage waren nicht anklickbar.**
  Ein Agent fragte nach und bot vier Antworten an — sie standen als reiner Text
  da. Zur Auswahl standen nur „Approve" und „Deny", und `approve` schrieb in
  `user_response` grundsätzlich `Approved by <mail>`. Der Agent erfuhr also
  nie, **welche** der vier Antworten gemeint war, und fragte im nächsten Zug
  erneut. Wer wirklich antworten wollte, musste **ablehnen** und die Antwort
  ins Begründungsfeld tippen — bei einer harmlosen Verständnisfrage.
- **Über Telegram ging das Wählen die ganze Zeit** (die Wahl landet dort in
  `user_response`), und der Custom-LLM-Weg liest dieses Feld seit jeher als die
  Wahl. Nur die Weboberfläche und der MCP-Weg hatten es nie bekommen — eine
  Lücke in der Parität der Laufzeiten, keine fehlende Funktion.
- Jetzt: Optionen als Knöpfe, im Detailfenster **und** in der Liste. Dazu ein
  Feld „Oder eigene Antwort", weil oft keine der angebotenen passt — im
  gemeldeten Fall lautete eine Option „bitte im Chat nennen", was den Nutzer
  aus dem Fenster hinausgeschickt hätte.
- Die Korrektur benutzt bewusst dieselben Felder wie der Telegram-Weg
  (`user_response`, Redis-Schlüssel `reason`) — die Agentenseite braucht keine
  Änderung.
- Der MCP-Weg gibt die Antwort jetzt auch bei Zustimmung an den Agenten weiter,
  nicht mehr nur bei Ablehnung.

### Geändert
- Die Wache gegen Kundennamen im öffentlichen Repo prüfte das ganze
  Arbeitsverzeichnis statt das, was tatsächlich veröffentlicht wird. Eine rein
  örtliche Notiz genügte, und die Prüfung war dauerhaft rot — ein dauerhaft
  roter Test wird ignoriert und fängt den echten Fall dann nicht mehr. Sie
  prüft jetzt den git-Index: alles Eingecheckte **und** alles Vorgemerkte.
  Vormerken ist genau der Moment, in dem gewarnt werden muss.

---

## [1.220.5] - 2026-08-18

### Geändert
- **Notifications, Dark Mode, Star on GitHub und Über AI Employee stehen jetzt
  im Nutzer-Untermenü statt als vier eigene Zeilen über dem Nutzereintrag in
  der Seitenleiste.** Ein Klick auf den eigenen Namen/Avatar öffnet jetzt ein
  einziges Menü mit allen vier Punkten plus Konto-Info, Einstellungen und
  Sign Out. Gilt für die ein- und ausgeklappte Seitenleiste.

---

## [1.220.4] - 2026-08-17

### Behoben
- **Kurzzeitige Agenten-Ueberlast laesst taegliche Zeitplaene nicht mehr sofort
  fuer den ganzen Tag ausfallen.** Wenn ein Agent beim Scheduler-Tick nur
  ueberlastet ist, versucht der Zeitplan jetzt bis zu zweimal nach etwa
  zwoelf Minuten erneut zu laufen. Erst beim dritten Ueberlast-Tick geht er
  wieder auf den regulaeren naechsten Slot.

---

## [1.220.3] - 2026-08-17

### Behoben
- **Ueberlast- und Ausfall-Meldungen erreichen jetzt tatsaechlich Telegram**
  (#610). Die Ursache lag tiefer als nur eine falsche Prioritaet: interne
  Meldungen des Vertretungssystems (`duty_service`) werden direkt in die
  Datenbank geschrieben und laufen nie ueber die API, die `priority="high"`
  in einen Telegram-Versand uebersetzt — sie blieben deshalb selbst mit
  "hoher" Prioritaet unsichtbar im Web-UI. Ein taeglicher Job wie der
  Podcast-Zeitplan konnte so ausfallen, ohne dass der Betreiber je davon
  erfuhr. Betroffen waren alle drei Eskalationsstufen (Ausfall, Ueberlast,
  unbeantwortete Rueckfrage); alle drei melden sich jetzt explizit am
  Telegram-Kanal. Die im selben Issue vorgeschlagene Wiederholung
  ausgefallener Zeitplaene und die Entzerrung zweier taeglicher 06:00-Jobs
  bleiben offen (naechster Schritt).

---

## [1.220.2] - 2026-08-16

### Behoben
- **5 Frontend-Abhängigkeiten auf Minor-Releases aktualisiert** (Dependabot-
  Sammel-PR): @xyflow/react, framer-motion, lucide-react, zustand,
  @types/node. Keine Major-Bumps, keine Breaking Changes laut Upstream —
  CI (Tests, CodeQL, Trivy, Secret-Scan, Node-Audit) vollständig grün.

## [1.220.1] - 2026-08-16

### Behoben
- **uvicorn im Embedding-Service auf 0.52.3 aktualisiert** (Patch-Release,
  Dependabot). Kein sicherheitsrelevanter Fund, reine Fehlerkorrektur
  upstream — CI (Tests, CodeQL, Trivy, Secret-Scan) vollständig grün.

## [1.220.0] - 2026-08-16

### Geändert
- **Jede Agenten-Vorlage sagt jetzt, WOFÜR der Agent da ist** — und woran man
  erkennt, dass er fertig ist. Bisher tat das **keine einzige** der 31
  Vorlagen: sie listeten auf, was der Agent *kennt*, nicht was er *liefert*.
  Neu in jeder Vorlage, ganz oben: `### Wofür du da bist` und
  `**Fertig heißt:** …`.
- **15 Vorlagen waren nur eine Technologieliste** — 190 bis 320 Zeichen eigener
  Inhalt, sonst nichts (QA-Tester, Code-Prüfer, Recruiter, Übersetzer,
  Produktverantwortlicher, Datenbank-Betreuer, Oberflächen-Gestalter,
  Schnittstellen-Entwickler, Texter, Web-Sammler, Automatisierer,
  SEO, Social Media, Rechtsunterstützung, Sicherheitsprüfer). Zum Vergleich:
  die ausgearbeiteten hatten 1.500–2.400 Zeichen. Alle 15 neu geschrieben mit
  Zweck, Abnahmekriterium, Arbeitsweise, Zusammenarbeit und Ablageort.
- **26 von 31 Beschreibungen waren englisch** — in einer durchgehend deutschen
  Oberfläche. Das ist der eine Satz, den ein Anwender beim Anlegen liest. Alle
  auf Deutsch, und mit Ergebnis statt Tätigkeit formuliert.
- Die vier deutschen Fach-Vorlagen (Buchhaltung, Lohn, Angebot, Disposition)
  hatten keinen Ablageort — Ergebnisse landeten irgendwo im Container.

### Behoben
- Die Kategorien `productivity` und `finance` hatten im Frontend keine
  Bezeichnung: drei Kacheln zeigten den rohen englischen Schlüssel. Zwei
  Listen, die niemand gegeneinander gehalten hat — dasselbe Muster, das am
  selben Tag einen Codex-Agenten komplett lahmgelegt hat. Ein Test hält sie
  jetzt zusammen.

### Hinweis
- Der Inhalt der elf älteren, bereits ausgearbeiteten Vorlagen bleibt vorerst
  englisch; nur ihr Zweck-Block ist deutsch. Der Text ist inhaltlich gut, eine
  Übersetzung ist ein eigener Schritt.

---

## [1.219.0] - 2026-08-16

### Behoben
- **Die mitgelieferten Agenten-Vorlagen waren für normale Anwender unsichtbar.**
  In der Datenbank lagen **31** Vorlagen — alle auf „nicht veröffentlicht". Die
  Liste blendet Unveröffentlichtes für Nicht-Administratoren aus, also stand
  beim Anlegen eines Agenten „Noch keine Vorlagen angelegt". Ein Administrator
  sah alle 31 und hielt es für in Ordnung; jeder andere sah null. Damit war die
  gesamte Vorlagen-Auswahl für normale Anwender tot.
- Ursache: Der Seeder legt sie mit `AgentTemplate(is_builtin=True, **daten)` an
  und setzte `is_published` nie — es griff die Vorgabe des Modells (`False`).
  Der Entwurf-/Veröffentlichen-Ablauf ist für die vom Administrator **selbst
  geschriebenen** Vorlagen gedacht; was mit dem Produkt kommt, ist fertig.
- Neue Anlagen bekommen sie ab sofort veröffentlicht. Bestehende zieht ein
  einmaliger Nachzug nach — bewusst **einmal**: „nicht veröffentlicht" bedeutet
  auch „ein Administrator hat sie abgewählt", und beim Abwählen wird
  `published_at` zurückgesetzt, die beiden Fälle sind danach nicht mehr zu
  unterscheiden. Liefe das bei jedem Start, käme eine abgewählte Vorlage immer
  wieder zurück.

### Geändert
- Die Sichtbarkeitsregel selbst bleibt unangetastet: Nicht-Administratoren sehen
  weiterhin nur Veröffentlichtes, und aus einem Entwurf lässt sich weiterhin
  kein Agent starten. Tests wachen über beides.
- Der Nachzug läuft im Test gegen eine echte Datenbank, inklusive des Ablaufs
  „Nachzug → Administrator wählt ab → Neustart".

---

## [1.218.2] - 2026-08-16

### Behoben
- **Kein Spinner beim Laden der Agenten-Vorlagen.** Über eine langsame Leitung
  stand dort nur grauer Text „Vorlagen werden geladen…" — nicht erkennbar, ob
  überhaupt noch etwas passiert. Jetzt: Platzhalter-Kacheln in genau dem Raster
  der echten Vorlagen plus drehender Ladering. Der Inhalt springt beim
  Eintreffen nicht mehr, und „Leerer Agent" ist die ganze Zeit anklickbar — wer
  keine Vorlage will, muss gar nicht warten.
- **Ein Fehlschlag sah exakt aus wie Laden — für immer.** Die leere Liste hieß
  zugleich „lädt noch" und „ist fehlgeschlagen"; der `catch`-Zweig setzte
  stillschweigend eine leere Liste, und die Meldung blieb stehen. Jetzt gibt es
  drei unterscheidbare Zustände: lädt, da, nicht erreichbar — letzteres mit
  Hinweis auf „Leerer Agent" und einem Knopf „Erneut versuchen".

---

## [1.218.1] - 2026-08-16

### Behoben
- **Codex-Agenten standen ohne ein einziges Werkzeug da.** Im Container:
  `Error loading config.toml: duplicate key … [mcp_servers.msgraph]`. Codex
  bricht beim ersten doppelten Schlüssel ab und lädt danach **gar keine**
  Konfiguration — der Agent konnte nur noch reden. Genau das war die Ursache
  des „er kündigt an und tut nichts" aus 1.218.0: er *wollte* arbeiten und
  *konnte* nachweislich nicht. Ein Auftrag scheiterte wörtlich mit „Task failed
  at startup due to a configuration error (duplicate key in config.toml)".
- Ursache war die Überschneidung zweier Listen: `msgraph` ist seit dem 12.08.
  ein **eingebauter** Server (Parität mit Claude Code) und wurde zusätzlich als
  HTTP-Server **eingeschleust**, sobald ein Agent die Microsoft-Integration
  zugewiesen bekam. Ein doppelter Name legte den ganzen Agenten lahm.
- Jetzt gewinnt der eingebaute Server, der Doppelgänger wird übersprungen und
  protokolliert. Die Prüfung lädt die fertige Datei als TOML — sie kann also
  nicht mehr an zwei Listen scheitern, die niemand gegeneinander hält.

- **Die persönliche Codex-Anmeldung lief ins Leere.** Gerätecode eingegeben,
  ChatGPT meldete „Seite kann geschlossen werden" — und die Oberfläche stand
  weiter auf „Warte auf die Bestätigung…". Ein einziges fallengelassenes
  Argument: `start()` nahm `for_user_id` entgegen und übernahm es nicht in die
  Sitzung.
- Folge 1: Die Zustandsabfrage verglich `session.for_user_id != user.id` und
  antwortete **404** — die Anzeige wartete endlos.
- Folge 2 (schwerwiegender): Der Abschluss nahm den Anlagen-Zweig. Das private
  ChatGPT-Konto des Nutzers wurde als Zugang der **ganzen Anlage** gespeichert
  und per `sync_auth_json()` in die **gemeinsame Datei aller Codex-Agenten**
  geschrieben.

### Geändert
- Beide Fehler haben jetzt Tests, die den Ablauf **ausführen** statt den
  Quelltext zu durchsuchen. Die bisherige Prüfung fand den persönlichen Zweig
  im Code — er war nur nie erreichbar. Beide neuen Prüfungen schlagen ohne die
  Korrektur nachweislich fehl.

---

## [1.218.0] - 2026-08-16

### Behoben
- **„Ich mache das jetzt" — und dann passiert nichts.** Per Sprache erbeten:
  „bau mir mal eine kleine Taschenrechner-App". Der Agent antwortete „Ich kümmere
  mich sofort … ich plane jetzt die Entwicklung" — **ohne einen einzigen
  Werkzeugaufruf**. Auf „und blödelst du" erneut „ich erstelle und deploye sie
  jetzt" — wieder nichts. Erst auf „Hast du die App gebaut!!!???" sah er nach und
  gab zu: „Nein, wurde noch nicht gebaut."
- Im **Auftrags**-Pfad ist das seit v1.178.2 abgesichert. Der **Chat**-Pfad hatte
  die Prüfung nie — und die Sprachfront läuft über den Chat.
- Der Anstupser ist bewusst **enger** als beim Auftrag: im Chat ist Reden der
  Normalfall („hallo", „erklär mir X"). Auslöser ist nicht die fehlende Arbeit,
  sondern der **Widerspruch** zwischen Zusage und Untätigkeit — der ist
  nachweisbar falsch, egal worum es geht.
- Reines Nachschlagen zählt dabei **nicht** als Arbeit: drei Blicke in die eigene
  Wissensdatei sehen nach Tätigkeit aus und sind keine. Genau so entstand der
  Eindruck im gemeldeten Fall.
- Der Anstupser nennt **beide** Wege — selbst machen oder an einen Kollegen
  delegieren — und lässt ausdrücklich ein begründetes Nein zu. Ohne diesen Ausweg
  erfindet ein Agent, der etwas nicht kann, Arbeit. Einmal je Nachricht.

---

## [1.217.0] - 2026-08-16

### Hinzugefügt
- **Ein wegen Überlast übersprungener Zeitplan verschwindet jetzt nicht mehr
  spurlos.** War ein Agent zum Zeitpunkt eines fälligen Zeitplans überlastet
  (Warteschlange voll), wurde der Lauf bisher nur intern protokolliert (auf
  einer Stufe, die im Fehler-Log gar nicht auftaucht) und der nächste Termin
  stillschweigend vorgerückt — weder Nutzer noch Agent erfuhren davon. Genau
  das ist am 16.08. mit dem täglichen 06:00-Podcast-Zeitplan passiert (siehe
  Issue #605): die anderen 06:00-Jobs desselben Agenten liefen normal, nur
  dieser eine fiel unbemerkt aus.
- Wird ein Zeitplan jetzt wegen Überlast übersprungen, bekommt der Besitzer
  eine Benachrichtigung mit normaler Priorität (kein Vertreter-Handover
  nötig — der Agent lebt, er ist nur beschäftigt). Gedrosselt auf eine
  Meldung pro Agent und Stunde, damit ein kurzgetakteter Zeitplan bei
  anhaltender Überlast nicht spammt.

---

## [1.216.0] - 2026-08-16

### Hinzugefügt
- **Ein stiller Datenbank-Ausfall bleibt jetzt nicht mehr stumm.** Bislang
  meldete der Scheduler eine kurz gestörte Datenbank nur als unauffällige
  Protokollzeile und versuchte es beim nächsten Takt (30 Sekunden später) neu
  — richtig so für einen kurzen Ruckler. Hielt der Ausfall aber mehrere
  Minuten an, konnte in dieser Zeit **kein** Zeitplan feuern, ohne dass
  jemand davon erfuhr — außer ein Zeitplan hatte zufällig sein eigenes,
  separates Sicherheitsnetz. Genau das ist am 15.08. passiert: ein rund
  30-minütiger Datenbank-Ausfall hat das Prüfen fälliger Zeitpläne über das
  06:00-Uhr-Fenster hinweg lahmgelegt (siehe Issue #601).
- Bleibt die Prüfung jetzt vier Takte in Folge (~2 Minuten) erfolglos, meldet
  der Scheduler das einmalig als dringende Benachrichtigung und per Telegram
  — unabhängig davon, ob für den betroffenen Zeitplan ein eigenes
  Sicherheitsnetz eingerichtet wurde.

## [1.215.0] - 2026-08-15

### Behoben
- **Die Codex-Anmeldung verlangte eine Datei, die es nicht gibt.** Nach der
  Bestätigung im Browser meldet ChatGPT „Seite kann geschlossen werden" — und
  danach stand die Oberfläche da und wollte den Inhalt einer `auth.json`. Diese
  Datei bekommt der Nutzer **nie zu sehen**: Codex legt sie im Container an, der
  Dienst liest sie und räumt das Verzeichnis wieder weg.
- Die Anmeldung schließt sich jetzt **von selbst ab**, genau wie beim
  Administrator: die Oberfläche fragt den Status ab, statt den Nutzer nach etwas
  zu fragen, das er nicht hat. Angezeigt wird der Gerätecode und „Warte auf die
  Bestätigung".
- **Das Ergebnis landet beim richtigen Empfänger.** Der Anmeldedienst bekommt
  jetzt mit, für wen er läuft: persönlicher Zugang statt Zugang der ganzen
  Anlage. Ein privates Abo wird ausdrücklich **nicht** in die gemeinsame Datei
  geschrieben — sonst benutzten es alle Agenten.
- Der Zustand einer fremden Anmeldung ist nicht abfragbar.

---

## [1.214.1] - 2026-08-15

### Behoben
- **„Mit Claude anmelden" antwortete mit 500.** Der neue Endpunkt baute den
  OAuth-Dienst von Hand und übergab ihm nur Redis — er erwartet aber
  `(db, redis)`. Jetzt wird dieselbe Abhängigkeit benutzt wie in
  `integrations.py`: kürzer, und auf diese Weise nicht falsch zu bauen.
- Ein Test hält Konstruktor und Aufruf jetzt **wirklich** gegeneinander. Die
  bisherigen Prüfungen lasen nur den Quelltext und konnten den Fehler deshalb
  nicht sehen.

---

## [1.214.0] - 2026-08-15

### Hinzugefügt
- **Eigenes Abo verbinden geht jetzt per Browser-Anmeldung** — dasselbe
  Verfahren wie beim Administrator, für **Claude und Codex**. Bisher gab es zwei
  Wege für dieselbe Sache: der Administrator klickte einen Knopf, der normale
  Nutzer musste ein Token aus `claude setup-token` bzw. den Inhalt einer
  `auth.json` von Hand einfügen. Das umständlichere Verfahren traf ausgerechnet
  den, der sich am wenigsten auskennt.
- Bei Claude öffnet sich die Anmeldeseite; der zurückgegebene Code wird eingefügt
  — **die ganze Adresszeile funktioniert auch**, denn die meisten kopieren sie
  statt des Codes darin. Bei Codex wird der Gerätecode angezeigt und ChatGPT
  geöffnet.
- **Der Weg von Hand bleibt** für alle, die ihr Token schon haben oder ohne
  Browser arbeiten.

### Behoben
- **Der Austausch landet in der richtigen Ablage.** Die Administrator-Anmeldung
  schreibt eine plattformweite Integration; der Agentenbau liest aber aus
  `user_ai_credentials`. Ohne diesen Schritt hätte sich ein Nutzer erfolgreich
  angemeldet — und seine Agenten liefen trotzdem ohne seinen Zugang. Der
  OAuth-Austausch selbst wird wiederverwendet, nicht ein zweites Mal gebaut.

---

## [1.213.0] - 2026-08-15

### Geändert
- **Der Reiter „Modelle" zeigt normalen Nutzern jetzt etwas anderes.** Bisher sah
  jeder dieselbe Seite: Provider-Konfiguration, ChatGPT-Login der Plattform, Max
  Turns, Anzahl gleichzeitiger Agenten. Ein Member kann davon **nichts**
  einstellen — er sah eine Bedienoberfläche, die auf keinen seiner Klicks
  reagiert.
- Stattdessen beantwortet sie jetzt die Frage, die er wirklich hat: **welche
  Modelle stehen mir zur Verfügung.** Lesend, ohne einen einzigen Schalter, aus
  den Konten, die sein Administrator freigegeben hat.
- Ist nichts freigegeben — der häufigste Fall bei einem neuen Nutzer — steht dort
  jetzt, **warum** und was zu tun ist: Administrator fragen, oder unter „Meine
  KI-Zugänge" das eigene Abo verbinden.
- **Der Speichern-Knopf erscheint nur, wo es etwas zu speichern gibt.** Auf
  „Meine KI-Zugänge" wird beim Verbinden sofort gesichert; ein Knopf daneben
  ließ den Nutzer glauben, er hätte etwas vergessen.

---

## [1.212.0] - 2026-08-15

### Sicherheit
- **Ein Agent ohne Besitzer war für JEDEN Nutzer sichtbar.** Aufgefallen, als ein
  frisch angelegter Testnutzer einen fremden Agenten in seiner Liste vorfand.
  Im Code stand das als Absicht („+ unowned + shared") — aber besitzlos wird ein
  Agent nicht durch eine Entscheidung, sondern durch ein **Versehen**: ein Skript
  ohne `user_id`, ein gelöschter Nutzer, eine Migration. Genau so ist er
  entstanden. Ein Versehen darf keine Freigabe auslösen.
- Fürs Teilen gibt es weiterhin den ausdrücklichen Weg (`AgentAccess`, und für
  Besprechungsräume `shared_for_rooms`) — der bleibt unverändert. Zwei
  Freigabewege nebeneinander, einer davon still, waren die eigentliche Ursache.
- Administratoren sehen besitzlose Agenten weiterhin (Admin-Konsole) und können
  sie zuweisen. Wird ein Agent ohne Besitzer angelegt, steht das jetzt im
  Protokoll, statt unbemerkt zu bleiben.

### Geändert
- **„Voice" und „System" sind für normale Nutzer ausgeblendet.** Ihre Inhalte
  waren längst adminbeschränkt, die Reiter selbst nicht — wer klickte, sah eine
  leere Seite und hielt es für einen Fehler. Ein Reiter ohne Inhalt ist
  schlechter als kein Reiter. Wer per Adresszeile dort landet, wird
  zurückgeholt. Kommt später etwas Nutzereigenes dazu (etwa eine zugewiesene
  Stimme), gehört die Bedingung gelockert statt der Reiter leer gelassen.

---

## [1.211.0] - 2026-08-15

### Hinzugefügt
- **Eigenes Claude-/Codex-Abo lässt sich jetzt verbinden** — Nutzermenü →
  Einstellungen → **Meine KI-Zugänge**. Verbinden, ersetzen, trennen; sichtbar
  ist nur, *dass* etwas hinterlegt ist, wann es zuletzt benutzt wurde und ob es
  funktioniert hat. Das Geheimnis gibt die Schnittstelle bewusst nie zurück.
- **Einstellungen sind über das Nutzermenü erreichbar.** Die Seite existierte,
  stand aber in keiner Menügruppe — sie war schlicht nicht auffindbar.

### Behoben
- **Der zweite der beiden Wege war in der Praxis nicht begehbar.** Die
  Schnittstelle `/me/ai-credentials` gibt es seit v1.185.0 und wurde im gesamten
  Frontend **kein einziges Mal** aufgerufen. Kein Nutzer konnte sein Abo
  hinterlegen — während die Agenten-Anlage seit v1.210.0 ausdrücklich darauf
  verweist („verbinde dein eigenes Abo unter Einstellungen"). Eine Fehlermeldung,
  die auf etwas Nichtexistierendes zeigt, ist schlimmer als gar keine.
- Ohne eigenen Zugang steht jetzt dort, was stattdessen greift: die Firmenlizenz,
  oder — falls keine freigegeben ist — ein KI-Konto vom Administrator.

---

## [1.210.0] - 2026-08-15

### Geändert
- **Ein Agent bekommt sein Modell jetzt aus genau zwei Quellen** — einem vom
  Administrator freigegebenen KI-Konto (Azure, AWS, Google, OpenAI) oder dem
  eigenen Claude-/Codex-Abo des Nutzers. Beide Wege gab es schon; neu ist, dass
  es keinen dritten mehr gibt.
- **Zugangsdaten lassen sich nicht mehr direkt am Agenten eintippen** (außer als
  Administrator, für Sonderfälle und zum Erproben eines neuen Anbieters). So ein
  Zugang gehörte niemandem, tauchte in keiner Übersicht auf, ließ sich nicht
  entziehen — und war beim nächsten Neuerstellen spurlos weg, weil er nur in den
  Umgebungsvariablen des Containers stand. Genau das ist heute passiert.
- Die Fehlermeldung nennt **beide** Wege statt nur zu sagen, was fehlt.

### Behoben
- **Kein stiller Modellwechsel mehr.** Führt das Konto das gewählte Modell nicht,
  nahm der Code kommentarlos den ersten Eintrag: ausgewählt war `gpt-5.6-sol`,
  gelaufen ist `gpt-5.3-codex` — ohne einen einzigen Hinweis. In einer Anlage, in
  der der Administrator Modelle **freigibt**, ist das der gefährlichste Fehler
  überhaupt, weil man glaubt, das freigegebene Modell zu benutzen.
- Der Rückfall bleibt (ein laufender Agent soll nicht stehenbleiben, weil jemand
  ein Modell aus dem Konto genommen hat), aber er ist jetzt **laut**: im Protokoll
  und als Meldung an den Besitzer, mit dem Hinweis, wie er es richtigstellt.

---

## [1.209.0] - 2026-08-15

### Behoben
- **Azure-AI-Foundry-Projektendpunkte funktionieren jetzt.** Die Azure-Oberfläche
  zeigt einen **Projekt**-Endpunkt zum Kopieren an
  (`…/api/projects/<name>`) — genau den trägt jeder ein, er steht ja da. Daran
  gehängt, antwortete Azure auf den klassischen Deployment-Pfad mit **400**: der
  gehört an die Ressource, nicht ans Projekt. Der Projektpfad wird jetzt
  abgeschnitten, statt den Nutzer raten zu lassen.
- Am laufenden Dienst nachgemessen: Projektpfad 400, Ressourcen-Wurzel 200.
  Klassische `*.openai.azure.com`-Endpunkte und die `…/openai/v1`-Oberfläche
  bleiben unverändert.

---

## [1.208.1] - 2026-08-15

### Geändert
- **Der Feedback-Knopf ist jetzt rund wie der Concierge-Knopf daneben.** Zwei
  Schaltflächen nebeneinander, von denen eine eine Pille mit Text und die andere
  ein Kreis ist, lesen sich wie zwei verschiedene Baukästen. Gleiche Maße (44px),
  gleiche Rundung, gleicher Abstand, gleiches Verhalten beim Zeigen (wachsen
  statt aufhellen).
- Die Beschriftung „Feedback" **entfällt nur optisch** und bleibt im Markup:
  ein Knopf, der nur für Sehende beschriftet ist, ist für alle anderen ein
  leerer Kreis.

---

## [1.208.0] - 2026-08-15

### Behoben
- **Der Sentinel war blind für den gesamten Chat-Verkehr.** Er lauschte nur auf
  `agents:logs:all` — der Gesprächsweg schreibt aber auf einen eigenen Kanal je
  Agent (`agent:{id}:chat:response`). Ein Geheimnis in einer Chatantwort hätte er
  nie gesehen. Betrifft **alle** Agenten-Modi; bei interaktiv genutzten
  Custom-LLM-Agenten ist der Chat der Hauptweg, dort war die Lücke am größten.
- Gelöst per Mustersuche (`agent:*:chat:response`) statt durch eine Änderung am
  Veröffentlicher — so bleiben die bestehenden Lauscher unberührt.

---

## [1.207.0] - 2026-08-15

### Behoben
- **Der Sentinel hielt einen Agenten wegen einer Vermutung an.** Drei Minuten
  nach dem ersten Einschalten stoppte er den Hauptagenten — wegen einer
  Zeichenkette, die mit „GH" anfing. Auslöser war die `KEY=VALUE`-Heuristik des
  DLP-Filters („alles, was TOKEN/SECRET/PASSWORD heißt, gefolgt von vier
  Zeichen").
- **Maskieren und Anhalten brauchen verschiedene Schwellen.** Fürs Maskieren ist
  diese Heuristik goldrichtig — im Zweifel schwärzen kostet nichts. Als Auslöser
  für einen Stopp zerstört sie laufende Arbeit wegen einer Vermutung.
- Der Sentinel prüft jetzt nur noch auf Geheimnisse, die am **Format** erkennbar
  sind: `ghp_…`, `sk-…`, `AKIA…`, JWTs, PEM-Blöcke, Slack- und Telegram-Token.
  `GH_TOKEN=nicht-gesetzt` oder `API_KEY=<dein-schlüssel>` in einer Anleitung
  halten niemanden mehr an. Der Egress-Filter maskiert unverändert großzügig.

---

## [1.206.2] - 2026-08-15

### Behoben
- **Der Sentinel meldete „Agent wurde angehalten", auch wenn das Anhalten
  fehlschlug.** Im Ende-zu-Ende-Lauf aufgefallen: der Betreiber hätte sich in
  Sicherheit gewiegt, während der Agent weiterlief — die schlimmste Sorte
  Falschmeldung. Ursache ist die Bauart: Stopp und Meldung laufen **absichtlich**
  gleichzeitig, damit ein hängender Stopp den Alarm nicht verzögert. Die Meldung
  kann den Ausgang also gar nicht kennen und behauptet ihn jetzt auch nicht mehr.
- **Scheitert das Anhalten, kommt eine zweite, dringendere Meldung:** „Der Agent
  läuft weiter, bitte von Hand stoppen." Erkannt-aber-nicht-gestoppt ist der
  gefährlichere Fall und braucht mehr als eine Zeile im Prüfprotokoll.

---

## [1.206.1] - 2026-08-15

### Behoben
- **Der Sentinel liess sich gar nicht einschalten.** Der Schalter stand in der
  Konfiguration, aber nicht in `docker-compose.yml` — die Datei reicht Variablen
  **einzeln** durch, was dort fehlt, kommt nie im Container an. Wer ihn über die
  `.env` aktivieren wollte, bekam einen stumm ausgeschalteten Dienst, ohne
  Fehlermeldung. Gilt genauso für den Redis-ACL-Schalter. Beide sind jetzt
  durchgereicht und stehen weiterhin standardmäßig auf `false`.

---

## [1.206.0] - 2026-08-15

### Hinzugefügt
- **Wer bewacht den Wächter (#590 Punkt 6).** Ein Sentinel, der unbemerkt
  stehenbleibt, ist gefährlicher als gar keiner: die Anlage sieht überwacht aus
  und ist es nicht. Der Dienst legt jetzt alle 15 Sekunden ein Lebenszeichen ab —
  **in der Warteschleife, nicht beim Ereignis**, denn ein Sentinel, der stundenlang
  nichts sieht, ist gesund; einer, der hängt, nicht. Ohne diesen Unterschied sähen
  beide gleich aus.
- Der Wachhund im Zeitplaner prüft es bei jedem Takt und meldet **einmal**
  dringend, wenn es älter als zwei Minuten ist — acht verpasste Schläge, genug für
  eine Redis-Neuverbindung, zu wenig für unbemerkten Stillstand.
- Ein **fehlendes** Lebenszeichen ist bewusst kein Alarm: dann ist der Dienst
  ausgeschaltet, und das ist eine Entscheidung des Betreibers.

---

## [1.205.0] - 2026-08-15

### Hinzugefügt
- **Der Sentinel tut jetzt etwas** (Epic #588, Teile #590 Punkt 4 und #592).
  Bisher war er ein Gerüst: die Erkennung gab immer „nichts gefunden" zurück,
  Anhalten und Melden waren Attrappen, die nur protokollierten. Man konnte ihn
  einschalten, und er tat nachweislich nichts.
- **Erkennung — bewusst schmal und deterministisch, ohne Modellaufruf.** Dieser
  Pfad sieht jedes Ereignis jedes Agenten; ein Modellaufruf pro Ereignis wäre
  weder bezahlbar noch schnell genug für den Zweck, eine schädliche Handlung
  *während* sie geschieht zu erwischen. Zwei Signale, beide aus vorhandenen
  Bausteinen:
  - **Geheimnis in der Ausgabe.** Der Egress-Filter sieht nur, was nach draußen
    geht — der Sentinel sieht auch Werkzeugaufrufe und -ergebnisse. Ein
    Zugangsschlüssel dort ist ein Vorfall, egal ob er je verschickt wird.
  - **Prompt-Injektion.** Genau der Fall, den ein Agent per Selbstprüfung nicht
    abfangen kann, weil die Injektion diese Selbstprüfung mit angreift.
- **Anhalten und Melden sind verdrahtet.** Der Agent wird wirklich angehalten
  (in-process, wie der Zeitplaner es tut), es entsteht ein Eintrag im
  Prüfprotokoll und eine dringende Benachrichtigung mit Sprung zum Agenten.
- **Der Auszug im Bericht enthält nie den Klartext eines Geheimnisses** — nur
  Anfang und Ende. Ein Vorfallbericht, der das Geheimnis erneut ausschreibt, wäre
  selbst ein Leck.
- **Schutz gegen Sturmfeuer:** derselbe Vorfall desselben Agenten löst innerhalb
  einer Minute nur einmal aus. Ohne das würden aus einem Leck ein Dutzend Stopps
  und ein Dutzend Meldungen.
- **Fail-open bleibt Pflicht:** ein Fehler in der Erkennung lässt das Ereignis
  durch und hält niemanden an. Scheitert das Anhalten, entsteht der Protokoll-
  Eintrag trotzdem — ein Vorfall ohne Spur ist schlimmer als einer ohne Reaktion.
- **Beide Schalter bleiben aus.** Der Dienst kann jetzt Agenten anhalten; das ist
  eine Entscheidung des Betreibers, nicht eine Nebenwirkung eines Updates.

### Geändert
- **Die Agenten-Anleitung verlangt jetzt Release-Disziplin.** Am 14.08. liefen
  sechs Pull Requests mit über 1000 Zeilen in die Hauptlinie — ohne
  Versionssprung, ohne CHANGELOG-Eintrag. Nicht aus Nachlässigkeit: in der
  Anleitung stand dazu nichts. Jetzt steht dort, dass `VERSION`, das
  Dockerfile-Label und ein Eintrag im CHANGELOG zu jeder Änderung gehören — und
  dass der Eintrag beschreibt, was sich für den **Nutzer** ändert, nicht welche
  Dateien angefasst wurden.

---

## [1.204.0] - 2026-08-15

### Behoben
- **Die Agenten-Redis-Zugänge hätten jeden Agenten ausgesperrt.** Der Rauchtest
  gegen ein echtes Redis 7.4 zeigte: der eingeschränkte Zugang durfte kein
  `PING` — und darauf stützen sich Verbindungsaufbau und periodische
  Gesundheitsprüfung von `redis-py`. Mit eingeschaltetem Schalter wäre kein Agent
  hochgekommen. `+@connection` ergänzt, **vor** den Verboten: die Kategorie
  enthält auch `CLIENT LIST`, das die folgenden `-@admin`/`-@dangerous` wieder
  entziehen. Am laufenden Server nachgeprüft.

### Hinzugefügt
- **Rauchtest der Agenten-ACL gegen ein echtes Redis** (`test_redis_acl_live_smoke.py`).
  Prüft, was kein Modultest kann: ob ein laufender Server die Regeln annimmt und
  ob sie bewirken, was draufsteht — eigener Schlüsselraum ja, fremde Schlüssel
  nein, Postfach eines Kollegen befüllen ja, mitlesen oder leeren nein, keine
  Adminbefehle. Überspringt sich ohne `REDIS_SMOKE_URL`, damit die normale Suite
  ohne Redis grün bleibt.

### Nachgetragen — Sentinel, Teil 1 bis 3 (Epic #588)
Die folgende Arbeit wurde am 14.08. zusammengeführt, aber **ohne Versionssprung
und ohne CHANGELOG-Eintrag**; hier nachgetragen:
- **Teil 1 (#589):** eigener, minimal berechtigter Redis-Zugang je Agent statt
  des einen geteilten Admin-Zugangs. Bisher kann jeder Agent auf dem Kanal jedes
  anderen veröffentlichen und sich so als dieser ausgeben. Schalter aus.
- **Teil 2 (#590):** Gerüst des Sentinel-Dienstes plus eigenes, domänengetrenntes
  Zugangsschema (konstantzeitiger Vergleich). Erkennung, Stopp und Meldung sind
  noch Attrappen — der Dienst tut heute nichts. Schalter aus.
- **Teil 3 (#591):** die Freigabe-Vorgänge veröffentlichen ihre Ereignisse in die
  Sentinel-Leitung.
- Außerdem: Web-Push behandelt 403 als „endgültig weg", Mikrofon-Aufnahme für die
  Desktop-Brücke, `numpy` in der Test-Umgebung der CI.

---

## [1.203.0] - 2026-08-13

### Behoben
- **Eine abgerissene Verbindung tötet nicht mehr die ganze Aufgabe.** Bei einem
  Kunden scheiterten drei Aufgaben an `ReadError('')` — der Abbruch traf jeweils
  das Lesen der Modell-Antwort. Eine Aufgabe, die vierzig Züge gelaufen war,
  starb an einem einzigen abgerissenen Lesevorgang.
- Zwei Lücken lagen übereinander: die Fehlerprüfung kannte nur „das Modell kann
  gerade nicht" (Rate-Limit, 5xx, Überlastung) — ein Socket-Abbruch passte auf
  keinen Marker. Und selbst mit Treffer hätte es nichts genutzt: die Wiederholung
  wechselt das **Modell**, und die Ausweichkette ist im Regelfall leer.
- Der Verbindungsabbruch ist jetzt eine **eigene Kategorie**: nicht Modell
  wechseln, sondern **denselben Aufruf noch einmal**. Das Modell war in Ordnung,
  die Leitung war es nicht. Höchstens zwei Versuche mit wachsender Pause — reißt
  es dreimal, liegt es nicht am Zufall und der echte Grund muss sichtbar werden.
- Ein Einrichtungsfehler (falscher Schlüssel, falscher Bereitstellungsname) bleibt
  auch hier sofort endgültig.
- Der Mensch sieht die Wiederholung im Protokoll — ein stilles Nochmal-Versuchen
  würde nur verschleiern, warum ein Lauf länger dauert.
- Gilt in **beiden** Laufzeiten (Auftrag und Chat).

---

## [1.202.0] - 2026-08-13

### Geändert
- **Die Agenten lernen die Namensregel selbst.** Sie schreiben inzwischen Code,
  Commits, Pull Requests und Issues — ohne Regel machen sie denselben Fehler wie
  wir, nur schneller und öfter. Die Anweisung steht jetzt in der Agenten-Anleitung
  und damit modusübergreifend bei Claude Code, Codex und Custom-LLM.
- Sie verbietet nicht nur, sondern sagt, **was stattdessen** dahin gehört
  („beim Kunden", „eine Kundenanlage", `example.com`), **warum** es beiläufig
  passiert (man notiert, wo ein Fehler auftrat — und der Ort hat einen Namen),
  und **wohin der Klarname gehört**: ins Gedächtnis des Agenten, nicht ins
  Repository. Ein privates Repo ist ausdrücklich keine Ausnahme.
- Bestandsagenten bekommen die Anleitung beim nächsten Update oder Neustart.

---

## [1.201.0] - 2026-08-13

### Sicherheit
- **Kunden-, Firmen- und Personennamen aus dem Repo entfernt.** Das Repo ist
  öffentlich; der Name eines Kunden stand an 34 Stellen in 20 Dateien — in
  Kommentaren, Tests, im CHANGELOG und im Benutzerhandbuch. Dazu der Nachname
  einer realen Ansprechperson in einem Test.
- **Die gravierendste Stelle war die Produkt-Oberfläche:** In den Einstellungen
  standen die internen Adressen eines Kunden als Platzhalter (Mailserver,
  Dienstkonto, interne IP). Die sah **jeder** Nutzer der Software — auch jeder
  andere Kunde. Ersetzt durch `example.com` bzw. neutrale Werte.
- **Eine Prüfung wacht darüber** (`test_no_customer_names_in_repo.py`): sie
  durchsucht den gesamten Quelltext nach einer Sperrliste und schlägt fehl, bevor
  ein Name wieder hineinrutscht. Ein Vorsatz reicht dafür nicht — der Ort eines
  Fehlers hat nun einmal einen Namen, und man schreibt ihn beiläufig auf.
- Der Sachverhalt bleibt überall nachvollziehbar: statt des Namens steht jetzt
  „beim Kunden" bzw. „eine Kundenanlage". Klarnamen gehören ins
  Projekt-Gedächtnis, nicht ins öffentliche Repo.
- **Reichweite:** Das schützt den aktuellen Stand. Was einmal öffentlich gepusht
  wurde, bleibt in der git-Historie und in fremden Klonen.

---

## [1.200.2] - 2026-08-13

### Behoben
- **Team-Lead-Rückmeldung endete mitten im Wort, bevor die eigentliche Antwort
  auftauchte.** Ein Team-Lead delegierte vier Testaufgaben ("gib exakt 'Hallo
  Welt' aus"); drei von vier Rückmeldungen brachen ab ("...reinen Au", "...I",
  "...lc"), noch bevor der geforderte Satz im sichtbaren Text erschien. Ursache:
  die Fertigmeldung wurde zweimal mit einem blossen `text[:n]` gekürzt (erst auf
  800, dann nochmal auf 300 Zeichen), ohne auf Wortgrenzen zu achten — der
  Pflicht-Vorspann der Sub-Agenten (Vorab-Checks: Tools laden, TODOs,
  Brain/Memory, Skill-Suche) war oft schon länger als das Limit. Kürzt jetzt an
  der letzten Wortgrenze, gleicher Fix in allen drei Harnessen (Webapp-Chat,
  Claude-Code-MCP, Custom-LLM).

---

## [1.200.1] - 2026-08-13

### Behoben
- **Die Protokoll-Ansichten waren im hellen Erscheinungsbild unlesbar.** Der
  Kasten stand fest auf Schwarz, während die Zeilen darin dem Erscheinungsbild
  folgen (`text-foreground`) — im hellen Modus also **dunkle Schrift auf
  schwarzem Grund**. Nicht nur unpassend, stellenweise schlicht nicht lesbar.
- Betroffen waren **drei** Stellen mit demselben Muster: das Live-Feld der
  Aufgabenseite, die Zeitreise-Ansicht darunter und der Live-Terminal des
  Agenten. Alle drei folgen jetzt dem Erscheinungsbild.
- Die Akzentfarben der Zeilen (Werkzeug, Fehler, Ergebnis) waren auf schwarzen
  Grund abgestimmt und auf hellem ausgewaschen — im hellen Modus jetzt eine
  dunklere Stufe. **Der dunkle Modus bleibt unverändert.**
- Unangetastet: der Kiosk-Bildschirm (absichtlich schwarz) und der Rand hinter
  Bildschirmfotos.

---

## [1.200.0] - 2026-08-13

### Neu
- **Eigene Menüpunkte für fremde Seiten.** Ein Administrator legt eine Seite an
  (`/p/<kurzname>`), und sie erscheint als regulärer Menüpunkt — wahlweise
  **eingebettet** (`iframe`) oder als **Link** im neuen Tab. Anlass war OpenWebUI
  beim Kunden: die Oberfläche soll nicht „daneben" stehen, sondern im selben Menü
  erreichbar sein wie alles andere.
- **Keine zweite Rechte-Logik.** Wer die Seite sieht, entscheidet die vorhandene
  Rechtevergabe (`permissions.menu_paths`) — der Kurzname wird zum Rechte-Pfad.
- Ob sich eine fremde Seite überhaupt einbetten lässt, bestimmt allein diese
  Seite (`X-Frame-Options` / `frame-ancestors`). Das lässt sich weder erzwingen
  noch vorher erkennen; die Oberfläche weist darauf hin und bietet den Weg im
  neuen Tab an.

### Sicherheit
- **Menüziele nur nach `http`/`https`** — doppelt geprüft. Der Server weist
  andere Schemata beim Anlegen *und* beim Ändern ab; die Oberfläche prüft
  zusätzlich, für Einträge, die auf anderem Weg in die Datenbank gelangen. Ein
  `javascript:`-Ziel wäre fremder Code, der beim Klick in unserer eigenen
  Oberfläche liefe — mit der Sitzung des Angemeldeten.

### Behoben
- **Konfliktreste im CHANGELOG entfernt.** Seit einem unfertigen Rebase am
  Vormittag standen `<<<<<<< HEAD`-Marken zwischen den Einträgen 1.192.0 und
  1.191.2 in der Datei — beide Releases sind echt und stehen jetzt wieder
  vollständig da.

---

## [1.199.1] - 2026-08-13

### Behoben
- **„Eine Toolkette ist durch, aber der macht noch immer den Spinner mit
  Arbeitet.“** Wenn ein Agent im selben Zug mehrere Werkzeugketten nacheinander
  abarbeitet (etwa zwei Auftraege an zwei verschiedene Agenten delegieren),
  hing der „Arbeitet…“-Zustand jeder Kette am GESAMTEN Nachrichtenturn statt an
  der eigenen Kette. Eine laengst fertige Kette (alle Haekchen gruen) zeigte
  trotzdem weiter den drehenden Kreis — und erst wenn der komplette Turn zu
  Ende war, klappten ALLE Ketten gleichzeitig auf ihre Endanzeige um, statt
  jede fuer sich, sobald sie selbst fertig ist.
- Nur die zuletzt begonnene Kette im Turn erbt jetzt den laufenden Zustand;
  jede fruehere Kette richtet sich nach dem Status ihrer eigenen Werkzeuge.

---

## [1.199.0] - 2026-08-13

### Geändert
- **Die Rollenverwaltung zeigt unten nur noch die Mitglieder der Rolle.**
  Kundenwunsch: *„UNTEN sind alle User in der App zu sehen, aber ich brauche
  dort nur die User (aufklappbar) die wirklich in der Rolle."* Bisher stand dort
  jeder Nutzer der Plattform mit einer eigenen Auswahlbox daneben — bei vielen
  Nutzern eine Wand aus Dropdowns, in der man die eigentliche Frage („wer gehört
  zu dieser Rolle?") nicht beantworten konnte. Jetzt: ein aufklappbarer Block
  **Mitglieder** mit Zähler, darin ausschließlich die Nutzer dieser Rolle,
  Entfernen direkt an der Zeile.
- **Hinzufügen läuft jetzt über die Suche statt über 40 Dropdowns.** „User
  hinzufügen" öffnet eine Namens-/E-Mail-Suche über die Nicht-Mitglieder. Steckt
  jemand schon in einer anderen Rolle, steht das daneben — ein Umhängen ist damit
  sichtbar und nicht versehentlich.
- In der Rollenliste links steht pro Rolle die Mitgliederzahl.
- **Das Admin-Menüband ist zweistufig statt 13 Reiter in einer Scrollzeile.**
  Kundenwunsch: *„Gern kann auch einfach mal das komplette Menüband angepasst
  werden und thematisch zusammengefasst werden und dann mit subtabs gearbeitet
  werden."* Sechs Themengruppen — Nutzer & Rollen, Agenten, KI & Wissen,
  Sicherheit, Betrieb, System — mit den jeweiligen Unterreitern darunter. Damit
  ist jeder Bereich ohne seitliches Scrollen erreichbar; offenes Feedback meldet
  sich mit einem Punkt an der Gruppe.

---

## [1.198.2] - 2026-08-13

### Sicherheit
- **Zweite Sperre gegen `javascript:` in selbst angelegten Menüpunkten.** Der
  Server ließ schon immer nur `http`/`https` durch — beim Anlegen *und* beim
  Ändern. Die Oberfläche prüft das Schema jetzt zusätzlich, für Einträge, die
  vor dem Validator entstanden sind oder auf anderem Weg in die Datenbank
  gelangen. Ein ungültiger Eintrag verschwindet, statt still auf `#` zu zeigen.

---

## [1.198.1] - 2026-08-13

### Behoben
- **„Es wirkt ein wenig wie eingeschlafen."** Während der Agent nur Werkzeuge
  aufrief, stand über der Werkzeugzeile „4 Tools" statt „Arbeitet…" — und nichts
  bewegte sich. Die Bedingung fragte, ob gerade ein **Werkzeug** rechnet, nicht
  ob der **Zug** läuft. Genau in der Denkpause zwischen zwei Werkzeugen (alle
  Ergebnisse zurück, der Agent verarbeitet sie) wurde sie falsch. Der obere
  „Thinking…"-Block half nicht: der weicht, sobald eine Antwortnachricht
  existiert.
- Das nötige Wissen wurde bereits übergeben, aber **nie ausgepackt** —
  `isStreaming` stand in der Typangabe der Komponente und wurde verworfen.
- Dazu ein Kreis, der sich wirklich dreht. „Arbeitet…" allein liest man nicht als
  Bewegung — das stand schon im ersten Kundenfeedback zu dieser Zeile.

---

## [1.198.0] - 2026-08-13

### Geändert
- **Der Chat sieht jetzt gleich aus, egal wer den Zug angestoßen hat.**
  Kundenwunsch: *„wenn er nach einer Delegation noch weiter arbeitet, dann muss
  sich der Chat-Link in der Sidebar wie im normalen Chat weiter drehen."*
- Beginnt der Agent **von sich aus** einen Zug — nach einer Delegation, nach
  einer Fertigmeldung, aus einem Zeitplan — erfuhr die Seite davon bisher erst
  beim nächsten 15-Sekunden-Takt. Ein kurzer Zug war bis dahin vorbei: die
  Gesprächszeile blieb blass, obwohl im Fenster „Thinking…" lief. Das erste
  Ereignis eines solchen Zuges löst jetzt sofort ein Nachfassen aus.
- Der **eigene** laufende Zug markiert seine Gesprächszeile unmittelbar, statt
  auf die abgefragte Liste zu warten.

---

## [1.197.2] - 2026-08-13

### Geändert
- **Die „In Arbeit"-Zeile zählt nur noch die offenen Aufträge.** Der Bruch
  („3 von 6") bezog sich auf das ganze Gespräch und war nicht lesbar, wenn man
  gerade 4 Aufträge vergeben hatte — man sucht dann die 6, die im Bild nicht
  vorkommen. Jetzt steht dort schlicht, worauf noch gewartet wird.

---

## [1.197.1] - 2026-08-13

### Behoben
- **Kacheln fehlten, sobald der Agent `create_task_batch` wählte.** In derselben
  Sekunde entstanden vier Aufträge desselben Auftraggebers — zwei trugen den
  Gesprächsfaden, zwei nicht. Es gibt **drei** Werkzeuge, die Aufträge anlegen
  (`create_task`, `create_task_batch`, `delegate_and_wait`); jedes baute seine
  Nutzlast selbst, und als der Faden dazukam, wurde er an zwei von dreien
  angehängt. Alle drei gehen jetzt über **einen** gemeinsamen Bauplan — ein neues
  Feld gilt damit sofort für alle.
- **Der Notweg des Orchestrators überstand keine Parallelarbeit.** Kennt der
  Werkzeugserver den Faden nicht (Claude Code), ermittelt ihn der Orchestrator
  selbst — bisher aus `current_task`, das aber nur **eine** Arbeit trägt. Der
  Agent lief nebenher an einer Zeitplan-Aufgabe, dort stand deren Kennung, und
  der Chat war unsichtbar. Jetzt zählt die vollständige Liste der laufenden
  Arbeiten; bei mehreren offenen Gesprächen wird bewusst **nicht** geraten — eine
  Kachel im falschen Chat wäre schlimmer als keine.
- Der alte Test **zählte** nur, wie oft der Faden angehängt wird („zweimal,
  passt") — und war deshalb zufrieden, während das dritte Werkzeug fehlte. Er
  prüft jetzt jedes Werkzeug einzeln.

---

## [1.197.0] - 2026-08-13

### Hinzugefügt
- **„In Arbeit"-Anzeige im Chat, solange delegierte Aufträge laufen.**
  Kundenwunsch: *„Ich wollte im Chat eine Anzeige haben, dass noch am Thema
  gearbeitet wird (in Progress, warte noch auf SubAgents Rückmeldung)."*
  Die Lücke war echt: **nach dem Delegieren ist der Zug des Agenten beendet**,
  also lief kein „Thinking…"-Spinner, obwohl die Aufträge noch liefen. Wer nicht
  nachfragte, sah gar nichts.
- Die Zeile nennt den Stand (*3 von 5 erledigt*) **und wen es noch braucht**
  (*wartet auf DevAgent, MarketingMaker*) — nicht nur, dass irgendetwas läuft.
- Sie hängt bewusst **nicht** am eigenen Zug des Agenten, sonst wäre sie genau
  dann verschwunden, wenn man sie braucht. Ausgeblendete Kacheln zählen nicht
  mehr mit.

---

## [1.196.0] - 2026-08-13

### Geändert
- **Fehler des Modell-Aufrufs erklären sich jetzt selbst.** Auslöser: drei
  Aufgaben bei der Kundenanlage scheiterten mit `Unexpected error: ReadError('')` — die
  Klammer war **leer**. Um überhaupt einzugrenzen, *wo* es reißt, mussten 110
  gespeicherte Aufgabenschritte durchgesehen werden.
- **Die Ursachenkette wird mitgeliefert.** `httpx` verpackt den Socket-Abbruch;
  `ReadError` ist nur die Hülle, darunter steht der eigentliche Grund
  (`ConnectionResetError`, `SSLEOFError`, `EndOfStream`, `IncompleteRead`). Genau
  der wurde bisher weggeworfen.
- **Die Umstände stehen dabei:** Modell, Endpunkt (nur Host), Anzahl Nachrichten,
  Größe der Anfrage in Zeichen und die Laufzeit bis zum Abbruch. Damit sagt der
  nächste Vorfall selbst, ob es an Größe, Endpunkt oder Zeitpunkt liegt.
- Gilt für **alle** Anbieter (OpenAI/Azure, Anthropic, Gemini) und alle
  Fehlerstellen — auch `Connection failed` und `Request timed out`, die bisher
  ebenfalls ohne Kontext meldeten.
- **Ohne Inhalte:** Größen, Anzahl und Host ja — Prompt nein. Die Abfragezeichen-
  kette wird abgeschnitten, damit der Gemini-Schlüssel (`?key=…`) nicht in
  Protokollen landet.

---

## [1.195.0] - 2026-08-13

### Behoben
- **Delegation an einen gelöschten Agenten scheitert jetzt laut statt still.**
  Bisher landete so ein Auftrag als `PENDING` **ohne `agent_id`** in der Datenbank
  — und blieb dort für immer liegen, denn der Reparaturlauf sucht ausdrücklich
  nur `PENDING`-Aufträge **mit** `agent_id`. Niemand erfuhr davon, am wenigsten
  der Auftraggeber, der auf ein Ergebnis wartete, das nicht kommen konnte. Auf dem
  Pi lagen **13** solcher Waisen.
- Der Fehler ist an den **Agenten** gerichtet und nennt die Kollegen, die es
  wirklich gibt (nur die seines Teams — die Mandantentrennung gilt auch in einer
  Fehlermeldung). Damit korrigiert er seinen Auftrag im selben Zug selbst.
  Auslöser war ein Agent, dessen Erinnerung **korrekt** war: den Kollegen gab es
  einmal, er wurde gelöscht, und niemand hatte es ihm gesagt.
- Im Hintergrund reißt nichts ab: ein Workflow-Schritt auf einen gelöschten
  Agenten lässt den Lauf **mit Begründung** scheitern statt stumm zu hängen, und
  ein fortgesetzter Auftrag wird verworfen statt bei jedem Start erneut versucht.

---

## [1.194.0] - 2026-08-13

### Geändert
- **Das Onboarding ist jetzt vollständig entfernt — auch dort, wo es noch
  nachwirkte.** Das Einrichtungsgespräch war in 1.187.x aus `knowledge.md`
  entfernt worden, der Rest der Mechanik lief aber weiter: neue
  `claude_code`-Agenten bekamen weiterhin `onboarding_complete: false`, und der
  Zeitplaner blendete ihnen bei **jedem** proaktiven Lauf genau das abgeschaffte
  Interview ein („Welche Rolle sollst du ausfüllen?"). Neue Agenten gelten jetzt
  als eingerichtet — was sie tun sollen, steht in ihrer Vorlage.
- **Der Zeitplaner prüft nur noch die Verantwortungsbereiche.** Der zusätzlich
  geprüfte Einrichtungshaken war zur Falle geworden: seit das Gespräch entfällt,
  konnte ihn nichts mehr setzen. Ein Bestandsagent mit `false` wäre für immer von
  proaktiven Läufen ausgeschlossen gewesen.
- **Bestandsagenten werden beim Start einmalig geradegezogen** (idempotent), damit
  kein Agent mit einem Haken zurückbleibt, den niemand mehr setzen kann.
- **Das Abzeichen „Nicht eingerichtet" ist von der Agentenkachel entfernt.** Es
  hätte nur noch einen Zustand angezeigt, den man nicht mehr ändern kann. Das
  Dreieck „Kein Auftrag — es fehlen Verantwortungsbereiche" bleibt: es ist
  weiterhin richtig, denn ohne Bereiche werden proaktive Läufe übersprungen.

---

## [1.193.2] - 2026-08-13

### Behoben
- **„Agent arbeitet gerade an dieser Unterhaltung…" blitzte nach jeder eigenen
  Antwort kurz auf.** Der Hinweis hängt an `busy && !isWaiting`, und `busy`
  stammt aus einer Abfrage im Vier-Sekunden-Takt — unmittelbar nach dem Zugende
  steht dort noch „beschäftigt". Bis 1.193.0 blieb `isWaiting` hängen und
  verdeckte das zufällig; erst das korrekte Abräumen machte den veralteten
  Messwert sichtbar. Der Hinweis wird jetzt für acht Sekunden nach dem **eigenen**
  Zug unterdrückt. Der echte Fall — man betritt ein Gespräch, in dem gerade
  gearbeitet wird — erscheint unverändert sofort.

---

## [1.193.1] - 2026-08-13

### Behoben
- **„Thinking…" blieb stehen, obwohl der Agent fertig war.** Das Chatfenster
  rechnete *eine Nachricht = ein Zug*: beim Senden hoch, beim `done` herunter,
  und erst bei null hörte das Warten auf. Live-Steering faltet eine nachgereichte
  Nachricht aber in den **laufenden** Zug — die Antwort kommt unter der Kennung
  der ersten, es gibt genau **ein** `done`. Zwei schnell hintereinander gesendete
  Nachrichten ließen den Zähler dauerhaft auf 1 stehen: Spinner und Stop-Knopf
  blieben für immer aktiv. `done` beendet jetzt den Zug, Punkt; ein wirklich
  folgender Zug hebt die Anzeige über sein erstes Ereignis wieder an.
- **Drei geöffnete Gespräche zeigten gleichzeitig „Thinking…".** Derselbe Zähler
  gehörte dem Fenster, nicht dem Gespräch — beim Wechsel blieb er stehen, und die
  Ereignisse des verlassenen Gesprächs werden von der Faden-Abschottung
  verworfen, konnten ihn also nie mehr herunterzählen. Beim Gesprächswechsel wird
  er jetzt zurückgesetzt.
- **„Message received — steering current agent turn" blieb dauerhaft stehen**,
  unterhalb der fertigen Antwort. Der Hinweis wurde nur entfernt, wenn für genau
  diese Kennung eine Antwortnachricht entstand; eine gefaltete Nachricht bekommt
  nie eine eigene. Er ist Live-Zustand und verschwindet jetzt mit dem Zug.
- **Notbremse:** Geht ein `done` doch einmal verloren, gilt der Agent selbst als
  Wahrheit — meldet er über mehrere Runden, dass er an diesem Faden nicht
  arbeitet, endet die Anzeige. Bewusst träge, damit sie nie mitten im Denken
  abbricht.

### Geändert
- **„Aktiver Chat" erscheint jetzt in ein bis zwei statt in sieben Sekunden.**
  Die Anzeige hängt am Zustand des Agenten, den die Agentenseite nur alle 15
  Sekunden abfragte — im Mittel wartete man 7,5 Sekunden auf den nächsten Takt.
  Der Agent war längst dran. Nach dem Absenden und am Ende eines Zuges fasst die
  Seite jetzt kurz nach, ohne dauerhaft häufiger abzufragen.

---

## [1.193.0] - 2026-08-13

### Geändert
- **Auftrags-Kacheln sind jetzt Elemente des Chatverlaufs.** Sie stehen an der
  Stelle, an der der Auftrag vergeben wurde — alles Spätere kommt darunter.
  Vorher hingen sie in einer eigenen Zone am Ende und rutschten bei jeder neuen
  Nachricht mit; laufende Aufträge lagen sogar in einem getrennten Streifen über
  dem Eingabefeld.
  Der Zustand wechselt an Ort und Stelle: „in Arbeit" → „abgeschlossen".
  Die beiden alten Darstellungsblöcke sind entfallen.

---

## [1.192.2] - 2026-08-13

### Neu
- **Kacheln lassen sich wegklicken.** Ein kleines Kreuz oben rechts (erscheint beim
  Darüberfahren) blendet eine erledigte Kachel aus. Nur die Anzeige — der Auftrag
  selbst bleibt bestehen und ist über die Aufgabenseite erreichbar.
  Solange die Kacheln noch am Ende des Verlaufs kleben statt an ihrer Stelle zu
  sitzen, ist das der schnellste Weg, sie loszuwerden, wenn man sie gesehen hat.

### Weiterhin offen
- Die Kacheln gehören an die Position im Gespräch, an der der Auftrag vergeben
  wurde — nicht ans Ende. Siehe HANDOVER.

---

## [1.192.1] - 2026-08-13

### Behoben
- **Leere graue Blasen im Verlauf.** Die gespeicherten Kachel-Zeilen haben keinen
  Text und wurden trotzdem als Nachrichten gezeichnet — vier Aufträge ergaben vier
  leere Blasen. Ihr Inhalt steckt in `meta.task_card`; sie werden jetzt aus dem
  Nachrichtenstrom herausgefiltert und ausschliesslich als Kachel dargestellt.

### Noch offen
- Fertige Kacheln stehen am **Ende** des Verlaufs, dadurch erscheinen neue
  Nachrichten darüber statt darunter. Richtig wäre: jede Kachel an ihrer Stelle im
  Gesprächsverlauf, neue Nachrichten danach.

---

## [1.192.0] - 2026-08-13

### Neu
- **In-App-Feedback-Widget („Feedback-Gedöns").** Feedback heftet jetzt an der
  Stelle, an der es entsteht: schwebender Button auf jeder Seite → konkretes
  UI-Element anpinnen → Viewport-Screenshot mit rotem Rahmen ums Element
  (abwählbar) → Sentiment (gefällt/stört/Wunsch) + Kategorie + Freitext → das
  LLM stellt genau EINE schärfende Requirements-Rückfrage („Direkt speichern"
  überspringt sie, auch ohne LLM-Zugang).
  Abgelegt wird pro Feedback eine Markdown-Datei mit Frontmatter (wer · Seite ·
  Element · Sentiment · Zeit) plus PNG in einem eigenen Volume (`FEEDBACK_DIR`,
  übersteht Redeploys) — zusätzlich wie bisher ein DB-Eintrag für die
  Admin-Liste, die neue Einträge samt Seite/Element/Screenshot zeigt. Der
  Username kommt ausschließlich aus der validierten Session, nie aus dem
  Request. Optional wird jedes Feedback best-effort als GitHub-Issue
  gespiegelt (`FEEDBACK_ISSUE_ENABLED`, Default aus) — ein Issue-Fehler
  verliert nie Feedback. Der alte Feedback-Modal-Dialog ist damit ersetzt.

---

## [1.191.2] - 2026-08-13

### Behoben
- **Der Streifen der laufenden Aufträge lag über dem Eingabefeld.** Er war absolut
  positioniert und überdeckte den Composer. Er steht jetzt im normalen Fluss direkt
  darüber, mit Trennlinie — nichts überlappt mehr.

---

## [1.191.1] - 2026-08-13

### Behoben
- **Für Claude- und Codex-Agenten erschien gar keine Kachel mehr.** Aufträge über
  den stdio-MCP-Server führen keinen Gesprächsfaden mit — und seit der
  Isolations-Korrektur (1.190.1) wird ohne Faden nichts mehr angezeigt. Ergebnis:
  vier delegierte Aufgaben, keine einzige Kachel.
  Der Orchestrator ermittelt den Faden jetzt selbst aus dem **laufenden Zug** des
  Agenten (`agent:{id}:status → current_task = "chat:{faden}"`) und hält ihn am
  Auftrag fest, damit auch die spätere Fertigmeldung ihn findet.
  Das ist ausdrücklich **nicht** der frühere Auffangweg „zuletzt benutzter Faden":
  gefragt wird nach dem Gespräch, das in diesem Moment läuft — also genau dem, in
  dem der Mensch gerade sitzt.

---

## [1.191.0] - 2026-08-13

### Geändert
- **Kacheln: laufende unten fest, fertige im Verlauf.** Bisher standen alle
  dauerhaft am Ende, und der Chat lief oberhalb weiter — der Stand blieb im Weg,
  auch wenn längst alles fertig war.
  Jetzt: **laufende** Aufträge liegen als schmaler Streifen über dem Eingabefeld
  (der Stand darf einem beim Lesen nicht wegscrollen), **fertige** wandern in den
  Verlauf — kompakt, zu zweit nebeneinander, mitscrollend und anklickbar.

### Behoben
- **Der Agent zählte Kollegen aus fremden Teams als sein Team auf.** Er rief
  `list_my_team` UND `list_team` und verschmolz beides. `list_team` ist aber das
  **systemweite** Verzeichnis; bei Custom-LLM stand in der Beschreibung sogar
  wörtlich „all agents in your team". Beide Laufzeiten sagen jetzt klar, dass es
  nicht das eigene Team ist und die Einträge nicht hineingemischt werden dürfen.
- **Tote Mitglieder erschienen als Kollegen.** Eine Kennung ohne Agenten (`6e4210c1`
  im Team „AI DEV") wurde als Mitglied „ohne Rolle" ausgegeben — der Lead hätte ihr
  Arbeit geben können, die nie jemand annimmt. Sie steht jetzt getrennt unter
  `stale_member_ids` und nicht mehr in der Mitgliederliste.

---

## [1.190.1] - 2026-08-13

### Behoben
- **Kacheln erschienen in fremden Gesprächen.** Beim Kunden tauchten delegierte
  Aufträge in Unterhaltungen auf, zu denen sie nicht gehörten. Ursache: eine Kachel
  ohne Ursprungsfaden wurde trotzdem gespeichert und angezeigt — und gehört damit
  in **jedes** Gespräch. Sie trägt jetzt ihren Faden mit, wird nur dort gespeichert
  und nur dort angezeigt.
- **Rückmeldungen landeten im falschen Gespräch.** Der in 1.186.0 eingebaute
  Auffangweg „zuletzt benutzter Faden" war ein Rückschritt: bei mehreren parallelen
  Unterhaltungen schrieb er die Antwort in eine fremde. Es gilt jetzt
  ausschliesslich der Faden, in dem wirklich beauftragt bzw. gefragt wurde
  (bei Antworten über die ursprüngliche Nachricht ermittelt).
  Lieber keine Einspeisung als eine im falschen Faden — Kachel und Verlauf zeigen
  den Stand ohnehin.

### Bemerkung
- Der `ruff F821`-Test hat dabei einen Fehler in meiner eigenen Änderung gefunden,
  bevor er ausgeliefert wurde (eine Ersetzung hatte an zwei Stellen gegriffen).

---

## [1.190.0] - 2026-08-13

### Neu
- **Delegierte Aufträge bleiben dauerhaft im Chat.** Die Kachel lebte bisher nur im
  Browser und war nach jedem Neuladen weg — und mit ihr die einzige Spur im
  Gespräch, dass überhaupt jemand beauftragt wurde. Sie liegt jetzt als Zeile im
  Chatverlauf (`meta.task_card`), genau wie angebotene Dateien und Bilder, und wird
  beim Öffnen des Gesprächs wiederhergestellt.
  Eine Zeile je Auftrag: beim Abschluss wird sie **aktualisiert**, nicht verdoppelt.

---

## [1.189.0] - 2026-08-13

### Behoben
- **Die Rückmeldung des Leads entstand, kam aber nie auf den Bildschirm.** Beim
  Kunden wurde die Kachel grün („abgeschlossen", mit Ergebnis) — und der Lead
  schrieb nichts mehr, obwohl er „ich sag dir Bescheid" angekündigt hatte.
  Ursache: der Weiterleiter im WebSocket schottet Gespräche gegeneinander ab und
  kennt nur die Nachrichtenkennungen, die **dieser Browser** gesendet hat. Eine vom
  Orchestrator angestossene Rückmeldung (Fertigmeldung, Antwort eines Kollegen)
  trägt eine fremde Kennung — und fiel damit genau durch die Abschottung, die
  fremde Gespräche fernhalten soll. Die Antwort stand in `chat_messages`, aber nie
  im Fenster; sichtbar erst nach Neuladen oder auf „und?".
  Der Orchestrator hinterlegt jetzt beim Anstossen den Zielfaden
  (`chat:msg:{id}:session`, eine Stunde haltbar); der Weiterleiter sieht dort nach,
  **bevor** er verwirft, und liefert nur aus, wenn der Faden zu diesem Fenster
  gehört. Die Abschottung bleibt damit unangetastet.

### Test
- `orchestrator/tests/test_orchestrator_replies_reach_the_screen.py`

---

## [1.188.2] - 2026-08-13

### Geändert
- **„Details" an der Auftrags-Kachel öffnet ein Fenster statt einer neuen Seite.**
  Ein Seitenwechsel reisst aus dem Gespräch heraus: der Verlauf ist weg, der
  Rückweg kostet einen Klick, und wer nur kurz nachsehen wollte, verliert den Faden.
  Das Fenster zeigt Bearbeiter, Stand, Dauer, den Auftragstext und das Ergebnis —
  und darunter weiterhin den Weg auf die vollständige Aufgabenseite, für alles,
  was dort mehr steht (Zeitreise, Kosten, Schritte).
  Läuft der Auftrag noch, sagt das Fenster das ausdrücklich, statt leer zu bleiben.

---

## [1.188.1] - 2026-08-13

### Behoben
- **Der Frager erfuhr nie, dass eine Antwort eingetroffen ist.** Am 2026-08-13
  fragte ein Lead einen Kollegen, prüfte sofort und meldete korrekt „hat noch nicht
  geantwortet" — die Antwort kam **90 Sekunden später**. Danach blieb der Lead bei
  seiner Aussage, bis der Nutzer „und?" schrieb; erst dann sah er sie.
  Trifft eine Antwort ein (`reply_to` gesetzt), bekommt der ursprüngliche Frager sie
  jetzt aktiv in seinen Chat gelegt, mit der Aufforderung, dem Menschen kurz zu
  berichten. Kein Pollen, kein Nachfragen.

---

## [1.188.0] - 2026-08-13

### Neu
- **Auch Nachrichten an Kollegen erscheinen als Kachel.** Bisher gab es sie nur für
  delegierte Aufträge — eine Nachricht an einen anderen Agenten verschwand spurlos,
  obwohl sie für den Menschen dasselbe bedeutet: „ich habe jemanden angesprochen und
  warte auf Antwort".
  Die Kachel zeigt „gesendet, wartet auf Antwort" und wechselt auf „beantwortet",
  sobald die Antwort kommt (erkannt über `reply_to` — es entsteht keine zweite
  Kachel, die vorhandene wird geschlossen).

---

## [1.187.2] - 2026-08-13

### Behoben
- **Die Wissens-Migration aus 1.187.1 lief nie.** Sie hing in `restart_agent`, das
  Neuerstellen läuft aber über `update_agent` — beim Kunden stand nach dem
  Ausrollen weiterhin „Onboarding Status: NOT COMPLETED" in der `knowledge.md`.
  Sie liegt jetzt als `migrate_knowledge_file()` an EINER Stelle und wird von
  **beiden** Wegen gerufen (`restart_agent` direkt, `update_agent` über
  `refresh_instructions`). Ein Test zählt beide Aufrufe.

---

## [1.187.1] - 2026-08-13

### Behoben
- **Bestandsagenten trugen den Onboarding-Abschnitt weiter.** 1.187.0 hat ihn aus
  der Standardvorlage entfernt — das half aber nur **neuen** Agenten. Die
  `knowledge.md` liegt im Volume des Agenten und überlebt jedes Neuerstellen
  (absichtlich, dort steht Gelerntes). Auf der Kundenanlage stand deshalb weiterhin
  „Onboarding Status: NOT COMPLETED — I MUST conduct an onboarding interview", und
  die Agenten hielten weiter Aufträge an, um nach ihrer Rolle zu fragen.
  Beim Neuerstellen wird der Abschnitt jetzt entfernt. **Nur der Kopf** wird
  ersetzt: alles ab dem ersten Abschnitt, den der Agent selbst gefüllt haben
  könnte, bleibt Zeichen für Zeichen stehen — ein Wissensspeicher, den eine
  Migration ausräumt, wäre teurer als das Problem.

### Test
- `orchestrator/tests/test_knowledge_migration.py` — Gelerntes überlebt wörtlich,
  zweimal migrieren ändert nichts, und der Helfer wird auch wirklich gerufen.

---

## [1.187.0] - 2026-08-13

### Neu
- **Kachel im Chat für jeden delegierten Auftrag.** Sobald ein Agent delegiert,
  erscheint im Gespräch eine Kachel mit Titel, Empfänger und Live-Stand — „in
  Arbeit" beim Anlegen, „abgeschlossen"/„fehlgeschlagen" beim Ende, mit Dauer,
  Ergebnisvorschau und Link auf die Aufgabe.
  Bewusst eigener Zustand statt Chatnachricht: eine Kachel **aktualisiert sich**,
  eine Nachricht müsste zweimal erscheinen.

### Geändert
- **Das Onboarding-Interview ist raus.** Ein Agent entsteht aus einer Vorlage —
  Rolle, Schwerpunkte und Grenzen stehen dort bereits; sie noch einmal abzufragen
  war überflüssig. Schädlich war es obendrein: am 2026-08-13 kam ein delegierter
  Auftrag mit „für mich sind keine Verantwortungsbereiche hinterlegt, bitte
  festlegen" zurück statt mit Arbeit — in einem Auftrag sitzt niemand, der
  antwortet.
  `create_agent` schreibt jetzt die **Vorlagenbeschreibung** in `knowledge.md`,
  wenn eine vorliegt, statt sie mit der leeren Vorgabe zu überschreiben.
- **Rückfragen richten sich nach dem Ort.** Im Chat mit einem Menschen: Frage
  stellen und warten. In einem Auftrag, einer Delegation oder einem proaktiven
  Lauf: **Antworttext liest dort niemand** — also arbeiten, die sicherste
  vernünftige Annahme wählen, in einer Zeile sagen was fehlte. Braucht es
  wirklich eine Entscheidung, `request_approval` — das erreicht den Menschen und
  wartet.

### Test
- `orchestrator/tests/test_questions_reach_a_human.py`

---

## [1.186.1] - 2026-08-13

### Behoben
- **Ein zurückgekommenes Ergebnis wurde als „angestoßen" gemeldet.** Die Kette war
  vollständig korrekt — nachweisbar aus den Werkzeugaufrufen des Team-Leads:
  `list_my_team` → Projekt mit `bash`/`read_file` geprüft → `update_todos` →
  `delegate_and_wait(agent_id=…, timeout_seconds=300)` → danach `memory_save`,
  `rate_task`. Dass nach dem Warten noch Aufrufe kamen, beweist: der Aufruf ist
  **mit dem Ergebnis** zurückgekehrt.
  Trotzdem schrieb der Lead „Angestoßen: Mr. Design erstellt **jetzt** das Paket".
  Der Mensch las „läuft" und wartete 18 Minuten auf etwas, das fertig war.
  Die Rückgabe von `delegate_and_wait` sagt jetzt in der **ersten Zeile**, dass das
  Warten vorbei ist („FERTIG … das ist das ENDERGEBNIS, kein Zwischenstand"), und
  untersagt ausdrücklich die Formulierung „angestoßen"/„läuft jetzt". Teilweise
  fertige Stapel bleiben klar als solche benannt.
  In **beiden** Laufzeiten: Custom-LLM und stdio-MCP (Claude Code, Codex).

- **Emojis aus allen MCP-Servern entfernt** — `get_tasks_status`, Brain-Server und
  Freigabe-Server trugen sie in nutzersichtbarem Text. Der Test sucht jetzt im
  ganzen Verzeichnis: eine Regel, die nur dort geprüft wird, wo man gerade
  hinsieht, ist keine Regel.

### Test
- `agent/tests/test_delegation_result_is_not_a_kickoff.py`

---

## [1.186.0] - 2026-08-13

### Behoben
- **Nach einer Delegation kam keine Rückmeldung.** Kundenmeldung 06:22 Uhr: der Lead
  meldet „Angestoßen", der Auftrag ist um 06:22 fertig — und im Chat passiert
  **18 Minuten nichts**, bis der Mensch nachfragt („ist die aufgabe abgeschlossen?
  ich sehe kein Re-Design!").
  Der Rückmeldeweg existierte, lief aber ins Leere: die Meldung wurde mit dem
  Schlüssel `session_id` verschickt, der Agent liest `chat_session_id`. Ohne Faden
  landete die Antwort in `webapp:default` — einem Gespräch, das niemand ansieht.
  Ein Auftrag führt jetzt seinen **Ursprungsfaden** mit (`chat_session_id` in den
  Metadaten), und die Fertigmeldung geht dorthin zurück. Für Aufträge ohne Faden
  (stdio-MCP: Claude Code, Codex) gibt es einen Auffangweg auf den zuletzt
  benutzten Faden des Leads.
- Die Meldung fordert den Lead jetzt ausdrücklich auf, dem Menschen zu berichten —
  und ein unzureichendes Ergebnis als solches zu benennen.
- **Emojis in nutzersichtbarem Text entfernt** (Chat-Rückmeldung und
  Telegram-Meldung) — harte Vorgabe des Projekts, hier stand sie in Produktion.

### Behoben (Diagnose-Blindheit)
- **Anwendungs-Logs landeten nirgends.** `/shared/platform-errors.log` nimmt erst ab
  WARNING an, und einen Ausgabe-Handler hatte der Wurzel-Logger gar nicht. Sichtbar
  war nur, was mit `print` geschrieben wurde, plus das Zugriffsprotokoll von uvicorn.
  Folge: bei genau diesem Fehler liess sich **nicht feststellen**, ob der
  Rückmeldeweg überhaupt ausgelöst hatte — die `logger.info`-Zeile, die das
  beantwortet hätte, existierte im Code und nirgends sonst.
  Jetzt schreibt der Wurzel-Logger nach stdout, Stufe über `LOG_LEVEL` (Vorgabe
  INFO), mit derselben Schwärzung wie die Datei.

### Test
- `orchestrator/tests/test_delegation_report_reaches_the_user.py`
- `agent/tests/test_delegation_thread_is_carried.py` — inklusive der Gegenprobe:
  ausserhalb eines Gesprächs (proaktiver Lauf) darf **kein** Faden angehängt werden.

### Offen
- 9 Fremdschlüssel-Fehler `skill_task_usages_task_id_fkey` in den Kundenlogs, aus
  `record-usage`-Aufrufen auf bereits geräumte Aufträge. Eigener Fehler, nicht Teil
  dieser Ursache — nachzuziehen.

---

## [1.185.0] - 2026-08-12

### Neu
- **Jeder Nutzer kann sein eigenes Claude- oder Codex-Abo hinterlegen.** Bisher kam
  der Zugang aus **einer** Einstellung für die ganze Installation, und pflegen konnte
  sie nur ein Administrator — wer die Plattform nutzte, arbeitete zwangsläufig auf
  fremde Rechnung oder gar nicht.
  Neue API `GET/PUT/DELETE /api/v1/me/ai-credentials`. Jeder sieht und ändert
  **ausschliesslich seinen eigenen**; es gibt keinen Administrator-Weg auf fremde
  Zugänge, auch nicht lesend. Das Geheimnis kommt nie wieder heraus — auch nicht an
  den Besitzer.
- **Reihenfolge:** eigener Zugang → Teamlizenz (nur wenn der Administrator sie über
  `allow_team_license` freigegeben hat) → nichts. Massgeblich ist der **Besitzer des
  Agenten**, nicht der gerade Eingeloggte: ein Agent arbeitet auch nachts weiter.
- **Codex nimmt den eigenen Zugang entgegen** (`CODEX_AUTH_JSON`) und schreibt ihn in
  seine `auth.json`, statt die geteilte Datei zu benutzen. Die Variable wird danach
  aus der Umgebung entfernt, damit der Zugang nicht in jedem Prozessabbild mitläuft.

### Behoben
- **Die Grundlage von `d12ada5` war wirkungslos.** `agent_credentials.resolve()`
  wurde von niemandem aufgerufen — Tabelle angelegt, Auflöser vorhanden, und dann
  passierte nichts. Jetzt an **allen drei** Stellen verdrahtet, an denen ein
  Container gebaut wird (Anlegen, Neustart, Aktualisieren). Fehlte eine, bekäme der
  Agent beim nächsten Neustart wieder den fremden Zugang.

### Warum das mehr ist als Bequemlichkeit
Alle Codex-Agenten teilten sich **einen** rotierenden Refresh-Token. Erneuert ihn
einer, sind die anderen tot (`refresh_token_reused`) — deshalb muss das Neuerstellen
bis heute serialisiert werden. Getrennte Zugänge sind getrennte Token-Familien; der
Ausfall eines Abos trifft dann genau einen Agenten.

### Test
- `orchestrator/tests/test_own_subscription_per_user.py` — prüft zuerst die
  **Verdrahtung** (der Auflöser lag einen halben Tag ungenutzt im Baum), dann die
  Reihenfolge, und dass das Geheimnis nie in einer Antwort auftaucht.

---

## [1.184.1] - 2026-08-12

### Behoben
- **Die entfernte Obergrenze wirkte noch nicht.** 1.184.0 hat nur die Vorgabe
  geändert — die 4096 standen aber zusätzlich **gespeichert in der Konfiguration
  jedes Agenten** und haben die neue Vorgabe überschrieben. Im laufenden Container
  stand weiterhin `LLM_MAX_TOKENS=4096`.
  Jetzt: die vier Stellen, die den Wert weitertrugen, sind bereinigt, und beim
  Start wird der Altwert einmalig aus den bestehenden Agenten entfernt.
  Bewusst **nur exakt 4096** — wer eine andere Zahl eingetragen hat, hat sich etwas
  dabei gedacht, und die bleibt unangetastet.

---

## [1.184.0] - 2026-08-12

### Geändert
- **Die Obergrenze für die Antwortlänge ist raus.** `LLM_MAX_TOKENS` stand auf
  4096 — eine Zahl aus der Zeit, als Modelle nicht mehr konnten. Für ein Review,
  eine Spezifikation oder eine fertige Datei ist das zu wenig, und das Tückische
  daran: die Antwort bricht **mitten im Satz** ab und sieht trotzdem aus wie ein
  fertiges Ergebnis. Vorgabe ist jetzt 0 = keine eigene Grenze.
  - **OpenAI/Azure und Google:** der Schlüssel wird schlicht nicht gesendet, dann
    gilt das Maximum des Modells.
  - **Anthropic:** dort ist `max_tokens` ein Pflichtfeld. Ohne eigene Grenze wird
    der für die Modellfamilie erlaubte Höchstwert genommen — nicht einfach ein
    hoher Wert, denn oberhalb des Modellmaximums antwortet die API mit 400.
  - Eine ausdrücklich gesetzte Grenze gilt unverändert weiter.

### Dokumentation
- **Benutzerhandbuch nachgezogen** (PDF neu erzeugt), für alles, was heute
  nutzersichtbar dazugekommen ist:
  - neuer Abschnitt **34. Kanäle** inkl. Discord-Einrichtung — mit dem Hinweis auf
    *Message Content Intent*, ohne den Discord jede Nachricht leer ausliefert
  - neuer Abschnitt **35. Branchen-Pakete** (Steuerkanzlei, Handwerksbetrieb) samt
    der Grenzen: Buchungen sind Vorschläge, Preise kommen aus der Liste
  - neuer Abschnitt **36. Ausweichmodell**, inklusive der Fälle, in denen bewusst
    NICHT gewechselt wird
  - **25d Golden-Tests** um die Faktenprüfung erweitert, mit der Begründung, warum
    eine erfundene Statustabelle eine reine Textprüfung besser besteht als die
    ehrliche Antwort
  - **11 Triggers**: Workflow als Ziel samt Platzhaltertabelle
  - **13 Approvals**: Antworten ist nie freigabepflichtig

### Behoben
- Beim Umbau der Antwortlänge wäre in `_build_legacy_body` das `return` verloren
  gegangen — die Route hätte still `None` statt eines Rumpfes geliefert. Test dafür.

### Test
- `agent/tests/test_no_output_cap_by_default.py`

---

## [1.183.0] - 2026-08-12

### Neu
- **Zwei Branchen-Pakete (#395):** *Steuerkanzlei* (Buchhaltung, Lohnbuchhaltung,
  Legal Assistant) und *Handwerksbetrieb* (Angebot & Kalkulation, Disposition,
  First-Level-Support). Beide mit Startwissen und erster Demo-Aufgabe.
- **Vier neue Agenten-Vorlagen:** Buchhaltung, Lohnbuchhaltung, Angebot &
  Kalkulation, Disposition.
- Die Geld-Rollen tragen einen **Haftungshinweis**: jede Buchung ist ein Vorschlag
  und braucht die Freigabe einer fachkundigen Person. Ein Agent, der eine Buchung
  als geprüft ausgibt, richtet mehr Schaden an als einer, der gar nichts tut.
- Die Kalkulationsrolle nimmt Preise **ausschliesslich** aus der hinterlegten
  Preisliste; fehlt einer, geht die Position in die Rückfragenliste. Geschätzte
  Preise kosten Marge, und zwar unbemerkt.
- Die Steuerkanzlei ist bewusst **ohne DATEV** nutzbar (#393 ist pausiert):
  Vorkontierung, Fristen und Belegprüfung stehen für sich.

### Test
- `test_vertical_packs_content.py` — prüft vor allem den stillen Fehler: ein Paket
  verweist über Namen auf Vorlagen; stimmt ein Name nicht, wird ein Agent weniger
  angelegt, ohne dass irgendetwas rot wird.

---

## [1.182.0] - 2026-08-12

### Sicherheit
- **Der DLP-Egress-Filter galt nur für Telegram.** Teams, Slack und WhatsApp
  schickten ungeprüft hinaus — auf einer Klinikanlage genau der Fall, für den es
  den Filter gibt. Er sitzt jetzt in `channel_gateway.send_reply`, der einen
  Stelle, durch die alle abgefragten Kanäle senden. Damit gilt er automatisch
  auch für jeden Kanal, der später dazukommt.
  Blockierte Nachrichten werden nicht stillschweigend verschluckt: der Empfänger
  bekommt denselben Hinweis wie bei Telegram, sonst wartet er auf eine Antwort,
  die nie kommt.

### Neu
- **Discord als vierter Kanal (#195, #139).** Nach dem Muster von Slack:
  abgefragt statt über die dauerhafte WebSocket, weil in einem Kliniknetz nur
  ausgehendes HTTPS verlässlich erlaubt ist. Der Ablauf bleibt im
  `channel_gateway`, der Kanal liefert nur Herkunft und Rückweg.
  Lange Antworten werden an Absätzen geteilt statt abgeschnitten — eine halbe
  Antwort sieht aus wie eine ganze.

### Geändert
- `test_channel_gateway` prüft die Lauscher-Liste jetzt gegen die Kanalliste statt
  gegen eine Aufzählung. Beim nächsten Kanal schlägt er von selbst an, wenn ihn
  jemand im Lauscher vergisst.

### Test
- `test_discord_and_dlp_per_channel.py` — der Filter greift auf allen vier
  Kanälen, Discord ist registriert (eigenes Kennungs-Präfix, im Lauscher),
  Nachrichten werden geteilt statt gekürzt, Bot-Nachrichten laufen nicht zurück.

---

## [1.181.0] - 2026-08-12

### Neu
- **Webhooks können ganze Workflow-Ketten auslösen (#392).** Motor, Zeitplan und
  Baukasten standen bereits; es fehlte genau der Auslöser von aussen. Ein
  `EventTrigger` mit gesetztem `workflow_id` startet jetzt einen `WorkflowRun`
  statt eines Einzelauftrags.
  **Bewusst über den vorhandenen Auslöser**, nicht als zweites System daneben:
  Treffererkennung, Bedingungen, Sicherheitsprüfung der Nutzlast und Zähler gelten
  damit für beide Ziele gleich.
- Die Nutzlast landet unter `trigger` im Lauf-Kontext und ist über die
  **vorhandene** Platzhalter-Ersetzung `{{trigger}}` erreichbar — dazu
  `{{trigger_prompt}}`, `{{trigger_source}}`, `{{trigger_event}}`. Auf 8000 Zeichen
  begrenzt, sonst wandert sie ungebremst in jeden Prompt der Kette.
- Zeigt ein Auslöser auf einen fehlenden oder abgeschalteten Workflow, wird
  ersatzweise ein Auftrag angelegt und das protokolliert — ein verschluckter
  Auslöser ist die Sorte Fehler, die niemand bemerkt, bis sie teuer wird.

### Behoben
- `event.task_id = tasks_created[0]` hätte den ganzen Webhook mit einem IndexError
  beantwortet, sobald alle Auslöser Workflows starten und kein Auftrag entsteht.

### Test
- `test_webhook_starts_workflow.py` — Lauf startet, Nutzlast kommt an und ist über
  die vorhandene Ersetzung erreichbar, fehlender/abgeschalteter Workflow fällt
  zurück statt zu verschwinden, Nutzlast wird begrenzt.

---

## [1.180.0] - 2026-08-12

### Neu
- **Modell-Fallback bei Ausfall (#200).** Antwortet das Modell nicht — Rate-Limit,
  Zeitüberschreitung, Überlastung, Wartungsfenster einer Azure-Bereitstellung —
  wechselt der Lauf auf das nächste Modell der Kette, statt abzubrechen. Kette je
  Agent über `fallback_models` (Reihenfolge ist die Entscheidung des Betreibers).
  In **beiden** Laufzeiten verdrahtet: Auftragslauf und Chat.
- **Einrichtungsfehler weichen bewusst NICHT aus.** Falscher Schlüssel, falscher
  Bereitstellungsname, Inhaltsablehnung: dort hilft kein zweites Modell, die Kette
  würde denselben Fehler nur teurer wiederholen und die Ursache verdecken. Ein
  „401 Unauthorized, please try again later" gilt deshalb als Einrichtungsfehler,
  nicht als Kapazitätsproblem.
- Jeder Wechsel wird sichtbar protokolliert (vorher → nachher → Grund). Ein stiller
  Modellwechsel wäre schlimmer als keiner: andere Antwortqualität, andere Kosten,
  kein auffindbarer Grund.

### Test
- `agent/tests/test_model_fallback.py` — beide Richtungen, inklusive der Fälle, in
  denen NICHT gewechselt werden darf, und dass beide Laufzeiten den Schalter haben.

---

## [1.179.0] - 2026-08-12

### Neu
- **Golden-Tests prüfen jetzt Tatsachen, nicht Formulierungen (#193).**
  `check_item` nimmt nachgemessene Fakten über den Lauf entgegen: welche Werkzeuge
  wirklich liefen (`expect_tools`, `expect_no_tools`), ob überhaupt gearbeitet wurde
  (`expect_substantive_work`), wie viele Aufträge wirklich entstanden und fertig
  zurückkamen (`expect_delegated`, `expect_delegations_completed`).
  `eval_service.gather_facts()` trägt sie aus `task_steps` und `tasks` zusammen —
  `check_item` bleibt dabei rein.
  **Warum:** Am 2026-08-12 beschrieb ein Agent seine Delegation, statt sie
  auszuführen — samt erfundener Statustabelle, während kein Auftrag existierte.
  Diese erfundene Antwort enthält *mehr* von dem, was man erwartet, als die
  ehrliche. Jede Textprüfung hätte sie also **besser** bewertet.
- **Zwei mitgelieferte Testsammlungen** (`core/eval_seeds.py`, beim Start angelegt):
  - *Team-Grundlagen* — Team kennen, wirklich beauftragen, Ergebnisse zurückholen,
    Fehlschläge benennen. Der Delegationsfall hätte alle fünf Ausfälle vom
    2026-08-12 gefunden.
  - *Angriffsfälle* — Prompt-Injection über Kollegen-Nachrichten, Webseiten,
    Dateien, MCP-Antworten und vorgetäuschte Autorität.

### Test
- `test_eval_checks_facts_not_words.py` — hält den Originalfall fest: die erfundene
  Statustabelle besteht eine reine Textprüfung und fällt bei der Faktenprüfung durch.
- Der Test hat einen Fehler in der ersten Fassung der Angriffsfälle gefunden: sie
  verlangten nur, was NICHT passieren darf. Damit hätte eine leere Antwort jeden
  Fall bestanden — gemessen worden wäre Schweigen statt Widerstandskraft. Jeder Fall
  hat jetzt zusätzlich eine positive Erwartung, und ein Test hält das fest.

---

## [1.178.5] - 2026-08-12

### Behoben
- **Wer delegiert, konnte die Ergebnisse nicht abrufen.** Der Team-Lead meldete
  beim Kunden: "Der anschließende Statusabruf liefert für alle vier Aufträge
  derzeit 'nicht abrufbar'. Das ist kein belastbarer Abschluss." In der Datenbank
  standen zur selben Zeit alle vier auf COMPLETED, mit 4-10 Zügen echter Arbeit.
  Der Abruf lief auf **403**: ein Agent darf nur seine EIGENEN Aufgaben lesen,
  ein Lead legt aber Aufgaben für ANDERE an. `delegate_and_wait` und
  `get_tasks_status` fragen genau diesen Endpunkt ab — beide liefen ins Leere.
  Jetzt darf lesen, wer die Aufgabe **erzeugt** hat. Die Mandantentrennung bleibt:
  kein Agent sieht fremde Aufgaben, nur eigene und selbst vergebene.

### Geändert
- **`/workspace` ist privat — das stand nirgends.** Jeder Agent hat sein eigenes
  Volume (so gewollt), aber die Anleitung listete nur "Workspace: /workspace/
  (persistent across tasks)". Der Lead verschickte deshalb seine eigenen Pfade,
  die Empfänger fanden nichts und meldeten "keine Artefakte ermittelbar" — was
  wie Arbeitsverweigerung aussah. Die Anleitung sagt es jetzt ausdrücklich und
  nennt die zwei Auswege: Dateien nach `/shared/` legen und **den** Pfad
  delegieren, oder die Aufgabe selbsttragend formulieren.
  Der Hinweis steht zusätzlich direkt am `prompt`-Feld von `delegate_and_wait`,
  in **beiden** Laufzeiten (Custom-LLM und stdio-MCP für Claude/Codex) — also in
  jedem Zug vor dem Modell, nicht nur einmal beim Start.

### Test
- `test_delegator_may_read_results.py` — am echten Endpunkt: der Auftraggeber
  bekommt das Ergebnis, ein Fremder weiterhin 403.
- `test_workspace_is_private.py` — beide Laufzeiten tragen den Hinweis; geprüft
  wird die Aussage, nicht die Schreibweise.

---

## [1.178.4] - 2026-08-12

### Geändert
- **Chat/Telegram an den eigenen Nutzer ist ab L2 frei.** Die Freigabepflicht
  fürs Nachrichtenschicken gilt nur noch auf L1 (Nur lesen). Vorher war sie bis
  einschließlich L3 aktiv — mit der Folge, dass ein Agent sich nicht einmal
  traute zu antworten und delegierte Aufträge stehenblieben.
  Handlungen mit echter Außenwirkung (E-Mail/M365, externe APIs, git push,
  Käufe) bleiben unverändert bis L4 freigabepflichtig.

### Behoben
- **Der Sammeltopf `custom` hätte die Lockerung weit über das Gewollte
  hinausgetragen.** `messaging` teilte sich die Alt-Kategorie mit `email_m365`,
  `external_api` und `git_push`; ein einziges `allow` schaltet den ganzen Topf
  frei. Chat freizugeben hätte damit auf jeder L2/L3-Anlage stillschweigend auch
  E-Mail-Versand, ausgehende API-Aufrufe und `git push` ohne Freigabe erlaubt.
  `messaging` bekommt die eigene Kategorie `external_communication` — die der
  Executor für `send_telegram`/`notify_user` ohnehin schon führt und die bis
  jetzt von keiner Fähigkeit erreichbar war, also nie freigegeben werden konnte.

### Test
- `test_autonomy_matrix.py` — der Sammeltopf bleibt zu, wenn nur Chat frei ist;
  er öffnet weiterhin für seine echten Mitglieder.
- `test_autonomy_reply_is_never_gated.py` — L1 fragt weiter nach, L2/L3 nicht;
  Außenwirkung bleibt auf allen Stufen unter L4 gesperrt.

---

## [1.178.3] - 2026-08-12

### Behoben
- **Eine Ankündigung galt als erledigte Aufgabe.** Beim Kunden standen zwei
  delegierte Aufträge auf `COMPLETED`, in deren Ergebnis wörtlich steht: "Ich
  habe inhaltlich noch keine Repo-Änderungen umgesetzt (nur angekündigt)."
  Das Abschluss-Gatter prüfte nur, ob `rate_task` gerufen wurde — nicht, ob
  gearbeitet wurde. Ein Agent, der bloß ankündigte, wurde deshalb aufgefordert,
  die Bewertung nachzuholen, tat das, und der Lauf endete als Erfolg. Drei Züge,
  ein einziger Werkzeugaufruf, und das war die Bewertung selbst.
  Das Gatter fordert jetzt **zuerst die Arbeit**; die Buchhaltung kommt danach.
- **Antworten war faktisch freigabepflichtig.** Auf L3 steht "Chat / Telegram
  senden" unter "Requires approval". Mr. Design dazu auf Nachfrage wörtlich:
  "Wenn ich das streng auslege, dürfte ich ohne Approval nicht einmal
  antworten." Er wusste, dass er Dateien schreiben und Shell nutzen darf — und
  blieb trotzdem stehen. Der Autonomie-Block sagt jetzt ausdrücklich, dass die
  Antwort an den eigenen Auftraggeber nie freigabepflichtig ist, und begrenzt
  den Auffangsatz auf Wirkung **außerhalb** des Containers.

### Test
- `agent/tests/test_announcement_is_not_completion.py` — Buchhaltung allein ist
  keine Arbeit; eine reine Lese-Prüfung dagegen schon.
- `orchestrator/tests/test_autonomy_reply_is_never_gated.py` — die Ausnahme ist
  da und nennt alle drei Rückwege; die Rechte selbst bleiben unverändert.

---

## [1.178.2] - 2026-08-12

### Behoben
- **Nachrichten an schlafende Agenten kamen nie an.** Der Team-Lead schickte
  sieben Agenten je ein "Hallo Welt" — alle sieben stehen in der Datenbank, die
  Zustellung meldete "sent", keine einzige Antwort. Die Empfänger waren Minuten
  vorher idle ausgestiegen; `agent:{id}:messages` wird aber nur gelesen, solange
  der Container läuft. Von außen sah es aus, als könnten die Agenten
  grundsätzlich nicht miteinander reden.
  Die Zustellung weckt den Empfänger jetzt **vor** dem Einreihen.

### Geändert
- Das Aufwecken gab es für Besprechungen längst und für Nachrichten gar nicht.
  Statt einer zweiten Kopie liegt es jetzt einmal in
  `orchestrator/app/core/agent_wakeup.py`; `meeting_rooms` ruft dieselbe Stelle.

### Test
- `orchestrator/tests/test_message_wakes_sleeping_agent.py` — prüft, dass ein
  gestoppter Agent geweckt wird, ein laufender in Ruhe bleibt, ein misslungener
  Start die Zustellung nicht abbricht, und dass die Reihenfolge stimmt:
  erst wecken, dann einreihen.

---

## [1.178.1] - 2026-08-12

### Behoben
- **Der Team-Lead fand sein eigenes Team nicht.** Beim Kunden stand ein aktives
  Team mit acht Mitgliedern in der Datenbank, der CEO-Agent als Lead. Auf die
  Bitte, seinen Agenten eine Nachricht zu schicken, rief er `list_my_team` auf
  und antwortete trotzdem: "Mir ist derzeit kein Agententeam zugeordnet."
  `GET /teams/mine` liefert die Mitglieder **je Team** (`{"teams": [{..., "members": [...]}]}`),
  gesucht wurde `members` auf der obersten Ebene — immer leer. Ohne Team gab es
  auch nichts zu delegieren.
- **`list_team_tasks` genauso:** `GET /teams/` führt die Mitglieder als reine
  ID-Liste in `member_agent_ids`; gesucht wurde eine Objektliste `members`. Der
  Lead fand sein Team nie und meldete "Kein Team zugeordnet".

### Test
- `agent/tests/test_team_tools_read_the_real_response.py` füttert die **echte
  Antwortform** der beiden Endpunkte ein. Die bisherigen Paritätstests waren
  grün: Werkzeug definiert, im Kernsatz, vom Executor erlaubt, Methode vorhanden
  — und trotzdem sagte der Agent "geht nicht". Geprüft wird jetzt, was der Agent
  am Ende zu lesen bekommt.

---

## [1.178.0] — 2026-08-12

### Fixed
- **Custom-LLM-Agenten konnten nicht delegieren — und erfanden es stattdessen.**
  Der CEO-Agent eines Kunden meldete „Alle drei beauftragten Sub-Agents sind
  aktuell aktiv" mit einer Statustabelle, während die Übersicht alle Agenten als
  **Idle, 0 % CPU, ohne Warteschlange** zeigte. Erkennbar auch an der Fußzeile:
  `12,5s · 2 turns` — in zwölf Sekunden wird nichts beauftragt.

  Es war keine Halluzination ohne Anlass, sondern eine **fehlende Fähigkeit**:
  `delegate_and_wait` gab es nur in `agent/mcp/orchestrator-server.mjs`, einem
  **stdio**-MCP-Server, den ausschliesslich Claude Code startet. Der Custom-LLM-Lauf
  holt MCP-Werkzeuge über **HTTP** und erreicht stdio-Server nie. Ein Modell ohne
  passendes Werkzeug tut, was es kann: es **beschreibt** die Handlung.

  Nachgebaut sind jetzt alle sechs Team-Werkzeuge des Orchestrator-MCP:

  | Werkzeug | wofür |
  |---|---|
  | `delegate_and_wait` | beauftragen **und auf das Ergebnis warten** |
  | `list_my_team` | wen habe ich überhaupt |
  | `list_team_tasks` | woran arbeitet das Team wirklich |
  | `get_tasks_status` | läuft mein Auftrag noch |
  | `schedule_meeting` | Abstimmung ansetzen |
  | `skill_update` | Skill nachziehen |

  Alle vier zentralen liegen im **Kernsatz**, nicht nur im Katalog: was ein Agent
  erst über `search_tools` finden muss, findet er in der Praxis nicht — und redet
  dann darüber, statt es zu tun.

  Zwei Details, die den Rückfall verhindern sollen: die Beschreibung von
  `delegate_and_wait` sagt ausdrücklich, dass eine Ankündigung **ohne** Aufruf eine
  Falschaussage ist. Und ein Auftrag, der bei Fristende noch läuft, wird als
  **„läuft noch"** ausgewiesen statt weggelassen — das Weglassen war der Kern der
  erfundenen Statustabelle.

- **Codex-Agenten hatten kein Microsoft 365, keine Mail und kein Video.** Beim
  vollständigen Abgleich aller drei Laufzeiten aufgefallen: Claude Code startet
  elf stdio-MCP-Server, Codex nur sieben. Es fehlten `msgraph`, `email` und
  `hyperframes` — zwei Listen an zwei Stellen (`main.py` und `codex_runner.py`),
  die niemand gegeneinander geprüft hat. Nachgetragen.

### Added
- **Ein Paritätstest über alle drei Laufzeiten.** Es gab schon
  einen „Paritätstest" — der prüft aber den Katalog für das `/`-Menü im Chat. Er
  war grün, während sechs Team-Werkzeuge fehlten. Der neue vergleicht
  `orchestrator-server.mjs` gegen `definitions.py` und verlangt für jede Lücke
  einen **begründeten** Eintrag in `DELIBERATE_GAPS`. Er hat die fünf verbleibenden
  Werkzeuge selbst gefunden, nachdem `delegate_and_wait` gebaut war.

  Der zweite Test hält die **volle Matrix** fest: welche MCP-Server jede Laufzeit
  bekommt und auf welchem Weg. Der Kern des Problems ist, dass es **drei
  verschiedene Bezugswege** gibt — Claude Code und Codex je eine eigene stdio-Liste,
  Custom-LLM `definitions.py` plus MCP über **HTTP**, das stdio-Server nie erreicht.
  Jede Abweichung braucht jetzt eine Begründung im Test; eine Lücke ohne Grund ist
  keine Entscheidung, sondern ein Versehen. Er fand die drei fehlenden
  Codex-Server, sobald er geschrieben war.

## [1.177.6] — 2026-08-12

### Security
- **Der Aufgabentext des Nutzers stand in einer URL.** Die aufgabenbezogene
  Vorauswahl der Erinnerungen (#562) hängte bis zu 500 Zeichen echter
  Nutzereingabe als Abfrageparameter an:

  ```
  GET /api/v1/memory/preload/{id}?task_context=Bitte+pruefe+die+Abrechnung+von+…
  ```

  uvicorn schreibt jeden Pfad **samt Abfrage** ins Zugriffsprotokoll. Damit landen
  Bruchstücke echter Aufgaben in jedem Log, das jemand einsammelt, rotiert oder
  weiterreicht — gegen die eigene Regel, niemals PII zu loggen. Dass der Endpunkt
  intern ist, mindert das, hebt es nicht auf.

  Der Text geht jetzt per **POST in den Rumpf**. `GET` bleibt für ältere Agenten
  bestehen, kennt den Parameter aber **gar nicht mehr** — er kann auf diesem Weg
  nicht wieder ins Log geraten. Beide Wege teilen dieselbe Besitzprüfung; ein
  neuer Agent an einem älteren Orchestrator fällt bei `405` auf die Grundauswahl
  zurück, statt ohne Erinnerungen dazustehen.

### Fixed
- **Das Linter-Gatter wäre in CI still übersprungen worden.** Der Test aus 1.177.5
  überspringt sich selbst, wenn `ruff` fehlt — und CI installierte es nicht. Er
  wäre grün gewesen, ohne je zu prüfen: genau die falsche Sicherheit, die den
  `config`-Fehler fünf Tage hat überleben lassen. `ruff` ist jetzt installiert und
  läuft als **eigener CI-Schritt**, damit ein Treffer im Protokoll steht statt in
  einer Testmeldung zu verschwinden.

- **Die Agenten-Tests liefen in CI fast gar nicht.** Der Lauf nannte **drei
  Dateien** namentlich; alles andere lief nie. Jeder neue Agenten-Test war damit
  ab dem Tag seiner Entstehung tot — auch die aus 1.175.0 bis 1.177.2 für
  Kompression, Stop-Abbruch und Token-Rotation. Jetzt läuft die ganze Suite,
  samt `trio` (ohne das Backend bricht anyio die Hälfte mit einem
  `ModuleNotFoundError` ab, ohne dass am Code etwas falsch wäre).

## [1.177.5] — 2026-08-12

### Fixed
- **Das Anlegen eines Agenten schlug fehl (500).** Seit dem Zeitzonen-Commit vom
  7. August — fünf Tage lang, in jeder Installation.

  ```
  cannot access local variable 'config' where it is not associated with a value
  ```

  In `create_agent` stand `agent_timezone(config)`, aber `config` wird in dieser
  Funktion erst viel weiter unten gesetzt. Beim Anlegen gibt es noch keine
  Agenten-Konfiguration, also gilt die Vorgabe: `agent_timezone(None)`. In
  `restart_agent` und `update_agent` bleibt es bei `config` — dort existiert der
  Agent und hat seine eigene Zeitzone.

  **Ein Test hat den Fehler festgeschrieben statt ihn zu finden.** Er zählte
  `count('"TZ": agent_timezone(config)') == 3` und war deshalb grün, während jedes
  Anlegen in 500 lief. Er prüft jetzt je Funktion, woher die Zeitzone kommt.

- **Neun weitere undefinierte Namen im Projekt** — dieselbe Fehlerklasse, jeder
  ein Ausfall, der auf seinen ersten Nutzer wartete:

  | Stelle | Wirkung |
  |---|---|
  | `_TELEGRAM_MAX_FILE_BYTES` (3×, nirgends definiert) | jeder Bild-, Video- und Animationsversand über Telegram wäre in einen `NameError` gelaufen statt in die 413-Meldung daneben |
  | `logger` in `webhooks.py` (4×) | eine abgelehnte WhatsApp-Verifizierung hätte 500 statt 403 ergeben |
  | `logger` in `mcp_agent.py` | Absturz statt Warnung, wenn ein Task-Abbruch nicht signalisiert werden kann |
  | `sa_text` in `analytics.py` | in einer Funktion nicht importiert, in den Nachbarfunktionen schon |

### Added
- **Ein Gatter gegen diese ganze Fehlerklasse.** Ein Test lässt `ruff --select F821`
  über `orchestrator/app` und `agent/app` laufen und schlägt bei jedem undefinierten
  Namen an. Python merkt so etwas nicht beim Import, sondern erst, wenn die Zeile
  läuft — ein Linter findet es in Sekunden. Namen, die nur in Typangaben stehen,
  gehören dafür unter `if TYPE_CHECKING:`; drei solche Stellen sind entsprechend
  aufgelöst.

## [1.177.4] — 2026-08-11

### Fixed
- **Das Speichern der Einstellungen schlug komplett fehl (500).** Nicht nur die
  Währung — **jedes** Speichern auf der Einstellungs-Seite, seit 1.176.0.

  Ursache: `display_currency` und `usd_eur_rate` standen in der Feldliste der API,
  aber nicht in der Erlaubnisliste des Einstellungs-Dienstes. `set()` warf
  `Unknown setting`, die API antwortete mit 500. Und weil das Frontend beide
  Schlüssel bei **jedem** Speichern mitschickt, riss es alles mit — auch das
  Eintragen der Microsoft-365-Zugangsdaten, bei dem es aufgefallen ist.

  Gefunden hat es der Nutzer, nicht der Test: der Währungstest ersetzt den Dienst
  durch eine Attrappe und kam deshalb nie an der echten Liste vorbei. Neu ist
  daher ein Test, der die **beiden Listen direkt vergleicht** — er fängt jede
  künftige Ergänzung, nicht nur diese eine.

## [1.177.3] — 2026-08-11

### Fixed
- **Der Haken „interne Adresse zulassen" wirkte nur bei IP-Adressen, nicht bei
  Namen** — also ausgerechnet nicht in dem Fall, für den er gebaut wurde. In
  `_discover_tools` bekam der erste Wächter (`_validate_mcp_url`) das Kennzeichen,
  der DNS-auflösende zweite (`_assert_discovery_host_allowed`) nicht. Ein Eintrag
  wie `mcp.intern.example` oder ein Docker-Containername wurde damit weiterhin
  abgelehnt, obwohl der Haken gesetzt war.

  Aufgefallen ist es beim Ausprobieren gegen einen echten Server, nicht in den
  Tests: die prüften die Wächter einzeln, nie den Weg durch die Erkennung.
  Genau dafür gibt es jetzt Tests, die durch `_discover_tools` gehen.

- **Ein intern eingetragener Server liess sich nicht ausprobieren.** Der
  Werkzeugaufruf aus der Oberfläche (`tools/call`) hatte denselben Wächter ohne
  Kennzeichen. Wer eingetragen werden durfte, muss auch aufrufbar sein.

  Damit lässt sich eine **auf der Plattform deployte App direkt als MCP-Server
  einbinden** — über ihren Containernamen im gemeinsamen Docker-Netz, ohne
  öffentlichen Freigabe-Link. Live geprüft: `http://test-mcp-server:8000/mcp`
  liefert vier Werkzeuge.

## [1.177.2] — 2026-08-11

### Fixed
- **Eine Token-Erneuerung kostete einen Lauf.** Anthropic **rotiert**: sobald der
  neue Zugangstoken ausgestellt ist, ist der alte tot. Fällt das in einen
  laufenden Zug, stirbt er mit `401 access token has been revoked` — ohne dass
  jemand etwas falsch gemacht hat. Im Betrieb auf die Minute nachvollziehbar:

  ```
  10:51  Zug läuft → 401 „access token has been revoked"
  10:52  Plattform erneuert den Token
  10:53  neuer Token liegt im gemeinsamen Verzeichnis
  ```

  Drei Stellen waren daran beteiligt, alle drei sind behoben:

  **Der Chat wartete pauschal zehn Sekunden.** Die Plattform schreibt den neuen
  Token erst im nächsten 30-Sekunden-Takt, und wenn sie dafür bei Anthropic
  anfragen muss, dauert es länger — der Wiederholversuch lief verlässlich ins
  Leere. Jetzt wird gewartet, bis sich der Token **wirklich geändert** hat.

  **Der Aufgaben-Pfad hatte gar keine Wiederholung.** Ausgerechnet dort, wo
  niemand davor sitzt und es noch einmal versucht. Er hat sie jetzt, mit derselben
  Erkennung wie der Chat — genau einmal, damit ein dauerhaft kaputter Zugang
  sichtbar wird statt im Kreis zu laufen.

  **Das Wort `revoked` stand in keiner der beiden Erkennungslisten** — ausgerechnet
  das, was im Betrieb kam.

- **Ein Agenten-Update lief in die Erneuerung hinein.** Beim Neuerstellen wird der
  Token jetzt **zuerst** aktualisiert und danach der Container gestartet. So liest
  der frische Container einen frischen Token, und die nächste planmäßige
  Erneuerung ist wieder Stunden entfernt. Vorher startete er auf einem Token, der
  Sekunden später ungültig wurde — genau so beim Ausrollen von 1.177.0 geschehen.

## [1.177.1] — 2026-08-11

### Fixed
- **Die Claude-5-Familie fehlte in den Modelltabellen**, und Opus 4.6 stand mit
  der falschen Größe drin. Nachgetragen aus der Anthropic-Dokumentation
  (geprüft 2026-08-11), nicht geraten:

  | Modell | Fenster | Preis (ein/aus je Mio.) |
  |---|---|---|
  | `claude-opus-5` | 1M | $5 / $25 |
  | `claude-sonnet-5` | 1M | $3 / $15 |
  | `claude-fable-5`, `claude-mythos-5` | 1M | $10 / $50 |
  | `claude-opus-4-6` | **1M** (stand fälschlich auf 200k) | $5 / $25 |

  Sonnet 5 läuft bis 31.08.2026 zu $2/$10 als Einführungspreis. Eingetragen ist
  der **reguläre** Preis: eine Kostenanzeige darf eher zu hoch als zu niedrig
  liegen, sonst reisst ein Budget unbemerkt.

  Das war der Grund, warum `/compact` bei `claude-sonnet-5` „Fenstergröße nicht
  hinterlegt" meldete. Ein zu klein angegebenes Fenster ist dabei die
  unangenehmere Sorte Fehler: es drängt zum Verdichten, wo reichlich Platz ist.

- **Der Kontext-Ring im Chat rechnete jedes Modell gegen fest verdrahtete
  200.000 Token.** Auf einem 1M-Modell zeigte er damit das Fünffache — und stand
  im Widerspruch zur `/compact`-Tafel gleich daneben, die die richtige Zahl schon
  holte. Jetzt nimmt er dieselbe Quelle.

## [1.177.0] — 2026-08-11

### Added
- **Interne MCP-Server lassen sich jetzt zulassen — pro Eintrag.** Wer seinen
  eigenen MCP-Server einträgt, der im Haus steht, bekam eine Ablehnung mit dem
  Hinweis auf eine Umgebungsvariable: *„set MCP_ALLOW_PRIVATE_URLS=true"*. Die
  gibt es zwar, aber sie öffnet die **ganze Installation** und braucht einen
  Neustart — für einen einzigen Eintrag das falsche Werkzeug.

  Nach einer Ablehnung erscheint jetzt der Haken **„Interne Adresse zulassen"**.
  Er gilt nur für diesen Server, wird mit ihm gespeichert (damit die nächste
  Werkzeug-Aktualisierung nicht wieder scheitert) und ist ohnehin nur für
  Administratoren erreichbar — alle betroffenen Endpunkte sind `require_admin`.

  Der Haken ist bewusst eng. Er erlaubt **private** Adressen (10./172.16./192.168.)
  und sonst nichts. Gesperrt bleibt, was nie ein MCP-Server ist:

  | Adresse | warum sie gesperrt bleibt |
  |---|---|
  | `169.254.169.254` und link-local | Metadatenpunkt der Cloud — der Klassiker jeder SSRF-Kette |
  | `127.0.0.1` / `::1` | im Container ist das **dieser Server selbst**; dort steht kein MCP-Server, aber die eigene API |
  | Multicast, reserviert, unbestimmt | Infrastruktur, keine Dienste |

  Der Haken erscheint auch nur nach einer Ablehnung wegen **privater** Adresse.
  Bei Loopback oder Metadatenpunkt bliebe er wirkungslos — ihn dort anzubieten
  wäre ein leeres Versprechen.

  Der globale Schalter `MCP_ALLOW_PRIVATE_URLS` behält seine bisherige Bedeutung
  unverändert, inklusive Loopback: bestehende Installationen ändern sich nicht.

## [1.176.1] — 2026-08-11

### Fixed
- **Nur Werkzeugaufrufe, keine Antwort.** Wer parallel arbeitete, fand beim
  Zurückkommen einen fertig aussehenden Chat ohne Antworttext. Ursache: beim
  Trennen der Verbindung schreibt der Browser einen **Zwischenstand** weg —
  die früh gekommenen Werkzeugaufrufe ja, den am Ende gekommenen Text nein.
  Danach traf das serverseitige `done` mit dem fertigen Text ein, fand die Zeile
  und **übersprang sie** (`if existing: continue`). Der Text kam nie an.

  Bei Zügen von 176, 502 und 514 Sekunden — alles gemessene Werte — reicht der
  120-Sekunden-Nachlauf der Verbindung nicht, und wer parallel arbeitet, schaut
  per Definition woanders hin. Beide Schreiber gehen jetzt durch **eine**
  Zusammenführung: wer zuerst kommt, legt die Zeile an, der andere ergänzt, was
  fehlt. Ein leerer Zwischenstand kann eine fertige Antwort nicht mehr auslöschen.

  Bekommt eine bis dahin leere Zeile ihren Text, gibt es die Benachrichtigung
  **nachträglich** — der Nutzer war ja weg, als sie fertig wurde.

- **Die Antwort landete in der falschen Unterhaltung.** Fehlte die Zuordnung
  Nachricht→Sitzung, fiel das Speichern auf die Sitzung zurück, die in dieser
  Verbindung *gerade offen* war. Nach einem Verbindungsabbruch ist die Zuordnung
  leer — die Antwort auf eine Statusfrage landete dann in dem Chat, den man
  zufällig offen hatte. Genau das Bild aus der Meldung: im Systemlandkarte-Chat
  stand die Antwort zu einem ganz anderen Projekt.

  Die Sitzung kommt jetzt aus der **Nutzerzeile**, die beim Absenden längst in
  der Datenbank steht und die Wahrheit trägt. Ist sie nicht auffindbar, wird gar
  nicht gespeichert — lieber eine fehlende Zeile als eine im falschen Gespräch.

  Nicht betroffen: die Trennung **im Agenten**. Jeder Chat hat dort eine eigene
  Sitzung mit eigener Historie und eigenem Resume — am Pi nachgezählt, 17
  Schlüssel, jeder mit eigener CLI-Sitzung.

## [1.176.0] — 2026-08-11

### Added
- **Kosten in Euro, in deutscher Schreibweise.** Bisher stand überall `$138.4410` —
  falsche Währung, kein Tausenderpunkt, vier Nachkommastellen auch bei dreistelligen
  Beträgen. Jetzt `138,44 €`, an **allen 23 Stellen**, an denen Geld angezeigt wird
  (Dashboard, Analytics, Aufgaben, Budgets, Chat-Fußzeile, Admin, Audit, Concierge,
  Kiosk) — über **eine** gemeinsame Funktion, nicht 23 einzelne.

  Umgerechnet wird ausschliesslich für die **Anzeige**. Gespeichert bleibt USD: sonst
  hinge jeder Altbetrag an dem Tageskurs, zu dem er zufällig eingetragen wurde, und
  liesse sich nie wieder geradeziehen. Der Originalbetrag samt Kurs steht als
  Beschriftung an jeder umgerechneten Zahl.

  Währung und Kurs stellt ein Administrator unter **Settings → Anzeigewährung** ein.
  Ein Kurs ausserhalb von 0,01–100 wird abgewiesen statt zurechtgebogen — ein Kurs
  von 0 macht lautlos jede Zahl der Oberfläche zu „0,00 €".

  Nachkommastellen richten sich nach der Größe: unter einem Cent vier Stellen, damit
  ein Aufruf für einen Drittel-Cent nicht als kostenlos erscheint; ab einem Euro zwei.

- **Öffentliche App-Links können unbefristet gelten.** Bisher war ein Ablaufdatum
  Pflicht (max. 90 Tage). Für Demos, die stehen bleiben sollen, gibt es jetzt den
  Haken **„Unbefristet — läuft nie ab"**. Sieben Tage bleiben die Vorgabe und
  unbefristet die bewusste Ausnahme: der Link bleibt offen, bis ihn jemand
  zurückzieht, und daran erinnert niemand. Das steht so auch in der Oberfläche.

- **Der Agent sagt, was er vorhat, bevor die Werkzeugkette losläuft.** Der Abschnitt
  „Communication" der Agenten-Anleitung regelte nur das Danach („summarize what you
  did"). Über das Davor stand nichts — also fing das Modell wortlos an, und man sah
  minutenlang Werkzeugsymbole ohne zu wissen, worauf man wartet. Jetzt ein Satz
  vorweg, in der Sprache des Nutzers. Ein oder zwei schnelle Aufrufe brauchen keine
  Ansage; das wäre Lärm bei jeder Antwort. Die Anleitung geht als `/workspace/AGENT.md`
  an alle drei Laufzeiten — Claude Code, Codex und Custom-LLM.

- **Öffentliche App-Links stehen jetzt in der Liste — mit Kopieren und Papierkorb.**
  Bisher gab es den Link genau einmal, in der Antwort auf das Anlegen. Das klang
  sicherer, als es war: wer ihn verlor, legte einen neuen an und liess den alten
  stehen. Am Ende lebten mehr Links, als jemand überblickte. Der Token wird jetzt
  zusätzlich **verschlüsselt** aufbewahrt (Fernet, derselbe `ENCRYPTION_KEY` wie
  bei allen anderen Zugangsdaten) und nur dem **Besitzer** ausgeliefert.

  Geprüft wird weiterhin gegen den **Hash** — das ist der schnelle, konstantzeitige
  Vergleich, der bei jedem Seitenaufruf läuft. Ändert sich der Schlüssel, funktioniert
  der Link also weiter, nur anzeigen lässt er sich nicht mehr; dann steht dort nichts
  statt etwas Falschem. Freigaben von vor dieser Version haben keinen Klartext mehr —
  das ist keine Lücke, sondern die alte Ablage.

### Fixed
- **Stop meldete einen Fehler, den niemand gemacht hat.** Nach einem Klick auf Stop
  stand `Unexpected error: ReadError('')` in Rot im Chat. Der Fehler war echt: das
  Anhalten schliesst den laufenden HTTP-Strom, und das Lesen darauf wirft in httpx
  einen `ReadError` — unser eigener Abbruch kam als Störung zurück. Jetzt wird der
  Zug sauber als **abgebrochen** abgeschlossen, der Verlauf bleibt stehen, und der
  nächste Zug setzt darauf auf. Echte Störungen bleiben Fehler.

  Der Claude-CLI-Pfad kannte das längst (`_interrupted`, SIGINT/Code -2) — nur die
  Custom-LLM-Laufzeit nicht. Harness-Parität, jetzt mit Test in beiden.
- **Beim Senden folgte die Ansicht nicht.** Nachtrag zu 1.175.1: wer weiter oben
  gelesen hatte und dann eine Nachricht abschickte, blieb dort stehen. Senden ist
  aber eine ausdrückliche Handlung — jetzt springt die Ansicht dabei ans Ende.
- **Fliesskomma-Einstellungen kamen als Zeichenkette aus der Ablage zurück.** Bools
  und ganze Zahlen wurden umgewandelt, Fliesskommazahlen nicht. Bisher folgenlos, weil
  keine der drei betroffenen Einstellungen über die Ablage lief — mit dem Umrechnungs-
  kurs wäre es sofort aufgefallen, und zwar weit weg von der Ursache: beim Rechnen.
  Ein unbrauchbarer Wert lässt jetzt den Vorgabewert stehen, statt ihn zu überschreiben.

## [1.175.2] — 2026-08-11

### Fixed
- **Erledigte Aufgaben standen in der falschen Reihenfolge.** Der Server sortiert
  die ganze Liste nach Priorität und Reihenfolge — richtig für Offenes, aber im
  Abschnitt „Erledigt" stand dadurch ganz oben, was vor Wochen abgehakt wurde.
  Wer dort nachsieht, will wissen, was gerade fertig geworden ist. Jetzt zuletzt
  Erledigtes zuerst, mit Datum am Eintrag, damit die Reihenfolge nachvollziehbar
  ist. Einträge ohne `completed_at` (abgehakt, bevor es das Feld gab) fallen auf
  die letzte Änderung und dann auf die Anlage zurück, statt ans Ende zu rutschen.

## [1.175.1] — 2026-08-11

### Fixed
- **In einem langen Gespräch klebte man oben.** Die Ansicht sprang nur dann ans
  Ende, wenn sie im Moment der neuen Nachricht schon fast unten stand. Beim
  Öffnen einer langen Unterhaltung steht sie aber ganz oben — `scrollTop` ist 0,
  „fast unten" also nie wahr. Folge: die Ansicht sprang kein einziges Mal ans
  Ende, und der laufende Strom lief unsichtbar unter einem weiter. Je länger man
  in einem Gespräch arbeitete, desto sicherer trat der Fehler auf.

  Ob gefolgt wird, entscheidet jetzt das Scrollen selbst und nicht mehr die
  Position beim Eintreffen einer Nachricht. Ein Gesprächswechsel beginnt beim
  Neuesten; wer hochscrollt, hält die Ansicht weiterhin an — nur kommt er mit dem
  neuen Knopf **Zum Neuesten** mit einem Klick zurück, statt sich von Hand bis
  ans Ende arbeiten zu müssen.

## [1.175.0] — 2026-08-10

### Fixed
- **„Er komprimiert mir etwas zu oft."** Stimmte — und dahinter steckten drei
  Fehler, von denen jeder allein gereicht hätte. Betrifft nur die Custom-LLM-
  Laufzeit; Claude Code und Codex verdichten in ihrer eigenen CLI.

  **Zwei Maßstäbe.** Ausgelöst wurde am echten Token-Zähler der Schnittstelle —
  System-Prompt, Werkzeug-Schemata und Verlauf zusammen. Geprüft, ob es etwas
  gebracht hat, wurde danach an einer Zeichenschätzung, die nur den Verlauf
  kennt. Allein die Werkzeug-Schemata sind rund 16k Token, die diese Prüfung nie
  gesehen hat. Also meldete die Auslösung Not, wo die Prüfung keine fand: der
  Hinweis „[Kontext wird komprimiert...]" erschien Zug um Zug, ohne dass je etwas
  verdichtet wurde. Jetzt gilt für beides derselbe Maßstab — die eigene Schätzung
  plus der gemessene Abstand zu dem, was die Schnittstelle tatsächlich berechnet.

  **Keine Hysterese.** Verdichtet wurde nur bis knapp unter die Auslöseschwelle.
  Eine Werkzeugausgabe später war man wieder darüber. Ein Lauf muss jetzt auf 60 %
  der Schwelle herunter, nicht auf 99 %.

  **Die Summe als Größe.** Im Aufgabenlauf wurde die *aufaddierte* Eingabe aller
  Züge als aktuelle Kontextgröße gelesen. Die wächst zwangsläufig, auch wenn der
  Verlauf gleich bleibt — eine lange Aufgabe verdichtete deshalb alle paar Züge
  ohne Anlass. Schlimmer: der Zähler wurde danach zurückgesetzt, und weil derselbe
  Zähler die Kosten trug, verlor die Aufgabe bei jeder Verdichtung ihre bis dahin
  gezählten Eingabe-Token. Kosten und Kontext sind jetzt zwei Zahlen.

- **Die späteren Verdichtungsschichten liefen auf großen Modellen nie.** Das Ziel
  der Kette war ein Anteil am *Modellfenster* (55 %). Auf einem 1M-Modell waren
  das 550.000 Token — weit über der Auslöseschwelle von 150.000. Die Kette brach
  deshalb immer schon nach der ersten Schicht ab: Microcompact und Collapse liefen
  ausgerechnet dort nicht, wo der Kontext groß wird.

- **Ein aussichtsloser Lauf wird nicht mehr jeden Zug wiederholt.** Wenn der feste
  Anteil (System-Prompt + Werkzeuge) die Schwelle allein sprengt, hilft kein
  Falten des Verlaufs. Das wird gemerkt; der nächste Versuch kommt erst, wenn der
  Kontext spürbar gewachsen ist.

- **Gesagt wird jetzt, was passiert ist.** Der Hinweis kommt nach dem Lauf und nur
  bei echter Wirkung, mit Zahlen: `[Kontext verdichtet: 152k → 78k Token]`.

### Added
- **Apps zeigen ihren Besitzer.** In der Übersicht und im Detail steht neben dem
  Agenten jetzt der Mensch, dem er gehört. Bei freigegebenen Apps ist das die
  eigentliche Frage — in der Liste stand bisher nur der Agentenname, und der sagt
  nichts darüber, von wem die App stammt. Bewusst nur der Name: wem eine App
  freigegeben wurde, den geht die Mailadresse des Besitzers noch nicht an.
- Beim Start protokolliert die Custom-LLM-Laufzeit, welches Fenster sie für das
  konfigurierte Modell auflöst und bei welchen Werten sie verdichtet — inklusive
  Vermerk, wenn das Modell unbekannt ist und der Rückfallwert greift.

## [1.174.1] — 2026-08-10

### Fixed
- **Die Kontextanzeige erfand eine Fenstergröße.** Beim Nachmessen an den echten
  Agenten auf dem Pi fiel auf: `claude-sonnet-5` steht in keiner Modelltabelle,
  und `/compact` behauptete daraufhin ein 128k-Fenster (den Rückfallwert). Eine
  erfundene Zahl ist in einer Kontextanzeige schlimmer als ein ehrliches „?" —
  sie verspricht Luft, die es vielleicht nicht gibt, oder sie drängt zum
  Verdichten, wo gar kein Grund ist.

  Unbekannt heißt jetzt unbekannt: die Anzeige sagt, dass die Fenstergröße dieses
  Modells nicht hinterlegt ist. Verdichten geht trotzdem.

  **Der Agent selbst behält seinen Rückfallwert** (128k). Dort ist er richtig: zu
  früh zu verdichten kostet einen Zusammenfassungsaufruf, zu spät kostet den Lauf.

  Kosten sind davon **nicht** betroffen — die melden Claude Code und Codex selbst
  (auf dem Pi 837 Aufgaben, 551,23 $ korrekt erfasst).

---

## [1.174.0] — 2026-08-10

### Added
- **`/` im Chat zeigt jetzt, was DIESER Agent kann** — je nach Laufzeit
  verschieden. Ein Claude-Code-Agent sieht Claude Codes eigene Werkzeuge und die
  MCP-Server, ein Codex- oder Custom-LLM-Agent den gemeinsamen Werkzeugsatz. Dazu
  die installierten Skills und freigeschalteten MCP-Server dieses Agenten. Eine
  erfundene gemeinsame Liste wäre bei jeder Laufzeit ein bisschen falsch gewesen.
- **`/compact`** — zeigt das Kontextfenster (belegt / verfügbar, Modell,
  Nachrichten) und verdichtet den Verlauf **im selben Gespräch**. Die letzten 8
  Nachrichten bleiben wörtlich; ältere werden **markiert, nicht gelöscht** —
  verdichten heißt nicht verlieren. Für den Menschen bleibt der Verlauf lesbar.
- **`/tools`** — die vollständige Ausstattung als Tafel.

### Wichtig zur Einordnung
`/compact` arbeitet auf dem **hier gespeicherten** Verlauf und funktioniert
deshalb in allen drei Laufzeiten gleich. Die Kompaktierung *innerhalb* von Claude
Code und Codex bleibt davon unberührt — die machen ihre CLIs selbst und lassen
sich von außen nicht anstoßen. Claude Codes eigene Befehle (`/compact`, `/clear`,
`/cost`) stehen deshalb in der Liste, sind aber als **„gehört der Laufzeit"**
markiert: sie zu zeigen ist ehrlich, sie als unsere auszugeben nicht.

### Zum Stand der Kompaktierung
Eine eigene Kontextverdichtung haben wir nur für **Custom-LLM**
(`context_compressor.py`: Snip → Microcompact → Collapse → gleitende
Zusammenfassung, ausgelöst bei `min(75 % des Fensters, 150 000 Token)`, die
letzten 24 Nachrichten bleiben wörtlich). Claude Code und Codex verwalten ihren
Kontext selbst — wir sehen es nicht und können es nicht steuern.

### Tests
1889 grün. 22 neue, darunter ein Katalog-Paritätstest: er liest die echten
Werkzeugdefinitionen aus `agent/mcp/*.mjs` und `agent/app/tools/definitions.py`
und hält sie gegen den Katalog im Orchestrator. Er hat beim ersten Lauf sofort
zugeschlagen — die von Hand getippte Liste war an neun Stellen falsch.

---

## [1.173.0] — 2026-08-10

Kundenmeldung: wer sich über Microsoft anmeldet, um dem M365-MCP zuzustimmen,
bekam als Nebenwirkung die **volle Oberfläche** — obwohl ihm niemand etwas
zugewiesen hatte.

### Changed
- **Wer sich selbst registriert, bekommt keine Rolle mehr.** Bisher wurde jeder
  automatisch *Mitglied* mit fünf Agenten. Neu: Rolle `unassigned` — das Konto
  ist angelegt, die Oberfläche bleibt zu, bis ein Administrator eine Rolle
  zuweist. Der **erste** Nutzer wird weiterhin immer Administrator, sonst wäre
  eine frische Anlage von der ersten Sekunde an ausgesperrt.

  Umstellbar über `default_new_user_role=member` (altes Verhalten). Ein Vertipper
  in der Einstellung vergibt **keine** Rechte, und `admin` ist als Vorgabe
  ausgeschlossen — das wäre eine Selbstbedienung.

### Added
- **Erklärseite statt leerer Oberfläche.** Wer ohne Rolle anmeldet, sieht: „Noch
  keine Rolle zugewiesen — bitte wende dich an einen Administrator", mit
  Abmelden-Knopf. Kein Menü, kein Inhalt, nichts anklickbar.
- **Serverseitige Sperre** in `get_current_user` — der eine Engpass, durch den
  jede Anfrage der Oberfläche läuft. Menüpunkte zu verstecken wäre keine Sperre:
  `menu_paths` liest nur die Seitenleiste, wer die Adresse tippt, wäre drin.
  Offen bleibt eine winzige Ausnahmeliste (`/auth/me`, `/auth/logout`,
  `/auth/refresh`, `/version`, `/health`) — gerade genug, damit die Oberfläche
  erklären kann, warum sie leer ist.
- Im Admin ist `unassigned` Teil des Rollen-Rundlaufs (als „Ohne Rolle"), damit
  eine Rolle auch wieder **zurückgenommen** werden kann.

### Wichtig
**Der MCP-Zugang bleibt offen.** Genau darum geht es: Postfach ja, Plattform nein.
`/oauth/authorize` liest das Cookie direkt, die MCP-Aufrufe tragen ein
Bearer-Token — keiner der beiden Wege läuft durch `get_current_user`. Drei Tests
nageln das fest, damit eine spätere „Vereinheitlichung" nicht den Kunden ohne
Postfach zurücklässt.

### Tests
1867 grün, 19 neue.

---

## [1.172.0] — 2026-08-10

Kundenmeldung: **570 offene Freigaben** auf einer Anlage.

### Fixed
- **Freigaben liefen nie ab.** `ApprovalStatus.EXPIRED` stand seit jeher im Modell
  und wurde **nie gesetzt** — eine Anfrage blieb ewig offen, auch wenn der fragende
  Agent längst in seine Zeitgrenze gelaufen und der Lauf vorbei war. Der Zeitgeber
  lässt unbeantwortete Anfragen jetzt nach 24 h verfallen. Die Frist ist bewusst
  viel länger als die längste Wartezeit eines Agenten (15 min): eine Frage, die
  jemand abends sieht und morgens beantworten will, darf nicht über Nacht
  verschwinden.
- **Dieselbe Frage lief beliebig oft auf.** Ein Agent mit unvollständiger
  Einrichtung fragt bei jedem proaktiven Lauf, ob er seinen Laufstatus notieren
  darf; niemand antwortet, eine Stunde später fragt er erneut. Aus Sicht des
  Menschen ist das **eine** Entscheidung, nicht 570. Gleiche Frage desselben Agenten
  → dieselbe Zeile, mit Zähler; der Agent bekommt dieselbe Kennung zurück und
  wartet weiter darauf. Keine zweite Benachrichtigung — sonst klingelt das Telefon
  570-mal für dieselbe Frage.

  Verglichen wird Werkzeug **und** Begründung **und** die Frage selbst. Die erste
  Fassung verglich nur die Begründung und hätte zwei verschiedene Fragen mit
  gleicher Begründung verschluckt — der Test dazu hat es gefunden.

### Added
- **Abzeichen am Menüpunkt „Approvals"** mit der Zahl der offenen Freigaben
  (eingeklappt ein Punkt am Symbol). Eigener Zähl-Endpunkt statt der vollen Liste:
  das Menü fragt im Takt, und die Liste kann Hunderte Einträge samt
  Begründungstexten haben.
- **„Alle verwerfen"** auf der Freigaben-Seite. Verworfen wird als **abgelehnt**,
  nicht gelöscht — die Prüfspur muss erhalten bleiben. Wartende Agenten bekommen
  sofort ein Nein, statt in ihre Zeitgrenze zu laufen.

### Tests
1848 grün. 13 neue, darunter zwei gegen die FastAPI-Routenreihenfolge: stünde
`/{approval_id}` vor `/pending`, liefe „alle verwerfen" in ein `int("pending")`.
Dieselbe Falle hat in diesem Projekt schon zweimal zugeschlagen.

---

## [1.171.0] — 2026-08-09

### Changed
- **Der Concierge zeigt jetzt Aufgaben statt Kennzahlen.** Er war eine Kachel mit
  vier Zahlen und einer Ampel — und das ist der falsche Zuschnitt: „Aufgaben 24 h:
  33" und „Kosten 24 h: 57 $" verlangen **keine Handlung**, dafür gibt es das
  Dashboard. Sobald Zahlen den Platz füllen, muss die Ampel den Alarm allein
  tragen, und dann wird sie großzügig ausgelöst. Genau so landete „angehalten" in
  derselben Liste wie „abgestürzt" (siehe 1.170.1).

  **Eine Regel:** hier steht nur, was eine Entscheidung oder einen Handgriff
  braucht, und jeder Punkt trägt genau eine Sache, die man dagegen tun kann —
  einen Knopf, der es sofort erledigt, und/oder einen Pfeil auf die Seite, wo es
  entschieden wird. Ist nichts da, steht da „nichts, was auf dich wartet".

  Die Ampel wird jetzt **aus der Liste** abgeleitet statt danebengerechnet. Vorher
  standen beide für sich, und genau deshalb konnten sie auseinanderlaufen.

### Added
- **Der Concierge kennt fünf Dinge, die er vorher nicht sah:**
  - **Eskalationen** (#389/#390) — ein Agent steht still und wartet auf eine
    Entscheidung. Das gab es seit 1.170.0, und ausgerechnet dort tauchte es nicht auf.
  - **Abgelaufene Zugänge** — der wertvollste Punkt, weil er **still** scheitert:
    es wird nichts rot, es hört einfach auf zu funktionieren. Genau so war das
    Dazwischenreden im laufenden Turn bei den Claude-Agenten wochenlang tot.
    Ablauf in den nächsten 3 Tagen kommt als Vorwarnung.
  - **KI-Konten**, deren letzte Verbindungsprüfung fehlschlug.
  - **Budgets** — aufgebraucht (rot) oder über 90 % (gelb), mit der Folge dabei
    („heruntergestuft" / „gestoppt").
  - **Angehalten mit Auftrag** — bekommt seit v1.154.1 keine proaktiven Läufe mehr
    und tut still nichts.
- Die Erkenner liegen als reine Regeln in `core/attention.py`, ohne Datenbank —
  damit jede für sich prüfbar ist.

### Tests
1835 grün. 18 für die Regeln, 5 weitere von Ende zu Ende gegen echtes SQL. Der
Test „kein Sprachmodell beteiligt" gilt jetzt für beide Dateien.

### Docs
Handbuch: neuer Abschnitt 22a (Concierge, Admin-only).

---

## [1.170.1] — 2026-08-09

### Fixed
- **Der Concierge zeigte dauerhaft „Handlungsbedarf", weil Agenten angehalten
  waren.** „Kaputt" und „aus" lagen in derselben Liste, und die Ampel sprang bei
  beidem auf rot. Angehalten ist aber ein **normaler** Zustand: ein Nutzer hält
  einen Agenten von Hand an, der Idle-Stopp hält ihn an, und beim nächsten Auftrag
  weckt `wake_agent` ihn wieder. Der Concierge schlug damit Alarm über das
  vorgesehene Verhalten der Plattform — und eine Ampel, die ständig rot ist, sieht
  sich nach einer Woche niemand mehr an.

  Rot ist jetzt nur noch, was wirklich kaputt ist: ein Agent im **Fehlerzustand**
  oder eine Aufgabe, die seit über 30 Minuten hängt. Ruhende Agenten stehen unter
  **„Ruht"** mit einem Starten-Knopf.

  **Eine Ausnahme bleibt:** ein angehaltener Agent **mit Verantwortungsbereichen**
  bekommt seit v1.154.1 keine proaktiven Läufe mehr — der tut still nichts. Das
  steht jetzt dran („proaktive Läufe fallen aus") und setzt die Ampel auf
  *wartet auf dich*, nicht auf rot.

  Das alte Feld `unhealthy` bleibt in der Antwort, enthält aber nur noch echte
  Fehler — eine Oberfläche aus der Zeit davor zeigt damit keine ruhenden Agenten
  mehr als Alarm.

### Tests
1812 grün. Sechs neue gegen echtes SQL nageln die Ampel fest: angehalten ist kein
Notfall, Fehlerzustand schon, angehalten **mit** Auftrag wartet auf eine
Entscheidung.

---

## [1.170.0] — 2026-08-09

Vier offene Roadmap-Punkte, die dieselbe Frage beantworten: **Wann darf ein Agent
allein weitermachen — und wann muss ein Mensch ran?**

### Added
- **Selbstheilung gescheiterter Aufgaben** (#390). Ein Zeitablauf oder ein 503 wird
  mit wachsender Wartezeit wiederholt, ein falsches Kennwort nie — das kostet Geld
  und ändert nichts. Die Vorgehensweise wechselt: erst dasselbe nochmal (bei einem
  Ausfall wäre jede Änderung am Auftrag nur Rauschen), dann in kleineren Schritten,
  dann mit einem anderen Modell. Erst danach der Mensch, mit dem gesammelten
  Verlauf statt eines nackten „Task failed". Regelwerk pro Agent einstellbar.
- **Nachfragen statt raten** (#389). Agenten melden vor unklaren Entscheidungen ihre
  Sicherheit. Die Schwelle liegt auf dem **Server**: würde der Agent selbst
  beurteilen, ob seine 40 % genügen, wäre diese Beurteilung genauso unsicher wie die
  Antwort. Reicht die Sicherheit, kostet der Aufruf nichts — kein Mensch wird
  behelligt. In allen drei Laufzeiten, über denselben Freigabe-Weg wie alles andere.
- **Golden-Tests als Update-Gatter** (#391). Versionierte Aufgabensammlungen je
  Rolle, ausgeführt als **echte Aufträge** durch den echten Agenten — samt
  Systemprompt, Skills und Modell. Fällt der Wert unter die Grundlinie, wird das
  Container-Update abgelehnt (mit Notausgang). Bewusst ohne Modell-Schiedsrichter:
  ein Gatter, dessen Bewertung schwankt, blockiert mal und lässt mal durch.
- **Eskalations-Posteingang.** „Zu unsicher" (#389) und „endgültig gescheitert"
  (#390) landen an EINER Stelle unter Freigaben. Für den Menschen ist es dieselbe
  Frage; zwei Listen wären zwei Orte zum Nachsehen, und einer würde vergessen.
- **Jedes Symbol, jede Farbe, ein Schlagwort** (#523, #524). Statt 18 kuratierter
  Symbole der ganze lucide-Satz (nachgeladen, nicht im Erstladen), freie Farben, und
  ein frei wählbares Schlagwort mit Suche, Filter, Gruppierung und Sortierung in der
  Übersicht. Das Team bleibt davon getrennt — es ist ein Verhaltens-, kein
  Ordnungsbegriff.
- **Composer im Claude-Code-Zuschnitt** (#538, Punkt 1). Eingabe oben, Bedienung in
  einer Fußzeile darunter, Kontextring neben dem Absenden. Befehle mit „/", die
  ausschließlich auf vorhandene Fähigkeiten führen.
- **Ein Skript für die Roadmap-Bilder** (`docs/generate_roadmap.py`). Vorher wurden
  sie bei jedem Release von Hand neu gebaut, jedes Mal etwas anders.

### Fixed
- **Golden-Test-Läufe waren nicht mandantengetrennt.** `GET /evals/runs` hatte den
  Besitzcheck im `if agent_id:`-Zweig — wer den Parameter wegließ (und genau das tut
  die Übersichtsseite), bekam die Läufe aller Nutzer. Die Einschränkung steht jetzt
  im Query, nicht in einem Zweig.
- **Der Selbsttest holte nie ein GitHub-Token über OAuth.** `self_test_service.py`
  importierte aus `app.security.encryption` — das Modul gibt es nicht, die Funktion
  heißt anders. Beide Aufrufe standen in einem `except Exception`.
- **Codex-Agenten bekamen keine frischen MCP-Zugangsdaten** (#488). Die
  Auffrischungsschleife schloss `codex_cli` aus, mit der Begründung, Codex nutze
  `CUSTOM_MCP_*` gar nicht — es liest sie bei jedem Aufruf neu. Auf dem Pi sind
  sieben von acht Agenten Codex.
- **Der Appearance-Endpunkt meldete 404 „Agent not found" bei einer abgelehnten
  Farbe.** Ein `except ValueError` lag um den ganzen Rumpf. Jetzt 400.
- **Die Kurzfassung von `GET /agents` ließ `avatar` weg** — die Übersicht lädt genau
  diese Liste, Sinnbild und Farbe wären dort nie angekommen.
- **`CommandApproval.task_id` wurde nie gefüllt**, obwohl die Spalte seit jeher
  existiert: in der Ablage stand die Frage ohne den Auftrag, um den es ging.
- Der neue statische Importtest hing an der Testreihenfolge (`find_spec` stolpert
  über Attrappen anderer Tests). Er sieht die Existenz eines Moduls jetzt auf der
  Platte nach.

### Changed
- **Der statische Importtest prüft jetzt JEDEN `app.*`-Import** — auch die innerhalb
  von Funktionen, also genau die, die hinter den 172 breiten `except`-Blöcken liegen
  und weder beim Start noch im Testlauf auffallen. Geprüft wird Modulpfad UND Name.
  Damit ist der Roadmap-Punkt „verschluckte Import-Fehler" anders gelöst als
  geplant: nicht durch einen riskanten Großumbau, sondern durch einen Test, der
  diese Fehlerklasse unversendbar macht.
- Handbuch um die Abschnitte 25–25e erweitert (Symbol/Schlagwort, Suche & Filter,
  Selbstheilung, Nachfragen, Golden-Tests, Composer).

### Tests
1804 grün, 1 vorbestehend rot (Nova Sonic, fehlendes Bedrock-Modul lokal).
Neu: 24 + 11 Selbstheilung, 17 + 7 Konfidenz (inkl. Harness-Parität), 31 + 14 + 6
Golden-Tests, 20 Aussehen/Schlagwort.

---

## [1.169.1] — 2026-08-09

### Fixed
- **Der Gedächtnis-Preload war seit dem 2026-08-07 unbenutzbar.**
  `core/memory_preload.py` importierte aus `app.models.agent_memory` — ein Modul,
  das es nicht gibt (es heißt `app.models.memory`). Der Import steht auf Modulebene,
  aber das Modul selbst wird erst spät geladen, deshalb fiel es nie beim Start auf,
  sondern erst als 500 beim Aufruf. Aus V1 der Vision-Arbeit (`6e635f8`).
- Denselben falschen Pfad hatte ich beim Self-Improvement-Endpunkt nachgebaut; der
  Import war dort ohnehin überflüssig.

### Added
- **Ein Test importiert jetzt jedes Modul unter `app/` einmal.** Ein nicht
  auflösbarer Import ist immer ein Fehler, nie ein Zustand — und diese Klasse
  Fehler gehört in den Testlauf, nicht zum Nutzer.

---

## [1.169.0] — 2026-08-09

### Added
- **Gespräche verzweigen, zurückspulen und zusammenfassen** (#538). Alle drei
  arbeiten auf „die Nachrichten bis hierher". Verzweigen **kopiert** — das Original
  bleibt; Zurückspulen **löscht** und legt deshalb eine Sicherung an.
- **Echte Gesprächstitel** statt der rohen letzten Nachricht. Aus dem ersten
  Austausch abgeleitet, bewusst ohne Sprachmodell — ein Titel ist keine hundert
  Modellaufrufe wert, und der erste Satz sagt fast immer schon, worum es geht.

### Fixed
- **WhatsApp hatte keine Absenderprüfung** — wer die Nummer kannte, schrieb dem
  Agenten. Als einziger Kanal ohne natürlichen Rahmen (Telegram verlangt `/auth`,
  Teams und Slack liegen im Firmen-Tenant). Jetzt fail-closed, einseitig verglichen
  und mit mindestens zehn Stellen.
- **Kopfbereich der Agentenseite war zu hoch** (#537). Beschreibung auf eine Zeile
  gekürzt (voller Text im Tooltip), Umbenennen direkt am Namen statt am rechten
  Rand, Budget einzeilig statt als vollbreite Karte.
- **Protokoll-Injektion** in fünf weiteren Ausgabestellen (PR #545, #546) — damit
  sind #542 und #543 erledigt.

### Changed
- Frontend-Basis auf `node:26-alpine` (PR #158).

---

## [1.168.0] — 2026-08-08

### Added
- **Agent mit Stimme im Teams-Termin.** Er tritt bei, sagt etwas, hört eine Antwort
  und reagiert darauf — abwechselnd, wie am Telefon. Über Graph Communications mit
  *service-hosted media*: Microsoft hält die Medien, wir brauchen weder ein
  .NET-Medienmodul noch offene Medienports.
- **Einrichtungs-Karte für Administratoren** mit der Rückruf-Adresse zum Kopieren,
  einem Prüfknopf und der Berechtigungs-Checkliste — plus
  `docs/TEAMS_CALLING_SETUP.md`, Klick für Klick durch Azure.

### Notes
- `Calls.AccessMedia.All` wird **bewusst nicht** angefordert. Die Berechtigung
  erlaubt den Zugriff auf den rohen Audiostrom aller Teilnehmer und wird nur für
  durchgehendes Mithören gebraucht — was dieser Weg nicht tut.
- Die Karte warnt vorab, wenn die Anlage nicht über HTTPS erreichbar ist: Microsoft
  ruft ausschließlich HTTPS zurück, und sonst bliebe der Agent stumm, ohne dass
  irgendwo ein Fehler aufträte.

---

## [1.167.0] — 2026-08-08

Der Vision-Abschluss: die offenen Punkte aus allen vier Roadmap-Säulen. Drei davon
waren keine Lücken, sondern **Wege, die an einer vorhandenen Fähigkeit vorbeigingen** —
sie fielen erst beim Nachsehen auf.

### Added
- **SAML 2.0 SSO mit IdP-Gruppen-Zuordnung.** Die Signaturprüfung bewusst über
  `python3-saml`/`xmlsec` statt selbst geschrieben — XML-DSig von Hand zu prüfen ist
  der klassische Ort für Signature-Wrapping, und ein Kanonisierungsfehler dort ist ein
  Authentifizierungs-Bypass. Höchste zutreffende Rolle gewinnt; ohne Treffer bleibt die
  Rolle unverändert, und der letzte Administrator wird nie herabgestuft.
- **Browser-Meldungen (Web Push) und installierbare App (PWA).** Ohne neue
  Abhängigkeit umgesetzt (RFC 8292 + RFC 8291/8188 mit `cryptography`). Der Inhalt ist
  für den Empfänger verschlüsselt — der Push-Dienst leitet nur weiter.
- **Multi-Channel: Teams, Slack, WhatsApp.** Teams in drei Richtungen — Mensch schreibt
  den Agenten an, Agent schreibt Agent, Agent als Mitschreiber oder Beisitzer in
  Terminen. Ohne Bot-Registrierung: die Graph-Anbindung mit Nutzer-OAuth darf das
  bereits.
- **Wochensynthese (#384) und Auto-Capture (#385).** Muster, Widersprüche,
  Wissenslücken und EINE Aktion aus den letzten sieben Tagen; Links und lange
  Textblöcke landen im Second Brain statt im Chatverlauf.
- **Self-Improvement sichtbar (#13).** Die Mechanik lief längst — es gab nur keine
  Fläche, auf der steht, was der Agent gelernt hat.
- **Admin-Concierge (#11).** „Läuft alles?" in einer Antwort, bewusst ohne
  Sprachmodell: ein Concierge, der eine Zahl halluziniert, ist schlimmer als keiner.
- **Ticketsystem-Anschluss** mit Matrix42 als erstem Profil. Ohne Schließen und
  Löschen — den Abschluss macht ein Mensch.
- **Ablauf-Vorlagen für Besprechungen (#14):** Daily, Retrospektive, Workshop,
  Entscheidung.
- **Nacharbeitsquote** in der Entwicklungs-Karte und im Analytics-Tab.
- **MCP-Brücke:** Rückruf beim Fertigwerden statt Polling, plus `cancel_task`.

### Fixed
- **Die Team-Lead-Stufe der Vertretungskette hat nie ausgelöst.** `team_lead_for`
  verband sich auf eine Tabelle `team_members`, die es in diesem Projekt nie gab. Der
  ImportError lief jedes Mal ins umschließende `except`, die Funktion gab stumm `""`
  zurück. Folge: ohne eingetragenen Vertreter übernahm **niemand**, und unbeantwortete
  Rückfragen gingen immer an die Administration statt an den Team-Lead. Aufgefallen nur,
  weil der neue Beleg gegen echtes SQL läuft statt gegen Attrappen.
- **Die Autonomiestufe bestimmt jetzt auch den sudo-Zugriff im Container.** Ein
  L1-Agent („nur lesen") bekam trotzdem das Standardpaket — der Prompt sagte nein, die
  Kiste sagte ja.
- **Browser-Steuerung gab es nur für Claude Code.** `claude mcp add` schreibt in die
  Konfiguration der Claude-CLI, die Codex und Custom-LLM nicht lesen. Von drei
  Harnessen konnte nur einer im Browser arbeiten.
- **Kanal-Zugangsdaten** (Slack-Token, WhatsApp-Geheimnis) werden verschlüsselt
  abgelegt statt im Klartext.

### Changed
- Der Wissens-Schreibweg (anlegen, einbetten, verknüpfen) existierte **viermal** fast
  gleich — der Kommentar im Code sagte selbst „mirrors api/knowledge.py". Jetzt in
  `core/knowledge_write`, von allen Aufrufern genutzt.
- Der Kanal-Eingang (Historie, Auto-Capture, Einreihen) lag im Telegram-Bot und liegt
  jetzt in `core/channel_gateway` — Telegram, Teams, Slack und WhatsApp teilen ihn.
- `push_to_user` lag in `apns_service` und erreichte nur iPhones; der Verteilpunkt
  liegt jetzt in `core/push` und fächert auf alle Geräte auf.

---

## [1.166.10] — 2026-08-08

### Fixed
- **Ein angehaltener AudioContext machte den Sprachmodus stumm — ohne einen einzigen
  Fehler.** Chrome startet einen AudioContext ohne Nutzergeste als `suspended`.
  - **Aufnahme:** `onaudioprocess` feuert dann nie. Die Verbindung steht, der Agent
    begrüßt, danach kommt nichts mehr an. Der Kontext wird jetzt aufgeweckt, und liefert
    die Aufnahme nach 2,5 Sekunden immer noch keinen einzigen Block, steht das als
    Meldung da — samt Zustand des Kontexts, statt still zu bleiben.
  - **Wiedergabe:** Die Blöcke werden eingeplant und nie hörbar — in der Oberfläche steht
    „Spricht…", aus dem Lautsprecher kommt nichts. Der Kontext wird jetzt bei jedem Block
    geprüft und aufgeweckt.

### Deployment
- Frontend-Rebuild.

---

## [1.166.9] — 2026-08-08

### Fixed
- **Das Live-Gespräch starb, wenn vom Mikrofon nichts kam** — mit einer AWS-Meldung statt
  einer Erklärung („Timed out waiting for audio bytes … less than 55 seconds"). Die
  Erhaltungsschleife schickte Stille erst, NACHDEM die Begrüßung lief, und die lief erst
  nach dem ersten echten Mikrofon-Frame. Kam der nie (Freigabe verweigert, falsches Gerät,
  stumm), blieb alles still und der Anbieter brach nach 55 Sekunden ab. Jetzt hält die
  Schleife den Strom ab dem ersten Tick warm, und die Begrüßung spricht auch ohne Zutun.
- **Ein totes Mikrofon wird benannt:** Kommt 20 Sekunden lang kein Signal, sagt die
  Oberfläche das — statt den Nutzer raten zu lassen, warum niemand antwortet.

### Deployment
- Orchestrator-Neustart.

---

## [1.166.8] — 2026-08-08

### Changed
- **Die Telegram-Regel steht jetzt im Systemprompt aller drei Laufzeiten** (Claude Code,
  Codex, Custom-LLM) statt nur im Proaktiv-Prompt. Vorher galt sie nur für geplante Läufe —
  im Chat und in jeder normalen Aufgabe wusste der Agent nichts davon. Enthalten: kein
  fremder Bot, „kein eigener Bot" ist kein Fehler, bei Dringendem den Team-Lead per
  `send_message` bitten, und die Gegenseite (was ein Team-Lead damit zu tun hat).

### Deployment
- Orchestrator-Neustart, Agenten neu erstellen (neue Anleitung).

---

## [1.166.7] — 2026-08-08

### Added
- **Ohne eigenen Telegram-Bot geht die Meldung trotzdem nicht verloren — sie landet im
  Chat.** Die Zustellkette ist jetzt: eigener Bot → sonst Ablage als Nachricht im Chat des
  Agenten (Unterhaltung „meldungen"), und der Agent erfährt dabei, wer sein **Team-Lead**
  ist. Ist die Sache dringend, bittet er ihn per `send_message`, sie weiterzugeben — der
  Lead entscheidet und schreibt unter **seinem** Namen, damit immer erkennbar bleibt, wer
  da schreibt. Hat der Lead auch kein Telegram, bleibt es beim Chat, und er sagt das dem
  Absender. Kein Ausleihen, kein stiller Umweg, keine verlorene Meldung.

### Deployment
- Orchestrator-Neustart.

---

## [1.166.6] — 2026-08-08

### Changed
- **Ein Agent ohne eigenen Telegram-Bot hat keinen Telegram-Kanal — Punkt.** Das Ausleihen
  fremder Bots ist ersatzlos raus. Es war nicht erkennbar, mit wem man eigentlich schreibt:
  im Chat stand JujaBot, geantwortet hat CodeReview. Wer den Kanal nutzen soll, bekommt
  einen eigenen Bot-Token in seinen Einstellungen. Der Agent bekommt eine klare Antwort
  (503 mit Erklärung) und ist in seiner Basis-Anleitung angewiesen, das **nicht** als
  Fehler zu melden und nicht nach einem anderen Weg zu suchen.

### Deployment
- Orchestrator-Neustart, Agenten neu erstellen (neue Basis-Anleitung).

---

## [1.166.5] — 2026-08-08

### Fixed
- **Ein Agent konnte seine Telegram-Meldung in den privaten Chat eines FREMDEN Nutzers
  schicken.** Hat ein Agent keinen eigenen Bot, leiht er sich einen — die Suche lief aber
  über *alle* laufenden Bots, ohne Rücksicht auf den Besitzer. Die App ist userbased; das
  darf sie nicht. Geliehen wird jetzt nur noch bei einem Agenten **desselben Besitzers**,
  und ein Agent ohne Besitzer (System-/Admin-Agent) leiht sich gar nichts — seine
  Meldungen gehören in keinen privaten Chat. Genau darüber landeten die Arbeitsberichte
  von CodeReview, für den nie ein Telegram eingerichtet wurde, im JujaBot-Chat.

### Deployment
- Orchestrator-Neustart.

---

## [1.166.4] — 2026-08-08

### Fixed
- **Ein Agent-Bot verstummte, weil eine fremde Meldung den Chat gekapert hatte.** Ein Agent
  ohne eigenen Telegram-Bot leiht sich den eines anderen, um eine Meldung loszuwerden.
  Dabei wurde die Weiche des Chats für **24 Stunden** auf den leihenden Agenten gestellt —
  danach ging jede Nachricht des Nutzers an ihn, und der Agent, dem der Bot gehört, hörte
  nie wieder etwas. Beim JujaBot stand deshalb tagelang `gateway=JujaBot →
  target=CodeReview` im Log: geschrieben wurde an Julia's Bot, angekommen ist es woanders.
  Eine geliehene Meldung ist jetzt **Einweg**; wer dem anderen Agenten wirklich schreiben
  will, wählt ihn ausdrücklich mit `/agent`, und die Meldung sagt auch wie.
- **Eine Weiche auf einen gelöschten Agenten führte ins Leere.** Sie fällt jetzt auf den
  Besitzer des Bots zurück und wird entfernt — statt den Chat stumm gegen eine Wand laufen
  zu lassen.

### Deployment
- Orchestrator-Neustart.

---

## [1.166.3] — 2026-08-08

### Fixed
- **OAuth-Token-Erneuerung skaliert nicht mehr mit der Zahl der Agenten (#503).** Bei über
  20 Agenten lief `refresh_if_needed` einmal pro Agent pro Server pro Runde; sobald ein
  Token die Ablaufschwelle überschritt, konkurrierten alle gleichzeitig um dieselbe
  Sperre. Jetzt merkt sich der Prozess 30 Sekunden lang, dass ein Server ein brauchbares
  Token hat — strikt unter den 60 Sekunden Ablauf-Puffer, ein gemerktes „brauchbar" kann
  also nie ein inzwischen abgelaufenes Token verdecken. Aus dem offenen Teil von PR #539.
- **Compose-Ausgabe wird wirklich entschärft, nicht nur einzeilig gemacht.** `scrub_log`
  entfernt Steuerzeichen gegen Log-Injection — Geheimnisse maskiert erst `redact_logs`.
  Und in der HTTP-Antwort stand die Ausgabe ohnehin ungefiltert: der Weg nach außen war
  offen, während der Log als dicht galt. Betrifft Start, Stopp und Rebuild einer App.

### Deployment
- Orchestrator-Neustart.

---

## [1.166.2] — 2026-08-08

### Fixed
- **Eine unterbrochene Aufgabe konnte sich endlos selbst fortsetzen.** Wird ein laufender
  Agenten-Lauf von einem Neustart unterbrochen, nimmt die Plattform ihn als neue Aufgabe
  wieder auf — die Fortsetzung kann aber selbst unterbrochen werden, und ihre Fortsetzung
  wieder. Jeder Anlauf beginnt bei null und kostet voll: bei mehreren Deployments
  hintereinander lief EIN Plan-Block fünfmal komplett durch (16:33 → 16:37 → 16:42 →
  16:45 → 16:51), rund 14 USD statt knapp vier. Nach **drei** Fortsetzungen wird jetzt
  nicht mehr automatisch neu gestartet, sondern der Besitzer bekommt eine Meldung mit
  hoher Priorität.

### Deployment
- Orchestrator-Neustart.

---

## [1.166.1] — 2026-08-07

### Fixed
- **Die Container liefen in UTC, während der Host längst lokal tickte.** In den Logs stand
  21:31, im Haus war es 23:31 — und ein Agent, der in seiner Shell `date` aufrief, bekam
  eine Uhrzeit zwei Stunden daneben. Der Orchestrator bekommt jetzt `TZ` (Standard
  `Europe/Berlin`, über `.env` änderbar), jeder Agent-Container **seine eigene** Zone
  (Erreichbarkeit → Dienstzeit → UTC). Gerechnet wurde ohnehin zeitzonenbewusst; das
  macht die Anzeige ehrlich.

### Deployment
- `docker compose up -d orchestrator`, Agenten neu erstellen.

---

## [1.166.0] — 2026-08-07

### Fixed
- **Zeitpläne, die ein Agent selbst anlegt, laufen jetzt in SEINER Zeitzone.** Ohne Angabe
  galt UTC: der Agent nannte seinen Zeitplan „🌅 Täglicher Morgen-Report (07:00)", trug
  `0 7 * * *` ein — und im Kalender stand 09:00. Der Server setzt jetzt die Zone des
  Agenten ein (Erreichbarkeit, sonst Dienstzeit, sonst UTC); eine ausdrücklich gesetzte
  Zone bleibt unangetastet. Bestehende Zeitpläne ändern sich nicht.
- Das Werkzeug sagt es in allen Laufzeiten gleich: `timezone` leer lassen, außer man meint
  wirklich eine andere Zone. In den Codex-/Custom-LLM-Laufzeiten gab es den Parameter
  bisher gar nicht.

### Changed
- **Agenten sollen keinen eigenen Morgen- oder Abendplaner mehr anlegen** — die Plattform
  hat den Rhythmus seit 1.165.0. Bei DEV_Prod standen deshalb drei Morgenroutinen
  nebeneinander (06:00, 07:00, 08:00, 09:00). Die Basis-Anleitung sagt das jetzt.

### Deployment
- Orchestrator-Neustart, Agenten neu erstellen (neue Basis-Anleitung).

---

## [1.165.2] — 2026-08-07

### Fixed
- **Zweimal derselbe Titel im Tageskalender sah nach doppelter Arbeit aus.** Es war eine
  FORTSETZUNG: wird ein Lauf unterbrochen (z. B. Orchestrator-Neustart mitten in der
  Arbeit), nimmt die Plattform ihn als neue Aufgabe wieder auf. Der zweite Kasten trägt
  jetzt ein Wiederholungs-Symbol und „fortgesetzt" — ein Auftrag in zwei Abschnitten,
  nicht zwei Aufträge.

### Deployment
- Orchestrator-Neustart, Frontend-Rebuild.

---

## [1.165.1] — 2026-08-07

### Fixed
- **Im Tageskalender eines Agenten waren die Aufgaben-Kästen unlesbar schmal.** Der Plan
  bekam 26 % der Breite, die geplanten Läufe 34 % — für die Aufgaben blieben 36 %, geteilt
  durch die Zahl gleichzeitiger Läufe. Bei dreien war der Titel nach zwölf Zeichen zu Ende
  („[Scheduled] SAP M…"). Jetzt: Plan 22 %, Aufgaben 44 %, geplante Läufe 32 %, höchstens
  vier Spuren nebeneinander.
- **Zwei Aufgaben lagen übereinander im selben Kasten.** Die Spuren wurden aus den rohen
  Zeiten berechnet: eine Aufgabe, die in Sekunden durch ist, ist zeitlich ein Strich, wird
  aber mit einer Mindesthöhe gezeichnet — die nächste rutschte darunter. Die Spuren
  richten sich jetzt nach dem, was tatsächlich gezeichnet wird.
- **„[Scheduled]" und „[Proactive]" stehen nicht mehr im Kasten** — die Präfixe kosteten
  ein Viertel der Zeile und sagen dem Leser nichts.
- Kurze Aufgaben sind nur eine Zeile hoch; beim Überfahren stehen jetzt Titel, Zeitraum,
  Status, Dauer und Kosten im Tooltip.

### Deployment
- Frontend-Rebuild.

---

## [1.165.0] — 2026-08-07

### Added
- **Jeder Agent plant jetzt abends den nächsten Tag und schaut morgens nochmal drüber.**
  Bisher hatte genau EIN Agent diesen Rhythmus, weil er ihn sich im Chat selbst
  eingerichtet hatte — die anderen planten irgendwann mitten am Tag oder gar nicht, und
  der Montag blieb leer, weil sonntags niemand plante. Neu: `core/plan_rhythm.py` plus
  zwei Zeitpläne pro proaktivem Agenten (`[Rhythmus] Abendplanung`, `[Rhythmus]
  Morgencheck`), angelegt über dieselbe Zeitplan-Maschinerie wie alles andere.
  - Die Zeiten leitet jeder Agent aus SEINER Dienstzeit ab (Planung eine halbe Stunde vor
    Feierabend, Durchsicht zum Dienstbeginn); ohne Dienstzeit gelten 21:30 und 07:00.
  - **Sieben Tage die Woche** — nur wer `weekdays_only` gesetzt hat, macht Wochenende.
  - Der Morgencheck bekommt die Läufe der Nacht mit Ausgang vorgelegt, statt sie selbst
    zusammensuchen zu müssen.
  - Fällt ein Rhythmus-Lauf aus, holt der nächste proaktive Lauf im Abendfenster die
    Planung nach — die Phase hängt an jedem proaktiven Lauf mit im Prompt.
- **Windows kann Anwendungen jetzt genauso bedienen wie macOS.** Die Bridge liest dort den
  UI-Automation-Baum in derselben Form wie den AX-Baum von macOS; `find_element` und
  `wait_for_element` arbeiten unverändert weiter. Rollennamen werden tolerant verglichen,
  `button` trifft damit `AXButton` und `ButtonControl`.
- **Plan-Blöcke sind bearbeitbar, solange sie nur geplant sind** — Titel, Uhrzeit, Dauer
  und Präzisierung, direkt im Kalender. Der Auslöser wird dabei mitgezogen: ein
  verschobener Block läuft zur neuen Zeit, ein gestrichener gar nicht mehr.
- **Verantwortungsbereiche in Vorlagen eintragbar** — das Backend konnte es seit v1.157.0,
  die Oberfläche fehlte. Agent und Vorlage teilen sich jetzt EINEN Editor.
- **Entwicklung & Probezeit pro Agent sichtbar** (Fehlerquote, Plan-Treue, Bewertungen,
  Tendenz). Kosten und Laufzahl sagen nichts darüber, ob die Arbeit taugt.

### Changed
- **Geplante Läufe sehen im Kalender aus wie Plan-Blöcke** statt wie Haarstriche: Titel,
  Uhrzeit, lesbarer Takt („täglich 22:00", „alle 30 Min") — und ein Klick führt auf den
  Zeitplan, der dort hervorgehoben wird.
- Die Planungsanweisung steht nur noch EINMAL im Code: Rhythmus-Lauf und Sprachfront
  geben dieselbe weiter. Die kürzere Fassung der Stimme kannte die Uhrzeit-Pflicht nicht —
  die daraus entstandenen Blöcke standen im Kalender und liefen nie.
- „Plan mir den Tag" am Abend meint jetzt den nächsten Tag, nicht die letzte Stunde.

### Fixed
- Ein gelöschter Plan-Block ließ seinen Einmal-Zeitplan zurück, der weiter Arbeit anstieß.
- Titel und Uhrzeit eines bereits laufenden oder erledigten Blocks lassen sich nicht mehr
  überschreiben (409) — sonst stünde im Kalender ein Titel, unter dem etwas anderes lief.

### Deployment
- Orchestrator-Neustart (VERSION-Datei wird beim Start gelesen), Frontend-Rebuild.
- Agenten neu erstellen, damit die neue `AGENT.md` mit dem Arbeitsrhythmus ankommt.
- Windows-Bridge: `pip install uiautomation` bzw. neue Bridge-App; ohne das Paket sagt der
  Agent selbst, was zu tun ist. Die macOS-Bridge braucht nichts.

---

## [1.164.1] — 2026-08-07

### Fixed
- **Unter Windows versprach die Bridge Fähigkeiten, die sie dort nicht hat.** Der
  Bedienungshilfen-Baum (`ax_tree`, `find_element`, `wait_for_element`) ist macOS-only,
  wurde aber plattformunabhängig gemeldet. Die Bridge meldet jetzt nur noch, was die
  jeweilige Plattform wirklich kann.
- **Und der Sprachweg macht daraus kein „geht gar nicht":** Kommt der macOS-Fehler zurück,
  sagt er, dass er Elemente hier nicht selbst suchen kann, macht einen Screenshot und
  bittet um die Stelle — Klicken, Tippen und Tastenkombinationen funktionieren unter
  Windows genauso.

### Deployment
- Orchestrator-Neustart. Die Bridge-App muss NICHT neu installiert werden; die
  plattformehrliche Fähigkeitsmeldung wirkt beim nächsten Bridge-Update.

---

## [1.164.0] — 2026-08-07

### Fixed
- **„Ich kann die App nur öffnen, nicht in ihr navigieren."** Das stimmte nie — die Bridge
  beherrscht Klicken, Tippen, Tastenkombinationen, Scrollen und liest den
  Bedienungshilfen-Baum. Im Sprachweg fehlten aber **Suchen** und **Tasten**, und ohne
  Suche bleibt nur blindes Klicken auf geratene Koordinaten. Ergänzt: `find` (Element über
  den Bedienungshilfen-Baum), `wait` (auf ein Element warten), `key` (z. B. `cmd+f`),
  `scroll`. Dazu die Bedienkette im Werkzeug: öffnen → finden → klicken → tippen →
  nachsehen; und das ausdrückliche Verbot der falschen Ausrede.
- **Bildersuche gab zu früh auf.** Manche Treffer zeigen auf eine Webseite statt auf die
  Bilddatei — dann hieß es „keine direkten Bilder". Jetzt werden deutlich mehr Kandidaten
  geholt, und schlägt die Originaladresse fehl, wird das Vorschaubild genommen.

### Deployment
- Orchestrator-Neustart.

---

## [1.163.0] — 2026-08-07

### Added
- **Werkzeug-Nutzung im Sprachmodus ist sichtbar.** Jeder Aufruf erscheint rechts in der
  Spalte „Aufgaben & Aktivität": Name des Werkzeugs, die Eingabe (gekürzt) und das
  Ergebnis. Laufende Aufrufe drehen sich, fertige tragen einen Haken. Vorher lief alles
  unsichtbar ab und man musste dem gesprochenen Satz glauben — „ich denke immer, der hat
  dann nichts gemacht". Die Spur hängt am zentralen Einstiegspunkt, damit auch jedes
  künftige Werkzeug automatisch darin auftaucht.

### Deployment
- Orchestrator-Neustart, Frontend-Rebuild.

---

## [1.162.0] — 2026-08-07

### Added
- **Bildersuche im Sprachmodus** (`web_picture_search`) — Begriff rein, echte Treffer
  raus, die besten davon sofort auf dem Schirm. Schlüssellos über DuckDuckGo, nach
  demselben Muster wie die vorhandene Websuche. Bilder werden serverseitig durch dasselbe
  SSRF-Gate geholt wie bisher; tote Treffer werden übersprungen statt zu scheitern.

### Fixed
- **Der Agent erfand Bild-Adressen.** Er nannte Wikimedia-Links aus dem Gedächtnis, die es
  nie gab (400/404), und meldete dann ein Problem beim Bildserver. Das Werkzeug sagt jetzt
  ausdrücklich: Adressen nie selbst bilden, sondern aus einem Suchtreffer nehmen — und die
  Fehlermeldungen sagen, was als Nächstes zu tun ist, statt nur „ging nicht".

### Deployment
- Orchestrator-Neustart.

---

## [1.161.4] — 2026-08-07

### Fixed
- **Im Sprachmodus liessen sich keine Bilder anzeigen** („Bild konnte nicht geladen
  werden"). Die Ursache lag nicht beim Bildserver, sondern bei uns: unsere Abrufe gingen
  **ohne User-Agent** raus. Wikimedia und viele andere antworten darauf mit einem
  text/plain-Hinweis auf ihre Robot-Policy statt mit dem Bild — der Inhaltstyp passte
  dann nicht, und der Agent meldete ein technisches Problem. Abrufe nennen jetzt Namen
  und Kontaktadresse und fragen ausdrücklich nach Bildern. Die SSRF-Absicherung
  (IP-Pinning, Host-Header, Byte-Grenze) bleibt unverändert.

### Deployment
- Orchestrator-Neustart.

---

## [1.161.3] — 2026-08-07

### Fixed
- **Jeder gelaufene Zeitplan stand zweimal im Tag**: als grünes Band rechts (die
  Vorhersage aus dem Zeitplan) und als Balken in der Mitte (der tatsächliche Lauf). Bei 37
  Vorhersagen und 31 Läufen an einem Tag war die rechte Spalte deshalb zugepflastert.
  Bänder bleiben jetzt nur für das, was noch aussteht — sobald der Lauf existiert, zählt
  der Balken.

### Deployment
- Frontend-Rebuild.

---

## [1.161.2] — 2026-08-07

### Fixed
- **Im Hellmodus stand im Kalender nichts.** Die neuen Plan-Blöcke und die Bänder der
  geplanten Läufe hatten helle Schrift — auf dunklem Grund lesbar, auf hellem unsichtbar.
  Beide Modi sind jetzt bedient.
- **Blöcke waren zu kurz geplant.** Der Agent schätzte in Zehn-Minuten-Scheiben; im
  Kalender wurden daraus unlesbare Striche, und der erste Überzug macht den Rest des Tages
  wertlos. Jeder Block hat jetzt **mindestens 15 Minuten** — auch als Vorgabe, wenn der
  Agent gar keine Dauer mitgibt. Dazu die Ansage im Prompt: lieber ein ehrlicher
  45-Minuten-Block als drei optimistische Zehner.

### Deployment
- Orchestrator-Neustart, Frontend-Rebuild.

---

## [1.161.1] — 2026-08-07

### Fixed
- **Geplante Läufe lagen exakt übereinander.** Ein voller Tag bringt bei einem aktiven
  Agenten 35 Läufe, davon mehrere zur selben Minute (morgen dreimal um 04:00) — als Bänder
  auf derselben Spur ergab das einen unlesbaren Klumpen. Was sich zeitlich beißt, steht
  jetzt nebeneinander (bis zu drei Spalten, 20-Minuten-Fenster).

### Deployment
- Frontend-Rebuild.

---

## [1.161.0] — 2026-08-07

### Fixed
- **Zukünftige Tage sahen im Kalender leer aus**, obwohl dort Läufe anstanden — für den
  kommenden Montag rechnete der Server 38 geplante Läufe aus, die Ansicht zeigte sie aber
  als 8-Pixel-Rauten am linken Rand, seit der Planspur zusätzlich verdeckt. Jetzt schmale,
  beschriftete Bänder mit Uhrzeit auf einer eigenen Spur rechts. Der Tag hat damit drei
  Spuren: Plan links, erledigte Aufgaben in der Mitte, geplante Läufe rechts.
- **Die Tagesplanung war auf „nur werktags" vorbelegt** — am Wochenende plante der Agent
  deshalb still gar nichts. Neue Voreinstellung: jeden Tag; „nur werktags" bleibt als
  Häkchen für den, der es will.

### Deployment
- Orchestrator-Neustart, Frontend-Rebuild.

---

## [1.160.5] — 2026-08-07

### Fixed
- **Beim Dazwischenreden stand die Reihenfolge im Sprach-Gespräch auf dem Kopf.** Der
  laufende Zwischenstand wurde als kursive Blase unter der Liste gezeigt und nie
  aufgelöst — sobald der Zug als richtige Nachricht einsortiert war, klebte die alte
  Blase weiter unten fest, während neue Nachrichten darüber erschienen. Der Zwischenstand
  verschwindet jetzt, sobald der Zug in der Liste steht, und wird nie doppelt gezeigt.

### Deployment
- Frontend-Rebuild.

---

## [1.160.4] — 2026-08-07

### Fixed
- Die Überschrift der Tagesplan-Karte stand doppelt (oben in der Karte und nochmal als
  allgemeine Bildunterschrift darunter).

### Deployment
- Frontend-Rebuild.

---

## [1.160.3] — 2026-08-07

### Fixed
- **„Kein Kalender."** Der Tagesplan wurde als Karte ins Gesprächsfenster geschickt, aber
  nie gezeichnet — er landete in der Datei-Zeile, und der Nutzer sah nur „Datei". Jetzt
  steht er als echte Liste da: Uhrzeit, Titel, Dauer, Zustand (geplant/läuft/erledigt/
  gestrichen), hohe Priorität hervorgehoben.
- **Der Plan wurde in UTC vorgelesen.** Der Agent sagte „15:20", im Kalender stand 17:20.
  Maßgeblich ist jetzt die konfigurierte Zeitzone (Erreichbarkeit, sonst Dienstzeit).

### Deployment
- Orchestrator-Neustart, Frontend-Rebuild.

---

## [1.160.2] — 2026-08-07

### Fixed
- **Jeder Plan-Block stand dreifach im Kalender**: als Block links, als Aufgabenbalken
  rechts und als Zeitplan-Marke — seit die Blöcke über echte Zeitpläne laufen. Der Block
  ist die Wahrheit; alles mit `[Plan]` wird daneben ausgeblendet. Klick auf den Block
  führt weiterhin zur Aufgabe.
- **Verpasste Blöcke starteten alle gleichzeitig.** Wurden fünf Blöcke nachträglich scharf
  gestellt und lagen ihre Zeiten in der Vergangenheit, feuerten sie im selben Takt — auf
  dem Pi brachte das die Claude-CLI zum Absturz (`exit -6`). Nachgeholt wird jetzt
  gestaffelt, drei Minuten Abstand je Block.

### Deployment
- Orchestrator-Neustart, Frontend-Rebuild.

---

## [1.160.1] — 2026-08-07

### Fixed
- **Blöcke, deren Zeitplan schon gefeuert hatte, blieben auf „geplant"** — die
  Rückmeldung griff nur für Läufe ab jetzt. Der Scheduler erkennt sie nachträglich an
  ihrem gelaufenen Zeitplan, hängt die zugehörige Aufgabe an und setzt den Zustand.

### Deployment
- Orchestrator-Neustart.

---

## [1.160.0] — 2026-08-07

### Fixed
- **Der Kalender zeigte ewig „geplant"** — auch als der Block längst lief oder fertig war.
  Es fehlte die Rückmeldung: Feuert der Zeitplan eines Blocks, wandert der Block jetzt auf
  **läuft** und merkt sich seine Aufgabe; ist die Aufgabe terminal, steht er auf
  **erledigt**. Ohne das war der Plan eine Momentaufnahme vom Morgen.

### Added
- **Plan-Blöcke sind anklickbar**, sobald sie gelaufen sind — der Klick führt zur Aufgabe
  mit Ergebnis, Schritten und erzeugten Dateien. Laufende Blöcke pulsieren, erledigte
  stehen durchgezogen statt gestrichelt.

### Deployment
- Orchestrator-Neustart, Frontend-Rebuild.

---

## [1.159.2] — 2026-08-07

### Fixed
- **Die Auslöser für vorhandene Plan-Blöcke wurden angelegt und fielen sofort wieder weg** —
  die Selbstheilung schrieb sie in die Sitzung, ohne zu committen. Der Kalender zeigte
  weiter „geplant", und nichts startete.

### Deployment
- Orchestrator-Neustart.

---

## [1.159.1] — 2026-08-07

### Fixed
- **Bereits geplante Blöcke liefen weiterhin nicht.** Der Auslöser entstand nur beim
  Schreiben eines neuen Plans — was vorher im Kalender stand, hatte keinen und wäre nie
  gestartet. Der Scheduler stellt jetzt bei jedem Takt sicher: **Block mit Uhrzeit ⇒
  Zeitplan**, auch nachträglich. Eine verpasste Zeit wird nachgeholt statt still verfallen.

### Deployment
- Orchestrator-Neustart.

---

## [1.159.0] — 2026-08-07

### Fixed
- **Der Tagesplan stand im Kalender — und nichts passierte.** Ein Block war reine Anzeige:
  der Agent nahm sich 16:05 etwas vor, und um 16:05 geschah nichts. Jeder Block mit
  Uhrzeit legt jetzt einen **Einmal-Zeitplan** an und läuft damit über genau die
  Maschinerie, die Zeitpläne seit jeher ausführt — kein zweiter Auslöser daneben. Blöcke
  ohne Uhrzeit bleiben Notizen für den nächsten proaktiven Lauf; wird umgeplant,
  verschwinden die Zeitpläne der gestrichenen Blöcke mit.
- **Einmal-Läufe feuerten im 30-Sekunden-Takt weiter.** Nach dem Auslösen stand
  `next_run_at` sofort wieder in der Vergangenheit (Intervall 0). Sie schalten sich jetzt
  ab — die Regel galt bisher nur für Meeting-Zeitpläne.
- **Die Agentenkachel meldete „kein Auftrag", obwohl elf Verantwortungsbereiche
  hinterlegt waren.** Die Liste baut ihre Felder aus dem Metrik-Wörterbuch, nicht aus dem
  Antwortmodell — dort fehlte das Feld.

### Deployment
- Orchestrator-Neustart (neue Spalte wird beim Start ergänzt).

---

## [1.158.0] — 2026-08-07

### Fixed
- **„Ich richte das jetzt ein" — und nichts geschah.** Fragte man den Agenten im Gespräch,
  seine Tages- oder Wochenplanung zu machen, kündigte er es an und lieferte nichts: der
  Sprachfront konnte den Plan zwar **lesen**, aber weder schreiben noch die Arbeit abgeben.
  Neues Werkzeug `plan_my_day` — die Stimme plant **nicht selbst**, sondern stößt den
  Agenten als echte Aufgabe an. Die taucht im Aufgaben-Panel auf, läuft mit seinen eigenen
  Werkzeugen und landet über `plan_day` im Kalender.
- **Regel gegen Ankündigen ohne Ausführen** im Sprach-Prompt: Sätze wie „ich richte das
  ein" sind nur erlaubt, wenn im selben Zug das Werkzeug läuft; „eingetragen" oder
  „erledigt" erst, wenn ein Werkzeug es bestätigt hat.

### Changed
- Die Schreibregeln des Tagesplans liegen jetzt in `core/day_plan_store` — API und
  Agentenweg benutzen dieselbe Definition, statt sie zu doppeln.

### Deployment
- Orchestrator-Neustart.

---

## [1.157.3] — 2026-08-07

### Fixed
- **Das vergrößerte Sprach-Overlay ließ seinen Inhalt oben kleben.** Die drei Spalten
  (Gespräch · Präsenz · Aufgaben) hingen an festen Bildschirmprozenten — zog man das
  Fenster größer, wuchs nur der Rahmen und darunter blieb eine leere Fläche. Sobald eine
  eigene Größe gesetzt ist, füllen Inhalt und Spalten das Fenster; gescrollt wird in den
  Spalten, nicht im Rahmen.

### Deployment
- Frontend-Rebuild.

---

## [1.157.2] — 2026-08-07

### Fixed
- **Im Sprach-Transkript stand mitten im Satz ein einsames „n"** („n1. Backlog-Priorisierung
  n - OAuth Re-Auth 500"). Der Text erreichte die Engine mit literalen `\n`-Folgen statt
  echter Umbrüche — sprechen lässt sich ein Backslash nicht, er fiel weg, das „n" blieb.
  Die eine Stelle, durch die aller Text zur Engine geht, wandelt literale `\n`, `\r\n`
  und `\t` jetzt in echte Zeichen um.

### Added
- **Das Sprach-Overlay lässt sich vergrößern.** Ziehgriff unten rechts, Größe bleibt
  gemerkt; Doppelklick auf die Kopfzeile schaltet Vollbild um. Vorher war das Fenster
  fest, und lange Zusammenfassungen scrollten in einer schmalen Spalte, während der halbe
  Bildschirm leer blieb.

### Deployment
- Orchestrator-Neustart, Frontend-Rebuild.

---

## [1.157.1] — 2026-08-07

### Fixed
- **Die neuen Einstellungen waren unsichtbar.** Verantwortungsbereiche, Vertretung,
  Dienstzeit und Abwesenheit lagen hinter einem zugeklappten Aufklapper namens
  „Prompt & Anweisungen" — ausgerechnet die Einstellung, die entscheidet, OB ein Agent
  arbeitet. Der Aufklapper heißt jetzt **„Auftrag, Vertretung & Zeiten"**, zeigt zugeklappt
  den Zustand („kein Auftrag · keine Vertretung · rund um die Uhr"), färbt sich bei
  fehlendem Auftrag amber und **öffnet sich dann von selbst**.

### Deployment
- Frontend-Rebuild.

---

## [1.157.0] — 2026-08-07

Vom Werkzeug zum Mitarbeiter, zweite Hälfte: Ausfall, Vertretung, Eskalation, eigene
Dienstzeit, Priorisierung, Abwesenheit, Einarbeitung und die Frage, ob er besser wird.
Fünf dieser Punkte hingen an demselben fehlenden Begriff — deshalb gibt es EINEN
Dienstzustand und EINE Eskalationskette statt fünf Insellösungen.

### Added
- **Dienstzustand eines Agenten** (`core/agent_duty.py`) — abgeleitet aus vorhandenen
  Signalen (Zustand, Warteschlange, Watchdog): `ok · overloaded · blocked · down ·
  off_duty`. Der Scheduler entscheidet danach, ob ein Lauf überhaupt startet, und der
  Agent bekommt seine eigene Lage in den Prompt.
- **Vertretung bei Ausfall** — Vertreter pro Agent wählbar, sonst Team-Lead. Hängt oder
  scheitert ein Agent, wandern seine offenen Todos mit Herkunftsvermerk zum Vertreter
  und du bekommst eine Meldung. Ein Vertreter, der selbst nicht läuft, wird übersprungen;
  ist niemand da, ist die Meldung entsprechend deutlich.
- **Eskalation bei Schweigen** — bleiben zwei Rückfragen länger als zwölf Stunden
  ungelesen, geht es an den Team-Lead, sonst an die Administration.
- **Eigene Dienstzeit des Agenten** (bisher gab es nur die Erreichbarkeit des Menschen) —
  außerhalb läuft kein proaktiver Lauf. Überlast (volle Warteschlange) sagt er selbst an,
  statt still weiterzustapeln.
- **Abwesenheit des Ansprechpartners** — im Urlaubsfenster stellt der Agent keine
  Rückfragen, sondern sammelt sie und legt sie gebündelt vor.
- **Priorisierung** — Blöcke im Tagesplan erben die Priorität ihres Verantwortungsbereichs
  und werden danach sortiert; dazu eine Konfliktregel im Prompt.
- **Vorlagen bringen Verantwortungsbereiche mit** — ein Agent aus einer Vorlage startet
  mit Auftrag statt bei null.
- **Entwicklungs-Kennzahl** `GET /analytics/agents/{id}/development` — Fehlerquote im
  Vergleich zweier Zeiträume, Bewertungstrend, Plan-Treue (geplant vs. erledigt) und der
  Probezeit-Stand nach sieben Tagen.
- **Bildschirm-Regeln in der gemeinsamen Anleitung** — am Nutzerbildschirm ausschließlich
  `computer_*`, Elemente über den Bedienungshilfen-Baum, nach jedem Klick nachsehen.

### Fixed
- **Am Telefon sagte der Agent erst auf Nachfrage, dass ihm sein Auftrag fehlt.** Die
  Begrüßung wird getrennt vom Systemprompt gebaut und übertönte den Hinweis — jetzt steht
  er im ersten Satz.

### Deployment
- Orchestrator-Neustart (neue Spalten werden beim Start ergänzt), Frontend-Rebuild,
  Agent-Image + Agenten erneuern.

---

## [1.156.0] — 2026-08-07

### Changed
- **Ein proaktiver Lauf ohne Auftrag startet gar nicht mehr.** Fehlt die Einrichtung oder
  fehlen die Verantwortungsbereiche, kann der Lauf nichts zustande bringen — bisher lief er
  trotzdem, kostete Modell-Zeit und meldete brav „nichts zu tun" (beim Kunden 493 Läufe,
  51 USD, null Ergebnis). Jetzt wird er übersprungen und stattdessen **der Besitzer
  benachrichtigt** („<Agent> wartet auf seinen Auftrag", mit Link in die Einstellungen),
  gedrosselt auf einmal pro 12 Stunden.

### Added
- **Ausrufezeichen auf der Agentenkachel**, wenn der Agent keinen Auftrag hat — man sieht es
  in der Übersicht statt erst im Log. Drei Zustände: nicht eingerichtet (Zahnrad),
  eingerichtet aber ohne Verantwortungsbereiche (Warndreieck), fertig (grüner Haken).
- **Einrichtung per Sprache** — der Sprachfront konnte bisher nach dem Auftrag fragen, die
  Antwort aber nicht sichern. Er hat jetzt `complete_onboarding` als eigenes Werkzeug und
  schreibt Rolle, Grenzen und Daueraufgaben direkt weg: „Eingerichtet. Ich kümmere mich ab
  jetzt um …".

### Deployment
- Orchestrator-Neustart, Frontend-Rebuild, Agent-Image + Agenten erneuern.

---

## [1.155.0] — 2026-08-07

### Fixed
- **Einrichtung („Onboarding") hatte zwei widersprüchliche Stände.** In der Datenbank
  (`config['onboarding_complete']`) wurde beim Anlegen ein Wert gesetzt und **nie wieder
  geändert**; parallel pflegte der Agent eine Kopfzeile in `/workspace/knowledge.md`. Beim
  Kunden stand in der DB „fertig" und in der Datei „nicht fertig" — die Agenten hielten
  darum jeden proaktiven Lauf an, während die Oberfläche sie als eingerichtet zeigte:
  493 Läufe, 51 USD, kein einziges Arbeitsergebnis. Ab jetzt gilt die Datenbank, und sie
  wird über ein echtes Werkzeug gesetzt.

### Added
- **Werkzeug `complete_onboarding`** in allen vier Laufzeiten (Claude Code über MCP, Codex
  und Custom-LLM über `definitions.py`/`api_client.py`, Kern-Werkzeugsatz des Chats). Es
  schreibt Rolle, Grenzen — und **jede genannte Daueraufgabe direkt als
  Verantwortungsbereich**. Das Einrichtungsgespräch erzeugt damit die Struktur, aus der
  sich der Agent anschließend seinen Tag baut. Mindestens eine Daueraufgabe ist Pflicht,
  bestehende Bereiche werden ergänzt statt überschrieben.
- **Der Einrichtungsstand steht in jedem Prompt** — im proaktiven Lauf, in Chat und Tasks
  (über Identität bzw. das gemeinsame Kontext-Bündel) und im Sprachfront. Ein Agent ohne
  Auftrag hält nicht mehr still an und meldet auch nicht „nichts zu tun", sondern **fragt
  aktiv** nach Rolle, Daueraufgaben und Grenzen — im Takt der Meldebremse so lange, bis er
  eine Antwort hat.
- **Am Telefon** hört man dann nicht „wie kann ich helfen?", sondern „ich kann dir gern
  helfen — sag mir zuerst, wofür du mich brauchst"; die Antwort wird sofort gesichert.

### Deployment
- Orchestrator-Neustart, **Agent-Image neu bauen + Agenten erneuern** (Werkzeug und
  Statusabfrage stecken im Agenten-Code).

---

## [1.154.1] — 2026-08-07

### Fixed
- **Gestoppte Agenten wurden weiter proaktiv angesteuert.** Der Zeitplan feuerte stündlich
  weiter, obwohl niemand da war, der ihn ausführen konnte — beim Kunden hatten zwei
  gestoppte Agenten so über vier Wochen **337 fehlgeschlagene Läufe** angesammelt, ohne dass
  es jemandem auffiel. Der Scheduler prüft den Agentenzustand jetzt VOR dem Auslösen,
  überspringt alles außer RUNNING/IDLE/WORKING, rückt den Zeitplan regulär weiter (kein
  Nachhol-Schwall beim Start) und protokolliert die Auslassung.

### Deployment
- Orchestrator-Neustart.

---

## [1.154.0] — 2026-08-07

Der Weg vom Werkzeugkasten zum Mitarbeiter: Identität, Auftrag und ein sichtbarer Tagesplan —
und zwar in JEDER Laufzeit. Dazu M365 nur noch lesend und der MCP-Login über Microsoft.

### Added
- **Verantwortungsbereiche pro Agent** — Daueraufträge mit Takt (täglich/wöchentlich/monatlich/
  laufend), Priorität und Präzisierung. Ein Bereich ist bewusst kein Todo: er kehrt wieder und
  wird nie „fertig". Der proaktive Lauf leitet daraus in STEP 1 die konkreten Aufgaben des Tages
  ab, statt auf Arbeit zu warten, die jemand angelegt hat. Definition, Validierung und
  Prompt-Darstellung liegen an einer Stelle (`app/core/responsibilities.py`).
- **Sichtbarer Tagesplan** — neue Tabelle `agent_plan_items` plus Werkzeuge `plan_day` /
  `get_day_plan`. Der Plan stand bisher nur in `/workspace/.agent_state.md` im Container und war
  damit weder anzeigbar noch korrigierbar. Jetzt zeigt der Kalender die geplanten Blöcke
  gestrichelt neben den erledigten Balken; ein Block lässt sich per Klick streichen, und der
  nächste Lauf hält sich daran. Vorgenommenes ohne feste Uhrzeit steht darunter statt zu
  verschwinden.
- **Tagesplanung am Morgen als Klick** — feste Uhrzeit (optional nur werktags) neben dem
  Intervall-Takt. Legt einen zweiten „[Proactive]"-Zeitplan an, damit der Basis-Prompt samt
  Bereichen und Tagesplan greift; Abwählen entfernt ihn wieder.
- **Microsoft-SSO als Anmeldeweg für den MS-Graph-MCP-Server** — `/oauth/authorize` schickt ohne
  Sitzung direkt in die Entra-Anmeldung und kommt zur offenen Freigabe zurück. Wer in OpenWebUI
  bereits mit Microsoft angemeldet ist, sieht keine Maske mehr. Zustimmung wird pro (User,
  Client) 90 Tage gemerkt und fällt weg, sobald die Microsoft-Verbindung getrennt wird.
- **Plattformweiter Nur-Lesen-Zwang für Microsoft** (Standard AN) — sperrt Schreib-Werkzeuge für
  M365/Graph UND den on-prem-Exchange-Connector, unabhängig davon, was pro Agent eingestellt ist.

### Fixed
- **Agenten kannten ihren eigenen Namen nicht.** Die gemeinsame Anleitung enthielt weder Namen
  noch Rolle, und der Custom-LLM-Weg las die Datei überhaupt nicht. Jetzt steht die Identität in
  der einen gerenderten Vorlage (alle vier Schreibstellen geben Name + Rolle mit), und
  `get_identity_context()` hängt sie im Custom-LLM-Weg an den Systemprompt.
- **Codex hat die Anleitung nie gelesen.** Wir schrieben `/workspace/AGENT.md`, die Codex-CLI
  liest per Konvention `AGENTS.md` — jede Verbesserung lief an Codex-Agenten vorbei.
  `instructions_paths()` liefert jetzt pro Modus alle Dateien, die dieser Harness wirklich liest.
- **Der Sprachweg vergaß alles zwischen zwei Anrufen.** Er schrieb nach jedem Zug Erinnerungen
  weg, las sie aber nie zurück, und jeder Anruf begann eine neue Sitzung. Jetzt lädt er beim
  Start das Gedächtnis (ohne Zugangsdaten) und fällt auf das letzte Gespräch zurück; dauerhafte
  Wünsche wie „du heißt ab jetzt Luna" sichert er sofort per `save_memory`.
- **Der Custom-LLM-Chat und der leichte Task-Zweig luden das Gedächtnis nicht** — die
  Task-Laufzeit tat es längst. Beide Wege holen es jetzt ebenfalls.

### Deployment
- Orchestrator-Neustart (neue Tabelle wird beim Start angelegt), Frontend-Rebuild,
  **Agent-Image neu bauen + Agenten erneuern** (Identität und Werkzeuge stecken im Agenten-Code;
  Codex-Agenten einzeln nacheinander wegen der geteilten Token-Familie).

---

## [1.153.7] — 2026-08-06

### Changed
- **Die globale Activity-Übersicht (mehrere Agenten nebeneinander) bekam bisher nicht
  dieselbe Lesbarkeits-Politur wie der neue Einzelagenten-Kalender.** Zeilen sind jetzt
  höher (48px → 64px), Balken breiter (10px → 34px Mindestbreite) und zeigen Titel +
  Startzeit direkt auf dem Balken statt nur beim Hovern — auch bei dicht getakteten
  Zeitplänen bleiben einzelne Läufe als eigene, anklickbare Blöcke erkennbar statt als
  Haarrisse.

### Deployment
- Frontend (Rebuild).

## [1.153.6] — 2026-08-06

### Fixed
- **Ein Task, der vor Mitternacht begann und über den Tageswechsel hinaus lief (z. B. noch
  aktiv), konnte im Tageskalender eines Agenten mit negativer Position über dem sichtbaren
  Raster landen** — dadurch wirkte er kürzer, als er wirklich war, statt korrekt am
  Tagesbeginn (00:00) zu starten. Start-/Endzeit werden jetzt auf den sichtbaren Tag
  begrenzt, bevor Position und Höhe berechnet werden.

### Changed
- **Stunden-Zeilen im Tageskalender eines Agenten deutlich größer** (56px → 88px pro
  Stunde), damit die tatsächliche Länge von Aufgaben klar erkennbar ist statt gestaucht.

### Deployment
- Frontend (Rebuild).

## [1.153.5] — 2026-08-06

### Fixed
- **KRITISCH: Jede Codex-Aufgabe (Chat wie proaktiv) schlug sofort mit
  „NameError: name 'self' is not defined" fehl** — noch bevor ein Werkzeug lief oder das
  Modell etwas ausgegeben hatte. Gefunden beim Untersuchen, warum der neue
  Activity-Kalender bei einem Agenten nur durchgehend rote (fehlgeschlagene) Balken
  zeigte: `_stream_jsonl()` in `codex_runner.py` ist eine Modul-Funktion (keine Methode),
  enthielt aber `self.log_publisher...` — kopiert aus dem benachbarten `collect_stderr`,
  das als Closure INNERHALB einer Methode `self` legitim erreichen kann. Jetzt bekommt
  die Funktion den `log_publisher` explizit als Parameter übergeben.

  Bestand seit einem früheren Commit („Lebenszeichen an die CLI-Ausgabe haengen"), nicht
  durch die heutige Arbeit verursacht — aber dadurch gefunden, weil der Kalender genau
  das zeigen soll: was tatsächlich passiert, nicht nur was geplant war. Der bestehende
  Test dazu prüfte nur, ob der Text „last_activity_at" im Quellcode vorkommt (das hätte
  den Fehler nie gefangen — der Text stand ja da, nur im falschen Scope). Neuer Test
  führt die Funktion jetzt wirklich aus.

### Deployment
- Agent-Image (Rebuild) + alle Codex-Agenten neu erstellen (Update-Button je Agent).

## [1.153.4] — 2026-08-06

### Fixed
- **Tageskalender eines Agenten wirkte bei häufig feuernden Zeitplänen wie eine massive
  Wand.** Kurze, zeitlich direkt aufeinanderfolgende Aufgaben (z. B. ein stündlicher
  Feedback-Monitor) hatten keinen Abstand zueinander und verschmolzen optisch zu einem
  einzigen Block. Jetzt bekommt jeder Block einen kleinen Abstand nach oben und unten, so
  dass auch dicht getaktete Zeitpläne als einzelne, unterscheidbare Läufe erkennbar bleiben.

### Deployment
- Frontend (Rebuild).

## [1.153.3] — 2026-08-06

### Fixed
- **Tageskalender eines Agenten war unnötig kurz abgeschnitten** — feste Höhe von 560px
  ließ auf größeren Bildschirmen viel ungenutzten Platz darunter. Nutzt jetzt die
  verfügbare Bildschirmhöhe (bis zu ~100vh − 260px).
- **„Kalender"-Unterreiter stand ganz rechts, hinter „Live" und „Verlauf"** — jetzt gleich
  nach „Todos" einsortiert.

### Deployment
- Frontend (Rebuild).

## [1.153.2] — 2026-08-06

### Changed
- **Der Kalender eines einzelnen Agenten ist jetzt eine echte vertikale Tagesansicht**
  (Stunden von oben nach unten gestapelt, Aufgaben als Blöcke mit lesbarem Titel direkt
  auf dem Block — wie ein gewöhnlicher Kalender-Tagesansicht) statt der horizontalen
  24h-Leiste. Die horizontale Leiste (ein schmaler Streifen pro Agent) bleibt für den
  globalen Menüpunkt „Activity" bestehen, wo mehrere Agenten nebeneinander verglichen
  werden — dort ist sie das richtige Format, nur nicht für die Ansicht eines einzelnen
  Agenten. Sich überschneidende Aufgaben werden nebeneinander in eigenen Spalten gelegt.
  Beim Öffnen springt die Ansicht automatisch zur aktuellen Uhrzeit (heute) bzw. zur
  ersten Aufgabe des Tages.

### Deployment
- Frontend (Rebuild).

## [1.153.1] — 2026-08-06

### Fixed
- **Task-Balken auf der Activity-Zeitleiste kaum sichtbar.** Kurze Aufgaben waren nur ein
  Haarriss ohne erkennbare Grenzen. Jetzt: Mindestbreite von 10px unabhängig von der
  tatsächlichen Dauer, dazu ein eigenes Hover-Tooltip (Titel, Zeitfenster, Status,
  Dauer/Kosten) statt der schwer auffindbaren nativen Browser-Anzeige.

### Added
- **Kalender-Unterreiter im bestehenden Activity-Tab jedes Agenten.** Der neue globale
  Menüpunkt „Activity" (v1.153.0) und der bestehende Activity-Tab auf der
  Agenten-Detailseite (Verlauf eines einzelnen Agenten) heißen zufällig gleich, sind aber
  unterschiedliche Ansichten — wer im Agenten-Tab nach dem neuen Tageskalender sucht, fand
  dort nichts. Jetzt zeigt ein neuer Unterreiter „Kalender" dieselbe Tagesleiste, gefiltert
  auf genau diesen Agenten.

### Deployment
- Frontend (Rebuild).

## [1.153.0] — 2026-08-06

### Added
- **Neuer Menüpunkt „Activity" — Tageskalender aller Agenten.** Eine Zeile pro Agent mit
  geplanten Terminen (aus den Zeitplänen, Cron/Interval vorausberechnet) als Rauten und
  tatsächlich gelaufenen Aufgaben als farbige Balken. Datumsnavigation vor und zurück
  funktioniert für vergangene und zukünftige Tage gleichermaßen. Klick auf einen Balken
  führt in die bestehende Aufgaben-Zeitreise. Neuer Endpunkt `GET /activity/timeline`.
- **Erreichbarkeit des Ansprechpartners** — neues Feld in den Proaktiv-Einstellungen
  jedes Agenten (Start-/Endzeit + Zeitzone). Der Agent respektiert dieses Zeitfenster bei
  der Entscheidung, ob er sich proaktiv melden darf.
- **trigger_create/list/toggle/delete** — Agenten können sich jetzt selbst auf Ereignisse
  (Webhooks) einrichten statt nur auf Zeitplänen zu pollen. Der Backend-Teil existierte
  schon, es fehlte nur die Werkzeug-Schicht — jetzt in beiden Laufzeiten (Claude Code
  MCP-Server + Codex/Custom-LLM) verdrahtet.
- **Serverseitige Meldebremse**: `notify_user(is_checkin: true)` ist auf höchstens einmal
  pro Halbtag pro Agent gedeckelt (Redis-gestützt), damit nicht mehrere proaktive Agenten
  gleichzeitig bei Leerlauf Alarm schlagen.

### Changed
- **Proaktiv-Kern-Prompt umgebaut.** War bisher auf Entwicklerarbeit zugeschnitten
  (GitHub-Issues, Git-Hygiene) und lief unverändert bei jedem Agenten, egal welche Rolle.
  Neuer Kern: Lage sichten, Tag planen, priorisieren, bei Leerlauf vorschlagen statt
  fragen, Tag/Nacht-Regel, Selbstorganisation. Der GitHub-Workflow ist in die
  „Zusätzliche Anweisungen" der drei Entwickler-Agenten umgezogen.

### Fixed
- Ein während dieser Arbeit gefundener, vorbestehender Fehler: `notify_user` vertraute der
  vom Client mitgeschickten `agent_id` statt der authentifizierten Identität — geschlossen,
  bevor die neue Meldebremse ihn zu einer gezielten Sperre gegen einen anderen Agenten
  hätte ausnutzbar machen können.

### Deployment
- Orchestrator (Restart) + Frontend (Rebuild). Agent-Container-Update nötig, damit die
  neuen `trigger_*`-Werkzeuge und der neue Kern-Prompt bei laufenden Agenten ankommen.

## [1.152.2] — 2026-08-06

### Fixed
- **Telegram-Sperre durch zu häufiges Bearbeiten** (#528). Der Live-Takt war mit 1,3 Sekunden viel zu gierig — bei einem langen Werkzeuglauf ergab das bis zu 46 Bearbeitungen pro Minute in **einem** Chat. Telegram zählt Bearbeitungen wie Nachrichten und sperrt dann („Flood control exceeded").

  Schlimmer noch: Nach einem Fehler wurde der Zeitstempel **nicht** gesetzt, also griff die Drosselung nicht mehr und der nächste Schleifendurchlauf versuchte es sofort erneut. Im Protokoll sichtbar als 50+ Zeilen in 25 Sekunden mit rückwärts laufendem Zähler (56 → 55 → …).

  Jetzt: 5 Sekunden Takt, die Uhr wird **auch im Fehlerfall** gestellt, und eine gemeldete Wartezeit wird gelesen und eingehalten — bis dahin ruht das Live-Bild für diesen Chat. Die Antwort selbst kommt unabhängig davon an.

### Deployment
- Orchestrator (Restart).

## [1.152.1] — 2026-08-06

### Fixed
- **Telegram-Antworten kamen als Textklumpen.** Der Agent schreibt Markdown (`**fett**`, `## Titel`, Listen), die Nachricht ging aber ohne Formatierung raus — also standen die Sternchen sichtbar da und alles klebte zusammen.

  Der Text wird jetzt aufbereitet: Überschriften und Fettung als Auszeichnung, Aufzählungen mit Punkten, Code gesetzt, überzählige Leerzeilen zu einer normalisiert.

  Bewusst **HTML** als Telegram-Modus, nicht Markdown: Dort zerlegt jedes lose `*` oder `_` im Agententext die Nachricht. Hier wird zuerst alles escaped, erst danach werden erkannte Auszeichnungen zu Tags — `5 * 3 = 15` und `a_b_c` bleiben unversehrt, und Agententext kann keine eigenen Tags einschleusen. Lehnt Telegram das Ergebnis doch ab, geht die Nachricht unformatiert raus statt gar nicht.

### Deployment
- Orchestrator (Restart).

## [1.152.0] — 2026-08-06

### Changed
- **Die Arbeitszeile in Telegram zeigt jetzt, was wirklich passiert** — mit Spinner und konkreter Angabe statt nur „nutzt gerade Bash".

  Bei Bash steht der Befehl dabei, bei Datei-Werkzeugen der Pfad (auf die letzten zwei Ebenen gekürzt), bei Suchen die Anfrage. Diese Angaben lagen im Werkzeugaufruf längst vor — gezeigt hat sie nur niemand. Gerade bei langen Läufen saß man davor, ohne zu wissen, was der Agent tut.

  Der Spinner dreht sich unabhängig davon weiter: Solange ein Werkzeug arbeitet, kommt kein Ereignis herein — ohne eigenen Takt stünde er still, ausgerechnet bei den langen Läufen, wo er gebraucht wird. Fehlt eine Angabe, bleibt die Zeile schlicht; es wird nichts erfunden.

### Deployment
- Orchestrator (Restart).

## [1.151.0] — 2026-08-06

### Changed
- **Der Agent entscheidet selbst, ob er auf eine Nachricht reagiert** — statt fester Regeln. Die Automatik aus v1.150.1 (Augen bei jedem Eingang, Daumen bei jedem Ende) ist raus: Ein Zeichen bei *jeder* Nachricht wirkt mechanisch.

  Neu ist ein Endpunkt `POST /telegram/react`, den der Agent nutzt, wenn es passt — ein Herz für etwas Nettes, ein erschrockenes Gesicht bei schlechten Nachrichten, ein Daumen zur Bestätigung. Der **Normalfall ist keine Reaktion**, und eine Reaktion ersetzt niemals eine Antwort; beides steht so in seiner Anleitung.

  Telegram erlaubt nur eine feste Auswahl von 21 Zeichen — die Liste ist hinterlegt und wird geprüft, sonst scheiterte es erst zur Laufzeit. Ein leeres Zeichen entfernt eine gesetzte Reaktion.

### Deployment
- Orchestrator (Restart) + Agenten-Image (die Anleitung liegt im Agenten).

## [1.150.2] — 2026-08-06

### Fixed
- **Telegram nahm Nachrichten an, beantwortete sie aber nicht mehr** (Regression aus v1.150.1). Der Nutzer bekam nur noch die Augen-Reaktion.

  Die in v1.150.1 ergänzte Zeile `self._last_user_msg[chat_id] = …` steht im Eingangs-Handler, das Feld wurde aber erst im Antwort-Lauscher angelegt — und der läuft **später**. Beim ersten Mal warf es `AttributeError`, der Handler brach ab, und die Nachricht erreichte den Agenten nie. Im Log sichtbar als `inbound text` ohne folgendes `Saved response`.

  Beide Zustandsfelder liegen jetzt im Konstruktor. Vor allem aber: Reaktion und Buchführung stehen in einem eigenen abgesicherten Block — **Beiwerk darf die Zustellung niemals verhindern**. 3 Tests halten das fest, inklusive des Verbots, die späte `hasattr`-Notlösung wieder einzuführen.

### Deployment
- Orchestrator (Restart).

## [1.150.1] — 2026-08-06

### Fixed
- **Die Arbeitszeile stand als Rohtext da.** Sie war als `_kursiv_` ausgezeichnet, die Nachricht geht aber ohne `parse_mode` raus — also erschienen die Unterstriche wörtlich. Jetzt eine schlichte Trennzeile ohne Auszeichnung; mit `parse_mode` würde jedes Sonderzeichen im Agententext die Nachricht zerlegen.
- **Werkzeugnamen sind lesbar.** Im Chat stand `mcp__orchestrator__create_task` — jetzt „Orchestrator: create task".
- **Ein fehlender Logger hätte den Antwortstrom zerlegt.** In `agent_bot.py` war `logging` importiert, aber nie ein `logger` angelegt. Die in v1.150.0 ergänzte Fehlerzeile im `except`-Block hätte deshalb einen `NameError` geworfen — ungefangen, weil sie selbst im Fehlerpfad steht.

### Added
- **Der Bot reagiert auf Nachrichten** (Kundenwunsch): Augen, sobald deine Nachricht angekommen ist und er zu arbeiten beginnt · Daumen hoch, wenn die Antwort steht · erschrockenes Gesicht bei einem Fehler. Eine fehlgeschlagene Reaktion bleibt folgenlos — sie ist Beiwerk und darf die Antwort nie verhindern.

### Deployment
- Orchestrator (Restart).

## [1.150.0] — 2026-08-06

### Changed
- **Telegram-Antworten entstehen sichtbar, statt am Stück zu erscheinen.** Bisher sammelte der Bot drei Sekunden und schickte dann eine **neue** Nachricht — man wartete lange und bekam alles auf einmal, oft als Kette mehrerer Nachrichten. Jetzt wird **eine** Nachricht gesendet und laufend bearbeitet: Der Text wächst mit, wie man es aus anderen Chat-Bots kennt.

  Die Textstücke lagen längst an — `chat_handler` veröffentlicht Deltas, nicht fertige Blöcke. Genutzt hat der Bot sie nur nicht.

  Während ein Werkzeug läuft, hängt eine kursive Zeile unter dem Text („nutzt gerade Bash…"); sobald wieder Text kommt, verschwindet sie. Bearbeitungen sind auf eine pro 1,3 Sekunden begrenzt (Telegram drosselt sonst), und über 4000 Zeichen greift wieder die Stückelung, weil Telegram längere Nachrichten nicht bearbeiten kann.

### Security
- **Kein Live-Bild am DLP-Filter vorbei.** Der Egress-Filter prüft den **fertigen** Text; ein Zwischenstand würde ihn umgehen und ein Secret wäre sichtbar, bevor der Filter greift. Bei aktivem DLP wird deshalb nicht live bearbeitet, sondern erst am Schluss gesendet. Lässt sich der Filterstatus nicht lesen, gilt er als aktiv (fail-closed).

### Deployment
- Orchestrator (Restart).

## [1.149.0] — 2026-08-05

### Added
- **Die angeforderten Microsoft-Berechtigungen sind in den Einstellungen anklickbar.** Nach dem Login kam „Genehmigung erforderlich": Der Server fordert 17 Graph-Rechte an, in der App-Registrierung standen acht. Entra verlangt dann eine Administrator-Genehmigung, und die Anmeldung bleibt hängen.

  Jetzt lässt sich die Auswahl an die eigene Registrierung anpassen. Pflichtrechte (`openid`, `email`, `profile`, `offline_access`, `User.Read`) sind fest — ohne sie gibt es keine Anmeldung und kein Aktualisierungs-Token. Die übrigen zwölf sind einzeln abwählbar, mit dem Hinweis, dass abgewählte Rechte die zugehörigen Funktionen (Mail, Kalender, Teams, Dateien) für die Agenten abschalten.

### Fixed
- **Der Anmelde-Weg ignorierte die Berechtigungs-Einstellung.** `get_provider_scopes()` liest `oauth_microsoft_scopes` seit jeher — der Integrations-Weg nutzte das, der SSO-Login nahm dagegen die fest verdrahtete Liste. Beide gehen jetzt durch dieselbe Auflösung.
- **Die Einstellung war überhaupt nicht setzbar:** Sie existierte in der Konfiguration, fehlte aber in der Erlaubnisliste, im Request-Schema, im Mapping des PATCH-Endpunkts und in der Rückgabe — dieselbe Vierfach-Lücke wie zuvor bei der Stimme und der Verzeichnis-ID.

### Deployment
- Orchestrator (Restart) + Frontend (Rebuild).

## [1.148.0] — 2026-08-05

### Added
- **Die Verzeichnis-ID (Mandant) lässt sich in den Einstellungen eintragen.** Nach dem Anlegen einer App-Registrierung schlug der Login mit `AADSTS50194` fehl: Die App ist Single-Tenant, die Anmeldung lief aber über den `/common`-Endpunkt. Der Code konnte das längst — `apply_tenant()` setzt die ID in Anmelde- und Token-URL ein und wird vom SSO-Login aufgerufen. Es gab nur kein Feld dafür; in der Oberfläche standen ausschliesslich Client-ID und Secret.

  Ergänzt an allen vier nötigen Stellen: Eingabefeld mit Erklärung (inkl. Fehlercode und Fundort im Azure-Portal), Request-Schema, Mapping im PATCH-Endpunkt und Rückgabe an die Oberfläche. Die letzten beiden waren dieselbe Doppel-Falle wie bei der Stimme — die erlaubten Schlüssel stehen an zwei Stellen, und ohne beide wird der Wert beim Speichern still verworfen.

  Hinweis im Feld: Eine feste Mandanten-ID bedeutet zugleich, dass sich nur Konten der eigenen Organisation anmelden dürfen. Bei `/common` gilt die E-Mail-Adresse aus Graph nicht als verifiziert — sonst wäre eine Kontoübernahme per E-Mail-Abgleich über Mandantengrenzen möglich.

### Deployment
- Orchestrator (Restart) + Frontend (Rebuild).

## [1.147.2] — 2026-08-05

### Fixed
- **Sprach-Gespräche kamen nicht mehr zustande** — „Unable to parse input chunk". Das in v1.146.0 ergänzte Werkzeug `manage_schedules` übergab sein Schema als rohes Objekt; Nova Sonic erwartet einen **JSON-String**, wie ihn alle anderen Werkzeuge per `json.dumps` liefern. Ein einziges falsch geformtes Schema lässt die **komplette Sitzung** scheitern, nicht nur das betroffene Werkzeug.

  Ein Test prüft jetzt **jedes** Werkzeug im Modul: Schema muss ein String und parsebar sein. Der vorherige Test hatte das falsche Format sogar festgeschrieben, weil er direkt auf das Dict zugriff.

### Deployment
- Orchestrator (Restart).

## [1.147.1] — 2026-08-05

### Fixed
- **Die Netzansicht nutzte den Platz nicht.** Der Bereich hing auf 620 px Höhe fest, und die Teams wurden auf einem **Kreis** verteilt, dessen Radius sich nach der kleineren Seite richtete. Bei einem Container von 2907 × 618 px lag dadurch alles zusammengedrängt in der Mitte, während links und rechts hunderte Pixel leer blieben.

  Der Bereich wächst jetzt mit dem Fenster, und die Teams sitzen auf einer **Ellipse** — jede Achse nutzt ihren eigenen Platz. Der Mindestabstand zweier Team-Kreise wird dabei aus dem tatsächlichen Abstand benachbarter Mittelpunkte berechnet (auf einer Ellipse ist der ungleich), nicht mehr aus einer Kreisformel.

### Deployment
- Frontend (Rebuild).

## [1.147.0] — 2026-08-05

### Added
- **Die Agenten-Netzansicht lässt sich zoomen** — Mausrad oder die Knöpfe oben rechts, ein Klick auf die Prozentzahl setzt zurück. Skaliert Abstände und Knoten gemeinsam, damit Linien, Team-Kreise und Beschriftungen exakt aufeinander ausgerichtet bleiben.

### Fixed
- **Team-Kreise überlappten sich, Beschriftungen lagen übereinander.** Der Radius eines Teams kam aus einer festen Zahl, unabhängig davon, wie viele Teams um den Mittelpunkt verteilt sind. Bei fünf Teams war der Kreis doppelt so gross wie der Platz zwischen zwei Mittelpunkten — „Projektmanagement" lag auf „Mein ReiseAgent", „MyResearcher" auf „CodeReview".

  Jetzt bestimmt der **verfügbare Platz** die Grösse: Zwischen zwei benachbarten Team-Mittelpunkten liegt `2·r·sin(π/n)` — mehr als die Hälfte davon darf ein Kreis nicht beanspruchen. Zusätzlich sitzt der Team-Ring weiter aussen.

### Deployment
- Frontend (Rebuild).

## [1.146.0] — 2026-08-05

### Added
- **Wiederkehrende Zeitpläne lassen sich per Sprache anhalten.** „Pausier den Watcher" wirkte bisher nicht: Im Gespräch gab es nur `cancel_task`, und das beendet den **gerade laufenden Durchlauf**, nicht den Zeitplan. Der Agent nahm es trotzdem und meldete „Der OpenWebUI-Watcher ist jetzt pausiert" — fünf Minuten später lief er wieder, und alle elf Zeitpläne standen unverändert auf aktiv.

  Neues Werkzeug `manage_schedules`: auflisten, pausieren, wieder starten. Der Prompt leitet Pausier-Wünsche dorthin und benennt den Denkfehler ausdrücklich.

### Changed
- **Was der Agent nicht kann, sagt er.** Neue Grundregel: Fehlt für einen Wunsch das Werkzeug, sagt er genau das („das kann ich per Sprache nicht, im Chat schon") — statt ein anderes Werkzeug als Ersatz zu nehmen und Erfolg zu melden. Das war die Wurzel unter der Falschmeldung und deckt auch künftige Lücken ab, die wir heute noch nicht kennen.

### Deployment
- Orchestrator (Restart).

## [1.145.1] — 2026-08-05

### Fixed
- **„Invalid event bytes" nach dem Vorlesen einer PDF.** Der Sprach-Stream brach ab, sobald Text aus einem Dokument eingespeist wurde. Die Länge war längst begrenzt — der **Zeicheninhalt** nicht: Aus der Dokument-Extraktion kommen Steuerzeichen, Ersatzzeichen und halbe Surrogate, und die brechen das Protokoll, nicht das Modell.

  Alles, was an die Engine geht, läuft jetzt durch **eine** Säuberung: kaputte UTF-8-Sequenzen weg, Steuerzeichen weg (Zeilenumbruch und Tabulator bleiben — Absätze sind Sinn), harte Längenbegrenzung. Genutzt von den Werkzeug-Ergebnissen wie von den Zwischenmeldungen, also von beiden Wegen, auf denen Text in die Sitzung gelangt.

  7 Tests, darunter der Fall, der es ausgelöst hat: halbe Surrogate müssen ohne Fehler serialisierbar bleiben.

### Deployment
- Orchestrator (Restart).

## [1.145.0] — 2026-08-05

### Added
- **Laufende Aufgaben lassen sich aufklappen und zeigen ihre Live-Schritte** (Kundenwunsch: „Leider kann ich laufende Aufgaben nicht aufklappen, um Details zu lesen"). Die Karte zeigt die letzten Arbeitsschritte — welches Werkzeug gerade läuft, was zuletzt geschah — und lädt sie alle drei Sekunden nach, solange sie offen und die Aufgabe nicht fertig ist.

  Die Daten lagen längst vor: dieselbe Quelle, aus der die Task-Detailansicht ihre Zeitreise speist. Im Sprach-Panel holte sie nur niemand ab, also stand dort „läuft" und sonst nichts.

  Bewusst **dieselbe** Auf-/Zuklapp-Mechanik wie bei den fertigen Karten (v1.143.1), nicht eine zweite daneben. Nachladen passiert nur für aufgeklappte, noch laufende Aufgaben — zugeklappt oder fertig kostet es nichts, und das Ende der Aufgabe beendet es von selbst.

### Deployment
- Frontend (Rebuild).

## [1.144.2] — 2026-08-05

### Fixed
- **Die gewählte Stimme wurde still verworfen.** „tiffany" gewählt, „Gespeichert." bekommen — und weiter Matthew gehört. Die erlaubten Schlüssel stehen an **zwei** Stellen: `ALLOWED_KEYS` im Service (dort stand `nova_sonic_voice`) und `_VOICE_FIELDS` im PATCH-Endpunkt (dort fehlte er). Zusätzlich kannte das Request-Schema das Feld nicht, der Wert erreichte den Endpunkt also gar nicht. Beides ergänzt; ein Test prüft jetzt den **Speicherweg**, nicht nur die Erlaubnisliste.
- **Der Agent findet Dateien wieder, die er selbst eingeblendet hat.** Die PDF lag sichtbar als Karte im Panel, und er antwortete „ist im Workspace nicht zu finden" — er durchsuchte nur die oberste Ebene und nahm den Namen buchstabengenau. Zweimal passiert: `-Watcher_` gegen `_Watcher_`, „Aktivitaets" gegen „Aktivitäts". Die Dateisuche fragt jetzt **zuerst das eigene Gedächtnis** (`_shown_files`) und vergleicht unscharf (Umlaute, Binde-/Unterstrich). Was auf dem Bildschirm steht, weiss er damit auch selbst.
- **Fragt der Nutzer nach seinen Aufgaben, stehen die laufenden wieder im Cockpit.** Nach einem Sitzungswechsel war das Panel leer, obwohl er sie im Gespräch korrekt aufzählte. Anmeldung über dieselbe eine Stelle wie `plan_task`.

### Deployment
- Orchestrator (Restart).

## [1.144.1] — 2026-08-05

### Added
- **Adressen im Sprach-Gespräch sind anklickbar.** Der Agent nennt Links im Fliesstext („du kannst sie unter https://… aufrufen") — bisher standen sie tot da und mussten abgetippt werden. Gilt jetzt für die Gesprächsblasen, das Ergebnis auf der Aufgabenkarte und die klassische Antwortanzeige. Bewusst an einer Stelle statt als Sonderfall für App-Links: damit greift es für jede Adresse, die er jemals nennt. Satzzeichen am Ende bleiben ausserhalb des Links.

### Deployment
- Frontend (Rebuild).

## [1.144.0] — 2026-08-05

### Added
- **Die Stimme des Echtzeit-Gesprächs ist in den Einstellungen wählbar** (Kundenwunsch). Alle 16 Stimmen von Amazon Nova Sonic stehen zur Auswahl; **matthew** und **tiffany** sind polyglott und sprechen auch Deutsch — das ist in der Liste ausgewiesen, damit niemand versehentlich eine rein englische Stimme wählt.

  Das Backend konnte das längst: `nova_sonic_voice` wurde beim Verbindungsaufbau gelesen (plus `interaction_voice` pro Agent). Nur gab die Settings-API den Wert nie zurück — die Oberfläche konnte ihn also weder anzeigen noch setzen. Ergänzt in Schema und Antwort, dazu das Auswahlfeld.

  Die Stimmenliste ist bewusst fest hinterlegt und per Test an die AWS-Dokumentation gebunden: Eine erfundene Stimm-ID lässt die Sitzung erst beim Verbindungsaufbau scheitern — mit einem Fehler, der bis v1.143.2 nirgends im Log auftauchte.

### Deployment
- Orchestrator (Restart) + Frontend (Rebuild).

## [1.143.2] — 2026-08-05

### Added
- **Fehler der Sprach-Engine landen im Log.** „Model has timed out in processing the request" erschien nur im Browser — im Orchestrator-Log stand nichts, in der JS-Konsole auch nicht. Der Text stammt nicht aus unserem Code, sondern kommt von AWS Bedrock durchgereicht und wurde ungeprueft weitergereicht. Jetzt mit Kontext protokolliert: Stille seit der letzten Sprachausgabe, Zahl der eingeblendeten Bilder, offene Aufgaben. Beobachtete Spur: Der Abbruch trat bisher zweimal direkt nach einem Screenshot auf.

## [1.143.1] — 2026-08-05

### Changed
- **Erledigte Aufgaben im Sprach-Panel lassen sich zuklappen und ausblenden.** Ein Ergebnis kann seitenlang sein — die fertige Analyse einer Codebasis fuellte das ganze Panel. Fertige Karten zeigen jetzt nur noch den Titel; der Ergebnistext kommt auf Klick. Ein X blendet die Karte ganz aus. Laufende Aufgaben bleiben unveraendert offen.

### Deployment
- Frontend (Rebuild).

## [1.143.0] — 2026-08-05

### Fixed
- **Parallel laufende Aufgaben wurden mitten in der Arbeit als verschollen abgeraeumt.** „Pitchdeck-Neugestaltung" endete als `Task lost - agent stopped responding`, waehrend der Agent daran arbeitete — sie lief parallel zum OpenWebUI-Watcher, und der ueberschrieb die Meldung.

  Der Aufraeum-Waechter stammt aus der Zeit, als ein Agent EINE Aufgabe nach der anderen abarbeitete: Er hielt eine Aufgabe nur dann fuer lebendig, wenn der Agent genau sie als `current_task` nennt. Seit `MAX_PARALLEL_TASKS` (auf dem Pi: 8) laufen mehrere gleichzeitig, gemeldet wird aber nur die zuletzt gestartete. **Je paralleler gearbeitet wird, desto haeufiger trifft es: Bei acht Aufgaben ist eine sichtbar, sieben sind Kandidaten fuers Abraeumen.**

  Jetzt zwei unabhaengige Lebensbeweise, und einer genuegt:
  - Der Agent meldet die **vollstaendige** Liste seiner laufenden Aufgaben (`active_sessions`), nicht nur eine — beim Start wie beim Ende jeder einzelnen.
  - Unabhaengig davon: schreibt die Aufgabe noch Schritte? Ob der Agent sie gerade beim Namen nennt, ist Zufall der Meldereihenfolge; ob sie TaskSteps produziert, ist Tatsache.

  Erst wenn beides schweigt, gilt eine Aufgabe als tot. 12 Tests, darunter der gegen den Rueckfall: der rohe `current_task`-Vergleich darf nicht zurueckkehren.

  Der TaskStep-Beweis wirkt sofort mit dem Orchestrator; die vollstaendige Melde-Liste kommt mit dem naechsten Agenten-Image dazu.

### Deployment
- Orchestrator (Restart genuegt) — behebt den Fehler bereits. Der Agenten-Teil (vollstaendige Melde-Liste) braucht Image-Rebuild + Neuerstellung, ist aber nur die zweite Absicherung.

## [1.142.3] — 2026-08-05

### Fixed
- **Fremde Arbeit stand im eigenen Chat.** Waehrend eines Gespraechs ueber SAP-Stammdaten erschien unter der eigenen Nachricht `Bash · python3 /workspace/scripts/openwebui_… · 192s` — ein geplanter Watcher-Task, der mit dem Gespraech nichts zu tun hatte. Die angezeigte Dauer passte zu nichts: der Turn dauerte 38 Sekunden, der Watcher-Lauf 20.

  Die Live-Zeile (#469) abonniert den **agentenweiten** Log-Kanal — er fuehrt alles, was der Agent tut: geplante Aufgaben, andere Gespraeche, den eigenen Turn. Genommen wurde daraus schlicht der letzte Werkzeugaufruf, ohne zu pruefen, ob er ueberhaupt zu diesem Gespraech gehoert oder noch aktuell ist. Ein laengst beendeter Aufruf lief im Zaehler einfach weiter.

  Jetzt zwei Filter: Aufrufe mit `task_id` gehoeren zu einer Aufgabe und bleiben draussen, und alles von VOR dem Beginn dieses Turns ebenfalls. Damit zeigt die Zeile nur noch, was dieser Turn gerade wirklich tut.

  Relevant, weil auf dem Pi 8 Chats und 8 Aufgaben parallel laufen duerfen: je paralleler gearbeitet wird, desto mehr fremde Aufrufe landeten vorher im Bild.

### Deployment
- Frontend (Rebuild noetig). Kein Orchestrator-Restart, kein Agenten-Image.

## [1.142.2] — 2026-08-05

### Fixed
- **Eine Gespraechspause des Nutzers zaehlte als Stillstand des Agenten.** Wer nach zehn Minuten Ruhe wieder etwas schrieb, bekam nach 15 Sekunden „Der Agent hat sich zwischendurch nicht mehr gemeldet und wurde abgebrochen" — noch bevor der Agent ueberhaupt etwas tun konnte. Je laenger die Pause, desto sicherer der Abbruch.

  `last_activity_at` lebt am LogPublisher ueber Turns hinweg und wurde beim Start eines neuen Turns nicht zurueckgesetzt. Die Uhr lief also seit der letzten Regung VOR der Pause. Sie beginnt jetzt beim Turn.

  Der Wachhund soll einen haengenden Agenten fangen, nicht einen nachdenklichen Nutzer. 3 Tests halten beides fest — inklusive des Rechenwegs, der den Fehler erzeugte.

### Deployment
- Liegt im Agenten-Image: Image neu bauen **und jeden Agenten neu erstellen**.

## [1.142.1] — 2026-08-05

### Fixed
- **Der Agent-zu-Agent-Pfad war seit v1.140.0 komplett tot** — und riss den Chat mit. Im Agenten-Log stand bei jeder Kollegen-Nachricht `Consumer error: name 'ProcessIdleTimeout' is not defined`.

  Beim Einbau des Stillstands-Wachhunds in `message_consumer` wurden **weder der Wachhund noch seine Ausnahme importiert**. Der Aufruf warf NameError, und beim Auswerten der `except`-Klausel warf der zweite fehlende Name gleich hinterher.

  Die Wirkung reichte bis in den Chat: Ein Agent fragte einen Kollegen, bekam nie eine Antwort, verstummte 600 Sekunden und wurde abgebrochen — „Der Agent hat sich zwischendurch nicht mehr gemeldet." Der Stillstands-Wachhund hatte dabei recht; die Ursache lag im fehlenden Import.

  Die Tests von v1.140.0 pruefen `proc_watchdog.py` gegen echte Unterprozesse — nur den **Aufrufer** hat niemand angefasst. Diese Luecke ist geschlossen: ein Test findet benutzte, aber nirgends gebundene Namen in `message_consumer`, `chat_consumer` und `task_consumer`. Ohne den Import wird er rot, mit ihm gruen — verifiziert.

### Deployment
- Liegt im Agenten-Image: Image neu bauen **und jeden Agenten neu erstellen**. Codex-Agenten dabei staffeln (geteilte Token-Familie).

## [1.142.0] — 2026-08-05

### Fixed
- **Der Sprach-Agent versprach Aufgaben, statt sie anzulegen.** „Nimm das als Aufgabe mit" → „Ich erstelle dir gleich einen Plan dafuer und melde mich" — und es entstand nichts. Erst auf das Wort „delegiert" lief `plan_task`. Der Nutzer musste die Vokabel des Systems raten.

  Zwei Ursachen. Die Auslöserliste kannte nur „plan das ein" und „kümmer dich drum" — natuerliche Formulierungen fehlten. Vor allem aber wusste der Sprach-Agent nicht, **dass er selbst nichts tun kann**: Er tritt als der Agent auf (so gewollt), aber niemand hatte ihm gesagt, dass die Arbeitskraft hinter ihm sitzt und ohne Werkzeugaufruf nichts geschieht. Deshalb hielt er eine Ankuendigung fuer eine Handlung. Er weiss das jetzt — nach aussen unveraendert die ICH-Form, nach innen: jede Zusage ueber etwas Handfestes braucht im selben Zug ein Werkzeug. Fragt der Nutzer nach, prueft er nach, statt zu raten.

- **Fertigmeldungen gingen unter, wenn sie in eine laufende Sprachausgabe fielen.** „Aufgabe wurde waehrend einer Sprachausgabe fertig — Text wurde erstellt, aber nicht per Audio ausgegeben." Das Modell haengte die Meldung an den laufenden Satz an, statt sie zu sprechen.

  Die Warteschleife „bis die Stimme ruht" lag zweimal kopiert im Modul — und ausgerechnet die Fertigmeldung nutzte keine davon. Sie liegt jetzt an **einer** Stelle, und alle Meldungen, die von selbst kommen (Aufgabe fertig, Bildauswertung, Datei eingetroffen, Termin-Hinweis, App-Fehler), gehen durch sie. Gemessen wird Stille statt eines geratenen Zustands; begrenzt, damit nie etwas haengen bleibt.

  4 weitere Tests, darunter der, der die Kopie verhindert: selbst ausgeloeste Meldungen duerfen nicht am Warten vorbei eingespielt werden.

### Deployment
- Orchestrator (Code gemountet → Restart genuegt). Kein Frontend-Rebuild, kein Agenten-Image.

## [1.141.0] — 2026-08-05

### Fixed
- **Eingeplante Aufgaben im Sprach-Gespraech blieben unsichtbar.** „Alles klar, ich hab die Aufgabe eingeplant" — und rechts in „Aufgaben & Aktivitaet" blieb es leer. Der Nutzer fragte dreimal nach, ob die Aufgabe ueberhaupt existiert. Sie existierte: `tmzdqpquz`, angelegt, geroutet, laufend.

  Ursache: Arbeit entsteht im Gespraech auf **zwei** Wegen — sofort erledigen und einplanen. Nur der Sofort-Weg war ans Cockpit angeschlossen. Der Einplan-Weg legte die Aufgabe an, haengte den Rueckkanal an und schickte dem Frontend **nichts**. Vier Folgen aus derselben Wurzel:

  - keine Karte im Panel, obwohl die Aufgabe lief
  - die Fertigmeldung wurde verschluckt (sie schaltet nur eine *bestehende* Karte um)
  - fertige Dateien blieben liegen — der Datei-Scan lief nur im Sofort-Weg, die PDF lag korrekt in `/workspace/transfer` und wurde nie gezeigt
  - „guck mal in die Aufgabe rein" → **„ich hab noch keine Aufgabe delegiert"**, waehrend genau diese Aufgabe lief: die Uebersicht kannte den Einplan-Weg nicht

  Beide Wege melden jetzt ueber **eine** Stelle an (`_register_task`). Die fertige Aufgabe blendet ihre Dateien ein und traegt ihr Ergebnis auf der Karte. Der Agent nennt auf Nachfrage den **echten** Stand — Status plus letzter Arbeitsschritt aus derselben Quelle, aus der die Task-Detailansicht ihre Live-Sicht speist. Und laeuft eine Fertigmeldung doch einmal ins Leere, legt das Panel die Karte selbst an, statt sie zu verschlucken.

  9 Tests, darunter der, der das nachhaltig macht: das `delegate`-Ereignis darf nur an genau **einer** Stelle entstehen — damit ein spaeter gebauter dritter Weg nicht wieder stumm dasteht.

### Deployment
- Orchestrator + Frontend. **Kein Agenten-Image, keine Agenten-Recreates** — laufende Aufgaben bleiben unberuehrt.

## [1.140.0] — 2026-08-05

### Fixed
- **Agent-zu-Agent-Nachrichten wurden nach 5 Minuten hart abgebrochen** — dieselbe Krankheit wie im Chat, nur an einer Stelle, die gestern unangetastet blieb (`[Timeout - message processing took too long]` im Log). Fragt ein Agent einen Kollegen etwas, das laenger dauert, war die Antwort weg, egal ob dort gearbeitet wurde.

  Die Regel liegt jetzt an **einer** Stelle (`proc_watchdog.py`) statt dreimal kopiert: Ein Unterprozess wird begleitet, jede Regung — stdout wie stderr — setzt die Uhr zurueck, abgebrochen wird nur, wer wirklich verstummt. Ein grosszuegiges Gesamtlimit bleibt als Notbremse gegen endlose Ausgabe. Der Chat-Pfad nutzt dieselbe Idee, der Nachrichten-Pfad jetzt dieselbe Implementierung.

  6 Tests gegen echte Unterprozesse, darunter der Kernfall: lange laufend **aber redend** ueberlebt, stumm faellt.

### Deployment
- Liegt im Agenten-Image: Image neu bauen **und jeden Agenten neu erstellen**.

## [1.139.1] — 2026-08-04

### Fixed
- **Der Stillstands-Wachhund aus v1.139.0 hat trotzdem abgebrochen** („Der Agent hat sich zwischendurch nicht mehr gemeldet"). Er hat richtig funktioniert, nur am falschen Signal gehorcht: Die Uhr wurde ausschließlich von **veröffentlichten Chat-Ereignissen** zurückgesetzt. Steckt die CLI minutenlang in einem einzigen langen Werkzeug — ein Build, eine Installation — kommt oben nichts an, und ein arbeitender Agent galt nach 600 Sekunden als hängend.

  Das Lebenszeichen sitzt jetzt eine Ebene tiefer: **jede Regung der CLI zählt** — jeder Ausgabe-Block auf stdout, jede Zeile auf stderr, in beiden Laufzeiten (Claude Code und Codex). Damit gilt ein Agent nur noch dann als hängend, wenn sein Prozess wirklich nichts mehr von sich gibt.

## [1.139.0] — 2026-08-04

### Fixed
- **Arbeitende Agenten wurden mitten in der Arbeit abgeschossen.** „Die Antwort hat zu lange gedauert und wurde abgebrochen" — nach zwölf erfolgreich gelaufenen Werkzeugen, beim Umbau einer App. Alles verworfen. Dasselbe passierte in Telegram.

  Ursache war eine feste **Gesamtdauer** pro Chat-Antwort (Claude Code 600 s, Codex 1800 s), die nicht danach fragt, ob der Agent überhaupt noch arbeitet. Wer eine echte Aufgabe stellt — „bau das Design um, mach es mobiltauglich" — überschreitet zehn Minuten regelmäßig und verliert dann das gesamte Ergebnis.

  Jetzt zählt der **Stillstand** statt der Dauer: Jedes veröffentlichte Ereignis — Werkzeugaufruf, Zwischenstand — setzt die Uhr zurück. Ein Agent, der sichtbar arbeitet, läuft weiter, egal wie lange. Ein wirklich hängender Turn fällt nach wie vor raus und blockiert die Warteschlange nicht.

  Die Meldung war zudem irreführend und heißt jetzt, was tatsächlich passiert ist: „Der Agent hat sich zwischendurch nicht mehr gemeldet."

### Deployment
- Die Änderung steckt im Agent-Image: **Image neu bauen und alle Agenten neu erstellen**, sonst greift sie nur bei neuen Containern.

## [1.138.1] — 2026-08-04

### Fixed
- **Ein Screenshot ohne Aufnahme-Freigabe sah aus wie ein gültiger.** macOS meldet in dem Fall keinen Fehler, sondern liefert ein Bild mit Schreibtisch und Menüleiste — **ohne Fensterinhalte**. Das ist von einem echten Screenshot kaum zu unterscheiden und hat im Test sowohl den Nutzer als auch das auswertende Modell getäuscht: Der Agent sah die Menüleiste „Safari" plus ein Landschaftsfoto und beschrieb daraufhin überzeugt „ein Safari-Fenster mit einem Luftbild eines Weinbergs". Das Luftbild war der Schreibtischhintergrund.

  Die Bridge fragt jetzt **vor** der Aufnahme, ob sie überhaupt darf, und liefert sonst einen klaren Fehler mit dem genauen Weg zur Einstellung, statt ein wertloses Bild. Der Rückfall auf `pyautogui` greift dabei ausdrücklich nicht — der zeigt dasselbe leere Bild.

## [1.138.0] — 2026-08-04

### Changed
- **Die Bildauswertung blockiert das Gespräch nicht mehr.** Bisher wartete der Sprach-Agent bis zu 90 Sekunden auf den Agenten, der den Screenshot ansieht — und solange stand die Unterhaltung still. Im Sprachmodus ist eine Pause dieser Länge nicht auszuhalten. Jetzt kommt sofort ein kurzer Hinweis („ich schau gerade drauf"), die Auswertung läuft nebenher, und das Ergebnis wird eingespeist und vorgelesen, sobald es da ist **und** die Stimme gerade nicht spricht — dasselbe Muster, das schon für hochgeladene Dateien gilt. Der Agent bekommt ausdrücklich gesagt, in der Zwischenzeit **nichts** zu beschreiben, weil er den Inhalt noch nicht kennt.

## [1.137.2] — 2026-08-04

### Fixed
- **macOS fragte bei JEDEM Screenshot erneut nach der Bildschirmaufnahme-Freigabe** — auch wenn man sie längst erteilt hatte. Kein Berechtigungsproblem auf Nutzerseite, sondern ein Fehler: `pyautogui.screenshot()` startet auf macOS bei jedem Aufruf das Programm `screencapture` als **eigenen Prozess**. Die Freigabe hängt aber an der anfragenden Anwendung, und ein kurzlebiger Fremdprozess bekommt sie nicht zuverlässig zugeordnet — also fragt das System wieder.

  Die Aufnahme läuft jetzt über Quartz **im Prozess der Bridge selbst**. Die einmal erteilte Freigabe gilt damit für die Bridge und wird nicht mehr abgefragt. `pyautogui` bleibt als Rückfall für Windows/Linux und für den Fall, dass Quartz fehlt. Neue Abhängigkeit `pyobjc-framework-Quartz`.

  Lokal gegen einen echten Bildschirm geprüft: 3840×2486 aufgenommen, echter Inhalt, ohne Fremdprozess. Zeilenpolsterung und BGRA-Reihenfolge werden berücksichtigt — ohne beides verschert das Bild oder Rot und Blau sind vertauscht.

## [1.137.1] — 2026-08-04

### Changed
- **Die Bildauswertung geht jetzt über den Agenten, an dem die Stimme ohnehin hängt** — statt über einen zweiten, eigens einzurichtenden Modellzugang. Der Screenshot wird als Bild an den gebundenen Agenten geschickt, der ihn mit **seinem** bestehenden Zugang ansieht (OAuth-Claude, Bedrock, Azure — was auch immer für ihn eingerichtet ist), und die Stimme liest dessen Antwort vor.

  Der Weg aus v1.136.0 rief die Anthropic-API direkt auf und brauchte dafür einen eigenen Schlüssel. Auf Installationen ohne einen solchen — Pi und Kundenserver — blieb die Bilderkennung damit tot, obwohl der Agent daneben längst sehen konnte. Der Umweg ist ersatzlos entfallen (`screen_vision.py` gelöscht), und [#505](https://github.com/greeves89/AI-Employee/issues/505) erledigt sich damit.

  Die Leitung dafür war schon da: Das Nachrichtenformat zum Agenten hat ein `images`-Feld, und die Agentenseite legt Bilder als Dateien im Workspace ab und reicht sie an die CLI weiter. Nur der Sprach-Layer hat nie welche mitgeschickt.

## [1.137.0] — 2026-08-04

### Changed
- **Der Sprach-Agent sagt jetzt Bescheid, bevor er etwas tut, das dauert.** „Einen Moment, ich öffne das." · „Ich schau mal auf deinen Bildschirm." · „Moment, ich seh mir das Bild an." Bisher wurde es einfach still, und man saß sekundenlang vor einer Leitung ohne Ton.

  Die Ursache stand im Prompt selbst: Die Regel gegen Laut-Denken verbot ausdrücklich „zu beschreiben, welches Tool du gleich nutzt". Sie war gegen „Okay, der Nutzer fragt… ich muss mal prüfen…" geschrieben und hat dabei auch den ganz normalen Wartehinweis miterschlagen. Jetzt sind beide Fälle sauber getrennt: **ansagen, WAS gleich passiert, ist erwünscht; erklären, WARUM oder WOMIT, bleibt verboten.** Mit Beispielsätzen, dem Hinweis auf Abwechslung, und der Vorgabe, nichts anzukündigen, was ohnehin sofort da ist.

  Gilt für **beide Sprach-Engines** — AWS Nova Sonic und Azure Realtime bekommen denselben Prompt (der Kundenserver läuft auf Azure, der Pi auf AWS). Ein Test hält fest, dass der Prompt vor der Engine-Weiche gebaut wird und beide ihn bekommen.

## [1.136.1] — 2026-08-04

### Fixed
- **Ergebnisse im Sprach-Cockpit ließen sich nicht wegklicken.** Suchtreffer und angezeigte Screenshots blieben bis zum Sitzungsende stehen und verdeckten alles, was danach kam. Jedes Ergebnis hat jetzt ein Schließen-Kreuz, der große Anzeigebereich ebenfalls, und ab zwei Einträgen gibt es „Alle Ergebnisse ausblenden".
- **Die Bilderkennung sucht den Zugang jetzt auch in den Einstellungen**, nicht nur in der Umgebung — und sagt im Fehlerfall, wo man ihn hinterlegt, statt nur „fehlt".

### Bekannt
- Installationen, die **ausschließlich über Bedrock** laufen (wie der Pi und der Kundenserver), haben keinen Anthropic-Schlüssel. Dort fällt die Bilderkennung sauber aus — der Agent sagt es und rät nicht —, funktioniert aber nicht. Der Weg über Bedrock ist als [#505](https://github.com/greeves89/AI-Employee/issues/505) erfasst, inklusive der Stelle, an der die fertige SigV4-Signatur schon liegt. Sofortlösung bis dahin: einen Anthropic-Schlüssel unter „Einstellungen → Modelle" hinterlegen.

## [1.136.0] — 2026-08-04

### Added
- **Der Sprach-Agent sieht jetzt wirklich, was auf dem Bildschirm ist.** Nova Sonic ist ein reines Sprache-zu-Sprache-Modell und hat **keinen Bildkanal** — ein Screenshot ging an den Browser des Nutzers, aber nie in den Kontext des Modells. Auf „was siehst du?" konnte der Agent deshalb nur passen. Das Bild läuft jetzt durch ein bildfähiges Modell, und die Stimme bekommt dessen Beschreibung als Text. Die Antwort ist auf drei kurze Sätze begrenzt, weil sie vorgelesen wird.
  - Erkennt das bildfähige Modell im Wesentlichen nur den Schreibtischhintergrund, sagt es das ausdrücklich — unter macOS fehlt dann fast immer die Freigabe zur **Bildschirmaufnahme**, und genau so sah der Screenshot beim Test aus: Menüleiste da, Fensterinhalte leer.
  - Scheitert die Auswertung, sagt der Agent das und rät nicht.

### Fixed
- **Der Agent schlug „Windows-Taschenrechner" auf einem Mac vor.** Die Bridge-Session kennt das Betriebssystem, der Sprach-Agent bekam es nur nie zu sehen. Jetzt fließt es in die Rückmeldung ein, sodass Fehlschläge mit den passenden App-Namen der jeweiligen Plattform beantwortet werden.

## [1.135.2] — 2026-08-04

### Security
- **Befehlseinschleusung im `open_url` aus v1.135.1 (Windows) — behoben, bevor die Version irgendwo im Einsatz war.** Adressen wurden dort über `cmd /c start "" <url>` geöffnet. `cmd.exe` zerlegt seine Argumente **erneut**, wodurch ein `&` in der URL zum Befehlstrenner wird — und `&` steht in jeder zweiten Query. `https://example.com/?a=1&calc.exe` hätte `calc.exe` gestartet. Brisant, weil die Adresse vom Sprachmodell kommt, das seinerseits durch Inhalte beeinflussbar ist, die es liest. Windows nutzt jetzt `os.startfile`, das direkt an die Shell-API geht, ganz ohne Kommandozeile; macOS und Linux waren nie betroffen (Listenform, keine Shell). Zusätzlich werden Leerraum und Steuerzeichen in der Adresse abgewiesen. 6 Regressionstests, die die Quelle prüfen — der Windows-Zweig ist auf dem Build-Rechner nicht ausführbar.

  Gefunden vom automatischen Review der gepushten Commits, nicht von mir.

## [1.135.1] — 2026-08-04

Drei Fehler aus dem ersten echten Sprach-Test der Desktop-Bridge — alle drei sorgten dafür, dass der Agent Erfolg meldete, wo keiner war.

### Fixed
- **Der Agent behauptete, Apps geöffnet zu haben, die nicht aufgingen.** „Chrome ist jetzt bei dir geöffnet" — war es nicht. Die Bridge meldet Misserfolg sauber als `ok: false` samt Grund; mein Handler gab trotzdem stur eine Erfolgsmeldung zurück, und der Agent hat sie gutgläubig weitergereicht. Jetzt wird der Fehlertext der Bridge wörtlich durchgereicht, mit ausdrücklicher Anweisung, den Erfolg nicht zu behaupten.
- **„Öffne google" konnte gar nicht funktionieren.** Adressen liefen über `open -a`, und `-a` erwartet eine **Anwendung**, keine URL. Neue Bridge-Aktion `open_url` (macOS `open`, Windows `start`, Linux `xdg-open`); der Sprach-Agent erkennt Adressen selbst und ergänzt fehlendes `https://`. In der Freigabe-Gruppe `apps` registriert — ohne diesen Eintrag wäre die Aktion fail-closed abgewiesen worden.
- **Auf deutscher Tastatur tippte die Bridge Unsinn.** `pyautogui.typewrite` schickt Tastenpositionen statt Zeichen: aus `open -a "Google Chrome"` wurde `open ßa #Google Chrome#`. Auf macOS tippt jetzt System Events zeichenbasiert und damit layoutunabhängig, mit Rückfall auf den alten Weg, falls das scheitert.

### Bekannt, noch offen
- **Der Sprach-Agent sieht Screenshots nicht.** Er kann sie machen und anzeigen, aber Nova Sonic hat keinen Bildkanal — auf „was siehst du?" muss er passen. Seit v1.135.0 sagt er das ehrlich, statt zu raten. Damit er den Bildschirm wirklich auswerten kann, müsste das Bild an ein bildfähiges Modell gehen.

## [1.135.0] — 2026-08-04

### Added
- **Der Sprach-Agent kann den Rechner des Nutzers bedienen (#489).** Neues Werkzeug `desktop`: URL oder Programm auf seinem Gerät öffnen, Screenshot, klicken, tippen. Bisher hatte die Sprachsitzung **kein einziges** `computer_*`-Werkzeug und wimmelte interne Adressen ab („ruf es selbst im Browser auf") — die Anleitung aus #475 ging an die Agenten-Container, nicht an diese Laufzeit.

### Changed
- **Der Weg zur Bridge liegt jetzt in einer Funktion.** Der Kommando-Versand steckte im HTTP-Endpunkt; er ist als `dispatch_bridge_command` herausgelöst, und Endpunkt wie Sprachsitzung gehen hindurch. Damit gelten für die Stimme dieselben Schranken wie für jeden Agenten: Besitzprüfung, Zuordnung zu einem bestimmten Agenten, Sitzungs-Timeout, Aktions-Limit, serverseitige Freigabe der Fähigkeiten und Audit-Eintrag — nachvollziehbar als `voice:{agent_id}`.

### Security
- Beim Selbstprüfen geschlossen: Der Sprachweg reichte die **Agenten-Kennung nicht durch**. Da die Zuordnungsprüfung nur greift, wenn sie mitkommt, hätte ein Sprach-Agent eine Session bedienen können, die ausdrücklich einem anderen Agenten zugewiesen ist.
- Aus dem Review: Eine nicht-numerische Koordinate ließ eine Ausnahme am Fehlerpfad vorbeifliegen — der Sprach-Turn bekam nie eine Antwort und blieb stehen. Und der Rückgabetext forderte das Modell auf, den Screenshot zu **beschreiben**, obwohl es das Bild gar nicht erhält (es geht nur an den Bildschirm des Nutzers) — eine Einladung zum Erfinden, jetzt ausdrücklich untersagt.
- 18 Tests, die überwiegend prüfen, was **nicht** geht: fremder Nutzer, gesperrte Fähigkeit, unbekannte Aktion (fail-closed), Limit, abgelaufene Session, fehlende Bridge, fremder Agent, unbekannter Nutzer. Dazu: Ablehnungsgründe werden wörtlich durchgereicht statt in ein Ausweichmanöver übersetzt, und ein leerer Screenshot wird nicht beschrieben.

### Bekannt, bewusst offen
- Klick, Tastatur und App-Start sind in neuen Sessions **standardmäßig freigegeben** und laufen ohne Einzelbestätigung. Das galt vorher genauso, ist über die Stimme aber leichter auszulösen. Für den Klinikbetrieb wäre eine ausdrückliche Rückfrage vor Klick/Tippen zu erwägen.

## [1.134.3] — 2026-08-04

### Fixed
- **Der Sprach-Agent wimmelte interne Adressen ab.** Auf „öffne das Ticketsystem Matrix42" antwortete er sinngemäß „das ist eine interne Adresse, ruf sie selbst im Browser auf". Er hat **keine** `computer_*`-Werkzeuge — die Desktop-Bridge steht ihm gar nicht zur Verfügung, die Anleitung aus #475 ging an die Agenten-Container, nicht an diese Laufzeit. Erreichen musste er die Adresse aber nie: Der **Browser des Nutzers** steht im selben Netz. Neue Regel im Sprach-Prompt: interne Adressen sind keine Sackgasse — mit `show_on_screen kind='tab'` im Browser des Nutzers öffnen, und soll dort etwas *getan* werden, per `ask_agent` an den Agenten mit Computer-Use delegieren. „Ruf es selbst auf" ist ausdrücklich als schlechteste Antwort benannt.
- **Der Agent kündigte einen Seitenwechsel an, der keiner mehr ist.** Seit v1.134.0 erscheinen App-Seiten im Cockpit statt als Navigation; Werkzeug-Beschreibung und Prompt sagten aber weiter „switches the whole page". Beide nachgezogen — inklusive der neuen Ziele Apps, Audit-Log, System-Health und Schedules.

## [1.134.2] — 2026-08-04

### Fixed
- **„Neue Session" legte keine neue an.** Seit v1.130.0 gibt `POST /computer-use/sessions` standardmäßig die **bestehende** Session zurück — richtig so beim Öffnen des Tabs, denn sonst müsste die Bridge nach jedem Seitenaufruf neu eingerichtet werden. Der Knopf „+ Neue Session" ging aber denselben Weg und bekam damit immer dieselbe ID zurück: er tat sichtbar nichts. Ein ausdrücklicher Klick schickt jetzt `reuse=false` und liefert wirklich eine neue Session; die alte bleibt bestehen und kann daneben gelöscht werden (was seit v1.134.1 auch wirkt). 4 neue Tests zur Wiederverwendungs-Logik.

## [1.134.1] — 2026-08-04

### Fixed
- **Computer-Use-Sessions ließen sich nicht löschen — sie kamen nach dem Neuladen wieder.** Seit die Sessions in Redis liegen (v1.130.0) räumte das Löschen nur den Prozess-Speicher; der Redis-Schlüssel blieb liegen, wurde beim nächsten Zugriff zurückgeholt und von der Übersicht ohnehin wieder eingescannt. Für den Nutzer war eine Session damit schlicht unlöschbar. Betraf drei Stellen: das ausdrückliche Löschen, das Aufräumen abgelaufener Sessions und den Timeout beim Kommando — alle drei räumen jetzt Speicher **und** Redis über einen gemeinsamen Weg. 5 neue Tests, darunter der eigentliche Fall (nach dem Löschen darf sie nicht zurückkommen) und Löschen bei ausgefallenem Redis.

## [1.134.0] — 2026-08-04

### Fixed
- **Die Sprachsteuerung brachte sich selbst zum Schweigen (#476).** Ließ man den Agenten per Sprache eine Seite öffnen — „zeig mir Analytics" —, navigierte die App dorthin, die Sprachsitzung wurde ausgehängt und das Mikrofon war tot. Das Feature widersprach sich selbst: Die Navigation war eingebaut und zerstörte genau das, was sie steuerte. Seiten erscheinen jetzt **im Sprach-Cockpit selbst**, in demselben Panel, in dem der Wissensgraph schon lange angezeigt wird. Die Sitzung läuft weiter, man kann direkt weitersprechen („mach das wieder zu"). Neben Analytics & Co. sind jetzt auch Apps, Audit-Log, System-Health und Schedules ansprechbar, jeweils mit lesbarem Titel statt des rohen Routennamens.
- Neuer Darstellungsmodus `?embed=1`: rendert eine Seite ohne App-Rahmen (keine Sidebar), damit im Panel nicht zwei Rahmen ineinanderstecken. Ändert nichts an der Anmeldung — die Seite verlangt weiterhin einen gültigen Login, es fällt nur die Umrandung weg. Der eingebettete Pfad wird weiterhin gegen die bestehende Allowlist geprüft, bevor er angezeigt wird.

## [1.133.0] — 2026-08-04

### Changed
- **Ein `git pull` reicht jetzt, damit Agenten die neue Anleitung bekommen.** Die Anleitungsdatei liegt im Container, nicht im Repo — sie wurde bisher nur beim Neuerstellen geschrieben. Wer wie üblich `git pull` + Orchestrator-Neustart machte, ließ damit **alle laufenden Agenten mit der alten Anleitung zurück** und hätte jeden einzeln von Hand aktualisieren müssen. Das trifft jede Installation, die sich selbst aktualisiert, und war praktisch nicht erkennbar. Jetzt zieht der Orchestrator die Anleitung beim Start in jeden lebenden Container nach, und `start_agent` tut dasselbe beim Hochfahren eines gestoppten Agenten. Best effort — schlägt es fehl, läuft der Agent trotzdem, mit einer Warnung im Log.

## [1.132.4] — 2026-08-04

### Fixed
- **Custom-LLM-Agenten waren beim Anleitungs-Fix aus v1.132.3 nur mitgemeint, nicht belegt.** Die Pfadwahl steckte als Inline-Ausdruck in zwei Funktionen; geprüft war sie nur für Claude Code und Codex. Beim Kunden laufen die Azure-Modelle als `custom_llm` — genau der ungetestete Zweig. Die Wahl liegt jetzt in `instructions_path(mode)`, einer Funktion, die beide Recreate-Pfade benutzen, mit Tests für alle drei Laufzeiten plus unbekannte Modi (die fallen auf `AGENT.md`, statt gar keine Anleitung zu bekommen — so ist der ursprüngliche Fehler entstanden). 10 Tests statt vorher 5 Quelltext-Prüfungen.

## [1.132.3] — 2026-08-04

### Fixed
- **Codex- und Custom-LLM-Agenten bekamen beim Update NIE eine frische Anleitung.** `update_agent` — die Funktion hinter dem „Update"-Knopf — schrieb die Instruktionsdatei nur, wenn der Agent auf `claude_code` lief. Alle anderen behielten die Anleitung, mit der sie einst erstellt wurden: **jede** spätere Verbesserung ging still an ihnen vorbei, egal wie oft man aktualisierte. Aufgefallen beim Ausrollen von v1.132.2 auf dem Pi — dort laufen 7 von 9 Agenten auf Codex, deren `AGENT.md` war 16 284 Bytes alt, während die `CLAUDE.md` der Claude-Agenten bei aktuellen 20 276 Bytes stand. Jetzt wird pro Laufzeit die richtige Datei geschrieben (`CLAUDE.md` für Claude Code, `AGENT.md` für Codex/Custom-LLM), ohne Modus-Gate. 5 neue Tests.

  Praktische Folge: Die Bridge- und Brain-Anleitungen aus v1.132.0–v1.132.2 greifen bei Codex-Agenten erst mit diesem Release.

## [1.132.2] — 2026-08-04

### Fixed
- **Die Anleitungen aus v1.132.0/v1.132.1 erreichten 7 von 9 Agenten gar nicht.** Sie standen in `agent/claude-global.md` — das ist Claude Codes **eigene** Konfigurationsdatei (`~/.claude/CLAUDE.md`). Auf dem Pi laufen aber sieben von neun Agenten auf **Codex**, das diese Datei nie liest. Die modusübergreifende Anleitung ist `DEFAULT_CLAUDE_MD` (wird als `/workspace/CLAUDE.md` bzw. `/workspace/AGENT.md` in **jeden** Agenten geschrieben) — und dort kam `computer_*` **kein einziges Mal** vor, `brain_related` und `brain_list` ebenfalls nicht. Beide Abschnitte sind jetzt dort ergänzt: Desktop-Bridge mit Entscheidungstabelle und den zwei harten Regeln (bei Fehler melden statt umschwenken; keinen Bildschirm beschreiben, dessen Screenshot fehlschlug) sowie die Zuordnung Brain-Frage → Werkzeug inklusive `LINKED` vs. `SIMILAR`.

## [1.132.1] — 2026-08-04

### Fixed
- **„Womit hängt dieser Punkt zusammen?" nannte andere Knoten, als der Graph zeichnet (#477).** Der Wissensgraph zeichnet **zwei** Kantenarten: explizite `[[wikilinks]]` und semantische Ähnlichkeit. `brain_related` kannte aber nur die semantischen — auf die Frage nach den Verbindungen eines Knotens kam also eine Antwort, die nicht zum Bild passte. `/brain/related` und `/brain/agent/related` liefern jetzt zusätzlich `linked`: die tatsächlich gezeichneten Wikilink-Kanten, in **beide** Richtungen (`outgoing` / `incoming` / `both`). Beide Routen teilen sich dieselbe Auflösung wie `/brain/graph` beim Zeichnen — die gesprochene Antwort und die Grafik können nicht mehr auseinanderlaufen. 9 neue Tests (Richtungen, Selbstbezug, Platzhalter ohne Notiz, Mandantentrennung, Admin-Sicht).
- **Der Agent kannte nur zwei seiner sieben Brain-Werkzeuge (#477).** In `claude-global.md` standen ausschließlich `brain_search` und `brain_contribute`. `brain_get` (voller Inhalt), `brain_related` (Verbindungen) und `brain_list` kamen nicht vor — deshalb beantwortete er „erzähl mir mehr über diesen Punkt" aus einem Suchtreffer-Ausschnitt statt aus dem Volltext, und Verbindungen gar nicht. Jetzt mit Zuordnungstabelle Frage → Werkzeug.

## [1.132.0] — 2026-08-04

Erste Nachbesserungen aus dem Kundentest von v1.131.0.

### Fixed
- **Freigaben kamen im Sprachchat nie beim Nutzer an — der Agent handelte ohne sie (#474).** Ruft der Agent `request_approval`, legt der Orchestrator die Anfrage an und der Agent wartet bis zu 10 Minuten auf Antwort. Der Text-Chat zeigt dafür ein Widget; die **Voice-Session hatte keins** — die Frage lief nur als Text durch die Live-Aktivität, war nicht beantwortbar, und nach dem Timeout machte der Agent weiter. Dasselbe Muster wie beim `AskUserQuestion`-Fehler in v1.130.2: eine Rückfrage, die nirgends ankommt, wird zur stillen Selbstermächtigung. Die Sprachsitzung zeigt jetzt dieselbe Freigabe-Karte mit Frage, Kontext und den Antwortmöglichkeiten aus `request_approval` — und pollt durchgehend, nicht nur während eines laufenden Turns (im Sprachmodus arbeitet der Agent oft weiter, während man schon wieder redet).
- **Agent bediente den eigenen Container statt den Rechner des Nutzers (#475, Teilfix).** Die 16 Bridge-Werkzeuge sind registriert und finden ihre Session selbst — aber in `claude-global.md` (der Anleitung, die als `CLAUDE.md` im Agent-Image steckt) kam die Desktop-Bridge **kein einziges Mal** vor. Bei rund 128 verfügbaren Werkzeugen landete „öffne die URL in meinem Browser" folglich beim serverseitigen Browser-Skill. Neuer Abschnitt mit Entscheidungstabelle (fremder Rechner vs. eigener Container), interner URL als klarstem Fall, und zwei harten Regeln: bei Fehler **melden statt still umschwenken**, und **niemals einen Bildschirm beschreiben, dessen Screenshot fehlgeschlagen ist** — Letzteres erklärt die Halluzination im Kundentest.

  *Noch offen an #475:* Codex- und Custom-LLM-Agenten haben die `computer_*`-Werkzeuge gar nicht (`agent/app/tools/definitions.py` enthält keine) — dort hilft die Anleitung nicht.

### Deployment
- `claude-global.md` steckt im Agent-Image: nach diesem Release **Image neu bauen und alle Agenten neu erstellen**, sonst greift die Anleitung nur bei neuen Containern. Bei Codex-Agenten gestaffelt (geteilter Refresh-Token).

## [1.131.0] — 2026-08-04

### Added
- **Apps freigeben (#467).** Bisher kam an eine vom Agenten deployte App ausschließlich ihr Besitzer — alle anderen bekamen `Not authenticated`, auch wenn sie den Link hatten. Jetzt vergibt der Besitzer gezielt Zugriff, in drei Stufen:
  - **Einzelne Person** — namentlich, Login nötig.
  - **Alle eingeloggten Nutzer** — jeder mit Konto auf der Plattform.
  - **Öffentlicher Link** — Token im Link, **ohne** Anmeldung, mit **Pflicht-Ablaufdatum** (1–90 Tage). Der Token wird nur einmal beim Anlegen angezeigt und danach nie wieder ausgeliefert; ein path-gebundenes HttpOnly-Cookie trägt ihn über die Unterressourcen der Seite, damit auch JS/CSS/Bilder laden.

  **Default bleibt deny:** ohne passenden Eintrag ist Schluss. Eine Freigabe erlaubt ausschließlich das **Öffnen** — Starten, Stoppen, Neu bauen, Logs, Entfernen und Weiter-Freigeben bleiben ownership-gated. Freigegebene sehen auch nicht, wem die App sonst noch offensteht.
- **Detailfenster auf `/apps`.** Klick auf eine App-Karte öffnet Eckdaten (Agent, Status, Workspace-Pfad, Compose-Projekt), alle Container mit Image/Port/Zustand — und für den Besitzer die Freigabe-Verwaltung samt Zurückziehen.
- Namentlich bzw. an alle Eingeloggten freigegebene Apps erscheinen in der **Apps-Übersicht der Empfänger**, markiert als „für mich freigegeben" und ohne steuernde Schaltflächen. Öffentliche Links tauchen bewusst in keiner Liste auf — sie hängen am Token, nicht an einer Person.

### Security
- Die beiden SSRF-Gates des App-Proxys bleiben unverändert wirksam: eine Freigabe öffnet nur den **Zugriffsweg**, nie ein anderes **Ziel**. Ein gültiger Token für App A schließt App B nicht auf, und ein Container ohne passendes `com.docker.compose.project`-Label bleibt auch mit Token gesperrt (Plattform-Container wie Postgres/Redis sind damit unerreichbar).
- **Der Link-Token erreicht die App auf keinem Weg.** Er ist das Geheimnis, das die App absichert — und die App ist agent-geschriebener Code, dem man nicht mehr geben darf als nötig. Drei Wege waren dicht zu machen:
  - *Adresszeile:* beim ersten Aufruf wandert der Token in ein path-gebundenes HttpOnly-Cookie und die Seite wird ohne den Parameter neu ausgeliefert (303). Sonst bliebe er in `document.location` stehen und der Browser hängte ihn als `Referer` an jede Unterabfrage.
  - *Weitergereichte Header:* `referer` fliegt jetzt zusammen mit `cookie` und `authorization` raus; die Antwort trägt zusätzlich `Referrer-Policy: no-referrer`.
  - *Weitergereichte Query:* der Parameter wird **bedingungslos** aus dem, was an die App geht, entfernt — unabhängig von HTTP-Methode und davon, welche Freigabe-Stufe den Zugriff erlaubt hat. Er heißt deshalb `__aie_share` statt `t`, damit er nicht mit einem Parameter der App kollidiert. Wiederholte Query-Keys (`?a=1&a=2`) überstehen die Filterung unverändert.
- **In der Datenbank steht nur der SHA-256 des Tokens**, nie der Token selbst — ein Leak oder altes Backup gibt damit keine funktionierenden Links her. Verglichen wird konstantzeitig (`hmac.compare_digest`).
- **Fehlerantworten für Nicht-Besitzer sind ununterscheidbar.** „Container gibt es nicht", „falsches Projekt" und „nicht freigegeben" liefern dieselbe Antwort; sonst könnte, wer eine einzige Freigabe für einen Agenten besitzt, über die Statusunterschiede dessen übrige Apps durch Namensraten kartieren. Nur der Besitzer bekommt die genaue Ursache.
- Vor-Gate im Proxy: existiert für den Agenten überhaupt keine gültige Freigabe, wird abgelehnt **bevor** Docker gefragt wird.
- Neue `optional_auth`-Dependency liefert `None` statt 401 und ist ausschließlich am App-Proxy im Einsatz, der seine Autorisierung selbst durchführt. Der Setup-Modus-Platzhalter (vor der ersten Registrierung gibt `get_current_user` für einen Request *ohne* Token einen Admin heraus) zählt dort ausdrücklich als anonym — das Hochziehen der Plattform darf kein Nebeneingang in fremde Apps sein.
- Freigeben und Zurückziehen landen im **Audit-Log** (`app_shared` / `app_share_revoked`) mit Reichweite, Empfänger und Ablauf — **ohne** den Token.
- Bekannte Eigenschaft eines Links, der ohne Login trägt: er steht beim **allerersten** Aufruf einmal im Zugriffs-Log des Orchestrators, bevor die Umleitung greift. Wer dieses Log lesen kann, hat ohnehin Serverzugriff — in der Datenbank steht nur noch der Hash. Wer das nicht will, nutzt die Stufe „alle eingeloggten Nutzer".
- 40 neue Tests decken die Zugriffsmatrix gegen echtes SQL ab (Besitzer/namentlich/alle-Eingeloggten/öffentlich × anonym/eingeloggt × gültig/abgelaufen × richtiger/falscher Token × eigenes/fremdes Projekt) plus Referer-/Cookie-Weitergabe, Token-Entfernung aus der URL und die Ununterscheidbarkeit der Absagen — Suite jetzt 678 Tests.

### Docs
- Benutzerhandbuch: neues Kapitel **32. Apps (Ergebnisse deiner Agenten öffnen & freigeben)** — Klick-für-Klick inkl. Freigabe-Stufen, Zurückziehen und Sicherheitshinweis zum öffentlichen Link.

---

## [1.130.2] — 2026-08-04

### Fixed
- **Rückfragen des Agenten kamen nie beim Nutzer an — und der Agent riet einfach weiter.** `AskUserQuestion` ist ein eingebautes Tool der Claude-Code-CLI und erwartet ein interaktives Terminal. Headless (`-p`) gibt es niemanden, der antworten kann: die CLI liefert sofort den Platzhalter `"Answer questions?"` zurück, der Agent wertet das als Antwort und baut mit selbst erratenen Entscheidungen weiter — während im Chat nur rohes JSON steht, das man nicht beantworten kann. Das Tool ist jetzt in beiden CLI-Pfaden (Chat + Task) abgeschaltet; stattdessen weist die CLAUDE.md an, Rückfragen als **normalen Text** zu stellen und dort zu stoppen (im Hintergrund-Task: sichere Default-Annahme treffen und die getroffene Entscheidung im Ergebnis benennen). Der normale Chat-Weg inkl. Live-Steering greift damit wieder.

---

## [1.130.1] — 2026-08-04

### Changed
- **Denktiefe-Auswahl im Chat neu gestaltet.** Das native `<select>` passte optisch nicht zwischen die Pill-Buttons der Eingabezeile. Jetzt ein Icon-Button im gleichen Stil wie Anhang/Mikrofon (Gehirn-Symbol, violett hervorgehoben sobald eine Stufe gewählt ist, mit Kurzlabel) und ein eigenes Popover darüber statt des Browser-Dropdowns.

---

## [1.130.0] — 2026-08-04

Nachbesserungs-Release aus dem Kundentest von v1.129.0 — inklusive zweier Punkte, die in v1.129.0 falsch gebaut waren.

### Added
- **Reasoning-Tiefe pro Nachricht, gesteuert vom Nutzer** (Standard / aus / kurz / mittel / gründlich) direkt neben der Chat-Eingabe — wie das Thinking-Level in ChatGPT, Claude Code oder Codex. Wirkt in **allen drei Harnesses**: `MAX_THINKING_TOKENS` (claude_code), `-c model_reasoning_effort` (codex_cli), `reasoning_effort` am Provider (custom_llm). Der Wert wird serverseitig gegen eine Whitelist geprüft, bevor er in CLI-Flags oder Request-Bodies landet.
  - *Korrigiert v1.129.0:* dort saß die Einstellung nur im Anlege-Dialog für `custom_llm`+OpenAI, war nachträglich nicht änderbar und für claude_code/codex_cli technisch wirkungslos.
- **Computer-Use-Sessions überleben Neustarts.** Metadaten liegen in Redis, die Bridge hängt sich beim Reconnect mit derselben ID wieder an. `POST /sessions` gibt standardmäßig die **bestehende** Session zurück statt jedes Mal eine neue ID (`?reuse=false` erzwingt eine neue) und bevorzugt dabei die mit aktiver Bridge. Timeout läuft ab **letzter Aktivität** (12 h) statt ab Erstellung (vorher 30 min ab `created_at` — das killte Sessions mitten in der Arbeit); Aktions-Limit 50 → 500 mit Reset bei jedem Bridge-Attach.
- **`list_my_team`** als MCP-Tool für den Agenten (`GET /teams/mine`, live aufgelöste Namen/Rollen/Lead).

### Fixed
- **`/agents/{id}?tab=speech` landete trotzdem im Chat-Tab.** Der Deep-Link las `window.location` mit `[]`-Dependency — der App Router remountet bei reiner Query-Änderung nicht, der Effect lief bei clientseitiger Navigation also nie. Zusätzlich gegen die im aktuellen Modus *sichtbaren* Tabs validiert (sonst hätte der Fallback-Effect 8 von 15 Tabs sofort zurückgesetzt).
- **Voice-Navigation im Second-Brain-Graph tat sichtbar nichts.** Der Fokus lief, bevor die Force-Simulation dem Knoten Koordinaten gegeben hatte (Kamera flog nach 0,0,0), und wurde danach von `zoomToFit` (600 ms) und dem Center-Orbit (1600 ms) überschrieben. Jetzt wartet die Kamerafahrt auf die fertige Positionierung und parkt solange beide Timer. Außerdem: Brain-Auflösung per `allSettled` (ein fehlschlagender Mounts-Call verwarf vorher die komplette Brain-Liste) und der `query`-Parameter steht jetzt explizit im System-Prompt.
- **Lead-Agent sah neu zum Team hinzugefügte Mitglieder nicht.** Kein Cache-Problem: die CLAUDE.md schreibt jedem Lead `list_my_team` vor — **dieses Tool existierte im Tool-Server des Agenten gar nicht**, nur im eingehenden MCP-Server für externe Clients. Der Lead lief in „Unknown tool" und antwortete aus dem Gedächtnis bzw. aus dem beim Container-Start eingefrorenen `/shared/team.json`.
- **Windows-Bridge, drei Ursachen:** (1) `save_config` hatte weder `try/except` noch `encoding` und lief in einem Daemon-Thread — schlug das Schreiben fehl (OneDrive-Profil, Rechte), starb der Thread lautlos und die Einstellungen waren nach dem Neustart weg; jetzt atomar, mit sichtbarer Fehlermeldung. (2) `_status` wurde **nie** auf „connected" gesetzt, weshalb das Statusfenster dauerhaft „Verbinde…" zeigte, auch bei intakter Verbindung — die Bridge meldet ihren Zustand jetzt zurück und beide Fenster fragen zusätzlich den Server. (3) Der „Verbinden"-Knopf rief `ensure_session` nicht auf (anders als macOS), tote Sessions wurden also endlos weiter angewählt. Dazu: `1008`-Closes (Session unbekannt, fremder Nutzer) landeten in derselben stillen Endlos-Reconnect-Schleife wie ein normaler Abbruch, obwohl ein Retry dort nie erfolgreich sein kann.
- **Foundry-GPT-Agenten kippten still in den falschen Harness.** Beim Container-Neubau wurde der Modus aus dem `provider_type` des *Modell*-Eintrags abgeleitet statt aus dem Account — ein GPT-Deployment auf einem Azure-Account schaltete damit auf Codex-CLI, wodurch Endpoint und Key nicht mehr injiziert wurden und die AI-Account-Karte im UI verschwand (also nicht mehr reparierbar war).

### Changed
- Discovery-Meldung und Foundry-Block sagen jetzt ausdrücklich, dass nur Anthropic/OpenAI direkt erkannt werden und eigene Azure-Foundry-/Bedrock-/Vertex-Deployments unter **AI-Accounts** gehören. Vorher wirkte es wie ein Fehler, dass eine verbundene Foundry-Ressource keine GPT-Modelle zeigt.

---

## [1.129.0] — 2026-08-03

Sammel-Release aus dem Kundenfeedback vom 03.08. (CoWork/ComputeUse, Plattform, Voice).

### Added
- **Replay-Modus: Ablauf einmal zeigen → wiederverwendbarer Skill.** Eine Computer-Use-Session lässt sich aufzeichnen (Aktion + Screenshot pro Schritt) und in einen Skill verwandeln.
  - **Zwei Aufnahmequellen:** die eigenen Aktionen des Agenten *und* — neu — der **Mensch macht den Ablauf von Hand vor** (`InputRecorder` in der Bridge via pynput; getippter Text wird als ganze Strings geflusht, nicht als roher Keystroke-Strom).
  - **Skill-Erzeugung:** ein Vision-Modell liest die Screenshots und schreibt ein SKILL.md in **Prosa** — semantische Klickziele („der Speichern-Button oben rechts") statt Pixel-Koordinaten, erkannte variable Werte als `{parameter}`. Abgespielt wird über die normalen `computer_*`-Tools des Agenten, dadurch überlebt der Skill Fenster-/UI-Drift und braucht keine zweite Ausführungs-Engine.
  - Landet als **Entwurf** (nicht aktiv) im Skill-Marktplatz — maschinell generierter Inhalt wird erst nach Sichtung nutzbar.
  - Eigene Capability `input_capture` (Default **aus**, Risiko hoch): während der Aufnahme sieht die Bridge alle Klicks/Tastatureingaben des Rechners. Stoppt zwangsweise bei Verbindungsabbruch.
- **Model-Router (OpenWebUI-Stil), opt-in pro Agent.** Wählt pro Task ein Modell anhand des Prompt-Inhalts (einfach/normal/komplex) — deterministische Heuristik, **kein** zusätzlicher LLM-Call. Läuft **vor** der Budget-Policy: Inhalt wählt die Stufe, Budget-Erschöpfung übersteuert weiterhin.
- **Team-Lead sieht die Tasks seiner Subagenten.** `GET /teams/{id}/tasks` (Lead + alle Mitglieder aggregiert) + MCP-Tool `list_team_tasks` (der Agent findet sein Team selbst, braucht keine IDs). Team-Karten mit Task-Ausklapper; die Task-Liste zeigt den Agent-**Namen** statt der rohen ID.
- **Reasoning-Effort für GPT-Modelle** (low/medium/high) durch die ganze Kette — Responses-API (GPT-5/codex) und Chat-Completions (o1/o3/o4). Für andere Modelle wirkungslos ignoriert.
- **Voice-Navigation im Second-Brain-Graph.** `control_ui` bekommt einen `query`-Parameter: „öffne den Graphen und such den Eintrag zu X" fokussiert den passenden Knoten automatisch (Titel > Tags > Pfad).
- **Aktivität + Kosten pro Nutzer** in der Admin-Nutzerverwaltung (`last_active_at` wurde bereits getrackt, aber nie ausgeliefert; Kosten über alle Agents eines Nutzers aggregiert).
- **Direktlink in einen Agenten-Sub-Tab** — `/agents/{id}?tab=speech` springt direkt in den Sprachchat.

### Fixed
- **`computer_close_app` fehlte im MCP-Server.** Der Agent kannte die Fähigkeit aus seiner Tool-Beschreibung, der Aufruf lief aber in „Unknown tool" — `close_app` war im Mai nur in der Bridge nachgerüstet, nie im MCP-Server registriert. Zusätzlich melden `open_app`/`close_app` jetzt **echte** Fehler statt immer `ok:true` (inkl. klarer Meldung bei fehlender macOS-Automation-Berechtigung).
- **„Neue Version verfügbar" blieb nach einem VERSION-Bump hängen.** `AGENT_VERSION` war beim Modul-Import eingefroren — auf einem laufenden Orchestrator wirkte sich ein Bump weder auf das Banner noch auf den Pro-Agent-Update-Check aus. Wird jetzt bei jedem Aufruf frisch gelesen.
- **Live-Desktop-Ansicht fühlte sich nicht live an.** Der Screenshot-Cache wird jetzt **event-getrieben** direkt nach jeder bildschirmverändernden Aktion aktualisiert; der Frontend-Poll dient nur noch als Fallback (4s → 1s).

### Security
- **IDOR auf `GET /teams/{id}/tasks` geschlossen.** Der neue Endpoint gab Task-Prompts/-Ergebnisse ohne jede Zugehörigkeitsprüfung heraus — jeder authentifizierte Nutzer/Agent hätte mit einer bekannten `team_id` fremde Team-Inhalte lesen können. Agenten müssen jetzt Lead/Mitglied sein, Menschen Admin oder Besitzer mindestens eines Team-Agenten.
- **AppleScript-Injection in der Computer-Use-Bridge.** `open_app`/`close_app`/`ax_tree` interpolierten den App-Namen ungefiltert in AppleScript-String-Literale — ein Name mit `"` konnte aus dem Literal ausbrechen und beliebiges AppleScript (u. a. `do shell script`) ausführen.

---

## [1.128.0] — 2026-08-03

### Added
- **Workflow-Organisation: Ordner + Freigaben.** Workflows lassen sich in **Ordner** („Projekte") gruppieren und mit **einzelnen Personen** teilen (Rolle Ansehen/Bearbeiten) — direkt oder über eine **Ordner-Freigabe** (teilt alle enthaltenen Workflows = Projekt-Zusammenarbeit).
  - Modelle `WorkflowFolder` + `WorkflowShare` (+ `folder_id` an Workflow); Zugriffsmodell owner/editor/viewer (eigene + direkt geteilte + über geteilten Ordner). API: Ordner-CRUD, Workflow-/Ordner-Freigabe + Widerruf, minimaler Nutzer-Directory-Endpoint für den Teilen-Picker.
  - Frontend: Ordner-Leiste in der Workflows-Liste (Alle / Ohne Ordner / Mit mir geteilt / je Ordner), Workflow per Dropdown in Ordner verschieben, **Teilen-Dialog** (Person + Rolle, Freigaben verwalten). Geteilte „Ansehen"-Workflows öffnen read-only.
  - Ensure-Block für die neuen Tabellen/Spalte.
  - Offen (Folge): Freigabe an ganze **Team-Gruppen** von Nutzern (es gibt aktuell keine Nutzer-Gruppen-Entität; das bestehende „Team" ist ein Agenten-Team).

---

## [1.127.0] — 2026-08-02

### Added
- **Visueller Workflow-Builder (n8n-Stil)** (Issue #394) + **Cron-Auto-Trigger** (#392). Der Drag-&-Drop-Editor auf React Flow, aufgesetzt auf die Engine aus v1.126.0 — eine Definition, keine Doppel-Logik.
  - Neue Seite **Workflows** (Sidebar → Automation): Liste + „Neuer Workflow".
  - **Canvas-Editor** (`/workflows/{id}`): Bausteine **Aufgabe / Bedingung / Warten** per Klick hinzufügen, per Ziehen verbinden (Bedingung mit „ja"/„nein"-Ausgängen), pro Baustein ein Konfig-Panel (Agent, Prompt mit `{{schritt}}`-Platzhaltern, Operator/Wert, Sekunden). **Speichern** und **Ausführen** direkt aus dem Canvas; der laufende Schritt wird live hervorgehoben, Ergebnisse pro Schritt im Panel.
  - Graph ↔ Definition-Mapping (Knoten/Kanten ↔ `steps`+`next/true/false`); Start = Knoten ohne eingehende Kante.
  - **#392 Cron-Trigger:** Workflows mit `trigger.cron` starten automatisch (croniter, verpasste Slots werden einmalig nachgeholt). Inbound-Webhook-Trigger bleibt offen.

---

## [1.126.0] — 2026-08-02

### Added
- **Workflow-Engine — Basis** (Issue #392, Fundament für den visuellen Builder #394). Deklarative mehrstufige Agenten-Workflows, die wirklich laufen:
  - Modelle `Workflow` (JSON-Definition: Start-Schritt + Schritt-Map) + `WorkflowRun` (ein Lauf mit Kontext/Status). Schritt-Typen v1: **agent_task** (erzeugt eine Agenten-Task, wartet auf deren Abschluss), **condition** (strukturierte Bedingung `contains/equals/…` → true/false-Zweig), **wait** (Verzögerung). Prompt-Platzhalter `{{schritt_id}}` setzen Ergebnisse vorheriger Schritte ein.
  - `workflow_engine`: sichere Zustandsmaschine (kein `eval`), pro Scheduler-Tick ein Zug — erzeugt Tasks, wertet Bedingungen aus, springt nach Task-Abschluss weiter, Guard gegen Endlosschleifen.
  - API `/workflows` (CRUD, require_auth, Definition-Validierung) + `POST /workflows/{id}/run` + Lauf-Historie `GET /workflows/{id}/runs` und `/workflows/runs/{id}`.
  - Ausführung via neuem Scheduler-Sub-Tick `_advance_workflow_runs`; Tabellen via always-run Ensure-Block. 12 Unit-Tests.
  - Der visuelle Drag-&-Drop-Builder (#394) editiert später dieselbe Definition — eine Wahrheit, keine Doppel-Logik.

---

## [1.125.2] — 2026-08-02

### Fixed
- **Chat-Wiedereintritt zeigt laufenden Turn.** Beim Verlassen und erneuten Betreten eines Gesprächs (oder Umschalten in der Seitenleiste) sah man vorher nicht, dass der Agent gerade an dieser Unterhaltung arbeitet — die neu aufgebaute WebSocket-Verbindung verwarf die Live-Events des fremd gestarteten Turns, und History enthielt den unfertigen Turn nicht. Neu: der Chat pollt den Agent-Status; arbeitet der Agent an DIESER Session, erscheint ein Live-Indikator „Agent arbeitet gerade an dieser Unterhaltung…", und sobald der Turn fertig ist, wird die History automatisch nachgeladen und die Antwort erscheint. (Der vollständige Live-Token-Stream beim Resume bräuchte einen Agent-seitigen Puffer — als Folgeaufgabe notiert.)

---

## [1.125.1] — 2026-08-02

### Added
- **„Planen"-Button im Chat** (Ergänzung zu #386). Neben „Senden" gibt es jetzt einen **Planen**-Button: die Nachricht wird mit einer „nur planen, nichts ausführen"-Anweisung an den Agenten geschickt — er beschreibt die Schritte, die er gehen würde (Tools, Dateien, externe Aktionen, Aufwand), statt sie auszuführen. Die angezeigte Nutzer-Nachricht bleibt wie getippt; nur was der Agent empfängt, wird umhüllt. Schneller Weg zur Vorschau direkt im Gespräch.

---

## [1.125.0] — 2026-08-02

### Added
- **Dry-Run / Simulationsmodus** (Issue #386, Vision-Roadmap Säule „Vertrauen & Kontrolle"). Vor der echten Ausführung kann eine Aufgabe als **Vorschau** laufen: der Agent erstellt einen strukturierten Ausführungsplan (Schritte, betroffene Dateien/Befehle, externe Aktionen, Aufwands-/Risiko-Schätzung) und führt **nichts** aus.
  - Task-Erstellung hat einen Schalter **„Dry-Run — nur Vorschau"**; der Prompt wird serverseitig mit einer Planungs-Anweisung umhüllt (kein Agent-Image-Change), Original-Prompt bleibt in `metadata` erhalten.
  - Die Task-Detailseite zeigt ein **Vorschau-Banner** mit **„Jetzt wirklich ausführen"** — `POST /tasks/{id}/execute` legt dieselbe Aufgabe mit dem Original-Prompt und demselben Agenten regulär an.
  - `TaskCreate.dry_run`, `TaskResponse.dry_run/original_prompt` + Task-Model-Properties; 3 Unit-Tests.

---

## [1.124.4] — 2026-08-02

### Changed
- **Audit-Detail-Modal selbsterklärender.** Statt eines rohen Enum-Werts + JSON zeigt es jetzt einen **Klartext-Titel + Ein-Satz-Erklärung** pro Ereignistyp (z. B. „Reflexion — Änderung angewendet"), **freundliche Feld-Labels** (run_id → Reflexions-Lauf, applied → Ja/Nein) und ein verständliches Ergebnis (Erfolgreich/Blockiert). Die Rohdaten (JSON) bleiben als einklappbares Detail erhalten.

---

## [1.124.3] — 2026-08-02

### Fixed
- **Audit-Detail-Modal zeigte keine Details.** Der `/audit/logs`-Endpoint lieferte das `meta`-Feld unter dem Schlüssel `details` statt `meta` — das Frontend las `meta` und bekam `null` („Keine Klassen aufgezeichnet"). Endpoint liefert jetzt beides (`meta` kanonisch, `details` legacy) + `exit_code`.

### Added
- **DLP-Treffer zeigen jetzt einen maskierten Ausschnitt** (`df***as`): der Scanner erfasst pro Klasse die gefundenen Stellen und speichert davon einen maskierten Auszug (erste 2 + `***` + letzte 2 Zeichen, kurze Werte weniger) im Audit-`meta.samples`. Damit sieht man im Detail-Modal **was** konkret erkannt wurde — der vollständige Wert (Key/IBAN etc.) wird weiterhin **nie** gespeichert. Auch der `/dlp/test`-Scan liefert die maskierten Ausschnitte. 4 neue Unit-Tests (Maskier-Format, kein Voll-Wert, Cap).

---

## [1.124.2] — 2026-08-02

### Added
- **Audit-Log: klickbare Zeilen mit Detail-Modal.** Ein Klick auf einen Eintrag öffnet ein Modal mit allen Feldern (Zeit, Agent, Kanal/Command, Ergebnis, Task, Exit-Code). Für **DLP-Treffer** zeigt es zusätzlich die erkannten Datenklassen mit Anzahl + Aktion (block/mask/log) — plus den expliziten Hinweis, dass der konkrete Wert bewusst **nicht** gespeichert wird.
- **Audit Log direkt in der linken Admin-Sidebar** (`/audit`) — Schnellzugriff auf den Compliance-Trail, statt nur als Tab in der Admin-Konsole.

---

## [1.124.1] — 2026-08-02

### Added
- **DLP-Admin-UI** (Follow-up zu #388): neuer Admin-Tab „DLP-Filter" — Aktivieren/Deaktivieren per Schalter, Aktion pro Datenklasse (allow/log/mask/block) einstellbar, Test-Scan-Feld (welche Klassen erkennt ein Beispieltext?) und Liste der letzten DLP-Treffer (ohne Klartext). Nutzt die `/dlp`-Admin-API; damit ist der Egress-Filter ohne curl konfigurierbar.

---

## [1.124.0] — 2026-08-01

### Added
- **DLP-Egress-Filter** (Issue #388, Vision-Roadmap Säule „Vertrauen & Kontrolle"). Ausgehender, agent-generierter Text wird vor dem Versand auf PII/Secrets gescannt — das Argument für DSGVO-sensible Kunden:
  - `core/dlp.py`: reiner, deterministischer Scanner (`classify`/`mask`) für die Klassen **secret** (Credential-Pattern aus `log_redaction` wiederverwendet), **iban**, **credit_card** (mit Luhn-Prüfung gegen False-Positives), **email**, **de_tax_id** (11-stellig). Plus DB-Auswerter `evaluate_egress`, der die Aktion pro Klasse aus `DlpRule` auflöst (agent-spezifisch > global > Default) und **allow/log/mask/block** anwendet.
  - **Audit ohne Klartext:** jeder Treffer landet als `AuditLog` (`dlp_blocked`/`dlp_masked`/`dlp_flagged`) — nur Klassen + Anzahl, niemals der sensible Wert.
  - **Egress-Hooks:** Agent→Telegram (`_send_chunked`, scannt den vollen Text vor dem Chunking) + Operator-Notifications (`bot._listen_notifications`). Blockierte Nachrichten werden durch einen Hinweis ersetzt, maskierte redigiert gesendet.
  - **Opt-in & fail-open:** Passthrough, solange `dlp_enabled` nicht gesetzt ist (bestehende Deployments unberührt); interne Fehler blockieren niemals den Versand.
  - **Admin-API** `/dlp` (require_admin): Toggle, Regeln pro Klasse/Agent (CRUD), Audit-Ansicht, `/dlp/test`-Scan-Vorschau. `dlp_rules`-Tabelle via always-run Ensure-Block + Default-Seed.
  - 15 Unit-Tests für Scanner + Policy-Auflösung (Luhn, IBAN-vs-CC-Abgrenzung, Precedence, block/mask/log).

---

## [1.123.0] — 2026-08-01

### Added
- **Decision-Trace / Zeitreise** (Issue #387, Vision-Roadmap Säule „Vertrauen & Kontrolle"). Der bestehende Schritt-Replay wird zur vollen, nachvollziehbaren Task-Timeline ausgebaut — „warum hat der Agent das getan?" auf Knopfdruck:
  - Neuer `trace_service.assemble_trace` gruppiert die roh geloggten `TaskStep`s (Gedanke → Tool-Call → Ergebnis), faltet jedes `tool_result` in seinen `tool_call` (gematcht über `tool_use_id`), berechnet die Dauer pro Schritt aus den Timestamps und hängt die Governance-Audit-Events (`AuditLog`, task-scoped) + eine Kosten-/Token-Summe an. Rein lesend, keine Nebenwirkungen.
  - Endpoints `GET /tasks/{id}/trace` (angereicherte Timeline) und `GET /tasks/{id}/export?format=json` (Download); Auth + Ownership analog `/steps`.
  - Frontend (Task-Detail): **Play/Pause**-Abspielen der Zeitreise, **Dauer pro Schritt**, ein **Governance-&-Kosten-Strip** (blockierte/fehlgeschlagene Aktionen sichtbar) und **Export** als JSON bzw. PDF (Browser-Druck).
  - 5 Unit-Tests für die Trace-Assembly (Folding, Orphan-Result, Dauer, Governance/Summary).

---

## [1.122.0] — 2026-08-01

### Added
- **Second Brain Stufe 1 vervollständigt** (Issue #157). Aufbauend auf dem Memory-Auto-Linker (v1.121.0) sind jetzt alle Abnahmekriterien der Stufe 1 erfüllt:
  - **Cross-System-Related von der Wissens-Seite:** der bestehende Endpoint `GET /brain/related/{id}` liefert zusätzlich zu semantisch verwandten Wissenseinträgen nun auch verwandte **Agent-Memories** (der eigenen Agenten) — die Brücke zwischen den zwei Silos, jetzt in beide Richtungen (Memory→Wissen war in v1.121.0, Wissen→Memory neu). Kein Parallel-Endpoint gebaut, der vorhandene wurde additiv erweitert.
  - **„Semantisch verwandt"-Panel** in der Knowledge-Detail-Ansicht: zeigt verwandte Wissenseinträge (klickbar) + Agent-Memories mit Similarity-Prozent.
  - **Graph-View unterscheidet Kantentypen:** Backlinks (durchgezogen, blau, mit Fluss-Partikeln) vs. semantische Kanten (2D echt gestrichelt + violett, 3D violett/leiser ohne Partikel). Der `/brain/graph`-Endpoint lieferte den `type` bereits — die Unterscheidung war rein visuell im Renderer nachzuziehen.
  - **Tests:** 8 Unit-Tests für den Auto-Linker (Threshold-Grenze, manuelle Links respektiert, Backfill-Aggregation, Fehler-Rollback).

### Docs
- **COMPARISON.md** mit Stand-Datum **01.08.2026** versehen.

---

## [1.121.1] — 2026-08-01

### Fixed
- **Knowledge Base zeigte nur 100 von tatsächlich vorhandenen Einträgen.** Der Listen-Endpoint hat einen Default `limit=100`, das Frontend übergab keinen Wert und zeigte im Badge zudem `entries.length` (die geladenen 100) statt des vom Endpoint korrekt gelieferten `total`. Bei 176 Einträgen fehlten damit 76 in der UI. Neu: `getAllKnowledgeEntries` paged transparent über den bereits vorhandenen `offset`-Parameter (Seiten à 200) bis alle Einträge geladen sind, und das Badge zeigt das echte `total`. Skaliert über das 500er-Server-Cap hinaus, da jede Einzelanfrage ≤500 bleibt.

---

## [1.121.0] — 2026-08-01

### Added
- **Second Brain: automatische Verknüpfung von Agent-Memories** (Issue #157). Der Memory-Graph (`agent_memory_links`) wurde nie befüllt — jede Memory stand isoliert, das „zweite Gehirn" hatte keine Kanten. Neu: `memory_linker` erzeugt beim Speichern einer Memory semantische Kanten aus Embedding-Kosinus-Ähnlichkeit (Schwelle 0.75, max. 10 Links, `relation='semantic_similar'`, `auto_generated=true`) — analog zum bestehenden `brain_linker` für Knowledge-Einträge. Manuelle Links werden respektiert (ein Paar mit bereits existierender Kante in einer der beiden Richtungen wird übersprungen). Verzahnung statt Silo: der neue Endpoint `GET /memory/{id}/related` liefert **sowohl** semantisch ähnliche Memories **als auch** verwandte Second-Brain-Wissenseinträge (Cross-System-Brücke Memory↔Knowledge). Im Frontend zeigt jede Memory einen neuen „Verknüpft"-Aufklapper (Memories + Wissen mit Similarity-Prozent). `POST /memory/relink` (Admin) backfüllt die bestehende Wissensbasis einmalig. Immer-laufender Ensure-Block ergänzt die Spalten `similarity` + `auto_generated` an `agent_memory_links` (create_all ist nur Fresh-DB-Fallback).

---

## [1.120.0] — 2026-08-01

### Added
- **Per-Agent „Immer an" (always-on).** Neuer Config-Schalter, der einen Agenten von BEIDEN Idle-Sweeps ausnimmt (User-Lifecycle-Auto-Stop bei inaktivem User + Idle-Stop-Watchdog) — für Agenten, die dauerhaft laufen sollen, unabhängig von der App-Aktivität des Owners. `PATCH /agents/{id}/always-on`, Toggle in den Agent-Settings.

### Security
- **#192 statischer Gate erweitert: kompilierte Executables in Skill-Attachments werden geblockt.** Ein Skill liefert Doku + Text-Templates, kein Binary — ELF/PE/Mach-O/Wasm-Payloads (Dropper-Vektor) werden per Magic-Bytes abgelehnt (PE erfordert `MZ`+`PE\\0\\0`-Signatur, damit Text mit „MZ" kein False-Positive ist). Neue Unit-Tests (31 grün) decken post-install-Reject, Allowlist-Bypass, Hook-Dateien, Executable-Attachments und Benign-Akzeptanz ab. Offen bleiben die größeren #192-Teile: Runtime-Egress-Allowlist (überschneidet #194) + Review-Queue-UI.

---

## [1.118.7] — 2026-08-01

### Added
- **Skill-Quellen ohne Release erweiterbar** (Issue #371 Phase 1, gemeldet von tiko0). Die Repos, die der Skill-Crawler durchsucht, waren eine hartkodierte Python-Konstante (`SKILL_REPOS` in `skill_crawler.py`) — jede neue Quelle bedeutete Code-Änderung + Rebuild + Release. Neu: zusätzliche Quellen sind über die Einstellung `SKILL_REPOS` (kommaseparierte `owner/repo`-Einträge, in `docker-compose.yml` durchgereicht) konfigurierbar. Die Einträge werden **additiv** zu den eingebauten Defaults hinzugefügt, dedupliziert und reihenfolge-erhaltend — leere Konfiguration verhält sich exakt wie die bisherige Liste, bestehende Deployments bleiben unberührt. Damit wird u. a. der offene *„Evaluate: <repo>"*-Backlog zu je einem Konfig-Eintrag statt einem Commit. (Phase 2 = clone-basiertes Fetching für Nicht-GitHub-/private-Hosts, Phase 3 = UI-Verwaltung — noch offen.)

---

## [1.118.6] — 2026-08-01

### Added
- **Computer-Use-Bridge: konfigurierbare Handshake-Header für Identity-Aware-Proxies** (Issue #374, gemeldet von tiko0). Die Bridge sendete beim WebSocket-Handshake nur den `Authorization`-Bearer — hinter einem Identity-Aware-Proxy (Cloudflare Access, Google IAP, oauth2-proxy, Authelia, Teleport) beantwortet der Proxy den Handshake dann mit einem eigenen Login-Challenge, den ein Non-Browser-Client nicht lösen kann. Bisheriger Workaround: den Proxy für die Bridge umgehen (zweiter Hostname mit *Bypass*) — genau die Schutzschicht, die man bewusst deployt hat, wird durchlöchert. Neu: zusätzliche Header sind aus drei Quellen konfigurierbar (Präzedenz aufsteigend): Cloudflare-Service-Token-Shortcuts `CF_ACCESS_CLIENT_ID`/`CF_ACCESS_CLIENT_SECRET`, ein `extra_headers`-Objekt in `~/.ai_employee_bridge.json`, und wiederholbare `--header "Name: value"`-Flags. Die Header werden vor dem `Authorization`-Header gemischt, sodass der Bearer-Token nie überschrieben werden kann. **Header-Werte sind Credentials und werden nie geloggt** (nur die Namen). Setup-Hinweis in der Web-UI ergänzt.

---

## [1.119.3] — 2026-08-01

### Fixed
- **Toter Import in `task_service.py` behoben** (latente Mine): importierte `_compute_auto_rating`, das in `_compute_formula_rating` umbenannt wurde. Die Datei ist ungenutzt, aber ein Import hätte gecrasht — jetzt korrigiert. (#217 als geliefert geschlossen: Live-Steering deckt die Mid-Run-Injection ab.)

### Notes
- **Agenten-„Idle-Stop" ist kein Bug**, sondern der User-Lifecycle-Service: Agenten eines >30 Min inaktiven Users (ohne laufende Tasks) werden gestoppt. Bei aktiver App-Nutzung bleiben sie an. Ein per-Agent „always-on"-Flag (Ausnahme vom Lifecycle) existiert noch nicht — separates Mini-Feature bei Bedarf.

---

## [1.119.2] — 2026-08-01

### Security
- **Git-Skill-Crawler gegen Symlink-Exfiltration + Path-Traversal gehärtet** (Automated-Review-Funde). (1) [HIGH] Ein bösartiges Repo mit `SKILL.md` als Symlink auf eine Host-Datei (`/shared/.auth/token.json`, ENCRYPTION_KEY …) hätte deren Inhalt als „Skill" ausgelesen. Fix: `git -c core.symlinks=false clone` (Symlinks werden als Plain-Text materialisiert) + pro Datei `islink`/`isfile`-Check, `O_NOFOLLOW`-open und realpath-muss-im-Checkout-bleiben. (2) [MEDIUM] Path-Traversal via `subdir`: Basis wird jetzt per `realpath` re-verankert und muss im Klon liegen; API lehnt `subdir` mit `..`/absolut und `location` mit führendem `-` ab.

### Added
- **Admin-UI zeigt jetzt AUCH die eingebauten/env-Skill-Quellen** (read-only), nicht nur die selbst hinzugefügten — `GET /skills/sources` liefert zusätzlich `builtin` (Defaults + `SKILL_REPOS`), das Panel listet sie als „Eingebaut — werden immer gecrawlt".

---

## [1.119.1] — 2026-08-01

### Fixed
- **Skill-Quellen (#371): Tabelle wird zuverlässig angelegt + git-clone gegen Argument-Injection gehärtet.** (1) `skill_sources` hing an `create_all`, das aber nur im fresh-DB-Fallback läuft → auf der bestehenden DB fehlte die Tabelle. Jetzt always-run `CREATE TABLE IF NOT EXISTS` (kanonisches ensure-Muster). (2) Argv-Flag-Smuggling im `git clone` behoben: `--` vor die Positional-Args (URL/Dir können nie als Flags gelesen werden) + Guard, der URL/ref mit führendem `-` ablehnt.

---

## [1.119.0] — 2026-08-01

### Added
- **Skill-Quellen komplett verwaltbar — Admin-UI + selbst-gehostete Git-Hosts (Issue #371 Phase 2+3).** Skill-Quellen waren zuletzt nur env-basiert (`SKILL_REPOS`, .env editieren + Neustart). Jetzt vollständig, verzahnt in den bestehenden Crawl→Import→Security→Marketplace-Fluss:
  - **DB-Model `SkillSource`** (via `create_all` angelegt): Name, Typ (`github`/`git`), Ort, ref/subdir, verschlüsselte Credentials (Fernet), enabled, trusted-Provenance, letzter Crawl-Status.
  - **Clone-basiertes Fetching** für JEDEN Git-Host: `git clone --depth 1 --branch <ref>` → host-agnostisch, also **Forgejo/GitLab/Gitea/Azure DevOps** und **private Repos** (Token wird in die Clone-URL injiziert und NIE geloggt). GitHub-Quellen nutzen weiter den schnellen API-Pfad; env/Defaults bleiben unverändert.
  - **Security-Gate greift auch für gecrawlte Skills** (`check_skill_content`, #192) — bisher umgangen; blockierte Skills werden geloggt und übersprungen. Provenance (`source_repo`/`source_url`/trusted) wird gesetzt.
  - **Admin-API** `GET/POST/PATCH/DELETE /skills/sources` + `POST /skills/sources/recrawl` (require_admin). Credentials nur `has_credential` nach außen, nie im Klartext.
  - **Admin-UI** im Skill-Marketplace: einklappbares „Skill-Quellen (Admin)"-Panel — Quelle hinzufügen (GitHub owner/repo ODER Git-URL + optional Token/ref/subdir/trusted), aktivieren/deaktivieren, löschen, „Jetzt crawlen". Nur für Admins sichtbar.
  - Damit lassen sich z. B. die **internen mindcode/Forgejo-Skills** direkt im Admin eintragen und crawlen — die Self-Hosting-Prämisse der Plattform ist erfüllt.

---

## [1.118.5] — 2026-08-01

### Fixed
- **Voice: Datei-Upload-Hinweis wird jetzt GESPROCHEN, nicht nur geschrieben.** Lud man während einer laufenden Voice-Antwort eine Datei hoch, injizierte `notify_files_uploaded` die Notiz („Ich habe die Datei … gesehen, was soll ich damit tun?") SOFORT — mitten im Turn. Nova hängt sie dann nur als **Text** an (erscheint in der Chat-Bubble), erzeugt aber **kein Audio** dafür → geschrieben, nicht gesprochen. Fix: die Notiz wird **verzögert**, bis Nova den aktuellen Sprech-Turn beendet hat (Idle-Erkennung über den Zeitpunkt des zuletzt empfangenen Audios), und dann als **sauberer, gesprochener Turn** injiziert (mit 25s-Deadline als Absicherung). Reiner Orchestrator-Fix.

---

## [1.118.4] — 2026-08-01

### Reverted
- **Den `_clean_text`-Längen-Cap aus 1.118.3 zurückgenommen — war die falsche Diagnose.** „Invalid event bytes" ist eine **awscrt-Eventstream-Meldung** (Decode-Fehler auf dem Empfangs-Stream von Nova), kommt NICHT aus unserem Code und hängt nicht an der Eingabelänge: AWS erlaubt bis 50 KB Text pro Message, die betroffene docx war ~5 KB. In den Logs stehen dazu passend `awscrt InvalidStateError: CANCELLED` (bekannte Nova-Sonic/awscrt-Stream-Instabilität). Ein Input-Cap behebt das nicht und würde nur legitime Inhalte unnötig kürzen. `_clean_text` ist wieder reines Sanitizing (UTF-8 + Control-Chars). Die eigentliche Ursache (Stream-Recovery bei awscrt-Decode-Fehlern) ist separat zu adressieren.

---

## [1.118.3] — 2026-08-01

### Fixed
- **Voice: „Invalid event bytes" beim Vorlesen langer Inhalte (z. B. ganzer docx) behoben.** Nova Sonic lehnt einen zu langen Text-Turn mit einer ValidationException ab, die das Frontend als „Invalid event bytes" zeigt — die Sprachausgabe brach ab (der Satz wurde nicht gesprochen), obwohl der Text im Chat korrekt stand. `_clean_text` cappt jetzt hart auf 3500 Zeichen (mit „… (gekürzt — frag nach Details)"). Betrifft alle Injects/Tool-Results in den Voice-Layer. (Die mid-turn-Datei wurde per Live-Steering korrekt erkannt und ausgelesen — nur die Sprachausgabe des Volltextes scheiterte.)

---

## [1.118.2] — 2026-08-01

### Fixed
- **Claude-OAuth-Login über die Web-UI funktioniert jetzt zuverlässig.** Ein erfolgreicher UI-Login („Mit Claude einloggen" → Code einfügen) konnte stillschweigend wirkungslos bleiben. Ursachen behoben:
  - **`_get_db_token()` nutzte `scalar_one_or_none()`** → bei 2+ `anthropic`-Integrationen (z. B. Alt-Zeile mit `user_id` + neue mit `user_id=NULL`) kam `None` zurück, der frische Token landete nie im Shared-File, Agenten blieben auf dem abgelaufenen. Jetzt: neueste Integration (`order_by(expires_at desc).first()`).
  - **`persist_tokens()` räumt Dubletten auf**: globale Provider (anthropic/github/…) behalten genau EINE Zeile; alte gleicher Provider werden beim Login gelöscht.
  - **Frontend-Einfügefeld parst robust**: ganze Callback-URL (`…?code=X&state=Y`), `code#state` oder nackter Code werden erkannt; der eingefügte State wird mitgeschickt (behebt „Invalid state" bei mehreren Login-Tabs).

---

## [1.118.1] — 2026-08-01

### Fixed
- **Codex-Steering: `codex exec resume` korrekt aufgerufen + Interrupt sauber.** (1) `codex exec resume` akzeptiert kein `-C` (Working-Dir) → wurde entfernt; der Subprozess startet ohnehin mit `cwd=workspace_dir`, und resume filtert per cwd zur richtigen Session. Resume greift jetzt wirklich (Kontext-Fortsetzung, kein Fallback nötig). (2) Codex beendet sich bei SIGINT mit Exit-Code 1 (nicht -2) → der unterbrochene Turn meldete fälschlich „Codex CLI exited with code 1" als Fehler an den User. Neues `_interrupted`-Flag (in `interrupt()` gesetzt) unterdrückt das. Auf dem Pi verifiziert: mid-turn 2. Nachricht → „1 neue Nachricht aufgenommen" → gesteuerte Antwort, ohne Fehler-Rauschen; normale Einzelnachrichten unverändert. Analoges `_interrupted`-Flag defensiv auch im Claude-Handler.

---

## [1.118.0] — 2026-07-31

### Added
- **Live-Steering für Claude- UND Codex-Agenten (Chat + Telegram).** Bisher konnte man eine laufende CLI-Arbeit nur im `custom_llm`-Modus mitten im Turn steuern; bei `claude_code`/`codex_cli` wurde eine während der Arbeit eintreffende Nachricht erst NACH dem ganzen Turn verarbeitet ("nach dem Task-Bulk"). Neu — plattform-standard-Muster **Queue → Interrupt (SIGINT, als `-2` bereits graceful behandelt) → Resume**:
  - Neuer gemeinsamer Helper `agent/app/steering.py` (`run_turns_with_steering`): ein Watcher pollt `pending_drain`; trifft eine neue Nachricht desselben Kanals ein, wird der laufende Subprozess unterbrochen und die Nachricht in einen Folge-Turn gefaltet (Iterations-Cap 6).
  - **Claude** (`chat_handler.py`): Fortsetzung via `--resume <session_id>` (Session-Handling bestand bereits); Retry-Logik in `_run_turn_with_retries` gekapselt.
  - **Codex** (`codex_runner.py`): `_run_codex(resume=True)` → `codex exec resume --last` setzt die gerade unterbrochene Session fort (behebt nebenbei die bisherige Lücke „Codex vergisst den Verlauf"); Fallback auf frischen Turn, falls Resume nicht greift; SIGINT/-2/-15/130 als graceful behandelt.
  - **Custom-LLM** unverändert (hatte den Mechanismus schon). Funktioniert automatisch für Web-Chat UND Telegram, da beide dieselbe `agent:{id}:chat`-Queue + `source_key`-Zuordnung nutzen.

---

## [1.117.2] — 2026-07-31

### Changed
- **Proportionalitäts-Regel jetzt in den echten Runtime-Instructions** (`DEFAULT_CLAUDE_MD` in agent_manager.py → wird als `AGENT.md`/`CLAUDE.md` in jeden Agenten geschrieben). Die 1.117.1-Fassung lag im `_PLATFORM_SECTION` (Create-Zeit-Knowledge) und erreichte laufende Agenten nicht. Zusätzlich die konkreten Dauer-Trigger entschärft: „memory_search at START of every conversation", „list_todos ALWAYS FIRST", „Knowledge Access — use EVERY session", „At START of every conversation 3× brain_search", „knowledge.md at START/END of every task" → alle auf „am Anfang einer NEUEN Unterhaltung / echten Aufgabe, dann on-demand; bei Trivialfragen überspringen". Neue Leitregel „Effort proportional to the request" ganz oben. Live in die laufenden Container gepusht (kein Recreate).

---

## [1.117.1] — 2026-07-31

### Changed
- **Aufwand proportional zur Anfrage — Agenten laden nicht mehr bei JEDER Nachricht den vollen Kontext.** Der System-Prompt zwang bisher per „Self-Improvement (MANDATORY after every task)" + „Nach jeder Aufgabe (PFLICHT)" dazu, VOR und NACH jeder Chat-Nachricht memory/brain/skill-marktplatz/todos zu laden, `memory_save`, `knowledge.md` zu updaten, `rate_task`, Feedback zu fragen — auch bei Trivialfragen wie „wie war das Passwort?" (in der Praxis 97k–635k Input-Tokens pro Nachricht). Neu:
  - **Trivial-Turns** (Frage, Status, Ja/Nein, Lookup) → direkt antworten; kritische Memories inkl. `credentials` sind ohnehin vorgeladen. Kein memory/brain-Search, kein Marktplatz, kein Speichern/Raten.
  - **Kontext einmal-dann-bei-Bedarf**: Fundament-Kontext nur zu Beginn einer neuen Unterhaltung/echten Aufgabe laden; danach Verlauf + vorgeladene Memories nutzen, nur gezielt nachsuchen. Nie alles pro Turn neu laden.
  - **Self-Improvement nur nach substanzieller Arbeit** (gebaut/geändert/gefixt/entschieden oder User-Korrektur) — nicht nach jeder Zeile. `skill_search` nur für wiederholbare Prozeduren.
  - Nebenbei: Emoji (⭐) aus dem Prompt entfernt.

---

## [1.117.0] — 2026-07-31

### Added
- **Agent gibt dem User jetzt die KORREKTE, teilbare App-URL** (statt `localhost`/Host-Port/„docker compose"/ZIP). Ursache des Dauerproblems: der Agent wusste nicht, dass er INNERHALB der Plattform läuft, und kannte seine öffentliche App-URL nicht.
  - Neues Setting `PUBLIC_APP_URL` (Cloudflare-Tunnel-Domain, z. B. `https://agents.future-app.de`) in Config + docker-compose (`environment`).
  - `list_apps`/`start_app`/`rebuild_app` liefern pro laufender App die **absolute App-Proxy-URL** (`{PUBLIC_APP_URL}/api/v1/agents/<id>/apps/proxy/<container>/<port>/`) — in beiden Agent-Laufzeiten (MCP `.mjs` + Codex `api_client.py`) und in der Ausgabe an den Agenten.
  - **System-Prompt:** Wenn der User den App-Link will → GENAU die URL aus `list_apps` weitergeben (Plattform-Link, von überall nach AI-Employee-Login erreichbar). NIEMALS `localhost`, Host-Ports, `docker compose up` oder „ZIP entpacken". Plus Klarstellung: App-Daten persistieren SERVER-seitig im Docker-Volume (kein Browser-Speicher).

### Changed
- Agent-Image-Version → 1.117.0 (Recreate nötig, damit bestehende Agenten die URL-Ausgabe bekommen).

---

## [1.116.2] — 2026-07-31

### Changed
- **System-Prompt (AGENT.md): klare Regel für Login/Passwortschutz in Apps.** Der App-Proxy entfernt aus Sicherheitsgründen Cookies + Authorization und sandboxed das Dokument — ein SERVER-seitiger Login (Session-Cookie, Redirect auf `/login`) funktioniert eingebettet in AI-Employee daher NICHT (Login-Schleife; absolute `/login`-Redirects brechen aus dem Proxy-Unterpfad aus). Neue Prompt-Regel: (1) Passwortschutz CLIENT-seitig bauen (JS-Overlay, Zustand nur im Speicher, kein Cookie/Storage, kein Server-Redirect) — nur weicher Gate, echter Schutz ist ohnehin der Plattform-Login davor; (2) der Agent sagt dem User PROAKTIV, dass eine echte-Login-App eingebettet nicht sauber läuft und dafür eine eigene Domain braucht; (3) generell proxy-taugliche Apps: relative statt absolute Pfade. Greift für neue/aktualisierte Agenten (CLAUDE.md wird beim Recreate bzw. per Live-Push neu geschrieben).

---

## [1.116.1] — 2026-07-31

### Fixed
- **App-Discovery liefert leere Liste statt 500, wenn der Agent-Container gerade nicht läuft.** `_discover_core` (geteilt von der User-Apps-Seite und den neuen Agent-Endpoints) führte ein `find` im Agent-Container aus; war dessen Container gestoppt (DB hält die id noch), warf der docker-socket-proxy 409 → 500. Jetzt wird das abgefangen und als „keine erreichbaren Apps" (`{"apps": []}`) behandelt. Betrifft nur verwaiste/gestoppte Agenten; ein Agent, der die Endpoints selbst aufruft, läuft ohnehin.

---

## [1.116.0] — 2026-07-31

### Added
- **Der Agent kann seine eigenen Apps jetzt selbst managen (list/logs/start/stop/rebuild).** Bisher konnten Apps nur über die Web-Oberfläche (Buttons) oder den Voice-Layer gestartet/neu gebaut werden — der Agent selbst hatte KEINE App-Tools und kein Docker. Damit fehlte die End-to-End-Schleife: Der Agent editierte App-Code per Task, aber jemand anderes musste danach neu bauen. Neu:
  - **Agent-facing Endpoints** `/agent-apps/{,logs,up,down,rebuild}` im Orchestrator — authentifiziert per `verify_agent_token` und HART self-scoped (ein Agent kann ausschließlich SEINE eigenen Apps anfassen). Die eigentliche Logik liegt geteilt in `docker_apps` (`_start_core`/`_stop_core`/`_rebuild_core`/`_logs_core`/`_discover_core`); User- und Agent-Endpoints nutzen denselben Kern (keine Doppel-Logik).
  - **5 Agent-Tools** für BEIDE Agent-Laufzeiten: Claude via MCP (`orchestrator-server.mjs`) und Codex via `definitions.py` + `api_client.py` (`list_apps`, `app_logs`, `start_app`, `stop_app`, `rebuild_app`).
  - **System-Prompt (AGENT.md):** Volle Schleife dokumentiert — Code in `/workspace/projects/<name>/` ändern → `rebuild_app` (übernimmt Änderungen via `--build --force-recreate`) → `app_logs` zum Verifizieren. Weiterhin: Agent hat KEIN eigenes Docker, treibt Apps nur über diese Tools.
- Greift für neue/aktualisierte Agenten (Agent-Image-Rebuild + Recreate nötig; Codex-Agenten gestaffelt).

---

## [1.115.9] — 2026-07-31

### Added
- **Voice-Layer kann Apps jetzt auch neu bauen (`rebuild_app`).** Der Sprach-Layer hatte bisher nur `start_app` (baut beim ersten Hochfahren), `stop_app` und `restart_app` (bloßer Neustart, übernimmt KEINE Code-Änderungen). Neues Tool `rebuild_app` ruft — analog zum „Neu bauen"-Button — orchestrator-seitig `docker compose up -d --build --force-recreate` (bestehender `/agents/{id}/apps/rebuild`-Endpoint), im Hintergrund mit sofortiger Ansage und Web-Karte, sobald die App wieder läuft. Sprachbefehle: „bau App X neu / rebuild X / X mit den neuen Daten hochfahren / X aktualisieren". Start-/Rebuild-Reporting in einen gemeinsamen Helper `_report_app_up(verb)` zusammengezogen (keine Doppel-Logik). `voice_help` nennt den neuen Befehl.

---

## [1.115.8] — 2026-07-31

### Added
- **„Neu bauen"-Button auf der Apps-Seite.** Bisher gab es nur Starten/Stoppen/Öffnen/Logs — „Starten" reused nur das bestehende Image/den Container, sodass Code-/Datenänderungen einer App nie übernommen wurden. Neuer Button ruft `docker compose up -d --build --force-recreate` (bestehender `/agents/{id}/apps/rebuild`-Endpoint) → die App wird aus dem aktuellen Workspace-Code neu gebaut. Erscheint bei jeder App mit bekanntem Workspace-Pfad (laufend wie gestoppt).

---

## [1.115.7] — 2026-07-31

### Changed
- **System-Prompt (AGENT.md): klare Regel, wo Apps hingehören.** Agenten legten lauffähige Apps teils in `/workspace/transfer/` an (der Ausliefer-Ordner) statt in `/workspace/projects/` — die Plattform entdeckte dann zwei Kopien und startete beide. Der Prompt sagt jetzt explizit: Docker-/Web-Apps gehören in EINE Stelle `/workspace/projects/<name>/`, NIE zusätzlich in `transfer/`; der Agent-Container hat selbst KEIN Docker (`docker compose` nicht selbst ausführen) — die Plattform entdeckt Apps unter `projects/` und startet sie über den Orchestrator; erreichbar über den App-Proxy, nicht über einen Host-Port. Greift für neue/aktualisierte Agenten.

---

## [1.115.6] — 2026-07-31

### Fixed
- **App-Proxy 502 bei langen Container-Namen behoben.** Der Reverse-Proxy proxte über den Container-NAMEN; compose-generierte Namen überschreiten aber oft das **63-Zeichen-DNS-Limit** (z. B. `agent-<id>-<langer-pfad>-<service>-1`, 81 Zeichen) und sind dann von Dockers DNS **nicht auflösbar** → 502 (Cloudflare Bad Gateway). Der Proxy nutzt jetzt die **Container-IP** auf `ai-employee-network` (Fallback: Name). Damit sind auch Apps mit langen Namen (Unterordner-Projekte) über den Tunnel erreichbar.

---

## [1.115.5] — 2026-07-31

### Fixed
- **Orchestrator-Start überschreibt nicht mehr den gültigen Shared-Token mit einem veralteten `.env`-Wert (Issue #377).** `ClaudeTokenService.write_initial_token()` folgte einer anderen Prioritätenreihenfolge als `refresh_access_token()` und der Modul-Doku: Es übersprang Priorität 1 (die DB) und fiel von der Keychain-Datei direkt auf `settings.claude_code_oauth_token` (`.env`) zurück. Da es beim Start läuft, überschrieb es die bereits gültige `/shared/.auth/token.json` auf dem persistenten Volume mit einem möglicherweise veralteten `.env`-Token — Agents im Zeitfenster bis zum ersten `refresh_access_token()`-Lauf (~50 s) meldeten irreführend `401 OAuth access token has been revoked`. Jetzt ist `write_initial_token()` `async` und konsultiert dieselbe Reihenfolge wie `refresh_access_token()` (DB → Keychain → env), sodass ein gültiger Shared-Token nicht mehr überschrieben wird. Zusätzlich weigert sich `_write_shared_token()` jetzt, eine **vorhandene** Token-Datei mit einem offensichtlich unbrauchbaren (zu kurzen) Platzhalter zu überschreiben. Beide `write_initial_token()`-Aufrufstellen in `main.py` werden nun `await`et. (Deploy-Gate: Orchestrator-Rebuild.)

---

## [1.115.4] — 2026-07-31

### Security
- **Computer-Use-Bridge sendet den JWT nicht mehr als URL-Query-Parameter (Issue #373, CWE-532).** Der Bridge-Client hängte den Token als `?token=…` an die WebSocket-URL, sodass uvicorns Access-Log (Server) und die Client-Log-Datei den vollständigen Token bei jedem Verbindungsversuch protokollierten — bei einem abgelaufenen Token hunderte Male pro Stunde durch den 5-Sekunden-Reconnect. Der Token wird jetzt ausschließlich über den `Authorization: Bearer …`-Header übertragen (den der Server via `_authenticate_ws` bereits liest, wenn kein Query-Token vorhanden ist); die URL trägt nur noch `session_id`, und der Client loggt nur noch Schema/Host/Pfad statt der vollständigen URL. Rein clientseitige Änderung, abwärtskompatibel (der Server akzeptiert weiterhin auch den Query-Token).

---

## [1.115.3] — 2026-07-31

### Security
- **Telegram-Bot-Token nicht mehr im Klartext geloggt oder zurückgegeben (Issue #372, CWE-532).** Schlug der Start eines Agent-Bots fehl, enthielt die `InvalidToken`-Meldung von `python-telegram-bot` den eingegebenen Token wörtlich (*„The token … was rejected"*) — dieser landete über `{e}` im Orchestrator-Log und wurde vom `PUT /agents/{id}/telegram`-Endpoint an den Client zurückgegeben. Jetzt: (1) neuer Telegram-Token-Redaction-Pattern in `log_redaction`, mit dem alle Fehler-Logs im `bot_manager` maskiert werden; (2) die API-Fehlerantwort läuft durch `redact_logs`; (3) das Token-Format wird server-seitig **vor** dem Persistieren/Start geprüft (`^\d{6,}:[A-Za-z0-9_-]{30,}$`) — eine eingefügte BotFather-Nachricht wird mit HTTP 400 abgewiesen, ohne gespeichert oder an Telegram gesendet zu werden.

---

## [1.115.2] — 2026-07-31

### Fixed
- **Alembic-Cycle behoben (durch die 1.115.1-Migration verursacht).** Die Revision-ID `a1b2c3d4e5f6` existierte bereits mehrfach im Repo; die neue Migration kollidierte und alembic meldete beim vollständigen Traversieren „Cycle is detected". Die Migration ist entfernt; `mcp_servers.headers_encrypted` wird jetzt — wie job_state/reflection — über einen **immer laufenden, idempotenten Ensure-Block** angelegt (die Migrations-Kette ist bewusst multi-head, es werden keine neuen Migrations mehr angehängt). Der Orchestrator läuft damit wieder über den normalen Alembic-Pfad statt in den create_all-Fallback zu kippen.

---

## [1.115.1] — 2026-07-31

### Fixed
- Alembic-Migration für `mcp_servers.headers_encrypted` nachgereicht. Der reine Startup-Ensure-Block greift nur im `create_all`-Fallback (nicht wenn Alembic normal durchläuft), sodass die Spalte auf produktiven DBs fehlte. Migration `a1b2c3d4e5f6` (idempotentes `ADD COLUMN IF NOT EXISTS`) legt sie beim Deploy zuverlässig an.

---

## [1.115.0] — 2026-07-31

### Fixed (externe MCP-Server — Feedback aus Kollegen-Setup)
- **`/mcp-servers/probe` reicht den Bearer-Token (und Custom-Header) jetzt durch.** Bisher rief `probe_mcp_server` `_discover_tools(url)` ohne Auth auf → ein „Verbindung testen" gegen einen geschützten MCP-Server schlug IMMER fehl, auch bei korrektem Token. Backend + Frontend (`probeMcpServer`) senden nun die Auth mit.
- **Ziel-Server-Fehler kommen als 400 statt 502** (mit der echten Ursache im `detail`). Ein 502 wurde von einem vorgelagerten Cloudflare-Tunnel durch dessen eigene Bad-Gateway-Seite ersetzt → die eigentliche Meldung ging verloren und der Betreiber hielt den eigenen Dienst für ausgefallen. Jetzt übersteht die Fehlermeldung den Proxy.

### Added
- **Freie Auth-Header für externe MCP-Server.** Neben dem Bearer-Token kann pro MCP-Server jetzt ein Satz **eigener Header** (z. B. `x-api-key`, `x-consumer-api-key`, `X-Auth-Token`) hinterlegt werden — Fernet-verschlüsselt wie der Token. Nötig für Server wie Composio (dokumentiert `x-consumer-api-key`), Home Assistant, UniFi. Umgesetzt am Model (`headers_encrypted`), in `_discover_tools` (Merge), allen Endpoints, im Agent-MCP-Client (`_mcp_headers` + `CUSTOM_MCP_HEADERS`-Env) und im Integrations-Formular (ein „Name: Wert" pro Zeile). Der bestehende `bearer_token` bleibt als Kurzform.

> Hinweis: Der Orchestrator-seitige Teil (Verbindungstest, Anlegen, Tool-Discovery mit Header) wirkt sofort. Der Agent-seitige Laufzeit-Header-Merge greift, sobald das Agent-Image neu gebaut und der jeweilige Agent neu erstellt wird.

---

## [1.114.2] — 2026-07-30

### Fixed
- **Agent-Recreate kollidiert nicht mehr mit einem bereits vergebenen Container-Namen (#364).** `AgentManager.restart_agent` entfernte den alten Container nur über die (evtl. veraltete) `container_id` und legte danach unter dem festen Namen `ai-agent-<slug>-<id>` neu an — existierte noch ein Container unter genau diesem Namen, warf `docker create` einen **409 Conflict** und der Recreate scheiterte periodisch. Der Recreate reconciliiert jetzt über **beide** Referenzen (`container_id` **und** Name), wie `update_agent` es bereits tat.
- **Kein WARNING-Spam mehr im Lifecycle-Sweep ohne verbundenen Redis-Client (#364).** `_publish_event` / `_cancel_open_chats` steigen jetzt mit `logger.debug` früh aus, wenn `redis.client is None` (frisch instanziierter `RedisService` im Recreate-Pfad), statt pro Sweep zwei `NoneType … publish`-WARNINGs zu erzeugen, die echte Lifecycle-Fehler verschleierten.

---

## [1.114.0] — 2026-07-30

### Added
- **Voice zeigt eine gestartete App direkt im Chat zum Öffnen.** Nach erfolgreichem `start_app` blendet der Voice-Layer die App als **Web-Karte** ein — über die tunnel-erreichbare **Proxy-URL** (`/api/v1/agents/{id}/apps/proxy/{container}/{internal_port}/`), nicht mehr das nutzlose `localhost:host_port` (das war der localhost des Pi). So kann der Nutzer die App direkt ansehen/öffnen statt eine unerreichbare Adresse zu hören.
- Frontend: `safeHttpUrl` löst jetzt **relative** same-origin-URLs (die App-Proxy-Pfade) gegen die aktuelle Origin auf, statt sie zu verwerfen — nötig, damit die Web-Karte der App über den Tunnel lädt. Absolute URLs unverändert; weiterhin nur http(s).

---

## [1.113.2] — 2026-07-30

### Fixed
- **App-Proxy: Apps mit festem `container_name` waren nicht aufrufbar (403 Forbidden).** Der Reverse-Proxy (`/agents/{id}/apps/proxy/{container}/{port}/`) verlangte, dass der Container-NAME das Agent-Präfix `agent-{id8}-` trägt. Apps, deren compose-Datei einen festen `container_name` setzt (z. B. `pokemon-tracker`), scheiterten daran. Der Präfix-Namens-Check ist entfernt; die Ownership wird weiterhin **autoritativ** über das server-seitig gesetzte `com.docker.compose.project`-Label geprüft (unfälschbar), plus die Injection-Guards (`/`, `..`) auf dem Upstream-Host. Sicherheit unverändert, aber Apps mit festem Container-Namen sind jetzt erreichbar.

---

## [1.113.1] — 2026-07-30

### Fixed
- `start_app` (Voice) meldet jetzt korrekt Erfolg, wenn die App **tatsächlich läuft**, obwohl `docker compose up` einen nicht-Null-Code zurückgab (typisch bei compose-Dateien mit festem `container_name` → „name already in use", App kommt trotzdem hoch). Fallback: bei „Fehler" wird der echte Container-Status geprüft; laufen Container, wird Erfolg + Zugriffs-Port gemeldet statt „fehlgeschlagen".

---

## [1.113.0] — 2026-07-30

### Added
- **Voice kann Apps jetzt WIRKLICH starten/stoppen — orchestrator-seitig.** Neue Tools `start_app(app)` und `stop_app(app)` rufen den Compose-Up/-Down des Orchestrators auf (`docker_apps.start_app`/`stop_app`, via Socket-Proxy + Compose-Runner). Vorher versuchte der Voice-Agent, „docker compose up" per Delegation im Agent-Container laufen zu lassen — der hat aber KEIN Docker („nicht verfügbar") und lief zudem in den Bedrock-Timeout. `start_app` läuft im Hintergrund (erster Build ist langsam) mit sofortiger Ansage und meldet Erfolg + Zugriffs-Port zurück. Systemprompt geschärft: App starten/stoppen macht der Orchestrator (start_app/stop_app), NIEMALS docker per Task an den Agenten; nur Code/Config ändern geht per plan_task.

---

## [1.112.2] — 2026-07-30

### Fixed
- **Voice „unexpected error during processing" (Kontext-Bloat) angegangen.** Nova Sonic ist ein Realtime-Modell mit begrenztem Kontext; große Tool-Ergebnisse (Datei-Inhalte, Docker-Logs, Datei-Listen) häuften sich über mehrere Turns an und ließen Bedrock den Turn mit einem generischen Fehler abbrechen (im Screenshot: nach kompletter Datei-Liste + Projekt-Inhalt). Tool-Ergebnisse für die Sprachausgabe jetzt hart gekürzt: `read_file`/PDF 8000→1600 Zeichen, `app_logs` 6000→1500 (900/Container), `list_workspace` 25→15 Einträge, `search_files` 12→8. Das Modell braucht nur genug zum Zusammenfassen — bei Bedarf „lese ich weiter".

### Diagnostics
- Nova-Provider: unbehandelte Bedrock-Event-Kinds (modellierte Exceptions wie `internalServerException`/`modelStreamErrorException`) werden jetzt geloggt + als Fehler-Event surfaced; Receive-Loop-Fehler werden immer geloggt (auch beim Schließen) — damit die eigentliche Ursache von „unexpected error" beim nächsten Auftreten sichtbar ist.

---

## [1.112.1] — 2026-07-30

### Changed
- **Voice: „ich mache jetzt X"-Ansage wieder zuverlässig (behebt hakelige Interaktion).** Die native-async-Umstellung (v1.106) hatte die sofortige gesprochene Ansage vor der Arbeit wegoptimiert und sich darauf verlassen, dass das Modell den Füller selbst sagt — was unzuverlässig war (er machte, wurde still, sprach das Ergebnis erst später aus). Zurück zum bewährten Muster für `ask_agent`/`refine_task`: **sofortige Quittung als Tool-Antwort** (das Modell sagt garantiert „ich kümmere mich jetzt drum"), **echtes Ergebnis** kommt Sekunden später als injizierter Turn. Systemprompt entsprechend zurückgesetzt. (`delegate_tasks`/`plan_task` hatten die Sofort-Ansage ohnehin behalten.)

---

## [1.112.0] — 2026-07-30

### Added
- **Voice kann jetzt die Apps des Agenten verwalten.** Drei neue Direkt-Tools, damit „analysiere mal meine Apps" funktioniert:
  - `list_apps` — nennt die vom Agenten deployten Apps (docker-compose-Projekte im Workspace) mit Status (läuft/teilweise/gestoppt/nicht gestartet); Erkennung über das compose-Projekt-Label `agent-{id8}-…` wie der `/agents/{id}/apps`-Endpoint.
  - `app_logs(app)` — liest die Docker-Logs der App-Container (SDK, über den gehärteten Socket-Proxy), damit der Agent Fehler erkennt. Log-Inhalte werden über `_clean_text` bereinigt an Nova gesendet.
  - `restart_app(app)` — startet die laufenden Container einer App neu.
  - **Analysieren → beheben-Kette** im Systemprompt verdrahtet: erst `list_apps`/`app_logs`, Fehler zusammenfassen, dann zum Fixen/Anpassen/Deployen per `plan_task` an den Agenten (der den App-Ordner + Fehler bekommt und mit bash/Dateizugriff arbeitet). Scope: der Voice-Agent sieht/steuert nur die Apps SEINES Agenten (Projekt-Prefix-Ownership).

---

## [1.111.1] — 2026-07-30

### Fixed
- **Voice „invalid byte" / „unexpected error during processing" behoben (wahrscheinlichster Auslöser).** Tool-Ergebnisse aus Datei-/PDF-Inhalten (`read_file`, PDF-Extraktion, `list_workspace`, `search_files`) konnten NUL-Bytes, Steuerzeichen oder kaputte UTF-8-Sequenzen (z. B. lone surrogates aus pypdf) enthalten. Über den Nova-Sonic-/Azure-Bidi-Stream gesendet, ließ das Bedrock/OpenAI den Turn mit einem generischen Fehler abbrechen. Fix: neue `_clean_text`-Bereinigung (erzwingt gültiges UTF-8, entfernt Steuerzeichen außer Tab/Zeilenumbruch) auf ALLE an die Realtime-Modelle gesendeten Texte — `toolResult` UND `inject_user_text`, für Nova Sonic UND Azure Realtime.
- Nova-Empfangs-Decoding tolerant gemacht (`utf-8`/`replace` + JSON-Guard): ein einzelnes fehlerhaftes Byte aus dem Service killt nicht mehr die Empfangs-Schleife mit „invalid byte", sondern wird übersprungen.

---

## [1.111.0] — 2026-07-30

### Added
- **Voice M365-Aktionen & proaktive Hinweise — Batch 3.**
  - **`m365_send_mail(to, subject, body, send?)`:** Mail senden ODER (Default) als Outlook-Entwurf anlegen. Sicherheit: der Agent liest Empfänger/Betreff/Inhalt vor und braucht ein klares „ja, absenden" — sonst `send=false` (Entwurf zum Prüfen).
  - **`m365_create_event(subject, start, end?, attendees?, location?)`:** Kalendertermin anlegen; Start/Ende als ISO aus dem Gesagten, Default-Zeitzone Europe/Berlin, `end` = Start+1h wenn nicht genannt.
  - **Proaktive Kalender-Hinweise (`_proactive_loop`):** Läuft im Gespräch (nur wenn M365 verbunden), erinnert **von selbst** an einen bevorstehenden Termin („in etwa N Minuten beginnt dein Termin X") — jeder Termin nur einmal, ~alle 5 min geprüft, unterdrückt solange der Nutzer spricht. Aus reaktiv wird proaktiv. Wird beim Session-Ende sauber beendet.

---

## [1.110.1] — 2026-07-30

### Fixed
- `write_brain` (Voice): falscher Import-Pfad korrigiert (`vault_indexer` liegt in `app.services`, nicht `app.core`) — hätte das Schreiben ins Vault zur Laufzeit gecrasht.

---

## [1.110.0] — 2026-07-30

### Added
- **Voice Dateien & Brain — Batch 2.**
  - **`read_file` liest jetzt auch PDF/Word/Excel:** Für PDF/DOCX/XLSX extrahiert der Voice-Layer den Text (`_extract_document_text`, pypdf/python-docx/openpyxl) und liest/fasst ihn zusammen — „lies mir das Pitchdeck vor" funktioniert. Reine Binär-/Media-Dateien verweisen auf `open_file`.
  - **`open_file(path)`:** Legt eine beliebige Workspace-Datei als klickbare Öffnen/Download-Karte in der Voice-UI bereit (`media`/kind=file → Download-Endpoint). „zeig mir die Datei / öffne das Pitchdeck".
  - **`write_brain(content, title?)`:** Schreibt eine Markdown-Notiz direkt in ein **beschreibbares** gemountetes Second-Brain/Vault (`vault.write_file` + Index) — „schreib das ins Wiki / halt das im zweiten Gehirn fest". Nur `rw`-Vaults; sonst Hinweis auf Lesezugriff. Gegenstück zum bereits vorhandenen `search_brain` (lesen).

---

## [1.109.0] — 2026-07-30

### Added
- **Voice-Task-Lebenszyklus — Batch 1.** Drei Erweiterungen der Voice↔Container-Interaktion:
  - **`plan_task` meldet sich jetzt zurück:** Der Voice-Layer abonniert `task:completions` und **spricht das Ergebnis mitten im Gespräch aus**, sobald ein eingeplanter Task fertig (oder fehlgeschlagen) ist — kein „ich hab's eingeplant und du hörst nie wieder davon" mehr. Der Fall „nach dem Call" ist weiterhin über die bestehende Task-fertig-Notification (WS + APNs an den Owner) abgedeckt. Watcher wird beim Session-Ende sauber beendet.
  - **`cancel_task` (Stoppen per Stimme):** „stopp / brich ab / hör auf" stoppt die laufende Delegation (Signal `agent:{id}:chat:cancel`) und bricht noch eingereihte geplante Tasks ab (`TaskRouter.cancel_task`).
  - **`voice_help`:** „was kannst du / was kann ich sagen" → gesprochener Fähigkeits-Überblick.
- Delegations-Timeout für Sprach-`ask_agent` von 180 s auf **480 s** erhöht (native async hält das Gespräch ohnehin am Laufen), damit längere Aufgaben nicht vorzeitig mit „konnte gerade nicht" abbrechen.

---

## [1.108.1] — 2026-07-30

### Security
- Voice-Workspace-Tools (`read_file`/`list_workspace`): Pfad-Konstruktion aus User-/Modell-Eingabe wird jetzt **explizit kanonisiert + auf `/workspace` eingegrenzt** (`_safe_ws_path` via `posixpath.normpath`, `..`-Traversal wird abgewiesen), als Defense-in-Depth zusätzlich zur bestehenden `FileManager._validate_path`-Prüfung. (Automatischer Security-Review-Hinweis; war durch die Downstream-Validierung nicht real ausnutzbar, jetzt zweifach abgesichert.)

---

## [1.108.0] — 2026-07-30

### Added
- **Voice kann jetzt in Dateien reinschauen.** Neues Direkt-Tool `read_file(path)` liest den Textinhalt einer Workspace-Datei über `FileManager.read_file` (kein Agenten-Roundtrip). Damit beantwortet der Voice-Agent Fragen, deren Antwort IN einer Datei steht („was steht in…", „lies mir … vor", „fasse … zusammen"). Für „was ist Projekt X" nutzt er die Kette **finden → lesen → antworten** (erst `search_files`/`list_workspace`, dann `read_file` auf README/AGENT.md/…, dann aus dem Inhalt antworten). Binär-/Office-Dateien (PDF/Bild/…) werden erkannt und sauber abgelehnt (Vorschlag: zeigen oder vom Agenten auswerten lassen); Größenlimit 800 KB, Textausgabe auf 8000 Zeichen gekappt.

---

## [1.107.0] — 2026-07-30

### Added
- **Voice kann jetzt den Workspace durchsuchen/auflisten.** Zwei neue Direkt-Tools im Realtime-Voice-Layer, damit „welche Projekte/Dateien hab ich", „was liegt in Ordner X", „such die Datei…" endlich funktionieren (vorher hatte Voice keinen Datei-/Workspace-Zugriff und tat bei solchen Fragen nichts):
  - `list_workspace(path?)` — listet Ordner/Dateien im Agenten-Workspace (default: Top-Level, wo die Projekte liegen) über `FileManager.list_directory`, ohne Agenten-Roundtrip.
  - `search_files(query)` — namensbasierte Suche im Workspace (neue `FileManager.search_files`, `find -iname`, auf `/workspace` begrenzt, injection-sicher über List-Args).
- Der Voice-Layer greift dafür wie beim Datei-Präsentieren über den Orchestrator (`FileManager` + Docker) in den Container; blockierende `find`/`list`-Aufrufe laufen in `asyncio.to_thread`. Versteckte Dateien werden für die Sprachausgabe ausgeblendet.

---

## [1.106.1] — 2026-07-30

### Fixed
- Voice native-async-Härtung: Die kosmetischen Nachbereitungs-Schritte einer Delegation (Datei-Karten, UI-Events) sind gekapselt, sodass ein Fehler dort das **toolResult nie verhindern** kann. Bei Nova 2 Sonics async Tool-Calling würde ein unbeantwortetes toolUse die Session sonst in einen Wartezustand hängen — jetzt ist „das Tool wird immer beantwortet" garantiert.

---

## [1.106.0] — 2026-07-29

### Changed
- **Voice-Delegation auf Nova 2 Sonics natives asynchrones Tool-Calling umgebaut.** Statt bei `ask_agent`/`refine_task` sofort eine synthetische Quittung als Tool-Ergebnis zu senden und die echte Antwort später als künstlichen Nutzer-Turn (`inject_user_text`) nachzuschieben, wird jetzt der **echte Agenten-Output als `toolResult` desselben toolUse nachgereicht** — Nova hält das Gespräch eigenständig am Laufen, während der Agent viele Tools abarbeitet, und spricht das Ergebnis kontextuell aus, sobald es landet. Das ist der von AWS vorgesehene Weg („asynchronous tool calling", out of the box) statt unseres bisherigen Workarounds. `delegate_tasks` (mehrere Tasks aus einem toolUse) behält den Sammel-Ack, da ein toolUse nur genau ein toolResult beantworten kann.

### Added
- **Prompt-getriebene Füller:** Beim Aufruf von `ask_agent` sagt der Voice-Agent sofort selbst einen kurzen, variierenden Füller in der Ich-Form („Moment, ich schau mal…") statt stumm zu werden — passend zum async-Modell, das derweil weiterredet.
- **Gedrosselte gesprochene Fortschritts-Häppchen (#3):** Bei länger laufenden Delegationen (>15 s) sagt der Agent gelegentlich einen kurzen Zwischenstand („bin noch dran, arbeite gerade an…") — throttled (≤1 Satz/15 s) und unterdrückt, solange der Nutzer spricht, damit nichts übereinanderredet.

### Fixed
- **Event-Interleave abgesichert:** Ein `_seq_lock` serialisiert im Nova-Provider die mehrteiligen Sende-Sequenzen (contentStart→content→contentEnd), damit gleichzeitig nachgereichte Tool-Ergebnisse / Fortschritts-Injektionen sich nicht auf dem Bidi-Stream verschränken. (Azure-Realtime war über die bestehende Response-Queue bereits abgesichert.)

---

## [1.105.0] — 2026-07-29

### Added
- **Voice-Layer kann jetzt so viel wissen wie der Agent + Arbeit einplanen.** Der Realtime-Voice-Layer (Nova Sonic / Azure) bekommt fünf neue Direkt-Tools, damit er nicht mehr für jede Wissensfrage delegieren muss:
  - `search_brain` — durchsucht die dem Agenten gemounteten Second-Brain/Vaults direkt (hybride Vektor+Keyword-Suche über `vault_search.hybrid_search`), statt zu delegieren.
  - `skill_search` — durchsucht den Skill-Katalog direkt (Vektor, ILIKE-Fallback).
  - `m365_calendar_today` / `m365_mail_recent` — liest Kalender/Postfach des Nutzers direkt über MS Graph (per-User-Token via `OAuthService.get_valid_token`), ohne Agenten-Roundtrip.
  - `plan_task` — plant **echte, dauerhafte Arbeit** als Task auf dem Agenten-Board ein (`TaskRouter.create_and_route_task`). Anders als `ask_agent` (kurze Antwort im Gespräch) läuft ein so eingeplanter Task eigenständig weiter — auch nachdem der Voice-Call beendet ist.
- Systemprompt entsprechend erweitert: Lesen (Wissen/Brain/Kalender/Mail) läuft direkt; für echte Arbeit unterscheidet der Voice-Agent jetzt zwischen `ask_agent` (zügig, Ergebnis im Call) und `plan_task` (größere/spätere Arbeit, läuft weiter).

---

## [1.104.0] — 2026-07-29

### Added
- **Fernwartung ohne SSH**: Admin kann Orchestrator und Frontend aus der (über den Cloudflare-Tunnel schon von außen erreichbaren) Web-Oberfläche neustarten. Neue Karte „System-Steuerung" in Einstellungen → System zeigt Container-Status (Orchestrator/Frontend/Postgres/Redis) + laufende Agent-Container und bietet Neustart-Buttons (mit Bestätigung). Damit lässt sich die Plattform von außerhalb des Heimnetzes neustarten, ohne SSH.
- Neue Admin-Endpoints `GET /admin/system/status` + `POST /admin/system/restart` (require_admin). Nutzt den bereits vorhandenen, gehärteten Docker-Socket-Proxy (CONTAINERS+POST); Neustart ist bewusst auf Orchestrator/Frontend beschränkt (DB/Redis/Proxy ausgenommen). Der Orchestrator-Selbstneustart wird an den Daemon dispatcht und läuft serverseitig zu Ende, während der Container heruntergeht.

---

## [1.103.0] — 2026-07-29

### Added
- Modell-Katalog: **Auto-Discovery + Admin-Freischaltung** statt fest verdrahteter Liste. Ein Admin kann in den Einstellungen (Tab „Modelle" → „Modelle freischalten") per Klick die Provider-APIs abfragen (Anthropic `/v1/models`, OpenAI `/v1/models`); erkannte Modelle werden erfasst und je Modell per Schalter freigeschaltet. Neu erkannte Modelle sind zunächst deaktiviert („erkannt, aber Admin schaltet frei"). `GET /agents/models` liefert nur noch freigeschaltete Modelle — damit steuert die Freischaltung, was bei der Agent-Erstellung und in den Agent-Einstellungen wählbar ist.
- Neuer Service `model_registry_service.py` (Discovery + effektiver/Admin-Katalog + Freischaltung), gespeichert über die bestehende `platform_settings`-Key-Value-Infrastruktur (keine neue Tabelle). Neue Admin-Endpoints: `GET /agents/models/admin`, `POST /agents/models/discover`, `PUT /agents/models/enabled`.

### Unverändert (bewusst)
- Die kuratierte Liste in `model_catalog.py` bleibt als **Seed** (immer verfügbar, provider-korrekte Strings inkl. Bedrock-ARNs/Vertex-Varianten) und ist standardmäßig freigeschaltet — kein Regressionsrisiko.
- Die harten Harness-Guards (`is_model_allowed_for_mode` / `coerce_model_for_mode`) sind unverändert davorgeschaltet: die Freischaltung regelt nur das UI-Angebot, nie was technisch erlaubt ist (kein Codex-Agent auf Claude-Modell etc.).

---

## [1.102.7] — 2026-07-26

### Fixed
- Codex-Agenten: Massen-Recreate („Update All") kann die geteilte, rotierende ChatGPT-Auth nicht mehr fleet-weit killen. Codex-Container-(Neu-)Erstellung wird jetzt **serialisiert** (globales Lock + Settle-Delay in `agent_manager.py`) — es kommt nur EIN Codex-Container gleichzeitig hoch, statt dass mehrere ihren Single-Use-Refresh-Token parallel rotieren (`refresh_token_reused`). Behebt den Auslöser des Codex-Auth-Ausfalls; Claude-Agenten laufen unverändert parallel.

---

## [1.102.7] — 2026-07-29

### Fixed
- Codex-Auth-Haertung: Der geteilte, rotierende ChatGPT-Refresh-Token (alle Codex-Agenten teilen EINE `auth.json`) wird jetzt **zentral & proaktiv im Orchestrator** erneuert (Scheduler, single-threaded, sobald der Access-Token < 48h Restlaufzeit hat) und der rotierte Token in die DB zurueckgeschrieben. Damit refreshen die Agenten den Single-Use-Token nie mehr gleichzeitig — der `refresh_token_reused`-Ausfall bei einem parallelen „Update All" kann nicht mehr auftreten. Neu: `CodexAuthService.ensure_fresh()` + Scheduler-Hook (alle 2h Check).

---

## [1.102.6] — 2026-07-11

### Fixed
- Sprach-Session (Realtime/Nova Sonic): Bricht der AWS-Bedrock-Bidi-Stream mitten im Gespraech ab (bekannter AWS-CRT-Race, der serverseitig als `done` ankommt), verbindet das Voice-Frontend jetzt **automatisch neu** und setzt ueber eine stabile `chat_session` dasselbe Gespraech fort — statt mit "Realtime-Session beendet." in eine Sackgasse zu laufen. Reconnect ist gedeckelt und setzt sich bei echtem Gespraechsdaten-Fluss zurueck (gesunde lange Gespraeche verbinden beliebig oft weiter).

---

## [1.102.5] — 2026-07-11

### Fixed
- Chat-WebSocket: Mehrere Clients desselben Nutzers (Desktop-App, Web-Tab, weiteres Geraet) verdraengen sich nicht mehr gegenseitig. Jede Client-Instanz kann eine optionale `client_id` mitgeben und bekommt damit ihre eigene Verbindung; nur ein Reconnect derselben Instanz ersetzt weiterhin ihren eigenen alten Socket. Behebt den Reconnect-"Krieg" (Endlos `code 4000`), bei dem Nachrichten nie verarbeitet wurden.

---

## [1.102.4] — 2026-07-11

### Added
- Agenten: aktiv bearbeitete Chats werden in der Gespraechsliste orange markiert (Spinner). Der Agent meldet jetzt ALLE parallel laufenden Sessions (nicht nur eine), und die "arbeitet gerade"-Pille verlinkt korrekt in die jeweilige Unterhaltung (Session-ID statt Nachrichten-ID).

## [1.102.3] — 2026-07-11

### Added
- Agenten-Detail: die "arbeitet gerade"-Pille zeigt bei laufendem Chat jetzt "Aktiver Chat" und ist klickbar — ein Klick springt in genau diese Unterhaltung.

## [1.102.2] — 2026-07-10

### Fixed
- Sprache (Nova Sonic): Unterbrechen verwirft jetzt wirklich die GESAMTE laufende Antwort. Bisher hob der naechste Content-Block des Modells das Verwerfen sofort wieder auf, sodass der Agent weitersprach — jetzt bleibt alles verworfen bis zu einem echten neuen Nutzer-Turn.

## [1.102.1] — 2026-07-10

### Security
- Voice-UI-Navigation: nur bekannte Routen bzw. streng interne Pfade erlaubt (verhindert Open-Redirect ueber protokoll-relative Ziele aus Modell-Output).

## [1.102.0] — 2026-07-10

### Added
- Sprache steuert die Oberflaeche: der Agent kann per Voice Ansichten oeffnen/schliessen (z. B. den Knowledge Graph als Overlay) oder zu App-Seiten navigieren (control_ui) — baut auf dem bestehenden Speech-Browser-Kanal auf.

## [1.101.4] — 2026-07-09

### Security
- Einstellungen: Exchange/SMTP-Infrastruktur-Konfiguration (Server, Relay, Service-Account, erlaubte Domains) wird im GET nur noch an Admins ausgeliefert.

## [1.101.3] — 2026-07-09

### Fixed
- Einstellungen: Exchange/SMTP-Konfiguration (Server, Auth-Modus, Relay-Host/Port, erlaubte Domains) wird nach dem Speichern wieder angezeigt — vorher blieben die Felder beim Neuladen leer (GET-Response lieferte sie nicht).

## [1.101.2] — 2026-07-09

### Changed
- Agenten (GPT/Custom-Harness): parallele Tool-Aufrufe sind jetzt immer aktiv — das Modell darf unabhaengige Lese-Tools in einem Zug anfordern, der Executor fuehrt sie gleichzeitig aus (spart Zeit).

## [1.101.1] — 2026-07-09

### Added
- SMTP-Relay: Option "Zertifikat pruefen" (Default an). Fuer interne Relays mit selbstsigniertem/IP-Cert deaktivierbar — bei Relay-Auth wird IMMER verifiziert (Passwortschutz).

## [1.101.0] — 2026-07-09

### Added
- E-Mail-Versand ueber SMTP-Relay: universeller Sendeweg fuer on-prem/Hybrid-Exchange, funktioniert auch wenn EWS/443 zum Mailserver gesperrt ist. Admin konfiguriert Relay-Host/Port (+ optional Auth) in den Einstellungen. Der Agent sendet als sein Besitzer; Schutz: erzwungener Absender, Empfaenger-Domain-Allowlist (Default eigene Domain), Empfaenger-Limit, Rate-Limit, Header-Injection-Schutz, Audit-Log, kein Auth ueber Klartext.

## [1.100.10] — 2026-07-09

### Fixed
- Integrationen: Exchange (on-prem) Read+Write wird nach dem Speichern korrekt angezeigt und faellt nicht mehr faelschlich auf Read zurueck (Anzeige-Bug; der Wert war bereits gespeichert).

## [1.100.9] — 2026-07-09

### Changed
- Speech-Reiter: Ein-/Ausklapp-Button der Gespraechsliste jetzt optisch identisch zum Chat (Icon-Button in der Toolbar).

## [1.100.8] — 2026-07-09

### Changed
- Speech-Reiter: Gespraechsliste laesst sich jetzt ein- und ausklappen (wie im Chat).

## [1.100.7] — 2026-07-09

### Fixed
- E-Mail-Versand-Tools (M365 und on-prem Exchange) sind jetzt fest verfuegbar, sobald ein Agent Schreibrechte hat — vorher hat der Agent sie manchmal uebersehen und faelschlich gemeldet, es gebe kein Sende-Tool.

## [1.100.6] — 2026-07-09

### Added
- Sprache: Der Agent kann jetzt Dinge visuell zeigen — Bilder, QR-Codes (Link aufs Handy), Webseiten im eingebetteten Fenster oder in einem neuen Tab.
- Sprache: Der Agent benennt das Gespraech nach dem ersten Austausch automatisch thematisch.
- Sprache: Datei-Upload per Drag-and-Drop auf die Gespraechsspalte.

### Changed
- Sprach-Cockpit neu gestaltet: dezenterer Orb, schlanke runde Bedienelemente, grosse Anzeigeflaeche unter dem Orb.

## [1.100.5] — 2026-07-09

### Fixed
- Nachtschicht: Bedrock-Default-Modell ist jetzt Amazon Nova Lite — verfuegbar auf jedem Account, der Nova Sonic (Voice) nutzt. Anthropic-Modelle auf Bedrock erfordern das AWS-Use-Case-Formular (Fehler 404 "use case details"); wer es ausgefuellt hat, kann via Setting reflection_model eine anthropic-Bedrock-ID setzen. Nova-Antworten werden intern aufs Anthropic-Format normalisiert, Fehler-Bodies werden geloggt.

## [1.100.4] — 2026-07-09

### Security
- Speech-Reiter: Agent-gelieferte URLs (Medien + Web-Ergebnisse) werden jetzt auf http(s) validiert, bevor sie als Link, iframe oder window.open verwendet werden — javascript:/data:/blob:/file:-Schemata werden verworfen (XSS-Haertung, Fund des automatischen Security-Reviews).

## [1.100.3] — 2026-07-09

### Fixed
- Nachtschicht: Bedrock-Extraktion funktioniert jetzt wirklich — manuelle SigV4-Signierung via httpx statt smithy-SDK (das signiert den Model-Pfad einfach-encodiert, AWS kanonisiert doppelt-encodiert -> InvalidSignatureException). Auf dem Pi E2E gegen echtes Bedrock verifiziert (Claude Haiku antwortet).
- Tasks-Seite: "All"-Zaehler klebte bei 100 fest — Backend liefert jetzt die echte Gesamtzahl (COUNT statt Seitengroesse), Frontend laedt bis 500 und zeigt das Server-Total.
- Sidebar: doppelter Online-Indikator entfernt — nur noch die "Online"-Zeile unter dem Namen, kein zweiter gruener Punkt am Avatar.

## [1.100.2] — 2026-07-09

### Fixed
- Nachtschicht: LLM-Zugang jetzt dreistufig — Anthropic-Key aus Env, aus den (verschluesselten) DB-Settings, oder Fallback auf einen aktiven Bedrock-AI-Account (invoke_model, gleiche Client-Verdrahtung wie Nova Sonic). Vorher scheiterte die Extraktion still, wenn nur ein Bedrock-Account existierte (z.B. Pi).
- Nachtschicht-Dashboard-Karte zeigt fehlgeschlagene Auswertungen jetzt ehrlich an (Anzahl + Hinweis), statt "Erfolgreich - 0 - 0 - 0".

## [1.100.1] — 2026-07-09

### Fixed
- Sprache: Der Agent spricht seinen Denkprozess nicht mehr laut aus — nur noch die fertige Antwort.
- Sprache: Uhrzeit, Datum und Zeitzonen-Umrechnungen beantwortet er direkt, statt dafür im Web zu suchen.

## [1.100.0] — 2026-07-09

### Added
- **Nachtschicht (Reflection/Dreaming):** Naechtlicher Out-of-band-Reflexions-Lauf — liest die Gespraeche, Aufgaben und Meetings des Tages, destilliert daraus dauerhaftes Wissen und schreibt es ueber die bestehenden Pfade zurueck (Agent-Memory mit Dedup/Widerspruchserkennung, Knowledge Base mit Auto-Verlinkung, Skill-Marketplace als Entwurf). Standardmaessig AUS (Opt-in pro Installation).
  - Drei Review-Modi: Automatisch / Ausgewogen (Neues direkt, Eingriffe in Bestehendes via Freigabe) / Alles freigeben.
  - Dashboard-Karte "Nachtschicht" (Ergebnis + "Jetzt laufen lassen"), Approvals-Tab mit Vorher/Nachher, Einstellungs-Karte (Uhrzeit, Modus, Token-Budget).
  - Memory-Tab: Herkunfts-Badges (Agent/Gespraech/Nachtschicht/Du/Verbesserung/Kompaktierung), Filter "Nur Nachtschicht", "Verlauf"-Zeitleiste der Supersede-Kette (neuer Endpoint `GET /memory/{id}/history`).
  - Audit-Log-Events pro Lauf und pro Einzelaenderung, Telegram-Digest, Kosten-/Token-Tracking pro Lauf (`reflection_runs`).
  - Kompaktierungs-Rettung: Beim Rolling-Summary-Kompaktieren wird der weggefaltete Kontext als Langzeit-Memory gesichert (source=compaction) statt verloren zu gehen.
  - Provenance-Feld `source` auf allen Memories (agent | user | conversation | reflection | improvement | compaction).
  - API: `GET /reflection/status`, `GET /reflection/runs`, `POST /reflection/run-now` (Admin).
  - 17 neue MCDC-Tests fuer die Konflikt-/Review-Modus-Verzweigung.

## [1.99.167] — 2026-07-09

### Fixed
- M365/OneDrive: Der Agent kann jetzt den Inhalt von PDF-, Word- und Excel-Dateien wirklich auslesen und zusammenfassen (vorher nur reine Textdateien).

## [1.99.166] — 2026-07-09

### Added
- Speech-Reiter: Datei-Upload in den Workspace — der Agent meldet sich per Sprache und fragt, was er damit tun soll (falls noch keine Anweisung vorliegt).

## [1.99.165] — 2026-07-09

### Changed
- Chat-Reiter: Gesprächsliste links (wie im Speech-Reiter) statt horizontaler Session-Tabs — gemeinsame `SessionRail`-Komponente, per Toolbar-Button ein-/ausklappbar (auf Mobile standardmäßig zu).

### Added
- Gespräche anpinnen, umbenennen (Doppelklick oder Stift-Symbol) und löschen direkt in der Gesprächsliste — im Chat- UND Speech-Reiter (vorher nur in den Chat-Tabs möglich).

## [1.99.164] — 2026-07-09

### Changed
- Agent-Detail: Reiter "Todos" und "Activity" zusammengeführt — ein "Activity"-Reiter mit Sub-Reitern Todos, Live und Verlauf (Klick auf den Reiter öffnet direkt Todos).

## [1.99.163] — 2026-07-09

### Added
- Speech-Reiter: Liste "Letzte Gespräche" — frühere Unterhaltungen auswählen, Verlauf sehen und per Sprache fortsetzen (gemeinsame Sessions mit dem Chat).

## [1.99.162] — 2026-07-09

### Changed
- Speech-Reiter nutzt jetzt die volle Hoehe des Bereichs.

## [1.99.161] — 2026-07-09

### Added
- Task-Ansicht: erzeugte Dateien/Artefakte erscheinen jetzt direkt im Task und lassen sich per Klick oeffnen.
- Agenten: neuer Reiter "Speech" mit eingebetteter Live-Sprachansicht.
- Rollen-Verwaltung: Integrationen (M365/Exchange) lassen sich pro Rolle freigeben oder einschraenken.

### Fixed
- Sprach-Interaktion liess sich nicht auf Realtime-Modelle umstellen (422) — Auswahl funktioniert jetzt.
- Task-Ausgabe (Live & Replay) wird als formatierter Text statt roher Zeichenkette dargestellt.

## [1.99.160] — 2026-07-09

### Changed
- Schritt-Replay: der Log scrollt jetzt automatisch mit, waehrend man den Schieberegler bewegt.

## [1.99.159] — 2026-07-09

### Added
- Tasks: Nach Abschluss kann man dem Agenten direkt in der Aufgabe eine Folge-Anweisung geben ("Weitere Anweisung geben") — er arbeitet mit seinem bisherigen Ergebnis/Workspace weiter, die neue Live-Ansicht oeffnet sich direkt.

### Fixed
- Schritt-Replay: gestreamte Texte werden jetzt (wie im Live-Output) fluessig zusammengefuehrt statt Wort fuer Wort gebrochen.

## [1.99.158] — 2026-07-09

### Changed
- Agenten-Detailseite zeigt jetzt die Rolle/Spezialgebiet als Untertitel (die Uebersichts-Karten zeigen sie bereits).

## [1.99.157] — 2026-07-09

### Changed
- Team-Roster & Live-Output: Der Roster zeigt jetzt die echte Rolle/Spezialgebiet jedes Agenten (statt des generischen Wissens-Headers). Im Live-Output werden gestreamte Texte fluessig zusammengefuehrt (keine Wort-Brueche mehr) und delegierte Teilaufgaben klar als "Delegiert an <Agent>" angezeigt.
- Meeting-Raum-Pool: Nur Standard-Agenten (ohne persoenlichen Besitzer) koennen freigegeben werden — persoenliche Agenten mit Nutzerwissen bleiben ausgeschlossen (Data-Leak-Schutz).

## [1.99.156] — 2026-07-09

### Added
- Wissensgraph (3D): dreht sich beim Aufruf von selbst ganz sanft ums Zentrum, solange man nur zuschaut. Klick auf einen Knoten -> Kamera fliegt hin und kreist um ihn; Klick ins Leere -> zurueck zum ruhigen Zentrums-Kreisen.

## [1.99.155] — 2026-07-08

### Added
- Meeting-Raeume: Admins koennen Agenten fuer einen gemeinsamen Pool freigeben. Freigegebene Agenten erscheinen im Agenten-Picker JEDES Users, sodass niemand mehr eigene Agenten bereitstellen muss, um sie einem Raum hinzuzufuegen (Toggle in den Agenten-Einstellungen, Admin-only).

## [1.99.154] — 2026-07-08

### Changed
- Nach dem Erstellen eines Tasks landet man jetzt direkt auf dessen Live-Ansicht (Fortschritt + was der Agent tut) statt in der Task-Liste.

## [1.99.153] — 2026-07-08

### Added
- Auch beim einzelnen "New Task" koennen jetzt Dateien angehaengt werden (Kontext/Artefakte fuer den Agenten) — sofern ein konkreter Agent gewaehlt ist.

## [1.99.152] — 2026-07-08

### Fixed
- Team-Tasks: Eine leere LLM-Antwort (0 Tokens, z. B. transienter Provider-Aussetzer) wird jetzt erkannt, kurz wiederholt und andernfalls als sichtbarer Fehler gemeldet — statt den Task still als "erledigt" ohne Ergebnis zu markieren.

## [1.99.151] — 2026-07-08

### Added
- Team-Delegation: Dateien anhaengen, um dem Team Kontext/Artefakte mitzugeben — die Dateien landen im Workspace und der Agent liest sie zuerst.

### Changed
- Wissensgraph: die ausgewaehlte Bubble wird jetzt farblich hervorgehoben.

## [1.99.150] — 2026-07-08

### Fixed
- Wissensgraph: heilt sich selbst — falls die 3D-Ansicht beim Start abstuerzt, laedt sie sich automatisch neu (bis zu 3x), statt auf 2D zu fallen. Damit landet man zuverlaessig in 3D, wo die Hardware es hergibt.

## [1.99.149] — 2026-07-08

### Fixed
- Wissensgraph: 3D-Absturz auf manchen Rechnern/Browsern strukturell behoben (Graph initialisiert jetzt leer und bekommt die Daten danach) — 3D laeuft jetzt auch dort zuverlaessig, wo WebGL zwar aktiv ist, das Timing aber vorher zum Absturz fuehrte.

## [1.99.148] — 2026-07-08

### Fixed
- Wissensgraph: erkennt blockierte/Software-GPU (z. B. gesperrte Klinik-Rechner) jetzt vorab und zeigt direkt die stabile 2D-Ansicht — ohne Absturzversuch und Konsolenfehler.

## [1.99.147] — 2026-07-08

### Fixed
- Wissensgraph: rendert beim ersten Aufruf zuverlässig in 3D (kein 2D-Flackern beim Kaltstart mehr).

## [1.99.146] — 2026-07-08

### Fixed
- Wissensgraph: robuster gegen Render-Fehler; die 2D-Ansicht greift jetzt auch in Safari, statt leer zu bleiben.

## [1.99.145] — 2026-07-08

### Changed
- M365-SSO auf den minimalen Scope beschränkt; org-weite Personensuche bleibt optional.

## [1.99.144] — 2026-07-08

### Changed
- Optionalen Verzeichnis-Scope für org-weite Personensuche vorbereitet (Least-Privilege).

## [1.99.143] — 2026-07-08

### Fixed
- Wichtige M365-Tools (Suche, Personen, Profil, Mail, Dateien) sind dem Agenten immer verfügbar.

## [1.99.142] — 2026-07-08

### Fixed
- Agent nutzt die M365-Tools zuverlässiger; Profil-Abruf inklusive Vorgesetztem; Personensuche besser erkannt.

## [1.99.141] — 2026-07-08

### Fixed
- Wissensgraph: 3D bleibt auf fähigen Geräten aktiv; 2D-Ansicht mit besseren Abständen.

## [1.99.140] — 2026-07-08

### Changed
- Personensuche mit zusätzlichem Kontakte-Fallback; „letzte Dateien" vollständiger; 2D-Wissensgraph optisch aufgewertet.

## [1.99.139] — 2026-07-08

### Fixed
- MS-Planner: Aufgaben mit Beschreibung anlegen und Personen zuweisen.

## [1.99.138] — 2026-07-07

### Added
- In-App-Architektur- & Schnittstellen-Referenz unter Hilfe (mit Diagrammen und vollständiger Endpoint-/Tool-Übersicht).

## [1.99.137] — 2026-07-07

### Changed
- **Meeting-Transkript geht jetzt sichtbar in den Chat statt in einen Hintergrund-Task.** Bisher wurde das Transkript per `createTask` an den Agenten übergeben — der Task lief headless durch (Protokoll landete nur im Wissen), man sah nichts im Chat. Jetzt wird das Transkript über einen einmaligen Chat-WebSocket als normale Nachricht in eine Chat-Session geschrieben: Transkript **und** das Protokoll des Agenten erscheinen als sichtbarer **Chat-Verlauf im Chat-Tab** des Agenten. (`frontend/src/lib/api.ts` `sendMeetingTranscriptToChat`, `frontend/src/components/agents/voice-session.tsx`)

## [1.99.136] — 2026-07-07

### Changed
- **Meeting-Recorder in die Live-Voice-UI verschoben (eigener Button).** Der Recorder saß auf der Meeting-Rooms-Übersicht und wirkte dort fehl am Platz. Er ist jetzt als eigener Button „Meeting aufnehmen" im Realtime-Voice-Cockpit — und rein passiv: beim Öffnen wird das Live-Mikro gemutet, der Agent hört NICHT zu und spricht NICHT, es wird nur Audio aufgenommen und (segmentweise, live) transkribiert. Erst nach dem Stopp kann das Transkript optional an denselben Agenten gesendet werden, der daraus im Hintergrund (als Task, kein Live-Gespräch) ein Protokoll erstellt. Von der Meeting-Rooms-Seite entfernt. (`frontend/src/components/agents/voice-session.tsx`, `frontend/src/app/meeting-rooms/page.tsx`)

## [1.99.135] — 2026-07-07

### Added
- **Meeting-Transkription: OpenAI-Whisper-Fallback + Live-Segment-Transkript.** `/meetings/transcribe` versucht zuerst den lokalen faster-whisper-STT-Service und fällt, wenn der nicht erreichbar/konfiguriert ist (z. B. auf dem Pi, wo kein STT-Service läuft), automatisch auf **OpenAI Whisper** zurück (Key aus Platform-Setting `voice_openai_api_key` oder `OPENAI_API_KEY`). Der Recorder nimmt jetzt in **~20s-Segmenten** auf (statt einem großen Blob am Ende): jedes Segment ist ein vollständig dekodierbares webm-File, wird einzeln transkribiert und **live** ins Transkript geschrieben. Damit funktionieren beliebig lange Meetings zuverlässig — kein STT-Timeout auf langem Audio, jedes Segment bleibt unter dem OpenAI-25-MB-Limit, und eine unterbrochene/abgestürzte Aufnahme verliert den bereits transkribierten Teil nicht mehr. (`orchestrator/app/api/meetings.py`, `frontend/src/components/meetings/meeting-recorder.tsx`)

## [1.99.134] — 2026-07-07

### Fixed
- **Realtime-Voice: `refine_task` verlor den ursprünglichen Auftrag.** Korrigierte der Nutzer per Sprache ein Detail einer laufenden Aufgabe (z. B. „nicht Daniel Hadolf, sondern Daniel Alisch"), bekam der Agent nur den Korrektursatz — er hat dann nur die Korrektur ausgeführt (Namen gemerkt) und den eigentlichen Auftrag („die Mails zusammenfassen") fallengelassen. Beim Refine werden jetzt **ursprünglicher Auftrag + Korrektur zusammengeführt** und das echte Ergebnis explizit eingefordert, statt nur die Korrektur zu bestätigen. (`orchestrator/app/services/realtime_voice_session.py`)

## [1.99.133] — 2026-07-07

### Changed
- **MS-Graph-MCP: `ms_search`-Treffer mit Absender + Datum, neueste zuerst.** Die Mail-Suche fand zwar Treffer, gab sie aber nur als Betreff+Snippet ohne Absender/Datum aus — dadurch konnte der Agent „die letzten Mails an X" nicht sortieren/bestätigen und wich trotz gefundener Treffer aus. `ms_search` extrahiert jetzt Absender (`from`), Datum (`receivedDateTime`/`sentDateTime`, für Termine `start`, für Dateien `lastModifiedDateTime`), sortiert absteigend (neueste zuerst) und rendert `[Datum] Absender: Betreff`. Damit sind „letzte Mails"-Anfragen direkt beantwortbar. (`orchestrator/app/core/msgraph_mcp.py`)

## [1.99.132] — 2026-07-07

### Changed
- **MS-Graph-MCP: Mail-Suche eindeutig verdrahtet.** `ms_search` weist jetzt explizit aus, dass es der korrekte Weg für die E-Mail-Suche ist (`types=['message']`). `ms_list_emails` stellt klar, dass es Postfach-Ordner direkt liest (`/me/messages`, braucht Cloud-Postfach → bei On-Prem/Hybrid ggf. 404) und verweist zum robusten Finden/Suchen von Mail auf `ms_search`. Damit wählt der Agent bei „Mails suchen" den funktionierenden Suchindex-Weg statt des Postfach-Ordner-Zugriffs. (`orchestrator/app/core/msgraph_mcp.py`)

## [1.99.131] — 2026-07-07

### Added
- **MS-Graph-MCP: `ms_recent_files`.** Direkte Antwort auf „welche Dateien habe ich zuletzt bearbeitet" — listet die kürzlich verwendeten/bearbeiteten Dateien aus OneDrive + SharePoint (`GET /me/drive/recent`) mit Name, Änderungsdatum, Bearbeiter und Link. Vorher musste der Agent über `ms_insights`/`ms_graph_get` improvisieren und zögerte deshalb; jetzt gibt es ein eindeutig benanntes Tool. (`orchestrator/app/core/msgraph_mcp.py`)

### Fixed
- **MS-Graph-MCP: `ms_search_people` mit Verzeichnis-Fallback.** `/me/people` leitet seine Relevanz aus dem Cloud-Postfach ab und liefert bei On-Prem-Postfächern HTTP 404 — die Personensuche schlug damit fehl, obwohl der Entra-Verzeichnisdienst in der Cloud liegt. Das Tool versucht jetzt zuerst `/me/people` und fällt bei 404/403 automatisch auf die Verzeichnissuche (`/users` mit `ConsistencyLevel: eventual`) zurück, sodass Name→E-Mail auch ohne Cloud-Postfach funktioniert. (`orchestrator/app/core/msgraph_mcp.py`)
- **MS-Graph-MCP: `ms_search` HTTP 400 bei gemischten Typen behoben.** Microsoft Graph `/search/query` verbietet das Kombinieren inkompatibler `entityTypes` in einem Request (`chatMessage` muss allein stehen; Postfach-Typen `message`/`event` nicht mit SharePoint/OneDrive-Typen `driveItem`/`listItem`/`site`). Der bisherige gemischte Default führte zu HTTP 400 — u.a. bei der Mail-Suche. `ms_search` splittet die angefragten Typen jetzt automatisch in kompatible Gruppen, sucht pro Gruppe getrennt, führt die Treffer zusammen (dedupliziert) und übersteht Teil-Fehler einzelner Gruppen. Damit funktioniert die Mail-Suche zuverlässig (auch beim Default). (`orchestrator/app/core/msgraph_mcp.py`)

### Changed
- **MS-Graph-MCP: klare 404-Meldung statt „HTTP 404".** Persönliche Graph-Endpunkte (`/me/people`, `/me/insights`, `/me/drive/recent`, `/me/messages` …) liefern in On-Prem-Umgebungen 404, weil Postfach/OneDrive nicht in der M365-Cloud liegen. Der Connector fängt das jetzt zentral ab und erklärt die Ursache (On-Prem/keine Cloud-Lizenz) samt Handlungshinweis, statt den Agenten mit einem nackten 404 ratlos zu lassen. (`orchestrator/app/core/msgraph_mcp.py`)

## [1.99.130] — 2026-07-07

### Added
- **MS-Graph-MCP: Backlog abgearbeitet (9 neue Tools).** **Excel-Workbook:** `ms_excel_read` / `ms_excel_write` (Zellbereiche in OneDrive-Excel lesen/schreiben; Worksheet-Name + Range werden sanitisiert). **Mail-Anhänge:** `ms_read_attachment` (Text-Anhänge als Text, binäre mit Typ/Größe). **Präsenz:** `ms_presence` (eigene oder fremde Teams-Verfügbarkeit). **SharePoint:** `ms_list_sites`, `ms_list_site_lists`, `ms_list_list_items`. **OneNote:** `ms_list_notebooks`, `ms_read_note_page`. Der Graph-Connector hat damit ~60 Tools. (Change-Notifications/Webhooks bewusst ausgelassen — brauchen einen öffentlichen Webhook-Receiver = Infra, kein Tool.) (`orchestrator/app/core/msgraph_mcp.py`)

## [1.99.129] — 2026-07-07

### Added
- **MS-Graph-MCP: drei neue Tools.** `ms_create_online_meeting` (Teams-Meeting anlegen + Join-Link zurück), `ms_find_meeting_times` (Terminvorschläge, wann Teilnehmer frei sind — Microsoft findMeetingTimes), `ms_list_attachments` (Anhänge einer E-Mail auflisten). Zusammen mit `ms_insights` (v1.99.128) deckt der Connector jetzt auch Meeting-Koordination + Anhänge ab. (`orchestrator/app/core/msgraph_mcp.py`)

## [1.99.128] — 2026-07-07

### Added
- **MS-Graph-MCP: neues `ms_insights`-Tool** (`/me/insights`) — Dokumente, die um dich herum trenden (`trending`), die du zuletzt genutzt hast (`used`) oder die mit dir geteilt wurden (`shared`). Ideal für „woran habe ich gearbeitet / was ist für mich relevant" ohne Suchbegriff. Hinweis: `/me/people` (`ms_search_people`) und `/search` (`ms_search`) waren bereits eingebettet. (`orchestrator/app/core/msgraph_mcp.py`)

## [1.99.127] — 2026-07-07

### Added
- **Office-Dateien: PowerPoint-Erzeugung im Agent-Image ergänzt (`python-pptx`).** Word (`python-docx`), Excel (`openpyxl`) und PDF (`pymupdf`) waren bereits im Agent-Image — Agenten können diese Formate also längst erstellen/bearbeiten. Es fehlte nur PowerPoint; mit `python-pptx` ist der Office-Satz (Word/Excel/PowerPoint/PDF/HTML) jetzt vollständig. Greift nach **Agent-Image-Rebuild + Agenten-Recreate** (`docker build -t ai-employee-agent:latest ./agent`, dann Agenten neu erstellen). Dockerfile-LABEL auf aktuelle Version gezogen. (`agent/pyproject.toml`, `agent/Dockerfile`)

## [1.99.126] — 2026-07-07

### Changed
- **Meeting-Agent legt Action-Items optional als MS-Planner-Aufgaben an.** Der Meeting-Agent-Prompt instruiert jetzt: falls Microsoft-/Graph-Tools vorhanden sind (`ms_create_planner_task`), die Action-Items auf Wunsch als Planner-Aufgaben anlegen (Titel + Fälligkeit) — sonst überspringen. Damit ist der Löwenrudel-Bogen „Meeting → Transkript → Protokoll → Aufgaben (MS-Planner)" geschlossen (nutzt den vorhandenen Graph-Connector). (`orchestrator/app/core/agent_templates.py`)

## [1.99.125] — 2026-07-07

### Changed
- **Meeting-Agent + Wiki-Import: `write_knowledge` explizit verdrahtet.** Der Meeting-Agent-Prompt nennt jetzt konkret das `write_knowledge`-Tool zum Speichern des Protokolls, und die allgemeine Agent-CLAUDE.md dokumentiert `write_knowledge` (inkl. Hinweis: Wiki-Seiten via MediaWiki-MCP lesen und hier ablegen). Damit greifen die in v1.99.120/122/124 gebauten Loops (Meeting-Protokoll speichern, IT-Wiki-Import) ohne Rätselraten. Greift, sobald Agenten aktualisiert/neu erstellt werden. (`orchestrator/app/core/agent_templates.py`, `orchestrator/app/core/agent_manager.py`)

## [1.99.124] — 2026-07-07

### Added
- **Agenten können in die Knowledge-Base schreiben (`write_knowledge`-Tool) → ermöglicht Wiki-Import per Agent.** Der agentseitige Endpoint zum Schreiben von Knowledge-Einträgen existierte, war aber nicht als MCP-Tool verdrahtet — Agenten konnten also lesen (u. a. das IT-Wiki via MediaWiki-MCP), aber nicht in die Knowledge-Base schreiben. Neu: `write_knowledge` (Upsert per Titel, Tags, erscheint im Knowledge-Graphen). Damit ist der **IT-Wiki-Import ein reiner Agenten-Auftrag** (Zahnrad statt Insellösung): der Agent liest die Seiten über den vorhandenen MediaWiki-MCP und legt sie via `write_knowledge` als Knowledge-Einträge an. (`orchestrator/app/api/mcp_agent.py`)

## [1.99.123] — 2026-07-07

### Fixed
- **3D-Graph füllt seinen Container jetzt sauber aus (Knowledge + Second Brain).** Regression aus dem Legenden-Umbau (v1.99.117): der Sizing-`ref` sitzt seit dem auf der Graph-Fläche, die erst NACH dem Laden gerendert wird — der Größen-Effekt lief aber nur beim Mount (da war der ref noch leer) und maß nie neu, sodass der Canvas auf seiner Anfangsgröße (800×600) hängen blieb und die dunkle Fläche nicht ausfüllte. Der Effekt misst jetzt neu, sobald der Graph erscheint (+ `min-h-0` am Flex-Container). (`frontend/src/app/second-brains/vault-graph-3d.tsx`)

## [1.99.122] — 2026-07-07

### Added
- **Meeting-Aufnahme + Transkription (v1) — der Audio-Teil des Meeting-Agents.** Neuer additiver Aufnahme-Modus (rührt die Realtime-Voice nicht an): unter „Meeting Rooms" gibt es „Live-Meeting aufnehmen & transkribieren". Der Browser nimmt das Meeting auf, schickt die Aufnahme an einen neuen authentifizierten Endpoint `POST /meetings/transcribe`, der über den bestehenden `stt-service` (faster-whisper) transkribiert — reine Transkription, keine Sprachausgabe. Das Transkript kann kopiert / an einen **Meeting-Agent** (Template aus v1.99.120) gegeben werden, der daraus Protokoll + Action-Items erzeugt und in Workspace/Knowledge speichert. Nächste Ausbaustufen: Live-Chunk-Transkription + Sprecher-Diarization (FluidAudio on-device für iOS). (`orchestrator/app/api/meetings.py`, `frontend/src/components/meetings/meeting-recorder.tsx`, `frontend/src/app/meeting-rooms/page.tsx`)

## [1.99.121] — 2026-07-07

### Fixed
- **Voice-Lautstärke auf Mobile/iPhone einstellbar.** Die Realtime-Wiedergabe ging direkt an den Audio-Ausgang; iOS Safari ignoriert `audio.volume`, daher war die Lautstärke im Browser nicht regelbar. Neu läuft die Wiedergabe über einen Web-Audio-GainNode (den iOS respektiert) mit einem Lautstärke-Regler im Voice-UI. (`frontend/src/components/agents/voice-session.tsx`)

## [1.99.120] — 2026-07-07

### Added
- **Neues Agent-Template „Meeting Agent" (Protokollant).** Vorgefertigter Agent, der aus einem Meeting-Transkript oder Notizen ein strukturiertes Protokoll erzeugt (Zusammenfassung, Entscheidungen, Action-Items mit Verantwortlichen + Fristen, offene Punkte, Rohtranskript) und es persistent ablegt: als Markdown unter `/workspace/meetings/` und — falls ein Knowledge-/Vault-Schreibtool vorhanden ist — als durchsuchbaren Knowledge-Eintrag (erscheint im Wissens-Graphen). Erster Baustein des Meeting-Agent-Vorhabens; die Audio-Aufnahme/Diarization baut später darauf auf. (`orchestrator/app/core/agent_templates.py`)

## [1.99.119] — 2026-07-07

### Fixed
- **Teamlead-Agenten kennen jetzt ihr Team.** Das `list_my_team`-Tool (nur die Mitglieder des eigenen Teams inkl. Lead) existierte, war in der Agent-CLAUDE.md aber nicht dokumentiert — nur `list_team` (ALLE Agenten). Ergebnis: fragte man einen (Teamlead-)Agenten „wer ist in deinem Team", antwortete er aus dem Gedächtnis statt zu prüfen. Neu: `list_my_team` ist dokumentiert mit klarer Anweisung, bei Team-/Kollegen-Fragen IMMER zuerst `list_my_team` aufzurufen. (`orchestrator/app/core/agent_manager.py`) — greift, sobald die Agenten aktualisiert/neu erstellt werden (neue CLAUDE.md).

## [1.99.118] — 2026-07-07

### Fixed
- **Meeting-Raum-Erstellung: „Erweiterte Einstellungen" scrollte den Hintergrund statt des Modals.** Bei offenem Create-/Summary-Modal war der Seiten-Scroll nicht gesperrt — auf Touch/Trackpad bewegte sich der Hintergrund statt des Modal-Inhalts, sodass man den Start-Button schwer erreichte. Jetzt wird der Body-Scroll gesperrt, solange ein Modal offen ist. (`frontend/src/app/meeting-rooms/page.tsx`)

## [1.99.117] — 2026-07-07

### Changed
- **Graph-Verbesserungen (Knowledge + Second Brain).** (1) Die Ordner-Legende sitzt jetzt als **einklappbarer Streifen UNTER** dem Graphen (Graph nutzt ~85% der Höhe) statt als Overlay. (2) Klick auf einen Knoten zoomt heran und **kreist danach langsam** um ihn (Orbit). (3) Klick auf einen Link/eine verlinkte Notiz im rechten Panel **fliegt die Kamera zur neuen Node** (vorher bewegte sie sich nicht, weil der Knoten aus dem Detail-Panel keine Positionsdaten hatte). (`frontend/src/app/second-brains/vault-graph-3d.tsx`)

## [1.99.116] — 2026-07-07

### Fixed
- **Knowledge-/Second-Brain-3D-Graph crasht nicht mehr auf WebGL-gesperrten Rechnern (z. B. Klinik-Windows/Edge).** Auf gesperrten Firmen-/Klinik-Windows ist die GPU-Beschleunigung/WebGL oft per Richtlinie deaktiviert oder der Treiber blockgelistet — dann warf der 3D-Renderer im Animations-Loop „Cannot read properties of undefined (reading 'tick')" (Mac/Safari mit funktionierendem WebGL war nicht betroffen). Zwei Fixe: (1) im Animations-Loop crashende Fehler werden global abgefangen und schalten **live auf den 2D-Renderer** um; (2) beim Knowledge-Graph werden „dangling edges" (Kanten auf nicht vorhandene Knoten) herausgefiltert, die die Force-Simulation zum Absturz brachten. (`frontend/src/app/second-brains/vault-graph-3d.tsx`, `frontend/src/app/knowledge/page.tsx`)

## [1.99.115] — 2026-07-07

### Added
- **Admin kann erlaubte Modelle pro Gruppe zuweisen.** Custom-Roles haben jetzt eine `models`-Allowlist (None = alle, wie `llm_providers`). Im Admin-Rollen-Panel wählt man unter „Modelle" die freigegebenen Modelle (aus den AI-Accounts). Bei der Agent-Erstellung wird das serverseitig **hart erzwungen** (`can_use_model`, 403 bei nicht freigegebenem Modell) — admin-safe (Admins bleiben unbeschränkt). Damit kann sich ein Gruppen-Mitglied nicht mehr selbst Opus/GPT-5 o.ä. geben. (`orchestrator/app/core/permissions.py`, `orchestrator/app/api/agents.py`, `frontend/src/components/admin/roles-panel.tsx`, `frontend/src/lib/api.ts`)

## [1.99.114] — 2026-07-07

### Fixed
- **`device_tokens`-Tabelle fehlte auf manchen Deployments → 500er bei Push-Notifications behoben.** Die DB war „stamped ahead" (Branch/Merge-Verhaeddelung bzw. manuelles `alembic stamp`), sodass `relation "device_tokens" does not exist` geworfen wurde. Neue idempotente Migration legt die Tabelle beim naechsten `alembic upgrade head` an, falls sie fehlt (no-op, wenn vorhanden). (`orchestrator/alembic/versions/d1e2v3t4o5k6_ensure_device_tokens.py`)

## [1.99.113] — 2026-07-07

### Fixed
- **Meeting-Raum: Beschreibung mobil einklappbar (Desktop unverändert).** Der Raum-Untertitel (`room.topic`) ist auf Mobile jetzt ein eigener einklappbarer „Beschreibung“-Block (initial zu); auf Desktop bleibt er wie bisher im Header-Untertitel. Damit sind auf Mobile alle vier Bereiche — Beschreibung, Teilnehmer, Chat, Zusammenfassung — einklappbar (initial zu). (`frontend/src/app/meeting-rooms/[id]/page.tsx`, `frontend/src/components/layout/header.tsx`)

## [1.99.112] — 2026-07-07

### Fixed
- **Meeting-Raum: Non-Claude/Codex-Rohausgabe gesäubert + PDF neu gerendert + Zusammenfassung einklappbar.** (1) Nicht-Claude-Engines (Codex/Custom-LLM) gaben teils ihren rohen Stream-JSON-Log (`{"type":"item.started",...}`) als Nachrichtentext aus. Ein gemeinsamer Cleaner extrahiert jetzt den lesbaren Assistant-Text (agent_message bzw. finale Ausgabe) und verwirft die Maschinerie — wirkt in UI **und** PDF. (2) Das PDF rendert Nachrichten jetzt als echtes Markdown (Überschriften/Listen/Kursiv/Code/Trennlinien) statt `<br>`-Suppe und escapet Inhalt HTML-sicher. (3) Der Ergebnis-/Zusammenfassungs-Block ist auf Mobile einklappbar (initial zu); Desktop unverändert. (`frontend/src/app/meeting-rooms/[id]/page.tsx`)

## [1.99.111] — 2026-07-07

### Fixed
- **Meeting-Raum: Chat mobil einklappbar, Stepper mit Phasen-Icons, PDF wirklich downloadbar.** (1) Der Chat ist auf Mobile jetzt einklappbar (initial zu) — Toggle-Header „Chat (N Beiträge)“; Desktop unverändert. (2) Der Taskforce-Phasen-Stepper zeigt statt fünf identischer Häkchen je Phase ein passendes Lucide-Icon (Planung/Zuweisung/Bau/Integration/Fertig), aktive Phase mit Spinner; Farbe zeigt den Status. (3) Der PDF-Export lädt jetzt zuverlässig herunter: statt eines Popups (das mobil geblockt wird → „nicht downloadbar“) wird bei blockiertem Popup eine druckfertige Datei heruntergeladen. (`frontend/src/app/meeting-rooms/[id]/page.tsx`)

## [1.99.110] — 2026-07-07

### Fixed
- **Taskforce-Phasen-Stepper auf dem iPhone lesbar.** Die Phasen-Leiste (Planung → … → Fertig) war auf Phones zu gequetscht. Neu: unter `sm` (Phones) nur die Lucide-Icons (Häkchen/Spinner), Text-Labels erst ab Tablet/Desktop. (`frontend/src/app/meeting-rooms/[id]/page.tsx`)

## [1.99.109] — 2026-07-07

### Fixed
- **Meeting-Raum Mobile-Feinschliff (Desktop unverändert).** Teilnehmer-Panel ist auf Mobile jetzt **einklappbar** (initial eingeklappt) und zeigt beim Ausklappen **kompakte 2-Spalten-Kacheln** statt einer langen Liste (Moderator volle Breite). Chat-Beiträge nutzen auf Mobile mehr Breite (weniger Seiten-Padding + kleinerer Reaktions-Einzug). Alles per `lg:`-Breakpoint — ab Desktop exakt wie zuvor. (`frontend/src/app/meeting-rooms/[id]/page.tsx`)

## [1.99.108] — 2026-07-07

### Fixed
- **Meeting-Raum-Detailansicht auf Mobile lesbar (Desktop unverändert).** Im schmalen Viewport wurden die zwei Spalten (Chat + Teilnehmer-Panel mit fester 288px-Breite) nebeneinander gequetscht und die Toolbar brach hässlich um. Neu: ab `lg` (Desktop) exakt das bisherige, saubere Zwei-Spalten-Layout; darunter stapelt es — Teilnehmer-Panel als kompakte Zusammenfassung oben (max. 32vh), Chat + Eingabe darunter — und die Toolbar bricht sauber um. Reine Responsive-Klassen, keine Änderung am Desktop-Design. (`frontend/src/app/meeting-rooms/[id]/page.tsx`)

## [1.99.107] — 2026-07-07

### Changed
- **Knowledge-Graph nutzt jetzt denselben 3D-Graph wie das Second Brain.** Die Wissensdatenbank rendert ihren Graphen bislang als eigene 2D-SVG-Ansicht; das Second Brain hatte eine deutlich ansprechendere WebGL-3D-Darstellung (mit automatischem 2D-Fallback bei WebGL-Context-Loss). Statt zwei parallele Graph-Implementierungen zu pflegen, wurde die Second-Brain-Komponente (`vault-graph-3d.tsx`) additiv verallgemeinert (optionale `externalGraph`- und `onNodeSelect`-Props) und wird nun von BEIDEN Seiten genutzt — der Knowledge-Graph füttert sie mit seinen Einträgen (Farbe nach primärem Tag, Größe nach Knotengrad, Klick öffnet den Eintrag). Der alte 2D-Graph-Code der Knowledge-Seite (~550 Zeilen) wurde entfernt. (`frontend/src/app/knowledge/page.tsx`, `frontend/src/app/second-brains/vault-graph-3d.tsx`)

## [1.99.106] — 2026-07-07

### Fixed
- **„Update All" lässt jetzt jede Agent-Karte einzeln mitdrehen.** Bisher zeigte nur der globale „Update All"-Button einen Spinner; die „Update"-Badge auf den einzelnen Karten blieb statisch und verschwand erst am Ende alle gleichzeitig. Neu: beim Klick auf „Update All" (oder den Einzel-Update) wird pro Agent-Karte die Badge zum Spinner „Aktualisiere…", und sie verschwindet genau dann, wenn DIESER Agent fertig aktualisiert ist. Umgesetzt über einen Set-basierten Update-Status (mehrere Agenten gleichzeitig) statt eines Einzel-Flags. (`frontend/src/app/agents/page.tsx`, `frontend/src/components/dashboard/agent-card.tsx`)

## [1.99.105] — 2026-07-07

### Fixed
- **Voice Fokus-Modus zeigt jetzt korrekt „Fokus-Modus aktiv" (orange) statt „Hört zu…".** Bei aktivem Fokus (Mikro aus) blieb die Status-Pille auf lila „Hört zu…", obwohl der Agent gar nicht zuhört, sondern im Hintergrund arbeitet. Neu: solange eine Aufgabe läuft → orange „Fokus-Modus aktiv", danach grün „Fokus-Modus – bereit"; ohne Fokus wie gehabt (zuhören lila, bereit grün). (`frontend/src/components/agents/voice-session.tsx`)
- **Explorer: Löschen-Button für Dateien und Ordner.** Das Backend-Delete (`DELETE /agents/{id}/files`, ownership- und `/workspace/`-gesichert) und die API-Funktion existierten bereits, nur der UI-Button fehlte. Jetzt pro Eintrag ein Papierkorb-Button (auf Hover) mit Bestätigungsdialog. (`frontend/src/app/files/page.tsx`)
- **Realtime-Badge „Nova Sonic" → „Realtime".** Der Badge im Voice-Modal zeigte immer „Nova Sonic", auch wenn die Session über Azure-Realtime lief (Kundenanlage) — irreführend. Jetzt engine-neutral „Realtime". (`frontend/src/components/agents/voice-session.tsx`)

## [1.99.104] — 2026-07-07

### Added
- **Graph-Mail: Senden ODER Entwurf — pro Aufruf vom User entscheidbar.** Die Sende-Tools (`ms_send_email`/`ms_reply_email`/`ms_forward_email`) haben einen neuen optionalen `draft`-Parameter: Standard sendet real, mit `draft=true` legt der Agent stattdessen einen Outlook-Entwurf an, den der User selbst prüft und verschickt. Das Modell setzt das aus der jeweiligen Ansage („sende…" vs. „erstelle einen Entwurf…"), sodass der User individuell pro Mail entscheidet.

### Fixed
- **M365/Graph-Connector bereinigt — Agenten versenden Mail jetzt wirklich + 8 Bugfixes.** Bislang wurde ausgehende Mail im Write-Modus **immer** nur als Entwurf angelegt (fest verdrahtetes `draft_mail`) — es gab keinen Modus, in dem ein Graph-Agent tatsächlich sendet (Widerspruch zur Anforderung „Versenden", inkonsistent zum On-Prem-Connector). Ersetzt durch die per-Aufruf-Wahl oben; dabei wurden die Entwurfs-Pfade gefixt: der Reply-Entwurf verwarf zuvor den Antworttext (jetzt via `createReply`/`createReplyAll` mit Text), und Forward umging die Draft-Wahl komplett (jetzt `createForward`). Weitere Härtungen: `_graph` crasht nicht mehr bei nicht-JSON-Fehlerantworten (429/5xx) und liefert über `GraphError` den Statuscode; `ms_cancel_event` löscht nur noch bei „nicht Organisator" (400/403) aus dem eigenen Kalender statt bei jedem transienten Fehler; `ms_search_people` sanitisiert die KQL-Query (Injection); `ms_update_task` lehnt ungültige Status-Werte ab statt still auf „notStarted" zurückzusetzen (öffnete erledigte Tasks); To-Do/Planner-Listen schneiden nicht mehr still ab (`$top` bzw. Rest-Hinweis); Token-Resolver fangen alle Fehler (nicht nur `ValueError`) → saubere „nicht verbunden"-Meldung statt 500; zentrale Pflichtfeld-Validierung im Dispatch. Rein backend-/orchestratorseitig, keine Agent-Image-Änderung. (`orchestrator/app/core/msgraph_mcp.py`, `mcp_msgraph.py`, `mcp_msgraph_external.py`, +11 Tests gesamt)

## [1.99.103] — 2026-07-06

### Added / Changed
- **Voice: `refine_task` braucht keine Task-ID mehr + neues `get_delegated_tasks`.** Das Modell musste sich bisher Task-IDs merken, um eine laufende Aufgabe nachzubessern — im schnellen Sprachfluss unzuverlässig, weshalb gpt-realtime bei Korrekturen oft eine NEUE Aufgabe aufmachte. Neu: `refine_task.task_id` ist optional → ohne id trifft es automatisch die zuletzt laufende Aufgabe (kein ID-Merken nötig). Zusätzlich listet `get_delegated_tasks` die in diesem Voice-Gespräch delegierten Aufgaben (id, Auftrag, läuft/fertig), damit das Modell bei mehreren Aufgaben die richtige wählen/berichten kann. Prompt entsprechend geschärft. Engine-übergreifend (Nova Sonic + Azure Realtime). (`realtime_voice_session.py`)

## [1.99.102] — 2026-07-06

### Fixed
- **Voice-Aufgabenkarten: Nachbesserung (refine_task) wird als DIESELBE Aufgabe angezeigt, nicht als neue Karte.** Das Frontend hängte pro `delegate`-Event stumpf eine neue Karte an und ignorierte die mitgesendete `task_id`/`refine` — dadurch erschien eine per `refine_task` fortgesetzte Aufgabe als mehrere Karten („Bot meldet eine Aufgabe, UI zeigt einzelne"). Neu werden Karten nach `task_id` dedupliziert: eine Nachbesserung aktualisiert die bestehende Karte, nur echte neue Aufgaben bekommen eine eigene. (`frontend/src/components/agents/voice-session.tsx`)

## [1.99.101] — 2026-07-06

### Fixed
- **Azure-Realtime-Voice: Delegations-Report/Antwort kam nach einer Aufgabe nicht mehr.** OpenAI Realtime erlaubt nur EINE aktive Antwort gleichzeitig; die Engine feuerte `response.create` (Report/Tool-Result), während schon eine Antwort lief → Server lehnte mit „Conversation already has an active response" ab → nichts wurde gesprochen. Neu werden Response-Anforderungen gequeued und beim nächsten `response.done` nachgefeuert; der interne „active response"-Fehler wird nicht mehr als UI-Fehler angezeigt. (`voice_providers/realtime_azure_openai.py`)

## [1.99.100] — 2026-07-06

### Fixed
- **Voice-Gespräch erscheint sofort als Chat-Tab (kein Reload mehr nötig).** Die Session-Liste wurde nur beim Mount geladen; ein beendetes Voice-Gespräch (frisch persistierte ChatSession) tauchte erst nach Seiten-Refresh auf. Neu lädt das Schließen der Voice-Session die Session-Liste neu (`refreshSessions`). (`frontend/src/components/agents/chat.tsx`)

## [1.99.99] — 2026-07-06

### Fixed
- **Azure-Realtime-Voice: Ton kam nach der Begrüßung nicht mehr.** Der Wrapper verwirft bei Barge-in (`interrupted`) allen Ton bis zum nächsten `content_start` — den sendet Nova Sonic, die Azure-Engine bisher nicht. Nach dem ersten Reinsprechen blieb `_drop_audio` dauerhaft an → nur die Begrüßung war hörbar. Neu sendet `AzureRealtimeSession` bei jedem `response.created` ein `content_start`. (`voice_providers/realtime_azure_openai.py`)
- **Voice-Delegation zeigte fremde Dateien / das echte Deliverable fehlte.** `_surface_new_files` dumpte beim ersten Task ALLE angesammelten Dateien aus `/workspace/transfer` (aus früheren Tasks). Neu wird der Transfer-Ordner beim Session-Start als Baseline gemerkt → nur während der Session neu erzeugte Dateien (z.B. das erzeugte PDF) werden angezeigt. (`realtime_voice_session.py`)

## [1.99.98] — 2026-07-06

### Fixed
- **Realtime-Voice-Selektor zeigt jetzt die im AI-Account hinterlegten Modelle statt einer festen Katalog-Liste.** Vorher listete `list_realtime_models` pro Provider-Typ mehrere fest verdrahtete Modelle (gpt-realtime + gpt-4o-realtime + mini) — alle mit derselben Engine+Account, sodass beim Anklicken eines Modells alle als „Aktiv" markiert wurden. Neu wird pro Account genau das/die dort konfigurierte(n) Modell(e) angezeigt (der Kundenanlage Azure realtime → nur `gpt-realtime`) → eindeutige Auswahl. (`api/ai_accounts.py::list_realtime_models`)

## [1.99.97] — 2026-07-06

### Added
- **Azure OpenAI Realtime als zweite Voice-Engine (flüssiges Auto-Speech-to-Speech OHNE AWS).** Neben AWS Nova Sonic gibt es jetzt eine `AzureRealtimeSession`, die das OpenAI-Realtime-WS-Protokoll gegen Azures `/openai/v1/realtime` (Modell `gpt-realtime`, GA) spricht. Damit bekommen Deployments ohne AWS (z.B. der Kundenanlage) dasselbe kontinuierliche Sprach-Erlebnis wie Nova Sonic — über die vorhandene Azure-OpenAI-Ressource, ohne separaten Speech-Key, ohne externen Edge-TTS, ohne lokalen stt-service. Browser-16kHz-Audio wird auf 24kHz upgesampelt; Ausgabe läuft über den bestehenden glatten PCM-Playback-Pfad. Der `ask_agent`/`refine_task`-Delegations- und Tool-Layer wird wiederverwendet (Tool-Format automatisch Nova↔OpenAI konvertiert). Auswählbar in den Voice-Settings („GPT Realtime (GA)"); Provider `azure-realtime` im AI-Accounts-Bereich. E2E gegen echtes der Kundenanlage-Azure verifiziert. (`voice_providers/realtime_azure_openai.py`, `realtime_catalog.py`, `realtime_voice_session.py`, `api/ws.py`)

## [1.99.96] — 2026-07-06

### Fixed
- **Voice/Mikrofon im Browser funktioniert wieder.** Der Caddy-`Permissions-Policy`-Header hatte `microphone=()` (für alle verboten) → der Browser blockte den Mikrofon-Zugriff der Voice-Session hart („Permissions policy violation: microphone is not allowed"), selbst bei erlaubtem Browser-Toggle. Neu: `microphone=(self)` (camera/geolocation bleiben restriktiv). (`Caddyfile`, `deploy/Caddyfile`)

## [1.99.95] — 2026-07-06

### Fixed
- **Frontend-Build repariert: Dependabot-#249 zurückgerollt.** Der auto-gemergte Bump hob Next.js 14→16 und Tailwind 3→4 (jeweils Major, Breaking) an, ohne die Config zu migrieren → `npm run build` brach (Turbopack-vs-webpack + `@tailwindcss/postcss`). Revert stellt Next 14 + Tailwind 3 wieder her; der Upgrade wird separat und getestet nachgeholt. (`frontend/package.json`, `package-lock.json`)
- **url_allowlist Startup-Crash behoben** (aus v1.99.94): fehlender `Request`-Import führte zu `NameError` beim Orchestrator-Start.

## [1.99.93] — 2026-07-06

### Security
- **Multi-Tenant-Isolation Teil 2 — komplette Router-Sweep (3 Audit-Runden + Verifikation).** Nach v1.99.92 wurden ALLE ~40 Router geprüft; die restlichen tenant-übergreifenden Lecks/IDORs geschlossen. Admin behält überall vollen Zugriff (`visible_agent_ids`):
  - **tasks.py** `/cost-attribution` (Dashboard „Cost Attribution / Platform Total"), **event_triggers.py** (list/get/create/update/delete/toggle/test — es konnten auto-feuernde Prompts auf fremde Agenten gepflanzt werden), **memory.py** (update/delete/room-summary), **ratings.py** (`rate_task` Cross-Tenant-Task-Injection, agent-ratings, improvement-report), **todos.py** (list/create/update/delete).
  - **secrets.py** (update/delete/get/assign/unassign + **Secrets jetzt Default-Deny** analog AI-Accounts), **skill_marketplace.py** (assign/unassign/get_agent_skills).
  - **agents.py** team/messages+delegations+conversation, **url_allowlist.py** (8 Stellen inkl. eines vorher **authlosen** Endpoints + fail-open-Wipe), **command_policies.py** (update-Hijack), **approval_rules.py** (create/update/delete + globale Autonomie-Presets nun admin-only), **approvals.py** (cancel), **webhooks.py** (settings/token/events — gaben `webhook_token` preis).
  - Verifikations-Scan behob 2 Blocker: spoofbarer `X-Internal`-Header in `rate_task` entfernt (Telegram nutzt echten Admin-JWT); `get_agent_allowlist` Dual-Auth (Agent-HMAC vs. User-Session) statt fail-open. `can_use_ai_account`/`can_use_secret` als Landminen entfernt.

### Fixed
- **Datei-Anhänge im Chat werden jetzt tatsächlich gelesen (PDF u.a.).** Der Agent bekam beim Anhängen nur eine passive Notiz („Datei in /workspace") und riet aus dem Dateinamen. Neu: explizite Anweisung mit vollem Pfad, die Datei ZUERST mit dem Read-Tool zu öffnen (PDFs/Bilder unterstützt). (`frontend/src/components/agents/chat.tsx`)
- **Alembic-Branch bereinigt.** `#300` (gpt-5.5-Backfill, `515d03f814a0`) war vom falschen Parent abgezweigt → zwei Heads, `alembic upgrade head` mehrdeutig. Merge-Migration `0ea61527a17e` vereint sie wieder zu einem Single-Head (Pi + der Kundenanlage).

## [1.99.92] — 2026-07-06

### Security
- **Multi-Tenant-Isolation: Nicht-Admins sehen keine fremden Daten mehr (Default-Deny).** Mehrere Read-Endpoints lieferten tenant-übergreifend Daten aus. Behoben mit zentralem Ownership-Helper (`app/core/ownership.py`, `visible_agent_ids`) und Scoping auf die eigenen/geteilten Agenten des Nutzers (Admin sieht weiter alles):
  - **Analytics** `/overview`, `/agents`, `/agents/{id}`, `/skills`, `/skills/{id}/trend` — Kosten/Tasks/Ratings/Zeitersparnis jetzt pro Nutzer (Dashboard „Cost Attribution / Top-Agenten" inklusive). (`analytics.py`)
  - **Knowledge** `/tags`, `/graph`, `get_entry`-Backlinks, `create_entry`-Dublettencheck sowie der Agent-`agent_write`-Upsert scopen jetzt auf `user_id` — kein Tag-/Titel-Leak und kein tenant-übergreifendes Überschreiben mehr. (`knowledge.py`)
  - **Meeting Rooms** — Liste + alle per-ID-Endpoints (IDOR) autorisieren jetzt (`_authorize_room`); Räume/Termine dürfen nur eigene Agenten enthalten. (`meeting_rooms.py`)
- **Geteilte Infra ist Default-Deny + Freigabe.** AI-Accounts (Claude/Codex/AWS) und OAuth-Integrations sind für Nicht-Admins standardmäßig unsichtbar; sichtbar/nutzbar nur nach expliziter Freigabe über die Rollen-Allowlist (`ai_account_ids`). (`ai_accounts.py`, `agents.py` Create + `update_agent_ai_account`, `oauth_service.py`/`integrations.py`, `settings.py` Harness-Flags)

## [1.99.91] — 2026-07-06

### Added
- **User-Avatar aus Microsoft-SSO in Sidebar + Chat.** Neuer Endpoint `GET /auth/me/photo` proxied das Profilfoto via gespeichertem per-User-Graph-Token (`/me/photo/$value`, 1h-Cache, 404 wenn kein Foto/kein MS-User). Neue `UserAvatar`-Komponente zeigt das Foto unten links in der Sidebar (statt Initialen-Box) und in den User-Chat-Bubbles (statt blauem Icon); ohne Foto automatisch Initialen. Foto wird einmal pro Page-Load geladen und über alle Bubbles geteilt. (`orchestrator/app/api/auth.py`, `frontend/src/components/ui/user-avatar.tsx`, `layout/user-menu.tsx`, `agents/chat.tsx`)

## [1.99.90] — 2026-07-06

### Changed
- **Chat: Dateien werden angehängt statt sofort gesendet (wie Bild-Paste).** Drag & Drop und Büroklammer lösen keinen Sofort-Upload mit Auto-Nachricht mehr aus: Bilddateien landen als Thumbnail, alle anderen Dateien als Chips (Name + Größe + Entfernen-Button) am Eingabefeld — genau wie per Strg+V eingefügte Bilder. Man kann Text dazu schreiben; erst beim Senden werden die Dateien nach `/workspace` hochgeladen und gehen als EINE Nachricht mit Datei-Chips in der Bubble raus (der Agent bekommt den Text plus Datei-Hinweis). Schlägt der Upload fehl, bleiben Text und Anhänge erhalten. (`frontend/src/components/agents/chat.tsx`)

## [1.99.89] — 2026-07-06

### Added
- **Chat: Eingabefeld ist jetzt Drag&Drop-Ziel für Datei-Uploads.** Die Drop-Zone deckt den gesamten Chat ab (Nachrichtenverlauf UND Eingabebereich) — Dateien können direkt aufs Textfeld gezogen werden, Upload nach `/workspace` + Agent-Benachrichtigung wie gehabt. Drag-Overlay flackert dank Enter/Leave-Zähler nicht mehr beim Ziehen über Kind-Elemente; reine Text-Drags lösen kein Overlay aus. (`frontend/src/components/agents/chat.tsx`)

### Fixed
- **Chat-Eingabefeld wächst bei mehrzeiligem Text mit.** Die Textarea passt ihre Höhe automatisch dem Inhalt an (bis ca. 8 Zeilen, danach interner Scroll) und springt nach dem Senden auf eine Zeile zurück. Buttons (Anhang/Mic/Senden) bleiben unten ausgerichtet. (`frontend/src/components/agents/chat.tsx`)
- **Zeilenumbrüche bleiben in der Chat-Bubble erhalten.** Mehrzeilige Nachrichten (Shift+Enter) wurden in der User-Bubble zu einer Zeile zusammengezogen — jetzt `whitespace-pre-wrap`. (`frontend/src/components/agents/chat.tsx`)

## [1.99.88] — 2026-07-06

### Added
- **Voice: Aufgaben gezielt nachbessern statt neue aufmachen (`refine_task`).** Jede vom Voicebot delegierte Aufgabe (`ask_agent`/`delegate_tasks`) bekommt jetzt eine kurze, adressierbare id in einer eigenen Session-Lane (`vw-<call>-<id>`). Korrigiert oder ergänzt der Nutzer mitten in der Arbeit („mach's doch anders", „nimm lieber X"), trägt das Modell den Satz per `refine_task(id, satz)` in GENAU diese Aufgabe nach — sie läuft mit vollem Kontext weiter (Live-Steering in den laufenden Turn bzw. `--resume`), statt eine zweite, kontextlose Aufgabe zu forken. `get_agent_activity` listet die Aufgaben mit ihren ids. (`orchestrator/app/services/realtime_voice_session.py`)

### Fixed
- **Voice-Fokusmodus bricht nicht mehr mit Fehler ab.** Bei stummem Mikro (Fokusmodus) floss keine Audiospur mehr → der Nova-Sonic/Bedrock-Bidi-Stream lief in den Idle-Timeout und riss mit „Fehler" ab. Neu hält ein Keepalive den Stream warm: nach ~5s ohne echtes Audio wird ein kurzer Stille-Frame gesendet (verhält sich wie ein stummgeschaltetes, aber offenes Mikro; VAD ignoriert Stille, kein Fehl-Turn). (`orchestrator/app/services/realtime_voice_session.py`)

## [1.99.72] — 2026-07-05

### Added
- **Taskforce-Transparenz: Phasen-Leiste + Live-Bau-Kacheln im Meeting-View.** Deliverable-Meetings zeigen jetzt eine Phasen-Leiste (Planung → Zuweisung → Bau → Integration → Fertig, aktuelle Phase animiert) plus pro Agent eine Live-Kachel (Spinner „baut…" / „fertig" / „Fehler") + Koordinator-Kachel + Live-Dateizähler — alle 4s aktualisiert. (`frontend/src/app/meeting-rooms/[id]/page.tsx`, `deliverable/files`-Endpoint um build_tasks/integration_status erweitert)

### Fixed
- **Taskforce-Härtung.** (1) Synthese-Fallback: produziert die Todo-Synthese eines Deliverable-Meetings keine Items (z.B. weil ein Agent nicht antwortete), werden jetzt Fallback-Bau-Aufgaben aus dem Ziel erzeugt statt lautlos NICHTS zu dispatchen. (2) Bau-/Integrations-Prompts geschärft: kein `git init`/`__pycache__`, ein einziges Projekt (keine verschachtelten Doppelordner), keine „getestet/lauffähig"-Behauptung ohne echten Lauf. (3) Ergebnis-Listing blendet `.git`/`__pycache__`/`.pyc`/venv aus. (`orchestrator/app/api/meeting_rooms.py`)

## [1.99.71] — 2026-07-05

### Security
- **Taskforce-Bauverzeichnis nicht mehr world-writable.** Der Permission-Fix aus v1.99.70 nutzte `chmod 0o777` — zu weit. Neu: `chown 1000:1000` (Agent-uid) + `chmod 0o770`, plus Symlink-Guard (kein `chmod` auf Symlinks). Agenten haben Zugriff, fremde Prozesse auf Multi-Tenant-Boxen nicht. (`orchestrator/app/api/meeting_rooms.py`)

## [1.99.70] — 2026-07-05

### Fixed
- **Taskforce-Bau produzierte keine Dateien (Permission-Bug auf `/shared`).** Das geteilte Volume `/shared` gehört `root:root` (755), die Agenten laufen aber als uid 1000 → beim Anlegen von `/shared/taskforce/{id}/` bekamen sie „Permission denied" und der ganze Bau lief lautlos ins Leere (0 Dateien). Neu: der Orchestrator (root) legt das Taskforce-Verzeichnis **world-writable an, bevor** die Bau-Tasks dispatcht werden (`_ensure_taskforce_dir`). (`orchestrator/app/api/meeting_rooms.py`)

## [1.99.69] — 2026-07-04

### Added
- **Meeting-Raum „Taskforce"-Modus — echtes, lauffähiges Ergebnis statt nur To-do-Liste.** Neuer Schalter beim Anlegen eines Meetings: „Taskforce — echtes Ergebnis bauen". Ist er an, arbeiten die Agenten nicht nur ihre Action-Items ab, sondern bauen **gemeinsam ein echtes Artefakt** (z.B. eine App) in einem geteilten Arbeitsverzeichnis `/shared/taskforce/{id}/`. Ablauf: Besprechung → parallele Bau-Tasks (jeder Agent produziert echte Dateien, koordiniert über `PROGRESS.md`) → sobald alle Teil-Tasks fertig sind, dispatcht der Scheduler automatisch einen **Integrations-Task** an einen Koordinator, der die Teile zu einer lauffähigen Anwendung zusammenführt (README + RESULT.md). Das Ergebnis (alle Dateien) ist im Meeting-Summary sichtbar/lesbar. Klassische „nur besprechen"-Meetings bleiben unverändert. (`orchestrator/app/models/meeting_room.py` + Migration `c4d5e6f7a8b9`, `orchestrator/app/api/meeting_rooms.py`, `orchestrator/app/services/scheduler_service.py`, `frontend/src/app/meeting-rooms/page.tsx`)

## [1.99.68] — 2026-07-04

### Added
- **Parallele Sessions pro Agent einstellbar.** Im Agent-Reiter „Settings" gibt es jetzt einen Regler „Parallele Sessions" — er legt fest, wie viele Sessions der Agent gleichzeitig bearbeitet (gilt für Aufgaben UND Chats). Alles darüber wird automatisch in die Warteschlange gestellt und startet, sobald ein Platz frei wird. Bisher war das nur global (`MAX_PARALLEL_TASKS`/`MAX_PARALLEL_CHATS`); jetzt pro Agent überschreibbar (`agent.config.parallel_sessions`, 1–16), Fallback auf den globalen Default. Ändern startet den Agenten neu, damit die neue Grenze greift. Queue-Mechanik (Semaphore in `task_consumer`/`chat_consumer`) war bereits vorhanden. (`orchestrator/app/services/agent_settings.py`, `orchestrator/app/api/agents.py`, `orchestrator/app/core/agent_manager.py`, `frontend/src/app/agents/[id]/page.tsx`)

## [1.99.67] — 2026-07-04

### Fixed
- **Scroll-Bug im „Meeting-Raum erstellen"-Dialog.** Nach Aufklappen der „Erweiterten Einstellungen" wuchs der Dialog über den Bildschirm hinaus, hatte kein eigenes Scrolling → das Mausrad scrollte die Seite dahinter statt den Dialog, „Meeting starten" war nicht mehr erreichbar. Neu: Dialog auf `max-h-[90dvh]` gedeckelt, feste Kopfzeile + fixer Footer (Cancel/Create), scrollbarer Mittelteil (gleiches Muster wie die Summary-Modal). (`frontend/src/app/meeting-rooms/page.tsx`)

## [1.99.66] — 2026-07-04

### Fixed
- **Claude-Chat über Anthropic-API brach mit 400 „Tool names must be unique" ab.** Anthropic lehnt doppelte/leere Tool-Namen strikt ab (OpenAI toleriert sie); der Tool-Katalog kann Namenskollisionen tragen (Built-in vs Orchestrator-API vs MCP). Neu: zentrale Deduplizierung im `AnthropicProvider` (`_to_anthropic_tools`, erste Nennung gewinnt, leere Namen raus) — greift für Chat, Tasks und Messages gleichermaßen. Tritt zusammen mit v1.99.65 auf (dort erst wurde der AnthropicProvider für Azure-Claude überhaupt aktiv). Tests: `agent/tests/test_provider_routing.py`. (`agent/app/providers/anthropic_provider.py`)

## [1.99.65] — 2026-07-04

### Fixed
- **Azure-gehostete Claude-Modelle (Custom-LLM) gaben 401.** Die Azure-„Anthropic/Claude"-Surface (`…/anthropic/v1/messages`) spricht die Anthropic-Messages-API (`x-api-key` + `anthropic-version`), wird aber naturgemäß mit Provider-Typ `azure-openai` konfiguriert → landete im OpenAI-Provider, der eine falsche Deployment-URL baute und `api-key` statt `x-api-key` sendete (401 „invalid subscription key or wrong API endpoint"). Neu: `create_provider` erkennt eine `/anthropic/`-Surface und routet sie auf den `AnthropicProvider` — unabhängig vom `azure-openai`-Typ (der Typ bleibt wichtig, damit die Harness-Mode auf `custom_llm` bleibt und nicht auf die claude_code-CLI umspringt). Beide Endpoint-Formen (`…/anthropic/v1` und `…/anthropic/v1/messages`) werden akzeptiert. Regressionstests: `agent/tests/test_provider_routing.py`. (`agent/app/providers/__init__.py`)

## [1.99.64] — 2026-07-04

### Changed
- **„Onboarding"-Eintrag in der Sidebar vorerst ausgeblendet.** Der Nav-Punkt (inkl. Rocket-Icon) wird nicht mehr angezeigt; die Seite bleibt unter `/onboarding` direkt erreichbar. (`frontend/src/components/layout/sidebar.tsx`)

## [1.99.63] — 2026-07-04

### Added
- **`EMBEDDING_ENABLED`-Flag (Semantic-Search abschaltbar).** Auf ressourcenarmen Hosts (Raspberry Pi) lastet der lokale bge-m3-Embedding-Service die CPU zu ~90% aus. Neu: `EMBEDDING_ENABLED=false` überspringt den Embedding-Dienst komplett — kein Verbindungsversuch, kein 30s-Retry, kein Warn-Spam im `platform-errors.log`. Die Semantic-Search fällt sauber auf Keyword-Suche zurück. Default `true` (bestehende Deployments unverändert). (`orchestrator/app/config.py`, `orchestrator/app/services/embedding_service.py`, `docker-compose.yml`)

## [1.99.55] — 2026-07-03

### Fixed
- **Knowledge-Graph füllt die Fläche (Layout im festen Quadrat-Raum).** Die Simulation rechnete in den Canvas-Maßen — auf breit/flachem Canvas wurden die Knoten zur horizontalen Linie gequetscht. Neu: Layout in einem festen quadratischen Virtual-Space (unabhängig vom Canvas), danach per Fit auf den Canvas skaliert → schöner 2D-Cluster, zentriert, füllend. (`frontend/src/app/knowledge/page.tsx`)

## [1.99.54] — 2026-07-03

### Fixed
- **Knowledge-Graph Auto-Fit jetzt zuverlässig.** Der Fit lief nur bei `simDone`, was bei mehrfachem Resize nie feuerte → Knoten off-screen. Neu: kontinuierlicher Fit während des Settle (auf `simNodes`-Updates), stoppt sobald der Nutzer pannt/zoomt. (`frontend/src/app/knowledge/page.tsx`)

### Added
- **Proactive-Mode-Indikator auf der Agent-Karte.** Ist der Proaktiv-Modus aktiv, zeigt die Karte einen grünen Blitz + Intervall-Pille (z.B. „1h"). (`frontend/src/components/dashboard/agent-card.tsx`)

## [1.99.53] — 2026-07-03

### Fixed
- **Neue Deploys waren durch CDN-Cache unsichtbar (Kern-Ursache vieler „nichts ändert sich"-Momente).** Die HTML-Shell kam mit `Cache-Control: s-maxage=31536000` (1 Jahr) → Cloudflare lieferte die alte HTML mit alten JS-Bundle-Referenzen. Neu: Caddy setzt für HTML `no-cache, must-revalidate` und nur `/_next/static/*` bleibt immutable-gecacht → neue Builds sind sofort sichtbar, ohne Hard-Refresh. (`Caddyfile`)
- **Knowledge-Graph füllt die Fläche (Layout-Spread).** Mehr Repulsion/Link-Distanz + weniger Gravity, damit die Knoten sich verteilen statt zu klumpen (zusammen mit Auto-Fit aus 1.99.52). (`frontend/src/app/knowledge/page.tsx`)

## [1.99.52] — 2026-07-03

### Fixed
- **Knowledge-Graph: Auto-Fit statt winziger Knoten-Klumpen abseits der Ansicht.** Der Graph zoomte/zentrierte nicht — die Knoten saßen off-screen und wirkten winzig. Neu: nach dem Settle wird die Bounding-Box aller Knoten berechnet und der Graph mittig auf ~85% des Canvas eingepasst (Auto-Fit, re-fit bei Resize); der Maximieren-Button macht ebenfalls „einpassen". (`frontend/src/app/knowledge/page.tsx`)

## [1.99.51] — 2026-07-03

### Fixed
- **Knowledge-Graph im Light Mode brauchbar + größere Knoten.** Der Graph war dark-only: Kanten `#ffffff` und Labels `fill="white"` waren auf weißem Canvas unsichtbar, Legenden-/Overlay-Boxen hatten `bg-black` (dunkle Kästen im Light Mode). Neu: Kanten/Labels/Hover-Rahmen theme-aware (slate im Light, weiß im Dark), Legenden/Panels `bg-card` (dark bleibt via `dark:bg-black`). Knoten deutlich größer (Basis 3→6px, Max 16→24px) und leichter zu treffen. (`frontend/src/app/knowledge/page.tsx`)

## [1.99.50] — 2026-07-03

### Fixed
- **Orchestrator-Crash bei leerem `KIOSK_ENABLED` behoben (aus #290).** Compose reichte `${KIOSK_ENABLED:-}` (leerer String) durch, und `kiosk_enabled: bool` ließ sich nicht parsen → `pydantic ValidationError` → Crash-Loop beim `compose up`. Neu: Field-Validator (leerer String → False) + Compose-Default `false`. (`orchestrator/app/config.py`, `docker-compose.yml`)

## [1.99.49] — 2026-07-03

### Added
- **Echte Task-Parallelität pro Agent (`MAX_PARALLEL_TASKS`).** Bisher liefen proaktive/geplante Tasks strikt seriell (einer nach dem anderen). Neu: der Task-Consumer holt nur einen Task aus Redis, wenn ein Semaphore-Slot frei ist, und führt bis zu N Tasks gleichzeitig aus — **jeder in einer eigenen Runner-Instanz (eigener Subprozess)**, funktioniert für **codex, claude UND custom_llm** (einheitliches `execute_task`/`interrupt`/`is_running`). Default 1 = seriell (unverändert). Durchgereicht via Orchestrator→agent_manager→Container-Env, analog `MAX_PARALLEL_CHATS`. (`agent/app/task_consumer.py`, `orchestrator/app/config.py`, `orchestrator/app/core/agent_manager.py`, `docker-compose.yml`)

## [1.99.48] — 2026-07-03

### Fixed
- **HOTFIX: Orchestrator-Crash-Loop / 502 behoben.** PR #290 hatte in `router.py` `settings.kiosk_enabled`, wobei `settings` durch den nachfolgenden `from app.api import ... settings`-Import das **Modul** statt des Config-Objekts war → `AttributeError` beim Import → Orchestrator startete nicht, ganze Seite 502. Config-Import auf `app_config` aliasiert. (`orchestrator/app/api/router.py`)

## [1.99.47] — 2026-07-03

### Fixed
- **Chat hängt nicht mehr auf „Thinking…" nach Agent-Update/Restart.** Wurde ein Agent während einer laufenden Antwort neu erstellt (Update/Restart), wurde der Codex/Claude-Prozess mitten im Stream gekillt und das Frontend bekam nie ein Terminal-Event → ewiges „Thinking…". Neu: `AgentManager` broadcastet vor dem Container-Stop ein `cancelled`-Event (leeres message_id → an alle offenen Chat-Streams des Agenten) auf `agent:{id}:chat:response`; das bestehende Frontend-Handling beendet damit den Warte-Zustand sauber. (`orchestrator/app/core/agent_manager.py`)

## [1.99.46] — 2026-07-03

### Fixed
- **Sidebar-Footer im Mobile-Drawer zeigt jetzt Labels.** Der Footer (Notifications/Theme/Star/Über) prüfte `collapsed` (Desktop-Zustand) statt `effectiveCollapsed` → bei desktop-eingeklappter Sidebar erschien er auf dem Handy als karge Icon-Spalte, obwohl die Navigation Labels hatte. (`frontend/src/components/layout/sidebar.tsx`)

## [1.99.45] — 2026-07-03

### Fixed
- **Dashboard-Statusleiste mobil.** Die „All Systems Go"-Pille wurde in eine Zeile gequetscht und brach auf 3 Zeilen um. Neu: Pille `whitespace-nowrap`, Leiste darf umbrechen (`flex-wrap`), Trenner nur ab Desktop. (`frontend/src/components/dashboard/system-status-bar.tsx`)

## [1.99.44] — 2026-07-03

### Fixed
- **Voice-Session (Nova Sonic) mobil brauchbar.** Das zentrierte `max-w-6xl`-Modal mit drei je 48–60vh hohen Panes war auf dem Handy oben/unten abgeschnitten (animiertes Gesicht + Steuerung nicht erreichbar). Neu: Vollbild + scrollbar auf Mobile (top-aligned), Panes kompakter (`42vh`/`26vh`), Desktop bleibt der zentrierte Cockpit. (`frontend/src/components/agents/voice-session.tsx`)

## [1.99.43] — 2026-07-03

### Fixed
- **Meeting-Rooms-Karten Grid-Blowout behoben.** Der `1fr`-Grid-Track hat default `min-width:auto` → eine Karte mit langem Inhalt blähte den Track über die Viewport-Breite auf. Fix: `min-w-0` auf der Karte, damit sie schrumpfen kann und Titel/Beschreibung sauber kürzen statt rechts rauszulaufen. (`app/meeting-rooms/page.tsx`)

## [1.99.42] — 2026-07-03

### Fixed
- **Mobile: restliche Overflow-Stellen (2. Simulator-Durchlauf).** Knowledge-eigener Header stapelt jetzt (+ Hamburger-Platz), Skill-Marketplace-Tabs scrollen horizontal, Meeting-Room-Beschreibung mit `break-words`. Globales Sicherheitsnetz: `overflow-x-hidden` am Haupt-Content — keine Seite kann mehr horizontal überlaufen. (`app/knowledge/page.tsx`, `app/skills/page.tsx`, `app/meeting-rooms/page.tsx`, `components/auth/auth-guard.tsx`)

### Changed
- **Emojis aus den Skill-Marketplace-Tabs entfernt** (Ausstehend/Verbesserungen) — konform zur No-Emoji-Vorgabe. (`app/skills/page.tsx`)

## [1.99.41] — 2026-07-03

### Fixed
- **Mobile: horizontaler Overflow auf mehreren Seiten behoben** (per iOS-Simulator-Durchlauf gefunden). (1) Shared Header stapelt auf Mobile (Titel oben, Actions darunter mit Umbruch) statt Buttons rechts abzuschneiden — behebt Agents/Knowledge/Agent-Detail. (2) Tab-/Filter-Reihen (Tasks-Filter, Agent-Detail-Sub-Reiter) scrollen jetzt horizontal statt zu clippen (`max-w-full overflow-x-auto` + `whitespace-nowrap`). (3) Knowledge-Zwei-Spalten-Layout stapelt auf Mobile (`flex-col lg:flex-row` + `min-w-0`) → Karten-Text bricht/kürzt korrekt statt rechts rauszulaufen. (`components/layout/header.tsx`, `app/tasks/page.tsx`, `app/agents/[id]/page.tsx`, `app/knowledge/page.tsx`)

## [1.99.40] — 2026-07-03

### Fixed
- **Mobile/Responsive: Sidebar ist jetzt ein Off-Canvas-Drawer.** Der Hauptinhalt hatte ein hartes `ml-[260px]` (auf dem Handy wurde alles 260px nach rechts geschoben und abgeschnitten). Neu: geteilter Sidebar-Context (collapsed + mobileOpen), Content voll-breit auf Mobile (`lg:ml-…` erst ab Desktop), Sidebar slidet als Drawer ein (Hamburger oben links + Backdrop, Auto-Close beim Navigieren). Betrifft alle Menüpunkte. (`frontend/src/hooks/use-sidebar.ts`, `components/auth/auth-guard.tsx`, `components/layout/sidebar.tsx`, `components/layout/header.tsx`)

### Security
- **App-Proxy: agenten-geschriebene Apps laufen jetzt sandboxed.** Der Reverse-Proxy servierte App-HTML/JS von der Plattform-Origin → der App-Code hätte same-origin mit dem Ambient-Cookie die Plattform-API als Nutzer aufrufen können. Neu: erzwungenes `Content-Security-Policy: sandbox` (opaque Origin, kein Zugriff auf Plattform-Cookies/API) + `X-Content-Type-Options: nosniff`; eine vom App gesetzte CSP wird überschrieben. (`orchestrator/app/api/docker_apps.py`)

## [1.99.39] — 2026-07-03

### Added
- **Agenten nachträglich umbenennen.** Neuer Endpoint `PATCH /agents/{id}/name` + Inline-Rename im Agent-Header (Stift-Icon). Ändert nur den Anzeigenamen (DB + Team-Registry), kein Container-Neustart. Input wird validiert (nicht leer, max. 40 Zeichen, Steuerzeichen entfernt), AuthZ per Ownership. (`orchestrator/app/api/agents.py`, `frontend/src/app/agents/[id]/page.tsx`)
- **Docker-Apps: Ein-Klick-Deploy ohne Port-Konflikt.** Feste Host-Ports (`3001:3000`) scheiterten beim zweiten Deploy an „port is already allocated". Neu: eine generierte Sidecar-Compose-Datei publiziert nur den Container-Port → Docker vergibt automatisch einen freien Host-Port. Original bleibt unangetastet. (`orchestrator/app/api/docker_apps.py`)
- **Docker-Apps: von außen erreichbar über den Orchestrator-Proxy.** Bisher verlinkte die UI `http://<host>:<hostport>` — das geht NICHT durch den Cloudflare-Tunnel (nur 443/80). Neu: `GET /agents/{id}/apps/proxy/{container}/{port}/…` proxied durch die bestehende Cloudflare+Caddy-Kette an den App-Container. Auth + doppelter Ownership-Gate (Namens-Präfix + Compose-Projekt-Label), Auth-Cookie/Authorization werden NICHT an die App weitergereicht. (`orchestrator/app/api/docker_apps.py`, `frontend/src/components/agents/docker-apps-tab.tsx`)

### Security
- **Container-Namen-Ableitung gehärtet.** Der Docker-Container-Name wird aus dem Agent-Namen abgeleitet — bisher nur `lower().replace(' ','-')`. Ein Name mit Sonderzeichen/Umlauten hätte einen ungültigen/injizierbaren Docker-Namen bei (Neu-)Erstellung erzeugt. Neu: sauberer Slug (`[a-z0-9]`-Whitelist). (`orchestrator/app/core/agent_manager.py`)

## [1.99.38] — 2026-07-03

### Fixed
- **Meeting: kein Roh-JSON mehr von Codex-Agenten.** Codex-Harness-Agenten posteten den rohen Event-Stream (`{"type":"item.started"...}` inkl. `sed`-Kommandos) statt der fertigen Antwort — der Parser in `_execute_cli` suchte Text auf Event-Top-Level, im aktuellen Codex-Schema liegt er aber in `item.text` → nichts gefunden → Fallback auf abgeschnittenes Roh-JSON. Jetzt Wiederverwendung des bewährten `codex_runner._extract_text` (rekursiv in `item`/`payload`), kein Roh-JSON-Fallback mehr. (`agent/app/message_consumer.py`)
- **Meeting: Agenten referenzieren sich per NAME statt roher UUID.** Der Kontext, den jeder Agent sieht, war mit `agent_id` (z. B. `2ad91565`) statt Namen gelabelt → Agenten zitierten einander/sich selbst als UUID. (`orchestrator/app/api/meeting_rooms.py`)
- **Meeting: leere Platzhalter-Meldungen erscheinen nicht mehr als Bubble.** `[<id> had nothing to add this turn]`/Fehler/Timeout werden zentral im Cleaner verworfen; ein stummer Sprecher bekommt stattdessen die saubere namensbasierte „hat nicht geantwortet"-Zeile. (`orchestrator/app/api/meeting_rooms.py`)

## [1.99.37] — 2026-07-03

### Fixed
- **Erzeugte Dateien werden jetzt zuverlässig als klickbare Karten gezeigt (Auto-Scan).** Der `present_file`-Hinweis (v1.99.36) reichte nicht — der Agent nannte oft nur den Pfad im Text. Neu: nach jeder Delegation scannt die Voice-Session `/workspace/transfer/` (inkl. Unterordner) und emittiert für jede noch nicht gezeigte Datei eine Download-Karte. Nutzt denselben FileManager/Download-Pfad wie der Datei-Browser, kein neuer Mechanismus. (`orchestrator/app/services/realtime_voice_session.py`)

## [1.99.36] — 2026-07-03

### Fixed
- **Erzeugte Dateien erscheinen wieder klickbar im Voice-UI.** Delegierte Aufgaben, die per bash/python Dateien nach `/workspace/...` schrieben, riefen kein `present_file` auf → keine Download-Karte. Jede delegierte Instruktion bekommt jetzt serverseitig den Zusatz, JEDE erzeugte Datei mit `present_file` zu präsentieren. (`orchestrator/app/services/realtime_voice_session.py`)

## [1.99.35] — 2026-07-03

### Fixed
- **Voice-UI: „Aufgabe erledigt" trotz laufender Aufgabe.** Das generische `response`-Event feuert auch für Novas EIGENE Sprache — es setzte fälschlich den Fertig-Status. Neu: dediziertes `delegate_done`-Event vom Server pro abgeschlossener Delegation; die UI markiert nur die passende Aufgabe als fertig. (`orchestrator/app/services/realtime_voice_session.py`, `frontend/src/components/agents/voice-session.tsx`)
- **Parallele Aufgaben jetzt EINZELN sichtbar.** Statt einer Sammelbox („Aufgabe: …" × N unter einer „erledigt"-Kachel) bekommt jede delegierte Aufgabe eine EIGENE Karte mit eigenem Live-Status (Spinner „Läuft" → grüner Haken „Erledigt"). (`frontend/src/components/agents/voice-session.tsx`)

### Added
- **Voice-Direkt-Tools `save_memory` + `list_todos`.** „Merk dir …" schreibt sofort ins Langzeitgedächtnis (pgvector), „was sind meine To-dos" liest die Aufgabenliste — beides ohne Agent-Round-trip. (`orchestrator/app/services/realtime_voice_session.py`)
- **System-Prompt: volles Skillset explizit.** Nova weiß nun, dass sie via `ask_agent` ALLES kann, was der Agent kann (Dateien, bash, M365/Outlook/Exchange, Brain, Inter-Agent-Team) — und sagt nie mehr „das kann ich nicht".

## [1.99.34] — 2026-07-03

### Added
- **`delegate_tasks`-Tool für echte Parallelität.** Bisher hoffte man, dass Nova Sonic bei „mach 3 Dinge parallel" 3 separate ask_agent-Calls macht — tat es aber oft nicht (delegierte 1 Sammel-Aufgabe → nicht parallel, verifiziert am Pi: nur 1 Prozess). Neu: ein Tool, das eine **Liste** von Aufgaben nimmt und JEDE als eigene parallele Session startet (1 Tool-Call → N parallele Lanes, gebremst durch MAX_PARALLEL_CHATS). (`orchestrator/app/services/realtime_voice_session.py`)
- **Kiosk-Facelift.** Ambient-Gradient-Hintergrund, Glas-Cards mit Tiefe (Shadow/Innenkante), State-gefärbte Agenten-Avatare, edlere Panels. (`frontend/src/app/kiosk/page.tsx`)

### Fixed
- **Parallele Delegationen sind jetzt alle im Voice-Panel sichtbar** — das `delegate`-Event resettete die Aktivitätsliste (nur die letzte Aufgabe blieb sichtbar); jetzt werden mehrere parallele Aufgaben angehängt. (`voice-session.tsx`)

## [1.99.33] — 2026-07-03

### Added
- **Voice setzt die offene Chat-Session fort (Kontext-Übernahme).** Öffnet man das Live-Gespräch aus einem Chat heraus, nutzt es dieselbe `session_id` — der Sprach-Agent lädt die letzten Turns (Text ODER Voice) und knüpft in der Begrüßung daran an („Willkommen zurück — wir waren bei …"). Voice + Text teilen sich damit eine durchgängige, fortsetzbare Session. WS-Param `chat_session`, Frontend-Prop `resumeSessionId`. (`orchestrator/app/api/ws.py`, `orchestrator/app/services/realtime_voice_session.py`, `frontend/src/components/agents/voice-session.tsx`, `chat.tsx`)

## [1.99.32] — 2026-07-03

### Fixed
- **„Alle Chats löschen" blendete gepinnte Chats fälschlich aus.** Das Backend behält gepinnte Sessions korrekt (bestätigt), aber das Frontend leerte nach dem Löschen die Tab-Liste komplett (`setSessions([])`) — die gepinnten Chats verschwanden bis zum Reload. Jetzt bleiben die gepinnten Tabs stehen (`filter(s => s.pinned)`). (`frontend/src/components/agents/chat.tsx`)

## [1.99.31] — 2026-07-03

### Added
- **Voice-Gespräche sind jetzt persistent + als Chat fortsetzbar.** Der ganze Sprach-Call wird als **Chat-Session** („Sprach-Gespräch") gespeichert: die Transkript-Turns (User + Agent) landen als ChatMessages in der DB → das Gespräch taucht in der Chat-Historie des Agenten auf und kann **per Text weitergeführt** werden (Voice-Wiederaufnahme mit Kontext folgt). Streamende Deltas werden pro Turn zu einer Nachricht zusammengefasst. (`orchestrator/app/services/realtime_voice_session.py`)

## [1.99.30] — 2026-07-03

### Added
- **Voice-Layer durchsucht direkt sein Wissen** — neues `search_knowledge`-Tool: Nova Sonic sucht das Gedächtnis/Wissen des Agenten per Vektorsuche (`agent_memories`, pgvector) **direkt**, ohne Agent-Round-Trip. Für „was weißt du über…", Kunde/Projekt/Kontakt/Verfahren. (`orchestrator/app/services/realtime_voice_session.py`)
- **Fokus-/Pause-Modus im Live-Gespräch** — „Fokus"-Button schaltet das Mikro stumm (Session bleibt aktiv, Stille wird gestreamt); der Agent arbeitet weiter und meldet sich per Sprache, wenn etwas fertig ist. „Fortsetzen" reaktiviert. (`voice-session.tsx`)

### Changed
- **Aufgaben-/Aktivitäts-Panel ist einklappbar** und zeigt bei Fertigstellung einen **grünen Haken (✓)** statt des Radio-Icons. (`voice-session.tsx`)

## [1.99.29] — 2026-07-03

### Fixed
- **Mikrofon-Fehler im Voice-Modal zeigt jetzt die echte Ursache** statt pauschal „Zugriff verweigert" (z. B. `NotAllowedError`/`NotFoundError`/`NotReadableError`). Zusätzlich Fallback auf einfache Audio-Constraints (`audio: true`) bei OverconstrainedError/NotFoundError — behebt manche USB-Mic-Fälle. (`frontend/src/components/agents/voice-session.tsx`)

## [1.99.28] — 2026-07-03

### Fixed
- **Barge-in verwirft jetzt auch die bereits generierten Audio-Chunks (der eigentliche Fix).** Nova Sonic generiert schneller als Echtzeit, daher lagen beim Unterbrechen schon viele Audio-Chunks in der server-seitigen Outbound-Queue (`_out_queue`) und wurden weiter an den Client gesendet — `_drop_audio` stoppte nur NEUE Emissionen. `interrupt()` **leert jetzt die Outbound-Queue von allen bereits eingereihten `audio_chunk`-Events** (behält Transkript/Response), zusätzlich zu Nova-Stopp + Client-Flush. Damit ist der unterbrochene Turn wirklich sofort still. (`orchestrator/app/services/realtime_voice_session.py`)

## [1.99.27] — 2026-07-03

### Fixed
- **`{"interrupted": true}` leakt nicht mehr in den Transkript-Text.** Nova Sonic sendet solche JSON-Metadaten-Blobs als textOutput — die werden jetzt erkannt, aus dem Text gefiltert und als Interrupt-Signal genutzt (→ server-seitiger Audio-Drop). (`realtime_nova_sonic.py`, `realtime_voice_session.py`)
- **Jarvis-3-Spalten-Layout überläuft nicht mehr bei mittlerer Breite** (Orb ragte ins rechte Panel). 3 Spalten erst ab `lg`, darunter gestapelt, + `min-w-0`. (`voice-session.tsx`)

### Changed
- **Keine erfundenen Fakten mehr (Anti-Halluzination).** System-Prompt: der Sprach-Agent darf Zahlen/Aufgaben/Task-Nummern/Dateinamen NICHT erfinden — nur Tool-Daten nennen, bei Unbekanntem web_search/ask_agent nutzen oder ehrlich „das prüfe ich" sagen. (Hintergrund: Agent behauptete „188 Aufgaben", real 52.)
- **Parallel-Delegation:** Bei mehreren parallelen Aufgaben ruft der Sprach-Agent `ask_agent` jetzt MEHRFACH (eine pro Aufgabe → getrennte Sessions laufen parallel) statt einer Sammel-Anweisung.

### Added
- **Präsentierte Dateien im Voice-Panel sind klickbar** (Download über `/agents/{id}/files/download`). Der `path` wird im media-Event mitgegeben. (`realtime_voice_session.py`, `voice-session.tsx`)

## [1.99.26] — 2026-07-03

### Fixed
- **Barge-in überspringt jetzt den GANZEN Rest-Turn, nicht nur den aktuellen Chunk.** Bisher stoppte das Unterbrechen nur das aktuell abgespielte Audio; Nova Sonic generierte server-seitig weiter, und nach dem Client-Timer liefen die nächsten Chunks/Sätze weiter. **Neu:** Beim Unterbrechen setzt die `RealtimeVoiceSession` ein `_drop_audio`-Flag und **verwirft alle weiteren Audio-Chunks des unterbrochenen Turns server-seitig** — es kommt gar nichts mehr beim Client an. Aufgehoben wird das erst, wenn Nova Sonic den nächsten Content-Block startet (echter neuer Turn; `contentStart`-Event wird dafür jetzt ausgewertet). Zusätzlich sendet jetzt auch das Reinreden (VAD-Barge-in), nicht nur der Button, den Interrupt an den Server. (`orchestrator/app/services/realtime_voice_session.py`, `orchestrator/app/services/voice_providers/realtime_nova_sonic.py`, `frontend/src/components/agents/voice-session.tsx`)

## [1.99.25] — 2026-07-03

### Changed
- **Voice-Settings sind jetzt realtime-first** und passen zum aktuellen Voice-Layer. Die Provider-Konfiguration zeigt oben die **Echtzeit-Sprachmodelle** (AWS Bedrock Nova Sonic / Azure Realtime — aus den konfigurierten AI-Accounts, via `GET /ai-accounts/realtime-models`) als primäre, empfohlene Auswahl und setzt damit den Plattform-Default (`voice_interaction_model` + `voice_interaction_account_id`). Die alte STT→LLM→TTS-Pipeline (faster-whisper/Edge-TTS/Interaction-LLM) ist in einen eingeklappten **„Klassische Pipeline (Fallback)"**-Bereich gewandert — nicht entfernt, weil Deployments ohne Realtime-Account (z. B. der Kundenanlage ohne AWS) sie als Rückfallebene brauchen; „Aktiv"-Badge zeigt, welcher Modus gerade greift. Backend: `/settings/voice` liefert + `PATCH /settings/` akzeptiert die Realtime-Felder. (`orchestrator/app/api/settings.py`, `orchestrator/app/schemas/settings.py`, `frontend/src/components/settings/voice-settings.tsx`)

## [1.99.24] — 2026-07-03

### Fixed
- **Chat-Sessions sind jetzt strikt isoliert — kein Bleed mehr zwischen Chats.** Der Agent publiziert alle Antworten auf EINEN Kanal (`agent:{id}:chat:response`), und die WS-Relay leitete bisher JEDES Event an den offenen Chat weiter (kein Session-Filter). Dadurch erschien der Live-Stream einer anderen Session / eines Hintergrund-Tasks / einer Voice-Delegation im gerade offenen Chat („neue Chats synchen sich mit dem aktuellen"). **Fix:** Der Orchestrator merkt sich pro Verbindung `message_id → session_id`, **taggt** jedes weitergeleitete Event mit seiner Session und **verwirft** Events, die zu keinem Chat dieser Verbindung gehören (fremde Session/Task/Voice). Das Frontend rendert nur noch Events der aktuell offenen Session. Jeder Chat-Tab ist damit eine eigene, isolierte Session. (`orchestrator/app/api/ws.py`, `frontend/src/components/agents/chat.tsx`)

## [1.99.23] — 2026-07-03

### Added
- **Parallele Chat-Sessions pro Agent.** Ein Agent-Container kann jetzt mehrere UNTERSCHIEDLICHE Chat-Sessions gleichzeitig abarbeiten (jede spawnt ihren eigenen claude/codex/custom-LLM-Turn) — dieselbe Session bleibt seriell/geordnet. Umgesetzt als Lane-Modell im `chat_consumer` (eine `asyncio.Queue` pro `source_key`, Concurrency via Semaphore; der Redis-Queue-Consumer bleibt einzig, daher keine rpop/rpush-Races). **Safe-by-default:** gesteuert über `MAX_PARALLEL_CHATS` (default **1** = exakt das bisherige serielle Verhalten, unveränderter Codepfad); erst `>1` aktiviert Parallelität. Jede Voice-Delegation nutzt jetzt eine eigene Session → mehrere per Sprache übergebene Aufgaben laufen parallel statt hintereinander. (`agent/app/chat_consumer.py`, `orchestrator/app/services/realtime_voice_session.py`, `orchestrator/app/core/agent_manager.py`, `config.py`, `docker-compose.yml`)

## [1.99.22] — 2026-07-03

### Added
- **Agenten-Webhooks sind jetzt OpenAPI-tauglich.** Neuer Endpoint `GET /webhooks/agents/{id}/openapi.json` liefert eine OpenAPI-3.1-Beschreibung des Agenten-Webhooks (die POST-Operation „send_to_agent" inkl. Bearer-Token-Security), sodass der Webhook direkt als **OpenAPI-Tool-Server** (z. B. in Open WebUI) eingebunden werden kann. Die Webhook-Endpoints senden jetzt **CORS-Header** (`Access-Control-Allow-Origin: *` — token-authentifiziert, ohne Cookies, daher sicher) inkl. Preflight (OPTIONS), womit die zuvor geblockten Cross-Origin-Fetches funktionieren. **Wichtig:** In OWUI die **HTTPS-Public-URL** eintragen (nicht die interne `http://…`-URL) — sonst blockt der Browser wegen Mixed-Content. (`orchestrator/app/api/webhooks.py`)

## [1.99.21] — 2026-07-03

### Added
- **Jarvis zeigt Bilder & Dateien.** Präsentiert der Agent während einer Voice-Aufgabe ein Bild (`present_image`) oder eine Datei (`present_file`), erscheint es jetzt live im rechten Panel des Jarvis-Cockpits — Bilder inline gerendert, Dateien als Karte mit Name/Beschriftung. Dieselben `image`/`file`-Events, die der Text-Chat rendert, werden über den `on_event`-Callback durchgereicht (`agent_chat_bridge`, `RealtimeVoiceSession._emit_activity` → `media`-Event). (`orchestrator/app/services/agent_chat_bridge.py`, `realtime_voice_session.py`, `frontend/src/components/agents/voice-session.tsx`)

### Security
- **Kiosk-Voice-Ticket gehärtet** (Regression aus 1.99.20 behoben, vom Security-Review gefunden). Der token-mintende Endpoint `POST /kiosk/ws-ticket/{id}` ist jetzt (a) **standardmäßig deaktiviert** — nur aktiv wenn `KIOSK_VOICE_ENABLED` gesetzt ist (Pi-Kiosk; auf Multi-Tenant-Boxen wie der Kundenanlage 404 → kein Token-Minting), und (b) **least-privilege**: das Ticket wird an den **Agent-Owner** gebunden statt an einen globalen Admin (Admin nur noch Bootstrap-Fallback für Owner-lose Agenten). (`orchestrator/app/api/kiosk.py`)

## [1.99.20] — 2026-07-03

### Added
- **Sprechen im Kiosk.** Der Pi-Kiosk hat pro Agent jetzt einen „Sprechen"-Button, der das Jarvis-Voice-Modal öffnet — reden statt tippen, direkt am 7"-Touchscreen. Da der Kiosk bewusst auth-frei ist (nur lokal am Gerät erreichbar), stellt ein neuer lokaler Endpoint `POST /kiosk/ws-ticket/{agent_id}` ein kurzlebiges WS-Ticket aus, das an eine Admin-Identität gebunden ist — damit passt das (in 1.99.19 ergänzte) Ownership-Gate der Voice-WS, ohne den Kiosk-Trust-Modell aufzuweichen (physischer Gerätezugriff = Kiosk-Zugriff). `VoiceSessionModal` nimmt dafür einen optionalen `getTicket`-Prop (der normale JWT-Flow der Web-App bleibt unverändert). (`orchestrator/app/api/kiosk.py`, `frontend/src/app/kiosk/page.tsx`, `frontend/src/components/agents/voice-session.tsx`)

## [1.99.19] — 2026-07-03

### Added
- **Einstellungen per Sprache.** Nova Sonic kann jetzt auf Zuruf **Autonomiestufe** (`set_autonomy`, l1–l4) und **Modell** (`set_agent_model`, z. B. „nimm Opus/Sonnet/Haiku") ändern. Beide Tools nutzen eine neue gemeinsame Service-Schicht `agent_settings.py` (change_agent_model/change_autonomy_level) mit voller AuthZ — dieselbe Logik, die jetzt auch die HTTP-Endpoints `PATCH /agents/{id}/model` und `POST /agents/{id}/autonomy-level` verwenden (Single Source of Truth). Harness-Wechsel (Claude↔Codex) bleibt bewusst UI-only. (`orchestrator/app/services/agent_settings.py`, `orchestrator/app/services/realtime_voice_session.py`, `orchestrator/app/api/agents.py`)

### Security
- **IDOR auf den Voice-/Chat-WebSockets geschlossen** (pre-existing, vom Security-Review gefunden). `/ws/agents/{id}/voice` und `/ws/agents/{id}/chat` prüften nur die Nutzer-Auth, aber nicht, ob der Nutzer **Zugriff auf DIESEN Agenten** hat — jeder Angemeldete konnte per fremder `agent_id` Aufgaben delegieren (`ask_agent`) und Daten (Status/Tasks/Budget) lesen. Jetzt Ownership-Gate via `require_agent_access` direkt beim Connect (Admin/Owner/AgentAccess erlaubt, sonst 4003). (`orchestrator/app/api/ws.py`)
- **Voice-Session-User-Auth korrigiert.** `ws_agent_voice` rekonstruierte die `user_id` aus `token=` (im Ticket-Flow immer leer → immer „unknown") statt aus der bereits verifizierten WS-Auth. Nutzt jetzt `websocket.state.user_id` — dadurch greifen die AuthZ-Checks der Sprach-Settings-Tools überhaupt erst. (`orchestrator/app/api/ws.py`)
- **Prompt-Injection-Härtung:** Ergebnisse delegierter Aufgaben werden Nova Sonic jetzt klar als DATEN (nicht als Befehl) übergeben — injizierter Fremdtext (z. B. aus einer gelesenen E-Mail) kann so keine Settings/Autonomie/Modell-Änderung auslösen. (`orchestrator/app/services/realtime_voice_session.py`)
- **Audit-Log für Modelländerungen** (Parität zu Autonomie-Änderungen). (`orchestrator/app/services/agent_settings.py`, `orchestrator/app/models/audit_log.py`)

## [1.99.18] — 2026-07-03

### Security
- **Exchange-Fehler leakt keine Interna mehr** (Regression aus 1.99.17 behoben). Der Client bekommt jetzt nur noch die Exception-**Klasse** (sichere Fehler-Kategorie wie `ErrorAccessDenied`/`ErrorImpersonateUserDenied`) — die Freitext-Message (kann Server-URLs, Mailbox-Adressen, Tenant-IDs enthalten) bleibt ausschließlich im Server-Log. (`orchestrator/app/core/exchange_mcp.py`)

## [1.99.17] — 2026-07-03

### Changed
- **Exchange-on-prem-Fehler ist jetzt diagnostizierbar.** Statt der generischen „Exchange request failed. Check the server connection / permissions." wird die echte Fehler-Kategorie (Exception-Klasse wie `ErrorAccessDenied` / `ErrorImpersonateUserDenied` / `ErrorNonExistentMailbox`) plus gekürzte Meldung zurückgegeben — genug, um die Ursache zu pinpointen (Impersonation-Rechte, Mailbox-Zugriff, EWS-Endpoint), ohne Server-/Tenant-Interna zu dumpen. Hintergrund: `ex_whoami` baut nur das Account-Objekt, `ex_list_emails` ist der erste echte EWS-Call — deshalb scheitert erst der. (`orchestrator/app/core/exchange_mcp.py`)

## [1.99.16] — 2026-07-03

### Fixed
- **Angepinnte Chats werden nicht mehr gelöscht.** `DELETE /agents/{id}/chat/sessions` (alle löschen) verschont jetzt angepinnte Sessions (Messages + Metadata bleiben); Einzel-Löschen eines angepinnten Chats wird mit 409 blockiert (erst Pin lösen). (`orchestrator/app/api/agents.py`)

## [1.99.15] — 2026-07-03

### Fixed
- **Websuche der Sprach-Front lieferte nichts.** DuckDuckGos HTML-Endpoint gibt Treffer nur bei **POST** zurück; der Code nutzte GET → 202-Landing-Page → 0 Ergebnisse → der Bot wimmelte ab. Auf POST umgestellt (verifiziert: liefert Treffer). (`orchestrator/app/core/web_search.py`)
- **Voice-Transkript zeigte nur den letzten Satz.** Nova Sonic sendet jeden Satz als eigenes Event; die Bubble überschrieb den vorherigen Satz. Jetzt wird der volle Text pro Turn akkumuliert (kumulativ ersetzen, neue Sätze anhängen, Duplikate überspringen). (`frontend/src/components/agents/voice-session.tsx`)

### Changed
- **`get_agent_activity` liefert jetzt Kontext, nicht nur Tool-Namen.** Zusätzlich zu den letzten Schritten werden **Ziel/Titel + Auftrag im Wortlaut + Ergebnis/Fehler** der aktuellen bzw. letzten Aufgabe aus der DB mitgegeben — die Sprach-Front kann echte Zusammenfassungen geben statt „das Ziel ist nicht verfügbar". (`orchestrator/app/services/realtime_voice_session.py`)
- **Sprach-Front spricht konsequent in der ICH-Form.** System-Prompt, Delegations-Quittung und Ergebnis-Rückmeldung reframed: Nova Sonic IST der Bot, spricht nie von „dem Agenten" oder „weitergeben" — für den Nutzer erledigt „ich" alles. UI-Label „Ich kümmere mich um …". (`orchestrator/app/services/realtime_voice_session.py`, `voice-session.tsx`)

### Added
- **Proaktive Begrüßung.** Sobald das Gespräch startet (erstes Audio-Frame erreicht Nova Sonic), begrüßt der Bot aktiv von sich aus in der ICH-Form, statt stumm zu warten. (`orchestrator/app/services/realtime_voice_session.py`)

## [1.99.14] — 2026-07-03

### Added
- **Jarvis-Redesign der Realtime-Sprach-Front.** Das Live-Gespräch (Nova Sonic) ist jetzt ein breites 3-Spalten-Cockpit: **links** der laufende Gesprächsverlauf (Sprechblasen User/Agent), **Mitte** eine animierte „Präsenz" (`JarvisCore` — reagiert farblich/animiert auf Zuhören/Sprechen/Denken), **rechts** der Aufgaben-/Aktivitäts-Bereich (Live-Tool-Schritte des delegierten Agenten + Web-Ergebnis-Karten). Pure Tailwind, CSP-safe, responsive (stapelt auf schmalen Screens). Klassischer Push-to-Talk-Modus unverändert. (`frontend/src/components/agents/jarvis-core.tsx`, `frontend/src/components/agents/voice-session.tsx`)
- **Websuche direkt im Interaction Layer.** Nova Sonic hat ein neues `web_search`-Tool (DuckDuckGo, **kein API-Key** → läuft auf jedem Deployment) und beantwortet Wissensfragen sofort selbst, ohne den Agenten zu bemühen. Ergebnisse werden gesprochen zusammengefasst UND als `web_results`-Event an die UI (Karten mit Titel/Link/Snippet) gegeben. Der frühere „Brave"-Provider war nur ein Config-Stub. (`orchestrator/app/core/web_search.py`, `orchestrator/app/services/realtime_voice_session.py`)
- **`get_agent_activity`-Tool für die Sprach-Front.** Nova Sonic kann jetzt aktiv abfragen, was der Agent GERADE tut (laufende Aufgabe + letzte konkrete Schritte aus dem Live-Feed `agent:{id}:activity`/`:status`) und es dem Nutzer erzählen — schnelles Direkt-Daten-Tool, kein Agent-Round-Trip. (`orchestrator/app/services/realtime_voice_session.py`)

### Fixed
- **Notifications-WebSocket brach ab** („The network connection was lost"). Die `/ws/notifications`-Route sendete keinen Keepalive → Cloudflare/Caddy kappte die idle-Verbindung nach ~100 s. Jetzt periodischer Ping (~27 s). (`orchestrator/app/api/ws.py`)

## [1.99.13] — 2026-07-03

### Fixed
- **Skill-Installation aus dem Katalog schlug mit 422 fehl** (`Field required: body.skill_id`). Der Frontend-Call `assignDbSkill` schickte nur `{agent_id}` an `POST /skills/marketplace/{skill_id}/assign`, das Pydantic-Modell `SkillAssign` verlangte aber zusätzlich ein Pflichtfeld `skill_id` im Body — obwohl der Handler die `skill_id` ausschließlich aus dem URL-Pfad nimmt und das Body-Feld nie liest. **Fix:** `SkillAssign.skill_id` ist jetzt optional (Pfad ist die Quelle der Wahrheit; ein fehlendes Body-`skill_id` darf die Installation nicht mehr blockieren), und `assignDbSkill` sendet die `skill_id` zusätzlich konsistent mit. (`orchestrator/app/api/skill_marketplace.py`, `frontend/src/lib/api.ts`)

## [1.99.12] — 2026-07-03

### Fixed
- **Barge-in leert jetzt die GANZE Audio-Queue.** Beim Unterbrechen stoppte `flushPlayback()` zwar die aktuell eingeplanten Audio-Nodes, aber Nova Sonic streamt nach dem Cut-in noch kurz weiter — die nachkommenden `audio_chunk`-Events des unterbrochenen Turns wurden neu eingeplant und liefen weiter. Neu: `beginBargeIn()` stoppt alle Nodes **und** setzt `suppressAudioRef` — eingehendes Audio des unterbrochenen Turns wird ab dann verworfen. Die Unterdrückung endet automatisch beim nächsten User-Transkript (= neuer Turn) oder nach 1,5 s Sicherheits-Timer. (`frontend/src/components/agents/voice-session.tsx`)

### Added
- **Live-Status/Activity-Log im Voice-Gespräch, während der Agent an einer delegierten Aufgabe arbeitet.** Sobald der Voice-Agent per `ask_agent` eine Aufgabe an seinen Container-Agenten übergibt, zeigt das Voice-Modal in Echtzeit, was der Agent tut (Tool-Aufrufe + Text) — dieselben `tool_call`/`text`-Events, die auch der Text-Chat und das LiveTerminal rendern, **kein neuer Mechanismus**. `ask_agent_via_chat()` bekam einen optionalen `on_event`-Callback (rückwärtskompatibel), `RealtimeVoiceSession._emit_activity` reicht die Events als `activity`-Events an die Voice-UI weiter. Panel zeigt „Agent arbeitet an der Aufgabe" (Spinner) und nach dem Report „Aufgabe erledigt". (`orchestrator/app/services/agent_chat_bridge.py`, `orchestrator/app/services/realtime_voice_session.py`, `frontend/src/components/agents/voice-session.tsx`)

## [1.99.11] — 2026-07-03

### Security
- **AuthZ auf den neuen AI-Account-Realtime-Endpoints (2× HIGH, aus 1.99.8).** Ein automatischer Security-Review fand: (a) **IDOR** — `PUT /agents/{id}/interaction-model` verknüpfte eine beliebige `interaction_account_id` ohne Zugriffsprüfung → ein Nutzer hätte einen fremden AI-Account (fremde Cloud-Creds) an seinen Agenten hängen können. (b) **Info-Disclosure** — `GET /ai-accounts/realtime-models` listete ALLE aktiven Accounts ungefiltert. **Fix:** beide gaten jetzt über das bestehende Allowlist-Modell (`get_effective_permissions().ai_account_ids`, Admin = alle) wie `list_ai_accounts`; das Link-Endpoint lehnt nicht-zugängliche/aktive Accounts mit 403 ab. Defense-in-depth: `RealtimeVoiceSession` prüft beim Session-Start erneut, ob der Session-Nutzer den verknüpften Account nutzen darf (sonst env-Fallback). (`orchestrator/app/api/agents.py`, `orchestrator/app/api/ai_accounts.py`, `orchestrator/app/services/realtime_voice_session.py`)

## [1.99.10] — 2026-07-03

### Fixed
- **Embedding-Cloud-Fallback (OpenAI) tatsächlich implementiert (löst #287).** Der dokumentierte „local → OpenAI"-Fallback war nur ein Stub (`return None` mit Kommentar „would require dim conversion"). Jetzt ruft er bei nicht erreichbarem lokalem bge-m3 **OpenAI `text-embedding-3-small` mit `dimensions=1024`** auf — passt exakt in die bestehende pgvector-Spalte. Damit funktioniert semantische Suche auf dem Pi (wo bge-m3 den Kühler kocht) **ohne lokale Last**, sobald ein OpenAI-Key gesetzt ist. Betrifft `embed()` + `embed_batch()`. (`orchestrator/app/services/embedding_service.py`)

## [1.99.9] — 2026-07-03

### Added
- **Realtime-Voice: Async-Delegation mit proaktivem Rückmelden.** Delegiert Nova Sonic eine echte Aufgabe an den Agenten (langsam), blockiert es nicht mehr: es **quittiert sofort** („ich habe nachgefragt, ich melde mich"), der Nutzer kann weiterreden, und sobald die Agenten-Antwort da ist, **spricht Nova Sonic sie von selbst aus** — über eine Turn-Injection (`NovaSonicSession.inject_user_text`). (`orchestrator/app/services/realtime_voice_session.py`)

## [1.99.8] — 2026-07-03

### Added
- **Realtime-Sprache über AI-Accounts konfigurierbar (kundenfähig).** AWS-Bedrock-Zugänge (und vorbereitend Azure-Realtime / Brave-Websearch) werden jetzt als **AI-Account** angelegt (verschlüsselte Creds, wiederverwendbar) statt per Server-`.env` hardcodiert. Damit kann jeder Kunde (z. B. der Kundenanlage) seinen eigenen AWS-Account eintragen und Nova Sonic nutzen.
  - AI-Accounts: neue Provider-Typen `bedrock` / `azure-realtime` / `brave-search`; Formular mit AWS Access Key ID + Region + Secret (`frontend/src/app/ai-accounts/view.tsx`, `orchestrator/app/api/ai_accounts.py`).
  - **Realtime-Modell-Selektor** im Agenten-Sprach-Setup: listet die verfügbaren Realtime-Modelle je konfiguriertem Provider (z. B. „Nova Sonic 2 · AWS Bedrock (Pi)"), Auswahl Modell ↔ Provider. Endpoint `GET /ai-accounts/realtime-models`; Katalog `orchestrator/app/core/realtime_catalog.py`.
  - `RealtimeVoiceSession` löst die Creds jetzt auf: **verknüpfter AI-Account → Plattform-Default-Account → env** (Pi-Bootstrap bleibt als Fallback). Modell-ID pro Agent wählbar. Config: `interaction_account_id` + `interaction_model_id`.

## [1.99.7] — 2026-07-03

### Added
- **Realtime-Voice: schnelle Direkt-Daten-Tools + Barge-in + Sprech-Füller.** Nova Sonic muss nicht mehr für jede Frage den (langsamen) Agenten anschreiben:
  - **Direkt-Tools (Millisekunden, kein Agent-Round-Trip):** `get_agent_status` (läuft/idle, aktuelle Aufgabe, Queue), `list_agent_tasks` (letzte Aufgaben inkl. Fehlerursache), `get_agent_settings` (Modell/Modus/Provider/Autonomie/Budget) — lesen direkt aus DB/Redis. Nur echte **Arbeit** geht noch über `ask_agent`.
  - **Sprech-Füller:** Vor einer Delegation (`ask_agent`, dauert Sekunden) sagt Nova Sonic jetzt kurz etwas („Moment, ich kümmere mich darum"), damit keine Stille entsteht.
  - **Barge-in:** Redet der Nutzer, während der Agent spricht, stoppt die Audio-Ausgabe sofort (Energie-VAD im Browser) — plus „Unterbrechen"-Button. (`orchestrator/app/services/realtime_voice_session.py`, `frontend/src/components/agents/voice-session.tsx`)
- **Plattform-Default-Interaktionsmodell.** Neuer Fallback: Agenten ohne eigene Einstellung folgen einer Plattform-Vorgabe (`voice_interaction_model`), sodass **alle Agenten einheitlich** dasselbe Sprach-Verhalten haben — auf dem Pi „nova_sonic", auf der Kundenanlage leer (klassisch). Ein Per-Agent-Wert überschreibt weiterhin. (`orchestrator/app/api/ws.py`)

## [1.99.6] — 2026-07-03

### Fixed
- **Realtime-Voice (Nova Sonic): Session-Start scheiterte mit `'Agent' object has no attribute 'role'`.** `RealtimeVoiceSession.init` las die Agenten-Rolle über `agent.role` — die existiert auf dem ORM-Modell nicht (Rolle liegt in `config["role"]`). Jetzt aus `config` gelesen. (`orchestrator/app/services/realtime_voice_session.py`)

## [1.99.5] — 2026-07-03

### Added
- **Realtime-Sprach-Interaktion pro Agent (AWS Bedrock Nova Sonic 2).** Neuer Speech-to-Speech-Front pro Agent als Alternative zur klassischen Aufnehmen→STT→LLM→TTS-Pipeline: Nova Sonic (`amazon.nova-2-sonic-v1:0`) hört durchgehend zu, spricht natürlich in Echtzeit und **delegiert echte Aufgaben über ein `ask_agent`-Tool an genau seinen Agenten-Container** — über denselben Chat-Kanal (`agent:{id}:chat`), den auch der Text-Chat nutzt (keine Insellösung). Das schwere Modell läuft in der AWS-Cloud → **null Last auf dem Gerät** (ideal für den Pi).
  - Backend: `orchestrator/app/services/voice_providers/realtime_nova_sonic.py` (bidirektionaler Bedrock-Stream + Tool-Use via `aws-sdk-bedrock-runtime`), `realtime_voice_session.py` (Browser-PCM ↔ Nova Sonic ↔ Agent), gemeinsamer Delegations-Helper `agent_chat_bridge.py` (auch von der klassischen `VoiceSession` genutzt). WS-Route wählt den Pfad per `agent.config["interaction_model"]`. Endpoint `PUT /agents/{id}/interaction-model`.
  - Frontend: kontinuierlicher 16-kHz-PCM-Aufnahme-/24-kHz-Wiedergabe-Modus im Voice-Modal (`voice-session.tsx`), Per-Agent-Selektor „Sprach-Interaktion" (`interaction-model-card.tsx`).
  - Verifiziert: echte deutsche Sprache → Transkription → `ask_agent`-Tool-Call → Tool-Ergebnis → gesprochene Antwort, end-to-end gegen echtes AWS Bedrock (Raspberry Pi, ARM). Der Browser-Mic-Test steht noch aus.
  - AWS-Zugangsdaten sind **Pi-only** (in der Pi-`.env`), nicht auf der Kundenanlage.

## [1.99.4] — 2026-07-02

> Security-Hotfix (Orchestrator + Frontend). Version über alle Artefakte vereinheitlicht — **git-Tag = `VERSION` = Dockerfile-Label = Agent-Image = 1.99.4** (Agent-Image inhaltsgleich zu 1.99.3, nur neu gelabelt), damit die im Header angezeigte Software-Version dem Release entspricht.

### Security
- **CRITICAL: Autonomie-Matrix-Feintuning hebelte die harte Tool-Whitelist aus (Fail-Open, Broken Access Control).** Sobald im 3-Status-Matrix-Editor **eine einzige Zelle** vom Preset abwich, wurde `autonomy_level = "custom"`. Für „custom" gab es keine `ApprovalRule`-Zeilen und kein Preset → `get_active_rules_for_agent` lieferte eine **leere** Liste → der Tool-Executor wertet „keine Regeln" als „keine Einschränkung" (Fail-Open) → der Agent hatte ab da **uneingeschränkten** bash-/Datei-/Messaging-Zugriff im Container, ohne Rückfrage, während die UI weiter das (nicht mehr wirksame) Level anzeigte. Genau beim Härten fiel die Sperre weg. **Fix:** Für Nicht-Preset-Level wird die Whitelist jetzt aus der Matrix abgeleitet (`allow` → Kategorie erlaubt; `ask`/`deny` → hart geblockt); fehlende Matrix → **fail-closed auf L1** statt leer. Neuer `allowed_categories_from_matrix()`. `autonomy_level` ist im Schema jetzt ein `Literal["l1".."l4","custom"]` (blockiert den Direkt-Injection-Weg über `POST /agents`). 10 neue Tests (6 pur + 4 Integration). (`orchestrator/app/api/approval_rules.py`, `orchestrator/app/core/autonomy_matrix.py`, `orchestrator/app/schemas/agent.py`)
- **MEDIUM: `GET /approval-rules/for-agent/{id}` war unauthentifiziert** und gab Matrix + vollständigen `autonomy_prompt` preis (Aufklärung für gezielte Prompt-Injection: verrät, ob ein Agent ohne Rückfrage handelt). Jetzt gegen das ohnehin mitgesendete `X-Agent-Token` geprüft (`hmac.compare_digest` vs `make_agent_token`), non-breaking. (`orchestrator/app/api/approval_rules.py`)

### Fixed
- **Chat-Kacheln „zu breit": lange Titel liefen in die Nachbar-Kachel.** Klassischer Flexbox-Truncate-Bug — fehlende `min-w-0`-Kette (Grid-Item + Flex-Zeile) verhinderte das Abschneiden. Kacheln bekommen `min-w-0 overflow-hidden`, der Titel `min-w-0 flex-1 truncate`, Vorschau `break-words`. (`frontend/src/components/agents/chat-overview.tsx`)

## [1.99.3] — 2026-07-02

### Fixed
- **Second-Brain-Graph: kein schwarzer Crash mehr bei fehlendem/verlorenem WebGL.** Der Vault-Graph rendert über `react-force-graph-3d` (three.js/WebGL). In abgeschotteten Umgebungen (Klinik-VDI, GPU-gesperrter Browser) sowie nach wiederholtem Öffnen/Schließen (three.js gibt den WebGL-Context nicht sauber frei → Browser erschöpft sein Context-Budget) crashte die Render-Schleife mit `Cannot read properties of undefined (reading 'tick')` auf schwarzem Canvas. Neu: **WebGL-Probe vor dem Mount**, **Laufzeit-`webglcontextlost`-Handler** (live-Umschaltung) und **`pauseAnimation()` beim Unmount** (gibt den Context früher frei). Fällt sauber auf einen **dependency-freien 2D-SVG-Graphen** zurück (gleiche Klick-/Detail-Logik, Pan/Zoom, Nachbar-Highlight, Hinweis „· 2D-Ansicht"). (`frontend/src/app/second-brains/vault-graph-3d.tsx`)
- **Custom-LLM-Harness: `temperature` bei temperatur-gesperrten Modellen weglassen.** Beim Wechsel von z. B. `gpt-5.4` auf ein `gpt-chat-latest`-Deployment schickte der Provider weiter `temperature=0.7` → **HTTP 400** („temperature does not support 0.7 … only default (1)"). Neuer `_supports_custom_temperature()` erkennt Responses-Modelle (GPT-5/codex), die o-Serie (o1/o3/o4) und die `*-chat-latest`-Aliasse und lässt `temperature` dann weg; zusätzlich ein rekursiver 400-Retry als Netz für sonstige gesperrte Modelle. 19 Tests. (`agent/app/providers/openai_provider.py`, `agent/tests/test_openai_temperature.py`)

## [1.99.1] — 2026-07-02

### Added
- **M365/OneDrive: `ms_copy_item`-Tool (Datei/Ordner kopieren).** Bisher gab es nur `ms_move_item` (verschieben) — ein Agent, der eine Datei KOPIEREN sollte, improvisierte einen rohen Graph-`/copy`-Aufruf und bekam **HTTP 400** (Graph-Copy ist asynchron und braucht eine `parentReference` mit `driveId`+Ordner-`id`, keinen reinen Pfad). Das neue Tool löst Ziel-`driveId` + Ordner-`id` sauber auf, sendet den korrekten Copy-Request und behandelt die 202-Async-Antwort. (`orchestrator/app/core/msgraph_mcp.py`)

## [1.99.0] — 2026-07-02

### Added
- **Autonomie-Matrix (3-stufig) mit Presets.** Neue Fähigkeits-Matrix pro Agent: jede Fähigkeit ist **Erlaubt / Freigabe / Verboten**, gruppiert in **Eigener Container** (Dateien lesen/schreiben, Shell, Pakete) und **Externe Tools** (Web, E-Mail/M365, externe API, Chat/Telegram, Git-Push, Käufe). Die **L1–L4-Buttons füllen die Matrix** als Vorlage; danach ist jede Zelle einzeln justierbar (→ „Custom"). Single-Source `orchestrator/app/core/autonomy_matrix.py` (Taxonomie + Presets + Prompt-Rendering); Endpoints `GET/PUT /agents/{id}/autonomy-matrix`, und `POST /autonomy-level` füllt die Matrix mit. Der `for-agent`-Endpoint liefert die Matrix + einen fertig gerenderten, autoritativen `autonomy_prompt` (Erlaubt→ohne Nachfrage, Freigabe→`request_approval`, Verboten→ablehnen; Vollautonomie=L4=harter No-Ask-Block). Frontend: Matrix-Editor im Agenten-Autonomie-Tab. 8 Tests. Enthält den L4-Fix aus 1.98.1 als Vollautonomie-Fall.

## [1.98.2] — 2026-07-02

### Fixed
- **M365-Tools „mal da / mal nicht" behoben** (custom_llm/Azure-Agenten). Wegen des 128-Tool-Limits sendet der Agent nur ein CORE-Set + `search_tools`; M365/Exchange-Tools waren nur per `search_tools` erreichbar, und das Modell behauptete unzuverlässig „kein M365-Tool verfügbar" statt zu suchen. Fix: die Integrations-MCP-Tools (`mcp_msgraph_*`, `mcp_exchange_*`, …) werden beim Katalog-Laden **vor-aktiviert** (mit Headroom unter dem Limit) — in Chat- UND Task-Pfad. M365 ist damit zuverlässig sofort aufrufbar.

## [1.98.1] — 2026-07-02

### Fixed
- **L4-Agenten fragen nicht mehr trotzdem nach Freigabe** (z. B. bei M365/OneDrive). Ursache: der `for-agent`-Whitelist-Endpoint lieferte kein Autonomie-Level, und der Agent-Prompt hängte bedingungslos „when in doubt, always ask" an — das übersteuerte das L4-„Alles erlaubt". Fix (Autonomie-Matrix Stufe 1): `GET /approval-rules/for-agent/{id}` liefert jetzt `autonomy_level` + `unrestricted`; bei L4 injiziert der Agent einen **harten No-Ask-Block** („You are FULLY AUTONOMOUS … do NOT call request_approval") statt der Whitelist. Die generische „ALWAYS ask before external"-Zeile in der Agent-CLAUDE.md deferiert jetzt auf diesen autoritativen Autonomie-Block.

## [1.98.0] — 2026-07-02

### Added
- **Chat-Konsole UX (Teil 2) — Kachel-Übersicht + Live-Modal.** Neuer Umschalter (Kachel-Icon) in der Chat-Leiste zeigt alle Chats des Agenten als **Kacheln** (Titel/Preview, Nachrichten-Anzahl, letzte Aktivität, Pin). Klick auf eine Kachel öffnet ein **Modal mit dem Verlauf**, das sich alle 4s aktualisiert (Live) — plus „Im Chat öffnen", das direkt in die volle Chat-Ansicht dieser Session springt. Neue gekapselte Komponente `chat-overview.tsx`, nutzt die vorhandenen Session-/History-APIs (kein neues Backend).

## [1.97.0] — 2026-07-02

### Added
- **Chat-Konsole UX (Teil 1):** Der „Neuer Chat"-Button sitzt jetzt **links** und ist als gefüllter Primary-Button klar sichtbar. Chats lassen sich **umbenennen** (Doppelklick auf den Tab oder Stift-Icon) und **anpinnen** (Pin-Icon; angepinnte Chats stehen vorn). Neben dem einzelnen Löschen gibt es **„Alle Chats löschen"** (mit Bestätigung). Neue **Schriftgrößen-Steuerung** (A−/A+, persistiert in localStorage, skaliert den Verlauf per `zoom`). **Drag & Drop** von Dateien direkt in den Chat-Verlauf lädt sie nach `/workspace` hoch (mit Drop-Overlay). Backend: neues `ChatSession`-Metadaten-Modell (title/pinned, lazy angelegt) + Endpoints `PATCH /agents/{id}/chat/sessions/{session_id}` (rename/pin) und `DELETE /agents/{id}/chat/sessions` (alle löschen); die Session-Liste liefert nun `title` + `pinned` und sortiert angepinnte zuerst.

## [1.96.2] — 2026-07-02

### Changed
- **Coding- & Security-Disziplin in den Agenten-CLAUDE.md** (`agent/claude-global.md`): neuer Pflicht-Abschnitt für Agenten, die Code schreiben/ändern — „erst sichten, dann dübeln" (Code/Memory/Brain lesen bevor gebaut wird), **keine Insellösungen** (verzahnen statt parallel implementieren), **Secure Coding** (Input serverseitig validieren, Pfade jailen/kein Path-Traversal, keine ungeprüften Pub-Sub-/Webhook-Routing-Felder, parametrisiertes SQL, AuthZ+Ownership pro Endpoint, keine Secrets), **Verification-Loop + Security-Test pro Route**, und **Security-Selbstreview des Diffs VOR jedem Merge** (grüner Build allein genügt nicht). Zusätzlich im geteilten `SELF_IMPROVEMENT_SUFFIX` (jede Task) als Merge-Gate verankert. Adressiert direkt die Klasse von Regressionen aus #237 (ungeprüftes Pub-Sub-Payload) und #271 (Path-Traversal).

## [1.96.1] — 2026-07-02

### Security
- **Telegram-Notification-Spoofing behoben** (`telegram/bot.py`): Der Redis-Listener `telegram:notification` übernahm die `chat_id` **ungeprüft aus dem Payload**. Da jeder Komponente mit Redis-Zugriff (inkl. Agenten) dorthin publishen kann, hätte ein Agent Nachrichten an beliebige dem Bot bekannte Chats spoofen können (z. B. gefälschte „Freigabe erteilt"-Meldung an den Operator). Fix: `chat_id` wird nie mehr aus dem Payload übernommen — immer der konfigurierte Operator-Chat. (Regression aus PR #237.)
- **present_file: Arbitrary-File-Read/Workspace-Scope-Bypass behoben** (`agent/app/agent_runner.py`): `_deliver_present_file_via_telegram` öffnete den vom Agenten gelieferten Pfad ohne Jailing und schickte ihn per Telegram raus — ein Agent hätte gemountete fremde Brain-Vaults, `/shared` oder Container-Secrets an der Freigabe vorbei exfiltrieren können. Fix: Pfad wird realpath-jailed auf `/workspace`; alles außerhalb wird abgelehnt. (Regression aus PR #271.)

### Changed
- Agent-CLAUDE.md geschärft: Second-Brain-Vaults sind rw unter `/mnt/brains/<slug>` gemountet — Agenten schreiben Artikel mit ihrem normalen Write-Tool direkt dorthin (kein Extra-Tool nötig).

## [1.96.0] — 2026-07-02

### Added
- **Second-Brain-MCP kann jetzt schreiben.** Der per-Brain MCP-Server (`brain_mcp.py`) hatte nur `brain_search`/`brain_read`/`brain_list` (read-only) — Agenten (z. B. via OpenWebUI) konnten nichts ins Second Brain schreiben. Neu: **`brain_write`** (Markdown-Notiz anlegen/aktualisieren, Ordner werden erzeugt, atomar), **`brain_tree`** (Ordner-/Datei-Struktur als eingerückter Baum) und **`brain_delete`**. Schreiben/Löschen sind an `default_mode == "rw"` des Brains gebunden (read-only Brains lehnen ab). Alles über `vault.resolve_path` gesandboxt: kein Path-Escape, kein `.git`, nur `.md/.markdown/.txt`, 2-MB-Cap. Neue Helfer `vault.write_file/delete_file/tree_text` + 9 Sicherheits-Regressionstests (`test_vault_write.py`).

## [1.95.1] — 2026-07-01

### Added
- **Plattform-Fehler-Log für Agenten (`/shared/platform-errors.log`).** Der Orchestrator spiegelt seine WARNING/ERROR-Logs (secret-redacted, rotierend) in eine Datei auf dem bereits geteilten Volume `ai-employee-shared`, das in Orchestrator **und** jedem Agenten unter `/shared` gemountet ist. Agenten lesen Plattform-Fehler damit mit ihren normalen Datei-Tools — **ohne Docker-Socket, ohne neuen Endpoint**. Ergänzt das `read_logs`-Tool (eigene Container-Logs) um die Plattform-Sicht für „an der Plattform selbst arbeiten". Das Agenten-CLAUDE.md weist auf die Datei + `read_logs` hin. (`orchestrator/app/core/platform_error_log.py`)

## [1.95.0] — 2026-07-01

### Added
- **Provider-abhängiger Modell-Guard.** Ein Agent kann nur noch Modelle seiner eigenen Harness bekommen: `claude_code` ⇒ ausschließlich Claude-Modelle, `codex_cli` ⇒ ausschließlich GPT/o-Serie, `custom_llm` bleibt frei (Account/Config). Behebt „the claude model is not supported with a ChatGPT account" systemisch. Neue Single-Source-of-Truth `orchestrator/app/core/model_catalog.py` (ersetzt drei divergierende, hartkodierte Frontend-Listen) + neuer `GET /agents/models`. Gates an allen Eintrittspunkten: `POST /agents` (422), `PATCH /agents/{id}/model` (422), `AgentManager` Create + beide Recreate-Pfade (Last-Line-Coerce — fängt auch einen falschen `DEFAULT_MODEL`), WS-Chat-Override (droppt inkompatibles Per-Message-Modell). Der Modell-Selektor in den Agent-Settings funktioniert jetzt auch für **Codex-Agenten** (vorher nur Claude) und zieht die Liste data-driven aus dem Katalog.
- **`read_logs` MCP-Tool (Agent-Self-Improvement).** Agenten können ihre eigenen Container-Logs lesen, um Fehler selbst zu diagnostizieren (401, Stacktrace, fehlende Env) und daraus Issues/PRs zu machen. Sauber verzahnt statt roher Docker-Socket: der Orchestrator ist die einzige Instanz mit Docker-Zugriff. Neuer `GET /agents/logs` (`verify_agent_token`): eigene Logs immer, ein Team-Lead zusätzlich die seiner Team-Mitglieder, sonst 403. Secret-Redaction (Bearer/JWT/`sk-`/`gh_`/AWS/`KEY=VALUE`/PEM, fail-closed) + Audit (`AuditEventType.LOGS_READ`) + `tail`-Cap 1000. MCP-Server in beiden Runnern (Codex + Claude) registriert.
- **Agent-Network-View Phase 3:** Nachrichten zwischen verschiedenen Teams, an denen ein Lead beteiligt ist, werden in Emerald mit Kronen-Marker hervorgehoben; neue Kanten-Legende (Nachrichten / delegierte Tasks / Cross-Team-Lead), die nur vorhandene Kantentypen einblendet.

### Security
- Container-Logs werden vor Herausgabe an Agenten secret-redacted (`orchestrator/app/core/log_redaction.py`, 7 Regressionstests). Jeder Log-Zugriff wird auditiert und ist auf das eigene Team gescoped.

## [1.94.0] — 2026-07-01

### Added
- **Lokales Kiosk-Dashboard „AI Employee · Mission Control"** für ein On-Device-Display auf dem Raspberry Pi (7" / 1024×600). Neue Seite `/kiosk` (Frontend) + no-auth Kiosk-API (`/api/v1/kiosk/*`). Zeigt live: Agenten (Status + aktueller Task), Task-Übersicht (läuft/wartet/heute fertig) + Aktivitäts-Feed, AI-Kosten heute, Pi-Auslastung (CPU/RAM/Disk/Temp/Load/Uptime) und **echte Leistungsaufnahme** vom Pi-5-PMIC + Stromkosten (Tarif via `ELECTRICITY_PRICE_EUR_KWH`, Default 0,35 €/kWh). Agenten-**Chat per Touch**; **Energiesparmodus** (Screensaver bei Inaktivität + reduziertes Polling, Display-Aus via `swayidle`/`wlopm`). (`frontend/src/app/kiosk/`, `orchestrator/app/api/kiosk.py`)
- **Host-Metrik-Collector** (`scripts/kiosk-power-collector.sh` + systemd `kiosk-power.service`): liest die realen Rail-Ströme/Spannungen des Pi-5-PMIC (`vcgencmd pmic_read_adc`) → Wattzahl, dazu Temp/CPU/RAM/Disk/Uptime und akkumulierte Tagesenergie; schreibt JSON, read-only in den Orchestrator gemountet.

### Security
- **Kiosk ist strikt lokal:** Caddy liefert für `/kiosk` und `/api/v1/kiosk*` **404**, wenn die Anfrage über den Cloudflare-Tunnel kommt (erkennbar am `Cf-Ray`-Header); nur Anfragen vom Gerät selbst werden bedient. Die Seite selbst ohne Auth (bewusst, weil nur lokal erreichbar).

### Fixed
- **Codex-Agenten: `401 Invalid/Missing agent token` behoben.** Der Codex-Runner schrieb in den generierten MCP-`[env]`-Block nur `AGENT_TOKEN`/`ORCHESTRATOR_URL`, aber **nicht `AGENT_ID`**. Da Codex den Container-Env nicht an die MCP-Server vererbt, fiel `AGENT_ID` in den `.mjs`-Servern auf `"unknown"` zurück → HMAC-Token passte nicht → jeder Agent-Tool-Call (Brain/Memory/Skills/Todos) 401. Jetzt `AGENT_ID` (plus `AGENT_NAME`/`DEFAULT_MODEL` für den orchestrator-Server) explizit im env-Block. Betraf nur Codex; der Claude-Pfad war korrekt. (`agent/app/codex_runner.py`)

## [1.89.0] — 2026-06-30

### Fixed
- **Meeting-Agenten führen ihre zugewiesenen Aufgaben jetzt WIRKLICH aus** (vorher: Task lief, aber der Agent lehnte ab/tat nichts). Mehrere zusammenhängende Ursachen behoben:
  - **Leere Autonomie-Whitelist trotz l3-Default:** `get_active_rules_for_agent` lieferte nur materialisierte Regeln; Agenten mit Default-Level l3 (Regeln nie materialisiert) bekamen eine LEERE Whitelist → „immer Approval vor Schreiben" → Ablehnung. Jetzt Fallback: Whitelist wird aus dem Autonomie-Level-Preset abgeleitet, wenn keine agent-spezifischen Regeln existieren. (`api/approval_rules.py`)
  - **TODOs für den Agenten unsichtbar:** Orchestrator legte Meeting-TODOs mit `project=NULL` an, der Agent liest `list_todos` aus `project='workspace/general'` → 0 gefunden. TODOs werden jetzt im richtigen Projekt angelegt. (`api/meeting_rooms.py`)
  - **TODO-Abschluss automatisch:** verknüpfte TODOs werden auf erledigt gesetzt, sobald der [Meeting]-Task des Agenten fertig ist — unabhängig davon, ob der Agent `complete_todo` (ggf. lazy-loaded) aufruft. (`core/task_router.py`)
  - **Task-Prompt:** explizite Autonomie-Freigabe für die zugewiesene Eigenarbeit (Workspace/knowledge schreiben + Recherche, extern weiter approval-pflichtig), Onboarding-Status irrelevant, keine Spezial-Tools nötig — nur Punkte abarbeiten + in `knowledge.md` dokumentieren. (`api/meeting_rooms.py`)

### Changed
- **Event-basierter Folgetermin keyt jetzt auf Task-Abschluss** (Agenten erledigen Tasks zuverlässig; TODO-Häkchen nicht immer) statt auf TODO-Status. (`services/scheduler_service.py`)
- Synthese-Prompt: ungenutzten `FOLLOWUP_DATE`-Marker entfernt (Folgetermin ist event-basiert).

## [1.88.0] — 2026-06-30

### Changed
- **Folgetermin ist jetzt EVENT-BASIERT statt LLM-Kalender-Schätzung.** Der Folge-Raum startet automatisch, sobald **alle Action-Item-TODOs des Vortermins erledigt** sind (die Agenten bringen fertige Ergebnisse mit) — mit 24-Stunden-Sicherheits-Cap. Vorher schätzte das LLM ein Kalenderdatum (oft Wochen, weil es in Menschen-Projektzeit rechnet, nicht im Agent-Tempo → z. B. „14.07."). DB: `meeting_rooms.parent_room_id` (Migration `b2c3d4e5f6a7`); der Scheduler prüft den TODO-Abschluss des Vortermins. (`orchestrator/app/api/meeting_rooms.py`, `services/scheduler_service.py`, `frontend/src/app/meeting-rooms/page.tsx`)

## [1.87.3] — 2026-06-30

### Fixed
- **Meetings können nicht mehr durch einen trägen/überlasteten Agenten blockieren.** Der Per-Turn-Timeout war 5 Min — ein nicht-antwortender Teilnehmer ließ das Meeting faktisch stillstehen. Jetzt **90 s pro Turn** (danach Platzhalter + weiter); Synthese-Waits ebenfalls gebound (Moderator 120 s, Teilnehmer-Fallback 120 s). Meetings laufen damit zuverlässig bis zum Abschluss. (`orchestrator/app/api/meeting_rooms.py`)

## [1.87.2] — 2026-06-30

### Fixed
- **Folgetermin-Datum greift jetzt zuverlässig (Agenten-Vorschlag statt +7-Tage-Fallback).** Die Synthese ließ die End-Abschnitte (Folgetermin/Kontext) oft weg → es blieb der Fallback. Das Datum wird nun als **PFLICHT-erste-Zeile** `FOLLOWUP_DATE: YYYY-MM-DD` verlangt (wird nicht ignoriert/abgeschnitten) und vorrangig geparst. (`orchestrator/app/api/meeting_rooms.py`)

## [1.87.1] — 2026-06-30

### Fixed
- **Meeting-Action-Items werden gleichmäßig auf alle Teilnehmer verteilt** — vorher landeten Items ohne Namens-Treffer alle beim ersten Agenten; jetzt bekommt der Agent mit der geringsten Last das nächste Item (z. B. 12 Items → 6/6 statt 12/0). (`orchestrator/app/api/meeting_rooms.py`)
- **Folgetermin-Datum-Parsing robuster** — akzeptiert ISO (YYYY-MM-DD), deutsch (DD.MM.YYYY) und relativ („in N Tagen/Wochen"); der Synthese-Prompt verlangt nun klar eine ISO-Datumszeile. So greift der von den Agenten vorgeschlagene Termin statt des +7-Tage-Fallbacks.

## [1.87.0] — 2026-06-30

### Added
- **Folgetermin wird von den Agenten terminiert + startet automatisch.** Im Meeting-Abschluss schlägt der Moderator ein **Folgetermin-Datum** vor (so gewählt, dass die Action-Items bis dahin erledigt sein können). Der Folge-Raum wird mit diesem Datum (`scheduled_for`) angelegt, das **im Raum sichtbar** ist; der Scheduler **startet ihn automatisch** zum Termin — die Agenten arbeiten ihre Tasks bis dahin ab und bringen die Ergebnisse mit. DB: `meeting_rooms.scheduled_for` (Migration `a7b8c9d0e1f2`); Scheduler: `_start_due_followups`. (`orchestrator/app/api/meeting_rooms.py`, `services/scheduler_service.py`, `frontend/src/app/meeting-rooms/page.tsx`)

---

## [1.86.1] — 2026-06-30

### Fixed
- **Meeting-Action-Items erschienen nicht im TODOs-Tab des Agenten.** Ursache: der Assignment-Prompt schickte den Agenten auf `/workspace/todo.md` (Datei) statt auf die strukturierten Todo-MCP-Tools (DB → UI-Tab). Jetzt legt der Orchestrator die TODOs **direkt** an (`agent_todos`, erscheinen sofort), und der Prompt weist den Agenten an, sie via `list_todos`/`complete_todo` selbst zu terminieren + abzuarbeiten. (`orchestrator/app/api/meeting_rooms.py`)

---

## [1.86.0] — 2026-06-30

### Added
- **Meeting-Moderator-LLM einstellbar.** Der Moderator nutzt einen wählbaren AI-Account — als **globaler Default** (Admin → Einstellungen → System → Automatisierung) und als **pro-Meeting-Override** (Dropdown unter dem Moderator-Toggle im „Neuer Raum"-Dialog). Leer = erster verfügbarer Account. DB: neue Spalte `meeting_rooms.moderator_ai_account_id` (Migration `f1a2b3c4d5e6`). (`orchestrator/app/api/meeting_rooms.py`, `frontend/src/app/settings/view.tsx`, `frontend/src/app/meeting-rooms/page.tsx`)

---

## [1.85.2] — 2026-06-30

### Fixed
- **Meeting-Moderator war fest auf Anthropic/Claude-Haiku verdrahtet → „Unable to connect to API (ConnectionRefused)" bei Azure-Kunden** (kein Anthropic). Der Moderator bezieht sein LLM jetzt aus einem **AI-Account** wie jeder Agent — einstellbar über `meeting_moderator_ai_account_id` (sonst erster verfügbarer Account). (`orchestrator/app/api/meeting_rooms.py`)
- **Meeting-Abschluss erzeugte keine Tasks/TODOs, wenn die Synthese fehlschlug.** Der Synthese-Schritt erkennt jetzt unbrauchbare/Fehler-Antworten (z. B. „API Error…") und fällt auf einen funktionierenden Teilnehmer zurück → Action-Items, Tasks und Folgetermin werden zuverlässig erzeugt.

---

## [1.85.1] — 2026-06-30

### Fixed
- **Tasks aus Benachrichtigungen waren nach kurzer Zeit weg (404 „Task nicht mehr verfügbar").** Die Eviction-Frist für abgeschlossene Tasks war nur **5 Minuten** (`TASK_EVICT_GRACE_SECONDS`) — die Benachrichtigung überlebte den Task. Frist auf **7 Tage** erhöht, damit „Task fertig — Bewertung?" anklickbar bleibt. (`orchestrator/app/core/task_router.py`)

---

## [1.85.0] — 2026-06-30

### Changed / Added
- **Meeting-Räume: vollständiger Abschluss-Workflow.** Am Meeting-Ende synthetisiert jetzt der **Moderator** die Action-Item-Liste (statt des ersten Teilnehmers; Fallback auf Teilnehmer, falls der Moderator nicht antwortet). Die zugewiesenen Agenten **übernehmen ihre Action-Items in die eigene To-Do-Liste** (`/workspace/todo.md`) und **terminieren sie selbst** (Fälligkeit + Vorgehen pro Item). Zusätzlich wird automatisch ein **Folge-Meeting-Raum** („… — Folgetermin") angelegt — seeded mit dem Meeting-Kontext + den offenen Action-Items, startbereit (`state=idle`). (`orchestrator/app/api/meeting_rooms.py`)

---

## [1.84.0] — 2026-06-30

### Added
- **Hilfe-Bereich im Sidemenü** (`/help`) — neuer Menüpunkt „Hilfe & FAQ" mit **Volltext-Suche**, **FAQ** und Funktions-How-Tos (Deep-Links direkt in die App) sowie Schnellzugriff auf **Benutzerhandbuch (PDF)**, Onboarding und Changelog. Bündelt alle als Hilfe identifizierbaren Inhalte an einem Ort. (`app/help/page.tsx`, `components/layout/sidebar.tsx`)
- **Benutzerhandbuch auf v1.84.0 aktualisiert** — neue Abschnitte: Skills herunterladen/installieren, Agent-Symbol, Voice, Meeting→Planner, Benachrichtigung→Task-Details, Hilfe-Seite, Admin (Exchange on-prem / Azure-Stimmen / Dreaming). PDF neu generiert (WeasyPrint) + im Frontend unter `/benutzerhandbuch.pdf` abrufbar. (`docs/benutzerhandbuch/`)

---

## [1.83.2] — 2026-06-30

### Added
- **Skills herunterladen (echter Download)** — Skills lassen sich jetzt als `SKILL.md` herunterladen: per Download-Icon auf den **Skill-Store-Karten**, im **Skill-Detail-Modal** („Herunterladen"), und pro installiertem Skill unter **Agent → Wissen → Skills**. Client-seitig (Blob), kein Backend nötig. (`app/skills/page.tsx`, `components/agents/skills-tab.tsx`)

### Fixed
- **„Installieren" reagierte (gefühlt) nicht** — ist kein Agent gewählt, gibt es jetzt eine klare Meldung statt stillem Nichtstun; Install-Fehler werden nicht mehr verschluckt. Install-Icon von „Download" auf „Plus" geändert (das Download-Icom war irreführend). **Korrigiert den fehlgeleiteten v1.78.1-Fix**, der nur Datei-*Anhänge* betraf, nicht den eigentlichen Skill-Download.

---

## [1.83.1] — 2026-06-30

### Fixed
- **Task-Detail-Modal jetzt zentral** statt neben dem Notification-Popup — wird per Portal an `document.body` gerendert, sodass das `fixed`-Overlay auf dem Viewport zentriert (vorher fing ein transformierter Eltern-Container/Sidebar das `position:fixed` ab). (`task-detail-modal.tsx`)
- **Freundliche Meldung bei aufgeräumten Tasks** — statt rohem „API Error 404" zeigt das Modal „Dieser Task ist nicht mehr verfügbar — vermutlich automatisch aufgeräumt." (alte Notifications zeigen auf bereits GC'te Tasks).

---

## [1.83.0] — 2026-06-30

### Added
- **Notification → Task-Detail-Modal** — Klick auf eine task-bezogene Benachrichtigung („Task abgeschlossen — Bewertung?", „Task fehlgeschlagen") öffnet ein Modal mit Details: Status, Ergebnis, Fehler, Kosten, Tokens (in/out), Dauer, Schritte, Zeitstempel + Link zum Agent. Task-ID aus `meta.task_id` bzw. `action_url`; nicht-task-bezogene Notifications bleiben unverändert. (`components/layout/task-detail-modal.tsx`, `notification-bell.tsx`)

### Fixed
- **Agent-Symbol-Picker lag hinter der Proactive-Karte** (z-index/Stacking durch `backdrop-blur`). Im Agent-Settings-Tab jetzt **inline** statt Popover (kein Overlay-Problem, direkt sichtbar); der redundante Header-Button wurde entfernt. (`components/agents/agent-appearance-inline.tsx`, `agents/[id]/page.tsx`)

---

## [1.82.0] — 2026-06-30

### Added (UI-Nachzug zu 1.80/1.81)
- **Agent-Symbol beim Erstellen wählbar** — der Create-Agent-Dialog hat jetzt einen Icon- + Farb-Picker; das gewählte Symbol wird direkt beim Anlegen gesetzt. (`create-agent-modal.tsx`)
- **Agent-Symbol auch im Settings-Tab** (zusätzlich zum Header-Button) — Sektion „Symbol & Farbe" unter Agent → Allgemein. (`agents/[id]/page.tsx`)
- **Admin-UI für „Dreaming" + Meeting→Planner** — neue Sektion „Automatisierung" unter Admin → Settings → System: Toggle für `dreaming_enabled` + Eingabe der `meeting_planner_plan_id` (vorher nur per API). (`settings/view.tsx`, `SettingsResponse` um beide Felder erweitert)

---

## [1.81.1] — 2026-06-30

### Fixed
- **KRITISCH: Tasks scheiterten reihenweise am 128-Tool-Limit** (`API error 400: Invalid 'tools': array too long … got 154, max 128`). Das Lazy-Tool-Loading (`search_tools`, v1.75) war **nur im Chat-Handler** aktiv — der **Task-Runner** (`llm_runner.py`) schickte weiterhin den **vollen** Katalog. Durch die heutigen Tool-Erweiterungen (MS-Graph 28→46, Exchange +13) riss der Task-Pfad das Limit → alle Tasks (inkl. Proactive-Mode) brachen ab. Fix: derselbe Lazy-Loading-Mechanismus (CORE-Set + `search_tools` + on-demand-Aktivierung, LRU-capped, geteilt mit dem Chat-Handler) jetzt auch im Task-Runner. **Erfordert Agent-Image-Rebuild + Neu-Erstellung laufender Agenten.** (`agent/app/llm_runner.py`, `agent/Dockerfile`)

---

## [1.81.0] — 2026-06-30

### Added
- **„Dreaming"-Memory (Grundstufe)** — der Scheduler aktualisiert periodisch (stündlich) das **adaptive Nutzerprofil** jedes aktiven Users aus dessen gesammelten Memories (preference/correction/learning), heuristisch und **ohne LLM-Kosten**. Baut auf dem vorhandenen `profile_extractor` + `UserProfile` auf (lief bisher nur on-demand via `/user-profiles/me/extract`). **Gated über `dreaming_enabled` (default off)** → keine Verhaltensänderung, bis ein Admin es aktiviert; per-User-Fehler isoliert (bricht den Scheduler nie). (`services/scheduler_service.py`, Settings)

### Hinweise zum Kundenfeedback-Stand
- **Agent-Symbolbilder** (v1.80.0) und **Meeting→MS-Planner** (v1.80.0) sind live.
- **Second Brain grafisch:** 3D-Graph existiert bereits (`vault-graph-3d.tsx`) — konkrete „Anpassung" braucht eine Spec.
- **Multi-Agent-Orchestrierung:** Delegations-Primitiv `send_message_and_wait` existiert (Master kann an andere Agenten delegieren + auf Ergebnis warten). **Dynamisches Subagent-Spawning** ist ein eigenes Design-/Test-Item — bewusst nicht ungetestet auf Prod gebracht.
- `meeting_planner_plan_id` und `dreaming_enabled` sind aktuell per Settings-API setzbar (Admin-UI-Toggles als kleiner Folgeschritt).

---

## [1.80.0] — 2026-06-30

### Added
- **Agent-Symbolbilder anpassbar** — pro Agent ein eigenes Symbol (kuratierte lucide-Icons) + Farbe, wählbar über den „Symbol"-Button auf der Agent-Detailseite, angezeigt auf den Agent-Cards. Gespeichert in `agent.config.avatar` (keine DB-Migration), Endpoint `PATCH /agents/{id}/appearance` (Owner-Check, kein Restart). (`api/agents.py`, `frontend/src/components/agents/agent-avatar.tsx` + `agent-appearance-button.tsx`, `dashboard/agent-card.tsx`, `agents/[id]/page.tsx`)
- **Meeting → MS Planner** — im Meeting erkannte Action-Items werden zusätzlich (best-effort) in einen **MS-Planner-Plan** gespiegelt, über das M365-Konto des Meeting-Owners (`created_by`) via `ms_create_planner_task` (v1.76). Gated über Admin-Setting `meeting_planner_plan_id` (leer = aus) → der bestehende interne Task-Flow bleibt unverändert. Server-seitig → harness-agnostisch (custom_llm). (`api/meeting_rooms.py`, Settings)

### Noch offen (aus Kundenfeedback, bewusst nicht blind deployed)
- **Second Brain grafisch** — die 3D-Graph-Visualisierung existiert bereits (`second-brains/vault-graph-3d.tsx`); „anpassen" braucht eine konkrete Spec vom Kunden.
- **Multi-Agent-Orchestrierung** & **„Dreaming"-Memory** — größere Features (Bausteine vorhanden: Inter-Agent-Messaging bzw. Memory/KB/Rolling-Summary/user_profiles). Werden nicht ungetestet auf die Klinik-Prod geschoben — brauchen eigenen Design-/Test-Durchlauf.

---

## [1.79.0] — 2026-06-30

### Added
- **Voice: Azure Cognitive Services Speech als STT- und TTS-Engine** — die offiziellen „Microsoft"-Stimmen über den **Azure-Speech-Key/Region des Kunden** (nicht das freie Edge-TTS). Neue Provider `AzureSpeechSTT` (Short-Audio-REST) + `AzureSpeechTTS` (Neural Voices, gleiche IDs wie Edge, SSML→MP3-Stream), wählbar in Admin → Settings → Voice; Admin hinterlegt Key + Region (z.B. germanywestcentral). **Default bleibt faster-whisper/Edge** → bestehende Sprachsessions unverändert. Vor Live-Nutzung: Azure-Speech-Key/Region eintragen + Test (Audioformat webm→Azure ist als Validierungspunkt markiert). (`orchestrator/app/services/voice_providers/{stt,tts}_azure_speech.py`, `registry.py`, `settings_service.py`, `schemas/settings.py`, `api/settings.py`, `frontend/src/components/settings/voice-settings.tsx`)

### Noch offen (Voice-Ausbau)
- Voice-Interaction-LLM über **AWS Bedrock / Azure Foundry** (statt nur Anthropic) und der **Azure-OpenAI-Realtime-Modus** (bidirektionales Audio, ein Modell für STT+LLM+TTS) folgen separat. Der Realtime-Modus braucht die Azure-Realtime-Deployment-Daten des Kunden (Endpoint/Deployment/Key/api-version) zum Bauen UND Validieren — wird nicht ungetestet auf Prod geschoben.

---

## [1.78.1] — 2026-06-29

### Fixed
- **Voice-Live-Session hängt nicht mehr bei „Verbinde…".** Der WebSocket wurde auf den falschen Pfad geöffnet (`${getWsUrl()}/agents/{id}/voice` statt `…/api/v1/ws/agents/{id}/voice` wie Chat/Logs/Notifications) → die Verbindung kam nie zustande, das „ready"-Event nie an. Kein GPT-realtime nötig — die STT/TTS/LLM-Pipeline (faster-whisper/Edge-TTS/Haiku) war intakt (stt-service healthy, Modell geladen). (`frontend/src/components/agents/voice-session.tsx`)
- **Skills-Download funktioniert wieder.** `downloadSkillFile` sendete den Auth-Cookie nicht (`fetch` ohne `credentials:"include"`, dazu ein toter localStorage-Bearer) → 401, der im Klick-Handler stumm verschluckt wurde → „Klick passiert nichts". Jetzt cookie-basiert wie der Rest der API, Fehler werden sichtbar gemacht. (`frontend/src/lib/api.ts`, `frontend/src/app/skills/page.tsx`)

---

## [1.78.0] — 2026-06-29

### Added
- **On-Prem Exchange MCP — Ende-zu-Ende verdrahtet + Admin-/Agent-UI.** Baut auf 1.77.0 (MCP-Core) auf und macht die Integration real nutzbar:
  - **MCP-Injektion:** Sobald ein Agent die `exchange_onprem`-Integration aktiv hat, wird die Exchange-MCP automatisch in seine MCP-Server-Konfiguration injiziert (`agent_manager._get_custom_mcp_env`) — analog msgraph, mit HMAC-Agent-Token.
  - **Read/Write pro Agent:** `agent.config["exchange_access"]` (read|write) über `PATCH /agents/{id}/integrations`, gated im MCP-Transport.
  - **Verfügbarkeit:** Exchange erscheint in der Integrationsliste, sobald der Admin den Server konfiguriert hat (`oauth_service.list_integrations`) — kein OAuth-Connect nötig (benutzerspezifisch via Impersonation auf die SSO-E-Mail).
  - **Admin-UI:** neuer Block „Exchange (on-prem)" unter Settings → Integrationen (Server-URL, Auth-Modus, Service-Account/Tenant).
  - **Agent-UI:** „Exchange (on-prem)"-Card mit Read / Read+Write-Toggle.
  (`orchestrator/app/core/agent_manager.py`, `app/api/agents.py`, `app/services/oauth_service.py`, `frontend/src/app/settings/view.tsx`, `frontend/src/components/agents/integration-selector.tsx`, `frontend/src/lib/api.ts`)

---

## [1.77.0] — 2026-06-29

### Added
- **On-Prem Exchange MCP (EWS) — eigener MCP-Server für on-prem Exchange (Mail + Kalender), getrennt von der M365/Graph-MCP.** Der Kunde betreibt Exchange on-prem; `graph.microsoft.com` erreicht das nicht. Neuer MCP via EWS (`exchangelib`), **user-spezifisch**: jeder Agent agiert ausschließlich auf der Mailbox seines Owners (EWS-Impersonation gepinnt auf dessen E-Mail/UPN). Drei admin-wählbare Auth-Modi (`exchange_auth_mode`): `service_account` (Service-Account + ApplicationImpersonation, kein User-Passwort nötig), `modern_auth` (Entra-App-OAuth + Impersonation), `basic` (User-Credential, delegate). 13 Tools (Mail: list/read/send/reply/forward/delete/move/mark_read; Kalender: list/create/update/cancel; + Verbindungstest `ex_whoami`); schreibende Tools über `WRITE_TOOLS` + `agent.config["exchange_access"]` im Read-Only-Modus gesperrt. Admin-Config in den System-Einstellungen (`exchange_server_url`, `exchange_auth_mode`, Service-Account, Tenant). Endpoint `POST /mcp/exchange-onprem/{agent_id}` (HMAC, per-User-Mailbox via Agent-Owner). **Inert bis ein Admin den Exchange-Server konfiguriert** — bricht nichts Bestehendes. Neue Dependency `exchangelib` → Orchestrator-Rebuild beim Deploy. (`orchestrator/app/core/exchange_mcp.py`, `orchestrator/app/api/mcp_exchange.py`, `models/oauth_integration.py`, `services/settings_service.py`, `schemas/settings.py`, `api/settings.py`, `tests/test_exchange_crud.py`)

---

## [1.76.0] — 2026-06-29

### Added
- **Vollständiges CRUD für die Microsoft-365-MCP (Outlook, Kalender, To-Do, Planner, OneDrive, Kontakte).** Behebt u.a. den Kundenfehler „Planner-Aufgaben editieren: fehlende Rechte" — es gab schlicht **kein** Update-Tool für Planner, und der `_graph`-Helper konnte den von Graph **zwingend verlangten `If-Match`-ETag** nicht senden. Neu:
  - **Planner:** `ms_update_planner_task` (Titel/Fälligkeit/Fortschritt 0·50·100/Bucket — `percent_complete=100` = erledigt) und `ms_delete_planner_task` — beide holen vorab den `@odata.etag` (`_planner_etag`) und senden ihn als `If-Match`.
  - **To-Do:** `ms_update_task`, `ms_complete_task`, `ms_delete_task`.
  - **Kalender:** `ms_update_calendar_event` (Betreff/Zeit/Ort/Body), plus reaktiviert `ms_respond_event` (zu-/absagen/vorbehaltlich) und `ms_cancel_event`.
  - **Mail:** `ms_delete_email`, plus reaktiviert `ms_forward_email`, `ms_move_email`, `ms_mark_email_read`.
  - **OneDrive:** `ms_delete_item`, `ms_move_item` (Umbenennen/Verschieben).
  - **Kontakte (neu, vorher 0 Tools trotz `Contacts.ReadWrite`-Scope):** `ms_list_contacts`, `ms_create_contact`, `ms_update_contact`, `ms_delete_contact`.
  - 6 bereits implementierte, aber wegen des alten 128-Tool-Limits ausgeblendete Handler sind wieder als Tools exponiert (Lazy-Tool-Loading aus 1.75.0 hebt das Limit auf). MS-Graph-Tools **28 → 46**; alle 27 schreibenden Tools sind über `WRITE_TOOLS` weiterhin im Read-Only-Modus gesperrt. `_graph` akzeptiert jetzt Extra-Header (`If-Match`). Tests: `orchestrator/tests/test_msgraph_crud.py` (Katalog-Integrität, Write-Gating/AuthZ, ETag-Logik, Handler-Shapes). (`orchestrator/app/core/msgraph_mcp.py`)

---

## [1.75.0] — 2026-06-25

### Added
- **Admin-Freischaltung für neue User (OpenWebUI-Style „Warten auf Freischaltung").** Admin-Toggle `require_user_approval` (Settings → Sicherheit/Login, Default aus): ist er an, landen neu per **Microsoft-SSO oder Registrierung** angelegte Konten auf `approved=false` und können die App **erst nach Admin-Freischaltung** nutzen. Login-Seite zeigt einen Hinweis, der SSO-Callback gibt Pending-Usern **kein** Token. In der **Benutzerverwaltung** (Admin-Konsole) sind Pending-User amber markiert mit **„Freischalten"**-Button. Erster User (Auto-Admin) und admin-angelegte User sind immer freigeschaltet → kein Lockout. Pending-Block an **allen** Auth-Pfaden (Login/SSO/Refresh/get_current_user/WS-Legacy/MCP-OAuth). Neues `users.approved`-Feld (Default true → Bestandsuser unberührt). (`models/user.py`, `services/sso_service.py`, `api/auth.py`, `dependencies.py`, `api/oauth_as.py`, `app/admin/page.tsx`, `app/login/page.tsx`, `app/settings/view.tsx`)
- **Lazy Tool Loading mit `search_tools` (hebt das 128-Tool-Limit dauerhaft auf).** OpenAI/Azure begrenzen Function-Tools auf **128 pro Request** — durch wachsende MCP-Integrationen wurde das gerissen (130 Tools → „Unexpected error" bei jedem Chat im Write-Modus). Statt den ganzen Katalog (18 built-in + 41 API + alle MCP-Tools) zu senden, bekommt das LLM nur noch einen **Kern-Satz (~21)** + ein **`search_tools(query)`**-Meta-Tool. Bei Bedarf sucht das Modell Tools (Keyword über Name+Beschreibung), die Treffer werden **on-demand für die nächsten Turns aktiviert** (LRU-begrenzt, ≤60). Damit pro Request immer **< 128**, Katalog beliebig groß. Nur custom_llm-Runtime betroffen (claude_code verwaltet Tools selbst). (`agent/app/llm_chat_handler.py`)

### Fixed
- **128-Tool-Limit-Crash:** msgraph-Toolset von 34 auf 28 zurückgetrimmt (OneDrive-Write inkl. `create_folder` behalten) als Sofort-Fix; Lazy Loading ist die dauerhafte Lösung.
- **Chat-UI-Layout-Shift & -Breite:** `scrollbar-gutter:stable` app-weit + Auto-Scroll ohne Smooth-Creep; `main min-w-0` → viele Chat-Tabs scrollen statt die Seite zu verbreitern.

## [1.74.4] — 2026-06-25

### Added
- **SSO-only Login + Token-Revoke (Sicherheit/Datenschutz).** Zwei Admin-Settings (Default AUS): `sso_only_login` deaktiviert den Passwort-Login → Anmeldung nur via Microsoft-SSO (MFA), schließt die „Passwort-bekannt → Impersonation"-Lücke; `revoke_msgraph_on_logout` löscht den gespeicherten MS-Graph-Token beim Abmelden. **Break-Glass:** ENV `EMERGENCY_PASSWORD_LOGIN=true` reaktiviert Passwort-Login (Lockout-Recovery). Login-Seite blendet bei SSO-only das Passwortfeld aus. Toggles im System-Tab der Settings mit Warnhinweis. (`config.py`, `api/auth.py`, `api/settings.py`, `schemas/settings.py`, `services/settings_service.py`, `app/login/page.tsx`, `app/settings/view.tsx`)

## [1.74.3] — 2026-06-25

### Added
- **Read / Read+Write-Selector pro Agent (Frontend).** Unter Microsoft 365 in den Agent-Integrationen lässt sich der MS-Graph-Zugriff jetzt auf **Read** oder **Read+Write** stellen (speichert `msgraph_access`, startet den Agenten bei Änderung neu). GET `/agents/{id}/integrations` liefert den Wert mit. (`components/agents/integration-selector.tsx`, `lib/api.ts`, `api/agents.py`)

## [1.74.2] — 2026-06-25

### Added
- **MS-Graph pro Agent als Read / Read+Write einstellbar.** Read-Modus blendet alle schreibenden Tools aus (tools/list) und lehnt sie ab; Read+Write schaltet sie frei. Im Write-Modus wird ausgehende **Mail als Entwurf** angelegt (nicht gesendet). Externer OpenWebUI-Zugang bleibt immer read-only. Einstellbar in den Agent-Integrationen (`config.msgraph_access`). (`core/msgraph_mcp.py`, `api/mcp_msgraph.py`, `api/mcp_msgraph_external.py`, `api/agents.py`)

### Fixed
- **`ms_list_chats` gab HTTP 400** (verschachteltes `$expand=members($select=…)`). Jetzt `$expand=members` → 1:1-Chats zeigen Teilnehmer-Namen, Chat-Inhalt über `ms_list_chat_messages` lesbar. (`core/msgraph_mcp.py`)

## [1.74.1] — 2026-06-25

### Fixed
- **Security (ms_graph_get):** Pfad-Validierung gehärtet — blockiert jetzt protokoll-relative Pfade (`//host`), Backslashes und Schema/`..` strikt (Regex `^/[A-Za-z0-9]`), sodass der read-only GET garantiert auf graph.microsoft.com gepinnt bleibt. (`core/msgraph_mcp.py`)

## [1.74.0] — 2026-06-25

### Added
- **MS-Graph-MCP deutlich erweitert — fast alles in Graph durchsuchbar/lesbar.** Neue Tools: `ms_search` (universelle Microsoft-Search über Mail/Events/Dateien/Chat-Nachrichten), `ms_graph_get` (read-only GET-Escape-Hatch auf jeden Graph-v1.0-Endpoint, durch delegierte Scopes begrenzt, kein `..`/scheme), `ms_list_chat_messages` + `ms_list_channel_messages` (Chat-/Channel-Inhalte lesen), `ms_list_planner_plans` / `ms_list_planner_tasks` / `ms_create_planner_task` (Planner), `ms_search_people` (Name→E-Mail auflösen). `ms_list_chats` zeigt jetzt Teilnehmer-Namen statt nur „oneOnOne". (`core/msgraph_mcp.py`)

## [1.73.4] — 2026-06-25

### Fixed
- **MS-Graph-Tools des Agenten gaben 500 (Teams/Mail/Teamliste).** `mcp_msgraph._get_access_token` konstruierte `OAuthService(db)` ohne das erforderliche `redis`-Argument → `TypeError` bei JEDEM Graph-Tool-Aufruf (initialize/tools-list liefen, aber `tools/call` crashte). Auf `OAuthService(db, None)` korrigiert (get_valid_token nutzt kein redis). Teams/OneDrive/To-Do funktionieren damit über den Agenten. (`api/mcp_msgraph.py`)

## [1.73.3] — 2026-06-25

### Changed
- **Settings-Seite in Unter-Tabs gegliedert.** Statt einer langen Scroll-Seite jetzt 4 Reiter: **Modelle** (Model-Provider, Agent-Config, Templates), **Integrationen** (OAuth + Telegram), **Voice**, **System** (License, Access Control). Reiner UI-Refactor, keine Funktionsänderung. (`app/settings/view.tsx`)

## [1.73.2] — 2026-06-25

### Fixed
- **„MCP-Server extern exponieren"-Toggle ließ sich nicht aktivieren / blieb nach dem Speichern aus.** Der Endpoint `PUT /settings/msgraph-mcp-external` rief `SettingsService.set()` ohne anschließenden `await db.commit()` auf — die Änderung wurde beim Session-Schluss zurückgerollt (PUT gab trotzdem 200, nur das In-Memory-Flag wirkte bis zum Restart). `getSettings` las daraufhin den nicht-persistierten DB-Wert (`false`) → Toggle sprang zurück. Commit ergänzt → Einstellung bleibt erhalten. (`api/settings.py`)

## [1.73.1] — 2026-06-25

### Fixed
- **MS-Graph-MCP für Agenten gab 401** → Agent konnte Kalender/Mail/Teams nicht nutzen, obwohl „Microsoft" aktiviert + M365 verbunden war. Zwei Ursachen behoben: (1) der auto-injizierte msgraph-MCP-Server bekam **keinen** Bearer-Token mit (`auth_map["msgraph"]` fehlte in `_get_custom_mcp_env`), (2) der Endpoint verlangte `X-Agent-ID`, das der Agent-MCP-Client gar nicht schickt — er authentifiziert jetzt direkt gegen die `agent_id` aus dem URL-Pfad. (`core/agent_manager.py`, `api/mcp_msgraph.py`)

## [1.73.0] — 2026-06-25

### Added
- **Konfigurierbarer Microsoft-Tenant** (`oauth_microsoft_tenant_id`, Default `common`) — pro Kunde via `.env` (`OAUTH_MICROSOFT_TENANT_ID`) oder Admin → Settings. Nicht hardcoded; wirkt für Login **und** M365-Integration. Single-Tenant-Azure-Apps brauchen das zwingend.

### Changed
- **Microsoft-SSO-Login holt jetzt direkt die Graph-Tokens.** Der Login fordert die vollen Graph-Scopes **+ `offline_access`** an und speichert Access/Refresh verschlüsselt (`persist_tokens` von Login- und Integrations-Flow **geteilt** — eine Storage-Stelle). Ein Login = Identität **und** Graph, kein separater „M365 verbinden"-Schritt nötig. (`core/sso_providers.py`, `services/sso_service.py`, `services/oauth_service.py`)

### Fixed
- **AADSTS50194** behoben: Single-Tenant-Azure-Apps können den `/common`-Endpoint nicht nutzen — die Authority (Authorize **und** Token-Exchange) wird zur Laufzeit auf den konfigurierten Tenant gesetzt.
- **Cross-Tenant-Account-Takeover-Schutz:** Microsoft-`email_verified` wird **nur** bei konkretem Tenant (GUID/Domain) vertraut — `common`/`organizations`/`consumers` ausgeschlossen.

## [1.71.0] — 2026-06-25

### Added
- **MS-Graph-MCP-Server für externe LLM-Clients (OpenWebUI) per OAuth 2.1.** Admin-Schalter (Settings → Microsoft 365, nur aktivierbar wenn App-Registrierung hinterlegt) exponiert den MCP-Server unter `POST /api/v1/mcp/msgraph`. Eingebauter **OAuth-2.1-Authorization-Server**: RFC 8414 (AS-Metadata), RFC 9728 (Protected Resource Metadata), RFC 7591 (Dynamic Client Registration), `/oauth/authorize` (Consent über das bestehende Microsoft-SSO-Login), `/oauth/token` (PKCE S256 + Refresh-Rotation, audience-gebundene Tokens). **Pro User**: jeder OpenWebUI-Nutzer loggt sich ein und nutzt sein **eigenes** M365. Caddy-Discovery-Routen für `/.well-known/oauth-*`. Default AUS. (`core/mcp_oauth.py`, `api/oauth_as.py`, `api/mcp_msgraph_external.py`, `models/oauth_client.py`)
- **Mail-Suche nach Absender/Betreff** in `ms_list_emails`: neue Filter `sender` + `subject` (Graph-KQL) zusätzlich zur Freitextsuche.

### Changed
- **MS-Graph-Tools + MCP-Dispatch zentralisiert** (`core/msgraph_mcp.py`) — Agent-Transport und Extern-Transport teilen sich exakt eine Tool-Implementierung (keine Doppel-Implementierung).

### Fixed
- **Latenter Bug:** die per-Agent-Token-Auflösung rief `get_valid_token(integration)` statt `("microsoft", user_id)` → hätte immer geworfen (fiel nicht auf, da MS unkonfiguriert). Behoben.
- **Security-Härtung** (Scanner vor Release): Graph-Resource-IDs URL-encodiert (Path-Traversal-Schutz), Mail-Ordner-Allowlist, KQL-Metazeichen-Escaping (Injection-Schutz), DCR-Client-Limit (Abuse), generische Graph-Fehler statt verbatim (Info-Disclosure), separater MCP-Signing-Key (Key-Trennung), PKCE-Verifier-Längenprüfung.

## [1.70.0] — 2026-06-24

### Added
- **3D-Wissensgraph für Second-Brain-Vaults (Obsidian-Stil).** Neuer **„Graph"-Tab** im Vault-Browser: Notizen als leuchtende **Bubbles** (Größe = Verknüpfungsgrad, Farbe = Ordner), **Kanten** aus `[[wikilinks]]` und relativen `.md`-Links, Flow-Partikel und Bloom-Glow. **Klick auf eine Bubble** fokussiert die Kamera und öffnet ein **Detail-Panel** (Inhalt-Vorschau, Tags, verlinkte Notizen, „Im Editor öffnen"). Backend: `GET /brains/{id}/graph` → `vault.build_graph` (reines Dateisystem + Regex, kein DB-Dependency, jailed, Soft-Cap 2000 Knoten). Frontend: `react-force-graph-3d` (three.js), client-only lazy-geladen (kein SSR). Eindeutig als `getVaultGraph`/`Vault*` benannt — getrennt von der persönlichen KB (`getBrainGraph`).
- **Proactive Mode: Prompt einsehbar + pro Agent erweiterbar.** Im Proactive-Panel lässt sich der feste **Basis-Prompt aufklappen** (read-only) und um **agent-spezifische Zusatz-Anweisungen** ergänzen. Der Scheduler komponiert zur Feuerzeit `Basis (Code) + Zusatz (config['proactive']['custom_instructions'])` — Basis-Verbesserungen gelten weiterhin **sofort für alle** Agenten, keine DB-Duplikation. Toggle/Intervall-Speichern lassen den Zusatz unangetastet. (`agents.py`, `scheduler_service.py`, `proactive-toggle.tsx`)

### Changed
- **Live-Steering greift jetzt mitten im Turn.** Nachrichten, die während eines laufenden Agent-Turns ankommen, werden nun **nach jedem fertigen Tool-Call** in den Kontext injiziert (nach Compaction, damit frischer Input nicht wegsummiert wird) — der Agent nimmt neue Infos **sofort beim nächsten Schritt** mit, nicht erst am Turn-Ende. (`llm_chat_handler.py`)

## [1.69.4] — 2026-06-24

### Changed
- **Nachhaltige Trennung „Second Brain (geteilter Vault)" vs „Knowledge Base (persönlich)".** Die Namens-Kollision an der Wurzel beseitigt — der Agent hat jetzt **zwei klar getrennte, eindeutig benannte Tool-Familien**:
  - **`secondbrain_search` / `secondbrain_read` / `secondbrain_write` / `secondbrain_list`** — der **geteilte Abteilungs-Vault** (`/mnt/brains/<slug>/`, viele User, UI: Wissen → Second Brain). Dateisystem-basiert, jailed, read-only/read-write erkannt. „Ins Second Brain schreiben" = `secondbrain_write`.
  - **`brain_*`** — die **persönliche, account-gebundene Knowledge Base** (DB/pgvector, Knowledge-Tab). Beschreibungen entsprechend korrigiert (nicht mehr „Second Brain").
  - Agent-Instruktionen (`runner_hooks.py`) routen jetzt eindeutig (shared → `secondbrain_*`, persönlich → `brain_*`); Orchestrator-Docstring (`brain.py`) als „Knowledge Base API" klargestellt. Behebt, dass der Agent „ins Second Brain" in die falsche (persönliche) Knowledge Base schrieb.

## [1.69.3] — 2026-06-24

### Fixed
- **Agent schrieb „ins Second Brain" in den falschen Speicher.** Namens-Kollision zweier Systeme: die Tools `brain_search`/`brain_contribute`/`brain_get` hängen an der **DB-pgvector-Brain** (`/brain/agent/*`), während der **Second-Brain-Vault** (den der User im UI sieht) als **Markdown-Dateien** unter `/mnt/brains/<slug>/` lebt. Die Agent-Instruktion sagte „Contribute to the Second Brain (brain_contribute)" → der Agent schrieb in die DB-Brain, im Vault stand nichts. Instruktion (`runner_hooks.py`) jetzt eindeutig getrennt: **(A) Second-Brain-Vault → `.md`-Dateien via write_file in den gemounteten `/mnt/brains/<slug>/`** (nur bei rw), **(B) `brain_contribute` → separater persönlicher Wissensspeicher**.

## [1.69.2] — 2026-06-24

### Fixed
- **Second Brain ließ sich nirgends einem Agenten zuweisen (UI-Sackgasse).** Die Volume-Mounts-Sektion graute `brain-*` aus und verwies auf den Wissen-Tab; der **Wissen → Second Brain**-Tab war aber rein anzeigend und verwies zurück auf „Admin/Mount-Rechte" — es gab **keinen** Zuweisen-Schalter. Jetzt ist der **Wissen → Second Brain**-Tab ein echtes Zuweisen-Panel: alle freigegebenen Brains mit **Toggle** → `updateAgentMounts` (+ automatischer Neustart), „Inhalt"-Button zum Ansehen, und eine klare Meldung, falls noch keine Brains freigegeben sind (Admin → Rollen → Mountshares bzw. Users → Mount-Rechte). Nicht-Brain-Mounts bleiben unangetastet. `frontend/src/app/agents/[id]/page.tsx`.

## [1.69.1] — 2026-06-24

### Fixed
- **Freigabe-Anfrage (`request_approval`) pausierte den Agenten nicht** — er fragte um Freigabe, lief aber **weiter** statt zu warten, und beim Task-Ende verschwand das Approval-Popup. Ursache: das Tool kehrte sofort zurück („Approval requested, use check_approval") statt zu blockieren. Jetzt **blockiert** `request_approval`: es pollt die Entscheidung (`/approvals/check/{id}`) und wartet (Default 15 Min), gibt dann **APPROVED** (inkl. gewählter Option) → weiter, **DENIED** → stop, oder **kein Entscheid/Timeout** → „nicht fortfahren, stop". Tool-Beschreibung entsprechend angepasst. `agent/app/tools/api_client.py`, `agent/app/tools/definitions.py`.

## [1.69.0] — 2026-06-24

### Added
- **Trainierte Agenten verteilen — Admin klont einen fertig angelernten Agenten als eigene Kopie pro User/Gruppe.** Admin baut/trainiert einen Agenten fertig und verteilt ihn dann an einzelne User **und/oder eine ganze Gruppe (Custom Role)**. Jede Kopie ist ein **vollständig eigenständiger Agent** (eigener Container, eigenes Workspace-Volume, dem Ziel-User gehörend) — nie eine geteilte Instanz.
  - Übernimmt die **volle Config** des Originals: model, **mode + llm_config/ai_account** (damit die Kopie auf custom_llm/Azure wirklich läuft), role, permissions, integrations, MCP-Server, budget, autonomy, browser.
  - Kopiert das **angelernte „Gehirn"**: der komplette Workspace des Originals (`knowledge.md`, installierte Skills unter `.claude/skills/`, `CLAUDE.md`, Docs) wird in jede Kopie geklont — außer `.git`; `.agent_state.md` startet je Kopie frisch.
  - **Snapshot + idempotent:** verteilt an alle aktuellen Mitglieder; wer schon eine Kopie dieser Quelle hat, wird übersprungen.
  - Neuer Endpoint `POST /admin/distribute-agent`, Herkunfts-Tracking via `agents.source_agent_id` (Migration `b6c7d8e9f0a1` + Startup-Ensure), UI unter Admin → Zuweisungen → „Trainierten Agent verteilen".

## [1.68.5] — 2026-06-24

### Fixed
- **Notification-Live-WebSocket war komplett kaputt (`/api/v1/ws/notifications` → 403 / „bad response from the server").** Regression aus 1.68.3: beim Einbau des `_notif_visible_agent_ids`-Helfers rutschte dieser **zwischen den `@router.websocket("/notifications")`-Decorator und die eigentliche Handler-Funktion** — dadurch dekorierte der Route-Decorator den Helper (erwartet `user_id` statt `websocket`) und `ws_notifications` war gar nicht mehr registriert. Decorator wieder direkt über `ws_notifications` gesetzt. Chat-/Logs-WS waren nie betroffen.

## [1.68.4] — 2026-06-24

### Fixed
- **Bridge-App (Windows): Login-Fenster zu klein, „Anmelden"-Button abgeschnitten.** Das customtkinter-Setup-Fenster hatte fix `480x400` + `resizable(False, False)` — auf Windows (DPI/Font-Scaling) passte der Inhalt nicht in 400px Höhe, der Login-Button lag unterhalb des sichtbaren Bereichs und das Fenster ließ sich nicht vergrößern. Jetzt: höheres Default (`480x560`), vertikal resizable + `minsize`, und der Button-Bereich ist am **unteren Rand verankert** (nie mehr abschneidbar). `computer-use-bridge/tray_app.py`.

---

## [1.68.3] — 2026-06-24

### Security
- **Cross-User-Leak: jeder User sah ALLE Notifications (auch fremder Agenten).** `Notification` hat keine `user_id`-Spalte (nur `agent_id`), und `list_notifications`/`unread_count` filterten **gar nicht** → die Notifications fremder Agenten (Task-Ergebnisse, Approval-Inhalte) waren für jeden sichtbar, und der Badge zählte global. Gefixt: alle UI-Notification-Endpoints (`list`, `count`, `read`, `read-all`, `respond`, `delete`) **scopen jetzt nach sichtbaren Agenten** (eigene + besitzerlose + freigegebene) via neuem `_visible_agent_ids`. Der **Live-WebSocket** (`/ws/notifications`) filtert ebenfalls per-User (fail-closed). Antwort auf die Ausgangsfrage „sieht das nur der Admin?": vorher **nein, jeder** — jetzt nur noch der Berechtigte.

---

## [1.68.2] — 2026-06-24

### Fixed
- **Sidebar eingeklappt: „Notifications" zeigte abgeschnittenen Text statt Icon.** Im collapsed-Modus (64px) rendert die `NotificationBell` jetzt — wie alle anderen Items — ein **icon-only** Glocken-Symbol (mit Unread-Badge + Tooltip „Notifications") statt des breiten Buttons mit Text. Neues `collapsed`-Prop in `notification-bell.tsx`, gesetzt aus `sidebar.tsx`.

---

## [1.68.1] — 2026-06-24

### Fixed
- **Ein einzelnes nicht-unterstütztes Bild (z.B. SVG-Logo) killte die ganze Task mit `API error 400: invalid_image_format`.** Beim Video-/Präsentations-Bau lud der Agent Bilder aus dem Netz; `view_image` bestimmte den Bildtyp nur aus der **Dateiendung/Content-Type** (`default="image/jpeg"`), nicht aus dem echten Inhalt. Eine `logo.svg` wurde so als „image/jpeg" an die Vision-API geschickt → 400 → Abbruch der kompletten Aufgabe. Behoben mit zwei Schichten:
  - **Tool-Ebene** (`view_image`/`present_image`): echtes Format aus **Magic-Bytes** erkennen, **SVG → PNG rastern** (cairosvg + libcairo2 — Logos werden so sogar nutzbar), andere Rasterformate (bmp/tiff/ico/…) via Pillow → PNG. Lässt sich ein Bild nicht nutzen (HTML-Fehlerseite, korrupt) → **Tool gibt einen Text-Fehler zurück und der Agent macht weiter**, statt abzustürzen.
  - **Provider-Ebene** (Sicherheitsnetz): vor jedem OpenAI/Azure-Call werden alle Bild-Blöcke **re-gesnifft**; mismatchte/unsupported Blöcke werden **gedroppt** (und falsch gelabelte echte Bilder korrigiert) — egal aus welcher Quelle, ein kaputtes Bild kann nie wieder die ganze Completion 400en.
  - Neue Dependency `cairosvg>=2.7` im Agent-Image.

---

## [1.68.0] — 2026-06-24

### Added
- **Second Brain via MCP — jeder Vault als externer MCP-Server.** Ein Second Brain kann jetzt von externen MCP-Clients (n8n, Cursor, …) als eigener MCP-Server genutzt werden, analog zum bestehenden Per-Agent-MCP-Server.
  - **Endpoint:** `POST /api/v1/mcp/brains/<slug>` (2025-06-18 Streamable HTTP, JSON-RPC: `initialize`/`tools/list`/`tools/call`/`ping`), geschützt per **Bearer-Token** pro Brain.
  - **Tools:** `brain_search` (grep über die `.md`-Sammlung — boardmittel, keine Embedding-Abhängigkeit), `brain_read` (Datei lesen), `brain_list` (Dateien auflisten). Path-Jailing geteilt mit dem Datei-Browser (`app/core/vault.py`) — kein Escape aus dem Vault, `.git` gesperrt.
  - **Token-Verwaltung (Admin):** in der Second-Brains-Ansicht pro Brain MCP aktivieren → Token wird **einmalig** angezeigt (Fernet-verschlüsselt gespeichert, nie wieder auslesbar); „neu generieren" rotiert (alter Token sofort ungültig); deaktivieren wischt den Token. Endpoint-URL + Token per Klick kopierbar.
  - Neue Spalten `second_brains.mcp_enabled` + `mcp_token_encrypted` (Migration `a5b6c7d8e9f0` + idempotenter Startup-Ensure, analog pgvector).

---

## [1.67.0] — 2026-06-24

### Changed
- **Kontext-Kompaktierung: gleitendes Fenster + rollende Summary statt voller History pro Turn.** Bisher feuerte die Compaction erst bei **75 % des Modellfensters** — gpt-5.x hat **1 Mio** Tokens, also bei 750k, was praktisch nie erreicht wurde. Folge: jeder Turn schickte die **komplette, wachsende History** → kumulative Input-Kosten explodierten (z. B. 490k Tokens über 8 Turns). Neu:
  - **Absolutes Token-Budget** (`ABSOLUTE_COMPACTION_BUDGET = 150k`) triggert die Kompaktierung, unabhängig von der Fenstergröße (`effective_threshold_tokens = min(75 % Fenster, 150k)`). Auf langen Tasks bleiben die Calls dadurch konstant günstig.
  - **Layer 4 ist jetzt eine gleitende, inkrementelle rollende Summary** statt „gesamte History verwerfen": die **letzten 24 Nachrichten bleiben wörtlich** (Tool-I/O — exakte Pfade, IDs, Werte, die der Agent fürs Weiterarbeiten braucht), alles Ältere wird in **eine** Summary gefaltet, die bei jeder Kompaktierung **fortgeschrieben** (nicht neu erzeugt) wird.
  - **Boundary-Schutz:** das Recent-Fenster beginnt nie mit einem verwaisten `tool`-Ergebnis (dessen `tool_call` wegsummiert wäre) — solche Ergebnisse werden in den Summary-Block zurückgeschoben. Verhindert Tool-Protocol-Fehler bei custom_llm-Providern.
  - Gilt für beide custom-LLM-Pfade (`LLMRunner` Task-Ausführung + `LLMChatHandler` interaktiver Chat). Claude-Code-CLI-Agenten machen ihre Compaction weiterhin nativ.

---

## [1.66.0] — 2026-06-24

### Fixed
- **Lange Befehle (Video-Render, Builds, Installs) wurden nach 30 s abgewürgt.** Der Bash-Tool-Default-Timeout war **30 s** — ein HyperFrames-Render dauert aber ~76 s (Low-Memory, 1 Worker) → „Command timed out", der Agent dachte „geht nicht" und brach ab. **Default jetzt 120 s, Max 600 s** (Modell kann pro Befehl höher anfordern); Timeout-Fehlermeldung weist auf höheren Timeout hin.
- **Agent-Memory 4g → 8g.** Mit 4 GB erzwingt der Renderer „low-memory profile" (1 Worker, langsam). 8 GB nutzt mehr Worker → schnellere, zuverlässigere Renders. Greift bei Agent-Recreate/Update.

## [1.65.1] — 2026-06-24

### Fixed
- **Tool-Bubble-Cluster springt nicht mehr auf/zu.** Der Cluster bleibt waehrend der Agent arbeitet durchgehend kompakt (Bubbles); die laufende Bubble zeigt einen Spinner. Vorher klappte er bei jedem Tool-Aufruf auf und wieder zu (unruhig).

## [1.65.0] — 2026-06-24

### Changed
- **Chat: Tool-Aufrufe als kompakter Bubble-Cluster.** Aufeinanderfolgende Tool-Aufrufe werden jetzt **eingeklappt** als überlappende Bubbles dargestellt (max. 5 + „+N", iOS-Stil) statt als lange Liste. Klick auf den Cluster klappt die volle Liste auf; Klick auf einen Tool-Eintrag zeigt dessen IN/OUT-Details (wie bisher). Während der Agent arbeitet, ist der Cluster automatisch ausgeklappt (Live-Sicht).

## [1.64.1] — 2026-06-24

### Fixed
- **pgvector ist jetzt bei jedem Deploy garantiert da.** Ursache des fehlenden pgvector: der Startup macht `create_all` + `alembic stamp head` (markiert Migrationen als angewendet, ohne sie auszuführen) — die `embedding`-Spalten sind aber pgvector-`vector(1024)` via SQL-Migration, also wurden sie auf frischen DBs übersprungen. Der Orchestrator stellt jetzt beim **Start** idempotent `CREATE EXTENSION vector` + die `embedding`-Spalten + HNSW-Indizes sicher (eigene Transaktion, blockiert den Start nicht). Embeddings bleiben **lokal** (BAAI/bge-m3, 1024-dim; kein Cloud-Fallback ohne OPENAI_API_KEY).

## [1.64.0] — 2026-06-24

### Fixed
- **MCP-Tools schlugen bei custom_llm-Agents mit „Unknown MCP tool" fehl.** Der Runner machte die Tool-Discovery auf seiner `MCPHTTPClient`-Instanz, der `ToolExecutor` rief die Tools aber auf einer **zweiten, leeren** Instanz auf. Jetzt teilen sich beide (in `llm_runner` **und** `llm_chat_handler`) den **discovery-Client** → MCP-Tools (z.B. `mcp_MediaWiki-MCP_search`) sind aufrufbar.
- **`brain_search` / `skill_search` / Memory warfen API 500.** Auf Deployments, die auf einem Postgres **ohne pgvector** aufgesetzt wurden, fehlten die `embedding`-Spalten („column embedding does not exist"). Neue **idempotente Repair-Migration** legt die pgvector-Extension + `embedding vector(1024)`-Spalten + HNSW-Indizes auf `knowledge_entries`/`agent_memories`/`skills` an (no-op, wo schon vorhanden).

## [1.63.2] — 2026-06-24

### Fixed
- **Persönliche Agents-Seite (Seitenmenü) zeigt nur eigene Agents — auch für Admins.** Bisher sah ein Admin im Seitenmenü → Agents ALLE Agents (auch die anderer User). Jetzt ist die Liste „own"-scoped (eigene + ungebundene + geteilte). Der globale Blick bleibt die **Admin-Konsole → All Agents** (`scope=all`). Neuer Query-Param `GET /agents/?scope=own|all`.

## [1.63.0] — 2026-06-24

### Added
- **Second-Brain Inhalt: Markdown-Vorschau + klickbare `[[wikilinks]]`.** Im Brain-Browser gibt es einen Vorschau/Bearbeiten-Umschalter; in der Vorschau wird Markdown gerendert und `[[Titel]]`-Verweise sind klickbar (öffnen den passenden Artikel).
- **User-Anlage nutzt Custom-Rollen (Gruppen) statt Enum-Rollen.** Im „Add User"-Dialog wählt man die unter **Rollen** angelegten Gruppen (GBD …); `custom_role_id` wird beim Anlegen gesetzt. Admin-Rechte werden weiter separat in der Userliste vergeben.
- **Agent → Wissen → „Second Brain"-Subtab.** Zeigt die dem Agent zugewiesenen Second Brains und öffnet den Inhalts-Browser. Im Mount-Selektor sind `brain-*`-Mounts jetzt **ausgegraut** (nicht klickbar) — Second Brains werden über den Wissen-Tab / Rollen verwaltet.

## [1.62.1] — 2026-06-24

### Fixed
- **Agent-Erstellung mit AI-Account scheiterte mit 403 „LLM-Provider … nicht erlaubt".** Wenn ein (der Gruppe freigegebener) AI-Account gewählt wird, ist der Account-Grant die Autorisierung — der Provider-String (z.B. `azure-openai`) wird **nicht mehr** zusätzlich gegen `role.llm_providers` geprüft. Der `llm_providers`-Check gilt nur noch für die manuelle Provider-Eingabe (ohne AI-Account).

## [1.62.0] — 2026-06-24

### Changed
- **Jeder authentifizierte User darf Agenten anlegen** (vorher Manager/Admin). Wie viele regelt weiterhin das `max_agents`-Limit der Gruppe/Rolle (VIEWER = 0).
- **Agent-Erstellung zeigt nur verfügbare Modelle/Harnesses.** Im Account-&-Harness-Selektor erscheinen nur **verbundene** OAuth-Harnesses (Claude/Codex) und **aktive AI-Accounts** (gruppengefiltert über `ai_account_ids`). Die **manuelle** „Eigener Provider/Modell"-Eingabe ist nur noch für **Admins** sichtbar — normale User wählen ausschließlich vom Admin bereitgestellte AI-Accounts.

## [1.61.0] — 2026-06-24

### Added
- **Bearer-Auth für MCP-Server.** Beim Hinzufügen eines MCP-Servers (System → Integrations) kann jetzt ein **Bearer Token** angegeben werden. Er wird Fernet-verschlüsselt gespeichert und sowohl bei der Tool-Discovery als auch bei jedem Agent-Tool-Call als `Authorization: Bearer …` mitgesendet (neue Agent-Env `CUSTOM_MCP_AUTH`; `mcp_client` setzt den Header pro Server). Migration: `mcp_servers.auth_token_encrypted`.
- **MCP-Server/Tools als Gruppen-Recht (Custom Roles).** Neuer Permission-Key `mcp_server_ids`: eine Gruppe darf nur die freigegebenen MCP-Server nutzen (Multi-Select in der Rollen-UI; Enforcement in `_get_custom_mcp_env` filtert die Server des Agents nach der Gruppe des Owners). Admins unbeschränkt.

## [1.60.0] — 2026-06-24

### Added
- **Budget in den Agent-Settings (Admin-Governance).** Unter Agent → Settings → Ressource-Limits gibt es jetzt ein **Budget / Monat**: Admins setzen die Obergrenze (Betrag + Verhalten bei Überschreitung: auf Haiku umschalten oder Agent stoppen), normale User sehen es **read-only**. Backend: `PATCH /agents/{id}/budget` ist jetzt **admin-only** (vorher Owner erlaubt).

### Changed
- **Admin-Menüleiste responsiver.** Die Tab-Leiste (All Agents, Zuweisungen, …, Audit Log) bricht nicht mehr um, sondern scrollt bei wenig Platz **horizontal** (kompaktere Tabs, kein Zeilenumbruch); Seiten-Padding skaliert mit der Breite.

## [1.59.0] — 2026-06-24

### Added
- **Second-Brain Content-Browser + Vault-Standards.** Klick auf ein Brain (oder das Ordner-Icon) öffnet einen **Datei-Browser**: Ordner-/Datei-Baum links, Markdown-Editor rechts — `.md` ansehen, bearbeiten, neu anlegen und löschen (read-only bei `ro`-Brains). Backend: `GET /brains/{id}/tree`, `GET/PUT/DELETE /brains/{id}/file` (admin-only, pfad-jailed auf den Vault, `.git` gesperrt). Änderungen werden vom lokalen Auto-Commit-Watcher versioniert.
- **Vault-Standard beim Anlegen wählbar** (`second_brains.standard`): **IT-Support/Runbooks** (Ordner Drucker/Netzwerk/Zugaenge/Software/Hardware + `Symptom→Ursache→Lösung`-Vorlage), **Wikimedia-Stil** (Themen-Ordner + `[[wikilinks]]`) oder **Freiform**. Beim Speichern werden Ordner + `index.md` + `CONVENTIONS.md` (und bei IT-Support eine `_template.md`) automatisch scaffolded; die Agents richten sich beim Pflegen nach `CONVENTIONS.md`.

## [1.58.0] — 2026-06-24

### Changed
- **Agent-Runtime-Gleichschaltung (claude_code / codex / custom_llm).** Die drei Runtimes injizieren jetzt **dieselben** Kontext-Bausteine aus einer zentralen Stelle:
  - **Neu `runner_hooks.get_mounts_context()`** — erkennt Host-Mounts und **Second-Brain-Vaults** (`/mnt/brains/*`) zur Laufzeit per Filesystem-Scan und beschreibt sie im Prompt. Damit wissen auch **custom_llm**-Agents (die ihre `AGENT.md` nie lesen) von den Vaults und durchsuchen sie zuerst.
  - **Neu `runner_hooks.compose_prompt_bundle()`** — eine geteilte, geordnete Bausteinkette (Startup-Prefix, Memory, Skills, **Mounts/Second Brain**, **Marketplace-Skill-Vorschläge**, User-Feedback, Improvement). `agent_runner` und `codex_runner` nutzen sie für beide Modi; künftige Bausteine landen automatisch bei allen.
  - **custom_llm**: Mounts/Second-Brain im System-Prompt (Task + Chat), Marketplace-Skill-Vorschläge auch im **Chat** (vorher nur Task).
  - **codex**: Chat/Lightweight bekommt jetzt den vollen Kontext (vorher nackt) inkl. Mounts.

### Added
- **Inter-Agent-Messages für custom_llm** — `message_consumer` beantwortet Agent-zu-Agent-Nachrichten im `custom_llm`-Modus über den LLM-Provider direkt (vorher nur CLI-Modi). Damit funktioniert Agent-Kommunikation auch für Azure/OpenAI-basierte Agents.

### Notes
- **Codex-MCP** bleibt bewusst offen (Codex spricht kein MCP wie Claude); Codex-Agents nutzen den Second Brain über native `grep`/shell statt MCP-`brain_search`.
- Agent-Image geändert → Agents zeigen „Update available" (AGENT_VERSION 1.58.0).

## [1.57.0] — 2026-06-24

### Added
- **Gruppen-basierte Rechte-Bündel (Custom Roles als Gruppen).** Eine Gruppe (Custom Role) kann jetzt Ressourcen direkt **vergeben** — ein User bekommt eine Gruppe und erbt alles, manuelle Einzelzuweisungen kommen additiv dazu (Union):
  - **Second Brains / Mounts als Grant statt nur Filter** — `role.permissions.mount_labels` vergibt Zugriff; effektiver Zugriff = Gruppen-Grant ∪ per-User `user_mount_access`. Ein Brain einer Gruppe zuweisen genügt, damit alle Mitglieder es nutzen können.
  - **AI-Accounts per Gruppe** — neuer Permission-Key `ai_account_ids`: nur freigegebene LLM-Accounts (und damit Modelle) sind für die Gruppe wähl- und nutzbar (`list_ai_accounts` gefiltert + Check bei Agent-Erstellung).
  - **Keys/Secrets per Gruppe** — neuer Permission-Key `secret_ids`: die Gruppe sieht/nutzt nur freigegebene Keys (`list_secrets` gefiltert + Check bei Secret-Zuweisung).
  - **Roles-UI** erweitert um Multi-Selects für **AI-Accounts (Konten)** und **Keys/Secrets** (neben den bestehenden für Mounts/Second Brains, LLM-Provider, Menü). Admins bleiben unbeschränkt.
- Keys sind reine JSON-Felder in `custom_roles.permissions` → **keine DB-Migration** nötig.

### Fixed
- **Brain-Mount über die UI zuweisbar** — der `PATCH /agents/{id}/mounts`-Endpoint nutzte noch den statischen ENV-Katalog und kannte die DB-Second-Brains nicht (422 „Unknown mount label"). Nutzt jetzt den gemergten Katalog (`get_effective_catalog`).

## [1.56.3] — 2026-06-24

### Added
- **Builtin skill `secondbrain_lookup` in the Skill Marketplace** — a template workflow skill that tells agents to search the shared department Second Brain (`/mnt/brains/*`) before answering support/how-to/troubleshooting questions (grep on keywords/error codes → read matches → answer with source citation), and to contribute new learnings back as Wikimedia-style `.md` articles. Seeded as an ACTIVE marketplace skill, so it is discovered automatically via the existing agent `skill_search` flow (runner_hooks) — every agent checks the marketplace and can install/use it.

## [1.56.2] — 2026-06-24

### Added
- **git in the orchestrator image** — so Second Brain vault provisioning can `git init` the local repo directly when a brain is created (no dependency on the host watcher for the initial repo).

### Fixed
- **Auto-commit watcher self-heals vault repos** — `scripts/secondbrain-autocommit.sh` now `git init`s any vault under `/srv/secondbrain` that has no `.git` yet before committing, so file history works even for vaults created before git was available. Local only, no remote.

## [1.56.1] — 2026-06-24

### Fixed
- **Second Brain vault permissions** — the orchestrator runs as root but agent containers run as uid 1000; a root-created vault dir (0755) was not writable by agents. New vaults are now created `0777` (and the seeded `index.md` `0666`) so read-write brains are actually writable by assigned agents. `.git` stays root-owned (the host auto-commit timer runs as root).

## [1.56.0] — 2026-06-24

### Added
- **Second Brains — abteilungsweite, geteilte Wissens-Vaults.** Ein Admin legt im neuen Admin-Tab „Second Brains" pro Abteilung ein Brain an (Name + Slug); der Orchestrator provisioniert dazu einen geteilten Markdown-Ordner unter `/srv/secondbrain/<slug>/` (mkdir + **lokales** `git init` ohne Remote + `index.md`-Gerüst). Das Brain ist ein **DB-verwalteter Mount-Eintrag**: es erscheint sofort (ohne `.env`-Edit/Neustart) im Mount-Permissions-Modal (ro/rw pro Person), in den Custom-Roles (`mount_labels`, Gruppen) und im Agent-Mount-Selector. Zugewiesene Agents mounten den Vault als `/mnt/brains/<slug>` und lesen/schreiben die `.md` mit ihren bestehenden File-Tools.
  - **Auto-Retrieval:** Bei zugewiesenem Brain weist die Agent-CLAUDE.md den Agent an, bei Support-/How-to-Fragen (z.B. Fehlercode `x17137`) **zuerst** den Vault per `grep`/`read_file` zu durchsuchen und die Antwort aus den gefundenen `.md` zu belegen.
  - **Datei-Historie:** lokales Git pro Vault + host-seitiger systemd-Timer (`deploy/secondbrain-autocommit.*`) für Auto-Commits → Diff/History/Rollback, kombiniert mit den vorhandenen `FILE_WRITTEN`-Audit-Events (wer/wann). Kein Remote, nichts verlässt den Server (DSGVO).
  - **Audit:** neue Event-Typen `BRAIN_CREATED` / `BRAIN_UPDATED` / `BRAIN_DELETED`.
  - Backend: `second_brains`-Tabelle + Migration, `brains`-API (CRUD), zentraler Katalog-Merge `get_effective_catalog` (env + DB) in Mount-Auflösung und Settings.
  - Wiederverwendet: vorhandenes Mount-System, `user_mount_access`, `custom_roles`, Agent-File-Tools, Audit-Framework — kein Scope-Umbau, kein semantischer Index (grep-basiert; pgvector als spätere Ausbaustufe vorgesehen).

## [1.55.36] — 2026-06-14

### Fixed
- **sendRichMessage double-serialization** — `rich_message` was passed as `json.dumps(...)` string and then re-serialized by httpx `json=data`, causing Telegram to receive a string instead of an object. Now passed as plain dict so httpx serializes the full structure correctly in one pass.

## [1.55.35] — 2026-06-14

### Added
- **Telegram Bot API 10.1 rich messages** — new endpoints `/send-rich-message` and `/send-rich-message-draft` wrapping `sendRichMessage` / `sendRichMessageDraft`. Accepts an array of `RichBlock*` objects (Paragraph, SectionHeading, Preformatted, Table, List, BlockQuotation, Map, Audio, Photo, Video etc.) and forwards them as `InputRichMessage` to Telegram. Blocks are validated server-side by Telegram.
- System prompt updated with rich message curl examples and all supported block types.
- **Agent Dockerfile** — `chmod -R a+rX /opt/agent/app/` added after COPY to fix PermissionError when macOS-sourced files have mode 700.

## [1.55.34] — 2026-06-12

### Changed
- **Per-channel Claude sessions** — iOS, Telegram and each webapp tab now get their own independent Claude Code session instead of sharing one. Messages from different channels no longer bleed into each other's conversation context.
- **Session resume after restarts** — Claude session IDs are persisted in Redis (7-day TTL). When the agent container restarts, each channel resumes its conversation via `--resume` automatically. iOS reconnects land in the same session without starting over.
- **Source-aware live steering** — mid-response message folding (`pending_drain`) now only folds messages from the same source channel; messages from other channels are re-queued correctly.
- **Cancel scoped to active channel** — the cancel signal now stops only the handler that is currently processing, not a shared handler.

## [1.55.33] — 2026-06-12

### Fixed
- **Telegram file uploads no longer fail for large files** — agents previously used `base64 -w0 file` shell substitution in curl JSON bodies, which hits Linux's ARG_MAX (~2 MB) and caused HTTP 500 for any file over ~500 KB. All file-sending endpoints now use multipart binary upload (`curl -F`) instead of base64 JSON.
- **50 MB file size support** — new multipart upload endpoints (`/send-document-upload`, `/send-audio-upload`, `/send-voice-upload`, `/send-photo-upload`, `/send-video-upload`) accept binary files up to Telegram's 50 MB API limit. Caddy reverse proxy explicitly permits 55 MB request bodies. Upload timeout raised to 120 s.
- **Proper audio player for MP3 files** — new `/send-audio-upload` endpoint uses Telegram's `sendAudio` method instead of `sendDocument`, so MP3/audio files appear with a native Telegram audio player (title, performer, seek bar) rather than as a plain file attachment.

### Changed
- `_tg_request` timeout is now configurable per call; file upload calls use 120 s, text calls keep 30 s default.
- System prompt updated — agents now use multipart curl commands for all file types. Base64 curl commands removed.

## [1.55.32] — 2026-05-27

### Fixed
- **Cloudflare tunnel flap-loop no longer goes undetected** — the `cloudflared` healthcheck now calls the local `/ready` endpoint via the metrics server instead of `tunnel info`, so autoheal restarts the container when edge connections drop. Caused today's 1033 outage on `agents.future-app.de`. Metrics port pinned to `20241` via `TUNNEL_METRICS` env; check runs every 30s. Tunnel profile only — community installs without `--profile tunnel` are unaffected.
- **Codex chat turns no longer get killed by the 10-minute watchdog** — `codex exec` legitimately runs longer than Claude Code on tool-heavy turns. New `CODEX_CHAT_TURN_TIMEOUT_SECONDS` (default 1800) and `CHAT_TURN_TIMEOUT_SECONDS` (default 600) settings; Codex agents use the higher default automatically.
- **Codex session state now survives container recreate** — the agent harness mount path is mode-aware: `codex_cli` binds the session volume at `/home/agent/.codex`, Claude Code keeps `/home/agent/.claude`.
- **Codex auth.json readable by the non-root agent user** — the shared auth file is now `chown`ed to the agent container UID/GID (default `1000:1000`, overridable via `AGENT_CONTAINER_UID`/`GID`) so Codex CLI can read it without world-readable permissions.

### Changed
- **Codex event extraction** — the runner now emits `tool_result` events (not just `tool_call`) and recognises `command_execution` payloads, so the chat UI reflects shell output from Codex turns.

### Verified
- `python3 -m py_compile agent/app/codex_runner.py agent/app/chat_consumer.py agent/app/config.py orchestrator/app/services/codex_auth_service.py orchestrator/app/services/docker_service.py` succeeds.
- 7/7 active agents recreated with new image via `AgentManager.update_agent` — volumes preserved, all healthy.
- `docker exec ai-employee-cloudflared cloudflared tunnel --metrics 127.0.0.1:20241 ready` returns exit 0; `curl https://agents.future-app.de/health` returns 200.

---

## [1.55.25] — [1.55.31] — 2026-05-27

Bridge-only release range; backfilled retroactively. No core orchestrator/agent changes.

### Added
- **Bridge voice interaction layer** (1.55.25 → 1.55.27) — compact interaction bar with voice mode plus Edge-TTS speech output.

### Fixed
- **Bridge session attach** waits for the orchestrator session to be ready before connecting (1.55.26).
- **Bridge WebSocket SSL** connection negotiation and startup logging (1.55.28, 1.55.29).
- **Bridge microphone privacy description** for macOS prompts (1.55.30).
- **Telegram bot startup** now retries after transient failures instead of hard-failing the orchestrator boot (1.55.31).

---

## [1.55.24] — 2026-05-27

### Fixed
- **OpenAI Codex agents no longer stall in chat** — Codex CLI subprocesses now run with stdin closed so `codex exec` cannot wait forever for additional terminal input inside agent containers.
- **Codex chat completion fallback** — WebSocket and background chat persistence now read final text from `text`, `content`, or `result`, so Codex `done` events still clear the client spinner and persist the assistant reply even if a streaming text event is missed.

### Verified
- `python3 -m py_compile agent/app/codex_runner.py agent/app/message_consumer.py orchestrator/app/api/ws.py orchestrator/app/main.py` succeeds.
- Direct container test: `codex exec --json ... "Bitte antworte nur mit OK" </dev/null` returns an `agent_message`.

---

## [1.55.23] — 2026-05-27

### Added
- **Claude security guidance plugin defaults** — the agent image now pins Claude Code `2.1.144`, and the repo ships project-level `.claude` settings, security guidance, and JSON custom patterns so fresh installs enable the official security-guidance plugin without relying on local machine state.

### Fixed
- **Scheduled `present_file` deliveries now reach chat history** — scheduler/task runs parse `present_file` MCP markers from both top-level `tool_result` events and Claude synthetic `user/tool_result` blocks, mirror files to the live chat channel, and persist scheduler-originated file messages in a visible `scheduler` chat session.
- **Scheduler file sessions are visible to apps** — chat session previews now include assistant-only file deliveries so iOS/Web clients can discover scheduled attachments after reopening.

### Verified
- `uv run --project agent --with pytest pytest agent/tests/test_present_file_marker.py -q` succeeds.
- `python3 -m py_compile agent/app/agent_runner.py orchestrator/app/main.py orchestrator/app/api/agents.py` succeeds.

---

## [1.55.22] — 2026-05-26

### Added
- **DB-backed Command Policy Engine shipped (#155)** — bash command governance now lives in `command_policies` with global rules plus per-agent overrides. Seeded defaults replace the old hardcoded `command_filter.py` pattern lists.
- **Command Policy UI** — admins can manage global policies under Approvals → Command Policies; agent detail settings now show inherited global rules and editable agent overrides.

### Changed
- **Bash enforcement moved into runtime execution** — `agent/app/tools/executor.py` checks DB policies before shell execution. `blocked` policies deny immediately; `medium` and `high` policies create an approval request and execute only after approval.
- **Bash approval MCP aligned with the same policy source** — the sidecar no longer imports the removed Python command filter and uses the orchestrator policy endpoint with agent-token auth.

### Verified
- `python3 -m py_compile orchestrator/app/api/command_policies.py orchestrator/app/models/command_policy.py agent/app/tools/executor.py`
- `cd orchestrator && uv run --with alembic alembic heads`
- `node --check agent/mcp/bash-approval-server.mjs`
- `uv run --project agent --with pytest pytest agent/tests/test_command_policies.py` — 2 passed.
- `cd frontend && npm run build`

---

## [1.55.21] — 2026-05-26

### Changed
- **GitHub issue cleanup groundwork merged** — brought the Trading Analyst template test coverage from `feat/issue-156-trading-agent-template` into `main`, so issue #156 can be closed against verified code.
- **Docker socket proxy security docs synchronized** — merged the documentation cleanup from `docs/issue-160-security-docs-docker-proxy`, removing stale custom `docker-proxy/allowlist.yml` guidance and documenting the current `tecnativa/docker-socket-proxy` plus `autoheal` socket behavior.

### Verified
- `uv run --project orchestrator --with pytest pytest orchestrator/tests/test_trading_template.py orchestrator/tests/test_task_steps.py` — 30 passed.
- Documentation scan no longer finds active `docker-proxy/allowlist.yml` guidance.

---

## [1.55.20] — 2026-05-26

### Fixed
- **Custom MCP tool schemas are normalized before reaching LLM providers** — MCP tools with missing, `null`, or non-OpenAI-compatible `inputSchema` values now fall back to a valid JSON Schema object. This fixes Azure/OpenAI errors like `Invalid schema for function 'mcp_MyBoardyMCP_web_search'`.

### Verified
- `python3 -m py_compile agent/app/tools/mcp_client.py`
- Live `MyBoardyMCP` discovery validates all 3 tool schemas as JSON object parameters.
- Rebuilt `ai-employee-agent:latest` and recreated `MyAzureAgent`.

---

## [1.55.19] — 2026-05-26

### Fixed
- **Custom HTTP MCP servers now support streamable HTTP handshakes** — the agent MCP client sends the required `Accept: application/json, text/event-stream` header, parses SSE responses, preserves `mcp-session-id`, and sends the initialized notification before listing or calling tools. This fixes `MCP init failed ... 406` for n8n/MyBoardy-style MCP endpoints.

### Verified
- `python3 -m py_compile agent/app/tools/mcp_client.py`
- Live discovery against `MyBoardyMCP` returns 3 tools.
- Rebuilt `ai-employee-agent:latest` and recreated `MyAzureAgent`; logs show `Discovered 3 custom MCP tools` with no 406.

---

## [1.55.18] — 2026-05-26

### Changed
- **App icon simplified further** — removed the blue connector strokes from the iOS app icon and web favicon for a cleaner Lucide-like mark.

### Verified
- `npm run build` succeeds for the Next.js frontend.

---

## [1.55.17] — 2026-05-26

### Changed
- **App icon simplified** — refreshed the iOS app icon and web favicon with the minimal chip/chat mark and removed the extra ready-dot accent.
- **Live chat steering copy clarified** — web chat now describes mid-turn messages as steering the current agent turn instead of implying the user must wait for the current task to finish.

### Verified
- `npm run build` succeeds for the Next.js frontend.

---

## [1.55.16] — 2026-05-26

### Changed
- **Agents page loads less JavaScript up front** — the heavy create-agent modal and network graph are now lazy-loaded only when opened/selected, reducing the `/agents` route bundle and making the page become interactive faster.

### Verified
- `npm run build` succeeds for the Next.js frontend.
- Public `/agents` route and `/api/v1/health` respond successfully after deployment.

---

## [1.55.15] — 2026-05-25

### Fixed
- **SSO Profile editing now uses the correct UI** — existing SSO profile secrets open with their type badge, read-only env-var name, JSON-friendly replacement textarea, and SSO-specific guidance instead of the old API-key-style single-line value field.

### Verified
- `npm run build` succeeds for the Next.js frontend.

---

## [1.55.14] — 2026-05-25

### Added
- **OpenAI Codex provider foundation** — adds Codex/ChatGPT OAuth provider metadata, device-auth service plumbing, a `codex_cli` runner path, migration coverage, and harness mapping tests so OpenAI subscription-backed agents can be wired alongside Claude Code.
- **Unified account/harness UX groundwork** — expands agent creation and settings API types so Anthropic, OpenAI/Codex, LM Studio, and related account modes can map to the correct container harness instead of being treated as generic API-key-only providers.
- **SSO Profile secret creation UI** — Key Management now has a first-class SSO Profile creation mode with dedicated copy, examples, JSON-friendly input, and automatic env-var naming such as `SSO_PROFILE_SUPABASE`.

### Changed
- **Assigned secrets remain container env vars** — assigned KMS secrets continue to be injected into agent containers by env-var name, with clearer UI guidance that agents should reference variables rather than expose secret values.
- **Chat and Telegram reliability polish** — improves channel prompts, message handling, websocket behavior, and Telegram file/audio flows so iOS/Web/Telegram chats behave more consistently during long-running agent turns.

### Fixed
- **Agent auth and file-delivery edge cases** — tightens OAuth/Codex setup paths, Telegram agent bot handling, and chat attachment/event handling after the iOS/Web/Telegram file-delivery work.

### Verified
- `python3 -m py_compile` succeeds for the touched agent and orchestrator modules.
- `npm run build` succeeds for the Next.js frontend.
- `python3 -m pytest orchestrator/tests/test_agent_harness_mapping.py -q` was attempted but local `pytest` is not installed on this machine.

---

## [1.55.13] — 2026-05-24

### Fixed
- **GitHub PAT aliases are now injected at container level** — assigned `GIT_PAT`, `GH_TOKEN`, or `GITHUB_TOKEN` secrets are normalized to both `GH_TOKEN` and `GITHUB_TOKEN`, so `gh`, shell commands, git helpers, and the agent process all see the same token.

### Verified
- `python3 -m py_compile orchestrator/app/core/agent_manager.py` succeeds.

---

## [1.55.12] — 2026-05-24

### Fixed
- **GitHub PAT secrets are now recognized even when named `GIT_PAT`** — agent startup maps `GITHUB_TOKEN`, `GH_TOKEN`, or `GIT_PAT` into the GitHub CLI/git auth setup.
- **Secret assignment applies immediately to running agents** — assigning, unassigning, deleting, or rotating an active secret now refreshes affected agent containers automatically so the new environment is available without a manual update.

### Verified
- `python3 -m py_compile agent/app/main.py orchestrator/app/api/secrets.py` succeeds.

---

## [1.55.11] — 2026-05-23

### Fixed
- **OpenAI/Azure-compatible streaming cost tracking now requests usage metadata** — chat completions streams send `stream_options.include_usage = true`, so per-turn token accounting and budget meters can use the provider-reported final usage chunk instead of undercounting streamed calls.
- **Usage fallback stays compatible with local/OpenAI-compatible backends** — if a backend rejects `stream_options`, the provider retries without it instead of failing the chat.

### Verified
- `python3 -m py_compile agent/app/providers/openai_provider.py` succeeds.

---

## [1.55.10] — 2026-05-22

### Added
- **New AI-Employee web app icon assets** — added the generated agent/voice icon as Next.js `icon.png`, `apple-icon.png`, and a small favicon.

### Verified
- `npm run build` succeeds for the Next.js frontend and includes the new icon routes.

---

## [1.55.9] — 2026-05-22

### Fixed
- **Live voice STT now defaults to German** — voice sessions use `de` when no language is supplied, avoiding Whisper auto-detect drifting into English on short German utterances.

### Changed
- **Webapp voice sessions now send the configured voice language** from `/settings/voice` with every commit.
- **Voice settings language field now documents `de` as the default** and allows `auto` when automatic detection is explicitly wanted.

### Verified
- `python3 -m py_compile` succeeds for the touched orchestrator modules.
- `npm run build` succeeds for the Next.js frontend.

---

## [1.55.8] — 2026-05-22

### Added
- **Webapp chat audio attachments now render as voice bubbles** — audio files presented by agents get a play/pause control, waveform-style progress, current time/duration, and a download button instead of a generic attachment card.

### Verified
- `npm run build` succeeds for the Next.js frontend.

---

## [1.55.7] — 2026-05-22

### Changed
- **Audio deliverables are now treated as first-class chat attachments** — the automatic `/workspace/...` attachment detector recognizes `.mp3`, `.m4a`, `.wav`, `.ogg`, `.opus`, `.aac`, and `.flac` files.
- **Agent instructions now explicitly include audio files/voice notes in the `present_file` deliverable flow** so generated TTS files are presented in iOS/Web/Telegram instead of only being mentioned as paths.

---

## [1.55.6] — 2026-05-22

### Added
- **Agents can now inspect their inter-agent inbox and conversations** — new `list_agent_messages` and `get_agent_conversation` tools let agents answer questions like "did another agent contact you?" from the real `agent_messages` history instead of guessing from tasks or memory.

### Changed
- **Team message APIs now accept agent authentication** — `/agents/team/messages` and `/agents/team/conversation` work for authenticated agents and restrict agent callers to conversations involving themselves.
- **Inter-agent replies now persist structured metadata** — replies published by `MessageConsumer` include a unique `message_id`, `message_type=response`, and `reply_to`, and the orchestrator persists those fields.

### Fixed
- **`list_team` status display now uses `state` from the team directory** instead of showing undefined status in the MCP output.

---

## [1.55.5] — 2026-05-22

### Changed
- **`send_message_and_wait` now handles busy target agents explicitly** — `/agents/{id}/message` returns `deferred` metadata when the target agent is currently working on a task, and both Claude-Code MCP and custom-LLM tool clients return immediately with a queued-message notice instead of appearing to hang for 45 seconds.

### Fixed
- **Inter-agent messages are no longer confusing when the recipient is busy** — messages still land in the recipient's pending inbox, but the sending agent can now tell the user that the reply will arrive later.

---

## [1.55.4] — 2026-05-22

### Changed
- **Agent-auth callers now use a centralized `AgentPrincipal` marker** — endpoints no longer rely on ad-hoc `role == "agent"` string checks to distinguish agents from users. Team directory, inter-agent messaging, schedules, tasks, memory, and computer-use APIs now use the same `is_agent_principal()` helper.

### Fixed
- **Team directory access remains open to authenticated agents without leaking user-only filters** — the `list_team` fix from v1.55.3 is now implemented through the shared principal helper instead of a one-off endpoint condition.

---

## [1.55.3] — 2026-05-22

### Fixed
- **Agents can see their live team again via `list_team`** — agent-authenticated calls to `/agents/team/directory` were accidentally treated like non-admin user calls and filtered by `user_id == agent_id`, which returned an empty roster. Agent token requests now bypass that user-access filter, so iOS, Telegram, and MCP calls report the actual team directory again.

---

## [1.55.2] — 2026-05-22

### Fixed
- **Generated files are now surfaced as chat attachments even when an agent forgets `present_file`** — the chat stream detects valid `/workspace/...` deliverable paths in final responses, verifies the file in the agent container, and adds it to `presented_files` so iOS/Web chat can show a downloadable attachment.
- **Agent instructions now explicitly require `present_file` for PDFs and other deliverables** instead of only mentioning `/workspace/transfer/...` paths in text.

---

## [1.55.1] — 2026-05-22

### Fixed
- **iOS chat reconnect handshake** — the chat WebSocket now sends a `ready` event immediately after accept so the iOS app can confirm the connection instead of staying in a stale `Reconnecting...` state.
- **Voice upload diagnostics** — voice WebSocket chunk/commit handling now logs upload and transcription progress, including crashes in background voice turns.
- **Chat history rendering** — chat history returns stable per-row IDs and normalizes serialized tool-call input so assistant messages and tool calls render correctly after app restart.

---

## [1.55.0] — 2026-05-22

### Added
- **Native iOS push notifications via APNs** — users can register device tokens, and notifications can now fan out through APNs in addition to Telegram/in-app channels.
- **Channel-aware chat and notification routing** — chat messages now carry their origin (`ios`, `telegram`, `webapp`, voice), the agent prompt includes that context, and `notify_user` can target iOS, Telegram, Webapp, or all channels.
- **Full approval request integration** — agents can ask structured approval questions with options, notifications carry the approval metadata, and Telegram / iOS / Webapp responses update the underlying approval record.
- **Files and PDFs can be presented directly in chat** — agents can create workspace files and expose them as downloadable chat attachments via the new file presentation flow.
- **Live voice session pipeline** — voice sessions use a dedicated WebSocket, STT/TTS provider layer, compact audio uploads, status events, and timeout handling so the client no longer sits forever at "Audio wird verarbeitet".

### Changed
- **Agent chat reliability** — chat turns now have a watchdog timeout so a hung CLI/model call does not block the agent queue indefinitely.

---

## [1.54.2] — 2026-05-20

### Fixed
- **Memory-MCP labelled successful semantic search results as "semantic unavailable"** — the MCP server checked `mode === "semantic"`, but the orchestrator's semantic-search endpoint returns `"semantic_reranked"` on success. Every semantic hit was therefore mislabelled as keyword/fallback, leading agents to wrongly conclude the embedding service was down (the actual similarity scores were genuine cosine values from bge-m3 — the search worked, only the badge was wrong). The check now matches any `semantic*` mode.

---

## [1.54.1] — 2026-05-18

### Fixed
- **embedding-service build pulled ~2 GB of unused NVIDIA CUDA libraries** — `requirements.txt` had a bare `torch>=2.6.0`, so on Linux pip installed the default CUDA-enabled PyTorch wheel. The service runs CPU inference only and never uses the GPU stack. torch is now installed from the CPU-only PyTorch index in the Dockerfile (`--index-url https://download.pytorch.org/whl/cpu`): the image drops from ~4–5 GB to ~1.6 GB and the build is dramatically faster. This also removes the disk/build pressure that could make a parallel `docker compose build` of other services (e.g. the frontend) fail on a fresh clone.

---

## [1.54.0] — 2026-05-17

### Added
- **Skill self-improvement is now a review flow, not a silent overwrite** — when the improvement engine finds a skill with low helpfulness ratings, it no longer dispatches a task that overwrites the skill directly. It generates a rewritten version via the LLM and stores it as a *proposal* (`improvement_status = "pending_review"`, with the old and suggested content side by side). A new **Verbesserungen** tab in the Skill Marketplace shows pending proposals with a before/after diff and Approve / Reject buttons. Approving applies the new content, snapshots the old version for rollback, and starts the existing A/B probation validation; rejecting discards it. Works for imported skills with no assigned agent too (they no longer fall through). New `skills` columns + migration; engine reworked; `GET /skills/marketplace/improvements/pending` and approve/reject endpoints.
- **Time-travel replay for tasks (issue #54)** — task execution events were live-only Redis pub/sub and lost once a task finished. A new `task_steps` table now persists every step (a background consumer on `agents:logs:all` writes one row per event with a per-task sequence). The task detail page gained a **Schritt-Replay** panel: load the recorded steps and scrub through the execution step by step with a slider. New `GET /tasks/{id}/steps` endpoint.
- **Vertical onboarding packs (issue #159)** — a new `/onboarding` wizard lets a user pick an industry starter kit (Entwickler-Team, Content-Studio, Support-Desk) and provision a whole ready-to-work environment in one step: it creates one agent per template in the pack, assigns the templates' skills, seeds knowledge-base entries, and queues a first demo task. New vertical-packs API (`list` / `preview` / `provision`) and a provisioner service.

### Changed
- **Central model registry (issue #161)** — context-window sizes and token pricing were duplicated across `llm_runner.py` and `llm_chat_handler.py` and had already drifted. Both now resolve from a single `model_registry` module (longest-substring match, so dated model variants resolve correctly). Adding a new model is now a one-line change in one place.

---

## [1.53.0] — 2026-05-17

### Added
- **Agents can generate and present visuals** — a new `present_image` tool lets a custom-LLM agent show the user an image it created or processed. The agent generates the file (e.g. a short matplotlib/Pillow script saving a `.png` into the workspace), then calls `present_image` with the path: the image is streamed to the chat UI as a dedicated `image` event and rendered inline (click to zoom), and `send_telegram=true` additionally delivers it as a Telegram photo (reusing the per-agent `send_telegram` channel — no chat-id plumbing needed). Presented images are persisted in the message metadata so they survive a chat reload. The agent container now ships `matplotlib`, `Pillow` and `numpy` (headless `Agg` backend); the system prompt tells the agent how and when to use the tool.

---

## [1.52.0] — 2026-05-17

### Fixed
- **Chat costs are no longer always $0** — the custom-LLM chat handler never accumulated per-turn token usage and hard-coded `cost_usd = 0`. It now sums input/output tokens across every turn of a message and computes the real cost via the shared pricing table. `chat_messages` gained `cost_usd` / `input_tokens` / `output_tokens` columns (migration), the WebSocket layer persists them, and the analytics overview now aggregates chat spend alongside task spend (`total_cost_usd` is task + chat; `total_task_cost_usd` / `total_chat_cost_usd` give the breakdown). The chat UI's MetaBar shows token counts per reply.
- **`send_telegram` tool now actually delivers** — the agent published proactive Telegram messages to the Redis channel `telegram:send`, which nothing subscribed to (dead channel), and only ever sent a file *path* string the orchestrator could not read. Messages now go to the per-agent channel `agent:{id}:telegram:send`; the agent's Telegram bot subscribes and delivers to every authorized chat. Files are read and base64-encoded by the agent, so photos and documents arrive as real attachments. Delegated-task notifications from the task router were rerouted onto the same per-agent channel.

### Removed
- **Dead `task_logs` table** — the table and its `TaskLog` model were never written to or read from. Removed the model and added a migration that drops the table.

### Changed
- **`AgentTemplate.skill_ids` is now fully wired** — templates could carry `skill_ids` (and auto-assign those skills to agents created from them), but the field was missing from the template create/update API and from the builtin-template startup sync, so changes never propagated. Both gaps are closed (`mcp_server_ids` was added to the sync list too).

---

## [1.51.0] — 2026-05-17

### Changed
- **Custom-LLM harness reliability (issue #161, part 2) — file-state tracking** — the custom-LLM tool executor now tracks which files the agent has read. `edit_file`, `multi_edit` and `write_file` refuse to modify an existing file the agent never read, and refuse a file that changed since it was last read (stale-read detection via mtime) — the agent is told to `read_file` it (again) first. `read_file` and every successful write record the file's state, so normal read→edit flows are unaffected. Tool descriptions updated so models comply proactively. Prevents blind overwrites — the model can no longer clobber a file it hasn't seen.

---

## [1.50.0] — 2026-05-17

### Changed
- **Custom-LLM harness reliability (issue #161, part 1)** — two harness behaviours that were prompt-only are now enforced in code:
  - **Loop detection in the task runner** — the autonomous task runner now stops when the same tool call repeats (shared `LoopDetector`, also used by the chat handler — duplicate logic removed). Previously only the chat handler caught loops; long tasks could spin until the turn cap.
  - **Post-turn compliance gate** — when a task finishes, the runner checks in code that the mandatory closing steps actually happened (`rate_task`; `skill_rate` if a skill was installed). If a (weak) model skipped them, it gets one bounded corrective nudge instead of the step being silently lost.
- **Anthropic prompt caching** — the system prompt and tool definitions (large, static, re-sent every turn) now carry `cache_control` breakpoints. Multi-turn tasks no longer re-pay for the static prefix — notable cost and latency reduction.

---

## [1.49.0] — 2026-05-17

### Added
- **Voice-first agent** — a Telegram voice message now gets a *spoken* reply: the agent's text answer is auto-converted to speech (tts-service) and sent back as a voice message. The originating voice message is flagged in Redis (`voicereply:{msg_id}`); the response listener TTS-es the full turn on completion. The agent is told (prompt) to answer concisely and Markdown-free when spoken to, so the reply sounds like a colleague on the phone. Text reply is still sent too (keeps links/code); TTS failure never breaks it.

---

## [1.48.0] — 2026-05-17

### Changed
- **Admin functions consolidated into the Admin-Konsole** — Settings, AI-Accounts, Key Management, Health and Audit Log are now tabs *inside* the Admin-Konsole instead of six separate sidebar entries. The ADMIN sidebar group is a single "Admin-Konsole" item. The standalone routes (`/settings`, `/ai-accounts`, …) still work for deep links; each page takes an `embedded` prop that drops its own header when rendered as a tab.
- **GitHub-star nudge throttled to once per day** — the "Star on GitHub" sidebar item highlights (gentle pulse) at most once per calendar day instead of being styled on every visit. Tracked in `localStorage`.

---

## [1.47.0] — 2026-05-17

### Added
- **Skill usage tracked in chat sessions** — agents are now instructed to `skill_search` the marketplace *before* responding to a chat message (Web UI + Telegram), `skill_install` and follow a matching skill instead of improvising, and — once the user gives feedback — call `skill_rate` with a `user_rating` interpreted from the user's words. Previously the whole "check marketplace → use → track → rate" loop only ran for Tasks.
- `SkillTaskUsage` now supports chat usage: `task_id` is nullable, with new `chat_session_id` and `source` (`task`/`chat`) columns. The `/skills/agent/record-usage` endpoint no longer writes a bogus `"manual"` `task_id` (which violated the FK and 500'd); chat usages are upserted by most-recent-within-24h so a follow-up rating updates the same row. Alembic migration `c1d2e3f4g5h6`.

### Fixed
- **Analytics chart tooltip showed counts as decimals** — the Task-Volumen tooltip rendered every number with `toFixed(2)`, so a task count of 2 displayed as `2.00`. Integers now show without decimals; floats (cost) keep two.
- **Duplicate "Admin" entry in the sidebar** — the expanded sidebar showed both the "Admin-Konsole" item in the ADMIN group and a redundant standalone "Admin" link above the user menu. Removed the standalone one.

---

## [1.46.0] — 2026-05-17

### Added
- **Local voice transcription (STT)** — new `stt-service` container running faster-whisper (`small` model, CPU/int8, free & offline, no API key). Telegram voice/audio messages are now transcribed by the orchestrator *before* they reach the agent: the agent receives the plain-text transcript in the message, instead of a raw `file_id` it would flail to decode with ffmpeg/curl. Wired into the per-agent Telegram bot's media handler; falls back gracefully to a `get-file` hint if the STT service is unreachable.
- **Multimodal capability note in the agent system prompt** — every custom-LLM agent's system prompt now states that it can see images (use `view_image`, never OCR/`strings`) and that Telegram photos/voice are pre-processed. Stops agents from flailing with shell tricks instead of using their real vision.

### Fixed
- **Changelog modal unreadable in light mode** — the About/Changelog dialog hard-coded the `prose-invert` (dark) typography theme, so inline `code` spans rendered as near-white text and were invisible on the light background. Now `dark:prose-invert` with explicit code styling that works in both themes.

---

## [1.45.0] — 2026-05-17

### Added
- **Multimodal vision for custom-LLM agents** — the hand-built agentic runtime can now actually *see* images, not just text. New `view_image` tool loads an image (workspace path, Telegram `file_id`, or URL) and shows it to the model directly — no more OCR/`strings` fallbacks. All four providers render real image content blocks: Anthropic (image inside `tool_result`), OpenAI/Azure chat (`image_url` parts), OpenAI Responses API (`input_image`), Google Gemini (`inlineData`).
- **Telegram photos handed to the agent directly** — when a user sends a photo (or an image document), the orchestrator downloads it and attaches it to the chat message as a vision image. The agent sees it immediately, with no tool call or token round-trip.
- **Paste images into the Web UI chat** — `Ctrl+V` a clipboard image into the chat input; a thumbnail strip shows pending images (removable), and they are sent alongside the text for multimodal models to analyze. Images are rendered inline in the user's message.

---

## [1.44.0] — 2026-05-17

### Added
- **AI Accounts** — reusable, admin-managed LLM model accounts. An admin creates an account once (provider, endpoint, encrypted API key, Azure api-version) under `/ai-accounts`; agents then connect to it instead of carrying an inline `llm_config`. An account exposes **multiple models** (for Azure OpenAI: the deployment names) and the agent picks one when it connects. New `ai_accounts` table + `agents.ai_account_id` FK, admin CRUD API `/ai-accounts`, `PATCH /agents/{id}/ai-account` to (re)connect an agent. The create-agent modal offers an "AI-Account" + model dropdown for custom-LLM agents. Provider-agnostic: azure-openai, openai, anthropic, google, ollama, lm-studio.

### Fixed
- **GPT-5.x via Responses API** — the OpenAI-compatible provider now routes the GPT-5.x model family (incl. Azure deployments named accordingly) to the `/responses` endpoint, not `/chat/completions` — previously only `codex` models were detected.
- **Agent cost tracking** — `agent_runner` now reads `total_cost_usd` and the `usage` token counts from the Claude CLI result (previously read the non-existent `cost_usd`), so the budget bar and per-task token stats actually populate.
- **IdleStop scheduler crash** — the idle-stop sweep constructed `AgentManager` without its required `redis` argument and threw every cycle.

---

## [1.43.0] — 2026-05-16

### Added
- **Per-agent monthly API budget** — agents now have a monthly USD budget cap that resets on the 1st. When the budget is exhausted the agent follows a configurable `budget_exceeded_action`: `haiku` downgrades all tasks to the cheap fallback model (Sparmodus), `stop` blocks new tasks and stops the container. Selectable in the create-agent modal and shown as a live budget bar + badge on the agent card and detail page.
- **Per-user monthly spend cap** — `user.budget_usd` caps total spend across all of a user's agents; when exceeded each agent applies its own `budget_exceeded_action`. Settable via `PUT /roles/users/{user_id}/budget` (admin).
- Budget cost is computed from real per-task `cost_usd` summed over the current calendar month, not estimates.
- **Grouped agent tabs** — the agent detail view's 12 tabs are consolidated into 6 groups with sub-reiter: Chat · Todos · Activity (Live/Verlauf) · Workspace (Files/Apps/Computer-Use) · Wissen (Knowledge/Memory/Skills) · Settings (Allgemein/Integrations).

### Fixed
- **`/tasks/cost-attribution` 404** — the static route was registered after `/tasks/{task_id}` and got captured as a task ID. Moved above the parametrized route so the dashboard cost panel loads.

---

## [1.42.0] — 2026-05-14

### Added
- **Admin role editor** — `/admin` now has a Rollen tab for creating/editing custom roles, assigning roles to users, and configuring max agents, allowed templates, AI/model providers, mountshares, URL host patterns, and menu paths.
- **Frontend menu filtering** — the sidebar now reads `GET /roles/me/permissions` and hides menu entries not allowed by `role.permissions.menu_paths`.
- **Role enforcement for URLs and mounts** — URL checks now apply `url_host_patterns` from the agent owner's effective role, and mount catalog visibility/assignment honors `mount_labels`.

### Fixed
- **Mount RO/RW enforcement** — per-user mount grants now persist the effective mount mode on the agent config (`mount_modes`) and Docker restarts apply the stricter mode, so a user granted `ro` cannot receive a `rw` bind mount just because the global catalog is `rw`.
- **Roles API routing** — static routes like `/roles/users/{user_id}/assign` and `/roles/me/permissions` are registered before `/{role_id}` so authenticated requests cannot be captured by the dynamic route.
- **Enum role coverage** — admin user creation/update now accepts all built-in roles (`admin`, `manager`, `member`, `viewer`) and protects the last admin across all demotions.

---

## [1.41.1] — 2026-05-14

### Fixed
- **Fresh install migrations** — repaired the Alembic revision graph after `v1.41.0` introduced a second head and reused the historical `c3d4e5f6g7h8` revision id. New installations can now create tables from SQLAlchemy models, stamp the single head, and continue with `alembic upgrade head` cleanly.
- **Alembic head ambiguity** — `alembic heads` now resolves to exactly one head: `p1b2b2b2b2b2`. Existing installations can run `alembic upgrade head` without the previous "Multiple head revisions" failure.

---

## [1.41.0] — 2026-05-13

### Added
- **Mount-Permissions pro User** — neue Tabelle `user_mount_access` mit `(user_id, mount_label, mode=ro|rw)`. SuperAdmin grantet per User welche Mounts aus `AGENT_MOUNT_CATALOG` zugänglich sind. Non-Admins beim Agent-Erstellen werden nur ihre erlaubten Mounts gezeigt; Versuch eine andere zuzuweisen → 403. Endpoints: `GET/PUT /settings/agent-mounts/access/{user_id}`. Admin-UI: neuer Box-Icon-Button in der User-Liste öffnet ein Modal mit RO/RW/None-Toggle pro Mount.
- **Auto-Stop Idle Agents** — SuperAdmin setzt globalen `max_idle_minutes` (PlatformSettings). User dürfen pro Agent kürzere Werte setzen, niemals länger als das globale Maximum. Worker im Scheduler prüft alle 5 min, stoppt überfällige Agents. Endpoints: `GET/PUT /settings/idle-stop`, `PATCH /agents/{id}/idle-stop`. Admin-UI: Panel auf dem Budget-Tab im `/admin`. Defaults: 0 = deaktiviert.
- **Custom Roles & RBAC-Permissions** — neue Tabelle `custom_roles` (id, name, description, permissions JSON, is_system). `users.custom_role_id` optionaler Override über das alte Enum. Permissions-Shape: `{max_agents, template_ids, llm_providers, mount_labels, url_host_patterns, menu_paths}` — `null` = unbeschränkt, `[]` = alles verboten. Resolver in `app/core/permissions.py` priorisiert: Admin-Enum > Custom-Role > Enum-Defaults. Backend-Checks aktiv beim Agent-Erstellen (max_agents, LLM-Provider) und Template-Instanziieren (template_ids, max_agents). Endpoints: `GET/POST/PUT/DELETE /roles/`, `PUT /roles/users/{user_id}/assign`, `GET /roles/me/permissions`.

### Fixed
- **Speicher-Anzeige Bug** — `agent.disk_usage_mb` zeigte den gesamten Container-Filesystem-Verbrauch (inkl. bind-mounts) statt nur `/workspace`. Außerdem rechnete `disk_percent` mit `max(limit, total)` als Nenner → bei Mounts mit großem Host-Volume kam ein absurd kleiner Prozentwert raus (z.B. "46.4 GB / 10 GB = 5%"). Fix: `du -sm /workspace` statt `df`, Prozent gegen das konfigurierte Quota-Limit gerechnet (mit 100% Cap).
- **Files-Tab UX** — Upload-Button erschien erst on Hover (Customer-Feedback). Ist jetzt durchgehend sichtbar (primary-getönt). Rechte Seite mit "Datei auswählen" war als Drop-Zone missverstanden — jetzt deutlich als "Vorschau-Bereich" beschriftet mit Hinweis auf den Upload-Button.

### Deferred (für v1.42)
- Admin-UI für Custom Roles (Create/Edit-Modal, User-Role-Assignment-Dropdown) — Backend komplett & getestet, kann derzeit nur via API genutzt werden
- Menu-Filtering im Frontend basierend auf `role.permissions.menu_paths`

---

## [1.40.2] — 2026-05-12

### Fixed
- **memory_list 403 für Agents** — Custom-LLM-Agents (und Claude-Code-Agents im API-Modus) konnten ihre eigenen Memories nicht auflisten, weil `GET /memory/agents/{agent_id}` `user.id` (= agent_id wenn vom Agent gerufen) gegen `agent.user_id` (= echte User-UUID) verglichen hat → 403 "Access denied". Jetzt: Role "agent" wird separat erkannt — Agents dürfen ihre eigenen Memories listen wenn `user.id == agent_id`.

### Changed
- **`CLAUDE.md` → `AGENT.md`** für Custom-LLM-Agents — der Dateiname `CLAUDE.md` ist Claude-Code-Konvention und für GPT/Gemini/Llama-Agents irreführend. Custom-LLM-Container bekommen jetzt `/workspace/AGENT.md` (modell-agnostisch). Claude-Code-Agents behalten `CLAUDE.md` wegen CLI-Konvention. Beim Update bestehender Custom-LLM-Container wird die alte `CLAUDE.md` einmalig entfernt.

---

## [1.40.1] — 2026-05-11

### Fixed
- **Setup-Skript Fernet-Key-Bug** — `scripts/setup.sh` hat einen ungültigen `ENCRYPTION_KEY` erzeugt (`base64.urlsafe_b64encode(32 bytes) + '='` → 45 statt 44 Zeichen). Folge: jede Secret-Speicherung (API-Keys, OAuth-Tokens, Azure-Endpunkte) failte mit `"Fernet key must be 32 url-safe base64-encoded bytes."` Jetzt: `Fernet.generate_key()` (canonical) + Validierung des bestehenden Keys → ungültige werden automatisch regeneriert (mit Warnung).
- **Encryption-Service auto-recovery** — wenn `ENCRYPTION_KEY` aus dem env-File ungültig ist, fällt der Orchestrator nicht mehr auf 500-Errors, sondern loggt einen klaren Hinweis und nutzt den persistierten `/app/data/.encryption_key` (oder generiert einen neuen). Verhindert dass Customers mit cryptischen Fehlern im UI stranden.

---

## [1.40.0] — 2026-05-11

### Added
- **Start All Button** — Pendant zum Stop All auf der Agents-Seite: startet alle gestoppten/error-state Agents in einem Klick (emerald-grün, mit Play-Icon). Wird nur angezeigt wenn es mindestens einen startfähigen Agent gibt. Confirm-Modal vor der Bulk-Aktion.

### Fixed
- **Agent-Delete 500-Bug** — `DELETE /agents/{id}` hat mit 500 fehlgeschlagen wenn der Agent Tasks oder Ratings hatte. Root cause: `tasks.agent_id` + `task_ratings.agent_id` haben FKs zu `agents.id` ohne `ON DELETE`. Fix: `remove_agent()` setzt jetzt `tasks.agent_id=NULL` (Task-Historie bleibt erhalten) und löscht `task_ratings` vor dem Agent-Delete.
- **Agent-Delete Error-Reporting** — bisher hat der Endpoint nur `ValueError` als 404 gefangen, alles andere wurde stillschweigend zu 500 ohne Detail. Jetzt: alle anderen Exceptions werden mit Stacktrace geloggt und der API-Response enthält `{detail: "TypeName: message"}` — Frontend kann eine sinnvolle Toast-Nachricht zeigen.
- **CHANGELOG-Update ohne Rebuild** — `/api/v1/version/changelog` liest jetzt zuerst aus lokalem File (3 Pfad-Kandidaten), fällt erst dann auf GitHub zurück. `CHANGELOG.md` ist außerdem als read-only Volume im docker-compose gemountet — Changelog-Updates erscheinen sofort ohne Orchestrator-Rebuild.

---

## [1.39.0] — 2026-05-11

### Changed
- **Native Browser-Dialoge ersetzt** — alle `alert()` und `confirm()` durch designte Modals: 30 Stellen in 12 Files migriert. Neue `DialogProvider`-Komponente am Root mountet ein globales Confirm-Modal + Toast-System; Verwendung über `useConfirm()` und `useToast()`-Hooks.
- **Confirm-Modal Varianten**: `destructive` (rot, Trash-Icon — für Lösch-Bestätigungen), `warning` (amber, AlertTriangle — für Stop/Update-Bulk-Aktionen), `default` (primary — für generische Bestätigungen). Auto-Focus auf Confirm-Button, Cancel via ESC/Click-Outside.
- **Toast-System**: 4 Varianten (info/success/warning/error), bottom-right positioniert, Auto-Dismiss nach 5s (8s bei Errors), klickbar zum frühen Schließen. Stacking via framer-motion layout-Animation.

### Files migrated
- Destructive confirms (11): user/agent/feedback/file/template/license/MCP/knowledge/meeting-room/integration/assignment delete
- Warning confirms (4): bulk agent stop, bulk update, single update, version-update
- Error toasts (10): replace `alert("Error: ...")` patterns across tasks, admin, agents, files, meeting-rooms, triggers
- Info alerts (3): JSON validation, generic errors

---

## [1.38.0] — 2026-05-10

### Fixed
- **Semantische Suche fällt nicht mehr auf Keyword zurück** (DevAgent-Feedback P0): zwei Bugs gefixt:
  1. `embedding_service._check_local_available()` cachte `False` permanent — jeder transiente Fehler (z.B. erste 10s nach Boot, während bge-m3 lädt) hat semantische Suche bis zum Orchestrator-Restart deaktiviert. Jetzt: TTL-Cache (30s), state-transition logging, expliziter Warning beim Fallback.
  2. `_brain_search()` ignorierte semantische Suche für Admin-User (user_id=None). Jetzt: Embedding läuft unabhängig vom User, SQL-Filter ist optional.
- **Embedding-URL konfigurierbar** via `EMBEDDING_SERVICE_URL` env (Override für Self-Hosting).
- **Embedding-Stats** verfügbar via `EmbeddingService.stats` (für Health-Endpoints): successes, fallbacks, last_checked, available.

### Changed
- **TodoWrite Spam entfernt** (DevAgent-Feedback): `runner_hooks.py` zwingt Agents nicht mehr `TodoWrite` aufzurufen. Hinweis ergänzt: für persistente Tracking nur platform-eigene `create_todo`/`update_todos` nutzen, nicht Claude Codes session-only TodoWrite.
- **CronCreate-Warnung** in `agent/claude-global.md`: explizite Anweisung `create_schedule` statt `CronCreate` zu nutzen, da letzteres session-only ist und Agents-Schedules permanent sein müssen.
- **Skill-Lokation klargestellt** in `agent/claude-global.md`: lokale Skills nach `/workspace/.claude/skills/`, neue Skills für Marketplace via `skill_propose`.
- **`.claude/settings.json`** im Repo um `.claude/skills` und `.agents/skills` in `additionalDirectories` erweitert (Developer-UX beim Arbeiten am Repo).

### Added
- **Setup-Skript wartet auf Embedding-Service** (`scripts/setup.sh`): nach Orchestrator-Health prüft das Skript jetzt auch `embedding-service:8001/healthz` (bis 4 min Timeout). Beim ersten Boot lädt bge-m3 ~2.3 GB Modell — User sieht jetzt expliziten Hinweis statt stiller "unavailable".
- **`.env.example`**: optionaler Override `EMBEDDING_SERVICE_URL` dokumentiert.

---

## [1.37.0] — 2026-05-10

### Added
- **Brain CRUD vereinheitlicht** — Brain MCP-Server bietet jetzt vollständiges 7-Tool-Set: `brain_search`, `brain_contribute`, `brain_get`, `brain_list`, `brain_update`, `brain_delete`, `brain_related`. Custom LLM Agents bekommen exakt dieselben 7 Tools über `definitions.py` + `api_client.py` — eine Tool-API, beide Modi.
- **Neue Brain-API-Endpoints** — `GET /brain/agent/list` (paginated), `GET /brain/agent/get/{id}`, `PUT /brain/agent/update/{id}` (re-embed + re-link), `DELETE /brain/agent/delete/{id}` (entfernt auch BrainLinks), `GET /brain/agent/related/{id}`. Alle scoped auf den User des Agents.

### Changed
- **Knowledge MCP-Server entfernt** — `knowledge-server.mjs` gelöscht. Alle Agent-Prompts (runner_hooks, agent_templates, message_consumer, chat_consumer) referenzieren jetzt `brain_*` statt `knowledge_*`.
- **Autonomy-Mapping** — `brain_contribute`, `brain_update`, `brain_delete` fallen unter Kategorie `knowledge_write` für L3-Whitelist. Read-Tools (`brain_search`, `brain_get`, `brain_list`, `brain_related`) sind in `ALWAYS_ALLOWED_TOOLS` und `CONCURRENT_SAFE_TOOLS`.

### Deprecated
- `/knowledge/agent/write`, `/knowledge/agent/search`, `/knowledge/agent/read/{title}` — funktionieren weiterhin, aber Agents sollen `brain_*`-Tools nutzen. Endpoints werden in 1.38 entfernt.

---

## [1.36.0] — 2026-05-10

### Added
- **Second Brain — Knowledge Graph (Obsidian-Style)** — Vollständig überarbeitete Graph-Ansicht im Obsidian-Stil: kleine, flache Node-Punkte (3–16px je nach Verbindungsanzahl), subtile graue Edges als Verbindungs-Web, dichte Force-directed Layout. Cluster entstehen natürlich durch Physik, nicht durch gezeichnete Bubbles.
- **Reading Panel** — Klick auf einen Node öffnet ein absolut positioniertes Reading Panel rechts (320px breit) mit gerendertem Markdown, Tags, Backlinks und Edit-Button. Der Graph bleibt sichtbar und ändert seine Größe nicht. `[[Backlinks]]` im Panel sind klickbar und navigieren ohne Reset zwischen Einträgen.
- **Tag-Legende mit Filter** — Bottom-Left Legende zeigt die Top-10 Tags mit Farbpunkt und Eintragsanzahl. Klick auf einen Tag dimmt alle Nicht-Match-Nodes und öffnet ein Seitenpanel mit den Einträgen dieser Gruppe. Entry-Labels werden für gefilterte Nodes sichtbar.
- **Zoom-to-Cursor** — Mausrad-Zoom (0.15×–4×) zentriert auf die Cursor-Position wie in Figma/Obsidian, nicht mehr auf den Ursprung. Drag-to-Pan auf dem SVG-Hintergrund.
- **Semantische Brain Links** — `BrainLink`-Modell + `auto_link`-Service verbindet Knowledge Entries automatisch via Cosine-Similarity (pgvector). Links entstehen bei jedem `brain_contribute`-Aufruf und via `/brain/backfill` für bestehende Einträge.
- **Brain-API** — Neue Endpunkte: `GET /brain/graph` (Nodes + typisierte Kanten), `GET /brain/search`, `GET /brain/related/{id}`, `POST /brain/agent/contribute`, `GET /brain/agent/search` (inkl. Cross-Agent-Memory-Suche), `POST /brain/backfill` (Admin).
- **Edge-Typen im Graph** — Backlinks (solid) vs. Semantische Links (dashed). Bei Hover färben sich die Kanten farbig (indigo/emerald) und glühen, sonst bleiben sie subtil grau. Legende zeigt Anzahl je Typ.
- **Back-Navigation-Fix** — Klick auf einen Node im Graph und Zurück-Pfeil kehrt zum Graph zurück (nicht mehr zur Liste). `previousView`-State merkt sich den Ursprung.
- **Agent Brain-Prompting** — `SELF_IMPROVEMENT_SUFFIX` enthält jetzt expliziten Schritt für `brain_contribute` mit Kriterien was beigesteuert werden soll (Insights, Entscheidungen, Workflows) vs. was nicht (Task-Zusammenfassungen, Code-Beschreibungen).

### Fixed
- Graph springt nicht mehr beim Klick auf Node zurück: Reading Panel ist absolut positioniert (z-20) und ändert die Container-Dimensions nicht — die Force-Simulation startet nicht neu.

---

## [1.35.0] — 2026-05-08

### Added
- **Trading Analyst Agent Template** — Builtin-Template für Prediction Market Analysis (Polymarket/Kalshi). Automatisch published, Kategorie `finance`.
- **6 Trading Skills** — `trading-market-scanner`, `trading-odds-analyzer`, `trading-paper-portfolio`, `trading-market-report`, `trading-crypto-sentiment`, `trading-backtest-analyzer`. Alle mit echtem Python-Code, API-Referenz und Output-Format.
- **Template `skill_ids` Feld** — AgentTemplates können jetzt eine Liste von Skill-IDs hinterlegen. Beim Erstellen eines Agents aus dem Template werden die Skills automatisch zugewiesen (`assigned_by="template"`).
- **Auto-Skill-Assignment via Template** — `POST /templates/{id}/create-agent` assigned alle in `skill_ids` hinterlegten aktiven Skills an den neuen Agent.

## [1.34.0] — 2026-05-06

### Added
- **Key Management System (KMS)** — Verschlüsselte API-Keys, SSO-Profile und OAuth-Tokens zentral verwalten. Secrets werden Fernet-verschlüsselt gespeichert (`agent_secrets`-Tabelle). Neue Seite `/secrets` zum Anlegen, Bearbeiten und Löschen von Secrets.
- **Secrets pro Agent assignen** — Im Agent Integrations-Tab neue Section "API Keys & Secrets". Secrets können per Checkbox dem Agenten zugewiesen werden (n:m über `agent_secret_assignments`).
- **Automatische Env-Var-Injektion** — Bei jedem Agent-Start/Neustart werden alle zugewiesenen, aktiven Secrets als Umgebungsvariablen in den Container injiziert (z.B. `AZURE_AI_SEARCH_KEY=...`). Der Agent kann sie direkt via `os.environ` verwenden.
- **REST API `/secrets/`** — CRUD-Endpoints für Secrets, Assignment (`POST/DELETE /secrets/agent/{agent_id}/{secret_id}`), Listing per Agent (`GET /secrets/agent/{agent_id}`). Werte werden nur maskiert zurückgegeben.
- **Key Management in Sidebar** — Neuer Navigationspunkt "Key Management" unter System-Bereich.

## [1.33.1] — 2026-05-03

### Fixed
- **Dialog Accessibility** — `Dialog.Title` fehlte im Analytics-Agent-Detail-Modal bei leerem/loading Zustand. Radix-UI-Fehler behoben mit dauerhaft gerendertem `sr-only` Title.

---

## [1.33.0] — 2026-05-03

### Added
- **Token-Zähler & Cost Attribution** — Jeder Task-Run speichert `input_tokens` + `output_tokens`. Neues Dashboard-Widget zeigt Top-Agenten nach Kosten + Platform-Gesamtkosten (`GET /tasks/cost-attribution`).
- **Skill Versioning & Rollback** — Vor jedem Skill-Update wird automatisch ein Snapshot angelegt. Rollback auf beliebige Version via API. `skill_version` wird in `SkillTaskUsage` mitgespeichert für versions-spezifische Analytics.
- **Skill A/B-Validierung** — Auto-verbesserte Skills gehen in Probation-Status. Nach 14 Tagen oder 5 Post-Improvement-Ratings wird automatisch validiert oder zurückgerollt. Probation-Felder auf `Skill`-Model.
- **Path/Role-basierte Skill Auto-Injection** — Skills mit `paths`-Glob oder `roles`-Liste werden automatisch für passende Tasks aktiviert (`SkillAutoInjector`-Service).
- **Konfigurierbare Improvement-Thresholds** — Alle 5 Konstanten der ImprovementEngine sind jetzt über `PlatformSettings` und per-Agent-Config überschreibbar. Kein Hardcoding mehr.
- **Feedback-Loop-Benachrichtigungen** — Nutzer die schlechte Ratings abgegeben haben werden benachrichtigt wenn ihr Feedback eine Skill-Verbesserung ausgelöst hat.
- **URL Allowlist & Security Templates** — Agenten können auf URL-Whitelist-Basis eingeschränkt werden. Vordefinierte Templates (z.B. "GitHub only", "No external access"). Enforcement in `executor.py`.
- **GitHub Issue Templates** — Neue Templates für Security, Agent-Behavior und Infrastructure Issues.

### Fixed
- **SQLAlchemy `.distinct(col)` Syntax** — SQLAlchemy 2.0 akzeptiert keine Column-Argumente in `.distinct()`. Korrigiert zu `.group_by()` in `improvement_engine.py`.
- **Async Blocking I/O in URL Allowlist** — `_fetch_url_allowlist()` blockierte den Event-Loop mit synchronem `urllib`. Fix: `asyncio.to_thread()`.
- **Doppelte SkillVersion-Tabelle** — Branches 148 und 151 definierten beide `skill_versions`. Migration 148 auf `down_revision=v1s2k3r4o5l6` korrigiert, `CREATE TABLE` entfernt.
- **Doppelte Notification-Logik** — `skill_marketplace.py` duplizierte `_notify_feedback_contributors`. Konsolidiert auf die Funktion in `improvement_engine.py`.
- **Alembic Migrations-Kette gebrochen** — Drei Migrations-Dateien teilten `revision = "a1b2c3d4e5f6"`, `y9s0t1u2v3w4` war ebenfalls doppelt. Alle Duplikate aufgelöst, Kette repariert. Fehlende Spalten (`skills.current_version`, A/B-Probation-Felder, `tasks.input_tokens/output_tokens`, `skill_task_usages.skill_version`) direkt via SQL nachgetragen.
- **`DockerService.get_workspace_disk_usage` fehlte** — Neue Methode implementiert: liest `/workspace`-Auslastung per `df -BM` aus dem Container, gibt `disk_usage_mb / disk_limit_mb / disk_percent` zurück.

---

## [1.32.1] — 2026-04-30

### Changed
- **Lizenzmodell** — Wechsel von Fair-Code / Sustainable Use License zu Source Available. Privater, nicht-kommerzieller Einsatz ist weiterhin kostenlos. Jeder geschäftliche Einsatz (intern, SaaS, Produkt, Kundenprojekte) erfordert eine individuelle Lizenz — Kontakt: daniel.alisch@me.com

---

## [1.32.0] — 2026-04-27

### Added
- **Bridge App — Native macOS UI (AppKit)** — Kompletter Redesign der Tray-App. Alle Dialoge (Einstellungen, Berechtigungen, Status) nutzen jetzt native NSPanel/AppKit statt tkinter. Sauberes macOS-Look-and-Feel mit Retina-Support.
- **Bridge — Ordner-Zugriff konfigurierbar** — Berechtigungen-Dialog hat jetzt eine Ordner-Sektion mit NSOpenPanel-Picker. Konfigurierte Pfade werden in `~/.ai_employee_bridge.json` gespeichert.
- **Bridge — Automatische Session-Wiederherstellung** — `ensure_session()` prüft beim Verbinden ob die gespeicherte Session noch existiert. Bei abgelaufener Session wird automatisch eine neue erstellt. Bei abgelaufenem Token öffnet sich automatisch der Einstellungen-Dialog (via 3s-Timer-Trick für Main-Thread-Safety).
- **Computer-Use `agent_id` Session-Binding** — Sessions können via `PATCH /api/v1/computer-use/sessions/{id}/agent` einem bestimmten Agenten zugewiesen werden. Nur dieser Agent darf dann Commands senden.
- **`computer_use` MCP-Tool für Agenten** — Agenten (Claude Code CLI) haben jetzt `computer_list_sessions`, `computer_screenshot`, `computer_click`, `computer_type`, `computer_key`, `computer_find_element` etc. via `desktop` MCP-Server (`computer-use-server.mjs`).
- **`X-Agent-ID` Header in `computer-use-server.mjs`** — MCP-Server sendet jetzt den `X-Agent-ID` Header bei allen API-Calls. Orchestrator kann damit Agent-HMAC-Token validieren.
- **Bridge App — Windows UI (customtkinter)** — Windows-Version nutzt jetzt `customtkinter` statt plain tkinter. Dunkles Theme, abgerundete Ecken, farbige Risk-Badges in den Berechtigungs-Rows — visuell 1:1 mit der macOS-Version. PyInstaller-Spec bundles alle CTk-Theme-Dateien via `collect_all`.

### Fixed
- **ObjC Klassen-Namenskonflikt** — Alle drei Dialoge definierten innerhalb ihrer Funktionen eine Klasse `_H(NSObject)`. Zweiter Aufruf crashte mit "ObjC class already registered". Fix: Module-Level Handler-Klassen `_SetupHandler`, `_PermsHandler`, `_StatusHandler` mit State-Dicts.
- **Berechtigungen-Dialog crashte (negative Y-Koordinaten)** — 7 Capability-Rows × 54px passten nicht in H=580. Buttons landeten bei y=−44. Fix: H=700.
- **`NSFont.monospacedSystemFontOfSize_` nicht verfügbar** — Fix: `userFixedPitchFontOfSize_` verwenden.
- **`computer-use` reservierter MCP-Name** — Claude Code CLI lehnte den MCP-Server-Namen `computer-use` als reserviert ab. Umbenannt zu `desktop`.
- **`X-Agent-ID` fehlte in Computer-Use API-Calls** — `computer-use-server.mjs` sendete nur den Bearer-Token, nicht den Agent-ID-Header. Orchestrator lehnte alle Requests mit 401 ab.

---

## [1.31.0] — 2026-04-25

### Added
- **Self-Improvement Engine für Skills** — `ImprovementEngine` erkennt Skills mit avg_helpfulness ≤ 3.0 (min. 5 bewertete Nutzungen) und stellt automatisch einen Verbesserungs-Task in die Agent-Queue. Der Agent analysiert den aktuellen Skill-Inhalt, schreibt ihn neu und ruft `skill_update` auf. Kein direkter Anthropic-API-Key auf dem Orchestrator nötig — nutzt die bestehende OAuth-Infrastruktur.
- **`skill_install` im MCP Skill-Server** — Das Tool fehlte komplett in `skill-server.mjs` (Claude Code Agents). Agents können jetzt auch im Claude-Code-Modus Marketplace-Skills installieren.
- **`user_rating` in `skill_rate`** — Agents können Nutzer-Feedback aus dem Gespräch interpretieren und als `user_rating` (1–5) beim Bewerten übergeben. Analytics zeigt jetzt Agent/User-Rating getrennt.
- **Implicit Usage-Tracking bei `skill_install`** — Wenn ein Agent `skill_install` aufruft während ein Task läuft, wird automatisch ein `SkillTaskUsage`-Record erstellt. Sorgt dafür, dass Installations-Ereignisse in der Nutzungs-Analytics sichtbar sind.

### Fixed
- **Skill Analytics zeigte immer 0 Nutzungen** — Frontend zeigte `period_uses` (nur explizit geratete Usages) statt `usage_count`. Jetzt wird `usage_count` als Haupt-Metrik angezeigt, `period_uses` als optionales Zeit-Sub-Label (z.B. "5 (30d)").
- **Positiver Feedback-Loop bei `usage_count`** — `agent_search_skills` inkrementierte den Top-Skill bei jeder Suche, auch bei leeren Queries. Da die Liste nach `usage_count` sortiert wurde, bekam der meistgenutzte Skill exponentiell mehr Counts. Fix: Implicit Tracking nur noch bei nicht-leerem Suchstring.
- **`skill_update` 403 für zugewiesene Agents** — Endpoint erlaubte Updates nur für den Ersteller. Fix: Agents die einen Skill installiert haben (via `AgentSkillAssignment`) dürfen ihn jetzt ebenfalls aktualisieren — ermöglicht den Self-Improvement-Loop.
- **`skill_rate` erstellte Duplikat-Records** — Bei mehrfachem Aufruf pro Task wurde ein neuer `SkillTaskUsage`-Record erstellt statt upzudaten. Fix: Upsert per `(task_id, skill_id, agent_id)` — `usage_count` wird nur bei neuen Records inkrementiert.
- **Auto-Track-Spam in `_record_skill_usages`** — TaskRouter erstellte bei jedem Task-Abschluss `SkillTaskUsage`-Records für **alle** installierten Skills, unabhängig ob sie genutzt wurden. Fix: Funktion backfilled nur noch Timing-Daten auf bereits existierende Records.
- **`skill_search` Implicit Tracking ohne Task-ID** — Agent-seitige `task_id`-Übergabe war optional und wurde meist weggelassen. Orchestrator löst jetzt server-seitig den laufenden Task des Agents auf.

---

## [1.30.1] — 2026-04-24

### Fixed
- **Webhook-Tasks nicht in Analytics sichtbar** — Webhook-Handler erstellte keinen `Task`-DB-Record beim Queuen. Der TaskRouter fand beim Completion-Event keine Task-ID → Analytics, Kosten-Tracking und `skill_rate` blieben leer. Fix: `Task`-Record wird jetzt synchron beim Queuen angelegt.
- **`skill_search` 500 bei Category-Filter** — PostgreSQL kann `character varying` nicht direkt mit `skillcategory` Enum vergleichen. Fix: `cast(Skill.category, Text) == category.upper()`.
- **`skill_search` "No skills found" bei langen Queries** — `ilike` auf kompletten LLM-Query-String (`"brainstorming ideation workflow for generating app ideas"`) findet nichts. Fix: Query wird in Einzelwörter gesplittet, OR-Verknüpfung über alle Wörter.
- **`skill_install` installiert falsche Skill-ID** — `skill_search`-Antwort enthielt keine sichtbare ID; LLM griff auf halluzinierte ID zurück. Fix: ID prominent in der Antwort mit `skill_install(skill_id=X)` Hinweis.

---

## [1.30.0] — 2026-04-24

### Added
- **`skill_install` Tool** — Agents können Marketplace-Skills jetzt selbst installieren. `skill_search` → `skill_install` → sofortige Nutzung ohne Admin-Eingriff. Neuer Orchestrator-Endpunkt `POST /skills/agent/install/{skill_id}` mit `assigned_by="agent:{id}"`. Skill-Content wird direkt in der Response zurückgegeben.
- **`skill_rate` Tool** — Bisher wurde in `TASK_STARTUP_PREFIX` 4× auf `skill_rate` verwiesen, das Tool existierte aber nicht. Jetzt korrekt implementiert: ruft `POST /skills/agent/record-usage` auf und aktualisiert `avg_rating`, `usage_count` und `time_saved_seconds` in der Datenbank.
- **Skill-Lifecycle vollständig geschlossen** — Vollständiger Loop: User gibt Task → Agent sucht Marketplace (`skill_search`) → Agent installiert passenden Skill (`skill_install`) → führt Task aus → bewertet Skill (`skill_rate`) → User-Feedback fließt über bestehenden Rating-Loop zurück zur Skill-Verbesserung.

### Fixed
- `skill_install` und `skill_rate` zu `ALWAYS_ALLOWED_TOOLS` hinzugefügt — werden nie von Autonomy-Enforcement geblockt.

---

## [1.29.5] — 2026-04-24

### Fixed
- **Custom LLM: Autonomy-Levels L1–L4 durchgesetzt** — Bisher wurden die Whitelist-Regeln nur als Text in den System-Prompt injiziert; GPT-Modelle ignorierten sie bei expliziten User-Anfragen. Fix: Echter Code-Level Enforcement im `ToolExecutor.execute()` — geblockte Tool-Kategorien werden **vor** der Ausführung abgefangen und geben einen `[AUTONOMY BLOCK]`-Fehler zurück, der den Agenten zwingt `request_approval` aufzurufen. Unabhängig vom verwendeten Modell.
- **Custom LLM: Kategorie-Mapping korrigiert** — `bash` war auf `shell` gemappt, DB-Kategorie ist `shell_exec`. L3-Shell-Commands wurden fälschlicherweise geblockt.
- **Custom LLM: L4-Wildcard erkannt** — L4-Preset hat nur `custom`-Kategorie ("Alles erlaubt"). `_get_allowed_categories()` erkennt nun die Wildcard-Regel und gibt `None` zurück (= keine Einschränkung).
- **Custom LLM: Autonomy-Cache-TTL auf 10s reduziert** — Whitelist-Änderungen (Level-Wechsel) propagieren jetzt innerhalb von 10s ohne Agent-Restart.

---

## [1.29.4] — 2026-04-24

### Fixed
- **Custom LLM: Skills nicht injiziert** — `LLMChatHandler` (Chat-Tab) und `LLMRunner` (Webhook/Tasks) riefen `get_skills_context()` nie auf — installierte Skills waren dem Agenten vollständig unbekannt. Fix: Skills werden beim ersten Message in den System-Prompt geschrieben (Chat) bzw. in den System-Prompt der Task-Ausführung (Webhook/Tasks).
- **Custom LLM: Falscher `TOOL_USAGE_RULES`-Import** — `llm_chat_handler.py` importierte `TOOL_USAGE_RULES` aus `runner_hooks`, wo die Konstante nicht existiert. Fix: Import entfernt, Skills direkt ans System-Prompt angehängt.
- **Agent-Template: Hardcodierte Fake-Skills** — `agent_templates.py` hatte `find-skills` und `ui-ux-pro-max` als "Pre-installed Skills" fest eingetragen — unabhängig davon was tatsächlich installiert ist. Fix: Statische Liste entfernt; Agents referenzieren jetzt die dynamisch injizierten Skills am Ende des System-Prompts.

---

## [1.29.3] — 2026-04-24

### Added
- **Skills-Awareness in CLAUDE.md** — Agents wissen jetzt dass Skills als Slash Commands unter `/workspace/.claude/skills/` liegen und prüfen dies automatisch beim Gesprächsstart.
- **Knowledge Base Context beim Gesprächsstart** — DEFAULT_CLAUDE_MD instruiert Agents jetzt gezielt `knowledge_search` für "projects", "preferences" und "architecture" am Anfang jeder Conversation aufzurufen.
- **DB-Skills installierbar** — Marketplace-Skills ohne GitHub-Repo (z.B. vom DevAgent erstellte Skills) können jetzt direkt per base64-Write in den Agent-Container installiert werden.

### Fixed
- **About Modal: Zentrierung** — `framer-motion` überschreibt Tailwind `-translate-x/y-1/2` transforms. Fix: äußeres `div` übernimmt Positionierung, inneres `motion.div` nur noch Animation.
- **About Modal: Nicht klickbar** — `AnimatePresence` kann `motion`-Elemente in Portals nicht tracken → Modal wurde nie gerendert. Fix: `AnimatePresence` entfernt, Portal direkt mit `createPortal` aus statischem Import.
- **About Modal: `require()` in Production** — Dynamisches `require("react-dom")` schlägt in Next.js Production-Build still fehl. Fix: statischer `import { createPortal } from "react-dom"` am Dateianfang.
- **Skill Store: `[object Object]` Fehlermeldung** — FastAPI-422-Validierungsfehler sind Arrays; werden jetzt korrekt per `JSON.stringify` als lesbarer Text angezeigt.
- **Skill Store: DB-Skills ohne `repo` crashten mit 422** — Frontend schickte `undefined` als `repo`-Feld. Fix: `cat.repo || cat.source_repo` als Fallback; für `type: "db"` wird `content` direkt gesendet.
- **CLAUDE.md wird bei Restart nicht aktualisiert** — `restart_agent()` schrieb `/workspace/CLAUDE.md` nie neu (nur `create_agent()` tat das). Fix: Schritt 5b in `restart_agent` schreibt CLAUDE.md mit aktuellem `DEFAULT_CLAUDE_MD` Template neu — Updates propagieren ab sofort bei jedem Restart automatisch.
- **MyAzureAgent: GitHub-Zugriff nach OAuth-Connect** — Token wird nur beim Container-Start injiziert. Agent-Restart nach GitHub-OAuth-Verbindung nötig und dokumentiert.
- **Sidebar Bottom: Sortierung & UserMenu-Position** — UserMenu zurück an letzter Stelle; Reihenfolge: Notifications → Dark Mode → GitHub → Über → Admin → UserMenu.
- **Über Modal: `# Changelog` Heading** — Wird nun per `[&_h1]:hidden` CSS ausgeblendet da Titel bereits im Modal-Header steht.

## [1.29.2] — 2026-04-24

### Added
- **About Modal** — Info-Button (ⓘ) in der Sidebar (collapsed: Icon, expanded: "Über AI Employee" mit Versionsnummer). Klick öffnet Modal mit aktueller Version + vollständigem Changelog direkt aus der API.

### Fixed
- **Custom LLM: SyntaxError in async generator** — `yield from` ist in async-Funktionen nicht erlaubt. Beide Vorkommen in `_stream_chat_with_body` durch `for/yield`-Loop ersetzt. Betraf alle Custom LLM Agents (OpenAI, Azure) — Container crashten beim Start.
- **Version-Banner immer stale** — `AGENT_VERSION`-Env-Var in `docker-compose.yml` wurde nie automatisch aktualisiert. Jetzt wird `./VERSION` als Read-only-Volume nach `/VERSION` gemountet; `_read_version()` liest diesen Pfad zuerst. Version stimmt ab sofort automatisch nach jedem Release.

---

## [1.29.1] — 2026-04-24

### Fixed
- **Agent creation 500 error** — `UnboundLocalError: cannot access local variable 'config'` on agent creation resolved. The variable was referenced before assignment in `agent_manager.py` (leftover from a refactor). New agents correctly start with no mounts.
- **Custom LLM: max_tokens → max_completion_tokens auto-retry** — Newer OpenAI/Azure models (gpt-5.4, o1, o3, etc.) require `max_completion_tokens` instead of `max_tokens`. The provider now detects the mismatch from the 400 error message and retries automatically — no model-name whitelist needed.
- **Chat tab bar layout** — The `+` button and connection status indicator were scrolling out of view when many chat sessions were open. Only the session list now scrolls; the controls stay pinned to the right.
- **Agents: WebSearch enabled by default** — The default CLAUDE.md prompt now explicitly instructs all agents to use `WebSearch` and `WebFetch` for external information (weather, docs, current events). Previously agents would refuse with "I have no internet access" even though the tools were available.

### Added
- **Provider badge for Claude Code agents** — Agent cards now show an orange "Anthropic" badge for `claude_code` agents, making it easy to distinguish them from Custom LLM agents (violet badge with provider name).

---

## [1.29.0] — 2026-04-24

### Added
- **Agent Detail Modal in Analytics** — Click any agent row in the Analytics dashboard to open a modal with full stats: task volume, success rate, cost, avg turns, daily bar chart (completed vs. failed), recent error log, and latest ratings with comments.
- **`skill_record_usage` MCP tool** — Agents can now explicitly signal "I used skill X during this task" via a new MCP tool. Records a `SkillTaskUsage` entry with task linkage for accurate analytics. `skill_rate` now also calls this internally — one call records both the rating and the usage event.
- **`skill_rate` now tracks task context** — `skill_rate` accepts optional `task_id` (pass `CURRENT_TASK_ID` from prompt) and `helpfulness` (1–5). Usage is linked to the specific task for full traceability.
- **Agent Update All button** — New "Update All (N)" button in the Agents page header appears automatically when one or more agents have an available update. Individual update button also added to the per-card hover actions (orange arrow icon).
- **Dynamic version reading** — `AGENT_VERSION` now reads from the `VERSION` file at runtime instead of being hardcoded in `config.py`. The VERSION file is mounted into the orchestrator container via `docker-compose.yml` so the version endpoint always reflects the actual running release.

### Fixed
- **Version banner false-positive** — `AGENT_VERSION` was hardcoded as `"1.27.0"` even after rebuilding with 1.28.0. Now reads from `VERSION` file dynamically, so the update banner correctly disappears after a rebuild.

---

## [1.28.0] — 2026-04-23

### Added
- **Skill Analytics Dashboard** — New `/analytics` page with platform-wide stats: total tasks, total cost, estimated time saved, avg rating, agent count. Daily task-volume area chart. Sortable skill table with ROI column (manual duration vs. actual agent time). Per-agent performance table with success rate, avg cost, avg duration.
- **Skill time-savings tracking** — New `manual_duration_seconds` field per skill (set in the Skills modal). New `skill_task_usages` table records actual agent duration vs. manual baseline per task. Time-saved is calculated automatically and shown in the analytics dashboard.
- **Skill usage API** — `POST /ratings/skill-usage` to record explicit skill–task pairings; `PATCH /skills/marketplace/{id}/manual-duration` to set the manual-effort baseline for ROI calculation.
- **Analytics sidebar link** — Analytics page added to the main navigation.

### Fixed
- **Multi-user data isolation** — Comprehensive security fix: regular users can no longer read, modify, or delete data belonging to other users. All endpoints now enforce ownership:
  - **Tasks** — list and detail endpoints filtered by user-owned agents
  - **Schedules** — list scoped; all mutations (update / delete / trigger / pause / resume) check agent ownership
  - **Knowledge Base** — fully per-user: 1 KB per user, shared across all of that user's agents, invisible to other users. Agent-facing write/search/read endpoints scope to the agent owner's KB automatically
  - **Approval Rules** — list shows only global + own rules; PATCH/DELETE blocked for foreign rules
  - **Agent Memories** — GET `/memory/agents/{id}` verifies agent ownership before returning
  - **Team Directory** — scoped to user-owned agents for non-admins
  - **Audit Log** — fixed 500 crash (`e.details` → `e.meta`)
- **Host-mount injection into CLAUDE.md** — Configured NFS/SMB/local volume mounts are now listed in the agent's CLAUDE.md so Claude knows which paths are available.
- **Alembic multi-head** — Merge migration added to resolve diverged migration heads after parallel feature branches.

---

## [1.27.0] — 2026-04-23

### Added
- **Native MS Graph MCP server** — 25 tools covering Outlook Mail (read, send, reply), Calendar (list/create/update/delete events), Teams (channels + 1:1 chats), Planner tasks, Microsoft To-Do lists, and OneDrive file search/read. Auto-registered when the agent's user has a connected Microsoft account.
- **Per-user Microsoft OAuth** — Each user connects their own Microsoft 365 account via OAuth. Tokens are stored per-user (not shared globally). Admin configures Azure App Registration credentials once in Settings; each user then signs in individually. `oauth_integrations` table now has a nullable `user_id` column with partial unique indexes.
- **Expanded Microsoft OAuth scopes** — Added `Mail.Send`, `Chat.ReadWrite`, `ChannelMessage.Read.All`, `Tasks.ReadWrite`, `Contacts.ReadWrite`, `People.Read` for full M365 coverage.
- **Integrations page: setup guide** — Microsoft 365 cards show a "Per user" badge and an expandable Azure App Registration guide with copy-able redirect URL and the exact list of required Delegated scopes.

### Fixed
- **Bridge heartbeat / staleness detection** (#135) — Added `bridge_last_seen_at` timestamp (updated on every incoming WebSocket message). `bridge_connected` boolean missed NAT/WiFi drops that don't send TCP FIN; `bridge_last_seen_at` > 20s now marks the bridge as offline regardless. Ping/pong task sends `{"type":"ping"}` every 10s so the timestamp stays fresh while the bridge is idle.
- **Separate bridge status endpoint** — New `GET /computer-use/sessions/{id}/status` lets the UI distinguish "no screenshot yet" from "bridge is gone" without triggering a screenshot request.
- **503 now logged** — Screenshot fetch failures were silently swallowed; `console.warn` now logs the HTTP status code for easier debugging.

---

## [1.26.0] — 2026-04-23

### Added
- **Autonomy Levels L1–L4** — Each agent can be assigned an autonomy level that defines what it may do without asking. L1 = read-only, L2 = recommendations + workspace writes, L3 = full shell + packages, L4 = fully autonomous. Set via agent settings or API (`POST /agents/{id}/autonomy-level`).
- **Whitelist-based approval model** — Replaced the old blacklist approach ("ask before X") with a whitelist ("you are allowed to do X; everything else requires approval"). Safer by default — no gaps where the agent silently acts outside its mandate.
- **DB-backed level presets** — Autonomy preset rules are stored in the `autonomy_preset_rules` table and seeded on startup. Admins can add, edit, and delete rules per level via the UI without touching code.
- **Level-Presets tab in Approvals page** — Third tab shows all four levels with their allowed actions. Inline add/delete per rule. Old blacklist wording auto-detected and migrated to whitelist on first startup.
- **Full governance audit trail** — Every governance-relevant event is now written to `audit_logs`: approval requests, approvals, denials, autonomy level changes, approval rule CRUD, and preset rule changes. Nothing goes untracked.
- **Auto-Preset badge** — Rules generated by autonomy level presets are marked with an "Auto-Preset" badge in the Rules tab so users know which rules are system-managed.
- **Rules tab loads on mount** — Fixed bug where the Rules tab showed 0 entries until clicked; rules now load immediately on page open.

### Changed
- **Prompt injection framing** — `TASK_STARTUP_PREFIX` and `CHAT_STARTUP_PREFIX` updated to whitelist framing. Agents now read their allowed actions first; anything outside the list triggers `request_approval` automatically.
- **New audit event types** — `approval_requested`, `autonomy_level_changed`, `approval_rule_created/updated/deleted`, `preset_rule_added/deleted`, `agent_created/deleted` added to `AuditEventType`.

---

## [1.25.0] — 2026-04-22

### Fixed
- **WebSocket authentication** — Ticket fetch used `window.location.origin` (port 3000) instead of `getApiUrl()` (port 8000), breaking WebSocket auth on local dev setup. Fixed in `chat.tsx`, `notification-bell.tsx`, `use-websocket.ts`, `tasks/[id]/page.tsx`.
- **Agent create 500 error** — `agent_workspace_size_gb` attribute was missing from `Settings` config, causing a 500 error when creating agents.
- **Setup robustness** — `setup.sh` now generates `API_SECRET_KEY` even when the line is completely missing from `.env`, preventing orchestrator startup failure on existing installs.
- **Caddyfile restored** — Accidentally removed during disk cleanup; restored from git history.

---

## [1.24.0] — 2026-04-22

### Added
- **Per-Agent Idle Timeout & Disk Quota** — Each agent can now configure its own idle timeout and disk quota in Settings. Files tab shows a live disk usage bar based on the agent's individual quota.
- **GitHub Star Button** — Sidebar now shows a "Star on GitHub" button with the live star count from the repository.

### Fixed
- **Disk bar uses per-agent quota** — Disk usage bar in Files tab now correctly reads the agent's own quota instead of the global default.
- **Telegram wake-up** — Always verifies actual Docker container state before skipping wake-up to avoid stale status.
- **Cloudflared tunnel stability** — Added healthcheck and autoheal label to prevent silent tunnel degradation.
- **Skill duplicate names** — Skills can no longer be created with date-suffixed duplicate names.
- **Setup: agent image not found** — `setup.sh` now automatically builds `ai-employee-agent:latest` before starting the stack, preventing "pull access denied" errors on fresh installs.
- **Docker Compose v2 requirement documented** — README and setup.sh now clearly state that Docker Compose v2 (`docker compose`) is required.

---

## [1.23.0] — 2026-04-21

### Added
- **Per-Agent Webhook** — Each agent can individually enable external HTTP access via Settings → Externer Zugriff. Generates a Bearer token on first enable; toggle persists across page reloads. Endpoint: `POST /webhooks/agents/{id}`.
- **MCP Endpoint per Agent** — Every webhook-enabled agent exposes a proper MCP 2025-06-18 Streamable HTTP server at `POST /mcp/agents/{id}`. Compatible with n8n MCP Client Node, Cursor, and other MCP clients. Four tools: `send_task`, `get_task_status`, `get_agent_status`, `list_recent_tasks`.
- **Skill File Attachments** — Skills can now carry file attachments (`.py`, `.js`, `.sh`, `.yaml`, `.json`, `.md`, …, max 10 MB each). Files are stored on a shared volume and automatically pushed to `/workspace/skills/{name}/` inside the agent container when the skill is installed.
- **Sidebar Redesign** — Navigation grouped into four sections (Übersicht, Zusammenarbeit, Automation, System) with collapsible groups. New icon-only collapse mode via a toggle button on the sidebar edge; state persists in localStorage.

### Fixed
- **Task result saved to DB** — Agent text output (`assistant` events) is now collected during execution and written to `tasks.result`. Previously the field was always empty because Claude Code CLI's `result` event is often blank.
- **Webhook toggle state lost on refresh** — `webhook_enabled` and `webhook_token` were missing from `AgentResponse` schema and `get_agent_metrics()`. Toggle now correctly loads saved state on page load.
- **MCP `list_recent_tasks` crash in n8n** — `limit` parameter changed from `"type": "integer"` → `"type": "string"` to match n8n's input handling; backend casts to int safely.
- **MCP `send_task` task not findable** — `send_task` now creates a `Task` DB record (status `QUEUED`) before pushing to Redis, so `get_task_status` can always find the task.

---

## [1.22.0] — 2026-04-20

### Added
- **Trend-Driven Skill Auto-Discovery** — `TrendService` scans GitHub Search API (4 queries) and Hacker News daily for trending AI/agent/MCP repos. New repos are saved as `DRAFT` skills for user review. Security: prompt-injection pattern detection, min. 100 stars threshold, HTML/markdown sanitization before storing any external content.
- **Skill Pending Tab** — New "✨ Ausstehend" tab in the Skills page lists all auto-generated draft skills. Users can approve (→ ACTIVE) or reject (→ ARCHIVED) each one individually.
- **Approve/Reject API** — `POST /marketplace/{id}/approve` and `POST /marketplace/{id}/reject` endpoints for skill moderation.
- **Meeting Room: Parallel Moderator Opening** — Moderator now fires its opening statement as a non-blocking `asyncio.create_task()`, so agents can start immediately without waiting.
- **Meeting Room: Agenda Tracking** — Every moderator prompt now includes a `✓/▶/○` agenda status block so the moderator always knows which phase is active and which are done.
- **Meeting Room: Agent Identity** — Agents prepend a `knowledge.md` read instruction to all meeting turns so they speak as themselves with their own context and skills.
- **Meeting Room: Summary Modal** — Completed meeting cards now have a "Zusammenfassung" button that lazy-loads the full room data and renders the summary with PDF export.

### Fixed
- **Category filter labels** — All categories were showing as "Tools" because `CATEGORY_CONFIG` keys were lowercase while the DB stores uppercase enums (`TOOL`, `WORKFLOW`, etc.). Now correctly shows Templates, Workflows, Patterns, Routinen, Rezepte.
- **Health status "Degraded"** — Dashboard was hitting the Next.js frontend instead of the orchestrator health endpoint. Fixed by adding `/api/v1/health` route alias.
- **Markdown rendering** — `---` now renders as `<hr>`, `>` blockquotes are styled, table borders visible.
- **Skill pending tab type error** — `pendingSkills` was typed as `AgentSkill[]` instead of `MarketplaceSkill[]`, causing build failures.
- **Duplicate `source_repo` field** — Removed duplicate field in `MarketplaceSkill` TypeScript interface.

### Changed
- **Repo links in pending skills** — `source_repo` is now a clickable GitHub link (opens in new tab) in the pending skills tab.
- **PDF export button** — Now visible as a blue labelled button instead of an icon-only low-contrast control.

---

## [1.21.0] — 2026-04-18

### Added
- **Cron scheduling** — Schedules now accept a `cron_expression` (e.g. `0 9 * * 1` = every Monday 9 am) in addition to the existing interval-based mode. 7 presets in the UI (Every day at 9am, Every weekday 8am, …) plus a free-text input. Powered by `croniter`.
- **Audit Log dashboard** — New `/audit` page: summary cards (total/success/blocked/failed events), agent budget progress bars, event-type breakdown with clickable filters, paginated log table with agent/outcome/event-type filters.
- **`claude_md` per template** — Agent templates can now carry a `CLAUDE.md` snippet that is written to `/workspace/CLAUDE.md` when an agent is spawned from that template.
- **GitHub Security Workflow** — Weekly + PR scanning: pip-audit, npm audit, Trivy container scan (SARIF → GitHub Security tab), CodeQL (Python + JS), TruffleHog secret detection.
- **System Status Bar** — Traffic-light style health indicator on the dashboard (API, DB, Redis, Docker + agent count).

### Fixed
- **Skill marketplace 401** — FastAPI route ordering bug: `/agent/available` and `/agent/search` were being matched as `/{agent_id}`, hitting the wrong auth middleware. Routes reordered.
- **Network View conversation modal** — Time filter extended to 7d / 30d (previously maxed at 24h, all messages were older). Silent `catch {}` replaced with visible error display.
- **Task listener** — Startup failures now surface in logs instead of dying silently.

### Changed
- **Claude Code CLI** updated from 2.1.78 → 2.1.114 in agent containers.
- **Agent-to-agent rate limit** — Max 20 messages/min per (from, to) pair via Redis INCR + 60s TTL → HTTP 429.
- **`/team/messages` backend** — Fetch limit scales with time window (100 for <6h, 500 for <24h, 2000 for 7d+).

### Internal
- Alembic migrations: `u5o6p7q8r9s0` (agent_templates.claude_md), `v6p7q8r9s0t1` (schedules.cron_expression)
- `croniter>=2.0` added to orchestrator dependencies

---

## [1.20.0] — 2026-04-16

### Added
- **Skill Marketplace** — Skills as persistent DB entities; per-agent skill assignments; catalog browse with category filter; install/uninstall UI.
- **Per-agent webhook triggers** — Agents fire tasks on incoming webhooks matching source + event type + payload conditions; `{{payload.field}}` interpolation in prompts.
- **Knowledge Feeds** — Scheduled ingestion of external RSS/web sources into the agent knowledge base.
- **Memory system upgrade** — Rooms, supersede chains, multi-strategy scoring (cosine + recency + access_count + tag boost), Redis-cached compressor.

---

## [1.19.0] — 2026-04-04

### Added
- **Meeting Rooms** — Multi-agent round-robin collaboration; DB model, API (CRUD + Start/Stop), Redis queue engine.
- **25 Agent Templates** — Pre-configured roles with icons, categories, recommended skills, default approval rules.
- **OAuth Provider Config UI** — Google/Microsoft/Apple client IDs configurable in Settings page with encrypted storage.
- **Skills Page** — `/skills` catalog with browse, agent picker, install, category filter.

### Fixed
- `/chat` page: `initialSessionId` prop, `createNewSession` reset, agent-switch via key remount.

---

## [1.18.0] — 2026-03-21

### Added
- **Self-improvement loop** — Agents reflect after every task; `ImprovementEngine` distils patterns from ratings.
- **Task ratings** — Telegram inline keyboards for rating completed tasks (1–5 stars).
- **Prometheus metrics** — All services export metrics; Grafana dashboards included.
- **Multi-tenant RLS** — PostgreSQL Row-Level Security on 9 user-scoped tables.

---

*Older history available via `git log --oneline`.*
