"use client";

import { useEffect, useMemo, useState } from "react";
import { Check, Loader2, Users } from "lucide-react";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import { useToast } from "@/components/ui/dialog-provider";

/**
 * Agent anderen Menschen zum Nutzen freigeben (PUT /agents/{id}/freigaben).
 *
 * Freigegebene chatten und sprechen mit dem Agenten, schicken und holen Dateien.
 * Jeder sieht nur seine eigenen Gespräche. Einstellungen, Gedächtnis, Zeitpläne und
 * Löschen bleiben beim Besitzer und beim Admin. Die Nutzerliste ist dieselbe wie
 * beim Freigeben eines Projekts.
 */
export function FreigebenCard({ agentId }: { agentId: string }) {
  const [nutzer, setNutzer] = useState<{ id: string; name: string; email?: string }[] | null>(null);
  const [gewaehlt, setGewaehlt] = useState<string[]>([]);
  const [gespeichert, setGespeichert] = useState<string[]>([]);
  const [suche, setSuche] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  useEffect(() => {
    let lebendig = true;
    Promise.all([api.getAgentFreigaben(agentId), api.getProjektVerzeichnis()])
      .then(([f, v]) => {
        if (!lebendig) return;
        const ids = f.nutzer.map((n) => n.id);
        setGewaehlt(ids);
        setGespeichert(ids);
        setNutzer(v.users);
      })
      .catch(() => lebendig && setNutzer(null));
    return () => { lebendig = false; };
  }, [agentId]);

  const sichtbar = useMemo(() => {
    const q = suche.trim().toLowerCase();
    const liste = nutzer ?? [];
    return q ? liste.filter((n) => `${n.name} ${n.email ?? ""}`.toLowerCase().includes(q)) : liste;
  }, [nutzer, suche]);

  if (nutzer === null) return null;
  const geaendert = [...gewaehlt].sort().join(",") !== [...gespeichert].sort().join(",");

  const speichern = async () => {
    setBusy(true);
    try {
      const r = await api.setAgentFreigaben(agentId, gewaehlt);
      const ids = r.nutzer.map((n) => n.id);
      setGewaehlt(ids);
      setGespeichert(ids);
      toast.success(ids.length ? `Für ${ids.length} ${ids.length === 1 ? "Person" : "Personen"} freigegeben` : "Freigabe entfernt");
    } catch (e) {
      toast.error("Speichern fehlgeschlagen", e instanceof Error ? e.message : undefined);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5 space-y-3">
      <div className="flex items-start gap-2">
        <Users className="h-4 w-4 mt-0.5 text-muted-foreground" />
        <div>
          <div className="text-sm font-medium">Freigeben für</div>
          <div className="text-[11px] text-muted-foreground/60">
            Freigegebene chatten und sprechen mit dem Agenten und sehen nur ihre eigenen Gespräche.
            Einstellungen, Gedächtnis, Zeitpläne und Löschen bleiben bei dir. Was der Agent in seinem
            Gedächtnis und seinen Dateien ablegt, kennt er für alle.
          </div>
        </div>
      </div>

      {nutzer.length === 0 ? (
        <p className="text-xs text-muted-foreground">Keine weiteren Nutzer auf dieser Anlage.</p>
      ) : (
        <>
          {nutzer.length > 8 && (
            <input
              type="search"
              value={suche}
              onChange={(e) => setSuche(e.target.value)}
              placeholder="Person suchen"
              aria-label="Person suchen"
              className="w-full rounded-lg border border-foreground/[0.08] bg-background px-3 py-1.5 text-sm"
            />
          )}
          <div className="max-h-56 space-y-1 overflow-y-auto rounded-lg border border-foreground/[0.06] p-1.5">
            {sichtbar.map((n) => {
              const an = gewaehlt.includes(n.id);
              return (
                <button
                  key={n.id}
                  type="button"
                  aria-pressed={an}
                  onClick={() => setGewaehlt((g) => (an ? g.filter((x) => x !== n.id) : [...g, n.id]))}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors",
                    an ? "bg-primary/[0.08]" : "hover:bg-foreground/[0.04]",
                  )}
                >
                  <span className={cn("flex h-4 w-4 shrink-0 items-center justify-center rounded border",
                    an ? "border-primary bg-primary text-white" : "border-foreground/20")}>
                    {an && <Check className="h-3 w-3" />}
                  </span>
                  <span className="truncate">{n.name}</span>
                  {n.email && <span className="truncate text-xs text-muted-foreground">{n.email}</span>}
                </button>
              );
            })}
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[11px] text-muted-foreground/60">
              {gewaehlt.length === 0 ? "Nur du nutzt diesen Agenten." : `${gewaehlt.length} ausgewählt`}
            </span>
            <button
              type="button"
              disabled={!geaendert || busy}
              onClick={speichern}
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-40"
            >
              {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              Speichern
            </button>
          </div>
        </>
      )}
    </div>
  );
}
