# Transkript-zu-Aufgaben (Meeting-Aufgaben-Agent)

Liest pro User die Teams-Transkripte der eigenen Meetings über Microsoft Graph
(delegiert), extrahiert die Aufgaben des angemeldeten Users und legt sie als
To-dos unter „Meine Aufgaben" an (Microsoft To Do, in der Planner-App sichtbar) –
bewusst keinem Plan zugeordnet: Die Aufgaben landen als Inbox, der User sortiert
selbst (Human in the loop).

## Sicherheitsmodell

- **Delegiert, kein App-only.** Jeder Agent arbeitet ausschließlich mit dem
  OAuth-Token seines Besitzers. Graph wertet jeden Call im Kontext dieses Users
  aus: Der Agent kann genau die Transkripte lesen, die der User selbst in Teams
  öffnen könnte – fremde Meetings sind serverseitig unerreichbar (403).
  Tenantweites Lesen würde eine Application-Permission plus Application Access
  Policy erfordern; beides wird nicht beantragt.
- Aufgaben anderer Personen werden nie angelegt, nur im Abschlussbericht genannt.
- Duplikatschutz über die transcript_id im Notizfeld der Aufgabe.
- Vertrauliche Nebeninhalte (Telefonate, Patienten-/Personaldaten) dürfen laut
  Playbook nie in Aufgabentexte übernommen werden.
- Hinweis Datenhaltung: Transkripttext wird nicht dauerhaft gespeichert, läuft
  aber durch den Chat-/Tool-Verlauf des Agenten. Retention-Anforderungen an die
  Plattform-Historie hier ansetzen.

## Voraussetzungen (einmalig, Tenant-Admin)

1. An der bestehenden App-Registrierung zwei **delegierte** Graph-Berechtigungen
   ergänzen:
   - `OnlineMeetings.Read`
   - `OnlineMeetingTranscript.Read.All` (Admin-Consent erforderlich)
   `Calendars.ReadWrite` und `Tasks.ReadWrite` sind in der Standard-Scope-Liste
   der Plattform bereits enthalten.
2. **Nach der Änderung müssen sich alle User einmal neu anmelden** (Re-Consent),
   damit die Refresh-Tokens die neuen Scopes abdecken.
3. Offene Verifikation je nach Tenant-Policy: Transkripte sind delegiert u. U.
   nur für den **Organisator** eines Meetings lesbar. Der Agent überspringt
   nicht lesbare Meetings (403) kommentarlos.

## Aktivierung (Plattform-Admin)

1. Admin-Einstellungen → **„Microsoft Read-only" ausschalten** (plattformweiter
   Schreibschutz, Standard: an). Ohne das sieht kein Agent Schreib-Tools.
2. Agent aus der Vorlage **„Meeting-Aufgaben"** anlegen (Template-Galerie).
3. Am Agenten unter Integrationen **`msgraph_access` auf „write"** stellen
   (Standard: read) und den Agenten neu starten.
4. Der User verbindet unter **Integrationen → Microsoft 365** sein Konto
   (OAuth-Flow; unabhängig vom SSO-Login).

## Testlauf

1. Teams-Termin einstellen, Transkription aktivieren, ein paar Aufgaben klar
   zuweisen („X macht bis Freitag Y").
2. Im Agent-Chat: *„Geh meine Meetings von heute durch und leg meine Aufgaben an."*
3. Erwartet: eigene Aufgaben erscheinen unter „Meine Aufgaben" (mit Quellvermerk
   im Notizfeld), fremde Aufgaben nur im Bericht, zweiter Lauf legt nichts
   doppelt an.
4. Dauerbetrieb per Zuruf im Chat: *„Richte dir einen täglichen Lauf um 17 Uhr
   ein."* (Agent nutzt `create_schedule`.)

## Technik-Überblick

- Neue MCP-Tools (`orchestrator/app/core/msgraph_mcp.py`):
  `ms_list_meeting_transcripts` (calendarView → onlineMeeting per joinWebUrl →
  transcript_ids; mehrere Fragmente pro Meeting bei Stopp/Weiter) und
  `ms_get_meeting_transcript` (Inhalt, standardmäßig als Sprecher-Dialog
  kondensiert statt Roh-VTT; `raw` liefert das Original).
- Agent-Vorlage `meeting-tasks` (`agent_templates.py`) mit dem vollständigen
  Playbook.
- Scopes in `oauth_providers.py` (Single Source of truth, SSO-Login zieht sie mit).
- Tests: `orchestrator/tests/test_msgraph_transcripts.py`.
