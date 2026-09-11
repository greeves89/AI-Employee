"use client";

import { useCallback, useEffect, useState } from "react";
import { Scale, Loader2, Search } from "lucide-react";
import { cn } from "@/lib/utils";
import { useToast } from "@/components/ui/dialog-provider";
import * as api from "@/lib/api";

export function GesetzeView({ embedded = false }: { embedded?: boolean }) {
  const toast = useToast();
  const [status, setStatus] = useState<api.GesetzeStatus | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<api.GesetzeTreffer[]>([]);
  const [searching, setSearching] = useState(false);
  const [searched, setSearched] = useState(false);

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await api.getGesetzeStatus());
    } catch {
      // Status ist rein informativ — kein Fehler-Toast fuer einen Nebenwert.
    }
  }, []);

  useEffect(() => { loadStatus(); }, [loadStatus]);

  const runSearch = async () => {
    const q = query.trim();
    if (!q) return;
    setSearching(true);
    try {
      const r = await api.searchGesetze(q);
      setResults(r.results);
      setSearched(true);
    } catch {
      toast.error("Gesetzessuche fehlgeschlagen.");
    } finally {
      setSearching(false);
    }
  };

  return (
    <div className={cn("space-y-5", !embedded && "p-6")}>
      <div className="flex items-start gap-3">
        <Scale className="mt-0.5 h-5 w-5 text-emerald-400" />
        <div className="flex-1">
          <h3 className="text-sm font-semibold">Gesetze</h3>
          <p className="mt-1 text-xs text-muted-foreground/70">
            Semantische Suche über das deutsche Bundesrecht (gesetze-im-internet.de),
            täglich neu gecrawlt und indiziert. Auch jeder Agent kann diese Quelle
            über das Werkzeug <code className="rounded bg-foreground/[0.06] px-1">gesetze_search</code> abfragen.
          </p>
          {status && (
            <p className="mt-1.5 text-[11px] text-muted-foreground/50">
              {status.law_count > 0
                ? `${status.law_count} Normen indiziert · zuletzt aktualisiert: ${
                    status.last_crawled_at ? new Date(status.last_crawled_at).toLocaleString("de-DE") : "unbekannt"
                  }`
                : "Erster Crawl-Lauf steht noch aus."}
            </p>
          )}
        </div>
      </div>

      <div className="rounded-xl border border-foreground/[0.06] bg-card/60 p-4">
        <div className="flex items-center gap-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && runSearch()}
            placeholder="z. B. Kündigungsfrist, DSGVO Löschfrist, Mindestlohn…"
            className="flex-1 rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-2.5 py-1.5 text-[13px] focus:border-primary/30 focus:outline-none"
          />
          <button
            onClick={runSearch}
            disabled={searching || !query.trim()}
            className="flex items-center gap-1.5 rounded-lg bg-foreground/[0.06] px-3 py-1.5 text-[12px] font-medium hover:bg-foreground/[0.1] transition-colors disabled:opacity-50"
          >
            {searching ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Search className="h-3.5 w-3.5" />}
            Suchen
          </button>
        </div>

        {searched && !searching && (
          <div className="mt-3 space-y-2">
            {results.length === 0 && (
              <p className="text-[12px] text-muted-foreground/60">Keine Treffer.</p>
            )}
            {results.map((r) => (
              <div key={r.path} className="rounded-lg border border-foreground/[0.05] bg-foreground/[0.02] p-3">
                <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground/50">{r.path}</p>
                {r.snippets.map((s, i) => (
                  <p key={i} className="mt-1 text-[12px] leading-relaxed text-foreground/80">{s}</p>
                ))}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
