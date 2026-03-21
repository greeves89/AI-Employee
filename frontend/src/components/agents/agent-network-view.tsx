"use client";

import { useMemo, useEffect, useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Bot, MessageSquare, Users } from "lucide-react";
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

  // Map API bubbles to position indices
  const mappedBubbles = useMemo(() => {
    return bubbles
      .map((b, i) => ({
        id: `bubble-${i}`,
        fromIdx: agentIndexMap[b.from],
        toIdx: agentIndexMap[b.to],
        text: b.text,
        fromName: b.from_name,
      }))
      .filter((b) => b.fromIdx !== undefined && b.toIdx !== undefined)
      .slice(0, 6); // max 6 floating bubbles
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

          return (
            <g key={`conn-${i}`}>
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

    {/* Message Log */}
    {bubbles.length > 0 && (
      <div className="rounded-xl border border-foreground/[0.06] bg-card/40 backdrop-blur-sm p-4 mt-4">
        <div className="flex items-center gap-2 mb-3">
          <MessageSquare className="h-4 w-4 text-indigo-400" />
          <span className="text-xs font-semibold text-foreground/80">Agent-Kommunikation</span>
          <span className="text-[10px] text-muted-foreground/50 ml-auto">{messageCount} Nachrichten (2h)</span>
        </div>
        <div className="space-y-1.5 max-h-48 overflow-y-auto">
          {bubbles.map((msg, i) => {
            const toAgent = agents.find((a) => a.id === msg.to);
            return (
              <motion.div
                key={`log-${i}`}
                className="flex items-start gap-2 text-[11px] py-1.5 px-2 rounded-lg hover:bg-foreground/[0.03] transition-colors"
                initial={{ opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: i * 0.05 }}
              >
                <span className="text-[10px] text-muted-foreground/40 font-mono shrink-0 mt-0.5">
                  {new Date(msg.timestamp).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}
                </span>
                <span className="font-semibold text-indigo-400 shrink-0">{msg.from_name}</span>
                <span className="text-muted-foreground/40">→</span>
                <span className="font-semibold text-violet-400 shrink-0">{toAgent?.name || msg.to}</span>
                <span className="text-muted-foreground/70 truncate">{msg.text}</span>
              </motion.div>
            );
          })}
        </div>
      </div>
    )}
    </>
  );
}
