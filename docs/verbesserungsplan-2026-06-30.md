# Verbesserungsplan & Umsetzung — Meeting-Board & Plattform (2026-06-30)

Aus dem Brainstorm: **jedes Ding → Symptom → Ursache → nachhaltige Zahnrad-Lösung → Release → Status.**
„Zahnrad" = verzahnt ins bestehende System, keine Insel: vorhandene Mechanik wiederverwenden, eine Stelle, alle Pfade.
Team-Orchestrierung bewusst ausgelassen (separater Bau).

Status-Legende: ✅ live + verifiziert · 🟡 geplant (Ursache+Lösung steht, Bau offen) · ⛔ extern blockiert.

---

## A — Meeting-Stabilität (Vorlauf, v1.89.x)

### Meeting-Start „Agent is not running" + Stall — ✅ v1.89.2
- **Symptom:** Meeting startet nicht / hängt bei 3/4 Runden, jede Runde „[Agent hat nicht geantwortet]".
- **Ursache:** Teilnehmer-Agenten idle-exiten; Meeting-Turns landen direkt in der Redis-Queue, die nur ein laufender Container liest.
- **Zahnrad:** `_ensure_agent_running()` weckt jeden Teilnehmer vor seinem Turn (+ `start_room` vorab); `create_room` verlangt kein „running" mehr. Nutzt den bestehenden AgentManager.start_agent.

