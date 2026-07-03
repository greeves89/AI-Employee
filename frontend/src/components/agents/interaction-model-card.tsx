"use client";

import { useEffect, useState } from "react";
import { Radio, Loader2, Mic, Check, ExternalLink } from "lucide-react";
import * as api from "@/lib/api";
import type { RealtimeModelOption } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Per-agent voice interaction front selector.
 *
 * - "Klassisch": staged Aufnehmen→STT→LLM→TTS pipeline (push-to-talk).
 * - A realtime model (from a configured AI-account): AWS Bedrock Nova Sonic etc.
 *   The agent listens continuously and delegates real work to itself.
 *
 * Realtime models come from AI-Accounts (AWS/Azure) — configure those first.
 */
export function InteractionModelCard({
  agentId,
  current,
  currentAccountId,
  currentModelId,
}: {
  agentId: string;
  current?: string | null;
  currentAccountId?: number | null;
  currentModelId?: string | null;
}) {
  const [models, setModels] = useState<RealtimeModelOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [selValue, setSelValue] = useState<string | null>(
    current && currentAccountId && currentModelId ? `${currentAccountId}:${currentModelId}` : null,
  );
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    api.getRealtimeModels()
      .then(setModels)
      .catch(() => setModels([]))
      .finally(() => setLoading(false));
  }, []);

  const applyClassic = async () => {
    if (saving) return;
    setSaving(true); setMsg("");
    try {
      await api.updateAgentInteractionModel(agentId, { interactionModel: null });
      setSelValue(null);
      setMsg("Auf klassische Sprach-Pipeline gestellt.");
    } catch (e) {
      setMsg(`Fehler: ${e instanceof Error ? e.message : String(e)}`);
    } finally { setSaving(false); }
  };

  const applyRealtime = async (opt: RealtimeModelOption) => {
    if (saving || opt.value === selValue) return;
    if (!opt.implemented) { setMsg(`${opt.provider_label} ist noch nicht verfügbar.`); return; }
    setSaving(true); setMsg("");
    try {
      await api.updateAgentInteractionModel(agentId, {
        interactionModel: opt.engine,
        interactionAccountId: opt.account_id,
        interactionModelId: opt.model_id,
      });
      setSelValue(opt.value);
      setMsg(`Aktiv: ${opt.model_label} über ${opt.account_name}.`);
    } catch (e) {
      setMsg(`Fehler: ${e instanceof Error ? e.message : String(e)}`);
    } finally { setSaving(false); }
  };

  return (
    <div className="overflow-hidden rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm">
      <div className="flex items-center justify-between border-b border-foreground/[0.06] px-5 py-3">
        <div className="flex items-center gap-2">
          <Radio className="h-4 w-4 text-fuchsia-400" />
          <span className="text-sm font-medium">Sprach-Interaktion</span>
          {selValue && (
            <span className="rounded-full border border-fuchsia-500/20 bg-fuchsia-500/10 px-2 py-0.5 text-[10px] font-medium text-fuchsia-400">
              Realtime
            </span>
          )}
        </div>
        {saving && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground/60" />}
      </div>
      <div className="space-y-3 p-5">
        <p className="text-[11px] leading-relaxed text-muted-foreground/70">
          Wie der Agent per Sprache mit dir spricht. Realtime-Modelle (z. B. AWS Nova Sonic)
          hören durchgehend zu und geben Aufgaben über <code>ask_agent</code> weiter — Zugänge
          richtest du unter <b>AI-Accounts</b> ein, hier wählst du Modell ↔ Provider.
        </p>

        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {/* Classic */}
          <button
            onClick={applyClassic}
            disabled={saving}
            className={cn(
              "flex flex-col gap-1 rounded-lg border p-3 text-left transition-colors disabled:opacity-60",
              selValue === null
                ? "border-fuchsia-500/40 bg-fuchsia-500/[0.06]"
                : "border-foreground/[0.08] hover:border-foreground/20 hover:bg-foreground/[0.03]",
            )}
          >
            <span className="flex items-center gap-1.5 text-sm font-medium">
              <Mic className="h-3.5 w-3.5" /> Klassisch
              {selValue === null && <Check className="ml-auto h-3.5 w-3.5 text-fuchsia-400" />}
            </span>
            <span className="text-[11px] text-muted-foreground/70">Aufnehmen &amp; senden (Push-to-Talk)</span>
          </button>

          {/* Realtime models from configured accounts */}
          {models.map((o) => {
            const active = selValue === o.value;
            return (
              <button
                key={o.value}
                onClick={() => applyRealtime(o)}
                disabled={saving || !o.implemented}
                title={o.implemented ? "" : "Noch nicht verfügbar"}
                className={cn(
                  "flex flex-col gap-1 rounded-lg border p-3 text-left transition-colors disabled:opacity-50",
                  active
                    ? "border-fuchsia-500/40 bg-fuchsia-500/[0.06]"
                    : "border-foreground/[0.08] hover:border-foreground/20 hover:bg-foreground/[0.03]",
                )}
              >
                <span className="flex items-center gap-1.5 text-sm font-medium">
                  <Radio className="h-3.5 w-3.5" /> {o.model_label}
                  {active && <Check className="ml-auto h-3.5 w-3.5 text-fuchsia-400" />}
                </span>
                <span className="text-[11px] text-muted-foreground/70">
                  {o.provider_label} · {o.account_name}
                  {!o.implemented && " · bald"}
                </span>
              </button>
            );
          })}
        </div>

        {!loading && models.length === 0 && (
          <a
            href="/?tab=ai-accounts"
            className="flex items-center gap-1.5 text-[11px] text-fuchsia-400 hover:underline"
          >
            <ExternalLink className="h-3 w-3" />
            Noch kein Realtime-Provider. Lege unter AI-Accounts einen AWS-Bedrock-Zugang an.
          </a>
        )}
        {msg && <p className="text-[11px] text-muted-foreground/70">{msg}</p>}
      </div>
    </div>
  );
}
