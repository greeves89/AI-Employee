/**
 * /goal: die Schlusszeile „ZIEL ERREICHT“ / „ZIEL PAUSIERT: …“ aus dem Text nehmen (#906).
 *
 * Die Zeile ist ein Signal an den Server, kein Satz für den Menschen. Gespeichert
 * wird ohne sie (`meta.ziel`, siehe orchestrator `app/core/ziel.py::ohne_marke`);
 * live kommt sie aber mit dem Strom an und muss hier genauso verschwinden.
 *
 * Dieselbe Erkennung wie auf dem Server: nur eine der letzten drei belegten
 * Zeilen zählt, und sie muss mit der Marke BEGINNEN. Erwähnt der Agent die Marke
 * mitten im Text, bleibt sie stehen.
 */

const ERREICHT = "ZIEL ERREICHT";
const PAUSIERT = "ZIEL PAUSIERT";

export type ZielStand = "erreicht" | "pausiert";

function bereinigt(zeile: string): string {
  return zeile.trim().replace(/^[*_`> ]+|[*_`> ]+$/g, "").trim();
}

export function ohneZielMarke(
  text: string,
  laufend = false,
): { text: string; ziel?: ZielStand } {
  if (!text) return { text: "" };
  const zeilen = text.split("\n");
  const belegt = zeilen.map((z, i) => (z.trim() ? i : -1)).filter((i) => i >= 0);
  for (const i of belegt.slice(-3)) {
    const oben = bereinigt(zeilen[i]).toUpperCase();
    const zustand: ZielStand | undefined = oben.startsWith(ERREICHT)
      ? "erreicht"
      : oben.startsWith(PAUSIERT)
        ? "pausiert"
        : undefined;
    if (!zustand) continue;
    const rest =
      zustand === "pausiert"
        ? bereinigt(zeilen[i]).slice(PAUSIERT.length).replace(/^[ :\-–—]+/, "").replace(/[*_`]+$/, "").trim()
        : "";
    const neu = [...zeilen];
    if (rest) neu[i] = rest;
    else neu.splice(i, 1);
    return { text: neu.join("\n").trimEnd(), ziel: zustand };
  }
  // Während des Stroms kommt die Marke stückweise an („ZIEL ERR“). Ein solcher
  // Anfang in der letzten Zeile wird bis zur Entscheidung nicht gezeigt.
  if (laufend && belegt.length > 0) {
    const letzte = belegt[belegt.length - 1];
    const oben = bereinigt(zeilen[letzte]).toUpperCase();
    if (oben.length >= 4 && (ERREICHT.startsWith(oben) || PAUSIERT.startsWith(oben))) {
      return { text: zeilen.slice(0, letzte).join("\n").trimEnd() };
    }
  }
  return { text };
}
