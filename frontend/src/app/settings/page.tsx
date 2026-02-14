"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import {
  Key, MessageSquare, Save, Loader2,
  CheckCircle2, AlertCircle, Shield, Bot, Gauge, Settings2,
} from "lucide-react";
import { Header } from "@/components/layout/header";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import type { Settings } from "@/lib/types";

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [authMethod, setAuthMethod] = useState<"api_key" | "oauth_token">("api_key");
  const [apiKey, setApiKey] = useState("");
  const [oauthToken, setOauthToken] = useState("");
  const [telegramToken, setTelegramToken] = useState("");
  const [telegramChatId, setTelegramChatId] = useState("");
  const [defaultModel, setDefaultModel] = useState("claude-sonnet-4-5-20250929");
  const [maxTurns, setMaxTurns] = useState(100);
  const [maxAgents, setMaxAgents] = useState(10);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    api.getSettings().then((s) => {
      setSettings(s);
      setDefaultModel(s.default_model);
      setMaxTurns(s.max_turns);
      setMaxAgents(s.max_agents);
      if (s.auth_method === "oauth_token") {
        setAuthMethod("oauth_token");
      } else {
        setAuthMethod("api_key");
      }
    });
  }, []);

  const handleSave = async () => {
    setSaving(true);
    setMessage("");
    try {
      const data: Record<string, unknown> = {
        default_model: defaultModel,
        max_turns: maxTurns,
        max_agents: maxAgents,
      };
      if (authMethod === "api_key" && apiKey) {
        data.anthropic_api_key = apiKey;
      }
      if (authMethod === "oauth_token" && oauthToken) {
        data.claude_oauth_token = oauthToken;
      }
      if (telegramToken && telegramChatId) {
        data.telegram = { bot_token: telegramToken, chat_id: telegramChatId };
      }
      await api.updateSettings(data);
      setMessage("Settings saved!");
      setApiKey("");
      setOauthToken("");
      const s = await api.getSettings();
      setSettings(s);
    } catch (e) {
      setMessage(`Error: ${e instanceof Error ? e.message : "Failed"}`);
    } finally {
      setSaving(false);
    }
  };

  const hasAuth = settings?.auth_method !== "none";
  const currentAuthLabel =
    settings?.auth_method === "api_key"
      ? "API Key"
      : settings?.auth_method === "oauth_token"
        ? "OAuth Token"
        : null;

  return (
    <div>
      <Header title="Settings" subtitle="Configure your AI Employee platform" />

      <motion.div
        className="px-8 py-8 max-w-2xl space-y-5"
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        {/* Claude Authentication */}
        <SettingsCard
          icon={Shield}
          iconColor="text-blue-400"
          iconBg="bg-blue-500/10"
          title="Claude Authentication"
          description="Authenticate with an API Key (paid) or OAuth Token (subscription)."
        >
          {/* Status */}
          <div className="flex items-center gap-2 mb-4">
            {hasAuth ? (
              <div className="inline-flex items-center gap-1.5 text-xs font-medium text-emerald-400">
                <CheckCircle2 className="h-3.5 w-3.5" />
                Authenticated via {currentAuthLabel}
              </div>
            ) : (
              <div className="inline-flex items-center gap-1.5 text-xs font-medium text-red-400">
                <AlertCircle className="h-3.5 w-3.5" />
                No authentication configured
              </div>
            )}
          </div>

          {/* Auth Method Toggle */}
          <div className="flex rounded-lg border border-foreground/[0.08] p-0.5 mb-4">
            <button
              onClick={() => setAuthMethod("api_key")}
              className={cn(
                "flex-1 rounded-md px-3 py-2 text-xs font-medium transition-all",
                authMethod === "api_key"
                  ? "bg-primary text-primary-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              <span className="flex items-center justify-center gap-1.5">
                <Key className="h-3 w-3" />
                API Key
              </span>
              <span className="block text-[10px] opacity-70 mt-0.5">Bezahlt per Token</span>
            </button>
            <button
              onClick={() => setAuthMethod("oauth_token")}
              className={cn(
                "flex-1 rounded-md px-3 py-2 text-xs font-medium transition-all",
                authMethod === "oauth_token"
                  ? "bg-primary text-primary-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              <span className="flex items-center justify-center gap-1.5">
                <Shield className="h-3 w-3" />
                OAuth Token
              </span>
              <span className="block text-[10px] opacity-70 mt-0.5">Claude Abo (Pro/Team)</span>
            </button>
          </div>

          {/* Input Field */}
          {authMethod === "api_key" ? (
            <div className="space-y-2">
              <input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="sk-ant-api03-..."
                className="w-full rounded-lg border border-foreground/[0.08] bg-background/80 px-4 py-2.5 text-sm font-mono outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all placeholder:text-muted-foreground/30"
              />
              <p className="text-[10px] text-muted-foreground/50">
                Erstelle einen API Key auf console.anthropic.com. Kosten werden per Token abgerechnet.
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              <input
                type="password"
                value={oauthToken}
                onChange={(e) => setOauthToken(e.target.value)}
                placeholder="sk-ant-oat01-..."
                className="w-full rounded-lg border border-foreground/[0.08] bg-background/80 px-4 py-2.5 text-sm font-mono outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all placeholder:text-muted-foreground/30"
              />
              <p className="text-[10px] text-muted-foreground/50">
                Fuehre &quot;claude login&quot; aus und extrahiere den Token aus der macOS Keychain (Keychain Access &gt; &quot;claude&quot; suchen).
              </p>
            </div>
          )}
        </SettingsCard>

        {/* Telegram Bot */}
        <SettingsCard
          icon={MessageSquare}
          iconColor="text-cyan-400"
          iconBg="bg-cyan-500/10"
          title="Telegram Bot"
          description="Get a bot token from @BotFather on Telegram."
        >
          <div className="flex items-center gap-2 mb-3">
            {settings?.has_telegram ? (
              <div className="inline-flex items-center gap-1.5 text-xs font-medium text-emerald-400">
                <CheckCircle2 className="h-3.5 w-3.5" />
                Telegram configured
              </div>
            ) : (
              <div className="inline-flex items-center gap-1.5 text-xs font-medium text-zinc-400">
                <AlertCircle className="h-3.5 w-3.5" />
                Not configured
              </div>
            )}
          </div>
          <div className="space-y-2.5">
            <input
              type="password"
              value={telegramToken}
              onChange={(e) => setTelegramToken(e.target.value)}
              placeholder="Bot Token"
              className="w-full rounded-lg border border-foreground/[0.08] bg-background/80 px-4 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all placeholder:text-muted-foreground/30"
            />
            <input
              type="text"
              value={telegramChatId}
              onChange={(e) => setTelegramChatId(e.target.value)}
              placeholder="Chat ID"
              className="w-full rounded-lg border border-foreground/[0.08] bg-background/80 px-4 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all placeholder:text-muted-foreground/30"
            />
          </div>
        </SettingsCard>

        {/* Agent Defaults */}
        <SettingsCard
          icon={Bot}
          iconColor="text-violet-400"
          iconBg="bg-violet-500/10"
          title="Agent Defaults"
          description="Default configuration for new agents and tasks."
        >
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="space-y-1.5">
              <label className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                <Settings2 className="h-3 w-3" />
                Default Model
              </label>
              <select
                value={defaultModel}
                onChange={(e) => setDefaultModel(e.target.value)}
                className="w-full rounded-lg border border-foreground/[0.08] bg-background/80 px-3 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all appearance-none"
              >
                <option value="claude-sonnet-4-5-20250929">Sonnet 4.5</option>
                <option value="claude-haiku-4-5-20251001">Haiku 4.5</option>
                <option value="claude-opus-4-6">Opus 4.6</option>
              </select>
            </div>
            <div className="space-y-1.5">
              <label className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                <Gauge className="h-3 w-3" />
                Max Turns/Task
              </label>
              <input
                type="number"
                value={maxTurns}
                onChange={(e) => setMaxTurns(Number(e.target.value))}
                className="w-full rounded-lg border border-foreground/[0.08] bg-background/80 px-3 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all tabular-nums"
              />
            </div>
            <div className="space-y-1.5">
              <label className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                <Key className="h-3 w-3" />
                Max Agents
              </label>
              <input
                type="number"
                value={maxAgents}
                onChange={(e) => setMaxAgents(Number(e.target.value))}
                className="w-full rounded-lg border border-foreground/[0.08] bg-background/80 px-3 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all tabular-nums"
              />
            </div>
          </div>
        </SettingsCard>

        {/* Save button */}
        <div className="flex items-center gap-3 pt-2">
          <button
            onClick={handleSave}
            disabled={saving}
            className="inline-flex items-center gap-2 rounded-xl bg-primary px-6 py-3 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 hover:bg-primary/90 disabled:opacity-50 disabled:shadow-none transition-all duration-200"
          >
            {saving ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Save className="h-4 w-4" />
            )}
            {saving ? "Saving..." : "Save Settings"}
          </button>
          {message && (
            <span
              className={cn(
                "inline-flex items-center gap-1.5 text-sm font-medium",
                message.startsWith("Error") ? "text-red-400" : "text-emerald-400"
              )}
            >
              {message.startsWith("Error") ? (
                <AlertCircle className="h-4 w-4" />
              ) : (
                <CheckCircle2 className="h-4 w-4" />
              )}
              {message}
            </span>
          )}
        </div>
      </motion.div>
    </div>
  );
}

function SettingsCard({
  icon: Icon,
  iconColor,
  iconBg,
  title,
  description,
  children,
}: {
  icon: typeof Shield;
  iconColor: string;
  iconBg: string;
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
      <div className="flex items-center gap-3 mb-4">
        <div className={cn("flex h-9 w-9 items-center justify-center rounded-xl", iconBg)}>
          <Icon className={cn("h-4.5 w-4.5", iconColor)} />
        </div>
        <div>
          <h3 className="text-sm font-semibold">{title}</h3>
          <p className="text-[11px] text-muted-foreground/70">{description}</p>
        </div>
      </div>
      {children}
    </div>
  );
}
