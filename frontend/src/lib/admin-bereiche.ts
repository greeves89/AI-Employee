/**
 * Die Bereiche des Admin-Bereichs und die Weiterleitungen alter Verweise (#899).
 *
 * Trennung nach Eigentum: Alles, was der ganzen Anlage gehoert, steht in der
 * Admin-Bereich — in EINER Ebene, eine Seitennavigation mit Gruppenueberschriften.
 * `/settings` zeigt nur noch, was dem angemeldeten Nutzer selbst gehoert.
 *
 * Bewusst ohne Importe: Die Datei laeuft auch ausserhalb von Next.js (der Test
 * fuehrt sie mit Node aus), deshalb stehen die Symbole in der Seite selbst.
 */

export type AdminBereich =
  | "suche"
  | "users" | "roles" | "sso-groups"
  | "agents" | "assignments" | "vorlagen"
  | "modelle" | "ai-accounts" | "sprache" | "second-brains" | "web-search"
  | "mcp" | "integrationen-anlage" | "system" | "pages"
  | "master-rules" | "secrets" | "dlp" | "audit" | "gesetze"
  | "health" | "budget" | "feedback";

export interface AdminGruppe {
  id: string;
  titel: string;
  bereiche: AdminBereich[];
}

/** Acht Seiten in der Leiste, jede mit Reitern (Daniel, 14.11.2026: „alles soooo viel
 *  und verschachtelt“). Eine Gruppe ist eine Seite, ihre Bereiche sind die Reiter.
 *  Die Kennungen der Bereiche bleiben — Links, Lesezeichen und Suche führen weiter
 *  direkt auf den richtigen Reiter (``?tab=<bereich>``). */
export const ADMIN_GRUPPEN: AdminGruppe[] = [
  { id: "start", titel: "Start", bereiche: ["suche", "health", "feedback"] },
  { id: "people", titel: "Nutzer", bereiche: ["users", "roles", "sso-groups"] },
  { id: "agents", titel: "Agenten", bereiche: ["agents", "assignments", "vorlagen"] },
  { id: "ki", titel: "KI", bereiche: ["modelle", "ai-accounts", "sprache", "web-search"] },
  { id: "werkzeuge", titel: "Werkzeuge", bereiche: ["mcp", "integrationen-anlage", "second-brains", "pages"] },
  { id: "security", titel: "Sicherheit", bereiche: ["master-rules", "dlp", "gesetze", "secrets", "audit"] },
  { id: "kosten", titel: "Kosten", bereiche: ["budget"] },
  { id: "system", titel: "System", bereiche: ["system"] },
];

/** Die Seite (Gruppe), zu der ein Bereich gehört. */
export function adminSeite(bereich: AdminBereich): AdminGruppe {
  return ADMIN_GRUPPEN.find((g) => g.bereiche.includes(bereich)) ?? ADMIN_GRUPPEN[0];
}

export const ADMIN_BEREICH_NAMEN: Record<AdminBereich, string> = {
  suche: "Übersicht",
  users: "Nutzer",
  roles: "Rollen & Rechte",
  "sso-groups": "SSO-Gruppen",
  agents: "Alle Agenten",
  assignments: "Zuweisungen",
  vorlagen: "Vorlagen",
  modelle: "Modelle & Anbieter",
  "ai-accounts": "KI-Konten",
  sprache: "Sprache",
  "second-brains": "Wissens-Ablagen",
  "web-search": "Websuche",
  mcp: "MCP-Server",
  "integrationen-anlage": "Dienste & Konten",
  system: "System & Lizenz",
  pages: "Eigene Menüpunkte",
  "master-rules": "Master-Regeln",
  secrets: "Schlüssel & Zugangsdaten",
  dlp: "Datenschutz-Filter",
  audit: "Protokoll",
  gesetze: "Gesetze",
  health: "Systemzustand",
  budget: "Kosten & Budget",
  feedback: "Rückmeldungen",
};

export const STANDARD_BEREICH: AdminBereich = "suche";

const ALLE_BEREICHE: readonly AdminBereich[] = ADMIN_GRUPPEN.flatMap((g) => g.bereiche);

