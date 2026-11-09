# Plan: Eigene Microsoft-365-Identität je Agent

Stand: 09.11.2026. Entscheidung: **lizenzierter Microsoft-365-Nutzer je Agent** (eigenes Postfach,
eigene Teams-Identität). Der Agent liest und schreibt als er selbst. Die Plattform bekommt ein
Ereignis, wenn bei ihm Mail oder eine Teams-Nachricht eingeht, und weckt ihn.

Dieses Dokument ordnet die Arbeit in das bestehende System ein. Es baut so wenig Neues wie möglich
und nutzt die vorhandenen Wege für Zugang, Rechte, Freigaben, Wecken und Sicherheitsstatus.

## Bestand (geprüft am 09.11.2026)

| Baustein | Heute | Datei |
|---|---|---|
| Microsoft-Anmeldung | Delegiert (Autorisierungscode), **immer als Mensch**: eine Zeile je (Anbieter, Nutzer) in `oauth_integrations`; das SSO-Login überschreibt sie bei jeder Anmeldung | `services/oauth_service.py`, `models/oauth_integration.py`, `services/sso_service.py` |
| Graph-Werkzeuge des Agenten | MCP-Endpunkt mit dem Token des **Besitzers**, alle Aufrufe fest auf `/me/…` | `api/mcp_msgraph.py`, `core/msgraph_mcp.py` |
| Rechte | Nur-Lesen-Schalter (plattformweit, Voreinstellung an), Modus je Agent (lesen/Entwurf/schreiben), Rechte-Matrix `email_m365` | `core/ms_access.py` |
| Mail-Freigabe | Entwurf → Freigabe durch den Besitzer → Senden mit seinem Token | `core/mail_freigabe.py` |
| Eingang Mail | Abfrage: Zeitplan „Postfach sichten“ im Sekretariat | `core/sekretariat.py` |
| Eingang Teams | Abfrage alle 30 s mit dem Token des Besitzers, ohne Oberfläche | `services/teams_gateway.py`, `core/channel_gateway.py` |
| Ereignisse | Auslöser je Agent, Webhook mit Agenten-Token | `api/event_triggers.py`, `api/webhooks.py`, `services/trigger_evaluator.py` |
| Wecken | `TaskRouter.create_and_route_task`, `agent_wakeup.ensure_agent_running` — **Webhook und Kanal-Eingang wecken nicht** | `core/task_router.py`, `core/agent_wakeup.py` |

Graph-Benachrichtigungen (Subscriptions) gibt es noch nicht. Die vorhandene Rückruf-Adresse für
Teams-Anrufe prüft nichts, schreibt aber auch nur ins Protokoll.

## Phasen

Jede Phase ist für sich auslieferbar, mit Tests in beide Richtungen, Sicherheitsreview, Live-Test
und Handbuch.

### Phase 1: Konto des Agenten (eigene Anmeldung)

> **Stand: umgesetzt in 1.445.0.** Abweichungen vom Plan: Die eine Stelle heißt
> `core/agenten_konto.token_fuer`. Die Seite „Heute“ im Sekretariat zeigt bewusst weiter das
> Postfach des Menschen; nur das Bearbeiten eines Agenten-Entwurfs dort nutzt das Postfach des
> Agenten. Der Abgleich „eigenes Konto“ läuft VOR dem Speichern und lehnt ab, wenn das Konto nicht
> feststellbar ist. Offen bis zum Test-Nutzer: der Ende-zu-Ende-Test mit echter Anmeldung.

- **Daten:** `oauth_integrations` bekommt eine Spalte `agent_id` (leer = Konto eines Menschen).
  Eindeutig je (Anbieter, Nutzer) für Menschen, je (Anbieter, Agent) für Agenten. Löscht man den
  Agenten, wird auch das Konto gelöscht. Migration mit Eintrag in der Liste der Migrations-Tests.
- **Anmelden:** Unter Agent → Konnektoren gibt es „Konto des Agenten verbinden“, nur für Besitzer
  und Admin. Der Zustand der Anmeldung trägt die Agenten-Kennung. Microsoft wird mit
  `prompt=select_account` aufgerufen: Der Besitzer meldet sich einmal mit den Zugangsdaten des
  Agenten-Nutzers an. Der Rückruf speichert mit `agent_id`. Das SSO-Login schreibt nur Zeilen
  ohne `agent_id` und kann ein Agenten-Konto nie überschreiben.
