/**
 * Die Bereiche der Admin-Konsole und die Weiterleitungen alter Verweise (#899).
 *
 * Trennung nach Eigentum: Alles, was der ganzen Anlage gehoert, steht in der
 * Admin-Konsole — in EINER Ebene, eine Seitennavigation mit Gruppenueberschriften.
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

/** Reihenfolge = Reihenfolge in der Seitennavigation. */
export const ADMIN_GRUPPEN: AdminGruppe[] = [
  { id: "people", titel: "Nutzer & Rollen", bereiche: ["users", "roles", "sso-groups"] },
  { id: "agents", titel: "Agenten", bereiche: ["agents", "assignments", "vorlagen"] },
  { id: "ki", titel: "KI & Wissen", bereiche: ["modelle", "ai-accounts", "sprache", "second-brains", "web-search"] },
  { id: "anlage", titel: "Anlage", bereiche: ["integrationen-anlage", "system", "pages"] },
  { id: "security", titel: "Compliance", bereiche: ["master-rules", "secrets", "dlp", "audit", "gesetze"] },
  { id: "ops", titel: "Betrieb", bereiche: ["health", "budget", "feedback"] },
];

export const ADMIN_BEREICH_NAMEN: Record<AdminBereich, string> = {
  users: "Nutzer",
  roles: "Rollen",
  "sso-groups": "SSO-Gruppen",
  agents: "Alle Agenten",
  assignments: "Zuweisungen",
  vorlagen: "Agenten-Vorlagen",
  modelle: "Modelle & Anbieter",
  "ai-accounts": "KI-Konten",
  sprache: "Sprache (Voice)",
  "second-brains": "Second Brains",
  "web-search": "Websuche",
  "integrationen-anlage": "Integrationen (Anlage)",
  system: "System & Lizenz",
  pages: "Seiten & Links",
  "master-rules": "Master-Regeln",
  secrets: "Schlüssel & Zugangsdaten",
  dlp: "DLP-Filter",
  audit: "Protokoll",
  gesetze: "Gesetze",
  health: "Systemzustand",
  budget: "Budget",
  feedback: "Feedback",
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

/** Adresse eines Bereichs der Admin-Konsole. */
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
 * den passenden Bereich der Admin-Konsole. Ein Mitglied bleibt immer hier: Die
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
