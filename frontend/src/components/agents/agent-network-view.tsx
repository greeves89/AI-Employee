"use client";

import { useMemo, useEffect, useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Bot, MessageSquare, Users, X, ArrowRight } from "lucide-react";
import type { Agent } from "@/lib/types";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";

/* -------------------------------------------------------------------------- */
/*  Types                                                                      */
/* -------------------------------------------------------------------------- */

interface ApiConnection {
  from: string;
  to: string;
  count: number;
  last_at: string;
}

interface ApiBubble {
  from: string;
  to: string;
  text: string;
  from_name: string;
  timestamp: string;
}

/* -------------------------------------------------------------------------- */
/*  Status helpers                                                             */
/* -------------------------------------------------------------------------- */

function statusColor(state: Agent["state"]) {
  switch (state) {
    case "idle":
    case "running":
      return { ring: "border-emerald-400/60", glow: "shadow-emerald-500/25", dot: "bg-emerald-400", pulse: "bg-emerald-400" };
    case "working":
      return { ring: "border-blue-400/60", glow: "shadow-blue-500/25", dot: "bg-blue-400", pulse: "bg-blue-400" };
    case "stopped":
      return { ring: "border-zinc-500/40", glow: "shadow-zinc-500/10", dot: "bg-zinc-500", pulse: "" };
    case "error":
      return { ring: "border-red-400/60", glow: "shadow-red-500/25", dot: "bg-red-400", pulse: "bg-red-400" };
    default:
      return { ring: "border-zinc-500/40", glow: "shadow-zinc-500/10", dot: "bg-zinc-500", pulse: "" };
  }
}

/* -------------------------------------------------------------------------- */
/*  Particle                                                                   */
/* -------------------------------------------------------------------------- */

function FlowingParticle({ x1, y1, x2, y2, delay, duration }: {
  x1: number; y1: number; x2: number; y2: number; delay: number; duration: number;
}) {
  return (
    <motion.circle
      r={2.5}
      fill="url(#particleGradient)"
      initial={{ cx: x1, cy: y1, opacity: 0 }}
      animate={{
        cx: [x1, (x1 + x2) / 2, x2],
        cy: [y1, (y1 + y2) / 2, y2],
        opacity: [0, 1, 1, 0],
      }}
      transition={{ duration, delay, repeat: Infinity, ease: "linear" }}
    />
  );
}

/* -------------------------------------------------------------------------- */
/*  Floating chat bubble                                                       */
/* -------------------------------------------------------------------------- */

function FloatingBubble({ fromX, fromY, toX, toY, text, delay }: {
  fromX: number; fromY: number; toX: number; toY: number; text: string; delay: number;
}) {
  const midX = (fromX + toX) / 2;
  const midY = (fromY + toY) / 2 - 20;

  return (
    <motion.div
      className="absolute pointer-events-none z-20"
      initial={{ x: fromX - 50, y: fromY - 12, opacity: 0, scale: 0.7 }}
      animate={{
        x: [fromX - 50, midX - 50, toX - 50],
        y: [fromY - 12, midY - 12, toY - 12],
        opacity: [0, 1, 1, 0],
        scale: [0.7, 1, 1, 0.7],
      }}
      transition={{ duration: 6, delay, repeat: Infinity, ease: "easeInOut" }}
    >
      <div className="flex items-center gap-1.5 rounded-full bg-card/90 backdrop-blur-md border border-foreground/[0.08] px-3 py-1.5 shadow-lg">
        <MessageSquare className="h-3 w-3 text-primary/70 shrink-0" />
        <span className="text-[10px] font-medium text-foreground/80 whitespace-nowrap max-w-[100px] truncate">
          {text}
        </span>
      </div>
    </motion.div>
  );
}

/* -------------------------------------------------------------------------- */
/*  Main component                                                             */
/* -------------------------------------------------------------------------- */

interface AgentNetworkViewProps {
  agents: Agent[];
}

