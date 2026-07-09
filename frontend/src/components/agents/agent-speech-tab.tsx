"use client";

import { useCallback, useEffect, useState } from "react";
import * as api from "@/lib/api";
import type { ChatSession } from "@/lib/api";
import { SessionRail } from "./session-rail";
import { VoiceSessionModal } from "./voice-session";

/** Speech tab: a "Gespräche" rail (shared component with the text chat, incl.
 *  pin/rename/delete) plus the embedded live voice view. Picking a conversation
 *  resumes it — its history is shown and the user speaks straight into the same
 *  session. "Neues Gespräch" starts fresh. Mirrors the chat's session model so
 *  voice and text stay one continuous thread. */
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

  const togglePin = useCallback(async (session: { id: string; pinned?: boolean }) => {
    const pinned = !session.pinned;
    setSessions((prev) => {
      const next = prev.map((s) => (s.id === session.id ? { ...s, pinned } : s));
      // Pinned first; the stable sort keeps the recency order within each group.
      return [...next].sort((a, b) => (a.pinned === b.pinned ? 0 : a.pinned ? -1 : 1));
    });
    try {
      await api.updateChatSession(agentId, session.id, { pinned });
    } catch {
      // optimistic value stays; just not persisted
    }
  }, [agentId]);

  const renameSession = useCallback(async (sessionId: string, title: string) => {
    setSessions((prev) => prev.map((s) => (s.id === sessionId ? { ...s, title: title || null } : s)));
    try {
      await api.updateChatSession(agentId, sessionId, { title });
    } catch {
      // optimistic value stays; just not persisted
    }
  }, [agentId]);

  const deleteSession = useCallback(async (sessionId: string) => {
    try {
      await api.deleteChatSession(agentId, sessionId);
      setSessions((prev) => prev.filter((s) => s.id !== sessionId));
      setSelected((cur) => (cur === sessionId ? null : cur));
    } catch {
      // ignore delete errors
    }
  }, [agentId]);

  return (
    <div className="flex h-full min-h-0 gap-3">
      <SessionRail
        className="rounded-2xl border border-border bg-card/60"
        sessions={sessions}
        selectedId={selected}
        loading={loading}
        onSelect={setSelected}
        onNew={() => setSelected(null)}
        onPin={togglePin}
        onRename={renameSession}
        onDelete={deleteSession}
      />

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
