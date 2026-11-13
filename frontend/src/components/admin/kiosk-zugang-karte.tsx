"use client";

/** Karte „Kiosk-Anzeige“ unter Admin → Betrieb (core/kiosk_zugang).
 *
 * Der Kiosk zeigt Status und lässt mit Agenten chatten, ohne Anmeldung einer Person.
 * Damit nicht jeder im Netz (oder ein Agent) ihn nutzen kann, braucht jedes Gerät
 * einen Zugang: hier einen Link erzeugen und ihn einmal auf dem Kiosk-Gerät öffnen.
 * Der Link steht nur in dieser Antwort; gespeichert wird ein Prüfwert.
 */

import { useCallback, useEffect, useState } from "react";
import { Copy, Loader2, MonitorSmartphone, Trash2 } from "lucide-react";
import * as api from "@/lib/api";
import { apiFehlertext } from "@/lib/api-fehler";
import { useToast } from "@/components/ui/dialog-provider";

export function KioskZugangKarte() {
  const toast = useToast();
  const [stand, setStand] = useState<api.KioskZugangStand | null>(null);
  const [link, setLink] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const laden = useCallback(async () => {
    try { setStand(await api.getKioskZugang()); } catch { setStand(null); }
  }, []);
  useEffect(() => { laden(); }, [laden]);

  if (!stand || !stand.kiosk_eingeschaltet) return null;

  const erzeugen = async () => {
    setBusy(true);
    try {
      const r = await api.erzeugeKioskZugang();
      setLink(`${window.location.origin}${r.link}`);
      await laden();
    } catch (e) {
      toast.error("Link nicht erzeugt", apiFehlertext(e, "Unbekannter Fehler"));
    } finally { setBusy(false); }
  };

  const entfernen = async () => {
    setBusy(true);
    try { await api.entferneKioskZugang(); setLink(null); await laden(); toast.success("Kiosk-Zugang entfernt"); }
    catch (e) { toast.error("Nicht entfernt", apiFehlertext(e, "Unbekannter Fehler")); }
    finally { setBusy(false); }
  };

  return (
    <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5 space-y-3 mb-4">
      <div className="flex items-start gap-2">
        <MonitorSmartphone className="h-4 w-4 mt-0.5 text-muted-foreground" />
        <div>
          <div className="text-sm font-medium">Kiosk-Anzeige</div>
          <div className="text-[11px] text-muted-foreground/60">
            Der Kiosk zeigt Status und lässt mit Agenten chatten, ohne Anmeldung. Er antwortet nur Geräten
            mit Zugang. Link erzeugen und einmal auf dem Kiosk-Gerät öffnen. Ein neuer Link macht den alten ungültig.
          </div>
        </div>
      </div>
      <div className="text-xs">{stand.eingerichtet ? "Zugang eingerichtet." : "Noch kein Zugang: Der Kiosk ist gesperrt."}</div>
      {link && (
        <div className="flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[12px]">
          <code className="truncate flex-1">{link}</code>
          <button type="button" aria-label="Link kopieren" onClick={() => { navigator.clipboard?.writeText(link); toast.success("Kopiert"); }}>
            <Copy className="h-3.5 w-3.5" />
          </button>
        </div>
      )}
      {link && <div className="text-[11px] text-muted-foreground/60">Der Link wird nur jetzt angezeigt.</div>}
      <div className="flex gap-2">
        <button type="button" disabled={busy} onClick={erzeugen}
          className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-40">
          {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          {stand.eingerichtet ? "Neuen Link erzeugen" : "Link erzeugen"}
        </button>
        {stand.eingerichtet && (
          <button type="button" disabled={busy} onClick={entfernen}
            className="inline-flex items-center gap-1.5 rounded-lg border border-foreground/10 px-3 py-1.5 text-xs disabled:opacity-40">
            <Trash2 className="h-3.5 w-3.5" /> Zugang entfernen
          </button>
        )}
      </div>
    </div>
  );
}
