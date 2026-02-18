"use client";

import { useState, useEffect } from "react";
import {
  Plug, Mail, Cloud, Smartphone, CheckCircle2,
  AlertCircle, Loader2, Unplug, ExternalLink, RefreshCw,
  Plus, Trash2, ChevronRight, Wrench, Globe, Power,
  Github, Eye, EyeOff, Save,
} from "lucide-react";
import { Header } from "@/components/layout/header";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import type { Integration } from "@/lib/types";
import type { McpServerInfo, McpTool } from "@/lib/api";
import { useSearchParams } from "next/navigation";

const PROVIDER_ICONS: Record<string, typeof Mail> = {
  Mail,
  Cloud,
  Smartphone,
  Github,
};

export default function IntegrationsPage() {
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [loading, setLoading] = useState(true);
  const [connecting, setConnecting] = useState<string | null>(null);
  const [disconnecting, setDisconnecting] = useState<string | null>(null);
  const [toast, setToast] = useState<{ type: "success" | "error"; message: string } | null>(null);
  const [patToken, setPatToken] = useState("");
  const [patSaving, setPatSaving] = useState<string | null>(null);
  const [patVisible, setPatVisible] = useState(false);
  const searchParams = useSearchParams();

  const loadIntegrations = async () => {
    try {
      const { integrations: list } = await api.getIntegrations();
      setIntegrations(list);
    } catch {
      // API not ready yet
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadIntegrations();
  }, []);

  // Handle OAuth callback redirects
  useEffect(() => {
    const connected = searchParams.get("connected");
    const error = searchParams.get("error");
    if (connected) {
      setToast({ type: "success", message: `Successfully connected ${connected}!` });
      loadIntegrations();
      window.history.replaceState({}, "", "/integrations");
    }
    if (error) {
      setToast({ type: "error", message: `Connection failed: ${error}` });
      window.history.replaceState({}, "", "/integrations");
    }
  }, [searchParams]);

  // Auto-dismiss toast
  useEffect(() => {
    if (toast) {
      const t = setTimeout(() => setToast(null), 5000);
      return () => clearTimeout(t);
    }
  }, [toast]);

  const handleConnect = async (provider: string) => {
    setConnecting(provider);
    try {
      const { auth_url } = await api.getAuthUrl(provider);
      window.location.href = auth_url;
    } catch (e) {
      setToast({ type: "error", message: e instanceof Error ? e.message : "Failed to start OAuth flow" });
      setConnecting(null);
    }
  };

  const handleDisconnect = async (provider: string) => {
    if (!confirm(`Disconnect ${provider}? Agents using this integration will lose access.`)) return;
    setDisconnecting(provider);
    try {
      await api.disconnectIntegration(provider);
      setToast({ type: "success", message: `Disconnected ${provider}` });
      setPatToken("");
      await loadIntegrations();
    } catch (e) {
      setToast({ type: "error", message: e instanceof Error ? e.message : "Failed to disconnect" });
    } finally {
      setDisconnecting(null);
    }
  };

  const handleSavePat = async (provider: string) => {
    if (!patToken.trim()) return;
    setPatSaving(provider);
    try {
      const result = await api.savePatToken(provider, patToken.trim());
      setToast({ type: "success", message: `Connected to ${provider} as ${result.account_label || "unknown"}` });
      setPatToken("");
      setPatVisible(false);
      await loadIntegrations();
    } catch (e) {
      setToast({ type: "error", message: e instanceof Error ? e.message : "Invalid token" });
    } finally {
      setPatSaving(null);
    }
  };

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <Header title="Integrations" subtitle="Connect external services and MCP servers for your agents" />

      {/* Toast */}
      {toast && (
        <div className={cn(
          "mx-6 mt-4 rounded-xl border px-4 py-3 text-sm flex items-center gap-2",
          toast.type === "success"
            ? "bg-emerald-500/10 border-emerald-500/20 text-emerald-400"
            : "bg-red-500/10 border-red-500/20 text-red-400"
        )}>
          {toast.type === "success" ? <CheckCircle2 className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
          {toast.message}
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-6 space-y-8">
        {/* MCP Servers Section */}
        <McpServersSection onToast={setToast} />

        {/* OAuth Integrations Section */}
        <div>
          <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider mb-4">OAuth Integrations</h2>
          {loading ? (
            <div className="flex items-center justify-center h-40">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : integrations.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-40 text-muted-foreground">
              <Plug className="h-8 w-8 mb-2" />
              <p className="text-sm">No integrations available</p>
              <p className="text-xs mt-1">Configure OAuth credentials in your .env file</p>
            </div>
          ) : (
            <div className="grid gap-4 max-w-3xl">
              {integrations.map((integration) => {
                const Icon = PROVIDER_ICONS[integration.icon] || Plug;
                const isConnecting = connecting === integration.provider;
                const isDisconnecting = disconnecting === integration.provider;

                return (
                  <div
                    key={integration.provider}
                    className={cn(
                      "rounded-xl border bg-card/80 backdrop-blur-sm p-5 transition-all",
                      integration.connected
                        ? "border-emerald-500/30"
                        : integration.available
                          ? "border-foreground/[0.06] hover:border-foreground/[0.12]"
                          : "border-foreground/[0.04] opacity-60"
                    )}
                  >
                    <div className="flex items-start justify-between">
                      <div className="flex items-start gap-4">
                        <div className={cn(
                          "flex h-12 w-12 items-center justify-center rounded-xl",
                          integration.connected ? "bg-emerald-500/10" : "bg-foreground/[0.06]"
                        )}>
                          <Icon className={cn(
                            "h-6 w-6",
                            integration.connected ? "text-emerald-400" : "text-muted-foreground"
                          )} />
                        </div>
                        <div>
                          <div className="flex items-center gap-2">
                            <h3 className="text-sm font-semibold">{integration.display_name}</h3>
                            {integration.connected && (
                              <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-400">
                                <CheckCircle2 className="h-2.5 w-2.5" />
                                Connected
                              </span>
                            )}
                          </div>
                          <p className="text-xs text-muted-foreground mt-0.5">{integration.description}</p>
                          {integration.connected && integration.account_label && (
                            <p className="text-xs text-emerald-400/80 mt-1.5">
                              Signed in as {integration.account_label}
                            </p>
                          )}
                          {!integration.available && !integration.connected && integration.auth_type !== "pat" && (
                            <p className="text-[10px] text-yellow-500/80 mt-1.5">
                              Not configured - set OAUTH_{integration.provider.toUpperCase()}_CLIENT_ID in .env
                            </p>
                          )}
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        {integration.connected ? (
                          <button
                            onClick={() => handleDisconnect(integration.provider)}
                            disabled={isDisconnecting}
                            className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium text-red-400 hover:bg-red-500/10 border border-red-500/20 hover:border-red-500/30 transition-all disabled:opacity-50"
                          >
                            {isDisconnecting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Unplug className="h-3 w-3" />}
                            Disconnect
                          </button>
                        ) : integration.auth_type === "pat" ? (
                          <span className="text-[10px] text-muted-foreground/40">Enter token below</span>
                        ) : integration.available ? (
                          <button
                            onClick={() => handleConnect(integration.provider)}
                            disabled={isConnecting}
                            className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium bg-primary text-primary-foreground hover:bg-primary/90 shadow-lg shadow-primary/20 transition-all disabled:opacity-50"
                          >
                            {isConnecting ? <Loader2 className="h-3 w-3 animate-spin" /> : <ExternalLink className="h-3 w-3" />}
                            Connect
                          </button>
                        ) : (
                          <span className="text-[10px] text-muted-foreground/40 px-3 py-2">Not available</span>
                        )}
                      </div>
                    </div>

                    {/* PAT token input for PAT-based providers (e.g. GitHub) */}
                    {integration.auth_type === "pat" && !integration.connected && (
                      <div className="mt-4 pt-4 border-t border-foreground/[0.06]">
                        <label className="text-[11px] font-medium text-muted-foreground mb-1.5 block">
                          Personal Access Token
                        </label>
                        <div className="flex items-center gap-2">
                          <div className="relative flex-1">
                            <input
                              type={patVisible ? "text" : "password"}
                              value={patToken}
                              onChange={(e) => setPatToken(e.target.value)}
                              placeholder="ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                              className="w-full rounded-lg border border-foreground/[0.08] bg-background/50 px-3 py-2 pr-9 text-sm font-mono outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all"
                              onKeyDown={(e) => e.key === "Enter" && handleSavePat(integration.provider)}
                            />
                            <button
                              type="button"
                              onClick={() => setPatVisible(!patVisible)}
                              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground/40 hover:text-muted-foreground transition-colors"
                            >
                              {patVisible ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                            </button>
                          </div>
                          <button
                            onClick={() => handleSavePat(integration.provider)}
                            disabled={!patToken.trim() || patSaving === integration.provider}
                            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-all"
                          >
                            {patSaving === integration.provider ? (
                              <Loader2 className="h-3 w-3 animate-spin" />
                            ) : (
                              <Save className="h-3 w-3" />
                            )}
                            Save
                          </button>
                        </div>
                        <p className="text-[10px] text-muted-foreground/50 mt-1.5">
                          Create a token at github.com/settings/tokens with repo, workflow, and read:org scopes
                        </p>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}


function McpServersSection({ onToast }: { onToast: (t: { type: "success" | "error"; message: string }) => void }) {
  const [servers, setServers] = useState<McpServerInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [addName, setAddName] = useState("");
  const [addUrl, setAddUrl] = useState("");
  const [adding, setAdding] = useState(false);
  const [expandedServer, setExpandedServer] = useState<number | null>(null);
  const [refreshing, setRefreshing] = useState<number | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);

  const loadServers = async () => {
    try {
      const { servers: list } = await api.getMcpServers();
      setServers(list);
    } catch {
      // API not ready
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadServers();
  }, []);

  const handleAdd = async () => {
    if (!addName.trim() || !addUrl.trim()) return;
    setAdding(true);
    try {
      const server = await api.addMcpServer(addName.trim(), addUrl.trim());
      setServers((prev) => [server, ...prev]);
      setAddName("");
      setAddUrl("");
      setShowAdd(false);
      setExpandedServer(server.id);
      onToast({ type: "success", message: `MCP Server "${server.name}" hinzugefuegt (${server.tools.length} Tools)` });
    } catch (e) {
      onToast({ type: "error", message: e instanceof Error ? e.message : "Verbindung fehlgeschlagen" });
    } finally {
      setAdding(false);
    }
  };

  const handleRefresh = async (id: number) => {
    setRefreshing(id);
    try {
      const updated = await api.refreshMcpServer(id);
      setServers((prev) => prev.map((s) => (s.id === id ? updated : s)));
      onToast({ type: "success", message: `Tools aktualisiert (${updated.tools.length} Tools)` });
    } catch (e) {
      onToast({ type: "error", message: e instanceof Error ? e.message : "Refresh fehlgeschlagen" });
    } finally {
      setRefreshing(null);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm("MCP Server entfernen? Agents muessen neu gestartet werden.")) return;
    setDeleting(id);
    try {
      await api.deleteMcpServer(id);
      setServers((prev) => prev.filter((s) => s.id !== id));
      onToast({ type: "success", message: "MCP Server entfernt" });
    } catch (e) {
      onToast({ type: "error", message: e instanceof Error ? e.message : "Fehler beim Entfernen" });
    } finally {
      setDeleting(null);
    }
  };

  const handleToggle = async (server: McpServerInfo) => {
    try {
      const updated = await api.updateMcpServer(server.id, { enabled: !server.enabled });
      setServers((prev) => prev.map((s) => (s.id === server.id ? updated : s)));
    } catch (e) {
      onToast({ type: "error", message: e instanceof Error ? e.message : "Fehler" });
    }
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">MCP Servers</h2>
        <button
          onClick={() => setShowAdd(!showAdd)}
          className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 shadow-lg shadow-primary/20 transition-all"
        >
          <Plus className="h-3 w-3" />
          MCP Server hinzufuegen
        </button>
      </div>

      {/* Add form */}
      {showAdd && (
        <div className="max-w-3xl mb-4 rounded-xl border border-primary/30 bg-card/80 backdrop-blur-sm p-5">
          <div className="space-y-3">
            <div>
              <label className="text-[11px] font-medium text-muted-foreground mb-1 block">Name</label>
              <input
                type="text"
                value={addName}
                onChange={(e) => setAddName(e.target.value)}
                placeholder="z.B. filesystem, github, slack..."
                className="w-full rounded-lg border border-foreground/[0.08] bg-background/50 px-3 py-2 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all"
              />
            </div>
            <div>
              <label className="text-[11px] font-medium text-muted-foreground mb-1 block">URL</label>
              <input
                type="text"
                value={addUrl}
                onChange={(e) => setAddUrl(e.target.value)}
                placeholder="http://localhost:8080/mcp"
                className="w-full rounded-lg border border-foreground/[0.08] bg-background/50 px-3 py-2 text-sm font-mono outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all"
                onKeyDown={(e) => e.key === "Enter" && handleAdd()}
              />
            </div>
            <div className="flex items-center gap-2 pt-1">
              <button
                onClick={handleAdd}
                disabled={adding || !addName.trim() || !addUrl.trim()}
                className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-all"
              >
                {adding ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plug className="h-3 w-3" />}
                {adding ? "Verbinde..." : "Verbinden & Tools laden"}
              </button>
              <button
                onClick={() => { setShowAdd(false); setAddName(""); setAddUrl(""); }}
                className="rounded-lg px-3 py-2 text-xs text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] transition-colors"
              >
                Abbrechen
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Server list */}
      {loading ? (
        <div className="flex items-center justify-center h-20">
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        </div>
      ) : servers.length === 0 && !showAdd ? (
        <div className="max-w-3xl rounded-xl border border-dashed border-foreground/[0.1] bg-card/30 p-10 text-center">
          <Globe className="h-8 w-8 text-muted-foreground/40 mx-auto mb-3" />
          <p className="text-sm text-muted-foreground mb-1">Keine MCP Server konfiguriert</p>
          <p className="text-xs text-muted-foreground/60">
            Verbinde externe MCP Server, damit deine Agents deren Tools nutzen koennen.
          </p>
        </div>
      ) : (
        <div className="grid gap-3 max-w-3xl">
          {servers.map((server) => {
            const isExpanded = expandedServer === server.id;
            const toolCount = server.tools?.length || 0;

            return (
              <div
                key={server.id}
                className={cn(
                  "rounded-xl border bg-card/80 backdrop-blur-sm transition-all overflow-hidden",
                  server.enabled ? "border-foreground/[0.06]" : "border-foreground/[0.04] opacity-60"
                )}
              >
                {/* Server header */}
                <div
                  className="flex items-center gap-3 p-4 cursor-pointer hover:bg-foreground/[0.02] transition-colors"
                  onClick={() => setExpandedServer(isExpanded ? null : server.id)}
                >
                  <ChevronRight className={cn(
                    "h-3.5 w-3.5 text-muted-foreground/50 transition-transform duration-150 shrink-0",
                    isExpanded && "rotate-90"
                  )} />

                  <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-violet-500/10 shrink-0">
                    <Globe className="h-5 w-5 text-violet-400" />
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <h3 className="text-sm font-semibold">{server.name}</h3>
                      <span className="inline-flex items-center gap-1 rounded-full bg-violet-500/10 px-2 py-0.5 text-[10px] font-medium text-violet-400">
                        <Wrench className="h-2.5 w-2.5" />
                        {toolCount} Tool{toolCount !== 1 && "s"}
                      </span>
                      {!server.enabled && (
                        <span className="text-[10px] text-muted-foreground/50">deaktiviert</span>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground/60 font-mono truncate mt-0.5">{server.url}</p>
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-1 shrink-0" onClick={(e) => e.stopPropagation()}>
                    <button
                      onClick={() => handleToggle(server)}
                      className={cn(
                        "flex h-7 w-7 items-center justify-center rounded-lg transition-colors",
                        server.enabled
                          ? "text-emerald-400 hover:bg-emerald-500/15"
                          : "text-muted-foreground/40 hover:bg-foreground/[0.06]"
                      )}
                      title={server.enabled ? "Deaktivieren" : "Aktivieren"}
                    >
                      <Power className="h-3.5 w-3.5" />
                    </button>
                    <button
                      onClick={() => handleRefresh(server.id)}
                      disabled={refreshing === server.id}
                      className="flex h-7 w-7 items-center justify-center rounded-lg text-muted-foreground/40 hover:text-foreground hover:bg-foreground/[0.06] transition-colors"
                      title="Tools neu laden"
                    >
                      {refreshing === server.id ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <RefreshCw className="h-3.5 w-3.5" />
                      )}
                    </button>
                    <button
                      onClick={() => handleDelete(server.id)}
                      disabled={deleting === server.id}
                      className="flex h-7 w-7 items-center justify-center rounded-lg text-muted-foreground/40 hover:text-red-400 hover:bg-red-500/15 transition-colors"
                      title="Entfernen"
                    >
                      {deleting === server.id ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Trash2 className="h-3.5 w-3.5" />
                      )}
                    </button>
                  </div>
                </div>

                {/* Tools list (expanded) */}
                {isExpanded && (
                  <div className="border-t border-foreground/[0.06] px-4 py-3">
                    {toolCount === 0 ? (
                      <p className="text-xs text-muted-foreground/50 py-2">Keine Tools gefunden</p>
                    ) : (
                      <div className="space-y-1.5">
                        {server.tools.map((tool: McpTool) => (
                          <div
                            key={tool.name}
                            className="flex items-start gap-2.5 rounded-lg bg-foreground/[0.02] border border-foreground/[0.04] px-3 py-2"
                          >
                            <Wrench className="h-3.5 w-3.5 text-violet-400 shrink-0 mt-0.5" />
                            <div className="min-w-0">
                              <span className="text-[12px] font-medium font-mono text-foreground">{tool.name}</span>
                              {tool.description && (
                                <p className="text-[11px] text-muted-foreground/60 mt-0.5 line-clamp-2">{tool.description}</p>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                    <p className="text-[10px] text-muted-foreground/40 mt-3">
                      Agents muessen neu gestartet werden, um neue MCP Server zu nutzen.
                    </p>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
