// Logo-Intro und animierter Logo-Abschluss für alle Videos (Klick-Tutorials, Werbefilm).
// Bild für Bild gerendert (Playwright), danach mit ffmpeg zu MP4; die ruhige Strecke am Ende wird
// nicht gerendert, sondern als Standbild verlängert.
//
//   node rahmen.mjs intro <ziel.mp4> --breite 1280 --hoehe 720 --dauer 4 --titel "…" [--untertitel "…"]
//   node rahmen.mjs ende  <ziel.mp4> --breite 1280 --hoehe 720 --dauer 5 [--zeile "…"] [--klein "…"]
//
// Braucht playwright-core im aktuellen Ordner (node_modules) und ffmpeg.
import fs from 'node:fs'; import path from 'node:path'; import os from 'node:os';
import { execFileSync } from 'node:child_process'; import { createRequire } from 'node:module';

const { chromium } = createRequire(path.join(process.cwd(), 'x.js'))('playwright-core');
const [art, ziel] = process.argv.slice(2, 4);
const opt = {}; for (let i = 4; i < process.argv.length; i += 2) opt[process.argv[i].replace(/^--/, '')] = process.argv[i + 1];
if (!['intro', 'ende'].includes(art) || !ziel) { console.error('Aufruf: node rahmen.mjs intro|ende <ziel.mp4> --dauer s …'); process.exit(1); }
const W = +(opt.breite || 1280), H = +(opt.hoehe || 720), FPS = 30, DAUER = +(opt.dauer || 4);
const ANIM = art === 'intro' ? 1.3 : 2.3;          // danach steht das Bild
const k = Math.min(W, H) / 720;                   // alle Maße sind für 720p angegeben; im Hochformat zählt die Breite
const esc = (s) => String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;');

// lucide „cpu“ — dasselbe Zeichen wie das Logo in der Seitenleiste der App.
const CPU = '<path d="M12 20v2"/><path d="M12 2v2"/><path d="M17 20v2"/><path d="M17 2v2"/><path d="M2 12h2"/><path d="M2 17h2"/><path d="M2 7h2"/><path d="M20 12h2"/><path d="M20 17h2"/><path d="M20 7h2"/><path d="M7 20v2"/><path d="M7 2v2"/><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="8" y="8" width="8" height="8" rx="1"/>';
const LOGO = (g) => `<div class="logo" style="width:${g}px;height:${g}px;border-radius:${g * .26}px"><svg viewBox="0 0 24 24" style="width:${g * .52}px;height:${g * .52}px">${CPU}</svg></div>`;

