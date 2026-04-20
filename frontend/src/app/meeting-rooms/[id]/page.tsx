"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useParams, useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import {
  ArrowLeft,
  Play,
  Square,
  Trash2,
  Loader2,
  Users,
  Bot,
  Send,
  MessageSquare,
  CheckCircle2,
  ListTodo,
  Gavel,
  Download,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Header } from "@/components/layout/header";
import * as api from "@/lib/api";
import type { MeetingRoom, MeetingMessage } from "@/lib/types";
import { cn } from "@/lib/utils";

const AGENT_COLORS = [
  "text-blue-400",
  "text-emerald-400",
  "text-purple-400",
  "text-amber-400",
  "text-pink-400",
  "text-cyan-400",
];

const AGENT_BG_COLORS = [
  "bg-blue-500/10",
  "bg-emerald-500/10",
  "bg-purple-500/10",
  "bg-amber-500/10",
  "bg-pink-500/10",
  "bg-cyan-500/10",
];

const AGENT_BORDER_COLORS = [
  "border-blue-500/30",
  "border-emerald-500/30",
  "border-purple-500/30",
  "border-amber-500/30",
  "border-pink-500/30",
  "border-cyan-500/30",
];

const AGENT_RING_COLORS = [
  "ring-blue-500/50",
  "ring-emerald-500/50",
  "ring-purple-500/50",
  "ring-amber-500/50",
  "ring-pink-500/50",
  "ring-cyan-500/50",
];

const AGENT_DOT_COLORS = [
  "bg-blue-400",
  "bg-emerald-400",
  "bg-purple-400",
  "bg-amber-400",
  "bg-pink-400",
  "bg-cyan-400",
];

