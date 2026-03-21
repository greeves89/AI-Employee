"use client";

import { useMemo, useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Bot, MessageSquare, Users } from "lucide-react";
import type { Agent } from "@/lib/types";
import { cn } from "@/lib/utils";

/* -------------------------------------------------------------------------- */
/*  Mock inter-agent communication data (will be wired to API later)          */
/* -------------------------------------------------------------------------- */

interface Connection {
  from: number; // index in agents array
  to: number;
  active: boolean;
}

interface ChatBubble {
  id: string;
  from: number;
  to: number;
  text: string;
}

interface MeetingRoom {
  id: string;
  label: string;
  members: number[]; // indices
}

function buildMockConnections(agentCount: number): Connection[] {
  if (agentCount < 2) return [];
  const conns: Connection[] = [];
  for (let i = 0; i < agentCount; i++) {
    // connect to next agent in circle
    const next = (i + 1) % agentCount;
    conns.push({ from: i, to: next, active: i % 2 === 0 });
    // a few cross-connections for visual interest
    if (agentCount > 3 && i % 3 === 0) {
      const cross = (i + Math.floor(agentCount / 2)) % agentCount;
      if (cross !== i && cross !== next) {
        conns.push({ from: i, to: cross, active: i % 4 === 0 });
      }
    }
  }
  return conns;
}

function buildMockBubbles(agentCount: number): ChatBubble[] {
  if (agentCount < 2) return [];
  const messages = [
    "Task complete",
    "Need review",
    "Deploying...",
    "Bug found",
    "PR merged",
    "Tests pass",
    "On it!",
    "Syncing data",
  ];
  const bubbles: ChatBubble[] = [];
  for (let i = 0; i < Math.min(agentCount, 4); i++) {
    const to = (i + 1) % agentCount;
    bubbles.push({
      id: `bubble-${i}`,
      from: i,
      to,
      text: messages[i % messages.length],
    });
  }
  return bubbles;
}

function buildMockMeetingRooms(agentCount: number): MeetingRoom[] {
  if (agentCount < 3) return [];
  return [
    {
      id: "room-1",
      label: "Meeting Room #1",
      members: [0, 1, Math.min(2, agentCount - 1)],
    },
  ];
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
    case "created":
      return { ring: "border-violet-400/60", glow: "shadow-violet-500/25", dot: "bg-violet-400", pulse: "bg-violet-400" };
    default:
      return { ring: "border-zinc-500/40", glow: "shadow-zinc-500/10", dot: "bg-zinc-500", pulse: "" };
  }
}

/* -------------------------------------------------------------------------- */
/*  Particle component — dot flowing along an SVG path                         */
/* -------------------------------------------------------------------------- */

function FlowingParticle({
  x1,
  y1,
  x2,
  y2,
  delay,
  duration,
}: {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  delay: number;
  duration: number;
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
      transition={{
        duration,
        delay,
        repeat: Infinity,
        ease: "linear",
      }}
    />
  );
}

/* -------------------------------------------------------------------------- */
/*  Chat bubble floating between nodes                                         */
/* -------------------------------------------------------------------------- */

