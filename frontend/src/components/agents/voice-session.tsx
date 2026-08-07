"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import dynamic from "next/dynamic";
import { Mic, MicOff, X, Loader2, Volume2, PhoneOff, Radio, Search, FileText, CheckCircle2, Pause, Play, ChevronDown, ChevronRight, ClipboardList, Paperclip, Globe, ExternalLink, Hand, Network, AlertTriangle, LayoutGrid, CalendarClock } from "lucide-react";
import { getWsUrl, getBase } from "@/lib/config";
import { JarvisCore } from "./jarvis-core";
import { MeetingRecorder } from "@/components/meetings/meeting-recorder";
import * as api from "@/lib/api";
import type { ApprovalRequest } from "@/lib/types";
import { sendMeetingTranscriptToChat, getChatHistory, uploadFiles } from "@/lib/api";

// The knowledge-graph overlay (WebGL) is client-only and heavy → load on demand.
const VaultGraph3D = dynamic(() => import("@/app/second-brains/vault-graph-3d"), { ssr: false });

// Voice UI targets that navigate to an app page (action="navigate").
const NAV_ROUTES: Record<string, string> = {
  dashboard: "/", tasks: "/tasks", agents: "/agents", meeting_rooms: "/meeting-rooms",
  knowledge: "/knowledge", skills: "/skills", triggers: "/triggers", approvals: "/approvals",
  integrations: "/integrations", settings: "/settings", analytics: "/analytics",
  apps: "/apps", audit: "/audit", health: "/health", schedules: "/schedules",
};

/** Anzeigename fürs Panel — der rohe Routenname stünde sonst als Überschrift da. */
const NAV_LABELS: Record<string, string> = {
  dashboard: "Dashboard", tasks: "Tasks", agents: "Agenten", meeting_rooms: "Meeting Rooms",
  knowledge: "Knowledge Base", skills: "Skill Marketplace", triggers: "Triggers",
  approvals: "Freigaben", integrations: "Integrationen", settings: "Einstellungen",
  analytics: "Analytics", apps: "Apps", audit: "Audit-Log", health: "System-Health",
  schedules: "Schedules",
};

type Turn = { role: "user" | "assistant"; text: string };
type WebResult = { title: string; url: string; snippet: string };
type WebResultSet = { query: string; results: WebResult[] };

// #474: in voice mode, clicking the approve/deny buttons breaks the flow the
// feature exists for — so a spoken decision must also work. Deliberately
// requires an explicit approval word (the button's own label, or a small fixed
// vocabulary) rather than a bare "ja"/"nein": those are far too common in normal
// conversation and a stray one must not silently authorize a pending action.
// Deny is checked first so an ambiguous "ja, aber lieber nicht" reads as a stop.
function matchApprovalIntent(rawText: string, approval: ApprovalRequest): "approve" | "deny" | null {
  const text = rawText.trim().toLowerCase();
  if (!text) return null;
  const denyWords = [
    (approval.options?.[1] || "").toLowerCase(),
    "ablehnen", "abgelehnt", "abbrechen", "verweiger", "nein nicht", "nein, nicht", "stopp", "stop", "lass es",
  ].filter(Boolean);
  if (denyWords.some((w) => text.includes(w))) return "deny";
  const approveWords = [
    (approval.options?.[0] || "").toLowerCase(),
    "freigeben", "freigegeben", "genehmig", "bestätig", "erlaub", "einverstanden",
    "ja mach", "ja, mach", "ja bitte", "ja, bitte", "ja los", "los geht",
  ].filter(Boolean);
  if (approveWords.some((w) => text.includes(w))) return "approve";
  return null;
}

// Agent-supplied URLs are untrusted (LLM output): only http(s) may reach
// href / iframe src / window.open — javascript:, data:, blob:, file: are dropped.
function safeHttpUrl(raw: unknown): string | undefined {
  if (!raw) return undefined;
  try {
    // Relative same-origin paths (e.g. the app-proxy "/api/v1/.../apps/proxy/…") are
    // resolved against the current origin; absolute URLs ignore the base. Only http(s) pass.
    const base = typeof window !== "undefined" ? window.location.origin : undefined;
    const parsed = new URL(String(raw), base);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") return parsed.toString();
  } catch {
    // not a parsable URL
  }
  return undefined;
}

type VoiceState = "connecting" | "ready" | "listening" | "processing" | "speaking" | "error";
type Mode = "classic" | "nova_sonic";

export type VoiceSessionSnapshot = {
  state: VoiceState;
  mode: Mode;
};

/** ArrayBuffer → base64 without spreading a typed array (build-safe). */
function bufToBase64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let binary = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode.apply(null, Array.from(bytes.subarray(i, i + CHUNK)));
  }
  return btoa(binary);
}

/** Float32 [-1,1] → 16-bit little-endian PCM bytes. */
function floatTo16LE(input: Float32Array): ArrayBuffer {
  const view = new DataView(new ArrayBuffer(input.length * 2));
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]));
    view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return view.buffer;
}

/** Downsample a Float32 buffer from inRate to outRate (window-average anti-alias). */
function downsample(input: Float32Array, inRate: number, outRate: number): Float32Array {
  if (outRate >= inRate) return input;
  const ratio = inRate / outRate;
  const outLen = Math.floor(input.length / ratio);
  const out = new Float32Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.floor((i + 1) * ratio);
    let sum = 0;
    let cnt = 0;
    for (let j = start; j < end && j < input.length; j++) {
      sum += input[j];
      cnt++;
    }
    out[i] = cnt ? sum / cnt : 0;
  }
  return out;
}

interface Props {
  agentId: string;
  agentName: string;
  onClose: () => void;
  /** Optional custom WS-ticket source (e.g. the unauthenticated kiosk). When
   *  omitted, the default authenticated `/ws/ticket` flow (JWT) is used. */
  getTicket?: () => Promise<string>;
  /** Continue an existing chat session by voice (shared session with the text chat;
   *  the agent picks up the prior context). */
  resumeSessionId?: string;
  /** Render inline inside a page/tab instead of as a fixed modal overlay:
   *  no dark backdrop, no close button, fills its container (used by the Speech tab). */
  embedded?: boolean;
  /** Keep the session/audio graph mounted while the provider shows only the compact indicator. */
  hidden?: boolean;
  /** Called when the user explicitly ends the call; unlike onClose this tears down provider state. */
  onEnd?: () => void;
  /** Mirrors high-level state to the app-level provider for the floating indicator. */
  onSnapshot?: (snapshot: VoiceSessionSnapshot) => void;
}


/** URLs in gesprochenem Text anklickbar machen.
 *
 *  Der Agent nennt Adressen im Fliesstext („du kannst sie unter https://… aufrufen"),
 *  bisher standen sie tot da — abtippen war die einzige Option. Bewusst hier an EINER
 *  Stelle statt als Sonderfall fuer App-Links: gilt damit fuer jede Adresse, die er
 *  jemals nennt. Satzzeichen am Ende gehoeren nicht zur Adresse. */
