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
2. Kommen die Audiospuren im Seitenskript an (`ontrack`), auch bei mehreren Sprechern?
3. Latenz Teams → Nova Sonic → Teams unter 2 s?
4. Erlaubt der Mandant anonymen Beitritt? Sonst mit einem eigenen Microsoft-Konto des Agenten
   anmelden (Kap. 37.16).
5. Nutzungsbedingungen von Microsoft für automatisierte Teilnehmer: vor dem Einsatz beim
   Kunden prüfen.

## Was dafür gebraucht wird

- Daniels Okay für diesen Weg.
- Ein Test-Termin in Daniels eigener Umgebung (Beitrittslink), kein Kundentermin.