- **Ein Weg zum Token:** `ms_token_fuer(agent)` liefert das Konto des Agenten, wenn eines verbunden
  ist, sonst wie heute das des Besitzers. Diesen einen Weg nutzen MCP-Werkzeuge, Teams-Kanal,
  Mail-Freigabe und Sekretariat. Weil das Token delegiert ist, bleibt `/me/…` richtig, es ist dann
  „ich, der Agent“.
- **Erneuern:** Der bestehende Erneuerungslauf deckt die neuen Zeilen ab. Scheitert er (MFA,
  Kennwortwechsel, 90 Tage ohne Nutzung), geht eine Meldung an den Besitzer, und der
  Sicherheitsstatus bekommt den Punkt „Agenten-Konten“.
- **Rechte unverändert:** Nur-Lesen-Schalter, Modus je Agent und Rechte-Matrix gelten genauso.
  Mail aus dem eigenen Postfach des Agenten gibt im Modus „Entwurf“ weiterhin der Besitzer frei.

### Phase 2: Ereignisse statt Abfragen (Graph-Benachrichtigungen)

> **Stand: Mail umgesetzt in 1.446.0** (`core/graph_abos.py`, `api/msgraph_benachrichtigung.py`,
> Tabelle `graph_abos`). Abweichungen vom Plan:
> - Das Geheimnis für `clientState` wird aus `ENCRYPTION_KEY` abgeleitet, nicht gespeichert.
> - Wecken ist opt-in je Agent, mit höchstens 20 Läufen je Stunde.
> - Im Auftrag stehen nur Absender und Betreff, gerahmt als fremder Text.
> - Nach dem Sicherheitsreview gilt:
>   - Nachgesehen wird nach Eingangszeit statt mit `delta`: im Abfrage-Betrieb alle fünf Minuten, bei Benachrichtigung zusätzlich stündlich, höchstens 24 Stunden zurück.
>   - Wecken dürfen voreingestellt nur Absender der eigenen Domäne. Automatische Mails wecken nie.
>   - Es gibt eine Grenze je Absender.
>   - `clientState` trägt die Abo-Kennung und wird ohne Datenbank geprüft.
>   - „Intern“ heißt: Urteil von Exchange `X-MS-Exchange-Organization-AuthAs: Internal`. **Im Ende-zu-Ende-Test mit dem Test-Nutzer prüfen, ob Graph diesen Kopf in `internetMessageHeaders` liefert.** Sonst gelten alle Mails als extern (fail closed), und nur der Haken „Auch Absender außerhalb“ hilft.
> - Das Chat-Abo zieht in Phase 3 um: Ohne eigene Teams-Kennung (`own_user_id`) würde der Agent von seinen eigenen Nachrichten geweckt.
> - Die Rückruf-Adresse für Teams-Anrufe bleibt eigenständig. Anrufe sind keine Graph-Abos und tragen kein `clientState`; sie kommen mit Phase 3.

- **Ein prüfender Empfang:** `POST /api/v1/msgraph/benachrichtigung`.
  - Er beantwortet `validationToken` als reinen Text.
  - Er prüft `clientState` gegen ein Geheimnis je Abonnement (HMAC).
  - Er antwortet sofort mit 202 und verarbeitet danach im Hintergrund.
  - Lebenszyklus-Meldungen (`reauthorizationRequired`, `subscriptionRemoved`, `missed`) bekommen eine eigene Adresse.
  - Die Rückruf-Adresse für Teams-Anrufe zieht auf denselben Prüfer um.
- **Abonnements:** Tabelle `graph_abos` (Agent, Ressource, Ablauf, Geheimnis). Es gibt zwei Abonnements:
  - Posteingang des Agenten (`/me/mailFolders('inbox')/messages`, etwa 3 Tage gültig)
  - Chats des Agenten (`/me/chats/getAllMessages`, etwa 1 Stunde gültig, kostenpflichtige API)

  Der Zeitplan-Dienst verlängert sie vor Ablauf.
- **Wecken:** Mail erzeugt einen Auftrag über `TaskRouter.create_and_route_task`, der den Agenten
  weckt. Teams läuft über `channel_gateway.deliver` und wird um das Wecken ergänzt; die Lücke
  gilt heute auch für Slack, Discord und den Webhook und wird für alle gemeinsam geschlossen.
  Optional gibt es Auslöser mit Quelle `msgraph` (`mail.eingang`, `teams.nachricht`), damit
  Besitzer Bedingungen setzen können.
