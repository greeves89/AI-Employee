"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { motion } from "framer-motion";
import { Send, ArrowLeft, Sparkles, Cpu, Gauge, Loader2, DollarSign } from "lucide-react";
import { Header } from "@/components/layout/header";
import { useAgents } from "@/hooks/use-agents";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";

const priorityOptions = [
  { value: 0, label: "Low", color: "text-zinc-400 border-zinc-500/20 bg-zinc-500/10" },
  { value: 1, label: "Normal", color: "text-blue-400 border-blue-500/20 bg-blue-500/10" },
  { value: 2, label: "High", color: "text-amber-400 border-amber-500/20 bg-amber-500/10" },
  { value: 3, label: "Urgent", color: "text-red-400 border-red-500/20 bg-red-500/10" },
];

export default function NewTaskPage() {
  const router = useRouter();
  const { agents } = useAgents();
  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [priority, setPriority] = useState(1);
  const [agentId, setAgentId] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [costEstimate, setCostEstimate] = useState<{
    min_usd: number;
    avg_usd: number;
    max_usd: number;
    agent_avg_usd: number | null;
  } | null>(null);
  const [estimating, setEstimating] = useState(false);

  // Fetch cost estimate when prompt changes (debounced)
  const estimateCost = async () => {
    if (!prompt.trim() || prompt.length < 20) {
      setCostEstimate(null);
      return;
    }
    setEstimating(true);
    try {
      const est = await api.estimateTaskCost({
        prompt: prompt.trim(),
        agent_id: agentId || undefined,
      });
      setCostEstimate(est);
    } catch {
      setCostEstimate(null);
    } finally {
      setEstimating(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim() || !prompt.trim()) return;

    setSubmitting(true);
    try {
      await api.createTask({
        title: title.trim(),
        prompt: prompt.trim(),
        priority,
        agent_id: agentId || undefined,
      });
      router.push("/tasks");
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to create task");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div>
      <Header
        title="New Task"
        subtitle="Create a task for an AI agent"
        actions={
          <button
            onClick={() => router.back()}
            className="inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-all"
          >
            <ArrowLeft className="h-4 w-4" />
            Back
          </button>
        }
      />

      <motion.div
        className="px-8 py-8 max-w-2xl"
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <form onSubmit={handleSubmit} className="space-y-5">
          {/* Title */}
          <div className="space-y-1.5">
            <label className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
              <Sparkles className="h-3 w-3" />
              Title
            </label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Short description of the task"
              className="w-full rounded-xl border border-foreground/[0.08] bg-card/80 backdrop-blur-sm px-4 py-3 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all placeholder:text-muted-foreground/40"
              required
            />
          </div>

          {/* Prompt */}
          <div className="space-y-1.5">
            <label className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
              <Send className="h-3 w-3" />
              Prompt
            </label>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Detailed instructions for the AI agent. Be specific about what you want it to build or do."
              rows={8}
              className="w-full rounded-xl border border-foreground/[0.08] bg-card/80 backdrop-blur-sm px-4 py-3 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all resize-none placeholder:text-muted-foreground/40"
              required
            />
          </div>

          {/* Priority + Agent */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            {/* Priority selector */}
            <div className="space-y-1.5">
              <label className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                <Gauge className="h-3 w-3" />
                Priority
              </label>
              <div className="flex gap-1.5">
                {priorityOptions.map((opt) => (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => setPriority(opt.value)}
                    className={cn(
                      "flex-1 rounded-lg border px-3 py-2.5 text-xs font-medium transition-all duration-150",
                      priority === opt.value
                        ? opt.color
                        : "border-foreground/[0.06] text-muted-foreground hover:border-foreground/[0.1] hover:text-foreground"
                    )}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Agent selector */}
            <div className="space-y-1.5">
              <label className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                <Cpu className="h-3 w-3" />
                Agent (optional)
              </label>
              <select
                value={agentId}
                onChange={(e) => setAgentId(e.target.value)}
                className="w-full rounded-xl border border-foreground/[0.08] bg-card/80 backdrop-blur-sm px-4 py-3 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all appearance-none"
              >
                <option value="">Auto-assign (recommended)</option>
                {agents
                  .filter((a) => a.state !== "stopped")
                  .map((agent) => (
                    <option key={agent.id} value={agent.id}>
                      {agent.name} ({agent.id.slice(0, 8)})
                    </option>
                  ))}
              </select>
            </div>
          </div>

          {/* Cost Estimate */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <label className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                <DollarSign className="h-3 w-3" />
                Cost Estimate
              </label>
              <button
                type="button"
                onClick={estimateCost}
                disabled={estimating || !prompt.trim() || prompt.length < 20}
                className="text-[11px] text-primary hover:text-primary/80 disabled:text-muted-foreground/40 transition-colors"
              >
                {estimating ? "Calculating..." : "Estimate cost"}
              </button>
            </div>
            {costEstimate && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                className="rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] px-4 py-3 text-xs font-mono space-y-1"
              >
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Min</span>
                  <span>${costEstimate.min_usd.toFixed(4)}</span>
                </div>
                <div className="flex justify-between font-medium">
                  <span className="text-muted-foreground">Avg</span>
                  <span>${costEstimate.avg_usd.toFixed(4)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Max</span>
                  <span>${costEstimate.max_usd.toFixed(4)}</span>
                </div>
                {costEstimate.agent_avg_usd !== null && (
                  <div className="flex justify-between border-t border-foreground/[0.06] pt-1 mt-1">
                    <span className="text-muted-foreground">Agent avg</span>
                    <span>${costEstimate.agent_avg_usd.toFixed(4)}</span>
                  </div>
                )}
              </motion.div>
            )}
          </div>

          {/* Actions */}
          <div className="flex gap-3 pt-3">
            <button
              type="submit"
              disabled={submitting || !title.trim() || !prompt.trim()}
              className="inline-flex items-center gap-2 rounded-xl bg-primary px-6 py-3 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 hover:bg-primary/90 disabled:opacity-50 disabled:shadow-none transition-all duration-200"
            >
              {submitting ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Send className="h-4 w-4" />
              )}
              {submitting ? "Creating..." : "Create Task"}
            </button>
            <button
              type="button"
              onClick={() => router.back()}
              className="rounded-xl px-6 py-3 text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-all"
            >
              Cancel
            </button>
          </div>
        </form>
      </motion.div>
    </div>
  );
}
