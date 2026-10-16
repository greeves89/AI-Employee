"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, Crown, Loader2, MessageSquare, Pencil, Send, Trash2 } from "lucide-react";
import { Header } from "@/components/layout/header";
import { CreateTeamModal } from "@/components/agents/create-team-modal";
import { AgentAvatar } from "@/components/agents/agent-avatar";
import { ProjektRaum } from "@/components/projekte/projekt-raum";
import { raumZustand } from "@/components/raum/iso";
import { ProjektBoardAnsicht, ProjektVerlauf } from "@/components/projekte/projekt-board";
import { useConfirm, useToast } from "@/components/ui/dialog-provider";
import { useAgents } from "@/hooks/use-agents";
import * as api from "@/lib/api";
import { apiFehlertext } from "@/lib/api-fehler";
import { agentZustand } from "@/lib/aufgaben-anzeige";
import { projektFarbe, type ProjektBoard, type ProjektKarte } from "@/lib/projekte";
import { setVisibleInterval } from "@/lib/visible-interval";
import { cn } from "@/lib/utils";

export default function ProjektPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const toast = useToast();
  const confirm = useConfirm();
  const { agents, refresh: agentenNeu } = useAgents();
  const [projekt, setProjekt] = useState<api.Team | null>(null);
  const [board, setBoard] = useState<ProjektBoard | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);
  const [bearbeiten, setBearbeiten] = useState(false);
  const [text, setText] = useState("");
  const [ziel, setZiel] = useState<string>("");
  const [sendet, setSendet] = useState(false);
  const eingabe = useRef<HTMLTextAreaElement>(null);
  const boardRef = useRef<HTMLDivElement>(null);

  // Zuletzt gezeigter Stand: alle fünf Sekunden kommt meist dasselbe — dann
  // bleibt der Raum unangetastet statt neu gezeichnet zu werden.
  const zuletzt = useRef("");
  // Zählt Ladevorgänge: eine späte Antwort (anderes Projekt, Abnahme dazwischen)
  // darf keinen neueren Stand überschreiben.
  const lauf = useRef(0);

  const laden = useCallback(async () => {
    const meiner = ++lauf.current;
    try {
      const r = await api.getProjektBoard(id);
      if (meiner !== lauf.current) return;
      const stand = JSON.stringify(r);
      if (stand === zuletzt.current) return;
      zuletzt.current = stand;
      setProjekt(r.projekt);
      setBoard({ spalten: r.spalten, kennzahlen: r.kennzahlen });
      setFehler(null);
    } catch (e) {
      if (meiner === lauf.current) setFehler(apiFehlertext(e, "Das Projekt konnte nicht geladen werden."));
    }
  }, [id]);

  useEffect(() => {
    // Anderes Projekt: den alten Stand nicht stehen lassen, bis der neue da ist.
    zuletzt.current = "";
    setProjekt(null);
    setBoard(null);
    setFehler(null);
    laden();
    return setVisibleInterval(laden, 5000);
  }, [laden]);

  const agentById = new Map(agents.map((a) => [a.id, a]));
  const mitglieder = (projekt?.member_agent_ids ?? []).map((mid) => ({ id: mid, agent: agentById.get(mid) }));
  const farbe = projektFarbe(projekt?.farbe);
  const lead = projekt?.lead_agent_id ? agentById.get(projekt.lead_agent_id) : undefined;
  const brauchtWahl = !projekt?.lead_agent_id && mitglieder.length > 1;

  const abgeben = async () => {
    if (!text.trim() || !projekt) return;
    setSendet(true);
    try {
      const karte = await api.projektAuftrag(projekt.id, { text: text.trim(), agent_id: ziel || null });
      toast.success("Auftrag abgegeben", karte.titel);
      setText("");
      await laden();
      agentenNeu();
    } catch (e) {
      toast.error("Nicht abgegeben", apiFehlertext(e));
    } finally {
      setSendet(false);
    }
  };

  const abnehmen = async (k: ProjektKarte) => {
    try {
      await api.projektAbnehmen(id, k.id);
      await laden();
    } catch (e) {
      toast.error("Nicht abgenommen", apiFehlertext(e));
    }
  };

  const zurueckgeben = async (k: ProjektKarte, hinweis: string) => {
    try {
      await api.projektZurueckgeben(id, k.id, hinweis);
      toast.success("Zurückgegeben", "Die Nacharbeit läuft.");
      await laden();
    } catch (e) {
      toast.error("Nicht zurückgegeben", apiFehlertext(e));
      throw e;
    }
  };

  const loeschen = async () => {
    if (!projekt) return;
    const ok = await confirm({
      title: "Projekt löschen?",
      message: `„${projekt.name}“ verschwindet aus der Übersicht. Die Agenten und ihre Aufträge bleiben erhalten.`,
      variant: "destructive",
      confirmLabel: "Löschen",
    });
    if (!ok) return;
    try {
      await api.deleteTeam(projekt.id);
      router.push("/projekte");
    } catch (e) {
      toast.error("Nicht gelöscht", apiFehlertext(e));
    }
  };

  if (fehler && !projekt) {
    return (
      <div>
        <Header title="Projekt" />
        <div className="p-6">
          <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-500">{fehler}</div>
          <Link href="/projekte" className="mt-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
            <ArrowLeft className="h-4 w-4" /> Alle Projekte
          </Link>
        </div>
      </div>
    );
  }

  if (!projekt || !board) {
    return (
      <div className="flex justify-center py-24 text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    );
  }

  return (
    <div>
      <Header
        title={projekt.name}
        subtitle={projekt.description || undefined}
        titleAdornment={<span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: farbe }} />}
        actions={
          <div className="flex items-center gap-2">
            <Link href="/projekte" className="inline-flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-sm text-muted-foreground hover:bg-foreground/[0.04] hover:text-foreground">
              <ArrowLeft className="h-4 w-4" /> Alle Projekte
            </Link>
            <button type="button" onClick={() => setBearbeiten(true)} className="inline-flex items-center gap-1 rounded-lg border border-foreground/[0.1] px-2.5 py-1.5 text-sm hover:bg-foreground/[0.04]">
              <Pencil className="h-3.5 w-3.5" /> Bearbeiten
            </button>
            <button type="button" onClick={loeschen} aria-label="Projekt löschen" title="Projekt löschen" className="rounded-lg p-2 text-muted-foreground hover:bg-red-500/10 hover:text-red-500">
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        }
      />

      <div className="space-y-6 p-4 sm:p-6">
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_22rem]">
          <div className="rounded-2xl border border-foreground/[0.08] bg-card p-3">
            <ProjektRaum
              name={projekt.name}
              farbe={farbe}
              mitglieder={mitglieder}
              leadId={projekt.lead_agent_id}
              kennzahlen={board.kennzahlen}
              laufend={board.spalten.in_arbeit}
              onAgent={(aid) => {
                setZiel(aid);
                eingabe.current?.focus();
              }}
              onStapel={() => boardRef.current?.scrollIntoView({ behavior: "smooth" })}
            />
          </div>

          <div className="space-y-4">
            {/* Auftrag ans Projekt */}
            <div className="rounded-2xl border border-foreground/[0.08] bg-card p-4">
              <h2 className="text-sm font-semibold">Auftrag geben</h2>
              <textarea
                ref={eingabe}
                value={text}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) abgeben();
                }}
                rows={4}
                placeholder="Was soll erledigt werden? Die erste Zeile wird zum Titel."
                className="mt-2 w-full resize-none rounded-lg border border-foreground/[0.1] bg-background/80 px-3 py-2 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20"
              />
              <div className="mt-2 flex items-center gap-2">
                <select
                  value={ziel}
                  onChange={(e) => setZiel(e.target.value)}
                  aria-label="Empfänger"
                  className="min-w-0 flex-1 rounded-lg border border-foreground/[0.1] bg-background/80 px-2.5 py-2 text-sm outline-none"
                >
                  {projekt.lead_agent_id ? (
                    <option value="">An den Lead{lead ? ` (${lead.name})` : ""} — er verteilt</option>
                  ) : mitglieder.length === 1 ? (
                    <option value="">An {mitglieder[0].agent?.name ?? "das Mitglied"}</option>
                  ) : (
                    <option value="">Agent wählen …</option>
                  )}
                  {mitglieder.map(({ id: mid, agent }) => (
                    <option key={mid} value={mid}>
                      Direkt an {agent?.name ?? mid}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={abgeben}
                  disabled={sendet || !text.trim() || mitglieder.length === 0 || (brauchtWahl && !ziel)}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3.5 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                >
                  {sendet ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                  Abgeben
                </button>
              </div>
              {mitglieder.length === 0 && (
                <p className="mt-2 text-xs text-muted-foreground">Weise dem Projekt zuerst Agenten zu (Bearbeiten).</p>
              )}
              {ziel && agentById.get(ziel) && (
                <Link href={`/agents/${ziel}`} className="mt-2 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
                  <MessageSquare className="h-3.5 w-3.5" /> Chat mit {agentById.get(ziel)?.name} öffnen
                </Link>
              )}
            </div>

            {/* Kennzahlen */}
            <div className="rounded-2xl border border-foreground/[0.08] bg-card p-4">
              <div className="flex items-baseline justify-between">
                <h2 className="text-sm font-semibold">Erledigt</h2>
                <span className="text-xs text-muted-foreground">
                  {board.kennzahlen.fertig_7_tage} in 7 Tagen · {board.kennzahlen.heute_fertig} heute
                </span>
              </div>
              <div className="mt-3">
                <ProjektVerlauf kennzahlen={board.kennzahlen} farbe={farbe} />
              </div>
            </div>

            {/* Mitglieder */}
            <div className="rounded-2xl border border-foreground/[0.08] bg-card p-4">
              <h2 className="text-sm font-semibold">Im Raum</h2>
              <ul className="mt-2 space-y-1.5">
                {mitglieder.map(({ id: mid, agent }) => {
                  const zustand = raumZustand(agent);
                  const laeuft = board.spalten.in_arbeit.find((k) => k.agent_id === mid);
                  return (
                    <li key={mid}>
                      <button
                        type="button"
                        onClick={() => {
                          setZiel(mid);
                          eingabe.current?.focus();
                        }}
                        className={cn(
                          "flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left hover:bg-foreground/[0.04]",
                          ziel === mid && "bg-primary/[0.06]",
                        )}
                      >
                        <AgentAvatar config={agent?.config} size="sm" active={zustand !== "aus"} />
                        <span className="min-w-0 flex-1">
                          <span className="flex items-center gap-1 text-sm font-medium">
                            <span className="truncate">{agent?.name ?? "Gelöschter Agent"}</span>
                            {projekt.lead_agent_id === mid && <Crown className="h-3 w-3 shrink-0 text-amber-500" />}
                          </span>
                          <span className="block truncate text-[11px] text-muted-foreground">
                            {laeuft ? `Arbeitet an: ${laeuft.titel}` : agentZustand(agent?.state)}
                          </span>
                        </span>
                        <span
                          className={cn(
                            "h-2 w-2 shrink-0 rounded-full",
                            zustand === "arbeitet" ? "bg-emerald-500" : zustand === "fehler" ? "bg-red-500" : zustand === "aus" ? "bg-foreground/20" : "bg-sky-500",
                          )}
                        />
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          </div>
        </div>

        <div ref={boardRef} className="scroll-mt-20">
          <ProjektBoardAnsicht board={board} agents={agents} onAbnehmen={abnehmen} onZurueckgeben={zurueckgeben} />
        </div>
      </div>

      <CreateTeamModal projekt open={bearbeiten} onOpenChange={setBearbeiten} onSaved={laden} agents={agents} team={projekt} />
    </div>
  );
}
