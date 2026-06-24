"use client";

import { useState, useEffect, useCallback } from "react";
import { X, FileText, Folder, Save, Trash2, Plus, Loader2, Eye, Pencil } from "lucide-react";
import ReactMarkdown from "react-markdown";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import type { BrainFileEntry } from "@/lib/api";
import type { SecondBrain } from "@/lib/types";

export function BrainBrowser({ brain, onClose }: { brain: SecondBrain; onClose: () => void }) {
  const [entries, setEntries] = useState<BrainFileEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [preview, setPreview] = useState(true);

  const readOnly = brain.default_mode === "ro";

  // Open a [[wikilink]] by matching a file whose name (minus .md) equals the title.
  const openWiki = (title: string) => {
    const t = title.trim().toLowerCase();
    const match = entries.find(
      (e) => e.type === "file" &&
        (e.name.toLowerCase() === `${t}.md` ||
         e.name.toLowerCase().replace(/\.md$/, "") === t ||
         e.path.toLowerCase().replace(/\.md$/, "").split("/").pop() === t),
    );
    if (match) openFile(match.path);
  };
  // Convert [[Title]] to a clickable link the renderer can route.
  const renderable = (md: string) => md.replace(/\[\[([^\]]+)\]\]/g, (_m, t) => `[${t}](#wiki:${encodeURIComponent(t)})`);

  const loadTree = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.getBrainTree(brain.id);
      setEntries(r.entries);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Konnte Inhalt nicht laden");
    } finally {
      setLoading(false);
    }
  }, [brain.id]);

  useEffect(() => { loadTree(); }, [loadTree]);

  const openFile = async (path: string) => {
    if (dirty && !window.confirm("Ungespeicherte Änderungen verwerfen?")) return;
    try {
      const r = await api.getBrainFile(brain.id, path);
      setSelected(path);
      setContent(r.content);
      setDirty(false);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Datei konnte nicht geladen werden");
    }
  };

  const save = async () => {
    if (!selected) return;
    setSaving(true);
    try {
      await api.saveBrainFile(brain.id, selected, content);
      setDirty(false);
      await loadTree();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Speichern fehlgeschlagen");
    } finally {
      setSaving(false);
    }
  };

  const newFile = () => {
    const path = window.prompt("Pfad der neuen Datei (z.B. Drucker/x17137.md):");
    if (!path) return;
    const base = path.split("/").pop()?.replace(/\.md$/i, "") ?? "Neu";
    setSelected(path);
    setContent(`# ${base}\n\n`);
    setDirty(true);
    setErr(null);
  };

  const del = async () => {
    if (!selected || !window.confirm(`"${selected}" wirklich löschen?`)) return;
    try {
      await api.deleteBrainFile(brain.id, selected);
      setSelected(null);
      setContent("");
      setDirty(false);
      await loadTree();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Löschen fehlgeschlagen");
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6" onClick={onClose}>
      <div
        className="flex h-[80vh] w-full max-w-5xl flex-col overflow-hidden rounded-xl border border-foreground/[0.08] bg-card shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-foreground/[0.08] px-5 py-3">
          <div className="min-w-0 text-sm font-semibold truncate">
            {brain.name}
            <span className="ml-2 text-xs font-normal text-muted-foreground/50">{brain.container_path}</span>
            {readOnly && <span className="ml-2 text-[10px] text-amber-400">read-only</span>}
          </div>
          <div className="flex items-center gap-2">
            {!readOnly && (
              <button onClick={newFile} className="inline-flex items-center gap-1 rounded-lg bg-foreground/[0.06] px-2.5 py-1.5 text-xs hover:bg-foreground/[0.1]">
                <Plus className="h-3.5 w-3.5" /> Neue Datei
              </button>
            )}
            <button onClick={onClose} className="text-muted-foreground hover:text-foreground"><X className="h-4 w-4" /></button>
          </div>
        </div>

        <div className="flex flex-1 overflow-hidden">
          <div className="w-64 shrink-0 overflow-y-auto border-r border-foreground/[0.08] p-2">
            {loading ? (
              <div className="flex justify-center py-10"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>
            ) : entries.length === 0 ? (
              <span className="block px-2 py-2 text-xs text-muted-foreground/50">Vault ist leer</span>
            ) : (
              entries.map((e) => (
                <button
                  key={e.path}
                  disabled={e.type === "dir"}
                  onClick={() => e.type === "file" && openFile(e.path)}
                  style={{ paddingLeft: `${(e.path.split("/").length - 1) * 12 + 8}px` }}
                  className={cn(
                    "flex w-full items-center gap-1.5 rounded px-2 py-1 text-left text-xs",
                    e.type === "dir" ? "text-muted-foreground/60" : "hover:bg-foreground/[0.05]",
                    selected === e.path && "bg-primary/10 text-primary",
                  )}
                >
                  {e.type === "dir" ? <Folder className="h-3.5 w-3.5 shrink-0" /> : <FileText className="h-3.5 w-3.5 shrink-0" />}
                  <span className="truncate">{e.name}</span>
                </button>
              ))
            )}
          </div>

          <div className="flex flex-1 flex-col overflow-hidden">
            {selected ? (
              <>
                <div className="flex items-center justify-between border-b border-foreground/[0.08] px-3 py-2">
                  <span className="truncate font-mono text-xs text-muted-foreground">{selected}{dirty && " •"}</span>
                  <div className="flex shrink-0 items-center gap-2">
                    <button onClick={() => setPreview((p) => !p)} title={preview ? "Bearbeiten" : "Vorschau"}
                      className="inline-flex items-center gap-1 rounded-lg bg-foreground/[0.06] px-2.5 py-1.5 text-xs hover:bg-foreground/[0.1]">
                      {preview ? <Pencil className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                      {preview ? "Bearbeiten" : "Vorschau"}
                    </button>
                    {!readOnly && !preview && (
                      <>
                        <button onClick={save} disabled={saving || !dirty}
                          className="inline-flex items-center gap-1 rounded-lg bg-primary px-2.5 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-40">
                          {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />} Speichern
                        </button>
                        <button onClick={del} className="rounded-lg p-1.5 text-muted-foreground hover:text-red-400 hover:bg-red-500/[0.06]">
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </>
                    )}
                  </div>
                </div>
                {preview ? (
                  <div className="prose prose-invert prose-sm max-w-none flex-1 overflow-auto p-4 text-sm">
                    <ReactMarkdown
                      components={{
                        a: ({ href, children }) => {
                          if (href?.startsWith("#wiki:")) {
                            return (
                              <a className="cursor-pointer text-primary underline decoration-dotted"
                                onClick={(e) => { e.preventDefault(); openWiki(decodeURIComponent(href.slice(6))); }}>
                                {children}
                              </a>
                            );
                          }
                          return <a href={href} target="_blank" rel="noreferrer" className="text-primary underline">{children}</a>;
                        },
                      }}
                    >
                      {renderable(content)}
                    </ReactMarkdown>
                  </div>
                ) : (
                  <textarea
                    value={content}
                    readOnly={readOnly}
                    onChange={(e) => { setContent(e.target.value); setDirty(true); }}
                    spellCheck={false}
                    className="flex-1 resize-none bg-transparent p-3 font-mono text-xs leading-relaxed outline-none"
                  />
                )}
              </>
            ) : (
              <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
                Datei links auswählen{readOnly ? "" : " oder neu anlegen"}.
              </div>
            )}
          </div>
        </div>

        {err && <div className="border-t border-red-500/20 bg-red-500/10 px-4 py-2 text-xs text-red-400">{err}</div>}
      </div>
    </div>
  );
}
