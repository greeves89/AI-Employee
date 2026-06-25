# Konzept: LLM-Observability mit Langfuse (verzahnt)

Status: Konzept / Entscheidungsgrundlage (noch kein Code)
Bezug: AI-Employee v1.70.0
Leitprinzip: Keine Insellösung — Langfuse wird in bestehende Datenflüsse, Patterns,
den Monitoring-Stack und die Admin-UI eingehängt, nicht parallel danebengebaut.

## 1. Ziel & Abgrenzung

Langfuse ergänzt die fehlende **Inhalts-Ebene** der Observability:
- Bestehend: Infra-Metriken (Prometheus/Grafana/Loki in `monitoring/`), aggregierte
  Kosten-/Token-/Erfolgs-KPIs (`/api/v1/analytics/*`, `/api/v1/tasks/cost-attribution`,
  Frontend `analytics/page.tsx`, `cost-attribution.tsx`).
- Neu durch Langfuse: **Tiefen-Traces** je Task (Prompt, RAG-Kontext, Tool-Calls,
  verschachtelte Agentenläufe), **gemessene** statt geschätzte Kosten, Qualitäts-Scores
  (LLM-as-a-Judge), Prompt-Versionierung.

Zwei Ebenen, klare Trennung:

| Ebene | Heimat | Langfuse-Rolle |
|---|---|---|
| Aggregat-KPIs (Kosten/Tokens/Erfolg je Agent/User) | bestehende Analytics/Admin-Views | speist echte Werte ein — NICHT neu nachbauen |
| Tiefen-Traces (einzelner Task end-to-end) | neuer Admin-Tab "Observability" | ist die Quelle — echter Mehrwert |

## 2. UI-Entscheidung: Nativ + Deep-Link

- **Neuer Admin-Tab "Observability"** (admin-only) über das bestehende
  `EMBEDDED_TABS`-Muster in `frontend/src/app/admin/page.tsx` und den Backend-Guard
  `_require_admin` (`orchestrator/app/api/admin.py:26`).
- Tab-Inhalt **nativ** (eure Recharts/Tabellen-Patterns wie Health/Audit-View),
  Daten über einen **Orchestrator-Proxy-Endpoint** auf die Langfuse-API
  (Service-Token serverseitig, kein Browser-Direktzugriff).
- Tiefe Einzel-Trace-Ansicht / Prompt-Management / Eval: **Deep-Link** in neuem Tab
  auf die self-hosted Langfuse-UI.
- **Kein iframe** — die kürzlich gehärteten `X-Frame-Options`/`frame-ancestors`
  (Anti-Clickjacking, Commit 4343415) bleiben unangetastet.
- Verzahnung mit Bestehendem: aus Task-/Analytics-Detail ein "Trace ansehen"-Link
  in den neuen Tab bzw. zur Langfuse-Detailseite — kein doppeltes Kosten-Dashboard.

## 3. Wo Traces entstehen (Backend)

Der Trace-Lebenszyklus liegt exakt auf dem bestehenden Task-Flow — keine neue Pipeline:

- Trace-Start: `TaskRouter.create_and_route_task()` (`orchestrator/app/core/task_router.py:166`)
- Trace-Ende: `handle_task_completion()` **nach** DB-Commit (`task_router.py:376`).
  Hier liegen die **echten** `cost_usd`, `input_tokens`, `output_tokens`, `duration_ms`,
  `num_turns` vom Agent vor (via Redis `task:completions`). Trace wird erst nach dem
  Commit gesendet → DB bleibt die führende Quelle.
- Trace-Kontext aus vorhandenen Feldern, **keine Schema-Änderung**:
  - `task.id` → trace-id
  - `task.agent_id` / `agent.user_id` → User-/Agent-Attribution
  - `task.parent_task_id` → verschachtelte Spans (Subtasks)
  - `task.model`, `started_at`, `completed_at` → Modell + Timing

## 4. Gemessene statt geschätzte Kosten (Kern-Verzahnung)

- Heute: `pricing.py:31 estimate_prompt_cost()` schätzt heuristisch (~4 Zeichen/Token).
- Real: `Task.cost_usd` / `input_tokens` / `output_tokens` werden bereits vom Agent
  geliefert und persistiert (`task_router.py:388`).
- Langfuse macht diese echten Werte historisierbar/auswertbar; der `/estimate`-Pfad
  kann künftig gegen die reale Langfuse-Historie kalibriert werden.
  → Aus "geschätzt" wird "gemessen", im selben Datenfluss.

## 5. Agent-Pfad (drei Modi, unterschiedliche Instrumentierung)

