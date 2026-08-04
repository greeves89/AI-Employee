"use client";

import { useState, useEffect, useCallback } from "react";
import { RefreshCw, Loader2, Cpu, Check, Search, Info } from "lucide-react";
import * as api from "@/lib/api";
import { cn } from "@/lib/utils";

// Admin freischaltung of models: shows every model the backend knows (seed +
// auto-discovered), with an enable toggle per model. Discovery queries the
// provider APIs; the admin decides which of the detected models agents may pick.
export function ModelCatalogAdmin() {
  const [catalog, setCatalog] = useState<api.AdminModelCatalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [discovering, setDiscovering] = useState(false);
  const [busyValue, setBusyValue] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setCatalog(await api.getAdminModelCatalog());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Katalog konnte nicht geladen werden");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const discover = async () => {
    setDiscovering(true);
    setNote(null);
    setError(null);
    try {
      const res = await api.discoverModels();
      setCatalog(res);
      const d = res.last_discovery;
      if (d) {
        const parts: string[] = [];
        parts.push(`Anthropic: ${d.anthropic_queried ? `${d.anthropic_found} erkannt` : "kein API-Key"}`);
        parts.push(`OpenAI: ${d.openai_queried ? `${d.openai_found} erkannt` : "kein API-Key"}`);
        // Say out loud what this can NOT find: only the public Anthropic/OpenAI
        // model APIs are queried. Azure-Foundry/Bedrock/Vertex deployments have
        // no such listing, so they never appear here — without this line it
        // looks like a bug when a connected Foundry resource shows no models.
        setNote(
          `${parts.join(" · ")} — ${d.new_extras} neue Modelle. Neue Modelle sind zunächst deaktiviert. ` +
          `Hinweis: Erkannt werden nur Anthropic/OpenAI direkt — eigene Azure-Foundry-, Bedrock- oder ` +
          `Vertex-Deployments trägst du unter AI-Accounts ein.`
        );
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Auto-Discovery fehlgeschlagen");
    } finally {
      setDiscovering(false);
    }
  };

  const toggle = async (value: string, enabled: boolean) => {
    setBusyValue(value);
    setError(null);
    try {
      setCatalog(await api.setModelsEnabled({ [value]: enabled }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Freischaltung fehlgeschlagen");
    } finally {
      setBusyValue(null);
    }
  };

  const discoveredAt = catalog?.discovered_at
    ? new Date(catalog.discovered_at).toLocaleString("de-DE")
    : null;

  return (
    <section>
      <div className="flex items-center gap-2 mb-3">
        <Cpu className="h-4 w-4 text-muted-foreground/60" />
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">
          Modelle freischalten
        </h2>
      </div>

      <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden">
        <div className="flex items-center justify-between gap-3 px-5 py-3.5 border-b border-foreground/[0.04]">
          <div className="min-w-0">
            <h3 className="text-sm font-semibold">Verfügbare Modelle</h3>
            <p className="text-[11px] text-muted-foreground/60">
              {discoveredAt
                ? `Zuletzt automatisch erkannt: ${discoveredAt}`
                : "Noch keine Auto-Discovery durchgeführt — Basis-Modelle sind freigeschaltet."}
            </p>
          </div>
          <button
            onClick={discover}
            disabled={discovering}
            className="inline-flex items-center gap-2 rounded-lg border border-primary/30 bg-primary/10 px-3 py-2 text-xs font-medium text-primary hover:bg-primary/15 disabled:opacity-50 transition-all shrink-0"
          >
            {discovering ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Search className="h-3.5 w-3.5" />
            )}
            Jetzt neu erkennen
          </button>
        </div>

        {note && (
          <div className="flex items-start gap-2 px-5 py-2.5 bg-primary/[0.04] border-b border-foreground/[0.04]">
            <Info className="h-3.5 w-3.5 text-primary/70 mt-0.5 shrink-0" />
            <p className="text-[11px] text-muted-foreground/80">{note}</p>
          </div>
        )}
        {error && (
          <div className="px-5 py-2.5 bg-red-500/[0.06] border-b border-red-500/10">
            <p className="text-[11px] text-red-400">{error}</p>
          </div>
        )}

        {loading ? (
          <div className="flex items-center justify-center py-10">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground/40" />
          </div>
        ) : (
          <div className="divide-y divide-foreground/[0.04]">
            {catalog?.modes.map((mode) => (
              <div key={mode.mode} className="px-5 py-4">
                <div className="flex items-center gap-2 mb-2.5">
                  <span className="text-xs font-semibold">{mode.label}</span>
                  <span className="text-[10px] text-muted-foreground/40 font-mono">{mode.mode}</span>
                </div>
                <div className="space-y-3">
                  {mode.providers.map((prov) => (
                    <div key={prov.provider}>
                      <div className="text-[10px] uppercase tracking-wider text-muted-foreground/40 mb-1.5">
                        {prov.provider}
                      </div>
                      <div className="space-y-1">
                        {prov.models.map((m) => (
                          <div
                            key={m.value}
                            className="flex items-center justify-between gap-3 rounded-lg border border-foreground/[0.05] bg-foreground/[0.015] px-3 py-2"
                          >
                            <div className="min-w-0">
                              <div className="flex items-center gap-2">
                                <span className="text-[13px] font-medium truncate">{m.label}</span>
                                <span
                                  className={cn(
                                    "text-[9px] uppercase tracking-wide rounded px-1.5 py-0.5 font-medium shrink-0",
                                    m.source === "discovered"
                                      ? "bg-amber-500/10 text-amber-400 border border-amber-500/20"
                                      : "bg-zinc-500/10 text-zinc-400 border border-zinc-500/20",
                                  )}
                                >
                                  {m.source === "discovered" ? "Erkannt" : "Basis"}
                                </span>
                              </div>
                              <div className="text-[10px] text-muted-foreground/40 font-mono truncate">{m.value}</div>
                            </div>
                            <button
                              role="switch"
                              aria-checked={m.enabled}
                              disabled={busyValue === m.value}
                              onClick={() => toggle(m.value, !m.enabled)}
                              className={cn(
                                "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors disabled:opacity-50",
                                m.enabled ? "bg-primary" : "bg-foreground/15",
                              )}
                            >
                              {busyValue === m.value ? (
                                <Loader2 className="absolute left-1/2 top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 animate-spin text-white" />
                              ) : (
                                <span
                                  className={cn(
                                    "inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform",
                                    m.enabled ? "translate-x-4" : "translate-x-0.5",
                                  )}
                                />
                              )}
                            </button>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}

        <div className="flex items-start gap-2 px-5 py-3 border-t border-foreground/[0.04] bg-foreground/[0.01]">
          <Check className="h-3.5 w-3.5 text-emerald-400/70 mt-0.5 shrink-0" />
          <p className="text-[10px] text-muted-foreground/50">
            Nur freigeschaltete Modelle erscheinen bei der Agent-Erstellung und in den Agent-Einstellungen.
            Die Harness-Zuordnung (Claude / Codex) wird serverseitig weiterhin hart erzwungen.
          </p>
        </div>
      </div>
    </section>
  );
}
