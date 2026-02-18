"use client";

import { useEffect, useState } from "react";
import { ApprovalModal } from "@/components/agents/approval-modal";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { approveCommand, denyCommand, getPendingApprovals } from "@/lib/api";
import type { ApprovalRequest } from "@/lib/types";
import { AlertCircle, ShieldAlert, AlertTriangle, Info, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";

export default function ApprovalsPage() {
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [selectedRequest, setSelectedRequest] = useState<ApprovalRequest | null>(null);
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
    // Poll for new approvals every 5 seconds
    const interval = setInterval(loadApprovals, 5000);
    return () => clearInterval(interval);
  }, []);

  const handleApprove = async (approvalId: string) => {
    await approveCommand(Number(approvalId));
    await loadApprovals();
  };

  const handleDeny = async (approvalId: string, reason?: string) => {
    await denyCommand(Number(approvalId), reason);
    await loadApprovals();
  };

  const openModal = (request: ApprovalRequest) => {
    setSelectedRequest(request);
    setIsModalOpen(true);
  };

  const riskConfig = {
    blocked: { icon: ShieldAlert, color: "text-red-600", label: "BLOCKED" },
    high: { icon: AlertCircle, color: "text-orange-600", label: "HIGH RISK" },
    medium: { icon: AlertTriangle, color: "text-yellow-600", label: "MEDIUM RISK" },
    low: { icon: Info, color: "text-blue-600", label: "LOW RISK" },
  };

  return (
    <div className="container mx-auto p-6 max-w-6xl">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-3xl font-bold">Command Approvals</h1>
          <p className="text-gray-600 dark:text-gray-400 mt-1">
            Review and approve agent command requests
          </p>
        </div>
        <Button onClick={loadApprovals} variant="outline" size="sm">
          <RefreshCw className="h-4 w-4 mr-2" />
          Refresh
        </Button>
      </div>

      {isLoading && approvals.length === 0 ? (
        <div className="text-center py-12 text-gray-500">
          Loading approvals...
        </div>
      ) : approvals.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-gray-500">
            No pending approval requests
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {approvals.map((approval) => {
            const config = riskConfig[approval.risk_level];
            const Icon = config.icon;
            
            return (
              <Card key={approval.approval_id} className="hover:shadow-md transition-shadow">
                <CardHeader>
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex items-start gap-3 flex-1">
                      <Icon className={cn("h-5 w-5 mt-0.5", config.color)} />
                      <div className="flex-1">
                        <CardTitle className="text-lg">
                          {approval.tool}
                        </CardTitle>
                        <CardDescription className="mt-1">
                          Agent: {approval.agent_id}
                        </CardDescription>
                      </div>
                    </div>
                    <Badge
                      variant={approval.risk_level === "high" || approval.risk_level === "blocked" ? "destructive" : "secondary"}
                    >
                      {config.label}
                    </Badge>
                  </div>
                </CardHeader>
                <CardContent>
                  <div className="space-y-3">
                    <div>
                      <div className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                        Command
                      </div>
                      <div className="text-sm font-mono bg-gray-100 dark:bg-gray-800 p-2 rounded-md overflow-x-auto">
                        {approval.input.command || JSON.stringify(approval.input)}
                      </div>
                    </div>

                    <div>
                      <div className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                        Reasoning
                      </div>
                      <div className="text-sm text-gray-600 dark:text-gray-400">
                        {approval.reasoning}
                      </div>
                    </div>

                    <div className="flex items-center justify-between pt-2">
                      <div className="text-xs text-gray-500">
                        {new Date(approval.created_at).toLocaleString()}
                      </div>
                      <Button onClick={() => openModal(approval)} size="sm">
                        Review Request
                      </Button>
                    </div>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
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
