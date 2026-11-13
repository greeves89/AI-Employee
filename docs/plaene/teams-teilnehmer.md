# Agent als echter Teilnehmer in Teams-Besprechungen (Stufe 3)

Wunsch (Daniel, 13.11.2026): Der Agent sitzt in der Teams-App als Teilnehmer. Er steht in der
Teilnehmerliste, hört durchgehend zu und spricht, wenn man ihn anspricht. Vorbild „Claude
Meetings“. Stufe 1 (Seite, Hinweis, Gäste-Regel, Protokoll ohne Werkzeuge) läuft seit
1.451/1.452.

## Warum nicht der bestehende Weg

`core/teams_calling.py` nutzt Graph Communications mit *service-hosted media*:
- Der Agent spricht abwechselnd einen Satz und nimmt dann eine Antwort auf, wie ein Funkgerät.
- Durchgehend zuhören geht nur mit *application-hosted media*. Das gibt es nur als .NET-SDK
  auf Windows-Servern, mit offenen Medienports und `Calls.AccessMedia.All`.
- Auf Linux bzw. dem Pi läuft das nicht. Jeder Kunde bräuchte eine Azure-Bot-Registrierung.

## Gewählter Weg: Browser-Teilnehmer (noch offen: Okay von Daniel)

Ein Behälter je laufender Besprechung mit einem headless Chromium (Playwright). Er tritt über
den **Beitrittslink** bei, wie ein Gast in Teams im Browser, mit dem Namen „<Agent> (KI)“. Ein
Mensch lässt ihn aus dem Wartebereich herein.

**Audio ohne virtuelle Soundkarte**, nur mit Skript in der Seite:
- **Hören:** Die eingehenden WebRTC-Spuren der Besprechung (`RTCPeerConnection.ontrack`) gehen
  in einen AudioWorklet. Daraus werden 16 kHz PCM.
- **Sprechen:** `getUserMedia` wird durch einen MediaStream ersetzt, den die Antwort-PCM
  (24 kHz) speist.
- **Brücke:** Der Behälter verbindet sich mit **`/agents/{id}/voice`**, also genau der
  Schnittstelle, die heute der Browser bzw. die App für die Sprachfront nutzt (Nova Sonic,
  `RealtimeVoiceSession`). Damit hat der Agent dieselben Werkzeuge wie überall (MCP,
  Werkzeug-Parität, `ask_agent`), dieselben Regeln und dieselben Kosten. Es gibt keine
  zweite Sprachlogik.

**Verzahnung mit Stufe 1:**
- `services/teams_meetings.py` startet den Teilnehmer zum Termin (Rolle „Teilnehmer“ neben
  Mitschreiber und Beisitzer) und beendet ihn nach dem Termin.
- Die **Gäste-Regel** gilt unverändert: Bei Gästen von außen tritt er nicht bei, außer es ist
  erlaubt.
- Der **Hinweis** kommt im Chat und zusätzlich gesprochen beim Beitritt („Ich höre mit und
  schreibe mit“).
- Was er hört, wird zum Transkript auf der **Besprechungsseite**. Das Protokoll entsteht wie in
  Stufe 1 ohne Werkzeuge.
- Er spricht nur, wenn er beim **Namen** genannt wird (Vorgabe). Sonst hört er nur zu.

**Sicherheit:**
- Der Behälter bekommt nur den Beitrittslink und ein kurzlebiges Ticket für
  `/agents/{id}/voice`, keine Zugangsdaten.
- Netzwächter und Egress-Proxy wie bei Apps. Ziele nur Teams bzw. Microsoft, Zielliste
  möglich.
- Werkzeuge mit Außenwirkung laufen weiter über Sendemodus und Freigaben. Was in der
  Besprechung gesagt wird, ist fremder Text (wie das Transkript).
- Mitschnitt nur, wenn der Hinweis gegeben wurde. Abbruch, sobald jemand den Agenten entfernt.

## Risiken, die zuerst geprüft werden (Spike)

1. Lässt Teams im Browser (headless Chromium, ARM64) einen Gast über den Link beitreten, und
   lassen sich Mikrofon und Kamera unterdrücken bzw. ersetzen?
2. Kommen die Audiospuren im Seitenskript an (`ontrack`), auch bei mehreren Sprechern? *(Kern im Spike 1 belegt, mehrere Sprecher in Teams offen)*
3. Latenz Teams → Nova Sonic → Teams unter 2 s?
4. Erlaubt der Mandant anonymen Beitritt? Sonst mit einem eigenen Microsoft-Konto des Agenten
   anmelden (Kap. 37.16).
5. Nutzungsbedingungen von Microsoft für automatisierte Teilnehmer: vor dem Einsatz beim
   Kunden prüfen.

## Spike 1: Audio-Brücke im Browser (13.11.2026, ohne Teams)

Dateien in `docs/plaene/teams-teilnehmer-spike/`:
- `bruecke.js`: das Seitenskript
- `seite.html`: eine WebRTC-Schleife als Ersatz für die Besprechung
- `spike.mjs`: Playwright-Lauf mit Auswertung

