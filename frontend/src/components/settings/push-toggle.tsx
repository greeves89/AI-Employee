"use client";

import { useCallback, useEffect, useState } from "react";
import { Bell, BellOff, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  getPushState,
  subscribeToPush,
  unsubscribeFromPush,
  type PushState,
} from "@/lib/webpush";
import { useToast } from "@/components/ui/dialog-provider";

/**
 * Browser-Meldungen an-/abschalten.
 *
 * Der Nutzen ist konkret: Wartet ein Agent auf eine Freigabe, steht er bis zur
 * Antwort still. Ohne Push sieht man das erst beim nächsten Blick in die Oberfläche —
 * bei einem nächtlichen Lauf also am nächsten Morgen.
 */
export function PushToggle() {
  const [state, setState] = useState<PushState | null>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  const refresh = useCallback(async () => {
    setState(await getPushState());
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const toggle = async () => {
    setBusy(true);
    try {
      const next = state === "subscribed" ? await unsubscribeFromPush() : await subscribeToPush();
      setState(next);
      if (next === "subscribed") toast.success("Browser-Meldungen aktiv");
      else if (next === "denied")
        toast.error(
          "Vom Browser blockiert",
          "Die Erlaubnis wurde abgelehnt. Das lässt sich nur in den Browser-Einstellungen dieser Seite zurücknehmen."
        );
      else toast.info("Browser-Meldungen aus");
    } catch (e) {
      toast.error("Fehlgeschlagen", e instanceof Error ? e.message : undefined);
    } finally {
      setBusy(false);
    }
  };

  if (state === null) return null;

  const active = state === "subscribed";
  const blocked = state === "denied";
  const unsupported = state === "unsupported";

  return (
    <div className="flex items-center justify-between gap-3">
      <div>
        <div className="flex items-center gap-2 text-sm font-medium">
          {active ? <Bell className="h-4 w-4 text-primary" /> : <BellOff className="h-4 w-4 text-muted-foreground/60" />}
          Browser-Meldungen
        </div>
        <p className="mt-0.5 text-[11px] text-muted-foreground/60">
          {unsupported
            ? "Dieser Browser unterstützt keine Push-Meldungen. Auf dem iPhone muss die Seite dafür zum Home-Bildschirm hinzugefügt werden."
            : blocked
              ? "Vom Browser blockiert — in den Seiteneinstellungen wieder erlauben."
              : "Freigabe-Anfragen und fertige Aufgaben erreichen dich auch, wenn die Seite geschlossen ist."}
        </p>
      </div>
      <button
        onClick={toggle}
        disabled={busy || unsupported || blocked}
        className={cn(
          "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors",
          active ? "bg-emerald-500" : "bg-foreground/[0.1]",
          (busy || unsupported || blocked) && "cursor-not-allowed opacity-40"
        )}
      >
        {busy ? (
          <Loader2 className="mx-auto h-3 w-3 animate-spin text-foreground" />
        ) : (
          <span
            className={cn(
              "inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform",
              active ? "translate-x-6" : "translate-x-1"
            )}
          />
        )}
      </button>
    </div>
  );
}
