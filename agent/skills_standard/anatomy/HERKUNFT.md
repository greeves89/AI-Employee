# Herkunft: Anatomy

Mitgelieferter Standard-Skill im Agenten-Abbild (`/opt/skills-standard/anatomy`), beim
Start jedes Agenten in seinen Skill-Ordner gelegt (`agent/app/standard_skills.py`).

- Quelle: https://github.com/wheresryan22/anatomy
- Stand: `1482e8469b0a` (18.10.2026), Lizenz MIT (siehe `LICENSE`)
- Geprüft am 06.11.2026: `kit/` nutzt nur eingebaute Node-Module, kein Netz, kein `eval`,
  keine Installation. `scripts/capture.mjs` startet nur einen lokalen Chromium
  (`/usr/bin/chromium`), gesteuert über 127.0.0.1.

Weggelassen:

- Veröffentlichen in die öffentliche Galerie des Projekts (`references/publish.md`,
  `scripts/publish.mjs`, `scripts/session.mjs`) und die beiden Verweise darauf in
  `SKILL.md`: Ein Agent soll Inhalte der Anlage nicht nach GitHub schicken.
- `README.md`, `CHANGELOG.md`, Tests, Evals, Projektdateien (`.github/` u. a.).
- Gebaute Beispielseiten und PDFs (`examples/**/*.html|pdf`, 24 MB): `node build.mjs` im
  Beispielordner erzeugt sie neu.

Aktualisieren: neuen Stand prüfen wie oben, dieselben Dateien weglassen, `STAND` anpassen.