Modus-Auswahl: `agent/app/task_consumer.py:25`.

- `custom_llm`: direkte httpx-Calls in `agent/app/providers/base.py` (`anthropic_provider.py:155`,
  `openai_provider.py:170`). → leicht via Langfuse-Wrapper / OTel-httpx-Instrumentierung.
- `claude_code` / `codex_cli`: CLI-Subprozess (`agent/app/agent_runner.py:98`), Blackbox.
  Tokens/Kosten werden bereits aus der CLI-Ausgabe geparst (`agent_runner.py:134`).
  → OTel-Span um den Subprozess legen, Usage als Span-Attribut mitgeben.
- Env-Injektion ist bereits vorhanden und wird wiederverwendet (kein neuer Weg):
  `_get_secrets_env()` / `_llm_env()` in `orchestrator/app/core/agent_manager.py`.
  Langfuse-Keys + OTel-Endpoint laufen über denselben Mechanismus in den Container.

## 6. Infra-Integration (verzahnt mit bestehendem Stack)

- Langfuse als self-hosted Service in `docker-compose.yml` auf das `internal`-Netz.
- **Eigene DB**: eigenes `langfuse`-Schema/-Datenbank im vorhandenen Postgres
  (Langfuse braucht zusätzlich ClickHouse — als eigener interner Service ergänzen),
  kein Konflikt mit der Orchestrator-DB.
- **Kein `ports:` nach außen** — Zugang ausschließlich über den vorhandenen
  Reverse-Proxy (Caddy/Traefik), gemäß Port-Exposure-Regel.
- Secrets über bestehendes Muster: `langfuse_*` in `orchestrator/app/config.py`
  + Eintrag in `SECRET_KEYS` (`orchestrator/app/services/settings_service.py:14`,
  Fernet-verschlüsselt). Nichts hardcoden.
- Monitoring: Langfuse als zusätzliche Grafana-Datasource neben Prometheus/Loki
  (`monitoring/grafana/provisioning/datasources/`). Infra + LLM-Content unter einem Dach.

## 7. Phasenplan

- **Phase 1 (schnell, hoher Wert):**
  - Langfuse + ClickHouse als Compose-Services (internal, kein Port nach außen).
  - `langfuse_*`-Config + Secrets-Eintrag.
  - Trace-Hook in `handle_task_completion()` (nach Commit), Kontext aus Task-Feldern.
  - Neuer Admin-Tab "Observability" (nativ, Daten via Orchestrator-Proxy) + Deep-Link.
  - Ergebnis: echte Kosten/Tokens pro Task + Trace-Drilldown sichtbar.
- **Phase 2:** Agent-CLI-Pfad via OTel → verschachtelte Agentenläufe als Traces.
- **Phase 3:** LLM-as-a-Judge-Scores (verzahnt mit vorhandenem `TaskRating` /
  `_auto_rate_task()` → direkt auf Langfuse-Scores mappen) + Prompt-Management
  für wiederkehrende System-Prompts.

## 8. Security & Datenschutz

- Self-hosted/On-Prem → keine Prompt-/Nutzerdaten an externes SaaS (passt zur
  Klinik-/Enterprise-Linie und zum PII-Gateway-Ansatz).
- Anti-Clickjacking-Härtung bleibt: kein iframe, Deep-Link statt Embed.
- Admin-only auf Frontend (`role !== "admin"` Guard) UND Backend (`_require_admin`).
- PII vor dem Tracing anonymisieren, wo sensible Daten in Prompts fließen.
- Langfuse-Service nicht direkt exponieren (nur via Reverse-Proxy + Auth).

## 9. Definition of Done (Phase 1)

- [ ] Langfuse (+ ClickHouse) laufen via Compose, nur intern erreichbar.
- [ ] Traces erscheinen pro Task mit echten Tokens/Kosten + Agent/User-Attribution.
- [ ] Admin-Tab "Observability" zeigt Trace-Liste/KPIs nativ; Deep-Link funktioniert.
- [ ] Keine Schema-Migration nötig; bestehende Analytics-Views unverändert lauffähig.
- [ ] `VERSION`-Bump + CHANGELOG-Eintrag nach Update-Workflow.

## 10. Offene Punkte / Entscheidungen für später

- Sampling-Strategie (alle Traces vs. Quote) wegen Volumen/Storage.
- Mapping `TaskRating` → Langfuse-Score (Skala/Namen) final festlegen.
- Aufbewahrungsfristen der Trace-Daten (Datenschutz/Storage).
- SSO/Auth-Brücke für direkten Langfuse-UI-Zugriff (Deep-Link-Ziel).
