"use client";

import { useState, useEffect } from "react";
import {
  Plug, Mail, Cloud, Smartphone, CheckCircle2,
  AlertCircle, Loader2, Unplug, ExternalLink, RefreshCw,
} from "lucide-react";
import { Header } from "@/components/layout/header";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import type { Integration } from "@/lib/types";
import { useSearchParams } from "next/navigation";

const PROVIDER_ICONS: Record<string, typeof Mail> = {
  Mail,
  Cloud,
  Smartphone,
};

export default function IntegrationsPage() {
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [loading, setLoading] = useState(true);
  const [connecting, setConnecting] = useState<string | null>(null);
  const [disconnecting, setDisconnecting] = useState<string | null>(null);
  const [toast, setToast] = useState<{ type: "success" | "error"; message: string } | null>(null);
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
      // Clean URL
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
      await loadIntegrations();
    } catch (e) {
      setToast({ type: "error", message: e instanceof Error ? e.message : "Failed to disconnect" });
    } finally {
      setDisconnecting(null);
    }
  };

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <Header title="Integrations" subtitle="Connect external services for your agents" />

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

      <div className="flex-1 overflow-y-auto p-6 space-y-4">
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
                      {/* Provider Icon */}
                      <div className={cn(
                        "flex h-12 w-12 items-center justify-center rounded-xl",
                        integration.connected
                          ? "bg-emerald-500/10"
                          : "bg-foreground/[0.06]"
                      )}>
                        <Icon className={cn(
                          "h-6 w-6",
                          integration.connected ? "text-emerald-400" : "text-muted-foreground"
                        )} />
                      </div>

                      {/* Info */}
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

                        {integration.connected && integration.expires_at && (
                          <p className="text-[10px] text-muted-foreground/60 mt-1">
                            Token expires: {new Date(integration.expires_at).toLocaleString()}
                          </p>
                        )}

                        {!integration.available && !integration.connected && (
                          <p className="text-[10px] text-yellow-500/80 mt-1.5">
                            Not configured - set OAUTH_{integration.provider.toUpperCase()}_CLIENT_ID in .env
                          </p>
                        )}
                      </div>
                    </div>

                    {/* Action Button */}
                    <div className="flex items-center gap-2">
                      {integration.connected ? (
                        <button
                          onClick={() => handleDisconnect(integration.provider)}
                          disabled={isDisconnecting}
                          className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium text-red-400 hover:bg-red-500/10 border border-red-500/20 hover:border-red-500/30 transition-all disabled:opacity-50"
                        >
                          {isDisconnecting ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            <Unplug className="h-3 w-3" />
                          )}
                          Disconnect
                        </button>
                      ) : integration.available ? (
                        <button
                          onClick={() => handleConnect(integration.provider)}
                          disabled={isConnecting}
                          className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium bg-primary text-primary-foreground hover:bg-primary/90 shadow-lg shadow-primary/20 transition-all disabled:opacity-50"
                        >
                          {isConnecting ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            <ExternalLink className="h-3 w-3" />
                          )}
                          Connect
                        </button>
                      ) : (
                        <span className="text-[10px] text-muted-foreground/40 px-3 py-2">
                          Not available
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* Info Box */}
        <div className="max-w-3xl mt-6 rounded-xl border border-foreground/[0.06] bg-card/50 p-4">
          <h4 className="text-xs font-semibold text-muted-foreground mb-2">How it works</h4>
          <ol className="text-xs text-muted-foreground/80 space-y-1.5 list-decimal list-inside">
            <li>Connect your accounts above using OAuth</li>
            <li>Go to an Agent's settings and enable the integrations you want it to use</li>
            <li>The agent receives access tokens and can interact with the APIs (Gmail, Drive, Calendar, etc.)</li>
            <li>Tokens are encrypted and auto-refreshed in the background</li>
          </ol>
        </div>
      </div>
    </div>
  );
}