/** Fruehere Kennungen, die weiter funktionieren muessen (Lesezeichen, Handbuch). */
const ALIASE: Record<string, { bereich: AdminBereich; anker?: string }> = {
  // Bis #899 ein Sammelreiter mit der kompletten Einstellungsseite darin.
  settings: { bereich: "modelle" },
  lizenz: { bereich: "system", anker: "lizenz" },
  voice: { bereich: "sprache" },
  integrationen: { bereich: "integrationen-anlage" },
  "mcp-server": { bereich: "mcp" },
  werkzeuge: { bereich: "mcp" },
};

function istBereich(tab: string): tab is AdminBereich {
  return (ALLE_BEREICHE as readonly string[]).includes(tab);
}

/** Welcher Bereich zu `?tab=` gehoert. Fehlendes und Unbekanntes oeffnet die Suche. */
export function adminBereichAusTab(tab: string | null | undefined): { bereich: AdminBereich; anker?: string } {
  if (!tab) return { bereich: STANDARD_BEREICH };
  if (istBereich(tab)) return { bereich: tab };
  if (Object.prototype.hasOwnProperty.call(ALIASE, tab)) return { ...ALIASE[tab] };
  return { bereich: STANDARD_BEREICH };
}

/** Adresse eines Bereichs des Admin-Bereichs. */
export function adminPfad(bereich: AdminBereich | "lizenz"): string {
  return `/admin?tab=${bereich}`;
}

// ---------------------------------------------------------------------------
// Suche im Admin-Bereich
// ---------------------------------------------------------------------------

/** Ein Ziel der Suche: ein Bereich oder eine Stelle darin (``anker`` = id auf der Seite). */
export interface AdminSuchziel {
  titel: string;
  beschreibung: string;
  bereich: AdminBereich;
  anker?: string;
  /** Weitere Woerter, unter denen man es sucht (Synonyme, englische Begriffe). */
  stichworte: string[];
}

