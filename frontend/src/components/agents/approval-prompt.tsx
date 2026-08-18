"use client";

/** Die Rückfrage eines Agenten — an EINER Stelle.
 *
 *  **Warum das hier steht.** Bis zum 18.08.2026 gab es dieselbe Anzeige
 *  dreimal: im Freigabe-Fenster, im Sprachcockpit und im Chat. Als die
 *  Antwortmöglichkeiten anklickbar wurden, bekamen zwei davon die Änderung —
 *  die dritte nicht. Der Chat zeigte weiter nur „Freigeben" und „Ablehnen",
 *  ohne die Frage, ohne die Optionen, ohne die Ansicht. Ein Agent, der dort
 *  vier Antworten anbot, bekam eine leere Bestätigung zurück und wusste
 *  nicht, was gemeint war.
 *
 *  Drei Fassungen derselben Sache laufen immer auseinander — es ist nur eine
 *  Frage, welche zuerst vergessen wird. Deshalb: eine.
 *
 *  Der Rückweg ist überall derselbe (`user_response`), damit die Antwort den
 *  Agenten erreicht, egal wo sie gegeben wurde.
 */

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { AGENT_VIEWS, AgentView } from "@/components/agents/agent-views";
import { cn } from "@/lib/utils";

export interface ApprovalPromptData {
  approval_id: string;
  agent_id: string;
  question?: string | null;
  options?: string[] | null;
  context?: string | null;
  reasoning?: string | null;
  tool?: string | null;
  view?: { name: string; data: Record<string, unknown> } | null;
}

interface Props {
  request: ApprovalPromptData;
  /** Die Wahl (oder freier Text) — geht als Zustimmung MIT Inhalt hinaus. */
  onAnswer: (antwort?: string) => void | Promise<void>;
  onDeny: () => void | Promise<void>;
  busy?: boolean;
  /** Knapper darstellen, wo wenig Platz ist (Chat, Sprachcockpit). */
  compact?: boolean;
}

export function ApprovalPrompt({ request, onAnswer, onDeny, busy, compact }: Props) {
  const [gewaehlt, setGewaehlt] = useState<string | null>(null);
  const [eigene, setEigene] = useState("");

  const antworten = (a?: string) => {
    setGewaehlt(a ?? null);
    onAnswer(a);
  };

  // Ohne Optionen bleibt es die alte Ja/Nein-Freigabe — die gibt es weiterhin,
  // etwa bei einem Befehl, der nur bestätigt werden muss.
  const optionen = request.options && request.options.length > 0 ? request.options : null;
  const zeigtAnsicht = Boolean(request.view && AGENT_VIEWS[request.view.name]);

  return (
    <div className="min-w-0">
      {/* Die Frage. Sie fehlte im Chat komplett — dort stand nur der
          Werkzeugname, der bei einer Rückfrage leer ist. */}
      <p className={cn("break-words", compact ? "text-sm" : "text-sm")}>
        {request.question || request.reasoning || request.tool || "Freigabe erforderlich"}
      </p>

      {request.context && (
        <p className="mt-1 text-xs text-muted-foreground break-words">{request.context}</p>
      )}

      {zeigtAnsicht && (
        <div className="mt-3">
          <AgentView
            name={request.view!.name}
            agentId={request.agent_id}
            data={request.view!.data || {}}
            antworten={antworten}
            beschaeftigt={busy}
          />
        </div>
      )}

      {/* Die Wortoptionen bleiben auch neben einer Ansicht stehen: wer lieber
          tippt oder die Bilder nicht laden kann, muss antworten können. */}
      <div className="mt-3 flex flex-wrap gap-2">
        {optionen ? (
          optionen.map((opt, i) => (
            <button
              key={opt}
              onClick={() => antworten(opt)}
              disabled={busy}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors disabled:opacity-50",
                i === 0
                  ? "bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30"
                  : "bg-foreground/[0.06] text-foreground/80 hover:bg-foreground/[0.1]"
              )}
            >
              {busy && gewaehlt === opt && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {opt}
            </button>
          ))
        ) : (
          <button
            onClick={() => antworten()}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-500/20 px-3 py-1.5 text-sm font-medium text-emerald-400 hover:bg-emerald-500/30 disabled:opacity-50"
          >
            {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Freigeben
          </button>
        )}
        <button
          onClick={onDeny}
          disabled={busy}
          className="rounded-lg bg-red-500/15 px-3 py-1.5 text-sm font-medium text-red-400 hover:bg-red-500/25 disabled:opacity-50 transition-colors"
        >
          Ablehnen
        </button>
      </div>

      {/* Eigene Antwort — oft passt keine der angebotenen Optionen. */}
      {optionen && (
        <div className="mt-2 flex gap-2">
          <input
            value={eigene}
            onChange={(e) => setEigene(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && eigene.trim()) antworten(eigene.trim());
            }}
            placeholder="Oder eigene Antwort…"
            disabled={busy}
            className="min-w-0 flex-1 rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-1.5 text-sm outline-none transition-all placeholder:text-muted-foreground/25 focus:border-primary/50 disabled:opacity-50"
          />
          <button
            onClick={() => antworten(eigene.trim())}
            disabled={busy || !eigene.trim()}
            className="shrink-0 rounded-lg border border-foreground/[0.08] px-3 py-1.5 text-sm transition-all hover:border-primary/40 hover:bg-primary/[0.06] disabled:cursor-not-allowed disabled:opacity-40"
          >
            Senden
          </button>
        </div>
      )}
    </div>
  );
}
