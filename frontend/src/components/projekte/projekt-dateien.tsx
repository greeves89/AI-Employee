"use client";

/**
 * Dateien am Projekt (Wissen → Dateien): hochladen per Knopf oder Ziehen,
 * herunterladen, löschen. Der ausgelesene Text steht als Seite im Projektwissen
 * und ist damit durchsuchbar — „Text“ öffnet ihn.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Download, FileText, Loader2, Paperclip, Trash2, Upload } from "lucide-react";
import * as api from "@/lib/api";
import { useConfirm, useToast } from "@/components/ui/dialog-provider";
import { apiFehlertext } from "@/lib/api-fehler";
import { cn } from "@/lib/utils";

function groesse(n: number): string {
  return n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;
}

export function ProjektDateien({
  projektId, darfSchreiben, onTextOeffnen, onGeaendert,
}: {
  projektId: string;
  darfSchreiben: boolean;
  /** Ausgelesenen Text einer Datei als Seite öffnen. */
  onTextOeffnen: (seite: string) => void;
  /** Nach Hochladen/Löschen: Seitenliste neu laden. */
  onGeaendert: () => void;
}) {
  const toast = useToast();
  const confirm = useConfirm();
  const toastRef = useRef(toast);
  toastRef.current = toast;
  const eingabe = useRef<HTMLInputElement>(null);
  const [dateien, setDateien] = useState<api.ProjektDatei[]>([]);
  const [endungen, setEndungen] = useState<string[]>([]);
  const [laedt, setLaedt] = useState(false);
  const [ziehen, setZiehen] = useState(false);

  const laden = useCallback(async () => {
    try {
      const r = await api.projektDateien(projektId);
      setDateien(r.dateien);
      setEndungen(r.endungen);
    } catch (e) {
      toastRef.current.error("Dateien nicht geladen", apiFehlertext(e));
    }
  }, [projektId]);

  useEffect(() => {
    laden();
  }, [laden]);

  const hochladen = async (liste: FileList | File[]) => {
    const alle = Array.from(liste);
    if (!alle.length) return;
    setLaedt(true);
    try {
      for (const datei of alle) {
        try {
          const r = await api.projektDateiHochladen(projektId, datei);
          if (r.text_ausgelesen) toast.success("Hochgeladen", `${r.name} — Text ist im Projektwissen durchsuchbar`);
          else toast.info("Hochgeladen", `${r.name} — ohne auslesbaren Text (z. B. ein Bild)`);
        } catch (e) {
          toast.error(`${datei.name} nicht hochgeladen`, apiFehlertext(e));
        }
      }
    } finally {
      setLaedt(false);
      if (eingabe.current) eingabe.current.value = "";
      await laden();
      onGeaendert();
    }
  };

  const loeschen = async (d: api.ProjektDatei) => {
    const ok = await confirm({
      title: "Datei löschen?",
      message: `„${d.name}“ und ihr ausgelesener Text verschwinden aus dem Projekt.`,
      variant: "destructive",
      confirmLabel: "Löschen",
    });
    if (!ok) return;
    try {
      await api.projektDateiLoeschen(projektId, d.name);
      toast.success("Gelöscht", d.name);
      await laden();
      onGeaendert();
    } catch (e) {
      toast.error("Nicht gelöscht", apiFehlertext(e));
    }
  };

  return (
    <div
      onDragOver={(e) => { if (darfSchreiben) { e.preventDefault(); setZiehen(true); } }}
      onDragLeave={() => setZiehen(false)}
      onDrop={(e) => { e.preventDefault(); setZiehen(false); if (darfSchreiben) hochladen(e.dataTransfer.files); }}
      className={cn("space-y-1.5 rounded-lg border border-dashed border-transparent p-1",
        ziehen && "border-primary/60 bg-primary/[0.05]")}
    >
      <div className="flex items-center justify-between px-1">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Dateien</span>
        {darfSchreiben && (
          <button type="button" onClick={() => eingabe.current?.click()} disabled={laedt}
            title={endungen.length ? `Erlaubt: ${endungen.join(", ")}` : undefined}
            className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-foreground/[0.04] hover:text-foreground disabled:opacity-50">
            {laedt ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />} Hochladen
          </button>
        )}
        <input ref={eingabe} type="file" multiple hidden accept={endungen.join(",")}
          onChange={(e) => e.target.files && hochladen(e.target.files)} />
      </div>
      {dateien.length === 0 ? (
        <p className="px-1 text-[11px] text-muted-foreground/70">
          {darfSchreiben ? "PDF, Word, Excel oder Text hierher ziehen — der Inhalt wird durchsuchbar." : "Keine Dateien."}
        </p>
      ) : (
        <ul className="space-y-0.5">
          {dateien.map((d) => (
            <li key={d.name} className="group flex items-center gap-1.5 rounded-lg px-1.5 py-1 text-sm hover:bg-foreground/[0.04]">
              <Paperclip className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate" title={d.name}>{d.name}</span>
              <span className="shrink-0 text-[10px] text-muted-foreground tabular-nums">{groesse(d.bytes)}</span>
              <button type="button" onClick={() => onTextOeffnen(d.seite)} title="Ausgelesenen Text ansehen" aria-label={`Text von ${d.name} ansehen`}
                className="rounded p-0.5 text-muted-foreground hover:text-foreground">
                <FileText className="h-3.5 w-3.5" />
              </button>
              <button type="button" title="Herunterladen" aria-label={`${d.name} herunterladen`}
                onClick={() => api.projektDateiHerunterladen(projektId, d.name).catch((e) => toast.error("Nicht heruntergeladen", apiFehlertext(e)))}
                className="rounded p-0.5 text-muted-foreground hover:text-foreground">
                <Download className="h-3.5 w-3.5" />
              </button>
              {darfSchreiben && (
                <button type="button" onClick={() => loeschen(d)} title="Löschen" aria-label={`${d.name} löschen`}
                  className="rounded p-0.5 text-muted-foreground hover:text-red-500">
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
