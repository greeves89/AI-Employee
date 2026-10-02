// Bilder der Blogbeiträge aus den Quellen in diesem Ordner erzeugen.
//
//   npm i --no-save playwright      (einmalig, in diesem Ordner)
//   node docs/blog-src/bilder/render.mjs
//
// Ergebnis in bilder/out/ (nicht eingecheckt — die Bilder liegen im Blog):
//   <adresse>.png      Titelbild 1200x630 je Beitrag, aus Titel, Rubrik und Symbol im Kopf der Beitragsdatei
//   grafik-*.png       je eine Grafik aus grafik-*.html
// Die Symbole stammen aus lucide (frontend/node_modules), Schriften aus dem Blog selbst.
import { chromium } from "playwright";
import { fileURLToPath, pathToFileURL } from "node:url";
import fs from "node:fs";
import path from "node:path";

const hier = path.dirname(fileURLToPath(import.meta.url));
const ziel = path.join(hier, "out");
const beitraege = path.join(hier, "..", "beitraege");
const lucide = path.join(hier, "..", "..", "..", "frontend", "node_modules", "lucide-react", "dist", "esm", "icons");
fs.mkdirSync(ziel, { recursive: true });

const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");

async function symbol(name) {
  const { __iconNode } = await import(pathToFileURL(path.join(lucide, `${name}.mjs`)).href);
  const teile = __iconNode.map(([tag, a]) =>
    `<${tag} ${Object.entries(a).filter(([k]) => k !== "key").map(([k, v]) => `${k}="${esc(v)}"`).join(" ")}/>`);
  return `<svg viewBox="0 0 24 24">${teile.join("")}</svg>`;
}

function kopf(datei) {
  const roh = fs.readFileSync(datei, "utf8").split("---\n")[1];
  return Object.fromEntries(roh.split("\n").filter((z) => /^[a-z_]+: /.test(z)).map((z) => [z.split(": ")[0], z.slice(z.indexOf(": ") + 2).trim()]));
}

const MARKE = `<svg viewBox="0 0 24 24"><path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2"/><path d="M20 14h2"/><path d="M15 13v2"/><path d="M9 13v2"/></svg>`;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 2 });

for (const name of fs.readdirSync(beitraege).filter((n) => n.endsWith(".md")).sort()) {
  const k = kopf(path.join(beitraege, name));
  if (!k.cover) continue;
  // Der Doppelpunkt trennt im Titel Frage und Zusatz — auf dem Bild steht nur die Frage.
  const zeile = k.bildtitel || k.title.split(/[:?]/)[0] + (k.title.includes("?") ? "?" : "");
  const html = `<!doctype html><html lang="de"><head><meta charset="utf-8"><base href="${pathToFileURL(hier).href}/">
<link rel="stylesheet" href="stil.css"></head><body><div class="titel" style="--acc:var(--${k.farbe || "blue"})">
<div><span class="mono">${esc(k.rubrik || "Blog")}</span><h1>${esc(zeile)}</h1>
<div class="marke"><i>${MARKE}</i>AI Employee</div></div>
<div class="symbol">${await symbol(k.symbol || "bot")}</div></div></body></html>`;
  const tmp = path.join(hier, ".titel.html");
  fs.writeFileSync(tmp, html);
  await page.goto(pathToFileURL(tmp).href);
  await page.evaluate(() => document.fonts.ready);
  await page.locator(".titel").screenshot({ path: path.join(ziel, k.cover) });
  fs.unlinkSync(tmp);
  console.log("Titelbild:", k.cover);
}

for (const name of fs.readdirSync(hier).filter((n) => /^grafik-.*\.html$/.test(n)).sort()) {
  await page.goto(pathToFileURL(path.join(hier, name)).href);
  await page.evaluate(() => document.fonts.ready);
  await page.locator(".grafik").screenshot({ path: path.join(ziel, name.replace(".html", ".png")) });
  console.log("Grafik:", name.replace(".html", ".png"));
}
await browser.close();
