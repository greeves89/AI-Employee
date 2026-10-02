# Videos: Klick-Tutorials und Werbefilm

Alle Videos haben denselben Rahmen: **Logo-Intro** mit Titel, dann die echte Oberfläche, am Ende der
**animierte Logo-Abschluss**. Dazu eine Sprecherstimme, leise Musik, die unter der Stimme abgesenkt wird,
und Untertitel (`.vtt`).

| Video | Drehbuch + Sprechtext | Ergebnis |
|---|---|---|
| Klick-Tutorials (Hilfe & FAQ, Willkommensfenster) | `docs/benutzerhandbuch/tutorials/<id>.mjs` + `<id>.json` | `frontend/public/hilfe/<id>.{mp4,jpg,vtt}` |
| Werbefilm der Landingpage (DE/EN) | `werbefilm/drehbuch.mjs` (`FILM_SPRACHE=de` oder `en`) + `werbefilm/film-{de,en}.json` | `docs/ios-app/landing/ai-employee-film-{de,en}.{mp4,jpg,vtt}` |

## Werkzeuge

- `ton.py <video.json> <arbeitsordner>` — Sprechtexte über ElevenLabs (Modell `eleven_v4`) → `vo-<abschnitt>.mp3` + `dauern.json`.
  Schlüssel aus `ELEVENLABS_API_KEY` oder `~/.elevenlabs.env`. Geänderten Text? Die passende `vo-*.mp3` vorher löschen.
- Aufnahme: `klickpfad.mjs` aus dem Skill **erklaervideo** (Format *Klickpfad*), ergänzt um `klickpfad-zeitplan.patch`
  (`patch klickpfad.mjs klickpfad-zeitplan.patch`). Der Patch schreibt `zeitplan.json` mit dem Beginn jedes Schritts —
  daran hängt die Stimme — und kennt `raffen` (Wartezeit als Zeitraffer) und `marke` (feste Zeile statt „Schritt n von m“).
- `rahmen.mjs intro|ende` — rendert Logo-Intro und Logo-Abschluss Bild für Bild (ruft `film.py` selbst auf).
- `film.py <video.json> <arbeitsordner> [--korrektur]` — baut den fertigen Film: Rahmen, beschnittene Aufnahme
  (weißer Rand rechts im Screencast), Zeitraffer, Stimme, Musik (`music.py` aus dem Skill, Ordner in
  `ERKLAERVIDEO_SKRIPTE`), Untertitel und Vorschaubild. Gibt die Pausen zwischen den Sätzen aus.

## Ablauf

```bash
export TUTORIAL_URL=https://ai.example.com TUTORIAL_ZUGANG=~/.ai-employee-demo.env
export TUTORIAL_AGENT_ID=<id> TUTORIAL_AGENT_NAME=Marketing ERKLAERVIDEO_SKRIPTE=<skill>/scripts
T=docs/benutzerhandbuch/tutorials
mkdir -p arbeit/chatten && cd arbeit/chatten && npm i --no-save playwright-core@1
python3 ../../docs/videos/ton.py ../../$T/chatten.json .            # Stimme + Längen
node <skill>/scripts/klickpfad.mjs ../../$T/chatten.mjs --probe     # Selektoren prüfen
node <skill>/scripts/klickpfad.mjs ../../$T/chatten.mjs             # Aufnahme, Takt = Sprechlänge
python3 ../../docs/videos/film.py ../../$T/chatten.json . --korrektur
```

**Pausen:** Ziel sind höchstens gut eine Sekunde Stille zwischen zwei Sätzen. Die Aktionen dauern oft länger
als geschätzt; `--korrektur` misst den Überhang je Abschnitt und schreibt ihn nach `korrektur.json` im
Arbeitsordner. Die nächste Aufnahme zieht ihn von den Haltezeiten ab — meist reicht ein zweiter Durchgang.

**Konto:** nur ein Demokonto mit Rolle *Mitglied* und erfundenen Inhalten — Admins sehen Daten anderer Nutzer.
