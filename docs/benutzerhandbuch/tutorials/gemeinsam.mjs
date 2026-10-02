// Gemeinsame Vorbereitung der Klick-Tutorials (Hilfe & FAQ, Willkommensfenster), vertont.
// Aufnahme mit dem Skill „erklaervideo“ (Format Klickpfad) gegen ein Demokonto mit Rolle Mitglied
// und ausschließlich erfundenen Inhalten — nie gegen ein Konto mit echten Daten. Siehe README.md.
import { readFileSync } from 'node:fs'; import os from 'node:os';
export const B = process.env.TUTORIAL_URL || (() => { throw new Error('TUTORIAL_URL setzen, z. B. https://ai.example.com'); })();
const env = Object.fromEntries(readFileSync(process.env.TUTORIAL_ZUGANG || os.homedir() + '/.ai-employee-demo.env', 'utf8').split('\n').filter(z => z.includes('=')).map(z => [z.slice(0, z.indexOf('=')), z.slice(z.indexOf('=') + 1).trim()]));
// Betreiberhinweis (private Adresse), Versionsbanner und das Willkommensfenster selbst gehören nicht ins Video.
const AUSBLENDEN = () => { const weg = () => document.querySelectorAll('div,aside,section').forEach(el => {
  const t = el.innerText || ''; if (el.children.length < 12 && t.length < 200 && (/nicht lizenziert/.test(t) || /Neue Version verfügbar/.test(t))) el.remove(); });
  const an = () => { new MutationObserver(weg).observe(document.documentElement, { childList: true, subtree: true }); weg(); };
  if (document.documentElement) an(); else document.addEventListener('DOMContentLoaded', an, { once: true }); };
// Demo-Agent, mit dem Chat, Rechte und Dateien gezeigt werden (Rolle Mitglied, nur erfundene Inhalte).
export const AGENT_ID = process.env.TUTORIAL_AGENT_ID || '';
export const AGENT_NAME = process.env.TUTORIAL_AGENT_NAME || 'Marketing';
// Vertonung: Jeder Abschnitt dauert so lange wie sein Sprechtext. Die Längen schreibt docs/videos/ton.py nach
// dauern.json, den gemessenen Überhang docs/videos/film.py --korrektur nach korrektur.json — beides im
// Arbeitsordner, in dem aufgenommen wird.
const lies = (datei) => { try { return JSON.parse(readFileSync(datei, 'utf8')); } catch { return {}; } };
const D = lies('dauern.json'), K = lies('korrektur.json');
const Z = { vorAktion: 250, zeigerFahrt: 650, nachKlick: 400, tippenJeZeichen: 38, lesenJeWort: 0, lesenBasis: 0, karte: 3000 };
// Geschätzte Eigenzeit einer Aktion (Zeiger, Klick-Welle, Tippen, Scrollen) in ms.
const eigen = (s) => s.scroll ? 1000 : (s.tippen || s.einfuegen) ? Z.vorAktion + Z.zeigerFahrt + 180 + (s.tippen ? String(s.wert).length * Z.tippenJeZeichen : 0)
  : s.klick ? Z.vorAktion + Z.zeigerFahrt + 180 : s.zeigen ? Z.vorAktion + Z.zeigerFahrt : 0;
// Ein Abschnitt = ein Sprechtext; der erste Schritt trägt Ton und Einblendung, die Sprechdauer verteilt sich auf alle Schritte.
export function abschnitt(ton, text, schritte) {
  if (!(ton in D)) throw new Error(`dauern.json fehlt „${ton}“ — erst docs/videos/ton.py im Arbeitsordner laufen lassen`);
  const rest = Math.max(0, D[ton] * 1000 + 350 - schritte.reduce((a, s) => a + eigen(s), 0) - (K[ton] || 0));
  const je = Math.max(K[ton] ? 150 : Z.nachKlick, Math.round(rest / schritte.length));
  return schritte.map((s, i) => ({ ...s, halten: s.halten ?? je, ...(i === 0 ? { ton, text } : {}) }));
}
export const basis = { baseUrl: B + '/login', size: [1280, 720], zoom: 1, tempo: 1, farbe: '#3b82f6', zeiten: Z };
export function vorbereitung(start, { agent } = {}) {
  return async (page) => {
    await page.context().addInitScript(AUSBLENDEN); await page.evaluate(AUSBLENDEN);
    await page.fill('#email', env.APP_USER); await page.fill('#password', env.APP_PASS); await page.click('button[type=submit]');
    await page.waitForURL(u => !u.pathname.startsWith('/login'), { timeout: 30000 });
    await page.request.post(B + '/api/v1/auth/me/tutorial-seen').catch(() => {});
    if (agent) { await page.request.post(`${B}/api/v1/agents/${agent}/start`).catch(() => {}); await page.waitForTimeout(6000); }
    await page.goto(B + start, { waitUntil: 'networkidle' }); await page.waitForTimeout(1500);
  };
}
