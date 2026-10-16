"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, BookOpen, Crown, GitBranch, KanbanSquare, Loader2, MessageSquare, Pencil, RefreshCw, Send, Trash2, TriangleAlert, Users } from "lucide-react";
import { Header } from "@/components/layout/header";
import { CreateTeamModal } from "@/components/agents/create-team-modal";
import { AgentAvatar } from "@/components/agents/agent-avatar";
import { ProjektRaum } from "@/components/projekte/projekt-raum";
import { ProjektBoardAnsicht, ProjektVerlauf, type BoardAktionen } from "@/components/projekte/projekt-board";
import { ProjektLeitstand } from "@/components/projekte/projekt-leitstand";
import { ProjektWissen } from "@/components/projekte/projekt-wissen";
import { raumZustand } from "@/components/raum/iso";
import { useConfirm, useToast } from "@/components/ui/dialog-provider";
import { useAgents } from "@/hooks/use-agents";
import { useAuthStore } from "@/lib/auth";
import * as api from "@/lib/api";
import { apiFehlertext } from "@/lib/api-fehler";
import { agentZustand } from "@/lib/aufgaben-anzeige";
import { projektFarbe, type ProjektBoard } from "@/lib/projekte";
import { setVisibleInterval } from "@/lib/visible-interval";
import { cn, timeAgo } from "@/lib/utils";

type Reiter = "board" | "wissen" | "team";

const REITER: { key: Reiter; label: string; Icon: typeof KanbanSquare }[] = [
  { key: "board", label: "Board", Icon: KanbanSquare },
  { key: "wissen", label: "Wissen", Icon: BookOpen },
  { key: "team", label: "Team", Icon: Users },
];