/** Stichworte je Bereich — der Name des Bereichs zaehlt immer mit. */
const BEREICH_STICHWORTE: Record<AdminBereich, { beschreibung: string; stichworte: string[] }> = {
  suche: { beschreibung: "Alle Einstellungen des Admin-Bereichs durchsuchen.", stichworte: ["finden", "search"] },
  users: { beschreibung: "Konten anlegen, freigeben, sperren, Passwort und Zwei-Faktor zurücksetzen.",
    stichworte: ["benutzer", "konto", "konten", "user", "anmeldung", "passwort", "zwei-faktor", "2fa", "mfa", "einladen", "freigeben", "sperren", "löschen", "mount-rechte"] },
  roles: { beschreibung: "Rollen, Gruppen und Rechte, Autonomie-Grenzen je Gruppe.",
    stichworte: ["rechte", "berechtigung", "gruppe", "rolle", "permissions", "autonomie", "mountshares"] },
  "sso-groups": { beschreibung: "Gruppen aus Microsoft Entra / SSO auf Rollen abbilden.",
    stichworte: ["sso", "entra", "azure ad", "microsoft", "single sign-on", "gruppen"] },
  agents: { beschreibung: "Alle Agenten der Anlage ansehen, stoppen, aktualisieren.",
    stichworte: ["agent", "agenten", "container", "aktualisieren", "update", "stoppen"] },
  assignments: { beschreibung: "Agenten Nutzern zuweisen.", stichworte: ["zuweisen", "zuordnung", "besitzer", "teilen"] },
  vorlagen: { beschreibung: "Vorlagen für neue Agenten.", stichworte: ["template", "vorlage", "rollenbeschreibung"] },
  modelle: { beschreibung: "KI-Modelle und Anbieter freischalten, Anthropic, OpenAI, Bedrock, Azure.",
    stichworte: ["modell", "llm", "anbieter", "provider", "claude", "openai", "gpt", "bedrock", "azure", "vertex", "ollama", "katalog"] },
  "ai-accounts": { beschreibung: "KI-Konten und Zugänge (API-Schlüssel, Abos).",
    stichworte: ["api key", "abo", "zugang", "konto", "codex", "chatgpt", "teamlizenz"] },
  sprache: { beschreibung: "Sprachassistent, Stimmen und Sprachmodelle.",
    stichworte: ["voice", "stimme", "sprachassistent", "nova sonic", "elevenlabs", "transkription", "whisper"] },
  "second-brains": { beschreibung: "Geteilte Wissens-Ablagen (Second Brains) anlegen und freigeben.",
    stichworte: ["second brain", "wissen", "vault", "ablage", "obsidian", "mount"] },
  "web-search": { beschreibung: "Anbieter und Einstellungen der Websuche.", stichworte: ["internet", "brave", "serp", "duckduckgo", "recherche"] },
  mcp: { beschreibung: "Werkzeug-Server (MCP) der Anlage anlegen, prüfen und für Agenten freigeben.",
    stichworte: ["mcp", "werkzeug-server", "werkzeuge", "tools", "server", "model context protocol", "anbindung"] },
  "integrationen-anlage": { beschreibung: "Dienste und Konten der Anlage: Microsoft 365, Google, Git, Telegram, Teams.",
    stichworte: ["integration", "anbindung", "microsoft 365", "m365", "outlook", "google", "github", "gitea", "forgejo", "telegram", "teams", "slack", "oauth"] },
  system: { beschreibung: "Version, Updates, Lizenz, Anzeigewährung und Grundeinstellungen der Anlage.",
    stichworte: ["lizenz", "version", "update", "währung", "euro", "dollar", "kurs", "einstellungen", "settings", "zwei-faktor-pflicht"] },
  pages: { beschreibung: "Eigene Menüpunkte im Seitenmenü.", stichworte: ["menü", "link", "seite", "navigation"] },
  "master-rules": { beschreibung: "Regeln, die für alle Agenten gelten.", stichworte: ["regeln", "richtlinie", "vorgaben", "prompt", "anweisung"] },
  secrets: { beschreibung: "Schlüssel und Zugangsdaten für Agenten, erlaubte Ziele.",
    stichworte: ["secret", "api-schlüssel", "token", "passwort", "zugangsdaten", "erlaubte ziele", "egress", "proxy"] },
  dlp: { beschreibung: "Filter gegen das Versenden sensibler Daten.", stichworte: ["dlp", "datenschutz", "filter", "sensible daten", "pii"] },
  audit: { beschreibung: "Protokoll aller sicherheitsrelevanten Aktionen.", stichworte: ["log", "protokoll", "audit", "verlauf", "nachvollziehen"] },
  gesetze: { beschreibung: "Gesetze und Vorgaben, die Agenten beachten.", stichworte: ["recht", "dsgvo", "ai act", "compliance"] },
  health: { beschreibung: "Zustand der Anlage: Dienste, Speicher, Sicherung, Sicherheitsstatus.",
    stichworte: ["status", "gesundheit", "health", "speicher", "platte", "sicherung", "backup", "sicherheitsstatus", "signaturen", "monitoring"] },
  budget: { beschreibung: "Kosten der Agenten, Budgets je Agent und je Nutzer.",
    stichworte: ["kosten", "budget", "geld", "ausgaben", "limit", "obergrenze", "monatsbudget", "verbrauch", "rechnung"] },
  feedback: { beschreibung: "Rückmeldungen der Nutzer.", stichworte: ["feedback", "rückmeldung", "fehler melden", "bug"] },
};

/** Einzelne Stellen innerhalb eines Bereichs, direkt anspringbar. */
const STELLEN: AdminSuchziel[] = [
  { titel: "Budget je Nutzer", bereich: "budget", anker: "nutzer-budget",
    beschreibung: "Höchstbetrag, den ein Nutzer mit allen Agenten im Monat verbrauchen darf; Standard für alle.",
    stichworte: ["nutzerbudget", "maximales budget", "höchstbetrag", "obergrenze", "limit", "user budget"] },
  { titel: "Budget je Agent", bereich: "budget", anker: "agenten-budget",
    beschreibung: "Monatsbudget einzelner Agenten und was bei Überschreitung passiert.",
    stichworte: ["agentenbudget", "stoppen", "günstigeres modell", "haiku"] },
  { titel: "Budget gilt auch im Chat", bereich: "budget", anker: "chat-budget",
    beschreibung: "Ob ein aufgebrauchtes Budget auch Chat-Nachrichten sperrt.", stichworte: ["chat", "nachrichten", "sperre"] },
  { titel: "Untätige Agenten automatisch stoppen", bereich: "budget", anker: "leerlauf-stopp",
    beschreibung: "Nach wie vielen Minuten ohne Arbeit ein Agent gestoppt wird.", stichworte: ["idle", "leerlauf", "ressourcen", "minuten"] },
  { titel: "Lizenzschlüssel", bereich: "system", anker: "lizenz",
    beschreibung: "Lizenz der Anlage eintragen und prüfen.", stichworte: ["lizenz", "schlüssel", "license"] },
];

