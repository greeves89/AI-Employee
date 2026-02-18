"use client";

import { useState, useEffect, useCallback } from "react";
import {
  Plug, CheckCircle2, Loader2, RefreshCw, AlertCircle,
  Network, ChevronRight, Wrench, Brain, Bell, Cpu,
} from "lucide-react";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import type { Integration } from "@/lib/types";
import type { McpServerInfo } from "@/lib/api";

// Built-in MCP servers that every agent has
const BUILTIN_MCP_SERVERS = [
  {
    name: "Memory",
    icon: Brain,
    color: "text-purple-400",
    iconBg: "bg-purple-500/10",
    tools: [
      { name: "memory_save", description: "Save important information to categorized memory" },
      { name: "memory_search", description: "Search memories by keyword and/or category" },
      { name: "memory_list", description: "List all memories, filtered by category" },
      { name: "memory_delete", description: "Delete a specific memory" },
    ],
  },
  {
    name: "Notifications",
    icon: Bell,
    color: "text-amber-400",
    iconBg: "bg-amber-500/10",
    tools: [
      { name: "notify_user", description: "Send notification (Web UI + Telegram for high/urgent)" },
      { name: "request_approval", description: "Ask for explicit approval before critical actions" },
    ],
  },
  {
    name: "Orchestrator",
    icon: Cpu,
    color: "text-blue-400",
    iconBg: "bg-blue-500/10",
    tools: [
      { name: "create_task", description: "Create tasks for self or other agents" },
      { name: "list_team", description: "See all team members with roles and status" },
      { name: "send_message", description: "Send a text message to another agent" },
      { name: "create_schedule", description: "Create recurring task schedules" },
    ],
  },
];

interface IntegrationSelectorProps {
  agentId: string;
}

