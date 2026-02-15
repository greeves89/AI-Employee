"use client";

import { useState, useEffect, useCallback } from "react";
import { Plug, CheckCircle2, Loader2, RefreshCw, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import type { Integration } from "@/lib/types";

interface IntegrationSelectorProps {
  agentId: string;
}

export function IntegrationSelector({ agentId }: IntegrationSelectorProps) {
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [agentIntegrations, setAgentIntegrations] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [changed, setChanged] = useState(false);

  const load = useCallback(async () => {
    try {
      const [{ integrations: all }, { integrations: enabled }] = await Promise.all([
        api.getIntegrations(),
        api.getAgentIntegrations(agentId),
      ]);
      setIntegrations(all);
      setAgentIntegrations(enabled);
    } catch {
      // API not ready
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    load();
  }, [load]);

  const toggle = (provider: string) => {
    setAgentIntegrations((prev) => {
      const next = prev.includes(provider)
        ? prev.filter((p) => p !== provider)
        : [...prev, provider];
      setChanged(true);
      return next;
    });
  };

  const save = async () => {
    setSaving(true);
    try {
      await api.updateAgentIntegrations(agentId, agentIntegrations);
      setChanged(false);
    } catch {
      // handle error
    } finally {
      setSaving(false);
    }
  };

  const connectedIntegrations = integrations.filter((i) => i.connected);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-40">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden">
        <div className="flex items-center justify-between border-b border-foreground/[0.06] px-5 py-3">
          <div className="flex items-center gap-2">
            <Plug className="h-4 w-4 text-blue-400" />
            <span className="text-sm font-medium">Agent Integrations</span>
            <span className="text-[10px] text-muted-foreground/60">
              Select which services this agent can access
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={load}
              className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[11px] font-medium text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] transition-colors"
            >
              <RefreshCw className="h-3 w-3" />
              Refresh
            </button>
            {changed && (
              <button
                onClick={save}
                disabled={saving}
                className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[11px] font-medium bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50"
              >
                {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <CheckCircle2 className="h-3 w-3" />}
                Save
              </button>
            )}
          </div>
        </div>

        <div className="p-5 space-y-3">
          {connectedIntegrations.length === 0 ? (
            <div className="text-center py-6 text-muted-foreground">
              <AlertCircle className="h-6 w-6 mx-auto mb-2 opacity-40" />
              <p className="text-sm">No integrations connected</p>
              <p className="text-xs mt-1">
                Go to{" "}
                <a href="/integrations" className="text-primary hover:underline">
                  Integrations
                </a>{" "}
                to connect your accounts first
              </p>
            </div>
          ) : (
            connectedIntegrations.map((integration) => {
              const enabled = agentIntegrations.includes(integration.provider);
              return (
                <label
                  key={integration.provider}
                  className={cn(
                    "flex items-center gap-3 rounded-xl border px-4 py-3 cursor-pointer transition-all",
                    enabled
                      ? "border-primary/30 bg-primary/5"
                      : "border-foreground/[0.06] hover:border-foreground/[0.12]"
                  )}
                >
                  <input
                    type="checkbox"
                    checked={enabled}
                    onChange={() => toggle(integration.provider)}
                    className="h-4 w-4 rounded border-foreground/20 bg-background text-primary focus:ring-primary/30"
                  />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium">{integration.display_name}</span>
                      {integration.account_label && (
                        <span className="text-[10px] text-muted-foreground/60">
                          ({integration.account_label})
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground/60">{integration.description}</p>
                  </div>
                  {enabled && <CheckCircle2 className="h-4 w-4 text-primary shrink-0" />}
                </label>
              );
            })
          )}
        </div>

        {changed && (
          <div className="border-t border-foreground/[0.06] px-5 py-3">
            <p className="text-[10px] text-yellow-500/80 flex items-center gap-1.5">
              <AlertCircle className="h-3 w-3" />
              Changes require an agent restart to take effect
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