/** Alle Ziele: jeder Bereich (ausser der Suche selbst) und die Stellen darin. */
export const ADMIN_SUCHZIELE: AdminSuchziel[] = [
  ...ADMIN_GRUPPEN.flatMap((g) => g.bereiche)
    .filter((b) => b !== "suche")
    .map((b) => ({ titel: ADMIN_BEREICH_NAMEN[b], bereich: b, ...BEREICH_STICHWORTE[b] })),
  ...STELLEN,
];

/** Kleinbuchstaben, Umlaute wie ausgeschrieben („Kosten“ findet „kosten“, „ae“ findet „ä“). */
function normal(text: string): string {
  return text.toLowerCase().replace(/ä/g, "ae").replace(/ö/g, "oe").replace(/ü/g, "ue").replace(/ß/g, "ss");
}

/** Treffer für den Suchtext, bester zuerst. Jedes Wort muss vorkommen. */
export function adminSuche(text: string): AdminSuchziel[] {
  const woerter = normal(text).split(/\s+/).filter(Boolean);
  if (!woerter.length) return [];
  const bewertet: { ziel: AdminSuchziel; punkte: number }[] = [];
  ADMIN_SUCHZIELE.forEach((ziel, reihenfolge) => {
    const titel = normal(ziel.titel);
    const bereich = normal(ADMIN_BEREICH_NAMEN[ziel.bereich]);
    const stichworte = ziel.stichworte.map(normal);
    const alles = [titel, bereich, normal(ziel.beschreibung), ...stichworte].join(" ");
    if (!woerter.every((w) => alles.includes(w))) return;
    let punkte = 0;
    for (const w of woerter) {
      if (titel.startsWith(w)) punkte += 6;
      else if (titel.includes(w)) punkte += 4;
      if (stichworte.some((s) => s.startsWith(w))) punkte += 3;
      if (bereich.includes(w)) punkte += 1;
    }
    // Bei Gleichstand gewinnt die Stelle (genauer) vor dem Bereich, dann die Reihenfolge.
    bewertet.push({ ziel, punkte: punkte * 1000 + (ziel.anker ? 500 : 0) - reihenfolge });
  });
  return bewertet.sort((a, b) => b.punkte - a.punkte).map((t) => t.ziel);
}

/** Adresse eines Suchziels, mit Sprungmarke. */
export function adminZiel(ziel: Pick<AdminSuchziel, "bereich" | "anker">): string {
  return adminPfad(ziel.bereich) + (ziel.anker ? `#${ziel.anker}` : "");
}

/** Direkt zum Lizenzschluessel (Streifen „Lizenzschlüssel eintragen“, Handbuch). */
export const LIZENZ_PFAD = adminPfad("lizenz");

/**
 * Wohin ein alter Verweis auf `/settings?tab=…#…` heute fuehrt — oder `null`,
 * wenn er auf „Meine Einstellungen“ bleibt.
 *
 * Bis #899 lagen dort auch die anlagenweiten Reiter (Modelle, Integrationen,
 * Sprachassistent, System). Fuer Administratoren fuehren solche Verweise jetzt in
 * den passenden Bereich des Admin-Bereichs. Ein Mitglied bleibt immer hier: Die
 * Konsole wuerde es ohnehin abweisen, und seine eigenen Bereiche stehen hier.
 */
export function alteEinstellungenWeiterleitung(
  tab: string | null | undefined,
  anker: string | null | undefined,
  istAdmin: boolean,
): string | null {
  if (!istAdmin) return null;
  const sprungziel = (anker || "").replace(/^#/, "");
  if (sprungziel === "lizenz") return LIZENZ_PFAD;
  switch (tab) {
    case "system":
      return adminPfad("system");
    case "voice":
      return adminPfad("sprache");
    case "modelle":
      return adminPfad("modelle");
    case "integrationen":
      return adminPfad("integrationen-anlage");
    default:
      return null;
  }
}
