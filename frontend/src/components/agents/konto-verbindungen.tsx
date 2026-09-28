"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Cloud, ExternalLink, Loader2, Mail, Plug, RefreshCw, Smartphone, Unplug } from "lucide-react";
import { Github } from "@/components/icons/github";
import * as api from "@/lib/api";
import type { Integration } from "@/lib/types";
import { useConfirm } from "@/components/ui/dialog-provider";
import { cn } from "@/lib/utils";

const ICONS: Record<string, typeof Mail> = { Mail, Cloud, Smartphone, Github };

/** Die eigenen Konten verbinden (Microsoft 365 u. a.) — direkt im Konnektoren-Fenster.
 *
 * Angeboten wird nur, was der Admin eingerichtet hat (``available``; bei
 * Microsoft heisst das: die Entra-App ist hinterlegt) oder schon verbunden ist.
 * Verbinden laeuft ueber denselben OAuth-Weg wie die Integrationsseite; danach
 * fuehrt der Login zurueck in den Chat dieses Agenten statt auf jene Seite.
 */
export function KontoVerbindungen({ agentId }: { agentId: string }) {
  const [konten, setKonten] = useState<Integration[]>([]);
  const [laedt, setLaedt] = useState(true);
  const [beschaeftigt, setBeschaeftigt] = useState<string | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);
  const confirm = useConfirm();

  const laden = useCallback(async () => {
    try {
      const { integrations } = await api.getIntegrations();
      // Claude-Login (anthropic) und Token-Zugänge (pat) gehören nicht hierher.
      setKonten(integrations.filter((i) => i.auth_type === "oauth" && i.provider !== "anthropic" && (i.available || i.connected)));
    } catch {
      setKonten([]);
    } finally {
      setLaedt(false);
    }
  }, []);
  useEffect(() => { laden(); }, [laden]);

  const verbinden = async (provider: string) => {
    setBeschaeftigt(provider);
    setFehler(null);
    try {
      const { auth_url } = await api.getAuthUrl(provider, `/agents/${agentId}?konnektoren=1`);
      window.location.href = auth_url;
    } catch (e) {
      setFehler(e instanceof Error ? e.message : "Verbinden fehlgeschlagen");
      setBeschaeftigt(null);
    }
  };

  const trennen = async (k: Integration) => {
    const ok = await confirm({
      title: `${k.display_name} trennen?`,
      message: "Deine Agenten verlieren den Zugriff auf dieses Konto.",
      variant: "destructive",
      confirmLabel: "Trennen",
    });
    if (!ok) return;
    setBeschaeftigt(k.provider);
    try {
      await api.disconnectIntegration(k.provider);
      await laden();
    } finally {
      setBeschaeftigt(null);
    }
  };

  if (laedt) return null;
  if (konten.length === 0) return null;

  return (
    <div className="rounded-xl border border-foreground/[0.06] bg-card/80 overflow-hidden">
      <div className="flex items-center gap-2.5 border-b border-foreground/[0.06] px-5 py-3.5">
        <div className="rounded-lg bg-primary/10 p-1.5">
          <Plug className="h-4 w-4 text-primary" />
        </div>
        <div>
          <h3 className="text-sm font-medium">Deine Konten</h3>
          <p className="mt-0.5 text-[11px] text-muted-foreground/60">Verbinde sie einmal — danach kannst du sie deinen Agenten freigeben.</p>
        </div>
      </div>
      <div className="divide-y divide-foreground/[0.04]">
        {konten.map((k) => {
          const Icon = ICONS[k.icon] || Plug;
          const neuNoetig = k.connected && (k.status === "expired" || k.status === "refresh_failing");
          const gesund = k.connected && !neuNoetig;
          const laeuft = beschaeftigt === k.provider;
          return (
            <div key={k.provider} className="flex items-center gap-3 px-5 py-3">
              <Icon className={cn("h-4 w-4 shrink-0", gesund ? "text-emerald-500" : neuNoetig ? "text-amber-500" : "text-muted-foreground")} />
              <div className="min-w-0 flex-1">
                <p className="text-[13px] font-medium">{k.display_name}</p>
                <p className="truncate text-[11px] text-muted-foreground/70">
                  {gesund
                    ? `Verbunden${k.account_label ? ` als ${k.account_label}` : ""}`
                    : neuNoetig
                      ? "Anmeldung abgelaufen — bitte neu verbinden"
                      : "Nicht verbunden"}
                </p>
              </div>
              {gesund && <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />}
              {neuNoetig && <AlertTriangle className="h-4 w-4 shrink-0 text-amber-500" />}
              {k.connected && !neuNoetig ? (
                <button
                  onClick={() => trennen(k)}
                  disabled={laeuft}
                  className="flex items-center gap-1 rounded-lg px-2.5 py-1 text-[11px] text-muted-foreground hover:bg-red-500/10 hover:text-red-400 disabled:opacity-40"
                >
                  {laeuft ? <Loader2 className="h-3 w-3 animate-spin" /> : <Unplug className="h-3 w-3" />}
                  Trennen
                </button>
              ) : (
                <button
                  onClick={() => verbinden(k.provider)}
                  disabled={laeuft}
                  className="flex items-center gap-1 rounded-lg bg-primary px-2.5 py-1 text-[11px] font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
                >
                  {laeuft ? <Loader2 className="h-3 w-3 animate-spin" /> : neuNoetig ? <RefreshCw className="h-3 w-3" /> : <ExternalLink className="h-3 w-3" />}
                  {neuNoetig ? "Neu verbinden" : "Verbinden"}
                </button>
              )}
            </div>
          );
        })}
      </div>
      {fehler && <p className="px-5 pb-3 text-[11px] text-red-400">{fehler}</p>}
    </div>
  );
}
