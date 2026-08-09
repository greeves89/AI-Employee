"use client";

import { useState } from "react";
import { Palette } from "lucide-react";
import * as api from "@/lib/api";
import { getAgentAvatar } from "./agent-avatar";
import { AppearancePicker } from "./appearance-picker";

/**
 * Aussehen in einem kleinen Aufklappfenster — für Stellen, an denen kein Platz für
 * die offene Auswahl ist.
 *
 * Die Kachelreihe stand hier bis #523 ein drittes Mal im Code. Sie kommt jetzt aus
 * ``AppearancePicker``; damit gilt die freie Auswahl überall, nicht nur dort, wo
 * jemand daran gedacht hat.
 */
export function AgentAppearanceButton({
  agentId,
  config,
  onSaved,
}: {
  agentId: string;
  config?: Record<string, unknown> | null;
  onSaved?: () => void;
}) {
  const initial = getAgentAvatar(config);
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState({
    icon: initial.icon || "Cpu",
    color: initial.color || "violet",
  });
  const [saving, setSaving] = useState(false);

  const pick = async (next: { icon: string; color: string }) => {
    setValue(next);
    setSaving(true);
    try {
      await api.updateAgentAppearance(agentId, next);
      onSaved?.();
    } catch (e) {
      console.error("Aussehen speichern fehlgeschlagen:", e);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        title="Symbol & Farbe anpassen"
        className="inline-flex items-center gap-1.5 rounded-lg border border-foreground/[0.08] px-2.5 py-1.5 text-xs text-muted-foreground hover:bg-foreground/[0.04]"
      >
        <Palette className="h-3.5 w-3.5" />
        Symbol
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-50 mt-2 w-80 rounded-xl border border-foreground/10 bg-card p-3 shadow-xl">
            <AppearancePicker compact value={value} onChange={pick} />
            {saving && (
              <span className="mt-2 block text-[10px] text-muted-foreground/60">Speichern…</span>
            )}
          </div>
        </>
      )}
    </div>
  );
}
