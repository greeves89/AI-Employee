# Offene Sicherheitspunkte nach 1.455–1.460 (Stand 14.11.2026)

Mit 1.455 bis 1.460 gibt es für Zugriffe eine gemeinsame Regel. Dieser Plan ordnet die offenen
Punkte daran und legt die Reihenfolge fest.

## Die gemeinsame Regel

Jeder Eingang (REST, WebSocket, Sprache, MCP, Kanal, Kiosk) entscheidet in drei Schritten, immer
an derselben Stelle:

1. **Wer fragt?** Mensch (Sitzung, SSO, IdP-Token), Agent (Agenten-Token) oder Dienstkonto
   (Brain- oder Webhook-Schlüssel). Unbekannt heißt abgewiesen, nie Gast.
2. **Darf er an den Agenten?** `dependencies.require_agent_access`. Standard ist
   **verwalten**. Nur Nutzungs-Endpunkte sagen ausdrücklich `nutzen=True`. Eine neue
   Schnittstelle ist für Nur-Nutzer also zu, bis jemand das anders entscheidet.
   Sichtbarkeitslisten gehen über `core/ownership` (`eigene_agent_ids`,
   `verwaltende_freigaben`).
3. **Darf er an dieses Stück?** Gespräche über `core/chat_besitz`, Meldungen über
   `notifications.empfaenger_id`, Dateien über den eigenen Ordner und den echten Pfad.
   Fremdes liefert 404, nicht 403.

Im Zweifel zu: Kann eine Prüfung nicht entscheiden, etwa wegen eines Datenbankfehlers,
gilt das als abgewiesen.

## Offene Punkte

| # | Punkt | Einordnung in die Regel | Stand | Nächster Schritt |
|---|---|---|---|---|
| 1 | **Kiosk ohne Anmeldung** ist im ganzen Netz der Anlage erreichbar. Caddy sperrt nur Anfragen über den Cloudflare-Tunnel (`Cf-Ray`). Beim Kunden ohne Tunnel kann jeder im LAN Status lesen und mit Agenten chatten. | Schritt 1: Eingang ohne Person | Seit 1.455 nur noch eigene `kiosk-`-Gespräche | Kiosk an eine Geräte-Kennung binden (Token je Kiosk, im Admin anlegen). Ohne Kennung nur Status, kein Chat. Ist ein eigenes Issue, betrifft jede Anlage. |
| 2 | **Automatisches Wecken übergeht den Stopp** durch den Nutzer: Projekt-Takt, Delegation, Kanal-Nachricht. | Schritt 2: Wirkung am Agenten | „Alle aktualisieren“ ist behoben (1.458) | `user_lifecycle.wake_agent` fragt `vom_nutzer_angehalten`. Wecken darf nur der Mensch selbst, Chat oder Start-Knopf. Automatische Wege reihen ein und melden „angehalten“. |
| 3 | **Freigabe „nur nutzen“ live prüfen:** zwei Mitglieder an einem Demo-Agenten, Chat, Sprache, Datei, Entzug. | Schritte 2 und 3 | Unit- und Gegenproben grün, live noch nicht | Zweites Demo-Mitglied anlegen, Klickpfad auf dem Pi durchspielen, Bildschirmfotos fürs Handbuch. |
| 4 | **OAuth mit Entra live:** App-Registrierung, echter Token, Claude Code mit `--client-id`. | Schritt 1 | 68 Tests, zwei Reviews | Daniel legt eine App-Registrierung im eigenen Tenant an (Anleitung im Handbuch 22.5.2), dann Test gegen den Pi. |
| 5 | **GitLab gegen einen echten Server:** gitlab.com mit einem Testprojekt, Board-Import, Merge Request. | Schritt 3 | Proxy-Tausch live geprüft, Anbieter nur nachgestellt | Testprojekt auf gitlab.com mit Projekt-Token, Abnahme laut #1194. |
| 6 | **Sicherung im Update-Skript** scheitert als `pi` (`/var/backups/ai-employee` gehört root). Das Update bricht dann korrekt ab, aber niemand sichert. | Betrieb | Am 14.11. von Hand gesichert | Ordner beim ersten Start dem Repo-Besitzer geben bzw. `SICHERUNG_ZIEL` aus `.env`. Fehlermeldung nennt den Befehl. |
| 7 | **Alte Agenten-Abbilder** halten bei „Stopp“ weiter alle Züge an (gezielter Stopp ab 1.457). | Schritt 3 | Erledigt sich mit dem Aktualisieren der Agenten | Nach dem Einspielen Agenten aktualisieren. Gestoppte bleiben dabei jetzt gestoppt. |
| 8 | **DNS-Rebinding beim Schlüsselabruf des IdP:** zweite Auflösung in httpx. | Schritt 1, nur Admin-Konfiguration | Gering | Denselben Transport mit fester IP wie bei GitLab verwenden (`projekt_git.git_client`), eine Stelle für beide. |
| 9 | **Freigabe-Anfragen an freigegebenen Agenten** gehen nur an den Besitzer. Der Nur-Nutzer wartet, ohne es zu sehen. | Schritt 3 | Kein Leck, aber unklar für den Nutzer | Im Chat des Nur-Nutzers eine Statuszeile „wartet auf Freigabe durch den Besitzer“, ohne den Inhalt der Anfrage. |

## Reihenfolge

1. Punkt 7 sofort nach dem Einspielen. Punkt 6 als kleiner Fix.
2. Punkt 2, dann Punkt 1. Beide wirken auf jeder Anlage.
3. Punkt 3 live mit Bildschirmfotos.
4. Punkte 4 und 5, sobald Testzugänge da sind.
5. Punkte 8 und 9.
