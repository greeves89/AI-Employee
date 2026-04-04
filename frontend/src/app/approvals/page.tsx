"use client";

import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ApprovalModal } from "@/components/agents/approval-modal";
import {
  getPendingApprovals, approveCommand, denyCommand,
  getApprovalRules, createApprovalRule, updateApprovalRule, deleteApprovalRule,
} from "@/lib/api";
import type { ApprovalRequest } from "@/lib/types";
import type { ApprovalRule } from "@/lib/api";
import {
  AlertCircle,
  ShieldAlert,
  AlertTriangle,
  Info,
  RefreshCw,
  ShieldCheck,
  Loader2,
  Plus,
  Trash2,
  DollarSign,
  Mail,
  FileX,
  Globe,
  ShoppingCart,
  Settings,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";

const CATEGORY_CONFIG: Record<string, { icon: typeof DollarSign; color: string; label: string }> = {
  money: { icon: DollarSign, color: "text-emerald-400", label: "Geld" },
  email: { icon: Mail, color: "text-blue-400", label: "E-Mail" },
  file_delete: { icon: FileX, color: "text-red-400", label: "Datei löschen" },
  external_api: { icon: Globe, color: "text-purple-400", label: "Externe API" },
  purchase: { icon: ShoppingCart, color: "text-amber-400", label: "Kauf" },
  custom: { icon: Settings, color: "text-zinc-400", label: "Sonstige" },
};

const containerVariants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { staggerChildren: 0.06 } },
};

const itemVariants = {
  hidden: { opacity: 0, y: 12 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.35, ease: [0.25, 0.46, 0.45, 0.94] as const },
  },
};

const riskConfig = {
  blocked: {
    icon: ShieldAlert,
    color: "text-red-400",
    bg: "bg-red-500/10",
    border: "border-red-500/20",
    label: "BLOCKED",
  },
  high: {
    icon: AlertCircle,
    color: "text-orange-400",
    bg: "bg-orange-500/10",
    border: "border-orange-500/20",
    label: "HIGH RISK",
  },
  medium: {
    icon: AlertTriangle,
    color: "text-amber-400",
    bg: "bg-amber-500/10",
    border: "border-amber-500/20",
    label: "MEDIUM RISK",
  },
  low: {
    icon: Info,
    color: "text-blue-400",
    bg: "bg-blue-500/10",
    border: "border-blue-500/20",
    label: "LOW RISK",
  },
};

