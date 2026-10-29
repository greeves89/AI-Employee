"use client";

/**
 * „Regeln für Agenten ins Repo“: der Abschnitt „Zusammenarbeit mit AI Employee“
 * für die AGENTS.md (orchestrator: core/uebernahme.agents_md_abschnitt). Zum
 * Kopieren — oder als Pull Request ins Repo, nie direkt auf den Standard-Branch.
 */

import { useEffect, useState } from "react";
import { Check, Copy, ExternalLink, GitPullRequest, Loader2 } from "lucide-react";
import { Fenster } from "@/components/ui/fenster";
import * as api from "@/lib/api";
import { apiFehlertext } from "@/lib/api-fehler";

export function ProjektAgentenRegeln({
  projektId, offen, schliessen, darfPr,
}: {
  projektId: string;
  offen: boolean;
  schliessen: () => void;
  /** Nur wer das Projekt angelegt hat, stellt den Pull Request (Schlüssel des Projekts). */
  darfPr: boolean;
}) {
  const [text, setText] = useState<string | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);
  const [kopiert, setKopiert] = useState(false);
  const [stellt, setStellt] = useState(false);
  const [pr, setPr] = useState<{ url: string | null; aktuell: boolean } | null>(null);

  useEffect(() => {
    if (!offen) return;
    setFehler(null);
    setPr(null);
    api.projektAgentsMd(projektId)
      .then((r) => setText(r.text))
      .catch((e) => setFehler(apiFehlertext(e)));
  }, [offen, projektId]);

  const kopieren = async () => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setKopiert(true);
      setTimeout(() => setKopiert(false), 2000);
    } catch {
      setFehler("Kopieren ging nicht — bitte den Text markieren und selbst kopieren.");
    }
  };

  const prStellen = async () => {
    setStellt(true);
    setFehler(null);
    try {
      setPr(await api.projektAgentsMdPr(projektId));
    } catch (e) {
      setFehler(apiFehlertext(e));
    } finally {
      setStellt(false);
    }
  };

  return (
    <Fenster offen={offen} schliessen={schliessen} titel="Regeln für Agenten ins Repo" breite="max-w-3xl">
      <div className="space-y-3 p-5 text-sm">
        <p className="text-muted-foreground">
          Dieser Abschnitt gehört in die <code className="rounded bg-foreground/[0.06] px-1">AGENTS.md</code> des
          Repos. Er sagt allen, die am Repo arbeiten (Claude Code, andere Anlagen, Menschen), wie man ein Ticket
          übernimmt, woran die Plattform es erkennt und dass der Branch einer Übernahme dem Übernehmer gehört.
        </p>
        {text === null && !fehler ? (
          <div className="flex justify-center py-10 text-muted-foreground"><Loader2 className="h-5 w-5 animate-spin" /></div>
        ) : text ? (
          <pre className="max-h-[50vh] overflow-auto whitespace-pre-wrap rounded-lg border border-foreground/[0.08] bg-foreground/[0.03] p-3 font-mono text-xs">
            {text}
          </pre>
        ) : null}
        {fehler && (
          <p className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-600 dark:text-red-400">{fehler}</p>
        )}
        {pr && (
          <p className="rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-700 dark:text-emerald-300">
            {pr.aktuell ? (
              "Der Abschnitt steht schon so in der AGENTS.md des Repos — kein neuer Pull Request nötig."
            ) : (
              <>
                Pull Request angelegt.{" "}
                {pr.url && (
                  <a href={pr.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 font-medium underline-offset-2 hover:underline">
                    Im Repo ansehen <ExternalLink className="h-3 w-3" />
                  </a>
                )}
              </>
            )}
          </p>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={kopieren} disabled={!text}
            className="inline-flex items-center gap-1.5 rounded-lg border border-foreground/[0.1] px-3 py-1.5 hover:bg-foreground/[0.04] disabled:opacity-50">
            {kopiert ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
            {kopiert ? "Kopiert" : "Text kopieren"}
          </button>
          {darfPr && (
            <button type="button" onClick={prStellen} disabled={!text || stellt}
              title="Legt einen Branch an und stellt den Abschnitt als Pull Request — der Standard-Branch bleibt unberührt."
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
              {stellt ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <GitPullRequest className="h-3.5 w-3.5" />}
              Als Pull Request ins Repo
            </button>
          )}
        </div>
      </div>
    </Fenster>
  );
}