Ergebnis (Chromium 1243, headless):
- **Hören:** Eingehende WebRTC-Spuren landen als 16 kHz PCM16 in 200-ms-Häppchen beim
  Steuerprozess: 3,2 s, RMS 21.356. Die gemessene Frequenz von 384 Hz ist ein Mischwert, weil in
  der Schleife beide Richtungen in einer Seite liegen (440 Hz Testton und 300 Hz Rückweg).
- **Sprechen:** 24 kHz PCM16 über die ersetzte `getUserMedia` kommt bei der Gegenseite an
  (RMS 0,26).
- **Falle:** Ein AudioWorklet aus einer `blob:`-Adresse lädt nicht. Die Inhaltsrichtlinie einer
  Seite (in Teams wahrscheinlich ebenso) kann nachgeladene Module sperren. Lösung im Spike:
  `ScriptProcessorNode`, braucht kein Modul. Für später zu prüfen: `MediaStreamTrackProcessor`
  (Insertable Streams), ebenfalls ohne Modul und nicht als veraltet markiert.
- Chrome gibt entfernte Spuren erst an WebAudio, wenn ein (stummes) Media-Element sie spielt.
  Ist im Skript berücksichtigt.

Damit ist Risiko 2 im Kern geklärt. Offen bleiben die Teile, die einen echten Teams-Termin
brauchen: Beitritt, Wartebereich, Spuren bei mehreren Sprechern, Latenz.

## Umsetzung, erste Fassung (Branch `feat/teams-teilnehmer`)

- **Rolle „teilnehmer“** (`core/kanaele`, `services/teams_meetings`): Zum Termin geht genau eine
  Nachricht `type=teilnehmen` an den Agenten. Sie enthält den Beitrittslink aus der Einladung
  (nur `https://teams.microsoft.com/…`) und den Namen „<Agent> (KI)“. Gäste-Regel und Hinweis
  wie bei Stufe 1. Nach dem Termin folgt das Protokoll wie beim Mitschreiber.
- **Agent** (`app/teams_teilnehmer.py`, `app/teams_bruecke.js`):
  - Läuft als Aufgabe im Serverprozess, das Token bleibt dort.
  - Startet den vorhandenen Chromium des Abbilds.
  - Tritt bei: „Im Browser fortfahren“, Name, „Jetzt teilnehmen“, in Deutsch und Englisch.
  - Greift den Ton ab und schickt alle 30 s ein WAV-Stück (Stille wird nicht geschickt).
  - Geht 15 min nach dem Termin bzw. nach 10 min ohne Ton (nicht hereingelassen).
- **Orchestrator** (`POST /meetings/{id}/teilnehmer-ton`):
  - Agenten-Token wird geprüft (`agent_zugang_pruefen`).
  - Nur der eigene Agent, nur Teams-Termine, nur im Zeitfenster, höchstens 4 MB.
  - Die Transkription läuft über denselben Weg wie der Browser-Mitschnitt (`transkribieren`).
  - Der Text wird an das Transkript der Besprechungsseite gehängt.

**Ende-zu-Ende im Agenten-Abbild 1.452.0 auf dem Pi (ohne Teams):**
- Aufbau: WebRTC-Schleife als Besprechung, ein nachgestellter Orchestrator nimmt den Ton an.
- Ergebnis: Ton kommt an, wird gestückelt und mit Token gesendet (3 Stücke, HTTP 200).

Gefunden und behoben:
- Python-Playwright erwartet einen anderen Browser-Build als das Abbild. Der vorhandene
  Chromium wird genommen.
- Ohne Soundkarte dekodiert Chrome eingehenden Ton nur, wenn ein (stummes) Element ihn
  abspielt. Ein ALSA-Null-Gerät ist dafür nicht nötig.
- Bevorzugter Abgriff ist `MediaStreamTrackProcessor`. Er braucht kein Ausgabegerät und kein
  nachgeladenes Modul.
- Das Testmikrofon von Chromium liefert im Behälter nur Stille. Tests speisen eine WAV-Datei ein.

**Noch nicht geprüft (braucht einen echten Termin):**
- Beitritt und Wartebereich
- Texte der Knöpfe in der aktuellen Teams-Fassung
- Medienweg durch Egress-Proxy und Netzwächter (WebRTC über TCP/TURN)
- mehrere Sprecher

Sprechen (Sprachfront) folgt nach dem ersten Live-Test.

## Live-Test mit echtem Teams-Termin (13.11.2026, 18:18–18:55)

Daniels Termin in seinem Teams, der Teilnehmer lief im Wegwerf-Behälter aus dem Agenten-Abbild
1.452.0 auf dem Pi, ohne Agent.

**Klappt:**
- Teams im Browser laden, „In diesem Browser fortfahren“, Name eintragen
- Mikrofon und Kamera werden erkannt („Fake Default Audio Input/Output“)
- Ankunft im Wartebereich („Hallo, Marketing - KI. Jemand wird Sie in Kürze einlassen.“)
- Daniel konnte ihn hereinlassen

