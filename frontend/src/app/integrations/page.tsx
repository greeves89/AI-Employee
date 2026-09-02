"use client";

import { useState, useEffect } from "react";
import {
  Plug, Mail, Cloud, Smartphone, CheckCircle2,
  AlertCircle, Loader2, Unplug, ExternalLink, RefreshCw,
  Plus, Trash2, ChevronRight, Wrench, Globe, Power,
  Eye, EyeOff, Save, Users, Copy, Info, Pencil, KeyRound, PlugZap,
  ShieldCheck, LogIn, Play,
} from "lucide-react";
import { Github } from "@/components/icons/github";
import { Header } from "@/components/layout/header";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import { useConfirm } from "@/components/ui/dialog-provider";
import type { Integration } from "@/lib/types";
import type { McpServerInfo, McpTool, McpAgentHealth, McpAgentHealthEntry, McpToolCallResult } from "@/lib/api";
import { useSearchParams } from "next/navigation";

const PROVIDER_ICONS: Record<string, typeof Mail> = {
  Mail,
  Cloud,
  Smartphone,
  Github,
};

function formatRelativeCheckedAt(value: string | null): string {
  if (!value) return "noch nicht geprüft";
  const checkedAt = new Date(value).getTime();
  if (Number.isNaN(checkedAt)) return "Zeitpunkt unbekannt";

  const diffMs = Math.max(0, Date.now() - checkedAt);
  const diffMinutes = Math.floor(diffMs / 60000);
  if (diffMinutes < 1) return "gerade geprüft";
  if (diffMinutes < 60) return `geprüft vor ${diffMinutes} Min`;

  const diffHours = Math.floor(diffMinutes / 60);
  if (diffHours < 24) return `geprüft vor ${diffHours} Std`;

  const diffDays = Math.floor(diffHours / 24);
  return `geprüft vor ${diffDays} Tag${diffDays === 1 ? "" : "en"}`;
}

// This status reflects the ORCHESTRATOR → MCP-server discovery check only. The
// agent container's view (what actually fails a tool call, e.g. a 401 that the
// orchestrator path never sees) is a separate #425 Phase-2 signal. The tooltip
// spells that out so a green "erreichbar" is not misread as "agents can use it".
const MCP_HEALTH_ORCH_ONLY =
  "Server-seitige Discovery-Prüfung (Orchestrator → MCP-Server). " +
  "Sie spiegelt nicht die Agent-Sicht wider — ein Server kann für Agent-Aufrufe " +
  "trotzdem fehlschlagen (z. B. 401). Agent-Perspektive folgt in #425 Phase 2.";

function formatMcpHealth(server: McpServerInfo): { ok: boolean; label: string; className: string; title: string } {
  const checked = formatRelativeCheckedAt(server.last_checked_at);
  if (!server.last_status) {
    return {
      ok: false,
      label: `Status unbekannt · ${checked}`,
      className: "text-muted-foreground/60",
      title: "Noch keine Discovery-Prüfung durch den Orchestrator.",
    };
  }

  if (server.last_status === "ok") {
    return {
      ok: true,
      label: `erreichbar · ${checked}`,
      className: "text-emerald-400",
      title: MCP_HEALTH_ORCH_ONLY,
    };
  }

  if (server.last_status === "needs_oauth") {
    return {
      ok: false,
      label: `OAuth erforderlich — auf „Verbinden“ klicken · ${checked}`,
      className: "text-amber-400",
      title: "Der Server ist OAuth-geschützt und wurde angelegt, aber noch nicht verbunden. Auf „Verbinden“ klicken, um die Autorisierung zu starten.",
    };
  }

  const fallback = {
    auth_failed: "Authentifizierung fehlgeschlagen",
    unreachable: "nicht erreichbar",
    protocol_error: "Protokollfehler",
  }[server.last_status] ?? "Fehler";

  return {
    ok: false,
    label: `${server.last_error || fallback} · ${checked}`,
    className: server.last_status === "auth_failed" ? "text-amber-400" : "text-red-400",
    title: MCP_HEALTH_ORCH_ONLY,
  };
}

// Agent-side view (#425 Phase 2): what each running agent's `claude mcp list`
// reports for this server, rolled up across agents. Distinct from the
// orchestrator discovery check above — an agent can fail a server (e.g. a 401
// on its per-agent token) that the orchestrator reaches fine.
function formatAgentHealth(
  entry: McpAgentHealthEntry,
): { label: string; className: string; ok: boolean } {
  const total = entry.connected + entry.failed + entry.needs_auth + entry.unknown;
  if (!entry.agent_status || total === 0) {
    return { label: "Agent-Sicht: keine Daten", className: "text-muted-foreground/50", ok: false };
  }
  if (entry.agent_status === "connected") {
    return {
      label: `Agent-Sicht: verbunden (${entry.connected}/${total} Agents)`,
      className: "text-emerald-400",
      ok: true,
    };
  }
  if (entry.agent_status === "needs_auth") {
    return {
      label: `Agent-Sicht: Authentifizierung nötig (${entry.needs_auth}/${total})`,
      className: "text-amber-400",
      ok: false,
    };
  }
  if (entry.agent_status === "failed") {
    return {
      label: `Agent-Sicht: nicht erreichbar (${entry.failed}/${total})`,
      className: "text-red-400",
      ok: false,
    };
  }
  return { label: "Agent-Sicht: unbekannt", className: "text-muted-foreground/60", ok: false };
}

