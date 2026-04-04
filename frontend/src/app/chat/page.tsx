"use client";

import { useState, useEffect, useMemo } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { motion } from "framer-motion";
import {
  Bot, ChevronDown, Cpu, MemoryStick, Sparkles, MessageSquare,
  Zap, Brain, FolderOpen, ListTodo, Check,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { AgentChat } from "@/components/agents/chat";
import * as api from "@/lib/api";
import type { Agent } from "@/lib/types";

const agentStateConfig: Record<string, { online: boolean; label: string; color: string }> = {
  running: { online: true, label: "Bereit", color: "text-emerald-400" },
  idle: { online: true, label: "Bereit", color: "text-emerald-400" },
  working: { online: true, label: "Arbeitet", color: "text-blue-400" },
  stopped: { online: false, label: "Gestoppt", color: "text-zinc-400" },
  error: { online: false, label: "Fehler", color: "text-red-400" },
  created: { online: false, label: "Startet", color: "text-violet-400" },
};

const quickActions = [
  { icon: Zap, label: "Code schreiben", prompt: "Schreibe mir " },
  { icon: Brain, label: "Recherchieren", prompt: "Recherchiere " },
  { icon: FolderOpen, label: "Dateien verwalten", prompt: "Zeige mir die Dateien in " },
  { icon: ListTodo, label: "Aufgabe erstellen", prompt: "Erstelle eine Aufgabe: " },
];

export default function ChatPage() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const agentId = searchParams.get("agent");
  const sessionId = searchParams.get("session");
  const chatKey = searchParams.get("t") || "default"; // forces remount on new chat

  const [agents, setAgents] = useState<Agent[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null);
  const [showAgentPicker, setShowAgentPicker] = useState(false);
  const [loading, setLoading] = useState(true);

  // Load agents
  useEffect(() => {
    const load = async () => {
      try {
        const data = await api.getAgents();
        const onlineAgents = data.agents.filter((a) =>
          ["running", "idle", "working"].includes(a.state)
        );
        setAgents(onlineAgents);

        // Select agent from URL or first available
        if (agentId) {
          const found = data.agents.find((a) => a.id === agentId);
          if (found) setSelectedAgent(found);
          else if (onlineAgents.length > 0) setSelectedAgent(onlineAgents[0]);
        } else if (onlineAgents.length > 0) {
          setSelectedAgent(onlineAgents[0]);
          router.replace(`/chat?agent=${onlineAgents[0].id}`);
        }
      } catch {
        // ignore
      } finally {
        setLoading(false);
      }
    };
    load();
    const interval = setInterval(async () => {
      try {
        const data = await api.getAgents();
        setAgents(data.agents.filter((a) => ["running", "idle", "working"].includes(a.state)));
        if (selectedAgent) {
          const updated = data.agents.find((a) => a.id === selectedAgent.id);
          if (updated) setSelectedAgent(updated);
        }
      } catch { /* ignore */ }
    }, 15000);
    return () => clearInterval(interval);
  }, [agentId]);

  const switchAgent = (agent: Agent) => {
    setSelectedAgent(agent);
    setShowAgentPicker(false);
    router.push(`/chat?agent=${agent.id}&t=${Date.now()}`);
  };

  const stateConfig = selectedAgent
    ? agentStateConfig[selectedAgent.state] ?? agentStateConfig.stopped
    : null;

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary/30 border-t-primary" />
      </div>
    );
  }

  // No agents available
  if (agents.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center text-center px-8">
        <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-primary/10 mb-4">
          <Bot className="h-8 w-8 text-primary" />
        </div>
        <h2 className="text-xl font-semibold mb-2">Kein Agent online</h2>
        <p className="text-muted-foreground text-sm max-w-md mb-6">
          Starte einen Agent im Dashboard, um mit ihm zu chatten.
        </p>
        <a
          href="/agents"
          className="inline-flex items-center gap-2 rounded-xl bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 hover:bg-primary/90 transition-all"
        >
          Agents verwalten
        </a>
      </div>
    );
  }

  // No agent selected yet — show welcome
  if (!selectedAgent) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary/30 border-t-primary" />
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Top bar with agent selector */}
      <div className="flex items-center justify-between border-b border-border px-4 py-2 shrink-0 bg-card/30 backdrop-blur-sm">
        {/* Agent Selector */}
        <div className="relative">
          <button
            onClick={() => setShowAgentPicker(!showAgentPicker)}
            className="flex items-center gap-2.5 rounded-xl px-3 py-2 hover:bg-accent/50 transition-all"
          >
            <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br from-violet-500 to-purple-600 shadow-md">
              <Bot className="h-3.5 w-3.5 text-white" />
            </div>
            <div className="text-left">
              <p className="text-sm font-semibold leading-tight">{selectedAgent.name}</p>
              <div className="flex items-center gap-1.5">
                <span className="relative flex h-1.5 w-1.5">
                  {stateConfig?.online && (
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                  )}
                  <span className={cn(
                    "relative inline-flex h-1.5 w-1.5 rounded-full",
                    stateConfig?.online ? "bg-emerald-500" : "bg-red-500"
                  )} />
                </span>
                <span className={cn("text-[10px] font-medium", stateConfig?.color)}>
                  {stateConfig?.label}
                </span>
              </div>
            </div>
            <ChevronDown className="h-3.5 w-3.5 text-muted-foreground ml-1" />
          </button>

          {/* Dropdown */}
          {showAgentPicker && (
            <>
              <div className="fixed inset-0 z-40" onClick={() => setShowAgentPicker(false)} />
              <motion.div
                initial={{ opacity: 0, y: -4, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: -4, scale: 0.98 }}
                transition={{ duration: 0.15 }}
                className="absolute top-full left-0 mt-1 z-50 w-72 rounded-xl border border-border bg-card shadow-2xl shadow-black/20 overflow-hidden"
              >
                <div className="p-2">
                  <p className="px-2 py-1.5 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground/60">
                    Agent wechseln
                  </p>
                  {agents.map((agent) => {
                    const sc = agentStateConfig[agent.state] ?? agentStateConfig.stopped;
                    const isSelected = agent.id === selectedAgent.id;
                    return (
                      <button
                        key={agent.id}
                        onClick={() => switchAgent(agent)}
                        className={cn(
                          "w-full flex items-center gap-3 rounded-lg px-3 py-2.5 text-left transition-all",
                          isSelected
                            ? "bg-primary/10 text-foreground"
                            : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
                        )}
                      >
                        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-violet-500/20 to-purple-600/20">
                          <Bot className="h-4 w-4 text-violet-400" />
                        </div>
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium truncate">{agent.name}</p>
                          <div className="flex items-center gap-2 text-[10px]">
                            <span className={cn("font-medium", sc.color)}>{sc.label}</span>
                            {agent.current_task && (
                              <span className="text-blue-400 truncate">
                                {agent.current_task.slice(0, 30)}...
                              </span>
                            )}
                          </div>
                        </div>
                        {isSelected && <Check className="h-4 w-4 text-primary shrink-0" />}
                      </button>
                    );
                  })}
                </div>
              </motion.div>
            </>
          )}
        </div>

        {/* Right side: agent stats */}
        <div className="flex items-center gap-4 text-xs text-muted-foreground">
          {selectedAgent.cpu_percent != null && (
            <div className="hidden md:flex items-center gap-1.5">
              <Cpu className="h-3 w-3 text-cyan-400" />
              <span className="text-cyan-400 font-medium tabular-nums">
                {selectedAgent.cpu_percent.toFixed(1)}%
              </span>
            </div>
          )}
          {selectedAgent.memory_usage_mb != null && (
            <div className="hidden md:flex items-center gap-1.5">
              <MemoryStick className="h-3 w-3 text-emerald-400" />
              <span className="text-emerald-400 font-medium tabular-nums">
                {selectedAgent.memory_usage_mb.toFixed(0)} MB
              </span>
            </div>
          )}
          <a
            href={`/agents/${selectedAgent.id}`}
            className="text-[11px] text-muted-foreground/60 hover:text-foreground transition-colors"
          >
            Details
          </a>
        </div>
      </div>

      {/* Chat Area — key forces remount on agent/session/new-chat change */}
      <div className="flex-1 min-h-0">
        <AgentChat key={`${selectedAgent.id}-${sessionId || chatKey}`} agentId={selectedAgent.id} initialSessionId={sessionId} />
      </div>
    </div>
  );
}