**Gefunden und behoben:**
1. **Name:** Teams erlaubt nur Buchstaben, Zahlen, Leerzeichen und `- ' . _ @`. Mit
   „Marketing (KI)“ blieb „Jetzt teilnehmen“ gesperrt. Jetzt heißt er „<Agent> - KI“, und
   `teams_name()` bereinigt den Namen.
2. **Kamera:** Teams fragt Mikrofon und Kamera zusammen ab. Eine Ablehnung der Kamera hielt
   Teams für „kein Audio“. Jetzt gibt es eine schwarze Ersatzkamera und
   `--use-fake-device-for-media-stream` für die Geräteliste.
3. **Tonspuren:** Teams legt die Empfänger schon im Wartebereich an. Ein `track`-Ereignis kommt
   danach nicht. Messung: 1 Verbindung, 0 Spuren. Jetzt fragt die Brücke zusätzlich alle 2 s
   `getReceivers()` ab. Danach: 1 Tonspur, laufend gelesen.

**Noch offen:** Ein Lauf, bei dem der Bot hereingelassen ist **und** jemand spricht. Beim
letzten Versuch war die Besprechung schon leer („… einlassen, wenn die Besprechung beginnt“).

## Zweiter Live-Test: Zuhören belegt (13.11.2026, 20:06)

Mit allen Fixes: Der Bot wurde eingelassen, die Brücke las die Tonspur. Aus Daniels Sprache wurde
über die Transkription der Anlage:

> „Das ist ein wunderschöner Test, oder Maus?“

Fast stille Stücke ließ Whisper englische Sätze erfinden („Bye-bye“, „Thank you for joining
us …“). Behoben:
- Stille wird am mittleren Pegel (RMS) erkannt statt am Spitzenwert.
- Die Transkription bekommt die Sprache `de` vorgegeben.

## Sprechen (Branch `feat/teams-teilnehmer`)

- **Orchestrator:** `ws /agents/{id}/teams-stimme?meeting_id=…`
  - Agenten-Token (`agent_zugang_pruefen`), nur eigener, laufender Teams-Termin.
  - Nur mit Echtzeit-Sprache (Nova Sonic bzw. Azure Realtime), sonst Code 4009.
  - Die Sitzung gehört dem Besitzer des Agenten (Rechte, Freigaben).
  - Dieselbe Schleife wie die App (`_sprachsitzung_betreiben`), kein zweiter Sprachweg.
- **Besprechungsmodus** (`RealtimeVoiceSession.besprechung`):
  - kein Gruß
  - spricht nur, wenn er beim Namen genannt wird
  - höchstens drei Sätze
  - Gesagtes gilt als fremder Text, Aufträge mit Außenwirkung nur auf ausdrückliche Bitte
- **Agent** (`Stimme`): Der Teilnehmer schickt den Ton aus Teams an die Sitzung und spielt die
  Antwort (24 kHz PCM) über das ersetzte Mikrofon in Teams ab. Ohne Echtzeit-Sprache hört er
  weiter nur zu.
- **Offen:** Live-Test des Sprechens (braucht diesen Stand auf der Anlage).

## Recherche 13.11.2026

- **Weg B ist erprobte Praxis:** Selbst gehostete Open-Source-Projekte wie Vexa (Apache 2.0)
  treten Teams-Terminen mit einem gesteuerten Browser bei. Anbieter beschreiben das offen
  (Selenium bzw. Puppeteer mit Chrome), weil es für externe Entwickler kein öffentliches
  Bot-SDK für Teams gibt.
- **Gegenwind von Microsoft:** Teams blockt inzwischen erkannte externe Besprechungs-Bots
  automatisch bzw. verlangt eine Zulassung. Mandanten können anonyme Teilnehmer abschalten.
  Ein **anonymer Gast-Bot ist deshalb nicht verlässlich.**
- **Folge für den Entwurf:** Der Teilnehmer meldet sich mit dem **eigenen Microsoft-365-Konto
  des Agenten** an (Kap. 37.16, seit 1.445.0). Damit ist er ein interner, lizenzierter
  Teilnehmer der eigenen Organisation, kein externer Bot. Der Gastbeitritt bleibt nur Notlösung
  für Termine anderer Organisationen (und wird dort von der Gäste-Regel ohnehin ausgeschlossen).
- **Offen:** Microsofts Nutzungsbedingungen zu automatisierten Teilnehmern. Die Recherche fand
  keine ausdrückliche Regel. Vor dem Einsatz beim Kunden mit dessen IT klären und im Handbuch
  nennen (Einverständnis, Hinweis an alle Teilnehmenden).

## Was dafür gebraucht wird

- Daniels Okay für diesen Weg.
- Ein Test-Termin in Daniels eigener Umgebung (Beitrittslink), kein Kundentermin.
- Für die Anmeldung als interner Teilnehmer: ein lizenzierter Microsoft-365-Testnutzer für den
  Agenten (derselbe, der auch Stufe 1 live prüfen würde).