### knowledge.md nicht beschreibbar — ✅ v1.89.3
- **Symptom:** Agent kann Ergebnisse nicht in `knowledge.md` speichern („Permission denied").
- **Ursache:** Datei wird per `write_file_in_container` als **root** geseedet; Agent läuft als uid 1000.
- **Zahnrad:** Beim Anlegen `chown -R 1000:1000 /workspace`; bestehende Agenten einmalig nachgezogen.

### Folgetermin-Endloskette — ✅ v1.89.4
- **Symptom:** „Folgetermin 2, 3, 4, 5, 6 …" — Meetings erzeugen endlos Folgetermine.
- **Ursache:** Jedes Meeting mit Action Items erzeugt einen event-basiert startenden Folgetermin → der erzeugt wieder einen.
- **Zahnrad:** Tiefenbegrenzung `MAX_FOLLOWUP_DEPTH=3` (aus dem Namen abgeleitet).

---

## B — Meeting-Transcript-Hygiene (v1.90.0 / 1.92.1 / 1.92.2)

### #1 Synthese-Truncation — ✅
- **Symptom:** Action-Item-Liste bricht mitten im Wort ab („… Prozessgr").
- **Ursache (verifiziert):** harter `text[:2000]`-Cap in `_execute_custom_llm` (Agent) — NICHT das Prompt-Budget.
- **Zahnrad:** Cap → 12000 (innerhalb `llm_max_tokens`) + Synthese-Prompt gestrafft (max 10 Items, kein Vorwort). Verify: 2654 Zeichen, sauberes Satzende.

### #2 Filler + doppelte Zeilen — ✅
- **Symptom:** „Ich lese zuerst /workspace/knowledge.md" in jedem Turn, doppelte Zeilen, geleakte Tool-Syntax (`<bash> cat …`).
- **Ursache:** Agent-/Moderator-Antwort wird 1:1 gespeichert; Moderator emittiert Vorwort/Tool-Syntax.
- **Zahnrad:** EINE zentrale `_clean_meeting_response()` an allen Capture-Pfaden (Turn + Synthese + Moderator) — strippt Vorwort-Floskeln (inkl. dt. Synonyme Wissensdatei/-basis), Tool-Leak und Dubletten, begrenzt (nukt nie echten Inhalt); zusätzlich „kein Vorwort"-Instruktion im Moderator-Prompt (Quelle). Verify: Moderator-Filler 0 (war 7).

---

## C — Fehler-Observability (v1.91.0, Agent-Image ausgerollt)

### #3 „Unexpected error:" (leer) — ✅
- **Symptom:** Tasks scheitern mit nutzlosem „Unexpected error: ".
- **Ursache:** leeres `str(e)` bei Timeouts/SDK-Fehlern.
- **Zahnrad:** zentraler `format_exception()` (Typname/repr-Fallback) in allen Providern → echter Fehler im Task-`error`.

### #9 „Warum gescheitert?"-Drilldown — ✅
- **Symptom:** kein Fehler-Detail pro Task.
- **Ursache:** nur Aggregat-Analytics; Task-Detail zeigte zwar `error`+Cost, aber Inhalt war leer (#3).
- **Zahnrad:** mit #3 hat das vorhandene Task-Detail jetzt verwertbaren Text — kein neues Datenmodell nötig.

### #8 „Tool nicht vorhanden" — ✅
- **Symptom:** Agent verweigert mit „Tool X nicht vorhanden".
- **Ursache (verifiziert):** `search_tools` wird im System-Prompt NIE erwähnt; Pflicht-Tools (skill_*) nicht im Kern-Set.
- **Zahnrad:** (1) Pflicht-Tools fest in `CORE_TOOL_NAMES`; (2) verbindlicher `search_tools`-Contract in beiden Startup-Prompts. Harness + Prompt.

---

## D — Meeting-Qualität & Robustheit (v1.92.0)

### #6 Redundanz / kein Disput — ✅
- **Symptom:** Agenten leiten 4–5× dasselbe her, widersprechen nie.
- **Ursache:** Moderator-Direktive fordert „mehr vom Gleichen", kein Coverage-Tracking.
- **Zahnrad:** Moderator fordert gezielt NEUES / Lücken / begründeten Widerspruch und verbietet Wiederholung; Teilnehmer-Prompt „neue Substanz". Verify: Agenten bringen Gegenbeispiele/Einwände.

### #5+ Keep-warm während Meeting — ✅
- **Symptom:** Idle-Reaper stoppt Teilnehmer zwischen Turns → Stop/Restart-Churn.
- **Ursache:** Reaper kennt laufende Meetings nicht.
- **Zahnrad:** `_stop_idle_agents` überspringt Agenten, die Teilnehmer eines laufenden Meetings sind (nutzt die bestehende Skip-Logik).

---

## E — Meeting → echtes Artefakt (#7, v1.93.0 / 1.93.2 / 1.93.3)

- **Symptom:** Meeting beschreibt PPT/Doku, erzeugt sie nie.
- **Ursache 1:** `generate-presentation`-Fähigkeit der Agenten ungenutzt. → **Zahnrad:** gegateter Folge-Task `_maybe_generate_artifact` (Muster wie MS-Planner-Mirror), Setting `meeting_artifact_enabled`; rendert Entscheidungsdoku (MD) + Foliendeck (PPTX) nach `/workspace/transfer/`.
- **Ursache 2 (Performance):** Task ging fix an `agent_ids[0]` → Stau hinter dessen Action-Item-Task (serielle Queue). → **Zahnrad:** Routing an den **least-busy** Agenten → parallele Erzeugung.
- **Ursache 3 (eigentlicher Bug):** `db.add` allein → Task blieb ewig PENDING; Agenten ziehen Arbeit aus **Redis**, nicht aus der DB. → **Zahnrad:** `redis.push_task` wie bei den Assignment-Tasks.
- **Status:** ✅ verifiziert — Artefakt-Task COMPLETED, von einem freien Agenten parallel erzeugt; reale Dateien (`…-entscheidung.md` ~4 KB + `.pptx` ~34 KB), PPTX vom Agenten nachgeladen+geprüft.

---

## Security (v1.93.1)

### Approval-Gate-Schwächung — ✅
- **Symptom:** Background-Security-Review flaggt „Approval Gate Weakening" in Meeting-Task-Prompts.
- **Ursache:** Prompts injizierten „… OHNE request_approval" und übersteuerten damit die pro-Agent gesetzte Autonomie-Stufe (bei l1/l2 echter Bypass). Der Grund (leere Whitelist) war seit v1.89.0 (Level-Preset-Fallback) ohnehin weg.
- **Zahnrad:** Bypass-Snippets entfernt — Berechtigungen regelt ausschließlich die echte Autonomie-Whitelist; Agent fordert für Aktionen außerhalb seiner Freigaben regulär request_approval an.

---

## Offen — net-neue Features (geplant)

| # | Feature | Ursache/Motivation | Zahnrad-Ansatz | Status |
|---|---|---|---|---|
| #14 | Meeting-Vorlagen (Daily/Retro/Workshop) | wiederkehrende Meeting-Typen manuell | Templates auf bestehende `stages_config`/Moderator-Mechanik | 🟡 |
| #13 | Self-Improvement-Dashboard | `improvement_engine`+`self_test` laufen unsichtbar | bestehende Services-Daten sichtbar machen (UI) | 🟡 |
| #11 | Concierge-/Admin-Widget | Admin-Fragen brauchen Klickwege | Floating-Widget + read-only Admin-MCP (security-review blockierend) | 🟡 |
| #12 | Voicebot-Realtime | Kundenwunsch | Azure-Realtime-Pfad | ⛔ braucht Kunden-Creds (Endpoint/Deployment/Key/api-version) |

---

**Bilanz:** 9 Releases v1.90.0→v1.93.3, alle live auf skbs-s-kichat + GitHub + mindcode; agent-seitige Releases auf alle 5 Agenten ausgerollt; Security-Fund behoben; #7 end-to-end verifiziert.
