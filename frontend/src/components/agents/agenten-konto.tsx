"use client";

import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { AlertTriangle, BellRing, CheckCircle2, ExternalLink, Loader2, RefreshCw, Unplug, UserRound } from "lucide-react";
import * as api from "@/lib/api";
import { useConfirm } from "@/components/ui/dialog-provider";
import { cn } from "@/lib/utils";

/** Eigenes Microsoft-365-Konto des Agenten (core/agenten_konto).
 *
 * Hat der Agent ein eigenes, lizenziertes Konto, liest und sendet er als er selbst —
 * nicht mehr über das Postfach seines Besitzers. Verbunden wird einmal mit den
 * Zugangsdaten des Agenten-Nutzers. Microsoft fragt dabei immer nach dem Konto; wer
 * versehentlich sein eigenes wählt, bekommt hier die Ablehnung zu sehen.
 */
export function AgentenKonto({ agentId }: { agentId: string }) {
  const [stand, setStand] = useState<api.AgentenKontoStand | null>(null);
  const [laeuft, setLaeuft] = useState(false);
  const [fehler, setFehler] = useState<string | null>(null);
  const confirm = useConfirm();
  const suchParameter = useSearchParams();

  const laden = useCallback(async () => {
    try {
      setStand(await api.getAgentMicrosoftKonto(agentId));
    } catch {
      setStand(null);
    }
  }, [agentId]);
  useEffect(() => { laden(); }, [laden]);

  // Zurück vom Microsoft-Login mit Ablehnung (z. B. das eigene Konto gewählt).
  useEffect(() => {
    const f = suchParameter.get("error");
    if (f && suchParameter.get("provider") === "microsoft") {
      setFehler(f === "access_denied" || f === "missing_code" ? "Anmeldung bei Microsoft abgebrochen." : f);
    }
  }, [suchParameter]);

  const verbinden = async () => {
    setLaeuft(true);
    setFehler(null);
    try {
      const { auth_url } = await api.verbindeAgentMicrosoftKonto(agentId);
      window.location.href = auth_url;
    } catch (e) {
      setFehler(e instanceof Error ? e.message : "Verbinden fehlgeschlagen");
      setLaeuft(false);
    }
  };

  const trennen = async () => {
    const ok = await confirm({
      title: "Konto des Agenten trennen?",
      message: "Der Agent arbeitet danach wieder mit deinem Microsoft-Konto, sofern du eines verbunden hast.",
      variant: "destructive",
      confirmLabel: "Trennen",
    });
    if (!ok) return;
    setLaeuft(true);
    try {
      setStand({ ...(stand as api.AgentenKontoStand), ...(await api.trenneAgentMicrosoftKonto(agentId)) });
    } catch (e) {
      setFehler(e instanceof Error ? e.message : "Trennen fehlgeschlagen");
    } finally {
      setLaeuft(false);
    }
  };

  const weckenSetzen = async (patch: { aktiv?: boolean; alle_absender?: boolean }) => {
    setLaeuft(true);
    setFehler(null);
    try {
      setStand({ ...(stand as api.AgentenKontoStand), ...(await api.setzeAgentMailWecken(agentId, patch)) });
    } catch (e) {
      setFehler(e instanceof Error ? e.message : "Speichern fehlgeschlagen");
    } finally {
      setLaeuft(false);
    }
  };

  if (!stand || (!stand.verfuegbar && !stand.verbunden)) return null;
  const neuNoetig = stand.verbunden && (stand.status === "expired" || stand.status === "refresh_failing");
  const gesund = stand.verbunden && !neuNoetig;

  return (
    <div className="rounded-xl border border-foreground/[0.06] bg-card/80 overflow-hidden">
      <div className="flex items-center gap-2.5 border-b border-foreground/[0.06] px-5 py-3.5">
        <div className="rounded-lg bg-primary/10 p-1.5">
          <UserRound className="h-4 w-4 text-primary" />
        </div>
        <div>
          <h3 className="text-sm font-medium">Microsoft-Konto des Agenten</h3>
          <p className="mt-0.5 text-[11px] text-muted-foreground/60">
            Eigenes Postfach statt deinem: Der Agent liest und sendet dann als er selbst.
          </p>
        </div>
      </div>
      <div className="flex items-center gap-3 px-5 py-3">
        <UserRound className={cn("h-4 w-4 shrink-0", gesund ? "text-emerald-500" : neuNoetig ? "text-amber-500" : "text-muted-foreground")} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-[13px] font-medium">
            {stand.verbunden ? (stand.konto || "Verbunden") : "Kein eigenes Konto"}
          </p>
          <p className="text-[11px] text-muted-foreground/70">
            {gesund
              ? "Verbunden — der Agent arbeitet mit diesem Konto"
              : neuNoetig
                ? "Anmeldung abgelaufen — bis zum Neuverbinden liest und sendet der Agent nichts"
                : "Der Agent nutzt dein Microsoft-Konto, falls verbunden"}
          </p>
        </div>
        {gesund && <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />}
        {neuNoetig && <AlertTriangle className="h-4 w-4 shrink-0 text-amber-500" />}
        {stand.darf_verbinden && (
          gesund ? (
            <button
              onClick={trennen}
              disabled={laeuft}
              className="flex items-center gap-1 rounded-lg px-2.5 py-1 text-[11px] text-muted-foreground hover:bg-red-500/10 hover:text-red-400 disabled:opacity-40"
            >
              {laeuft ? <Loader2 className="h-3 w-3 animate-spin" /> : <Unplug className="h-3 w-3" />}
              Trennen
            </button>
          ) : (
            <button
              onClick={verbinden}
              disabled={laeuft || !stand.verfuegbar}
              className="flex items-center gap-1 rounded-lg bg-primary px-2.5 py-1 text-[11px] font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
            >
              {laeuft ? <Loader2 className="h-3 w-3 animate-spin" /> : neuNoetig ? <RefreshCw className="h-3 w-3" /> : <ExternalLink className="h-3 w-3" />}
              {neuNoetig ? "Neu verbinden" : "Konto verbinden"}
            </button>
          )
        )}
      </div>
      {gesund && (
        <div className="flex items-start gap-3 border-t border-foreground/[0.06] px-5 py-3">
          <BellRing className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
          <div className="min-w-0 flex-1">
            <p className="text-[13px] font-medium">Bei neuer Mail wecken</p>
            <p className="text-[11px] text-muted-foreground/70">
              {stand.mail_wecken
                ? stand.wecken_weg === "benachrichtigung"
                  ? "An — Microsoft meldet neue Mails sofort."
                  : "An — die Anlage schaut alle fünf Minuten nach neuen Mails."
                : "Jede neue Mail im Postfach des Agenten startet einen Auftrag. Höchstens 20 je Stunde."}
            </p>
            {stand.mail_wecken && (
              <label className="mt-2 flex items-center gap-2 text-[11px] text-muted-foreground">
                <input
                  type="checkbox"
                  checked={!!stand.alle_absender}
                  disabled={laeuft || !stand.darf_verbinden}
                  onChange={(e) => weckenSetzen({ alle_absender: e.target.checked })}
                  className="h-3.5 w-3.5 rounded border-foreground/20"
                />
                Auch Absender außerhalb der eigenen Organisation
              </label>
            )}
            {stand.mail_wecken && stand.wecken_fehler && (
              <p className="mt-1 text-[11px] text-amber-600 dark:text-amber-400">{stand.wecken_fehler}</p>
            )}
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={!!stand.mail_wecken}
            aria-label="Bei neuer Mail wecken"
            disabled={laeuft || !stand.darf_verbinden}
            onClick={() => weckenSetzen({ aktiv: !stand.mail_wecken })}
            className={cn(
              "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-50",
              stand.mail_wecken ? "bg-emerald-500" : "bg-foreground/15",
            )}
          >
            <span className={cn("inline-block h-4 w-4 rounded-full bg-white transition-transform",
              stand.mail_wecken ? "translate-x-6" : "translate-x-1")} />
          </button>
        </div>
      )}
      {!stand.verbunden && stand.darf_verbinden && (
        <p className="px-5 pb-3 text-[11px] text-muted-foreground/60">
          Bei Microsoft mit dem Konto des Agenten anmelden, nicht mit deinem eigenen.
        </p>
      )}
      {fehler && <p className="px-5 pb-3 text-[11px] text-red-400">{fehler}</p>}
    </div>
  );
}
