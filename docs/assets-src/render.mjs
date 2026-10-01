// README-Grafiken aus den HTML-Quellen in diesem Ordner erzeugen.
//
//   npm i --no-save playwright   (einmalig, in diesem Ordner)
//   node docs/assets-src/render.mjs
//
// Ergebnis: docs/assets/{comparison,architecture,pricing}.png in doppelter
// Aufloesung. Die PNGs sind eingecheckt; dieses Skript gibt es, damit eine
// Grafik nach einer Aenderung an der HTML-Quelle reproduzierbar neu entsteht.
import { chromium } from "playwright";
import { fileURLToPath } from "node:url";
import path from "node:path";

const hier = path.dirname(fileURLToPath(import.meta.url));
const ziel = path.join(hier, "..", "assets");
const browser = await chromium.launch();
const page = await browser.newPage({ deviceScaleFactor: 2 });
for (const name of ["comparison", "architecture", "pricing"]) {
  await page.goto("file://" + path.join(hier, `${name}.html`));
  await page.locator("body").screenshot({ path: path.join(ziel, `${name}.png`) });
  console.log("gerendert:", name);
}
await browser.close();
