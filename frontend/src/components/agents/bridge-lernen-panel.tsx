"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, Loader2, Sparkles, Trash2, X } from "lucide-react";
import {
  ablehnenBridgeVorschlag,
  annehmenBridgeVorschlag,
  auswertenBridgeLernen,
  deleteBridgeProtokoll,
  getBridgeLernen,
  getBridgeProtokoll,
  setBridgeLernen,
  type BridgeLernenStand,
  type BridgeNutzungEintrag,
} from "@/lib/api";
import { cn } from "@/lib/utils";

function fehlerText(e: unknown): string {
  const roh = e instanceof Error ? e.message : String(e);
  const json = roh.indexOf("{");
  if (json >= 0) {
    try {
      const detail = JSON.parse(roh.slice(json)).detail;
      if (typeof detail === "string") return detail;
    } catch {
      /* roher Text */
    }
  }
  return roh;
}

const STATUS_TEXT: Record<string, string> = {
  ok: "Ausgewertet.",
  zu_wenig: "Noch zu wenig Daten: Erst wenn dieselben Fenster an mehreren Tagen wiederkehren, lässt sich etwas ableiten.",
  kein_agent: "Der Agent braucht ein aktuelles Abbild, um auszuwerten — bitte aktualisieren.",
  fehler: "Der Agent konnte gerade nicht antworten. Später nochmal versuchen.",
  aus: "Lernen ist aus.",
};

function minuten(sek: number): string {
  return sek < 90 ? `${sek} s` : `${Math.round(sek / 60)} min`;
}

/** „Aus meiner Arbeit lernen“: Schalter (Standard aus), einsehbares und löschbares
 *  Protokoll, Regelvorschläge des Agenten. Ausschalten löscht das Protokoll. */