function FloatingBubble({
  fromX,
  fromY,
  toX,
  toY,
  text,
  delay,
}: {
  fromX: number;
  fromY: number;
  toX: number;
  toY: number;
  text: string;
  delay: number;
}) {
  const midX = (fromX + toX) / 2;
  const midY = (fromY + toY) / 2 - 20;

  return (
    <motion.div
      className="absolute pointer-events-none z-20"
      initial={{ x: fromX - 40, y: fromY - 12, opacity: 0, scale: 0.7 }}
      animate={{
        x: [fromX - 40, midX - 40, toX - 40],
        y: [fromY - 12, midY - 12, toY - 12],
        opacity: [0, 1, 1, 0],
        scale: [0.7, 1, 1, 0.7],
      }}
      transition={{
        duration: 5,
        delay,
        repeat: Infinity,
        ease: "easeInOut",
      }}
    >
      <div className="flex items-center gap-1.5 rounded-full bg-card/90 backdrop-blur-md border border-foreground/[0.08] px-3 py-1.5 shadow-lg">
        <MessageSquare className="h-3 w-3 text-primary/70 shrink-0" />
        <span className="text-[10px] font-medium text-foreground/80 whitespace-nowrap max-w-[80px] truncate">
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
  const radius = Math.min(cx, cy) - 80;

  // Calculate circular positions
  const positions = useMemo(() => {
    return agents.map((_, i) => {
      const angle = (2 * Math.PI * i) / agents.length - Math.PI / 2;
      return {
        x: cx + radius * Math.cos(angle),
        y: cy + radius * Math.sin(angle),
      };
    });
  }, [agents.length, cx, cy, radius]);

  const connections = useMemo(() => buildMockConnections(agents.length), [agents.length]);
  const bubbles = useMemo(() => buildMockBubbles(agents.length), [agents.length]);
  const meetingRooms = useMemo(() => buildMockMeetingRooms(agents.length), [agents.length]);

  if (agents.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-foreground/[0.1] bg-card/30 p-16 text-center">
        <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-foreground/[0.06] mb-4">
          <Bot className="h-7 w-7 text-muted-foreground" />
        </div>
        <h3 className="text-lg font-semibold mb-1.5">No agents to visualize</h3>
        <p className="text-sm text-muted-foreground">
          Create agents to see the network view.
        </p>
      </div>
    );
  }

  return (
    <div
      id="network-container"
      className="relative w-full rounded-xl border border-foreground/[0.06] bg-card/40 backdrop-blur-sm overflow-hidden"
      style={{ minHeight: 520 }}
    >
      {/* Background grid pattern */}
      <div
        className="absolute inset-0 opacity-[0.03]"
        style={{
          backgroundImage: `radial-gradient(circle, currentColor 1px, transparent 1px)`,
          backgroundSize: "24px 24px",
        }}
      />

      {/* SVG layer — connections, meeting rooms, particles */}
      <svg
        className="absolute inset-0 w-full h-full"
        viewBox={`0 0 ${containerSize.w} ${containerSize.h}`}
        preserveAspectRatio="xMidYMid meet"
      >
        <defs>
          {/* Gradient for connection lines */}
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
          {/* Animated dash */}
          <filter id="glow">
            <feGaussianBlur stdDeviation="3" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* Meeting rooms — dashed circles */}
        {meetingRooms.map((room) => {
          const memberPositions = room.members
            .filter((idx) => idx < positions.length)
            .map((idx) => positions[idx]);
          if (memberPositions.length < 2) return null;
          const roomCx =
            memberPositions.reduce((s, p) => s + p.x, 0) / memberPositions.length;
          const roomCy =
            memberPositions.reduce((s, p) => s + p.y, 0) / memberPositions.length;
          const roomRadius =
            Math.max(
              ...memberPositions.map((p) =>
                Math.sqrt((p.x - roomCx) ** 2 + (p.y - roomCy) ** 2)
              )
            ) + 60;

          return (
            <g key={room.id}>
              <motion.circle
                cx={roomCx}
                cy={roomCy}
                r={roomRadius}
                fill="none"
                stroke="rgb(168,85,247)"
                strokeOpacity={0.15}
                strokeWidth={1.5}
                strokeDasharray="8 6"
                initial={{ pathLength: 0, opacity: 0 }}
                animate={{ pathLength: 1, opacity: 1 }}
                transition={{ duration: 1.5, ease: "easeOut" }}
              />
              {/* Subtle fill glow */}
              <circle
                cx={roomCx}
                cy={roomCy}
                r={roomRadius}
                fill="rgb(168,85,247)"
                fillOpacity={0.02}
              />
              {/* Label */}
              <g>
                <rect
                  x={roomCx - 56}
                  y={roomCy + roomRadius - 8}
                  width={112}
                  height={20}
                  rx={10}
                  fill="rgb(168,85,247)"
                  fillOpacity={0.1}
                />
                <text
                  x={roomCx}
                  y={roomCy + roomRadius + 6}
                  textAnchor="middle"
                  className="text-[10px] font-medium"
                  fill="rgb(168,85,247)"
                  fillOpacity={0.6}
                >
                  <tspan alignmentBaseline="middle">
                    <Users className="inline h-3 w-3" />
                  </tspan>
                  {room.label}
                </text>
              </g>
            </g>
          );
        })}

        {/* Connection lines */}
        {connections.map((conn, i) => {
          const from = positions[conn.from];
          const to = positions[conn.to];
          if (!from || !to) return null;

          // Slight curve via a control point
          const midX = (from.x + to.x) / 2;
          const midY = (from.y + to.y) / 2;
          const dx = to.x - from.x;
          const dy = to.y - from.y;
          const offsetX = -dy * 0.15;
          const offsetY = dx * 0.15;
          const ctrlX = midX + offsetX;
          const ctrlY = midY + offsetY;
          const pathD = `M ${from.x} ${from.y} Q ${ctrlX} ${ctrlY} ${to.x} ${to.y}`;

          const isHighlighted =
            hovered !== null && (hovered === conn.from || hovered === conn.to);

          return (
            <g key={`conn-${i}`}>
              {/* Base line */}
              <motion.path
                d={pathD}
                fill="none"
                stroke={conn.active ? "url(#lineGradientActive)" : "url(#lineGradient)"}
                strokeWidth={isHighlighted ? 2 : 1}
                strokeOpacity={isHighlighted ? 1 : 0.5}
                initial={{ pathLength: 0 }}
                animate={{ pathLength: 1 }}
                transition={{ duration: 1, delay: i * 0.1, ease: "easeOut" }}
              />
              {/* Animated dash overlay for active connections */}
              {conn.active && (
                <motion.path
                  d={pathD}
                  fill="none"
                  stroke="rgb(165,180,252)"
                  strokeWidth={1}
                  strokeOpacity={0.4}
                  strokeDasharray="4 8"
                  initial={{ strokeDashoffset: 0 }}
                  animate={{ strokeDashoffset: -24 }}
                  transition={{
                    duration: 2,
                    repeat: Infinity,
                    ease: "linear",
                  }}
                />
              )}
              {/* Flowing particles on active connections */}
              {conn.active && (
                <>
                  <FlowingParticle
                    x1={from.x}
                    y1={from.y}
                    x2={to.x}
                    y2={to.y}
                    delay={i * 0.8}
                    duration={3}
                  />
                  <FlowingParticle
                    x1={from.x}
                    y1={from.y}
                    x2={to.x}
                    y2={to.y}
                    delay={i * 0.8 + 1.5}
                    duration={3}
                  />
                </>
              )}
            </g>
          );
        })}
      </svg>

      {/* Chat bubbles floating between agents (HTML layer for rich styling) */}
      <AnimatePresence>
        {bubbles.map((bubble, i) => {
          const from = positions[bubble.from];
          const to = positions[bubble.to];
          if (!from || !to) return null;
          return (
            <FloatingBubble
              key={bubble.id}
              fromX={from.x}
              fromY={from.y}
              toX={to.x}
              toY={to.y}
              text={bubble.text}
              delay={i * 2.5}
            />
          );
        })}
      </AnimatePresence>

      {/* Agent nodes (HTML layer for richer styling) */}
      {agents.map((agent, i) => {
        const pos = positions[i];
        if (!pos) return null;
        const colors = statusColor(agent.state);
        const isActive = ["idle", "running", "working"].includes(agent.state);
        const isHovered = hovered === i;
        const nodeSize = 72;

        return (
          <motion.div
            key={agent.id}
            className="absolute z-10"
            style={{
              left: pos.x - nodeSize / 2,
              top: pos.y - nodeSize / 2,
              width: nodeSize,
              height: nodeSize,
            }}
            initial={{ scale: 0, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ delay: i * 0.08, type: "spring", stiffness: 260, damping: 20 }}
            onMouseEnter={() => setHovered(i)}
            onMouseLeave={() => setHovered(null)}
          >
            {/* Outer pulse ring for active agents */}
            {isActive && colors.pulse && (
              <motion.div
                className={cn(
                  "absolute inset-0 rounded-full",
                  colors.pulse
                )}
                animate={{
                  scale: [1, 1.5, 1.5],
                  opacity: [0.3, 0, 0],
                }}
                transition={{
                  duration: 2.5,
                  repeat: Infinity,
                  ease: "easeOut",
                }}
              />
            )}

            {/* Node circle */}
            <motion.div
              className={cn(
                "relative w-full h-full rounded-full bg-card/80 backdrop-blur-sm border-2 flex items-center justify-center cursor-pointer transition-all duration-300",
                colors.ring,
                colors.glow,
                isHovered && "shadow-lg scale-110"
              )}
              style={{ boxShadow: isActive ? undefined : undefined }}
              animate={
                isHovered
                  ? { scale: 1.12 }
                  : { scale: 1 }
              }
              transition={{ type: "spring", stiffness: 400, damping: 25 }}
            >
              {/* Inner gradient glow */}
              {isActive && (
                <div
                  className={cn(
                    "absolute inset-0 rounded-full opacity-20",
                    agent.state === "working"
                      ? "bg-gradient-to-br from-blue-500/30 to-transparent"
                      : "bg-gradient-to-br from-emerald-500/30 to-transparent"
                  )}
                />
              )}
              <Bot
                className={cn(
                  "h-6 w-6 relative z-10",
                  isActive ? "text-foreground" : "text-muted-foreground/60"
                )}
              />
            </motion.div>

            {/* Status dot */}
            <div
              className={cn(
                "absolute -bottom-0.5 right-1 h-3 w-3 rounded-full border-2 border-card",
                colors.dot
              )}
            />

            {/* Agent name label */}
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
                {agent.role && (
                  <span className="text-[9px] text-muted-foreground/60 font-medium">
                    {agent.role}
                  </span>
                )}
              </div>
            </motion.div>

            {/* Hover tooltip with details */}
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
                        <span className="font-mono font-medium">
                          {agent.model.split("-").slice(0, 2).join("-")}
                        </span>
                      </div>
                      {agent.current_task && (
                        <div className="mt-1.5 pt-1.5 border-t border-foreground/[0.06]">
                          <span className="text-[10px] text-blue-400 font-medium truncate block max-w-[140px]">
                            {agent.current_task}
                          </span>
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

      {/* Center label */}
      <motion.div
        className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 pointer-events-none z-0"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.6 }}
      >
        <div className="flex flex-col items-center gap-1">
          <div className="h-10 w-10 rounded-xl bg-foreground/[0.04] flex items-center justify-center">
            <Users className="h-5 w-5 text-muted-foreground/30" />
          </div>
          <span className="text-[10px] font-medium text-muted-foreground/30">
            {agents.length} Agent{agents.length !== 1 ? "s" : ""} Connected
          </span>
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
  );
}
