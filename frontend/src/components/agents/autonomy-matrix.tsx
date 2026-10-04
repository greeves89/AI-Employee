"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, HelpCircle, Ban, Loader2, Save, Container, Globe, ShieldCheck, Lock } from "lucide-react";
import * as api from "@/lib/api";
import type { AutonomyState, AutonomyTaxonomy } from "@/lib/api";
import { apiFehlertext } from "@/lib/api-fehler";
import { useAutonomieGrenze } from "@/lib/autonomie-grenze";
import { cn } from "@/lib/utils";

const STATE_META: Record<AutonomyState, { label: string; icon: typeof Check; on: string }> = {
  allow: { label: "Erlaubt", icon: Check, on: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30" },
  ask: { label: "Freigabe", icon: HelpCircle, on: "bg-amber-500/15 text-amber-700 dark:text-amber-400 border-amber-500/30" },
  deny: { label: "Verboten", icon: Ban, on: "bg-red-500/15 text-red-400 border-red-500/30" },
};
const GROUP_ICON: Record<string, typeof Container> = { container: Container, external: Globe };
const LEVEL_LABEL: Record<string, string> = {
  l1: "L1", l2: "L2", l3: "L3", l4: "L4", custom: "Custom",
};
// Wie frei ein Zustand ist — für den Vergleich mit der Rollen-Grenze (#910).
const RANG: Record<AutonomyState, number> = { deny: 0, ask: 1, allow: 2 };

/**
 * 3-state autonomy capability matrix. Lx preset buttons fill the matrix; each
 * capability can then be fine-tuned to Erlaubt / Freigabe / Verboten. The matrix
 * is the single source that drives the agent's approval behaviour server-side.
 */
export function AutonomyMatrix({
  agentId,
  onLevelChange,
}: {
  agentId: string;
  onLevelChange?: (level: string) => void;
}) {
  const [taxonomy, setTaxonomy] = useState<AutonomyTaxonomy | null>(null);
  const [level, setLevel] = useState("l3");
  const [matrix, setMatrix] = useState<Record<string, AutonomyState>>({});
  const [saved, setSaved] = useState<Record<string, AutonomyState>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [fehler, setFehler] = useState<string | null>(null);
  // Rollen-Grenze (#910): darüber ist ausgegraut. Bestandsschutz — was der Agent
  // schon hat (aktive Stufe, gespeicherte Zelle), bleibt wählbar.
  const { grenze, ueber } = useAutonomieGrenze();

  const load = useCallback(async () => {
    try {
      const d = await api.getAutonomyMatrix(agentId);
      setTaxonomy(d.taxonomy);
      setLevel(d.autonomy_level);
      setMatrix(d.matrix);
      setSaved(d.matrix);
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => { load(); }, [load]);

  const dirty = useMemo(
    () => taxonomy?.capabilities.some((c) => matrix[c.key] !== saved[c.key]) ?? false,
    [taxonomy, matrix, saved]
  );

  const applyPreset = async (lvl: string) => {
    setBusy(true);
    setFehler(null);
    try {
      await api.setAgentAutonomyLevel(agentId, lvl);
      await load();
      onLevelChange?.(lvl);
    } catch (e) {
      setFehler(apiFehlertext(e, "Die Stufe konnte nicht gesetzt werden."));
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    setBusy(true);
    setFehler(null);
    try {
      const res = await api.updateAutonomyMatrix(agentId, matrix);
      setSaved(res.matrix);
      setMatrix(res.matrix);
      setLevel(res.autonomy_level);
      onLevelChange?.(res.autonomy_level);
    } catch (e) {
      setFehler(apiFehlertext(e, "Die Matrix konnte nicht gespeichert werden."));
    } finally {
      setBusy(false);
    }
  };

  // Ein Zustand liegt über der Grenze, wenn er freier ist als die Voreinstellung
  // der Grenzstufe UND freier als bisher gespeichert.
  const zustandGesperrt = (key: string, st: AutonomyState) => {
    if (!grenze || !taxonomy) return false;
    const erlaubt = taxonomy.presets[grenze]?.matrix?.[key] ?? "ask";
    const bisher = saved[key] ?? "ask";
    return RANG[st] > RANG[erlaubt] && RANG[st] > RANG[bisher];
  };

  if (loading) {
    return <div className="flex items-center justify-center py-8 text-muted-foreground"><Loader2 className="h-5 w-5 animate-spin" /></div>;
  }
  if (!taxonomy) return null;

  return (
    <div className="space-y-4">
      {/* Preset buttons */}
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-[11px] font-medium text-muted-foreground/70">Vorlage:</span>
        {(["l1", "l2", "l3", "l4"] as const).map((lvl) => {
          const gesperrt = ueber(lvl) && level !== lvl;
          return (
            <button
              key={lvl}
              onClick={() => applyPreset(lvl)}
              disabled={busy || gesperrt}
              className={cn(
                "inline-flex items-center gap-1 rounded-lg border px-2.5 py-1.5 text-[11px] font-semibold transition-all disabled:opacity-40",
                gesperrt && "cursor-not-allowed",
                level === lvl
                  ? "bg-primary/15 text-primary border-primary/30"
                  : "border-foreground/[0.08] text-muted-foreground hover:text-foreground hover:bg-foreground/[0.05]"
              )}
              title={gesperrt
                ? `Deine Rolle erlaubt höchstens Stufe ${grenze?.toUpperCase()}.`
                : taxonomy.presets[lvl]?.label}
            >
              {gesperrt && <Lock className="h-3 w-3" />}
              {taxonomy.presets[lvl]?.label || lvl.toUpperCase()}
            </button>
          );
        })}
        <span className="ml-auto flex items-center gap-1 text-[11px] text-muted-foreground/70">
          <ShieldCheck className="h-3.5 w-3.5" /> Aktiv: <b className="text-foreground">{LEVEL_LABEL[level] || level}</b>
        </span>
      </div>

      {grenze && (
        <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <Lock className="h-3 w-3" />
          Deine Rolle erlaubt höchstens Stufe {grenze.toUpperCase()}. Was darüber liegt, ist ausgegraut
          — bestehende Einstellungen bleiben erhalten.
        </p>
      )}
      {fehler && (
        <p className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-[11px] text-red-400">{fehler}</p>
      )}

      {/* Groups */}
      {taxonomy.groups.map((g) => {
        const GIcon = GROUP_ICON[g.key] || Globe;
        const caps = taxonomy.capabilities.filter((c) => c.group === g.key);
        return (
          <div key={g.key} className="rounded-xl border border-foreground/[0.06] bg-card/40 overflow-hidden">
            <div className="flex items-center gap-2 border-b border-foreground/[0.06] px-4 py-2 text-xs font-medium text-muted-foreground">
              <GIcon className="h-3.5 w-3.5" /> {g.label}
            </div>
            <div className="divide-y divide-foreground/[0.04]">
              {caps.map((c) => (
                <div key={c.key} className="flex items-center justify-between gap-3 px-4 py-2.5">
                  <div className="min-w-0">
                    <div className="text-sm font-medium">{c.label}</div>
                    <div className="truncate text-[11px] text-muted-foreground/70">{c.description}</div>
                  </div>
                  <div className="flex shrink-0 gap-1">
                    {(taxonomy.states).map((st) => {
                      const meta = STATE_META[st];
                      const Icon = meta.icon;
                      const active = matrix[c.key] === st;
                      const gesperrt = !active && zustandGesperrt(c.key, st);
                      return (
                        <button
                          key={st}
                          onClick={() => setMatrix((m) => ({ ...m, [c.key]: st }))}
                          disabled={gesperrt}
                          className={cn(
                            "inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-[11px] font-medium transition-all",
                            active ? meta.on : "border-transparent text-muted-foreground/50 hover:bg-foreground/[0.05]",
                            gesperrt && "cursor-not-allowed opacity-30 hover:bg-transparent"
                          )}
                          title={gesperrt ? `Über der Grenze deiner Rolle (Stufe ${grenze?.toUpperCase()})` : meta.label}
                        >
                          <Icon className="h-3 w-3" />
                          <span className="hidden sm:inline">{meta.label}</span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </div>
        );
      })}

      <div className="flex items-center justify-between">
        <p className="text-[11px] text-muted-foreground/60">
          Änderungen wirken beim nächsten Task des Agenten (kein Neustart nötig).
        </p>
        {dirty && (
          <button
            onClick={save}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-1.5 text-[11px] font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
          >
            {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
            Matrix speichern
          </button>
        )}
      </div>
    </div>
  );
}
