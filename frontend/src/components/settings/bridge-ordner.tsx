"use client";

/**
 * „Meine Bridge-Ordner“ (#1092): einen in der Bridge freigegebenen Ordner (z. B.
 * einen Obsidian-Vault) als Second Brain auf den Server spiegeln. Nur in diese
 * Richtung, nur für dich — und nutzbar auch, wenn der Rechner aus ist.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Brain, FolderSync, Loader2, Plus, RefreshCw, Trash2, X } from "lucide-react";
import * as api from "@/lib/api";
import type { SecondBrain } from "@/lib/types";
import { apiFehlertext } from "@/lib/api-fehler";
import { useConfirm, useToast } from "@/components/ui/dialog-provider";

function stand(b: SecondBrain): string {
  if (!b.sync_stand) return "Noch nicht abgeglichen — passiert, sobald die Bridge verbunden ist.";
  return `Zuletzt abgeglichen: ${new Date(b.sync_stand).toLocaleString("de-DE")}`;
}

export function BridgeOrdner() {
  const toast = useToast();
  const confirm = useConfirm();
  const toastRef = useRef(toast);
  toastRef.current = toast;
  const [brains, setBrains] = useState<SecondBrain[] | null>(null);
  const [neu, setNeu] = useState<{ ordner: string[]; wahl: string; name: string } | null>(null);
  const [laedt, setLaedt] = useState(false);
  const [beschaeftigt, setBeschaeftigt] = useState<number | null>(null);

  const laden = useCallback(async () => {
    try {
      setBrains((await api.listSecondBrains()).filter((b) => b.bridge_ordner));
    } catch (e) {
      setBrains([]);
      toastRef.current.error("Gespiegelte Ordner nicht geladen", apiFehlertext(e));
    }
  }, []);

  useEffect(() => {
    laden();
  }, [laden]);

  const ordnerHolen = async () => {
    setLaedt(true);
    try {
      const { ordner } = await api.bridgeOrdner();
      if (!ordner.length) {
        toast.info("Kein Ordner freigegeben", "In der Bridge unter Berechtigungen → Ordner-Zugriff einen Ordner hinzufügen.");
        return;
      }
      const vergeben = new Set((brains ?? []).map((b) => b.bridge_ordner));
      const frei = ordner.filter((o) => !vergeben.has(o));
      if (!frei.length) {
        toast.info("Alle freigegebenen Ordner werden schon gespiegelt");
        return;
      }
      setNeu({ ordner: frei, wahl: frei[0], name: frei[0].split(/[\\/]/).filter(Boolean).pop() ?? "" });
    } catch (e) {
      toast.error("Bridge nicht erreichbar", apiFehlertext(e));
    } finally {
      setLaedt(false);
    }
  };

  const anlegen = async () => {
    if (!neu || !neu.name.trim()) return;
    setLaedt(true);
    try {
      await api.bridgeBrainAnlegen(neu.name.trim(), neu.wahl);
      toast.success("Ordner wird gespiegelt", "Der erste Abgleich läuft — je nach Größe ein paar Minuten.");
      setNeu(null);
      await laden();
    } catch (e) {
      toast.error("Nicht angelegt", apiFehlertext(e));
    } finally {
      setLaedt(false);
    }
  };

  const abgleichen = async (b: SecondBrain) => {
    setBeschaeftigt(b.id);
    try {
      await api.bridgeBrainSync(b.id);
      toast.success("Abgleich gestartet", b.name);
      // Kurz warten, dann den neuen Stand zeigen — kleine Ordner sind bis dahin durch.
      setTimeout(() => laden(), 4000);
    } catch (e) {
      toast.error("Nicht gestartet", apiFehlertext(e));
    } finally {
      setBeschaeftigt(null);
    }
  };

  const loeschen = async (b: SecondBrain) => {
    const ok = await confirm({
      title: "Spiegel löschen?",
      message: `„${b.name}“ verschwindet vom Server — samt Anbindung an Projekte. Der Ordner auf deinem Rechner bleibt unberührt.`,
      variant: "destructive",
      confirmLabel: "Löschen",
    });
    if (!ok) return;
    setBeschaeftigt(b.id);
    try {
      await api.bridgeBrainLoeschen(b.id);
      toast.success("Gelöscht", b.name);
      await laden();
    } catch (e) {
      toast.error("Nicht gelöscht", apiFehlertext(e));
    } finally {
      setBeschaeftigt(null);
    }
  };

  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-foreground/[0.06] bg-card/80 p-5 backdrop-blur-sm">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <p className="min-w-0 flex-1 text-sm text-muted-foreground">
            Einen in der Bridge freigegebenen Ordner (z. B. deinen Obsidian-Vault) als Second Brain auf den Server
            spiegeln. Markdown und Text kommen so, wie sie sind, PDF, Word und Excel als ausgelesener Text. Nur du
            siehst ihn; du kannst ihn an Projekte anbinden, und er bleibt nutzbar, wenn dein Rechner aus ist.
          </p>
          {!neu && (
            <button type="button" onClick={ordnerHolen} disabled={laedt}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-50">
              {laedt ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />} Ordner spiegeln
            </button>
          )}
        </div>
        {neu && (
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1 block text-[11px] font-medium text-muted-foreground/70">Freigegebener Ordner</span>
              <select value={neu.wahl} onChange={(e) => setNeu({ ...neu, wahl: e.target.value })}
                className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-2 text-sm outline-none focus:border-primary/50">
                {neu.ordner.map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
            </label>
            <label className="block">
              <span className="mb-1 block text-[11px] font-medium text-muted-foreground/70">Name des Brains</span>
              <input value={neu.name} maxLength={200} onChange={(e) => setNeu({ ...neu, name: e.target.value })}
                className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-2 text-sm outline-none focus:border-primary/50" />
            </label>
            <div className="flex justify-end gap-2 sm:col-span-2">
              <button type="button" onClick={() => setNeu(null)}
                className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm text-muted-foreground hover:bg-foreground/[0.04]">
                <X className="h-4 w-4" /> Abbrechen
              </button>
              <button type="button" onClick={anlegen} disabled={laedt || !neu.name.trim()}
                className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-50">
                {laedt ? <Loader2 className="h-4 w-4 animate-spin" /> : <FolderSync className="h-4 w-4" />} Spiegeln
              </button>
            </div>
          </div>
        )}
      </div>

      {brains === null ? (
        <div className="flex justify-center py-6 text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /></div>
      ) : brains.length > 0 && (
        <ul className="space-y-2">
          {brains.map((b) => (
            <li key={b.id} className="flex items-start gap-3 rounded-xl border border-foreground/[0.06] bg-card/80 px-4 py-3 backdrop-blur-sm">
              <Brain className="mt-0.5 h-4 w-4 shrink-0 text-violet-500" />
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">{b.name}</div>
                <div className="truncate text-[11px] text-muted-foreground" title={b.bridge_ordner ?? undefined}>{b.bridge_ordner}</div>
                <div className="mt-0.5 text-[11px] text-muted-foreground/80">{stand(b)}</div>
                {b.sync_fehler && (
                  <div className="mt-1 flex items-start gap-1.5 text-[11px] text-amber-700 dark:text-amber-400">
                    <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0" /> {b.sync_fehler}
                  </div>
                )}
              </div>
              <button type="button" onClick={() => abgleichen(b)} disabled={beschaeftigt === b.id}
                title="Jetzt abgleichen" aria-label={`${b.name} jetzt abgleichen`}
                className="rounded-lg p-2 text-muted-foreground hover:bg-foreground/[0.06] hover:text-primary disabled:opacity-50">
                {beschaeftigt === b.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              </button>
              <button type="button" onClick={() => loeschen(b)} disabled={beschaeftigt === b.id}
                title="Spiegel löschen" aria-label={`${b.name} löschen`}
                className="rounded-lg p-2 text-muted-foreground hover:bg-red-500/[0.06] hover:text-red-500 disabled:opacity-50">
                <Trash2 className="h-4 w-4" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
