"use client";

import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ApprovalModal } from "@/components/agents/approval-modal";
import { getPendingApprovals, approveCommand, denyCommand } from "@/lib/api";
import type { ApprovalRequest } from "@/lib/types";
import {
  AlertCircle,
  ShieldAlert,
  AlertTriangle,
  Info,
  RefreshCw,
  ShieldCheck,
  Loader2,
} from "lucide-react";
import { cn } from "@/lib/utils";

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
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [selectedRequest, setSelectedRequest] =
    useState<ApprovalRequest | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isModalOpen, setIsModalOpen] = useState(false);

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
    const interval = setInterval(loadApprovals, 5000);
    return () => clearInterval(interval);
  }, []);

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
          onClick={loadApprovals}
          className="inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-all"
        >
          <RefreshCw
            className={cn("h-4 w-4", isLoading && "animate-spin")}
          />
          Refresh
        </button>
      </div>

      {/* Content */}
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
              const config = riskConfig[approval.risk_level];
              const Icon = config.icon;

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
                            {approval.tool}
                          </span>
                          <span
                            className={cn(
                              "inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider",
                              config.bg,
                              config.color,
                              config.border
                            )}
                          >
                            {config.label}
                          </span>
                        </div>

                        {/* Command preview */}
                        <div className="text-xs font-mono bg-foreground/[0.04] border border-foreground/[0.06] rounded-lg px-3 py-2 mb-2 overflow-x-auto text-muted-foreground">
                          {(approval.input.command as string) ||
                            JSON.stringify(approval.input)}
                        </div>

                        {/* Reasoning */}
                        <p className="text-[11px] text-muted-foreground/60 line-clamp-2">
                          {approval.reasoning}
                        </p>

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
