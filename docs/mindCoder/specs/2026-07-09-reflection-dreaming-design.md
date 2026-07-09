# Reflection / Dreaming — Design

**Datum:** 2026-07-09
**Branch:** `feature/reflection-dreaming`
**Status:** vom User freigegeben (Ansatz A, "alles in einem Wurf", Hybrid-Review, naechtlich, zweistufiges Wissen)

## Ziel

Out-of-band-Reflection ("Dreaming"): Ein naechtlicher Orchestrator-Job liest die Session-Transcripts
(ChatMessage/TaskStep) des Tages, extrahiert Fakten/Learnings/Team-Wissen/Skill-Kandidaten und schreibt
sie ueber die BESTEHENDEN Pfade (save_memory, KnowledgeEntry, Skill-Marketplace-DRAFT). Der Admin sieht
und steuert alles ueber vorhandene UI-Zahnraeder (Dashboard-Karte, Approvals, Memory-Tab, Audit).

Leitprinzip UX: "Ein Mitarbeiter, der nachts aufraeumt und morgens einen Zettel hinlegt."
Keine Insellösung: keine neue Dedup-/Konflikt-/Embedding-Logik — alles erbt von `save_memory()`.

## Entscheidungen (User)

| Frage | Entscheidung |
|---|---|
| v1-Scope | Alles in einem Wurf (4 Module + komplettes Tracking) |
| Review-Modus | Hybrid: Neues auto, Eingriffe in Bestehendes via Approvals; pro Installation umschaltbar |
| Frequenz | Naechtlich (Default 03:00 lokal, konfigurierbar) |
| Wissens-Scope | Zweistufig: Agent-Memory pro Agent + Team-Erkenntnisse in KnowledgeEntry/Second Brain |
| Architektur | A: Orchestrator-Service im Scheduler-Loop (Muster ImprovementEngine) |
| Default | Feature aus bei Bestandsinstallationen (Opt-in) |

## Datenmodell

1. `agent_memories.source` (String, nullable): `agent | user | conversation | reflection | improvement | compaction`.
   Bestand bleibt NULL. Befuellung an allen Schreibstellen.
2. Neue Tabelle `reflection_runs`: id, started_at, finished_at, status (running/completed/failed/budget_exceeded),
   stats JSON (transcripts_read, facts_new, facts_superseded, pending_approvals, skills_drafted, kb_entries, skipped),
   cost_usd, error. Watermark pro Agent in stats bzw. Setting `reflection_watermark`.

## Job-Ablauf (reflection_service.py)

1. Sammeln: ChatMessage + TaskStep seit Watermark, pro Agent gebuendelt, Deckel `reflection_max_transcripts` (30).
2. Extrahieren: 1 LLM-Call (haiku, gleiches Plumbing wie ImprovementEngine) pro Buendel ->
   strukturierte Liste {facts, learnings, team_insights, skill_candidates} mit importance/confidence.
   Transcript-Inhalte gelten als untrusted (Injection-Haertung im Prompt).
3. Schreiben:
   - Neue Fakten/Learnings -> save_memory(source=reflection) -> AUTO (Dedup/409 laeuft mit).
   - Supersede bestehender Memories oder 409-Widerspruch -> Approval-Eintrag Typ `memory_change`
     (Payload = kompletter vorgeschlagener Write, Vorher/Nachher). Apply erst bei Admin-Approve.
   - Team-Insights -> KnowledgeEntry (neu = auto, Aenderung bestehender = Approval).
   - Skill-Kandidaten (Tasks Rating >= 4) -> Marketplace DRAFT (Pfad wie trend_service; Freigabe-Flow existiert).
4. Abschliessen: reflection_runs-Zeile, Audit-Events, optional Telegram-Digest.

Fehler: Buendel unabhaengig; LLM-Fehler ueberspringt Buendel (Watermark rueckt fuer den Agenten nicht vor).
Token-Budget-Deckel bricht sauber ab (status=budget_exceeded), Rest am Folgetag.

## Vier Module

| Modul | Quelle | Ziel | Modus |
|---|---|---|---|
| Transcript-Reflection | ChatMessage/TaskStep | Agent-Memory | auto / Approval bei Konflikt |
| Skill-Distillation | Tasks Rating >= 4 | Marketplace DRAFT | immer Freigabe (existiert) |
| Meeting-Konsolidierung | Meeting-Verlaeufe nach Ende | KnowledgeEntry/Brain | neu auto, Aenderung Approval |
| Kompaktierungs-Rettung | Rolling-Summary-Delta (agent-seitig, sofort) | Agent-Memory imp=2 source=compaction | auto |

Modul 4 ist der einzige agent-seitige Eingriff: context_compressor -> api_client.memory_save.

## Tracking & UI

- Dashboard-Karte "Nachtschicht": Ergebniszeile + Button zu Freigaben.
- Approvals-Seite: Tab "Dreaming", Vorher/Nachher nebeneinander, Uebernehmen/Verwerfen. Kein Fachjargon.
- Memory-Tab: Herkunfts-Badge (source), Filter "Nur Nachtschicht", Aufklapper "Verlauf" = Supersede-Kette als Zeitleiste.
- Audit: Events pro Lauf + pro Einzelaenderung.
- Settings (eine Admin-Karte): An/Aus, Uhrzeit, Modus (Automatisch/Ausgewogen[Default]/Alles freigeben), Token-Budget.
- DE/EN, lucide-Icons, keine Emojis.

## Kosten & Sicherheit

- haiku, hartes Token-Budget pro Lauf (Default 200k). Kosten sichtbar in reflection_runs.
- Mandanten-Grenzen wie ueberall: pro User/Agent gescoped; Team-Wissen nur in berechtigte Brains.
- Prompt-Injection: Transcripts sind Daten, nicht Instruktionen.

## Tests

- MCDC-Matrix Review-Modus (auto/hybrid/strict) x Schreibfall (neu/supersede/409-Widerspruch).
- Integration: Dedup-Vererbung (0.92/0.88), Approval-Apply fuehrt exakt den Payload-Write aus,
  Watermark-Fortschritt bei Teilfehlern, Budget-Abbruch.

## Rollout

Feature-Branch, 2 Alembic-Migrationen, Versionsbump (minor), CHANGELOG, Handbuch-Kapitel "Nachtschicht"
mit Screenshots, Deploy: Pi zuerst; SKBS unberuehrt (pullt selbst, Feature default aus).
