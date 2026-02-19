"use client";

import { useState, useEffect } from "react";
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
} from "lucide-react";
import * as api from "@/lib/api";
import type { AgentTemplate, PermissionPackage } from "@/lib/types";
import { cn } from "@/lib/utils";

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
};

const CATEGORY_LABELS: Record<string, string> = {
  dev: "Development",
  data: "Data & Analytics",
  writing: "Writing & Docs",
  ops: "Operations",
  creative: "Creative",
  general: "General",
};

const CATEGORY_COLORS: Record<string, string> = {
  dev: "bg-blue-500/10 text-blue-400",
  data: "bg-emerald-500/10 text-emerald-400",
  writing: "bg-purple-500/10 text-purple-400",
  ops: "bg-amber-500/10 text-amber-400",
  creative: "bg-pink-500/10 text-pink-400",
  general: "bg-gray-500/10 text-gray-400",
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

  // Load templates and permission packages on open
  useEffect(() => {
    if (open) {
      setStep("template");
      setSelectedTemplate(null);
      setName("");
      setRole("");
      setBudgetUsd("");
      setError(null);

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
    } else {
      setName("");
      setRole("");
      setSelectedPermissions(["package-install"]);
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

  const handleCreate = async () => {
    if (!name.trim() && !selectedTemplate) return;
    setCreating(true);
    setError(null);
    try {
      const parsedBudget = budgetUsd ? parseFloat(budgetUsd) : undefined;
      if (selectedTemplate) {
        await api.createAgentFromTemplate(selectedTemplate.id, name.trim() || undefined);
      } else {
        await api.createAgent(
          name.trim(),
          undefined,
          role.trim() || undefined,
          selectedPermissions.length > 0 ? selectedPermissions : undefined,
          parsedBudget && parsedBudget > 0 ? parsedBudget : undefined,
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
                      {step === "template" ? "Vorlage waehlen" : "Agent konfigurieren"}
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
                              : "z.B. dev-agent, researcher, writer..."
                          }
                          autoFocus
                          className="w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-4 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all"
                        />
                      </div>

                      {/* Role (only for blank agent) */}
                      {!selectedTemplate && (
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
                            className="w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-4 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all"
                          />
                        </div>
                      )}

                      {/* Budget (only for blank agent) */}
                      {!selectedTemplate && (
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
                              className="w-full rounded-lg border border-foreground/[0.1] bg-background/80 pl-7 pr-4 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all tabular-nums"
                            />
                          </div>
                          <p className="text-[11px] text-muted-foreground/50 mt-1">
                            Max. Kosten fuer diesen Agent. Ohne Angabe: unbegrenzt.
                          </p>
                        </div>
                      )}

                      {/* Permission Packages (only for blank agent) */}
                      {!selectedTemplate && (
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
                        className="inline-flex items-center gap-2 rounded-lg bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-all shadow-lg shadow-primary/20"
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
