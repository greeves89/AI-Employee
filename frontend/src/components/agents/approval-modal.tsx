"use client";

import { useState } from "react";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { AlertCircle, ShieldAlert, AlertTriangle, Info } from "lucide-react";
import { cn } from "@/lib/utils";

interface ApprovalRequest {
  approval_id: string;
  agent_id: string;
  tool: string;
  input: Record<string, unknown>;
  reasoning: string;
  risk_level: "blocked" | "high" | "medium" | "low";
  status: "pending" | "approved" | "denied";
  created_at: string;
}

interface ApprovalModalProps {
  request: ApprovalRequest | null;
  open: boolean;
  onClose: () => void;
  onApprove: (approvalId: string) => Promise<void>;
  onDeny: (approvalId: string, reason?: string) => Promise<void>;
}

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

  const riskConfig = {
    blocked: {
      icon: ShieldAlert,
      color: "text-red-600 dark:text-red-400",
      bgColor: "bg-red-50 dark:bg-red-950/30",
      borderColor: "border-red-300 dark:border-red-700",
      badgeVariant: "destructive" as const,
      label: "BLOCKED",
      description: "This command is forbidden and cannot be executed.",
    },
    high: {
      icon: AlertCircle,
      color: "text-orange-600 dark:text-orange-400",
      bgColor: "bg-orange-50 dark:bg-orange-950/30",
      borderColor: "border-orange-300 dark:border-orange-700",
      badgeVariant: "destructive" as const,
      label: "HIGH RISK",
      description: "This command could cause serious damage. Review carefully.",
    },
    medium: {
      icon: AlertTriangle,
      color: "text-yellow-600 dark:text-yellow-400",
      bgColor: "bg-yellow-50 dark:bg-yellow-950/30",
      borderColor: "border-yellow-300 dark:border-yellow-700",
      badgeVariant: "secondary" as const,
      label: "MEDIUM RISK",
      description: "This command may have unintended effects.",
    },
    low: {
      icon: Info,
      color: "text-blue-600 dark:text-blue-400",
      bgColor: "bg-blue-50 dark:bg-blue-950/30",
      borderColor: "border-blue-300 dark:border-blue-700",
      badgeVariant: "outline" as const,
      label: "LOW RISK",
      description: "This command appears safe.",
    },
  };

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
    // Format command input for display
    if (input.command) {
      return input.command as string;
    }
    return JSON.stringify(input, null, 2);
  };

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <div className="flex items-center gap-3">
            <Icon className={cn("h-6 w-6", config.color)} />
            <div className="flex-1">
              <DialogTitle className="text-xl">Command Approval Required</DialogTitle>
              <DialogDescription className="mt-1">
                Agent wants to execute a command that requires your approval
              </DialogDescription>
            </div>
            <Badge variant={config.badgeVariant}>{config.label}</Badge>
          </div>
        </DialogHeader>

        <div className="space-y-4">
          {/* Risk Level Warning */}
          <div
            className={cn(
              "rounded-lg border p-4",
              config.bgColor,
              config.borderColor
            )}
          >
            <p className={cn("text-sm font-medium", config.color)}>
              {config.description}
            </p>
          </div>

          {/* Tool Name */}
          <div>
            <label className="text-sm font-semibold text-gray-700 dark:text-gray-300">
              Tool
            </label>
            <div className="mt-1 rounded-md bg-gray-50 dark:bg-gray-900 px-3 py-2 text-sm font-mono">
              {request.tool}
            </div>
          </div>

          {/* Command/Input */}
          <div>
            <label className="text-sm font-semibold text-gray-700 dark:text-gray-300">
              Command
            </label>
            <div className="mt-1 rounded-md bg-gray-50 dark:bg-gray-900 px-3 py-2 text-sm font-mono whitespace-pre-wrap break-all">
              {formatInput(request.input)}
            </div>
          </div>

          {/* Agent Reasoning */}
          <div>
            <label className="text-sm font-semibold text-gray-700 dark:text-gray-300">
              Agent&apos;s Reasoning
            </label>
            <div className="mt-1 rounded-md bg-gray-50 dark:bg-gray-900 px-3 py-2 text-sm">
              {request.reasoning}
            </div>
          </div>

          {/* Agent ID */}
          <div className="text-xs text-gray-500 dark:text-gray-400">
            Agent: {request.agent_id} • Requested:{" "}
            {new Date(request.created_at).toLocaleString()}
          </div>

          {/* Deny Reason Form */}
          {showDenyForm && (
            <div>
              <label className="text-sm font-semibold text-gray-700 dark:text-gray-300">
                Reason for Denial (Optional)
              </label>
              <Textarea
                className="mt-1"
                placeholder="Why are you denying this command?"
                value={denyReason}
                onChange={(e) => setDenyReason(e.target.value)}
                rows={3}
              />
            </div>
          )}
        </div>

        <DialogFooter className="gap-2">
          {isBlocked ? (
            // Blocked commands cannot be approved
            <Button onClick={onClose} variant="outline" className="w-full">
              Close
            </Button>
          ) : showDenyForm ? (
            // Show deny confirmation
            <>
              <Button
                onClick={() => {
                  setShowDenyForm(false);
                  setDenyReason("");
                }}
                variant="outline"
                disabled={isSubmitting}
              >
                Cancel
              </Button>
              <Button
                onClick={handleDeny}
                variant="destructive"
                disabled={isSubmitting}
              >
                {isSubmitting ? "Denying..." : "Confirm Denial"}
              </Button>
            </>
          ) : (
            // Show approve/deny buttons
            <>
              <Button
                onClick={() => setShowDenyForm(true)}
                variant="outline"
                disabled={isSubmitting}
              >
                Deny
              </Button>
              <Button
                onClick={handleApprove}
                disabled={isSubmitting}
                className={cn(
                  request.risk_level === "high" &&
                    "bg-orange-600 hover:bg-orange-700 dark:bg-orange-700 dark:hover:bg-orange-800"
                )}
              >
                {isSubmitting ? "Approving..." : "Approve & Execute"}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
