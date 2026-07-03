"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { Mic, MicOff, X, Loader2, Volume2, PhoneOff, Radio } from "lucide-react";
import { getWsUrl, getBase } from "@/lib/config";

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
}

export function VoiceSessionModal({ agentId, agentName, onClose }: Props) {
  const [state, setState] = useState<VoiceState>("connecting");
  const [mode, setMode] = useState<Mode>("classic");
  const [transcript, setTranscript] = useState("");
  const [response, setResponse] = useState("");
  const [statusMsg, setStatusMsg] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState(false);

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
  const nextPlayRef = useRef(0);
  const liveSourcesRef = useRef<AudioBufferSourceNode[]>([]);

  // ── WebSocket connect ──────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const token = localStorage.getItem("token");
        const tr = await fetch(`${getBase()}/ws/ticket`, {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!tr.ok) throw new Error("ticket failed");
        const { ticket } = await tr.json();
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
        if (cancelled) return;
        const url = `${getWsUrl()}/api/v1/ws/agents/${agentId}/voice?ticket=${ticket}`;
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
        if (modeRef.current === "classic") setState("processing");
        break;
      case "response":
        setResponse(String(data.text || ""));
        break;
      case "status":
        setStatusMsg(String(data.message || ""));
        break;
      case "delegate":
        setStatusMsg(`Agent bearbeitet: ${String(data.instruction || "")}`);
        break;
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
      outCtxRef.current = new AudioContext();
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
      node.connect(ctx.destination);
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

  // ── Realtime capture (continuous 16 kHz PCM) ─────────────────
  const startLive = useCallback(async () => {
    if (inCtxRef.current) return; // already live
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
      });
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
            flushPlayback();
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
    } catch {
      setError("Mikrofon-Zugriff verweigert");
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
    flushPlayback();
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "interrupt" }));
    }
    setState("listening");
  }, [flushPlayback]);

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
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="relative w-full max-w-lg rounded-2xl border border-border bg-card shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          onClick={onClose}
          className="absolute top-3 right-3 rounded-md p-1 text-muted-foreground hover:bg-foreground/[0.06]"
          aria-label="Schließen"
        >
          <X className="h-4 w-4" />
        </button>

        <div className="p-6">
          <div className="mb-4 flex items-center gap-2">
            <div>
              <h2 className="text-lg font-semibold">
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
                <Radio className="h-3 w-3" /> Nova Sonic
              </span>
            )}
          </div>

          <StatusPill state={state} realtime={isRealtime} />

          {isRealtime ? (
            /* ── Realtime UI: continuous, no push-to-talk ── */
            <>
              <div className="my-6 flex justify-center">
                <div
                  className={`flex h-24 w-24 items-center justify-center rounded-full transition-all ${
                    state === "speaking"
                      ? "bg-emerald-500/20 border-2 border-emerald-500 scale-105"
                      : state === "listening"
                      ? "bg-fuchsia-500/15 border-2 border-fuchsia-500 animate-pulse"
                      : state === "error"
                      ? "bg-red-500/10 border-2 border-red-500/40"
                      : "bg-primary/10 border-2 border-primary/30"
                  }`}
                >
                  {state === "connecting" ? (
                    <Loader2 className="h-8 w-8 animate-spin text-primary" />
                  ) : state === "speaking" ? (
                    <Volume2 className="h-8 w-8 text-emerald-500 animate-pulse" />
                  ) : state === "error" ? (
                    <MicOff className="h-8 w-8 text-red-400" />
                  ) : (
                    <Mic className={`h-8 w-8 ${live ? "text-fuchsia-400" : "text-primary"}`} />
                  )}
                </div>
              </div>
              <div className="flex justify-center gap-2">
                {state === "speaking" && (
                  <button
                    onClick={bargeIn}
                    className="rounded-md bg-foreground/[0.06] px-3 py-1.5 text-xs hover:bg-foreground/[0.10]"
                  >
                    Unterbrechen
                  </button>
                )}
                <button
                  onClick={endLive}
                  className="inline-flex items-center gap-1.5 rounded-md bg-red-500/10 px-3 py-1.5 text-xs font-medium text-red-400 hover:bg-red-500/20"
                >
                  <PhoneOff className="h-3.5 w-3.5" /> Gespräch beenden
                </button>
              </div>
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

          {statusMsg && state !== "error" && (
            <p className="mt-4 text-center text-xs text-muted-foreground/70">{statusMsg}</p>
          )}

          {transcript && (
            <div className="mb-3 mt-4 rounded-lg bg-foreground/[0.04] p-3">
              <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground/60">
                Du sagtest
              </div>
              <p className="text-sm">{transcript}</p>
            </div>
          )}
          {response && (
            <div className="rounded-lg border border-primary/20 bg-primary/10 p-3">
              <div className="mb-1 text-[10px] uppercase tracking-wider text-primary/80">
                {agentName} antwortet
              </div>
              <p className="whitespace-pre-wrap text-sm">{response}</p>
            </div>
          )}

          {error && <div className="mt-3 text-sm text-red-400">{error}</div>}
        </div>
      </div>
    </div>
  );
}

function StatusPill({ state, realtime }: { state: VoiceState; realtime: boolean }) {
  const map: Record<VoiceState, { label: string; cls: string }> = {
    connecting: { label: "Verbinde…", cls: "bg-zinc-500/10 text-zinc-400" },
    ready: { label: realtime ? "Verbunden" : "Bereit", cls: "bg-emerald-500/10 text-emerald-400" },
    listening: { label: realtime ? "Hört zu…" : "Höre zu…", cls: "bg-fuchsia-500/10 text-fuchsia-400" },
    processing: { label: "Agent arbeitet…", cls: "bg-amber-500/10 text-amber-400" },
    speaking: { label: "Spricht…", cls: "bg-emerald-500/10 text-emerald-400" },
    error: { label: "Fehler", cls: "bg-red-500/10 text-red-400" },
  };
  const m = map[state];
  return (
    <div className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[11px] font-medium ${m.cls}`}>
      <span className="h-1.5 w-1.5 rounded-full bg-current animate-pulse" />
      {m.label}
    </div>
  );
}
