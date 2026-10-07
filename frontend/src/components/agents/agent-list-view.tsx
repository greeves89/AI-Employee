"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  ChevronDown,
  ChevronRight,
  Columns3,
  Loader2,
  Play,
  RotateCw,
  Square,
  Trash2,
  X,
} from "lucide-react";
import type { Agent } from "@/lib/types";
import * as api from "@/lib/api";
import { cn, timeAgo } from "@/lib/utils";
import { formatMoney } from "@/lib/money";
import { apiFehlertext } from "@/lib/api-fehler";
import { useToast } from "@/components/ui/dialog-provider";
import { AgentAvatar, getAgentTag } from "@/components/agents/agent-avatar";
import {
  COLUMN_LABELS,
  LIST_COLUMNS,
  STATUS_LABELS,
  agentStatusKey,
  type AgentStatusKey,
  type ListColumn,
  type ListGrouping,
  type SortDirection,
} from "@/lib/agents-page-prefs";

/**
 * Kompakte Listenansicht der Agentenseite — für 100 und mehr Agenten, wo die
 * Karten unübersichtlich werden: eine Zeile je Agent, Name vorn, scrollbar mit
 * stehender Kopfzeile. Suche, Filter und Gruppierung kommen fertig von der Seite
 * (dieselben wie bei den Karten); sortiert wird hier, je Gruppe.
 */

export interface AgentListGroup {
  key: string;
  name: string;
  agents: Agent[];
}

/** Höchstens so viele Start/Stopp-Aufrufe gleichzeitig bei der Mehrfachauswahl. */
const BULK_CONCURRENCY = 5;

const ACTIVE_STATES = ["running", "idle", "working"];

const STATUS_DOT: Record<AgentStatusKey, string> = {
  working: "bg-blue-400 animate-pulse",
  ready: "bg-emerald-400",
  sleeping: "bg-foreground/25",
  stopped: "bg-foreground/50",
  error: "bg-red-400",
  starting: "bg-amber-400",
};

/** Sortierrang des Status: wer etwas tut, steht oben. */
const STATUS_RANK: Record<AgentStatusKey, number> = {
  working: 0,
  ready: 1,
  starting: 2,
  error: 3,
  sleeping: 4,
  stopped: 5,
};

/** Auf schmalen Bildschirmen nur Name, Status und Zuletzt aktiv. */
const ALWAYS_VISIBLE: ListColumn[] = ["name", "status", "zuletzt_aktiv"];

function budgetReached(a: Agent): boolean {
  return a.budget_usd != null && a.budget_usd > 0 && a.monthly_cost_usd >= a.budget_usd;
}

export function agentWarnings(a: Agent): string[] {
  const w: string[] = [];
  if (a.update_available) w.push("Update verfügbar");
  if (a.image_outdated) w.push("Image veraltet");
  if (budgetReached(a) && a.budget_exceeded_action === "stop") w.push("Budget-Stopp");
  if (!a.has_responsibilities) w.push("Keine Verantwortlichkeiten");
  return w;
}

function groupLabel(a: Agent, teamName: string | undefined): string {
  return [teamName, getAgentTag(a.config as Record<string, unknown> | null)].filter(Boolean).join(" · ");
}

function compareAgents(a: Agent, b: Agent, column: ListColumn, teamNameOf: Record<string, string>): number {
  switch (column) {
    case "status":
      return STATUS_RANK[agentStatusKey(a)] - STATUS_RANK[agentStatusKey(b)];
    case "aufgabe": {
      const load = (x: Agent) => (x.current_task ? 1000 : 0) + (x.queue_depth ?? 0);
      return load(a) - load(b);
    }
    case "gruppe":
      return groupLabel(a, teamNameOf[a.id]).localeCompare(groupLabel(b, teamNameOf[b.id]), "de");
    case "zuletzt_aktiv":
      return new Date(a.last_active_at ?? 0).getTime() - new Date(b.last_active_at ?? 0).getTime();
    case "kosten":
      return a.monthly_cost_usd - b.monthly_cost_usd;
    case "warnungen":
      return agentWarnings(a).length - agentWarnings(b).length;
    default:
      return a.name.localeCompare(b.name, "de");
  }
}

function sortAgents(agents: Agent[], column: ListColumn, direction: SortDirection, teamNameOf: Record<string, string>) {
  const sign = direction === "asc" ? 1 : -1;
  return [...agents].sort((a, b) => {
    // „Nie aktiv“ gehört in beide Richtungen ans Ende, nicht an den Anfang.
    if (column === "zuletzt_aktiv" && !a.last_active_at !== !b.last_active_at) return a.last_active_at ? -1 : 1;
    return sign * compareAgents(a, b, column, teamNameOf) || a.name.localeCompare(b.name, "de");
  });
}