- **Abfrage als Rückfall:** Ohne öffentlich erreichbare HTTPS-Adresse (Anlage ohne Tunnel) und
  nach einer Meldung `missed` holt eine Abfrage mit `delta` nach, was fehlt.

### Phase 3: Teams als eigene Identität

> **Stand: Sicherheitsteil umgesetzt in 1.447.0.**
> - Es gibt einen Weg zum Senden (`teams_gateway.senden_nach_regeln`) mit derselben Entscheidung wie für Mail (`ms_access.sendemodus`). Bis 1.446 prüfte die Kanal-Antwort nur den Nur-Lesen-Schalter.
> - Gebundene Freigabe `teams_senden` im Modus „Entwurf“, für Werkzeug und Kanal.
> - Schleifenschutz in zwei Halten: Liste gesendeter Nachrichten, auch beim Konto des Besitzers, und bei eigenem Konto die eigene Kennung aus `/me`. Das Feld `own_user_id` setzte vorher niemand.
> - Kanal-Nachrichten wecken gestoppte Agenten, außer bewusst angehaltene.
> - **1.448.0:** Oberfläche und API für die Kanal-Einstellungen (`core/kanaele.py`, Karte „Kanäle“). Bei Teams werden Chats und Kanäle aus Teams ausgewählt. Das Handbuch Kap. 34 entspricht jetzt der Oberfläche.
> - **Offen:** Teams-Chats per Graph-Abo statt Abfrage (Benachrichtigung für Chats). Die Abfrage im Scheduler-Takt funktioniert, ist aber langsamer.

- Die eigene Teams-Kennung des Agenten wird beim Verbinden aus `/me` gesetzt (`own_user_id`).
  Der Agent antwortet sich damit nie selbst. Heute setzt diesen Wert niemand.
- Teams-Nachrichten senden die Kanal-Antwort und das Werkzeug nach denselben Regeln wie Mail:
  Nur-Lesen-Schalter (seit 1.444.1), Modus je Agent, Rechte-Matrix. Im Modus „Entwurf“ gibt es
  eine neue Freigabe `teams_senden`.
- Oberfläche für die Kanal-Einstellung (welche Chats und Kanäle, nur bei Erwähnung). Heute gibt es
  sie nur in der Konfiguration.

### Phase 4: Einführung beim Kunden

- Handbuch: Agenten-Konto verbinden (Klick für Klick mit Bildschirmfotos), Ereignisse, Grenzen.
- Checkliste für die IT des Kunden:
  - Lizenz je Agenten-Nutzer (Postfach und Teams)
  - Regel für Conditional Access und MFA für diese Nutzer (Anmeldung über AI Employee)
  - Zustimmung zu den Berechtigungen
  - Öffentliche HTTPS-Adresse für Benachrichtigungen, oder der Hinweis, dass ohne sie abgefragt wird

## Risiken und Antworten

| Risiko | Antwort im Plan |
|---|---|
| Ein delegiertes Token hängt an einer interaktiven Anmeldung; MFA, Conditional Access oder ein Kennwortwechsel beenden es | Meldung an den Besitzer und ein Punkt im Sicherheitsstatus. Erneut verbinden geht mit einem Klick. Die Regel für die Agenten-Nutzer klärt die IT des Kunden (Phase 4). |
| App-only wäre robuster, gilt aber ohne Richtlinie für ALLE Postfächer des Mandanten und kann keine Teams-Chats als Nutzer senden | Bewusst delegiert: Der Agent kann nur, was sein Nutzer kann. |
| Der Empfang der Benachrichtigungen ist öffentlich | `clientState`-Prüfung je Abonnement, Antwort sofort, Verarbeitung entkoppelt. Kein Pfad ohne Prüfung. |
| Ablaufende Abonnements (Chats: 1 Stunde) | Verlängerung im Zeitplan-Dienst, Abfrage als Rückfall, `missed` wird nachgeholt. |
| Der Agent antwortet sich selbst | `own_user_id` aus `/me` beim Verbinden (Phase 3). |

## Was zuerst gebraucht wird

- Ein **Test-Nutzer mit Lizenz** in einem Test-Mandanten (Postfach und Teams) für die Live-Tests.
  Ohne ihn lassen sich Phase 1 und Phase 2 nur mit nachgebauten Graph-Antworten prüfen.
- Für Phase 2: die öffentliche HTTPS-Adresse der Anlage (beim Kunden: wie erreicht Microsoft den
  Server?).
