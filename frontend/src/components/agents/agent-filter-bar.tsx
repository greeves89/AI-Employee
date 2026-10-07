"use client";

import { useState } from "react";
import { Search, Tag, X, ArrowDownAZ, Bookmark, Check } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  MAX_SAVED_FILTERS,
  STATUS_FILTERS,
  STATUS_LABELS,
  type ListGrouping,
  type SavedFilter,
  type StatusFilter,
} from "@/lib/agents-page-prefs";

export type GroupBy = ListGrouping;
export type SortBy = "name" | "state" | "tag";

/**
 * Suchen, filtern, sortieren in der Agentenübersicht (#524).
 *
 * Die Übersicht gruppierte bisher ausschließlich nach Team — und ein Team ist ein
 * **Verhaltens**begriff: es hat eine Leitung, ist Ziel von Delegation und nimmt an
 * Besprechungen teil. Wer nur aufräumen will, handelt sich damit Wirkungen ein, die
 * er nie wollte. Das Schlagwort ist die rein organisatorische Achse daneben.
 *
 * Dazu der Status-Filter (mehrfach wählbar, gilt in Liste und Karten) und
 * gespeicherte Filter: Ist ein Filter aktiv, lässt er sich unter einem Namen am
 * Konto ablegen und später per Klick wieder anwenden.
 */
