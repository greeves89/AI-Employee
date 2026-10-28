"use client";

import { useState, useEffect } from "react";
import {
  Plug, Mail, Cloud, Smartphone, CheckCircle2,
  AlertCircle, Loader2, Unplug, ExternalLink, RefreshCw,
  Plus, Trash2, ChevronRight, Wrench, Globe, Power,
  Eye, EyeOff, Save, Users, Copy, Info, Pencil, KeyRound, PlugZap,
  ShieldCheck, LogIn, Play, AlertTriangle,
} from "lucide-react";
import { Github } from "@/components/icons/github";
import { Header } from "@/components/layout/header";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import { useConfirm } from "@/components/ui/dialog-provider";
import type { Integration } from "@/lib/types";
import type { McpServerInfo, McpTool, McpAgentHealth, McpAgentHealthEntry, McpToolCallResult } from "@/lib/api";
import { useSearchParams } from "next/navigation";
import { ClaudeLoginDialog, startClaudeLogin } from "@/components/integrations/claude-login-dialog";

const PROVIDER_ICONS: Record<string, typeof Mail> = {
  Mail,
  Cloud,
  Smartphone,
  Github,
};
export default function IntegrationsPage() {
  const confirm = useConfirm();
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [loading, setLoading] = useState(true);
  const [connecting, setConnecting] = useState<string | null>(null);
  // Anthropic zeigt nach dem Login nur "code#state" an statt zurueckzuleiten —
  // ein Redirect-Flow endet dort in einer Sackgasse. Daher Code-Dialog.
  const [claudeLoginOpen, setClaudeLoginOpen] = useState(false);
  const [claudeAuthState, setClaudeAuthState] = useState("");
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
      setToast({ type: "success", message: `${connected} verbunden` });
      loadIntegrations();
      window.history.replaceState({}, "", "/integrations");
    }
    if (error) {
      setToast({ type: "error", message: `Verbindung fehlgeschlagen: ${error}` });
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
    if (provider === "anthropic") {
      try {
        setClaudeAuthState(await startClaudeLogin());
        setClaudeLoginOpen(true);
      } catch (e) {
        setToast({ type: "error", message: e instanceof Error ? e.message : "Anmeldung konnte nicht gestartet werden" });
      }
      return;
    }
    setConnecting(provider);
    try {
      const { auth_url } = await api.getAuthUrl(provider);
      window.location.href = auth_url;
    } catch (e) {
      setToast({ type: "error", message: e instanceof Error ? e.message : "Anmeldung konnte nicht gestartet werden" });
      setConnecting(null);
    }
  };

  const handleDisconnect = async (provider: string) => {
    const ok = await confirm({
      title: `Verbindung zu ${provider} trennen?`,
      message: "Agenten, die diese Verbindung nutzen, verlieren den Zugriff.",
      variant: "destructive",
      confirmLabel: "Trennen",
    });
    if (!ok) return;
    setDisconnecting(provider);
    try {
      await api.disconnectIntegration(provider);
      setToast({ type: "success", message: `Verbindung zu ${provider} getrennt` });
      setPatToken("");
      await loadIntegrations();
    } catch (e) {
      setToast({ type: "error", message: e instanceof Error ? e.message : "Trennen fehlgeschlagen" });
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
      setToast({ type: "success", message: `Mit ${provider} verbunden als ${result.account_label || "unbekannt"}` });
      setPatToken("");
      setPatBaseUrl("");
      setPatSelfHosted(false);
      setPatVisible(false);
      await loadIntegrations();
    } catch (e) {
      setToast({ type: "error", message: e instanceof Error ? e.message : "Ungültiges Token" });
    } finally {
      setPatSaving(null);
    }
  };

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <Header title="Verbindungen" subtitle="Konten, die deine Agenten in deinem Namen nutzen dürfen" />

      {/* Claude-Login per Code (Anthropic leitet nicht zurueck) */}
      <ClaudeLoginDialog
        open={claudeLoginOpen}
        authState={claudeAuthState}
        onClose={() => setClaudeLoginOpen(false)}
        onConnected={async () => {
          setToast({ type: "success", message: "Claude verbunden – das neue Token gilt ab sofort für alle Agenten." });
          await loadIntegrations();
        }}
      />

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
        {/* Die Werkzeug-Server (MCP) der Anlage verwalten Admins im Admin-Bereich unter
            „Dienste & Konten“ (Redesign 2026-10) — hier steht nur, was du selbst verbindest. */}

        {/* OAuth Integrations Section */}
        <div>
          <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider mb-4">OAuth-Anbindungen</h2>
          {loading ? (
            <div className="flex items-center justify-center h-40">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : integrations.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-40 text-muted-foreground">
              <Plug className="h-8 w-8 mb-2" />
              <p className="text-sm">Keine Anbindungen verfügbar</p>
              <p className="text-xs mt-1">Die OAuth-Zugangsdaten richtet der Administrator in den Einstellungen ein</p>
            </div>
          ) : (
            <div className="grid gap-4 max-w-3xl">
              {integrations.map((integration) => {
                const Icon = PROVIDER_ICONS[integration.icon] || Plug;
                const isConnecting = connecting === integration.provider;
                const isDisconnecting = disconnecting === integration.provider;
                // "connected" heisst nur: es gibt eine Zeile. Ob der Token noch taugt,
                // sagt status (Backend: app/core/integration_health.py).
                const isExpired = integration.status === "expired";
                const isFailing = integration.status === "refresh_failing";
                const needsReauth = integration.connected && (isExpired || isFailing);
                const isHealthy = integration.connected && !needsReauth;

                return (
                  <div
                    key={integration.provider}
                    className={cn(
                      "rounded-xl border bg-card/80 backdrop-blur-sm p-5 transition-all",
                      needsReauth
                        ? "border-amber-500/40"
                        : isHealthy
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
                          needsReauth ? "bg-amber-500/10" : isHealthy ? "bg-emerald-500/10" : "bg-foreground/[0.06]"
                        )}>
                          <Icon className={cn(
                            "h-6 w-6",
                            needsReauth ? "text-amber-500" : isHealthy ? "text-emerald-400" : "text-muted-foreground"
                          )} />
                        </div>
                        <div>
                          <div className="flex items-center gap-2">
                            <h3 className="text-sm font-semibold">{integration.display_name}</h3>
                            {isHealthy && (
                              <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-400">
                                <CheckCircle2 className="h-2.5 w-2.5" />
                                Verbunden
                              </span>
                            )}
                            {isExpired && (
                              <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400 border border-amber-500/30">
                                <AlertTriangle className="h-2.5 w-2.5" />
                                Abgelaufen
                              </span>
                            )}
                            {isFailing && (
                              <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400 border border-amber-500/30">
                                <AlertTriangle className="h-2.5 w-2.5" />
                                Erneuerung schlägt fehl
                              </span>
                            )}
                            {integration.per_user && (
                              <span className="inline-flex items-center gap-1 rounded-full bg-blue-500/10 px-2 py-0.5 text-[10px] font-medium text-blue-400 border border-blue-500/20">
                                <Users className="h-2.5 w-2.5" />
                                Je Nutzer
                              </span>
                            )}
                          </div>
                          <p className="text-xs text-muted-foreground mt-0.5">{integration.description}</p>
                          {integration.connected && integration.account_label && (
                            <p className={cn("text-xs mt-1.5", needsReauth ? "text-muted-foreground" : "text-emerald-400/80")}>
                              Angemeldet als {integration.account_label}
                            </p>
                          )}
                          {needsReauth && (
                            <div className="mt-1.5 space-y-0.5 text-xs text-amber-700 dark:text-amber-400/90">
                              <p>
                                {isExpired
                                  ? `Token seit ${integration.expires_at ? new Date(integration.expires_at).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" }) : "unbekannt"} abgelaufen – alles, was diese Integration nutzt, schlägt fehl. Bitte neu verbinden.`
                                  : `Token gilt noch${integration.expires_at ? ` bis ${new Date(integration.expires_at).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" })}` : ""}, lässt sich aber nicht erneuern. Neu verbinden, bevor er abläuft.`}
                              </p>
                              {integration.refresh_error && (
                                <p className="font-mono text-[10px] text-amber-700/80 dark:text-amber-400/70">
                                  Letzter Fehler: {integration.refresh_error}
                                </p>
                              )}
                            </div>
                          )}
                          {!integration.available && !integration.connected && integration.auth_type !== "pat" && (
                            <p className="text-[10px] text-yellow-500/80 mt-1.5">
                              Noch nicht eingerichtet — die Zugangsdaten (Client ID) hinterlegt der Administrator unter Admin-Bereich → Dienste & Konten.
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
                        {needsReauth && (
                          <button
                            onClick={() => handleConnect(integration.provider)}
                            disabled={isConnecting}
                            className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium bg-primary text-primary-foreground hover:bg-primary/90 shadow-lg shadow-primary/20 transition-all disabled:opacity-50"
                          >
                            {isConnecting ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
                            Neu verbinden
                          </button>
                        )}
                        {integration.connected ? (
                          <button
                            onClick={() => handleDisconnect(integration.provider)}
                            disabled={isDisconnecting}
                            className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium text-red-400 hover:bg-red-500/10 border border-red-500/20 hover:border-red-500/30 transition-all disabled:opacity-50"
                          >
                            {isDisconnecting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Unplug className="h-3 w-3" />}
                            Trennen
                          </button>
                        ) : integration.auth_type === "pat" ? (
                          <span className="text-[10px] text-muted-foreground/40">Token unten eingeben</span>
                        ) : integration.available ? (
                          <button
                            onClick={() => handleConnect(integration.provider)}
                            disabled={isConnecting}
                            className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium bg-primary text-primary-foreground hover:bg-primary/90 shadow-lg shadow-primary/20 transition-all disabled:opacity-50"
                          >
                            {isConnecting ? <Loader2 className="h-3 w-3 animate-spin" /> : <ExternalLink className="h-3 w-3" />}
                            Verbinden
                          </button>
                        ) : (
                          <span className="text-[10px] text-muted-foreground/40 px-3 py-2">Nicht verfügbar</span>
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
                          So richtest du Microsoft 365 ein (Azure-App-Registrierung)
                          <ChevronRight className={cn("h-3 w-3 transition-transform", setupExpanded === "microsoft" && "rotate-90")} />
                        </button>
                        {setupExpanded === "microsoft" && (
                          <div className="mt-3 rounded-lg border border-blue-500/20 bg-blue-500/5 p-4 space-y-3 text-xs text-muted-foreground">
                            <ol className="list-decimal list-inside space-y-2">
                              <li><strong className="text-foreground">portal.azure.com</strong> öffnen → Microsoft Entra ID → App-Registrierungen → Neue Registrierung</li>
                              <li>Umleitungs-URI (Web) setzen auf:
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
                              <li>Unter <strong className="text-foreground">API-Berechtigungen</strong> → Berechtigung hinzufügen → Microsoft Graph → Delegiert:<br />
                                <span className="text-[10px] font-mono text-blue-300/80">User.Read, Mail.ReadWrite, Mail.Send, Calendars.ReadWrite, Files.ReadWrite, Chat.ReadWrite, Chat.ReadBasic, ChannelMessage.Read.All, ChannelMessage.Send, Team.ReadBasic.All, Tasks.ReadWrite, Contacts.ReadWrite, People.Read, offline_access</span>
                                <p className="mt-1 text-amber-700 dark:text-amber-400/80">→ Danach auf <strong>„Administratorzustimmung erteilen“</strong> klicken</p>
                              </li>
                              <li>Unter <strong className="text-foreground">Zertifikate &amp; Geheimnisse</strong> einen neuen geheimen Clientschlüssel anlegen</li>
                              <li><strong className="text-foreground">Client-ID &amp; Secret</strong> unter <strong className="text-foreground">Admin-Bereich → Dienste & Konten → Microsoft 365</strong> eintragen</li>
                            </ol>
                            <p className="text-[10px] text-muted-foreground/60">
                              Der Administrator richtet das einmal ein. Danach verbindet jeder Nutzer hier sein eigenes Konto — Token werden je Nutzer gespeichert, nicht geteilt.
                            </p>
                          </div>
                        )}
                      </div>
                    )}

                    {/* PAT token input for PAT-based providers (e.g. GitHub) */}
                    {integration.auth_type === "pat" && !integration.connected && (
                      <div className="mt-4 pt-4 border-t border-foreground/[0.06]">
                        <label className="text-[11px] font-medium text-muted-foreground mb-1.5 block">
                          Persönliches Zugangstoken
                        </label>
                        <div className="flex items-center gap-2">
                          <div className="relative flex-1">
                            <input
                              type={patVisible ? "text" : "password"}
                              value={patToken}
                              onChange={(e) => setPatToken(e.target.value)}
                              placeholder="ghp_… (dein persönliches Zugangstoken)"
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
                            Speichern
                          </button>
                        </div>
                        <p className="text-[10px] text-muted-foreground/50 mt-1.5">
                          Ein Token legst du unter github.com/settings/tokens mit den Rechten repo, workflow und read:org an
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
                              Selbst betrieben (GitHub Enterprise Server)
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