function linkify(text: string) {
  const parts = String(text ?? "").split(/(https?:\/\/[^\s<>"']+)/g);
  return parts.map((part, i) => {
    if (i % 2 === 0) return part;
    const trailing = part.match(/[.,;:!?)\]]+$/)?.[0] ?? "";
    const url = trailing ? part.slice(0, -trailing.length) : part;
    return (
      <span key={i}>
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="underline decoration-primary/40 underline-offset-2 hover:decoration-primary break-all"
        >
          {url}
        </a>
        {trailing}
      </span>
    );
  });
}

export function VoiceSessionModal({
  agentId,
  agentName,
  onClose,
  getTicket,
  resumeSessionId,
  embedded = false,
  hidden = false,
  onEnd,
  onSnapshot,
}: Props) {
  const [state, setState] = useState<VoiceState>("connecting");
  const [mode, setMode] = useState<Mode>("classic");
  const [transcript, setTranscript] = useState("");
  const [response, setResponse] = useState("");
  const [statusMsg, setStatusMsg] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState(false);
  const [activity, setActivity] = useState<{ kind: string; label: string; detail: string }[]>([]);
  // Each delegated task is its own card with its own status — several run in parallel,
  // so we track them individually instead of one shared "delegating" flag.
  const [tasks, setTasks] = useState<{ id: string; instruction: string; done: boolean; result?: string }[]>([]);
  // Aufgeklappte Ergebnisse. Fertige Karten sind standardmaessig zu — ein Ergebnis
  // kann seitenlang sein und haette sonst das ganze Panel gefuellt.
  const [openTasks, setOpenTasks] = useState<Set<string>>(new Set());
  // Schritte einer aufgeklappten LAUFENDEN Aufgabe. Sie liegen bereits in der
  // Datenbank (dieselbe Quelle wie die Task-Detailansicht) — bisher holte sie im
  // Sprach-Panel nur niemand ab, also stand dort „laeuft" und sonst nichts.
  const [taskSteps, setTaskSteps] = useState<Record<string, string[]>>({});
  const delegating = tasks.some((t) => !t.done); // any task still running
  const activityRef = useRef<HTMLDivElement>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [webResults, setWebResults] = useState<WebResultSet[]>([]);
  // Werkzeug-Spur: was hat er gerade benutzt, womit, und was kam raus. Vorher lief
  // das unsichtbar ab und es sah aus, als haette er nichts getan.
  const [toolLog, setToolLog] = useState<{ name: string; input?: string; output?: string; done: boolean }[]>([]);
  const [media, setMedia] = useState<{ kind: string; media_type?: string; b64?: string; filename?: string; caption?: string; path?: string; url?: string; embeddable?: boolean; auto_open?: boolean;
    // kind="plan": der Tagesplan als Karte — sehen schlaegt hoeren.
    items?: { title: string; time?: string; minutes?: number; priority?: string; status?: string; notes?: string }[] }[]>([]);
  // Page the agent asked to show inside the app (iframe modal).
  const [webModal, setWebModal] = useState<{ url: string; caption?: string } | null>(null);
  // Voice-driven UI overlay (e.g. the knowledge graph) shown on top of the cockpit.
  const [graphOverlay, setGraphOverlay] = useState<{ brainId: number | null; query?: string } | null>(null);
  // Eine App-Seite im Cockpit statt eines Seitenwechsels (#476) — sonst wird diese
  // Komponente ausgehaengt und das Mikrofon stirbt mitten im Satz.
  const [pageOverlay, setPageOverlay] = useState<{ path: string; label: string } | null>(null);
  // URLs whose auto-open we already attempted, and those the popup blocker swallowed.
  const autoOpenedRef = useRef<Set<string>>(new Set());
  const [blockedUrls, setBlockedUrls] = useState<Set<string>>(new Set());
  const transcriptRef = useRef<HTMLDivElement>(null);
  const [paused, setPaused] = useState(false);       // focus mode: mic muted, agent still reports
  const [activityOpen, setActivityOpen] = useState(true);
  const [volume, setVolume] = useState(1);           // playback volume (works on iOS via GainNode)
  // Meeting recorder: PURE audio capture → transcript. No live agent interaction
  // while recording (the live mic is muted so the agent neither listens nor speaks).
  const [meetingOpen, setMeetingOpen] = useState(false);
  const [meetingMsg, setMeetingMsg] = useState<string | null>(null);
  // Offene Freigabe dieses Agenten (#474). Der Agent ruft `request_approval` und
  // wartet bis zu 10 Minuten auf eine Antwort — im Sprachchat gab es bisher keine
  // Stelle, an der man sie geben konnte, also lief jede Freigabe ins Leere und der
  // Agent machte ohne sie weiter.
  const [pendingApproval, setPendingApproval] = useState<ApprovalRequest | null>(null);
  const [approvalBusy, setApprovalBusy] = useState(false);
  const lastApprovalIdRef = useRef<string | null>(null);

  useEffect(() => {
    onSnapshot?.({ state, mode });
  }, [state, mode, onSnapshot]);

  const changeVolume = useCallback((v: number) => {
    setVolume(v);
    volumeRef.current = v;
    if (gainNodeRef.current) gainNodeRef.current.gain.value = v;
  }, []);

  // Focus mode: mute/unmute the mic track. Keeps the session alive (silence is
  // still streamed) so the agent can proactively speak when a task finishes.
  const togglePause = useCallback(() => {
    setPaused((p) => {
      const next = !p;
      streamRef.current?.getAudioTracks().forEach((t) => { t.enabled = !next; });
      return next;
    });
  }, []);

  // Open the meeting recorder. This is PURE recording — mute the live mic so the
  // agent neither hears the meeting nor talks back; only audio is captured and
  // transcribed. The transcript can later be sent to the agent as a BACKGROUND
  // task (a protocol job), never as a live conversation.
  const openMeeting = useCallback(() => {
    streamRef.current?.getAudioTracks().forEach((t) => { t.enabled = false; });
    setPaused(true);
    setMeetingMsg(null);
    setMeetingOpen(true);
  }, []);

  // Hand the finished transcript to THIS agent as a visible CHAT thread (not a
  // headless task): the transcript + the agent's protocol reply appear in the
  // agent's Chat tab. Explicit user action (button after recording stops).
  const handleMeetingTranscript = useCallback(async (text: string) => {
    const t = text.trim();
    if (!t) return;
    setMeetingOpen(false);
    setMeetingMsg("Transkript an den Chat gesendet — der Agent schreibt das Protokoll dort (im Chat-Tab sichtbar).");
    try {
      await sendMeetingTranscriptToChat(agentId, t);
      setMeetingMsg("Protokoll im Chat erstellt — öffne den Chat-Tab dieses Agenten, um es zu sehen.");
    } catch {
      setMeetingMsg("Konnte das Transkript nicht an den Chat senden.");
    }
    window.setTimeout(() => setMeetingMsg(null), 12000);
  }, [agentId]);

  // Append a conversation turn, coalescing consecutive same-role events into ONE
  // bubble. Nova Sonic emits each sentence as a separate event; naive replace would
  // show only the last sentence. So: if the new text extends the current bubble
  // (cumulative) replace it; if it's a fresh delta, append it; skip pure repeats.
  const upsertTurn = useCallback((role: "user" | "assistant", text: string) => {
    const t = String(text || "").trim();
    if (!t) return;
    setTurns((prev) => {
      const last = prev.length ? prev[prev.length - 1] : null;
      if (last && last.role === role) {
        const cur = last.text;
        let merged: string;
        if (t.startsWith(cur)) merged = t;             // cumulative stream → take the fuller text
        else if (cur.endsWith(t) || cur.includes(t)) merged = cur;  // duplicate → keep
        else merged = `${cur} ${t}`;                    // new sentence → append
        if (merged === cur) return prev;
        const next = prev.slice();
        next[next.length - 1] = { role, text: merged };
        return next;
      }
      return [...prev, { role, text: t }];
    });
  }, []);

  // When resuming a past conversation (from the Speech tab's "Letzte Gespräche"
  // list), seed the transcript with its history so the user sees the earlier turns
  // and speaks straight into the same session — same shared session as the text chat.
  useEffect(() => {
    if (!resumeSessionId) return;
    let cancelled = false;
    getChatHistory(agentId, 200, resumeSessionId)
      .then((res) => {
        if (cancelled) return;
        const seeded: Turn[] = [];
        for (const m of res.messages || []) {
          if (m.role !== "user" && m.role !== "assistant") continue;
          const text = String(m.content || "").trim();
          if (!text) continue;
          seeded.push({ role: m.role, text });
        }
        if (seeded.length) setTurns(seeded);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [agentId, resumeSessionId]);

  // The agent asked to open a page in a new tab. Browsers block window.open() outside a
  // user gesture, so we try once and — if swallowed — surface a click-to-open card
  // (that click IS a gesture and always works).
  useEffect(() => {
    for (const m of media) {
      if (m.kind !== "web" || !m.auto_open || !m.url) continue;
      if (autoOpenedRef.current.has(m.url)) continue;
      autoOpenedRef.current.add(m.url);
      const win = window.open(m.url, "_blank", "noopener,noreferrer");
      if (!win) setBlockedUrls((prev) => new Set(prev).add(m.url!));
    }
  }, [media]);

  const wsRef = useRef<WebSocket | null>(null);
  // Auto-reconnect: the AWS Nova Sonic bidi stream can drop mid-conversation (a known
  // AWS-CRT race → the server emits "done"). We keep a stable chat_session id and
  // silently reconnect, resuming the conversation, instead of dead-ending.
  const reconnectsRef = useRef(0);
  const closingRef = useRef(false);
  const wsReconnectTimer = useRef<number | undefined>(undefined);
  const voiceSessionRef = useRef<string>("");
  const MAX_VOICE_RECONNECTS = 8;
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState<string | null>(null);

  // Solange die Sprachsitzung steht, auf Freigaben dieses Agenten horchen. Anders als
  // im Text-Chat NICHT an einen "arbeitet gerade"-Zustand gekoppelt: im Sprachmodus
  // laeuft die Arbeit oft im Hintergrund weiter, waehrend der Nutzer schon wieder redet.
  useEffect(() => {
    if (state === "connecting" || state === "error") {
      setPendingApproval(null);
      lastApprovalIdRef.current = null;
      return;
    }
    let stop = false;
    const poll = async () => {
      try {
        const r = await api.getPendingApprovals();
        if (stop) return;
        const approval = r.approvals.find((a) => a.agent_id === agentId) || null;
        setPendingApproval(approval);
        if (approval && approval.approval_id !== lastApprovalIdRef.current) {
          lastApprovalIdRef.current = approval.approval_id;
          const label = approval.question || approval.tool || "Freigabe erforderlich";
          const detail = approval.context || approval.reasoning || "";
          setActivity((prev) => {
            const next = [...prev, { kind: "approval", label, detail }];
            return next.length > 40 ? next.slice(-40) : next;
          });
        }
        if (!approval) lastApprovalIdRef.current = null;
      } catch { /* Netzwerkaussetzer ignorieren, naechster Tick versucht es erneut */ }
    };
    poll();
    const iv = setInterval(poll, 3000);
    return () => { stop = true; clearInterval(iv); };
  }, [state, agentId]);

  const decideApproval = useCallback(async (approve: boolean) => {
    if (!pendingApproval || approvalBusy) return;
    setApprovalBusy(true);
    try {
      if (approve) await api.approveCommand(pendingApproval.approval_id);
      else await api.denyCommand(pendingApproval.approval_id, "Im Sprachchat abgelehnt");
      setPendingApproval(null);
    } catch { /* bleibt stehen, damit der Nutzer es erneut versuchen kann */ }
    finally { setApprovalBusy(false); }
  }, [pendingApproval, approvalBusy]);

  // handleServerEvent below intentionally has a stable ([]) dependency array — it
  // must not tear down/recreate the websocket handler on every render — so it
  // cannot close over pendingApproval/decideApproval directly. Mirror them into
  // refs it reads live, to resolve a pending approval from spoken text (#474).
  const pendingApprovalRef = useRef<ApprovalRequest | null>(null);
  useEffect(() => { pendingApprovalRef.current = pendingApproval; }, [pendingApproval]);
  const decideApprovalRef = useRef(decideApproval);
  useEffect(() => { decideApprovalRef.current = decideApproval; }, [decideApproval]);
  const [dragOver, setDragOver] = useState(false);

  // Drop file(s) into the agent's workspace, then tell the live session so the agent
  // picks it up by voice ("Datei X unter /workspace/X hochgeladen") and asks what to do.
  const handleUpload = useCallback(async (picked: FileList | null) => {
    if (!picked || picked.length === 0) return;
    setUploading(true);
    setUploadMsg(null);
    try {
      await uploadFiles(agentId, "/workspace", picked);
      const names = Array.from(picked).map((f) => `${f.name} unter /workspace/${f.name}`);
      wsRef.current?.send(JSON.stringify({ type: "files_uploaded", data: { files: names } }));
      setUploadMsg(
        picked.length === 1
          ? `${picked[0].name} hochgeladen`
          : `${picked.length} Dateien hochgeladen`,
      );
      window.setTimeout(() => setUploadMsg(null), 8000);
    } catch (e) {
      setUploadMsg(`Upload fehlgeschlagen: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }, [agentId]);

  // Interpret a voice UI command: open/close an in-app overlay, or navigate a page.
  const handleUiCommand = useCallback(async (action: string, target: string, query?: string) => {
    const a = (action || "").toLowerCase().trim();
    const t = (target || "").toLowerCase().trim();
    if (a === "close") { setGraphOverlay(null); setPageOverlay(null); return; }
    if (t === "knowledge_graph" || t === "graph" || t === "wissensgraph" || t === "knowledgegraph") {
      let brainId: number | null = null;
      // allSettled, not all: if the mounts call fails (403/404/network) we can
      // still fall back to the first brain. With Promise.all a failing mounts
      // call threw away a perfectly good brain list and showed "kein Second
      // Brain gefunden".
      const [mountsRes, brainsRes] = await Promise.allSettled([
        api.getAgentMounts(agentId),
        api.listSecondBrains(),
      ]);
      if (brainsRes.status === "fulfilled") {
        const all = brainsRes.value;
        const labels = mountsRes.status === "fulfilled"
          ? (mountsRes.value.mounts || []).filter((l) => l.startsWith("brain-"))
          : [];
        brainId = all.find((b) => labels.includes(b.label))?.id ?? all[0]?.id ?? null;
      }
      setGraphOverlay({ brainId, query: query?.trim() || undefined });
      return;
    }
    // Show an app page. `target` is LLM output (untrusted) → only allow a known
    // route, or a strictly-internal path: single leading slash + alnum first char, no
    // "//host" (open redirect), no backslash/colon/dots.
    const isSafeInternal = /^\/[a-zA-Z0-9][a-zA-Z0-9/_-]*$/.test(t);
    const path = NAV_ROUTES[t] || (isSafeInternal ? t : null);
    // Show it INSIDE the cockpit, exactly like the knowledge graph — navigating away
    // would unmount this component and cut the microphone mid-sentence. That was the
    // whole bug: the voice assistant could open a page, and thereby silence itself.
    if (path) setPageOverlay({ path, label: NAV_LABELS[t] || t });
  }, [agentId]);

  const modeRef = useRef<Mode>("classic");
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const playQueueRef = useRef<Promise<void>>(Promise.resolve());
  const voiceLanguageRef = useRef("de");

  // Realtime (Nova Sonic) audio graph
  const inCtxRef = useRef<AudioContext | null>(null);
  const procRef = useRef<ScriptProcessorNode | null>(null);
  const srcNodeRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const outCtxRef = useRef<AudioContext | null>(null);
  const gainNodeRef = useRef<GainNode | null>(null);
  const volumeRef = useRef(1);
  const nextPlayRef = useRef(0);
  const liveSourcesRef = useRef<AudioBufferSourceNode[]>([]);
  const suppressAudioRef = useRef(false);
  const suppressTimerRef = useRef<number | undefined>(undefined);

  // ── WebSocket connect (with auto-reconnect) ─────────────────
  useEffect(() => {
    let cancelled = false;
    closingRef.current = false;
    // Stable session id so a reconnect resumes the SAME conversation. Reuse the caller's
    // resume id, or mint one for this call.
    if (!voiceSessionRef.current) {
      const rnd = (crypto as Crypto & { randomUUID?: () => string }).randomUUID?.() || Math.random().toString(36).slice(2);
      voiceSessionRef.current = resumeSessionId || `voice-${rnd}`;
    }

    const scheduleReconnect = () => {
      if (cancelled || closingRef.current) return;
      if (reconnectsRef.current >= MAX_VOICE_RECONNECTS) {
        setState("error");
        setError("Sprachverbindung verloren. Bitte neu starten.");
        return;
      }
      reconnectsRef.current += 1;
      setState("connecting");
      wsReconnectTimer.current = window.setTimeout(() => { void connectWs(); }, 600);
    };

    const connectWs = async () => {
      if (cancelled || closingRef.current) return;
      try {
        let ticket: string;
        if (getTicket) {
          ticket = await getTicket();
          voiceLanguageRef.current = "de";
        } else {
          const token = localStorage.getItem("token");
          const tr = await fetch(`${getBase()}/ws/ticket`, {
            method: "POST",
            headers: { Authorization: `Bearer ${token}` },
          });
          if (!tr.ok) throw new Error("ticket failed");
          ticket = (await tr.json()).ticket;
          try {
            const cfg = await fetch(`${getBase()}/settings/voice`, {
              headers: { Authorization: `Bearer ${token}` },
            });
            if (cfg.ok) {
              const voice = await cfg.json();
              voiceLanguageRef.current = voice.language || "de";
            }
          } catch {
            voiceLanguageRef.current = "de";
          }
        }
        if (cancelled || closingRef.current) return;
        const url = `${getWsUrl()}/api/v1/ws/agents/${agentId}/voice?ticket=${ticket}&chat_session=${encodeURIComponent(voiceSessionRef.current)}`;
        const ws = new WebSocket(url);
        wsRef.current = ws;
        ws.onopen = () => setError(null);
        ws.onmessage = (e) => handleServerEvent(e.data);
        ws.onerror = () => { /* onclose drives the reconnect */ };
        ws.onclose = () => { scheduleReconnect(); };
      } catch {
        scheduleReconnect();
      }
    };

    void connectWs();
    return () => {
      cancelled = true;
      closingRef.current = true;
      if (wsReconnectTimer.current) window.clearTimeout(wsReconnectTimer.current);
      const ws = wsRef.current;
      if (ws) { ws.onclose = null; ws.close(); }
      stopRecording();
      teardownRealtime();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId]);


  // Nur fuer aufgeklappte, noch laufende Aufgaben nachladen — zugeklappt oder fertig
  // kostet es nichts. Ende der Aufgabe beendet das Nachladen von selbst.
  useEffect(() => {
    const live = tasks.filter((t) => !t.done && t.id && openTasks.has(t.id));
    if (live.length === 0) return;
    let stop = false;
    const pull = async () => {
      for (const t of live) {
        try {
          const { steps } = await api.getTaskSteps(t.id);
          if (stop) return;
          const lines = steps
            .map((st) => {
              const d = (st.data || {}) as Record<string, unknown>;
              if (st.type === "tool_call") return `nutzt ${String(d.tool || "ein Werkzeug")}`;
              const txt = String(d.text || d.message || "").trim();
              return txt ? txt.slice(0, 160) : "";
            })
            .filter(Boolean)
            .slice(-8);
          setTaskSteps((prev) => ({ ...prev, [t.id]: lines }));
        } catch {
          // Schritte sind Beiwerk — ein Fehler darf das Gespraech nie stoeren.
        }
      }
    };
    pull();
    const iv = setInterval(pull, 3000);
    return () => { stop = true; clearInterval(iv); };
  }, [tasks, openTasks]);

  // Auto-scroll the live activity log to the newest line.
  useEffect(() => {
    if (activityRef.current) activityRef.current.scrollTop = activityRef.current.scrollHeight;
  }, [activity]);

  // Auto-scroll the conversation transcript to the newest turn.
  useEffect(() => {
    if (transcriptRef.current) transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight;
  }, [turns, transcript]);

  // ── Server event handler ───────────────────────────────────
  const handleServerEvent = useCallback(async (raw: string) => {
    let evt: { type: string; data?: Record<string, unknown> };
    try {
      evt = JSON.parse(raw);
    } catch {
      return;
    }
    const data = evt.data || {};
    switch (evt.type) {
      case "ready": {
        const m: Mode = data.mode === "nova_sonic" ? "nova_sonic" : "classic";
        modeRef.current = m;
        setMode(m);
        setState("ready");
        if (m === "nova_sonic") startLive();
        break;
      }
      case "ui_command":
        // Agent drives the app UI by voice: open/close an overlay, or navigate.
        handleUiCommand(String(data.action || ""), String(data.target || ""), data.query ? String(data.query) : undefined);
        break;
      case "transcript": {
        reconnectsRef.current = 0; // real conversation data → healthy session
        const spokenText = String(data.text || "");
        setTranscript(spokenText);
        if (modeRef.current === "classic") {
          setState("processing");
        } else {
          suppressAudioRef.current = false;  // new user turn -> allow the response audio
          upsertTurn("user", spokenText);
          // Der Zwischenstand hat seinen Zweck erfuellt, sobald der Zug in der Liste
          // steht. Blieb er stehen, klebte er als kursive Blase UNTEN fest, waehrend
          // neue Nachrichten darueber einsortiert wurden — beim Dazwischenreden sah es
          // aus, als stuende die Reihenfolge auf dem Kopf.
          setTranscript("");
        }
        // #474: resolve a pending approval from what the user just said, since
        // clicking is the wrong interaction in voice mode.
        const approval = pendingApprovalRef.current;
        if (approval) {
          const intent = matchApprovalIntent(spokenText, approval);
          if (intent) void decideApprovalRef.current(intent === "approve");
        }
        break;
      }
      case "response":
        // Fires for MY OWN speech AND for delegation reports — must NOT flip a task to
        // "erledigt" here (that's delegate_done's job), else a task reads done while it
        // is still running.
        setResponse(String(data.text || ""));
        if (modeRef.current === "nova_sonic") upsertTurn("assistant", String(data.text || ""));
        break;
      case "tool_call":
        setToolLog((prev) => [
          ...prev.slice(-19),
          { name: String(data.name || ""), input: String(data.input || ""), done: false },
        ]);
        break;
      case "tool_result": {
        const name = String(data.name || "");
        setToolLog((prev) => {
          const next = [...prev];
          for (let i = next.length - 1; i >= 0; i--) {
            if (next[i].name === name && !next[i].done) {
              next[i] = { ...next[i], output: String(data.output || ""), done: true };
              return next;
            }
          }
          return [...next.slice(-19), { name, output: String(data.output || ""), done: true }];
        });
        break;
      }
      case "media":
        // Agent presented an image/file while working — show it in the Jarvis panel.
        setMedia((prev) =>
          [
            {
              kind: String(data.kind || ""),
              media_type: String(data.media_type || ""),
              b64: data.b64 ? String(data.b64) : undefined,
              filename: String(data.filename || ""),
              caption: String(data.caption || ""),
              items: Array.isArray((data as { items?: unknown }).items)
                ? ((data as { items: { title: string; time?: string; minutes?: number; priority?: string; status?: string; notes?: string }[] }).items)
                : undefined,
              path: data.path ? String(data.path) : undefined,
              url: safeHttpUrl(data.url),
              embeddable: Boolean(data.embeddable),
              auto_open: Boolean(data.auto_open),
            },
            ...prev,
          ].slice(0, 8)
        );
        break;
      case "web_results":
        setWebResults((prev) =>
          [
            {
              query: String(data.query || ""),
              results: Array.isArray(data.results)
                ? (data.results as WebResult[]).map((r) => ({
                    ...r,
                    url: safeHttpUrl(r.url) ?? "",
                  }))
                : [],
            },
            ...prev,
          ].slice(0, 5)
        );
        break;
      case "status":
        setStatusMsg(String(data.message || ""));
        break;
      case "delegate": {
        const instruction = String(data.instruction || "");
        const taskId = String(data.task_id || "");
        setStatusMsg(`Ich kümmere mich um: ${instruction}`);
        // Dedupe by task_id: a refine_task (correction to the SAME task) updates the
        // existing card instead of adding a new one — otherwise "one task" would show
        // as several cards. Genuinely new tasks get a fresh card.
        setTasks((prev) => {
          const idx = taskId ? prev.findIndex((t) => t.id === taskId) : -1;
          if (idx >= 0) {
            const copy = [...prev];
            copy[idx] = { ...copy[idx], instruction, done: false };
            return copy;
          }
          return [...prev, { id: taskId, instruction, done: false }];
        });
        break;
      }
      case "delegate_done": {
        const taskId = String(data.task_id || "");
        const instruction = String(data.instruction || "");
        const result = String(data.result || "");
        setTasks((prev) => {
          let flipped = false;
          const next = prev.map((t) => {
            if (flipped || t.done) return t;
            const match = taskId ? t.id === taskId : t.instruction === instruction;
            if (match) {
              flipped = true;
              return { ...t, done: true, result: result || t.result };
            }
            return t;
          });
          // Kein Treffer? Dann hat ein Weg die Aufgabe nie angemeldet. Statt die
          // Fertigmeldung zu verschlucken (so blieb das Panel leer, obwohl die
          // Aufgabe lief und fertig wurde) zeigen wir sie als erledigte Karte.
          if (!flipped && (instruction || taskId)) {
            return [...next, { id: taskId, instruction, done: true, result }];
          }
          return next;
        });
        break;
      }
      case "activity": {
        // Live view of the delegated agent's work — same chat-stream events the
        // text chat / LiveTerminal render (tool_call / text), surfaced in real time.
        const kind = String(data.kind || "");
        if (kind === "tool_result") break; // result just confirms the tool; no new row
        const item =
          kind === "tool"
            ? { kind, label: String(data.tool || "Tool"), detail: String(data.input || "") }
            : { kind: "text", label: String(data.text || ""), detail: "" };
        if (!item.label) break;
        setActivity((prev) => {
          const next = [...prev, item];
          return next.length > 40 ? next.slice(-40) : next;
        });
        break;
      }
      case "tts_start":
        setState("speaking");
        break;
      case "clear_audio":
        flushPlayback();
        break;
      case "audio_chunk": {
        reconnectsRef.current = 0; // agent speaking → healthy session
        const b64 = String(data.b64 || "");
        if (!b64) break;
        if (modeRef.current === "nova_sonic" || data.mime === "audio/pcm") {
          if (suppressAudioRef.current) break;
          setState("speaking");
          playPcmChunk(b64, Number(data.rate) || 24000);
        } else {
          playMp3Chunk(b64, String(data.mime || "audio/mpeg"));
        }
        break;
      }
      case "tts_end":
        playQueueRef.current.then(() => setState((s) => (s === "speaking" ? "ready" : s)));
        break;
      case "done":
        if (modeRef.current === "classic") {
          playQueueRef.current.then(() => setState("ready"));
        } else {
          // Realtime stream ended (usually the AWS-CRT drop). The socket closes right
          // after → the connect effect reconnects and resumes the same chat_session.
          // Show "reconnecting" instead of dead-ending.
          setState("connecting");
        }
        break;
      case "error":
        setError(String(data.message || "Fehler"));
        setState("error");
        break;
    }
  }, []);

  // ── Classic MP3 playback ─────────────────────────────────────
  const playMp3Chunk = useCallback((b64: string, mime: string) => {
    playQueueRef.current = playQueueRef.current.then(async () => {
      try {
        const bin = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
        const url = URL.createObjectURL(new Blob([bin], { type: mime }));
        await new Promise<void>((res) => {
          const audio = new Audio(url);
          audio.onended = () => {
            URL.revokeObjectURL(url);
            res();
          };
          audio.onerror = () => {
            URL.revokeObjectURL(url);
            res();
          };
          void audio.play().catch(() => res());
        });
      } catch {
        /* ignore single-chunk errors */
      }
    });
  }, []);

  // ── Realtime PCM playback (24 kHz, gapless scheduled) ────────
  const ensureOutCtx = useCallback((): AudioContext => {
    if (!outCtxRef.current || outCtxRef.current.state === "closed") {
      const ctx = new AudioContext();
      // Route playback through a GainNode so volume is adjustable — crucially this
      // works on iOS Safari, which ignores HTMLMediaElement.volume.
      const gain = ctx.createGain();
      gain.gain.value = volumeRef.current;
      gain.connect(ctx.destination);
      gainNodeRef.current = gain;
      outCtxRef.current = ctx;
      nextPlayRef.current = 0;
    }
    return outCtxRef.current;
  }, []);

  const playPcmChunk = useCallback((b64: string, rate: number) => {
    try {
      const bin = atob(b64);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      const pcm = new Int16Array(bytes.buffer, 0, bytes.length >> 1);
      const ctx = ensureOutCtx();
      const buf = ctx.createBuffer(1, pcm.length, rate);
      const ch = buf.getChannelData(0);
      for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 0x8000;
      const node = ctx.createBufferSource();
      node.buffer = buf;
      node.connect(gainNodeRef.current ?? ctx.destination);
      const t = Math.max(ctx.currentTime + 0.02, nextPlayRef.current);
      node.start(t);
      nextPlayRef.current = t + buf.duration;
      liveSourcesRef.current.push(node);
      node.onended = () => {
        liveSourcesRef.current = liveSourcesRef.current.filter((n) => n !== node);
        if (modeRef.current === "nova_sonic" && liveSourcesRef.current.length === 0) {
          setState((s) => (s === "speaking" ? "listening" : s));
        }
      };
    } catch {
      /* ignore */
    }
  }, [ensureOutCtx]);

  const flushPlayback = useCallback(() => {
    liveSourcesRef.current.forEach((n) => {
      try {
        n.stop();
      } catch {
        /* already stopped */
      }
    });
    liveSourcesRef.current = [];
    nextPlayRef.current = 0;
  }, []);

  // Barge-in: stop the agent NOW and drop audio still arriving from the
  // interrupted turn (Nova Sonic keeps streaming a moment after the user cuts in).
  // Suppression lifts on the next user transcript (= new turn) or a safety timer.
  const beginBargeIn = useCallback(() => {
    flushPlayback();
    suppressAudioRef.current = true;
    // Tell the server to SKIP the rest of the interrupted turn — server-side it now
    // drops ALL audio until a genuinely new USER turn, so nothing resumes speaking.
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "interrupt" }));
    }
    // The real lift is the next USER transcript (a new turn). This timer is only a
    // stuck-state fallback — long enough that Nova's 1–2 s look-ahead buffer is fully
    // discarded before we'd ever accept audio again.
    if (suppressTimerRef.current) window.clearTimeout(suppressTimerRef.current);
    suppressTimerRef.current = window.setTimeout(() => {
      suppressAudioRef.current = false;
    }, 6000);
  }, [flushPlayback]);

  // ── Realtime capture (continuous 16 kHz PCM) ─────────────────
  const startLive = useCallback(async () => {
    if (inCtxRef.current) return; // already live
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        setError("Kein Mikrofon-Zugriff (kein sicherer Kontext / mediaDevices fehlt)");
        setState("error");
        return;
      }
      let stream: MediaStream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
        });
      } catch (e) {
        // Fall back to the simplest constraint if the device rejects the extras
        // (OverconstrainedError on some USB mics) — then rethrow if that fails too.
        if ((e as Error)?.name === "OverconstrainedError" || (e as Error)?.name === "NotFoundError") {
          stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        } else {
          throw e;
        }
      }
      streamRef.current = stream;
      const ctx = new AudioContext();
      inCtxRef.current = ctx;
      const source = ctx.createMediaStreamSource(stream);
      srcNodeRef.current = source;
      const proc = ctx.createScriptProcessor(4096, 1, 1);
      procRef.current = proc;
      let vadHigh = 0;
      proc.onaudioprocess = (e) => {
        if (wsRef.current?.readyState !== WebSocket.OPEN) return;
        const input = e.inputBuffer.getChannelData(0);
        // Barge-in: if the agent is speaking and the user starts talking, stop the
        // agent's (buffered) audio immediately so the user can cut in. Echo from the
        // speakers is largely removed by echoCancellation; require a few consecutive
        // loud frames to avoid false triggers.
        if (liveSourcesRef.current.length > 0) {
          let sum = 0;
          for (let i = 0; i < input.length; i++) sum += input[i] * input[i];
          const rms = Math.sqrt(sum / input.length);
          vadHigh = rms > 0.025 ? vadHigh + 1 : 0;
          if (vadHigh >= 2) {
            beginBargeIn();
            setState("listening");
            vadHigh = 0;
          }
        }
        const ds = downsample(input, ctx.sampleRate, 16000);
        const b64 = bufToBase64(floatTo16LE(ds));
        wsRef.current.send(JSON.stringify({ type: "audio_chunk", data: { b64 } }));
      };
      source.connect(proc);
      proc.connect(ctx.destination); // required for onaudioprocess to fire
      setLive(true);
      setState("listening");
    } catch (e) {
      const name = (e as Error)?.name || "";
      const msg = (e as Error)?.message || "";
      setError(`Mikrofon-Fehler: ${name || "unbekannt"}${msg ? ` — ${msg}` : ""}`);
      setState("error");
    }
  }, []);

  const teardownRealtime = useCallback(() => {
    try {
      procRef.current?.disconnect();
      srcNodeRef.current?.disconnect();
    } catch {
      /* noop */
    }
    procRef.current = null;
    srcNodeRef.current = null;
    if (inCtxRef.current && inCtxRef.current.state !== "closed") {
      void inCtxRef.current.close();
    }
    inCtxRef.current = null;
    flushPlayback();
    if (outCtxRef.current && outCtxRef.current.state !== "closed") {
      void outCtxRef.current.close();
    }
    outCtxRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    setLive(false);
  }, [flushPlayback]);

  const endLive = useCallback(() => {
    teardownRealtime();
    wsRef.current?.close();
    onEnd?.();
    if (!onEnd) onClose();
  }, [teardownRealtime, onClose, onEnd]);

  const bargeIn = useCallback(() => {
    beginBargeIn(); // already sends the interrupt to the server
    setState("listening");
  }, [beginBargeIn]);

  // ── Classic push-to-talk recording ──────────────────────────
  const startRecording = useCallback(async () => {
    if (state !== "ready" || !wsRef.current) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus"
        : "audio/webm";
      const rec = new MediaRecorder(stream, { mimeType: mime });
      recorderRef.current = rec;
      rec.ondataavailable = async (e) => {
        if (!e.data || e.data.size === 0) return;
        if (wsRef.current?.readyState !== WebSocket.OPEN) return;
        const buf = await e.data.arrayBuffer();
        wsRef.current.send(JSON.stringify({ type: "audio_chunk", data: { b64: bufToBase64(buf) } }));
      };
      rec.start(250);
      setState("listening");
    } catch {
      setError("Mikrofon-Zugriff verweigert");
      setState("error");
    }
  }, [state]);

  const stopRecording = useCallback(() => {
    recorderRef.current?.stop();
    recorderRef.current = null;
    if (modeRef.current === "classic") {
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
  }, []);

  const commitTurn = useCallback(() => {
    stopRecording();
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "commit", data: { language: voiceLanguageRef.current } }));
      setState("processing");
    }
  }, [stopRecording]);

  const interrupt = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "interrupt" }));
    }
    setState("ready");
  }, []);

  // ── UI ────────────────────────────────────────────────────
  const isRealtime = mode === "nova_sonic";
  // Embedded in the Speech tab we get a full-height area, so let the panes grow tall
  // and fill it; as a modal we keep the compact viewport-fraction heights.
  // The newest visual the agent pushed — it gets the big stage under the orb.
  // Files stay in the right-hand activity pane (they're downloads, not visuals).
  const stageItem = media.find((m) => m.kind === "image" || m.kind === "web");
  // Groesse des Overlays: der Nutzer zieht sie sich zurecht, wir merken sie uns.
  // Vorher war das Fenster fest (max-w-6xl) — bei langen Zusammenfassungen scrollte
  // man in einer schmalen Spalte, obwohl der Bildschirm leer daneben lag.
  const SIZE_KEY = "voice-overlay-size";
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const [maximized, setMaximized] = useState(false);
  const dragRef = useRef<{ x: number; y: number; w: number; h: number } | null>(null);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(SIZE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw) as { w: number; h: number };
        if (parsed?.w > 320 && parsed?.h > 240) setSize(parsed);
      }
    } catch { /* kaputter Eintrag → Standardgroesse */ }
  }, []);

  const startResize = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const box = (e.currentTarget as HTMLElement).parentElement?.getBoundingClientRect();
    dragRef.current = {
      x: e.clientX, y: e.clientY,
      w: size?.w ?? box?.width ?? 1100,
      h: size?.h ?? box?.height ?? 700,
    };
    const onMove = (ev: MouseEvent) => {
      const d = dragRef.current;
      if (!d) return;
      // Untergrenze, damit man das Fenster nicht unbedienbar klein zieht; Obergrenze
      // ist der sichtbare Bereich.
      setSize({
        w: Math.min(Math.max(d.w + (ev.clientX - d.x), 420), window.innerWidth - 32),
        h: Math.min(Math.max(d.h + (ev.clientY - d.y), 320), window.innerHeight - 32),
      });
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      dragRef.current = null;
      setSize((cur) => {
        if (cur) { try { localStorage.setItem(SIZE_KEY, JSON.stringify(cur)); } catch { /* egal */ } }
        return cur;
      });
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  // Sobald der Nutzer die Groesse selbst gesetzt hat, duerfen die Spalten NICHT mehr
  // an Bildschirmprozenten haengen — sonst waechst der Rahmen und der Inhalt bleibt
  // oben kleben, mit einer leeren Flaeche darunter.
  const sized = !embedded && (maximized || !!size);
  const paneMedia = media.filter((m) => m.kind !== "image" && m.kind !== "web");
  const paneHeight = embedded
    ? "min-h-[50vh] lg:min-h-[68vh] lg:max-h-[74vh]"
    : sized
    ? "h-full min-h-0"
    : "max-h-[42vh] min-h-[26vh] lg:max-h-[60vh] lg:min-h-[48vh]";
  return (
    <div
      className={embedded
        ? `${hidden ? "hidden " : ""}w-full h-full`
        : `${hidden ? "hidden " : ""}fixed inset-0 z-50 flex items-stretch justify-center bg-background/80 backdrop-blur-sm sm:items-center sm:p-4`}
      onClick={embedded ? undefined : onClose}
    >
      <div
        className={embedded
          ? "relative flex h-full w-full flex-col rounded-2xl border border-border bg-card"
          : `relative flex w-full flex-col ${sized ? "overflow-hidden" : "overflow-y-auto"} border-border bg-card shadow-2xl h-[100dvh] max-h-[100dvh] rounded-none sm:h-auto sm:max-h-[90vh] sm:rounded-2xl sm:border ${
              maximized ? "sm:max-w-none" : isRealtime ? "max-w-6xl" : "max-w-lg"
            }`}
        style={
          embedded
            ? undefined
            : maximized
            ? { width: "calc(100vw - 2rem)", height: "calc(100vh - 2rem)", maxHeight: "none" }
            : size
            ? { width: size.w, height: size.h, maxWidth: "none", maxHeight: "none" }
            : undefined
        }
        onDoubleClick={embedded ? undefined : (e) => {
          // Doppelklick auf die Kopfzeile (nicht auf Inhalte) schaltet Vollbild um.
          if ((e.target as HTMLElement).closest("[data-voice-header]")) setMaximized((v) => !v);
        }}
        onClick={embedded ? undefined : (e) => e.stopPropagation()}
      >
        {!embedded && (
          <div
            onMouseDown={startResize}
            title="Ziehen zum Vergrössern — Doppelklick auf die Kopfzeile für Vollbild"
            className="absolute bottom-0 right-0 z-20 hidden h-5 w-5 cursor-nwse-resize items-end justify-end p-1 text-muted-foreground/40 hover:text-foreground sm:flex"
          >
            <svg viewBox="0 0 10 10" className="h-3 w-3 fill-current">
              <path d="M9 1v8H1z" opacity=".35" />
              <path d="M9 5v4H5z" />
            </svg>
          </div>
        )}
        {!embedded && (
          <button
            onClick={onClose}
            className="absolute top-3 right-3 rounded-md p-1 text-muted-foreground hover:bg-foreground/[0.06]"
            aria-label="Schließen"
          >
            <X className="h-4 w-4" />
          </button>
        )}

        <div className={embedded
          ? "flex min-h-0 flex-1 flex-col overflow-y-auto p-4 sm:p-6 lg:p-8"
          : sized ? "flex min-h-0 flex-1 flex-col p-4 sm:p-6" : "p-4 sm:p-6"}>
          <div data-voice-header className="mb-4 flex items-center gap-2 pr-8 select-none">
            <div className="min-w-0">
              <h2 className="text-lg font-semibold truncate">
                {isRealtime ? "Live-Gespräch" : "Live-Session"}: {agentName}
              </h2>
              <p className="text-xs text-muted-foreground/70 mt-0.5">
                {isRealtime
                  ? "Sprich einfach los — der Agent hört durchgehend zu und antwortet in Echtzeit."
                  : "Halte den Knopf gedrückt zum Sprechen, oder klicke einmal zum Toggeln."}
              </p>
            </div>
            {isRealtime && (
              <span className="ml-auto inline-flex items-center gap-1 rounded-full bg-fuchsia-500/10 px-2 py-1 text-[10px] font-medium text-fuchsia-400">
                <Radio className="h-3 w-3" /> Realtime
              </span>
            )}
          </div>

          {/* Freigabe-Anfrage (#474) — ueber beiden Modi, damit sie im Sprachchat
              nicht in der durchlaufenden Live-Aktivitaet untergeht. */}
          {pendingApproval && (
            <div className="mb-4 rounded-xl border border-amber-500/30 bg-amber-500/10 p-4">
              <div className="flex items-start gap-3">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-amber-300">Der Agent braucht deine Freigabe</p>
                  <p className="mt-1 text-sm text-foreground/90 break-words">
                    {pendingApproval.question
                      || pendingApproval.reasoning
                      || pendingApproval.tool
                      || "Freigabe erforderlich"}
                  </p>
                  {pendingApproval.context && (
                    <p className="mt-1 text-xs text-muted-foreground break-words">{pendingApproval.context}</p>
                  )}
                  {pendingApproval.tool && pendingApproval.question && (
                    <p className="mt-1 text-[11px] text-amber-400/70 font-mono">{pendingApproval.tool}</p>
                  )}
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button
                      onClick={() => decideApproval(true)}
                      disabled={approvalBusy}
                      className="flex items-center gap-1.5 rounded-lg bg-emerald-500/20 px-3 py-1.5 text-sm font-medium text-emerald-400 hover:bg-emerald-500/30 disabled:opacity-50 transition-colors"
                    >
                      {approvalBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                      {pendingApproval.options?.[0] || "Freigeben"}
                    </button>
                    <button
                      onClick={() => decideApproval(false)}
                      disabled={approvalBusy}
                      className="rounded-lg bg-red-500/15 px-3 py-1.5 text-sm font-medium text-red-400 hover:bg-red-500/25 disabled:opacity-50 transition-colors"
                    >
                      {pendingApproval.options?.[1] || "Ablehnen"}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}

          {!isRealtime && <StatusPill state={state} realtime={isRealtime} />}

          {isRealtime ? (
            /* ── Jarvis: 3-pane realtime cockpit (Gespräch | Präsenz | Aufgaben) ── */
            <>
            <div className={`mt-4 grid grid-cols-1 gap-4 lg:grid-cols-[1fr_minmax(280px,1.1fr)_1fr] lg:items-stretch${sized ? " min-h-0 flex-1" : ""}`}>
              {/* LEFT — conversation transcript, doubles as the file drop zone */}
              <div
                onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                onDragLeave={(e) => { e.preventDefault(); setDragOver(false); }}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragOver(false);
                  handleUpload(e.dataTransfer.files);
                }}
                className={`relative order-2 flex ${paneHeight} min-w-0 flex-col rounded-xl border bg-foreground/[0.02] lg:order-1 transition-colors ${
                  dragOver ? "border-primary/60 bg-primary/[0.04]" : "border-border"
                }`}
              >
                {dragOver && (
                  <div className="pointer-events-none absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 rounded-xl bg-background/70 backdrop-blur-[1px]">
                    <Paperclip className="h-6 w-6 text-primary" />
                    <p className="text-xs font-medium text-primary">Datei hier ablegen</p>
                    <p className="text-[11px] text-muted-foreground/60">Ich frage dann, was ich damit tun soll</p>
                  </div>
                )}
                <div className="flex items-center justify-between border-b border-border px-3 py-2">
                  <span className="text-[10px] uppercase tracking-wider text-muted-foreground/60">Gespräch</span>
                  <input
                    ref={fileInputRef}
                    type="file"
                    multiple
                    className="hidden"
                    onChange={(e) => handleUpload(e.target.files)}
                  />
                  <button
                    onClick={() => fileInputRef.current?.click()}
                    disabled={uploading}
                    className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-muted-foreground/70 hover:bg-foreground/[0.06] hover:text-foreground disabled:opacity-50"
                    title="Datei in den Workspace laden (oder einfach hierher ziehen)"
                  >
                    {uploading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Paperclip className="h-3.5 w-3.5" />}
                    Datei
                  </button>
                </div>
                <div ref={transcriptRef} className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
                  {turns.length === 0 ? (
                    <p className="text-xs text-muted-foreground/50">Sprich einfach los …</p>
                  ) : (
                    turns.map((t, i) => (
                      <div key={i} className={t.role === "user" ? "text-right" : "text-left"}>
                        <div
                          className={`inline-block max-w-[92%] rounded-2xl px-3 py-1.5 text-sm ${
                            t.role === "user"
                              ? "bg-fuchsia-500/15 text-foreground"
                              : "border border-primary/20 bg-primary/10 text-foreground"
                          }`}
                        >
                          {linkify(t.text)}
                        </div>
                      </div>
                    ))
                  )}
                  {transcript && state === "listening" &&
                   turns[turns.length - 1]?.text !== transcript && (
                    <div className="text-right">
                      <div className="inline-block max-w-[92%] rounded-2xl bg-fuchsia-500/10 px-3 py-1.5 text-sm italic text-muted-foreground">
                        {transcript}…
                      </div>
                    </div>
                  )}
                </div>
              </div>

              {/* CENTER — presence, quiet call controls, and the stage below */}
              <div className="order-1 flex min-w-0 flex-col items-center gap-3.5 py-2 lg:order-2">
                <JarvisCore state={state} compact />
                <StatusPill state={state} realtime focus={paused} working={delegating} />
                {statusMsg && state !== "error" && (
                  <p className="max-w-[240px] text-center text-xs text-muted-foreground/70">{statusMsg}</p>
                )}
                {paused && (
                  <p className="max-w-[260px] text-center text-xs text-amber-400/90">
                    Fokus-Modus: Mikro aus — ich arbeite weiter und melde mich, wenn etwas fertig ist.
                  </p>
                )}

                {/* Call controls — round, icon-only; they recede until you look for them */}
                <div className="flex items-center gap-2">
                  {state === "speaking" && (
                    <CtrlButton onClick={bargeIn} title="Unterbrechen">
                      <Hand className="h-4 w-4" />
                    </CtrlButton>
                  )}
                  <CtrlButton
                    onClick={togglePause}
                    title={paused ? "Fortsetzen" : "Fokus-Modus (Mikro aus)"}
                    tone={paused ? "amber" : "neutral"}
                  >
                    {paused ? <Play className="h-4 w-4" /> : <Pause className="h-4 w-4" />}
                  </CtrlButton>
                  <CtrlButton onClick={openMeeting} title="Meeting aufnehmen (Agent hört nicht mit)">
                    <ClipboardList className="h-4 w-4" />
                  </CtrlButton>
                  <CtrlButton onClick={endLive} title="Gespräch beenden" tone="red">
                    <PhoneOff className="h-4 w-4" />
                  </CtrlButton>
                </div>

                {/* Playback volume — GainNode-based so it also works on iOS Safari */}
                <div className="flex w-36 items-center gap-2 opacity-60 transition-opacity hover:opacity-100">
                  <Volume2 className="h-3.5 w-3.5 shrink-0 text-muted-foreground/60" />
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.05}
                    value={volume}
                    onChange={(e) => changeVolume(Number(e.target.value))}
                    aria-label="Lautstärke"
                    className="h-0.5 flex-1 cursor-pointer accent-emerald-500"
                  />
                </div>

                {/* THE STAGE — whatever the agent is showing right now, big */}
                {stageItem && (
                  <div className="relative w-full max-w-md rounded-xl border border-border bg-foreground/[0.02] p-3">
                    {/* Ohne das bleibt ein Screenshot bis zum Sitzungsende stehen und
                        verdeckt alles, was danach kommt. */}
                    <button
                      onClick={() => setMedia((prev) => prev.filter((m) => m !== stageItem))}
                      title="Ausblenden"
                      aria-label="Anzeige ausblenden"
                      className="absolute right-2 top-2 z-10 rounded-md bg-background/70 p-1 text-muted-foreground backdrop-blur hover:bg-background hover:text-foreground"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                    {stageItem.kind === "image" && stageItem.b64 ? (
                      <>
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          src={`data:${stageItem.media_type || "image/png"};base64,${stageItem.b64}`}
                          alt={stageItem.caption || "Anzeige"}
                          className="max-h-72 w-full rounded-lg bg-white/5 object-contain"
                        />
                        {stageItem.caption && (
                          <p className="mt-2 text-center text-xs text-muted-foreground/70">{stageItem.caption}</p>
                        )}
                      </>
                    ) : stageItem.url ? (
                      <div className="space-y-2">
                        <div className="flex items-start gap-2">
                          <Globe className="mt-0.5 h-4 w-4 shrink-0 text-sky-400" />
                          <div className="min-w-0">
                            {stageItem.caption && <p className="text-sm text-foreground/90">{stageItem.caption}</p>}
                            <p className="truncate text-[11px] text-muted-foreground/60">{stageItem.url}</p>
                          </div>
                        </div>
                        {blockedUrls.has(stageItem.url) && (
                          <p className="text-[11px] text-amber-400/90">
                            Dein Browser hat den Tab blockiert — hier klicken zum Öffnen.
                          </p>
                        )}
                        <div className="flex flex-wrap gap-1.5">
                          {stageItem.embeddable && (
                            <button
                              onClick={() => setWebModal({ url: stageItem.url!, caption: stageItem.caption })}
                              className="rounded-md bg-sky-500/10 px-2.5 py-1 text-[11px] font-medium text-sky-300 hover:bg-sky-500/20"
                            >
                              Im Fenster öffnen
                            </button>
                          )}
                          <a
                            href={stageItem.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-1 rounded-md bg-foreground/[0.06] px-2.5 py-1 text-[11px] font-medium hover:bg-foreground/[0.10]"
                          >
                            <ExternalLink className="h-3 w-3" /> Neuer Tab
                          </a>
                        </div>
                      </div>
                    ) : null}
                  </div>
                )}

                {uploadMsg && (
                  <div className="text-center text-xs text-emerald-400/90">{uploadMsg}</div>
                )}
                {error && <div className="text-center text-xs text-red-400">{error}</div>}
              </div>

              {/* RIGHT — tasks, live activity, web results */}
              <div className={`order-3 flex ${paneHeight} min-w-0 flex-col rounded-xl border border-border bg-foreground/[0.02]`}>
                <div className="border-b border-border px-3 py-2 text-[10px] uppercase tracking-wider text-muted-foreground/60">
                  Aufgaben &amp; Aktivität
                </div>
                <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
                  {/* Werkzeug-Spur: sichtbar machen, dass und WOMIT er gearbeitet hat. */}
                  {toolLog.length > 0 && (
                    <div className="space-y-1">
                      {toolLog.slice(-8).map((t, ti) => (
                        <div
                          key={ti}
                          className="rounded-md border border-border bg-foreground/[0.02] px-2 py-1 text-[10px]"
                        >
                          <div className="flex items-center gap-1.5">
                            {t.done ? (
                              <CheckCircle2 className="h-3 w-3 shrink-0 text-emerald-500" />
                            ) : (
                              <Loader2 className="h-3 w-3 shrink-0 animate-spin text-sky-500" />
                            )}
                            <span className="font-mono text-[10px] text-foreground/80">{t.name}</span>
                          </div>
                          {t.input && (
                            <div className="mt-0.5 truncate pl-4 text-muted-foreground/60">
                              → {t.input}
                            </div>
                          )}
                          {t.output && (
                            <div className="mt-0.5 line-clamp-2 pl-4 text-muted-foreground/80">
                              {t.output}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                  {paneMedia.map((m, mi) => (
                    <div key={mi} className="rounded-lg border border-border bg-foreground/[0.03] p-2">
                      {m.kind === "plan" && m.items ? (
                        /* Der Tagesplan als Karte. Ohne eigene Darstellung landete er in
                           der Datei-Zeile und der Nutzer sah nur „Datei" — kein Kalender. */
                        <div>
                          <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium text-foreground">
                            <CalendarClock className="h-3.5 w-3.5 text-sky-400" />
                            {m.caption || "Tagesplan"}
                          </div>
                          <div className="space-y-1">
                            {m.items.map((it, ii) => (
                              <div
                                key={ii}
                                className={`flex items-start gap-2 rounded-md border-l-2 px-2 py-1 text-[11px] ${
                                  it.status === "done"
                                    ? "border-l-emerald-500/60 bg-emerald-500/[0.10] text-muted-foreground dark:border-l-emerald-400/60 dark:bg-emerald-400/[0.06]"
                                    : it.status === "running"
                                    ? "border-l-sky-500 bg-sky-500/[0.10] text-foreground dark:border-l-sky-400 dark:bg-sky-400/[0.08]"
                                    : it.status === "dropped"
                                    ? "border-l-foreground/20 bg-foreground/[0.02] text-muted-foreground/50 line-through"
                                    : "border-l-sky-500/40 bg-sky-500/[0.06] text-foreground/90 dark:border-l-sky-400/40 dark:bg-sky-400/[0.04]"
                                }`}
                              >
                                <span className="shrink-0 font-mono text-[10px] text-muted-foreground/70">
                                  {it.time || "--:--"}
                                </span>
                                <span className="min-w-0 flex-1">
                                  <span className="block truncate">{it.title}</span>
                                  <span className="text-[10px] text-muted-foreground/50">
                                    {it.status === "done"
                                      ? "erledigt"
                                      : it.status === "running"
                                      ? "läuft"
                                      : it.status === "dropped"
                                      ? "gestrichen"
                                      : "geplant"}
                                    {it.minutes ? ` · ${it.minutes} Min` : ""}
                                    {it.priority === "high" ? " · hohe Priorität" : ""}
                                  </span>
                                </span>
                              </div>
                            ))}
                          </div>
                        </div>
                      ) : m.path ? (
                        <button
                          onClick={async () => {
                            try {
                              const r = await fetch(
                                `${getBase()}/agents/${agentId}/files/download?path=${encodeURIComponent(m.path!)}`,
                                { credentials: "include" }
                              );
                              if (!r.ok) return;
                              const blob = await r.blob();
                              const u = URL.createObjectURL(blob);
                              const a = document.createElement("a");
                              a.href = u;
                              a.download = m.filename || "download";
                              document.body.appendChild(a);
                              a.click();
                              a.remove();
                              URL.revokeObjectURL(u);
                            } catch {
                              /* ignore */
                            }
                          }}
                          className="flex w-full items-center gap-2 text-left text-xs hover:opacity-80"
                          title="Herunterladen"
                        >
                          <FileText className="h-4 w-4 shrink-0 text-sky-400" />
                          <span className="truncate underline decoration-dotted underline-offset-2">
                            {m.filename || "Datei"}
                          </span>
                        </button>
                      ) : (
                        <div className="flex items-center gap-2 text-xs">
                          <FileText className="h-4 w-4 shrink-0 text-sky-400" />
                          <span className="truncate">{m.filename || "Datei"}</span>
                        </div>
                      )}
                      {/* Beim Tagesplan steht die Ueberschrift schon oben in der Karte. */}
                      {m.caption && m.kind !== "plan" && (
                        <div className="mt-1 text-[11px] text-muted-foreground/70">{m.caption}</div>
                      )}
                    </div>
                  ))}
                  {/* One card per delegated task — each with its own live status. */}
                  {tasks.map((t, ti) => {
                    const key = t.id || t.instruction;
                    // Ein Ergebnis kann seitenlang sein. Fertige Aufgaben zeigen daher
                    // nur den Titel; der Text kommt auf Klick. Laufende bleiben offen —
                    // dort steht ohnehin noch nichts, was Platz kostet.
                    const open = openTasks.has(key);
                    return (
                    <div
                      key={ti}
                      className={`flex items-start gap-2 rounded-lg border p-2.5 text-xs ${
                        t.done
                          ? "border-emerald-500/30 bg-emerald-500/[0.06]"
                          : "border-amber-500/30 bg-amber-500/[0.05]"
                      }`}
                    >
                      {t.done ? (
                        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" />
                      ) : (
                        <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-amber-400" />
                      )}
                      <div className="min-w-0 flex-1">
                        <div className="flex items-start gap-1.5">
                          <button
                            onClick={() => (t.done ? t.result : t.id) && setOpenTasks((prev) => {
                              const next = new Set(prev);
                              next.has(key) ? next.delete(key) : next.add(key);
                              return next;
                            })}
                            className={`min-w-0 flex-1 text-left ${(t.done ? t.result : t.id) ? "cursor-pointer" : "cursor-default"}`}
                          >
                            <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-muted-foreground/60">
                              {t.done ? "Erledigt" : "Läuft"}
                              {(t.done ? t.result : t.id) && (
                                open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />
                              )}
                            </div>
                            <div className="text-foreground/90">{t.instruction}</div>
                          </button>
                          {t.done && (
                            <button
                              onClick={() => setTasks((prev) => prev.filter((x) => (x.id || x.instruction) !== key))}
                              className="shrink-0 rounded p-0.5 text-muted-foreground/40 hover:text-foreground hover:bg-foreground/[0.08]"
                              title="Aufgabe ausblenden"
                            >
                              <X className="h-3 w-3" />
                            </button>
                          )}
                        </div>
                        {!t.done && open && (
                          <div className="mt-1 space-y-0.5 font-mono text-[10px] leading-relaxed text-muted-foreground/70">
                            {(taskSteps[t.id] || []).map((line, li) => (
                              <div key={li} className="truncate">· {line}</div>
                            ))}
                            {!(taskSteps[t.id] || []).length && (
                              <div className="opacity-60">Noch keine Schritte gemeldet…</div>
                            )}
                          </div>
                        )}
                        {t.done && t.result && open && (
                          <div className="mt-1 whitespace-pre-wrap break-words text-[11px] leading-relaxed text-muted-foreground/80">
                            {linkify(t.result)}
                          </div>
                        )}
                      </div>
                    </div>
                    );
                  })}
                  {activity.length > 0 && (
                    <div className="rounded-lg border border-border bg-black/40 p-2.5">
                      <button
                        onClick={() => setActivityOpen((o) => !o)}
                        className="mb-1.5 flex w-full items-center gap-1.5 text-[10px] uppercase tracking-wider text-muted-foreground/60 hover:text-muted-foreground/90"
                      >
                        {activityOpen ? (
                          <ChevronDown className="h-3 w-3" />
                        ) : (
                          <ChevronRight className="h-3 w-3" />
                        )}
                        Live-Aktivität
                      </button>
                      <div
                        ref={activityRef}
                        className={`max-h-52 overflow-y-auto font-mono text-[11px] leading-relaxed ${activityOpen ? "" : "hidden"}`}
                      >
                        {activity.map((a, i) => (
                          <div
                            key={i}
                            className={
                              a.kind === "header"
                                ? "mb-1 text-foreground/90"
                                : a.kind === "tool"
                                ? "text-sky-400"
                                : a.kind === "approval"
                                ? "text-amber-300"
                                : "text-muted-foreground"
                            }
                          >
                            {a.kind === "header" && (
                              <>
                                <span className="text-muted-foreground/60">Aufgabe: </span>
                                {a.label}
                              </>
                            )}
                            {a.kind === "tool" && (
                              <>
                                <span className="text-amber-400">[{a.label}]</span>
                                {a.detail && <span className="text-muted-foreground/70"> {a.detail}</span>}
                              </>
                            )}
                            {a.kind === "approval" && (
                              <>
                                <span className="text-amber-400">[Freigabe]</span> {a.label}
                                {a.detail && <span className="text-muted-foreground/70"> {a.detail}</span>}
                              </>
                            )}
                            {a.kind === "text" && <span>{a.label}</span>}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {webResults.map((w, wi) => (
                    <div key={wi} className="rounded-lg border border-border bg-foreground/[0.03] p-2.5">
                      <div className="mb-1.5 flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-muted-foreground/60">
                        <Search className="h-3 w-3 text-indigo-400" />
                        <span className="min-w-0 flex-1 truncate">{w.query}</span>
                        {/* Ergebnisse bleiben sonst bis zum Sitzungsende stehen und
                            verdecken, was danach passiert. */}
                        <button
                          onClick={() => setWebResults((prev) => prev.filter((_, i) => i !== wi))}
                          title="Ausblenden"
                          aria-label="Suchergebnisse ausblenden"
                          className="shrink-0 rounded p-0.5 text-muted-foreground/50 hover:bg-foreground/[0.06] hover:text-foreground"
                        >
                          <X className="h-3 w-3" />
                        </button>
                      </div>
                      <div className="space-y-1.5">
                        {w.results.map((r, ri) => (
                          <a
                            key={ri}
                            href={r.url}
                            target="_blank"
                            rel="noreferrer"
                            className="block rounded-md p-1.5 hover:bg-foreground/[0.04]"
                          >
                            <div className="truncate text-xs font-medium text-indigo-300">{r.title || r.url}</div>
                            <div className="line-clamp-2 text-[11px] text-muted-foreground/70">{r.snippet}</div>
                          </a>
                        ))}
                      </div>
                    </div>
                  ))}
                  {(webResults.length > 1 || media.length > 1) && (
                    <button
                      onClick={() => { setWebResults([]); setMedia([]); }}
                      className="w-full rounded-lg border border-border/60 py-1.5 text-[11px] text-muted-foreground/70 hover:bg-foreground/[0.04] hover:text-foreground"
                    >
                      Alle Ergebnisse ausblenden
                    </button>
                  )}
                  {tasks.length === 0 && activity.length === 0 && webResults.length === 0 && media.length === 0 && (
                    <p className="text-xs text-muted-foreground/50">
                      Hier erscheint live, was der Agent tut — und Web-Ergebnisse, wenn ich etwas nachschlage.
                    </p>
                  )}
                </div>
              </div>
            </div>
            {webModal && (
              <WebModal url={webModal.url} caption={webModal.caption} onClose={() => setWebModal(null)} />
            )}
            {graphOverlay && (
              <div className="fixed inset-0 z-[60] flex flex-col bg-background/95 backdrop-blur-sm">
                <div className="flex items-center gap-2 border-b border-border px-4 py-2.5">
                  <Network className="h-4 w-4 text-primary" />
                  <span className="text-sm font-medium">Knowledge Graph</span>
                  <span className="text-[11px] text-muted-foreground/50">— per Sprache steuerbar („mach den Graphen wieder zu")</span>
                  <button
                    onClick={() => setGraphOverlay(null)}
                    className="ml-auto rounded-md p-1 text-muted-foreground hover:bg-foreground/[0.06]"
                    aria-label="Schließen"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
                <div className="min-h-0 flex-1">
                  {graphOverlay.brainId != null ? (
                    <VaultGraph3D brainId={graphOverlay.brainId} initialQuery={graphOverlay.query} />
                  ) : (
                    <div className="flex h-full items-center justify-center text-sm text-muted-foreground/60">
                      Kein Second Brain für diesen Agenten gefunden.
                    </div>
                  )}
                </div>
              </div>
            )}
            {pageOverlay && (
              <div className="fixed inset-0 z-[60] flex flex-col bg-background/95 backdrop-blur-sm">
                <div className="flex items-center gap-2 border-b border-border px-4 py-2.5">
                  <LayoutGrid className="h-4 w-4 text-primary" />
                  <span className="text-sm font-medium">{pageOverlay.label}</span>
                  <span className="hidden text-[11px] text-muted-foreground/50 sm:inline">
                    — sprich einfach weiter („mach das wieder zu")
                  </span>
                  <a
                    href={pageOverlay.path}
                    target="_blank"
                    rel="noopener noreferrer"
                    title="In eigenem Tab öffnen"
                    className="ml-auto rounded-md p-1 text-muted-foreground hover:bg-foreground/[0.06]"
                  >
                    <ExternalLink className="h-4 w-4" />
                  </a>
                  <button
                    onClick={() => setPageOverlay(null)}
                    className="rounded-md p-1 text-muted-foreground hover:bg-foreground/[0.06]"
                    aria-label="Schließen"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
                {/* Gleiche Herkunft → die Sitzung des Nutzers gilt auch hier. `embed=1`
                    laesst die Seite ohne Sidebar rendern, damit nicht zwei Rahmen
                    ineinanderstecken. Der Pfad ist oben gegen eine Allowlist geprueft. */}
                <iframe
                  src={`${pageOverlay.path}${pageOverlay.path.includes("?") ? "&" : "?"}embed=1`}
                  title={pageOverlay.label}
                  className="min-h-0 flex-1 border-0 bg-background"
                />
              </div>
            )}
            {meetingOpen && (
              <div className="mt-4 rounded-xl border border-sky-500/30 bg-sky-500/[0.04] p-3">
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-[11px] font-medium uppercase tracking-wider text-sky-300/80">
                    Meeting aufnehmen &amp; transkribieren
                  </span>
                  <button
                    onClick={() => setMeetingOpen(false)}
                    className="text-xs text-muted-foreground/60 hover:text-foreground"
                  >
                    Schließen
                  </button>
                </div>
                <p className="mb-2 text-[11px] text-muted-foreground/70">
                  Reine Aufnahme — {agentName} hört dabei nicht zu und spricht nicht. Am Ende kannst du das
                  Transkript an {agentName} senden; Transkript und Protokoll erscheinen dann als Chat-Verlauf
                  im Chat-Tab dieses Agenten.
                </p>
                <MeetingRecorder onTranscript={handleMeetingTranscript} />
              </div>
            )}
            {meetingMsg && <p className="mt-3 text-center text-xs text-sky-300">{meetingMsg}</p>}
            </>
          ) : (
            /* ── Classic push-to-talk UI ── */
            <>
              <div className="my-6 flex justify-center">
                <button
                  onMouseDown={startRecording}
                  onMouseUp={commitTurn}
                  onTouchStart={(e) => {
                    e.preventDefault();
                    startRecording();
                  }}
                  onTouchEnd={(e) => {
                    e.preventDefault();
                    commitTurn();
                  }}
                  disabled={state === "connecting" || state === "error"}
                  className={`flex h-24 w-24 items-center justify-center rounded-full transition-all ${
                    state === "listening"
                      ? "bg-red-500 shadow-lg shadow-red-500/40 scale-110"
                      : state === "speaking"
                      ? "bg-emerald-500/20 border-2 border-emerald-500"
                      : state === "processing"
                      ? "bg-amber-500/20 border-2 border-amber-500"
                      : "bg-primary text-primary-foreground hover:bg-primary/90"
                  } disabled:opacity-40 disabled:cursor-not-allowed`}
                  title={state === "listening" ? "Loslassen zum Senden" : "Drücken & sprechen"}
                >
                  {state === "processing" ? (
                    <Loader2 className="h-8 w-8 animate-spin" />
                  ) : state === "speaking" ? (
                    <Volume2 className="h-8 w-8 text-emerald-500 animate-pulse" />
                  ) : state === "listening" ? (
                    <Mic className="h-8 w-8 text-white animate-pulse" />
                  ) : state === "error" ? (
                    <MicOff className="h-8 w-8" />
                  ) : (
                    <Mic className="h-8 w-8" />
                  )}
                </button>
              </div>
              {state === "speaking" && (
                <div className="mb-4 flex justify-center">
                  <button
                    onClick={interrupt}
                    className="rounded-md bg-foreground/[0.06] px-3 py-1.5 text-xs hover:bg-foreground/[0.10]"
                  >
                    Unterbrechen
                  </button>
                </div>
              )}
            </>
          )}

          {!isRealtime && statusMsg && state !== "error" && (
            <p className="mt-4 text-center text-xs text-muted-foreground/70">{statusMsg}</p>
          )}

          {!isRealtime && transcript && (
            <div className="mb-3 mt-4 rounded-lg bg-foreground/[0.04] p-3">
              <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground/60">
                Du sagtest
              </div>
              <p className="text-sm">{transcript}</p>
            </div>
          )}
          {!isRealtime && activity.length > 0 && (
            <div className="mb-3 mt-4 rounded-lg border border-border bg-black/40 p-3">
              <div className="mb-2 flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-muted-foreground/60">
                {delegating ? (
                  <Loader2 className="h-3 w-3 animate-spin text-amber-400" />
                ) : (
                  <Radio className="h-3 w-3 text-emerald-400" />
                )}
                {delegating ? "Agent arbeitet an der Aufgabe" : "Aufgabe erledigt"}
              </div>
              <div
                ref={activityRef}
                className="max-h-40 overflow-y-auto font-mono text-[11px] leading-relaxed"
              >
                {activity.map((a, i) => (
                  <div
                    key={i}
                    className={
                      a.kind === "header"
                        ? "mb-1 text-foreground/90"
                        : a.kind === "tool"
                        ? "text-sky-400"
                        : a.kind === "approval"
                        ? "text-amber-300"
                        : "text-muted-foreground"
                    }
                  >
                    {a.kind === "header" && (
                      <>
                        <span className="text-muted-foreground/60">Aufgabe: </span>
                        {a.label}
                      </>
                    )}
                    {a.kind === "tool" && (
                      <>
                        <span className="text-amber-400">[{a.label}]</span>
                        {a.detail && <span className="text-muted-foreground/70"> {a.detail}</span>}
                      </>
                    )}
                    {a.kind === "approval" && (
                      <>
                        <span className="text-amber-400">[Freigabe]</span> {a.label}
                        {a.detail && <span className="text-muted-foreground/70"> {a.detail}</span>}
                      </>
                    )}
                    {a.kind === "text" && <span>{a.label}</span>}
                  </div>
                ))}
              </div>
            </div>
          )}
          {!isRealtime && response && (
            <div className="rounded-lg border border-primary/20 bg-primary/10 p-3">
              <div className="mb-1 text-[10px] uppercase tracking-wider text-primary/80">
                {agentName} antwortet
              </div>
              <p className="whitespace-pre-wrap text-sm">{linkify(response)}</p>
            </div>
          )}

          {!isRealtime && error && <div className="mt-3 text-sm text-red-400">{error}</div>}
        </div>
      </div>
    </div>
  );
}

/** Round, icon-only call control. Quiet by default so the orb + stage carry the view. */
function CtrlButton({
  onClick, title, tone = "neutral", children,
}: {
  onClick: () => void; title: string; tone?: "neutral" | "amber" | "red"; children: React.ReactNode;
}) {
  const tones = {
    neutral: "bg-foreground/[0.05] text-muted-foreground hover:bg-foreground/[0.10] hover:text-foreground",
    amber: "bg-amber-500/15 text-amber-300 hover:bg-amber-500/25",
    red: "bg-red-500/10 text-red-400 hover:bg-red-500/20",
  } as const;
  return (
    <button
      onClick={onClick}
      title={title}
      aria-label={title}
      className={`inline-flex h-9 w-9 items-center justify-center rounded-full transition-colors ${tones[tone]}`}
    >
      {children}
    </button>
  );
}

/** In-app window for pages that allow embedding (our own HTML reports always do).
 *  Sandboxed: the framed page may run scripts, but gets no same-origin access to us. */
function WebModal({ url, caption, onClose }: { url: string; caption?: string; onClose: () => void }) {
  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-background/80 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="relative flex h-[85vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-border px-4 py-2.5">
          <Globe className="h-4 w-4 shrink-0 text-sky-400" />
          <div className="min-w-0 flex-1">
            {caption && <p className="truncate text-sm font-medium">{caption}</p>}
            <p className="truncate text-[11px] text-muted-foreground/60">{url}</p>
          </div>
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 rounded-md bg-foreground/[0.06] px-2.5 py-1 text-[11px] hover:bg-foreground/[0.10]"
          >
            <ExternalLink className="h-3 w-3" /> Neuer Tab
          </a>
          <button
            onClick={onClose}
            className="rounded-md p-1 text-muted-foreground hover:bg-foreground/[0.06]"
            aria-label="Schließen"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <iframe
          src={url}
          title={caption || url}
          className="min-h-0 flex-1 bg-white"
          // No allow-same-origin: combined with allow-scripts it would let the framed
          // page escape the sandbox and read our cookies/localStorage. Opaque origin.
          sandbox="allow-scripts allow-forms allow-popups"
          referrerPolicy="no-referrer"
        />
      </div>
    </div>
  );
}

function StatusPill({ state, realtime, focus = false, working = false }: { state: VoiceState; realtime: boolean; focus?: boolean; working?: boolean }) {
  const map: Record<VoiceState, { label: string; cls: string }> = {
    connecting: { label: "Verbinde…", cls: "bg-zinc-500/10 text-zinc-400" },
    ready: { label: realtime ? "Verbunden" : "Bereit", cls: "bg-emerald-500/10 text-emerald-400" },
    listening: { label: realtime ? "Hört zu…" : "Höre zu…", cls: "bg-fuchsia-500/10 text-fuchsia-400" },
    processing: { label: "Agent arbeitet…", cls: "bg-orange-500/10 text-orange-400" },
    speaking: { label: "Spricht…", cls: "bg-emerald-500/10 text-emerald-400" },
    error: { label: "Fehler", cls: "bg-red-500/10 text-red-400" },
  };
  // Focus mode (mic muted): the agent isn't listening. While it still works on a
  // task → orange "Fokus-Modus aktiv"; once idle → green "bereit". Not "Hört zu…".
  const m = focus
    ? working
      ? { label: "Fokus-Modus aktiv", cls: "bg-orange-500/10 text-orange-400" }
      : { label: "Fokus-Modus – bereit", cls: "bg-emerald-500/10 text-emerald-400" }
    : map[state];
  return (
    <div className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[11px] font-medium ${m.cls}`}>
      <span className="h-1.5 w-1.5 rounded-full bg-current animate-pulse" />
      {m.label}
    </div>
  );
}