/** `fn` für alle `items`, höchstens `limit` gleichzeitig. Liefert die Fehlschläge. */
async function runLimited<T>(items: T[], limit: number, fn: (item: T) => Promise<unknown>) {
  const failures: { item: T; error: unknown }[] = [];
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const item = items[next++];
      try {
        await fn(item);
      } catch (error) {
        failures.push({ item, error });
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return failures;
}

type BulkAction = "start" | "stop" | "restart";

const BULK: Record<BulkAction, { label: string; verb: string; applies: (a: Agent) => boolean; run: (id: string) => Promise<unknown> }> = {
  start: { label: "Starten", verb: "gestartet", applies: (a) => !ACTIVE_STATES.includes(a.state), run: api.startAgent },
  stop: { label: "Stoppen", verb: "gestoppt", applies: (a) => ACTIVE_STATES.includes(a.state), run: api.stopAgent },
  restart: { label: "Neu starten", verb: "neu gestartet", applies: () => true, run: api.restartAgent },
};

export function AgentListView({
  groups,
  grouping,
  teamNameOf,
  columns,
  sortColumn,
  sortDirection,
  onSort,
  onColumnsChange,
  actionLoading,
  onStart,
  onStop,
  onRestart,
  onRemove,
  onRefresh,
}: {
  groups: AgentListGroup[];
  grouping: ListGrouping;
  teamNameOf: Record<string, string>;
  columns: ListColumn[];
  sortColumn: ListColumn;
  sortDirection: SortDirection;
  onSort: (column: ListColumn) => void;
  onColumnsChange: (columns: ListColumn[]) => void;
  actionLoading: string | null;
  onStart: (id: string) => void;
  onStop: (id: string) => void;
  onRestart: (id: string) => void;
  onRemove: (id: string) => void;
  onRefresh: () => Promise<void> | void;
}) {
  const router = useRouter();
  const toast = useToast();
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkRunning, setBulkRunning] = useState<BulkAction | null>(null);
  const [bulkFailures, setBulkFailures] = useState<{ name: string; message: string }[]>([]);

  const sortedGroups = useMemo(
    () => groups.map((g) => ({ ...g, agents: sortAgents(g.agents, sortColumn, sortDirection, teamNameOf) })),
    [groups, sortColumn, sortDirection, teamNameOf],
  );
  const visible = useMemo(() => sortedGroups.flatMap((g) => g.agents), [sortedGroups]);

  // Nur, was gerade zu sehen ist, zählt als ausgewählt — ein weggefilterter Agent
  // soll nicht unbemerkt mit gestoppt werden.
  const selectedAgents = useMemo(() => visible.filter((a) => selected.has(a.id)), [visible, selected]);
  const allSelected = visible.length > 0 && selectedAgents.length === visible.length;

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(visible.map((a) => a.id)));

  const clearSelection = () => {
    setSelected(new Set());
    setBulkFailures([]);
  };

  const runBulk = async (action: BulkAction) => {
    const { applies, run, verb } = BULK[action];
    const targets = selectedAgents.filter(applies);
    if (targets.length === 0) {
      toast.info(`Nichts zu tun — keiner der ausgewählten Agenten kann ${verb} werden.`);
      return;
    }
    setBulkRunning(action);
    setBulkFailures([]);
    try {
      const failures = await runLimited(targets, BULK_CONCURRENCY, (a) => run(a.id));
      setBulkFailures(failures.map(({ item, error }) => ({ name: item.name, message: apiFehlertext(error) })));
      // Wer geklappt hat, fällt aus der Auswahl; die Fehlschläge bleiben gewählt
      // und lassen sich so direkt noch einmal versuchen.
      const failed = new Set(failures.map((f) => f.item.id));
      setSelected((prev) => new Set([...prev].filter((id) => failed.has(id) || !targets.some((t) => t.id === id))));
      const ok = targets.length - failures.length;
      if (failures.length === 0) toast.success(`${ok} Agent${ok !== 1 ? "en" : ""} ${verb}`);
      else toast.error(`${failures.length} von ${targets.length} nicht ${verb}`, failures.map((f) => f.item.name).join(", "));
      await onRefresh();
    } finally {
      setBulkRunning(null);
    }
  };

  const toggleGroup = (key: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const cellVisibility = (c: ListColumn) => (ALWAYS_VISIBLE.includes(c) ? "" : "hidden md:table-cell");
  const colSpan = columns.length + 2;
  const showGroupHeaders = grouping !== "none";

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        {selectedAgents.length > 0 ? (
          <div
            data-testid="bulk-bar"
            className="flex flex-1 flex-wrap items-center gap-2 rounded-xl border border-primary/20 bg-primary/[0.06] px-3 py-2"
          >
            <span className="text-sm font-medium">{selectedAgents.length} ausgewählt</span>
            {(Object.keys(BULK) as BulkAction[]).map((action) => {
              const Icon = action === "start" ? Play : action === "stop" ? Square : RotateCw;
              return (
                <button
                  key={action}
                  onClick={() => runBulk(action)}
                  disabled={bulkRunning !== null}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-foreground/[0.08] bg-card px-2.5 py-1.5 text-xs hover:bg-foreground/[0.05] disabled:opacity-50"
                >
                  {bulkRunning === action ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Icon className="h-3.5 w-3.5" />}
                  {BULK[action].label}
                </button>
              );
            })}
            <button
              onClick={clearSelection}
              disabled={bulkRunning !== null}
              className="ml-auto inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-xs text-muted-foreground hover:text-foreground disabled:opacity-50"
            >
              <X className="h-3.5 w-3.5" />
              Auswahl aufheben
            </button>
            {bulkFailures.length > 0 && (
              <ul className="w-full space-y-0.5 text-xs text-red-500 dark:text-red-400" data-testid="bulk-failures">
                {bulkFailures.map((f) => (
                  <li key={f.name}>
                    <span className="font-medium">{f.name}:</span> {f.message}
                  </li>
                ))}
              </ul>
            )}
          </div>
        ) : (
          <div className="flex-1" />
        )}
        <ColumnOrderMenu columns={columns} onChange={onColumnsChange} />
      </div>

      <div
        data-testid="agent-list"
        className="max-h-[calc(100vh-16rem)] min-h-[12rem] overflow-auto rounded-xl border border-foreground/[0.06] bg-card/50"
      >
        <table className="w-full border-separate border-spacing-0 text-sm">
          <thead>
            <tr>
              <th className="sticky top-0 z-10 hidden w-10 border-b border-foreground/[0.06] bg-card px-3 md:table-cell">
                <input
                  type="checkbox"
                  aria-label="Alle auswählen"
                  data-testid="row-select-all"
                  checked={allSelected}
                  onChange={toggleAll}
                  className="h-4 w-4 cursor-pointer accent-primary"
                />
              </th>
              {columns.map((c) => {
                const active = sortColumn === c;
                return (
                  <th
                    key={c}
                    data-testid={`col-${c}`}
                    aria-sort={active ? (sortDirection === "asc" ? "ascending" : "descending") : "none"}
                    className={cn(
                      "sticky top-0 z-10 h-10 whitespace-nowrap border-b border-foreground/[0.06] bg-card p-0 text-left text-[11px] font-medium uppercase tracking-wide text-muted-foreground",
                      cellVisibility(c),
                    )}
                  >
                    <button
                      onClick={() => onSort(c)}
                      className={cn("flex h-full w-full items-center gap-1 px-3 uppercase hover:text-foreground", active && "text-foreground")}
                      title={`Nach ${COLUMN_LABELS[c]} sortieren`}
                    >
                      {COLUMN_LABELS[c]}
                      {active && (sortDirection === "asc" ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}
                    </button>
                  </th>
                );
              })}
              <th className="sticky top-0 z-10 hidden w-28 border-b border-foreground/[0.06] bg-card md:table-cell">
                <span className="sr-only">Aktionen</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {visible.length === 0 && (
              <tr>
                <td colSpan={colSpan} className="px-3 py-8 text-center text-sm text-muted-foreground">
                  Kein Agent passt zu diesem Filter.
                </td>
              </tr>
            )}
            {sortedGroups.map((g) => {
              const isCollapsed = showGroupHeaders && collapsed.has(g.key);
              return [
                showGroupHeaders && (
                  <tr key={`g-${g.key}`} data-testid="group-header">
                    <td colSpan={colSpan} className="border-b border-foreground/[0.06] bg-foreground/[0.03] px-3 py-1.5">
                      <button
                        onClick={() => toggleGroup(g.key)}
                        aria-expanded={!isCollapsed}
                        className="flex w-full items-center gap-1.5 text-left text-xs font-semibold"
                      >
                        {isCollapsed ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                        {g.name}
                        <span className="font-normal text-muted-foreground">({g.agents.length})</span>
                      </button>
                    </td>
                  </tr>
                ),
                ...(isCollapsed
                  ? []
                  : g.agents.map((agent) => (
                      <AgentRow
                        key={agent.id}
                        agent={agent}
                        columns={columns}
                        teamName={teamNameOf[agent.id]}
                        selected={selected.has(agent.id)}
                        busy={actionLoading === agent.id}
                        cellVisibility={cellVisibility}
                        onToggle={() => toggle(agent.id)}
                        onOpen={() => router.push(`/agents/${agent.id}`)}
                        onStart={() => onStart(agent.id)}
                        onStop={() => onStop(agent.id)}
                        onRestart={() => onRestart(agent.id)}
                        onRemove={() => onRemove(agent.id)}
                      />
                    ))),
              ];
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function AgentRow({
  agent,
  columns,
  teamName,
  selected,
  busy,
  cellVisibility,
  onToggle,
  onOpen,
  onStart,
  onStop,
  onRestart,
  onRemove,
}: {
  agent: Agent;
  columns: ListColumn[];
  teamName: string | undefined;
  selected: boolean;
  busy: boolean;
  cellVisibility: (c: ListColumn) => string;
  onToggle: () => void;
  onOpen: () => void;
  onStart: () => void;
  onStop: () => void;
  onRestart: () => void;
  onRemove: () => void;
}) {
  const status = agentStatusKey(agent);
  const active = ACTIVE_STATES.includes(agent.state);
  const stop = (e: React.SyntheticEvent) => e.stopPropagation();
  const cellBase = "h-11 border-b border-foreground/[0.04] px-3";

  const renderCell = (c: ListColumn) => {
    switch (c) {
      case "name":
        return (
          <div className="flex min-w-0 items-center gap-2.5">
            <AgentAvatar config={agent.config} active={active} size="sm" className="h-7 w-7" />
            <Link
              href={`/agents/${agent.id}`}
              onClick={stop}
              data-testid="agent-name"
              className="truncate font-medium hover:underline"
            >
              {agent.name}
            </Link>
          </div>
        );
      case "status":
        return (
          <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
            <span className={cn("h-2 w-2 shrink-0 rounded-full", STATUS_DOT[status])} aria-hidden />
            <span data-testid="agent-status">{STATUS_LABELS[status]}</span>
          </span>
        );
      case "aufgabe": {
        const queued = agent.queue_depth ?? 0;
        return (
          <div className="flex min-w-0 max-w-[18rem] items-center gap-2 text-muted-foreground">
            <span className="truncate" title={agent.current_task || undefined}>
              {agent.current_task ? (agent.current_task.startsWith("chat:") ? "Antwortet im Chat" : agent.current_task) : "–"}
            </span>
            {queued > 0 && (
              <span className="shrink-0 rounded-full bg-foreground/[0.06] px-1.5 text-[10px]" title={`${queued} in Warteschlange`}>
                +{queued}
              </span>
            )}
          </div>
        );
      }
      case "gruppe":
        return <span className="block max-w-[14rem] truncate text-muted-foreground">{groupLabel(agent, teamName) || "–"}</span>;
      case "zuletzt_aktiv":
        return agent.last_active_at ? (
          <span
            data-testid="agent-last-active"
            title={new Date(agent.last_active_at).toLocaleString("de-DE")}
            className="whitespace-nowrap text-muted-foreground"
          >
            {timeAgo(agent.last_active_at)}
          </span>
        ) : (
          <span data-testid="agent-last-active" className="text-muted-foreground/60">
            –
          </span>
        );
      case "kosten":
        return (
          <span
            className={cn("whitespace-nowrap tabular-nums", budgetReached(agent) ? "font-medium text-red-500 dark:text-red-400" : "text-muted-foreground")}
            title={agent.budget_usd ? `Budget ${formatMoney(agent.budget_usd)}` : undefined}
          >
            {formatMoney(agent.monthly_cost_usd)}
          </span>
        );
      case "warnungen": {
        const warnings = agentWarnings(agent);
        return warnings.length > 0 ? (
          <span title={warnings.join(" · ")} aria-label={warnings.join(", ")} className="inline-flex items-center gap-1 text-amber-600 dark:text-amber-400">
            <AlertTriangle className="h-4 w-4" />
            {warnings.length > 1 && <span className="text-[11px]">{warnings.length}</span>}
          </span>
        ) : null;
      }
    }
  };

  const actionBtn = "flex h-7 w-7 items-center justify-center rounded-lg text-muted-foreground transition-colors";

  return (
    <tr
      data-testid="agent-row"
      data-agent-id={agent.id}
      onClick={onOpen}
      className={cn("group cursor-pointer hover:bg-foreground/[0.03]", selected && "bg-primary/[0.04]")}
    >
      <td className={cn(cellBase, "hidden w-10 md:table-cell")} onClick={stop}>
        <input
          type="checkbox"
          data-testid="row-select"
          aria-label={`${agent.name} auswählen`}
          checked={selected}
          onChange={onToggle}
          className="h-4 w-4 cursor-pointer accent-primary"
        />
      </td>
      {columns.map((c) => (
        <td key={c} className={cn(cellBase, cellVisibility(c), c === "name" && "max-w-[16rem]")}>
          {renderCell(c)}
        </td>
      ))}
      <td className={cn(cellBase, "hidden md:table-cell")} onClick={stop}>
        <div className="flex items-center justify-end gap-0.5 can-hover:opacity-0 can-hover:group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
          {busy ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
          ) : (
            <>
              {active ? (
                <button onClick={onStop} title="Anhalten" className={cn(actionBtn, "hover:bg-amber-500/15 hover:text-amber-600")}>
                  <Square className="h-3.5 w-3.5" />
                </button>
              ) : (
                <button onClick={onStart} title="Starten" className={cn(actionBtn, "hover:bg-emerald-500/15 hover:text-emerald-500")}>
                  <Play className="h-3.5 w-3.5" />
                </button>
              )}
              <button onClick={onRestart} title="Neu starten" className={cn(actionBtn, "hover:bg-foreground/[0.06] hover:text-foreground")}>
                <RotateCw className="h-3.5 w-3.5" />
              </button>
              <button onClick={onRemove} title="Löschen" className={cn(actionBtn, "hover:bg-red-500/15 hover:text-red-400")}>
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </>
          )}
        </div>
      </td>
    </tr>
  );
}

/** Menü „Spalten anordnen“: Reihenfolge per Pfeil, Zurücksetzen auf den Standard. */
function ColumnOrderMenu({ columns, onChange }: { columns: ListColumn[]; onChange: (c: ListColumn[]) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  const move = (index: number, by: -1 | 1) => {
    const target = index + by;
    if (target < 0 || target >= columns.length) return;
    const next = [...columns];
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  };

  return (
    <div ref={ref} className="relative hidden md:block">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        data-testid="columns-menu"
        className="inline-flex items-center gap-1.5 rounded-lg border border-foreground/[0.06] bg-card/50 px-2.5 py-2 text-xs text-muted-foreground hover:text-foreground"
      >
        <Columns3 className="h-3.5 w-3.5" />
        Spalten anordnen
      </button>
      {open && (
        <div className="absolute right-0 z-20 mt-1 w-56 rounded-xl border border-foreground/[0.08] bg-card p-1.5 shadow-lg">
          <ul>
            {columns.map((c, i) => (
              <li key={c} data-testid={`column-order-${c}`} className="flex items-center gap-1 rounded-lg px-2 py-1 text-sm hover:bg-foreground/[0.04]">
                <span className="flex-1">{COLUMN_LABELS[c]}</span>
                <button
                  onClick={() => move(i, -1)}
                  disabled={i === 0}
                  title="Nach vorn"
                  aria-label={`${COLUMN_LABELS[c]} nach vorn`}
                  className="rounded p-1 text-muted-foreground hover:text-foreground disabled:opacity-30"
                >
                  <ArrowUp className="h-3.5 w-3.5" />
                </button>
                <button
                  onClick={() => move(i, 1)}
                  disabled={i === columns.length - 1}
                  title="Nach hinten"
                  aria-label={`${COLUMN_LABELS[c]} nach hinten`}
                  className="rounded p-1 text-muted-foreground hover:text-foreground disabled:opacity-30"
                >
                  <ArrowDown className="h-3.5 w-3.5" />
                </button>
              </li>
            ))}
          </ul>
          <button
            onClick={() => onChange([...LIST_COLUMNS])}
            className="mt-1 w-full rounded-lg px-2 py-1.5 text-left text-xs text-muted-foreground hover:bg-foreground/[0.04] hover:text-foreground"
          >
            Standard-Reihenfolge
          </button>
        </div>
      )}
    </div>
  );
}
