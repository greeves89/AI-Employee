# Klick-Tutorials neu aufnehmen

Die Videos unter `frontend/public/hilfe/` (Hilfe & FAQ, Willkommensfenster beim ersten Start)
entstehen aus diesen Drehbüchern. Welche Tutorials es gibt, steht an EINER Stelle:
`frontend/src/lib/tutorials.ts` — Datei-Name = `id` des Tutorials.

**Werkzeug:** Skill „erklaervideo“ (Format *Klickpfad*), Skript `klickpfad.mjs`. Es fährt die echte
Oberfläche per Playwright ab und baut mit ffmpeg die MP4.

**Konto:** nur ein Demokonto mit Rolle *Mitglied* und erfundenen Inhalten. Admins sehen Daten
anderer Nutzer — dafür nie aufnehmen. Betreiberhinweis, Versionsbanner und das Willkommensfenster
blendet `gemeinsam.mjs` aus.

```bash
export TUTORIAL_URL=https://ai.example.com          # Anlage mit dem Demokonto
export TUTORIAL_ZUGANG=~/.ai-employee-demo.env      # APP_USER=… / APP_PASS=…
export TUTORIAL_AGENT_ID=<id> TUTORIAL_AGENT_NAME=Marketing   # Demo-Agent für Chat, Rechte, Dateien
mkdir -p aufnahme/chatten && cd aufnahme/chatten && npm i --no-save playwright-core@1
node <skill>/klickpfad.mjs ../../chatten.mjs --probe     # Selektoren prüfen (führt Klicks wirklich aus)
node <skill>/klickpfad.mjs ../../chatten.mjs             # → klickpfad.mp4 + kontakt/
```

Danach verkleinern und ablegen (ohne Ton, 1280 × 720). Der Screencast hat rechts einen gut 6 px
breiten weißen Rand; `crop` schneidet ihn weg, `scale` stellt die Größe wieder her:

```bash
R="crop=1272:716:0:0,scale=1280:720:flags=lanczos"
ffmpeg -i klickpfad.mp4 -vf "$R" -c:v libx264 -preset slow -crf 30 -pix_fmt yuv420p -movflags +faststart -an frontend/public/hilfe/chatten.mp4
ffmpeg -ss 6 -i klickpfad.mp4 -vf "$R" -frames:v 1 -q:v 4 frontend/public/hilfe/chatten.jpg
```

**Nebenwirkungen im Demokonto:** `agent-anlegen` legt den Agenten „Marktbeobachtung“ an (danach
löschen), `aufgabe` legt eine Probelauf-Aufgabe an, `chatten` schreibt eine Nachricht, `rechte`
stellt am Ende wieder L3 ein. Agenten danach stoppen.
