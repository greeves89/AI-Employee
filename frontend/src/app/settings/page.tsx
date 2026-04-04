"use client";

import { useState, useEffect, useMemo } from "react";
import { motion } from "framer-motion";
import {
  Key, MessageSquare, Save, Loader2,
  CheckCircle2, AlertCircle, Shield, Bot, Gauge,
  UserPlus, Cloud, Server, Lock, Globe, Cpu, Layers,
  ExternalLink, Copy, LogIn,
} from "lucide-react";
import { useAuthStore } from "@/lib/auth";
import { Header } from "@/components/layout/header";
import { TemplateManager } from "@/components/settings/template-manager";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import type { Settings, ModelProvider } from "@/lib/types";

// ── Model options per provider ──────────────────────────────
const MODEL_OPTIONS: Record<ModelProvider, { value: string; label: string; tier: string }[]> = {
  anthropic: [
    { value: "claude-sonnet-4-6", label: "Sonnet 4.6 (Latest)", tier: "Balanced" },
    { value: "claude-sonnet-4-5-20250929", label: "Sonnet 4.5", tier: "Balanced" },
    { value: "claude-haiku-4-5-20251001", label: "Haiku 4.5", tier: "Fast" },
    { value: "claude-opus-4-6", label: "Opus 4.6", tier: "Powerful" },
  ],
  bedrock: [
    { value: "us.anthropic.claude-sonnet-4-6-v1:0", label: "Sonnet 4.6 (Latest)", tier: "Balanced" },
    { value: "us.anthropic.claude-sonnet-4-5-20250929-v1:0", label: "Sonnet 4.5", tier: "Balanced" },
    { value: "us.anthropic.claude-haiku-4-5-20251001-v1:0", label: "Haiku 4.5", tier: "Fast" },
    { value: "us.anthropic.claude-opus-4-20250514-v1:0", label: "Opus 4", tier: "Powerful" },
  ],
  vertex: [
    { value: "claude-sonnet-4-6@latest", label: "Sonnet 4.6 (Latest)", tier: "Balanced" },
    { value: "claude-sonnet-4-5@20250929", label: "Sonnet 4.5", tier: "Balanced" },
    { value: "claude-haiku-4-5@20251001", label: "Haiku 4.5", tier: "Fast" },
    { value: "claude-opus-4@20250514", label: "Opus 4", tier: "Powerful" },
  ],
  foundry: [
    { value: "claude-sonnet-4-6", label: "Sonnet 4.6 (Latest)", tier: "Balanced" },
    { value: "claude-sonnet-4-5", label: "Sonnet 4.5", tier: "Balanced" },
    { value: "claude-haiku-4-5", label: "Haiku 4.5", tier: "Fast" },
    { value: "claude-opus-4-6", label: "Opus 4.6", tier: "Powerful" },
  ],
};

const AWS_REGIONS = [
  "us-east-1", "us-east-2", "us-west-2",
  "eu-central-1", "eu-west-1", "eu-west-3",
  "ap-southeast-1", "ap-southeast-2", "ap-northeast-1",
];

const VERTEX_REGIONS = [
  "us-east5", "us-central1", "europe-west1", "europe-west4", "asia-southeast1",
];

