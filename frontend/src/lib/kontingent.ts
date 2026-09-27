/** Erkennt die Meldung eines erschöpften KI-Kontingents und macht sie lesbar.
 *
 * Gleiche Wortlaute wie im Orchestrator (``run_outcome._SIGNATUREN``,
 * „Kontingent erschoepft"). Dort entscheidet die Erkennung, ob ein Lauf als
 * fehlgeschlagen gilt; hier nur, wie er im Chat aussieht — statt zweimal
 * „You've hit your weekly limit · resets Oct 2, 7am (UTC)" ein deutscher Satz.
 */

const MUSTER = /You['’]?ve hit your (\w+ ){0,3}limit|rate.?limit(ed)? exceeded|429 Too Many Requests/i;

const MONATE: Record<string, number> = {
  jan: 0, feb: 1, mar: 2, apr: 3, may: 4, jun: 5, jul: 6, aug: 7, sep: 8, oct: 9, nov: 10, dec: 11,
};

export function istKontingentMeldung(text: string | null | undefined): boolean {
  return !!text && MUSTER.test(text);
}

/** Wann es weitergeht — in Ortszeit, wenn die Meldung UTC nennt, sonst wörtlich. */
export function kontingentWiederAb(text: string, jetzt = new Date()): string | null {
  // „resets Oct 2, 7am (UTC)" / „resets Oct 2, 7:30pm (UTC)"
  const utc = text.match(/resets\s+([A-Za-z]{3})\w*\s+(\d{1,2}),?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*\(UTC\)/i);
  if (utc) {
    const monat = MONATE[utc[1].toLowerCase()];
    if (monat !== undefined) {
      let stunde = Number(utc[3]) % 12;
      if (utc[5].toLowerCase() === "pm") stunde += 12;
      let jahr = jetzt.getUTCFullYear();
      let zeit = Date.UTC(jahr, monat, Number(utc[2]), stunde, Number(utc[4] || 0));
      if (zeit < jetzt.getTime() - 86_400_000) zeit = Date.UTC(++jahr, monat, Number(utc[2]), stunde, Number(utc[4] || 0));
      return new Date(zeit).toLocaleString("de-DE", {
        weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
      }) + " Uhr";
    }
  }
  const roh = text.match(/(?:resets|try again at)\s+([^.·\n)]+\)?)/i);
  return roh ? roh[1].trim() : null;
}
