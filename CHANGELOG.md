# Changelog

All notable changes to AI-Employee are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) · Versioning: [SemVer](https://semver.org/)

---

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
