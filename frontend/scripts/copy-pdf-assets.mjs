/**
 * Kopiert die Beiwerk-Dateien von pdf.js in den oeffentlichen Ordner.
 *
 * Der Betrachter holte cMaps und Schriften bisher zur Laufzeit von unpkg.com.
 * Das ist aus zwei Gruenden falsch: die Anlage laeuft selbst gehostet (auch
 * abgeschottet ohne Weg nach draussen), und jedes geoeffnete PDF haette einem
 * Fremdanbieter verraten, dass es geoeffnet wurde. Jetzt liegt alles auf dem
 * eigenen Ursprung — und automatisch in genau der Fassung, die mitgeliefert
 * wird, statt in einer, die auf dem CDN vielleicht fehlt.
 */
import { cp, mkdir, rm } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";

const require = createRequire(import.meta.url);
const pdfjs = dirname(require.resolve("pdfjs-dist/package.json"));
const ziel = join(process.cwd(), "public", "pdfjs");

await rm(ziel, { recursive: true, force: true });
await mkdir(ziel, { recursive: true });
for (const teil of ["cmaps", "standard_fonts"]) {
  await cp(join(pdfjs, teil), join(ziel, teil), { recursive: true });
}
console.log(`pdf.js-Beiwerk kopiert nach ${ziel}`);
