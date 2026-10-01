/** Den lesbaren Text aus einem API-Fehler holen.
 *
 *  `fetchJSON` wirft „API Error 402: {json}". Ohne das stünde im Dialog eine
 *  JSON-Zeile, und der Grund (etwa „Lizenz-Limit erreicht") wäre unlesbar.
 *  FastAPI liefert `detail` als Text oder als Objekt mit `message`. */
export function apiFehlertext(fehler: unknown, ersatz = "Das hat nicht geklappt."): string {
  const roh = fehler instanceof Error ? fehler.message : typeof fehler === "string" ? fehler : "";
  if (!roh) return ersatz;
  const start = roh.indexOf("{");
  if (start < 0) return roh.replace(/^API Error \d+:\s*/, "") || ersatz;
  try {
    const parsed = JSON.parse(roh.slice(start));
    const detail = parsed?.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail.message === "string") return detail.message;
    return roh;
  } catch {
    return roh;
  }
}