const html = `<!doctype html><html lang="de"><head><meta charset="utf-8"><style>
*{box-sizing:border-box;margin:0}
body{width:${W}px;height:${H}px;overflow:hidden;background:#0f172a;color:#fff;font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif;position:relative}
.glanz{position:absolute;inset:0;background:radial-gradient(${560 * k}px ${340 * k}px at 50% 42%,rgba(59,130,246,.22),transparent 70%)}
.logo{display:grid;place-items:center;background:linear-gradient(135deg,#3b82f6,#22d3ee);box-shadow:0 ${14 * k}px ${44 * k}px rgba(59,130,246,.45)}
.logo svg{fill:none;stroke:#fff;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
#intro{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:0 ${110 * k}px}
#intro .marke{display:flex;align-items:center;gap:${16 * k}px;font-size:${22 * k}px;font-weight:700;letter-spacing:-.01em;margin-bottom:${30 * k}px}
#intro h1{font-size:${44 * k}px;font-weight:800;letter-spacing:-.02em;line-height:1.1}
#intro p{font-size:${19 * k}px;opacity:.75;margin-top:${16 * k}px;line-height:1.4}
#ende{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center}
#ende .reihe{display:flex;align-items:center;justify-content:center}
#ende .wort{overflow:hidden;white-space:nowrap;font-size:${46 * k}px;font-weight:800;letter-spacing:-.025em}
#ende .wort span{display:inline-block;padding-left:${22 * k}px}
#ende #l{position:relative}
#ende .ring{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);pointer-events:none;border-radius:50%;border:${2 * k}px solid rgba(34,211,238,.7)}
#ende .zeile{font-size:${21 * k}px;opacity:.85;margin-top:${30 * k}px;font-weight:600}
#ende .klein{font-size:${16 * k}px;opacity:.6;margin-top:${12 * k}px;letter-spacing:.01em}
</style></head><body><div class="glanz"></div>
${art === 'intro' ? `<div id="intro"><div class="marke" id="m">${LOGO(60 * k)}AI Employee</div>
<h1 id="t">${esc(opt.titel)}</h1>${opt.untertitel ? `<p id="u">${esc(opt.untertitel)}</p>` : ''}</div>`
: `<div id="ende"><div class="reihe"><div id="l">${LOGO(96 * k)}<div class="ring" id="r"></div></div><div class="wort" id="w"><span>AI Employee</span></div></div>
${opt.zeile ? `<div class="zeile" id="z">${esc(opt.zeile)}</div>` : ''}${opt.klein ? `<div class="klein" id="kl">${esc(opt.klein)}</div>` : ''}</div>`}
<script>
const cl=(x)=>Math.min(1,Math.max(0,x)), P=(t,a,b)=>cl((t-a)/(b-a));
const eo=(x)=>1-Math.pow(1-x,3);
const zurueck=(x)=>{const c=1.7,d=c+1;return 1+d*Math.pow(x-1,3)+c*Math.pow(x-1,2)};  // leichtes Überschwingen
const $=(id)=>document.getElementById(id);
function auf(el,q,y){if(!el)return;el.style.opacity=q;el.style.transform='translateY('+(1-q)*y+'px)'}
window.render=(t)=>{
  if(${art === 'intro'}){
    const m=$('m'), q=P(t,0,.55); m.style.opacity=eo(q); m.style.transform='scale('+(.7+.3*zurueck(q))+')';
    auf($('t'),eo(P(t,.25,.85)),${22 * k}); auf($('u'),eo(P(t,.45,1.05)),${18 * k});
    return;
  }
  const l=$('l'), w=$('w'), r=$('r'), q=P(t,0,.7);
  l.firstChild.style.opacity=eo(P(t,0,.25)); l.firstChild.style.transform='scale('+(zurueck(q))+') rotate('+((1-eo(q))*-14)+'deg)';
  const rq=P(t,.25,1.35), g=${96 * k}*(1+1.9*eo(rq)); r.style.width=g+'px'; r.style.height=g+'px'; r.style.opacity=(1-rq)*(rq>0?.9:0);
  // Wortmarke klappt rechts neben dem Logo auf; das Logo rückt dabei mit, weil die Reihe zentriert ist.
  const wq=eo(P(t,.75,1.4)); w.style.width=(wq*w.scrollWidth)+'px'; w.style.opacity=wq;
  auf($('z'),eo(P(t,1.3,1.85)),${16 * k}); auf($('kl'),eo(P(t,1.6,2.2)),${12 * k});
};
window.render(0);
</script></body></html>`;

const ordner = fs.mkdtempSync(path.join(os.tmpdir(), 'rahmen-'));
fs.writeFileSync(path.join(ordner, 'seite.html'), html);
const b = await chromium.launch();
const page = await b.newPage({ viewport: { width: W, height: H } });
await page.goto('file://' + path.join(ordner, 'seite.html'));
await page.evaluate(() => document.fonts.ready);
const n = Math.round(Math.min(ANIM, DAUER) * FPS);
for (let f = 0; f <= n; f++) {
  await page.evaluate((t) => window.render(t), f / FPS);
  await page.screenshot({ path: path.join(ordner, `${String(f).padStart(5, '0')}.png`) });
}
await b.close();
// Animation + Standbild bis zur Dauer; der Abschluss blendet in der letzten halben Sekunde ab.
const rest = Math.max(0, DAUER - (n + 1) / FPS);
const vf = [`tpad=stop_mode=clone:stop_duration=${rest.toFixed(3)}`, 'format=yuv420p'];
if (art === 'ende') vf.push(`fade=t=out:st=${(DAUER - .5).toFixed(3)}:d=0.5`);
execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-framerate', String(FPS), '-i', path.join(ordner, '%05d.png'),
  '-vf', vf.join(','), '-r', String(FPS), '-c:v', 'libx264', '-preset', 'slow', '-crf', '20', '-t', DAUER.toFixed(3), ziel]);
fs.rmSync(ordner, { recursive: true, force: true });
console.log(`${art}: ${ziel} (${DAUER.toFixed(1)} s, ${W}×${H})`);