export function IntegrationSelector({ agentId }: IntegrationSelectorProps) {
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [agentIntegrations, setAgentIntegrations] = useState<string[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServerInfo[]>([]);
  const [agentMcpServerIds, setAgentMcpServerIds] = useState<number[] | null>(null);
  const [expandedItem, setExpandedItem] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [changed, setChanged] = useState(false);
  const [mcpChanged, setMcpChanged] = useState(false);

  const load = useCallback(async () => {
    try {
      const [{ integrations: all }, { integrations: enabled }, { servers }, mcpResp] = await Promise.all([
        api.getIntegrations(),
        api.getAgentIntegrations(agentId),
        api.getMcpServers(),
        api.getAgentMcpServers(agentId),
      ]);
      setIntegrations(all);
      setAgentIntegrations(enabled);
      setMcpServers(servers);
      setAgentMcpServerIds(mcpResp.mcp_servers);
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

  const toggleMcp = (serverId: number) => {
    setAgentMcpServerIds((prev) => {
      if (prev === null) {
        // Switching from "all" to explicit selection - include all except toggled
        const allIds = enabledMcpServers.map((s) => s.id);
        return allIds.filter((id) => id !== serverId);
      }
      const next = prev.includes(serverId)
        ? prev.filter((id) => id !== serverId)
        : [...prev, serverId];
      return next;
    });
    setMcpChanged(true);
  };

  const isMcpEnabled = (serverId: number) => {
    if (agentMcpServerIds === null) return true;
    return agentMcpServerIds.includes(serverId);
  };

  const save = async () => {
    setSaving(true);
    try {
      const promises: Promise<void>[] = [];
      if (changed) {
        promises.push(api.updateAgentIntegrations(agentId, agentIntegrations));
      }
      if (mcpChanged) {
        promises.push(api.updateAgentMcpServers(agentId, agentMcpServerIds));
      }
      await Promise.all(promises);
      setChanged(false);
      setMcpChanged(false);
    } catch {
      // handle error
    } finally {
      setSaving(false);
    }
  };

  const connectedIntegrations = integrations.filter((i) => i.connected);
  const enabledMcpServers = mcpServers.filter((s) => s.enabled);
  const activeMcpCount = agentMcpServerIds === null
    ? enabledMcpServers.length
    : enabledMcpServers.filter((s) => agentMcpServerIds.includes(s.id)).length;
  const totalMcpCount = BUILTIN_MCP_SERVERS.length + activeMcpCount;
  const hasChanges = changed || mcpChanged;

  if (loading) {
    return (
      <div className="flex items-center justify-center h-40">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* MCP Servers - Built-in + External */}
      <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden">
        <div className="flex items-center justify-between border-b border-foreground/[0.06] px-5 py-3">
          <div className="flex items-center gap-2">
            <Network className="h-4 w-4 text-violet-400" />
            <span className="text-sm font-medium">MCP Tools</span>
            <span className="text-[10px] text-muted-foreground/60">
              Tool servers available to this agent
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-violet-500/10 text-violet-400 border border-violet-500/20">
              {totalMcpCount} servers active
            </span>
            {mcpChanged && (
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

        <div className="divide-y divide-foreground/[0.04]">
          {/* Built-in servers */}
          {BUILTIN_MCP_SERVERS.map((server) => {
            const Icon = server.icon;
            const key = `builtin-${server.name}`;
            const isExpanded = expandedItem === key;
            return (
              <div key={key}>
                <button
                  onClick={() => setExpandedItem(isExpanded ? null : key)}
                  className="flex items-center gap-3 w-full px-5 py-3 hover:bg-foreground/[0.02] transition-colors"
                >
                  <div className={cn("flex h-8 w-8 shrink-0 items-center justify-center rounded-lg", server.iconBg)}>
                    <Icon className={cn("h-4 w-4", server.color)} />
                  </div>
                  <div className="flex-1 text-left min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium">{server.name}</span>
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-foreground/[0.06] text-muted-foreground/60">
                        built-in
                      </span>
                    </div>
                    <p className="text-[11px] text-muted-foreground/60 mt-0.5">
                      {server.tools.length} tools
                    </p>
                  </div>
                  <ChevronRight
                    className={cn(
                      "h-4 w-4 text-muted-foreground/40 transition-transform duration-200 shrink-0",
                      isExpanded && "rotate-90"
                    )}
                  />
                </button>

                {isExpanded && (
                  <div className="px-5 pb-3 space-y-1.5">
                    <div className="pl-11 space-y-1">
                      {server.tools.map((tool) => (
                        <div
                          key={tool.name}
                          className="flex items-start gap-2.5 py-1.5 px-3 rounded-lg bg-foreground/[0.02]"
                        >
                          <Wrench className="h-3 w-3 text-muted-foreground/40 shrink-0 mt-0.5" />
                          <div className="min-w-0">
                            <span className="text-[11px] font-mono font-medium text-foreground/80">
                              {tool.name}
                            </span>
                            <span className="text-[10px] text-muted-foreground/60 ml-2">
                              {tool.description}
                            </span>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            );
          })}

          {/* External MCP servers - with per-agent checkboxes */}
          {enabledMcpServers.map((server) => {
            const key = `ext-${server.id}`;
            const isExpanded = expandedItem === key;
            const toolCount = server.tools?.length ?? 0;
            const enabled = isMcpEnabled(server.id);
            return (
              <div key={key} className={cn(!enabled && "opacity-50")}>
                <div className="flex items-center gap-3 w-full px-5 py-3 hover:bg-foreground/[0.02] transition-colors">
                  <input
                    type="checkbox"
                    checked={enabled}
                    onChange={() => toggleMcp(server.id)}
                    className="h-4 w-4 rounded border-foreground/20 bg-background text-violet-500 focus:ring-violet-500/30 shrink-0"
                  />
                  <button
                    onClick={() => setExpandedItem(isExpanded ? null : key)}
                    className="flex items-center gap-3 flex-1 min-w-0"
                  >
                    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-violet-500/10">
                      <Network className="h-4 w-4 text-violet-400" />
                    </div>
                    <div className="flex-1 text-left min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium">{server.name}</span>
                        <span className="text-[10px] text-muted-foreground/50 truncate max-w-[300px]">
                          {server.url}
                        </span>
                      </div>
                      <p className="text-[11px] text-muted-foreground/60 mt-0.5">
                        {toolCount} tool{toolCount !== 1 ? "s" : ""}
                      </p>
                    </div>
                    <ChevronRight
                      className={cn(
                        "h-4 w-4 text-muted-foreground/40 transition-transform duration-200 shrink-0",
                        isExpanded && "rotate-90"
                      )}
                    />
                  </button>
                </div>

                {isExpanded && server.tools && server.tools.length > 0 && (
                  <div className="px-5 pb-3 space-y-1.5">
                    <div className="pl-11 space-y-1">
                      {server.tools.map((tool) => (
                        <div
                          key={tool.name}
                          className="flex items-start gap-2.5 py-1.5 px-3 rounded-lg bg-foreground/[0.02]"
                        >
                          <Wrench className="h-3 w-3 text-violet-400/60 shrink-0 mt-0.5" />
                          <div className="min-w-0">
                            <span className="text-[11px] font-mono font-medium text-foreground/80">
                              {tool.name}
                            </span>
                            {tool.description && (
                              <p className="text-[10px] text-muted-foreground/60 mt-0.5">
                                {tool.description}
                              </p>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {enabledMcpServers.length === 0 && (
          <div className="border-t border-foreground/[0.04] px-5 py-3">
            <p className="text-[11px] text-muted-foreground/50">
              Add external MCP servers on the{" "}
              <a href="/integrations" className="text-primary hover:underline">Integrations</a>{" "}
              page, then restart the agent to activate them.
            </p>
          </div>
        )}

        {mcpChanged && (
          <div className="border-t border-foreground/[0.06] px-5 py-3">
            <p className="text-[10px] text-yellow-500/80 flex items-center gap-1.5">
              <AlertCircle className="h-3 w-3" />
              Changes require an agent restart to take effect
            </p>
          </div>
        )}
      </div>

      {/* OAuth Integrations */}
      <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden">
        <div className="flex items-center justify-between border-b border-foreground/[0.06] px-5 py-3">
          <div className="flex items-center gap-2">
            <Plug className="h-4 w-4 text-blue-400" />
            <span className="text-sm font-medium">Integrations</span>
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

        {hasChanges && (
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
