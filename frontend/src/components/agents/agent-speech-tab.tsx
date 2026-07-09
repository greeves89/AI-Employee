"use client";

import { useCallback, useEffect, useState } from "react";
import { Plus, MessageSquare, Loader2 } from "lucide-react";
import * as api from "@/lib/api";
import type { ChatSession } from "@/lib/api";
import { cn, timeAgo } from "@/lib/utils";
import { VoiceSessionModal } from "./voice-session";

/** Speech tab: a "Letzte Gespräche" list (shared with the text chat's sessions) plus
 *  the embedded live voice view. Picking a conversation resumes it — its history is
 *  shown and the user speaks straight into the same session. "Neues Gespräch" starts
 *  fresh. Mirrors the chat's session model so voice and text stay one continuous thread. */
export function AgentSpeechTab({ agentId, agentName }: { agentId: string; agentName: string }) {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const loadSessions = useCallback(async () => {
    try {
      const { sessions: s } = await api.getChatSessions(agentId);
      setSessions(s);
    } catch {
      setSessions([]);
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => { loadSessions(); }, [loadSessions]);

  return (
    <div className="flex h-full min-h-0 gap-3">
      {/* Left rail — recent conversations */}
      <div className="flex w-56 shrink-0 flex-col rounded-2xl border border-border bg-card/60">
        <div className="flex items-center justify-between border-b border-border px-3 py-2.5">
          <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/60">
            Gespräche
          </span>
          <button
            onClick={() => setSelected(null)}
            className="inline-flex items-center justify-center rounded-lg bg-primary p-1.5 text-primary-foreground shadow-sm hover:bg-primary/90 transition-all"
            title="Neues Gespräch"
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
        </div>
        <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto p-2">
          {loading ? (
            <div className="flex items-center justify-center py-6 text-muted-foreground/50">
              <Loader2 className="h-4 w-4 animate-spin" />
            </div>
          ) : (
            <>
              <button
                onClick={() => setSelected(null)}
                className={cn(
                  "flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs transition-colors",
                  selected === null
                    ? "bg-primary/10 text-foreground"
                    : "text-muted-foreground/70 hover:bg-foreground/[0.04]",
                )}
              >
                <Plus className="h-3.5 w-3.5 shrink-0" />
                <span className="truncate">Neues Gespräch</span>
              </button>
              {sessions.map((s) => {
                const shown = s.title || s.preview || "Gespräch";
                return (
                  <button
                    key={s.id}
                    onClick={() => setSelected(s.id)}
                    className={cn(
                      "flex w-full flex-col gap-0.5 rounded-lg px-2.5 py-2 text-left transition-colors",
                      selected === s.id
                        ? "bg-primary/10 text-foreground"
                        : "text-muted-foreground/70 hover:bg-foreground/[0.04]",
                    )}
                  >
                    <span className="flex items-center gap-1.5 text-xs">
                      <MessageSquare className="h-3 w-3 shrink-0 text-muted-foreground/50" />
                      <span className="truncate">{shown}</span>
                    </span>
                    {s.last_message_at && (
                      <span className="pl-4 text-[10px] text-muted-foreground/40">
                        {timeAgo(s.last_message_at)} · {s.message_count}
                      </span>
                    )}
                  </button>
                );
              })}
              {sessions.length === 0 && (
                <p className="px-2.5 py-4 text-center text-[11px] text-muted-foreground/40">
                  Noch keine Gespräche
                </p>
              )}
            </>
          )}
        </div>
      </div>

      {/* Right — live voice view (remounts per selected session) */}
      <div className="min-w-0 flex-1">
        <VoiceSessionModal
          key={`voice-${agentId}-${selected ?? "new"}`}
          agentId={agentId}
          agentName={agentName}
          resumeSessionId={selected ?? undefined}
          onClose={() => setSelected(null)}
          embedded
        />
      </div>
    </div>
  );
}
