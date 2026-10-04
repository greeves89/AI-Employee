/**
 * Zustandsregeln des Agenten-Chats, als reine Funktionen (Abnahme v1.362.1).
 *
 * Bewusst ohne Importe: Die Datei läuft auch außerhalb von Next.js (der Test
 * `orchestrator/tests/test_chat_zustand.py` führt sie mit Node aus).
 */

// ── Gespräch in der Adresse (#900) ───────────────────────────────────────────

/**
 * Der Suchteil der Agentenseite (ohne „?“) für das offene Gespräch — oder `null`,
 * wenn die Adresse bleibt, wie sie ist.
 *
 * * Ein Gespräch ist offen: `session=<id>`; ein `neu=1` fällt weg.
 * * „Neues Gespräch“ (vorher war eins offen): `session` weg, `neu=1` dazu.
 *   Ohne `neu=1` öffnet die Agentenseite nach F5 das zuletzt aktive Gespräch
 *   („Agent öffnen → letztes Gespräch“) — der Nutzer hatte aber bewusst ein
 *   neues gewählt.
 * * Kein Gespräch und vorher auch keins (Agent ohne Gespräche, F5 im neuen
 *   Gespräch): nichts hinzufügen, ein vorhandenes `neu=1` bleibt.
 */
export function gespraechsSuche(
  suche: string,
  gespraech: string | null,
  vorher: string | null,
): string | null {
  const params = new URLSearchParams(suche);
  const alt = params.toString();
  if (gespraech) {
    params.set("session", gespraech);
    params.delete("neu");
  } else {
    params.delete("session");
    if (vorher) params.set("neu", "1");
  }
  const neu = params.toString();
  return neu === alt ? null : neu;
}

/**
 * Welches Gespräch der Chat nach außen meldet (Adresse der Agentenseite).
 *
 * Nach „Neues Gespräch“ nennt der Server die neue Kennung sofort — ein Gespräch
 * daraus wird aber erst mit der ersten Nachricht (#907). Bis dahin steht sie
 * nicht in der Adresse: Nach F5 gäbe es sie nicht, und der Chat spränge ins
 * vorige Gespräch zurück.
 */
export function gemeldetesGespraech(aktiv: string | null, leerNeu: string | null): string | null {
  return aktiv && aktiv !== leerNeu ? aktiv : null;
}

// ── Rückfrage-Karte ──────────────────────────────────────────────────────────

interface Rueckfrage {
  approval_id: string;
  agent_id: string;
  tool?: string | null;
}

/**
 * Die offene Rückfrage dieses Agenten, die der Chat zeigt.
 *
 * `erledigt` sind die hier schon beantworteten: Eine Abfrage, die vor der
 * Antwort losging, liefert sie noch als offen — sie darf die Karte nicht als
 * bedienbar zurückholen. Vorschläge der Nachtschicht gehören nicht in den Chat.
 */
export function offeneRueckfrage<T extends Rueckfrage>(
  liste: T[],
  agentId: string,
  erledigt: ReadonlySet<string>,
): T | null {
  return liste.find((a) =>
    a.agent_id === agentId && a.tool !== "reflection_change" && !erledigt.has(String(a.approval_id)),
  ) ?? null;
}

/** Die Zeile einer erledigten Karte: „Beantwortet: …“, „Freigegeben“ oder „Abgelehnt“. */
export function rueckfrageErgebnis(ergebnis: "beantwortet" | "abgelehnt", antwort?: string | null): string {
  if (ergebnis === "abgelehnt") return "Abgelehnt";
  const text = (antwort || "").trim();
  return text ? `Beantwortet: ${text}` : "Freigegeben";
}

// ── Ziel-Banner (/goal) ──────────────────────────────────────────────────────

interface Nachricht {
  role: string;
  content?: string | null;
  meta?: { source?: unknown } | null;
}

const ZIEL_BEFEHL = /^\/(?:goal|ziel)\b/i;

function gehoertZumZiel(n: Nachricht): boolean {
  return n.meta?.source === "goal" || ZIEL_BEFEHL.test((n.content || "").trim());
}

/**
 * Steht das Ziel-Banner über dem Eingabefeld?
 *
 * Ein laufendes Ziel (aktiv, pausiert) immer. Ein abgeschlossenes (erreicht,
 * Obergrenze) nur bis zur nächsten eigenen Nachricht — danach geht es um etwas
 * anderes; das erreichte Ziel steht weiter als Chip an der Antwort. Entschieden
 * wird am Verlauf, damit es nach F5 genauso aussieht.
 */
export function zielBannerSichtbar(status: string | null | undefined, nachrichten: Nachricht[]): boolean {
  if (!status || status === "gestoppt") return false;
  if (status === "aktiv" || status === "pausiert") return true;
  for (let i = nachrichten.length - 1; i >= 0; i -= 1) {
    const n = nachrichten[i];
    if (n.role !== "user") continue;
    return gehoertZumZiel(n);
  }
  return true;
}
