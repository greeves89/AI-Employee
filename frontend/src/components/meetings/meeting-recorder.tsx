"use client";

import { useCallback, useRef, useState } from "react";
import { Mic, Square, Loader2, Copy, Check } from "lucide-react";
import { transcribeMeetingChunk } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Self-contained meeting recorder. Records the microphone in short SEGMENTS
 * (~20s each) instead of one giant blob: every segment is a complete, independently
 * decodable webm file that is transcribed on its own and appended to the transcript
 * live (a near-live transcript with a short delay per segment). This is what makes
 * long meetings (30/40+ min) work reliably:
 *  - no single huge request → no STT timeout on long audio,
 *  - every segment stays well under OpenAI's 25 MB fallback limit,
 *  - the transcript is built incrementally, so an interrupted/crashed recording
 *    never loses the part that was already transcribed.
 *
 * Transcription itself runs server-side (/meetings/transcribe): local faster-whisper
 * where available (SKBS), OpenAI Whisper fallback where not (Pi).
 */
// Short segments = a near-live transcript (update roughly every segment). Kept at
// 20s as a balance: live-ish feel vs. the tiny audio gap at each segment boundary.
const SEGMENT_MS = 20_000;

export function MeetingRecorder({
  onTranscript,
}: {
  onTranscript?: (text: string) => void;
}) {
  const [recording, setRecording] = useState(false);
  const [pending, setPending] = useState(0); // segments still transcribing
  const [transcript, setTranscript] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const mrRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const cycleRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const stoppingRef = useRef(false);
  // Serialize appends so segments land in recording order even if a later
  // segment transcribes faster than an earlier one.
  const queueRef = useRef<Promise<void>>(Promise.resolve());

  const transcribeBlob = useCallback((blob: Blob) => {
    if (!blob.size) return;
    setPending((n) => n + 1);
    queueRef.current = queueRef.current.then(async () => {
      try {
        const text = (await transcribeMeetingChunk(blob)).trim();
        if (text) setTranscript((prev) => (prev ? prev + " " : "") + text);
      } catch {
        setError("Transkription eines Abschnitts fehlgeschlagen (STT/OpenAI-Fallback).");
      } finally {
        setPending((n) => Math.max(0, n - 1));
      }
    });
  }, []);

  // Start a fresh recorder for the next segment on the SAME stream. Each segment
  // is its own complete webm file (headers included), so it decodes standalone.
  const startSegment = useCallback(() => {
    const stream = streamRef.current;
    if (!stream) return;
    const chunks: Blob[] = [];
    const mr = new MediaRecorder(stream);
    mr.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    mr.onstop = () => {
      const blob = new Blob(chunks, { type: mr.mimeType || "audio/webm" });
      transcribeBlob(blob);
      // If the user hasn't stopped the whole recording, roll into the next segment.
      if (!stoppingRef.current) startSegment();
    };
    mr.start();
    mrRef.current = mr;
  }, [transcribeBlob]);

  const start = useCallback(async () => {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      stoppingRef.current = false;
      startSegment();
      setRecording(true);
      setElapsed(0);
      timerRef.current = setInterval(() => setElapsed((e) => e + 1), 1000);
      // Cut a new segment every SEGMENT_MS: stopping the current recorder fires
      // its onstop (→ transcribe) and rolls into the next segment.
      cycleRef.current = setInterval(() => {
        if (mrRef.current && mrRef.current.state === "recording") mrRef.current.stop();
      }, SEGMENT_MS);
    } catch {
      setError("Mikrofon-Zugriff nicht möglich. Bitte im Browser erlauben.");
    }
  }, [startSegment]);

  const stop = useCallback(() => {
    stoppingRef.current = true;
    if (cycleRef.current) clearInterval(cycleRef.current);
    if (timerRef.current) clearInterval(timerRef.current);
    // Final segment: stopping fires onstop → transcribe; onstop won't roll over
    // because stoppingRef is set.
    if (mrRef.current && mrRef.current.state === "recording") mrRef.current.stop();
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    setRecording(false);
  }, []);

  const mmss = `${String(Math.floor(elapsed / 60)).padStart(2, "0")}:${String(elapsed % 60).padStart(2, "0")}`;

  return (
    <div className="rounded-xl border border-border bg-card/60 p-4 space-y-3">
      <div className="flex items-center gap-3 flex-wrap">
        <button
          onClick={recording ? stop : start}
          className={cn(
            "inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors",
            recording
              ? "bg-red-500/15 text-red-400 hover:bg-red-500/25"
              : "bg-primary text-primary-foreground hover:bg-primary/90",
          )}
        >
          {recording ? <Square className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
          {recording ? "Aufnahme stoppen" : "Meeting aufnehmen"}
        </button>
        {recording && (
          <span className="inline-flex items-center gap-1.5 text-sm text-red-400">
            <span className="h-2 w-2 rounded-full bg-red-500 animate-pulse" /> {mmss}
          </span>
        )}
        {pending > 0 && (
          <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground/70">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> transkribiere Abschnitt…
          </span>
        )}
      </div>

      {error && <p className="text-xs text-red-400">{error}</p>}

      {(transcript || recording) && (
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
            placeholder={recording ? "Das Transkript erscheint segmentweise, während du sprichst…" : ""}
            className="w-full resize-y rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40"
          />
          {onTranscript && transcript && !recording && (
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
