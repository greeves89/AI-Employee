"use client";

/**
 * Projektwissen: eigene Wissensablage je Projekt (orchestrator: core/projekt_wissen).
 * index.md geht bei jedem Projektauftrag an den Agenten mit — aber nur ihr Anfang.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { FileText, History, Loader2, Pencil, Plus, RotateCcw, Save, Search, Sparkles, X } from "lucide-react";
import * as api from "@/lib/api";
import { apiFehlertext } from "@/lib/api-fehler";
import { MarkdownContent } from "@/components/ui/markdown-content";
import { Fenster } from "@/components/ui/fenster";
import { useToast } from "@/components/ui/dialog-provider";
import { cn } from "@/lib/utils";

/**
 * Spiegel von ``projekt_wissen.AUSZUG_ZEICHEN`` (orchestrator). Mehr von index.md
 * geht nicht in einen Projektauftrag ein; wer die Grenze nicht kennt, schreibt
 * den Seitenschluss ins Leere (Issue #979). Ein Test im Orchestrator hält die
 * beiden Zahlen zusammen.
 */
const AUSZUG_ZEICHEN = 1500;

export function ProjektWissen({
  projektId, darfSchreiben, onAufbauen,
}: {
  projektId: string;
  darfSchreiben: boolean;
  /** Karte „Projektwissen aufbauen“ für den Lead anlegen. */
  onAufbauen?: () => Promise<void>;
}) {
  const toast = useToast();
  // Über eine Referenz, damit Laden/Öffnen nicht bei jedem Rendern neu entstehen
  // (sonst lüde der Effekt unten endlos nach, falls useToast kein festes Objekt liefert).
  const toastRef = useRef(toast);
  toastRef.current = toast;
  const [seiten, setSeiten] = useState<string[]>([]);
  const [pfad, setPfad] = useState("index.md");
  const [inhalt, setInhalt] = useState<string | null>(null);
  const [entwurf, setEntwurf] = useState("");
  const [bearbeiten, setBearbeiten] = useState(false);
  const [speichert, setSpeichert] = useState(false);
  const [frage, setFrage] = useState("");
  const [treffer, setTreffer] = useState<{ path: string; snippets: string[] }[] | null>(null);
  const [verlauf, setVerlauf] = useState<{ version: string; wer: string; wann: string }[] | null>(null);
  const [alt, setAlt] = useState<{ version: string; inhalt: string } | null>(null);
  const [baut, setBaut] = useState(false);

  const verlaufOeffnen = async () => {
    setAlt(null);
    try {
      setVerlauf((await api.projektWissenVerlauf(projektId, pfad)).versionen);
    } catch (e) {
      toastRef.current.error("Verlauf nicht geladen", apiFehlertext(e));
    }
  };

  const versionZeigen = async (version: string) => {
    try {
      setAlt({ version, inhalt: (await api.projektWissenVersion(projektId, pfad, version)).inhalt });
    } catch (e) {
      toastRef.current.error("Version nicht geladen", apiFehlertext(e));
    }
  };

  const zurueckholen = async () => {
    if (!alt) return;
    try {
      const r = await api.projektWissenWiederherstellen(projektId, pfad, alt.version);
      if (r?.hinweis) toastRef.current.warning("Zurückgeholt, aber gekürzt im Auftrag", r.hinweis);
      else toastRef.current.success("Zurückgeholt", `${pfad} — als neue Version gespeichert`);
      setVerlauf(null);
      setAlt(null);
      await oeffnen(pfad);
    } catch (e) {
      toastRef.current.error("Nicht zurückgeholt", apiFehlertext(e));
    }
  };

  const liste = useCallback(async () => {
    try {
      const r = await api.projektWissenListe(projektId);
      setSeiten(r.dateien.filter((d) => d.type === "file" && /\.(md|markdown|txt)$/i.test(d.path)).map((d) => d.path));
    } catch (e) {
      toastRef.current.error("Wissen nicht geladen", apiFehlertext(e));
    }
  }, [projektId]);

  const oeffnen = useCallback(async (p: string) => {
    setPfad(p);
    setBearbeiten(false);
    setInhalt(null);
    try {
      const r = await api.projektWissenLesen(projektId, p);
      setInhalt(r.inhalt);
    } catch (e) {
      setInhalt("");
      toastRef.current.error("Seite nicht geladen", apiFehlertext(e));
    }
  }, [projektId]);

  useEffect(() => {
    liste();
    oeffnen("index.md");
  }, [liste, oeffnen]);

  const speichern = async () => {
    setSpeichert(true);
    try {
      const r = await api.projektWissenSchreiben(projektId, pfad, entwurf);
      setInhalt(entwurf);
      setBearbeiten(false);
      // r.hinweis steht da, wenn die Seite nicht mehr vollstaendig in einen
      // Projektauftrag eingeht (Issue #979) — gespeichert ist sie trotzdem.
      if (r?.hinweis) toast.warning("Gespeichert, aber gekürzt im Auftrag", r.hinweis);
      else toast.success("Gespeichert", pfad);
      liste();
    } catch (e) {
      toast.error("Nicht gespeichert", apiFehlertext(e));
    } finally {
      setSpeichert(false);
    }
  };

  const neueSeite = () => {
    const name = window.prompt("Name der neuen Seite (z. B. entscheidungen.md)")?.trim();
    if (!name) return;
    const datei = /\.(md|markdown|txt)$/i.test(name) ? name : `${name}.md`;
    setPfad(datei);
    setInhalt("");
    setEntwurf(`# ${datei.replace(/\.(md|markdown|txt)$/i, "")}\n\n`);
    setBearbeiten(true);
  };

  return (
    <div className="grid gap-4 lg:grid-cols-[16rem_minmax(0,1fr)]">
      <aside className="space-y-3">
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            if (!frage.trim()) return setTreffer(null);
            try {
              setTreffer((await api.projektWissenSuchen(projektId, frage.trim())).treffer);
            } catch (err) {
              toast.error("Suche fehlgeschlagen", apiFehlertext(err));
            }
          }}
          className="flex items-center gap-1.5 rounded-lg border border-foreground/[0.1] bg-background/80 px-2"
        >
          <Search className="h-3.5 w-3.5 text-muted-foreground" />
          <input value={frage} onChange={(e) => setFrage(e.target.value)} placeholder="Im Projektwissen suchen"
            className="min-w-0 flex-1 bg-transparent py-1.5 text-sm outline-none" />
          {treffer && (
            <button type="button" onClick={() => { setTreffer(null); setFrage(""); }} aria-label="Suche zurücksetzen">
              <X className="h-3.5 w-3.5 text-muted-foreground" />
            </button>
          )}
        </form>
        <ul className="space-y-0.5">
          {(treffer ? treffer.map((t) => t.path) : seiten).map((s) => (
            <li key={s}>
              <button type="button" onClick={() => oeffnen(s)}
                className={cn("flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-foreground/[0.04]",
                  s === pfad && "bg-primary/[0.08] font-medium")}>
                <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                <span className="truncate">{s}</span>
              </button>
              {treffer && (
                <p className="line-clamp-2 px-7 text-[11px] text-muted-foreground">
                  {treffer.find((t) => t.path === s)?.snippets.join(" … ")}
                </p>
              )}
            </li>
          ))}
          {treffer && treffer.length === 0 && <li className="px-2 text-xs text-muted-foreground">Nichts gefunden.</li>}
        </ul>
        {darfSchreiben && onAufbauen && (
          <button type="button" disabled={baut} onClick={async () => { setBaut(true); try { await onAufbauen(); } finally { setBaut(false); } }}
            title="Der Lead liest Repo und Beschreibung und schreibt index.md, architektur.md und glossar.md"
            className="flex w-full items-center gap-1.5 rounded-lg border border-dashed border-foreground/[0.15] px-2 py-1.5 text-xs text-muted-foreground hover:bg-foreground/[0.04] hover:text-foreground disabled:opacity-50">
            {baut ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />} Wissen aufbauen lassen
          </button>
        )}
        {darfSchreiben && (
          <button type="button" onClick={neueSeite}
            className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-muted-foreground hover:bg-foreground/[0.04] hover:text-foreground">
            <Plus className="h-3.5 w-3.5" /> Neue Seite
          </button>
        )}
      </aside>

      <section className="min-w-0 rounded-2xl border border-foreground/[0.08] bg-card p-4">
        <div className="mb-3 flex items-center justify-between gap-2">
          <h3 className="truncate text-sm font-semibold">{pfad}</h3>
          {!bearbeiten && inhalt !== null && (
            <button type="button" onClick={verlaufOeffnen}
              className="ml-auto inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-muted-foreground hover:bg-foreground/[0.04] hover:text-foreground">
              <History className="h-3 w-3" /> Verlauf
            </button>
          )}
          {darfSchreiben && !bearbeiten && inhalt !== null && (
            <button type="button" onClick={() => { setEntwurf(inhalt); setBearbeiten(true); }}
              className="inline-flex items-center gap-1 rounded-lg border border-foreground/[0.1] px-2.5 py-1 text-xs hover:bg-foreground/[0.04]">
              <Pencil className="h-3 w-3" /> Bearbeiten
            </button>
          )}
          {bearbeiten && (
            <div className="flex gap-1.5">
              <button type="button" onClick={() => setBearbeiten(false)} className="rounded-lg px-2.5 py-1 text-xs text-muted-foreground hover:bg-foreground/[0.04]">
                Abbrechen
              </button>
              <button type="button" onClick={speichern} disabled={speichert}
                className="inline-flex items-center gap-1 rounded-lg bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground disabled:opacity-50">
                {speichert ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />} Speichern
              </button>
            </div>
          )}
        </div>
        {pfad === "index.md" && (
          <p className="mb-3 rounded-lg bg-foreground/[0.03] px-3 py-2 text-xs text-muted-foreground">
            Diese Seite bekommt jeder Agent bei jedem Projektauftrag mit — aber nur ihre ersten{" "}
            {AUSZUG_ZEICHEN} Zeichen. Das Wichtigste zuerst, Ausführliches in eigene Seiten.
          </p>
        )}
        {bearbeiten ? (
          <textarea value={entwurf} onChange={(e) => setEntwurf(e.target.value)} rows={22}
            className="w-full resize-y rounded-lg border border-foreground/[0.1] bg-background/80 px-3 py-2 font-mono text-[13px] outline-none focus:border-primary/50" />
        ) : inhalt === null ? (
          <div className="flex justify-center py-10 text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /></div>
        ) : inhalt ? (
          <div className="text-sm"><MarkdownContent content={inhalt} /></div>
        ) : (
          <p className="text-sm text-muted-foreground">Die Seite ist leer.</p>
        )}
      </section>
      <Fenster offen={verlauf !== null} schliessen={() => { setVerlauf(null); setAlt(null); }} titel={`Verlauf: ${pfad}`} breite="max-w-3xl">
        <div className="grid gap-3 p-4 sm:grid-cols-[14rem_minmax(0,1fr)]">
          <ul className="max-h-[60vh] space-y-1 overflow-y-auto">
            {(verlauf ?? []).length === 0 && <li className="text-xs text-muted-foreground">Noch keine Versionen.</li>}
            {(verlauf ?? []).map((v) => (
              <li key={v.version}>
                <button type="button" onClick={() => versionZeigen(v.version)}
                  className={cn("w-full rounded-lg px-2 py-1.5 text-left text-xs hover:bg-foreground/[0.04]", alt?.version === v.version && "bg-primary/[0.08]")}>
                  <span className="block font-medium">{new Date(v.wann).toLocaleString("de-DE")}</span>
                  <span className="text-muted-foreground">{v.wer}</span>
                </button>
              </li>
            ))}
          </ul>
          <div className="min-w-0">
            {alt ? (
              <>
                <div className="max-h-[52vh] overflow-y-auto rounded-lg bg-foreground/[0.03] p-3 text-sm"><MarkdownContent content={alt.inhalt} /></div>
                {darfSchreiben && (
                  <button type="button" onClick={zurueckholen}
                    className="mt-2 inline-flex items-center gap-1 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90">
                    <RotateCcw className="h-3 w-3" /> Diese Version zurückholen
                  </button>
                )}
              </>
            ) : (
              <p className="text-xs text-muted-foreground">Eine Version links wählen, um sie anzusehen.</p>
            )}
          </div>
        </div>
      </Fenster>
    </div>
  );
}
