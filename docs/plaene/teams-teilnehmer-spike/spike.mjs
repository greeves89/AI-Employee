import { chromium } from "playwright";
import { readFileSync } from "node:fs";
const browser = await chromium.launch({ 
  args: ["--autoplay-policy=no-user-gesture-required", "--use-fake-ui-for-media-stream"] });
const page = await browser.newPage();
const rahmen = [];
await page.exposeFunction("__aieHoeren", (b64) => rahmen.push(Buffer.from(b64, "base64")));
await page.addInitScript({ content: readFileSync("bruecke.js", "utf8") });
await page.goto("file://" + process.cwd() + "/seite.html");
await page.evaluate(() => window.start());
// Sprechen: 1,5 s 300-Hz-Ton als 24-kHz-PCM16 in Häppchen einspeisen
const n = 24000 * 1.5, pcm = Buffer.alloc(n * 2);
for (let i = 0; i < n; i++) pcm.writeInt16LE(Math.round(Math.sin(2 * Math.PI * 300 * i / 24000) * 12000), 2 * i);
for (let o = 0; o < pcm.length; o += 9600) await page.evaluate((b) => window.__aieSprechen(b), pcm.subarray(o, o + 9600).toString("base64"));
await page.waitForTimeout(3000);
// Auswertung Hören: Pegel und Grundfrequenz (Nulldurchgänge) der abgegriffenen 16-kHz-PCM
const alle = Buffer.concat(rahmen); const s = alle.length / 2; let summe = 0, null_ = 0, vor = 0;
for (let i = 0; i < s; i++) { const v = alle.readInt16LE(2 * i); summe += v * v; if (i && (vor < 0) !== (v < 0)) null_++; vor = v; }
const rms = Math.sqrt(summe / Math.max(s, 1)), sek = s / 16000, freq = null_ / 2 / Math.max(sek, 1e-9);
console.log(JSON.stringify({ hoeren: { rahmen_200ms: rahmen.length, sekunden: +sek.toFixed(2), rms: Math.round(rms), frequenz_hz: Math.round(freq) },
  sprechen: { rueck_rms: +(await page.evaluate(() => window.__rueck)).toFixed(3) } }));
await browser.close();
