# Bridge als Mitdenker — Plan (Epic)

Stand: 2026-10-26. Vorbild: ein Hardware-Assistent, der den Bildschirm mitliest,
von sich aus Hilfe anbietet, jeden folgenreichen Schritt anhält, bis der Mensch
bestätigt, und vom Handy aus steuerbar ist.

Heute ist die Bridge rein **ausführend**: Sie wartet auf Befehle eines Agenten
(`CommandDispatcher` in `computer-use-bridge/bridge.py`). Sie schaut nie von sich
aus hin, kennt keine Regeln, hält keinen Schritt zur Bestätigung an und kann
den Menschen nur über die Sprach-Bar ansprechen.

## Entscheidungen (Daniel, 2026-10-26)

- Alle fünf Stufen, Stufe für Stufe als eigene Releases.
- Der Beobachter darf **dauerhaft Bildschirmfotos** machen (Capability `observe`,
  aus bis zum Einschalten).

## Grundsätze

- **Zahnrad, keine Insel.** Vorschläge und Aufträge laufen über den normalen Chat
  des Agenten (`agent_chat_bridge.ask_agent_via_chat`, Budgetprüfung
  inklusive). Rückfragen und Freigaben laufen über `approvals.anfrage_anlegen`
  (Glocke, Telegram, iOS, Chat). Das Overlay ist **ein weiterer Kanal** dieser
  Wege, kein eigenes Freigabesystem.
- **Gleich in allen Laufzeiten.** Der Server spricht mit dem Agenten nur über den
  Chat; neue Agentenwerkzeuge kommen in `agent/mcp/computer-use-server.mjs`, das
  alle Laufzeiten laden.
- **Kosten unter Kontrolle.** Die Bridge entscheidet lokal, WANN sich ein Blick
  lohnt (Kontextwechsel, Bild hat sich beruhigt, Mindestabstand). Regeln werden
  lokal abgeglichen und kosten kein Token. Ein Tagesdeckel je Nutzer begrenzt
  die Mitdenk-Läufe.
- **Userbased.** Regeln, Beobachtungen und Vorschläge gehören einem Nutzer;
  jeder Endpunkt prüft die Eigentümerschaft.

## Stufe 1 — Beobachter, Regeln, Overlay (Issue: Stufe 1)

Bridge:
- `ScreenObserver`: liest alle paar Sekunden aktive App und Fenstertitel,
  rechnet einen kleinen Bild-Fingerabdruck (Durchschnitts-Hash). Meldet
  `observation` (App, Titel, Bild als JPEG) nur bei Kontextwechsel oder
  deutlicher Bildänderung, nachdem sich das Bild beruhigt hat, frühestens nach
  dem Mindestabstand.
- Regeln vom Server (`observer_config`) werden lokal gegen App und Titel
  geprüft. Ein Treffer meldet `rule_hit` und zeigt das Angebot sofort im Overlay.
- Overlay oben in der Bildschirmmitte (Ausbau der Interaction Bar): Angebot oder
  Frage, Antwortfeld, Knöpfe Ja / Nein / Stopp / Chat / Ausblenden,
  Schrittzähler.

Orchestrator:
- Tabelle `bridge_regeln` (Nutzer, Agent, Name, Bedingungen, Angebot, Auftrag,
  aktiv, Herkunft). CRUD-API mit Eigentümerprüfung; Änderungen gehen sofort an
  die verbundene Bridge.
- `observation` → Live-Bild-Cache aktualisieren; wenn Mitdenken an und Deckel
  frei: Agent bekommt das Bild mit einer kurzen Beobachter-Anweisung im
  Beobachter-Gesprächsfaden und antwortet mit einem Angebot oder `NICHTS`.
- `rule_hit` → Angebot ohne Modellaufruf.
- `suggestion_response` „Ja“ → Auftrag samt aktuellem Bild in den normalen Chat
  des Agenten; jeder Bridge-Befehl erhöht den Schrittzähler im Overlay; das
  Ergebnis erscheint im Overlay.
- Werkzeuge für den Agenten: Regel anlegen, auflisten, löschen („Merk dir: Wenn
  ich in YouTube Studio bin, biete Titel an“).
- Weboberfläche: Regeln im Computer-Use-Reiter ansehen, ausschalten, löschen;
  Schalter Beobachten / Mitdenken, Tagesdeckel.

## Stufe 2 — Bestätigungstaste (Issue: Stufe 2)

- Folgenreiche Bridge-Aktionen halten an: Senden/Kaufen/Löschen/Bezahlen-Klicks
  (Text des Ziels), Eingaben in Passwortfelder, Shell, App schließen,
  Tastenkürzel wie Beenden. Klassifizierung serverseitig in
  `dispatch_bridge_command`.
- Anhalten = `anfrage_anlegen` (Art `bridge_bestaetigung`). Freigeben am
  Rechner (Overlay, Tastenkürzel) oder überall, wo Freigaben heute ankommen.
- Rückfragen des Agenten (`question` + `options`) erscheinen ebenfalls im
  Overlay und werden dort beantwortet.

## Stufe 3 — Wächter „Sag Bescheid, wenn …“ (Issue: Stufe 3)

- Agentenwerkzeug `bildschirm_waechter`: Bedingung (Titel enthält, sichtbarer
  Text enthält, Bild ändert sich nicht mehr), Meldeweg (Push, Telegram, Mail),
  Ablauf. Geprüft lokal in der Bridge; ein Treffer meldet sich beim Server, der
  über die vorhandenen Benachrichtigungswege zustellt.

## Stufe 4 — Anbindungen selbst einrichten, Geheimnisse lokal (Issue: Stufe 4)

- Agentenwerkzeug `geheimnis_eingeben`: Overlay fragt verdeckt nach dem Wert und
  die Bridge tippt ihn lokal ins fokussierte Feld. Der Wert verlässt den Rechner
  nie und erscheint weder im Modell noch in Protokollen.
- Anleitung/Skill für den Agenten: Einrichtung einer Anbindung im Browser Schritt
  für Schritt, Halt bei jedem Geheimnis.

## Stufe 5 — Lernen aus Beobachtung (Issue: Stufe 5)

- Nur mit Schalter: Die Bridge führt ein lokales Nutzungsprotokoll (App, Titel,
  Dauer — keine Bilder). Einmal täglich wertet der Agent es aus und schlägt
  Regeln vor („Du öffnest jeden Montag …, soll ich …?“). Annehmen legt die Regel
  an.

## Stufe 6 — iOS (eigenes Issue im iOS-Repo, eigene Sitzung)

- Vorschläge, Rückfragen und Bestätigungen des Overlays auch als Push mit
  Face-ID-Freigabe (Server-Seite über `anfrage_anlegen` schon vorhanden).
- Live-Bild ereignisgesteuert statt im 4-s-Takt, Schrittzähler und Stopp-Knopf.
- Wächter-Meldungen und Regeln in der App.

## Prüfen

- Gezielte Tests je Stufe (`orchestrator/tests/test_bridge_*`), keine
  Komplettläufe auf dem Mac. Bridge-Builds über `build-bridge.yml`.
- Sicherheitsprüfung vor jedem Merge (neue Endpunkte, Bilddaten, Geheimnisse).
