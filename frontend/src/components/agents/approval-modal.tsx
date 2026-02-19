"use client";

import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { motion, AnimatePresence } from "framer-motion";
import {
  X,
  AlertCircle,
  ShieldAlert,
  AlertTriangle,
  Info,
  Loader2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { ApprovalRequest } from "@/lib/types";

interface ApprovalModalProps {
  request: ApprovalRequest | null;
  open: boolean;
  onClose: () => void;
  onApprove: (approvalId: string) => Promise<void>;
  onDeny: (approvalId: string, reason?: string) => Promise<void>;
}

const riskConfig = {
  blocked: {
    icon: ShieldAlert,
    color: "text-red-400",
    bg: "bg-red-500/10",
    border: "border-red-500/20",
    label: "BLOCKED",
    description: "This command is forbidden and cannot be executed.",
  },
  high: {
    icon: AlertCircle,
    color: "text-orange-400",
    bg: "bg-orange-500/10",
    border: "border-orange-500/20",
    label: "HIGH RISK",
    description: "This command could cause serious damage. Review carefully.",
  },
  medium: {
    icon: AlertTriangle,
    color: "text-amber-400",
    bg: "bg-amber-500/10",
    border: "border-amber-500/20",
    label: "MEDIUM RISK",
    description: "This command may have unintended effects.",
  },
  low: {
    icon: Info,
    color: "text-blue-400",
    bg: "bg-blue-500/10",
    border: "border-blue-500/20",
    label: "LOW RISK",
    description: "This command appears safe.",
  },
};

export function ApprovalModal({
  request,
  open,
  onClose,
  onApprove,
  onDeny,
}: ApprovalModalProps) {
  const [denyReason, setDenyReason] = useState("");
  const [showDenyForm, setShowDenyForm] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  if (!request) return null;

  const config = riskConfig[request.risk_level];
  const Icon = config.icon;
  const isBlocked = request.risk_level === "blocked";

  const handleApprove = async () => {
    setIsSubmitting(true);
    try {
      await onApprove(request.approval_id);
      onClose();
    } catch (error) {
      console.error("Failed to approve:", error);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDeny = async () => {
    setIsSubmitting(true);
    try {
      await onDeny(request.approval_id, denyReason || undefined);
      setDenyReason("");
      setShowDenyForm(false);
      onClose();
    } catch (error) {
      console.error("Failed to deny:", error);
    } finally {
      setIsSubmitting(false);
    }
  };

  const formatInput = (input: Record<string, unknown>) => {
    if (input.command) return input.command as string;
    return JSON.stringify(input, null, 2);
  };

  return (
    <Dialog.Root open={open} onOpenChange={onClose}>
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
                      <div
                        className={cn(
                          "flex h-8 w-8 items-center justify-center rounded-lg",
                          config.bg
                        )}
                      >
                        <Icon className={cn("h-4 w-4", config.color)} />
                      </div>
                      <div>
                        <Dialog.Title className="text-lg font-semibold">
                          Command Approval Required
                        </Dialog.Title>
                        <Dialog.Description className="text-[11px] text-muted-foreground/60">
                          Agent wants to execute a command that requires your
                          approval
                        </Dialog.Description>
                      </div>
                    </div>
                    <div className="flex items-center gap-3">
                      <span
                        className={cn(
                          "inline-flex items-center rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider",
                          config.bg,
                          config.color,
                          config.border
                        )}
                      >
                        {config.label}
                      </span>
                      <Dialog.Close className="rounded-lg p-1.5 text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] transition-colors">
                        <X className="h-4 w-4" />
                      </Dialog.Close>
                    </div>
                  </div>

                  {/* Body */}
                  <div className="px-6 py-5 space-y-4">
                    {/* Risk Warning */}
                    <div
                      className={cn(
                        "rounded-lg border p-3",
                        config.bg,
                        config.border
                      )}
                    >
                      <p className={cn("text-sm font-medium", config.color)}>
                        {config.description}
                      </p>
                    </div>

                    {/* Tool */}
                    <div>
                      <label className="text-[11px] font-medium text-muted-foreground/70 mb-1.5 block">
                        Tool
                      </label>
                      <div className="rounded-lg bg-foreground/[0.03] border border-foreground/[0.06] px-3.5 py-2.5 text-sm font-mono">
                        {request.tool}
                      </div>
                    </div>

                    {/* Command */}
                    <div>
                      <label className="text-[11px] font-medium text-muted-foreground/70 mb-1.5 block">
                        Command
                      </label>
                      <div className="rounded-lg bg-foreground/[0.03] border border-foreground/[0.06] px-3.5 py-2.5 text-sm font-mono whitespace-pre-wrap break-all">
                        {formatInput(request.input)}
                      </div>
                    </div>

                    {/* Reasoning */}
                    <div>
                      <label className="text-[11px] font-medium text-muted-foreground/70 mb-1.5 block">
                        Agent&apos;s Reasoning
                      </label>
                      <div className="rounded-lg bg-foreground/[0.03] border border-foreground/[0.06] px-3.5 py-2.5 text-sm">
                        {request.reasoning}
                      </div>
                    </div>

                    {/* Meta */}
                    <div className="text-[10px] text-muted-foreground/40">
                      Agent: {request.agent_id} &middot; Requested:{" "}
                      {new Date(request.created_at).toLocaleString()}
                    </div>

                    {/* Deny Reason Form */}
                    {showDenyForm && (
                      <div>
                        <label className="text-[11px] font-medium text-muted-foreground/70 mb-1.5 block">
                          Reason for Denial (Optional)
                        </label>
                        <textarea
                          placeholder="Why are you denying this command?"
                          value={denyReason}
                          onChange={(e) => setDenyReason(e.target.value)}
                          rows={3}
                          className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all placeholder:text-muted-foreground/25 resize-none"
                        />
                      </div>
                    )}
                  </div>

                  {/* Footer */}
                  <div className="flex items-center justify-end gap-3 border-t border-foreground/[0.06] px-6 py-4 bg-foreground/[0.02]">
                    {isBlocked ? (
                      <button
                        onClick={onClose}
                        className="rounded-lg px-4 py-2.5 text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-all"
                      >
                        Close
                      </button>
                    ) : showDenyForm ? (
                      <>
                        <button
                          onClick={() => {
                            setShowDenyForm(false);
                            setDenyReason("");
                          }}
                          disabled={isSubmitting}
                          className="rounded-lg px-4 py-2.5 text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-all disabled:opacity-50"
                        >
                          Cancel
                        </button>
                        <button
                          onClick={handleDeny}
                          disabled={isSubmitting}
                          className="inline-flex items-center gap-2 rounded-lg bg-red-500/10 border border-red-500/20 px-5 py-2.5 text-sm font-medium text-red-400 hover:bg-red-500/20 transition-all disabled:opacity-50"
                        >
                          {isSubmitting && (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          )}
                          {isSubmitting ? "Denying..." : "Confirm Denial"}
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          onClick={() => setShowDenyForm(true)}
                          disabled={isSubmitting}
                          className="rounded-lg px-4 py-2.5 text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-all disabled:opacity-50"
                        >
                          Deny
                        </button>
                        <button
                          onClick={handleApprove}
                          disabled={isSubmitting}
                          className={cn(
                            "inline-flex items-center gap-2 rounded-lg px-5 py-2.5 text-sm font-medium text-primary-foreground shadow-lg transition-all disabled:opacity-50",
                            request.risk_level === "high"
                              ? "bg-orange-600 hover:bg-orange-500 shadow-orange-600/20"
                              : "bg-primary hover:bg-primary/90 shadow-primary/20"
                          )}
                        >
                          {isSubmitting && (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          )}
                          {isSubmitting ? "Approving..." : "Approve & Execute"}
                        </button>
                      </>
                    )}
                  </div>
                </motion.div>
              </motion.div>
            </Dialog.Content>
          </Dialog.Portal>
        )}
      </AnimatePresence>
    </Dialog.Root>
  );
}
