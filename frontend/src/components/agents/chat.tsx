"use client";

import { useEffect, useRef, useState, useCallback, useMemo } from "react";
import {
  Send, RotateCcw, Bot, AlertTriangle, WifiOff, ListChecks,
  Paperclip, Loader2, Square, Mic,
  ChevronRight, CheckCircle2, XCircle, Clock, X, Play, Pause, Download,
  Trash2, Type, LayoutGrid, FileText, PanelLeft, PanelLeftClose, Brain, Check, Wrench,
  GitBranch,
  ArrowDown,
  Undo2,
  Sparkles as SummarizeIcon,
} from "lucide-react";
import { useWebSocket } from "@/hooks/use-websocket";
import type { LogEvent } from "@/lib/types";
import { ChatOverview } from "./chat-overview";
import { SessionRail } from "./session-rail";
import { MarkdownContent } from "@/components/ui/markdown-content";
import { cn, formatBytes } from "@/lib/utils";
import { useConfirm, useToast } from "@/components/ui/dialog-provider";
import * as api from "@/lib/api";
import { useAuthStore } from "@/lib/auth";
import { UserAvatar } from "@/components/ui/user-avatar";
import { useSimpleMode } from "@/hooks/use-simple-mode";

/* ─── Types ─────────────────────────────────────────────────────────── */

/** How hard the agent should think before answering. "" keeps whatever the
 *  agent's harness is configured with; the rest is an explicit per-message
 *  override. `short` is what's shown next to the icon once a level is picked. */
type ReasoningLevel = "" | "off" | "low" | "medium" | "high";

const REASONING_OPTIONS: { value: ReasoningLevel; label: string; short: string }[] = [
  { value: "", label: "Standard", short: "" },
  { value: "off", label: "Nicht nachdenken", short: "aus" },
  { value: "low", label: "Kurz nachdenken", short: "kurz" },
  { value: "medium", label: "Mittel", short: "mittel" },
  { value: "high", label: "Gründlich nachdenken", short: "gründlich" },
];

interface TextStep {
  type: "text";
  content: string;
}

interface ToolStep {
  type: "tool_call";
  id: string;
  tool: string;
  input: Record<string, unknown>;
  output?: string;
  status: "running" | "done" | "error";
}

type AssistantStep = TextStep | ToolStep;

interface ChatImage {
  media_type: string;
  data: string; // base64 (no data: prefix)
}

interface ChatFile {
  path: string;
  filename: string;
  media_type?: string;
  size?: number;
  caption?: string;
}

interface ChatMessage {
  id: string;
  agentId?: string;
  role: "user" | "assistant" | "system" | "error";
  content: string;
  timestamp: string;
  isStreaming?: boolean;
  isQueued?: boolean;
  steps?: AssistantStep[];
  toolCalls?: { tool: string; input: string }[];
  meta?: { cost_usd?: number; duration_ms?: number; num_turns?: number; input_tokens?: number; output_tokens?: number; presented_files?: ChatFile[] };
  images?: ChatImage[];
  files?: ChatFile[];
}

interface ChatEvent {
  agent_id: string;
  message_id: string;
  session_id?: string;  // owning session (set by the server) — used to isolate chat tabs
  type: "text" | "tool_call" | "tool_result" | "error" | "system" | "done" | "session" | "cancelled" | "queued" | "image" | "file" | "task_card";
  data: Record<string, unknown>;
  timestamp: string;
}

/** Ein delegierter Auftrag als Kachel im Chat.
 *
 * Ohne die sah der Mensch nach „ich habe beauftragt" nichts mehr: kein
 * Fortschritt, kein Ende. Er musste nachfragen, um zu erfahren, ob ueberhaupt
 * etwas passiert — am 2026-08-13 nach 18 Minuten Stille.
 */
interface TaskCard {
  task_id: string;
  title: string;
  phase: "queued" | "done";
  status: string;
  assigned_agent_id?: string;
  assigned_agent_name?: string;
  kind?: "task" | "message";
  result_preview?: string;
  cost_usd?: number | null;
  duration_ms?: number | null;
  session_id?: string | null;
  at: number;
}

interface SessionTab {
  id: string;
  label: string;
  preview: string;
  title?: string | null;   // custom rename; falls back to preview
  pinned?: boolean;
  isNew?: boolean;
  last_message_at?: string | null;
  message_count?: number;
}

/* ─── Live activity line (issue #469) ───────────────────────────────────
 * While the agent works on a turn, the waiting indicator otherwise shows only
 * three dots. This subscribes to the agent's live log channel (the same source
 * the Activity/Terminal tab uses) and surfaces the most recent tool call —
 * tool name, target and elapsed time — so a long turn no longer looks stuck.
 * It is rendered ONLY while waiting, so the extra socket lives exactly as long
 * as the indicator does and closes itself when the turn ends. */

/** Extract a short, human-readable target from a tool's input arguments. */
function describeToolTarget(input: unknown): string {
  if (!input || typeof input !== "object") return "";
  const d = input as Record<string, unknown>;
  const str = (v: unknown) => (typeof v === "string" ? v : "");
  const isPath = Boolean(d.file_path || d.path || d.notebook_path);
  let raw =
    str(d.file_path) || str(d.path) || str(d.notebook_path) ||
    str(d.pattern) || str(d.command) || str(d.query) ||
    str(d.url) || str(d.description);
  if (!raw) return "";
  if (isPath && raw.includes("/")) {
    raw = raw.split("/").filter(Boolean).slice(-2).join("/");
  }
  raw = raw.replace(/\s+/g, " ").trim();
  return raw.length > 48 ? raw.slice(0, 47) + "…" : raw;
}

function LiveActivity({ agentId }: { agentId: string }) {
  const { messages } = useWebSocket(`/ws/agents/${agentId}/logs`);
  const [now, setNow] = useState(() => Date.now());
  // Der Kanal ist AGENTENWEIT: er fuehrt alles, was der Agent tut — geplante
  // Aufgaben, andere Gespraeche, diesen Turn. Diese Zeile gehoert aber zu EINEM
  // wartenden Turn, und der beginnt jetzt: alles Aeltere ist fremde Arbeit.
  const turnStartedAt = useRef(Date.now()).current;

  useEffect(() => {
    const iv = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(iv);
  }, []);

  const latestToolCall = useMemo<LogEvent | null>(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m.type !== "tool_call") continue;
      // Traegt eine task_id → gehoert zu einer geplanten Aufgabe, nicht zu diesem
      // Gespraech. Ohne diesen Filter stand der OpenWebUI-Watcher im Chat des
      // Nutzers, waehrend der ueber etwas voellig anderes sprach.
      if (m.task_id) continue;
      // Und: ein Aufruf von VOR diesem Turn ist laengst vorbei. Frueher wurde sein
      // Alter munter weitergezaehlt — daher die „192s" an einem Turn, der 38s dauerte.
      const ts = new Date(m.timestamp).getTime();
      if (!Number.isFinite(ts) || ts < turnStartedAt) continue;
      return m;
    }
    return null;
  }, [messages, turnStartedAt]);

  if (!latestToolCall) return null;

  const data = latestToolCall.data as Record<string, unknown>;
  const tool = String(data.tool || "");
  if (!tool) return null;
  const target = describeToolTarget(data.input);
  const startedAt = new Date(latestToolCall.timestamp).getTime();
  const elapsed = Number.isFinite(startedAt)
    ? Math.max(0, Math.floor((now - startedAt) / 1000))
    : 0;

  return (
    <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground/70 tabular-nums">
      <Wrench className="h-3 w-3 shrink-0" />
      <span className="font-medium text-muted-foreground/90">{tool}</span>
      {target && (
        <span className="truncate max-w-[220px]">· {target}</span>
      )}
      <span className="text-muted-foreground/50 shrink-0">· {elapsed}s</span>
    </div>
  );
}

import { getWsUrl, getApiUrl } from "@/lib/config";
import { useVoiceSession } from "./voice-session-provider";
import { formatMoney } from "@/lib/money";
const MAX_RECONNECT_ATTEMPTS = 5;

/* ─── Tool Display Helper ───────────────────────────────────────────── */

function getToolDisplay(tool: string, input: Record<string, unknown>): { label: string; description: string; detail: string } {
  const inp = input || {};
  switch (tool) {
    case "Bash":
    case "bash":
      return {
        label: "Bash",
        description: String(inp.description || ""),
        detail: String(inp.command || ""),
      };
    case "Read":
    case "read":
      return {
        label: "Read",
        description: String(inp.file_path || "").split("/").pop() || "Read file",
        detail: String(inp.file_path || ""),
      };
    case "Write":
    case "write":
      return {
        label: "Write",
        description: String(inp.file_path || "").split("/").pop() || "Write file",
        detail: String(inp.file_path || ""),
      };
    case "Edit":
    case "edit":
      return {
        label: "Edit",
        description: String(inp.file_path || "").split("/").pop() || "Edit file",
        detail: String(inp.file_path || ""),
      };
    case "Grep":
    case "grep":
      return {
        label: "Grep",
        description: `Search: ${String(inp.pattern || "")}`,
        detail: `${inp.pattern || ""}${inp.path ? ` in ${inp.path}` : ""}`,
      };
    case "Glob":
    case "glob":
      return {
        label: "Glob",
        description: String(inp.pattern || ""),
        detail: String(inp.pattern || ""),
      };
    case "WebSearch":
    case "web_search":
      return {
        label: "WebSearch",
        description: String(inp.query || "Search"),
        detail: String(inp.query || ""),
      };
    case "WebFetch":
    case "web_fetch":
      return {
        label: "WebFetch",
        description: "Fetch URL",
        detail: String(inp.url || ""),
      };
    case "Task":
    case "task":
      return {
        label: "Task",
        description: String(inp.description || "Run subagent"),
        detail: String(inp.prompt || "").slice(0, 300),
      };
    case "TodoWrite":
      return {
        label: "TodoWrite",
        description: "Update tasks",
        detail: "",
      };
    default:
      return {
        label: tool || "Tool",
        description: "",
        detail: JSON.stringify(inp).slice(0, 300),
      };
  }
}

function extractResultContent(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((block) => {
        if (typeof block === "string") return block;
        if (block?.type === "text") return block.text;
        return JSON.stringify(block);
      })
      .join("\n");
  }
  return typeof content === "object" ? JSON.stringify(content, null, 2) : String(content);
}

/* ─── Main Component ────────────────────────────────────────────────── */

/**
 * Befehle, die im Eingabefeld über „/" erreichbar sind.
 *
 * Sie führen AUSSCHLIESSLICH auf Fähigkeiten, die es schon gibt — dieselben
 * Funktionen, die auch die Knöpfe auslösen. Ein Hinweis auf Befehle, die nirgends
 * hinführen, wäre schlimmer als gar keiner.
 */
/** Notnagel, falls die Ausstattung des Agenten (noch) nicht geladen ist.
 *
 *  Nur die Befehle, die auf dem gespeicherten Verlauf arbeiten — die gelten in
 *  jeder Laufzeit. Werkzeuge stehen hier bewusst NICHT: welche der Agent hat,
 *  weiss nur der Server, und etwas anzubieten, das er nicht kann, wäre schlimmer
 *  als eine kurze Liste. */
const FALLBACK_COMMANDS: api.AgentToolset["commands"] = [
  { name: "compact", hint: "Kontext anzeigen und den Verlauf verdichten" },
  { name: "planen", hint: "Nur den Weg beschreiben, nichts ausführen" },
  { name: "zusammenfassen", hint: "In frischem Gespräch weiterreden" },
  { name: "verzweigen", hint: "Ab der letzten Nachricht abzweigen" },
  { name: "zurueckspulen", hint: "Auf die letzte Nachricht zurücksetzen" },
];

/** Kontextring — der belegte Anteil des Gesprächsfensters als Kreis.
 *
 *  Ein Ring statt eines Balkens, weil er neben dem Absenden liegt und dort nur
 *  wenige Millimeter breit sein darf. Die Zahlen dahinter stehen im Aufklapper. */
function ContextRing({ percent }: { percent: number }) {
  const radius = 6;
  const circumference = 2 * Math.PI * radius;
  const filled = (Math.min(Math.max(percent, 0), 100) / 100) * circumference;
  const color =
    percent < 50 ? "stroke-emerald-500" : percent < 80 ? "stroke-amber-500" : "stroke-red-500";
  return (
    <svg viewBox="0 0 16 16" className="h-3.5 w-3.5 -rotate-90">
      <circle cx="8" cy="8" r={radius} className="fill-none stroke-foreground/[0.12]" strokeWidth="2.5" />
      <circle
        cx="8"
        cy="8"
        r={radius}
        className={cn("fill-none transition-all duration-500", color)}
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeDasharray={`${filled} ${circumference}`}
      />
    </svg>
  );
}

