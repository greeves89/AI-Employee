"use client";

import { useState, useEffect, useRef } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { motion, AnimatePresence } from "framer-motion";
import {
  X,
  Plus,
  Loader2,
  Package,
  Settings,
  ShieldOff,
  Check,
  ArrowLeft,
  Bot,
  Code2,
  BarChart3,
  FileText,
  Server,
  Search,
  Presentation,
  Megaphone,
  Headphones,
  TrendingUp,
  Crown,
  Zap,
  Plug,
  Eye,
  EyeOff,
  ShieldAlert,
  GitPullRequest,
  TestTube2,
  Share2,
  Scale,
  UserPlus,
  Languages,
  Kanban,
  Database,
  Palette,
  PenTool,
  Globe,
} from "lucide-react";
import * as api from "@/lib/api";
import type { AgentMode, AgentTemplate, LLMConfig, LLMProviderType, PermissionPackage } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useSimpleMode } from "@/hooks/use-simple-mode";

const PERMISSION_ICON_MAP: Record<string, React.ElementType> = {
  package: Package,
  settings: Settings,
  "shield-off": ShieldOff,
};

const TEMPLATE_ICON_MAP: Record<string, React.ElementType> = {
  Bot,
  Code2,
  BarChart3,
  FileText,
  Server,
  Search,
  Presentation,
  Megaphone,
  Headphones,
  TrendingUp,
  Crown,
  ShieldAlert,
  GitPullRequest,
  TestTube2,
  Share2,
  Scale,
  UserPlus,
  Languages,
  Kanban,
  Database,
  Palette,
  PenTool,
  Globe,
  Zap,
  Plug,
};

const CATEGORY_LABELS: Record<string, string> = {
  dev: "Development",
  data: "Data & Analytics",
  writing: "Writing & Docs",
  ops: "Operations",
  creative: "Creative",
  general: "General",
  marketing: "Marketing",
  support: "Support",
  sales: "Sales",
  management: "Management",
  security: "Security",
};

const CATEGORY_COLORS: Record<string, string> = {
  dev: "bg-blue-500/10 text-blue-400",
  data: "bg-emerald-500/10 text-emerald-400",
  writing: "bg-purple-500/10 text-purple-400",
  ops: "bg-amber-500/10 text-amber-400",
  creative: "bg-pink-500/10 text-pink-400",
  general: "bg-gray-500/10 text-gray-400",
  marketing: "bg-orange-500/10 text-orange-400",
  support: "bg-cyan-500/10 text-cyan-400",
  sales: "bg-rose-500/10 text-rose-400",
  management: "bg-indigo-500/10 text-indigo-400",
  security: "bg-red-500/10 text-red-400",
};

const PROVIDER_PRESETS: Record<string, { endpoint: string; models: string[] }> = {
  openai: {
    endpoint: "https://api.openai.com/v1",
    models: ["gpt-4o", "gpt-4o-mini", "o3-mini", "codex-mini-latest"],
  },
  anthropic: {
    endpoint: "https://api.anthropic.com/v1",
    models: ["claude-sonnet-4-20250514", "claude-haiku-4-20250414"],
  },
  google: {
    endpoint: "https://generativelanguage.googleapis.com/v1beta",
    models: ["gemini-2.5-flash", "gemini-2.5-pro"],
  },
};

interface CreateAgentModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: () => void;
}

type Step = "template" | "configure";

