"use client";

import { useEffect, useRef, useState } from "react";
import { Tag } from "lucide-react";
import * as api from "@/lib/api";
import { getAgentAvatar, getAgentTag } from "./agent-avatar";
import { AppearancePicker } from "./appearance-picker";

/**
 * Aussehen und Einsortierung eines Agenten — direkt auf der Agentenseite.
 *
 * Kein Aufklapp-Fenster: die Auswahl steht offen da, damit sie sich nicht mit
 * anderen Ebenen um die Vorderseite streitet. Gespeichert wird sofort bei jeder
 * Wahl, über denselben Endpunkt, der auch das Schlagwort (#524) entgegennimmt —
 * Symbol, Farbe und Schlagwort sind dieselbe Klasse Angabe.
 */
export function AgentAppearanceInline({
  agentId,
  config,
}: {
  agentId: string;
  config?: Record<string, unknown> | null;
}) {
  const initial = getAgentAvatar(config);
  const [value, setValue] = useState({
    icon: initial.icon || "Cpu",
    color: initial.color || "violet",
  });
  const [tag, setTag] = useState(getAgentTag(config));
  const [saving, setSaving] = useState(false);
  const tagTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const save = async (patch: { icon?: string; color?: string; tag?: string }) => {
    setSaving(true);
    try {
      await api.updateAgentAppearance(agentId, patch);
    } catch (e) {
      console.error("Aussehen speichern fehlgeschlagen:", e);
    } finally {
      setSaving(false);
    }
  };

  const pick = (next: { icon: string; color: string }) => {
    setValue(next);
    save(next);
  };

  // Beim Tippen nicht bei jedem Zeichen speichern — sonst schickt ein Schlagwort
  // mit zehn Buchstaben zehn Anfragen los.
  const onTag = (next: string) => {
    setTag(next);
    if (tagTimer.current) clearTimeout(tagTimer.current);
    tagTimer.current = setTimeout(() => save({ tag: next }), 600);
  };

  useEffect(() => () => {
    if (tagTimer.current) clearTimeout(tagTimer.current);
  }, []);

  return (
    <div className="space-y-3">
      <AppearancePicker value={value} onChange={pick} />

      <div>
        <label className="mb-1 flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
          <Tag className="h-3 w-3" />
          Schlagwort
        </label>
        <input
          value={tag}
          onChange={(e) => onTag(e.target.value)}
          placeholder="z. B. Kunde Meier, Vertrieb, Sandkasten"
          maxLength={32}
          className="w-full max-w-xs rounded-lg border border-foreground/[0.1] bg-background/80 px-3 py-1.5 text-xs outline-none transition-all focus:border-primary/50"
        />
        <p className="mt-1 text-[10px] text-muted-foreground/50">
          Frei wählbar. In der Übersicht lässt sich danach filtern und sortieren.
        </p>
      </div>

      {saving && <span className="text-[10px] text-muted-foreground/60">Speichern…</span>}
    </div>
  );
}
