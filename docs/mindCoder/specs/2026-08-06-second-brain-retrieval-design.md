# Second-Brain-Retrieval: das Bessere anstöpseln

**Datum:** 2026-08-06
**Status:** Entwurf zur Freigabe (Fassung 2 — vollständig verdrahtet)
**Auslöser:** Architekturvergleich mit EdubaWare („Multiplayer AI", Jake Van Clief, 2026-08-06).
Die Frage war: ist unser Wissensansatz state of the art?

> **Fassung 2:** Die erste Fassung teilte die Arbeit in drei Stufen, von denen zwei
> ausgeliefert und eine vertagt worden wäre. Das hätte drei Inseln hinterlassen
> (Abschnitt 8). Diese Fassung liefert **ein** Release, Ende-zu-Ende verdrahtet.

---

## 1. Befund

Der Vergleich fiel unbequem aus. Nicht weil unsere Architektur schwächer wäre — sie ist
deutlich reichhaltiger — sondern weil der beste Teil davon den Agenten nie erreicht.

**Was wir haben und was besser ist als der Wettbewerber:**

- Echte semantische Suche, lokal: `BAAI/bge-m3`, 1024 Dimensionen, pgvector mit
  HNSW-Index, in einem eigenen Sidecar-Container ohne Cloud-Abhängigkeit. Findet
  Umschreibungen und funktioniert über Sprachgrenzen (deutsch gefragt, englisch abgelegt).
- Agenten-Gedächtnis mit Widerspruchserkennung beim Schreiben (Schwellen 0,88–0,92
  Widerspruch, ≥0,92 Dublette) und Ablöse-Semantik statt Überschreiben (`superseded_by`).
- Semantische Auto-Verknüpfung bei jedem Speichern (Schwelle 0,75) und mehrstufiges
  Re-Ranking bei der Gedächtnissuche.
- Eine Hybrid-Suche über den Second Brain: Vektor + Volltext, per Reciprocal Rank Fusion
  verschmolzen, mit markdown-bewusstem Chunking, das Code-Blöcke nicht zerschneidet und
  die nächste Überschrift mitführt (`orchestrator/app/services/vault_search.py:104`).

**Was der Wettbewerber besser macht — und es ist nicht die Technik:**

Ihre „ICM-Methode" macht Ordnerstruktur zur Methode. Die Wette: ein starkes Modell, das
eine gut geordnete Datei-Landkarte sieht und gezielt navigiert, schlägt Retrieval. Bei
einem kuratierten, von Menschen geordneten Korpus stimmt diese Wette oft. Struktur trägt
Bedeutung — ein Ordnername ist bereits eine Aussage, und genau die geht beim Chunking
verloren.

**Die Lücken, die dieses Dokument schließt:**

| # | Lücke | Fundstelle |
|---|---|---|
| 1 | Der Agent nutzt die Hybrid-Suche nicht. Sein `secondbrain_search` ist ein Substring-Zähler über alle Dateien. Die Hybrid-Suche erreichen nur der externe MCP-Endpunkt (n8n, Cursor) und die Sprach-Sitzung. | `agent/app/tools/executor.py:971` |
| 2 | Der Vault-Index driftet. Agenten-Schreibzugriffe indexieren nie — im Gegensatz zu Admin-UI, externem MCP und Sprach-Sitzung, die alle sofort indexieren. | `executor.py:1023` vs. `brains.py:433`, `brain_mcp.py:271`, `realtime_voice_session.py:3420` |
| 3 | Die Datei-Landkarte wird nie eingespielt. Wir injizieren den Verzeichnis*namen*, aber keinen Dateibaum. | `agent/app/runner_hooks.py:597` |
| 4 | Drei Prompt-Stellen schicken den Agenten ausdrücklich zu `grep` und „No special tool needed" — sie würden jedes bessere Werkzeug zum toten Pfad machen. | `agent_manager.py:556`, `runner_hooks.py:626`, `main.py:1341` |
| 5 | `DEFAULT_CLAUDE_MD` spricht von „TWO knowledge sources" und behauptet, die Wissensdatenbank sei „SHARED ACROSS ALL AGENTS" — sie ist `user_id`-begrenzt. Chat- und Proaktiv-Pfad kennen den Second Brain gar nicht. | `agent_manager.py:329,355`; `runner_hooks.py:153` |
| 6 | Claude-Code-Agenten haben **gar kein** `secondbrain_*`-Werkzeug. | `agent/mcp/` enthält nur `brain-server.mjs`, `memory-server.mjs` |

Kurios als Randnotiz: **n8n durchsucht unseren Vault heute besser als unsere eigenen Agenten.**

---

## 2. Nicht-Ziele

Bewusst ausgeschlossen, um den Umfang beherrschbar zu halten. Jeder Punkt ist eine
abgeschlossene Entscheidung, kein offenes Ende:

- **Kein neues Retrieval-System.** Alles verdrahtet vorhandenen Code. Kein neues
  Embedding-Modell, keine neue Datenbank, kein neuer Dienst.
- **Keine Schreib-Koordination / kein Locking.** Mehrere Agenten, dieselbe Datei — eigenes
  Thema, eigene Spec. Hier nicht angefasst.
- **Kein Umbau von `brain_search` (persönliche Wissensdatenbank) oder `memory_search`
  (Agenten-Gedächtnis).** Beide sind sauber angeschlossen und funktionieren.
- **Keine Korrektur der UI-Wissenssuche.** Dass der Knowledge-Tab nur `ILIKE` kann, ist ein
  echter Befund, betrifft aber Menschen, nicht Agenten. Separat zu behandeln.
- **Keine Agenten-Brücke zu `/memory/{id}/related`.** Nice-to-have, kein Engpass.

---

## 3. Suche: Agent bekommt die Hybrid-Suche

### 3.1 Neuer agenten-authentifizierter Suchendpunkt

`GET /brains/agent/search?q=<text>&limit=<n>&label=<optional>`

- **Auth:** `verify_agent_token` — dieselbe Absicherung wie bei den übrigen
  agenten-seitigen Endpunkten. Die `agent_id` kommt aus dem Token, nie aus der Anfrage.
- **Scoping (sicherheitskritisch):** Der Endpunkt ermittelt die dem Agenten zugewiesenen
  Mount-Labels aus `Agent.config["mounts"]`, schneidet sie gegen den effektiven Katalog
  (`get_effective_catalog`) und behält nur aktive `SecondBrain`-Einträge. Ein Agent darf
  **ausschließlich** Brains durchsuchen, die ihm zugewiesen sind. Ein mitgeschicktes
  `label` außerhalb dieser Menge → 404, nicht leere Trefferliste (ein Tippfehler soll nicht
  wie „nichts gefunden" aussehen).
- **Ausführung:** je zugewiesenem Brain
  `vault_search.hybrid_search(db, brain.label, brain.host_path, q, limit)`.
- **Antwort:** `{"mode": "hybrid"|"grep", "hits": [{"brain": <slug>, "path": ..., "snippets": [...]}]}`.
  Ergebnisse mehrerer Brains werden zusammengeführt und auf `limit` gekürzt.

### 3.2 Werkzeug umstellen — in **beiden** Laufzeiten

**Codex / Custom-LLM** (`agent/app/tools/api_client.py`, `definitions.py`):
`secondbrain_search` ruft künftig den Endpunkt über den `api_client` auf. Vorbild ist
`brain_search` (`api_client.py:625`), das genau so schon funktioniert.

**Claude Code** (`agent/mcp/secondbrain-server.mjs`, neu): derselbe Werkzeugname, dieselben
Parameter, derselbe Endpunkt — nach dem Muster, das bei den `trigger_*`-Werkzeugen am
2026-08-06 angewandt wurde. Ohne diesen Server bliebe der `DEV_Prod Agent` (claude_code)
außen vor.

Der neue Server wird wie die bestehenden in der Agenten-MCP-Konfiguration registriert und
im `Dockerfile` mitgeliefert.

**Rückfallebene:** Der heutige lokale Substring-Walk bleibt in `executor.py` erhalten. Ist
der Orchestrator nicht erreichbar, sucht der Agent lokal weiter, statt blind zu werden —
der Vault ist gemountet und damit auch ohne Orchestrator lesbar.

**Autonomie-Einstufung:** `secondbrain_search` steht heute in `ALWAYS_ALLOWED_TOOLS`
(Lesewerkzeug) — bleibt so. **Entscheidung:** es wird zusätzlich in
`CONCURRENT_SAFE_TOOLS` und `_CACHEABLE_TOOLS` aufgenommen. Heute steht es dort nicht — als
lokaler Walk über alle Dateien war das richtig. Als API-Aufruf ist es fachlich dasselbe wie
`brain_search`, das in beiden Mengen steht.

**Zwingend dazu — Cache-Invalidierung:** `secondbrain_write` wird in die
Invalidierungsliste in `executor.py:336` aufgenommen, die heute nur
`("write_file", "edit_file", "multi_edit", "bash")` kennt. Ohne das entsteht ein
Widerspruch aus den beiden Entscheidungen oben: Der Agent schreibt einen Artikel, sucht ihn
unmittelbar danach — und bekommt 120 Sekunden lang den zwischengespeicherten
„nicht gefunden"-Stand. Cachebare Suche und neues Schreibwerkzeug gehören zusammen.

---

## 4. Schreiben: der Agent indexiert wie alle anderen auch

Heute ist der Agent der **einzige** Schreibweg, der nicht indexiert:

| Schreibweg | indexiert sofort? |
|---|---|
| Admin-UI (`brains.py:433`) | ja |
| Externer MCP (`brain_mcp.py:271`) | ja |
| Sprach-Sitzung (`realtime_voice_session.py:3420`) | ja |
| **Agent (`executor.py:1023`)** | **nein** |

### 4.1 `secondbrain_write` über den Endpunkt

Neu: `POST /brains/agent/write` — gleiche Auth und gleiches Scoping wie die Suche,
zusätzlich Schreibrecht-Prüfung (`ro`-gemountete Brains lehnen mit 403 ab). Der
Orchestrator schreibt über `vault.write_file()` (atomar via tmp+rename) und ruft
anschließend `index_file()` auf — **derselbe Zweischritt wie die drei bestehenden
Schreibwege**. Keine Parallel-Implementierung.

Auch dieses Werkzeug kommt in beide Laufzeiten (`api_client.py` + `secondbrain-server.mjs`).

**Rückfallebene:** Ist der Orchestrator nicht erreichbar, schreibt der Agent wie bisher
lokal ins Dateisystem. Die Datei ist dann geschrieben, aber noch nicht indiziert — das
fängt der Durchlauf aus 4.2.

### 4.2 Re-Index-Durchlauf als Sicherheitsnetz

Neuer `_tick_vault_reindex()` in `orchestrator/app/services/scheduler_service.py`, gedrosselt
nach dem Muster von `_tick_failure_watchdog` (fauler Aufruf aus der Hauptschleife, eigener
Zeitstempel, Intervall **5 Minuten** — dieselbe Taktung wie der bestehende
Git-Autocommit-Timer, damit Index und Verlauf im Gleichschritt laufen).

Je aktivem Brain: `reindex_vault(db, brain.label, brain.host_path)`. Die Funktion ist
bereits inkrementell und datei-hash-basiert — unveränderte Dateien werden übersprungen.
Der Durchlauf kostet im Normalfall Datei-IO und Hashing, keine Embedding-Aufrufe.

**Rolle:** Sicherheitsnetz, nicht Hauptweg. Es fängt (a) den Rückfall aus 4.1, (b) direkte
`write_file`-Zugriffe auf `/mnt/brains/...`, (c) Dateien, die jemand per SSH auf dem Host
ablegt.

**Bewusst verworfene Alternative:** ein Dateisystem-Watcher (inotify). Reagiert schneller,
braucht aber zusätzliche Mechanik und ist über Bind-Mounts hinweg unzuverlässig. Der
periodische Durchlauf fügt sich in ein Muster ein, das im Scheduler bereits dreimal
existiert.

---

## 5. Landkarte und Prompt-Wahrheit

### 5.1 Datei-Landkarte im Startkontext

Einbauort: `get_mounts_context()` in `agent/app/runner_hooks.py:597` — die eine Stelle,
an der alle Laufzeiten ihre Mount-Kenntnis herbekommen, auch `custom_llm`.

Heute wird pro Brain nur `- /mnt/brains/<slug>` ausgegeben. Künftig zusätzlich ein
begrenzter Dateibaum. Die Begrenzung ist der entscheidende Teil, weil dieser Block in
**jeden** Task-Prompt geht:

- Nur `.md`, `.markdown`, `.txt`; `.git` wird übersprungen.
- **Bis 120 Dateien je Vault:** vollständige Liste relativer Pfade, alphabetisch.
- **Darüber:** Verzeichnis-Übersicht statt Dateiliste — je Verzeichnis Pfad und Anzahl
  enthaltener Dateien, plus Hinweis auf `secondbrain_list`. Bei einem großen Vault ist die
  *Form* nützlicher als eine abgeschnittene Liste.
- Harte Obergrenze 6000 Zeichen für den gesamten Landkarten-Block über alle Vaults;
  bei Überschreitung wird gekürzt und als gekürzt gekennzeichnet.
- Fehlerfall: Landkarte entfällt still, der bisherige Verzeichnis-Hinweis bleibt.
  `get_mounts_context()` fängt heute schon alles ab und gibt im Zweifel `""` zurück.

### 5.2 Alle Prompt-Stellen — vollständige Liste

Sechs Stellen. Werden alle in diesem Release angefasst; keine bleibt widersprüchlich:

| Stelle | Was heute falsch/fehlend ist | Änderung |
|---|---|---|
| `agent_manager.py:329` | „SHARED ACROSS ALL AGENTS" — falsch, `user_id`-begrenzt | korrigieren auf „über die Agenten *dieses Nutzers*" |
| `agent_manager.py:355` | „I have TWO knowledge sources" | drei benennen (Gedächtnis / persönliche KB / Second Brain) |
| `agent_manager.py:556` | „use `grep` … **No special tool needed**" | auf `secondbrain_search` / `secondbrain_write` umstellen |
| `runner_hooks.py:626` | „`grep` the keywords/code across the vault" (in der Funktion, die 5.1 anfasst) | auf `secondbrain_search` umstellen |
| `runner_hooks.py:153` | `CHAT_STARTUP_PREFIX` nennt Second Brain gar nicht | `secondbrain_search` ergänzen |
| `PROACTIVE_PROMPT` (Kontext laden) | Second Brain fehlt | ergänzen |

Dazu der mitgelieferte Skill `secondbrain_lookup` (`main.py:1341`), der wörtlich
`grep -ril "$Q" /mnt/brains/*/` verschickt: wird auf `secondbrain_search` umgeschrieben.
Der grep-Weg bleibt darin als ausdrücklicher Notnagel erwähnt, falls kein Orchestrator
erreichbar ist — konsistent zur Rückfallebene aus 3.2.

Die Entscheidungsregel aus `TASK_STARTUP_PREFIX:93-99` ist gut und bleibt Vorlage — sie
wird auf die anderen Pfade übertragen, nicht neu erfunden.

---

## 6. Sichtbarkeit: ein driftender Index darf nicht still sein

Das `mode`-Feld aus 3.1 wäre ein toter Pfad, wenn es niemand liest. Deshalb:

- **Protokoll:** Liefert `hybrid_search` für ein Brain `mode="grep"`, obwohl der Brain
  Chunks hat, wird eine Warnung geloggt. Das macht Drift sichtbar, statt sie zu verstecken.
- **Admin-UI:** Die bestehende Brain-Verwaltung zeigt je Vault `Dateien im Index` und
  `zuletzt indiziert`. Der manuelle Reindex-Knopf existiert bereits — er bekommt damit
  endlich eine Anzeige, an der man ablesen kann, ob er nötig ist.

---

## 7. Datenfluss

```
SUCHE
  Agent (beide Laufzeiten) ──► secondbrain_search
        │
        ├─ API erreichbar ──► GET /brains/agent/search
        │                          ├─ Token → agent_id
        │                          ├─ agent_id → zugewiesene Brain-Labels (config["mounts"] ∩ Katalog)
        │                          └─ je Brain: hybrid_search
        │                                   ├─ indiziert  → Vektor + Volltext, RRF   → mode="hybrid"
        │                                   └─ nicht ind. → vault.search (grep)      → mode="grep" + Warnung
        │
        └─ API nicht erreichbar ──► lokaler Substring-Walk (Rückfall, wie bisher)

SCHREIBEN
  Agent ──► secondbrain_write
        │
        ├─ API erreichbar ──► POST /brains/agent/write
        │                          ├─ Scoping + Schreibrecht (ro → 403)
        │                          ├─ vault.write_file()  (atomar)
        │                          └─ index_file()        ◄── gleicher Zweischritt wie Admin-UI / MCP / Voice
        │
        └─ API nicht erreichbar ──► lokaler Schreibzugriff (Index folgt per Durchlauf)

NETZ
  Scheduler alle 5 min ──► reindex_vault je Brain ──► nur geänderte Dateien neu einbetten
        fängt: Rückfälle, direkte write_file auf /mnt/brains, Dateien per SSH auf dem Host
```

---

## 8. Verzahnungs-Nachweis

Gegen die Regel „keine Insellösungen" geprüft.

**Bestehendes, das wir aufrufen (statt nachzubauen):**

| Vorhanden | Wird genutzt von |
|---|---|
| `vault_search.hybrid_search()` | neuer Suchendpunkt (3.1) |
| `vault.write_file()` + `vault_indexer.index_file()` | neuer Schreibendpunkt (4.1) — gleicher Zweischritt wie die drei bestehenden Schreibwege |
| `vault_indexer.reindex_vault()` | Scheduler-Durchlauf (4.2) |
| `get_effective_catalog()` / `resolve_agent_mounts()` | Scoping in beiden Endpunkten |
| `verify_agent_token` | Auth in beiden Endpunkten |
| `api_client` + `definitions.py`-Muster von `brain_search` | Werkzeug-Umstellung (3.2) |
| `_tick_failure_watchdog`-Drosselmuster | Scheduler-Durchlauf (4.2) |
| `vault.resolve_path()` (Ausbruchschutz, `.git` gesperrt) | Pfadprüfung im Schreibendpunkt (4.1) |
| Cache-Invalidierung `executor.py:336` | erhält `secondbrain_write` (3.2) |
| Bestehender Reindex-Knopf in der Brain-Verwaltung | bekommt die Anzeige aus Abschnitt 6 |

**Bestehendes, das uns aufruft:**

| Aufrufer | Was er bekommt |
|---|---|
| `TASK_STARTUP_PREFIX` / `CHAT_STARTUP_PREFIX` / `PROACTIVE_PROMPT` | verweisen künftig auf `secondbrain_search` statt auf `grep` |
| `compose_prompt_bundle()` | erhält über `get_mounts_context()` die Landkarte |
| Skill `secondbrain_lookup` | nutzt künftig das Werkzeug |
| Sprach-Sitzung, externer MCP-Endpunkt | **unverändert** — nutzten `hybrid_search` schon korrekt |

**Was ausdrücklich NICHT offen bleibt:**

- Keine Laufzeit außen vor: Codex, Custom-LLM **und** Claude Code bekommen beide Werkzeuge.
- Kein Schreibweg ohne Index: Endpunkt indexiert sofort, Durchlauf fängt den Rest.
- Keine Prompt-Stelle, die noch auf `grep` zeigt: alle sechs plus der Skill werden angefasst.
- Kein Diagnosefeld ohne Leser: `mode` geht in Protokoll und Admin-Anzeige.

---

## 9. Warum alles zusammen ausgeliefert wird

Die Hybrid-Suche **ohne** den Re-Index wäre schlechter als der heutige Zustand.

`_has_chunks` prüft mit `SELECT 1 FROM vault_chunks WHERE brain_label = :b LIMIT 1` — eine
**einzige** Chunk-Zeile genügt, damit ein Vault als „indiziert" gilt. Ein teilweise
veralteter Index fällt deshalb **nie** auf grep zurück; neu geschriebene Dateien wären
schlicht unsichtbar. Ein Agent, der eine gerade geschriebene Anleitung nicht findet, ist
schlimmer als einer, der langsam sucht.

Ebenso: Prompts, die auf `grep` zeigen, machen jedes bessere Werkzeug wirkungslos. Und ein
Werkzeug in nur einer Laufzeit lässt den meistgenutzten Agenten außen vor.

Deshalb: **ein Release.**

---

## 10. Testplan

Getestet wird Verhalten, nicht Formulierung. Prompt-Tests nach dem Muster aus
`orchestrator/tests/test_proactive_prompt_rework.py`; Endpunkt-Tests gegen echtes SQL über
In-Memory-SQLite wie in `test_activity_timeline.py`.

**Suchendpunkt:**
- Ein Agent bekommt **nur** Treffer aus ihm zugewiesenen Brains.
- Ein `label` außerhalb der Zuweisung → 404.
- Agent ohne zugewiesene Brains → leere Trefferliste, kein Fehler.
- Leere Anfrage → 422.
- Nicht indizierter Vault → `mode: "grep"` statt Fehler.

**Schreibendpunkt:**
- `ro`-gemountetes Brain → 403.
- Nicht zugewiesenes Brain → 404.
- Erfolgreicher Schreibzugriff ruft `index_file` auf (der eigentliche Zweck).
- Pfad-Ausbruch (`../`) wird abgewiesen.

**Agenten-Werkzeuge (`agent/tests/`):**
- Erfolgsfall nutzt den API-Weg, nicht den lokalen Walk.
- API-Fehler → lokaler Walk liefert Ergebnisse.
- `secondbrain_search`/`secondbrain_write` existieren in **beiden** Laufzeiten mit
  gleichen Namen und Pflichtparametern (Muster: `agent/tests/test_trigger_tools.py`).
- `secondbrain_search` in `ALWAYS_ALLOWED_TOOLS`, `CONCURRENT_SAFE_TOOLS`, `_CACHEABLE_TOOLS`.
- **Schreiben invalidiert den Cache:** nach `secondbrain_write` liefert eine unmittelbar
  folgende `secondbrain_search` den neuen Stand, nicht den zwischengespeicherten alten.

**Re-Index-Durchlauf:**
- Läuft innerhalb des Intervalls nicht zweimal (Drosselung greift).
- Unveränderte Dateien werden übersprungen (kein Embedding-Aufruf).
- Gelöschte Datei verschwindet aus `vault_chunks`.

**Landkarte:**
- Unter der Schwelle: vollständige Dateiliste.
- Über der Schwelle: Verzeichnis-Übersicht mit Zählern.
- Zeichen-Obergrenze eingehalten, Kürzung gekennzeichnet.
- Kein gemounteter Brain → Block entfällt (heutiges Verhalten).

**Prompt-Wahrheit:**
- „SHARED ACROSS ALL AGENTS" kommt nicht mehr vor.
- Keine der sechs Stellen schickt den Agenten noch primär zu `grep`.
- Chat- und Proaktiv-Pfad nennen `secondbrain_search`.

---

## 11. Risiken

| Risiko | Einschätzung | Gegenmaßnahme |
|---|---|---|
| Ein Agent durchsucht/beschreibt ein fremdes Brain | **hoch, falls falsch gebaut** | Scoping ausschließlich serverseitig aus dem Token; eigene Tests je Endpunkt |
| Landkarte bläht jeden Task-Prompt auf | mittel — trifft jeden Lauf | harte Deckel (120 Dateien / 6000 Zeichen), Verzeichnis-Übersicht bei großen Vaults |
| Prompt-Änderungen verschlechtern das Verhalten aller Agenten | mittel | nur belegbar falsche Aussagen korrigieren, funktionierende Regeln unangetastet; Prompt-Tests |
| Re-Index-Durchlauf kostet IO auf dem Pi | gering | inkrementell und hash-basiert; nur geänderte Dateien werden eingebettet |
| Agent verliert Such-/Schreibfähigkeit bei Orchestrator-Ausfall | gering | lokale Rückfallebene bleibt in beiden Fällen erhalten |
| Neuer MCP-Server bricht Claude-Code-Agenten | mittel | Muster von `trigger_*` (2026-08-06) bereits erprobt; Agent-Start-Log prüfen |

---

## 12. Deployment

- Orchestrator-Neustart (neue Endpunkte, Scheduler-Durchlauf).
- **Agent-Image-Rebuild und Neuerstellung aller Agenten** — Werkzeuge und MCP-Server liegen
  im Image. Codex-Agenten dabei staffeln (geteilter Refresh-Token, siehe
  `codex-shared-auth-recreate-gotcha`).
- Frontend-Rebuild nur für die Index-Anzeige aus Abschnitt 6.
- Nach dem Ausrollen einmal `POST /brains/{id}/reindex` je Vault, damit der Index sofort
  vollständig ist, statt erst über den 5-Minuten-Durchlauf aufzuholen.
