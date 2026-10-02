"""Sprechtexte → je Abschnitt eine MP3 (ElevenLabs) und die Längen → dauern.json.

    python3 ton.py <sprechtext.json> <arbeitsordner>

sprechtext.json: {"stimme": "<voice-id>", "modell": "eleven_v4", "texte": {"intro": "…", …, "outro": "…"}}
Schlüssel aus ELEVENLABS_API_KEY oder ~/.elevenlabs.env (Zeile ELEVENLABS_API_KEY=…).
Vorhandene MP3s bleiben stehen — wer einen Text ändert, löscht vorher die passende vo-<name>.mp3.
"""
import json, os, subprocess, sys, urllib.request

quelle, arbeit = sys.argv[1], sys.argv[2]
os.makedirs(arbeit, exist_ok=True)
schluessel = os.environ.get("ELEVENLABS_API_KEY") or open(os.path.expanduser("~/.elevenlabs.env")).read().split("=", 1)[1].strip()
d = json.load(open(quelle, encoding="utf-8"))
dauern = {}
for name, text in d["texte"].items():
    ziel = os.path.join(arbeit, f"vo-{name}.mp3")
    if not os.path.exists(ziel):
        req = urllib.request.Request(
            f"https://api.elevenlabs.io/v1/text-to-speech/{d['stimme']}?output_format=mp3_44100_128",
            data=json.dumps({"text": text, "model_id": d.get("modell", "eleven_v4"),
                             "voice_settings": {"stability": 0.55, "similarity_boost": 0.75, "style": 0.1}}).encode(),
            headers={"xi-api-key": schluessel, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r, open(ziel, "wb") as f:
            f.write(r.read())
    dauern[name] = float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", ziel]).decode())
json.dump(dauern, open(os.path.join(arbeit, "dauern.json"), "w"), indent=1)
print({k: round(v, 1) for k, v in dauern.items()}, "gesamt", round(sum(dauern.values()), 1), "s")
