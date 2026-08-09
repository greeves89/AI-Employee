"use client";

import { useCallback, useEffect, useState } from "react";
import { HeartPulse, Loader2, RotateCcw } from "lucide-react";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import { useToast } from "@/components/ui/dialog-provider";

/**
 * Selbstheilung pro Agent (#390).
 *
 * Angezeigt wird die **wirksame** Fassung, nicht die gespeicherte: sonst stünden
 * hier leere Felder, während im Betrieb die Vorgaben greifen — und wer speichert,
 * würde die Vorgaben unbeabsichtigt einfrieren.
 *
 * Die Zahlen sind absichtlich schlicht gehalten. Wer feiner steuern will als
 * „wie oft" und „wie lange warten", steuert am eigentlichen Problem vorbei: die
 * Wartezeit ist der Wirkstoff, nicht die Wiederholung.
 */
export function SelfHealingCard({ agentId }: { agentId: string }) {
  const [policy, setPolicy] = useState<api.SelfHealingPolicy | null>(null);
  const [customized, setCustomized] = useState(false);
  const [saving, setSaving] = useState(false);
  const toast = useToast();

  const load = useCallback(async () => {
    try {
      const data = await api.getAgentSelfHealing(agentId);
      setPolicy(data.policy);
      setCustomized(data.customized);
    } catch {
      setPolicy(null);
    }
  }, [agentId]);

  useEffect(() => {
    load();
  }, [load]);

  const save = async (patch: Partial<api.SelfHealingPolicy>) => {
    if (!policy) return;
    const next = { ...policy, ...patch };
    setPolicy(next);
    setSaving(true);
    try {
      const res = await api.updateAgentSelfHealing(agentId, next);
      setPolicy(res.policy);
      setCustomized(res.customized);
    } catch (e) {
      toast.error("Speichern fehlgeschlagen", e instanceof Error ? e.message : undefined);
      await load();
    } finally {
      setSaving(false);
    }
  };

  const reset = async () => {
    setSaving(true);
    try {
      const res = await api.resetAgentSelfHealing(agentId);
      setPolicy(res.policy);
      setCustomized(false);
      toast.success("Vorgaben wiederhergestellt");
    } catch (e) {
      toast.error("Fehlgeschlagen", e instanceof Error ? e.message : undefined);
    } finally {
      setSaving(false);
    }
  };

  if (!policy) return null;

  return (
    <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
      <div className="mb-3 flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 text-sm font-medium">
            <HeartPulse className="h-4 w-4 text-emerald-400" />
            Selbstheilung
          </div>
          <div className="text-[11px] text-muted-foreground/60">
            Scheitert eine Aufgabe an einem Zeitablauf oder einem Ausfall, versucht der
            Agent es selbst noch einmal — statt dich zu wecken. Dauerhafte Fehler
            (falsches Kennwort, fehlende Berechtigung) werden <b>nicht</b> wiederholt,
            sondern sofort weitergereicht.
          </div>
        </div>
        <button
          onClick={() => save({ enabled: !policy.enabled })}
          disabled={saving}
          aria-pressed={policy.enabled}
          className={cn(
            "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-50",
            policy.enabled ? "bg-emerald-500/80" : "bg-foreground/15",
          )}
        >
          <span
            className={cn(
              "inline-block h-4 w-4 rounded-full bg-white transition-transform",
              policy.enabled ? "translate-x-6" : "translate-x-1",
            )}
          />
        </button>
      </div>

      {policy.enabled && (
        <div className="space-y-3 border-t border-foreground/[0.06] pt-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block">
              <span className="text-[11px] text-muted-foreground">
                Neue Versuche (max. 5)
              </span>
              <input
                type="number"
                min={0}
                max={5}
                value={policy.max_attempts}
                onChange={(e) => save({ max_attempts: Number(e.target.value) })}
                className="mt-1 w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-1.5 text-xs outline-none focus:border-primary/50"
              />
              <span className="mt-1 block text-[10px] text-muted-foreground/50">
                1. gleich nochmal · 2. in kleineren Schritten · 3. mit anderem Modell
              </span>
            </label>
            <label className="block">
              <span className="text-[11px] text-muted-foreground">Erste Wartezeit (Sekunden)</span>
              <input
                type="number"
                min={5}
                max={3600}
                value={policy.base_delay_seconds}
                onChange={(e) => save({ base_delay_seconds: Number(e.target.value) })}
                className="mt-1 w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-1.5 text-xs outline-none focus:border-primary/50"
              />
              <span className="mt-1 block text-[10px] text-muted-foreground/50">
                verdoppelt sich je Versuch, höchstens {policy.max_delay_seconds}s
              </span>
            </label>
          </div>

          <label className="flex items-start gap-2 text-[11px] text-muted-foreground">
            <input
              type="checkbox"
              checked={policy.retry_unknown}
              onChange={(e) => save({ retry_unknown: e.target.checked })}
              className="mt-0.5"
            />
            <span>
              Auch bei <b>unklaren</b> Fehlern wiederholen. Ein Lauf, der ohne Meldung
              endet, wurde meist abgebrochen (Speicher, Absturz) — das ist wiederholbar.
            </span>
          </label>
        </div>
      )}

      <div className="mt-3 flex items-center gap-3">
        {saving && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground/60" />}
        {customized && (
          <button
            onClick={reset}
            disabled={saving}
            className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground hover:text-foreground"
          >
            <RotateCcw className="h-3 w-3" />
            Vorgaben wiederherstellen
          </button>
        )}
      </div>
    </div>
  );
}
