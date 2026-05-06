"use client";

import { useState, useEffect, useCallback } from "react";
import {
  Plug, CheckCircle2, Loader2, RefreshCw, AlertCircle,
  Network, ChevronRight, Wrench, Brain, Bell, Cpu,
  Shield, Plus, Trash2, ChevronDown, KeyRound, ExternalLink,
} from "lucide-react";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import type { Integration } from "@/lib/types";
import type { McpServerInfo, UrlAllowlistEntry, UrlAllowlistTemplate, AgentSecretEntry } from "@/lib/api";

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

  // URL Allowlist state
  const [allowlist, setAllowlist] = useState<UrlAllowlistEntry[]>([]);
  const [templates, setTemplates] = useState<UrlAllowlistTemplate[]>([]);
  const [newUrl, setNewUrl] = useState("");
  const [newUrlDesc, setNewUrlDesc] = useState("");
  const [addingUrl, setAddingUrl] = useState(false);
  const [showTemplates, setShowTemplates] = useState(false);
  const [applyingTemplate, setApplyingTemplate] = useState<number | null>(null);

  // KMS secrets state
  const [allSecrets, setAllSecrets] = useState<AgentSecretEntry[]>([]);
  const [agentSecretIds, setAgentSecretIds] = useState<Set<number>>(new Set());
  const [togglingSecret, setTogglingSecret] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const [{ integrations: all }, { integrations: enabled }, { servers }, mcpResp, entries, tmpl, secretsAll, agentSecretsResp] = await Promise.all([
        api.getIntegrations(),
        api.getAgentIntegrations(agentId),
        api.getMcpServers(),
        api.getAgentMcpServers(agentId),
        api.getAgentUrlAllowlist(agentId),
        api.getUrlAllowlistTemplates(),
        api.listSecrets(),
        api.getAgentSecrets(agentId),
      ]);
      setIntegrations(all);
      setAgentIntegrations(enabled);
      setMcpServers(servers);
      setAgentMcpServerIds(mcpResp.mcp_servers);
      setAllowlist(entries);
      setTemplates(tmpl);
      setAllSecrets(secretsAll);
      setAgentSecretIds(new Set(agentSecretsResp.map((s) => s.id)));
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

  const handleAddUrl = async () => {
    if (!newUrl.trim()) return;
    setAddingUrl(true);
    try {
      await api.addAgentUrl(agentId, newUrl.trim(), newUrlDesc.trim());
      setNewUrl("");
      setNewUrlDesc("");
      setAllowlist(await api.getAgentUrlAllowlist(agentId));
    } finally {
      setAddingUrl(false);
    }
  };

  const handleDeleteUrl = async (entryId: number) => {
    await api.deleteAgentUrl(agentId, entryId);
    setAllowlist((prev) => prev.filter((e) => e.id !== entryId));
  };

  const handleApplyTemplate = async (templateId: number) => {
    setApplyingTemplate(templateId);
    try {
      await api.applyUrlTemplate(agentId, templateId);
      setAllowlist(await api.getAgentUrlAllowlist(agentId));
      setShowTemplates(false);
    } finally {
      setApplyingTemplate(null);
    }
  };

  const handleToggleSecret = async (secretId: number) => {
    setTogglingSecret(secretId);
    try {
      if (agentSecretIds.has(secretId)) {
        await api.unassignSecret(agentId, secretId);
        setAgentSecretIds(prev => { const n = new Set(prev); n.delete(secretId); return n; });
      } else {
        await api.assignSecret(agentId, secretId);
        setAgentSecretIds(prev => new Set(Array.from(prev).concat(secretId)));
      }
    } finally {
      setTogglingSecret(null);
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
      {/* URL Allowlist */}
      <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden">
        <div className="flex items-center justify-between border-b border-foreground/[0.06] px-5 py-3">
          <div className="flex items-center gap-2">
            <Shield className="h-4 w-4 text-emerald-400" />
            <span className="text-sm font-medium">URL Allowlist</span>
            <span className="text-[10px] text-muted-foreground/60">
              Restrict which URLs this agent can access
            </span>
          </div>
          <div className="flex items-center gap-2">
            {allowlist.length > 0 && (
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                {allowlist.length} {allowlist.length === 1 ? "rule" : "rules"}
              </span>
            )}
            <button
              onClick={() => setShowTemplates((v) => !v)}
              className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[11px] font-medium text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] transition-colors"
            >
              <ChevronDown className={cn("h-3 w-3 transition-transform", showTemplates && "rotate-180")} />
              Templates
            </button>
          </div>
        </div>

        {/* Template picker */}
        {showTemplates && (
          <div className="border-b border-foreground/[0.06] px-5 py-3 space-y-2">
            <p className="text-[10px] text-muted-foreground/60 mb-2">Apply a preset — entries will be added to existing rules</p>
            {templates.map((t) => (
              <button
                key={t.id}
                onClick={() => handleApplyTemplate(t.id)}
                disabled={applyingTemplate === t.id}
                className="flex items-center justify-between w-full rounded-lg border border-foreground/[0.06] px-3 py-2 hover:bg-foreground/[0.04] transition-colors text-left"
              >
                <div>
                  <span className="text-[11px] font-medium">{t.name}</span>
                  {t.is_builtin && (
                    <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded bg-foreground/[0.06] text-muted-foreground/60">built-in</span>
                  )}
                  <p className="text-[10px] text-muted-foreground/60 mt-0.5">{t.description}</p>
                </div>
                {applyingTemplate === t.id
                  ? <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground shrink-0" />
                  : <Plus className="h-3.5 w-3.5 text-muted-foreground/40 shrink-0" />
                }
              </button>
            ))}
          </div>
        )}

        {/* Existing entries */}
        <div className="divide-y divide-foreground/[0.04]">
          {allowlist.length === 0 ? (
            <div className="px-5 py-4 text-center">
              <p className="text-[11px] text-muted-foreground/50">No rules — agent can access all URLs</p>
            </div>
          ) : (
            allowlist.map((entry) => (
              <div key={entry.id} className="flex items-center gap-3 px-5 py-2.5">
                <Shield className="h-3.5 w-3.5 text-emerald-400/60 shrink-0" />
                <div className="flex-1 min-w-0">
                  <span className="text-[11px] font-mono text-foreground/80 truncate block">{entry.url_pattern}</span>
                  {entry.description && (
                    <span className="text-[10px] text-muted-foreground/50">{entry.description}</span>
                  )}
                </div>
                <button
                  onClick={() => handleDeleteUrl(entry.id)}
                  className="p-1 rounded text-muted-foreground/40 hover:text-red-400 hover:bg-red-500/10 transition-colors shrink-0"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            ))
          )}
        </div>

        {/* Add URL */}
        <div className="border-t border-foreground/[0.06] px-5 py-3 space-y-2">
          <div className="flex gap-2">
            <input
              type="text"
              value={newUrl}
              onChange={(e) => setNewUrl(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleAddUrl()}
              placeholder="https://api.example.com/* or *.github.com"
              className="flex-1 rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-1.5 text-[11px] font-mono placeholder:text-muted-foreground/40 focus:outline-none focus:border-emerald-500/40"
            />
            <input
              type="text"
              value={newUrlDesc}
              onChange={(e) => setNewUrlDesc(e.target.value)}
              placeholder="Description (optional)"
              className="w-36 rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-1.5 text-[11px] placeholder:text-muted-foreground/40 focus:outline-none focus:border-emerald-500/40"
            />
            <button
              onClick={handleAddUrl}
              disabled={addingUrl || !newUrl.trim()}
              className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[11px] font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 hover:bg-emerald-500/20 transition-colors disabled:opacity-40"
            >
              {addingUrl ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />}
              Add
            </button>
          </div>
        </div>
      </div>

      {/* API Keys & Secrets (KMS) */}
      <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden">
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-foreground/[0.06]">
          <div className="flex items-center gap-2.5">
            <div className="rounded-lg p-1.5 bg-violet-500/10">
              <KeyRound className="h-4 w-4 text-violet-400" />
            </div>
            <div>
              <h3 className="text-sm font-medium">API Keys & Secrets</h3>
              <p className="text-[11px] text-muted-foreground/60 mt-0.5">
                {agentSecretIds.size > 0 ? `${agentSecretIds.size} secret${agentSecretIds.size !== 1 ? "s" : ""} assigned` : "No secrets assigned"}
                {" — injected as env vars when agent starts"}
              </p>
            </div>
          </div>
          <a
            href="/secrets"
            target="_blank"
            className="flex items-center gap-1 text-[11px] text-muted-foreground/50 hover:text-muted-foreground transition-colors"
          >
            Manage secrets
            <ExternalLink className="h-3 w-3" />
          </a>
        </div>

        {allSecrets.length === 0 ? (
          <div className="px-5 py-6 text-center">
            <KeyRound className="h-6 w-6 mx-auto mb-2 text-muted-foreground/30" />
            <p className="text-[11px] text-muted-foreground/50">No secrets configured yet.</p>
            <a href="/secrets" target="_blank" className="text-[11px] text-violet-400 hover:text-violet-300 transition-colors mt-1 inline-block">
              Create your first secret →
            </a>
          </div>
        ) : (
          <div className="divide-y divide-foreground/[0.04]">
            {allSecrets.filter(s => s.is_active).map((secret) => {
              const assigned = agentSecretIds.has(secret.id);
              const toggling = togglingSecret === secret.id;
              return (
                <div key={secret.id} className="flex items-center gap-3 px-5 py-3">
                  <div className="rounded-md p-1.5 bg-foreground/[0.04]">
                    <KeyRound className="h-3.5 w-3.5 text-muted-foreground/60" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-[12px] font-medium truncate">{secret.name}</span>
                      <span className="text-[10px] font-mono text-muted-foreground/50 bg-foreground/[0.04] px-1.5 py-0.5 rounded">
                        {secret.key_name}
                      </span>
                    </div>
                    {secret.description && (
                      <p className="text-[10px] text-muted-foreground/40 mt-0.5 truncate">{secret.description}</p>
                    )}
                  </div>
                  <button
                    onClick={() => handleToggleSecret(secret.id)}
                    disabled={toggling}
                    className={cn(
                      "flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-[11px] font-medium border transition-colors shrink-0 disabled:opacity-40",
                      assigned
                        ? "bg-violet-500/10 text-violet-400 border-violet-500/20 hover:bg-violet-500/20"
                        : "bg-foreground/[0.04] text-muted-foreground border-foreground/[0.08] hover:bg-foreground/[0.08]"
                    )}
                  >
                    {toggling
                      ? <Loader2 className="h-3 w-3 animate-spin" />
                      : assigned ? <CheckCircle2 className="h-3 w-3" /> : <Plus className="h-3 w-3" />
                    }
                    {assigned ? "Assigned" : "Assign"}
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
