"use client";

/**
 * Projektgespräch: Menschen und Agenten eines Projekts (orchestrator: core/projekt_gespraech).
 * @Name spricht einen Agenten an — er antwortet in einem eigenen Lauf hier im Gespräch.
 * Wer niemanden anspricht, spricht den Lead an.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Bot, Gavel, Globe, Loader2, Send, User, Wand2 } from "lucide-react";
import type { Agent } from "@/lib/types";
import * as api from "@/lib/api";
import { apiFehlertext } from "@/lib/api-fehler";
import { NACHRICHT_ART, type ProjektNachricht } from "@/lib/projekte";
import { AgentAvatar } from "@/components/agents/agent-avatar";
import { MarkdownContent } from "@/components/ui/markdown-content";
import { useToast } from "@/components/ui/dialog-provider";
import { setVisibleInterval } from "@/lib/visible-interval";
import { cn, timeAgo } from "@/lib/utils";

export function ProjektGespraech({
  projektId,
  mitglieder,
  leadId,
  darfSchreiben,
}: {
  projektId: string;
  mitglieder: { id: string; agent?: Agent }[];
  leadId: string | null;
  darfSchreiben: boolean;
}) {
  const toast = useToast();
  const toastRef = useRef(toast);
  toastRef.current = toast;
  const [nachrichten, setNachrichten] = useState<ProjektNachricht[] | null>(null);
  const [text, setText] = useState("");
  // Eine Nachricht ist eine Nachricht. Nur eine Entscheidung tut mehr: Sie landet
  // zusätzlich in entscheidungen.md. Eine eigene Art „Frage“ bewirkte nichts außer
  // einem Etikett — die Auswahl dafür verwirrte mehr, als sie half.
  const [entscheidung, setEntscheidung] = useState(false);
  const [sendet, setSendet] = useState(false);
  // Angesprochene Agenten, deren Antwort noch aussteht (Wecken läuft im Hintergrund).
  const [wartetAuf, setWartetAuf] = useState<{ id: string; seit: number; ab: number }[]>([]);
  const ende = useRef<HTMLDivElement>(null);
  const zuletzt = useRef("");
  const agentById = new Map(mitglieder.filter((m) => m.agent).map((m) => [m.id, m.agent as Agent]));

  const laden = useCallback(async () => {
    try {
      const r = await api.projektGespraech(projektId);
      setWartetAuf((w) => {
        const offen = w.filter((x) => Date.now() - x.ab < 10 * 60_000
          && !r.nachrichten.some((n) => n.id > x.seit && n.autor_id === x.id));
        return offen.length === w.length ? w : offen;
      });
      const stand = JSON.stringify(r.nachrichten.map((n) => n.id));
      if (stand === zuletzt.current) return;
      zuletzt.current = stand;
      setNachrichten(r.nachrichten);
      requestAnimationFrame(() => ende.current?.scrollIntoView({ block: "end" }));
    } catch (e) {
      toastRef.current.error("Gespräch nicht geladen", apiFehlertext(e));
    }
  }, [projektId]);

  useEffect(() => {
    zuletzt.current = "";
    laden();
    return setVisibleInterval(laden, 5000);
  }, [laden]);

  const senden = async () => {
    if (!text.trim()) return;
    setSendet(true);
    try {
      const r = await api.projektGespraechSchreiben(projektId, {
        text: text.trim(), art: entscheidung ? "entscheidung" : "nachricht",
      });
      setText("");
      setEntscheidung(false);
      if (r.geweckt.length) {
        setWartetAuf((w) => [...w.filter((x) => !r.geweckt.includes(x.id)), ...r.geweckt.map((id) => ({ id, seit: r.id, ab: Date.now() }))]);
      }
      await laden();
    } catch (e) {
      toastRef.current.error("Nicht gesendet", apiFehlertext(e));
    } finally {
      setSendet(false);
    }
  };

  const lead = leadId ? agentById.get(leadId) : undefined;

  return (
    <div className="flex min-w-0 flex-col rounded-2xl border border-foreground/[0.08] bg-card">
      <div className="max-h-[36rem] min-h-[16rem] space-y-3 overflow-y-auto p-4">
        {nachrichten === null ? (
          <div className="flex justify-center py-10 text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /></div>
        ) : nachrichten.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted-foreground">
            Noch nichts besprochen. Schreib eine Nachricht — mit @Name sprichst du einen Agenten direkt an,
            ohne Ansprache antwortet {lead ? lead.name : "der Lead"}.
          </p>
        ) : (
          nachrichten.map((n) => {
            const agent = n.autor_id ? agentById.get(n.autor_id) : undefined;
            const meta = NACHRICHT_ART[n.art] ?? NACHRICHT_ART.nachricht;
            const system = n.autor_typ === "system";
            return (
              <div key={n.id} className={cn("flex gap-2.5", system && "rounded-xl bg-foreground/[0.03] p-2.5")}>
                <div className="shrink-0 pt-0.5">
                  {n.autor_typ === "agent" ? (
                    <AgentAvatar config={agent?.config} size="sm" />
                  ) : (
                    <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-foreground/[0.06]">
                      {system ? <Wand2 className="h-4 w-4 text-muted-foreground" />
                        : n.autor_typ === "extern" ? <Globe className="h-4 w-4 text-muted-foreground" />
                          : <User className="h-4 w-4 text-muted-foreground" />}
                    </div>
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs">
                    <span className="font-semibold">{n.autor_name}</span>
                    {meta.label && <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-medium", meta.klasse)}>{meta.label}</span>}
                    {n.an.length > 0 && (
                      <span className="text-muted-foreground">an {n.an.map((id) => agentById.get(id)?.name ?? id).join(", ")}</span>
                    )}
                    {n.erstellt && <span className="text-muted-foreground">{timeAgo(n.erstellt)}</span>}
                  </div>
                  <div className="mt-0.5 text-sm text-foreground/90"><MarkdownContent content={n.text} /></div>
                </div>
              </div>
            );
          })
        )}
        {wartetAuf.length > 0 && (
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            {wartetAuf.map((x) => agentById.get(x.id)?.name ?? x.id).join(", ")} antwortet gleich …
          </p>
        )}
        <div ref={ende} />
      </div>

      {darfSchreiben && (
        <div className="border-t border-foreground/[0.06] p-3">
          <div className="mb-2 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
            <span>Ansprechen:</span>
            {mitglieder.map(({ id, agent }) => agent && (
              <button key={id} type="button" onClick={() => setText((t) => `${t}${t && !t.endsWith(" ") ? " " : ""}@${agent.name} `)}
                className="inline-flex items-center gap-1 rounded-full border border-foreground/[0.1] px-2 py-0.5 hover:bg-foreground/[0.04]">
                <Bot className="h-3 w-3" /> {agent.name}
              </button>
            ))}
          </div>
          <div className="flex items-end gap-2">
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) senden(); }}
              rows={2}
              placeholder={`Nachricht ans Projekt — ohne @Name antwortet ${lead ? lead.name : "der Lead"}`}
              className="min-w-0 flex-1 resize-none rounded-lg border border-foreground/[0.1] bg-background/80 px-3 py-2 text-sm outline-none focus:border-primary/50"
            />
            <button
              type="button"
              onClick={() => setEntscheidung((e) => !e)}
              aria-pressed={entscheidung}
              title="Als Entscheidung festhalten — landet zusätzlich in entscheidungen.md im Projektwissen"
              className={cn(
                "inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-2 text-xs font-medium transition-colors",
                entscheidung
                  ? "border-primary/40 bg-primary/10 text-primary"
                  : "border-foreground/[0.1] text-muted-foreground hover:bg-foreground/[0.04] hover:text-foreground",
              )}
            >
              <Gavel className="h-3.5 w-3.5" /> Entscheidung
            </button>
            <button type="button" onClick={senden} disabled={sendet || !text.trim()} aria-label="Senden" title="Senden (Strg/Cmd + Enter)"
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
              {sendet ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
            </button>
          </div>
          {entscheidung && (
            <p className="mt-1.5 text-[11px] text-muted-foreground">Entscheidungen landen zusätzlich in entscheidungen.md im Projektwissen.</p>
          )}
        </div>
      )}
    </div>
  );
}
