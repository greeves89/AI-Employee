# Second-Brain-Retrieval: das Bessere anstöpseln

**Datum:** 2026-08-06
**Status:** Entwurf zur Freigabe
**Auslöser:** Architekturvergleich mit EdubaWare („Multiplayer AI", Jake Van Clief, 2026-08-06).
Die Frage war: ist unser Wissensansatz state of the art?

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

**Die vier Lücken, die dieses Dokument schließt:**

| # | Lücke | Fundstelle |
|---|---|---|
| 1 | Der Agent nutzt die Hybrid-Suche nicht. Sein `secondbrain_search` ist ein Substring-Zähler über alle Dateien. Die Hybrid-Suche erreichen nur der externe MCP-Endpunkt (n8n, Cursor) und die Sprach-Sitzung. | `agent/app/tools/executor.py:971` |
| 2 | Der Vault-Index driftet. Schreibt ein Agent in den Vault — und die Prompts sagen ihm ausdrücklich, er solle das mit dem normalen Schreib-Werkzeug tun — wird `vault_chunks` nie aktualisiert. Kein Watcher, kein Cron. | kein Aufrufer außer Admin-UI, Reindex-Knopf, externer MCP, Voice |
| 3 | Die Datei-Landkarte wird nie eingespielt. Wir injizieren den Verzeichnis*namen*, aber keinen Dateibaum. | `agent/app/runner_hooks.py:597` |
| 4 | Die Prompts widersprechen sich: `DEFAULT_CLAUDE_MD` spricht von „TWO knowledge sources" und behauptet, die Wissensdatenbank sei „SHARED ACROSS ALL AGENTS" — sie ist `user_id`-begrenzt. Chat- und Proaktiv-Pfad kennen den Second Brain gar nicht. | `orchestrator/app/core/agent_manager.py:329,355`; `agent/app/runner_hooks.py:153` |

Kurios als Randnotiz: **n8n durchsucht unseren Vault heute besser als unsere eigenen Agenten.**

---

## 2. Nicht-Ziele

Bewusst ausgeschlossen, um den Umfang beherrschbar zu halten:

- **Kein neues Retrieval-System.** Alles, was hier gebaut wird, verdrahtet vorhandenen
  Code. Kein neues Embedding-Modell, keine neue Datenbank, kein neuer Dienst.
- **Keine Schreib-Koordination / kein Locking.** Das ist ein eigenes Thema (mehrere
  Agenten schreiben dieselbe Datei) und wird hier nicht angefasst.
- **Kein Umbau der persönlichen Wissensdatenbank (`brain_search`) oder des
  Agenten-Gedächtnisses (`memory_search`).** Beide sind sauber angeschlossen und
  funktionieren.
- **Keine Korrektur der UI-Wissenssuche.** Dass der Knowledge-Tab nur `ILIKE` kann, ist ein
  echter Befund, betrifft aber Menschen, nicht Agenten. Separat zu behandeln.

---

## 3. Architektur

### Stufe 1 — Landkarte und Prompt-Wahrheit

Billig, sofort spürbar, kaum Risiko. Erreicht **alle** Laufzeiten, weil beides im
gemeinsamen Kontext-Bündel bzw. in den zentralen Prompts sitzt.

#### 3.1 Datei-Landkarte im Startkontext

Einbauort: `get_mounts_context()` in `agent/app/runner_hooks.py:597` — die eine Stelle,
an der alle Laufzeiten ihre Mount-Kenntnis herbekommen, auch `custom_llm`.

Heute wird pro Brain nur `- /mnt/brains/<slug>` ausgegeben. Künftig zusätzlich ein
begrenzter Dateibaum. Die Begrenzung ist der entscheidende Teil, weil dieser Block in
**jeden** Task-Prompt geht:

- Nur `.md`, `.markdown`, `.txt`; `.git` wird übersprungen.
- **Bis 120 Dateien je Vault:** vollständige Liste relativer Pfade, alphabetisch.
- **Darüber:** Verzeichnis-Übersicht statt Dateiliste — je Verzeichnis der Pfad und die
  Anzahl enthaltener Dateien, plus Hinweis auf `secondbrain_list` für die Tiefe. Bei einem
  großen Vault ist die *Form* nützlicher als eine abgeschnittene Liste.
- Harte Obergrenze 6000 Zeichen für den gesamten Landkarten-Block über alle Vaults; wird
  sie erreicht, wird abgeschnitten und der Abschnitt als gekürzt gekennzeichnet.
- Fehlerfall (Verzeichnis nicht lesbar): Landkarte entfällt still, der bisherige
  Verzeichnis-Hinweis bleibt. `get_mounts_context()` fängt heute schon alles ab und gibt
  im Zweifel `""` zurück — dieses Verhalten bleibt.

#### 3.2 Prompt-Widersprüche geraderücken

- `DEFAULT_CLAUDE_MD` (`agent_manager.py:329`, `:355`): „TWO knowledge sources" → drei
  benennen; die falsche Behauptung „SHARED ACROSS ALL AGENTS" korrigieren auf
  „geteilt über die Agenten *dieses Nutzers*".
- `CHAT_STARTUP_PREFIX` (`runner_hooks.py:153`): `secondbrain_search` ergänzen — im Chat
  fehlt der Second Brain heute vollständig.
- `PROACTIVE_PROMPT` (`agent_manager.py`, Schritt „Kontext laden"): Second Brain ergänzen.

Die Entscheidungsregel aus `TASK_STARTUP_PREFIX:93-99` ist gut und bleibt Vorlage — sie
wird auf die anderen Pfade übertragen, nicht neu erfunden.

### Stufe 2 — die Hybrid-Suche anstöpseln

Die beiden Teile dieser Stufe **müssen zusammen ausgeliefert werden**. Begründung siehe
Abschnitt 5.

#### 3.3 Neuer agenten-authentifizierter Suchendpunkt

`GET /brains/agent/search?q=<text>&limit=<n>&label=<optional>`

- **Auth:** `verify_agent_token` — dieselbe Absicherung wie bei den übrigen
  agenten-seitigen Endpunkten. Die `agent_id` kommt aus dem Token, nie aus der Anfrage.
- **Scoping (sicherheitskritisch):** Der Endpunkt ermittelt die dem Agenten zugewiesenen
  Mount-Labels aus `Agent.config["mounts"]`, schneidet sie gegen den effektiven Katalog
  (`get_effective_catalog`) und behält nur `SecondBrain`-Einträge, die aktiv sind. Ein
  Agent darf **ausschließlich** Brains durchsuchen, die ihm zugewiesen sind. Ein
  mitgeschicktes `label`, das nicht in dieser Menge liegt, führt zu 404 — nicht zu einer
  leeren Trefferliste, damit ein Tippfehler nicht wie „nichts gefunden" aussieht.
- **Ausführung:** je zugewiesenem Brain
  `vault_search.hybrid_search(db, brain.label, brain.host_path, q, limit)`. Die Funktion
  degradiert bereits von selbst auf `vault.search()` (grep), wenn ein Vault nicht indiziert
  ist — dieses Verhalten wird bewusst genutzt, nicht ersetzt.
- **Antwort:** `{"mode": "hybrid"|"grep", "hits": [{"brain": <slug>, "path": ..., "snippets": [...]}]}`.
  Das `mode`-Feld ist Diagnose: es macht sichtbar, ob echt semantisch gesucht wurde oder
  nur gegriffen — ohne dieses Feld ist ein driftender Index unsichtbar.
- Ergebnisse mehrerer Brains werden zusammengeführt und auf `limit` gekürzt.

#### 3.4 Agenten-Tool auf den API-Weg umstellen

`secondbrain_search` ruft künftig diesen Endpunkt über den `api_client` auf — Vorbild ist
`brain_search` (`agent/app/tools/api_client.py:625`), das genau so schon funktioniert.

**Der heutige lokale Substring-Walk bleibt als Rückfallebene erhalten.** Ist der
Orchestrator nicht erreichbar, sucht der Agent wie bisher lokal weiter, statt blind zu
werden. Das ist kein Schönheitsfehler, sondern Absicht: der Vault ist ins Dateisystem
gemountet und damit auch ohne Orchestrator lesbar.

`secondbrain_search` steht heute in `ALWAYS_ALLOWED_TOOLS` (Lesewerkzeug, keine
Autonomie-Freigabe nötig) — daran ändert der Transportweg nichts.

**Entscheidung:** `secondbrain_search` wird zusätzlich in `CONCURRENT_SAFE_TOOLS` und
`_CACHEABLE_TOOLS` aufgenommen. Heute steht es dort **nicht** — als lokaler Walk über
alle Dateien war das auch richtig. Als API-Aufruf ist es fachlich dasselbe wie
`brain_search`, das in beiden Mengen steht. Damit darf es nebenläufig laufen und sein
Ergebnis wird wie andere Lesezugriffe für 120 Sekunden zwischengespeichert.

#### 3.5 Re-Index-Durchlauf im Scheduler

Neuer `_tick_vault_reindex()` in `orchestrator/app/services/scheduler_service.py`, gedrosselt
nach dem Muster von `_tick_failure_watchdog` (fauler Aufruf aus der Hauptschleife, eigener
Zeitstempel, Intervall **5 Minuten** — dieselbe Taktung wie der bestehende
Git-Autocommit-Timer, damit Index und Verlauf im Gleichschritt laufen).

Je aktivem Brain: `reindex_vault(db, brain.label, brain.host_path)`. Die Funktion ist
bereits inkrementell und datei-hash-basiert — unveränderte Dateien werden übersprungen,
nur geänderte werden neu eingebettet. Der Durchlauf kostet also im Normalfall Datei-IO und
Hashing, keine Embedding-Aufrufe.

**Bewusst verworfene Alternative:** ein Dateisystem-Watcher (inotify). Er würde schneller
reagieren, braucht aber zusätzliche Mechanik und ist über Bind-Mounts hinweg
erfahrungsgemäß unzuverlässig. Der periodische Durchlauf fügt sich in ein Muster ein, das
im Scheduler bereits dreimal existiert.

**Bekannte Einschränkung:** Zwischen Schreibvorgang und nächstem Durchlauf können bis zu
fünf Minuten vergehen, in denen die Hybrid-Suche den alten Stand liefert. Für ein
Wissens-Vault ist das vertretbar. Wer sofort sucht, was er gerade selbst geschrieben hat,
findet die Datei ohnehin über den grep-Rückfall.

### Stufe 3 — Gleichstand der Laufzeiten (Folgearbeit, nicht in diesem Umfang)

Claude-Code-Agenten haben **gar kein** `secondbrain_*`-Werkzeug; in `agent/mcp/` gibt es
nur `brain-server.mjs` und `memory-server.mjs`. Sie erreichen den Vault ausschließlich über
generisches `grep`/`Read`/`bash`.

**Das heißt für Stufe 1+2 ehrlich:** Die verbesserte Suche landet zunächst nur bei
Codex- und Custom-LLM-Agenten. Claude-Code-Agenten bekommen aus diesem Umfang die
Datei-Landkarte und die Prompt-Korrekturen — beides wirkt auch dort, weil es im
gemeinsamen Bündel sitzt — aber nicht die Hybrid-Suche.

Stufe 3 schließt das mit einem `secondbrain-server.mjs` nach dem Muster, das bei den
`trigger_*`-Werkzeugen am 2026-08-06 bereits angewandt wurde (Python-Definitionen **und**
MCP-Server, gleiche Werkzeugnamen, gleiche Parameter).

---

## 4. Datenfluss

```
Agent stellt Frage
      │
      ▼
secondbrain_search (Agent-Container)
      │
      ├─ API erreichbar ──► GET /brains/agent/search
      │                          │
      │                          ├─ Token → agent_id
      │                          ├─ agent_id → zugewiesene Brain-Labels (config["mounts"] ∩ Katalog)
      │                          └─ je Brain: hybrid_search
      │                                   ├─ indiziert  → Vektor + Volltext, RRF-Fusion   → mode="hybrid"
      │                                   └─ nicht ind. → vault.search (grep)             → mode="grep"
      │
      └─ API nicht erreichbar ──► lokaler Substring-Walk über /mnt/brains (wie bisher)

Parallel, unabhängig:
Scheduler alle 5 min ──► reindex_vault je Brain ──► nur geänderte Dateien neu einbetten
```

---

## 5. Warum die Reihenfolge zwingend ist

Die Hybrid-Suche **ohne** den Re-Index wäre schlechter als der heutige Zustand.

Grep findet immerhin, was tatsächlich auf der Platte steht. Ein veralteter Vektor-Index
liefert dagegen selbstbewusst Treffer von gestern — und weil `hybrid_search` nur dann auf
grep zurückfällt, wenn ein Vault **gar keine** Chunks hat, würde ein teilweise veralteter
Index die neuen Dateien schlicht nie zeigen. Ein Agent, der eine gerade geschriebene
Anleitung nicht findet, ist schlimmer als einer, der langsam sucht.

Deshalb: 3.3, 3.4 und 3.5 gehen zusammen live oder gar nicht.

---

## 6. Testplan

Getestet wird das Verhalten, nicht die Formulierung. Die Prompt-Tests folgen dem Muster
aus `orchestrator/tests/test_proactive_prompt_rework.py`.

**Endpunkt (`orchestrator/tests/`, echtes SQL über In-Memory-SQLite wie in
`test_activity_timeline.py`):**
- Ein Agent bekommt **nur** Treffer aus ihm zugewiesenen Brains.
- Ein `label`, das dem Agenten nicht zugewiesen ist → 404.
- Ein Agent ohne zugewiesene Brains → leere Trefferliste, kein Fehler.
- Leere Anfrage → 422.
- Nicht indizierter Vault → Antwort mit `mode: "grep"` statt Fehler.

**Agenten-Werkzeug (`agent/tests/`):**
- Erfolgsfall nutzt den API-Weg (nicht den lokalen Walk).
- Schlägt der API-Aufruf fehl, greift der lokale Walk und liefert Ergebnisse.
- Der lokale Walk selbst bleibt funktionsfähig (Bestandsschutz für den Rückfall).
- `secondbrain_search` ist in `ALWAYS_ALLOWED_TOOLS`, `CONCURRENT_SAFE_TOOLS` und
  `_CACHEABLE_TOOLS` — analog zu `brain_search`.

**Re-Index-Durchlauf:**
- Läuft innerhalb des Intervalls **nicht** zweimal (Drosselung greift).
- Unveränderte Dateien werden übersprungen (kein Embedding-Aufruf).
- Eine gelöschte Datei verschwindet aus `vault_chunks`.

**Datei-Landkarte:**
- Unter der Schwelle: vollständige Dateiliste.
- Über der Schwelle: Verzeichnis-Übersicht mit Zählern statt Liste.
- Zeichen-Obergrenze wird eingehalten und die Kürzung ist gekennzeichnet.
- Kein gemounteter Brain → Block entfällt vollständig (heutiges Verhalten).

**Prompt-Wahrheit:**
- Die Behauptung „SHARED ACROSS ALL AGENTS" kommt in `DEFAULT_CLAUDE_MD` nicht mehr vor.
- Chat- und Proaktiv-Pfad nennen `secondbrain_search`.

---

## 7. Risiken

| Risiko | Einschätzung | Gegenmaßnahme |
|---|---|---|
| Landkarte bläht jeden Task-Prompt auf | mittel — trifft jeden Lauf | harte Deckel (120 Dateien / 6000 Zeichen), Verzeichnis-Übersicht bei großen Vaults |
| Re-Index-Durchlauf kostet IO auf dem Pi | gering | inkrementell und hash-basiert; nur geänderte Dateien werden eingebettet |
| Agent verliert Suchfähigkeit bei Orchestrator-Ausfall | gering | lokaler Walk bleibt als Rückfallebene erhalten |
| Ein Agent durchsucht ein fremdes Brain | **hoch, falls falsch gebaut** | Scoping ausschließlich serverseitig aus dem Token abgeleitet; eigener Test |
| Prompt-Änderungen verschlechtern das Verhalten aller Agenten | mittel | nur Korrekturen belegbar falscher Aussagen, keine Umformulierung funktionierender Regeln; Prompt-Tests |
| Claude-Code-Agenten profitieren nur teilweise | bekannt, akzeptiert | in Abschnitt 3 offen benannt; Stufe 3 schließt es |

---

## 8. Umsetzungsreihenfolge

1. Stufe 1 (Landkarte + Prompt-Korrekturen) — eigenständig lauffähig, sofort ausrollbar.
2. Stufe 2 (Endpunkt + Werkzeug-Umstellung + Re-Index-Durchlauf) — gemeinsam.
3. Release als ein Versionssprung; Deployment: Orchestrator-Neustart, Frontend unberührt,
   **Agent-Image-Rebuild und Neuerstellung aller Agenten** (das Werkzeug liegt im Image).
4. Stufe 3 (Claude-Code-Parität) als eigener Folge-Release.
