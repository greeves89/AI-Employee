"""Fertigen Film bauen: Logo-Intro + Aufnahme der Oberfläche + animierter Logo-Abschluss,
dazu Stimme, leise Musik und Untertitel.

    python3 film.py <video.json> <arbeitsordner> [--korrektur]

<video.json> (liegt neben dem Drehbuch):
  {"stimme": "…", "modell": "eleven_v4", "texte": {"intro": "…", "<abschnitt>": "…", …, "outro": "…"},
   "film": {"ziel": "../../frontend/public/hilfe/chatten",     # relativ zu video.json, ohne Endung
            "intro": {"titel": "…", "untertitel": "…"},
            "ende": {"zeile": "…", "klein": "…"},
            "schnitt": {"von": 0, "bis": null},                 # Schritt-Nummern aus zeitplan.json (optional)
            "rand_rechts": 6, "crf": 26, "audio_kbps": 96}}
<arbeitsordner>: klickpfad.mp4 + zeitplan.json (Aufnahme) und vo-*.mp3 + dauern.json (ton.py).
Schreibt <ziel>.mp4, <ziel>.jpg (Vorschaubild) und <ziel>.vtt (Untertitel).
--korrektur: misst die Pausen zwischen zwei Sätzen und schreibt den Überhang je Abschnitt nach
<arbeitsordner>/korrektur.json — das Drehbuch zieht ihn bei der nächsten Aufnahme von den Haltezeiten ab.

Musik: music.py aus dem Skill „erklaervideo“ (Ordner in ERKLAERVIDEO_SKRIPTE); ohne ihn kein Musikbett.
"""
import json, os, re, subprocess, sys, tempfile

hier = os.path.dirname(os.path.abspath(__file__))
quelle, arbeit = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
mit_korrektur = "--korrektur" in sys.argv
cfg = json.load(open(quelle, encoding="utf-8")); f = cfg["film"]; texte = cfg["texte"]
laengen = json.load(open(os.path.join(arbeit, "dauern.json")))
ziel = os.path.normpath(os.path.join(os.path.dirname(quelle), f["ziel"]))
X = 0.5            # Überblendung Intro → Aufnahme → Abschluss
RAFFEN_AUF = 2.5   # Wartezeiten (Schritt mit raffen: true) laufen so lange, egal wie lange sie dauerten; raffen: 5 = 5 s
LUFT = 0.8         # gewünschte Pause zwischen zwei Sätzen

def sh(*a):
    subprocess.run(a, check=True)

def dauer(p):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p]).decode())

def groesse(p):
    w, h = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=width,height", "-of", "csv=p=0", p]).decode().strip().split(",")
    return int(w), int(h)

roh = os.path.join(arbeit, "klickpfad.mp4"); W, H = groesse(roh); ROH = dauer(roh)
z = json.load(open(os.path.join(arbeit, "zeitplan.json"))); t0 = z["t0"]; schritte = z["schritte"]
schnitt = f.get("schnitt", {})
nach_i = {s["i"]: s for s in schritte}
von = (nach_i[schnitt["von"]]["t"] - t0 - 0.1) if schnitt.get("von") else 0.0
bis = (nach_i[schnitt["bis"]]["t"] - t0) if schnitt.get("bis") is not None else ROH
von = max(0.0, von)
# Ohne festes Ende: kurz nach dem letzten Satz schneiden (Nachlauf für die letzten Klicks), statt die
# Aufnahme bis zum letzten Bild laufen zu lassen.
if schnitt.get("bis") is None:
    sprechend = [s for s in schritte if s.get("ton") and s["ton"] not in ("intro", "outro")]
    if sprechend:
        letzte = sprechend[-1]
        rest = [s for s in schritte if s["t"] > letzte["t"]]
        ende_ton = letzte["t"] - t0 + laengen[letzte["ton"]] + 0.25
        ende_klick = (rest[-1]["t"] - t0 + 1.6) if rest else 0  # Schritte nach dem letzten Satz (z. B. „Schließen“)
        bis = min(ROH, max(ende_ton + float(f.get("nachlauf", 1.2)), ende_klick))

# Zeitraffer-Strecken (relativ zum Schnittbeginn) und die Abbildung Aufnahmezeit → Filmzeit.
strecken = []
for a, b in zip(schritte, schritte[1:] + [None]):
    if a.get("raffen"):
        s, e = a["t"] - t0 - von, (b["t"] - t0 - von) if b else bis - von
        auf = a["raffen"] if not isinstance(a["raffen"], bool) else RAFFEN_AUF  # raffen: true oder Zieldauer in s
        if e - s > auf:
            strecken.append((s, e, (e - s) / auf))
teile, pos = [], 0.0
for s, e, fak in strecken:
    if s > pos: teile.append((pos, s, 1.0))
    teile.append((s, e, fak)); pos = e
if pos < bis - von: teile.append((pos, bis - von, 1.0))
U = sum((e - s) / fak for s, e, fak in teile)

def abbilden(x):
    y = 0.0
    for s, e, fak in teile:
        if x >= e: y += (e - s) / fak
        elif x > s: return y + (x - s) / fak
        else: return y
    return y

