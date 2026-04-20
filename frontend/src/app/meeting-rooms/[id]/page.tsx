"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useParams, useRouter } from "next/navigation";
import { motion } from "framer-motion";
import {
  ArrowLeft,
  Play,
  Square,
  Trash2,
  Loader2,
  Users,
  Bot,
  Send,
} from "lucide-react";
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

  const getAgentColor = (agentId: string) => {
    if (!room) return AGENT_COLORS[0];
    const idx = room.agent_ids.indexOf(agentId);
    return AGENT_COLORS[idx >= 0 ? idx % AGENT_COLORS.length : 0];
  };

  const getAgentBg = (agentId: string) => {
    if (!room) return AGENT_BG_COLORS[0];
    const idx = room.agent_ids.indexOf(agentId);
    return AGENT_BG_COLORS[idx >= 0 ? idx % AGENT_BG_COLORS.length : 0];
  };

  const getAgentName = (agentId: string | null) => {
    if (!agentId) return "System";
    return room?.agent_names?.[agentId] || agentId.slice(0, 8);
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

  return (
    <div className="flex flex-col h-[calc(100vh-2rem)]">
      <Header title={room.name} subtitle={room.topic || undefined} />

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
                  className={cn(
                    "font-medium",
                    AGENT_COLORS[idx % AGENT_COLORS.length],
                  )}
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
              onClick={handleDelete}
              className="flex items-center gap-1.5 rounded-lg bg-red-500/10 px-3 py-1.5 text-sm font-medium text-red-400 hover:bg-red-500/20 transition-colors"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>

      {/* Chat Messages */}
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
                msg.role === "system" && "mx-auto max-w-lg",
              )}
            >
              {msg.role === "system" ? (
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
                      className={cn(
                        "h-4 w-4",
                        getAgentColor(msg.agent_id || ""),
                      )}
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
                    <div className="rounded-xl bg-card/80 border border-border px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap">
                      {msg.content}
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
  );
}
