"use client";

import { useState } from "react";
import { Loader2, MessageSquare, Pencil, Pin, Plus, X } from "lucide-react";
import { cn, timeAgo } from "@/lib/utils";

/** One conversation entry in the rail. A custom `title` (rename) wins over the
 *  derived `preview`; `fallbackLabel` covers sessions that have neither. */
export interface RailSession {
  id: string;
  title?: string | null;
  preview?: string;
  fallbackLabel?: string;
  pinned?: boolean;
  last_message_at?: string | null;
  message_count?: number;
}

/** Shared left-hand conversation list used by the Chat and Speech tabs.
 *  Select/new/pin/delete are delegated to the owner; the rail only keeps its
 *  inline-rename state. Rename commits on Enter/blur, Escape cancels. */
export function SessionRail({
  sessions,
  selectedId,
  onSelect,
  onNew,
  onPin,
  onRename,
  onDelete,
  loading = false,
  newDisabled = false,
  busyIds,
  className,
}: {
  sessions: RailSession[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onPin: (session: RailSession) => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  loading?: boolean;
  newDisabled?: boolean;
  /** Session ids the agent is actively processing right now → marked orange. */
  busyIds?: string[];
  className?: string;
}) {
  const busy = new Set(busyIds || []);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");

  const startRename = (s: RailSession) => {
    setEditingId(s.id);
    setEditValue(s.title || s.preview || "");
  };

  const saveRename = (id: string) => {
    setEditingId(null);
    onRename(id, editValue.trim());
  };

  return (
    <div className={cn("flex w-56 shrink-0 flex-col", className)}>
      <div className="flex items-center justify-between border-b border-border px-3 py-2.5">
        <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/60">
          Gespräche
        </span>
        <button
          onClick={onNew}
          disabled={newDisabled}
          className="inline-flex items-center justify-center rounded-lg bg-primary p-1.5 text-primary-foreground shadow-sm hover:bg-primary/90 disabled:opacity-40 transition-all"
          title="Neues Gespräch"
        >
          <Plus className="h-3.5 w-3.5" />
        </button>
      </div>
      <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto p-2">
        {loading ? (
          <div className="flex items-center justify-center py-6 text-muted-foreground/50">
            <Loader2 className="h-4 w-4 animate-spin" />
          </div>
        ) : (
          <>
            <button
              onClick={onNew}
              disabled={newDisabled}
              className={cn(
                "flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs transition-colors disabled:opacity-40",
                selectedId === null
                  ? "bg-primary/10 text-foreground"
                  : "text-muted-foreground/70 hover:bg-foreground/[0.04]",
              )}
            >
              <Plus className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate">Neues Gespräch</span>
            </button>
            {sessions.map((s) => {
              const shown = s.title || s.preview || s.fallbackLabel || "Gespräch";
              const editing = editingId === s.id;
              const isBusy = busy.has(s.id);
              return (
                <div
                  key={s.id}
                  onClick={() => { if (!editing) onSelect(s.id); }}
                  className={cn(
                    "group/sess relative flex w-full cursor-pointer flex-col gap-0.5 rounded-lg px-2.5 py-2 text-left transition-colors",
                    isBusy && "ring-1 ring-inset ring-amber-500/40 bg-amber-500/[0.06]",
                    selectedId === s.id
                      ? "bg-primary/10 text-foreground"
                      : "text-muted-foreground/70 hover:bg-foreground/[0.04]",
                  )}
                >
                  <span className="flex items-center gap-1.5 text-xs">
                    {isBusy ? (
                      <Loader2 className="h-3 w-3 shrink-0 animate-spin text-amber-400" />
                    ) : s.pinned ? (
                      <Pin className="h-3 w-3 shrink-0 fill-amber-400/30 text-amber-400" />
                    ) : (
                      <MessageSquare className="h-3 w-3 shrink-0 text-muted-foreground/50" />
                    )}
                    {editing ? (
                      <input
                        autoFocus
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        onClick={(e) => e.stopPropagation()}
                        onBlur={() => saveRename(s.id)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") saveRename(s.id);
                          if (e.key === "Escape") setEditingId(null);
                        }}
                        className="w-full min-w-0 border-b border-primary/60 bg-transparent text-xs outline-none"
                      />
                    ) : (
                      <span className="truncate" title="Doppelklick zum Umbenennen" onDoubleClick={(e) => { e.stopPropagation(); startRename(s); }}>
                        {shown}
                      </span>
                    )}
                  </span>
                  {s.last_message_at && (
                    <span className="pl-4 text-[10px] text-muted-foreground/40">
                      {timeAgo(s.last_message_at)}
                      {s.message_count !== undefined && ` · ${s.message_count}`}
                    </span>
                  )}
                  {!editing && (
                    <span className="absolute right-1.5 top-1.5 hidden items-center gap-0.5 rounded-md border border-border bg-card px-0.5 py-0.5 shadow-sm group-hover/sess:flex">
                      <button
                        onClick={(e) => { e.stopPropagation(); onPin(s); }}
                        className="rounded p-0.5 hover:bg-foreground/[0.1]"
                        title={s.pinned ? "Loslösen" : "Anpinnen"}
                      >
                        <Pin className={cn("h-3 w-3", s.pinned && "text-amber-400")} />
                      </button>
                      <button
                        onClick={(e) => { e.stopPropagation(); startRename(s); }}
                        className="rounded p-0.5 hover:bg-foreground/[0.1]"
                        title="Umbenennen"
                      >
                        <Pencil className="h-3 w-3" />
                      </button>
                      <button
                        onClick={(e) => { e.stopPropagation(); onDelete(s.id); }}
                        className="rounded p-0.5 hover:bg-red-500/15 hover:text-red-400"
                        title="Löschen"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </span>
                  )}
                </div>
              );
            })}
            {sessions.length === 0 && (
              <p className="px-2.5 py-4 text-center text-[11px] text-muted-foreground/40">
                Noch keine Gespräche
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