I = max(3.0, 0.4 + laengen.get("intro", 0) + 0.8)
E = max(3.2, 0.6 + laengen.get("outro", 0) + 1.2)
A = I - X                 # Beginn der Aufnahme im Film
B = A + U - X             # Beginn des Abschlusses
T = B + E

# Stimme: Intro, je Abschnitt ab Schrittbeginn (+0,25 s), Abschluss.
stimmen = [("intro", 0.4)] if "intro" in texte else []
for s in schritte:
    if s.get("ton") and s["ton"] not in ("intro", "outro"):
        x = max(0.0, s["t"] - t0 - von) if s["t"] - t0 - von > -1 else -1  # erster Schritt beginnt oft knapp vor dem ersten Bild
        if 0 <= x <= bis - von:
            stimmen.append((s["ton"], A + abbilden(x) + 0.25))
if "outro" in texte:
    stimmen.append(("outro", B + 0.6))
stimmen.sort(key=lambda v: v[1])
# Nie zwei Sätze übereinander: ist ein Abschnitt zu kurz geraten, rückt der nächste Satz nach.
for i in range(1, len(stimmen)):
    frei = stimmen[i - 1][1] + laengen[stimmen[i - 1][0]] + 0.15
    if stimmen[i][1] < frei:
        print(f"  Hinweis: „{stimmen[i][0]}“ rückt {frei - stimmen[i][1]:.1f} s nach (Abschnitt davor zu kurz)")
        stimmen[i] = (stimmen[i][0], frei)

with tempfile.TemporaryDirectory() as tmp:
    # 1. Rahmen
    intro, ende = os.path.join(tmp, "intro.mp4"), os.path.join(tmp, "ende.mp4")
    rahmen = ["node", os.path.join(hier, "rahmen.mjs")]
    sh(*rahmen, "intro", intro, "--breite", str(W), "--hoehe", str(H), "--dauer", f"{I:.3f}",
       "--titel", f["intro"]["titel"], *(["--untertitel", f["intro"]["untertitel"]] if f["intro"].get("untertitel") else []))
    sh(*rahmen, "ende", ende, "--breite", str(W), "--hoehe", str(H), "--dauer", f"{E:.3f}",
       *(["--zeile", f["ende"]["zeile"]] if f.get("ende", {}).get("zeile") else []),
       *(["--klein", f["ende"]["klein"]] if f.get("ende", {}).get("klein") else []))

    # 2. Bild: Aufnahme beschneiden (weißer Rand rechts im Screencast), raffen, mit Rahmen überblenden.
    rand = int(f.get("rand_rechts", 0)); cw = W - rand; ch = round(cw * H / W / 2) * 2
    gemein = f"fps=30,format=yuv420p,setsar=1,settb=AVTB"
    fl = [f"[1:v]trim=start={von:.3f}:end={bis:.3f},setpts=PTS-STARTPTS,crop={cw}:{ch}:0:0,scale={W}:{H}:flags=lanczos,split={len(teile)}" + "".join(f"[r{i}]" for i in range(len(teile)))]
    for i, (s, e, fak) in enumerate(teile):
        fl.append(f"[r{i}]trim=start={s:.3f}:end={e:.3f},setpts=(PTS-STARTPTS)/{fak:.4f}[c{i}]")
    fl.append("".join(f"[c{i}]" for i in range(len(teile))) + f"concat=n={len(teile)}:v=1:a=0,{gemein}[ui]")
    fl.append(f"[0:v]{gemein}[in]"); fl.append(f"[2:v]{gemein}[en]")
    fl.append(f"[in][ui]xfade=transition=fade:duration={X}:offset={A:.3f}[iu]")
    fl.append(f"[iu][en]xfade=transition=fade:duration={X}:offset={B:.3f}[v]")
    bild = os.path.join(tmp, "bild.mp4")
    sh("ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", intro, "-i", roh, "-i", ende,
       "-filter_complex", ";".join(fl), "-map", "[v]", "-c:v", "libx264", "-preset", "slow",
       "-crf", str(f.get("crf", 24)), "-t", f"{T:.3f}", bild)

    # 3. Musikbett: ruhig, ohne Effekte, Glocken zum Schluss.
    musik = None
    skripte = os.environ.get("ERKLAERVIDEO_SKRIPTE")
    if skripte and os.path.exists(os.path.join(skripte, "music.py")):
        s = open(os.path.join(skripte, "music.py"), encoding="utf-8").read()
        a = s.index("# ================= KONFIGURATION ================="); b = s.index("# =================================================", a + 10)
        cfg_musik = f"""# ================= KONFIGURATION =================
DUR = {T:.2f}
BPM = 96; T0 = 0.6
END = {T - 4.5:.2f}
CH = [[65, 69, 72], [64, 69, 72], [62, 65, 69], [62, 65, 70]]
ROOT = [41, 45, 38, 46]
OUTRO = [41, 48, 57, 60, 65]
KICK = (.14, .06)
CLAP = 0.0
TIMBRE = (1, .05, .25, .02)
HOOK_NOTES = []
TAPS = []
BLIPS = []
CHIMES = []
WHOOSH = []
SCRIBBLE = []
OUTRO_BELLS = [({B + .2:.2f}, 77), ({B + .7:.2f}, 81), ({B + 1.4:.2f}, 84)]
BREAKS = []
MUSIC_GAIN = 1.0
"""
        open(os.path.join(tmp, "musik.py"), "w", encoding="utf-8").write(s[:a] + cfg_musik + s[b:])
        subprocess.run(["python3", "musik.py"], cwd=tmp, check=True, stdout=subprocess.DEVNULL)
        musik = os.path.join(tmp, "music.wav")
    else:
        print("Hinweis: ERKLAERVIDEO_SKRIPTE nicht gesetzt — Film ohne Musik.")

    # 4. Ton: Stimmen an ihre Zeiten, Musik leise und unter der Stimme abgesenkt, -16 LUFS.
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for n, _ in stimmen:
        cmd += ["-i", os.path.join(arbeit, f"vo-{n}.mp3")]
    m = len(stimmen); fa = []
    for i, (n, st) in enumerate(stimmen):
        ms = int(st * 1000); fa.append(f"[{i}:a]aresample=48000,adelay={ms}|{ms}[a{i}]")
    fa.append("".join(f"[a{i}]" for i in range(m)) + f"amix=inputs={m}:normalize=0" + (",asplit=2[stimme][schluessel]" if musik else "[stimme]"))
    if musik:
        cmd += ["-i", musik]
        fa.append(f"[{m}:a]aresample=48000,volume=0.22[musik]")
        fa.append("[musik][schluessel]sidechaincompress=threshold=0.03:ratio=6:attack=40:release=500[geduckt]")
        fa.append("[stimme][geduckt]amix=inputs=2:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[ton]")
    else:
        fa.append("[stimme]loudnorm=I=-16:TP=-1.5:LRA=11[ton]")
    ton = os.path.join(tmp, "ton.wav")
    sh(*cmd, "-filter_complex", ";".join(fa), "-map", "[ton]", "-c:a", "pcm_s16le", ton)

    # 5. Zusammenführen; apad + -t statt -shortest (sonst fehlt das Ende).
    os.makedirs(os.path.dirname(ziel), exist_ok=True)
    sh("ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", bild, "-i", ton, "-map", "0:v", "-map", "1:a",
       "-c:v", "copy", "-af", "apad", "-c:a", "aac", "-b:a", f"{f.get('audio_kbps', 128)}k", "-t", f"{T:.3f}",
       "-movflags", "+faststart", ziel + ".mp4")
    # Vorschaubild: die Titelkarte mit Logo.
    sh("ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{min(1.8, I - .6):.2f}", "-i", ziel + ".mp4",
       "-frames:v", "1", "-vf", f"scale={min(W, 1280)}:-2", "-q:v", "3", ziel + ".jpg")

