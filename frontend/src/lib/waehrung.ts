/**
 * Umrechnung zwischen gespeichertem USD und Anzeigewährung — für EINGABEN (#896).
 *
 * Gespeichert wird in USD (so rechnen die Anbieter ab), angezeigt in der
 * Anzeigewährung der Plattform. Bis v1.362 stand im Budgetfeld ein „$" und der
 * eingegebene Wert ging unverändert als USD hinaus, während daneben alles in €
 * stand: Wer „10" für 10 € meinte, bekam 10 $ = 9,20 €.
 *
 * Jetzt: Eingabe in der Anzeigewährung, ``zuUsd`` beim Speichern, ``alsEingabe``
 * beim Vorbelegen. USD wird beim Speichern NICHT gerundet — sonst käme aus
 * „10,00 €" nach dem Neuladen „9,99 €" zurück.
 *
 * Bewusst ohne Importe: reine Rechnung, auch außerhalb des Browsers prüfbar.
 */

export interface WaehrungsKonfig {
  /** "EUR" oder "USD" */
  currency: string;
  /** USD → EUR. Bei currency === "USD" ohne Belang. */
  rate: number;
}

function euro(cfg: WaehrungsKonfig): boolean {
  return cfg.currency === "EUR" && Number(cfg.rate) > 0;
}

/** Gespeicherter USD-Betrag → Betrag in der Anzeigewährung (Zahl, ungerundet). */
export function vonUsd(usd: number, cfg: WaehrungsKonfig): number {
  return euro(cfg) ? usd * Number(cfg.rate) : usd;
}

/** Betrag in der Anzeigewährung → USD zum Speichern. */
export function zuUsd(betrag: number, cfg: WaehrungsKonfig): number {
  return euro(cfg) ? betrag / Number(cfg.rate) : betrag;
}

/**
 * Summe einer Liste so, wie sie dasteht (#896): jede Zeile wird cent-genau in
 * der Anzeigewährung gerundet und erst dann addiert. Ergebnis in USD — passend
 * für ``formatMoney``.
 *
 * Die exakte Summe stand vorher neben gerundeten Zeilen und wich um einen Cent
 * ab (8,70 € über einer Liste, die 8,69 € ergab). Wer nachrechnet, rechnet mit
 * dem, was er sieht; der exakte Betrag gehört in ``moneyTitle``.
 */
export function summeDerZeilen(betraegeUsd: number[], cfg: WaehrungsKonfig): number {
  const cent = betraegeUsd.reduce((summe, usd) => {
    const n = Number(usd);
    return summe + (Number.isFinite(n) ? Math.round(vonUsd(n, cfg) * 100) : 0);
  }, 0);
  return zuUsd(cent / 100, cfg);
}

export function waehrungsCode(cfg: WaehrungsKonfig): "EUR" | "USD" {
  return euro(cfg) ? "EUR" : "USD";
}

export function waehrungsZeichen(cfg: WaehrungsKonfig): "€" | "$" {
  return euro(cfg) ? "€" : "$";
}

/** Vorbelegung eines Eingabefelds: leer = kein Limit, sonst zwei Nachkommastellen. */
export function alsEingabe(usd: number | null | undefined, cfg: WaehrungsKonfig): string {
  if (usd == null || !Number.isFinite(Number(usd))) return "";
  return (Math.round(vonUsd(Number(usd), cfg) * 100) / 100).toFixed(2);
}

/**
 * Eingabe lesen. Leer → ``null`` (kein Limit), ungültig/negativ → ``undefined``,
 * sonst der USD-Betrag. Komma und Punkt sind beide als Dezimaltrenner erlaubt.
 */
export function ausEingabe(text: string, cfg: WaehrungsKonfig): number | null | undefined {
  const roh = (text ?? "").trim().replace(/\s/g, "");
  if (roh === "") return null;
  const normal = roh.includes(",") ? roh.replace(/\./g, "").replace(",", ".") : roh;
  const wert = Number(normal);
  if (!Number.isFinite(wert) || wert < 0) return undefined;
  return zuUsd(wert, cfg);
}