export default function MeetingRoomDetailPage() {
  const params = useParams();
  const router = useRouter();
  const roomId = params.id as string;

  const [room, setRoom] = useState<MeetingRoom | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [initialMessage, setInitialMessage] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const prevMessageCount = useRef(0);

  const fetchRoom = useCallback(async () => {
    try {
      const data = await api.getMeetingRoom(roomId);
      setRoom(data);
    } catch {
      // room may have been deleted
    } finally {
      setLoading(false);
    }
  }, [roomId]);

  useEffect(() => {
    fetchRoom();
    const interval = setInterval(fetchRoom, 3000);
    return () => clearInterval(interval);
  }, [fetchRoom]);

  useEffect(() => {
    const count = room?.messages?.length || 0;
    if (count > prevMessageCount.current) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
    prevMessageCount.current = count;
  }, [room?.messages?.length]);

  const getAgentIndex = (agentId: string) => {
    if (!room) return 0;
    const idx = room.agent_ids.indexOf(agentId);
    return idx >= 0 ? idx % AGENT_COLORS.length : 0;
  };

  const getAgentColor = (agentId: string) => AGENT_COLORS[getAgentIndex(agentId)];
  const getAgentBg = (agentId: string) => AGENT_BG_COLORS[getAgentIndex(agentId)];

  const getAgentName = (agentId: string | null) => {
    if (!agentId) return "System";
    return room?.agent_names?.[agentId] || agentId.slice(0, 8);
  };

  // Which agent is currently "thinking" (about to speak)
  const activeAgentId =
    room?.state === "running"
      ? room.agent_ids[room.current_turn % room.agent_ids.length]
      : null;

  // Per-agent stats
  const agentStats = (agentId: string) => {
    const msgs = (room?.messages || []).filter(
      (m) => m.agent_id === agentId && m.role === "agent"
    );
    const last = msgs[msgs.length - 1];
    return { count: msgs.length, last };
  };

  const handleStart = async () => {
    setActionLoading(true);
    try {
      await api.startMeetingRoom(roomId, initialMessage || undefined);
      setInitialMessage("");
      await fetchRoom();
    } catch (e) {
      alert(`Error: ${e}`);
    } finally {
      setActionLoading(false);
    }
  };

  const handleStop = async () => {
    setActionLoading(true);
    try {
      await api.stopMeetingRoom(roomId);
      await fetchRoom();
    } catch (e) {
      alert(`Error: ${e}`);
    } finally {
      setActionLoading(false);
    }
  };

  const handleDelete = async () => {
    if (!confirm("Delete this meeting room?")) return;
    try {
      await api.deleteMeetingRoom(roomId);
      router.push("/meeting-rooms");
    } catch (e) {
      alert(`Error: ${e}`);
    }
  };

  const handleExportPDF = () => {
    if (!room) return;
    const messages = room.messages || [];
    const agentNames = room.agent_names || {};
    const getName = (id: string | null) => id ? (agentNames[id] || id) : "System";
    const date = new Date().toLocaleDateString("de-DE", { day: "2-digit", month: "long", year: "numeric" });

    const roleLabel: Record<string, string> = {
      agent: "", system: "System", moderator: "Moderator", summary: "Action Items", reaction: "Reaktion",
    };

    const msgHtml = messages.map(m => {
      if (m.role === "system" && m.content.startsWith("---")) {
        return `<div class="phase-break">${m.content.replace(/\*\*/g, "")}</div>`;
      }
      if (m.role === "summary") {
        const md = m.content
          .replace(/## (.*)/g, "<h3>$1</h3>")
          .replace(/- \[ \] (.*)/g, "<li>☐ $1</li>")
          .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
          .replace(/\n/g, "<br>");
        return `<div class="msg summary"><div class="msg-header summary-header">📋 Meeting-Ergebnis: Action Items</div><div class="msg-body">${md}</div></div>`;
      }
      if (m.role === "moderator") {
        return `<div class="msg moderator"><div class="msg-header">🎤 Moderator</div><div class="msg-body italic">${m.content}</div></div>`;
      }
      if (m.role === "reaction") {
        return `<div class="msg reaction"><div class="msg-header">${getName(m.agent_id)} <span class="label">reagiert</span></div><div class="msg-body italic">${m.content}</div></div>`;
      }
      const md = m.content
        .replace(/## (.*)/g, "<h3>$1</h3>")
        .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
        .replace(/`([^`]+)`/g, "<code>$1</code>")
        .replace(/\n/g, "<br>");
      const time = new Date(m.timestamp).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
      return `<div class="msg agent"><div class="msg-header">${getName(m.agent_id)} <span class="label">${time}</span></div><div class="msg-body">${md}</div></div>`;
    }).join("");

    const participants = room.agent_ids.map(id => getName(id)).join(", ");

    const html = `<!DOCTYPE html><html lang="de"><head><meta charset="UTF-8">
<title>${room.name}</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 13px; color: #1a1a1a; background: white; padding: 40px; max-width: 800px; margin: 0 auto; }
  h1 { font-size: 22px; font-weight: 700; margin-bottom: 4px; }
  .meta { color: #666; font-size: 12px; margin-bottom: 6px; }
  .topic { font-size: 14px; color: #333; margin-bottom: 4px; }
  .participants { font-size: 12px; color: #888; margin-bottom: 24px; }
  hr { border: none; border-top: 1px solid #e5e5e5; margin: 20px 0; }
  .msg { margin-bottom: 14px; padding: 10px 14px; border-radius: 8px; border: 1px solid #f0f0f0; break-inside: avoid; }
  .msg-header { font-size: 11px; font-weight: 600; margin-bottom: 5px; color: #555; }
  .msg-header .label { font-weight: 400; color: #aaa; margin-left: 6px; }
  .msg-body { line-height: 1.6; }
  .msg-body h3 { font-size: 13px; font-weight: 600; margin: 8px 0 4px; }
  .msg-body code { background: #f4f4f4; padding: 1px 4px; border-radius: 3px; font-size: 11px; font-family: monospace; }
  .msg-body li { margin-left: 16px; margin-bottom: 3px; }
  .msg.agent { background: #fafafa; }
  .msg.moderator { background: #faf0ff; border-color: #e9d5ff; }
  .msg.moderator .msg-header { color: #7c3aed; }
  .msg.moderator .italic { font-style: italic; }
  .msg.summary { background: #f0f9ff; border-color: #bae6fd; }
  .summary-header { color: #0369a1; font-size: 13px; font-weight: 600; margin-bottom: 8px; }
  .msg.reaction { background: #f9f9f9; border-color: #eee; opacity: 0.85; }
  .msg.reaction .italic { font-style: italic; color: #666; }
  .phase-break { text-align: center; color: #999; font-size: 11px; margin: 16px 0; border-top: 1px dashed #ddd; padding-top: 10px; }
  @media print { body { padding: 20px; } .msg { break-inside: avoid; } }
</style></head><body>
<h1>${room.name}</h1>
<div class="meta">${date} · ${room.rounds_completed}/${room.max_rounds} Runden</div>
<div class="topic">📋 ${room.topic || "Kein Thema"}</div>
<div class="participants">Teilnehmer: ${participants}${room.use_moderator ? " + Moderator" : ""}</div>
<hr>
${msgHtml}
<hr>
<div class="meta" style="text-align:center; margin-top:16px">Generiert von AI Employee · ${date}</div>
</body></html>`;

    const win = window.open("", "_blank");
    if (!win) return;
    win.document.write(html);
    win.document.close();
    win.onload = () => { win.print(); };
  };

  if (loading) {
    return (
      <div>
        <Header title="Meeting Room" />
        <div className="flex items-center justify-center py-20">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      </div>
    );
  }

  if (!room) {
    return (
      <div>
        <Header title="Meeting Room" />
        <div className="flex flex-col items-center justify-center py-20">
          <p className="text-muted-foreground">Room not found</p>
          <button
            onClick={() => router.push("/meeting-rooms")}
            className="mt-4 text-sm text-primary hover:underline"
          >
            Back to rooms
          </button>
        </div>
      </div>
    );
  }

  const messages = room.messages || [];
  const progressPct =
    room.max_rounds > 0
      ? Math.min((room.rounds_completed / room.max_rounds) * 100, 100)
      : 0;

  return (
    <div className="flex flex-col h-[calc(100vh-2rem)]">
      <Header title={room.name} subtitle={room.topic || undefined} />

      {/* Toolbar */}
      <div className="px-6 pb-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button
              onClick={() => router.push("/meeting-rooms")}
              className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors"
            >
              <ArrowLeft className="h-4 w-4" />
              Back
            </button>

            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Users className="h-4 w-4" />
              {room.agent_ids.map((aid, idx) => (
                <span
                  key={aid}
                  className={cn("font-medium", AGENT_COLORS[idx % AGENT_COLORS.length])}
                >
                  {getAgentName(aid)}
                  {idx < room.agent_ids.length - 1 ? "," : ""}
                </span>
              ))}
            </div>

            <span className="text-xs text-muted-foreground">
              Round {room.rounds_completed}/{room.max_rounds}
            </span>
          </div>

          <div className="flex items-center gap-2">
            {(room.state === "idle" || room.state === "paused") && (
              <button
                onClick={handleStart}
                disabled={actionLoading}
                className="flex items-center gap-1.5 rounded-lg bg-emerald-500/10 px-3 py-1.5 text-sm font-medium text-emerald-400 hover:bg-emerald-500/20 disabled:opacity-50 transition-colors"
              >
                {actionLoading ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Play className="h-4 w-4" />
                )}
                {room.state === "paused" ? "Resume" : "Start"}
              </button>
            )}
            {room.state === "running" && (
              <button
                onClick={handleStop}
                disabled={actionLoading}
                className="flex items-center gap-1.5 rounded-lg bg-amber-500/10 px-3 py-1.5 text-sm font-medium text-amber-400 hover:bg-amber-500/20 disabled:opacity-50 transition-colors"
              >
                {actionLoading ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Square className="h-4 w-4" />
                )}
                Stop
              </button>
            )}
            <button
              onClick={handleExportPDF}
              title="Als PDF exportieren"
              className="flex items-center gap-1.5 rounded-lg bg-blue-500/10 border border-blue-500/20 px-3 py-1.5 text-sm font-medium text-blue-400 hover:bg-blue-500/20 transition-colors"
            >
              <Download className="h-4 w-4" />
              <span>PDF</span>
            </button>
            <button
              onClick={handleDelete}
              className="flex items-center gap-1.5 rounded-lg bg-red-500/10 px-3 py-1.5 text-sm font-medium text-red-400 hover:bg-red-500/20 transition-colors"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>

      {/* Main layout: chat + sidebar */}
      <div className="flex flex-1 min-h-0 gap-0">

        {/* Chat Messages */}
        <div className="flex flex-col flex-1 min-w-0">
          <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
            {messages.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full text-center">
                <Bot className="h-12 w-12 text-muted-foreground/20 mb-4" />
                <p className="text-muted-foreground text-sm">
                  {room.state === "idle"
                    ? "Start the meeting to begin the discussion."
                    : "Waiting for responses..."}
                </p>
              </div>
            ) : (
              messages.map((msg: MeetingMessage, i: number) => (
                <motion.div
                  key={i}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: Math.min(i * 0.02, 0.5) }}
                  className={cn(
                    "flex gap-3 max-w-3xl",
                    (msg.role === "system" || msg.role === "moderator") && "mx-auto max-w-2xl w-full",
                    msg.role === "reaction" && "max-w-xl ml-11 opacity-80",
                  )}
                >
                  {msg.role === "reaction" ? (
                    <div className="flex items-start gap-2 w-full">
                      <div
                        className={cn(
                          "shrink-0 flex h-6 w-6 items-center justify-center rounded-full",
                          getAgentBg(msg.agent_id || ""),
                        )}
                      >
                        <Bot className={cn("h-3 w-3", getAgentColor(msg.agent_id || ""))} />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-1.5 mb-0.5">
                          <span className={cn("text-[11px] font-medium", getAgentColor(msg.agent_id || ""))}>
                            {getAgentName(msg.agent_id)}
                          </span>
                          <span className="text-[10px] text-muted-foreground/40">reagiert</span>
                        </div>
                        <div className="rounded-lg bg-foreground/[0.03] border border-foreground/[0.05] px-3 py-1.5 text-sm text-foreground/70 italic leading-snug">
                          {msg.content}
                        </div>
                      </div>
                    </div>
                  ) : msg.role === "moderator" ? (
                    <div className="w-full flex items-start gap-2.5 max-w-2xl mx-auto">
                      <div className="shrink-0 flex h-7 w-7 items-center justify-center rounded-full bg-violet-500/10">
                        <Gavel className="h-3.5 w-3.5 text-violet-400" />
                      </div>
                      <div className="flex-1 rounded-xl bg-violet-500/5 border border-violet-500/20 px-4 py-2.5 text-sm text-violet-300/90 italic leading-relaxed">
                        {msg.content}
                      </div>
                    </div>
                  ) : msg.role === "summary" ? (
                    <div className="w-full rounded-xl border border-primary/20 bg-primary/5 px-5 py-4">
                      <div className="flex items-center gap-2 mb-3">
                        <ListTodo className="h-4 w-4 text-primary" />
                        <span className="text-sm font-semibold text-primary">Meeting-Ergebnis: Action Items</span>
                      </div>
                      <div className="text-sm text-foreground/90 leading-relaxed">
                        <ReactMarkdown
                          remarkPlugins={[remarkGfm]}
                          components={{
                            h2: ({ children }) => <h2 className="text-sm font-semibold mb-1.5 mt-3 first:mt-0">{children}</h2>,
                            p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
                            strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
                            ul: ({ children }) => <ul className="list-none pl-0 mb-2 space-y-1">{children}</ul>,
                            li: ({ children }) => <li className="flex items-start gap-2 text-sm"><span className="mt-0.5 shrink-0">☐</span><span>{children}</span></li>,
                          }}
                        >
                          {msg.content}
                        </ReactMarkdown>
                      </div>
                    </div>
                  ) : msg.role === "system" ? (
                    <div className="w-full rounded-xl bg-accent/30 border border-border px-4 py-3 text-center text-sm text-muted-foreground italic">
                      {msg.content}
                    </div>
                  ) : (
                    <>
                      <div
                        className={cn(
                          "shrink-0 flex h-8 w-8 items-center justify-center rounded-full",
                          getAgentBg(msg.agent_id || ""),
                        )}
                      >
                        <Bot
                          className={cn("h-4 w-4", getAgentColor(msg.agent_id || ""))}
                        />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          <span
                            className={cn(
                              "text-sm font-medium",
                              getAgentColor(msg.agent_id || ""),
                            )}
                          >
                            {getAgentName(msg.agent_id)}
                          </span>
                          {msg.round !== undefined && (
                            <span className="text-[10px] text-muted-foreground/50">
                              R{msg.round + 1}
                            </span>
                          )}
                          <span className="text-[10px] text-muted-foreground/50">
                            {new Date(msg.timestamp).toLocaleTimeString()}
                          </span>
                        </div>
                        <div className="rounded-xl bg-card/80 border border-border px-4 py-3 text-sm leading-relaxed">
                          <ReactMarkdown
                            remarkPlugins={[remarkGfm]}
                            components={{
                              h2: ({ children }) => <h2 className="text-sm font-semibold text-foreground mb-1.5 mt-3 first:mt-0">{children}</h2>,
                              h3: ({ children }) => <h3 className="text-xs font-semibold text-foreground/80 mb-1 mt-2 first:mt-0">{children}</h3>,
                              p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
                              strong: ({ children }) => <strong className="font-semibold text-foreground">{children}</strong>,
                              em: ({ children }) => <em className="italic text-foreground/80">{children}</em>,
                              ul: ({ children }) => <ul className="list-disc list-outside pl-4 mb-2 space-y-0.5">{children}</ul>,
                              ol: ({ children }) => <ol className="list-decimal list-outside pl-4 mb-2 space-y-0.5">{children}</ol>,
                              li: ({ children }) => <li className="text-sm">{children}</li>,
                              hr: () => <hr className="my-3 border-t border-foreground/20" />,
                              blockquote: ({ children }) => <blockquote className="border-l-2 border-primary/50 pl-3 my-2 text-foreground/70 italic">{children}</blockquote>,
                              table: ({ children }) => <div className="overflow-x-auto mb-3"><table className="w-full text-xs border-collapse">{children}</table></div>,
                              th: ({ children }) => <th className="border border-foreground/20 px-2 py-1.5 bg-foreground/[0.06] font-semibold text-left">{children}</th>,
                              td: ({ children }) => <td className="border border-foreground/20 px-2 py-1.5">{children}</td>,
                              code: ({ children }) => <code className="rounded bg-foreground/[0.06] px-1 py-0.5 font-mono text-[11px]">{children}</code>,
                            }}
                          >
                            {msg.content}
                          </ReactMarkdown>
                        </div>
                      </div>
                    </>
                  )}
                </motion.div>
              ))
            )}
            <div ref={messagesEndRef} />

            {room.state === "running" && (
              <div className="flex items-center gap-2 text-sm text-muted-foreground animate-pulse">
                <Loader2 className="h-4 w-4 animate-spin" />
                Meeting in progress...
              </div>
            )}
          </div>

          {/* Start prompt (only show when idle) */}
          {room.state === "idle" && messages.length === 0 && (
            <div className="border-t border-border px-6 py-4">
              <div className="flex items-center gap-3 max-w-3xl">
                <input
                  type="text"
                  value={initialMessage}
                  onChange={(e) => setInitialMessage(e.target.value)}
                  placeholder="Optional: Set the initial topic or question..."
                  className="flex-1 rounded-xl border border-border bg-background px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50"
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      handleStart();
                    }
                  }}
                />
                <button
                  onClick={handleStart}
                  disabled={actionLoading}
                  className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
                >
                  {actionLoading ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Send className="h-4 w-4" />
                  )}
                  Start Meeting
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Right Sidebar */}
        <div className="w-72 shrink-0 border-l border-border flex flex-col overflow-hidden">
          {/* Agent Status */}
          <div className="flex-1 overflow-y-auto p-4 space-y-2">
            <p className="text-[11px] font-medium text-muted-foreground/70 uppercase tracking-wider mb-3">
              Teilnehmer
            </p>

            {/* Moderator card */}
            {room.use_moderator && (
              <motion.div
                initial={{ opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                className="rounded-xl border border-violet-500/20 bg-violet-500/5 p-3 mb-1"
              >
                <div className="flex items-center gap-2.5 mb-2">
                  <div className="relative shrink-0">
                    <div className={cn(
                      "h-2.5 w-2.5 rounded-full bg-violet-400",
                      room.state === "running" ? "opacity-100" : "opacity-40",
                    )} />
                    {room.state === "running" && (
                      <div className="absolute inset-0 h-2.5 w-2.5 rounded-full animate-ping bg-violet-400" />
                    )}
                  </div>
                  <Gavel className="h-3.5 w-3.5 text-violet-400 shrink-0" />
                  <span className="text-sm font-medium text-violet-300 flex-1 truncate">Moderator</span>
                  <div className="flex items-center gap-1 text-[10px] text-muted-foreground/50 shrink-0">
                    <MessageSquare className="h-3 w-3" />
                    {messages.filter(m => m.role === "moderator").length}
                  </div>
                </div>
                <AnimatePresence mode="wait">
                  {(() => {
                    const lastMod = [...messages].reverse().find(m => m.role === "moderator");
                    return lastMod ? (
                      <motion.p
                        key={lastMod.timestamp}
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        className="text-[11px] text-violet-300/60 leading-relaxed line-clamp-2 italic"
                      >
                        {lastMod.content}
                      </motion.p>
                    ) : (
                      <motion.p
                        key="mod-waiting"
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        className="text-[11px] text-muted-foreground/40 italic"
                      >
                        {room.state === "running" ? "Wartet auf ersten Beitrag…" : "Bereit"}
                      </motion.p>
                    );
                  })()}
                </AnimatePresence>
              </motion.div>
            )}

            {room.agent_ids.map((aid, idx) => {
              const colorIdx = idx % AGENT_COLORS.length;
              const isActive = aid === activeAgentId;
              const stats = agentStats(aid);
              const lastMsg = stats.last;

              return (
                <motion.div
                  key={aid}
                  animate={isActive ? { scale: [1, 1.01, 1] } : { scale: 1 }}
                  transition={isActive ? { repeat: Infinity, duration: 2 } : {}}
                  className={cn(
                    "rounded-xl border p-3 transition-colors duration-300",
                    isActive
                      ? cn(
                          AGENT_BG_COLORS[colorIdx],
                          AGENT_BORDER_COLORS[colorIdx],
                          "ring-1",
                          AGENT_RING_COLORS[colorIdx],
                        )
                      : "border-foreground/[0.06] bg-card/40",
                  )}
                >
                  <div className="flex items-center gap-2.5 mb-2">
                    {/* Animated dot */}
                    <div className="relative shrink-0">
                      <div
                        className={cn(
                          "h-2.5 w-2.5 rounded-full",
                          AGENT_DOT_COLORS[colorIdx],
                          isActive ? "opacity-100" : "opacity-40",
                        )}
                      />
                      {isActive && (
                        <div
                          className={cn(
                            "absolute inset-0 h-2.5 w-2.5 rounded-full animate-ping",
                            AGENT_DOT_COLORS[colorIdx],
                          )}
                        />
                      )}
                    </div>

                    <span
                      className={cn(
                        "text-sm font-medium flex-1 truncate",
                        isActive ? AGENT_COLORS[colorIdx] : "text-foreground/80",
                      )}
                    >
                      {getAgentName(aid)}
                    </span>

                    <div className="flex items-center gap-1 text-[10px] text-muted-foreground/50 shrink-0">
                      <MessageSquare className="h-3 w-3" />
                      {stats.count}
                    </div>
                  </div>

                  {/* Status line */}
                  <AnimatePresence mode="wait">
                    {isActive ? (
                      <motion.div
                        key="thinking"
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        className={cn(
                          "flex items-center gap-1.5 text-[11px]",
                          AGENT_COLORS[colorIdx],
                        )}
                      >
                        <Loader2 className="h-3 w-3 animate-spin" />
                        Denkt nach…
                      </motion.div>
                    ) : lastMsg ? (
                      <motion.p
                        key="last-msg"
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        className="text-[11px] text-muted-foreground/60 leading-relaxed line-clamp-2"
                      >
                        {lastMsg.content}
                      </motion.p>
                    ) : (
                      <motion.p
                        key="waiting"
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        className="text-[11px] text-muted-foreground/40 italic"
                      >
                        Noch nicht dran
                      </motion.p>
                    )}
                  </AnimatePresence>
                </motion.div>
              );
            })}
          </div>

          {/* Round Progress */}
          <div className="border-t border-border p-4 space-y-3">
            <div className="flex items-center justify-between">
              <p className="text-[11px] font-medium text-muted-foreground/70 uppercase tracking-wider">
                Fortschritt
              </p>
              <span className="text-[11px] text-muted-foreground/60">
                {room.rounds_completed} / {room.max_rounds} Runden
              </span>
            </div>

            <div className="h-1.5 w-full rounded-full bg-foreground/[0.06] overflow-hidden">
              <motion.div
                className="h-full rounded-full bg-primary/60"
                initial={{ width: 0 }}
                animate={{ width: `${progressPct}%` }}
                transition={{ duration: 0.5, ease: "easeOut" }}
              />
            </div>

            {/* State badge */}
            <div className="flex items-center gap-2">
              {room.state === "running" && (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 px-2.5 py-1 text-[11px] font-medium text-emerald-400">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
                  Läuft
                </span>
              )}
              {room.state === "paused" && (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-500/10 border border-amber-500/20 px-2.5 py-1 text-[11px] font-medium text-amber-400">
                  <Square className="h-2.5 w-2.5" />
                  Pausiert
                </span>
              )}
              {room.state === "idle" && (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-foreground/[0.04] border border-foreground/[0.08] px-2.5 py-1 text-[11px] font-medium text-muted-foreground">
                  Bereit
                </span>
              )}
              {room.state === "completed" && (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-500/10 border border-blue-500/20 px-2.5 py-1 text-[11px] font-medium text-blue-400">
                  <CheckCircle2 className="h-3 w-3" />
                  Abgeschlossen
                </span>
              )}
              <span className="text-[11px] text-muted-foreground/50 ml-auto">
                {messages.filter((m) => m.role === "agent").length} Beiträge
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
