"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Workflow as WorkflowIcon, Plus, Loader2, Trash2 } from "lucide-react";
import { Header } from "@/components/layout/header";
import { useConfirm, useToast } from "@/components/ui/dialog-provider";
import * as api from "@/lib/api";

export default function WorkflowsPage() {
  const router = useRouter();
  const toast = useToast();
  const confirm = useConfirm();
  const [workflows, setWorkflows] = useState<api.Workflow[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    try {
      const { workflows } = await api.getWorkflows();
      setWorkflows(workflows);
    } catch {
      toast.error("Workflows konnten nicht geladen werden.");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { load(); }, [load]);

  const createNew = async () => {
    setCreating(true);
    try {
      // A minimal valid workflow: one agent-task start step. Edited on the canvas.
      const wf = await api.createWorkflow({
        name: "Neuer Workflow",
        definition: {
          start: "s1",
          steps: { s1: { type: "agent_task", title: "Schritt 1", prompt: "", next: null, _pos: { x: 240, y: 120 } } },
        },
      });
      router.push(`/workflows/${wf.id}`);
    } catch {
      toast.error("Workflow konnte nicht erstellt werden.");
      setCreating(false);
    }
  };

  const remove = async (id: string, name: string) => {
    if (!(await confirm({ title: "Workflow löschen?", message: `„${name}" wird dauerhaft gelöscht.`, confirmLabel: "Löschen", variant: "destructive" }))) return;
    try {
      await api.deleteWorkflow(id);
      setWorkflows((w) => w.filter((x) => x.id !== id));
    } catch {
      toast.error("Löschen fehlgeschlagen.");
    }
  };

  return (
    <div>
      <Header
        title="Workflows"
        subtitle="Mehrstufige Agenten-Abläufe visuell bauen und ausführen"
        actions={
          <button
            onClick={createNew}
            disabled={creating}
            className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
          >
            {creating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
            Neuer Workflow
          </button>
        }
      />

      <div className="px-8 py-8">
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /> Lade…</div>
        ) : workflows.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-foreground/[0.1] py-20 text-center">
            <WorkflowIcon className="h-8 w-8 text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground">Noch keine Workflows. Baue deinen ersten mehrstufigen Ablauf.</p>
            <button onClick={createNew} disabled={creating} className="mt-1 inline-flex items-center gap-2 rounded-lg bg-foreground/[0.06] px-3 py-1.5 text-sm hover:bg-foreground/[0.1] transition-colors">
              <Plus className="h-4 w-4" /> Workflow erstellen
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {workflows.map((wf) => {
              const stepCount = Object.keys(wf.definition?.steps ?? {}).length;
              return (
                <div
                  key={wf.id}
                  onClick={() => router.push(`/workflows/${wf.id}`)}
                  className="group cursor-pointer rounded-2xl border border-foreground/[0.06] bg-card/80 p-5 hover:border-primary/30 transition-colors"
                >
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-2.5">
                      <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10">
                        <WorkflowIcon className="h-4.5 w-4.5 text-primary" />
                      </div>
                      <div>
                        <p className="font-medium">{wf.name}</p>
                        <p className="text-[11px] text-muted-foreground/60">{stepCount} Schritt{stepCount === 1 ? "" : "e"}{wf.enabled ? "" : " · deaktiviert"}</p>
                      </div>
                    </div>
                    <button
                      onClick={(e) => { e.stopPropagation(); remove(wf.id, wf.name); }}
                      className="text-muted-foreground/30 hover:text-red-400 transition-colors"
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
    </div>
  );
}