export function CreateAgentModal({
  open,
  onOpenChange,
  onCreated,
}: CreateAgentModalProps) {
  const { simpleMode } = useSimpleMode();
  const [step, setStep] = useState<Step>("template");
  const [templates, setTemplates] = useState<AgentTemplate[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState<AgentTemplate | null>(null);
  const [name, setName] = useState("");
  const [role, setRole] = useState("");
  const [selectedPermissions, setSelectedPermissions] = useState<string[]>([]);
  const [budgetUsd, setBudgetUsd] = useState<string>("");
  const [packages, setPackages] = useState<PermissionPackage[]>([]);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Mode selection
  const [mode, setMode] = useState<AgentMode>("claude_code");

  // Custom LLM fields
  const [llmProvider, setLlmProvider] = useState<LLMProviderType>("openai");
  const [llmEndpoint, setLlmEndpoint] = useState("https://api.openai.com/v1");
  const [llmApiKey, setLlmApiKey] = useState("");
  const [llmModelName, setLlmModelName] = useState("gpt-4o");
  const [llmMaxTokens, setLlmMaxTokens] = useState("4096");
  const [llmTemperature, setLlmTemperature] = useState("0.7");
  const [llmSystemPrompt, setLlmSystemPrompt] = useState("");
  const [llmToolsEnabled, setLlmToolsEnabled] = useState(true);
  const [showApiKey, setShowApiKey] = useState(false);

  // Load templates and permission packages on open
  useEffect(() => {
    if (open) {
      setStep("template");
      setSelectedTemplate(null);
      setName("");
      setRole("");
      setBudgetUsd("");
      setError(null);
      setMode("claude_code");
      setLlmProvider("openai");
      setLlmEndpoint("https://api.openai.com/v1");
      setLlmApiKey("");
      setLlmModelName("gpt-4o");
      setLlmMaxTokens("4096");
      setLlmTemperature("0.7");
      setLlmSystemPrompt("");
      setLlmToolsEnabled(true);
      setShowApiKey(false);

      api.getTemplates().then((data) => {
        setTemplates(data.templates);
      }).catch(() => setTemplates([]));

      api.getPermissionPackages().then((data) => {
        setPackages(data.packages);
        setSelectedPermissions(data.defaults);
      }).catch(() => {
        setPackages([]);
        setSelectedPermissions(["package-install"]);
      });
    }
  }, [open]);

  const selectTemplate = (template: AgentTemplate | null) => {
    setSelectedTemplate(template);
    if (template) {
      setName("");
      setRole(template.role);
      setSelectedPermissions(template.permissions.length > 0 ? template.permissions : ["package-install"]);
      // Pre-fill system prompt from template for Custom LLM usage
      const templatePrompt = template.knowledge_template
        ? `Du bist ein ${template.display_name}.\n\nRolle: ${template.role}\n\n${template.knowledge_template}`
        : `Du bist ein ${template.display_name}.\n\nRolle: ${template.role}`;
      setLlmSystemPrompt(templatePrompt);
    } else {
      setName("");
      setRole("");
      setSelectedPermissions(["package-install"]);
      setLlmSystemPrompt("");
    }
    setStep("configure");
  };

  const togglePermission = (id: string) => {
    setSelectedPermissions((prev) => {
      if (id === "full-access") {
        return prev.includes(id) ? [] : ["full-access"];
      }
      const without = prev.filter((p) => p !== "full-access" && p !== id);
      if (prev.includes(id)) {
        return without;
      }
      return [...without, id];
    });
  };

  const handleProviderChange = (provider: LLMProviderType) => {
    setLlmProvider(provider);
    const preset = PROVIDER_PRESETS[provider];
    if (preset) {
      setLlmEndpoint(preset.endpoint);
      setLlmModelName(preset.models[0]);
    }
  };

  const handleCreate = async () => {
    if (!name.trim() && !selectedTemplate) return;

    // Validate custom LLM fields
    if (mode === "custom_llm") {
      if (!llmApiKey.trim()) {
        setError("API Key ist erforderlich");
        return;
      }
      if (!llmModelName.trim()) {
        setError("Model Name ist erforderlich");
        return;
      }
    }

    setCreating(true);
    setError(null);
    try {
      const parsedBudget = budgetUsd ? parseFloat(budgetUsd) : undefined;

      if (selectedTemplate && mode !== "custom_llm") {
        await api.createAgentFromTemplate(selectedTemplate.id, name.trim() || undefined);
      } else if (mode === "custom_llm") {
        const llmConfig: LLMConfig = {
          provider_type: llmProvider,
          api_endpoint: llmEndpoint.trim(),
          api_key: llmApiKey.trim(),
          model_name: llmModelName.trim(),
          max_tokens: parseInt(llmMaxTokens) || 4096,
          temperature: parseFloat(llmTemperature) || 0.7,
          system_prompt: llmSystemPrompt.trim(),
          tools_enabled: llmToolsEnabled,
        };
        await api.createAgent(
          name.trim(),
          llmModelName.trim(),
          role.trim() || undefined,
          selectedPermissions.length > 0 ? selectedPermissions : undefined,
          parsedBudget && parsedBudget > 0 ? parsedBudget : undefined,
          "custom_llm",
          llmConfig,
        );
      } else {
        await api.createAgent(
          name.trim(),
          undefined,
          role.trim() || undefined,
          selectedPermissions.length > 0 ? selectedPermissions : undefined,
          parsedBudget && parsedBudget > 0 ? parsedBudget : undefined,
          "claude_code",
        );
      }
      setName("");
      setRole("");
      setSelectedPermissions([]);
      setSelectedTemplate(null);
      onOpenChange(false);
      onCreated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create agent");
    } finally {
      setCreating(false);
    }
  };

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <AnimatePresence>
        {open && (
          <Dialog.Portal forceMount>
            <Dialog.Overlay asChild>
              <motion.div
                className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.15 }}
              />
            </Dialog.Overlay>

            <Dialog.Content asChild>
              <motion.div
                className="fixed inset-0 z-50 flex items-center justify-center p-4"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2, ease: "easeOut" }}
              >
              <motion.div
                className="w-full max-w-2xl rounded-2xl border border-foreground/[0.08] bg-card shadow-2xl shadow-black/40 overflow-hidden max-h-[90vh] overflow-y-auto"
                initial={{ scale: 0.95, y: 10 }}
                animate={{ scale: 1, y: 0 }}
                exit={{ scale: 0.95, y: 10 }}
                transition={{ duration: 0.2, ease: "easeOut" }}
              >
                {/* Header */}
                <div className="flex items-center justify-between border-b border-foreground/[0.06] px-6 py-4">
                  <div className="flex items-center gap-3">
                    {step === "configure" && (
                      <button
                        onClick={() => setStep("template")}
                        className="rounded-lg p-1.5 text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] transition-colors"
                      >
                        <ArrowLeft className="h-4 w-4" />
                      </button>
                    )}
                    <Dialog.Title className="text-lg font-semibold">
                      {step === "template" ? "Vorlage waehlen" : simpleMode ? "Agent benennen" : "Agent konfigurieren"}
                    </Dialog.Title>
                  </div>
                  <Dialog.Close className="rounded-lg p-1.5 text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] transition-colors">
                    <X className="h-4 w-4" />
                  </Dialog.Close>
                </div>

                {/* Step 1: Template Selection */}
                {step === "template" && (
                  <div className="px-6 py-5">
                    <p className="text-sm text-muted-foreground mb-4">
                      Waehle eine Vorlage fuer den neuen Agent oder starte ohne Vorlage.
                    </p>

                    {/* Blank Agent Option */}
                    <button
                      onClick={() => selectTemplate(null)}
                      className="w-full flex items-center gap-4 rounded-xl border border-dashed border-foreground/[0.15] p-4 mb-4 text-left transition-all duration-200 hover:border-primary/40 hover:bg-primary/[0.04]"
                    >
                      <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-foreground/[0.06] text-muted-foreground">
                        <Plus className="h-5 w-5" />
                      </div>
                      <div>
                        <p className="text-sm font-medium">Leerer Agent</p>
                        <p className="text-xs text-muted-foreground/70 mt-0.5">
                          Ohne Vorlage starten - Name und Rolle selbst festlegen
                        </p>
                      </div>
                    </button>

                    {/* Template Grid */}
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      {templates.map((tmpl) => {
                        const Icon = TEMPLATE_ICON_MAP[tmpl.icon] || Bot;
                        return (
                          <button
                            key={tmpl.id}
                            onClick={() => selectTemplate(tmpl)}
                            className="flex items-start gap-3 rounded-xl border border-foreground/[0.08] p-4 text-left transition-all duration-200 hover:border-primary/40 hover:bg-primary/[0.04]"
                          >
                            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                              <Icon className="h-5 w-5" />
                            </div>
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2">
                                <p className="text-sm font-medium truncate">{tmpl.display_name}</p>
                                <span className={cn(
                                  "text-[9px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded",
                                  CATEGORY_COLORS[tmpl.category] || CATEGORY_COLORS.general
                                )}>
                                  {CATEGORY_LABELS[tmpl.category] || tmpl.category}
                                </span>
                              </div>
                              <p className="text-xs text-muted-foreground/70 mt-0.5 line-clamp-2">
                                {tmpl.description}
                              </p>
                              {tmpl.permissions.length > 0 && (
                                <div className="flex gap-1 mt-2">
                                  {tmpl.permissions.map((p) => (
                                    <span key={p} className="text-[9px] px-1.5 py-0.5 rounded bg-foreground/[0.06] text-muted-foreground/80">
                                      {p}
                                    </span>
                                  ))}
                                </div>
                              )}
                            </div>
                          </button>
                        );
                      })}
                    </div>

                    {templates.length === 0 && (
                      <p className="text-xs text-muted-foreground/50 text-center py-6">
                        Vorlagen werden geladen...
                      </p>
                    )}
                  </div>
                )}

                {/* Step 2: Configure Agent */}
                {step === "configure" && (
                  <>
                    <div className="px-6 py-5 space-y-5">
                      {/* Selected template badge */}
                      {selectedTemplate && (
                        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-primary/[0.06] border border-primary/20">
                          {(() => {
                            const Icon = TEMPLATE_ICON_MAP[selectedTemplate.icon] || Bot;
                            return <Icon className="h-4 w-4 text-primary" />;
                          })()}
                          <span className="text-xs font-medium text-primary">
                            Vorlage: {selectedTemplate.display_name}
                          </span>
                        </div>
                      )}

                      {/* Mode Selector (hidden in simple mode) */}
                      {!simpleMode && (
                      <div>
                        <label className="block text-xs font-medium text-muted-foreground mb-2.5">
                          Agent-Modus
                        </label>
                          <div className="grid grid-cols-2 gap-3">
                            <button
                              type="button"
                              onClick={() => setMode("claude_code")}
                              className={cn(
                                "flex items-start gap-3 rounded-xl border p-4 text-left transition-all duration-200",
                                mode === "claude_code"
                                  ? "border-primary/50 bg-primary/[0.08] ring-1 ring-primary/20"
                                  : "border-foreground/[0.08] hover:border-foreground/[0.15] hover:bg-foreground/[0.02]"
                              )}
                            >
                              <div className={cn(
                                "flex h-10 w-10 shrink-0 items-center justify-center rounded-lg",
                                mode === "claude_code" ? "bg-primary/20 text-primary" : "bg-foreground/[0.06] text-muted-foreground"
                              )}>
                                <Zap className="h-5 w-5" />
                              </div>
                              <div>
                                <p className="text-sm font-medium">Claude Code</p>
                                <p className="text-[11px] text-muted-foreground/70 mt-0.5">
                                  Voll ausgestatteter Coding-Agent mit CLI, MCP Tools und Knowledge
                                </p>
                              </div>
                            </button>

                            <button
                              type="button"
                              onClick={() => setMode("custom_llm")}
                              className={cn(
                                "flex items-start gap-3 rounded-xl border p-4 text-left transition-all duration-200",
                                mode === "custom_llm"
                                  ? "border-violet-500/50 bg-violet-500/[0.08] ring-1 ring-violet-500/20"
                                  : "border-foreground/[0.08] hover:border-foreground/[0.15] hover:bg-foreground/[0.02]"
                              )}
                            >
                              <div className={cn(
                                "flex h-10 w-10 shrink-0 items-center justify-center rounded-lg",
                                mode === "custom_llm" ? "bg-violet-500/20 text-violet-400" : "bg-foreground/[0.06] text-muted-foreground"
                              )}>
                                <Plug className="h-5 w-5" />
                              </div>
                              <div>
                                <p className="text-sm font-medium">Custom LLM</p>
                                <p className="text-[11px] text-muted-foreground/70 mt-0.5">
                                  Eigener LLM-Provider mit vollem Tool-Set und Proactive Mode
                                </p>
                              </div>
                            </button>
                          </div>
                        </div>
                      )}

                      {/* Name */}
                      <div>
                        <label className="block text-xs font-medium text-muted-foreground mb-1.5">
                          Name <span className="text-red-400">*</span>
                        </label>
                        <input
                          type="text"
                          value={name}
                          onChange={(e) => setName(e.target.value)}
                          onKeyDown={(e) =>
                            e.key === "Enter" && !creating && handleCreate()
                          }
                          placeholder={
                            selectedTemplate
                              ? `z.B. my-${selectedTemplate.name}`
                              : mode === "custom_llm"
                              ? "z.B. gpt-coder, gemini-researcher..."
                              : "z.B. dev-agent, researcher, writer..."
                          }
                          autoFocus
                          className="w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-4 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all"
                        />
                      </div>

                      {/* ===== CUSTOM LLM FIELDS (hidden in simple mode) ===== */}
                      {!simpleMode && mode === "custom_llm" && (
                        <>
                          {/* Provider */}
                          <div>
                            <label className="block text-xs font-medium text-muted-foreground mb-1.5">
                              Provider
                            </label>
                            <div className="grid grid-cols-3 gap-2">
                              {(["openai", "anthropic", "google"] as LLMProviderType[]).map((p) => (
                                <button
                                  key={p}
                                  type="button"
                                  onClick={() => handleProviderChange(p)}
                                  className={cn(
                                    "rounded-lg border px-3 py-2 text-sm font-medium transition-all",
                                    llmProvider === p
                                      ? "border-violet-500/50 bg-violet-500/10 text-violet-400"
                                      : "border-foreground/[0.08] text-muted-foreground hover:border-foreground/[0.15]"
                                  )}
                                >
                                  {p === "openai" ? "OpenAI" : p === "anthropic" ? "Anthropic" : "Google"}
                                </button>
                              ))}
                            </div>
                            <p className="text-[11px] text-muted-foreground/50 mt-1">
                              OpenAI-kompatible APIs (Together, Groq, Ollama, etc.) nutzen &quot;OpenAI&quot;.
                            </p>
                          </div>

                          {/* API Endpoint */}
                          <div>
                            <label className="block text-xs font-medium text-muted-foreground mb-1.5">
                              API Endpoint
                            </label>
                            <input
                              type="text"
                              value={llmEndpoint}
                              onChange={(e) => setLlmEndpoint(e.target.value)}
                              placeholder="https://api.openai.com/v1"
                              className="w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-4 py-2.5 text-sm font-mono outline-none focus:border-violet-500/50 focus:ring-1 focus:ring-violet-500/20 transition-all"
                            />
                          </div>

                          {/* API Key */}
                          <div>
                            <label className="block text-xs font-medium text-muted-foreground mb-1.5">
                              API Key <span className="text-red-400">*</span>
                            </label>
                            <div className="relative">
                              <input
                                type={showApiKey ? "text" : "password"}
                                value={llmApiKey}
                                onChange={(e) => setLlmApiKey(e.target.value)}
                                placeholder="sk-..."
                                className="w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-4 py-2.5 pr-10 text-sm font-mono outline-none focus:border-violet-500/50 focus:ring-1 focus:ring-violet-500/20 transition-all"
                              />
                              <button
                                type="button"
                                onClick={() => setShowApiKey(!showApiKey)}
                                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground/50 hover:text-muted-foreground transition-colors"
                              >
                                {showApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                              </button>
                            </div>
                            <p className="text-[11px] text-muted-foreground/50 mt-1">
                              Wird verschluesselt gespeichert. Nie in API-Responses sichtbar.
                            </p>
                          </div>

                          {/* Model Name */}
                          <ModelInput
                            value={llmModelName}
                            onChange={setLlmModelName}
                            suggestions={PROVIDER_PRESETS[llmProvider]?.models ?? []}
                          />

                          {/* Temperature */}
                          <div>
                            <label className="block text-xs font-medium text-muted-foreground mb-1.5">
                              Temperature
                            </label>
                            <input
                              type="number"
                              step="0.1"
                              min="0"
                              max="2"
                              value={llmTemperature}
                              onChange={(e) => setLlmTemperature(e.target.value)}
                              className="w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-4 py-2.5 text-sm outline-none focus:border-violet-500/50 focus:ring-1 focus:ring-violet-500/20 transition-all tabular-nums"
                            />
                            <p className="text-[11px] text-muted-foreground/50 mt-1">
                              0 = praezise, 1 = kreativ. Fuer Coding: 0.2-0.5 empfohlen.
                            </p>
                          </div>

                          {/* System Prompt */}
                          <div>
                            <label className="block text-xs font-medium text-muted-foreground mb-1.5">
                              System Prompt{" "}
                              <span className="text-muted-foreground/40">(optional)</span>
                            </label>
                            <textarea
                              value={llmSystemPrompt}
                              onChange={(e) => setLlmSystemPrompt(e.target.value)}
                              placeholder="Du bist ein hilfreicher KI-Assistent..."
                              rows={3}
                              className="w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-4 py-2.5 text-sm outline-none focus:border-violet-500/50 focus:ring-1 focus:ring-violet-500/20 transition-all resize-none"
                            />
                          </div>

                          {/* Tools Toggle */}
                          <div className="flex items-center justify-between rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] px-4 py-3">
                            <div>
                              <p className="text-sm font-medium">Tool-Nutzung</p>
                              <p className="text-[11px] text-muted-foreground/70 mt-0.5">
                                Shell, Dateien, Memory, TODOs, Notifications, Team-Koordination
                              </p>
                            </div>
                            <button
                              type="button"
                              onClick={() => setLlmToolsEnabled(!llmToolsEnabled)}
                              className={cn(
                                "relative h-6 w-11 shrink-0 rounded-full transition-colors",
                                llmToolsEnabled ? "bg-violet-500" : "bg-foreground/20"
                              )}
                            >
                              <span
                                className={cn(
                                  "absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform",
                                  llmToolsEnabled && "translate-x-5"
                                )}
                              />
                            </button>
                          </div>
                        </>
                      )}

                      {/* ===== SHARED FIELDS (both modes, no template, hidden in simple mode) ===== */}
                      {!simpleMode && !selectedTemplate && (
                        <>
                          {/* Role */}
                          <div>
                            <label className="block text-xs font-medium text-muted-foreground mb-1.5">
                              Rolle{" "}
                              <span className="text-muted-foreground/40">
                                (optional)
                              </span>
                            </label>
                            <input
                              type="text"
                              value={role}
                              onChange={(e) => setRole(e.target.value)}
                              placeholder="z.B. Fullstack Developer, Data Analyst, Technical Writer..."
                              className={cn(
                                "w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-4 py-2.5 text-sm outline-none transition-all",
                                mode === "custom_llm"
                                  ? "focus:border-violet-500/50 focus:ring-1 focus:ring-violet-500/20"
                                  : "focus:border-primary/50 focus:ring-1 focus:ring-primary/20"
                              )}
                            />
                          </div>

                          {/* Budget */}
                          <div>
                            <label className="block text-xs font-medium text-muted-foreground mb-1.5">
                              Budget (USD){" "}
                              <span className="text-muted-foreground/40">
                                (optional)
                              </span>
                            </label>
                            <div className="relative">
                              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-sm text-muted-foreground/50">$</span>
                              <input
                                type="number"
                                step="0.01"
                                min="0"
                                value={budgetUsd}
                                onChange={(e) => setBudgetUsd(e.target.value)}
                                placeholder="Unlimited"
                                className={cn(
                                  "w-full rounded-lg border border-foreground/[0.1] bg-background/80 pl-7 pr-4 py-2.5 text-sm outline-none transition-all tabular-nums",
                                  mode === "custom_llm"
                                    ? "focus:border-violet-500/50 focus:ring-1 focus:ring-violet-500/20"
                                    : "focus:border-primary/50 focus:ring-1 focus:ring-primary/20"
                                )}
                              />
                            </div>
                            <p className="text-[11px] text-muted-foreground/50 mt-1">
                              Max. Kosten fuer diesen Agent. Ohne Angabe: unbegrenzt.
                            </p>
                          </div>

                          {/* Permission Packages */}
                          <div>
                            <label className="block text-xs font-medium text-muted-foreground mb-2.5">
                              Berechtigungen (Sudo-Pakete)
                            </label>
                            <div className="space-y-2">
                              {packages.map((pkg) => {
                                const Icon = PERMISSION_ICON_MAP[pkg.icon] || Package;
                                const isSelected = selectedPermissions.includes(pkg.id);
                                const isFullAccess = pkg.id === "full-access";

                                return (
                                  <button
                                    key={pkg.id}
                                    type="button"
                                    onClick={() => togglePermission(pkg.id)}
                                    className={cn(
                                      "w-full flex items-start gap-3 rounded-xl border p-3.5 text-left transition-all duration-200",
                                      isSelected
                                        ? isFullAccess
                                          ? "border-amber-500/40 bg-amber-500/[0.08]"
                                          : "border-primary/40 bg-primary/[0.08]"
                                        : "border-foreground/[0.06] bg-foreground/[0.02] hover:bg-foreground/[0.04]"
                                    )}
                                  >
                                    <div
                                      className={cn(
                                        "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg transition-colors",
                                        isSelected
                                          ? isFullAccess
                                            ? "bg-amber-500/20 text-amber-400"
                                            : "bg-primary/20 text-primary"
                                          : "bg-foreground/[0.06] text-muted-foreground"
                                      )}
                                    >
                                      <Icon className="h-4 w-4" />
                                    </div>
                                    <div className="flex-1 min-w-0">
                                      <div className="flex items-center gap-2">
                                        <span
                                          className={cn(
                                            "text-sm font-medium",
                                            isSelected ? "text-foreground" : "text-muted-foreground"
                                          )}
                                        >
                                          {pkg.label}
                                        </span>
                                        {pkg.default && !isFullAccess && (
                                          <span className="text-[10px] font-medium uppercase tracking-wider text-primary/60 bg-primary/10 px-1.5 py-0.5 rounded">
                                            Default
                                          </span>
                                        )}
                                      </div>
                                      <p className="text-xs text-muted-foreground/70 mt-0.5">
                                        {pkg.description}
                                      </p>
                                    </div>
                                    <div
                                      className={cn(
                                        "flex h-5 w-5 shrink-0 items-center justify-center rounded-md border transition-all mt-0.5",
                                        isSelected
                                          ? isFullAccess
                                            ? "border-amber-500 bg-amber-500 text-white"
                                            : "border-primary bg-primary text-white"
                                          : "border-foreground/20"
                                      )}
                                    >
                                      {isSelected && <Check className="h-3 w-3" />}
                                    </div>
                                  </button>
                                );
                              })}

                              {packages.length === 0 && (
                                <p className="text-xs text-muted-foreground/50 text-center py-3">
                                  Berechtigungspakete werden geladen...
                                </p>
                              )}
                            </div>
                            <p className="text-[11px] text-muted-foreground/50 mt-2">
                              Ohne Auswahl: nur pip/npm install (kein sudo). Basis-Tools
                              (git, curl, node) sind immer verfuegbar.
                            </p>
                          </div>
                        </>
                      )}

                      {/* Template info summary */}
                      {selectedTemplate && (
                        <div className="rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] p-3 space-y-1.5">
                          <p className="text-xs font-medium text-muted-foreground">Wird konfiguriert mit:</p>
                          <div className="flex flex-wrap gap-1.5">
                            <span className="text-[10px] px-2 py-0.5 rounded bg-blue-500/10 text-blue-400">
                              {selectedTemplate.model.split("-").slice(0, 2).join(" ")}
                            </span>
                            {selectedTemplate.permissions.map((p) => (
                              <span key={p} className="text-[10px] px-2 py-0.5 rounded bg-amber-500/10 text-amber-400">
                                {p}
                              </span>
                            ))}
                            {selectedTemplate.integrations.map((i) => (
                              <span key={i} className="text-[10px] px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400">
                                {i}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Error */}
                      {error && (
                        <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
                          {error}
                        </div>
                      )}
                    </div>

                    {/* Footer */}
                    <div className="flex items-center justify-end gap-3 border-t border-foreground/[0.06] px-6 py-4 bg-foreground/[0.02]">
                      <Dialog.Close className="rounded-lg px-4 py-2.5 text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-all">
                        Abbrechen
                      </Dialog.Close>
                      <button
                        onClick={handleCreate}
                        disabled={creating || (!name.trim() && !selectedTemplate)}
                        className={cn(
                          "inline-flex items-center gap-2 rounded-lg px-5 py-2.5 text-sm font-medium text-primary-foreground disabled:opacity-50 transition-all shadow-lg",
                          mode === "custom_llm"
                            ? "bg-violet-600 hover:bg-violet-500 shadow-violet-600/20"
                            : "bg-primary hover:bg-primary/90 shadow-primary/20"
                        )}
                      >
                        {creating ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          <Plus className="h-4 w-4" />
                        )}
                        {creating ? "Erstelle..." : "Agent erstellen"}
                      </button>
                    </div>
                  </>
                )}
              </motion.div>
              </motion.div>
            </Dialog.Content>
          </Dialog.Portal>
        )}
      </AnimatePresence>
    </Dialog.Root>
  );
}