export function AgentChat({ agentId, initialSessionId, embedded, busySessionIds }: { agentId: string; initialSessionId?: string | null; embedded?: boolean; busySessionIds?: string[] }) {
  const { simpleMode } = useSimpleMode();
  const [sessions, setSessions] = useState<SessionTab[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const chatToast = useToast();
  const chatConfirm = useConfirm();
  // Chat management UX
  const [viewMode, setViewMode] = useState<"chat" | "overview">("chat");
  // Conversation rail (left) — starts open on desktop, collapsed on small screens.
  const [railOpen, setRailOpen] = useState(false);
  useEffect(() => {
    if (typeof window !== "undefined" && window.matchMedia("(min-width: 768px)").matches) {
      setRailOpen(true);
    }
  }, []);
  const [isDragOver, setIsDragOver] = useState(false);
  // Counts nested dragenter/dragleave so the overlay doesn't flicker while
  // dragging across child elements (textarea, buttons).
  const dragDepthRef = useRef(0);
  // Font size (persisted): 0.85–1.4, applied to the whole chat via CSS var.
  const [fontScale, setFontScale] = useState(1);
  useEffect(() => {
    const saved = typeof window !== "undefined" ? window.localStorage.getItem("chatFontScale") : null;
    if (saved) setFontScale(Math.min(1.4, Math.max(0.85, parseFloat(saved) || 1)));
  }, []);
  const changeFontScale = (delta: number) => {
    setFontScale((prev) => {
      const next = Math.min(1.4, Math.max(0.85, Math.round((prev + delta) * 100) / 100));
      if (typeof window !== "undefined") window.localStorage.setItem("chatFontScale", String(next));
      return next;
    });
  };
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  // Delegierte Auftraege dieses Gespraechs, nach Auftragskennung.
  const [taskCards, setTaskCards] = useState<Record<string, TaskCard>>({});
  // Aufgabe, deren Einzelheiten gerade im Fenster stehen (null = zu).
  const [cardDetail, setCardDetail] = useState<TaskCard | null>(null);
  const [cardDetailFull, setCardDetailFull] = useState<Record<string, unknown> | null>(null);
  const [input, setInput] = useState("");
  // Per-message reasoning depth, picked by the user (like the thinking selector
  // in ChatGPT/Claude Code). "" = leave the agent's harness at its default.
  const [reasoning, setReasoning] = useState<ReasoningLevel>("");
  const [reasoningOpen, setReasoningOpen] = useState(false);
  const reasoningRef = useRef<HTMLDivElement | null>(null);
  // Close the popover on an outside click — it sits above the input, so leaving
  // it open would cover the conversation.
  useEffect(() => {
    if (!reasoningOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!reasoningRef.current?.contains(e.target as Node)) setReasoningOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [reasoningOpen]);
  const [pendingImages, setPendingImages] = useState<ChatImage[]>([]);
  // Files attached via drag&drop or paperclip — uploaded on send, like pasted images.
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const voiceSession = useVoiceSession();
  const [isConnected, setIsConnected] = useState(false);
  const [isWaiting, setIsWaiting] = useState(false);
  // Resume (#chat-live): the agent is working on THIS session but the turn wasn't
  // started from this connection (e.g. we just re-entered the chat). Shows a live
  // indicator and reloads history when the turn finishes.
  const [liveElsewhere, setLiveElsewhere] = useState(false);
  const [historyReloadKey, setHistoryReloadKey] = useState(0);
  const isWaitingRef = useRef(false);
  const pendingCountRef = useRef(0);
  const [connectionFailed, setConnectionFailed] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [sessionsLoaded, setSessionsLoaded] = useState(false);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [thinkingStartTime, setThinkingStartTime] = useState<number | null>(null);
  const [thinkingElapsed, setThinkingElapsed] = useState(0);
  const [totalCost, setTotalCost] = useState(0);
  const [totalTurns, setTotalTurns] = useState(0);

  // L3 approval polling
  interface PendingApproval {
    approval_id: string;
    tool: string;
    reasoning: string;
    risk_level: string;
    agent_id: string;
  }
  const [pendingApproval, setPendingApproval] = useState<PendingApproval | null>(null);
  const [messageCount, setMessageCount] = useState(0);
  const wsRef = useRef<WebSocket | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  // Folgt die Ansicht dem Strom? Wird ausschliesslich vom Scrollen gesetzt, nicht
  // von der Position beim Eintreffen einer Nachricht — siehe Auto-Scroll unten.
  const followRef = useRef(true);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  // Auto-grow the input with its content (capped via max-h on the element);
  // collapses back to one row when cleared (e.g. after sending).
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [input]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const reconnectTimeout = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const reconnectAttempts = useRef(0);
  const intentionalClose = useRef(false);
  const currentWsSessionId = useRef<string | null>(null);
  const activeSessionIdRef = useRef<string | null>(null);
  // Stable per-tab id so the backend can keep this tab's socket separate from other
  // tabs/windows chatting with the same agent — without it, opening a 2nd chat kicks the 1st.
  const tabClientIdRef = useRef<string | undefined>(undefined);
  if (tabClientIdRef.current === undefined) {
    tabClientIdRef.current = typeof crypto !== "undefined" && crypto.randomUUID
      ? crypto.randomUUID()
      : Math.random().toString(36).slice(2);
  }

  // Keep ref in sync with state
  useEffect(() => {
    activeSessionIdRef.current = activeSessionId;
  }, [activeSessionId]);

  useEffect(() => {
    isWaitingRef.current = isWaiting;
  }, [isWaiting]);

  // Load sessions from DB on mount
  useEffect(() => {
    if (sessionsLoaded) return;
    const loadSessions = async () => {
      try {
        const { sessions: dbSessions } = await api.getChatSessions(agentId);
        if (dbSessions.length > 0) {
          // Filter out sessions with no real content (e.g. phantom "default" entries)
          const validSessions = dbSessions.filter(
            (s) => s.message_count > 1 || s.preview
          );
          const sessionsToUse = validSessions.length > 0 ? validSessions : dbSessions;
          const tabs: SessionTab[] = sessionsToUse.map((s, i) => ({
            id: s.id,
            label: `Chat ${sessionsToUse.length - i}`,
            preview: s.preview || "",
            title: s.title ?? null,
            pinned: !!s.pinned,
            last_message_at: s.last_message_at,
            message_count: s.message_count,
          }));
          setSessions(tabs);
          // Only auto-select a session if explicitly requested (e.g. from conversation list)
          // If no initialSessionId → user wants a new chat, don't auto-select
          if (initialSessionId) {
            const found = tabs.find((t) => t.id === initialSessionId);
            setActiveSessionId(found ? found.id : tabs[0].id);
          }
          // If no initialSessionId, leave activeSessionId null → new chat
        }
      } catch {
        // No sessions yet
      } finally {
        setSessionsLoaded(true);
      }
    };
    loadSessions();
  }, [agentId, sessionsLoaded, initialSessionId]);

  // Re-fetch the session list on demand (e.g. after a voice conversation ends) so the
  // freshly persisted voice session shows up as a tab WITHOUT a page reload.
  const refreshSessions = useCallback(async () => {
    try {
      const { sessions: dbSessions } = await api.getChatSessions(agentId);
      const validSessions = dbSessions.filter((s) => s.message_count > 1 || s.preview);
      const sessionsToUse = validSessions.length > 0 ? validSessions : dbSessions;
      setSessions(
        sessionsToUse.map((s, i) => ({
          id: s.id,
          label: `Chat ${sessionsToUse.length - i}`,
          preview: s.preview || "",
          title: s.title ?? null,
          pinned: !!s.pinned,
          last_message_at: s.last_message_at,
          message_count: s.message_count,
        }))
      );
    } catch {
      /* keep current tabs on failure */
    }
  }, [agentId]);

  // Load messages when active session changes
  useEffect(() => {
    if (!activeSessionId) {
      setHistoryLoaded(true);
      return;
    }
    setHistoryLoaded(false);
    const loadHistory = async () => {
      try {
        const { messages: history, has_more: hasMore } = await api.getChatHistory(agentId, 500, activeSessionId);
        if (hasMore) {
          console.warn("[Chat] More than 500 messages in session - older messages not shown");
        }
        // Gespeicherte Auftrags-Kacheln zurueckholen. Ohne das waren sie nach
        // jedem Neuladen weg — und mit ihnen die einzige Spur im Gespraech, dass
        // ueberhaupt jemand beauftragt wurde.
        const wiederhergestellt: Record<string, TaskCard> = {};
        for (const m of history) {
          const gespeichert = (m as { meta?: { task_card?: TaskCard } }).meta?.task_card;
          if (gespeichert?.task_id) {
            wiederhergestellt[gespeichert.task_id] = {
              ...gespeichert,
              at: new Date(m.timestamp).getTime() || Date.now(),
            };
          }
        }
        setTaskCards(wiederhergestellt);

        if (history.length > 0) {
          const restored: ChatMessage[] = history.map((m) => {
            // Convert legacy toolCalls to steps
            let steps: AssistantStep[] | undefined;
            if (m.role === "assistant") {
              steps = [];
              if (m.toolCalls && m.toolCalls.length > 0) {
                for (const tc of m.toolCalls) {
                  let parsedInput: Record<string, unknown> = {};
                  try { parsedInput = JSON.parse(tc.input || "{}"); } catch { /* truncated */ }
                  steps.push({
                    type: "tool_call",
                    id: `hist-${Math.random().toString(36).slice(2, 8)}`,
                    tool: tc.tool,
                    input: parsedInput,
                    status: "done",
                  });
                }
              }
              if (m.content) {
                steps.push({ type: "text", content: m.content });
              }
            }
            // Use message_id for user messages, response-{message_id} for assistant
            const displayId = m.role === "assistant" ? `response-${m.id}` : m.id;
            const presented = (m.meta?.presented_images as ChatImage[] | undefined);
            const presentedFiles = (m.meta?.presented_files as ChatFile[] | undefined);
            return {
              id: displayId,
              agentId,
              role: m.role,
              content: m.content,
              timestamp: m.timestamp,
              steps,
              meta: m.meta ?? undefined,
              images: m.role === "assistant" && presented?.length ? presented : m.images,
              files: m.role === "assistant" && presentedFiles?.length ? presentedFiles : undefined,
            };
          });
          // Deduplicate by id+role
          const seen = new Set<string>();
          const deduped = restored.filter((m) => {
            const key = `${m.id}-${m.role}`;
            if (seen.has(key)) return false;
            seen.add(key);
            return true;
          });
          // Merge with any currently streaming messages (don't lose active streams)
          setMessages((prev) => {
            const streaming = prev.filter((m) => m.isStreaming);
            if (streaming.length === 0) return deduped;
            // Keep streaming messages, add history that isn't already present
            const streamIds = new Set(streaming.map((m) => m.id));
            const merged = [...deduped.filter((m) => !streamIds.has(m.id)), ...streaming];
            return merged;
          });
          let cost = 0;
          let turns = 0;
          for (const m of restored) {
            if (m.meta?.cost_usd) cost += m.meta.cost_usd;
            if (m.meta?.num_turns) turns += m.meta.num_turns;
          }
          setTotalCost(cost);
          setTotalTurns(turns);
          setMessageCount(restored.filter((m) => m.role === "user" || m.role === "assistant").length);
        } else {
          setMessages([]);
          setTotalCost(0);
          setTotalTurns(0);
          setMessageCount(0);
        }
      } catch {
        setMessages([]);
      } finally {
        setHistoryLoaded(true);
      }
    };
    loadHistory();
  }, [agentId, activeSessionId, historyReloadKey]);

  // Live-resume: while a conversation is open, poll the agent's status. If the agent
  // is working on THIS session but not via our own turn (we re-entered mid-run), show
  // a live indicator; when it finishes, reload history so the answer appears — even
  // though this reconnected socket never received the (foreign message_id) stream.
  useEffect(() => {
    if (!activeSessionId) { setLiveElsewhere(false); return; }
    let cancelled = false;
    let prevBusy = false;
    const check = async () => {
      try {
        const a = await api.getAgent(agentId);
        const list = (a as unknown as { active_sessions?: string[] }).active_sessions;
        const busy = Array.isArray(list) && list.includes(`chat:${activeSessionId}`);
        if (cancelled) return;
        setLiveElsewhere(busy && !isWaitingRef.current);
        if (prevBusy && !busy) setHistoryReloadKey((k) => k + 1);  // a turn just finished
        prevBusy = busy;
      } catch { /* transient — ignore */ }
    };
    check();
    const iv = setInterval(check, 4000);
    return () => { cancelled = true; clearInterval(iv); };
  }, [agentId, activeSessionId]);

  const connect = useCallback(async () => {
    if (reconnectAttempts.current >= MAX_RECONNECT_ATTEMPTS) {
      setConnectionFailed(true);
      setMessages((prev) => [
        ...prev.filter((m) => m.id !== "reconnecting"),
        {
          id: "connection-failed",
          role: "error",
          content: "Could not connect to agent. The container may be stopped or removed.",
          timestamp: new Date().toISOString(),
        },
      ]);
      return;
    }

    // Fetch one-time ticket for WebSocket auth (short-lived, single-use).
    // SECURITY (#337): never fall back to a long-lived JWT in the URL — that
    // leaks the token into proxy/access logs, browser history and Referer
    // headers. If no ticket can be obtained, treat it as a temporary connection
    // failure and retry with backoff instead of degrading auth.
    let ticket: string | null = null;
    try {
      const resp = await fetch(`${getApiUrl()}/api/v1/ws/ticket`, {
        method: "POST",
        credentials: "include",
      });
      if (resp.ok) {
        ticket = (await resp.json())?.ticket ?? null;
      }
    } catch {
      ticket = null;
    }

    if (!ticket) {
      setIsConnected(false);
      reconnectAttempts.current++;
      if (reconnectAttempts.current < MAX_RECONNECT_ATTEMPTS) {
        const delay = Math.min(1000 * Math.pow(2, reconnectAttempts.current - 1), 10000);
        setMessages((prev) => [
          ...prev.filter((m) => m.id !== "reconnecting"),
          {
            id: "reconnecting",
            role: "system",
            content: `Reconnecting... (${reconnectAttempts.current}/${MAX_RECONNECT_ATTEMPTS})`,
            timestamp: new Date().toISOString(),
          },
        ]);
        reconnectTimeout.current = setTimeout(connect, delay);
      } else {
        setConnectionFailed(true);
        setMessages((prev) => [
          ...prev.filter((m) => m.id !== "reconnecting"),
          {
            id: "connection-failed",
            role: "error",
            content: "Could not authenticate the connection. Please refresh the page and sign in again.",
            timestamp: new Date().toISOString(),
          },
        ]);
      }
      return;
    }

    const ws = new WebSocket(`${getWsUrl()}/api/v1/ws/agents/${agentId}/chat?ticket=${ticket}&client_id=${tabClientIdRef.current}`);
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnected(true);
      setConnectionFailed(false);
      reconnectAttempts.current = 0;
      // Only clean up reconnect/error messages - don't override history
      setMessages((prev) =>
        prev.filter((m) => m.id !== "reconnecting" && m.id !== "connection-failed")
      );
    };

    ws.onclose = (event) => {
      setIsConnected(false);
      // Don't reconnect if we intentionally closed (e.g., navigation / unmount)
      if (intentionalClose.current) {
        intentionalClose.current = false;
        return;
      }
      // 4001 = auth failure (permanent), 4004 = agent not found (permanent)
      // 4010 = container stopped/restarting → treat as temporary, keep retrying
      if (event.code === 4001 || event.code === 4004) {
        setConnectionFailed(true);
        setMessages((prev) => [
          ...prev.filter((m) => m.id !== "reconnecting"),
          {
            id: "agent-unavailable",
            role: "error",
            content: event.reason || "Agent is not available. Container may be stopped or removed.",
            timestamp: new Date().toISOString(),
          },
        ]);
        return;
      }
      reconnectAttempts.current++;
      if (reconnectAttempts.current < MAX_RECONNECT_ATTEMPTS) {
        const delay = Math.min(1000 * Math.pow(2, reconnectAttempts.current - 1), 10000);
        const isContainerDown = event.code === 4010;
        setMessages((prev) => {
          const filtered = prev.filter((m) => m.id !== "reconnecting");
          return [
            ...filtered,
            {
              id: "reconnecting",
              role: "system",
              content: isContainerDown
                ? `Agent container is starting… reconnecting (${reconnectAttempts.current}/${MAX_RECONNECT_ATTEMPTS})`
                : `Reconnecting... (${reconnectAttempts.current}/${MAX_RECONNECT_ATTEMPTS})`,
              timestamp: new Date().toISOString(),
            },
          ];
        });
        reconnectTimeout.current = setTimeout(connect, delay);
      } else {
        setConnectionFailed(true);
        setMessages((prev) => [
          ...prev.filter((m) => m.id !== "reconnecting"),
          {
            id: "connection-failed",
            role: "error",
            content: "Could not connect to agent. The container may be stopped or removed.",
            timestamp: new Date().toISOString(),
          },
        ]);
      }
    };

    ws.onerror = () => ws.close();

    ws.onmessage = (event) => {
      try {
        const chatEvent: ChatEvent = JSON.parse(event.data);
        if (chatEvent.type === "session") {
          const sid = String(chatEvent.data.session_id || "");
          if (sid) {
            currentWsSessionId.current = sid;
            // Only adopt new session if we don't have one yet
            // (this happens on first-ever message or after /reset)
            if (!activeSessionIdRef.current) {
              setActiveSessionId(sid);
            }
            // Only add to session tabs if truly new (not already in list)
            setSessions((prev) => {
              if (prev.some((s) => s.id === sid)) return prev;
              return [{ id: sid, label: `Chat ${prev.length + 1}`, preview: "", isNew: true }, ...prev];
            });
          }
          return;
        }
        handleChatEvent(chatEvent);
      } catch {
        // Ignore non-JSON messages
      }
    };
  }, [agentId]);

  /* ─── Event Handler (step-based) ──────────────────────────────────── */

  const handleChatEvent = useCallback((event: ChatEvent) => {
    const { message_id, type, data } = event;

    // Session isolation: only render events for the chat currently open. The
    // server tags each response with its owning session_id; anything from a
    // different session (another tab, a background task, a voice delegation)
    // must NOT bleed into this view.
    if (event.session_id && activeSessionIdRef.current && event.session_id !== activeSessionIdRef.current) {
      return;
    }

    // Kachel eines delegierten Auftrags — eigener Zustand, kein Chatverlauf:
    // sie aktualisiert sich an Ort und Stelle (queued -> done), statt zweimal
    // als Nachricht aufzutauchen.
    if (type === "task_card") {
      const card = data as unknown as TaskCard;
      if (!card?.task_id) return;
      setTaskCards((prev) => {
        const next = { ...prev };
        next[card.task_id] = { ...(next[card.task_id] || {}), ...card, at: Date.now() };
        return next;
      });
      return;
    }

    setMessages((prev) => {
      const msgs = [...prev];
      let assistantIdx = msgs.findIndex(
        (m) => (m.id === `response-${message_id}` || m.id === message_id) && m.role === "assistant"
      );

      // Create assistant message if it doesn't exist yet
      if (assistantIdx === -1 && (type === "text" || type === "tool_call" || type === "tool_result" || type === "image" || type === "file")) {
        // Remove the queued indicator for this message (if any)
        const queuedMsgId = `queued-${message_id}`;
        const withoutQueued = msgs.filter((m) => m.id !== queuedMsgId);
        msgs.length = 0;
        msgs.push(...withoutQueued);

        msgs.push({
          id: `response-${message_id}`,
          agentId,
          role: "assistant",
          content: "",
          timestamp: event.timestamp,
          isStreaming: true,
          steps: [],
        });
        assistantIdx = msgs.length - 1;
      }

      if (type === "text") {
        const steps = [...(msgs[assistantIdx].steps || [])];
        // Text after tool calls means all previous tools completed
        const updatedSteps = steps.map((s) =>
          s.type === "tool_call" && s.status === "running"
            ? { ...s, status: "done" as const }
            : s
        );
        const lastStep = updatedSteps[updatedSteps.length - 1];
        if (lastStep && lastStep.type === "text") {
          // Append to existing text step
          updatedSteps[updatedSteps.length - 1] = { ...lastStep, content: lastStep.content + String(data.text || "") };
        } else {
          // New text step (after tool calls or at start)
          updatedSteps.push({ type: "text", content: String(data.text || "") });
        }
        msgs[assistantIdx] = {
          ...msgs[assistantIdx],
          steps: updatedSteps,
          content: msgs[assistantIdx].content + String(data.text || ""),
        };
      } else if (type === "tool_call") {
        const steps = [...(msgs[assistantIdx].steps || [])];
        const toolId = String(data.tool_use_id || `tc-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`);
        // Skip if we already have this tool_call (dedup)
        const alreadyExists = steps.some((s) => s.type === "tool_call" && s.id === toolId);
        if (!alreadyExists) {
          // A new tool call means all previous running tools have completed
          const updatedSteps = steps.map((s) =>
            s.type === "tool_call" && s.status === "running"
              ? { ...s, status: "done" as const }
              : s
          );
          const inputObj = (typeof data.input === "object" && data.input !== null)
            ? data.input as Record<string, unknown>
            : {};
          updatedSteps.push({
            type: "tool_call",
            id: toolId,
            tool: String(data.tool || ""),
            input: inputObj,
            status: "running",
          });
          msgs[assistantIdx] = { ...msgs[assistantIdx], steps: updatedSteps };
        } else {
          msgs[assistantIdx] = { ...msgs[assistantIdx], steps };
        }
      } else if (type === "tool_result") {
        const steps = [...(msgs[assistantIdx].steps || [])];
        const toolUseId = String(data.tool_use_id || "");
        const content = extractResultContent(data.content);
        // Match by tool_use_id first, then fall back to last running tool
        let tcIdx = -1;
        if (toolUseId) {
          tcIdx = steps.findIndex((s) => s.type === "tool_call" && s.id === toolUseId);
        }
        if (tcIdx === -1) {
          // Find last running tool_call
          for (let i = steps.length - 1; i >= 0; i--) {
            if (steps[i].type === "tool_call" && (steps[i] as ToolStep).status === "running") {
              tcIdx = i;
              break;
            }
          }
        }
        if (tcIdx !== -1) {
          const tc = steps[tcIdx] as ToolStep;
          steps[tcIdx] = { ...tc, output: content, status: "done" };
        }
        msgs[assistantIdx] = { ...msgs[assistantIdx], steps };
      } else if (type === "image") {
        // Agent presented a generated/processed image via the present_image tool
        if (assistantIdx !== -1) {
          const imgs = [...(msgs[assistantIdx].images || [])];
          const dataStr = String(data.data || "");
          if (dataStr) {
            imgs.push({ media_type: String(data.media_type || "image/png"), data: dataStr });
          }
          msgs[assistantIdx] = { ...msgs[assistantIdx], images: imgs };
        }
      } else if (type === "file") {
        if (assistantIdx !== -1) {
          const files = [...(msgs[assistantIdx].files || [])];
          const path = String(data.path || "");
          if (path) {
            files.push({
              path,
              filename: String(data.filename || path.split("/").pop() || "download"),
              media_type: String(data.media_type || "application/octet-stream"),
              size: Number(data.size || 0),
              caption: String(data.caption || ""),
            });
          }
          msgs[assistantIdx] = { ...msgs[assistantIdx], files };
        }
      } else if (type === "queued") {
        // The agent drains pending chat messages mid-turn, so this is a live
        // steering acknowledgement rather than a "wait until later" state.
        const queuedMsgId = `queued-${message_id}`;
        if (!msgs.some((m) => m.id === queuedMsgId)) {
          msgs.push({
            id: queuedMsgId,
            role: "system",
            content: "Message received — steering current agent turn",
            timestamp: event.timestamp,
            isQueued: true,
          });
        }
      } else if (type === "error") {
        msgs.push({
          id: `error-${message_id}-${Date.now()}`,
          role: "error",
          content: String(data.message || "Unknown error"),
          timestamp: event.timestamp,
        });
        pendingCountRef.current = Math.max(0, pendingCountRef.current - 1);
        if (pendingCountRef.current === 0) {
          setIsWaiting(false);
        }
      } else if (type === "cancelled") {
        // Agent was stopped by user
        if (assistantIdx !== -1) {
          const steps = (msgs[assistantIdx].steps || []).map((s) =>
            s.type === "tool_call" && s.status === "running"
              ? { ...s, status: "done" as const }
              : s
          );
          msgs[assistantIdx] = {
            ...msgs[assistantIdx],
            isStreaming: false,
            steps,
          };
        }
        pendingCountRef.current = 0;
        setIsWaiting(false);
      } else if (type === "done") {
        if (assistantIdx !== -1) {
          const meta = {
            cost_usd: Number(data.cost_usd || 0),
            duration_ms: Number(data.duration_ms || 0),
            num_turns: Number(data.num_turns || 0),
            input_tokens: Number(data.input_tokens || 0),
            output_tokens: Number(data.output_tokens || 0),
          };
          // Mark all running tool calls as done
          const steps = (msgs[assistantIdx].steps || []).map((s) =>
            s.type === "tool_call" && s.status === "running"
              ? { ...s, status: "done" as const }
              : s
          );
          msgs[assistantIdx] = {
            ...msgs[assistantIdx],
            isStreaming: false,
            meta,
            steps,
          };
          setTotalCost((c) => c + meta.cost_usd);
          setTotalTurns((t) => t + meta.num_turns);
          setMessageCount((c) => c + 1);
        }
        // Decrement pending count - only stop waiting when all messages are processed
        pendingCountRef.current = Math.max(0, pendingCountRef.current - 1);
        if (pendingCountRef.current === 0) {
          setIsWaiting(false);
        }
      }

      return msgs;
    });
  }, []);

  useEffect(() => {
    connect();
    return () => {
      intentionalClose.current = true;
      wsRef.current?.close();
      clearTimeout(reconnectTimeout.current);
    };
  }, [connect]);

  // Auto-scroll — jump instantly (no "smooth", which made the view creep on every
  // streamed token) and only while the view is FOLLOWING, so reading older
  // messages isn't yanked away mid-stream.
  //
  // Ob gefolgt wird, entscheidet jetzt das Scrollen selbst (``followRef``) und
  // nicht mehr die Position im Moment der neuen Nachricht. Der Unterschied
  // zaehlt genau in einem langen Gespraech: dort steht die frisch geladene
  // Ansicht ganz oben, ``scrollTop`` ist 0 — „fast unten" war also nie wahr, und
  // damit sprang die Ansicht kein einziges Mal ans Ende. Man klebte oben, und
  // der laufende Strom lief unsichtbar unter einem weiter.
  useEffect(() => {
    if (!followRef.current) return;
    requestAnimationFrame(() => {
      bottomRef.current?.scrollIntoView({ behavior: "auto" });
    });
  }, [messages]);

  // Ein Gesprächswechsel beginnt beim Neuesten — es gibt noch keine Lesestelle,
  // die zu schuetzen waere.
  useEffect(() => {
    followRef.current = true;
    setShowJumpToLatest(false);
  }, [activeSessionId]);

  // Nach dem Laden der Vorgeschichte stehen die Nachrichten erst im DOM, wenn
  // der Browser gelayoutet hat — deshalb hier und nicht im Ladepfad.
  useEffect(() => {
    if (!historyLoaded || !followRef.current) return;
    requestAnimationFrame(() => {
      bottomRef.current?.scrollIntoView({ behavior: "auto" });
    });
  }, [historyLoaded, activeSessionId]);

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    followRef.current = nearBottom;
    setShowJumpToLatest(!nearBottom);
  }, []);

  /** Einzelheiten einer Kachel im Fenster zeigen — nicht auf einer neuen Seite.
   *
   * Ein Seitenwechsel reisst aus dem Gespraech heraus: der Verlauf ist weg, der
   * Rueckweg kostet einen Klick, und wer nur kurz nachsehen wollte, verliert den
   * Faden. Die vollstaendige Aufgabe wird nachgeladen; die Kachel selbst zeigt
   * sofort, was sie schon weiss. */
  const openCardDetail = useCallback(async (card: TaskCard) => {
    setCardDetail(card);
    setCardDetailFull(null);
    try {
      const task = await api.getTask(card.task_id);
      setCardDetailFull(task as unknown as Record<string, unknown>);
    } catch {
      setCardDetailFull(null);   // Kachelangaben genuegen dann
    }
  }, []);

  const jumpToLatest = useCallback(() => {
    followRef.current = true;
    setShowJumpToLatest(false);
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  const sendMessage = useCallback(async (plan = false) => {
    const text = input.trim();
    const imgs = pendingImages;
    const files = pendingFiles;
    if ((!text && imgs.length === 0 && files.length === 0) || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;

    // Wer selbst schreibt, will die Antwort sehen — auch wenn er vorher weiter
    // oben gelesen hat. Senden hebt das Anhalten der Ansicht auf; nur Scrollen
    // haelt sie an, nicht ein einmal weiter oben gesetzter Zustand.
    followRef.current = true;
    setShowJumpToLatest(false);

    // Upload attached files to the agent's workspace first — the message only
    // goes out if the upload succeeds (pending chips stay on failure).
    let agentText = text;
    if (files.length > 0) {
      setIsUploading(true);
      try {
        await api.uploadFiles(agentId, "/workspace", files);
      } catch (e) {
        setMessages((prev) => [
          ...prev,
          { id: `upload-error-${Date.now()}`, role: "error", content: `Upload fehlgeschlagen: ${e instanceof Error ? e.message : "Unbekannter Fehler"}`, timestamp: new Date().toISOString() },
        ]);
        setIsUploading(false);
        return;
      }
      setIsUploading(false);
      const filePaths = files.map((f) => `/workspace/${f.name}`).join(", ");
      // Explicit read-instruction (not a passive note) — otherwise the agent treats the
      // filename as mere context and answers without opening the file (the reported bug:
      // "PDF im Chat nicht sichtbar"). Full paths + a clear order to read first.
      agentText = `${text ? `${text}\n\n` : ""}[Angehängte Datei(en) im Workspace: ${filePaths}. WICHTIG: Öffne und lies die Datei(en) ZUERST selbst mit deinem Read-Tool (PDFs und Bilder werden unterstützt; große Textdateien ggf. mit bash/grep) und antworte dann auf Basis des TATSÄCHLICHEN Inhalts — rate NICHT aus dem Dateinamen.]`;
    }

    const msgId = `user-${Date.now()}`;
    setMessages((prev) => [
      ...prev,
      {
        id: msgId,
        role: "user",
        content: text,
        timestamp: new Date().toISOString(),
        images: imgs.length > 0 ? imgs : undefined,
        files: files.length > 0
          ? files.map((f) => ({ path: `/workspace/${f.name}`, filename: f.name, media_type: f.type || undefined, size: f.size }))
          : undefined,
      },
    ]);
    setMessageCount((c) => c + 1);

    // Plan mode (#386): inject a "plan only" instruction so the agent describes the
    // steps it WOULD take instead of executing. The visible user message stays as
    // typed; only what the agent receives is wrapped.
    if (plan) {
      agentText = `[NUR PLANEN — NICHT AUSFÜHREN] Beschreibe kurz und konkret, welche Schritte du für die folgende Aufgabe gehen würdest (Tools, betroffene Dateien/Befehle, externe Aktionen, grober Aufwand/Risiken). Führe nichts aus, ändere nichts, sende nichts — gib NUR den Plan zurück.\n\nAufgabe: ${agentText}`;
    }

    wsRef.current.send(JSON.stringify({
      text: agentText,
      images: imgs,
      session_id: activeSessionId || currentWsSessionId.current,
      source: "webapp",
      reasoning,
    }));
    setInput("");
    setPendingImages([]);
    setPendingFiles([]);
    pendingCountRef.current += 1;
    setIsWaiting(true);
    inputRef.current?.focus();

    setSessions((prev) =>
      prev.map((s) =>
        s.id === (activeSessionId || currentWsSessionId.current)
          ? { ...s, preview: (text || files[0]?.name || "Bild").slice(0, 80) }
          : s
      )
    );
  }, [input, pendingImages, pendingFiles, activeSessionId, agentId]);

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of Array.from(items)) {
      if (!item.type.startsWith("image/")) continue;
      const file = item.getAsFile();
      if (!file) continue;
      e.preventDefault();
      if (file.size > 5 * 1024 * 1024) {
        setMessages((prev) => [
          ...prev,
          { id: `img-err-${Date.now()}`, role: "error", content: "Bild zu groß (max. 5 MB).", timestamp: new Date().toISOString() },
        ]);
        continue;
      }
      const reader = new FileReader();
      reader.onload = () => {
        const result = String(reader.result || "");
        const base64 = result.split(",")[1];
        if (base64) {
          setPendingImages((prev) => [...prev, { media_type: file.type, data: base64 }].slice(0, 4));
        }
      };
      reader.readAsDataURL(file);
    }
  }, []);

  const stopGeneration = useCallback(() => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ action: "stop" }));
  }, []);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape" && slashOpen) {
      // Nicht das Feld leeren: der Nutzer wollte vielleicht wirklich einen Text
      // schreiben, der mit einem Schraegstrich beginnt.
      e.preventDefault();
      setInput(input + " ");
      return;
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (slashOpen) {
        // Enter bei offener Befehlsliste fuehrt den Befehl aus, statt „/zusa" als
        // Nachricht abzuschicken.
        const match = slashCommands.find((c) => c.name.startsWith(input.slice(1).toLowerCase()));
        if (match) {
          runSlash(match.name);
          return;
        }
      }
      sendMessage();
    }
  };

  const retryConnect = () => {
    reconnectAttempts.current = 0;
    setConnectionFailed(false);
    setMessages((prev) => prev.filter((m) => m.id !== "connection-failed" && m.id !== "agent-unavailable"));
    connect();
  };

  const createNewSession = () => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ text: "/reset" }));
      // Clear active session so the next "session" event from backend adopts the new one
      setActiveSessionId(null);
      currentWsSessionId.current = null;
      setMessages([{
        id: "new-session",
        role: "system",
        content: "Neuer Chat gestartet.",
        timestamp: new Date().toISOString(),
      }]);
      setTotalCost(0);
      setTotalTurns(0);
      setMessageCount(0);
    }
  };

  // Verzweigen / Zurueckspulen / Zusammenfassen (#538). Alle drei arbeiten auf
  // "die Nachrichten bis hierher" und liefern ein neues Gespraech, in das direkt
  // gewechselt wird — sonst muesste man es in der Liste suchen.
  const forkFrom = useCallback(async (messageId: string) => {
    if (!activeSessionId) return;
    try {
      const res = await api.forkChatSession(agentId, activeSessionId, messageId);
      chatToast.success("Abgezweigt", `${res.copied} Nachricht(en) übernommen.`);
      await refreshSessions();
      switchSession(res.session_id);
    } catch (e) {
      chatToast.error("Abzweigen fehlgeschlagen", e instanceof Error ? e.message : undefined);
    }
  }, [agentId, activeSessionId]);

  const rewindTo = useCallback(async (messageId: string) => {
    if (!activeSessionId) return;
    const ok = await chatConfirm({
      title: "Bis hierher zurückspulen?",
      message: "Alles danach wird aus diesem Gespräch entfernt. Eine Sicherung bleibt als eigenes Gespräch erhalten.",
      variant: "destructive",
      confirmLabel: "Zurückspulen",
    });
    if (!ok) return;
    try {
      const res = await api.rewindChatSession(agentId, activeSessionId, messageId);
      chatToast.success(
        `${res.removed} Nachricht(en) entfernt`,
        res.backup_session_id ? "Die Sicherung liegt als eigenes Gespräch in der Liste." : undefined,
      );
      await refreshSessions();
      window.location.reload();
    } catch (e) {
      chatToast.error("Zurückspulen fehlgeschlagen", e instanceof Error ? e.message : undefined);
    }
  }, [agentId, activeSessionId]);

  const summarizeToNew = useCallback(async () => {
    if (!activeSessionId) return;
    try {
      const res = await api.summarizeChatSession(agentId, activeSessionId);
      chatToast.success("Fortsetzung angelegt", `${res.summarized} Nachrichten verdichtet.`);
      await refreshSessions();
      switchSession(res.session_id);
    } catch (e) {
      chatToast.error("Nicht möglich", e instanceof Error ? e.message : undefined);
    }
  }, [agentId, activeSessionId]);

  const switchSession = (sessionId: string) => {
    if (sessionId === activeSessionId) return;
    setActiveSessionId(sessionId);
    // No need to notify backend - session_id is sent with every message
  };

  const deleteSession = useCallback(async (sessionId: string) => {
    try {
      await api.deleteChatSession(agentId, sessionId);
      setSessions((prev) => prev.filter((s) => s.id !== sessionId));
      // If we deleted the active session, switch to the next one or clear
      if (activeSessionId === sessionId) {
        const remaining = sessions.filter((s) => s.id !== sessionId);
        if (remaining.length > 0) {
          setActiveSessionId(remaining[0].id);
        } else {
          setActiveSessionId(null);
          setMessages([]);
        }
      }
    } catch {
      // Ignore delete errors
    }
  }, [agentId, activeSessionId, sessions]);

  const renameSession = useCallback(async (sessionId: string, title: string) => {
    setSessions((prev) => prev.map((s) => (s.id === sessionId ? { ...s, title: title || null } : s)));
    try {
      await api.updateChatSession(agentId, sessionId, { title });
    } catch {
      // keep optimistic value; a failed rename just isn't persisted
    }
  }, [agentId]);

  const togglePin = useCallback(async (session: { id: string; pinned?: boolean }) => {
    const pinned = !session.pinned;
    setSessions((prev) => {
      const next = prev.map((s) => (s.id === session.id ? { ...s, pinned } : s));
      return [...next].sort((a, b) => (a.pinned === b.pinned ? 0 : a.pinned ? -1 : 1));
    });
    try {
      await api.updateChatSession(agentId, session.id, { pinned });
    } catch {
      // ignore
    }
  }, [agentId]);

  const deleteAllSessions = useCallback(async () => {
    if (!window.confirm("Wirklich ALLE Chats dieses Agenten löschen? Das kann nicht rückgängig gemacht werden.")) return;
    try {
      await api.deleteAllChatSessions(agentId);
      // The server KEEPS pinned sessions — so keep their tabs too instead of
      // wiping the list (which only hid them until a refresh).
      setSessions((prev) => prev.filter((s) => s.pinned));
      setActiveSessionId(null);
      setMessages([]);
    } catch {
      // ignore
    }
  }, [agentId]);

  // Attach files without sending: images become pending images (like Ctrl+V paste),
  // everything else becomes a pending file chip. Upload happens in sendMessage.
  const addPendingFiles = useCallback((files: FileList | File[] | null) => {
    if (!files || files.length === 0) return;
    for (const file of Array.from(files)) {
      if (file.type.startsWith("image/")) {
        if (file.size > 5 * 1024 * 1024) {
          setMessages((prev) => [
            ...prev,
            { id: `img-err-${Date.now()}`, role: "error", content: `Bild zu groß (max. 5 MB): ${file.name}`, timestamp: new Date().toISOString() },
          ]);
          continue;
        }
        const reader = new FileReader();
        reader.onload = () => {
          const base64 = String(reader.result || "").split(",")[1];
          if (base64) {
            setPendingImages((prev) => [...prev, { media_type: file.type, data: base64 }].slice(0, 4));
          }
        };
        reader.readAsDataURL(file);
      } else {
        setPendingFiles((prev) =>
          prev.some((f) => f.name === file.name && f.size === file.size) ? prev : [...prev, file]
        );
      }
    }
    if (fileInputRef.current) fileInputRef.current.value = "";
    inputRef.current?.focus();
  }, []);

  // Thinking timer - counts up while waiting for first response
  useEffect(() => {
    if (!isWaiting) {
      setThinkingStartTime(null);
      setThinkingElapsed(0);
      return;
    }
    // Only start timer if no streaming message exists yet
    const hasStreaming = messages.some((m) => m.isStreaming);
    if (hasStreaming) {
      setThinkingStartTime(null);
      return;
    }
    if (!thinkingStartTime) {
      setThinkingStartTime(Date.now());
    }
    const interval = setInterval(() => {
      if (thinkingStartTime) {
        setThinkingElapsed(Math.floor((Date.now() - thinkingStartTime) / 1000));
      }
    }, 1000);
    return () => clearInterval(interval);
  }, [isWaiting, messages, thinkingStartTime]);

  // Poll for pending L3 approvals when agent is working
  useEffect(() => {
    if (!isWaiting) { setPendingApproval(null); return; }
    const poll = setInterval(async () => {
      try {
        const res = await fetch(`${getApiUrl()}/api/v1/approvals/pending`, { credentials: "include" });
        if (!res.ok) return;
        const data = await res.json();
        const agentApprovals = (data.approvals || []).filter((a: PendingApproval) => a.agent_id === agentId);
        setPendingApproval(agentApprovals[0] || null);
      } catch {}
    }, 3000);
    return () => clearInterval(poll);
  }, [isWaiting, agentId]);

  // Fenstergroesse fuer den Ring im Composer. Einmal je Gespraech geholt — sie
  // aendert sich nur, wenn jemand das Modell umstellt.
  const [modelWindow, setModelWindow] = useState<number | null>(null);
  useEffect(() => {
    if (!activeSessionId) { setModelWindow(null); return; }
    let alive = true;
    api.getChatContext(agentId, activeSessionId)
      .then((c) => { if (alive) setModelWindow(c.window); })
      .catch(() => {});
    return () => { alive = false; };
  }, [agentId, activeSessionId]);

  const estimatedTokens = messages.reduce((sum, m) => sum + Math.ceil((m.content?.length || 0) / 4), 0);
  // Die ECHTE Fenstergroesse des Modells, nicht mehr fest verdrahtete 200k. Der
  // Ring rechnete bisher jedes Modell gegen 200.000 — auf einem 1M-Modell zeigte
  // er dadurch das Fuenffache und stand im Widerspruch zur /compact-Tafel, die
  // die richtige Zahl schon holte. Ist das Fenster unbekannt, bleibt es beim
  // Rueckfallwert; die Tafel sagt dann ausdruecklich, dass es unbekannt ist.
  const contextLimit = modelWindow ?? 200000;
  const contextPercent = Math.min((estimatedTokens / contextLimit) * 100, 100);

  // Der Composer zeigt das Modell an. Einmal geholt, nicht bei jeder Nachricht:
  // es aendert sich nur, wenn jemand es in den Einstellungen umstellt.
  const [agentModel, setAgentModel] = useState("");
  useEffect(() => {
    api.getAgent(agentId).then((a) => setAgentModel(a.model || "")).catch(() => {});
  }, [agentId]);

  // Befehlsliste: oeffnet sich, sobald die Eingabe mit "/" beginnt und noch kein
  // Leerzeichen enthaelt — danach ist es Fliesstext, kein Befehl mehr.
  const slashOpen = /^\/[a-z]*$/i.test(input);

  // Die Ausstattung DIESES Agenten — je nach Laufzeit verschieden. Einmal
  // geholt: sie ändert sich nur, wenn jemand Skills oder MCP-Server umstellt.
  const [toolset, setToolset] = useState<api.AgentToolset | null>(null);
  useEffect(() => {
    api.getAgentToolset(agentId).then(setToolset).catch(() => setToolset(null));
  }, [agentId]);

  const slashCommands = toolset?.commands ?? FALLBACK_COMMANDS;

  // /tools und /compact öffnen eine Tafel statt einer Nachricht — beides sind
  // Auskünfte, keine Aufträge an den Agenten.
  const [panel, setPanel] = useState<"tools" | "compact" | null>(null);
  const [contextInfo, setContextInfo] = useState<api.ChatContextInfo | null>(null);
  const [compacting, setCompacting] = useState(false);

  const openCompact = useCallback(async () => {
    if (!activeSessionId) {
      chatToast.info("Kein Gespräch", "Schreib zuerst eine Nachricht.");
      return;
    }
    setPanel("compact");
    setContextInfo(null);
    try {
      setContextInfo(await api.getChatContext(agentId, activeSessionId));
    } catch (e) {
      chatToast.error("Kontext nicht abrufbar", e instanceof Error ? e.message : undefined);
      setPanel(null);
    }
  }, [agentId, activeSessionId, chatToast]);

  const doCompact = useCallback(async () => {
    if (!activeSessionId) return;
    setCompacting(true);
    try {
      const res = await api.compactChatSession(agentId, activeSessionId);
      chatToast.success(
        "Verdichtet",
        `${res.folded} Nachricht(en) zusammengefasst, ${res.kept} bleiben wörtlich.`,
      );
      setPanel(null);
      // Den Verlauf neu holen — die gefalteten Nachrichten sind jetzt markiert.
      setHistoryReloadKey((k) => k + 1);
    } catch (e) {
      chatToast.error("Verdichten fehlgeschlagen", e instanceof Error ? e.message : undefined);
    } finally {
      setCompacting(false);
    }
  }, [agentId, activeSessionId, chatToast]);

  const runSlash = useCallback((name: string) => {
    setInput("");
    const lastId = [...messages].reverse().find((m) => m.role !== "system")?.id;
    if (name === "planen") {
      // Ohne Text gibt es nichts zu planen — das Feld bleibt leer und der Nutzer
      // schreibt weiter, statt eine leere Nachricht abzuschicken.
      inputRef.current?.focus();
      chatToast.info("Planen", "Schreib den Auftrag und drück auf „Planen“.");
      return;
    }
    if (name === "compact") { void openCompact(); return; }
    if (name === "tools") { setPanel("tools"); return; }
    if (name === "zusammenfassen") { void summarizeToNew(); return; }
    if (name === "verzweigen" && lastId) { void forkFrom(lastId); return; }
    if (name === "zurueckspulen" && lastId) { void rewindTo(lastId); return; }
    // Befehle, die IN der Laufzeit stecken (Claude Codes eigenes /compact): wir
    // können sie von aussen nicht auslösen. Das zu verschweigen wäre schlimmer
    // als es zu sagen.
    const known = slashCommands.find((c) => c.name === name);
    if (known?.runtime_only) {
      chatToast.info(
        `/${name} gehört der Laufzeit`,
        "Der Befehl läuft in der CLI des Agenten und ist von hier nicht auslösbar. " +
          "Nimm /compact — das verdichtet den hier gespeicherten Verlauf.",
      );
      return;
    }
    chatToast.info("Geht gerade nicht", "Dafür braucht es mindestens eine Nachricht.");
  }, [messages, summarizeToNew, forkFrom, rewindTo, chatToast, openCompact,
      slashCommands]);

  const [inputFocused, setInputFocused] = useState(false);
  const [contextOpen, setContextOpen] = useState(false);
  const contextRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!contextOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!contextRef.current?.contains(e.target as Node)) setContextOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [contextOpen]);

  /* ─── Render ──────────────────────────────────────────────────────── */

  return (
    <div
      className={cn(
        "relative flex h-full min-h-0 overflow-hidden",
        !embedded && "rounded-xl border border-border bg-card/80 backdrop-blur-sm",
        isDragOver && "ring-2 ring-inset ring-primary/50"
      )}
      onDragEnter={(e) => {
        if (!e.dataTransfer.types.includes("Files")) return;
        e.preventDefault();
        dragDepthRef.current += 1;
        if (!isDragOver) setIsDragOver(true);
      }}
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes("Files")) e.preventDefault();
      }}
      onDragLeave={(e) => {
        if (!e.dataTransfer.types.includes("Files")) return;
        dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
        if (dragDepthRef.current === 0) setIsDragOver(false);
      }}
      onDrop={(e) => {
        if (!e.dataTransfer.types.includes("Files")) return;
        e.preventDefault();
        dragDepthRef.current = 0;
        setIsDragOver(false);
        if (e.dataTransfer.files && e.dataTransfer.files.length > 0) addPendingFiles(e.dataTransfer.files);
      }}
    >
      {isDragOver && (
        <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center bg-primary/5 backdrop-blur-[1px]">
          <div className="flex items-center gap-2 rounded-xl border-2 border-dashed border-primary/50 bg-card/90 px-5 py-3 text-sm font-medium text-primary shadow-lg">
            <Paperclip className="h-4 w-4" /> Dateien hier ablegen zum Anhängen
          </div>
        </div>
      )}
      {/* Conversation rail (shared with the Speech tab) — hidden in embedded (modal) mode */}
      {!embedded && railOpen && (
        <SessionRail
          className="border-r border-border bg-card/40"
          sessions={sessions.map((s) => ({ ...s, fallbackLabel: s.label }))}
          selectedId={activeSessionId}
          busyIds={busySessionIds}
          onSelect={switchSession}
          onNew={createNewSession}
          newDisabled={!isConnected}
          onPin={togglePin}
          onRename={renameSession}
          onDelete={deleteSession}
        />
      )}

      {/* Right column: toolbar + messages + input */}
      {/* relative: Anker fuer den „Zum Neuesten"-Knopf ueber dem Eingabefeld */}
      <div className="relative flex h-full min-w-0 flex-1 flex-col">
      {/* Toolbar — hidden in embedded (modal) mode */}
      {!embedded && (
      <div className="flex items-center gap-1 border-b border-border px-3 py-1.5 shrink-0 min-w-0">
        <button
          onClick={() => setRailOpen((o) => !o)}
          className="rounded-lg p-1.5 text-muted-foreground/60 hover:text-foreground hover:bg-foreground/[0.06] transition-all shrink-0"
          title={railOpen ? "Gesprächsliste ausblenden" : "Gesprächsliste einblenden"}
        >
          {railOpen ? <PanelLeftClose className="h-3.5 w-3.5" /> : <PanelLeft className="h-3.5 w-3.5" />}
        </button>
        <div className="flex-1 min-w-0" />

        {/* Right controls: summarize · overview toggle · font size · delete all · connection */}
        <div className="flex items-center gap-0.5 shrink-0 border-l border-border pl-2 ml-1">
          {/* Fortsetzung mit kurzem Stand (#538). Ein langes Gespraech wird traege und
              teuer; hier geht es in einem frischen weiter, ohne dass der Verlauf
              verloren geht — er bleibt unangetastet in der Liste. */}
          <button
            onClick={summarizeToNew}
            disabled={!activeSessionId || messages.length < 6}
            className="mr-0.5 rounded-lg p-1.5 text-muted-foreground/60 transition-all hover:bg-foreground/[0.06] hover:text-foreground disabled:opacity-30"
            title="In einem frischen Gespräch weiterreden — mit dem Stand von hier"
          >
            <SummarizeIcon className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={() => setViewMode((m) => (m === "chat" ? "overview" : "chat"))}
            className={cn(
              "rounded-lg p-1.5 transition-all mr-0.5",
              viewMode === "overview"
                ? "bg-primary/15 text-primary"
                : "text-muted-foreground/60 hover:text-foreground hover:bg-foreground/[0.06]"
            )}
            title={viewMode === "overview" ? "Zur Chat-Ansicht" : "Chat-Übersicht (Kacheln)"}
          >
            <LayoutGrid className="h-3.5 w-3.5" />
          </button>
          <button onClick={() => changeFontScale(-0.1)} disabled={fontScale <= 0.85} className="rounded px-1 py-0.5 text-[11px] font-semibold text-muted-foreground/60 hover:text-foreground hover:bg-foreground/[0.06] disabled:opacity-30" title="Schrift kleiner">A−</button>
          <Type className="h-3 w-3 text-muted-foreground/40" />
          <button onClick={() => changeFontScale(0.1)} disabled={fontScale >= 1.4} className="rounded px-1 py-0.5 text-[13px] font-semibold text-muted-foreground/60 hover:text-foreground hover:bg-foreground/[0.06] disabled:opacity-30" title="Schrift größer">A+</button>
          {sessions.length > 0 && (
            <button onClick={deleteAllSessions} className="ml-1 rounded p-1 text-muted-foreground/50 hover:text-red-400 hover:bg-red-500/10 transition-all" title="Alle Chats löschen">
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          )}
        </div>

        {/* Connection status */}
        <div className="flex items-center gap-1.5 ml-1 shrink-0 border-l border-border pl-2">
          <span className="relative flex h-2 w-2">
            {isConnected && (
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
            )}
            <span className={cn(
              "relative inline-flex h-2 w-2 rounded-full",
              isConnected ? "bg-emerald-500" : connectionFailed ? "bg-red-500" : "bg-yellow-500"
            )} />
          </span>
          <span className="text-[10px] text-muted-foreground/60">
            {isConnected ? "Online" : connectionFailed ? "Offline" : "..."}
          </span>
          {connectionFailed && (
            <button onClick={retryConnect} className="text-[10px] text-yellow-500 hover:text-yellow-400 transition-colors">
              <RotateCcw className="h-3 w-3" />
            </button>
          )}
        </div>
      </div>
      )}

      {/* Chat overview (tiles + live modal) */}
      {viewMode === "overview" && (
        <div className="flex-1 overflow-hidden">
          <ChatOverview
            agentId={agentId}
            onOpenSession={(sid) => { setActiveSessionId(sid); setViewMode("chat"); }}
          />
        </div>
      )}

      {/* Messages area — font size via zoom; file drag&drop is handled on the chat root */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className={cn(
          "relative flex-1 overflow-y-auto [scrollbar-gutter:stable] px-5 py-4 space-y-4 bg-background dark:bg-[#0d1117]",
          viewMode === "overview" && "hidden"
        )}
        style={{ zoom: fontScale }}
      >
        {messages.length === 0 && !connectionFailed && historyLoaded && (
          <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
            <Bot className="h-8 w-8 mb-2" />
            <p className="text-sm">Send a message to start chatting</p>
          </div>
        )}
        {messages.length === 0 && !connectionFailed && !historyLoaded && (
          <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
            <Loader2 className="h-6 w-6 animate-spin mb-2" />
            <p className="text-xs">Loading chat history...</p>
          </div>
        )}
        {messages.length === 0 && connectionFailed && (
          <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
            <WifiOff className="h-8 w-8 mb-2 text-red-400" />
            <p className="text-sm text-red-400">Agent is not reachable</p>
            <p className="text-xs mt-1">The container may be stopped or removed</p>
            <button onClick={retryConnect} className="mt-3 inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium bg-primary text-primary-foreground hover:bg-primary/90 transition-colors">
              <RotateCcw className="h-3 w-3" /> Retry Connection
            </button>
          </div>
        )}
        {messages.filter((msg, idx, arr) => arr.findIndex((m) => m.id === msg.id && m.role === msg.role) === idx).map((msg) => (
          <MessageRow key={`${msg.id}-${msg.role}`} message={msg} onFork={forkFrom} onRewind={rewindTo} />
        ))}
        {isWaiting && !messages.some((m) => m.isStreaming) && (
          <div className="flex items-start gap-3 pl-1 py-2">
            <div className="flex h-6 w-6 items-center justify-center rounded-md bg-violet-500/20 shrink-0">
              <Bot className="h-3.5 w-3.5 text-violet-400" />
            </div>
            <div className="flex flex-col gap-1 min-w-0">
              <div className="flex items-center gap-2">
                <div className="flex gap-1">
                  <span className="h-1.5 w-1.5 rounded-full bg-violet-400 animate-bounce" style={{ animationDelay: "0ms" }} />
                  <span className="h-1.5 w-1.5 rounded-full bg-violet-400 animate-bounce" style={{ animationDelay: "150ms" }} />
                  <span className="h-1.5 w-1.5 rounded-full bg-violet-400 animate-bounce" style={{ animationDelay: "300ms" }} />
                </div>
                <span className="text-xs text-muted-foreground">
                  {thinkingElapsed > 0 ? (
                    <>Thinking... <span className="tabular-nums text-muted-foreground/60">{thinkingElapsed}s</span></>
                  ) : (
                    "Thinking..."
                  )}
                </span>
                {thinkingElapsed > 30 && (
                  <span className="text-[10px] text-muted-foreground/60 italic">Complex task — this may take a while</span>
                )}
              </div>
              <LiveActivity agentId={agentId} />
            </div>
          </div>
        )}
        {/* Live-resume: agent is working on this conversation (turn started elsewhere) */}
        {liveElsewhere && !isWaiting && !messages.some((m) => m.isStreaming) && (
          <div className="flex items-start gap-3 pl-1 py-2">
            <div className="flex h-6 w-6 items-center justify-center rounded-md bg-amber-500/20 shrink-0">
              <Bot className="h-3.5 w-3.5 text-amber-400" />
            </div>
            <div className="flex flex-col gap-1 min-w-0">
              <div className="flex items-center gap-2">
                <div className="flex gap-1">
                  <span className="h-1.5 w-1.5 rounded-full bg-amber-400 animate-bounce" style={{ animationDelay: "0ms" }} />
                  <span className="h-1.5 w-1.5 rounded-full bg-amber-400 animate-bounce" style={{ animationDelay: "150ms" }} />
                  <span className="h-1.5 w-1.5 rounded-full bg-amber-400 animate-bounce" style={{ animationDelay: "300ms" }} />
                </div>
                <span className="text-xs text-muted-foreground">Agent arbeitet gerade an dieser Unterhaltung… die Antwort erscheint automatisch.</span>
              </div>
              <LiveActivity agentId={agentId} />
            </div>
          </div>
        )}
        {/* Delegierte Auftraege dieses Gespraechs. Sie stehen bewusst am Ende und
            nicht mitten im Verlauf: eine Kachel aktualisiert sich (wartet ->
            erledigt), eine Chatnachricht kann das nicht. */}
        {Object.keys(taskCards).length > 0 && viewMode !== "overview" && (
          <div className="mx-auto w-full max-w-3xl space-y-2 px-4 pb-2">
            {Object.values(taskCards)
              .sort((a, b) => a.at - b.at)
              .map((card) => {
                const laeuft = card.phase !== "done";
                const gescheitert = card.status === "failed";
                return (
                  <div
                    key={card.task_id}
                    className={`rounded-lg border px-3 py-2 text-sm transition-colors ${
                      laeuft
                        ? "border-amber-500/40 bg-amber-500/5"
                        : gescheitert
                          ? "border-destructive/40 bg-destructive/5"
                          : "border-emerald-600/40 bg-emerald-600/5"
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      {laeuft ? (
                        <Loader2 className="h-4 w-4 shrink-0 animate-spin text-amber-500" />
                      ) : gescheitert ? (
                        <XCircle className="h-4 w-4 shrink-0 text-destructive" />
                      ) : (
                        <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />
                      )}
                      <span className="min-w-0 flex-1 truncate font-medium">{card.title}</span>
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {card.assigned_agent_name}
                      </span>
                    </div>
                    <div className="mt-1 flex items-center gap-3 pl-6 text-xs text-muted-foreground">
                      <span>
                        {card.kind === "message"
                          ? laeuft
                            ? "gesendet, wartet auf Antwort"
                            : "beantwortet"
                          : laeuft
                            ? "in Arbeit"
                            : gescheitert
                              ? "fehlgeschlagen"
                              : "abgeschlossen"}
                      </span>
                      {card.duration_ms ? <span>{Math.round(card.duration_ms / 1000)} s</span> : null}
                      {card.kind === "message" ? null : (
                        <button
                          type="button"
                          onClick={() => openCardDetail(card)}
                          className="ml-auto underline-offset-2 hover:underline"
                        >
                          Details
                        </button>
                      )}
                    </div>
                    {!laeuft && card.result_preview ? (
                      <p className="mt-1 line-clamp-2 pl-6 text-xs text-muted-foreground">
                        {card.result_preview}
                      </p>
                    ) : null}
                  </div>
                );
              })}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Wer hochgescrollt hat, kommt mit einem Klick zurueck. Ohne das bleibt
          man in einem langen Gespraech oben stehen und muesste sich per Hand bis
          ans Ende arbeiten, um dem Strom wieder zu folgen. */}
      {showJumpToLatest && viewMode !== "overview" && (
        <button
          onClick={jumpToLatest}
          title="Zum Neuesten springen"
          className="absolute bottom-28 left-1/2 z-20 -translate-x-1/2 inline-flex items-center gap-1.5 rounded-full border border-border bg-card/95 px-3 py-1.5 text-xs font-medium shadow-lg backdrop-blur transition-colors hover:bg-accent"
        >
          <ArrowDown className="h-3.5 w-3.5" />
          Zum Neuesten
        </button>
      )}

      {/* Einzelheiten einer Auftrags-Kachel — im Fenster, damit das Gespraech
          stehen bleibt. */}
      {cardDetail && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
          onClick={() => setCardDetail(null)}
        >
          <div
            className="max-h-[80dvh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-border bg-card p-5 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start gap-3">
              <h3 className="min-w-0 flex-1 text-sm font-semibold">{cardDetail.title}</h3>
              <button
                type="button"
                onClick={() => setCardDetail(null)}
                className="shrink-0 rounded p-1 text-muted-foreground hover:bg-accent"
                aria-label="Schliessen"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
              <dt className="text-muted-foreground">Bearbeiter</dt>
              <dd>{cardDetail.assigned_agent_name || "—"}</dd>
              <dt className="text-muted-foreground">Stand</dt>
              <dd>
                {cardDetail.phase === "done"
                  ? cardDetail.status === "failed" ? "fehlgeschlagen" : "abgeschlossen"
                  : "in Arbeit"}
              </dd>
              <dt className="text-muted-foreground">Kennung</dt>
              <dd className="font-mono">{cardDetail.task_id}</dd>
              {cardDetail.duration_ms ? (
                <>
                  <dt className="text-muted-foreground">Dauer</dt>
                  <dd>{Math.round(cardDetail.duration_ms / 1000)} s</dd>
                </>
              ) : null}
            </dl>

            {cardDetailFull?.prompt ? (
              <>
                <h4 className="mt-4 text-xs font-semibold text-muted-foreground">Auftrag</h4>
                <pre className="mt-1 max-h-48 overflow-y-auto whitespace-pre-wrap rounded-lg bg-foreground/[0.04] p-3 text-xs">
                  {String(cardDetailFull.prompt)}
                </pre>
              </>
            ) : null}

            {(cardDetailFull?.result || cardDetail.result_preview) ? (
              <>
                <h4 className="mt-4 text-xs font-semibold text-muted-foreground">Ergebnis</h4>
                <div className="mt-1 max-h-72 overflow-y-auto rounded-lg bg-foreground/[0.04] p-3 text-xs">
                  <MarkdownContent content={String(cardDetailFull?.result || cardDetail.result_preview)} />
                </div>
              </>
            ) : cardDetail.phase !== "done" ? (
              <p className="mt-4 text-xs text-muted-foreground">
                Läuft noch — das Ergebnis erscheint hier, sobald es vorliegt.
              </p>
            ) : null}

            <a
              href={`/tasks/${cardDetail.task_id}`}
              className="mt-4 inline-block text-xs underline-offset-2 hover:underline"
            >
              Vollständige Aufgabenseite öffnen
            </a>
          </div>
        </div>
      )}

      {/* L3 Approval Request Banner */}
      {/* Tafeln fuer /tools und /compact. Beides sind Auskuenfte, keine Auftraege
          an den Agenten — deshalb ein Fenster und keine Nachricht im Verlauf. */}
      {panel && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
          onClick={() => setPanel(null)}
        >
          <div
            className="max-h-[80dvh] w-full max-w-lg overflow-y-auto rounded-2xl border border-border bg-card p-5 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            {panel === "compact" ? (
              <>
                <h3 className="text-sm font-semibold">Kontext</h3>
                {!contextInfo ? (
                  <p className="mt-3 text-xs text-muted-foreground">Wird ermittelt…</p>
                ) : (
                  <>
                    {contextInfo.percent !== null && contextInfo.window !== null ? (
                      <div className="mt-3 flex items-center gap-3">
                        <div className="h-2 flex-1 overflow-hidden rounded-full bg-foreground/[0.08]">
                          <div
                            className={cn(
                              "h-2 rounded-full transition-all",
                              contextInfo.percent < 50 ? "bg-emerald-500"
                                : contextInfo.percent < 80 ? "bg-amber-500" : "bg-red-500",
                            )}
                            style={{ width: `${Math.max(2, contextInfo.percent)}%` }}
                          />
                        </div>
                        <span className="w-14 shrink-0 text-right font-mono text-xs tabular-nums">
                          {contextInfo.percent}%
                        </span>
                      </div>
                    ) : (
                      // Lieber ehrlich als falsch: eine erfundene Fenstergroesse
                      // verspricht Luft, die es vielleicht nicht gibt.
                      <p className="mt-3 rounded-lg border border-amber-500/20 bg-amber-500/[0.06] p-2.5 text-[11px] text-amber-700 dark:text-amber-300">
                        Die Fenstergröße von <b>{contextInfo.model || "diesem Modell"}</b> ist
                        hier nicht hinterlegt — der Anteil lässt sich nicht ausrechnen.
                        Verdichten geht trotzdem.
                      </p>
                    )}
                    <dl className="mt-3 space-y-1 text-[11px] text-muted-foreground">
                      <div className="flex justify-between">
                        <dt>Geschätzt belegt</dt>
                        <dd className="tabular-nums text-foreground/80">
                          {contextInfo.used_estimate.toLocaleString()}
                          {contextInfo.window !== null
                            ? ` von ${contextInfo.window.toLocaleString()} Token`
                            : " Token (Grenze unbekannt)"}
                        </dd>
                      </div>
                      <div className="flex justify-between">
                        <dt>Nachrichten</dt>
                        <dd className="tabular-nums text-foreground/80">{contextInfo.messages}</dd>
                      </div>
                      {contextInfo.compacted > 0 && (
                        <div className="flex justify-between">
                          <dt>bereits verdichtet</dt>
                          <dd className="tabular-nums text-foreground/80">{contextInfo.compacted}</dd>
                        </div>
                      )}
                      {contextInfo.model && (
                        <div className="flex justify-between">
                          <dt>Modell</dt>
                          <dd className="font-mono text-foreground/80">{contextInfo.model}</dd>
                        </div>
                      )}
                    </dl>
                    <p className="mt-3 text-[10px] leading-relaxed text-muted-foreground/60">
                      Die Belegung ist <b>geschätzt</b> (Zeichen ÷ 4). Genau ginge nur mit dem
                      Tokenisierer des jeweiligen Modells — eine Zahl, die genauer aussieht
                      als sie ist, wäre schlechter als eine gerundete.
                    </p>
                    <button
                      onClick={doCompact}
                      disabled={!contextInfo.can_compact || compacting}
                      className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground disabled:opacity-40"
                    >
                      {compacting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                      Verlauf verdichten
                    </button>
                    <p className="mt-2 text-[10px] leading-relaxed text-muted-foreground/60">
                      {contextInfo.can_compact
                        ? `Die letzten ${contextInfo.keeps_verbatim} Nachrichten bleiben wörtlich — die jüngste Werkzeug-Ein- und -Ausgabe ist zusammengefasst wertlos. Ältere werden markiert, nicht gelöscht.`
                        : "Noch zu kurz zum Verdichten."}
                    </p>
                  </>
                )}
              </>
            ) : (
              <>
                <h3 className="text-sm font-semibold">
                  Werkzeuge dieses Agenten
                  {toolset && (
                    <span className="ml-2 font-normal text-[11px] text-muted-foreground">
                      {toolset.mode === "claude_code" ? "Claude Code"
                        : toolset.mode === "codex_cli" ? "Codex" : "Eigenes Modell"}
                      {" · "}{toolset.total}
                    </span>
                  )}
                </h3>
                {!toolset ? (
                  <p className="mt-3 text-xs text-muted-foreground">Wird ermittelt…</p>
                ) : (
                  <div className="mt-3 space-y-3">
                    {toolset.groups.map((g) => (
                      <div key={g.key}>
                        <div className="text-[11px] font-medium">
                          {g.label}
                          {g.note && (
                            <span className="ml-1.5 font-normal text-muted-foreground/50">
                              — {g.note}
                            </span>
                          )}
                        </div>
                        <div className="mt-1 flex flex-wrap gap-1">
                          {g.tools.map((t) => (
                            <span
                              key={t}
                              className="rounded border border-foreground/[0.08] bg-foreground/[0.03] px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground"
                            >
                              {t}
                            </span>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}
            <button
              onClick={() => setPanel(null)}
              className="mt-4 w-full rounded-xl border border-foreground/[0.08] px-4 py-2 text-sm text-muted-foreground hover:bg-foreground/[0.06]"
            >
              Schließen
            </button>
          </div>
        </div>
      )}

      {pendingApproval && (
        <div className="mx-4 mb-3 rounded-xl border border-amber-500/20 bg-amber-500/10 p-4">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-amber-300">Freigabe erforderlich</p>
              <p className="mt-0.5 text-xs text-amber-400/80">{pendingApproval.tool}</p>
              {pendingApproval.reasoning && (
                <p className="mt-1 text-xs text-muted-foreground">{pendingApproval.reasoning}</p>
              )}
            </div>
            <div className="flex gap-2 shrink-0">
              <button
                onClick={async () => {
                  await fetch(`${getApiUrl()}/api/v1/approvals/${pendingApproval.approval_id}/approve`, {
                    method: "POST", credentials: "include"
                  });
                  setPendingApproval(null);
                }}
                className="rounded-lg bg-emerald-500/20 px-3 py-1.5 text-xs font-medium text-emerald-400 hover:bg-emerald-500/30"
              >
                Freigeben
              </button>
              <button
                onClick={async () => {
                  await fetch(`${getApiUrl()}/api/v1/approvals/${pendingApproval.approval_id}/deny`, {
                    method: "POST", credentials: "include",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ decision: "deny", reason: "Vom Nutzer abgelehnt" })
                  });
                  setPendingApproval(null);
                }}
                className="rounded-lg bg-red-500/20 px-3 py-1.5 text-xs font-medium text-red-400 hover:bg-red-500/30"
              >
                Ablehnen
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Input area */}
      <div className={cn("border-t border-border p-4", viewMode === "overview" && "hidden")}>
        <input ref={fileInputRef} type="file" multiple className="hidden" onChange={(e) => addPendingFiles(e.target.files)} />
        {pendingFiles.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-2.5">
            {pendingFiles.map((file, i) => (
              <div key={`${file.name}-${i}`} className="group inline-flex items-center gap-1.5 rounded-lg border border-border bg-background/80 px-2.5 py-1.5 text-xs">
                <FileText className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                <span className="max-w-[180px] truncate">{file.name}</span>
                <span className="text-[10px] text-muted-foreground/60 tabular-nums">{formatBytes(file.size)}</span>
                <button
                  onClick={() => setPendingFiles((prev) => prev.filter((_, j) => j !== i))}
                  className="ml-0.5 rounded-full p-0.5 text-muted-foreground hover:text-foreground hover:bg-foreground/10"
                  title="Datei entfernen"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            ))}
          </div>
        )}
        {pendingImages.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-2.5">
            {pendingImages.map((img, i) => (
              <div key={i} className="group relative h-16 w-16 overflow-hidden rounded-lg border border-border">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={`data:${img.media_type};base64,${img.data}`}
                  alt={`pasted ${i + 1}`}
                  className="h-full w-full object-cover"
                />
                <button
                  onClick={() => setPendingImages((prev) => prev.filter((_, j) => j !== i))}
                  className="absolute right-0.5 top-0.5 rounded-full bg-black/70 p-0.5 text-white opacity-0 transition-opacity group-hover:opacity-100"
                  title="Bild entfernen"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            ))}
          </div>
        )}
        {/* Composer im Claude-Code-Zuschnitt (#538): Eingabe oben, Bedienung in
            einer Fusszeile darunter. Vorher standen sechs Knoepfe NEBEN dem
            Eingabefeld — auf schmalen Schirmen blieb fuer den Text eine Spalte, und
            die Kontextanzeige lag als eigener Streifen darunter, ohne Bezug. */}
        <div
          className={cn(
            "rounded-2xl border bg-background/80 transition-all",
            inputFocused ? "border-primary/50 ring-1 ring-primary/20" : "border-border",
            !isConnected && "opacity-40",
          )}
        >
          <div className="relative">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              onPaste={handlePaste}
              onFocus={() => setInputFocused(true)}
              onBlur={() => setInputFocused(false)}
              placeholder={
                connectionFailed
                  ? "Agent not connected"
                  : isWaiting
                  ? "Agent arbeitet… (du kannst trotzdem schreiben)"
                  : "Nachricht… — / für Befehle, Bild mit Strg+V"
              }
              disabled={!isConnected}
              className="max-h-48 w-full resize-none overflow-y-auto bg-transparent px-4 pt-3 pb-1.5 text-sm outline-none transition-all placeholder:text-muted-foreground/30 disabled:opacity-40"
              rows={1}
            />

            {/* Befehlsliste. Sie fuehrt AUSSCHLIESSLICH auf Dinge, die es schon
                gibt — ein Hinweis auf Befehle, die nirgends hinfuehren, waere
                schlimmer als gar keiner. */}
            {slashOpen && (
              <div className="absolute bottom-full left-3 z-50 mb-2 w-72 overflow-hidden rounded-xl border border-border bg-card shadow-xl shadow-black/20">
                <p className="px-3 pb-1.5 pt-2.5 text-[10px] font-medium uppercase tracking-wider text-muted-foreground/60">
                  Befehle
                </p>
                {slashCommands.filter((c) =>
                  c.name.startsWith(input.slice(1).toLowerCase()),
                ).map((c) => (
                  <button
                    key={c.name}
                    onMouseDown={(e) => { e.preventDefault(); runSlash(c.name); }}
                    className="flex w-full items-start gap-2.5 px-3 py-2 text-left transition-colors hover:bg-foreground/[0.06]"
                  >
                    <span className="font-mono text-xs text-primary">/{c.name}</span>
                    <span className="text-[11px] text-muted-foreground/70">{c.hint}</span>
                  </button>
                ))}
                {slashCommands.every((c) => !c.name.startsWith(input.slice(1).toLowerCase())) && (
                  <p className="px-3 pb-2.5 text-[11px] text-muted-foreground/50">
                    Kein Befehl mit diesem Namen — Enter schickt den Text normal ab.
                  </p>
                )}
              </div>
            )}
          </div>

          {/* Fusszeile */}
          <div className="flex flex-wrap items-center gap-1.5 border-t border-border/60 px-2 py-1.5">
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={!isConnected || isUploading}
              className="flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground/70 transition-all hover:bg-foreground/[0.06] hover:text-foreground disabled:opacity-40"
              title="Dateien anhängen"
            >
              {isUploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Paperclip className="h-4 w-4" />}
            </button>
            <button
              onClick={() => {
                voiceSession.startSession({
                  agentId,
                  agentName: agentId,
                  resumeSessionId: activeSessionId ?? undefined,
                  onEnd: () => { void refreshSessions(); },
                });
              }}
              disabled={!isConnected}
              className="flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground/70 transition-all hover:bg-foreground/[0.06] hover:text-foreground disabled:opacity-40"
              title="Live-Sprachsession starten"
            >
              <Mic className="h-4 w-4" />
            </button>

            <div className="relative" ref={reasoningRef}>
              <button
                onClick={() => setReasoningOpen((o) => !o)}
                disabled={!isConnected}
                title={`Denktiefe: ${REASONING_OPTIONS.find((o) => o.value === reasoning)?.label}`}
                className={cn(
                  "flex h-8 items-center gap-1.5 rounded-lg px-2 text-[11px] transition-all disabled:opacity-40",
                  reasoning
                    ? "bg-violet-500/10 text-violet-300 hover:bg-violet-500/20"
                    : "text-muted-foreground/70 hover:bg-foreground/[0.06] hover:text-foreground",
                )}
              >
                <Brain className="h-4 w-4" />
                <span className="font-medium">
                  {reasoning
                    ? REASONING_OPTIONS.find((o) => o.value === reasoning)?.short
                    : "Auto"}
                </span>
              </button>
              {reasoningOpen && (
                <div className="absolute bottom-full left-0 z-50 mb-2 w-52 overflow-hidden rounded-xl border border-border bg-card shadow-xl shadow-black/20">
                  <p className="px-3 pb-1.5 pt-2.5 text-[10px] font-medium uppercase tracking-wider text-muted-foreground/60">
                    Wie gründlich denken?
                  </p>
                  {REASONING_OPTIONS.map((opt) => (
                    <button
                      key={opt.value || "default"}
                      onClick={() => { setReasoning(opt.value); setReasoningOpen(false); }}
                      className={cn(
                        "flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-xs transition-colors hover:bg-foreground/[0.06]",
                        reasoning === opt.value ? "text-violet-300" : "text-foreground/80",
                      )}
                    >
                      <span>{opt.label}</span>
                      {reasoning === opt.value && <Check className="h-3.5 w-3.5 shrink-0" />}
                    </button>
                  ))}
                </div>
              )}
            </div>

            <div className="ml-auto flex items-center gap-1.5">
              {agentModel && (
                <span
                  className="hidden max-w-[10rem] truncate font-mono text-[10px] text-muted-foreground/50 sm:inline"
                  title={agentModel}
                >
                  {agentModel}
                </span>
              )}

              {/* Kontextring: ersetzt den frueheren Streifen unter dem Composer.
                  Dort stand er ohne Bezug zur Eingabe; hier sitzt er neben dem
                  Absenden, also da, wo die Entscheidung faellt. */}
              <div className="relative" ref={contextRef}>
                <button
                  onClick={() => setContextOpen((o) => !o)}
                  title="Kontext: belegter Anteil des Gesprächsfensters"
                  className="flex h-8 items-center gap-1.5 rounded-lg px-1.5 text-[10px] tabular-nums text-muted-foreground/70 transition-all hover:bg-foreground/[0.06] hover:text-foreground"
                >
                  <ContextRing percent={contextPercent} />
                  <span>{contextPercent.toFixed(0)}%</span>
                </button>
                {contextOpen && (
                  <div className="absolute bottom-full right-0 z-50 mb-2 w-64 overflow-hidden rounded-xl border border-border bg-card p-3 shadow-xl shadow-black/20">
                    <div className="space-y-1 text-[11px] text-muted-foreground">
                      <div className="flex justify-between">
                        <span>Zeichen als Tokens geschätzt</span>
                        <span className="tabular-nums text-foreground/80">
                          {estimatedTokens.toLocaleString()} / {(contextLimit / 1000).toFixed(0)}k
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span>Nachrichten</span>
                        <span className="tabular-nums text-foreground/80">{messageCount}</span>
                      </div>
                      {totalTurns > 0 && (
                        <div className="flex justify-between">
                          <span>Züge</span>
                          <span className="tabular-nums text-foreground/80">{totalTurns}</span>
                        </div>
                      )}
                      {totalCost > 0 && (
                        <div className="flex justify-between">
                          <span>Kosten</span>
                          <span className="tabular-nums text-foreground/80">{formatMoney(totalCost)}</span>
                        </div>
                      )}
                    </div>
                    <button
                      onClick={() => { setContextOpen(false); void summarizeToNew(); }}
                      disabled={!activeSessionId || messages.length < 6}
                      className="mt-2.5 w-full rounded-lg border border-foreground/[0.08] px-2 py-1.5 text-[11px] transition-colors hover:bg-foreground/[0.06] disabled:opacity-30"
                    >
                      In frischem Gespräch weiterreden
                    </button>
                    <p className="mt-1.5 text-[10px] text-muted-foreground/50">
                      Der Verlauf bleibt unangetastet — die Fortsetzung startet mit
                      einem kurzen Stand statt der vollen Last.
                    </p>
                    {/* Erklaert die haeufigste Rueckfrage: hier stehen 7%, und der
                        Agent verdichtet trotzdem. Beides stimmt — es sind zwei
                        verschiedene Dinge. */}
                    <p className="mt-1.5 border-t border-border/60 pt-1.5 text-[10px] text-muted-foreground/50">
                      Gezählt wird das sichtbare Gespräch. Der Agent trägt zusätzlich
                      seine Anweisungen und jede Werkzeugausgabe mit — deshalb kann er
                      verdichten, während hier noch viel Platz steht.
                    </p>
                  </div>
                )}
              </div>

              {isWaiting ? (
                <button
                  onClick={stopGeneration}
                  className="flex h-9 w-9 items-center justify-center rounded-xl bg-red-500/90 text-white shadow-lg shadow-red-500/20 transition-all hover:bg-red-500"
                  title="Stop"
                >
                  <Square className="h-4 w-4 fill-current" />
                </button>
              ) : (
                <>
                  <button
                    onClick={() => sendMessage(true)}
                    disabled={!isConnected || isUploading || !input.trim()}
                    className="flex h-9 items-center gap-1.5 rounded-xl border border-amber-500/30 bg-amber-500/10 px-2.5 text-[11px] font-medium text-amber-300 transition-all hover:bg-amber-500/20 disabled:opacity-40"
                    title="Nur planen — der Agent beschreibt die Schritte, führt aber nichts aus"
                  >
                    <ListChecks className="h-4 w-4" /> Planen
                  </button>
                  <button
                    onClick={() => sendMessage()}
                    disabled={!isConnected || isUploading || (!input.trim() && pendingImages.length === 0 && pendingFiles.length === 0)}
                    className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary text-primary-foreground shadow-lg shadow-primary/20 transition-all hover:bg-primary/90 disabled:opacity-40 disabled:shadow-none"
                  >
                    {isUploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* end right column */}
      </div>
    </div>
  );
}

/* ─── Message Row ───────────────────────────────────────────────────── */

function MessageRow({
  message,
  onFork,
  onRewind,
}: {
  message: ChatMessage;
  onFork?: (messageId: string) => void;
  onRewind?: (messageId: string) => void;
}) {
  if (message.role === "system") {
    if (message.isQueued) {
      return (
        <div className="text-center py-1">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-500/10 border border-amber-500/20 px-3 py-1 text-[10px] text-amber-500/80">
            <Clock className="h-3 w-3" />
            Message received — steering current agent turn
          </span>
        </div>
      );
    }
    return (
      <div className="text-center py-1">
        <span className="inline-flex items-center gap-1.5 rounded-full bg-muted/60 border border-border px-3 py-1 text-[10px] text-muted-foreground">
          {message.content}
        </span>
      </div>
    );
  }

  if (message.role === "error") {
    return (
      <div className="flex items-start gap-2 pl-1">
        <XCircle className="h-4 w-4 text-red-400 shrink-0 mt-0.5" />
        <span className="text-sm text-red-400">{message.content}</span>
      </div>
    );
  }

  const actions = (
    <MessageActions messageId={message.id} onFork={onFork} onRewind={onRewind} />
  );

  if (message.role === "user") {
    return (
      <UserMessage
        content={message.content}
        images={message.images}
        files={message.files}
        timestamp={message.timestamp}
        actions={actions}
      />
    );
  }

  // Assistant message - render as timeline of steps
  return <AssistantResponse message={message} actions={actions} />;
}

/** Aktionen an einer einzelnen Nachricht (#538).

    Erscheint erst beim Darüberfahren: dauerhaft sichtbare Knöpfe an jeder Nachricht
    machen einen langen Verlauf unruhig. Zurückspulen ist rot, weil es als einziges
    etwas entfernt — auch wenn eine Sicherung angelegt wird. */
function MessageActions({
  messageId,
  onFork,
  onRewind,
}: {
  messageId?: string;
  onFork?: (id: string) => void;
  onRewind?: (id: string) => void;
}) {
  if (!messageId || (!onFork && !onRewind)) return null;
  return (
    <span className="ml-1 inline-flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
      {onFork && (
        <button
          onClick={() => onFork(messageId)}
          title="Ab hier in einem neuen Gespräch weiterreden — dieses bleibt erhalten"
          className="rounded p-1 text-muted-foreground/60 hover:bg-foreground/[0.06] hover:text-foreground"
        >
          <GitBranch className="h-3 w-3" />
        </button>
      )}
      {onRewind && (
        <button
          onClick={() => onRewind(messageId)}
          title="Bis hierher zurückspulen — alles danach wird entfernt (Sicherung bleibt)"
          className="rounded p-1 text-muted-foreground/60 hover:bg-red-500/10 hover:text-red-400"
        >
          <Undo2 className="h-3 w-3" />
        </button>
      )}
    </span>
  );
}

/** Subtle message timestamp — HH:MM, full date/time in the title tooltip. */
function MsgTime({ ts }: { ts?: string }) {
  if (!ts) return null;
  const d = new Date(ts);
  if (isNaN(d.getTime())) return null;
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return (
    <time dateTime={ts} title={d.toLocaleString()} className="text-[10px] text-muted-foreground/50 tabular-nums">
      {time}
    </time>
  );
}

/* ─── User Message ──────────────────────────────────────────────────── */

function UserMessage({ content, images, files, timestamp, actions }: { content: string; images?: ChatImage[]; files?: ChatFile[]; timestamp?: string; actions?: React.ReactNode }) {
  const { user } = useAuthStore();
  return (
    <div className="group flex items-start gap-3 pl-1">
      <UserAvatar name={user?.name || "Du"} className="h-6 w-6 rounded-md text-[10px] shrink-0 mt-0.5" />
      <div className="text-sm text-foreground leading-relaxed pt-0.5 space-y-2">
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-medium text-muted-foreground">Du</span>
          {actions}
          <MsgTime ts={timestamp} />
        </div>
        {images && images.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {images.map((img, i) => (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                key={i}
                src={`data:${img.media_type};base64,${img.data}`}
                alt={`Bild ${i + 1}`}
                className="max-h-48 rounded-lg border border-border object-contain"
              />
            ))}
          </div>
        )}
        {files && files.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {files.map((file, i) => (
              <span key={`${file.filename}-${i}`} className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-muted/40 px-2.5 py-1.5 text-xs" title={file.path}>
                <FileText className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                <span className="max-w-[200px] truncate">{file.filename}</span>
                {typeof file.size === "number" && (
                  <span className="text-[10px] text-muted-foreground/60 tabular-nums">{formatBytes(file.size)}</span>
                )}
              </span>
            ))}
          </div>
        )}
        {content && <div className="whitespace-pre-wrap break-words">{content}</div>}
      </div>
    </div>
  );
}

/* ─── Assistant Response (Claude CLI Style) ─────────────────────────── */

function AssistantResponse({ message, actions }: { message: ChatMessage; actions?: React.ReactNode }) {
  const steps = message.steps || [];
  const { simpleMode } = useSimpleMode();

  // If no steps at all (legacy), show as simple text
  if (steps.length === 0 && message.content) {
    return (
      <div className="group pl-1 space-y-2">
        <div className="flex items-center">
          <MsgTime ts={message.timestamp} />
          {actions}
        </div>
        <MarkdownContent content={message.content} />
        <PresentedImages images={message.images} />
        <PresentedFiles agentId={String(message.agentId || "")} files={message.files} />
        {message.meta && !simpleMode && <MetaBar meta={message.meta} />}
      </div>
    );
  }

  // Simple mode: only show text steps, hide tool calls
  const visibleSteps = simpleMode
    ? steps.filter((s) => s.type === "text")
    : steps;

  // In simple mode, if there are no text steps yet (only tool calls running), show a working indicator
  const hasRunningTools = simpleMode && steps.some((s) => s.type === "tool_call" && s.status === "running");
  const noVisibleContent = visibleSteps.length === 0;

  return (
    <div className="group space-y-2.5 pl-1">
      <div className="flex items-center">
        <MsgTime ts={message.timestamp} />
        {actions}
      </div>
      {simpleMode && hasRunningTools && noVisibleContent && (
        <div className="flex items-center gap-2 text-muted-foreground/60 text-xs py-1">
          <Loader2 className="h-3 w-3 animate-spin" />
          <span>Arbeitet...</span>
        </div>
      )}
      {(() => {
        // Group consecutive tool calls into one collapsible cluster (overlapping
        // bubbles, max 5 + "+N"); text segments stay inline between clusters.
        const groups: Array<
          | { kind: "text"; content: string; idx: number }
          | { kind: "tools"; steps: ToolStep[]; idx: number }
        > = [];
        visibleSteps.forEach((step, i) => {
          if (step.type === "tool_call") {
            const last = groups[groups.length - 1];
            if (last && last.kind === "tools") last.steps.push(step);
            else groups.push({ kind: "tools", steps: [step], idx: i });
          } else if (step.type === "text") {
            groups.push({ kind: "text", content: step.content, idx: i });
          }
        });
        return groups.map((g) =>
          g.kind === "text" ? (
            <div key={`text-${g.idx}`}>
              <MarkdownContent content={g.content} />
              {message.isStreaming && g.idx === visibleSteps.length - 1 && (
                <span className="inline-block w-1.5 h-4 bg-muted-foreground/50 animate-pulse ml-0.5 rounded-sm" />
              )}
            </div>
          ) : (
            <ToolCluster
              key={`tools-${g.idx}`}
              steps={g.steps}
              isStreaming={message.isStreaming && g.steps.some((s) => s.status === "running")}
            />
          )
        );
      })()}
      <PresentedImages images={message.images} />
      <PresentedFiles agentId={String(message.agentId || "")} files={message.files} />
      {message.meta && !message.isStreaming && !simpleMode && <MetaBar meta={message.meta} />}
    </div>
  );
}

/* ─── Presented Images (present_image tool output) ──────────────────── */

function PresentedImages({ images }: { images?: ChatImage[] }) {
  if (!images || images.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-2 pt-1">
      {images.map((img, i) => (
        <a
          key={i}
          href={`data:${img.media_type};base64,${img.data}`}
          target="_blank"
          rel="noopener noreferrer"
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={`data:${img.media_type};base64,${img.data}`}
            alt={`Generiertes Bild ${i + 1}`}
            className="max-h-96 rounded-lg border border-border object-contain cursor-zoom-in"
          />
        </a>
      ))}
    </div>
  );
}

function PresentedFiles({ agentId, files }: { agentId: string; files?: ChatFile[] }) {
  if (!files || files.length === 0) return null;

  const download = async (file: ChatFile) => {
    const url = `${getApiUrl()}/api/v1/agents/${agentId}/files/download?path=${encodeURIComponent(file.path)}`;
    const resp = await fetch(url, { credentials: "include" });
    if (!resp.ok) return;
    const blob = await resp.blob();
    const objectUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = objectUrl;
    a.download = file.filename || "download";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(objectUrl);
  };

  return (
    <div className="space-y-2 pt-1">
      {files.map((file, i) =>
        isAudioFile(file) ? (
          <AudioAttachment key={`${file.path}-${i}`} agentId={agentId} file={file} onDownload={() => download(file)} />
        ) : (
          <button
            key={`${file.path}-${i}`}
            type="button"
            onClick={() => download(file)}
            className="flex max-w-md items-center gap-3 rounded-lg border border-border bg-muted/35 px-3 py-2 text-left hover:bg-muted/55 transition-colors"
          >
            <Paperclip className="h-4 w-4 shrink-0 text-muted-foreground" />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium text-foreground">{file.filename}</span>
              <span className="block text-xs text-muted-foreground">
                {file.caption || file.media_type || "Attachment"}
                {file.size ? ` · ${Math.max(1, Math.round(file.size / 1024))} KB` : ""}
              </span>
            </span>
          </button>
        )
      )}
    </div>
  );
}

function isAudioFile(file: ChatFile) {
  if (file.media_type?.startsWith("audio/")) return true;
  return /\.(mp3|m4a|wav|ogg|opus|aac|flac)$/i.test(file.filename || file.path);
}

function AudioAttachment({ agentId, file, onDownload }: { agentId: string; file: ChatFile; onDownload: () => void }) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const ensureAudio = useCallback(async () => {
    if (objectUrl) return objectUrl;
    setLoading(true);
    setError(null);
    try {
      const url = `${getApiUrl()}/api/v1/agents/${agentId}/files/download?path=${encodeURIComponent(file.path)}`;
      const resp = await fetch(url, { credentials: "include" });
      if (!resp.ok) throw new Error(`Download failed (${resp.status})`);
      const blob = await resp.blob();
      const nextUrl = URL.createObjectURL(blob);
      setObjectUrl(nextUrl);
      return nextUrl;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Audio konnte nicht geladen werden");
      return null;
    } finally {
      setLoading(false);
    }
  }, [agentId, file.path, objectUrl]);

  useEffect(() => {
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [objectUrl]);

  const toggle = useCallback(async () => {
    const url = await ensureAudio();
    if (!url) return;
    const audio = audioRef.current;
    if (!audio) return;
    if (playing) {
      audio.pause();
      setPlaying(false);
      return;
    }
    try {
      await audio.play();
      setPlaying(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Audio konnte nicht abgespielt werden");
    }
  }, [ensureAudio, playing]);

  const progress = duration > 0 ? Math.min(currentTime / duration, 1) : 0;

  return (
    <div className="max-w-md rounded-2xl border border-blue-500/15 bg-blue-500/10 px-3 py-3">
      {objectUrl && (
        <audio
          ref={audioRef}
          src={objectUrl}
          preload="metadata"
          onLoadedMetadata={(e) => setDuration(e.currentTarget.duration || 0)}
          onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime || 0)}
          onEnded={() => {
            setPlaying(false);
            setCurrentTime(duration);
          }}
          className="hidden"
        />
      )}
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={toggle}
          disabled={loading}
          className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-blue-500 text-white transition hover:bg-blue-400 disabled:opacity-70"
          aria-label={playing ? "Pause audio" : "Play audio"}
        >
          {loading ? <Loader2 className="h-5 w-5 animate-spin" /> : playing ? <Pause className="h-5 w-5" /> : <Play className="h-5 w-5 translate-x-0.5" />}
        </button>
        <div className="min-w-0 flex-1">
          <div className="mb-1 flex items-center gap-2">
            <span className="truncate text-sm font-medium">{file.caption || file.filename}</span>
            <button
              type="button"
              onClick={onDownload}
              className="shrink-0 rounded-md p-1 text-muted-foreground transition hover:bg-background/60 hover:text-foreground"
              aria-label="Download audio"
            >
              <Download className="h-3.5 w-3.5" />
            </button>
          </div>
          <Waveform seed={file.path} progress={progress} />
          <div className="mt-1 flex justify-between text-[11px] tabular-nums text-muted-foreground">
            <span>{formatAudioTime(currentTime)}</span>
            <span>{duration > 0 ? formatAudioTime(duration) : file.size ? `${Math.max(1, Math.round(file.size / 1024))} KB` : "Voice"}</span>
          </div>
        </div>
      </div>
      {error && <p className="mt-2 text-xs text-red-400">{error}</p>}
    </div>
  );
}

function Waveform({ seed, progress }: { seed: string; progress: number }) {
  const chars = seed.length ? Array.from(seed).map((ch) => ch.charCodeAt(0)) : [17];
  const bars = Array.from({ length: 32 }, (_, i) => 22 + ((chars[i % chars.length] + i * 29) % 58));
  const active = Math.floor(progress * bars.length);
  return (
    <div className="flex h-7 items-center gap-1">
      {bars.map((height, i) => (
        <span
          key={i}
          className={cn("w-1 rounded-full transition-colors", i <= active ? "bg-blue-400" : "bg-muted-foreground/25")}
          style={{ height: `${height}%` }}
        />
      ))}
    </div>
  );
}

function formatAudioTime(seconds: number) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "0:00";
  const rounded = Math.round(seconds);
  return `${Math.floor(rounded / 60)}:${String(rounded % 60).padStart(2, "0")}`;
}

/* ─── Tool Call Block (Claude CLI Style) ────────────────────────────── */

function ToolCluster({ steps }: { steps: ToolStep[]; isStreaming?: boolean }) {
  // Stays compact (overlapping bubbles) at all times — even while the agent is
  // working — so it doesn't pop open and resize on every tool call. The running
  // tool's bubble shows a live spinner; click to expand for details.
  const [expanded, setExpanded] = useState(false);
  const anyRunning = steps.some((s) => s.status === "running");

  if (expanded) {
    return (
      <div className="space-y-1.5">
        <button
          onClick={() => setExpanded(false)}
          className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
        >
          <ChevronRight className="h-3 w-3 rotate-90" /> {steps.length} Tool-Aufrufe einklappen
        </button>
        {steps.map((s) => (
          <ToolCallBlock key={s.id} step={s} />
        ))}
      </div>
    );
  }

  const MAX = 5;
  const shown = steps.slice(0, MAX);
  const extra = steps.length - shown.length;
  return (
    <button
      onClick={() => setExpanded(true)}
      className="group flex items-center gap-2.5 rounded-full py-0.5 pr-2 transition-colors hover:bg-foreground/[0.04]"
      title="Tool-Aufrufe ansehen"
    >
      <div className="flex items-center">
        {shown.map((s, idx) => {
          const { label } = getToolDisplay(s.tool, s.input);
          return (
            <span
              key={s.id}
              title={label}
              className={cn(
                "flex h-7 w-7 items-center justify-center rounded-full border-2 border-background bg-card shadow-sm",
                idx > 0 && "-ml-2.5"
              )}
              style={{ zIndex: shown.length - idx }}
            >
              {s.status === "running" ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin text-amber-500" />
              ) : s.status === "error" ? (
                <XCircle className="h-3.5 w-3.5 text-red-400" />
              ) : (
                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
              )}
            </span>
          );
        })}
        {extra > 0 && (
          <span
            className="-ml-2.5 flex h-7 w-7 items-center justify-center rounded-full border-2 border-background bg-foreground/10 text-[10px] font-semibold text-muted-foreground"
            style={{ zIndex: 0 }}
          >
            +{extra}
          </span>
        )}
      </div>
      <span className="text-[11px] text-muted-foreground group-hover:text-foreground">
        {anyRunning ? "Arbeitet…" : `${steps.length} ${steps.length === 1 ? "Tool" : "Tools"}`} · Details
      </span>
    </button>
  );
}

function ToolCallBlock({ step, isStreaming }: { step: ToolStep; isStreaming?: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const { label, description, detail } = getToolDisplay(step.tool, step.input);
  const isRunning = step.status === "running";
  const hasOutput = Boolean(step.output);

  return (
    <div className="group">
      {/* Header row */}
      <div
        className="flex items-center gap-2 cursor-pointer hover:bg-foreground/[0.04] dark:hover:bg-foreground/[0.06] rounded-md px-1.5 py-1 -mx-1.5 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        {/* Status dot */}
        <span className="relative flex h-3 w-3 shrink-0 items-center justify-center">
          {isRunning ? (
            <>
              <span className="absolute inline-flex h-2.5 w-2.5 animate-ping rounded-full bg-amber-400 opacity-60" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-amber-500" />
            </>
          ) : step.status === "error" ? (
            <XCircle className="h-3.5 w-3.5 text-red-400" />
          ) : (
            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
          )}
        </span>

        {/* Tool label */}
        <span className="text-[13px] font-semibold text-foreground">{label}</span>

        {/* Description */}
        {description && (
          <span className="text-[12px] text-muted-foreground truncate">{description}</span>
        )}

        {/* Expand chevron */}
        <ChevronRight
          className={cn(
            "h-3 w-3 text-muted-foreground/50 transition-transform ml-auto shrink-0",
            expanded && "rotate-90"
          )}
        />
      </div>

      {/* Expanded content: IN / OUT */}
      {(expanded || isRunning) && (
        <div className="ml-5 mt-1 space-y-1.5">
          {/* IN block */}
          {detail && (
            <div className="flex gap-0">
              <span className="text-[10px] text-muted-foreground/50 w-10 shrink-0 text-right pr-2 pt-1.5 font-mono select-none">IN</span>
              <pre className="text-[12px] font-mono text-muted-foreground bg-muted/80 dark:bg-muted/40 border border-border rounded-md px-3 py-2 overflow-x-auto max-w-full flex-1 whitespace-pre-wrap break-all">
                {detail}
              </pre>
            </div>
          )}

          {/* OUT block */}
          {hasOutput && (
            <div className="flex gap-0">
              <span className="text-[10px] text-muted-foreground/50 w-10 shrink-0 text-right pr-2 pt-1.5 font-mono select-none">OUT</span>
              <pre className="text-[12px] font-mono text-muted-foreground bg-muted/80 dark:bg-muted/40 border border-border rounded-md px-3 py-2 overflow-x-auto max-w-full flex-1 max-h-60 overflow-y-auto whitespace-pre-wrap break-all">
                {(step.output || "").length > 2000
                  ? step.output!.slice(0, 2000) + "\n... (truncated)"
                  : step.output}
              </pre>
            </div>
          )}

          {/* Running indicator */}
          {isRunning && !hasOutput && (
            <div className="flex items-center gap-2 ml-10 text-muted-foreground/60 text-xs">
              <Loader2 className="h-3 w-3 animate-spin" />
              <span>Running...</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ─── Meta Bar ──────────────────────────────────────────────────────── */

function MetaBar({ meta }: { meta: { cost_usd?: number; duration_ms?: number; num_turns?: number; input_tokens?: number; output_tokens?: number } }) {
  const parts: string[] = [];
  if (meta.duration_ms) parts.push(`${(meta.duration_ms / 1000).toFixed(1)}s`);
  if (meta.cost_usd) parts.push(formatMoney(meta.cost_usd));
  if (meta.num_turns) parts.push(`${meta.num_turns} turns`);
  if (meta.input_tokens || meta.output_tokens)
    parts.push(`${meta.input_tokens ?? 0} ↑ / ${meta.output_tokens ?? 0} ↓ tok`);
  if (parts.length === 0) return null;

  return (
    <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground/60 tabular-nums pl-1 pt-1">
      <CheckCircle2 className="h-3 w-3 text-emerald-600 dark:text-emerald-500" />
      <span>{parts.join(" \u00b7 ")}</span>
    </div>
  );
}

/* ─── Markdown Content ──────────────────────────────────────────────── */
// Shared with the task live/replay output — see components/ui/markdown-content.