export function AgentFilterBar({
  query,
  onQuery,
  tags,
  tagFilter,
  onTagFilter,
  groupBy,
  onGroupBy,
  sortBy,
  onSortBy,
  showSort = true,
  statusFilter,
  onStatusFilter,
  presets,
  onApplyPreset,
  onSavePreset,
  onDeletePreset,
  shown,
  total,
}: {
  query: string;
  onQuery: (v: string) => void;
  tags: string[];
  tagFilter: string | null;
  onTagFilter: (v: string | null) => void;
  groupBy: GroupBy;
  onGroupBy: (v: GroupBy) => void;
  sortBy: SortBy;
  onSortBy: (v: SortBy) => void;
  /** Die Liste sortiert über ihre Spaltenköpfe — dort ist die Auswahl überflüssig. */
  showSort?: boolean;
  statusFilter: StatusFilter[];
  onStatusFilter: (v: StatusFilter[]) => void;
  presets: SavedFilter[];
  onApplyPreset: (f: SavedFilter) => void;
  onSavePreset: (name: string) => void;
  onDeletePreset: (id: string) => void;
  shown: number;
  total: number;
}) {
  const [naming, setNaming] = useState(false);
  const [presetName, setPresetName] = useState("");
  const filterActive = query.trim() !== "" || tagFilter !== null || statusFilter.length > 0;
  const presetsFull = presets.length >= MAX_SAVED_FILTERS;

  const toggleStatus = (s: StatusFilter) =>
    onStatusFilter(statusFilter.includes(s) ? statusFilter.filter((x) => x !== s) : [...statusFilter, s]);

  const savePreset = () => {
    const name = presetName.trim();
    if (!name || presetsFull) return;
    onSavePreset(name);
    setPresetName("");
    setNaming(false);
  };

  return (
    <div className="mb-5 space-y-2">
    <div className="flex flex-wrap items-center gap-2">
      <div className="relative min-w-[13rem] flex-1 sm:max-w-xs">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground/50" />
        <input
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          placeholder="Agent suchen — Name, Rolle, Schlagwort"
          className="w-full rounded-lg border border-foreground/[0.06] bg-card/50 py-2 pl-9 pr-8 text-sm outline-none transition-all focus:border-primary/40"
        />
        {query && (
          <button
            onClick={() => onQuery("")}
            title="Suche leeren"
            className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground/60 hover:text-foreground"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {tags.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <Tag className="h-3.5 w-3.5 text-muted-foreground/50" />
          {tags.map((t) => (
            <button
              key={t}
              // Nochmal auf dasselbe Schlagwort hebt den Filter auf — sonst gibt es
              // keinen Weg zurück ausser einem zusaetzlichen „alle"-Knopf.
              onClick={() => onTagFilter(tagFilter === t ? null : t)}
              className={cn(
                "rounded-full border px-2.5 py-1 text-[11px] transition-colors",
                tagFilter === t
                  ? "border-primary/40 bg-primary/10 text-primary"
                  : "border-foreground/[0.08] text-muted-foreground hover:bg-foreground/[0.05]",
              )}
            >
              {t}
            </button>
          ))}
        </div>
      )}

      <div className="ml-auto flex items-center gap-2">
        {shown !== total && (
          <span className="text-[11px] text-muted-foreground/60">
            {shown} von {total}
          </span>
        )}
        <div className="flex items-center rounded-lg border border-foreground/[0.06] bg-card/50 p-0.5">
          {(["team", "tag", "none"] as GroupBy[]).map((g) => (
            <button
              key={g}
              onClick={() => onGroupBy(g)}
              className={cn(
                "rounded-md px-2.5 py-1.5 text-[11px] text-muted-foreground transition-all hover:text-foreground",
                groupBy === g && "bg-foreground/[0.08] text-foreground",
              )}
              title={g === "team" ? "Nach Team gruppieren" : g === "tag" ? "Nach Schlagwort gruppieren" : "Nicht gruppieren"}
            >
              {g === "team" ? "Team" : g === "tag" ? "Schlagwort" : "Keine"}
            </button>
          ))}
        </div>
        {showSort && (
        <div className="flex items-center gap-1.5 rounded-lg border border-foreground/[0.06] bg-card/50 px-2 py-1.5">
          <ArrowDownAZ className="h-3.5 w-3.5 text-muted-foreground/50" />
          <select
            value={sortBy}
            onChange={(e) => onSortBy(e.target.value as SortBy)}
            className="bg-transparent text-[11px] text-muted-foreground outline-none"
          >
            <option value="name">Name</option>
            <option value="state">Zustand</option>
            <option value="tag">Schlagwort</option>
          </select>
        </div>
        )}
      </div>
    </div>

    <div className="flex flex-wrap items-center gap-1.5">
      {STATUS_FILTERS.map((s) => {
        const on = statusFilter.includes(s);
        return (
          <button
            key={s}
            data-testid={`status-filter-${s}`}
            aria-pressed={on}
            onClick={() => toggleStatus(s)}
            className={cn(
              "rounded-full border px-2.5 py-1 text-[11px] transition-colors",
              on
                ? "border-primary/40 bg-primary/10 text-primary"
                : "border-foreground/[0.08] text-muted-foreground hover:bg-foreground/[0.05]",
            )}
          >
            {STATUS_LABELS[s]}
          </button>
        );
      })}

      {presets.length > 0 && <span className="mx-1 h-4 w-px bg-foreground/[0.08]" aria-hidden />}
      {presets.map((f) => (
        <span
          key={f.id}
          className="inline-flex items-center rounded-full border border-violet-500/25 bg-violet-500/[0.06] text-[11px] text-violet-700 dark:text-violet-300"
        >
          <button
            data-testid="filter-preset"
            onClick={() => onApplyPreset(f)}
            title="Filter anwenden"
            className="py-1 pl-2.5 pr-1"
          >
            {f.name}
          </button>
          <button
            data-testid="filter-preset-delete"
            onClick={() => onDeletePreset(f.id)}
            title={`Filter „${f.name}“ löschen`}
            aria-label={`Filter „${f.name}“ löschen`}
            className="rounded-full py-1 pl-0.5 pr-2 opacity-60 hover:opacity-100"
          >
            <X className="h-3 w-3" />
          </button>
        </span>
      ))}

      {filterActive && !naming && (
        <button
          data-testid="filter-save"
          onClick={() => setNaming(true)}
          disabled={presetsFull}
          title={presetsFull ? `Höchstens ${MAX_SAVED_FILTERS} gespeicherte Filter` : "Aktuellen Filter unter einem Namen speichern"}
          className="inline-flex items-center gap-1 rounded-full border border-dashed border-foreground/[0.15] px-2.5 py-1 text-[11px] text-muted-foreground hover:text-foreground disabled:opacity-50"
        >
          <Bookmark className="h-3 w-3" />
          Filter speichern
        </button>
      )}
      {naming && (
        <form
          onSubmit={(e) => { e.preventDefault(); savePreset(); }}
          className="inline-flex items-center gap-1"
        >
          <input
            data-testid="filter-name"
            autoFocus
            value={presetName}
            onChange={(e) => setPresetName(e.target.value)}
            onKeyDown={(e) => e.key === "Escape" && setNaming(false)}
            maxLength={60}
            placeholder="Name des Filters"
            className="w-40 rounded-full border border-foreground/[0.1] bg-card/50 px-2.5 py-1 text-[11px] outline-none focus:border-primary/40"
          />
          <button
            type="submit"
            data-testid="filter-save-confirm"
            disabled={!presetName.trim()}
            title="Speichern"
            className="rounded-full p-1 text-primary hover:bg-primary/10 disabled:opacity-40"
          >
            <Check className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => { setNaming(false); setPresetName(""); }}
            title="Abbrechen"
            className="rounded-full p-1 text-muted-foreground hover:text-foreground"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </form>
      )}
    </div>
    </div>
  );
}
