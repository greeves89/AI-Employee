"use client";

import { Search, Tag, X, ArrowDownAZ } from "lucide-react";
import { cn } from "@/lib/utils";

export type GroupBy = "team" | "tag";
export type SortBy = "name" | "state" | "tag";

/**
 * Suchen, filtern, sortieren in der Agentenübersicht (#524).
 *
 * Die Übersicht gruppierte bisher ausschließlich nach Team — und ein Team ist ein
 * **Verhaltens**begriff: es hat eine Leitung, ist Ziel von Delegation und nimmt an
 * Besprechungen teil. Wer nur aufräumen will, handelt sich damit Wirkungen ein, die
 * er nie wollte. Das Schlagwort ist die rein organisatorische Achse daneben.
 *
 * Die Leiste erscheint nur, wenn es etwas zu ordnen gibt — bei drei Agenten ist sie
 * Ballast.
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
  shown: number;
  total: number;
}) {
  return (
    <div className="mb-5 flex flex-wrap items-center gap-2">
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
              // keinen Weg zurueck ausser einem zusaetzlichen „alle"-Knopf.
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
          {(["team", "tag"] as GroupBy[]).map((g) => (
            <button
              key={g}
              onClick={() => onGroupBy(g)}
              className={cn(
                "rounded-md px-2.5 py-1.5 text-[11px] text-muted-foreground transition-all hover:text-foreground",
                groupBy === g && "bg-foreground/[0.08] text-foreground",
              )}
              title={g === "team" ? "Nach Team gruppieren" : "Nach Schlagwort gruppieren"}
            >
              {g === "team" ? "Team" : "Schlagwort"}
            </button>
          ))}
        </div>
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
      </div>
    </div>
  );
}
