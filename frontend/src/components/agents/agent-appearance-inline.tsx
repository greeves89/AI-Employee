"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import { AVATAR_ICONS, AVATAR_COLORS, getAgentAvatar, AgentAvatar } from "./agent-avatar";

/** Inline icon + color picker (no popover → no stacking/z-index issues).
 *  Saves immediately on each pick via the appearance endpoint. */
export function AgentAppearanceInline({
  agentId,
  config,
}: {
  agentId: string;
  config?: Record<string, unknown> | null;
}) {
  const initial = getAgentAvatar(config);
  const [icon, setIcon] = useState(initial.icon || "Cpu");
  const [color, setColor] = useState(initial.color || "violet");
  const [saving, setSaving] = useState(false);

  const save = async (nextIcon: string, nextColor: string) => {
    setIcon(nextIcon);
    setColor(nextColor);
    setSaving(true);
    try {
      await api.updateAgentAppearance(agentId, nextIcon, nextColor);
    } catch (e) {
      console.error("Avatar speichern fehlgeschlagen:", e);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex items-start gap-4">
      <AgentAvatar config={{ avatar: { icon, color } }} size="lg" />
      <div className="flex-1 space-y-2">
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(AVATAR_ICONS).map(([n, Icon]) => (
            <button
              key={n}
              type="button"
              onClick={() => save(n, color)}
              className={cn(
                "flex h-8 w-8 items-center justify-center rounded-md border transition-colors",
                icon === n
                  ? "border-primary/50 bg-primary/10"
                  : "border-transparent hover:bg-foreground/[0.06]",
              )}
              title={n}
            >
              <Icon className="h-4 w-4" />
            </button>
          ))}
        </div>
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(AVATAR_COLORS).map(([n, c]) => (
            <button
              key={n}
              type="button"
              onClick={() => save(icon, n)}
              className={cn(
                "h-5 w-5 rounded-full transition-all",
                c.dot,
                color === n ? "ring-2 ring-offset-2 ring-offset-background ring-foreground/40" : "",
              )}
              title={n}
            />
          ))}
        </div>
      </div>
      {saving && <span className="text-[10px] text-muted-foreground/60 mt-1">Speichern…</span>}
    </div>
  );
}
