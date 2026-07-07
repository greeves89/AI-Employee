"use client";

import { useCallback, useRef, useState } from "react";
import { Mic, Square, Loader2, Copy, Check } from "lucide-react";
import { transcribeMeetingChunk } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Self-contained meeting recorder: records the microphone, and on stop sends the
 * recording to the STT service (/meetings/transcribe) and shows the transcript.
 * The transcript can then be handed to a Meeting agent to produce a protocol.
 *
 * v1 transcribes the whole recording once on stop (reliable across browsers).
 * Live chunk-transcription + speaker diarization are the next enhancement.
 */
export function MeetingRecorder({
  onTranscript,
}: {
  onTranscript?: (text: string) => void;
}) {
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const mrRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const start = useCallback(async () => {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      chunksRef.current = [];
      const mr = new MediaRecorder(stream);
      mr.ondataavailable = (e) => { if (e.data.size) chunksRef.current.push(e.data); };
      mr.onstop = async () => {
        streamRef.current?.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunksRef.current, { type: mr.mimeType || "audio/webm" });
        if (!blob.size) return;
        setBusy(true);
        try {
          const text = await transcribeMeetingChunk(blob);
          setTranscript((prev) => (prev ? prev + "\n" : "") + text);
        } catch {
          setError("Transkription fehlgeschlagen (STT-Service).");
        } finally {
          setBusy(false);
        }
      };
      mr.start();
      mrRef.current = mr;
      setRecording(true);
      setElapsed(0);
      timerRef.current = setInterval(() => setElapsed((e) => e + 1), 1000);
    } catch {
      setError("Mikrofon-Zugriff nicht möglich. Bitte im Browser erlauben.");
    }
  }, []);

  const stop = useCallback(() => {
    mrRef.current?.stop();
    setRecording(false);
    if (timerRef.current) clearInterval(timerRef.current);
  }, []);

  const mmss = `${String(Math.floor(elapsed / 60)).padStart(2, "0")}:${String(elapsed % 60).padStart(2, "0")}`;

  return (
    <div className="rounded-xl border border-border bg-card/60 p-4 space-y-3">
      <div className="flex items-center gap-3">
        <button
          onClick={recording ? stop : start}
          disabled={busy}
          className={cn(
            "inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors disabled:opacity-50",
            recording
              ? "bg-red-500/15 text-red-400 hover:bg-red-500/25"
              : "bg-primary text-primary-foreground hover:bg-primary/90",
          )}
        >
          {busy ? <Loader2 className="h-4 w-4 animate-spin" />
            : recording ? <Square className="h-4 w-4" />
            : <Mic className="h-4 w-4" />}
          {busy ? "Transkribiere…" : recording ? "Aufnahme stoppen" : "Meeting aufnehmen"}
        </button>
        {recording && (
          <span className="inline-flex items-center gap-1.5 text-sm text-red-400">
            <span className="h-2 w-2 rounded-full bg-red-500 animate-pulse" /> {mmss}
          </span>
        )}
      </div>

      {error && <p className="text-xs text-red-400">{error}</p>}

      {transcript && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground/60">Transkript</span>
            <button
              onClick={() => { navigator.clipboard?.writeText(transcript); setCopied(true); setTimeout(() => setCopied(false), 1500); }}
              className="inline-flex items-center gap-1 text-[11px] text-muted-foreground/60 hover:text-foreground"
            >
              {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />} Kopieren
            </button>
          </div>
          <textarea
            value={transcript}
            onChange={(e) => setTranscript(e.target.value)}
            rows={6}
            className="w-full resize-y rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40"
          />
          {onTranscript && (
            <button
              onClick={() => onTranscript(transcript)}
              className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
            >
              An Meeting-Agent senden (Protokoll erstellen)
            </button>
          )}
        </div>
      )}
    </div>
  );
}
