"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, Inbox, Loader2, Trash2, FolderInput, Link2 } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  getAllKnowledgeEntries,
  updateKnowledgeEntry,
  deleteKnowledgeEntry,
} from "@/lib/api";
import type { KnowledgeEntry } from "@/lib/types";
import { useConfirm, useToast } from "@/components/ui/dialog-provider";

/**
 * Capture-Inbox (#385) — was automatisch hereingekommen ist, aber noch niemand
 * angesehen hat.
 *
 * Bewusst ohne eigene Endpunkte: Ein aufgenommener Eintrag ist ein ganz normaler
 * Wissenseintrag mit den Merkmalen `capture` und `unread`. Die Inbox ist deshalb nur
 * eine gefilterte Liste über die vorhandenen Wissens-Aufrufe, und „behalten" heißt
 * schlicht, `unread` zu entfernen. Damit gibt es keinen zweiten Zustand, der mit dem
 * Eintrag auseinanderlaufen könnte.
 */
const UNREAD = "unread";
const CAPTURE = "capture";

export function CaptureInbox({ onOpenEntry }: { onOpenEntry?: (id: number) => void }) {
  const [items, setItems] = useState<KnowledgeEntry[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const confirm = useConfirm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { entries } = await getAllKnowledgeEntries(undefined, CAPTURE);
      setItems(entries.filter((e) => (e.tags || []).includes(UNREAD)));
      setSelected(new Set());
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const targets = (id?: number) => (id !== undefined ? [id] : [...selected]);

  const keep = async (id?: number) => {
    const ids = targets(id);
    if (!ids.length) return;
    setBusy(true);
    try {
      for (const entryId of ids) {
        const entry = items.find((e) => e.id === entryId);
        if (!entry) continue;
        await updateKnowledgeEntry(entryId, {
          tags: (entry.tags || []).filter((t) => t !== UNREAD),
        });
      }
      toast.success(ids.length === 1 ? "Behalten" : `${ids.length} behalten`);
      await load();
    } catch (e) {
      toast.error("Fehlgeschlagen", e instanceof Error ? e.message : undefined);
    } finally {
      setBusy(false);
    }
  };

  const moveToProject = async (id: number) => {
    const entry = items.find((e) => e.id === id);
    if (!entry) return;
    // Ein Projekt ist hier ein Tag — dasselbe Mittel, mit dem die Wissensbasis
    // ohnehin sortiert. Kein zweiter Ordnungsbegriff daneben.
    const project = window.prompt("In welches Projekt? (Tag-Name)");
    if (!project?.trim()) return;
    setBusy(true);
    try {
      await updateKnowledgeEntry(id, {
        tags: [
          ...(entry.tags || []).filter((t) => t !== UNREAD),
          project.trim().replace(/^#/, ""),
        ],
      });
      toast.success(`Nach „${project.trim()}" verschoben`);
      await load();
    } catch (e) {
      toast.error("Fehlgeschlagen", e instanceof Error ? e.message : undefined);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id?: number) => {
    const ids = targets(id);
    if (!ids.length) return;
    const ok = await confirm({
      title: ids.length === 1 ? "Eintrag löschen?" : `${ids.length} Einträge löschen?`,
      message: "Das lässt sich nicht rückgängig machen.",
      variant: "destructive",
      confirmLabel: "Löschen",
    });
    if (!ok) return;
    setBusy(true);
    try {
      for (const entryId of ids) await deleteKnowledgeEntry(entryId);
      toast.success(ids.length === 1 ? "Gelöscht" : `${ids.length} gelöscht`);
      await load();
    } catch (e) {
      toast.error("Fehlgeschlagen", e instanceof Error ? e.message : undefined);
    } finally {
      setBusy(false);
    }
  };

  const toggle = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-[12px] text-muted-foreground">
          Links, längere Textblöcke und ausdrücklich gemerkte Nachrichten landen hier
          automatisch — bis du entscheidest, was damit passiert.
        </p>
        {selected.size > 0 && (
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-muted-foreground">{selected.size} ausgewählt</span>
            <button
              onClick={() => keep()}
              disabled={busy}
              className="flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-[12px] font-medium text-primary-foreground disabled:opacity-40"
            >
              <Check className="h-3.5 w-3.5" />
              Behalten
            </button>
            <button
              onClick={() => remove()}
              disabled={busy}
              className="flex items-center gap-1.5 rounded-lg border border-red-500/30 px-3 py-1.5 text-[12px] font-medium text-red-500 disabled:opacity-40"
            >
              <Trash2 className="h-3.5 w-3.5" />
              Löschen
            </button>
          </div>
        )}
      </div>

      {loading ? (
        <div className="flex flex-1 items-center justify-center">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : items.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 text-center">
          <Inbox className="h-6 w-6 text-muted-foreground/40" />
          <p className="text-sm text-muted-foreground">Nichts Ungesehenes.</p>
        </div>
      ) : (
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
          {items.map((e) => {
            const isSelected = selected.has(e.id);
            const isLink = /^https?:\/\//i.test(e.title);
            return (
              <div
                key={e.id}
                className={cn(
                  "flex items-start gap-3 rounded-xl border p-4 transition-colors",
                  isSelected
                    ? "border-primary/40 bg-primary/[0.06]"
                    : "border-foreground/[0.06] bg-card/80 hover:bg-foreground/[0.03]"
                )}
              >
                <input
                  type="checkbox"
                  checked={isSelected}
                  onChange={() => toggle(e.id)}
                  className="mt-1 h-3.5 w-3.5 shrink-0 accent-current"
                />
                <div
                  className="min-w-0 flex-1 cursor-pointer"
                  onClick={() => onOpenEntry?.(e.id)}
                >
                  <div className="flex items-center gap-1.5">
                    {isLink && <Link2 className="h-3.5 w-3.5 shrink-0 text-primary" />}
                    <h3 className="truncate text-sm font-medium">{e.title}</h3>
                  </div>
                  <p className="mt-0.5 line-clamp-2 text-[12px] text-muted-foreground">
                    {e.content}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    onClick={() => keep(e.id)}
                    disabled={busy}
                    title="Behalten"
                    className="rounded-lg p-2 text-muted-foreground hover:bg-foreground/[0.06] hover:text-emerald-500 disabled:opacity-40"
                  >
                    <Check className="h-4 w-4" />
                  </button>
                  <button
                    onClick={() => moveToProject(e.id)}
                    disabled={busy}
                    title="In Projekt verschieben"
                    className="rounded-lg p-2 text-muted-foreground hover:bg-foreground/[0.06] hover:text-foreground disabled:opacity-40"
                  >
                    <FolderInput className="h-4 w-4" />
                  </button>
                  <button
                    onClick={() => remove(e.id)}
                    disabled={busy}
                    title="Löschen"
                    className="rounded-lg p-2 text-muted-foreground hover:bg-foreground/[0.06] hover:text-red-500 disabled:opacity-40"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
