import type { Agent } from "./types";

/**
 * Einstellungen der Agentenseite — am Konto unter dem Schlüssel `agents_page`
 * (`/auth/me/ui-preferences`), damit sie auf jedem Gerät gelten.
 *
 * Der Server prüft nur Schlüssel und Größe; was hier ankommt, kann aus einer
 * älteren Version stammen oder von Hand verändert sein. `normalizeAgentsPagePrefs`
 * macht daraus immer einen gültigen Stand: Unbekanntes fällt weg, Fehlendes kommt
 * aus den Standardwerten.
 */

export const AGENTS_PAGE_KEY = "agents_page";

export type ViewMode = "grid" | "network" | "teams" | "list";
export const VIEW_MODES: ViewMode[] = ["grid", "network", "teams", "list"];

export type ListGrouping = "team" | "tag" | "none";
export const LIST_GROUPINGS: ListGrouping[] = ["team", "tag", "none"];

export type SortDirection = "asc" | "desc";

/** Spalten der Listenansicht in Standard-Reihenfolge (das Auswahl-Kästchen steht immer vorn). */
export const LIST_COLUMNS = ["name", "status", "aufgabe", "gruppe", "zuletzt_aktiv", "kosten", "warnungen"] as const;
export type ListColumn = (typeof LIST_COLUMNS)[number];

export const COLUMN_LABELS: Record<ListColumn, string> = {
  name: "Name",
  status: "Status",
  aufgabe: "Aufgabe",
  gruppe: "Gruppe",
  zuletzt_aktiv: "Zuletzt aktiv",
  kosten: "Kosten (Monat)",
  warnungen: "Hinweise",
};

/** Statusgruppen, nach denen gefiltert werden kann (`filter[].status`). */
export const STATUS_FILTERS = ["working", "ready", "sleeping", "stopped", "error"] as const;
export type StatusFilter = (typeof STATUS_FILTERS)[number];
/** „Startet“ (state `created`) ist kein Filter — ein Übergang, kein Zustand. */
export type AgentStatusKey = StatusFilter | "starting";

export const STATUS_LABELS: Record<AgentStatusKey, string> = {
  working: "Arbeitet",
  ready: "Bereit",
  sleeping: "Schläft",
  stopped: "Gestoppt",
  error: "Fehler",
  starting: "Startet",
};

/** Gestoppt ist nicht gleich gestoppt: der Leerlauf-Stopp „schläft“ und wacht bei
 *  Bedarf wieder auf; hat der Mensch ihn angehalten, bleibt er aus. */
export function agentStatusKey(agent: Pick<Agent, "state" | "stopped_by_user">): AgentStatusKey {
  switch (agent.state) {
    case "working":
      return "working";
    case "running":
    case "idle":
      return "ready";
    case "stopped":
      return agent.stopped_by_user ? "stopped" : "sleeping";
    case "error":
      return "error";
    default:
      return "starting";
  }
}

export interface SavedFilter {
  id: string;
  name: string;
  suche: string;
  schlagwort: string;
  status: StatusFilter[];
}

export const MAX_SAVED_FILTERS = 20;

export interface AgentsPagePrefs {
  ansicht: ViewMode;
  liste: {
    spalten: ListColumn[];
    sortierung: { spalte: ListColumn; richtung: SortDirection };
    gruppierung: ListGrouping;
  };
  filter: SavedFilter[];
}

export const DEFAULT_AGENTS_PAGE_PREFS: AgentsPagePrefs = {
  // Karten bleiben für alle der Standard.
  ansicht: "grid",
  liste: {
    spalten: [...LIST_COLUMNS],
    sortierung: { spalte: "name", richtung: "asc" },
    gruppierung: "team",
  },
  filter: [],
};

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function oneOf<T extends string>(v: unknown, allowed: readonly T[], fallback: T): T {
  return allowed.includes(v as T) ? (v as T) : fallback;
}

/** Unbekannte Spaltenschlüssel ignorieren, doppelte entfernen, fehlende hinten anhängen. */
export function normalizeColumns(raw: unknown): ListColumn[] {
  const seen = new Set<ListColumn>();
  if (Array.isArray(raw)) {
    for (const c of raw) {
      if (LIST_COLUMNS.includes(c as ListColumn)) seen.add(c as ListColumn);
    }
  }
  for (const c of LIST_COLUMNS) seen.add(c);
  return [...seen];
}

function normalizeFilter(raw: unknown): SavedFilter | null {
  if (!isRecord(raw)) return null;
  const name = typeof raw.name === "string" ? raw.name.trim() : "";
  if (!name) return null;
  const status = Array.isArray(raw.status)
    ? [...new Set(raw.status.filter((s): s is StatusFilter => STATUS_FILTERS.includes(s as StatusFilter)))]
    : [];
  return {
    id: typeof raw.id === "string" && raw.id ? raw.id : newFilterId(),
    name,
    suche: typeof raw.suche === "string" ? raw.suche : "",
    schlagwort: typeof raw.schlagwort === "string" ? raw.schlagwort : "",
    status,
  };
}

export function normalizeAgentsPagePrefs(raw: unknown): AgentsPagePrefs {
  const d = DEFAULT_AGENTS_PAGE_PREFS;
  if (!isRecord(raw)) return { ...d, liste: { ...d.liste, spalten: [...d.liste.spalten] }, filter: [] };
  const liste = isRecord(raw.liste) ? raw.liste : {};
  const sort = isRecord(liste.sortierung) ? liste.sortierung : {};
  const filter = Array.isArray(raw.filter)
    ? raw.filter.map(normalizeFilter).filter((f): f is SavedFilter => f !== null).slice(0, MAX_SAVED_FILTERS)
    : [];
  return {
    ansicht: oneOf(raw.ansicht, VIEW_MODES, d.ansicht),
    liste: {
      spalten: normalizeColumns(liste.spalten),
      sortierung: {
        spalte: oneOf(sort.spalte, LIST_COLUMNS, d.liste.sortierung.spalte),
        richtung: oneOf(sort.richtung, ["asc", "desc"] as const, d.liste.sortierung.richtung),
      },
      gruppierung: oneOf(liste.gruppierung, LIST_GROUPINGS, d.liste.gruppierung),
    },
    filter,
  };
}

export function newFilterId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `f-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}
