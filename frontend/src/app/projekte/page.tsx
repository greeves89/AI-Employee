"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { FolderKanban, Loader2, Plus } from "lucide-react";
import { Header } from "@/components/layout/header";
import { CreateTeamModal } from "@/components/agents/create-team-modal";
import { ProjektRaum } from "@/components/projekte/projekt-raum";
import { ProjektVerlauf } from "@/components/projekte/projekt-board";
import { useAgents } from "@/hooks/use-agents";
import * as api from "@/lib/api";
import { apiFehlertext } from "@/lib/api-fehler";
import { TAKTE, bis, projektFarbe } from "@/lib/projekte";
import { setVisibleInterval } from "@/lib/visible-interval";

function Zahl({ wert, label, farbe }: { wert: number; label: string; farbe?: string }) {
  return (
    <div className="text-right">
      <div className="text-xl font-semibold tabular-nums" style={farbe && wert ? { color: farbe } : undefined}>
        {wert}
      </div>
      <div className="text-[10px] text-muted-foreground">{label}</div>
    </div>
  );
}

export default function ProjektePage() {
  const { agents } = useAgents();
  const [projekte, setProjekte] = useState<api.ProjektUebersicht[] | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);
  const [neu, setNeu] = useState(false);

  const laden = useCallback(async () => {
    try {
      const r = await api.getProjekte();
      setProjekte(r.projekte);
      setFehler(null);
    } catch (e) {
      setFehler(apiFehlertext(e));
    }
  }, []);

  useEffect(() => {
    laden();
    return setVisibleInterval(laden, 10000);
  }, [laden]);

  const agentById = new Map(agents.map((a) => [a.id, a]));

  return (
    <div>
      <Header
        title="Projekte"
        subtitle="Agenten einem Projekt zuweisen, Aufträge geben, Ergebnisse abnehmen"
        actions={
          <button
            type="button"
            onClick={() => setNeu(true)}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3.5 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          >
            <Plus className="h-4 w-4" />
            Neues Projekt
          </button>
        }
      />

      <div className="p-4 sm:p-6">
        {fehler && (
          <div className="mb-4 rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-500">{fehler}</div>
        )}
        {projekte === null && !fehler ? (
          <div className="flex justify-center py-20 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin" />
          </div>
        ) : projekte && projekte.length === 0 ? (
          <div className="mx-auto max-w-md py-20 text-center">
            <FolderKanban className="mx-auto h-10 w-10 text-muted-foreground/50" />
            <h2 className="mt-3 font-semibold">Noch kein Projekt</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Lege ein Projekt an, weise ihm Agenten zu und gib ihm Aufträge. Du siehst die Agenten dann bei der Arbeit.
            </p>
            <button
              type="button"
              onClick={() => setNeu(true)}
              className="mt-4 inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
            >
              <Plus className="h-4 w-4" />
              Neues Projekt
            </button>
          </div>
        ) : (
          <div className="grid gap-5 lg:grid-cols-2 2xl:grid-cols-3">
            {projekte?.map((pr) => {
              const farbe = projektFarbe(pr.farbe);
              return (
                <Link
                  key={pr.id}
                  href={`/projekte/${pr.id}`}
                  className="group flex flex-col rounded-2xl border border-foreground/[0.08] bg-card p-4 transition-colors hover:border-foreground/[0.16]"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h2 className="flex items-center gap-2 text-lg font-semibold">
                        <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: farbe }} />
                        <span className="truncate">{pr.name}</span>
                      </h2>
                      {pr.description && <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">{pr.description}</p>}
                      <p className="mt-0.5 text-[11px] text-muted-foreground">
                        {pr.takt?.aktiv ? `Takt ${TAKTE.find((t) => t.minuten === pr.takt.minuten)?.label.toLowerCase() ?? "an"} · nächster ${bis(pr.takt.naechster_lauf) ?? "—"}` : "Takt aus"}
                        {pr.kennzahlen.eingang ? ` · ${pr.kennzahlen.eingang} in der Inbox` : ""}
                        {pr.kennzahlen.triage ? ` · ${pr.kennzahlen.triage} Rückfrage${pr.kennzahlen.triage === 1 ? "" : "n"} offen` : ""}
                      </p>
                    </div>
                    <div className="flex shrink-0 gap-4">
                      <Zahl wert={pr.kennzahlen.bereit} label="bereit" farbe={farbe} />
                      <Zahl wert={pr.kennzahlen.in_arbeit} label="in Arbeit" />
                      <Zahl wert={pr.kennzahlen.pruefung} label="Review" farbe="#f97316" />
                      <Zahl wert={pr.kennzahlen.heute_fertig} label="heute fertig" />
                    </div>
                  </div>
                  <div className="mt-2">
                    <ProjektRaum
                      kompakt
                      name={pr.name}
                      farbe={farbe}
                      mitglieder={pr.member_agent_ids.map((id) => ({ id, agent: agentById.get(id) }))}
                      leadId={pr.lead_agent_id}
                      kennzahlen={pr.kennzahlen}
                      laufend={pr.laufend}
                    />
                  </div>
                  <div className="mt-2 grid grid-cols-[1fr_auto] items-end gap-4">
                    <div className="min-w-0 space-y-1">
                      {pr.laufend.length === 0 && pr.naechste.length === 0 ? (
                        <p className="text-xs text-muted-foreground">Gerade nichts in Arbeit.</p>
                      ) : (
                        <>
                          {pr.laufend.slice(0, 2).map((l) => (
                            <p key={l.task_id} className="truncate text-xs">
                              <span className="text-muted-foreground">Läuft: </span>
                              {l.karte_titel ?? l.titel}
                            </p>
                          ))}
                          {pr.naechste.slice(0, 1).map((k) => (
                            <p key={k.id} className="truncate text-xs">
                              <span className="text-muted-foreground">Als Nächstes: </span>
                              {k.titel}
                            </p>
                          ))}
                        </>
                      )}
                    </div>
                    <div className="w-40">
                      <ProjektVerlauf kennzahlen={pr.kennzahlen} farbe={farbe} />
                    </div>
                  </div>
                </Link>
              );
            })}
          </div>
        )}
      </div>

      <CreateTeamModal projekt open={neu} onOpenChange={setNeu} onSaved={laden} agents={agents} />
    </div>
  );
}