export default function ApprovalsPage() {
  const [activeTab, setActiveTab] = useState<"pending" | "rules">("pending");
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [selectedRequest, setSelectedRequest] =
    useState<ApprovalRequest | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isModalOpen, setIsModalOpen] = useState(false);

  // Rules state
  const [rules, setRules] = useState<ApprovalRule[]>([]);
  const [rulesLoading, setRulesLoading] = useState(false);
  const [showRuleForm, setShowRuleForm] = useState(false);
  const [ruleDraft, setRuleDraft] = useState<{ name: string; description: string; category: string; threshold: string }>({
    name: "",
    description: "",
    category: "custom",
    threshold: "",
  });

  const loadApprovals = async () => {
    setIsLoading(true);
    try {
      const data = await getPendingApprovals();
      setApprovals(data.approvals);
    } catch (error) {
      console.error("Failed to load approvals:", error);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadApprovals();
    const interval = setInterval(loadApprovals, 10000);
    return () => clearInterval(interval);
  }, []);

  const loadRules = async () => {
    setRulesLoading(true);
    try {
      const data = await getApprovalRules();
      setRules(data.rules);
    } catch (error) {
      console.error("Failed to load rules:", error);
    } finally {
      setRulesLoading(false);
    }
  };

  useEffect(() => {
    if (activeTab === "rules") loadRules();
  }, [activeTab]);

  const handleSaveRule = async () => {
    if (!ruleDraft.name.trim() || !ruleDraft.description.trim()) return;
    try {
      await createApprovalRule({
        name: ruleDraft.name.trim(),
        description: ruleDraft.description.trim(),
        category: ruleDraft.category,
        threshold: ruleDraft.threshold ? parseFloat(ruleDraft.threshold) : null,
      });
      setRuleDraft({ name: "", description: "", category: "custom", threshold: "" });
      setShowRuleForm(false);
      await loadRules();
    } catch (error) {
      console.error("Failed to save rule:", error);
    }
  };

  const handleToggleRule = async (rule: ApprovalRule) => {
    try {
      await updateApprovalRule(rule.id, { is_active: !rule.is_active });
      await loadRules();
    } catch (error) {
      console.error("Failed to toggle rule:", error);
    }
  };

  const handleDeleteRule = async (id: number) => {
    try {
      await deleteApprovalRule(id);
      await loadRules();
    } catch (error) {
      console.error("Failed to delete rule:", error);
    }
  };

  const handleApprove = async (approvalId: string) => {
    await approveCommand(approvalId);
    await loadApprovals();
  };

  const handleDeny = async (approvalId: string, reason?: string) => {
    await denyCommand(approvalId, reason);
    await loadApprovals();
  };

  const openModal = (request: ApprovalRequest) => {
    setSelectedRequest(request);
    setIsModalOpen(true);
  };

  return (
    <div className="px-8 py-8 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-4">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10">
            <ShieldCheck className="h-5 w-5 text-primary" />
          </div>
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">
              Command Approvals
            </h1>
            <p className="text-sm text-muted-foreground/60 mt-0.5">
              Review and approve agent command requests
            </p>
          </div>
        </div>
        <button
          onClick={activeTab === "pending" ? loadApprovals : loadRules}
          className="inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-all"
        >
          <RefreshCw
            className={cn("h-4 w-4", (isLoading || rulesLoading) && "animate-spin")}
          />
          Aktualisieren
        </button>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 p-1 rounded-xl bg-foreground/[0.03] mb-6 w-fit">
        <button
          onClick={() => setActiveTab("pending")}
          className={cn(
            "px-4 py-2 text-sm font-medium rounded-lg transition-all",
            activeTab === "pending"
              ? "bg-background shadow-sm text-foreground"
              : "text-muted-foreground hover:text-foreground"
          )}
        >
          Ausstehend ({approvals.length})
        </button>
        <button
          onClick={() => setActiveTab("rules")}
          className={cn(
            "px-4 py-2 text-sm font-medium rounded-lg transition-all",
            activeTab === "rules"
              ? "bg-background shadow-sm text-foreground"
              : "text-muted-foreground hover:text-foreground"
          )}
        >
          Regeln ({rules.length})
        </button>
      </div>

      {/* Rules Tab */}
      {activeTab === "rules" && (
        <div className="space-y-4">
          <div className="flex items-start justify-between">
            <div>
              <p className="text-sm text-muted-foreground">
                Definiere Regeln, bei denen Agents IMMER um Erlaubnis fragen müssen.
              </p>
              <p className="text-[11px] text-muted-foreground/60 mt-1">
                Diese Regeln werden bei jedem Task automatisch in den Agent-Prompt injiziert.
              </p>
            </div>
            <button
              onClick={() => setShowRuleForm(true)}
              className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 hover:bg-primary/90 transition-all"
            >
              <Plus className="h-4 w-4" />
              Neue Regel
            </button>
          </div>

          {/* Rule Form */}
          {showRuleForm && (
            <motion.div
              initial={{ opacity: 0, y: -8 }}
              animate={{ opacity: 1, y: 0 }}
              className="rounded-xl border border-foreground/[0.08] bg-card/80 backdrop-blur-sm p-5"
            >
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-sm font-semibold">Neue Regel anlegen</h3>
                <button
                  onClick={() => setShowRuleForm(false)}
                  className="rounded-lg p-1 text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04]"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-[11px] font-medium text-muted-foreground/70 mb-1.5 block">Name</label>
                    <input
                      value={ruleDraft.name}
                      onChange={(e) => setRuleDraft({ ...ruleDraft, name: e.target.value })}
                      placeholder="z.B. Große Ausgaben"
                      className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20"
                    />
                  </div>
                  <div>
                    <label className="text-[11px] font-medium text-muted-foreground/70 mb-1.5 block">Kategorie</label>
                    <select
                      value={ruleDraft.category}
                      onChange={(e) => setRuleDraft({ ...ruleDraft, category: e.target.value })}
                      className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20"
                    >
                      {Object.entries(CATEGORY_CONFIG).map(([key, cfg]) => (
                        <option key={key} value={key}>{cfg.label}</option>
                      ))}
                    </select>
                  </div>
                </div>
                <div>
                  <label className="text-[11px] font-medium text-muted-foreground/70 mb-1.5 block">
                    Beschreibung (was der Agent beachten soll)
                  </label>
                  <textarea
                    value={ruleDraft.description}
                    onChange={(e) => setRuleDraft({ ...ruleDraft, description: e.target.value })}
                    placeholder="z.B. Frag IMMER bevor du Geld ausgibst das 50 EUR übersteigt"
                    rows={3}
                    className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 resize-none"
                  />
                </div>
                <div>
                  <label className="text-[11px] font-medium text-muted-foreground/70 mb-1.5 block">
                    Schwellwert (optional, z.B. Betrag)
                  </label>
                  <input
                    type="number"
                    value={ruleDraft.threshold}
                    onChange={(e) => setRuleDraft({ ...ruleDraft, threshold: e.target.value })}
                    placeholder="50"
                    className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20"
                  />
                </div>
                <div className="flex justify-end gap-2 pt-2">
                  <button
                    onClick={() => setShowRuleForm(false)}
                    className="rounded-xl px-4 py-2 text-sm text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-all"
                  >
                    Abbrechen
                  </button>
                  <button
                    onClick={handleSaveRule}
                    disabled={!ruleDraft.name.trim() || !ruleDraft.description.trim()}
                    className="rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
                  >
                    Speichern
                  </button>
                </div>
              </div>
            </motion.div>
          )}

          {/* Rules List */}
          {rulesLoading && rules.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-muted-foreground/50">
              <Loader2 className="h-6 w-6 animate-spin mb-3" />
              <span className="text-sm">Lade Regeln...</span>
            </div>
          ) : rules.length === 0 ? (
            <div className="rounded-xl border border-dashed border-foreground/[0.1] bg-card/30 p-16 text-center">
              <ShieldCheck className="h-10 w-10 mx-auto mb-3 text-muted-foreground/30" />
              <p className="text-sm text-muted-foreground/50">Noch keine Regeln definiert</p>
              <p className="text-[11px] text-muted-foreground/30 mt-1">
                Lege Regeln an, damit Agents wissen wann sie fragen müssen.
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              {rules.map((rule) => {
                const cfg = CATEGORY_CONFIG[rule.category] || CATEGORY_CONFIG.custom;
                const Icon = cfg.icon;
                return (
                  <div
                    key={rule.id}
                    className={cn(
                      "rounded-xl border bg-card/80 backdrop-blur-sm p-4 flex items-start gap-3",
                      rule.is_active ? "border-foreground/[0.06]" : "border-foreground/[0.04] opacity-60"
                    )}
                  >
                    <div className={cn("flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-foreground/[0.04]")}>
                      <Icon className={cn("h-4 w-4", cfg.color)} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-sm font-medium">{rule.name}</span>
                        <span className="inline-flex items-center rounded-full bg-foreground/[0.04] border border-foreground/[0.06] px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
                          {cfg.label}
                        </span>
                        {rule.threshold !== null && (
                          <span className="inline-flex items-center rounded-full bg-amber-500/10 border border-amber-500/20 px-2 py-0.5 text-[10px] font-medium text-amber-400">
                            &gt; {rule.threshold}
                          </span>
                        )}
                      </div>
                      <p className="text-xs text-muted-foreground leading-relaxed">{rule.description}</p>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <button
                        onClick={() => handleToggleRule(rule)}
                        className={cn(
                          "relative inline-flex h-6 w-11 items-center rounded-full transition-colors",
                          rule.is_active ? "bg-emerald-500" : "bg-zinc-600"
                        )}
                      >
                        <span
                          className={cn(
                            "inline-block h-4 w-4 transform rounded-full bg-white transition-transform",
                            rule.is_active ? "translate-x-6" : "translate-x-1"
                          )}
                        />
                      </button>
                      <button
                        onClick={() => handleDeleteRule(rule.id)}
                        className="rounded-lg p-1.5 text-muted-foreground/50 hover:text-red-400 hover:bg-red-500/10 transition-all"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Pending Approvals Tab */}
      {activeTab === "pending" && (
      <>
      {isLoading && approvals.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-muted-foreground/50">
          <Loader2 className="h-6 w-6 animate-spin mb-3" />
          <span className="text-sm">Loading approvals...</span>
        </div>
      ) : approvals.length === 0 ? (
        <div className="rounded-xl border border-dashed border-foreground/[0.1] bg-card/30 p-16 text-center">
          <ShieldCheck className="h-10 w-10 mx-auto mb-3 text-muted-foreground/30" />
          <p className="text-sm text-muted-foreground/50">
            No pending approval requests
          </p>
          <p className="text-[11px] text-muted-foreground/30 mt-1">
            Approval requests from agents will appear here
          </p>
        </div>
      ) : (
        <motion.div
          className="space-y-3"
          variants={containerVariants}
          initial="hidden"
          animate="visible"
        >
          <AnimatePresence>
            {approvals.map((approval) => {
              const config = riskConfig[approval.risk_level] ?? riskConfig.medium;
              const Icon = config.icon;
              const isQuestion = Boolean(approval.question);

              return (
                <motion.div
                  key={approval.approval_id}
                  variants={itemVariants}
                  layout
                  className="group relative rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5 transition-all duration-200 hover:border-foreground/[0.1] hover:bg-card/90 hover:shadow-lg hover:shadow-primary/5"
                >
                  <div className="flex items-start justify-between gap-4">
                    {/* Left: icon + details */}
                    <div className="flex items-start gap-3 flex-1 min-w-0">
                      <div
                        className={cn(
                          "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg",
                          config.bg
                        )}
                      >
                        <Icon className={cn("h-4 w-4", config.color)} />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          <span className="text-sm font-medium truncate">
                            {isQuestion ? "Agent Question" : approval.tool}
                          </span>
                          <span
                            className={cn(
                              "inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider",
                              config.bg,
                              config.color,
                              config.border
                            )}
                          >
                            {isQuestion ? "APPROVAL" : config.label}
                          </span>
                        </div>

                        {isQuestion ? (
                          <>
                            {/* Question */}
                            <p className="text-sm mb-2">{approval.question}</p>
                            {/* Options */}
                            {approval.options && approval.options.length > 0 && (
                              <div className="flex flex-wrap gap-2 mb-2">
                                {approval.options.map((opt) => (
                                  <span
                                    key={opt}
                                    className="inline-flex items-center rounded-lg bg-foreground/[0.04] border border-foreground/[0.06] px-2.5 py-1 text-xs text-muted-foreground"
                                  >
                                    {opt}
                                  </span>
                                ))}
                              </div>
                            )}
                            {/* Context */}
                            {approval.context && (
                              <p className="text-[11px] text-muted-foreground/60 line-clamp-2">
                                {approval.context}
                              </p>
                            )}
                          </>
                        ) : (
                          <>
                            {/* Command preview */}
                            <div className="text-xs font-mono bg-foreground/[0.04] border border-foreground/[0.06] rounded-lg px-3 py-2 mb-2 overflow-x-auto text-muted-foreground">
                              {(approval.input?.command as string) ||
                                JSON.stringify(approval.input)}
                            </div>
                            {/* Reasoning */}
                            <p className="text-[11px] text-muted-foreground/60 line-clamp-2">
                              {approval.reasoning}
                            </p>
                          </>
                        )}

                        {/* Meta */}
                        <div className="flex items-center gap-3 mt-2 text-[10px] text-muted-foreground/40">
                          <span>Agent: {approval.agent_id}</span>
                          <span>
                            {new Date(approval.created_at).toLocaleString()}
                          </span>
                        </div>
                      </div>
                    </div>

                    {/* Right: action button */}
                    <button
                      onClick={() => openModal(approval)}
                      className="shrink-0 inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 hover:bg-primary/90 transition-all"
                    >
                      Review
                    </button>
                  </div>
                </motion.div>
              );
            })}
          </AnimatePresence>
        </motion.div>
      )}
      </>
      )}

      <ApprovalModal
        request={selectedRequest}
        open={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        onApprove={handleApprove}
        onDeny={handleDeny}
      />
    </div>
  );
}