/** Custom model input with dropdown suggestions (replaces native datalist). */
function ModelInput({
  value,
  onChange,
  suggestions,
}: {
  value: string;
  onChange: (v: string) => void;
  suggestions: string[];
}) {
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const filtered = suggestions.filter(
    (s) => s.toLowerCase().includes(value.toLowerCase()) && s !== value
  );

  return (
    <div ref={wrapperRef} className="relative">
      <label className="block text-xs font-medium text-muted-foreground mb-1.5">
        Model <span className="text-red-400">*</span>
      </label>
      <input
        type="text"
        value={value}
        onChange={(e) => {
          onChange(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        placeholder="gpt-4o"
        className="w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-4 py-2.5 text-sm outline-none focus:border-violet-500/50 focus:ring-1 focus:ring-violet-500/20 transition-all"
      />
      {open && filtered.length > 0 && (
        <div className="absolute z-50 mt-1 w-full rounded-lg border border-foreground/[0.1] bg-card shadow-xl shadow-black/30 overflow-hidden">
          {filtered.map((model) => (
            <button
              key={model}
              type="button"
              onClick={() => {
                onChange(model);
                setOpen(false);
              }}
              className="w-full px-4 py-2 text-sm text-left hover:bg-foreground/[0.06] transition-colors"
            >
              {model}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
