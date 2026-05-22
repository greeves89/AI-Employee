"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { Mic, MicOff, X, Loader2, Volume2 } from "lucide-react";
import { getWsUrl, getBase } from "@/lib/config";

type VoiceState = "connecting" | "ready" | "listening" | "processing" | "speaking" | "error";

/** ArrayBuffer → base64 without spreading a typed array (build-safe). */
function bufToBase64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let binary = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode.apply(
      null,
      Array.from(bytes.subarray(i, i + CHUNK)),
    );
  }
  return btoa(binary);
}

interface Props {
  agentId: string;
  agentName: string;
  onClose: () => void;
}

export function VoiceSessionModal({ agentId, agentName, onClose }: Props) {
  const [state, setState] = useState<VoiceState>("connecting");
  const [transcript, setTranscript] = useState("");
  const [response, setResponse] = useState("");
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const playQueueRef = useRef<Promise<void>>(Promise.resolve());
  const voiceLanguageRef = useRef("de");

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
        const url = `${getWsUrl()}/agents/${agentId}/voice?ticket=${ticket}`;
        const ws = new WebSocket(url);
        wsRef.current = ws;
        ws.onmessage = (e) => handleServerEvent(e.data);
        ws.onerror = () => {
          setError("Verbindung fehlgeschlagen");
          setState("error");
        };
        ws.onclose = () => {
          if (!cancelled) setState("error");
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
      case "ready":
        setState("ready");
        break;
      case "transcript":
        setTranscript(String(data.text || ""));
        setState("processing");
        break;
      case "response":
        setResponse(String(data.text || ""));
        break;
      case "tts_start":
        setState("speaking");
        break;
      case "audio_chunk": {
        const b64 = String(data.b64 || "");
        const mime = String(data.mime || "audio/mpeg");
        if (b64) playAudioChunk(b64, mime);
        break;
      }
      case "tts_end":
        // wait for queued playback to finish before going back to ready
        playQueueRef.current.then(() => {
          setState((s) => (s === "speaking" ? "ready" : s));
        });
        break;
      case "done":
        playQueueRef.current.then(() => setState("ready"));
        break;
      case "error":
        setError(String(data.message || "Fehler"));
        setState("error");
        break;
    }
  }, []);

  // ── Audio playback ───────────────────────────────────────────
  // MP3 chunks are queued and played sequentially via HTMLAudioElement,
  // which tolerates MP3 fragments better than decodeAudioData.
  const playAudioChunk = useCallback((b64: string, mime: string) => {
    playQueueRef.current = playQueueRef.current.then(async () => {
      try {
        const bin = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
        const blob = new Blob([bin], { type: mime });
        const url = URL.createObjectURL(blob);
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
        // ignore single-chunk errors
      }
    });
  }, []);

  // ── Recording ──────────────────────────────────────────────
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
        const b64 = bufToBase64(buf);
        wsRef.current.send(
          JSON.stringify({ type: "audio_chunk", data: { b64 } })
        );
      };
      rec.start(250); // chunk every 250ms
      setState("listening");
    } catch {
      setError("Mikrofon-Zugriff verweigert");
      setState("error");
    }
  }, [state]);

  const stopRecording = useCallback(() => {
    recorderRef.current?.stop();
    recorderRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }, []);

  const commitTurn = useCallback(() => {
    stopRecording();
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({ type: "commit", data: { language: voiceLanguageRef.current } })
      );
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
          <div className="mb-4">
            <h2 className="text-lg font-semibold">Live-Session: {agentName}</h2>
            <p className="text-xs text-muted-foreground/70 mt-0.5">
              Halte den Knopf gedrückt zum Sprechen, oder klicke einmal zum
              Toggeln.
            </p>
          </div>

          {/* Status pill */}
          <StatusPill state={state} />

          {/* Mic button */}
          <div className="flex justify-center my-6">
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
            <div className="flex justify-center mb-4">
              <button
                onClick={interrupt}
                className="text-xs px-3 py-1.5 rounded-md bg-foreground/[0.06] hover:bg-foreground/[0.10]"
              >
                Unterbrechen
              </button>
            </div>
          )}

          {/* Transcript + response */}
          {transcript && (
            <div className="mb-3 rounded-lg bg-foreground/[0.04] p-3">
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground/60 mb-1">
                Du sagtest
              </div>
              <p className="text-sm">{transcript}</p>
            </div>
          )}
          {response && (
            <div className="rounded-lg bg-primary/10 border border-primary/20 p-3">
              <div className="text-[10px] uppercase tracking-wider text-primary/80 mb-1">
                {agentName} antwortet
              </div>
              <p className="text-sm whitespace-pre-wrap">{response}</p>
            </div>
          )}

          {error && (
            <div className="mt-3 text-sm text-red-400">{error}</div>
          )}
        </div>
      </div>
    </div>
  );
}

function StatusPill({ state }: { state: VoiceState }) {
  const map: Record<VoiceState, { label: string; cls: string }> = {
    connecting: { label: "Verbinde…", cls: "bg-zinc-500/10 text-zinc-400" },
    ready: { label: "Bereit", cls: "bg-emerald-500/10 text-emerald-400" },
    listening: { label: "Höre zu…", cls: "bg-red-500/10 text-red-400" },
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
