"use client";

import { useEffect, useRef, useState, useCallback, memo } from "react";
import {
  Send, RotateCcw, Bot, User, AlertTriangle, WifiOff,
  Paperclip, Loader2, Plus, MessageSquare, Gauge, Square, Mic,
  ChevronRight, CheckCircle2, XCircle, Clock, X, Play, Pause, Download,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import { useAuthStore } from "@/lib/auth";
import { useSimpleMode } from "@/hooks/use-simple-mode";

/* ─── Types ─────────────────────────────────────────────────────────── */

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
  type: "text" | "tool_call" | "tool_result" | "error" | "system" | "done" | "session" | "cancelled" | "queued" | "image" | "file";
  data: Record<string, unknown>;
  timestamp: string;
}

interface SessionTab {
  id: string;
  label: string;
  preview: string;
  isNew?: boolean;
}

import { getWsUrl, getApiUrl } from "@/lib/config";
import { VoiceSessionModal } from "./voice-session";
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

export function AgentChat({ agentId, initialSessionId }: { agentId: string; initialSessionId?: string | null }) {
  const { simpleMode } = useSimpleMode();
  const [sessions, setSessions] = useState<SessionTab[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [pendingImages, setPendingImages] = useState<ChatImage[]>([]);
  const [voiceOpen, setVoiceOpen] = useState(false);
  const [isConnected, setIsConnected] = useState(false);
  const [isWaiting, setIsWaiting] = useState(false);
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
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const reconnectTimeout = useRef<ReturnType<typeof setTimeout>>();
  const reconnectAttempts = useRef(0);
  const intentionalClose = useRef(false);
  const currentWsSessionId = useRef<string | null>(null);
  const activeSessionIdRef = useRef<string | null>(null);

  // Keep ref in sync with state
  useEffect(() => {
    activeSessionIdRef.current = activeSessionId;
  }, [activeSessionId]);

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

    // Fetch one-time ticket for WebSocket auth
    let authParam = "";
    try {
      const resp = await fetch(`${getApiUrl()}/api/v1/ws/ticket`, {
        method: "POST",
        credentials: "include",
      });
      if (resp.ok) {
        const { ticket } = await resp.json();
        authParam = `?ticket=${ticket}`;
      }
    } catch {
      // Fallback to legacy token auth
      const token = useAuthStore.getState().wsToken;
      authParam = token ? `?token=${token}` : "";
    }

    const ws = new WebSocket(`${getWsUrl()}/api/v1/ws/agents/${agentId}/chat${authParam}`);
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
  // streamed token) and ONLY when the user is already near the bottom, so reading
  // older messages isn't yanked away mid-stream.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    if (nearBottom) {
      requestAnimationFrame(() => {
        bottomRef.current?.scrollIntoView({ behavior: "auto" });
      });
    }
  }, [messages]);

  const sendMessage = useCallback(() => {
    const text = input.trim();
    const imgs = pendingImages;
    if ((!text && imgs.length === 0) || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;

    const msgId = `user-${Date.now()}`;
    setMessages((prev) => [
      ...prev,
      {
        id: msgId,
        role: "user",
        content: text,
        timestamp: new Date().toISOString(),
        images: imgs.length > 0 ? imgs : undefined,
      },
    ]);
    setMessageCount((c) => c + 1);

    wsRef.current.send(JSON.stringify({
      text,
      images: imgs,
      session_id: activeSessionId || currentWsSessionId.current,
      source: "webapp",
    }));
    setInput("");
    setPendingImages([]);
    pendingCountRef.current += 1;
    setIsWaiting(true);
    inputRef.current?.focus();

    setSessions((prev) =>
      prev.map((s) =>
        s.id === (activeSessionId || currentWsSessionId.current)
          ? { ...s, preview: (text || "Bild").slice(0, 80) }
          : s
      )
    );
  }, [input, pendingImages, activeSessionId]);

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
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
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

  const handleFileUpload = useCallback(async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setIsUploading(true);
    try {
      const result = await api.uploadFiles(agentId, "/workspace", files);
      const fileNames = Array.from(files).map((f) => f.name).join(", ");
      setMessages((prev) => [
        ...prev,
        {
          id: `upload-${Date.now()}`,
          role: "system",
          content: `Uploaded ${result.uploaded} file(s): ${fileNames}`,
          timestamp: new Date().toISOString(),
        },
      ]);
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        const mention = `I just uploaded these files to /workspace: ${fileNames}. Please acknowledge them.`;
        wsRef.current.send(JSON.stringify({ text: mention, source: "webapp" }));
        setMessages((prev) => [
          ...prev,
          { id: `user-upload-${Date.now()}`, role: "user", content: mention, timestamp: new Date().toISOString() },
        ]);
        setIsWaiting(true);
      }
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        { id: `upload-error-${Date.now()}`, role: "error", content: `Upload failed: ${e instanceof Error ? e.message : "Unknown error"}`, timestamp: new Date().toISOString() },
      ]);
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }, [agentId]);

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

  const estimatedTokens = messages.reduce((sum, m) => sum + Math.ceil((m.content?.length || 0) / 4), 0);
  const contextLimit = 200000;
  const contextPercent = Math.min((estimatedTokens / contextLimit) * 100, 100);

  /* ─── Render ──────────────────────────────────────────────────────── */

  return (
    <div className="flex flex-col h-full min-h-0 rounded-xl border border-border bg-card/80 backdrop-blur-sm overflow-hidden">
      {voiceOpen && (
        <VoiceSessionModal
          agentId={agentId}
          agentName={agentId}
          onClose={() => setVoiceOpen(false)}
        />
      )}
      {/* Session tabs */}
      <div className="flex items-center gap-1 border-b border-border px-3 py-1.5 shrink-0 min-w-0">
        <div className="flex items-center gap-1 flex-1 min-w-0 overflow-x-auto scrollbar-none">
          {sessions.map((session) => (
            <div
              key={session.id}
              className={cn(
                "group/tab inline-flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-[11px] font-medium transition-all whitespace-nowrap shrink-0",
                activeSessionId === session.id
                  ? "bg-foreground/[0.08] text-foreground"
                  : "text-muted-foreground/60 hover:text-muted-foreground hover:bg-foreground/[0.04]"
              )}
            >
              <button
                onClick={() => switchSession(session.id)}
                className="inline-flex items-center gap-1.5"
              >
                <MessageSquare className="h-3 w-3" />
                {session.preview ? session.preview.slice(0, 20) + (session.preview.length > 20 ? "..." : "") : session.label}
              </button>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  deleteSession(session.id);
                }}
                className="ml-0.5 rounded p-0.5 opacity-0 group-hover/tab:opacity-100 hover:bg-foreground/[0.1] transition-all"
                title="Close session"
              >
                <X className="h-2.5 w-2.5" />
              </button>
            </div>
          ))}
        </div>
        <button
          onClick={createNewSession}
          disabled={!isConnected}
          className="inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-[11px] font-medium text-muted-foreground/60 hover:text-foreground hover:bg-foreground/[0.06] disabled:opacity-40 transition-all shrink-0 ml-auto"
          title="New chat session"
        >
          <Plus className="h-3 w-3" />
        </button>

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

      {/* Messages area */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto [scrollbar-gutter:stable] px-5 py-4 space-y-4 bg-background dark:bg-[#0d1117]">
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
          <MessageRow key={`${msg.id}-${msg.role}`} message={msg} />
        ))}
        {isWaiting && !messages.some((m) => m.isStreaming) && (
          <div className="flex items-center gap-3 pl-1 py-2">
            <div className="flex h-6 w-6 items-center justify-center rounded-md bg-violet-500/20 shrink-0">
              <Bot className="h-3.5 w-3.5 text-violet-400" />
            </div>
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
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* L3 Approval Request Banner */}
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
      <div className="border-t border-border p-4">
        <input ref={fileInputRef} type="file" multiple className="hidden" onChange={(e) => handleFileUpload(e.target.files)} />
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
        <div className="flex gap-2.5">
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={!isConnected || isUploading}
            className="flex h-10 w-10 items-center justify-center rounded-xl border border-border bg-background/80 text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] disabled:opacity-40 transition-all shrink-0"
            title="Upload files"
          >
            {isUploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Paperclip className="h-4 w-4" />}
          </button>
          <button
            onClick={() => setVoiceOpen(true)}
            disabled={!isConnected}
            className="flex h-10 w-10 items-center justify-center rounded-xl border border-border bg-background/80 text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] disabled:opacity-40 transition-all shrink-0"
            title="Live-Sprachsession starten"
          >
            <Mic className="h-4 w-4" />
          </button>
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            placeholder={connectionFailed ? "Agent not connected" : isWaiting ? "Agent arbeitet... (du kannst trotzdem schreiben)" : "Nachricht... (Bild mit Strg+V einfügen, Enter zum Senden)"}
            disabled={!isConnected}
            className="flex-1 resize-none rounded-xl border border-border bg-background/80 px-4 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 disabled:opacity-40 transition-all placeholder:text-muted-foreground/30"
            rows={1}
          />
          {isWaiting ? (
            <button
              onClick={stopGeneration}
              className="flex h-10 w-10 items-center justify-center rounded-xl bg-red-500/90 text-white hover:bg-red-500 shadow-lg shadow-red-500/20 transition-all"
              title="Stop"
            >
              <Square className="h-4 w-4 fill-current" />
            </button>
          ) : (
            <button
              onClick={sendMessage}
              disabled={!isConnected || (!input.trim() && pendingImages.length === 0)}
              className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-40 shadow-lg shadow-primary/20 disabled:shadow-none transition-all"
            >
              <Send className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>

      {/* Context usage bar (hidden in simple mode) */}
      {!simpleMode && <div className="border-t border-border px-4 py-2 flex items-center gap-4 text-[10px] text-muted-foreground/60 tabular-nums shrink-0">
        <div className="flex items-center gap-1.5">
          <Gauge className="h-3 w-3" />
          <span>Context</span>
        </div>
        <div className="flex-1 h-1.5 rounded-full bg-foreground/[0.06] max-w-[200px]">
          <div
            className={cn(
              "h-1.5 rounded-full transition-all duration-500",
              contextPercent < 50 ? "bg-emerald-500" : contextPercent < 80 ? "bg-amber-500" : "bg-red-500"
            )}
            style={{ width: `${contextPercent}%` }}
          />
        </div>
        <span>{estimatedTokens.toLocaleString()} / {(contextLimit / 1000).toFixed(0)}k tokens</span>
        <span className="border-l border-border pl-3">{messageCount} msgs</span>
        {totalTurns > 0 && <span>{totalTurns} turns</span>}
        {totalCost > 0 && <span>${totalCost.toFixed(4)}</span>}
      </div>}
    </div>
  );
}

/* ─── Message Row ───────────────────────────────────────────────────── */

function MessageRow({ message }: { message: ChatMessage }) {
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

  if (message.role === "user") {
    return <UserMessage content={message.content} images={message.images} />;
  }

  // Assistant message - render as timeline of steps
  return <AssistantResponse message={message} />;
}

/* ─── User Message ──────────────────────────────────────────────────── */

function UserMessage({ content, images }: { content: string; images?: ChatImage[] }) {
  return (
    <div className="flex items-start gap-3 pl-1">
      <div className="flex h-6 w-6 items-center justify-center rounded-md bg-blue-500/15 dark:bg-blue-500/20 shrink-0 mt-0.5">
        <User className="h-3.5 w-3.5 text-blue-500 dark:text-blue-400" />
      </div>
      <div className="text-sm text-foreground leading-relaxed pt-0.5 space-y-2">
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
        {content && <div>{content}</div>}
      </div>
    </div>
  );
}

/* ─── Assistant Response (Claude CLI Style) ─────────────────────────── */

function AssistantResponse({ message }: { message: ChatMessage }) {
  const steps = message.steps || [];
  const { simpleMode } = useSimpleMode();

  // If no steps at all (legacy), show as simple text
  if (steps.length === 0 && message.content) {
    return (
      <div className="pl-1 space-y-2">
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
    <div className="space-y-2.5 pl-1">
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
  if (meta.cost_usd) parts.push(`$${meta.cost_usd.toFixed(4)}`);
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

const MarkdownContent = memo(function MarkdownContent({ content }: { content: string }) {
  return (
    <div
      className={cn(
        "prose prose-sm dark:prose-invert max-w-none break-words leading-relaxed",
        "text-foreground/80",
        "[&_h1]:text-base [&_h1]:font-bold [&_h1]:mt-3 [&_h1]:mb-1.5 [&_h1]:text-foreground",
        "[&_h2]:text-sm [&_h2]:font-bold [&_h2]:mt-2.5 [&_h2]:mb-1 [&_h2]:text-foreground",
        "[&_h3]:text-sm [&_h3]:font-semibold [&_h3]:mt-2 [&_h3]:mb-1 [&_h3]:text-foreground/90",
        "[&_p]:my-1.5 [&_p]:leading-relaxed",
        "[&_p:first-child]:mt-0 [&_p:last-child]:mb-0",
        "[&_ul]:my-1.5 [&_ul]:pl-4 [&_ul]:space-y-0.5",
        "[&_ol]:my-1.5 [&_ol]:pl-4 [&_ol]:space-y-0.5",
        "[&_li]:text-sm [&_li]:text-foreground/80",
        "[&_strong]:font-semibold [&_strong]:text-foreground",
        "[&_code]:rounded [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:text-xs [&_code]:font-mono [&_code]:bg-muted [&_code]:text-amber-600 dark:[&_code]:text-amber-300",
        "[&_pre]:rounded-md [&_pre]:p-3 [&_pre]:my-2 [&_pre]:overflow-x-auto [&_pre]:text-xs [&_pre]:bg-muted/80 dark:[&_pre]:bg-muted/40 [&_pre]:border [&_pre]:border-border",
        "[&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_pre_code]:text-muted-foreground",
        "[&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:my-2 [&_blockquote]:text-muted-foreground",
        "[&_hr]:my-3 [&_hr]:border-border",
        "[&_table]:my-2 [&_table]:text-xs",
        "[&_th]:px-2 [&_th]:py-1 [&_th]:text-left [&_th]:font-semibold [&_th]:border-b [&_th]:border-border [&_th]:text-foreground/80",
        "[&_td]:px-2 [&_td]:py-1 [&_td]:border-b [&_td]:border-border [&_td]:text-muted-foreground",
        "[&_a]:text-blue-500 dark:[&_a]:text-blue-400 [&_a]:underline [&_a]:underline-offset-2"
      )}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
});
