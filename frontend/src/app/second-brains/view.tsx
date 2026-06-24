"use client";

import { useState, useEffect, useCallback } from "react";
import { Brain, Plus, Trash2, Pencil, Check, X, Loader2, Power, FolderOpen } from "lucide-react";
import { Header } from "@/components/layout/header";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import type { SecondBrain } from "@/lib/types";
import { BrainBrowser } from "./brain-browser";

type Standard = "freeform" | "wikimedia" | "it_support";

const STANDARDS: { value: Standard; label: string; hint: string }[] = [
  { value: "it_support", label: "IT-Support / Runbooks", hint: "Ordner Drucker/Netzwerk/… + Symptom→Ursache→Lösung-Vorlage" },
  { value: "wikimedia", label: "Wikimedia-Stil", hint: "Themen-Ordner + index.md + [[wikilinks]]" },
  { value: "freeform", label: "Freiform", hint: "Nur index.md, frei organisierbar" },
];

type FormState = {
  name: string;
  slug: string;
  default_mode: "ro" | "rw";
  standard: Standard;
  description: string;
};

const EMPTY_FORM: FormState = { name: "", slug: "", default_mode: "rw", standard: "it_support", description: "" };

function slugify(raw: string): string {
  return raw.trim().toLowerCase().replace(/\s+/g, "_").replace(/[^a-z0-9_-]/g, "").replace(/^[-_]+|[-_]+$/g, "");
}

function Toast({ type, message, onClose }: { type: "success" | "error"; message: string; onClose: () => void }) {
  useEffect(() => { const t = setTimeout(onClose, 3500); return () => clearTimeout(t); }, [onClose]);
  return (
    <div className={cn(
      "fixed bottom-6 right-6 z-50 flex items-center gap-3 rounded-xl px-4 py-3 text-sm font-medium shadow-xl",
      type === "success"
        ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30"
        : "bg-red-500/20 text-red-300 border border-red-500/30"
    )}>
      {type === "success" ? <Check size={16} /> : <X size={16} />}
      {message}
    </div>
  );
}

