# Klick-Tutorials neu aufnehmen

Die Videos unter `frontend/public/hilfe/` (Hilfe & FAQ, Willkommensfenster beim ersten Start)
entstehen aus diesen Drehbüchern. Welche Tutorials es gibt, steht an EINER Stelle:
`frontend/src/lib/tutorials.ts` — Datei-Name = `id` des Tutorials.

Je Tutorial gibt es zwei Dateien:
- `<id>.mjs` — Drehbuch: die Klicks, in Abschnitte gegliedert. Jeder Abschnitt dauert so lange wie sein
  Sprechtext (`abschnitt()` in `gemeinsam.mjs`); Wartezeiten mit `raffen` erscheinen im Film als Zeitraffer.
- `<id>.json` — Sprechtexte je Abschnitt (`intro` und `outro` laufen über Logo-Intro und -Abschluss) und
  die Angaben für den Film (Titel, Schlusszeile, Ziel).

Aufnehmen, vertonen und bauen: siehe [`docs/videos/README.md`](../../videos/README.md).

**Konto:** nur ein Demokonto mit Rolle *Mitglied* und erfundenen Inhalten. Admins sehen Daten
anderer Nutzer — dafür nie aufnehmen. Betreiberhinweis, Versionsbanner und das Willkommensfenster
blendet `gemeinsam.mjs` aus.

**Nebenwirkungen im Demokonto:** `agent-anlegen` legt den Agenten „Marktbeobachtung“ an (danach
löschen), `aufgabe` legt eine Probelauf-Aufgabe an, `chatten` schreibt eine Nachricht, `rechte`
stellt am Ende wieder L3 ein. Agenten danach stoppen.
