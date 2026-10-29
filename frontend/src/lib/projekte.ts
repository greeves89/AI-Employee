/** Projekte: ein Projekt ist ein Team mit Board aus Karten, Raum, Takt und Wissen
 *  (orchestrator: core/projekte.py, api/teams.py). */

/** Gespeicherte Schlüssel der Spalten — angezeigt heißt „eingang“ Inbox und „pruefung“ Review. */
export type ProjektSpalte = "eingang" | "bereit" | "in_arbeit" | "triage" | "pruefung" | "erledigt" | "verworfen";
/** Wer ein Issue übernommen hat (orchestrator: core/uebernahme.py). */
export type BearbeiterArt = "plattform" | "claude_code" | "andere_anlage" | "mensch" | "extern";

export const BEARBEITER_ART: Record<BearbeiterArt, string> = {
  plattform: "Agent dieser Anlage",
  claude_code: "Claude-Code-Sitzung",
  andere_anlage: "Agent einer anderen AI-Employee-Anlage",
  mensch: "Mensch (Zuständige im Repo)",
  extern: "Jemand außerhalb des Boards (Status-Tag im Repo)",
};
export type ProjektStufe = "planung" | "bau" | "tests" | "pr" | "ci" | "merge";

export interface ProjektKarte {
  id: string;
  titel: string;
  text: string | null;
  quelle: "eigen" | "issue" | "pr";
  extern_nr: number | null;
  url: string | null;
  labels: string[];
  spalte: ProjektSpalte;
  /** Rückfrage offen: in Triage oder im Review mit Triage-Tag (orchestrator: projekte.wartet_auf_dich). */
  wartet_auf_dich?: boolean;
  /** 1 hoch, 2 normal, 3 niedrig */
  prioritaet: number;
  agent_id: string | null;
  task_id: string | null;
  task_status: string | null;
  stufe: ProjektStufe | null;
  notiz: string | null;
  /** Wer das Issue übernommen hat (Name aus der Übernahme-Kopfzeile, „@name“ oder „extern“). */
  extern_bearbeiter: string | null;
  /** Art des Bearbeiters: Agent dieser Anlage, Claude Code, andere Anlage, Mensch, sonst extern. */
  bearbeiter_art?: BearbeiterArt | null;
  /** Bei einem Agenten dieser Anlage: seine Kennung (für Bild und Namen). */
  extern_agent_id?: string | null;
  /** Seit wann jemand von außen dran ist (geschätzt aus dem Repo). */
  extern_seit?: string | null;
  /** Letztes Lebenszeichen von außen: Änderung am Issue oder Commit auf dem Branch. */
  extern_regung?: string | null;
  /** Branch mit der Ticketnummer, auf dem von außen gearbeitet wird. */
  extern_branch?: string | null;
  /** Übernommen, aber abgelaufen: kein Lebenszeichen innerhalb der Dauer des Projekts. */
  extern_stockt?: boolean;
  /** Bis wann die Übernahme von außen ohne weiteres Lebenszeichen gilt. */
  extern_bis?: string | null;
  /** Zwei gültige Übernahmen oder ein fremder Commit auf dem Branch — rot markiert. */
  konflikt?: string | null;
  /** Hält der Bearbeiter von außen mehr Übernahmen als erlaubt: wie viele. */
  bearbeiter_uebernahmen?: number | null;
  pr_nr: number | null;
  pr_url: string | null;
  pr_zustand: string | null;
  erstellt: string | null;
  aktualisiert: string | null;
  erledigt: string | null;
  kosten_usd: number | null;
  ergebnis: string | null;
  fehler: string | null;
}

export interface ProjektLauf {
  task_id: string;
  titel: string;
  agent_id: string | null;
  status: string;
  gestartet: string | null;
  karte_id: string | null;
  karte_titel: string | null;
  stufe: ProjektStufe | null;
  teilarbeit: boolean;
  /** Was der Agent zuletzt geschrieben oder getan hat (nur bei laufenden Läufen). */
  letzte_ausgabe: string | null;
}