export default function ProjektPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const toast = useToast();
  const confirm = useConfirm();
  const rolle = useAuthStore((s) => s.user?.role);
  const darfSchreiben = rolle !== "viewer";
  const { agents, refresh: agentenNeu } = useAgents();
  const [projekt, setProjekt] = useState<api.Team | null>(null);
  const [board, setBoard] = useState<ProjektBoard | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);
  const [bearbeiten, setBearbeiten] = useState(false);
  const [reiter, setReiter] = useState<Reiter>("board");
  const [text, setText] = useState("");
  const [ziel, setZiel] = useState<string>("");
  const [nurEingang, setNurEingang] = useState(false);
  const [sendet, setSendet] = useState(false);
  const [gleichtAb, setGleichtAb] = useState(false);
  const eingabe = useRef<HTMLTextAreaElement>(null);
  const boardRef = useRef<HTMLDivElement>(null);
  // Zuletzt gezeigter Stand: alle fünf Sekunden kommt meist dasselbe — dann bleibt
  // der Raum unangetastet. Der Zähler verwirft späte Antworten (anderes Projekt).
  const zuletzt = useRef("");
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
      setBoard({ spalten: r.spalten, laufend: r.laufend, kennzahlen: r.kennzahlen, takt: r.takt });
      setFehler(null);
    } catch (e) {
      if (meiner === lauf.current) setFehler(apiFehlertext(e, "Das Projekt konnte nicht geladen werden."));
    }
  }, [id]);

  useEffect(() => {
    zuletzt.current = "";
    setProjekt(null);
    setBoard(null);
    setFehler(null);
    laden();
    return setVisibleInterval(laden, 5000);
  }, [laden]);

  const agentById = new Map(agents.map((a) => [a.id, a]));
  const mitgliederIds = projekt?.member_agent_ids ?? [];
  const mitglieder = mitgliederIds.map((mid) => ({ id: mid, agent: agentById.get(mid) }));
  const farbe = projektFarbe(projekt?.farbe);
  const lead = projekt?.lead_agent_id ? agentById.get(projekt.lead_agent_id) : undefined;
  const brauchtWahl = !projekt?.lead_agent_id && mitglieder.length > 1;
  // Agenten auf altem Abbild kennen die Projekt-Werkzeuge nicht (projekt_board …).
  const veraltet = mitglieder.filter((m) => m.agent?.update_available || m.agent?.image_outdated);

  const ausfuehren = async (titel: string, fn: () => Promise<unknown>) => {
    try {
      await fn();
      await laden();
      agentenNeu();
    } catch (e) {
      toast.error(titel, apiFehlertext(e));
    }
  };

  const aktionen: BoardAktionen = {
    verschieben: (k, aenderung) => ausfuehren("Nicht verschoben", () => api.karteAendern(id, k.id, aenderung)),
    vergeben: (k, agentId) => ausfuehren("Nicht vergeben", () => api.karteVergeben(id, k.id, agentId)),
    abnehmen: (k) => ausfuehren("Nicht abgenommen", () => api.karteAbnehmen(id, k.id)),
    zurueckgeben: async (k, hinweis) => {
      try {
        await api.karteZurueckgeben(id, k.id, hinweis);
        toast.success("Zurückgegeben", "Die Nacharbeit läuft.");
        await laden();
      } catch (e) {
        toast.error("Nicht zurückgegeben", apiFehlertext(e));
        throw e;
      }
    },
    anlegen: (titel, beschreibung) =>
      ausfuehren("Nicht angelegt", () => api.karteAnlegen(id, { titel, text: beschreibung, spalte: "eingang" })),
  };

  const abgeben = async () => {
    if (!text.trim() || !projekt) return;
    setSendet(true);
    try {
      const karte = await api.projektAuftrag(projekt.id, { text: text.trim(), agent_id: ziel || null, nur_eingang: nurEingang });
      toast.success(nurEingang ? "In den Eingang gelegt" : "Auftrag abgegeben", karte.titel);
      setText("");
      await laden();
      agentenNeu();
    } catch (e) {
      toast.error("Nicht abgegeben", apiFehlertext(e));
    } finally {
      setSendet(false);
    }
  };

  const loeschen = async () => {
    if (!projekt) return;
    const ok = await confirm({
      title: "Projekt löschen?",
      message: `„${projekt.name}“ verschwindet aus der Übersicht, sein Takt hört auf. Agenten, Aufträge und das Projektwissen bleiben erhalten.`,
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

  const repoAbgleichen = async () => {
    if (!projekt) return;
    setGleichtAb(true);
    try {
      const r = await api.projektRepoAbgleichen(projekt.id);
      toast.success("Abgeglichen", `${r.neu} neu · ${r.aktualisiert} aktualisiert · ${r.erledigt} erledigt`);
      zuletzt.current = "";
      await laden();
    } catch (e) {
      toast.error("Nicht abgeglichen", apiFehlertext(e));
      zuletzt.current = "";
      await laden();
    } finally {
      setGleichtAb(false);
    }
  };

  const zumBoard = () => {
    setReiter("board");
    boardRef.current?.scrollIntoView({ behavior: "smooth" });
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
            {darfSchreiben && projekt.darf_verwalten && (
              <>
                <button type="button" onClick={() => setBearbeiten(true)} className="inline-flex items-center gap-1 rounded-lg border border-foreground/[0.1] px-2.5 py-1.5 text-sm hover:bg-foreground/[0.04]">
                  <Pencil className="h-3.5 w-3.5" /> Bearbeiten
                </button>
                <button type="button" onClick={loeschen} aria-label="Projekt löschen" title="Projekt löschen" className="rounded-lg p-2 text-muted-foreground hover:bg-red-500/10 hover:text-red-500">
                  <Trash2 className="h-4 w-4" />
                </button>
              </>
            )}
          </div>
        }
      />

      <div className="space-y-6 p-4 sm:p-6">
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_24rem]">
          <div className="space-y-4">
            <div className="rounded-2xl border border-foreground/[0.08] bg-card p-3">
              <ProjektRaum
                name={projekt.name}
                farbe={farbe}
                mitglieder={mitglieder}
                leadId={projekt.lead_agent_id}
                kennzahlen={board.kennzahlen}
                laufend={board.laufend}
                onAgent={(aid) => { setZiel(aid); eingabe.current?.focus(); }}
                onStapel={zumBoard}
              />
            </div>

            <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_14rem]">
              {darfSchreiben ? (
                <div className="rounded-2xl border border-foreground/[0.08] bg-card p-4">
                  <h2 className="text-sm font-semibold">Auftrag geben</h2>
                  <textarea
                    ref={eingabe}
                    value={text}
                    onChange={(e) => setText(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) abgeben(); }}
                    rows={3}
                    placeholder="Was soll erledigt werden? Die erste Zeile wird zum Titel der Karte."
                    className="mt-2 w-full resize-none rounded-lg border border-foreground/[0.1] bg-background/80 px-3 py-2 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20"
                  />
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <select
                      value={ziel}
                      onChange={(e) => setZiel(e.target.value)}
                      aria-label="Empfänger"
                      disabled={nurEingang}
                      className="min-w-0 flex-1 rounded-lg border border-foreground/[0.1] bg-background/80 px-2.5 py-2 text-sm outline-none disabled:opacity-50"
                    >
                      {projekt.lead_agent_id ? (
                        <option value="">An den Lead{lead ? ` (${lead.name})` : ""} — er verteilt</option>
                      ) : mitglieder.length === 1 ? (
                        <option value="">An {mitglieder[0].agent?.name ?? "das Mitglied"}</option>
                      ) : (
                        <option value="">Agent wählen …</option>
                      )}
                      {mitglieder.map(({ id: mid, agent }) => (
                        <option key={mid} value={mid}>Direkt an {agent?.name ?? mid}</option>
                      ))}
                    </select>
                    <label className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
                      <input type="checkbox" checked={nurEingang} onChange={(e) => setNurEingang(e.target.checked)} />
                      nur in den Eingang
                    </label>
                    <button
                      type="button"
                      onClick={abgeben}
                      disabled={sendet || !text.trim() || mitglieder.length === 0 || (!nurEingang && brauchtWahl && !ziel)}
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
              ) : <div />}
              <div className="rounded-2xl border border-foreground/[0.08] bg-card p-4">
                <div className="flex items-baseline justify-between">
                  <h2 className="text-sm font-semibold">Erledigt</h2>
                  <span className="text-[11px] text-muted-foreground">{board.kennzahlen.fertig_7_tage} in 7 Tagen</span>
                </div>
                <div className="mt-3"><ProjektVerlauf kennzahlen={board.kennzahlen} farbe={farbe} /></div>
              </div>
            </div>
          </div>

          <ProjektLeitstand board={board} agents={agents} farbe={farbe} onZumBoard={zumBoard} />
        </div>

        <div ref={boardRef} className="scroll-mt-20 space-y-4">
          {veraltet.length > 0 && (
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-xl border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">
              <TriangleAlert className="h-3.5 w-3.5" />
              <span>
                {veraltet.length === 1 ? "Ein Agent läuft" : `${veraltet.length} Agenten laufen`} auf altem Stand — ohne
                Aktualisieren fehlen {veraltet.length === 1 ? "ihm" : "ihnen"} die Projekt-Werkzeuge (Board, Karten, Wissen):
              </span>
              {veraltet.map((m) => (
                <Link key={m.id} href={`/agents/${m.id}`} className="font-medium underline-offset-2 hover:underline">{m.agent?.name}</Link>
              ))}
            </div>
          )}
          {!projekt.repo && projekt.darf_verwalten && (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-dashed border-foreground/[0.12] px-3 py-2 text-xs text-muted-foreground">
              <GitBranch className="h-3.5 w-3.5" />
              <span>Kein GitHub-Repo verbunden — mit Repo kommen offene Issues und Pull Requests von selbst aufs Board.</span>
              <button type="button" onClick={() => setBearbeiten(true)}
                className="ml-auto rounded-lg border border-foreground/[0.1] px-2 py-1 text-foreground hover:bg-foreground/[0.04]">
                Repo verbinden
              </button>
            </div>
          )}
          {projekt.repo && (
            <div className={cn("flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border px-3 py-2 text-xs",
              projekt.repo_fehler ? "border-red-500/30 bg-red-500/5" : "border-foreground/[0.08] bg-card")}>
              <GitBranch className="h-3.5 w-3.5 text-muted-foreground" />
              <a href={`https://github.com/${projekt.repo}`} target="_blank" rel="noopener noreferrer" className="font-medium hover:underline">
                {projekt.repo}
              </a>
              <span className="text-muted-foreground">
                {projekt.repo_abgleich_at ? `abgeglichen ${timeAgo(projekt.repo_abgleich_at)}` : "noch nicht abgeglichen"} · alle 10 Min. automatisch
              </span>
              {projekt.repo_fehler && (
                <span className="inline-flex items-center gap-1 text-red-600 dark:text-red-400">
                  <TriangleAlert className="h-3.5 w-3.5" /> {projekt.repo_fehler}
                </span>
              )}
              {darfSchreiben && (
                <button type="button" onClick={repoAbgleichen} disabled={gleichtAb}
                  className="ml-auto inline-flex items-center gap-1 rounded-lg border border-foreground/[0.1] px-2 py-1 hover:bg-foreground/[0.04] disabled:opacity-50">
                  <RefreshCw className={cn("h-3 w-3", gleichtAb && "animate-spin")} /> Jetzt abgleichen
                </button>
              )}
            </div>
          )}
          <div className="flex gap-1 border-b border-foreground/[0.06]">
            {REITER.map(({ key, label, Icon }) => (
              <button key={key} type="button" onClick={() => setReiter(key)}
                className={cn("-mb-px inline-flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm",
                  reiter === key ? "border-primary font-medium text-foreground" : "border-transparent text-muted-foreground hover:text-foreground")}>
                <Icon className="h-4 w-4" /> {label}
              </button>
            ))}
          </div>

          {reiter === "board" && (
            <ProjektBoardAnsicht board={board} agents={agents} mitglieder={mitgliederIds} leadId={projekt.lead_agent_id}
              farbe={farbe} aktionen={aktionen} />
          )}
          {reiter === "wissen" && <ProjektWissen projektId={projekt.id} darfSchreiben={darfSchreiben} />}
          {reiter === "team" && (
            <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
              {mitglieder.map(({ id: mid, agent }) => {
                const zustand = raumZustand(agent);
                const laeuft = board.laufend.find((l) => l.agent_id === mid);
                return (
                  <li key={mid}>
                    <Link href={`/agents/${mid}`} className="flex items-center gap-3 rounded-xl border border-foreground/[0.08] bg-card px-3 py-2.5 hover:bg-foreground/[0.03]">
                      <AgentAvatar config={agent?.config} size="sm" active={zustand !== "aus"} />
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-1 text-sm font-medium">
                          <span className="truncate">{agent?.name ?? "Gelöschter Agent"}</span>
                          {projekt.lead_agent_id === mid && <Crown className="h-3 w-3 shrink-0 text-amber-500" />}
                        </span>
                        <span className="block truncate text-[11px] text-muted-foreground">
                          {laeuft ? `Arbeitet an: ${laeuft.karte_titel ?? laeuft.titel}` : agentZustand(agent?.state)}
                        </span>
                      </span>
                      <span className={cn("h-2 w-2 shrink-0 rounded-full",
                        zustand === "arbeitet" ? "bg-emerald-500" : zustand === "fehler" ? "bg-red-500" : zustand === "aus" ? "bg-foreground/20" : "bg-sky-500")} />
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>

      <CreateTeamModal projekt open={bearbeiten} onOpenChange={setBearbeiten} onSaved={() => { zuletzt.current = ""; laden(); }}
        agents={agents} team={projekt} />
    </div>
  );
}