// The signal #425 exists to surface: the orchestrator's own check says a server
// is reachable, but the agents that actually call it disagree.
function hasOrchAgentDisagreement(server: McpServerInfo, entry: McpAgentHealthEntry | undefined): boolean {
  if (!entry || !entry.agent_status) return false;
  return server.last_status === "ok" && (entry.agent_status === "failed" || entry.agent_status === "needs_auth");
}

// Admin diagnostic tool runner (#414): invoke a single MCP tool by hand from the
// server's tool list and show the raw JSON-RPC response. The inputs are generated
// from the tool's stored inputSchema; the call is audit-logged server-side.
function McpToolRunner({ serverId, tool }: { serverId: number; tool: McpTool }) {
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<McpToolCallResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const schema = (tool.inputSchema ?? {}) as {
    properties?: Record<string, { type?: string; description?: string }>;
    required?: string[];
  };
  const properties = schema.properties ?? {};
  const required = new Set(schema.required ?? []);
  const propNames = Object.keys(properties);

  const run = async () => {
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const args: Record<string, unknown> = {};
      for (const [key, raw] of Object.entries(values)) {
        if (raw === "") continue;
        const t = properties[key]?.type;
        if (t === "number" || t === "integer") {
          const n = Number(raw);
          args[key] = Number.isNaN(n) ? raw : n;
        } else if (t === "boolean") {
          args[key] = raw === "true";
        } else {
          args[key] = raw;
        }
      }
      setResult(await api.callMcpTool(serverId, tool.name, args));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Aufruf fehlgeschlagen");
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="mt-1.5">
      <button
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1 text-[10px] text-violet-400 hover:text-violet-300"
      >
        <Play className="h-3 w-3" /> {open ? "Schliessen" : "Tool testen"}
      </button>
      {open && (
        <div className="mt-1.5 space-y-2 rounded-md bg-foreground/[0.02] border border-foreground/[0.04] p-2.5">
          {propNames.length === 0 ? (
            <p className="text-[10px] text-muted-foreground/50">Keine Parameter.</p>
          ) : (
            propNames.map((key) => {
              const prop = properties[key] || {};
              return (
                <div key={key} className="space-y-0.5">
                  <label className="block text-[10px] font-mono text-muted-foreground/70">
                    {key}
                    {required.has(key) && <span className="text-red-400">*</span>}
                    {prop.type && <span className="text-muted-foreground/40"> : {prop.type}</span>}
                  </label>
                  <input
                    type="text"
                    value={values[key] ?? ""}
                    onChange={(e) => setValues((v) => ({ ...v, [key]: e.target.value }))}
                    placeholder={prop.description || ""}
                    className="w-full rounded-md bg-background border border-foreground/10 px-2 py-1 text-[11px] font-mono focus:outline-none focus:border-violet-400/40"
                  />
                </div>
              );
            })
          )}
          <button
            onClick={run}
            disabled={running}
            className="inline-flex items-center gap-1.5 rounded-md bg-violet-500/15 text-violet-300 px-2.5 py-1 text-[11px] font-medium hover:bg-violet-500/25 disabled:opacity-50"
          >
            {running ? <Loader2 className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />}
            Ausfuehren
          </button>
          {error && <p className="text-[10px] text-red-400">{error}</p>}
          {result && (
            <pre
              className={cn(
                "mt-1 max-h-56 overflow-auto rounded-md border p-2 text-[10px] font-mono whitespace-pre-wrap break-all",
                result.is_error
                  ? "border-red-500/30 bg-red-500/[0.06] text-red-300"
                  : "border-emerald-500/20 bg-emerald-500/[0.04] text-foreground/80",
              )}
            >
              {JSON.stringify(result.result, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

export default function IntegrationsPage() {
  const confirm = useConfirm();
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [loading, setLoading] = useState(true);
  const [connecting, setConnecting] = useState<string | null>(null);
  const [disconnecting, setDisconnecting] = useState<string | null>(null);
  const [toast, setToast] = useState<{ type: "success" | "error"; message: string } | null>(null);
  const [patToken, setPatToken] = useState("");
  const [patBaseUrl, setPatBaseUrl] = useState("");
  const [patSelfHosted, setPatSelfHosted] = useState(false);
  const [patSaving, setPatSaving] = useState<string | null>(null);
  const [patVisible, setPatVisible] = useState(false);
  const [setupExpanded, setSetupExpanded] = useState<string | null>(null);
  const searchParams = useSearchParams();

  const redirectUrl = typeof window !== "undefined"
    ? `${window.location.origin}/api/v1/integrations/microsoft/callback`
    : "/api/v1/integrations/microsoft/callback";

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
    const ok = await confirm({
      title: `Disconnect ${provider}?`,
      message: "Agents using this integration will lose access.",
      variant: "destructive",
      confirmLabel: "Disconnect",
    });
    if (!ok) return;
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
      const baseUrl = patSelfHosted ? patBaseUrl.trim() : "";
      const result = await api.savePatToken(provider, patToken.trim(), baseUrl);
      setToast({ type: "success", message: `Connected to ${provider} as ${result.account_label || "unknown"}` });
      setPatToken("");
      setPatBaseUrl("");
      setPatSelfHosted(false);
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
                            {integration.per_user && (
                              <span className="inline-flex items-center gap-1 rounded-full bg-blue-500/10 px-2 py-0.5 text-[10px] font-medium text-blue-400 border border-blue-500/20">
                                <Users className="h-2.5 w-2.5" />
                                Per user
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
                              Not configured — set OAUTH_{integration.provider.toUpperCase()}_CLIENT_ID in settings
                            </p>
                          )}
                          {integration.auth_type === "oauth" && integration.scopes?.trim() && (() => {
                            const scopeList = integration.scopes.split(/\s+/).filter(Boolean);
                            return (
                              <details className="mt-1.5 group">
                                <summary className="cursor-pointer list-none text-[10px] text-muted-foreground/60 hover:text-muted-foreground/90 select-none">
                                  {integration.connected ? "Erteilte" : "Angeforderte"} Berechtigungen ({scopeList.length})
                                  <span className="ml-1 text-muted-foreground/40 group-open:hidden">· anzeigen</span>
                                </summary>
                                <div className="mt-1.5 flex flex-wrap gap-1">
                                  {scopeList.map((s) => (
                                    <span
                                      key={s}
                                      className="inline-block rounded bg-foreground/[0.05] border border-foreground/[0.06] px-1.5 py-0.5 text-[10px] font-mono text-muted-foreground/70"
                                    >
                                      {s}
                                    </span>
                                  ))}
                                </div>
                                {!integration.connected && (
                                  <p className="mt-1 text-[10px] text-muted-foreground/40">
                                    Anpassbar via OAUTH_{integration.provider.toUpperCase()}_SCOPES.
                                  </p>
                                )}
                              </details>
                            );
                          })()}
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

                    {/* Microsoft 365 admin setup guide */}
                    {integration.provider === "microsoft" && !integration.available && !integration.connected && (
                      <div className="mt-4 pt-4 border-t border-foreground/[0.06]">
                        <button
                          onClick={() => setSetupExpanded(setupExpanded === "microsoft" ? null : "microsoft")}
                          className="flex items-center gap-2 text-[11px] font-medium text-blue-400 hover:text-blue-300 transition-colors"
                        >
                          <Info className="h-3.5 w-3.5" />
                          How to set up Microsoft 365 (Azure App Registration)
                          <ChevronRight className={cn("h-3 w-3 transition-transform", setupExpanded === "microsoft" && "rotate-90")} />
                        </button>
                        {setupExpanded === "microsoft" && (
                          <div className="mt-3 rounded-lg border border-blue-500/20 bg-blue-500/5 p-4 space-y-3 text-xs text-muted-foreground">
                            <ol className="list-decimal list-inside space-y-2">
                              <li>Open <strong className="text-foreground">portal.azure.com</strong> → Azure Active Directory → App registrations → New registration</li>
                              <li>Set Redirect URI (Web) to:
                                <div className="mt-1 flex items-center gap-2 rounded-md border border-foreground/10 bg-background/50 px-3 py-1.5 font-mono text-[10px]">
                                  <span className="flex-1 text-emerald-400 break-all">{redirectUrl}</span>
                                  <button
                                    onClick={() => navigator.clipboard.writeText(redirectUrl)}
                                    className="text-muted-foreground/40 hover:text-muted-foreground transition-colors flex-shrink-0"
                                  >
                                    <Copy className="h-3 w-3" />
                                  </button>
                                </div>
                              </li>
                              <li>Under <strong className="text-foreground">API Permissions</strong> → Add permission → Microsoft Graph → Delegated:<br />
                                <span className="text-[10px] font-mono text-blue-300/80">User.Read, Mail.ReadWrite, Mail.Send, Calendars.ReadWrite, Files.ReadWrite, Chat.ReadWrite, Chat.ReadBasic, ChannelMessage.Read.All, ChannelMessage.Send, Team.ReadBasic.All, Tasks.ReadWrite, Contacts.ReadWrite, People.Read, offline_access</span>
                                <p className="mt-1 text-amber-400/80">→ Then click <strong>&quot;Grant admin consent&quot;</strong></p>
                              </li>
                              <li>Under <strong className="text-foreground">Certificates &amp; Secrets</strong> create a new Client Secret</li>
                              <li>Enter <strong className="text-foreground">Client ID &amp; Secret</strong> in <strong className="text-foreground">Settings → OAuth → Microsoft 365</strong></li>
                            </ol>
                            <p className="text-[10px] text-muted-foreground/60">
                              Admin setup is done once. Each user then connects their own account here — tokens are stored per user, not shared.
                            </p>
                          </div>
                        )}
                      </div>
                    )}

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
                              placeholder="ghp_… (dein Personal Access Token)"
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

                        {integration.provider === "github" && (
                          <div className="mt-3">
                            <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground cursor-pointer select-none">
                              <input
                                type="checkbox"
                                checked={patSelfHosted}
                                onChange={(e) => setPatSelfHosted(e.target.checked)}
                                className="h-3 w-3"
                              />
                              Self-hosted (GitHub Enterprise Server)
                            </label>
                            {patSelfHosted && (
                              <input
                                type="text"
                                value={patBaseUrl}
                                onChange={(e) => setPatBaseUrl(e.target.value)}
                                placeholder="https://ghe.example.com"
                                className="mt-1.5 w-full rounded-lg border border-foreground/[0.08] bg-background/50 px-3 py-2 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all"
                              />
                            )}
                          </div>
                        )}
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
  const confirm = useConfirm();
  const [servers, setServers] = useState<McpServerInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);  // null = add mode
  const [addName, setAddName] = useState("");
  const [addUrl, setAddUrl] = useState("");
  const [addBearer, setAddBearer] = useState("");
  const [addHeaders, setAddHeaders] = useState("");  // one "Name: value" per line
  const [removeToken, setRemoveToken] = useState(false);
  // Der Server hat wegen einer PRIVATEN Adresse abgelehnt — erst dann ist der
  // Haken sinnvoll. Bei Loopback oder Metadatenpunkt lehnt er auch mit Haken ab;
  // ihn dort anzubieten waere ein leeres Versprechen.
  const [privateBlocked, setPrivateBlocked] = useState(false);
  const [allowPrivate, setAllowPrivate] = useState(false);
  const [removeHeaders, setRemoveHeaders] = useState(false);
  // Eigene Rueckkehr-Adresse fuer den OAuth-Tanz dieses einen Servers.
  // Ohne dieses Feld waere der Wert nur per API zu setzen — die Einstellung
  // existierte, aber niemand kaeme in der Oberflaeche an sie heran.
  const [addCallbackBase, setAddCallbackBase] = useState("");
  const [adding, setAdding] = useState(false);
  const [probing, setProbing] = useState(false);
  const [probeResult, setProbeResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [expandedServer, setExpandedServer] = useState<number | null>(null);
  const [refreshing, setRefreshing] = useState<number | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);
  const [agentHealth, setAgentHealth] = useState<McpAgentHealth | null>(null);
  const [checkingAgents, setCheckingAgents] = useState(false);
  const [oauthBusy, setOauthBusy] = useState<number | null>(null);

  const editingServer = editingId != null ? servers.find((s) => s.id === editingId) ?? null : null;

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

  // Surface the result of the OAuth browser round-trip (#426): the callback
  // redirects back to /integrations?mcp_oauth=connected|error, then we clean the URL.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const result = params.get("mcp_oauth");
    if (!result) return;
    const name = params.get("server");
    if (result === "connected") {
      onToast({ type: "success", message: `OAuth verbunden${name ? `: ${name}` : ""}` });
      loadServers();
    } else {
      onToast({ type: "error", message: `OAuth fehlgeschlagen: ${params.get("detail") || "unbekannt"}` });
    }
    const url = new URL(window.location.href);
    ["mcp_oauth", "server", "detail"].forEach((k) => url.searchParams.delete(k));
    window.history.replaceState({}, "", url.toString());
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  // Discover OAuth config (once) then start the authorization_code flow, sending
  // the browser to the authorization server. Returns via the callback → /integrations.
  const handleOAuthConnect = async (server: McpServerInfo) => {
    setOauthBusy(server.id);
    try {
      if (!server.oauth_enabled || !server.oauth_client_id) {
        const disc = await api.discoverMcpOAuth(server.id);
        if (disc.needs_client_id) {
          const clientId = window.prompt(
            "Dieser Authorization-Server bietet keine dynamische Registrierung. " +
            "Bitte die vorab registrierte client_id eingeben:",
          );
          if (!clientId?.trim()) {
            setOauthBusy(null);
            return;
          }
          await api.discoverMcpOAuth(server.id, clientId.trim());
        }
      }
      const { authorization_url } = await api.connectMcpOAuth(server.id);
      window.location.href = authorization_url;
    } catch (e) {
      onToast({ type: "error", message: e instanceof Error ? e.message : "OAuth-Start fehlgeschlagen" });
      setOauthBusy(null);
    }
  };

  const parseHeaderLines = (text: string): Record<string, string> => {
    const out: Record<string, string> = {};
    for (const line of text.split("\n")) {
      const t = line.trim();
      const i = t.indexOf(":");
      if (i <= 0) continue;
      const k = t.slice(0, i).trim();
      if (k) out[k] = t.slice(i + 1).trim();
    }
    return out;
  };

  const resetForm = () => {
    setAddName("");
    setAddUrl("");
    setAddBearer("");
    setAddHeaders("");
    setRemoveToken(false);
    setRemoveHeaders(false);
    setPrivateBlocked(false);
    setAllowPrivate(false);
    setProbeResult(null);
  };

  const openAdd = () => {
    setEditingId(null);
    resetForm();
    setShowForm(true);
  };

  const openEdit = (server: McpServerInfo) => {
    setEditingId(server.id);
    resetForm();
    setAddName(server.name);
    setAddUrl(server.url);
    setAddCallbackBase(server.oauth_callback_base_url || "");
    setShowForm(true);
  };

  const closeForm = () => {
    setShowForm(false);
    setEditingId(null);
    resetForm();
  };

  const handleSubmit = () => (editingId == null ? handleAdd() : handleSave());

  const handleAdd = async () => {
    if (!addName.trim() || !addUrl.trim()) return;
    setAdding(true);
    try {
      const headers = parseHeaderLines(addHeaders);
      const server = await api.addMcpServer(
        addName.trim(), addUrl.trim(), addBearer.trim() || undefined,
        Object.keys(headers).length ? headers : undefined,
        allowPrivate,
      );
      setServers((prev) => [server, ...prev]);
      closeForm();
      setExpandedServer(server.id);
      onToast({ type: "success", message: `MCP Server "${server.name}" hinzugefuegt (${server.tools.length} Tools)` });
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Verbindung fehlgeschlagen";
      // Nur bei einer PRIVATEN Adresse anbieten. Loopback und Metadatenpunkt
      // lehnt der Server auch mit Haken ab — dort waere das Angebot eine Luege.
      if (/private address/i.test(msg)) setPrivateBlocked(true);
      onToast({ type: "error", message: msg });
    } finally {
      setAdding(false);
    }
  };

  const handleSave = async () => {
    if (editingId == null || !addName.trim() || !addUrl.trim()) return;
    setAdding(true);
    try {
      const data: {
        name?: string; url?: string; bearer_token?: string;
        headers?: Record<string, string>; oauth_callback_base_url?: string;
      } = {
        name: addName.trim(),
        url: addUrl.trim(),
      };
      // "" loescht den Wert, weglassen laesst ihn unveraendert (PATCH-Semantik).
      // Deshalb nur senden, wenn er sich gegenueber dem Gespeicherten geaendert hat.
      const basisVorher = editingServer?.oauth_callback_base_url || "";
      if (addCallbackBase.trim() !== basisVorher) {
        data.oauth_callback_base_url = addCallbackBase.trim();
      }
      // Token: only send when the user typed one, or explicitly asked to remove it
      // (""). Leaving it untouched keeps the stored credential (PATCH: None = unchanged).
      if (removeToken) data.bearer_token = "";
      else if (addBearer.trim()) data.bearer_token = addBearer.trim();
      // Headers: same rule — {} clears, omitting leaves them unchanged.
      if (removeHeaders) data.headers = {};
      else {
        const headers = parseHeaderLines(addHeaders);
        if (Object.keys(headers).length) data.headers = headers;
      }
      const updated = await api.updateMcpServer(editingId, data);
      setServers((prev) => prev.map((s) => (s.id === editingId ? updated : s)));
      closeForm();
      onToast({ type: "success", message: `MCP Server "${updated.name}" aktualisiert` });
    } catch (e) {
      onToast({ type: "error", message: e instanceof Error ? e.message : "Speichern fehlgeschlagen" });
    } finally {
      setAdding(false);
    }
  };

  const handleProbe = async () => {
    if (!addUrl.trim()) return;
    setProbing(true);
    setProbeResult(null);
    try {
      const headers = parseHeaderLines(addHeaders);
      const res = await api.probeMcpServer(
        addName.trim() || "probe", addUrl.trim(),
        addBearer.trim() || undefined,
        Object.keys(headers).length ? headers : undefined,
        allowPrivate,
      );
      setProbeResult({
        ok: true,
        message: `Verbindung OK — ${res.tool_count} Tool${res.tool_count !== 1 ? "s" : ""} gefunden`,
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Verbindung fehlgeschlagen";
      if (/private address/i.test(msg)) setPrivateBlocked(true);
      setProbeResult({ ok: false, message: msg });
    } finally {
      setProbing(false);
    }
  };

  const handleRefresh = async (id: number) => {
    setRefreshing(id);
    try {
      const updated = await api.refreshMcpServer(id);
      setServers((prev) => prev.map((s) => (s.id === id ? updated : s)));
      onToast({ type: "success", message: `Tools aktualisiert (${updated.tools.length} Tools)` });
    } catch (e) {
      await loadServers();
      onToast({ type: "error", message: e instanceof Error ? e.message : "Refresh fehlgeschlagen" });
    } finally {
      setRefreshing(null);
    }
  };

  const handleDelete = async (id: number) => {
    const ok = await confirm({
      title: "MCP Server entfernen?",
      message: "Agents müssen neu gestartet werden um die Änderung zu übernehmen.",
      variant: "destructive",
      confirmLabel: "Entfernen",
    });
    if (!ok) return;
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

  const handleCheckAgents = async () => {
    setCheckingAgents(true);
    try {
      const health = await api.getMcpAgentHealth();
      setAgentHealth(health);
      onToast({
        type: "success",
        message: `Agent-Sicht geprüft (${health.agents_checked}/${health.agents_total} Agents)`,
      });
    } catch (e) {
      onToast({ type: "error", message: e instanceof Error ? e.message : "Agent-Prüfung fehlgeschlagen" });
    } finally {
      setCheckingAgents(false);
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
        <div className="flex items-center gap-2">
          <button
            onClick={handleCheckAgents}
            disabled={checkingAgents}
            title="Führt in jedem laufenden Agent-Container `claude mcp list` aus und zeigt, wie die Agents die Server sehen (unabhängig von der Orchestrator-Prüfung)."
            className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-foreground border border-foreground/[0.1] hover:bg-foreground/[0.06] disabled:opacity-50 transition-all"
          >
            {checkingAgents ? <Loader2 className="h-3 w-3 animate-spin" /> : <Users className="h-3 w-3" />}
            Agent-Sicht prüfen
          </button>
          <button
            onClick={() => (showForm ? closeForm() : openAdd())}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 shadow-lg shadow-primary/20 transition-all"
          >
            <Plus className="h-3 w-3" />
            MCP Server hinzufuegen
          </button>
        </div>
      </div>

      {/* Add / edit form */}
      {showForm && (
        <div className="max-w-3xl mb-4 rounded-xl border border-primary/30 bg-card/80 backdrop-blur-sm p-5">
          <div className="space-y-3">
            <p className="text-xs font-semibold text-foreground">
              {editingId == null ? "Neuen MCP Server hinzufuegen" : `„${editingServer?.name ?? ""}" bearbeiten`}
            </p>
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
                onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
              />
            </div>
            <div>
              <label className="text-[11px] font-medium text-muted-foreground mb-1 block">
                Bearer Token{" "}
                <span className="text-muted-foreground/40">
                  {editingServer?.has_auth ? "(leer lassen = unverändert)" : "(optional)"}
                </span>
              </label>
              <input
                type="password"
                value={addBearer}
                onChange={(e) => setAddBearer(e.target.value)}
                disabled={removeToken}
                placeholder={editingServer?.has_auth ? "unverändert" : "für geschützte MCP-Server (Authorization: Bearer …)"}
                className="w-full rounded-lg border border-foreground/[0.08] bg-background/50 px-3 py-2 text-sm font-mono outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all disabled:opacity-40"
                onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
              />
              {editingServer?.has_auth && (
                <label className="mt-1.5 flex items-center gap-1.5 text-[10px] text-muted-foreground/70 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={removeToken}
                    onChange={(e) => { setRemoveToken(e.target.checked); if (e.target.checked) setAddBearer(""); }}
                    className="h-3 w-3 rounded border-foreground/20 accent-red-500"
                  />
                  Gespeicherten Token entfernen
                </label>
              )}
            </div>
            <div>
              <label className="text-[11px] font-medium text-muted-foreground mb-1 block">
                Eigene Header{" "}
                <span className="text-muted-foreground/40">
                  {editingServer?.has_headers
                    ? "(leer lassen = unverändert — ein Name: Wert pro Zeile)"
                    : "(optional — ein Name: Wert pro Zeile)"}
                </span>
              </label>
              <textarea
                value={addHeaders}
                onChange={(e) => setAddHeaders(e.target.value)}
                disabled={removeHeaders}
                rows={2}
                placeholder={editingServer?.has_headers ? "unverändert" : "x-api-key: dein-schlüssel\nx-consumer-api-key: …"}
                className="w-full rounded-lg border border-foreground/[0.08] bg-background/50 px-3 py-2 text-sm font-mono outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all resize-y disabled:opacity-40"
              />
              {editingServer?.has_headers && (
                <label className="mt-1.5 flex items-center gap-1.5 text-[10px] text-muted-foreground/70 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={removeHeaders}
                    onChange={(e) => { setRemoveHeaders(e.target.checked); if (e.target.checked) setAddHeaders(""); }}
                    className="h-3 w-3 rounded border-foreground/20 accent-red-500"
                  />
                  Gespeicherte Header entfernen
                </label>
              )}
              <p className="text-[10px] text-muted-foreground/50 mt-1">
                Für Server, die statt „Bearer" einen eigenen Header erwarten (z. B. Composio: x-consumer-api-key).
              </p>
            </div>
            {editingId != null && (
              <div>
                <label className="text-[11px] font-medium text-muted-foreground mb-1 block">
                  Eigene OAuth-Rückkehr-Adresse{" "}
                  <span className="text-muted-foreground/40">(optional — leer = die dieser Installation)</span>
                </label>
                <input
                  type="url"
                  value={addCallbackBase}
                  onChange={(e) => setAddCallbackBase(e.target.value)}
                  placeholder="https://beispiel.invalid"
                  className="w-full rounded-lg border border-foreground/[0.08] bg-background/50 px-3 py-2 text-sm font-mono outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all"
                />
                <p className="text-[10px] text-muted-foreground/50 mt-1">
                  Nur nötig, wenn dieser Anbieter eine enge Liste erlaubter Rückkehr-Adressen führt.
                  Sonst musste bisher die Adresse der ganzen Installation umgestellt werden — was
                  gleichzeitig Anmeldung und Kalender-Anbindung umgehängt hat.
                  {editingServer?.oauth_client_id && (
                    <>
                      {" "}Achtung: Beim Ändern wird die automatische Anbieter-Registrierung verworfen
                      und beim nächsten Verbinden neu angelegt.
                    </>
                  )}
                </p>
              </div>
            )}
            {probeResult && (
              <div className={cn(
                "rounded-lg border px-3 py-2 text-[11px] flex items-center gap-2",
                probeResult.ok
                  ? "bg-emerald-500/10 border-emerald-500/20 text-emerald-400"
                  : "bg-red-500/10 border-red-500/20 text-red-400"
              )}>
                {probeResult.ok ? <CheckCircle2 className="h-3.5 w-3.5 shrink-0" /> : <AlertCircle className="h-3.5 w-3.5 shrink-0" />}
                {probeResult.message}
              </div>
            )}
            {privateBlocked && (
              // Erscheint erst NACH einer Ablehnung wegen privater Adresse — also
              // genau dann, wenn er hilft, und nicht als Dauerangebot.
              <div className="rounded-lg border border-amber-500/25 bg-amber-500/10 px-3 py-2.5 space-y-1.5">
                <label className="flex items-start gap-2 text-[11px] text-amber-200/90 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={allowPrivate}
                    onChange={(e) => setAllowPrivate(e.target.checked)}
                    className="mt-0.5 h-3.5 w-3.5 shrink-0 rounded border-border accent-amber-500"
                  />
                  <span>
                    <strong>Interne Adresse zulassen</strong> — der Server steht im
                    eigenen Netz und ist von aussen nicht erreichbar.
                  </span>
                </label>
                <p className="text-[10px] text-amber-200/60 pl-5">
                  Gilt nur für diesen Eintrag. Adressen, hinter denen nie ein
                  MCP-Server steht, bleiben gesperrt: dieser Server selbst
                  (127.0.0.1) und der Metadatenpunkt der Cloud (169.254.169.254).
                </p>
              </div>
            )}
            {editingServer?.has_auth && !addBearer.trim() && !removeToken && (
              <p className="text-[10px] text-muted-foreground/50">
                Hinweis: Der Verbindungstest nutzt nur ein hier eingegebenes Token — der gespeicherte Token kann dafür nicht ausgelesen werden.
              </p>
            )}
            <div className="flex items-center gap-2 pt-1">
              <button
                onClick={handleSubmit}
                disabled={adding || !addName.trim() || !addUrl.trim()}
                className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-all"
              >
                {adding ? <Loader2 className="h-3 w-3 animate-spin" /> : editingId == null ? <Plug className="h-3 w-3" /> : <Save className="h-3 w-3" />}
                {adding ? "Speichere..." : editingId == null ? "Verbinden & Tools laden" : "Speichern"}
              </button>
              <button
                onClick={handleProbe}
                disabled={probing || !addUrl.trim()}
                className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium text-foreground border border-foreground/[0.1] hover:bg-foreground/[0.06] disabled:opacity-50 transition-all"
              >
                {probing ? <Loader2 className="h-3 w-3 animate-spin" /> : <PlugZap className="h-3 w-3" />}
                Verbindung testen
              </button>
              <button
                onClick={closeForm}
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
      ) : servers.length === 0 && !showForm ? (
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
            const health = formatMcpHealth(server);
            const agentEntry = agentHealth?.servers[String(server.id)];
            const agentView = agentEntry ? formatAgentHealth(agentEntry) : null;
            const disagreement = hasOrchAgentDisagreement(server, agentEntry);

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
                      {server.has_auth && (
                        <span
                          title="Zugangs-Token gespeichert"
                          className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-400"
                        >
                          <KeyRound className="h-2.5 w-2.5" />
                          Token
                        </span>
                      )}
                      {server.oauth_enabled && (
                        <span
                          title={server.oauth_connected
                            ? "OAuth verbunden — Access-Token wird serverseitig automatisch erneuert"
                            : "OAuth konfiguriert, aber noch nicht verbunden — auf „Verbinden“ klicken"}
                          className={cn(
                            "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium",
                            server.oauth_connected
                              ? "bg-emerald-500/10 text-emerald-400"
                              : "bg-amber-500/10 text-amber-400"
                          )}
                        >
                          <ShieldCheck className="h-2.5 w-2.5" />
                          {server.oauth_connected ? "OAuth" : "OAuth offen"}
                        </span>
                      )}
                      <span className="inline-flex items-center gap-1 rounded-full bg-violet-500/10 px-2 py-0.5 text-[10px] font-medium text-violet-400">
                        <Wrench className="h-2.5 w-2.5" />
                        {toolCount} Tool{toolCount !== 1 && "s"} entdeckt
                      </span>
                      {!server.enabled && (
                        <span className="text-[10px] text-muted-foreground/50">deaktiviert</span>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground/60 font-mono truncate mt-0.5">{server.url}</p>
                    <div className={cn("mt-1 flex items-center gap-1.5 text-[11px]", health.className)} title={health.title}>
                      {health.ok ? <CheckCircle2 className="h-3 w-3 shrink-0" /> : <AlertCircle className="h-3 w-3 shrink-0" />}
                      <span className="truncate">{health.label}</span>
                    </div>
                    {agentView && (
                      <div
                        className={cn("mt-1 flex items-center gap-1.5 text-[11px]", agentView.className)}
                        title="Ergebnis von `claude mcp list` in den laufenden Agent-Containern (Agent-Perspektive, #425)."
                      >
                        {agentView.ok ? <CheckCircle2 className="h-3 w-3 shrink-0" /> : <AlertCircle className="h-3 w-3 shrink-0" />}
                        <span className="truncate">{agentView.label}</span>
                      </div>
                    )}
                    {disagreement && (
                      <div className="mt-1 flex items-center gap-1.5 text-[11px] text-amber-400" title="Der Orchestrator erreicht den Server, aber mindestens ein Agent nicht — oft ein pro-Agent-Token, das der Server ablehnt.">
                        <AlertCircle className="h-3 w-3 shrink-0" />
                        <span className="truncate">Diskrepanz: Orchestrator erreichbar, Agents melden Probleme</span>
                      </div>
                    )}
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
                      onClick={() => handleOAuthConnect(server)}
                      disabled={oauthBusy === server.id}
                      className={cn(
                        "flex h-7 w-7 items-center justify-center rounded-lg transition-colors",
                        server.oauth_connected
                          ? "text-emerald-400 hover:bg-emerald-500/15"
                          : "text-muted-foreground/40 hover:text-foreground hover:bg-foreground/[0.06]"
                      )}
                      title={server.oauth_connected
                        ? "OAuth erneut verbinden (neuer Login)"
                        : "Mit OAuth verbinden (Login im Browser)"}
                    >
                      {oauthBusy === server.id ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <LogIn className="h-3.5 w-3.5" />
                      )}
                    </button>
                    <button
                      onClick={() => openEdit(server)}
                      className="flex h-7 w-7 items-center justify-center rounded-lg text-muted-foreground/40 hover:text-foreground hover:bg-foreground/[0.06] transition-colors"
                      title="Bearbeiten"
                    >
                      <Pencil className="h-3.5 w-3.5" />
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
                            <div className="min-w-0 flex-1">
                              <span className="text-[12px] font-medium font-mono text-foreground">{tool.name}</span>
                              {tool.description && (
                                <p className="text-[11px] text-muted-foreground/60 mt-0.5 line-clamp-2">{tool.description}</p>
                              )}
                              <McpToolRunner serverId={server.id} tool={tool} />
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
