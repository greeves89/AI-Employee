"use client";

import { useEffect, useState } from "react";
import { EyeOff, Loader2, TriangleAlert } from "lucide-react";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import { useToast } from "@/components/ui/dialog-provider";

/**
 * Anonymisierer vor dem Modell (core/anonymisierer, egress/anonym.py).
 *
 * Eingeschaltet ersetzt der Egress-Proxy Personendaten in allem, was der Agent an den
 * Modellanbieter schickt, durch Platzhalter und setzt sie in der Antwort wieder ein.
 * Namen erkennt keine Regel verlässlich — sie kommen aus der Liste hier.
 * Wirkt nicht, wenn der Egress-Proxy aus ist: dann steht das hier gelb.
 */
export function AnonymisiererCard({ agentId }: { agentId: string }) {
  const [stand, setStand] = useState<api.AnonymisiererStand | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  useEffect(() => {
    api.getAgentAnonymisierer(agentId)
      .then((s) => { setStand(s); setText(s.begriffe.join("\n")); })
      .catch(() => setStand(null));
  }, [agentId]);

  const speichern = async (patch: { aktiv?: boolean; begriffe?: string[] }) => {
    setBusy(true);
    try {
      const s = await api.updateAgentAnonymisierer(agentId, patch);
      setStand(s);
      setText(s.begriffe.join("\n"));
      toast.success(s.neu_gestartet ? "Eingeschaltet, der Agent wurde neu gestartet" : "Gespeichert");
    } catch (e) {
      toast.error("Speichern fehlgeschlagen", e instanceof Error ? e.message : undefined);
    } finally {
      setBusy(false);
    }
  };

  if (!stand) return null;
  const begriffe = text.split("\n").map((b) => b.trim()).filter(Boolean);
  const geaendert = begriffe.join("\n") !== stand.begriffe.join("\n");

  return (
    <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5 space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-2">
          <EyeOff className="h-4 w-4 mt-0.5 text-muted-foreground" />
          <div>
            <div className="text-sm font-medium">Personendaten vor dem Modell verbergen</div>
            <div className="text-xs text-muted-foreground/60">
              E-Mail-Adressen, Telefonnummern, IBAN, Kartennummern und die Namen unten gehen nur als
              Platzhalter an den Modellanbieter. In der Antwort und in Werkzeugaufrufen stehen wieder
              die echten Werte.
            </div>
          </div>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={stand.aktiv}
          aria-label="Anonymisierer"
          disabled={busy || !stand.unterstuetzt}
          onClick={() => speichern({ aktiv: !stand.aktiv })}
          className={cn(
            "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-50",
            stand.aktiv ? "bg-emerald-500" : "bg-foreground/15",
          )}
        >
          <span className={cn("inline-block h-4 w-4 rounded-full bg-white transition-transform",
            stand.aktiv ? "translate-x-6" : "translate-x-1")} />
        </button>
      </div>

      {stand.hinweis && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[12px] text-amber-800 dark:text-amber-300">
          <TriangleAlert className="h-4 w-4 shrink-0 mt-0.5" />
          <span>{stand.hinweis}</span>
        </div>
      )}

      {stand.unterstuetzt && (
        <div className="space-y-2">
          <label className="text-xs font-medium" htmlFor={`anonym-begriffe-${agentId}`}>
            Namen und Begriffe (einer je Zeile)
          </label>
          <textarea
            id={`anonym-begriffe-${agentId}`}
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={4}
            placeholder={"Herr Müller\nFirma Beispiel GmbH"}
            className="w-full rounded-lg border border-foreground/10 bg-background px-3 py-2 text-sm"
          />
          <div className="flex items-center justify-between">
            <span className="text-xs text-muted-foreground/60">
              Kunden, Personen, Projekte. Groß- und Kleinschreibung egal.
            </span>
            <button
              type="button"
              disabled={busy || !geaendert}
              onClick={() => speichern({ begriffe })}
              className="inline-flex items-center gap-1.5 rounded-lg border border-foreground/10 px-3 py-1.5 text-xs font-medium hover:bg-foreground/5 disabled:opacity-50"
            >
              {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              Liste speichern
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
