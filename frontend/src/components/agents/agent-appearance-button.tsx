"use client";

import { useState } from "react";
import { Palette, Check } from "lucide-react";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import { AVATAR_ICONS, AVATAR_COLORS, getAgentAvatar, AgentAvatar } from "./agent-avatar";

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
  const [icon, setIcon] = useState(initial.icon || "Cpu");
  const [color, setColor] = useState(initial.color || "violet");
  const [saving, setSaving] = useState(false);

  const save = async (nextIcon: string, nextColor: string) => {
    setIcon(nextIcon);
    setColor(nextColor);
    setSaving(true);
    try {
      await api.updateAgentAppearance(agentId, nextIcon, nextColor);
      onSaved?.();
    } catch (e) {
      console.error("Avatar speichern fehlgeschlagen:", e);
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
          <div className="absolute right-0 z-50 mt-2 w-64 rounded-xl border border-foreground/10 bg-card p-3 shadow-xl">
            <div className="mb-2 flex items-center gap-2">
              <AgentAvatar config={{ avatar: { icon, color } }} />
              <span className="text-xs text-muted-foreground">Vorschau</span>
            </div>
            <div className="grid grid-cols-6 gap-1.5 mb-3">
              {Object.entries(AVATAR_ICONS).map(([name, Icon]) => (
                <button
                  key={name}
                  type="button"
                  onClick={() => save(name, color)}
                  className={cn(
                    "flex h-8 w-8 items-center justify-center rounded-lg border transition-colors",
                    icon === name
                      ? "border-primary/50 bg-primary/10"
                      : "border-transparent hover:bg-foreground/[0.06]",
                  )}
                  title={name}
                >
                  <Icon className="h-4 w-4" />
                </button>
              ))}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(AVATAR_COLORS).map(([name, c]) => (
                <button
                  key={name}
                  type="button"
                  onClick={() => save(icon, name)}
                  className={cn("flex h-6 w-6 items-center justify-center rounded-full", c.dot)}
                  title={name}
                >
                  {color === name && <Check className="h-3 w-3 text-white" />}
                </button>
              ))}
            </div>
            {saving && <p className="mt-2 text-[10px] text-muted-foreground/60">Speichern…</p>}
          </div>
        </>
      )}
    </div>
  );
}
