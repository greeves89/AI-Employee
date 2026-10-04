/**
 * Text in die Zwischenablage legen — auch auf Anlagen ohne HTTPS (#900).
 *
 * `navigator.clipboard` gibt es nur in einem sicheren Kontext (HTTPS oder
 * localhost). Viele Anlagen laufen im eigenen Netz über HTTP; dort fehlt die
 * Schnittstelle ganz, und ein Kopieren-Knopf täte nichts. Der Rückfall über ein
 * unsichtbares Textfeld und `execCommand("copy")` funktioniert dort weiterhin.
 *
 * Rückgabe: ob kopiert wurde.
 */
export async function inZwischenablage(text: string): Promise<boolean> {
  try {
    if (typeof window !== "undefined" && window.isSecureContext && navigator.clipboard) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* Rückfall unten — z. B. verweigerte Berechtigung */
  }
  if (typeof document === "undefined") return false;
  const feld = document.createElement("textarea");
  feld.value = text;
  feld.setAttribute("readonly", "");
  feld.style.position = "fixed";
  feld.style.top = "0";
  feld.style.left = "0";
  feld.style.opacity = "0";
  document.body.appendChild(feld);
  try {
    feld.select();
    feld.setSelectionRange(0, text.length);
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    document.body.removeChild(feld);
  }
}
