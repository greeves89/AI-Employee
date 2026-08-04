"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { Loader2, Maximize2, Mic, PhoneOff, Radio } from "lucide-react";
import { VoiceSessionModal, type VoiceSessionSnapshot } from "./voice-session";

type VoiceSessionRequest = {
  agentId: string;
  agentName: string;
  getTicket?: () => Promise<string>;
  resumeSessionId?: string;
};

type ActiveVoiceSession = VoiceSessionRequest & {
  sessionKey: string;
};

type VoiceSessionContextValue = {
  activeSession: ActiveVoiceSession | null;
  snapshot: VoiceSessionSnapshot | null;
  expanded: boolean;
  startSession: (request: VoiceSessionRequest) => void;
  collapseSession: () => void;
  expandSession: () => void;
  endSession: () => void;
  isActiveForAgent: (agentId: string) => boolean;
};

const VoiceSessionContext = createContext<VoiceSessionContextValue | null>(null);

export function VoiceSessionProvider({ children }: { children: ReactNode }) {
  const [activeSession, setActiveSession] = useState<ActiveVoiceSession | null>(null);
  const [snapshot, setSnapshot] = useState<VoiceSessionSnapshot | null>(null);
  const [expanded, setExpanded] = useState(false);

  const startSession = useCallback((request: VoiceSessionRequest) => {
    setActiveSession((current) => {
      if (
        current
        && current.agentId === request.agentId
        && current.resumeSessionId === request.resumeSessionId
      ) {
        return { ...current, ...request };
      }
      setSnapshot(null);
      return {
        ...request,
        sessionKey: `${request.agentId}:${request.resumeSessionId ?? "new"}:${Date.now()}`,
      };
    });
    setExpanded(true);
  }, []);

  const collapseSession = useCallback(() => setExpanded(false), []);
  const expandSession = useCallback(() => setExpanded(true), []);
  const endSession = useCallback(() => {
    setExpanded(false);
    setSnapshot(null);
    setActiveSession(null);
  }, []);
  const isActiveForAgent = useCallback(
    (agentId: string) => activeSession?.agentId === agentId,
    [activeSession],
  );

  const value = useMemo<VoiceSessionContextValue>(() => ({
    activeSession,
    snapshot,
    expanded,
    startSession,
    collapseSession,
    expandSession,
    endSession,
    isActiveForAgent,
  }), [activeSession, snapshot, expanded, startSession, collapseSession, expandSession, endSession, isActiveForAgent]);

  return (
    <VoiceSessionContext.Provider value={value}>
      {children}
      {activeSession && (
        <>
          <VoiceSessionModal
            key={activeSession.sessionKey}
            agentId={activeSession.agentId}
            agentName={activeSession.agentName}
            getTicket={activeSession.getTicket}
            resumeSessionId={activeSession.resumeSessionId}
            onClose={collapseSession}
            onEnd={endSession}
            onSnapshot={setSnapshot}
            hidden={!expanded}
          />
          {!expanded && (
            <VoiceSessionIndicator
              session={activeSession}
              snapshot={snapshot}
              onExpand={expandSession}
              onEnd={endSession}
            />
          )}
        </>
      )}
    </VoiceSessionContext.Provider>
  );
}

export function useVoiceSession(): VoiceSessionContextValue {
  const ctx = useContext(VoiceSessionContext);
  if (!ctx) throw new Error("useVoiceSession must be used inside <VoiceSessionProvider>");
  return ctx;
}

function VoiceSessionIndicator({
  session,
  snapshot,
  onExpand,
  onEnd,
}: {
  session: ActiveVoiceSession;
  snapshot: VoiceSessionSnapshot | null;
  onExpand: () => void;
  onEnd: () => void;
}) {
  const state = snapshot?.state ?? "connecting";
  const busy = state === "connecting" || state === "processing";
  const speaking = state === "speaking";
  const label = speaking ? "Spricht" : state === "listening" ? "Hört zu" : busy ? "Arbeitet" : "Verbunden";

  return (
    <div className="fixed bottom-4 right-4 z-50 flex max-w-[calc(100vw-2rem)] items-center gap-2 rounded-full border border-border bg-card/95 px-3 py-2 text-sm shadow-2xl shadow-black/30 backdrop-blur-xl">
      <button
        onClick={onExpand}
        className="flex min-w-0 items-center gap-2 rounded-full pr-1 text-left"
        title="Live-Gespräch öffnen"
      >
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-fuchsia-500/15 text-fuchsia-300">
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Mic className="h-4 w-4" />}
        </span>
        <span className="min-w-0">
          <span className="flex items-center gap-1.5 text-xs font-medium text-foreground">
            {snapshot?.mode === "nova_sonic" && <Radio className="h-3 w-3 text-fuchsia-400" />}
            {label}
          </span>
          <span className="block truncate text-[11px] text-muted-foreground">
            spricht mit {session.agentName}
          </span>
        </span>
      </button>
      <button
        onClick={onExpand}
        className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-muted-foreground hover:bg-foreground/[0.06] hover:text-foreground"
        title="Aufklappen"
        aria-label="Live-Gespräch aufklappen"
      >
        <Maximize2 className="h-4 w-4" />
      </button>
      <button
        onClick={onEnd}
        className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-red-400 hover:bg-red-500/10"
        title="Gespräch beenden"
        aria-label="Live-Gespräch beenden"
      >
        <PhoneOff className="h-4 w-4" />
      </button>
    </div>
  );
}
