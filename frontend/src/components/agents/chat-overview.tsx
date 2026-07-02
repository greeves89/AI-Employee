"use client";

import { useCallback, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { MessageSquare, Pin, X, ArrowRight, Loader2, Clock } from "lucide-react";
import * as api from "@/lib/api";
import type { ChatSession } from "@/lib/api";
import { cn } from "@/lib/utils";

// Load the full chat lazily to avoid a static circular import (chat.tsx imports
// ChatOverview). Rendered inside the modal so a chat session is fully usable
// (send messages, streaming) — not just a read-only viewer.
const EmbeddedChat = dynamic(() => import("./chat").then((m) => m.AgentChat), {
  ssr: false,
  loading: () => (
    <div className="flex flex-1 items-center justify-center text-muted-foreground">
      <Loader2 className="h-5 w-5 animate-spin" />
    </div>
  ),
});

function relTime(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso).getTime();
  const s = Math.max(0, Math.floor((Date.now() - d) / 1000));
  if (s < 60) return "gerade eben";
  if (s < 3600) return `vor ${Math.floor(s / 60)} min`;
  if (s < 86400) return `vor ${Math.floor(s / 3600)} h`;
  return `vor ${Math.floor(s / 86400)} d`;
}

/**
 * Chat overview: one tile per chat session (summary), click opens a live modal
 * of the conversation. Reuses the existing session + history APIs — no new
 * backend. onOpenSession jumps into the full chat for that session.
 */
export function ChatOverview({
  agentId,
  onOpenSession,
}: {
  agentId: string;
  onOpenSession?: (sessionId: string) => void;
}) {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [openId, setOpenId] = useState<string | null>(null);
  const [openTitle, setOpenTitle] = useState("");

  const load = useCallback(async () => {
    try {
      const { sessions } = await api.getChatSessions(agentId);
      setSessions(sessions);
    } catch {
      setSessions([]);
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    load();
    const t = setInterval(load, 10000); // keep tiles fresh
    return () => clearInterval(t);
  }, [load]);

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    );
  }

  if (sessions.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground">
        <MessageSquare className="h-8 w-8 opacity-40" />
        <p className="text-sm">Noch keine Chats.</p>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {sessions.map((s) => {
          const title = s.title || s.preview || "Chat";
          const recent = !!s.last_message_at && (Date.now() - new Date(s.last_message_at).getTime()) < 5 * 60 * 1000;
          return (
            <button
              key={s.id}
              onClick={() => { setOpenId(s.id); setOpenTitle(title); }}
              className={cn(
                "group relative flex min-w-0 flex-col gap-2.5 overflow-hidden rounded-2xl border p-4 text-left transition-all duration-200",
                "bg-gradient-to-b from-card/80 to-card/40 hover:-translate-y-0.5 hover:shadow-lg hover:shadow-primary/5",
                s.pinned
                  ? "border-amber-500/25 hover:border-amber-500/50"
                  : "border-foreground/[0.08] hover:border-primary/40"
              )}
            >
              {s.pinned && (
                <span className="absolute right-2.5 top-2.5 z-10 text-amber-400" title="Angepinnt">
                  <Pin className="h-3.5 w-3.5 fill-amber-400/30" />
                </span>
              )}
              <div className="flex min-w-0 items-center gap-2">
                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                  <MessageSquare className="h-3.5 w-3.5" />
                </span>
                <span className="min-w-0 flex-1 truncate pr-5 text-sm font-semibold">{title}</span>
              </div>
              <p className="line-clamp-2 min-h-[2rem] break-words text-xs leading-relaxed text-muted-foreground/80">
                {s.title ? (s.preview || "—") : "Chat öffnen für den Verlauf"}
              </p>
              <div className="mt-auto flex items-center gap-2 pt-1 text-[10px] text-muted-foreground/60">
                {recent && (
                  <span className="flex items-center gap-1 text-emerald-500" title="gerade aktiv">
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" /> aktiv
                  </span>
                )}
                <span className="flex items-center gap-1">
                  <MessageSquare className="h-3 w-3 opacity-60" /> {s.message_count}
                </span>
                <span className="ml-auto flex items-center gap-1">
                  <Clock className="h-3 w-3 opacity-60" /> {relTime(s.last_message_at)}
                </span>
              </div>
            </button>
          );
        })}
      </div>

      {openId && (
        <ChatViewModal
          agentId={agentId}
          sessionId={openId}
          title={openTitle}
          onClose={() => setOpenId(null)}
          onOpenSession={onOpenSession}
        />
      )}
    </div>
  );
}

function ChatViewModal({
  agentId,
  sessionId,
  title,
  onClose,
  onOpenSession,
}: {
  agentId: string;
  sessionId: string;
  title: string;
  onClose: () => void;
  onOpenSession?: (sessionId: string) => void;
}) {
  // Close on Escape (but not while typing — the embedded chat owns the input).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !(e.target as HTMLElement)?.closest("textarea,input")) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm" onClick={onClose}>
      <div
        className="flex h-[85vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-foreground/[0.1] bg-card shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border px-4 py-2.5 shrink-0">
          <div className="flex min-w-0 items-center gap-2">
            <MessageSquare className="h-4 w-4 shrink-0 text-primary/70" />
            <span className="truncate text-sm font-medium">{title}</span>
          </div>
          <div className="flex items-center gap-1">
            {onOpenSession && (
              <button
                onClick={() => { onOpenSession(sessionId); onClose(); }}
                className="inline-flex items-center gap-1 rounded-lg bg-foreground/[0.06] px-2.5 py-1.5 text-[11px] font-medium text-muted-foreground hover:text-foreground hover:bg-foreground/[0.1]"
                title="Als Haupt-Chat öffnen"
              >
                Vollbild <ArrowRight className="h-3 w-3" />
              </button>
            )}
            <button onClick={onClose} className="rounded-lg p-1.5 text-muted-foreground/60 hover:bg-foreground/[0.06] hover:text-foreground" title="Schließen">
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Full interactive chat for this session (embedded, no tab bar) */}
        <div className="flex min-h-0 flex-1 flex-col">
          <EmbeddedChat agentId={agentId} initialSessionId={sessionId} embedded />
        </div>
      </div>
    </div>
  );
}