export function SecondBrainsView({ embedded = false }: { embedded?: boolean }) {
  const [brains, setBrains] = useState<SecondBrain[]>([]);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState<{ type: "success" | "error"; message: string } | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [slugTouched, setSlugTouched] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState<number | null>(null);
  const [browsing, setBrowsing] = useState<SecondBrain | null>(null);

  const showToast = (type: "success" | "error", message: string) => setToast({ type, message });

  const load = useCallback(async () => {
    try {
      setBrains(await api.listSecondBrains());
    } catch {
      showToast("error", "Konnte Second Brains nicht laden");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const openCreate = () => { setForm(EMPTY_FORM); setSlugTouched(false); setEditingId(null); setShowForm(true); };
  const openEdit = (b: SecondBrain) => {
    setForm({ name: b.name, slug: b.slug, default_mode: b.default_mode, standard: b.standard, description: b.description || "" });
    setSlugTouched(true);
    setEditingId(b.id);
    setShowForm(true);
  };

  const onNameChange = (name: string) => {
    setForm((f) => ({ ...f, name, slug: slugTouched ? f.slug : slugify(name) }));
  };

  const save = async () => {
    const slug = slugify(form.slug || form.name);
    if (!form.name.trim() || !slug) {
      showToast("error", "Name (und ein gültiger Slug) sind Pflicht");
      return;
    }
    setSaving(true);
    try {
      if (editingId) {
        await api.updateSecondBrain(editingId, {
          name: form.name.trim(),
          default_mode: form.default_mode,
          description: form.description.trim() || null,
        });
        showToast("success", "Second Brain aktualisiert");
      } else {
        await api.createSecondBrain({
          name: form.name.trim(),
          slug,
          default_mode: form.default_mode,
          standard: form.standard,
          description: form.description.trim() || null,
        });
        showToast("success", "Second Brain erstellt");
      }
      setShowForm(false);
      await load();
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : "Speichern fehlgeschlagen");
    } finally {
      setSaving(false);
    }
  };

  const toggleActive = async (b: SecondBrain) => {
    try {
      await api.updateSecondBrain(b.id, { is_active: !b.is_active });
      await load();
    } catch {
      showToast("error", "Status konnte nicht geändert werden");
    }
  };

  const remove = async (b: SecondBrain) => {
    setDeleting(b.id);
    try {
      await api.deleteSecondBrain(b.id);
      showToast("success", "Second Brain entfernt (Ordnerdaten bleiben erhalten)");
      await load();
    } catch {
      showToast("error", "Entfernen fehlgeschlagen");
    } finally {
      setDeleting(null);
    }
  };

  const inputCls = "w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm outline-none focus:border-primary/50";

  return (
    <div className={embedded ? "" : "min-h-screen"}>
      {!embedded && <Header title="Second Brains" subtitle="Abteilungsweite, geteilte Wissens-Vaults — einmal anlegen, an Agents hängen" />}
      <div className={embedded ? "mx-auto max-w-4xl" : "mx-auto max-w-4xl px-6 py-8"}>
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-xl font-semibold flex items-center gap-2">
              <Brain className="h-5 w-5 text-primary" /> Second Brains
            </h1>
            <p className="text-sm text-muted-foreground/70 mt-1">
              Geteilte Wissens-Vaults pro Abteilung. Rechte (lesen/schreiben) pro Person setzt du
              unter <b>Users → Mount-Rechte</b>; zuweisen tut der User in den Agent-Einstellungen.
            </p>
          </div>
          <button
            onClick={openCreate}
            className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20"
          >
            <Plus className="h-4 w-4" /> Neues Brain
          </button>
        </div>

        {showForm && (
          <div className="mb-6 rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5 space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold">{editingId ? "Brain bearbeiten" : "Neues Brain"}</h2>
              <button onClick={() => setShowForm(false)} className="text-muted-foreground hover:text-foreground">
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-[11px] font-medium text-muted-foreground/70 mb-1">Name / Abteilung</label>
                <input className={inputCls} value={form.name}
                  onChange={(e) => onNameChange(e.target.value)}
                  placeholder="z.B. IT Operations" />
              </div>
              <div>
                <label className="block text-[11px] font-medium text-muted-foreground/70 mb-1">
                  Slug {editingId && <span className="text-muted-foreground/40">(fix)</span>}
                </label>
                <input className={cn(inputCls, editingId && "opacity-60")} value={form.slug}
                  disabled={!!editingId}
                  onChange={(e) => { setSlugTouched(true); setForm({ ...form, slug: e.target.value }); }}
                  placeholder="it_operations" />
                <p className="text-[10px] text-muted-foreground/40 mt-1">
                  Pfad: <code>/srv/secondbrain/{slugify(form.slug || form.name) || "<slug>"}</code> → <code>/mnt/brains/{slugify(form.slug || form.name) || "<slug>"}</code>
                </p>
              </div>
              <div>
                <label className="block text-[11px] font-medium text-muted-foreground/70 mb-1">Standard-Modus</label>
                <select className={inputCls} value={form.default_mode}
                  onChange={(e) => setForm({ ...form, default_mode: e.target.value as "ro" | "rw" })}>
                  <option value="rw">read-write (Obergrenze)</option>
                  <option value="ro">read-only</option>
                </select>
                <p className="text-[10px] text-muted-foreground/40 mt-1">Pro Person/Rolle weiter einschränkbar (ro gewinnt).</p>
              </div>
              <div>
                <label className="block text-[11px] font-medium text-muted-foreground/70 mb-1">Beschreibung</label>
                <input className={inputCls} value={form.description}
                  onChange={(e) => setForm({ ...form, description: e.target.value })}
                  placeholder="optional" />
              </div>
              <div className="col-span-2">
                <label className="block text-[11px] font-medium text-muted-foreground/70 mb-1">
                  Vault-Standard {editingId && <span className="text-muted-foreground/40">(beim Anlegen festgelegt)</span>}
                </label>
                <select className={cn(inputCls, editingId && "opacity-60")} value={form.standard} disabled={!!editingId}
                  onChange={(e) => setForm({ ...form, standard: e.target.value as Standard })}>
                  {STANDARDS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
                </select>
                <p className="text-[10px] text-muted-foreground/40 mt-1">
                  {STANDARDS.find((s) => s.value === form.standard)?.hint}
                  {!editingId && " — Ordner + CONVENTIONS.md werden beim Speichern angelegt."}
                </p>
              </div>
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <button onClick={() => setShowForm(false)}
                className="rounded-xl px-4 py-2 text-sm text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04]">
                Abbrechen
              </button>
              <button onClick={save} disabled={saving}
                className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50">
                {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />} Speichern
              </button>
            </div>
          </div>
        )}

        {loading ? (
          <div className="flex justify-center py-20"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>
        ) : brains.length === 0 ? (
          <div className="text-center py-16 text-muted-foreground">
            <Brain className="h-8 w-8 mx-auto mb-2 opacity-30" />
            Noch keine Second Brains. Lege das erste an.
          </div>
        ) : (
          <div className="space-y-2">
            {brains.map((b) => (
              <div key={b.id} className={cn(
                "rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-4 flex items-center gap-4",
                !b.is_active && "opacity-50"
              )}>
                <button onClick={() => setBrowsing(b)} title="Inhalt öffnen"
                  className="flex-1 min-w-0 text-left rounded-lg -m-1 p-1 hover:bg-foreground/[0.04]">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium truncate">{b.name}</span>
                    <span className="inline-flex items-center rounded-full border border-sky-500/20 bg-sky-500/10 px-2.5 py-1 text-[11px] font-medium text-sky-400">
                      {b.label}
                    </span>
                    <span className={cn(
                      "inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium",
                      b.default_mode === "rw" ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-400" : "border-amber-500/20 bg-amber-500/10 text-amber-400"
                    )}>
                      {b.default_mode}
                    </span>
                    <span className="inline-flex items-center rounded-full border border-foreground/[0.1] bg-foreground/[0.04] px-2 py-0.5 text-[10px] text-muted-foreground/70">
                      {b.standard}
                    </span>
                  </div>
                  <p className="text-[11px] text-muted-foreground/60 mt-0.5 truncate">
                    {b.container_path}{b.description ? ` — ${b.description}` : ""}
                  </p>
                </button>
                <button onClick={() => setBrowsing(b)} title="Inhalt ansehen/bearbeiten"
                  className="rounded-lg p-2 text-muted-foreground hover:text-primary hover:bg-foreground/[0.06]">
                  <FolderOpen className="h-4 w-4" />
                </button>
                <button onClick={() => toggleActive(b)} title={b.is_active ? "Deaktivieren" : "Aktivieren"}
                  className={cn("rounded-lg p-2 hover:bg-foreground/[0.06]", b.is_active ? "text-emerald-400" : "text-muted-foreground")}>
                  <Power className="h-4 w-4" />
                </button>
                <button onClick={() => openEdit(b)} className="rounded-lg p-2 text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06]">
                  <Pencil className="h-4 w-4" />
                </button>
                <button onClick={() => remove(b)} disabled={deleting === b.id}
                  className="rounded-lg p-2 text-muted-foreground hover:text-red-400 hover:bg-red-500/[0.06]">
                  {deleting === b.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
      {browsing && <BrainBrowser brain={browsing} onClose={() => setBrowsing(null)} />}
      {toast && <Toast type={toast.type} message={toast.message} onClose={() => setToast(null)} />}
    </div>
  );
}
