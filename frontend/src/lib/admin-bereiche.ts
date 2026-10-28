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
  | "users" | "roles" | "sso-groups"
  | "agents" | "assignments" | "vorlagen"
  | "modelle" | "ai-accounts" | "sprache" | "second-brains" | "web-search"
  | "integrationen-anlage" | "system" | "pages"
  | "master-rules" | "secrets" | "dlp" | "audit" | "gesetze"
  | "health" | "budget" | "feedback";

export interface AdminGruppe {
  id: string;
  titel: string;
  bereiche: AdminBereich[];
}

/** Reihenfolge = Reihenfolge in der Leiste des Admin-Bereichs (Redesign 2026-10,
 *  Vorbild lavalake-Konsole: Menschen, Agenten, KI, Anbindungen, Regeln, Anlage). */
export const ADMIN_GRUPPEN: AdminGruppe[] = [
  { id: "people", titel: "Menschen", bereiche: ["users", "roles", "sso-groups"] },
  { id: "agents", titel: "Agenten", bereiche: ["agents", "assignments", "vorlagen"] },
  { id: "ki", titel: "KI", bereiche: ["modelle", "ai-accounts", "sprache", "web-search"] },
  { id: "anlage", titel: "Anbindungen", bereiche: ["integrationen-anlage", "second-brains", "pages"] },
  { id: "security", titel: "Regeln", bereiche: ["master-rules", "dlp", "gesetze", "secrets", "audit"] },
  { id: "ops", titel: "Anlage", bereiche: ["health", "budget", "system", "feedback"] },
];

export const ADMIN_BEREICH_NAMEN: Record<AdminBereich, string> = {
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

export const STANDARD_BEREICH: AdminBereich = "users";

const ALLE_BEREICHE: readonly AdminBereich[] = ADMIN_GRUPPEN.flatMap((g) => g.bereiche);

/** Fruehere Kennungen, die weiter funktionieren muessen (Lesezeichen, Handbuch). */
const ALIASE: Record<string, { bereich: AdminBereich; anker?: string }> = {
  // Bis #899 ein Sammelreiter mit der kompletten Einstellungsseite darin.
  settings: { bereich: "modelle" },
  lizenz: { bereich: "system", anker: "lizenz" },
  voice: { bereich: "sprache" },
  integrationen: { bereich: "integrationen-anlage" },
};

function istBereich(tab: string): tab is AdminBereich {
  return (ALLE_BEREICHE as readonly string[]).includes(tab);
}

/** Welcher Bereich zu `?tab=` gehoert. Unbekanntes faellt auf die Nutzerliste. */
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
