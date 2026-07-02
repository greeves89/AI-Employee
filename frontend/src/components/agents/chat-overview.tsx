"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { MessageSquare, Pin, X, ArrowRight, Loader2, RefreshCw, Bot, User } from "lucide-react";
import * as api from "@/lib/api";
import type { ChatSession, ChatHistoryMessage } from "@/lib/api";
import { cn } from "@/lib/utils";

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
          return (
            <button
              key={s.id}
              onClick={() => { setOpenId(s.id); setOpenTitle(title); }}
              className="group flex flex-col gap-2 rounded-xl border border-foreground/[0.08] bg-card/60 p-3.5 text-left transition-all hover:border-primary/40 hover:bg-card hover:shadow-md"
            >
              <div className="flex items-center gap-1.5">
                {s.pinned && <Pin className="h-3 w-3 shrink-0 fill-amber-400/30 text-amber-400" />}
                <MessageSquare className="h-3.5 w-3.5 shrink-0 text-primary/70" />
                <span className="truncate text-sm font-medium">{title}</span>
              </div>
              {s.preview && s.title && (
                <p className="line-clamp-2 text-xs text-muted-foreground/80">{s.preview}</p>
              )}
              <div className="mt-auto flex items-center justify-between pt-1 text-[10px] text-muted-foreground/60">
                <span>{s.message_count} Nachrichten</span>
                <span>{relTime(s.last_message_at)}</span>
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
  const [messages, setMessages] = useState<ChatHistoryMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);
  const wasAtBottom = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const { messages } = await api.getChatHistory(agentId, 500, sessionId);
      setMessages(messages);
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, [agentId, sessionId]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 4000); // "live" view
    return () => clearInterval(t);
  }, [refresh]);

  // Keep the view pinned to the bottom while new messages stream in.
  useEffect(() => {
    const el = scrollRef.current;
    if (el && wasAtBottom.current) el.scrollTop = el.scrollHeight;
  }, [messages]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm" onClick={onClose}>
      <div
        className="flex h-[80vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-foreground/[0.1] bg-card shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div className="flex min-w-0 items-center gap-2">
            <MessageSquare className="h-4 w-4 shrink-0 text-primary/70" />
            <span className="truncate text-sm font-medium">{title}</span>
            <span className="flex items-center gap-1 text-[10px] text-emerald-500">
              <span className="relative flex h-1.5 w-1.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-500" />
              </span>
              live
            </span>
          </div>
          <div className="flex items-center gap-1">
            <button onClick={refresh} className="rounded-lg p-1.5 text-muted-foreground/60 hover:bg-foreground/[0.06] hover:text-foreground" title="Aktualisieren">
              <RefreshCw className="h-3.5 w-3.5" />
            </button>
            {onOpenSession && (
              <button
                onClick={() => { onOpenSession(sessionId); onClose(); }}
                className="inline-flex items-center gap-1 rounded-lg bg-primary px-2.5 py-1.5 text-[11px] font-semibold text-primary-foreground hover:bg-primary/90"
                title="Im Chat öffnen"
              >
                Im Chat öffnen <ArrowRight className="h-3 w-3" />
              </button>
            )}
            <button onClick={onClose} className="rounded-lg p-1.5 text-muted-foreground/60 hover:bg-foreground/[0.06] hover:text-foreground" title="Schließen">
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
          {loading ? (
            <div className="flex h-full items-center justify-center text-muted-foreground"><Loader2 className="h-5 w-5 animate-spin" /></div>
          ) : messages.length === 0 ? (
            <div className="flex h-full items-center justify-center text-sm text-muted-foreground">Keine Nachrichten.</div>
          ) : (
            messages.map((m) => (
              <div key={m.id} className={cn("flex gap-2", m.role === "user" ? "flex-row-reverse" : "flex-row")}>
                <div className={cn(
                  "flex h-6 w-6 shrink-0 items-center justify-center rounded-full",
                  m.role === "user" ? "bg-primary/15 text-primary" : m.role === "error" ? "bg-red-500/15 text-red-400" : "bg-foreground/[0.06] text-muted-foreground"
                )}>
                  {m.role === "user" ? <User className="h-3.5 w-3.5" /> : <Bot className="h-3.5 w-3.5" />}
                </div>
                <div className={cn(
                  "max-w-[78%] whitespace-pre-wrap break-words rounded-2xl px-3 py-2 text-sm",
                  m.role === "user" ? "bg-primary text-primary-foreground" : m.role === "error" ? "bg-red-500/10 text-red-300" : "bg-foreground/[0.05] text-foreground"
                )}>
                  {m.content || (m.toolCalls?.length ? `(${m.toolCalls.length} Tool-Aufruf${m.toolCalls.length > 1 ? "e" : ""})` : "…")}
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