export interface ProjektKennzahlen {
  eingang: number;
  bereit: number;
  in_arbeit: number;
  /** Offene Rückfragen: Karten in Triage und Review-Karten mit Triage-Tag — warten auf einen Menschen. */
  triage?: number;
  pruefung: number;
  /** Verworfen in den letzten 30 Tagen. */
  verworfen?: number;
  wartet_auf_merge: number;
  laeufe: number;
  /** Kosten der Läufe des Projekts im laufenden Monat (USD). */
  kosten_monat_usd?: number;
  /** Höchstens so viele offene Übernahmen je Bearbeiter (Projekteinstellung). */
  obergrenze_uebernahmen?: number;
  /** Karten mit Konflikt (zwei Übernahmen, fremder Commit). */
  konflikte?: number;
  heute_fertig: number;
  fertig_7_tage: number;
  verlauf: { tag: string; anzahl: number }[];
}

export interface ProjektTakt {
  minuten: number | null;
  aktiv: boolean;
  naechster_lauf: string | null;
  agent_id: string | null;
}

export interface ProjektBoard {
  spalten: Record<ProjektSpalte, ProjektKarte[]>;
  laufend: ProjektLauf[];
  kennzahlen: ProjektKennzahlen;
  takt: ProjektTakt;
}

/** Spalten des Boards in ihrer Reihenfolge. ``beschreibung``: graue Zeile unter dem Namen.
 *  ``schmal``: lässt sich einklappen (Verworfen). */
export const SPALTEN: { key: ProjektSpalte; label: string; beschreibung: string; leer: string; schmal?: boolean }[] = [
  { key: "eingang", label: "Inbox", beschreibung: "Neu angelegt, noch nicht gesichtet", leer: "Nichts Neues." },
  { key: "triage", label: "Triage", beschreibung: "Rückfragen offen, wartet auf einen Menschen", leer: "Keine offenen Rückfragen." },
  { key: "bereit", label: "Bereit", beschreibung: "Freigegeben von einem Menschen, Agenten nehmen sie", leer: "Nichts freigegeben." },
  { key: "in_arbeit", label: "In Arbeit", beschreibung: "In Umsetzung", leer: "Gerade arbeitet niemand an einer Karte." },
  { key: "pruefung", label: "Review", beschreibung: "PR offen, wartet auf Prüfung und Merge", leer: "Nichts abzunehmen." },
  { key: "erledigt", label: "Erledigt", beschreibung: "Gemergt bzw. Issue geschlossen", leer: "Noch nichts erledigt." },
  { key: "verworfen", label: "Verworfen", beschreibung: "Wird nicht umgesetzt, Begründung am Issue", leer: "Nichts verworfen.", schmal: true },
];

/** Anzeigename einer Spalte (für Texte außerhalb des Boards). */
export const spaltenName = (key: string) => SPALTEN.find((s) => s.key === key)?.label ?? key;

export const STUFEN: { key: ProjektStufe; label: string }[] = [
  { key: "planung", label: "Planung" },
  { key: "bau", label: "Bau" },
  { key: "tests", label: "Tests" },
  { key: "pr", label: "PR" },
  { key: "ci", label: "CI" },
  { key: "merge", label: "Merge" },
];

export const PRIORITAETEN: Record<number, { label: string; klasse: string }> = {
  1: { label: "P1", klasse: "bg-red-500/15 text-red-600 dark:text-red-400" },
  2: { label: "P2", klasse: "bg-foreground/[0.06] text-muted-foreground" },
  3: { label: "P3", klasse: "bg-foreground/[0.04] text-muted-foreground/70" },
};

export const TAKTE: { minuten: number; label: string }[] = [
  { minuten: 0, label: "Aus — nur auf Klick" },
  { minuten: 30, label: "Alle 30 Minuten" },
  { minuten: 60, label: "Stündlich" },
  { minuten: 120, label: "Alle 2 Stunden" },
  { minuten: 240, label: "Alle 4 Stunden" },
];

export const PR_ZUSTAND: Record<string, string> = {
  offen: "PR offen",
  ci_laeuft: "CI läuft",
  ci_rot: "CI rot",
  ci_gruen: "CI grün",
  gemergt: "gemergt",
  geschlossen: "geschlossen",
};

