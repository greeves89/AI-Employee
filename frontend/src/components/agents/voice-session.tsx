"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { Mic, MicOff, X, Loader2, Volume2, PhoneOff, Radio, Search, FileText, CheckCircle2, Pause, Play, ChevronDown, ChevronRight, ClipboardList } from "lucide-react";
import { getWsUrl, getBase } from "@/lib/config";
import { JarvisCore } from "./jarvis-core";
import { MeetingRecorder } from "@/components/meetings/meeting-recorder";
import { sendMeetingTranscriptToChat, getChatHistory } from "@/lib/api";

type Turn = { role: "user" | "assistant"; text: string };
type WebResult = { title: string; url: string; snippet: string };
type WebResultSet = { query: string; results: WebResult[] };

type VoiceState = "connecting" | "ready" | "listening" | "processing" | "speaking" | "error";
type Mode = "classic" | "nova_sonic";

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
}

export function VoiceSessionModal({ agentId, agentName, onClose, getTicket, resumeSessionId, embedded = false }: Props) {
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
  const [tasks, setTasks] = useState<{ id: string; instruction: string; done: boolean }[]>([]);
  const delegating = tasks.some((t) => !t.done); // any task still running
  const activityRef = useRef<HTMLDivElement>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [webResults, setWebResults] = useState<WebResultSet[]>([]);
  const [media, setMedia] = useState<{ kind: string; media_type?: string; b64?: string; filename?: string; caption?: string; path?: string }[]>([]);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const [paused, setPaused] = useState(false);       // focus mode: mic muted, agent still reports
  const [activityOpen, setActivityOpen] = useState(true);
  const [volume, setVolume] = useState(1);           // playback volume (works on iOS via GainNode)
  // Meeting recorder: PURE audio capture → transcript. No live agent interaction
  // while recording (the live mic is muted so the agent neither listens nor speaks).
  const [meetingOpen, setMeetingOpen] = useState(false);
  const [meetingMsg, setMeetingMsg] = useState<string | null>(null);

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

  const wsRef = useRef<WebSocket | null>(null);
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

  // ── WebSocket connect ──────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        let ticket: string;
        if (getTicket) {
          // Kiosk / custom flow: caller supplies the ticket (no JWT available).
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
        if (cancelled) return;
        const url = `${getWsUrl()}/api/v1/ws/agents/${agentId}/voice?ticket=${ticket}${
          resumeSessionId ? `&chat_session=${encodeURIComponent(resumeSessionId)}` : ""
        }`;
        const ws = new WebSocket(url);
        wsRef.current = ws;
        ws.onmessage = (e) => handleServerEvent(e.data);
        ws.onerror = () => {
          setError("Verbindung fehlgeschlagen");
          setState("error");
        };
        ws.onclose = () => {
          if (!cancelled) setState((s) => (s === "error" ? s : "error"));
        };
      } catch (e) {
        setError(String(e));
        setState("error");
      }
    })();
    return () => {
      cancelled = true;
      wsRef.current?.close();
      stopRecording();
      teardownRealtime();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId]);

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
      case "transcript":
        setTranscript(String(data.text || ""));
        if (modeRef.current === "classic") {
          setState("processing");
        } else {
          suppressAudioRef.current = false;  // new user turn -> allow the response audio
          upsertTurn("user", String(data.text || ""));
        }
        break;
      case "response":
        // Fires for MY OWN speech AND for delegation reports — must NOT flip a task to
        // "erledigt" here (that's delegate_done's job), else a task reads done while it
        // is still running.
        setResponse(String(data.text || ""));
        if (modeRef.current === "nova_sonic") upsertTurn("assistant", String(data.text || ""));
        break;
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
              path: data.path ? String(data.path) : undefined,
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
              results: Array.isArray(data.results) ? (data.results as WebResult[]) : [],
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
        setTasks((prev) => {
          let flipped = false;
          return prev.map((t) => {
            if (flipped || t.done) return t;
            const match = taskId ? t.id === taskId : t.instruction === instruction;
            if (match) {
              flipped = true;
              return { ...t, done: true };
            }
            return t;
          });
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
          setState("error");
          setError("Realtime-Session beendet.");
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
    // Tell the server to SKIP the rest of the interrupted turn (drops all further
    // audio server-side until the next turn), so nothing resumes after the timer.
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "interrupt" }));
    }
    if (suppressTimerRef.current) window.clearTimeout(suppressTimerRef.current);
    suppressTimerRef.current = window.setTimeout(() => {
      suppressAudioRef.current = false;
    }, 1500);
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
    onClose();
  }, [teardownRealtime, onClose]);

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
  const paneHeight = embedded
    ? "min-h-[50vh] lg:min-h-[68vh] lg:max-h-[74vh]"
    : "max-h-[42vh] min-h-[26vh] lg:max-h-[60vh] lg:min-h-[48vh]";
  return (
    <div
      className={embedded
        ? "w-full h-full"
        : "fixed inset-0 z-50 flex items-stretch justify-center bg-background/80 backdrop-blur-sm sm:items-center sm:p-4"}
      onClick={embedded ? undefined : onClose}
    >
      <div
        className={embedded
          ? "relative flex h-full w-full flex-col rounded-2xl border border-border bg-card"
          : `relative flex w-full flex-col overflow-y-auto border-border bg-card shadow-2xl h-[100dvh] max-h-[100dvh] rounded-none sm:h-auto sm:max-h-[90vh] sm:rounded-2xl sm:border ${
              isRealtime ? "max-w-6xl" : "max-w-lg"
            }`}
        onClick={embedded ? undefined : (e) => e.stopPropagation()}
      >
        {!embedded && (
          <button
            onClick={onClose}
            className="absolute top-3 right-3 rounded-md p-1 text-muted-foreground hover:bg-foreground/[0.06]"
            aria-label="Schließen"
          >
            <X className="h-4 w-4" />
          </button>
        )}

        <div className={embedded ? "flex min-h-0 flex-1 flex-col overflow-y-auto p-4 sm:p-6 lg:p-8" : "p-4 sm:p-6"}>
          <div className="mb-4 flex items-center gap-2 pr-8">
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

          {!isRealtime && <StatusPill state={state} realtime={isRealtime} />}

          {isRealtime ? (
            /* ── Jarvis: 3-pane realtime cockpit (Gespräch | Präsenz | Aufgaben) ── */
            <>
            <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-[1fr_minmax(280px,1.1fr)_1fr] lg:items-stretch">
              {/* LEFT — conversation transcript */}
              <div className={`order-2 flex ${paneHeight} min-w-0 flex-col rounded-xl border border-border bg-foreground/[0.02] lg:order-1`}>
                <div className="border-b border-border px-3 py-2 text-[10px] uppercase tracking-wider text-muted-foreground/60">
                  Gespräch
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
                          {t.text}
                        </div>
                      </div>
                    ))
                  )}
                  {transcript && state === "listening" && (
                    <div className="text-right">
                      <div className="inline-block max-w-[92%] rounded-2xl bg-fuchsia-500/10 px-3 py-1.5 text-sm italic text-muted-foreground">
                        {transcript}…
                      </div>
                    </div>
                  )}
                </div>
              </div>

              {/* CENTER — animated presence + controls */}
              <div className="order-1 flex min-w-0 flex-col items-center justify-center gap-5 py-2 lg:order-2">
                <JarvisCore state={state} />
                <StatusPill state={state} realtime focus={paused} working={delegating} />
                {statusMsg && state !== "error" && (
                  <p className="max-w-[240px] text-center text-xs text-muted-foreground/70">{statusMsg}</p>
                )}
                {paused && (
                  <p className="max-w-[260px] text-center text-xs text-amber-400/90">
                    Fokus-Modus: Mikro aus — ich arbeite weiter und melde mich, wenn etwas fertig ist.
                  </p>
                )}
                {/* Playback volume — GainNode-based so it also works on iOS Safari */}
                <div className="flex w-full max-w-[240px] items-center gap-2">
                  <Volume2 className="h-4 w-4 shrink-0 text-muted-foreground/60" />
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.05}
                    value={volume}
                    onChange={(e) => changeVolume(Number(e.target.value))}
                    aria-label="Lautstärke"
                    className="h-1 flex-1 cursor-pointer accent-emerald-500"
                  />
                </div>
                <div className="flex flex-wrap justify-center gap-2">
                  {state === "speaking" && (
                    <button
                      onClick={bargeIn}
                      className="rounded-md bg-foreground/[0.06] px-3 py-1.5 text-xs hover:bg-foreground/[0.10]"
                    >
                      Unterbrechen
                    </button>
                  )}
                  <button
                    onClick={togglePause}
                    className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium ${
                      paused
                        ? "bg-amber-500/15 text-amber-300 hover:bg-amber-500/25"
                        : "bg-foreground/[0.06] hover:bg-foreground/[0.10]"
                    }`}
                  >
                    {paused ? <><Play className="h-3.5 w-3.5" /> Fortsetzen</> : <><Pause className="h-3.5 w-3.5" /> Fokus</>}
                  </button>
                  <button
                    onClick={openMeeting}
                    className="inline-flex items-center gap-1.5 rounded-md bg-sky-500/10 px-3 py-1.5 text-xs font-medium text-sky-300 hover:bg-sky-500/20"
                    title="Nur Audio aufnehmen & transkribieren — der Agent hört dabei nicht zu und spricht nicht"
                  >
                    <ClipboardList className="h-3.5 w-3.5" /> Meeting aufnehmen
                  </button>
                  <button
                    onClick={endLive}
                    className="inline-flex items-center gap-1.5 rounded-md bg-red-500/10 px-3 py-1.5 text-xs font-medium text-red-400 hover:bg-red-500/20"
                  >
                    <PhoneOff className="h-3.5 w-3.5" /> Gespräch beenden
                  </button>
                </div>
                {error && <div className="text-center text-xs text-red-400">{error}</div>}
              </div>

              {/* RIGHT — tasks, live activity, web results */}
              <div className={`order-3 flex ${paneHeight} min-w-0 flex-col rounded-xl border border-border bg-foreground/[0.02]`}>
                <div className="border-b border-border px-3 py-2 text-[10px] uppercase tracking-wider text-muted-foreground/60">
                  Aufgaben &amp; Aktivität
                </div>
                <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
                  {media.map((m, mi) => (
                    <div key={mi} className="rounded-lg border border-border bg-foreground/[0.03] p-2">
                      {m.kind === "image" && m.b64 ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          src={`data:${m.media_type || "image/png"};base64,${m.b64}`}
                          alt={m.caption || "Bild"}
                          className="max-h-64 w-full rounded object-contain"
                        />
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
                      {m.caption && (
                        <div className="mt-1 text-[11px] text-muted-foreground/70">{m.caption}</div>
                      )}
                    </div>
                  ))}
                  {/* One card per delegated task — each with its own live status. */}
                  {tasks.map((t, ti) => (
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
                      <div className="min-w-0">
                        <div className="text-[10px] uppercase tracking-wider text-muted-foreground/60">
                          {t.done ? "Erledigt" : "Läuft"}
                        </div>
                        <div className="text-foreground/90">{t.instruction}</div>
                      </div>
                    </div>
                  ))}
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
                            {a.kind === "text" && <span>{a.label}</span>}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {webResults.map((w, wi) => (
                    <div key={wi} className="rounded-lg border border-border bg-foreground/[0.03] p-2.5">
                      <div className="mb-1.5 flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-muted-foreground/60">
                        <Search className="h-3 w-3 text-indigo-400" /> {w.query}
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
                  {tasks.length === 0 && activity.length === 0 && webResults.length === 0 && media.length === 0 && (
                    <p className="text-xs text-muted-foreground/50">
                      Hier erscheint live, was der Agent tut — und Web-Ergebnisse, wenn ich etwas nachschlage.
                    </p>
                  )}
                </div>
              </div>
            </div>
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
              <p className="whitespace-pre-wrap text-sm">{response}</p>
            </div>
          )}

          {!isRealtime && error && <div className="mt-3 text-sm text-red-400">{error}</div>}
        </div>
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
