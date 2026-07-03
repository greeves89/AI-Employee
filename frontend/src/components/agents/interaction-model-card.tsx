"use client";

import { useState } from "react";
import { Radio, Loader2, Mic, Check } from "lucide-react";
import * as api from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Per-agent voice interaction front selector.
 *
 * - "classic": the staged Aufnehmen→STT→LLM→TTS pipeline (push-to-talk).
 * - "nova_sonic": AWS Bedrock Nova Sonic realtime speech-to-speech — the agent
 *   listens continuously and delegates real work to itself via the ask_agent tool.
 */
export function InteractionModelCard({
  agentId,
  current,
}: {
  agentId: string;
  current?: string | null;
}) {
  const [model, setModel] = useState<string | null>(current ?? null);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  const apply = async (next: string | null) => {
    if (saving || next === model) return;
    setSaving(true);
    setMsg("");
    try {
      await api.updateAgentInteractionModel(agentId, next);
      setModel(next);
      setMsg("Gespeichert.");
    } catch (e) {
      setMsg(`Fehler: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSaving(false);
    }
  };

  const options: { key: string | null; label: string; hint: string; icon: typeof Mic }[] = [
    { key: null, label: "Klassisch", hint: "Aufnehmen & senden (Push-to-Talk)", icon: Mic },
    { key: "nova_sonic", label: "Nova Sonic (Echtzeit)", hint: "Durchgehendes Live-Gespräch, delegiert Aufgaben selbst", icon: Radio },
  ];

  return (
    <div className="overflow-hidden rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm">
      <div className="flex items-center justify-between border-b border-foreground/[0.06] px-5 py-3">
        <div className="flex items-center gap-2">
          <Radio className="h-4 w-4 text-fuchsia-400" />
          <span className="text-sm font-medium">Sprach-Interaktion</span>
          {model === "nova_sonic" && (
            <span className="rounded-full border border-fuchsia-500/20 bg-fuchsia-500/10 px-2 py-0.5 text-[10px] font-medium text-fuchsia-400">
              Realtime
            </span>
          )}
        </div>
        {saving && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground/60" />}
      </div>
      <div className="space-y-3 p-5">
        <p className="text-[11px] leading-relaxed text-muted-foreground/70">
          Wie der Agent per Sprache mit dir interagiert. „Nova Sonic" ist ein
          Echtzeit-Sprachmodell (AWS Bedrock): es hört durchgehend zu, spricht
          natürlich und gibt erkannte Aufgaben über <code>ask_agent</code> an den
          Agenten weiter. Läuft in der Cloud — keine Last auf dem Gerät.
        </p>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {options.map((o) => {
            const active = (model ?? null) === o.key;
            const Icon = o.icon;
            return (
              <button
                key={o.key ?? "classic"}
                onClick={() => apply(o.key)}
                disabled={saving}
                className={cn(
                  "flex flex-col gap-1 rounded-lg border p-3 text-left transition-colors disabled:opacity-60",
                  active
                    ? "border-fuchsia-500/40 bg-fuchsia-500/[0.06]"
                    : "border-foreground/[0.08] hover:border-foreground/20 hover:bg-foreground/[0.03]",
                )}
              >
                <span className="flex items-center gap-1.5 text-sm font-medium">
                  <Icon className="h-3.5 w-3.5" />
                  {o.label}
                  {active && <Check className="ml-auto h-3.5 w-3.5 text-fuchsia-400" />}
                </span>
                <span className="text-[11px] text-muted-foreground/70">{o.hint}</span>
              </button>
            );
          })}
        </div>
        {model === "nova_sonic" && (
          <p className="text-[11px] text-amber-400/80">
            Benötigt konfigurierte AWS-Bedrock-Zugangsdaten auf dem Server.
          </p>
        )}
        {msg && <p className="text-[11px] text-muted-foreground/70">{msg}</p>}
      </div>
    </div>
  );
}