/** Kennfarben — dieselben Schlüssel wie PROJEKT_FARBEN im Orchestrator (api/teams.py). */
export const PROJEKT_FARBEN: Record<string, { name: string; hex: string }> = {
  blau: { name: "Blau", hex: "#3b82f6" },
  violett: { name: "Violett", hex: "#8b5cf6" },
  gruen: { name: "Grün", hex: "#22c55e" },
  orange: { name: "Orange", hex: "#f97316" },
  rot: { name: "Rot", hex: "#ef4444" },
  tuerkis: { name: "Türkis", hex: "#14b8a6" },
  gelb: { name: "Gelb", hex: "#eab308" },
  grau: { name: "Grau", hex: "#94a3b8" },
};

export function projektFarbe(farbe: string | null | undefined): string {
  return (farbe && PROJEKT_FARBEN[farbe]?.hex) || PROJEKT_FARBEN.blau.hex;
}

/** Wochentag-Kürzel für den Verlauf (Mo, Di …). */
export function wochentag(isoTag: string): string {
  const d = new Date(`${isoTag}T12:00:00`);
  return ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"][d.getDay()];
}

/** „in 12 Min.“ bis zu einem Zeitpunkt (Takt). */
export function bis(iso: string | null): string | null {
  if (!iso) return null;
  const min = Math.round((new Date(iso).getTime() - Date.now()) / 60000);
  if (min <= 0) return "gleich";
  if (min < 60) return `in ${min} Min.`;
  const std = Math.floor(min / 60);
  return `in ${std} Std.${min % 60 ? ` ${min % 60} Min.` : ""}`;
}

export type NachrichtArt = "nachricht" | "frage" | "entscheidung" | "antwort" | "protokoll" | "bericht" | "ergebnis";

export interface ProjektNachricht {
  id: number;
  /** extern: aus einem Issue-Kommentar „@ai-employee …“ (Rückkanal). */
  autor_typ: "mensch" | "agent" | "system" | "extern";
  autor_id: string | null;
  autor_name: string;
  text: string;
  art: NachrichtArt;
  an: string[];
  karte_id: string | null;
  task_id: string | null;
  erstellt: string | null;
}

export const NACHRICHT_ART: Record<NachrichtArt, { label: string; klasse: string }> = {
  nachricht: { label: "", klasse: "" },
  frage: { label: "Frage", klasse: "bg-sky-500/15 text-sky-700 dark:text-sky-300" },
  entscheidung: { label: "Entscheidung", klasse: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300" },
  antwort: { label: "Antwort", klasse: "bg-foreground/[0.06] text-muted-foreground" },
  protokoll: { label: "Protokoll", klasse: "bg-violet-500/15 text-violet-700 dark:text-violet-300" },
  bericht: { label: "Tagesbericht", klasse: "bg-amber-500/15 text-amber-700 dark:text-amber-300" },
  ergebnis: { label: "Ergebnis", klasse: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" },
};

export interface ProjektTermin {
  id: string;
  art: "daily" | "weekly" | "einmalig";
  thema: string;
  uhrzeit: string | null;
  wochentag: string | null;
  cron: string | null;
  aktiv: boolean;
  naechster: string | null;
  zuletzt: string | null;
}

export interface ProjektBesprechung {
  id: string;
  name: string;
  zustand: string;
  erstellt: string | null;
  zusammenfassung: string | null;
}

export const WOCHENTAGE: { key: string; label: string }[] = [
  { key: "mo", label: "Montag" }, { key: "di", label: "Dienstag" }, { key: "mi", label: "Mittwoch" },
  { key: "do", label: "Donnerstag" }, { key: "fr", label: "Freitag" }, { key: "sa", label: "Samstag" },
  { key: "so", label: "Sonntag" },
];


/** GitHub-Repo (owner/name oder github.com-Adresse)? Sonst Forgejo/Gitea mit eigener Adresse. */
export function istGithubRepo(repo: string): boolean {
  const r = repo.trim().toLowerCase();
  return !r.startsWith("https://") || r.startsWith("https://github.com/");
}

/** Wohin der Repo-Link führt: GitHub aus owner/name, Forgejo/Gitea steht schon als Adresse da. */
export function repoAdresse(repo: string): string {
  return repo.startsWith("https://") ? repo : `https://github.com/${repo}`;
}

/** Kurzform für die Anzeige: owner/name, bei eigenem Server mit Servername davor. */
export function repoName(repo: string): string {
  if (!repo.startsWith("https://")) return repo;
  return repo.replace(/^https:\/\//, "");
}