// ── Provider config ──────────────────────────────────────────
const PROVIDERS: {
  id: ModelProvider;
  label: string;
  short: string;
  icon: typeof Cloud;
  color: string;
  bgColor: string;
  description: string;
}[] = [
  {
    id: "anthropic",
    label: "Anthropic Direct",
    short: "Anthropic",
    icon: Key,
    color: "text-orange-400",
    bgColor: "bg-orange-500/10 border-orange-500/20",
    description: "Direct API access via API Key or OAuth Token",
  },
  {
    id: "bedrock",
    label: "Amazon Bedrock",
    short: "Bedrock",
    icon: Cloud,
    color: "text-amber-400",
    bgColor: "bg-amber-500/10 border-amber-500/20",
    description: "AWS managed service with IAM credentials",
  },
  {
    id: "vertex",
    label: "Google Vertex AI",
    short: "Vertex",
    icon: Globe,
    color: "text-blue-400",
    bgColor: "bg-blue-500/10 border-blue-500/20",
    description: "GCP managed service with service account",
  },
  {
    id: "foundry",
    label: "Azure AI Foundry",
    short: "Foundry",
    icon: Server,
    color: "text-sky-400",
    bgColor: "bg-sky-500/10 border-sky-500/20",
    description: "Microsoft Azure managed deployment",
  },
];

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  // Provider
  const [provider, setProvider] = useState<ModelProvider>("anthropic");
  // Anthropic Direct
  const [authMethod, setAuthMethod] = useState<"api_key" | "oauth_token">("api_key");
  const [apiKey, setApiKey] = useState("");
  const [oauthToken, setOauthToken] = useState("");
  // Bedrock
  const [awsAccessKey, setAwsAccessKey] = useState("");
  const [awsSecretKey, setAwsSecretKey] = useState("");
  const [awsRegion, setAwsRegion] = useState("us-east-1");
  // Vertex
  const [vertexProjectId, setVertexProjectId] = useState("");
  const [vertexRegion, setVertexRegion] = useState("us-east5");
  const [vertexCredentials, setVertexCredentials] = useState("");
  // Foundry
  const [foundryApiKey, setFoundryApiKey] = useState("");
  const [foundryResource, setFoundryResource] = useState("");
  // Telegram
  const [telegramToken, setTelegramToken] = useState("");
  const [telegramChatId, setTelegramChatId] = useState("");
  // Agent defaults
  const [defaultModel, setDefaultModel] = useState("claude-sonnet-4-5-20250929");
  const [maxTurns, setMaxTurns] = useState(100);
  const [maxAgents, setMaxAgents] = useState(10);
  const [registrationOpen, setRegistrationOpen] = useState(true);
  // OAuth integration credentials
  const [googleClientId, setGoogleClientId] = useState("");
  const [googleClientSecret, setGoogleClientSecret] = useState("");
  const [microsoftClientId, setMicrosoftClientId] = useState("");
  const [microsoftClientSecret, setMicrosoftClientSecret] = useState("");
  const [appleClientId, setAppleClientId] = useState("");
  const [appleTeamId, setAppleTeamId] = useState("");
  // Claude OAuth login
  const [claudeLoginOpen, setClaudeLoginOpen] = useState(false);
  const [claudeAuthState, setClaudeAuthState] = useState("");
  const [claudeCode, setClaudeCode] = useState("");
  const [claudeLoginLoading, setClaudeLoginLoading] = useState(false);
  const [claudeLoginError, setClaudeLoginError] = useState("");
  // UI state
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === "admin";

  // Available models for current provider
  const modelOptions = useMemo(() => MODEL_OPTIONS[provider] || MODEL_OPTIONS.anthropic, [provider]);

  useEffect(() => {
    api.getSettings().then((s) => {
      setSettings(s);
      setProvider(s.model_provider || "anthropic");
      setDefaultModel(s.default_model);
      setMaxTurns(s.max_turns);
      setMaxAgents(s.max_agents);
      setRegistrationOpen(s.registration_open);
      setAwsRegion(s.aws_region || "us-east-1");
      setVertexRegion(s.vertex_region || "us-east5");
      setFoundryResource(s.foundry_resource || "");
      if (s.auth_method === "oauth_token") {
        setAuthMethod("oauth_token");
      } else {
        setAuthMethod("api_key");
      }
    });
  }, []);

  // When provider changes, reset model to first option if current is invalid
  useEffect(() => {
    const valid = MODEL_OPTIONS[provider]?.some((m) => m.value === defaultModel);
    if (!valid && MODEL_OPTIONS[provider]?.length) {
      setDefaultModel(MODEL_OPTIONS[provider][0].value);
    }
  }, [provider, defaultModel]);

  const handleSave = async () => {
    setSaving(true);
    setMessage("");
    try {
      const data: Record<string, unknown> = {
        model_provider: provider,
        default_model: defaultModel,
        max_turns: maxTurns,
        max_agents: maxAgents,
        registration_open: registrationOpen,
      };
      // Provider-specific credentials
      if (provider === "anthropic") {
        if (authMethod === "api_key" && apiKey) {
          data.anthropic_api_key = apiKey;
        }
        if (authMethod === "oauth_token" && oauthToken) {
          data.claude_oauth_token = oauthToken;
        }
      } else if (provider === "bedrock") {
        if (awsAccessKey) data.aws_access_key_id = awsAccessKey;
        if (awsSecretKey) data.aws_secret_access_key = awsSecretKey;
        data.aws_region = awsRegion;
      } else if (provider === "vertex") {
        if (vertexProjectId) data.vertex_project_id = vertexProjectId;
        data.vertex_region = vertexRegion;
        if (vertexCredentials) data.vertex_credentials_json = vertexCredentials;
      } else if (provider === "foundry") {
        if (foundryApiKey) data.foundry_api_key = foundryApiKey;
        if (foundryResource) data.foundry_resource = foundryResource;
      }
      // Telegram
      if (telegramToken && telegramChatId) {
        data.telegram = { bot_token: telegramToken, chat_id: telegramChatId };
      }
      // OAuth integration credentials
      if (googleClientId) data.oauth_google_client_id = googleClientId;
      if (googleClientSecret) data.oauth_google_client_secret = googleClientSecret;
      if (microsoftClientId) data.oauth_microsoft_client_id = microsoftClientId;
      if (microsoftClientSecret) data.oauth_microsoft_client_secret = microsoftClientSecret;
      if (appleClientId) data.oauth_apple_client_id = appleClientId;
      if (appleTeamId) data.oauth_apple_team_id = appleTeamId;
      await api.updateSettings(data);
      setMessage("Settings saved!");
      // Clear secret fields
      setApiKey("");
      setOauthToken("");
      setAwsAccessKey("");
      setAwsSecretKey("");
      setVertexCredentials("");
      setFoundryApiKey("");
      setGoogleClientId("");
      setGoogleClientSecret("");
      setMicrosoftClientId("");
      setMicrosoftClientSecret("");
      setAppleClientId("");
      setAppleTeamId("");
      const s = await api.getSettings();
      setSettings(s);
    } catch (e) {
      setMessage(`Error: ${e instanceof Error ? e.message : "Failed"}`);
    } finally {
      setSaving(false);
    }
  };

  // Claude OAuth login handlers
  const handleClaudeLogin = async () => {
    try {
      setClaudeLoginError("");
      const { auth_url } = await api.getAuthUrl("anthropic");
      // Extract state from auth URL
      const url = new URL(auth_url);
      const state = url.searchParams.get("state") || "";
      setClaudeAuthState(state);
      setClaudeLoginOpen(true);
      setClaudeCode("");
      // Open Anthropic login in new tab
      window.open(auth_url, "_blank");
    } catch (e) {
      setMessage(`Error: ${e instanceof Error ? e.message : "Failed to start login"}`);
    }
  };

  const handleClaudeCodeSubmit = async () => {
    if (!claudeCode.trim()) return;
    setClaudeLoginLoading(true);
    setClaudeLoginError("");
    try {
      await api.exchangeOAuthCode("anthropic", claudeCode.trim(), claudeAuthState);
      setClaudeLoginOpen(false);
      setClaudeCode("");
      setMessage("Claude Login erfolgreich! Bot hat eigene Session.");
      // Refresh settings
      const s = await api.getSettings();
      setSettings(s);
    } catch (e) {
      setClaudeLoginError(e instanceof Error ? e.message : "Code exchange failed");
    } finally {
      setClaudeLoginLoading(false);
    }
  };

  // Provider status helpers
  const providerConfigured = (p: ModelProvider): boolean => {
    if (!settings) return false;
    if (p === "anthropic") return settings.auth_method !== "none";
    if (p === "bedrock") return settings.has_bedrock;
    if (p === "vertex") return settings.has_vertex;
    if (p === "foundry") return settings.has_foundry;
    return false;
  };

  const activeProvider = PROVIDERS.find((p) => p.id === provider)!;

  return (
    <div>
      <Header title="Settings" subtitle="Configure your AI Employee platform" />

      <motion.div
        className="px-8 py-8 max-w-5xl mx-auto space-y-6"
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        {/* ─── Section 1: Model Provider ─── */}
        <section>
          <div className="flex items-center gap-2 mb-3">
            <Cpu className="h-4 w-4 text-muted-foreground/60" />
            <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">
              Model Provider
            </h2>
          </div>

          {/* Provider Cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2.5 mb-4">
            {PROVIDERS.map((p) => {
              const Icon = p.icon;
              const active = provider === p.id;
              const configured = providerConfigured(p.id);
              return (
                <button
                  key={p.id}
                  onClick={() => setProvider(p.id)}
                  className={cn(
                    "relative flex flex-col items-start gap-2 rounded-xl border p-3.5 text-left transition-all duration-200",
                    active
                      ? `${p.bgColor} ring-1 ring-current/10`
                      : "border-foreground/[0.06] bg-card/40 hover:bg-card/80 hover:border-foreground/[0.1]"
                  )}
                >
                  <div className="flex items-center justify-between w-full">
                    <div
                      className={cn(
                        "flex h-8 w-8 items-center justify-center rounded-lg transition-colors",
                        active ? p.bgColor : "bg-foreground/[0.04]"
                      )}
                    >
                      <Icon className={cn("h-4 w-4", active ? p.color : "text-muted-foreground/50")} />
                    </div>
                    {configured && (
                      <CheckCircle2
                        className={cn(
                          "h-3.5 w-3.5",
                          active ? "text-emerald-400" : "text-emerald-400/50"
                        )}
                      />
                    )}
                  </div>
                  <div>
                    <p className={cn(
                      "text-xs font-semibold",
                      active ? "text-foreground" : "text-muted-foreground"
                    )}>
                      {p.short}
                    </p>
                    <p className="text-[10px] text-muted-foreground/50 leading-tight mt-0.5">
                      {p.id === "anthropic" ? "Direct API" :
                       p.id === "bedrock" ? "AWS" :
                       p.id === "vertex" ? "Google Cloud" : "Azure"}
                    </p>
                  </div>
                </button>
              );
            })}
          </div>

          {/* Active Provider: Credentials Card */}
          <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden">
            {/* Card Header */}
            <div className="flex items-center justify-between px-5 py-3.5 border-b border-foreground/[0.04]">
              <div className="flex items-center gap-3">
                <div className={cn("flex h-8 w-8 items-center justify-center rounded-lg", activeProvider.bgColor)}>
                  <activeProvider.icon className={cn("h-4 w-4", activeProvider.color)} />
                </div>
                <div>
                  <h3 className="text-sm font-semibold">{activeProvider.label}</h3>
                  <p className="text-[11px] text-muted-foreground/60">{activeProvider.description}</p>
                </div>
              </div>
              {providerConfigured(provider) ? (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 px-2.5 py-1 text-[10px] font-medium text-emerald-400">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                  Connected
                </span>
              ) : (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-zinc-500/10 border border-zinc-500/20 px-2.5 py-1 text-[10px] font-medium text-zinc-400">
                  <span className="h-1.5 w-1.5 rounded-full bg-zinc-500" />
                  Not configured
                </span>
              )}
            </div>

            {/* Card Body: Credentials */}
            <div className="p-5">
              {/* Anthropic Direct */}
              {provider === "anthropic" && (
                <div className="space-y-4">
                  <div className="grid grid-cols-2 gap-2 p-1 rounded-lg bg-foreground/[0.03]">
                    <button
                      onClick={() => setAuthMethod("api_key")}
                      className={cn(
                        "rounded-md px-3 py-2 text-xs font-medium transition-all",
                        authMethod === "api_key"
                          ? "bg-background shadow-sm text-foreground"
                          : "text-muted-foreground hover:text-foreground"
                      )}
                    >
                      <span className="flex items-center justify-center gap-1.5">
                        <Key className="h-3 w-3" />
                        API Key
                      </span>
                      <span className="block text-[10px] opacity-50 mt-0.5">Pay per token</span>
                    </button>
                    <button
                      onClick={() => setAuthMethod("oauth_token")}
                      className={cn(
                        "rounded-md px-3 py-2 text-xs font-medium transition-all",
                        authMethod === "oauth_token"
                          ? "bg-background shadow-sm text-foreground"
                          : "text-muted-foreground hover:text-foreground"
                      )}
                    >
                      <span className="flex items-center justify-center gap-1.5">
                        <Lock className="h-3 w-3" />
                        OAuth Token
                      </span>
                      <span className="block text-[10px] opacity-50 mt-0.5">Claude Pro/Team</span>
                    </button>
                  </div>
                  {authMethod === "api_key" ? (
                    <CredentialField
                      label="API Key"
                      type="password"
                      value={apiKey}
                      onChange={setApiKey}
                      placeholder="sk-ant-api03-..."
                      hint="Create an API key at console.anthropic.com"
                      mono
                    />
                  ) : (
                    <div className="space-y-3">
                      <button
                        onClick={handleClaudeLogin}
                        className="w-full flex items-center justify-center gap-2 rounded-xl bg-orange-500/10 border border-orange-500/20 px-4 py-3 text-sm font-medium text-orange-400 hover:bg-orange-500/20 transition-all duration-200"
                      >
                        <LogIn className="h-4 w-4" />
                        Mit Claude einloggen
                        <ExternalLink className="h-3 w-3 opacity-50" />
                      </button>
                      <p className="text-[10px] text-muted-foreground/40">
                        Erstellt eine eigene OAuth-Session für den Bot. Kein Konflikt mit VS Code.
                      </p>
                      {settings?.has_oauth_token && (
                        <div className="flex items-center gap-1.5 text-[11px] text-emerald-400">
                          <CheckCircle2 className="h-3 w-3" />
                          OAuth Session aktiv
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}

              {/* Amazon Bedrock */}
              {provider === "bedrock" && (
                <div className="space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    <CredentialField
                      label="AWS Access Key ID"
                      type="password"
                      value={awsAccessKey}
                      onChange={setAwsAccessKey}
                      placeholder="AKIA..."
                      mono
                    />
                    <CredentialField
                      label="AWS Secret Access Key"
                      type="password"
                      value={awsSecretKey}
                      onChange={setAwsSecretKey}
                      placeholder="Secret key..."
                      mono
                    />
                  </div>
                  <SelectField
                    label="Region"
                    value={awsRegion}
                    onChange={setAwsRegion}
                    options={AWS_REGIONS.map((r) => ({ value: r, label: r }))}
                  />
                  <p className="text-[10px] text-muted-foreground/40 pt-1">
                    Ensure Claude models are enabled in your AWS Bedrock console for the selected region.
                  </p>
                </div>
              )}

              {/* Google Vertex AI */}
              {provider === "vertex" && (
                <div className="space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    <CredentialField
                      label="GCP Project ID"
                      value={vertexProjectId}
                      onChange={setVertexProjectId}
                      placeholder="my-project-123456"
                    />
                    <SelectField
                      label="Region"
                      value={vertexRegion}
                      onChange={setVertexRegion}
                      options={VERTEX_REGIONS.map((r) => ({ value: r, label: r }))}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-[11px] font-medium text-muted-foreground/70">Service Account JSON</label>
                    <textarea
                      value={vertexCredentials}
                      onChange={(e) => setVertexCredentials(e.target.value)}
                      placeholder='{"type": "service_account", ...}'
                      rows={3}
                      className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-xs font-mono outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all placeholder:text-muted-foreground/25 resize-none"
                    />
                  </div>
                  <p className="text-[10px] text-muted-foreground/40 pt-1">
                    Paste the full JSON of your GCP service account key. Vertex AI API must be enabled.
                  </p>
                </div>
              )}

              {/* Microsoft Foundry */}
              {provider === "foundry" && (
                <div className="space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    <CredentialField
                      label="Foundry Resource Name"
                      value={foundryResource}
                      onChange={setFoundryResource}
                      placeholder="my-foundry-resource"
                    />
                    <CredentialField
                      label="API Key"
                      type="password"
                      value={foundryApiKey}
                      onChange={setFoundryApiKey}
                      placeholder="Foundry API key..."
                      mono
                    />
                  </div>
                  <p className="text-[10px] text-muted-foreground/40 pt-1">
                    Configure in Azure AI Foundry portal. Ensure Claude models are deployed in your resource.
                  </p>
                </div>
              )}
            </div>

            {/* Footer: configured providers info */}
            <div className="px-5 py-3 bg-foreground/[0.015] border-t border-foreground/[0.04]">
              <p className="text-[10px] text-muted-foreground/40">
                Modell-Auswahl erfolgt pro Agent in den Agent-Settings.
              </p>
            </div>
          </div>
        </section>

        {/* ─── Section 2: Agent Configuration ─── */}
        <section>
          <div className="flex items-center gap-2 mb-3">
            <Bot className="h-4 w-4 text-muted-foreground/60" />
            <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">
              Agent Configuration
            </h2>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-4">
              <div className="flex items-center gap-2 mb-3">
                <Gauge className="h-3.5 w-3.5 text-muted-foreground/50" />
                <label className="text-[11px] font-medium text-muted-foreground/70">Max Turns per Task</label>
              </div>
              <input
                type="number"
                value={maxTurns}
                onChange={(e) => setMaxTurns(Number(e.target.value))}
                className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm font-medium outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all tabular-nums"
              />
              <p className="text-[10px] text-muted-foreground/40 mt-1.5">
                Maximum API round-trips per task execution.
              </p>
            </div>

            <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-4">
              <div className="flex items-center gap-2 mb-3">
                <Bot className="h-3.5 w-3.5 text-muted-foreground/50" />
                <label className="text-[11px] font-medium text-muted-foreground/70">Max Concurrent Agents</label>
              </div>
              <input
                type="number"
                value={maxAgents}
                onChange={(e) => setMaxAgents(Number(e.target.value))}
                className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm font-medium outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all tabular-nums"
              />
              <p className="text-[10px] text-muted-foreground/40 mt-1.5">
                Maximum number of agents running simultaneously.
              </p>
            </div>
          </div>
        </section>

        {/* ─── Section 3: Agent Templates ─── */}
        <section>
          <div className="flex items-center gap-2 mb-3">
            <Layers className="h-4 w-4 text-muted-foreground/60" />
            <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">
              Agent Templates
            </h2>
          </div>
          <TemplateManager isAdmin={isAdmin} />
        </section>

        {/* ─── Section 4: Notifications ─── */}
        <section>
          <div className="flex items-center gap-2 mb-3">
            <MessageSquare className="h-4 w-4 text-muted-foreground/60" />
            <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">
              Notifications
            </h2>
          </div>

          <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden">
            <div className="flex items-center justify-between px-5 py-3.5 border-b border-foreground/[0.04]">
              <div className="flex items-center gap-3">
                <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-cyan-500/10 border border-cyan-500/20">
                  <MessageSquare className="h-4 w-4 text-cyan-400" />
                </div>
                <div>
                  <h3 className="text-sm font-semibold">Telegram Bot</h3>
                  <p className="text-[11px] text-muted-foreground/60">Receive notifications via Telegram</p>
                </div>
              </div>
              {settings?.has_telegram ? (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 px-2.5 py-1 text-[10px] font-medium text-emerald-400">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                  Connected
                </span>
              ) : (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-zinc-500/10 border border-zinc-500/20 px-2.5 py-1 text-[10px] font-medium text-zinc-400">
                  <span className="h-1.5 w-1.5 rounded-full bg-zinc-500" />
                  Not configured
                </span>
              )}
            </div>
            <div className="p-5">
              <div className="grid grid-cols-2 gap-3">
                <CredentialField
                  label="Bot Token"
                  type="password"
                  value={telegramToken}
                  onChange={setTelegramToken}
                  placeholder="123456:ABC-DEF..."
                  mono
                />
                <CredentialField
                  label="Chat ID"
                  value={telegramChatId}
                  onChange={setTelegramChatId}
                  placeholder="-1001234567890"
                />
              </div>
              <p className="text-[10px] text-muted-foreground/40 mt-3">
                Create a bot via @BotFather on Telegram and add it to your group or channel.
              </p>
            </div>
          </div>
        </section>

        {/* ─── Section 5: OAuth Integrations ─── */}
        {isAdmin && (
          <section>
            <div className="flex items-center gap-2 mb-3">
              <Globe className="h-4 w-4 text-muted-foreground/60" />
              <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">
                OAuth Integrations
              </h2>
            </div>

            <div className="space-y-3">
              {/* Google */}
              <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden">
                <div className="flex items-center justify-between px-5 py-3.5 border-b border-foreground/[0.04]">
                  <div className="flex items-center gap-3">
                    <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-red-500/10 border border-red-500/20">
                      <Globe className="h-4 w-4 text-red-400" />
                    </div>
                    <div>
                      <h3 className="text-sm font-semibold">Google OAuth</h3>
                      <p className="text-[11px] text-muted-foreground/60">Gmail, Calendar, Drive</p>
                    </div>
                  </div>
                  {settings?.has_google_oauth ? (
                    <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 px-2.5 py-1 text-[10px] font-medium text-emerald-400">
                      <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                      Configured
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1.5 rounded-full bg-zinc-500/10 border border-zinc-500/20 px-2.5 py-1 text-[10px] font-medium text-zinc-400">
                      <span className="h-1.5 w-1.5 rounded-full bg-zinc-500" />
                      Not configured
                    </span>
                  )}
                </div>
                <div className="p-5 grid grid-cols-2 gap-3">
                  <CredentialField
                    label="Client ID"
                    value={googleClientId}
                    onChange={setGoogleClientId}
                    placeholder="123456.apps.googleusercontent.com"
                    mono
                  />
                  <CredentialField
                    label="Client Secret"
                    type="password"
                    value={googleClientSecret}
                    onChange={setGoogleClientSecret}
                    placeholder="GOCSPX-..."
                    mono
                  />
                </div>
              </div>

              {/* Microsoft */}
              <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden">
                <div className="flex items-center justify-between px-5 py-3.5 border-b border-foreground/[0.04]">
                  <div className="flex items-center gap-3">
                    <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-500/10 border border-blue-500/20">
                      <Globe className="h-4 w-4 text-blue-400" />
                    </div>
                    <div>
                      <h3 className="text-sm font-semibold">Microsoft OAuth</h3>
                      <p className="text-[11px] text-muted-foreground/60">Outlook, OneDrive, Teams</p>
                    </div>
                  </div>
                  {settings?.has_microsoft_oauth ? (
                    <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 px-2.5 py-1 text-[10px] font-medium text-emerald-400">
                      <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                      Configured
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1.5 rounded-full bg-zinc-500/10 border border-zinc-500/20 px-2.5 py-1 text-[10px] font-medium text-zinc-400">
                      <span className="h-1.5 w-1.5 rounded-full bg-zinc-500" />
                      Not configured
                    </span>
                  )}
                </div>
                <div className="p-5 grid grid-cols-2 gap-3">
                  <CredentialField
                    label="Application (Client) ID"
                    value={microsoftClientId}
                    onChange={setMicrosoftClientId}
                    placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                    mono
                  />
                  <CredentialField
                    label="Client Secret"
                    type="password"
                    value={microsoftClientSecret}
                    onChange={setMicrosoftClientSecret}
                    placeholder="Client secret value..."
                    mono
                  />
                </div>
              </div>

              {/* Apple */}
              <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden">
                <div className="flex items-center justify-between px-5 py-3.5 border-b border-foreground/[0.04]">
                  <div className="flex items-center gap-3">
                    <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-zinc-500/10 border border-zinc-500/20">
                      <Globe className="h-4 w-4 text-zinc-300" />
                    </div>
                    <div>
                      <h3 className="text-sm font-semibold">Apple Sign In</h3>
                      <p className="text-[11px] text-muted-foreground/60">Apple ID authentication</p>
                    </div>
                  </div>
                  {settings?.has_apple_oauth ? (
                    <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 px-2.5 py-1 text-[10px] font-medium text-emerald-400">
                      <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                      Configured
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1.5 rounded-full bg-zinc-500/10 border border-zinc-500/20 px-2.5 py-1 text-[10px] font-medium text-zinc-400">
                      <span className="h-1.5 w-1.5 rounded-full bg-zinc-500" />
                      Not configured
                    </span>
                  )}
                </div>
                <div className="p-5 grid grid-cols-2 gap-3">
                  <CredentialField
                    label="Services ID"
                    value={appleClientId}
                    onChange={setAppleClientId}
                    placeholder="com.example.app"
                    mono
                  />
                  <CredentialField
                    label="Team ID"
                    value={appleTeamId}
                    onChange={setAppleTeamId}
                    placeholder="ABCDE12345"
                    mono
                  />
                </div>
              </div>
            </div>
          </section>
        )}

        {/* ─── Section 6: Access Control (Admin only) ─── */}
        {isAdmin && (
          <section>
            <div className="flex items-center gap-2 mb-3">
              <Shield className="h-4 w-4 text-muted-foreground/60" />
              <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">
                Access Control
              </h2>
            </div>

            <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-500/10 border border-emerald-500/20">
                    <UserPlus className="h-4 w-4 text-emerald-400" />
                  </div>
                  <div>
                    <p className="text-sm font-semibold">
                      {registrationOpen ? "Registration Open" : "Registration Closed"}
                    </p>
                    <p className="text-[11px] text-muted-foreground/60">
                      {registrationOpen
                        ? "Anyone can create an account on the login page."
                        : "Only admins can create new user accounts."}
                    </p>
                  </div>
                </div>
                <button
                  onClick={() => setRegistrationOpen(!registrationOpen)}
                  className={cn(
                    "relative inline-flex h-6 w-11 items-center rounded-full transition-colors",
                    registrationOpen ? "bg-emerald-500" : "bg-zinc-600"
                  )}
                >
                  <span
                    className={cn(
                      "inline-block h-4 w-4 transform rounded-full bg-white transition-transform",
                      registrationOpen ? "translate-x-6" : "translate-x-1"
                    )}
                  />
                </button>
              </div>
            </div>
          </section>
        )}

        {/* ─── Save Button ─── */}
        <div className="flex items-center gap-3 pt-2 pb-8">
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
        {/* ─── Claude OAuth Code Paste Modal ─── */}
        {claudeLoginOpen && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              className="w-full max-w-md rounded-2xl border border-foreground/[0.08] bg-card p-6 shadow-2xl"
            >
              <h3 className="text-base font-semibold mb-1">Claude Login Code eingeben</h3>
              <p className="text-xs text-muted-foreground/60 mb-4">
                Ein neuer Tab wurde geöffnet. Logge dich dort ein und kopiere den angezeigten Code hierher.
              </p>

              <input
                type="text"
                value={claudeCode}
                onChange={(e) => setClaudeCode(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleClaudeCodeSubmit()}
                placeholder="Code hier einfügen..."
                autoFocus
                className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-3 text-sm font-mono outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all placeholder:text-muted-foreground/25"
              />

              {claudeLoginError && (
                <p className="text-xs text-red-400 mt-2 flex items-center gap-1">
                  <AlertCircle className="h-3 w-3" />
                  {claudeLoginError}
                </p>
              )}

              <div className="flex gap-2 mt-4">
                <button
                  onClick={handleClaudeCodeSubmit}
                  disabled={claudeLoginLoading || !claudeCode.trim()}
                  className="flex-1 inline-flex items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 hover:bg-primary/90 disabled:opacity-50 transition-all"
                >
                  {claudeLoginLoading ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <CheckCircle2 className="h-4 w-4" />
                  )}
                  Verbinden
                </button>
                <button
                  onClick={() => { setClaudeLoginOpen(false); setClaudeCode(""); setClaudeLoginError(""); }}
                  className="rounded-xl px-4 py-2 text-sm text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-all"
                >
                  Abbrechen
                </button>
              </div>
            </motion.div>
          </div>
        )}
      </motion.div>
    </div>
  );
}

// ── Reusable Components ─────────────────────────────────────

function CredentialField({
  label,
  type = "text",
  value,
  onChange,
  placeholder,
  hint,
  mono,
}: {
  label: string;
  type?: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  hint?: string;
  mono?: boolean;
}) {
  return (
    <div className="space-y-1.5">
      <label className="text-[11px] font-medium text-muted-foreground/70">{label}</label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className={cn(
          "w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all placeholder:text-muted-foreground/25",
          mono && "font-mono text-xs"
        )}
      />
      {hint && <p className="text-[10px] text-muted-foreground/40">{hint}</p>}
    </div>
  );
}

function SelectField({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <div className="space-y-1.5">
      <label className="text-[11px] font-medium text-muted-foreground/70">{label}</label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all appearance-none"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </div>
  );
}
