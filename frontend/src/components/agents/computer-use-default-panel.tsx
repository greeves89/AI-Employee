"use client";

import { useEffect, useState } from "react";
import { Camera, Check, Clipboard, Eye, FolderOpen, Keyboard, Loader2, Monitor, MousePointer2, Save, Terminal } from "lucide-react";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";

// Dauerhafter Pro-Agent-Deckel fuer Computer-Use (Issue #787 Punkt 1) -- vorher
// gab es dafuer KEINEN Wert: jede Desktop-Session startete immer mit demselben
// Plattform-Default, unabhaengig davon, ob der Agent laut Autonomie-Matrix
// ueberhaupt Shell-/System-Aktionen ausfuehren darf. Eine Session kann diesen
// Deckel nur noch UNTERSCHREITEN, nie ueberschreiten (serverseitig erzwungen in
// computer_use.py, nicht nur hier angezeigt).
const LABELS: Record<string, { label: string; icon: React.ElementType }> = {
  screenshots: { label: "Bildschirmfotos", icon: Camera },
  accessibility: { label: "Bedienoberflaeche lesen", icon: Eye },
  mouse: { label: "Maussteuerung", icon: MousePointer2 },
  keyboard: { label: "Tastatureingaben", icon: Keyboard },
  apps: { label: "Programme oeffnen/schliessen", icon: FolderOpen },
  clipboard: { label: "Zwischenablage", icon: Clipboard },
  shell: { label: "Shell-Befehle", icon: Terminal },
  input_capture: { label: "Eigene Eingaben mitschneiden", icon: Keyboard },
  voice_capture: { label: "Sprachmitschnitt", icon: Monitor },
  browser: { label: "Browser-Steuerung", icon: Monitor },
  ego_browser: { label: "Browser-Steuerung (Ego)", icon: Monitor },
};

export function ComputerUseDefaultPanel({ agentId }: { agentId: string }) {
  const [groups, setGroups] = useState<{ id: string; default: boolean }[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.getComputerUseDefault(agentId)
      .then((data) => {
        setGroups(data.computer_use_capability_groups);
        setSelected(data.computer_use_default_capabilities);
      })
      .catch(() => setGroups([]))
      .finally(() => setLoading(false));
  }, [agentId]);

  const toggle = (id: string) => {
    setSaved(false);
    setSelected((prev) => (prev.includes(id) ? prev.filter((c) => c !== id) : [...prev, id]));
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const result = await api.updateComputerUseDefault(agentId, selected);
      setSelected(result.computer_use_default_capabilities);
      setSaved(true);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden">
      <div className="flex items-center justify-between border-b border-foreground/[0.06] px-5 py-3">
        <div className="flex items-center gap-2">
          <Monitor className="h-4 w-4 text-primary" />
          <span className="text-sm font-medium">Computer-Use-Standard</span>
        </div>
        <button
          onClick={handleSave}
          disabled={saving}
          className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-1.5 text-[11px] font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40 transition-all"
        >
          {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
          Speichern
        </button>
      </div>
      <p className="px-5 pt-4 text-[11px] text-muted-foreground">
        Diese Faehigkeiten erhaelt jede Desktop-Sitzung dieses Agenten hoechstens
        — unabhaengig davon, was der Plattform-Standard sonst erlaubt. Eine
        laufende Sitzung kann dies nur noch einschraenken, nie erweitern.
      </p>
      <div className="p-5 space-y-2">
        {groups.map((group) => {
          const meta = LABELS[group.id] || { label: group.id, icon: Monitor };
          const Icon = meta.icon;
          const isSelected = selected.includes(group.id);
          return (
            <button
              key={group.id}
              type="button"
              onClick={() => toggle(group.id)}
              className={cn(
                "w-full flex items-center gap-3 rounded-xl border p-3 text-left transition-all duration-200",
                isSelected
                  ? "border-primary/40 bg-primary/[0.08]"
                  : "border-foreground/[0.06] bg-foreground/[0.02] hover:bg-foreground/[0.04]"
              )}
            >
              <div
                className={cn(
                  "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg transition-colors",
                  isSelected ? "bg-primary/20 text-primary" : "bg-foreground/[0.06] text-muted-foreground"
                )}
              >
                <Icon className="h-4 w-4" />
              </div>
              <span className={cn("flex-1 text-sm font-medium", isSelected ? "text-foreground" : "text-muted-foreground")}>
                {meta.label}
              </span>
              <div
                className={cn(
                  "flex h-5 w-5 shrink-0 items-center justify-center rounded-md border transition-all",
                  isSelected ? "border-primary bg-primary text-white" : "border-foreground/20"
                )}
              >
                {isSelected && <Check className="h-3 w-3" />}
              </div>
            </button>
          );
        })}
      </div>
      {saved && (
        <p className="px-5 pb-4 text-[11px] text-emerald-600 dark:text-emerald-400">
          Gespeichert.
        </p>
      )}
    </div>
  );
}
