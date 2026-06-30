"use client";

import { useState, useEffect, useMemo } from "react";
import { motion } from "framer-motion";
import {
  Key, MessageSquare, Save, Loader2,
  CheckCircle2, AlertCircle, Shield, Bot, Gauge,
  UserPlus, Cloud, Server, Lock, Globe, Cpu, Layers,
  ExternalLink, Copy, LogIn, Info, ChevronRight, Sparkles, Network,
  Plug, Mic, AlertTriangle,
} from "lucide-react";
import { useAuthStore } from "@/lib/auth";
import { Header } from "@/components/layout/header";
import { TemplateManager } from "@/components/settings/template-manager";
import { VoiceSettings } from "@/components/settings/voice-settings";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import { useConfirm } from "@/components/ui/dialog-provider";
import type { Settings, ModelProvider } from "@/lib/types";

// ── Model options per provider ──────────────────────────────
const MODEL_OPTIONS: Record<ModelProvider, { value: string; label: string; tier: string }[]> = {
  anthropic: [
    { value: "claude-opus-4-8", label: "Opus 4.8 (Latest)", tier: "Most Powerful" },
    { value: "claude-sonnet-4-6", label: "Sonnet 4.6", tier: "Balanced" },
    { value: "claude-haiku-4-5", label: "Haiku 4.5", tier: "Fast" },
    { value: "claude-opus-4-7", label: "Opus 4.7", tier: "Legacy" },
    { value: "claude-opus-4-6", label: "Opus 4.6", tier: "Legacy" },
    { value: "claude-sonnet-4-5", label: "Sonnet 4.5", tier: "Legacy" },
  ],
  bedrock: [
    { value: "anthropic.claude-opus-4-8", label: "Opus 4.8 (Latest)", tier: "Most Powerful" },
    { value: "anthropic.claude-sonnet-4-6", label: "Sonnet 4.6", tier: "Balanced" },
    { value: "anthropic.claude-haiku-4-5-20251001-v1:0", label: "Haiku 4.5", tier: "Fast" },
    { value: "us.anthropic.claude-opus-4-7-v1:0", label: "Opus 4.7", tier: "Legacy" },
    { value: "anthropic.claude-opus-4-6-v1", label: "Opus 4.6", tier: "Legacy" },
    { value: "anthropic.claude-sonnet-4-5-20250929-v1:0", label: "Sonnet 4.5", tier: "Legacy" },
  ],
  vertex: [
    { value: "claude-opus-4-8", label: "Opus 4.8 (Latest)", tier: "Most Powerful" },
    { value: "claude-sonnet-4-6", label: "Sonnet 4.6", tier: "Balanced" },
    { value: "claude-haiku-4-5@20251001", label: "Haiku 4.5", tier: "Fast" },
    { value: "claude-opus-4-7", label: "Opus 4.7", tier: "Legacy" },
    { value: "claude-opus-4-6", label: "Opus 4.6", tier: "Legacy" },
    { value: "claude-sonnet-4-5@20250929", label: "Sonnet 4.5", tier: "Legacy" },
  ],
  foundry: [
    { value: "claude-opus-4-8", label: "Opus 4.8 (Latest)", tier: "Most Powerful" },
    { value: "claude-sonnet-4-6", label: "Sonnet 4.6", tier: "Balanced" },
    { value: "claude-haiku-4-5", label: "Haiku 4.5", tier: "Fast" },
    { value: "claude-opus-4-7", label: "Opus 4.7", tier: "Legacy" },
    { value: "claude-opus-4-6", label: "Opus 4.6", tier: "Legacy" },
    { value: "claude-sonnet-4-5", label: "Sonnet 4.5", tier: "Legacy" },
  ],
  codex: [
    { value: "gpt-5.5", label: "GPT-5.5", tier: "Codex" },
    { value: "gpt-5.4", label: "GPT-5.4", tier: "Codex" },
    { value: "gpt-5-codex", label: "GPT-5 Codex", tier: "Codex" },
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
  {
    id: "codex",
    label: "OpenAI Codex",
    short: "Codex",
    icon: Sparkles,
    color: "text-emerald-400",
    bgColor: "bg-emerald-500/10 border-emerald-500/20",
    description: "Codex CLI via ChatGPT subscription login",
  },
];

export function SettingsView({ embedded = false }: { embedded?: boolean }) {
  const confirm = useConfirm();
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
  const [defaultModel, setDefaultModel] = useState("claude-sonnet-4-6");
  const [maxTurns, setMaxTurns] = useState(100);
  const [maxAgents, setMaxAgents] = useState(10);
  const [registrationOpen, setRegistrationOpen] = useState(true);
  // OAuth integration credentials
  const [googleClientId, setGoogleClientId] = useState("");
  const [googleClientSecret, setGoogleClientSecret] = useState("");
  const [microsoftClientId, setMicrosoftClientId] = useState("");
  const [microsoftClientSecret, setMicrosoftClientSecret] = useState("");
  // On-prem Exchange (EWS) admin config
  const [exchangeServerUrl, setExchangeServerUrl] = useState("");
  const [exchangeAuthMode, setExchangeAuthMode] = useState("service_account");
  const [exchangeSvcUser, setExchangeSvcUser] = useState("");
  const [exchangeSvcPass, setExchangeSvcPass] = useState("");
  const [exchangeTenantId, setExchangeTenantId] = useState("");
  const [msGuideExpanded, setMsGuideExpanded] = useState(false);
  const [msgraphExtSaving, setMsgraphExtSaving] = useState(false);
  const [appleClientId, setAppleClientId] = useState("");
  const [appleTeamId, setAppleTeamId] = useState("");
  // Claude OAuth login
  const [claudeLoginOpen, setClaudeLoginOpen] = useState(false);
  const [claudeAuthState, setClaudeAuthState] = useState("");
  const [claudeCode, setClaudeCode] = useState("");
  const [claudeLoginLoading, setClaudeLoginLoading] = useState(false);
  const [claudeLoginError, setClaudeLoginError] = useState("");
  // Codex ChatGPT login
  const [codexLoginOpen, setCodexLoginOpen] = useState(false);
  const [codexAuthJson, setCodexAuthJson] = useState("");
  const [codexDeviceSessionId, setCodexDeviceSessionId] = useState("");
  const [codexDeviceUrl, setCodexDeviceUrl] = useState("");
  const [codexDeviceCode, setCodexDeviceCode] = useState("");
  const [codexDeviceStatus, setCodexDeviceStatus] = useState<api.DeviceAuthStatus["status"] | "idle">("idle");
  const [codexLoginLoading, setCodexLoginLoading] = useState(false);
  const [codexLoginError, setCodexLoginError] = useState("");
  // UI state
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [secTab, setSecTab] = useState<"modelle" | "integrationen" | "voice" | "system">("modelle");
  const user = useAuthStore((s) => s.user);

  const toggleMsgraphExt = async (enabled: boolean) => {
    setMsgraphExtSaving(true);
    try {
      await api.setMsgraphMcpExternal(enabled);
      setSettings(await api.getSettings());
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Konnte MCP-Exposition nicht ändern");
    } finally {
      setMsgraphExtSaving(false);
    }
  };
  const [ssoOnlySaving, setSsoOnlySaving] = useState(false);
  const toggleSsoOnly = async (enabled: boolean) => {
    setSsoOnlySaving(true);
    try {
      await api.updateSettings({ sso_only_login: enabled });
      setSettings(await api.getSettings());
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Konnte SSO-Only nicht ändern");
    } finally {
      setSsoOnlySaving(false);
    }
  };
  const [dreamingSaving, setDreamingSaving] = useState(false);
  const toggleDreaming = async (enabled: boolean) => {
    setDreamingSaving(true);
    try {
      await api.updateSettings({ dreaming_enabled: enabled });
      setSettings(await api.getSettings());
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Konnte Dreaming nicht ändern");
    } finally {
      setDreamingSaving(false);
    }
  };
  const [plannerPlanId, setPlannerPlanId] = useState("");
  const [plannerSaving, setPlannerSaving] = useState(false);
  const savePlannerPlan = async () => {
    setPlannerSaving(true);
    try {
      await api.updateSettings({ meeting_planner_plan_id: plannerPlanId.trim() });
      setSettings(await api.getSettings());
      setMessage("Meeting → Planner gespeichert");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Konnte Planner-Plan nicht speichern");
    } finally {
      setPlannerSaving(false);
    }
  };
  const [approvalSaving, setApprovalSaving] = useState(false);
  const toggleRequireApproval = async (enabled: boolean) => {
    setApprovalSaving(true);
    try {
      await api.updateSettings({ require_user_approval: enabled });
      setSettings(await api.getSettings());
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Konnte Freischaltungs-Pflicht nicht ändern");
    } finally {
      setApprovalSaving(false);
    }
  };
  const [revokeMsgraphSaving, setRevokeMsgraphSaving] = useState(false);
  const toggleRevokeMsgraph = async (enabled: boolean) => {
    setRevokeMsgraphSaving(true);
    try {
      await api.updateSettings({ revoke_msgraph_on_logout: enabled });
      setSettings(await api.getSettings());
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Konnte Logout-Token-Einstellung nicht ändern");
    } finally {
      setRevokeMsgraphSaving(false);
    }
  };
  const isAdmin = user?.role === "admin";
  // License state
  const [license, setLicense] = useState<import("@/lib/api").License | null>(null);
  const [licenseKeyInput, setLicenseKeyInput] = useState("");
  const [licenseBusy, setLicenseBusy] = useState(false);
  const [licenseError, setLicenseError] = useState("");

  // Available models for current provider
  const modelOptions = useMemo(() => MODEL_OPTIONS[provider] || MODEL_OPTIONS.anthropic, [provider]);

  const loadLicense = async () => {
    try {
      const lic = await api.getLicenseStatus();
      setLicense(lic);
    } catch {
      // ignore — fallback community tier displayed
    }
  };

  const handleApplyLicense = async () => {
    if (!licenseKeyInput.trim()) return;
    setLicenseBusy(true);
    setLicenseError("");
    try {
      await api.applyLicense(licenseKeyInput.trim());
      setLicenseKeyInput("");
      await loadLicense();
    } catch (e) {
      setLicenseError(e instanceof Error ? e.message : "Invalid license");
    } finally {
      setLicenseBusy(false);
    }
  };

  const handleRemoveLicense = async () => {
    const ok = await confirm({
      title: "License entfernen?",
      message: "Enterprise-Features werden deaktiviert.",
      variant: "destructive",
      confirmLabel: "Entfernen",
    });
    if (!ok) return;
    setLicenseBusy(true);
    try {
      await api.removeLicense();
      await loadLicense();
    } finally {
      setLicenseBusy(false);
    }
  };

  useEffect(() => {
    loadLicense();
  }, []);

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
      setPlannerPlanId(s.meeting_planner_plan_id || "");
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

  useEffect(() => {
    if (!codexLoginOpen || !codexDeviceSessionId || codexDeviceStatus !== "pending") return;

    const timer = window.setInterval(async () => {
      try {
        const status = await api.getDeviceAuthStatus("codex", codexDeviceSessionId);
        setCodexDeviceStatus(status.status);
        if (status.status === "connected") {
          window.clearInterval(timer);
          setCodexLoginOpen(false);
          setCodexAuthJson("");
          setCodexDeviceSessionId("");
          setCodexDeviceUrl("");
          setCodexDeviceCode("");
          await api.updateSettings({ model_provider: "codex", default_model: defaultModel });
          setProvider("codex");
          setMessage("Codex Login erfolgreich! Bot nutzt jetzt die ChatGPT/Codex-Session.");
          const s = await api.getSettings();
          setSettings(s);
        } else if (status.status === "error" || status.status === "expired" || status.status === "cancelled") {
          window.clearInterval(timer);
          setCodexLoginError(status.error || `Codex Login ${status.status}`);
        }
      } catch (e) {
        setCodexLoginError(e instanceof Error ? e.message : "Codex Login Status konnte nicht gelesen werden");
      }
    }, 2000);

    return () => window.clearInterval(timer);
  }, [codexLoginOpen, codexDeviceSessionId, codexDeviceStatus, defaultModel]);

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
      // On-prem Exchange (only written when the server URL is provided)
      if (exchangeServerUrl) {
        data.exchange_server_url = exchangeServerUrl;
        data.exchange_auth_mode = exchangeAuthMode;
        if (exchangeSvcUser) data.exchange_service_account_user = exchangeSvcUser;
        if (exchangeSvcPass) data.exchange_service_account_password = exchangeSvcPass;
        if (exchangeTenantId) data.exchange_tenant_id = exchangeTenantId;
      }
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
      setExchangeSvcPass("");
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

  const handleCodexLogin = async () => {
    setCodexLoginLoading(true);
    setCodexLoginError("");
    setCodexAuthJson("");
    try {
      const session = await api.startDeviceAuth("codex");
      setCodexDeviceSessionId(session.session_id);
      setCodexDeviceUrl(session.verification_uri);
      setCodexDeviceCode(session.user_code);
      setCodexDeviceStatus("pending");
      setCodexLoginOpen(true);
      window.open(session.verification_uri, "_blank");
    } catch (e) {
      setCodexLoginError(e instanceof Error ? e.message : "Codex device login failed");
      setCodexLoginOpen(true);
    } finally {
      setCodexLoginLoading(false);
    }
  };

  const handleCodexAuthSubmit = async () => {
    if (!codexAuthJson.trim()) return;
    setCodexLoginLoading(true);
    setCodexLoginError("");
    try {
      await api.saveAuthJson("codex", codexAuthJson.trim());
      await api.updateSettings({ model_provider: "codex", default_model: defaultModel });
      setProvider("codex");
      setCodexLoginOpen(false);
      setCodexAuthJson("");
      setMessage("Codex Login erfolgreich! Bot nutzt jetzt die ChatGPT/Codex-Session.");
      const s = await api.getSettings();
      setSettings(s);
    } catch (e) {
      setCodexLoginError(e instanceof Error ? e.message : "Codex auth import failed");
    } finally {
      setCodexLoginLoading(false);
    }
  };

  const handleCodexCancel = async () => {
    if (codexDeviceSessionId && codexDeviceStatus === "pending") {
      try {
        await api.cancelDeviceAuth("codex", codexDeviceSessionId);
      } catch {
        // Best-effort cleanup only.
      }
    }
    setCodexLoginOpen(false);
    setCodexAuthJson("");
    setCodexLoginError("");
    setCodexDeviceSessionId("");
    setCodexDeviceUrl("");
    setCodexDeviceCode("");
    setCodexDeviceStatus("idle");
  };

  // Provider status helpers
  const providerConfigured = (p: ModelProvider): boolean => {
    if (!settings) return false;
    if (p === "anthropic") return settings.auth_method !== "none";
    if (p === "bedrock") return settings.has_bedrock;
    if (p === "vertex") return settings.has_vertex;
    if (p === "foundry") return settings.has_foundry;
    if (p === "codex") return settings.has_codex_oauth;
    return false;
  };

  const activeProvider = PROVIDERS.find((p) => p.id === provider)!;

  return (
    <div>
      {!embedded && <Header title="Settings" subtitle="Configure your AI Employee platform" />}

      <motion.div
        className="px-8 py-8 max-w-5xl mx-auto space-y-6"
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        {/* ─── Sub-tab navigation ─── */}
        <div className="flex gap-1 overflow-x-auto rounded-xl border border-border/50 bg-card/50 p-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {([
            { id: "modelle" as const, label: "Modelle", icon: Cpu },
            { id: "integrationen" as const, label: "Integrationen", icon: Plug },
            { id: "voice" as const, label: "Voice", icon: Mic },
            { id: "system" as const, label: "System", icon: Shield },
          ]).map((t) => {
            const Icon = t.icon;
            return (
              <button
                key={t.id}
                onClick={() => setSecTab(t.id)}
                className={cn(
                  "flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-2 text-sm font-medium transition-all",
                  secTab === t.id
                    ? "bg-accent text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                <Icon className="h-4 w-4" />
                {t.label}
              </button>
            );
          })}
        </div>

        {/* ─── Tab: Modelle ─── */}
        {secTab === "modelle" && (
        <div className="space-y-6">
        {/* ─── Section 1: Model Provider ─── */}
        <section>
          <div className="flex items-center gap-2 mb-3">
            <Cpu className="h-4 w-4 text-muted-foreground/60" />
            <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">
              Model Provider
            </h2>
          </div>

          {/* Provider Cards */}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-2.5 mb-4">
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
                       p.id === "vertex" ? "Google Cloud" :
                       p.id === "codex" ? "ChatGPT" : "Azure"}
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

              {/* OpenAI Codex */}
              {provider === "codex" && (
                <div className="space-y-3">
                  <div className="grid grid-cols-1 gap-2 p-1 rounded-lg bg-foreground/[0.03]">
                    <div className="rounded-md bg-background shadow-sm px-3 py-2 text-xs font-medium text-foreground">
                      <span className="flex items-center justify-center gap-1.5">
                        <Sparkles className="h-3 w-3" />
                        ChatGPT Login
                      </span>
                      <span className="block text-center text-[10px] opacity-50 mt-0.5">
                        Nutzt deine Codex/ChatGPT Subscription, keinen API-Key
                      </span>
                    </div>
                  </div>
                  <button
                    onClick={handleCodexLogin}
                    disabled={codexLoginLoading}
                    className="w-full flex items-center justify-center gap-2 rounded-xl bg-emerald-500/10 border border-emerald-500/20 px-4 py-3 text-sm font-medium text-emerald-400 hover:bg-emerald-500/20 transition-all duration-200"
                  >
                    {codexLoginLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <LogIn className="h-4 w-4" />}
                    Mit ChatGPT/Codex einloggen
                  </button>
                  <p className="text-[10px] text-muted-foreground/40">
                    Öffnet den ChatGPT Device-Login im Browser. Nach der Autorisierung speichert der
                    Orchestrator die Codex-Session verschlüsselt und stellt sie Agent-Containern als CODEX_HOME bereit.
                  </p>
                  {settings?.has_codex_oauth && (
                    <div className="flex items-center gap-1.5 text-[11px] text-emerald-400">
                      <CheckCircle2 className="h-3 w-3" />
                      Codex ChatGPT Session aktiv
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
        </div>
        )}

        {/* ─── Tab: Integrationen ─── */}
        {secTab === "integrationen" && (
        <div className="space-y-6">
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
        </div>
        )}

        {/* ─── Tab: Voice ─── */}
        {secTab === "voice" && (
        <div className="space-y-6">
        {/* ─── Voice Live-Sessions ─── */}
        {isAdmin && <VoiceSettings />}
        </div>
        )}

        {/* ─── Tab: System ─── */}
        {secTab === "system" && (
        <div className="space-y-6">
        {/* ─── Automatisierung ─── */}
        {isAdmin && (
          <section>
            <div className="flex items-center gap-2 mb-3">
              <Shield className="h-4 w-4 text-muted-foreground/60" />
              <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">
                Automatisierung
              </h2>
            </div>
            <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm divide-y divide-foreground/[0.04]">
              <div className="flex items-center justify-between gap-3 px-5 py-4">
                <div className="min-w-0">
                  <div className="text-sm font-medium">&bdquo;Dreaming&ldquo;-Memory</div>
                  <p className="mt-0.5 text-[11px] text-muted-foreground/60">
                    Aktualisiert stündlich das adaptive Nutzerprofil aus den Memories (heuristisch, ohne LLM-Kosten).
                  </p>
                </div>
                <button
                  onClick={() => toggleDreaming(!settings?.dreaming_enabled)}
                  disabled={dreamingSaving}
                  className={cn(
                    "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors",
                    settings?.dreaming_enabled ? "bg-emerald-500" : "bg-foreground/[0.1]",
                    dreamingSaving && "opacity-40 cursor-not-allowed",
                  )}
                >
                  <span className={cn(
                    "inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform",
                    settings?.dreaming_enabled ? "translate-x-6" : "translate-x-1",
                  )} />
                </button>
              </div>
              <div className="px-5 py-4">
                <div className="text-sm font-medium">Meeting → MS Planner</div>
                <p className="mt-0.5 mb-2 text-[11px] text-muted-foreground/60">
                  Plan-ID, in die erkannte Meeting-Action-Items gespiegelt werden (leer = aus).
                </p>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={plannerPlanId}
                    onChange={(e) => setPlannerPlanId(e.target.value)}
                    placeholder={settings?.meeting_planner_plan_id || "Planner-Plan-ID"}
                    className="flex-1 rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-2 text-sm font-mono outline-none focus:border-primary/50"
                  />
                  <button
                    onClick={savePlannerPlan}
                    disabled={plannerSaving}
                    className="rounded-lg bg-foreground/[0.06] hover:bg-foreground/[0.10] px-3 text-xs disabled:opacity-40"
                  >
                    Speichern
                  </button>
                </div>
              </div>
            </div>
          </section>
        )}
        {/* ─── License ─── */}
        {isAdmin && (
          <section>
            <div className="flex items-center gap-2 mb-3">
              <Lock className="h-4 w-4 text-muted-foreground/60" />
              <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">
                License
              </h2>
            </div>
            <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
              {license && (
                <div className="mb-4 flex items-center justify-between">
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <span className={cn(
                        "inline-flex items-center rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider",
                        license.tier === "enterprise" ? "bg-purple-500/10 text-purple-400 border-purple-500/20" :
                        license.tier === "business" ? "bg-blue-500/10 text-blue-400 border-blue-500/20" :
                        license.tier === "team" ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" :
                        "bg-zinc-500/10 text-zinc-400 border-zinc-500/20"
                      )}>
                        {license.tier}
                      </span>
                      {license.valid && !license.is_expired && (
                        <span className="inline-flex items-center gap-1 text-[11px] text-emerald-400">
                          <CheckCircle2 className="h-3 w-3" />
                          Active
                        </span>
                      )}
                      {license.is_expired && (
                        <span className="inline-flex items-center gap-1 text-[11px] text-red-400">
                          <AlertCircle className="h-3 w-3" />
                          Expired
                        </span>
                      )}
                    </div>
                    <p className="text-sm text-muted-foreground">
                      {license.tier === "community"
                        ? "Community Edition — all core features included, free for internal business use"
                        : `Licensed to ${license.issued_to}`}
                    </p>
                    {license.expires_at && (
                      <p className="text-[11px] text-muted-foreground/60 mt-1">
                        Expires: {new Date(license.expires_at).toLocaleDateString("de-DE")}
                      </p>
                    )}
                    <p className="text-[10px] text-muted-foreground/50 mt-1.5">
                      {license.features.length} features enabled
                    </p>
                  </div>
                  {license.tier !== "community" && license.license_id !== "community-default" && (
                    <button
                      onClick={handleRemoveLicense}
                      disabled={licenseBusy}
                      className="text-[11px] text-red-400 hover:text-red-300 underline underline-offset-2"
                    >
                      Remove license
                    </button>
                  )}
                </div>
              )}
              {(license?.tier === "community" || license?.is_expired || !license?.valid) && (
                <div className="space-y-3 border-t border-foreground/[0.04] pt-4">
                  <div>
                    <label className="text-[11px] font-medium text-muted-foreground/70 mb-1.5 block">
                      License Key
                    </label>
                    <textarea
                      value={licenseKeyInput}
                      onChange={(e) => setLicenseKeyInput(e.target.value)}
                      placeholder="Paste your license key here (format: base64url.signature)"
                      rows={3}
                      className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-xs font-mono outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 resize-none"
                    />
                  </div>
                  {licenseError && (
                    <p className="text-[11px] text-red-400 flex items-center gap-1">
                      <AlertCircle className="h-3 w-3" />
                      {licenseError}
                    </p>
                  )}
                  <div className="flex items-center justify-between">
                    <p className="text-[10px] text-muted-foreground/60">
                      Don't have a license? The Community Edition includes all core features.
                      Upgrade at{" "}
                      <a href="https://github.com/greeves89/AI-Employee#pricing" target="_blank" rel="noopener" className="text-primary hover:underline">
                        github.com/greeves89/AI-Employee
                      </a>
                    </p>
                    <button
                      onClick={handleApplyLicense}
                      disabled={licenseBusy || !licenseKeyInput.trim()}
                      className="rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                    >
                      {licenseBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                      Apply License
                    </button>
                  </div>
                </div>
              )}
            </div>
          </section>
        )}
        </div>
        )}

        {/* ─── Tab: Integrationen (continued) — OAuth Integrations ─── */}
        {secTab === "integrationen" && (
        <div className="space-y-6">
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
                      <h3 className="text-sm font-semibold">Microsoft 365 — SSO + MS Graph</h3>
                      <p className="text-[11px] text-muted-foreground/60">
                        Enables: Login with Microsoft &amp; per-user M365 integration (Outlook, Teams, Calendar, OneDrive, To-Do)
                      </p>
                    </div>
                  </div>
                  {settings?.has_microsoft_oauth ? (
                    <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 px-2.5 py-1 text-[10px] font-medium text-emerald-400">
                      <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                      Active
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1.5 rounded-full bg-zinc-500/10 border border-zinc-500/20 px-2.5 py-1 text-[10px] font-medium text-zinc-400">
                      <span className="h-1.5 w-1.5 rounded-full bg-zinc-500" />
                      Not configured
                    </span>
                  )}
                </div>

                {/* Azure App Registration guide */}
                <div className="px-5 pt-4 pb-2">
                  <button
                    onClick={() => setMsGuideExpanded(!msGuideExpanded)}
                    className="flex items-center gap-2 text-[11px] font-medium text-blue-400 hover:text-blue-300 transition-colors"
                  >
                    <Info className="h-3.5 w-3.5" />
                    Azure App Registration — Setup-Anleitung
                    <ChevronRight className={cn("h-3 w-3 transition-transform", msGuideExpanded && "rotate-90")} />
                  </button>
                  {msGuideExpanded && (
                    <div className="mt-3 rounded-lg border border-blue-500/20 bg-blue-500/5 p-4 space-y-3 text-xs text-muted-foreground">
                      <p className="text-[11px] font-medium text-blue-300">
                        Einmalige Einrichtung durch den Admin. Danach können sich alle User per Microsoft anmelden UND ihre M365-Konten verbinden.
                      </p>
                      <ol className="list-decimal list-inside space-y-2.5">
                        <li>
                          <strong className="text-foreground">portal.azure.com</strong> → Azure Active Directory → App-Registrierungen → Neue Registrierung
                        </li>
                        <li>
                          Unter <strong className="text-foreground">Authentifizierung</strong> → Plattform hinzufügen → Web → folgende <strong className="text-foreground">zwei</strong> Redirect-URIs eintragen:
                          {[
                            `${typeof window !== "undefined" ? window.location.origin : "https://deine-domain.com"}/api/v1/auth/sso/microsoft/callback`,
                            `${typeof window !== "undefined" ? window.location.origin : "https://deine-domain.com"}/api/v1/integrations/microsoft/callback`,
                          ].map((url) => (
                            <div key={url} className="mt-1.5 flex items-center gap-2 rounded-md border border-foreground/10 bg-background/50 px-3 py-1.5 font-mono text-[10px]">
                              <span className="flex-1 text-emerald-400 break-all">{url}</span>
                              <button
                                onClick={() => navigator.clipboard.writeText(url)}
                                className="text-muted-foreground/40 hover:text-muted-foreground transition-colors flex-shrink-0"
                                title="Kopieren"
                              >
                                <Copy className="h-3 w-3" />
                              </button>
                            </div>
                          ))}
                        </li>
                        <li>
                          Unter <strong className="text-foreground">API-Berechtigungen</strong> → Berechtigung hinzufügen → Microsoft Graph → Delegiert:
                          <div className="mt-1.5 rounded-md border border-foreground/10 bg-background/50 px-3 py-2 font-mono text-[10px] text-blue-300/80 leading-relaxed">
                            User.Read, Mail.ReadWrite, Mail.Send, Calendars.ReadWrite, Files.ReadWrite, Chat.ReadWrite, Chat.ReadBasic, ChannelMessage.Read.All, ChannelMessage.Send, Team.ReadBasic.All, Tasks.ReadWrite, Contacts.ReadWrite, People.Read, offline_access
                          </div>
                          <p className="mt-1 text-[10px] text-amber-400/80">→ Danach <strong>&quot;Administratorzustimmung erteilen&quot;</strong> klicken</p>
                        </li>
                        <li>
                          Unter <strong className="text-foreground">Zertifikate &amp; Geheimnisse</strong> → Neuer geheimer Clientschlüssel erstellen
                        </li>
                        <li>
                          <strong className="text-foreground">Application (Client) ID</strong> und Secret in die Felder unten eintragen &amp; speichern
                        </li>
                      </ol>
                      <p className="text-[10px] text-muted-foreground/50 pt-1 border-t border-foreground/[0.06]">
                        Jeder User verbindet sein eigenes M365-Konto unter <strong>Integrations</strong>. Token werden pro User gespeichert, nicht geteilt.
                      </p>
                    </div>
                  )}
                </div>

                <div className="p-5 pt-3 grid grid-cols-2 gap-3">
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

                {settings?.has_microsoft_oauth && (
                  <div className="px-5 pb-4 flex items-center gap-2 text-[11px] text-emerald-400/80">
                    <CheckCircle2 className="h-3.5 w-3.5" />
                    SSO aktiv — &quot;Mit Microsoft anmelden&quot; erscheint auf der Login-Seite. User können ihr Konto unter Integrations verbinden.
                  </div>
                )}

                {/* Admin: expose the MS Graph MCP server to external LLMs (OpenWebUI) */}
                <div className="px-5 pb-4 pt-3 border-t border-foreground/[0.04]">
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5 text-[12px] font-medium">
                        <Network className="h-3.5 w-3.5 text-blue-400" />
                        MCP-Server extern exponieren (OpenWebUI)
                      </div>
                      <p className="mt-0.5 text-[10px] text-muted-foreground/60">
                        Stellt den MS-Graph-MCP-Server externen LLM-Clients per OAuth 2.1 bereit. Jeder User loggt sich ein und nutzt sein eigenes M365.
                      </p>
                    </div>
                    <button
                      onClick={() => toggleMsgraphExt(!settings?.msgraph_mcp_external_enabled)}
                      disabled={!settings?.has_microsoft_oauth || msgraphExtSaving}
                      className={cn(
                        "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors",
                        settings?.msgraph_mcp_external_enabled ? "bg-emerald-500" : "bg-foreground/[0.1]",
                        (!settings?.has_microsoft_oauth || msgraphExtSaving) && "opacity-40 cursor-not-allowed",
                      )}
                    >
                      {msgraphExtSaving ? (
                        <Loader2 className="mx-auto h-3 w-3 animate-spin text-white" />
                      ) : (
                        <span className={cn(
                          "inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform",
                          settings?.msgraph_mcp_external_enabled ? "translate-x-6" : "translate-x-1",
                        )} />
                      )}
                    </button>
                  </div>
                  {!settings?.has_microsoft_oauth && (
                    <p className="mt-1.5 text-[10px] text-amber-400/70">Erst die Microsoft App-Registrierung oben eintragen &amp; speichern.</p>
                  )}
                  {settings?.msgraph_mcp_external_enabled && (
                    <div className="mt-2.5 space-y-1.5 rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-3">
                      <p className="text-[10px] font-medium text-emerald-300">
                        In OpenWebUI → Admin Settings → External Tools → Add Server → Type: MCP (Streamable HTTP), Auth: OAuth 2.1 — diese Server-URL eintragen:
                      </p>
                      <div className="flex items-center gap-2 rounded-md border border-foreground/10 bg-background/50 px-3 py-1.5 font-mono text-[10px]">
                        <span className="flex-1 break-all text-emerald-400">
                          {typeof window !== "undefined" ? window.location.origin : "https://deine-domain.com"}/api/v1/mcp/msgraph
                        </span>
                        <button
                          onClick={() => navigator.clipboard.writeText(`${window.location.origin}/api/v1/mcp/msgraph`)}
                          className="flex-shrink-0 text-muted-foreground/40 transition-colors hover:text-muted-foreground"
                          title="Kopieren"
                        >
                          <Copy className="h-3 w-3" />
                        </button>
                      </div>
                      <p className="text-[10px] text-muted-foreground/50">
                        Login &amp; Client-Registrierung (DCR) laufen automatisch über OAuth. Token sind pro User — kein geteilter Zugriff.
                      </p>
                    </div>
                  )}
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

        {/* ─── Exchange (on-prem) ─── */}
        {isAdmin && (
          <section>
            <div className="flex items-center gap-2 mb-3">
              <Network className="h-4 w-4 text-muted-foreground/60" />
              <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">
                Exchange (on-prem)
              </h2>
            </div>
            <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden">
              <div className="px-5 py-3.5 border-b border-foreground/[0.04]">
                <p className="text-[11px] text-muted-foreground/60 leading-relaxed">
                  On-prem Exchange (EWS) für Mail &amp; Kalender — getrennt von M365/Graph. Zugriff ist{" "}
                  <strong className="text-foreground">benutzerspezifisch</strong>: jeder Agent liest/schreibt das
                  Postfach seines Owners via Impersonation auf dessen E-Mail (aus dem SSO-Login). Hier konfiguriert
                  der Admin die Verbindung einmalig — danach erscheint &quot;Exchange (on-prem)&quot; bei den
                  Agent-Integrationen.
                </p>
              </div>
              <div className="p-5 space-y-3">
                <CredentialField
                  label="EWS-Server (Host)"
                  value={exchangeServerUrl}
                  onChange={setExchangeServerUrl}
                  placeholder="mail.klinikum-bs.de"
                  mono
                />
                <SelectField
                  label="Auth-Modus"
                  value={exchangeAuthMode}
                  onChange={setExchangeAuthMode}
                  options={[
                    { value: "service_account", label: "Service-Account + Impersonation (empfohlen)" },
                    { value: "modern_auth", label: "Modern Auth (Entra-App) + Impersonation" },
                    { value: "basic", label: "Basic (User-Credentials, delegate)" },
                  ]}
                />
                {exchangeAuthMode === "service_account" && (
                  <div className="grid grid-cols-2 gap-3">
                    <CredentialField
                      label="Service-Account (UPN)"
                      value={exchangeSvcUser}
                      onChange={setExchangeSvcUser}
                      placeholder="svc-aiemployee@klinikum-bs.de"
                      mono
                    />
                    <CredentialField
                      label="Service-Account Passwort"
                      type="password"
                      value={exchangeSvcPass}
                      onChange={setExchangeSvcPass}
                      placeholder="••••••••"
                    />
                  </div>
                )}
                {exchangeAuthMode === "modern_auth" && (
                  <CredentialField
                    label="Entra Tenant-ID"
                    value={exchangeTenantId}
                    onChange={setExchangeTenantId}
                    placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                    hint="Nutzt die Microsoft-OAuth-Client-ID/-Secret aus den OAuth-Integrationen oben (gleiche Entra-App)."
                    mono
                  />
                )}
                <p className="text-[10px] text-muted-foreground/50 pt-1 border-t border-foreground/[0.06]">
                  Server-URL eintragen &amp; speichern aktiviert die Integration. Im service_account/modern_auth-Modus
                  muss der User nichts hinterlegen. Voraussetzung am Server: ApplicationImpersonation-Rolle
                  (service_account) bzw. EWS-App-Berechtigung (modern_auth).
                </p>
              </div>
            </div>
          </section>
        )}
        </div>
        )}

        {/* ─── Tab: System (continued) — Access Control ─── */}
        {secTab === "system" && (
        <div className="space-y-6">
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

        {/* ─── Sicherheit / Login (Admin only) ─── */}
        {isAdmin && (
          <section>
            <div className="flex items-center gap-2 mb-3">
              <Lock className="h-4 w-4 text-muted-foreground/60" />
              <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">
                Sicherheit / Login
              </h2>
            </div>

            <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden">
              {/* Nur SSO-Login */}
              <div className="p-5">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5 text-[12px] font-medium">
                      <Shield className="h-3.5 w-3.5 text-blue-400" />
                      Nur SSO-Login (Passwort-Login deaktivieren)
                    </div>
                    <p className="mt-0.5 text-[10px] text-muted-foreground/60">
                      Blendet die Passwort-Anmeldung auf der Login-Seite aus — nur noch SSO.
                    </p>
                  </div>
                  <button
                    onClick={() => toggleSsoOnly(!settings?.sso_only_login)}
                    disabled={ssoOnlySaving}
                    className={cn(
                      "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors",
                      settings?.sso_only_login ? "bg-emerald-500" : "bg-foreground/[0.1]",
                      ssoOnlySaving && "opacity-40 cursor-not-allowed",
                    )}
                  >
                    {ssoOnlySaving ? (
                      <Loader2 className="mx-auto h-3 w-3 animate-spin text-white" />
                    ) : (
                      <span className={cn(
                        "inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform",
                        settings?.sso_only_login ? "translate-x-6" : "translate-x-1",
                      )} />
                    )}
                  </button>
                </div>
                <div className="mt-2.5 flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3">
                  <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-400 mt-0.5" />
                  <p className="text-[10px] leading-relaxed text-amber-300">
                    <strong>Achtung:</strong> Danach ist die Anmeldung NUR noch über Microsoft-SSO möglich. Nutzer ohne SSO-Konto im konfigurierten Tenant werden ausgesperrt. Notfall-Zugang: auf dem Server ENV <code className="font-mono">EMERGENCY_PASSWORD_LOGIN=true</code> setzen.
                  </p>
                </div>
              </div>

              {/* Neue User: Admin-Freischaltung */}
              <div className="p-5 pt-3 border-t border-foreground/[0.04]">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5 text-[12px] font-medium">
                      <Shield className="h-3.5 w-3.5 text-blue-400" />
                      Neue User müssen freigeschaltet werden
                    </div>
                    <p className="mt-0.5 text-[10px] text-muted-foreground/60">
                      Neu per SSO oder Registrierung angelegte Konten landen auf „Warten auf Freischaltung" — ein Admin gibt sie unter Admin-Konsole → Benutzer frei (wie OpenWebUI).
                    </p>
                  </div>
                  <button
                    onClick={() => toggleRequireApproval(!settings?.require_user_approval)}
                    disabled={approvalSaving}
                    className={cn(
                      "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors",
                      settings?.require_user_approval ? "bg-emerald-500" : "bg-foreground/[0.1]",
                      approvalSaving && "opacity-40 cursor-not-allowed",
                    )}
                  >
                    {approvalSaving ? (
                      <Loader2 className="mx-auto h-3 w-3 animate-spin text-white" />
                    ) : (
                      <span className={cn(
                        "inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform",
                        settings?.require_user_approval ? "translate-x-6" : "translate-x-1",
                      )} />
                    )}
                  </button>
                </div>
              </div>

              {/* MS-Graph-Token bei Logout entfernen */}
              <div className="p-5 pt-3 border-t border-foreground/[0.04]">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5 text-[12px] font-medium">
                      <Network className="h-3.5 w-3.5 text-blue-400" />
                      MS-Graph-Token bei Logout entfernen
                    </div>
                    <p className="mt-0.5 text-[10px] text-muted-foreground/60">
                      Entfernt den gespeicherten Microsoft-Token beim Abmelden. Autonome Agenten verlieren MS-Graph dann bis zum nächsten Login + Verbinden.
                    </p>
                  </div>
                  <button
                    onClick={() => toggleRevokeMsgraph(!settings?.revoke_msgraph_on_logout)}
                    disabled={revokeMsgraphSaving}
                    className={cn(
                      "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors",
                      settings?.revoke_msgraph_on_logout ? "bg-emerald-500" : "bg-foreground/[0.1]",
                      revokeMsgraphSaving && "opacity-40 cursor-not-allowed",
                    )}
                  >
                    {revokeMsgraphSaving ? (
                      <Loader2 className="mx-auto h-3 w-3 animate-spin text-white" />
                    ) : (
                      <span className={cn(
                        "inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform",
                        settings?.revoke_msgraph_on_logout ? "translate-x-6" : "translate-x-1",
                      )} />
                    )}
                  </button>
                </div>
              </div>
            </div>
          </section>
        )}
        </div>
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

        {/* ─── Codex ChatGPT Device Auth Modal ─── */}
        {codexLoginOpen && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              className="w-full max-w-xl rounded-2xl border border-foreground/[0.08] bg-card p-6 shadow-2xl"
            >
              <h3 className="text-base font-semibold mb-1">Mit ChatGPT/Codex einloggen</h3>
              <p className="text-xs text-muted-foreground/60 mb-4">
                Öffne den Link, gib den Code ein und autorisiere Codex. Danach übernimmt der Orchestrator
                die Session automatisch.
              </p>

              {codexDeviceSessionId && (
                <div className="space-y-3 rounded-xl border border-foreground/[0.08] bg-foreground/[0.02] p-4">
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-xs text-muted-foreground/60">Code</span>
                    <button
                      onClick={() => navigator.clipboard?.writeText(codexDeviceCode)}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-foreground/[0.08] px-2.5 py-1 text-xs text-muted-foreground hover:text-foreground"
                    >
                      <Copy className="h-3 w-3" />
                      Kopieren
                    </button>
                  </div>
                  <div className="rounded-lg bg-background px-4 py-3 text-center font-mono text-2xl font-semibold tracking-widest text-emerald-400">
                    {codexDeviceCode}
                  </div>
                  <a
                    href={codexDeviceUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-emerald-500/10 border border-emerald-500/20 px-4 py-2.5 text-sm font-medium text-emerald-400 hover:bg-emerald-500/20 transition-all"
                  >
                    <ExternalLink className="h-4 w-4" />
                    ChatGPT Login öffnen
                  </a>
                  <div className="flex items-center gap-2 text-xs text-muted-foreground/60">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    Warte auf Autorisierung...
                  </div>
                </div>
              )}

              <details className="mt-4 rounded-xl border border-foreground/[0.06] bg-foreground/[0.02] p-3">
                <summary className="cursor-pointer text-xs font-medium text-muted-foreground hover:text-foreground">
                  Fallback: auth.json manuell importieren
                </summary>
                <textarea
                  value={codexAuthJson}
                  onChange={(e) => setCodexAuthJson(e.target.value)}
                  placeholder='{"auth_mode":"chatgpt","tokens":{...}}'
                  rows={7}
                  className="mt-3 w-full rounded-lg border border-foreground/[0.08] bg-background px-3.5 py-3 text-xs font-mono outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all placeholder:text-muted-foreground/25 resize-none"
                />
              </details>

              {codexLoginError && (
                <p className="text-xs text-red-400 mt-2 flex items-center gap-1">
                  <AlertCircle className="h-3 w-3" />
                  {codexLoginError}
                </p>
              )}

              <div className="flex gap-2 mt-4">
                <button
                  onClick={handleCodexAuthSubmit}
                  disabled={codexLoginLoading || !codexAuthJson.trim()}
                  className="flex-1 inline-flex items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 hover:bg-primary/90 disabled:opacity-50 transition-all"
                >
                  {codexLoginLoading ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <CheckCircle2 className="h-4 w-4" />
                  )}
                  Fallback speichern
                </button>
                <button
                  onClick={handleCodexCancel}
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
