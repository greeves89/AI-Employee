"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Maximize2, Mic, PanelLeft, PanelLeftClose, PhoneOff, Radio } from "lucide-react";
import * as api from "@/lib/api";
import type { ChatHistoryMessage, ChatSession } from "@/lib/api";
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
  // Gesprächsverlauf des ausgewählten Gesprächs — in der Speech-Ansicht fehlte
  // er ganz; der Nutzer sah nur einen Startknopf, aber nicht, worüber schon
  // gesprochen wurde.
  const [history, setHistory] = useState<ChatHistoryMessage[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
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

  // Titel folgt dem Modell: rename_conversation schreibt den neuen Titel in die
  // DB, aber die Liste hier war danach veraltet. Ein leiser Takt holt sie nach —
  // solange eine Sprachsitzung läuft, damit der vom Modell gesetzte Name auch in
  // der Speech-Ansicht auftaucht.
  useEffect(() => {
    if (!voiceSession.activeSession) return;
    const t = setInterval(loadSessions, 8000);
    return () => clearInterval(t);
  }, [voiceSession.activeSession, loadSessions]);

  // Verlauf des ausgewählten Gesprächs laden (dieselbe Quelle wie der Text-Chat).
  useEffect(() => {
    let cancelled = false;
    if (!selected) { setHistory([]); return; }
    setHistoryLoading(true);
    api.getChatHistory(agentId, 200, selected)
      .then(({ messages }) => { if (!cancelled) setHistory(messages); })
      .catch(() => { if (!cancelled) setHistory([]); })
      .finally(() => { if (!cancelled) setHistoryLoading(false); });
    return () => { cancelled = true; };
  }, [agentId, selected, voiceSession.snapshot?.state]);

  // Immer ans Ende scrollen — die jüngste Nachricht liegt direkt über dem
  // Button-Dock und fadet dort aus.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [history]);

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
          <div className="relative flex h-full min-h-[360px] flex-col overflow-hidden rounded-2xl border border-border bg-card">
            {/* Titel des Gesprächs — folgt dem, was das Modell per
                rename_conversation gesetzt hat. */}
            {selected && (
              <div className="shrink-0 border-b border-border/70 px-4 py-2.5">
                <div className="truncate text-sm font-semibold">
                  {sessions.find((s) => s.id === selected)?.title || "Gespräch"}
                </div>
              </div>
            )}
            {(() => {
              const shown = history.filter(
                (m) => (m.role === "user" || m.role === "assistant") && m.content?.trim(),
              );
              // ── Verlauf oben, fadet nach unten aus (Maske) — genau die Idee:
              //    man sieht das Gespräch, es läuft nach unten ins Button-Dock aus.
              if (selected && shown.length > 0) {
                return (
                  <div
                    ref={scrollRef}
                    className="min-h-0 flex-1 space-y-2 overflow-y-auto px-4 pb-28 pt-4"
                    style={{
                      maskImage: "linear-gradient(to bottom, black 0%, black 52%, transparent 90%)",
                      WebkitMaskImage: "linear-gradient(to bottom, black 0%, black 52%, transparent 90%)",
                    }}
                  >
                    {shown.map((m) => (
                      <SpeechBubble key={m.id} message={m} />
                    ))}
                  </div>
                );
              }
              // ── Kein/leeres Gespräch: der zentrierte Start-Zustand.
              return (
                <div className="flex flex-1 flex-col items-center justify-center px-6 pb-24 text-center">
                  <div className="grid h-14 w-14 place-items-center rounded-full bg-fuchsia-500/15 text-fuchsia-300">
                    <Mic className="h-6 w-6" />
                  </div>
                  <h3 className="mt-4 text-base font-semibold">Live-Gespräch</h3>
                  <p className="mt-1 max-w-md text-sm text-muted-foreground">
                    {selected
                      ? historyLoading
                        ? "Lade Gesprächsverlauf…"
                        : "Dieses Gespräch hat noch keinen Verlauf — sprich einfach los."
                      : "Startet ein neues Sprachgespräch mit diesem Agenten."}
                  </p>
                </div>
              );
            })()}

            {/* Button-Dock — sitzt unten, fadet über dem auslaufenden Verlauf ein. */}
            <div className="pointer-events-none absolute inset-x-0 bottom-0 flex flex-col items-center gap-3 bg-gradient-to-t from-card from-40% via-card/90 to-transparent px-6 pb-6 pt-16">
              {voiceSession.activeSession && (
                <div className="pointer-events-auto inline-flex items-center gap-1.5 rounded-full bg-fuchsia-500/10 px-3 py-1 text-xs text-fuchsia-300">
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
              <div className="pointer-events-auto flex flex-wrap justify-center gap-2">
                <button
                  onClick={() => voiceSession.startSession({
                    agentId,
                    agentName,
                    resumeSessionId: selected ?? undefined,
                  })}
                  className="inline-flex items-center gap-2 rounded-xl bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 transition-all hover:bg-primary/90"
                >
                  <Mic className="h-4 w-4" />
                  {voiceSession.isActiveForAgent(agentId)
                    ? "Gespräch öffnen"
                    : selected
                      ? "Gespräch weiterführen"
                      : "Gespräch beginnen"}
                </button>
                {voiceSession.activeSession && (
                  <>
                    <button
                      onClick={voiceSession.expandSession}
                      className="inline-flex items-center gap-2 rounded-xl bg-foreground/[0.06] px-4 py-2.5 text-sm font-medium hover:bg-foreground/[0.10]"
                    >
                      <Maximize2 className="h-4 w-4" />
                      Aufklappen
                    </button>
                    <button
                      onClick={voiceSession.endSession}
                      className="inline-flex items-center gap-2 rounded-xl bg-red-500/10 px-4 py-2.5 text-sm font-medium text-red-400 hover:bg-red-500/20"
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
    </div>
  );
}

/** Eine leichte, lesbare Gesprächsblase für die Speech-Vorschau — bewusst NICHT
 *  die interaktive Timeline des Text-Chats: hier geht es nur ums Nachlesen, was
 *  gesprochen wurde. Nutzer rechts (Akzent), Agent links (dezent). */
function SpeechBubble({ message }: { message: ChatHistoryMessage }) {
  const isUser = message.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[82%] whitespace-pre-wrap break-words rounded-2xl px-3.5 py-2 text-sm ${
          isUser
            ? "bg-primary/15 text-primary"
            : "bg-foreground/[0.05] text-foreground"
        }`}
      >
        {message.content}
      </div>
    </div>
  );
}