# 6. Untertitel: je Satz eine Einblendung, kurze Sätze zusammen; Zeit nach Zeichenanteil.
def zeit(t):
    h, r = divmod(t, 3600); mi, se = divmod(r, 60)
    return f"{int(h):02d}:{int(mi):02d}:{se:06.3f}"
cues = []
for n, st in stimmen:
    ende_s = st + laengen[n]
    zus = []
    for t in [t.strip() for t in re.split(r"(?<=[.?!:])\s+", texte[n]) if t.strip()]:
        if zus and len(zus[-1]) + len(t) < 90: zus[-1] += " " + t
        else: zus.append(t)
    gesamt = sum(len(t) for t in zus); a = st
    for t in zus:
        b = a + (ende_s - st) * len(t) / gesamt; cues.append((a, b, t)); a = b
open(ziel + ".vtt", "w", encoding="utf-8").write(
    "WEBVTT\n\n" + "\n\n".join(f"{zeit(a)} --> {zeit(b)}\n{t}" for a, b, t in cues) + "\n")

# 7. Pausen zwischen den Sätzen (und auf Wunsch Korrektur für die nächste Aufnahme).
print(f"{os.path.basename(ziel)}: {T:.1f} s, {os.path.getsize(ziel + '.mp4') / 1e6:.1f} MB"
      + (f", {len(strecken)} Zeitraffer" if strecken else ""))
k_pfad = os.path.join(arbeit, "korrektur.json")
k = json.load(open(k_pfad)) if os.path.exists(k_pfad) else {}
for (n, st), naechste in zip(stimmen, stimmen[1:] + [None]):
    if not naechste:
        print(f"  {n:12s} {st:5.1f}–{st + laengen[n]:5.1f}"); continue
    luecke = naechste[1] - (st + laengen[n])
    print(f"  {n:12s} {st:5.1f}–{st + laengen[n]:5.1f}  Pause danach {luecke:4.1f} s")
    if mit_korrektur and n != "intro" and naechste[0] != "outro":
        k[n] = max(0, int(k.get(n, 0) + (luecke - LUFT) * 1000))
if mit_korrektur:
    json.dump({n: v for n, v in k.items() if v}, open(k_pfad, "w"), indent=1)
    print("  korrektur.json:", {n: v for n, v in k.items() if v})
