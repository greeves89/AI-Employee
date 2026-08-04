"use client";

import { useCallback, useEffect, useState } from "react";
import { Maximize2, Mic, PanelLeft, PanelLeftClose, PhoneOff, Radio } from "lucide-react";
import * as api from "@/lib/api";
import type { ChatSession } from "@/lib/api";
import { SessionRail } from "./session-rail";
import { useVoiceSession } from "./voice-session-provider";

/** Speech tab: a "Gespräche" rail (shared component with the text chat, incl.
 *  pin/rename/delete) plus the embedded live voice view. Picking a conversation
 *  resumes it — its history is shown and the user speaks straight into the same
 *  session. "Neues Gespräch" starts fresh. Mirrors the chat's session model so
 *  voice and text stay one continuous thread. */
export function AgentSpeechTab({ agentId, agentName }: { agentId: string; agentName: string }) {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [railOpen, setRailOpen] = useState(true);  // collapsible like the chat rail
  const voiceSession = useVoiceSession();

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
      {railOpen && (
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
      )}

      {/* Right — app-level live voice session control */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Toolbar — mirrors the chat's collapse control (icon-only). */}
        <div className="flex items-center gap-1 border-b border-border px-3 py-1.5 shrink-0">
          <button
            onClick={() => setRailOpen((o) => !o)}
            className="rounded-lg p-1.5 text-muted-foreground/60 hover:text-foreground hover:bg-foreground/[0.06] transition-all shrink-0"
            title={railOpen ? "Gesprächsliste ausblenden" : "Gesprächsliste einblenden"}
          >
            {railOpen ? <PanelLeftClose className="h-3.5 w-3.5" /> : <PanelLeft className="h-3.5 w-3.5" />}
          </button>
        </div>
        <div className="min-h-0 flex-1 pt-3">
          <div className="flex h-full min-h-[360px] flex-col items-center justify-center rounded-2xl border border-border bg-card px-6 text-center">
            <div className="grid h-14 w-14 place-items-center rounded-full bg-fuchsia-500/15 text-fuchsia-300">
              <Mic className="h-6 w-6" />
            </div>
            <h3 className="mt-4 text-base font-semibold">Live-Gespräch</h3>
            <p className="mt-1 max-w-md text-sm text-muted-foreground">
              {voiceSession.activeSession
                ? `Aktive Session mit ${voiceSession.activeSession.agentName}`
                : selected
                  ? "Setzt das ausgewählte Gespräch per Sprache fort."
                  : "Startet ein neues Sprachgespräch mit diesem Agenten."}
            </p>
            {voiceSession.activeSession && (
              <div className="mt-4 inline-flex items-center gap-1.5 rounded-full bg-fuchsia-500/10 px-3 py-1 text-xs text-fuchsia-300">
                {voiceSession.snapshot?.mode === "nova_sonic" && <Radio className="h-3 w-3" />}
                {voiceSession.snapshot?.state === "speaking"
                  ? "Spricht"
                  : voiceSession.snapshot?.state === "listening"
                    ? "Hört zu"
                    : voiceSession.snapshot?.state === "processing"
                      ? "Arbeitet"
                      : "Verbunden"}
              </div>
            )}
            <div className="mt-5 flex flex-wrap justify-center gap-2">
              <button
                onClick={() => voiceSession.startSession({
                  agentId,
                  agentName,
                  resumeSessionId: selected ?? undefined,
                })}
                className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
              >
                <Mic className="h-4 w-4" />
                {voiceSession.isActiveForAgent(agentId) ? "Gespräch öffnen" : "Gespräch starten"}
              </button>
              {voiceSession.activeSession && (
                <>
                  <button
                    onClick={voiceSession.expandSession}
                    className="inline-flex items-center gap-2 rounded-lg bg-foreground/[0.06] px-4 py-2 text-sm font-medium hover:bg-foreground/[0.10]"
                  >
                    <Maximize2 className="h-4 w-4" />
                    Aufklappen
                  </button>
                  <button
                    onClick={voiceSession.endSession}
                    className="inline-flex items-center gap-2 rounded-lg bg-red-500/10 px-4 py-2 text-sm font-medium text-red-400 hover:bg-red-500/20"
                  >
                    <PhoneOff className="h-4 w-4" />
                    Beenden
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
