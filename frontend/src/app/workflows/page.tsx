"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Workflow as WorkflowIcon, Plus, Loader2, Trash2, Folder, FolderPlus, Users2, Share2, X, Inbox,
} from "lucide-react";
import { Header } from "@/components/layout/header";
import { cn } from "@/lib/utils";
import { useConfirm, useToast } from "@/components/ui/dialog-provider";
import * as api from "@/lib/api";

const ROLE_LABEL: Record<string, string> = { owner: "Eigentümer", editor: "Bearbeiter", viewer: "Ansehen" };

export default function WorkflowsPage() {
  const router = useRouter();
  const toast = useToast();
  const confirm = useConfirm();
  const [workflows, setWorkflows] = useState<api.Workflow[]>([]);
  const [folders, setFolders] = useState<api.WorkflowFolder[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [sel, setSel] = useState<string>("all"); // all | none | shared | <folderId>
  const [shareFor, setShareFor] = useState<api.Workflow | null>(null);

  const load = useCallback(async () => {
    try {
      const [w, f] = await Promise.all([api.getWorkflows(), api.getWorkflowFolders()]);
      setWorkflows(w.workflows);
      setFolders(f.folders);
    } catch {
      toast.error("Konnte Workflows/Ordner nicht laden.");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { load(); }, [load]);

  const createNew = async () => {
    setCreating(true);
    try {
      const wf = await api.createWorkflow({
        name: "Neuer Workflow",
        folder_id: /^wff_/.test(sel) ? sel : null,
        definition: { start: "s1", steps: { s1: { type: "agent_task", title: "Schritt 1", prompt: "", next: null, _pos: { x: 240, y: 120 } } } },
      });
      router.push(`/workflows/${wf.id}`);
    } catch { toast.error("Workflow konnte nicht erstellt werden."); setCreating(false); }
  };

  const newFolder = async () => {
    const name = window.prompt("Name des Ordners?");
    if (!name?.trim()) return;
    try {
      const f = await api.createWorkflowFolder(name.trim());
      setFolders((fs) => [...fs, f]);
    } catch { toast.error("Ordner konnte nicht erstellt werden."); }
  };

  const removeFolder = async (id: string, name: string) => {
    if (!(await confirm({ title: "Ordner löschen?", message: `„${name}" wird gelöscht. Die Workflows darin bleiben erhalten (ohne Ordner).`, confirmLabel: "Löschen", variant: "destructive" }))) return;
    try {
      await api.deleteWorkflowFolder(id);
      setFolders((fs) => fs.filter((f) => f.id !== id));
      setWorkflows((ws) => ws.map((w) => w.folder_id === id ? { ...w, folder_id: null } : w));
      if (sel === id) setSel("all");
    } catch { toast.error("Löschen fehlgeschlagen."); }
  };

  const removeWorkflow = async (wf: api.Workflow) => {
    if (!(await confirm({ title: "Workflow löschen?", message: `„${wf.name}" wird dauerhaft gelöscht.`, confirmLabel: "Löschen", variant: "destructive" }))) return;
    try {
      await api.deleteWorkflow(wf.id);
      setWorkflows((w) => w.filter((x) => x.id !== wf.id));
    } catch { toast.error("Löschen fehlgeschlagen (nur der Eigentümer kann löschen)."); }
  };

  const moveToFolder = async (wf: api.Workflow, folderId: string | null) => {
    try {
      await api.updateWorkflow(wf.id, { name: wf.name, definition: wf.definition, enabled: wf.enabled, folder_id: folderId });
      setWorkflows((ws) => ws.map((w) => w.id === wf.id ? { ...w, folder_id: folderId } : w));
    } catch { toast.error("Verschieben fehlgeschlagen."); }
  };

  const filtered = useMemo(() => workflows.filter((w) => {
    if (sel === "all") return true;
    if (sel === "none") return !w.folder_id && (w.role ?? "owner") === "owner";
    if (sel === "shared") return (w.role ?? "owner") !== "owner";
    return w.folder_id === sel;
  }), [workflows, sel]);

  const sharedCount = workflows.filter((w) => (w.role ?? "owner") !== "owner").length;

  return (
    <div>
      <Header
        title="Workflows"
        subtitle="Mehrstufige Agenten-Abläufe visuell bauen, organisieren und teilen"
        actions={
          <button onClick={createNew} disabled={creating} className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors">
            {creating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />} Neuer Workflow
          </button>
        }
      />

      <div className="flex gap-6 px-8 py-8">
        {/* Folder rail */}
        <aside className="w-56 shrink-0 space-y-1">
          <RailItem active={sel === "all"} icon={<WorkflowIcon className="h-4 w-4" />} label="Alle" count={workflows.length} onClick={() => setSel("all")} />
          <RailItem active={sel === "none"} icon={<Inbox className="h-4 w-4" />} label="Ohne Ordner" onClick={() => setSel("none")} />
          {sharedCount > 0 && <RailItem active={sel === "shared"} icon={<Users2 className="h-4 w-4" />} label="Mit mir geteilt" count={sharedCount} onClick={() => setSel("shared")} />}
          <div className="flex items-center justify-between px-2 pb-1 pt-4 text-[11px] uppercase tracking-wide text-muted-foreground/50">
            Ordner
            <button onClick={newFolder} className="text-muted-foreground/50 hover:text-foreground" title="Ordner erstellen"><FolderPlus className="h-3.5 w-3.5" /></button>
          </div>
          {folders.map((f) => (
            <div key={f.id} className="group flex items-center">
              <RailItem active={sel === f.id} icon={<Folder className={cn("h-4 w-4", f.shared && "text-sky-400")} />} label={f.name} count={workflows.filter((w) => w.folder_id === f.id).length} onClick={() => setSel(f.id)} />
              {!f.shared && (
                <button onClick={() => removeFolder(f.id, f.name)} className="ml-1 shrink-0 text-muted-foreground/0 group-hover:text-muted-foreground/40 hover:!text-red-400"><Trash2 className="h-3.5 w-3.5" /></button>
              )}
            </div>
          ))}
        </aside>

        {/* Workflow grid */}
        <div className="min-w-0 flex-1">
          {loading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /> Lade…</div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-foreground/[0.1] py-20 text-center">
              <WorkflowIcon className="h-8 w-8 text-muted-foreground/40" />
              <p className="text-sm text-muted-foreground">Keine Workflows hier.</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {filtered.map((wf) => {
                const stepCount = Object.keys(wf.definition?.steps ?? {}).length;
                const isOwner = (wf.role ?? "owner") === "owner";
                return (
                  <div key={wf.id} className="group rounded-2xl border border-foreground/[0.06] bg-card/80 p-4 hover:border-primary/30 transition-colors">
                    <div className="flex items-start justify-between">
                      <div onClick={() => router.push(`/workflows/${wf.id}`)} className="flex min-w-0 flex-1 cursor-pointer items-center gap-2.5">
                        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-primary/10"><WorkflowIcon className="h-4.5 w-4.5 text-primary" /></div>
                        <div className="min-w-0">
                          <p className="truncate font-medium">{wf.name}</p>
                          <p className="text-[11px] text-muted-foreground/60">
                            {stepCount} Schritt{stepCount === 1 ? "" : "e"}
                            {!isOwner && <span className="ml-1 rounded bg-sky-500/10 px-1 text-sky-400">{ROLE_LABEL[wf.role ?? "viewer"]}</span>}
                          </p>
                        </div>
                      </div>
                      <div className="flex items-center gap-1">
                        {isOwner && <button onClick={() => setShareFor(wf)} title="Teilen" className="text-muted-foreground/30 hover:text-primary"><Share2 className="h-4 w-4" /></button>}
                        {isOwner && <button onClick={() => removeWorkflow(wf)} title="Löschen" className="text-muted-foreground/30 hover:text-red-400"><Trash2 className="h-4 w-4" /></button>}
                      </div>
                    </div>
                    {isOwner && folders.filter((f) => !f.shared).length > 0 && (
                      <select
                        value={wf.folder_id ?? ""}
                        onClick={(e) => e.stopPropagation()}
                        onChange={(e) => moveToFolder(wf, e.target.value || null)}
                        className="mt-3 w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-2 py-1 text-[11px] text-muted-foreground/80 outline-none focus:border-primary/30"
                      >
                        <option value="">— kein Ordner —</option>
                        {folders.filter((f) => !f.shared).map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
                      </select>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {shareFor && <ShareDialog workflow={shareFor} onClose={() => setShareFor(null)} />}
    </div>
  );
}

function RailItem({ active, icon, label, count, onClick }: { active: boolean; icon: React.ReactNode; label: string; count?: number; onClick: () => void }) {
  return (
    <button onClick={onClick} className={cn("flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] transition-colors", active ? "bg-primary/10 text-foreground" : "text-muted-foreground hover:bg-foreground/[0.04]")}>
      <span className={cn(active ? "text-primary" : "text-muted-foreground/60")}>{icon}</span>
      <span className="flex-1 truncate text-left">{label}</span>
      {count != null && <span className="text-[11px] text-muted-foreground/40">{count}</span>}
    </button>
  );
}

function ShareDialog({ workflow, onClose }: { workflow: api.Workflow; onClose: () => void }) {
  const toast = useToast();
  const [dir, setDir] = useState<api.DirectoryUser[]>([]);
  const [shares, setShares] = useState<api.WorkflowShare[]>([]);
  const [userId, setUserId] = useState("");
  const [role, setRole] = useState("viewer");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.getWorkflowDirectory().then((d) => setDir(d.users)).catch(() => {});
    api.getWorkflowShares(workflow.id).then((s) => setShares(s.shares)).catch(() => {});
  }, [workflow.id]);

  const add = async () => {
    if (!userId) return;
    setBusy(true);
    try {
      const s = await api.shareWorkflow(workflow.id, userId, role);
      const name = dir.find((u) => u.id === userId)?.name ?? null;
      setShares((prev) => [...prev.filter((x) => x.user_id !== userId), { ...s, user_name: name }]);
      setUserId("");
    } catch { toast.error("Freigabe fehlgeschlagen."); } finally { setBusy(false); }
  };

  const revoke = async (id: string) => {
    try { await api.revokeWorkflowShare(id); setShares((p) => p.filter((s) => s.id !== id)); }
    catch { toast.error("Entfernen fehlgeschlagen."); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} className="w-full max-w-md rounded-2xl border border-foreground/[0.08] bg-card p-5 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="flex items-center gap-2 text-sm font-semibold"><Share2 className="h-4 w-4 text-primary" /> „{workflow.name}" teilen</h3>
          <button onClick={onClose} className="text-muted-foreground/50 hover:text-foreground"><X className="h-4 w-4" /></button>
        </div>

        <div className="flex items-center gap-2">
          <select value={userId} onChange={(e) => setUserId(e.target.value)} className="min-w-0 flex-1 rounded-lg border border-foreground/[0.1] bg-foreground/[0.03] px-2.5 py-1.5 text-[13px] outline-none focus:border-primary/40">
            <option value="">Person wählen…</option>
            {dir.filter((u) => !shares.some((s) => s.user_id === u.id)).map((u) => <option key={u.id} value={u.id}>{u.name || u.email}</option>)}
          </select>
          <select value={role} onChange={(e) => setRole(e.target.value)} className="rounded-lg border border-foreground/[0.1] bg-foreground/[0.03] px-2 py-1.5 text-[13px] outline-none focus:border-primary/40">
            <option value="viewer">Ansehen</option>
            <option value="editor">Bearbeiten</option>
          </select>
          <button onClick={add} disabled={busy || !userId} className="rounded-lg bg-primary px-3 py-1.5 text-[13px] font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50">Teilen</button>
        </div>

        <div className="mt-4 space-y-1.5">
          {shares.length === 0 ? (
            <p className="text-[12px] text-muted-foreground/50">Noch mit niemandem geteilt.</p>
          ) : shares.map((s) => (
            <div key={s.id} className="flex items-center gap-2 rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] px-2.5 py-1.5 text-[13px]">
              <span className="flex-1 truncate">{s.user_name || s.user_id}</span>
              <span className="text-[11px] text-muted-foreground/60">{ROLE_LABEL[s.role] ?? s.role}</span>
              <button onClick={() => revoke(s.id)} className="text-muted-foreground/30 hover:text-red-400"><X className="h-3.5 w-3.5" /></button>
            </div>
          ))}
        </div>
        <p className="mt-3 text-[11px] text-muted-foreground/40">Freigabe an ganze Team-Gruppen folgt separat. Ordner-Freigabe teilt alle enthaltenen Workflows.</p>
      </div>
    </div>
  );
}