export function AgentNetworkView({ agents }: AgentNetworkViewProps) {
  const [containerSize, setContainerSize] = useState({ w: 800, h: 600 });
  const [hovered, setHovered] = useState<number | null>(null);
  const [connections, setConnections] = useState<ApiConnection[]>([]);
  const [bubbles, setBubbles] = useState<ApiBubble[]>([]);
  const [messageCount, setMessageCount] = useState(0);

  // Conversation modal
  const [convoOpen, setConvoOpen] = useState(false);
  const [convoAgents, setConvoAgents] = useState<{ a: string; b: string; aName: string; bName: string } | null>(null);
  const [convoMessages, setConvoMessages] = useState<{ from_id: string; from_name: string; to_id: string; text: string; timestamp: string }[]>([]);
  const [convoLoading, setConvoLoading] = useState(false);

  const openConversation = async (agentA: string, agentB: string) => {
    const nameA = agents.find((a) => a.id === agentA)?.name || agentA;
    const nameB = agents.find((a) => a.id === agentB)?.name || agentB;
    setConvoAgents({ a: agentA, b: agentB, aName: nameA, bName: nameB });
    setConvoOpen(true);
    setConvoLoading(true);
    try {
      const data = await api.getAgentConversation(agentA, agentB);
      setConvoMessages(data.messages);
    } catch {
      setConvoMessages([]);
    } finally {
      setConvoLoading(false);
    }
  };

  // Fetch real inter-agent messages
  const fetchMessages = useCallback(async () => {
    try {
      const data = await api.getAgentMessages(1440); // last 24 hours
      setConnections(data.connections);
      setBubbles(data.messages);
      setMessageCount(data.total);
    } catch {
      // API might not be available yet
    }
  }, []);

  useEffect(() => {
    fetchMessages();
    const interval = setInterval(fetchMessages, 10000); // refresh every 10s
    return () => clearInterval(interval);
  }, [fetchMessages]);

  // Responsive resize
  useEffect(() => {
    function measure() {
      const el = document.getElementById("network-container");
      if (el) {
        setContainerSize({ w: el.clientWidth, h: Math.max(el.clientHeight, 520) });
      }
    }
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  const cx = containerSize.w / 2;
  const cy = containerSize.h / 2;
  const radius = Math.min(cx, cy) - 90;

  // Build agent ID → index map
  const agentIndexMap = useMemo(() => {
    const map: Record<string, number> = {};
    agents.forEach((a, i) => { map[a.id] = i; });
    return map;
  }, [agents]);

  // Calculate circular positions (scales with agent count)
  const positions = useMemo(() => {
    return agents.map((_, i) => {
      const angle = (2 * Math.PI * i) / agents.length - Math.PI / 2;
      return {
        x: cx + radius * Math.cos(angle),
        y: cy + radius * Math.sin(angle),
      };
    });
  }, [agents.length, cx, cy, radius]);

  // Map API connections to position indices
  const mappedConnections = useMemo(() => {
    return connections
      .map((conn) => ({
        fromIdx: agentIndexMap[conn.from],
        toIdx: agentIndexMap[conn.to],
        count: conn.count,
        active: true,
      }))
      .filter((c) => c.fromIdx !== undefined && c.toIdx !== undefined);
  }, [connections, agentIndexMap]);

  // One bubble per connection — the LATEST message only
  const mappedBubbles = useMemo(() => {
    // For each connection, find the most recent message
    const latestPerPair: Record<string, typeof bubbles[0]> = {};
    for (const msg of bubbles) {
      const pair = [msg.from, msg.to].sort().join(":");
      if (!latestPerPair[pair]) {
        latestPerPair[pair] = msg; // bubbles are already sorted desc
      }
    }
    return Object.values(latestPerPair)
      .map((b, i) => ({
        id: `bubble-${i}`,
        fromIdx: agentIndexMap[b.from],
        toIdx: agentIndexMap[b.to],
        text: b.text,
        fromName: b.from_name,
      }))
      .filter((b) => b.fromIdx !== undefined && b.toIdx !== undefined);
  }, [bubbles, agentIndexMap]);

  if (agents.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-foreground/[0.1] bg-card/30 p-16 text-center">
        <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-foreground/[0.06] mb-4">
          <Bot className="h-7 w-7 text-muted-foreground" />
        </div>
        <h3 className="text-lg font-semibold mb-1.5">No agents to visualize</h3>
        <p className="text-sm text-muted-foreground">Create agents to see the network view.</p>
      </div>
    );
  }

  return (
    <>
    <div
      id="network-container"
      className="relative w-full rounded-xl border border-foreground/[0.06] bg-card/40 backdrop-blur-sm overflow-hidden"
      style={{ minHeight: 520 }}
    >
      {/* Background grid */}
      <div
        className="absolute inset-0 opacity-[0.03]"
        style={{
          backgroundImage: `radial-gradient(circle, currentColor 1px, transparent 1px)`,
          backgroundSize: "24px 24px",
        }}
      />

      {/* SVG layer */}
      <svg
        className="absolute inset-0 w-full h-full"
        viewBox={`0 0 ${containerSize.w} ${containerSize.h}`}
        preserveAspectRatio="xMidYMid meet"
      >
        <defs>
          <linearGradient id="lineGradient" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor="rgb(99,102,241)" stopOpacity={0.5} />
            <stop offset="50%" stopColor="rgb(139,92,246)" stopOpacity={0.7} />
            <stop offset="100%" stopColor="rgb(99,102,241)" stopOpacity={0.5} />
          </linearGradient>
          <linearGradient id="lineGradientActive" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor="rgb(59,130,246)" stopOpacity={0.6} />
            <stop offset="50%" stopColor="rgb(99,102,241)" stopOpacity={0.9} />
            <stop offset="100%" stopColor="rgb(59,130,246)" stopOpacity={0.6} />
          </linearGradient>
          <radialGradient id="particleGradient">
            <stop offset="0%" stopColor="rgb(165,180,252)" stopOpacity={1} />
            <stop offset="100%" stopColor="rgb(99,102,241)" stopOpacity={0} />
          </radialGradient>
        </defs>

        {/* Connection lines (from real data) */}
        {mappedConnections.map((conn, i) => {
          const from = positions[conn.fromIdx];
          const to = positions[conn.toIdx];
          if (!from || !to) return null;

          const midX = (from.x + to.x) / 2;
          const midY = (from.y + to.y) / 2;
          const dx = to.x - from.x;
          const dy = to.y - from.y;
          const offsetX = -dy * 0.12;
          const offsetY = dx * 0.12;
          const ctrlX = midX + offsetX;
          const ctrlY = midY + offsetY;
          const pathD = `M ${from.x} ${from.y} Q ${ctrlX} ${ctrlY} ${to.x} ${to.y}`;

          const isHighlighted = hovered !== null && (hovered === conn.fromIdx || hovered === conn.toIdx);
          // Thicker line = more messages
          const strokeW = Math.min(1 + conn.count * 0.3, 4);

          const fromAgent = agents[conn.fromIdx];
          const toAgent = agents[conn.toIdx];

          return (
            <g key={`conn-${i}`} className="cursor-pointer" onClick={() => {
              if (fromAgent && toAgent) openConversation(fromAgent.id, toAgent.id);
            }}>
              {/* Invisible wide hitbox for easier clicking */}
              <path d={pathD} fill="none" stroke="transparent" strokeWidth={16} />
              <motion.path
                d={pathD}
                fill="none"
                stroke="url(#lineGradientActive)"
                strokeWidth={isHighlighted ? strokeW + 1 : strokeW}
                strokeOpacity={isHighlighted ? 1 : 0.6}
                initial={{ pathLength: 0 }}
                animate={{ pathLength: 1 }}
                transition={{ duration: 1, delay: i * 0.1, ease: "easeOut" }}
              />
              {/* Animated dash */}
              <motion.path
                d={pathD}
                fill="none"
                stroke="rgb(165,180,252)"
                strokeWidth={1}
                strokeOpacity={0.4}
                strokeDasharray="4 8"
                initial={{ strokeDashoffset: 0 }}
                animate={{ strokeDashoffset: -24 }}
                transition={{ duration: 2, repeat: Infinity, ease: "linear" }}
              />
              {/* Particles */}
              <FlowingParticle x1={from.x} y1={from.y} x2={to.x} y2={to.y} delay={i * 0.6} duration={3} />
              <FlowingParticle x1={from.x} y1={from.y} x2={to.x} y2={to.y} delay={i * 0.6 + 1.5} duration={3} />
              {/* Message count badge at midpoint */}
              <g>
                <circle cx={ctrlX} cy={ctrlY} r={10} fill="rgba(99,102,241,0.15)" />
                <text x={ctrlX} y={ctrlY + 1} textAnchor="middle" dominantBaseline="middle"
                  className="text-[9px] font-bold" fill="rgb(165,180,252)">{conn.count}</text>
              </g>
            </g>
          );
        })}
      </svg>

      {/* Chat bubbles (real messages) */}
      <AnimatePresence>
        {mappedBubbles.map((bubble, i) => {
          const from = positions[bubble.fromIdx];
          const to = positions[bubble.toIdx];
          if (!from || !to) return null;
          return (
            <FloatingBubble
              key={bubble.id}
              fromX={from.x}
              fromY={from.y}
              toX={to.x}
              toY={to.y}
              text={bubble.text}
              delay={i * 3}
            />
          );
        })}
      </AnimatePresence>

      {/* Agent nodes */}
      {agents.map((agent, i) => {
        const pos = positions[i];
        if (!pos) return null;
        const colors = statusColor(agent.state);
        const isActive = ["idle", "running", "working"].includes(agent.state);
        const isHovered = hovered === i;
        const nodeSize = 72;

        // Count connections for this agent
        const agentConnCount = mappedConnections.filter(
          (c) => c.fromIdx === i || c.toIdx === i
        ).length;

        return (
          <motion.div
            key={agent.id}
            className="absolute z-10"
            style={{ left: pos.x - nodeSize / 2, top: pos.y - nodeSize / 2, width: nodeSize, height: nodeSize }}
            initial={{ scale: 0, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ delay: i * 0.08, type: "spring", stiffness: 260, damping: 20 }}
            onMouseEnter={() => setHovered(i)}
            onMouseLeave={() => setHovered(null)}
          >
            {/* Pulse ring */}
            {isActive && colors.pulse && (
              <motion.div
                className={cn("absolute inset-0 rounded-full", colors.pulse)}
                animate={{ scale: [1, 1.5, 1.5], opacity: [0.3, 0, 0] }}
                transition={{ duration: 2.5, repeat: Infinity, ease: "easeOut" }}
              />
            )}

            {/* Node */}
            <motion.div
              className={cn(
                "relative w-full h-full rounded-full bg-card/80 backdrop-blur-sm border-2 flex items-center justify-center cursor-pointer transition-all duration-300",
                colors.ring, colors.glow, isHovered && "shadow-lg"
              )}
              animate={isHovered ? { scale: 1.12 } : { scale: 1 }}
              transition={{ type: "spring", stiffness: 400, damping: 25 }}
            >
              {isActive && (
                <div className={cn(
                  "absolute inset-0 rounded-full opacity-20",
                  agent.state === "working"
                    ? "bg-gradient-to-br from-blue-500/30 to-transparent"
                    : "bg-gradient-to-br from-emerald-500/30 to-transparent"
                )} />
              )}
              <Bot className={cn("h-6 w-6 relative z-10", isActive ? "text-foreground" : "text-muted-foreground/60")} />
            </motion.div>

            {/* Status dot */}
            <div className={cn("absolute -bottom-0.5 right-1 h-3 w-3 rounded-full border-2 border-card", colors.dot)} />

            {/* Name + connection count */}
            <motion.div
              className="absolute left-1/2 -translate-x-1/2 whitespace-nowrap"
              style={{ top: nodeSize + 6 }}
              initial={{ opacity: 0, y: -4 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.08 + 0.3 }}
            >
              <div className="flex flex-col items-center gap-0.5">
                <span className="text-[11px] font-semibold text-foreground/90 bg-card/70 backdrop-blur-sm px-2 py-0.5 rounded-md border border-foreground/[0.06]">
                  {agent.name}
                </span>
                {agentConnCount > 0 && (
                  <span className="text-[9px] text-indigo-400/70 font-medium">
                    {agentConnCount} connection{agentConnCount !== 1 ? "s" : ""}
                  </span>
                )}
              </div>
            </motion.div>

            {/* Hover tooltip */}
            <AnimatePresence>
              {isHovered && (
                <motion.div
                  className="absolute z-30 left-1/2 -translate-x-1/2 pointer-events-none"
                  style={{ bottom: nodeSize + 8 }}
                  initial={{ opacity: 0, y: 6, scale: 0.95 }}
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  exit={{ opacity: 0, y: 6, scale: 0.95 }}
                  transition={{ duration: 0.15 }}
                >
                  <div className="rounded-xl bg-card/95 backdrop-blur-md border border-foreground/[0.08] p-3 shadow-xl min-w-[160px]">
                    <div className="flex items-center gap-2 mb-2">
                      <div className={cn("h-2 w-2 rounded-full", colors.dot)} />
                      <span className="text-xs font-semibold">{agent.name}</span>
                    </div>
                    <div className="space-y-1">
                      <div className="flex justify-between text-[10px]">
                        <span className="text-muted-foreground">State</span>
                        <span className="font-medium capitalize">{agent.state}</span>
                      </div>
                      <div className="flex justify-between text-[10px]">
                        <span className="text-muted-foreground">Model</span>
                        <span className="font-mono font-medium">{agent.model.split("-").slice(0, 2).join("-")}</span>
                      </div>
                      {agentConnCount > 0 && (
                        <div className="flex justify-between text-[10px]">
                          <span className="text-muted-foreground">Connections</span>
                          <span className="font-medium text-indigo-400">{agentConnCount}</span>
                        </div>
                      )}
                      {agent.current_task && (
                        <div className="mt-1.5 pt-1.5 border-t border-foreground/[0.06]">
                          <span className="text-[10px] text-blue-400 font-medium truncate block max-w-[140px]">{agent.current_task}</span>
                        </div>
                      )}
                    </div>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </motion.div>
        );
      })}

      {/* Center info */}
      <motion.div
        className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 pointer-events-none z-0"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.6 }}
      >
        <div className="flex flex-col items-center gap-1">
          <div className="h-12 w-12 rounded-xl bg-foreground/[0.04] flex items-center justify-center">
            <Users className="h-5 w-5 text-muted-foreground/30" />
          </div>
          <span className="text-[10px] font-medium text-muted-foreground/30">
            {agents.length} Agent{agents.length !== 1 ? "s" : ""}
          </span>
          {messageCount > 0 && (
            <span className="text-[9px] font-medium text-indigo-400/50">
              {messageCount} message{messageCount !== 1 ? "s" : ""} (2h)
            </span>
          )}
        </div>
      </motion.div>

      {/* Legend */}
      <motion.div
        className="absolute bottom-4 left-4 flex items-center gap-4 z-20"
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 1 }}
      >
        <div className="flex items-center gap-4 rounded-lg bg-card/70 backdrop-blur-sm border border-foreground/[0.06] px-3 py-2">
          <div className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-emerald-400" />
            <span className="text-[10px] text-muted-foreground">Idle</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-blue-400" />
            <span className="text-[10px] text-muted-foreground">Working</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-zinc-500" />
            <span className="text-[10px] text-muted-foreground">Stopped</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="h-1.5 w-4 rounded-full bg-gradient-to-r from-indigo-400 to-violet-400 opacity-60" />
            <span className="text-[10px] text-muted-foreground">Active link</span>
          </div>
        </div>
      </motion.div>
    </div>

    {/* Clickable Conversations */}
    {connections.length > 0 && (
      <div className="rounded-xl border border-foreground/[0.06] bg-card/40 backdrop-blur-sm p-4 mt-4">
        <div className="flex items-center gap-2 mb-3">
          <MessageSquare className="h-4 w-4 text-indigo-400" />
          <span className="text-xs font-semibold text-foreground/80">Konversationen</span>
          <span className="text-[10px] text-muted-foreground/50 ml-auto">{messageCount} Nachrichten gesamt</span>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
          {connections.map((conn, i) => {
            const agentA = agents.find((a) => a.id === conn.from);
            const agentB = agents.find((a) => a.id === conn.to);
            return (
              <motion.button
                key={`conv-${i}`}
                onClick={() => openConversation(conn.from, conn.to)}
                className="flex items-center gap-2 rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] px-3 py-2.5 text-left hover:bg-foreground/[0.05] hover:border-indigo-500/20 transition-all group"
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.05 }}
              >
                <div className="flex items-center gap-1.5 flex-1 min-w-0">
                  <span className="text-[11px] font-semibold text-indigo-400 truncate">{agentA?.name || conn.from}</span>
                  <ArrowRight className="h-3 w-3 text-muted-foreground/30 shrink-0 group-hover:text-indigo-400/50 transition-colors" />
                  <span className="text-[11px] font-semibold text-violet-400 truncate">{agentB?.name || conn.to}</span>
                </div>
                <span className="inline-flex items-center justify-center h-5 min-w-[20px] rounded-full bg-indigo-500/10 border border-indigo-500/20 px-1.5 text-[10px] font-bold text-indigo-400 shrink-0">
                  {conn.count}
                </span>
              </motion.button>
            );
          })}
        </div>
      </div>
    )}

    {/* Conversation Modal */}
    <AnimatePresence>
      {convoOpen && convoAgents && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={() => setConvoOpen(false)}>
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 10 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 10 }}
            className="w-full max-w-2xl max-h-[80vh] rounded-2xl border border-foreground/[0.08] bg-card shadow-2xl flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div className="flex items-center justify-between px-5 py-4 border-b border-foreground/[0.06]">
              <div className="flex items-center gap-2">
                <MessageSquare className="h-4 w-4 text-indigo-400" />
                <span className="text-sm font-semibold text-indigo-400">{convoAgents.aName}</span>
                <ArrowRight className="h-3.5 w-3.5 text-muted-foreground/40" />
                <span className="text-sm font-semibold text-violet-400">{convoAgents.bName}</span>
                <span className="text-[10px] text-muted-foreground/50 ml-2">
                  {convoMessages.length} Nachrichten
                </span>
              </div>
              <button onClick={() => setConvoOpen(false)} className="rounded-lg p-1.5 hover:bg-foreground/[0.06] transition-colors">
                <X className="h-4 w-4 text-muted-foreground" />
              </button>
            </div>

            {/* Messages */}
            <div className="flex-1 overflow-y-auto px-5 py-4 space-y-3">
              {convoLoading ? (
                <div className="flex items-center justify-center py-12">
                  <div className="h-6 w-6 border-2 border-indigo-400/30 border-t-indigo-400 rounded-full animate-spin" />
                </div>
              ) : convoMessages.length === 0 ? (
                <p className="text-sm text-muted-foreground/50 text-center py-12">Keine Nachrichten</p>
              ) : (
                convoMessages.map((msg, i) => {
                  const isFromA = msg.from_id === convoAgents.a;
                  return (
                    <motion.div
                      key={i}
                      className={cn("flex", isFromA ? "justify-start" : "justify-end")}
                      initial={{ opacity: 0, y: 6 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: i * 0.02 }}
                    >
                      <div className={cn(
                        "max-w-[80%] rounded-2xl px-4 py-2.5",
                        isFromA
                          ? "bg-indigo-500/10 border border-indigo-500/15 rounded-tl-md"
                          : "bg-violet-500/10 border border-violet-500/15 rounded-tr-md"
                      )}>
                        <div className="flex items-center gap-2 mb-1">
                          <span className={cn("text-[10px] font-semibold", isFromA ? "text-indigo-400" : "text-violet-400")}>
                            {msg.from_name}
                          </span>
                          <span className="text-[9px] text-muted-foreground/40">
                            {new Date(msg.timestamp).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                          </span>
                        </div>
                        <p className="text-xs text-foreground/80 whitespace-pre-wrap break-words leading-relaxed">{msg.text}</p>
                      </div>
                    </motion.div>
                  );
                })
              )}
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
    </>
  );
}