export function BridgeLernenPanel({ agentId, beobachten, onRegelAngelegt }: {
  agentId: string;
  beobachten: boolean;
  onRegelAngelegt: () => void;
}) {
  const [stand, setStand] = useState<BridgeLernenStand | null>(null);
  const [protokoll, setProtokoll] = useState<BridgeNutzungEintrag[] | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);
  const [hinweis, setHinweis] = useState<string | null>(null);
  const [arbeitet, setArbeitet] = useState(false);

  const laden = useCallback(async () => {
    try {
      setStand(await getBridgeLernen());
    } catch (e) {
      setFehler(fehlerText(e));
    }
  }, []);

  useEffect(() => {
    laden();
  }, [laden]);

  const ausfuehren = async (aktion: () => Promise<void>) => {
    setArbeitet(true);
    setFehler(null);
    setHinweis(null);
    try {
      await aktion();
    } catch (e) {
      setFehler(fehlerText(e));
    } finally {
      setArbeitet(false);
    }
  };

  const umschalten = () =>
    ausfuehren(async () => {
      if (!stand) return;
      const an = !stand.aktiv;
      await setBridgeLernen(an, an ? agentId : undefined);
      setProtokoll(null);
      await laden();
    });

  const protokollZeigen = () =>
    ausfuehren(async () => {
      setProtokoll((await getBridgeProtokoll()).eintraege);
    });

  const protokollLoeschen = () =>
    ausfuehren(async () => {
      await deleteBridgeProtokoll();
      setProtokoll(null);
      await laden();
    });

  const auswerten = () =>
    ausfuehren(async () => {
      const r = await auswertenBridgeLernen();
      setHinweis(r.status === "ok" && r.vorschlaege === 0 ? "Nichts vorzuschlagen." : STATUS_TEXT[r.status] ?? r.status);
      await laden();
    });

  const annehmen = (id: number) =>
    ausfuehren(async () => {
      await annehmenBridgeVorschlag(id);
      await laden();
      onRegelAngelegt();
    });

  const ablehnen = (id: number) =>
    ausfuehren(async () => {
      await ablehnenBridgeVorschlag(id);
      await laden();
    });

  const an = !!stand?.aktiv;

  return (
    <div className="space-y-2 rounded-lg border border-foreground/[0.06] p-3" data-testid="bridge-lernen">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 text-xs font-medium text-foreground">
            <Sparkles className="h-3 w-3" />
            Aus meiner Arbeit lernen
          </p>
          <p className="text-[10px] text-muted-foreground/60">
            Die Bridge merkt sich, welche Anwendung und welchen Fenstertitel du wie lange offen hast — keine Bilder,
            keine Passwort- oder Anmeldefenster. Der Agent wertet das täglich aus und schlägt dir Regeln vor.
            Angelegt wird nur, was du annimmst. Ausschalten löscht das Protokoll.
          </p>
        </div>
        <button
          disabled={(!an && !beobachten) || !stand || arbeitet}
          onClick={umschalten}
          className={cn(
            "relative inline-flex h-5 w-9 shrink-0 rounded-full border-2 border-transparent transition-colors disabled:opacity-40",
            an ? "bg-red-500" : "bg-foreground/[0.12]",
          )}
          role="switch"
          aria-checked={an}
          aria-label="Aus meiner Arbeit lernen"
        >
          <span className={cn(
            "pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow transform transition-transform",
            an ? "translate-x-4" : "translate-x-0",
          )} />
        </button>
      </div>

      {!beobachten && (
        <p className="text-[10px] text-amber-700 dark:text-amber-400/80">
          Braucht „Mitsehen“ unter Berechtigungen.
        </p>
      )}

      {stand && stand.vorschlaege.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-[10px] font-medium text-muted-foreground/60 uppercase tracking-wider">Vorschläge</p>
          {stand.vorschlaege.map((v) => (
            <div key={v.id} className="rounded-lg border border-violet-500/20 bg-violet-500/5 px-3 py-2">
              <p className="text-xs font-medium text-foreground">{v.name}</p>
              <p className="text-[10px] text-muted-foreground/70">
                Wenn {[v.wenn_app && `App enthält „${v.wenn_app}“`, v.wenn_titel && `Titel enthält „${v.wenn_titel}“`].filter(Boolean).join(" und ")}
                {" → „"}{v.angebot}{"“"}
              </p>
              <p className="text-[10px] text-muted-foreground/70">Danach tut der Agent: {v.auftrag}</p>
              {v.begruendung && <p className="text-[10px] italic text-muted-foreground/50">{v.begruendung}</p>}
              <div className="mt-1.5 flex gap-1.5">
                <button
                  disabled={arbeitet}
                  onClick={() => annehmen(v.id)}
                  className="inline-flex items-center gap-1 rounded border border-violet-500/30 px-2 py-1 text-[10px] font-medium text-violet-400 disabled:opacity-40"
                >
                  <Check className="h-3 w-3" /> Annehmen
                </button>
                <button
                  disabled={arbeitet}
                  onClick={() => ablehnen(v.id)}
                  className="inline-flex items-center gap-1 rounded border border-foreground/[0.08] px-2 py-1 text-[10px] text-muted-foreground hover:text-foreground disabled:opacity-40"
                >
                  <X className="h-3 w-3" /> Ablehnen
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {an && stand && (
        <div className="space-y-1.5">
          <p className="text-[10px] text-muted-foreground/60">
            {stand.eintraege} Einträge im Protokoll (werden nach {stand.aufbewahrung_tage} Tagen gelöscht).
            {stand.ausgewertet_at ? ` Zuletzt ausgewertet: ${new Date(stand.ausgewertet_at).toLocaleString("de-DE")}.` : ""}
          </p>
          <div className="flex flex-wrap gap-1.5">
            <button onClick={protokollZeigen} disabled={arbeitet}
              className="rounded border border-foreground/[0.08] px-2 py-1 text-[10px] text-muted-foreground hover:text-foreground disabled:opacity-40">
              Protokoll ansehen
            </button>
            <button onClick={auswerten} disabled={arbeitet}
              className="inline-flex items-center gap-1 rounded border border-foreground/[0.08] px-2 py-1 text-[10px] text-muted-foreground hover:text-foreground disabled:opacity-40">
              {arbeitet && <Loader2 className="h-3 w-3 animate-spin" />} Jetzt auswerten
            </button>
            <button onClick={protokollLoeschen} disabled={arbeitet || stand.eintraege === 0}
              className="inline-flex items-center gap-1 rounded border border-foreground/[0.08] px-2 py-1 text-[10px] text-muted-foreground hover:text-red-400 disabled:opacity-40">
              <Trash2 className="h-3 w-3" /> Protokoll löschen
            </button>
          </div>
        </div>
      )}

      {protokoll && (
        <div className="max-h-48 overflow-y-auto rounded-lg border border-foreground/[0.06]">
          {protokoll.length === 0 && <p className="px-3 py-2 text-[10px] text-muted-foreground/60">Noch nichts protokolliert.</p>}
          {protokoll.map((e) => (
            <p key={e.id} className="truncate border-b border-foreground/[0.04] px-3 py-1 text-[10px] text-muted-foreground last:border-0">
              <span className="text-foreground">{e.app || "–"}</span> · {e.titel || "–"} · {minuten(e.dauer_sekunden)}
              {e.ende ? ` · ${new Date(e.ende).toLocaleString("de-DE")}` : ""}
            </p>
          ))}
        </div>
      )}

      {hinweis && <p className="text-[11px] text-muted-foreground">{hinweis}</p>}
      {fehler && <p className="text-[11px] text-red-400">{fehler}</p>}
    </div>
  );
}
