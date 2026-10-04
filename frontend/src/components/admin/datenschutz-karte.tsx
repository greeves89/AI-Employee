"use client";

/** Karte „Datenschutz & Aufbewahrung“ unter Admin → Betrieb (#892).
 *
 * - Aufbewahrungsfristen für Prüfprotokoll und Chatverläufe in Tagen. Leer heißt
 *   unbegrenzt (Voreinstellung). Ein täglicher Aufräumlauf auf dem Server löscht,
 *   was älter ist, und schreibt dazu einen Eintrag ins Prüfprotokoll.
 * - Schalter für das tägliche Lebenszeichen an den Lizenzserver (Standard: an).
 *
 * Alles serverseitig nur für Administratoren (GET/PUT /settings/datenschutz).
 */

import { useEffect, useState } from "react";
import { Loader2, Radio, ShieldCheck } from "lucide-react";
import * as api from "@/lib/api";
import type { DatenschutzStand } from "@/lib/api";
import { apiFehlertext } from "@/lib/api-fehler";
import { useToast } from "@/components/ui/dialog-provider";
import { cn } from "@/lib/utils";

const EINGABE =
  "w-28 rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-2 text-sm font-medium tabular-nums outline-none transition-all focus:border-primary/50 focus:ring-1 focus:ring-primary/20";

function alsText(tage: number | null): string {
  return tage === null ? "" : String(tage);
}

/** Leer → null (unbegrenzt); sonst ganze Zahl. Ungültig → undefined. */
function alsTage(text: string): number | null | undefined {
  const roh = text.trim();
  if (!roh) return null;
  if (!/^\d+$/.test(roh)) return undefined;
  const zahl = Number(roh);
  return zahl === 0 ? null : zahl;
}

function zeitpunkt(iso: string): string {
  return new Date(iso).toLocaleString("de-DE", { dateStyle: "medium", timeStyle: "short" });
}

export function DatenschutzKarte() {
  const toast = useToast();
  const [stand, setStand] = useState<DatenschutzStand | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);
  const [audit, setAudit] = useState("");
  const [chat, setChat] = useState("");
  const [speichert, setSpeichert] = useState(false);

  function uebernehmen(neu: DatenschutzStand) {
    setStand(neu);
    setAudit(alsText(neu.audit_aufbewahrung_tage));
    setChat(alsText(neu.chat_aufbewahrung_tage));
  }

  useEffect(() => {
    api.getDatenschutz()
      .then(uebernehmen)
      .catch((e) => setFehler(apiFehlertext(e, "Unbekannter Fehler")));
  }, []);

  async function speichern(aenderung: Parameters<typeof api.setDatenschutz>[0], erfolg: string) {
    setSpeichert(true);
    try {
      uebernehmen(await api.setDatenschutz(aenderung));
      toast.success(erfolg);
    } catch (e) {
      toast.error("Nicht gespeichert", apiFehlertext(e));
    } finally {
      setSpeichert(false);
    }
  }

  function fristenSpeichern() {
    const auditTage = alsTage(audit);
    const chatTage = alsTage(chat);
    if (auditTage === undefined || chatTage === undefined) {
      toast.error("Nicht gespeichert", "Bitte nur ganze Tage eintragen oder das Feld leer lassen.");
      return;
    }
    speichern(
      { audit_aufbewahrung_tage: auditTage, chat_aufbewahrung_tage: chatTage },
      "Aufbewahrungsfristen gespeichert",
    );
  }

  if (fehler) {
    return (
      <div className="mb-4 rounded-xl border border-red-500/20 bg-red-500/5 p-4 text-sm text-red-500">
        Datenschutz-Einstellungen nicht abrufbar: {fehler}
      </div>
    );
  }
  if (!stand) {
    return (
      <div className="mb-4 flex items-center gap-2 rounded-xl border border-foreground/[0.06] bg-card/80 p-4 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Datenschutz-Einstellungen werden geladen …
      </div>
    );
  }

  const geaendert =
    audit.trim() !== alsText(stand.audit_aufbewahrung_tage) ||
    chat.trim() !== alsText(stand.chat_aufbewahrung_tage);
  const lauf = stand.letzter_lauf;

  return (
    <div className="mb-4 rounded-xl border border-foreground/[0.06] bg-card/80 p-4">
      <div className="flex items-start gap-3">
        <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1 space-y-4">
          <div>
            <h3 className="text-sm font-semibold">Datenschutz &amp; Aufbewahrung</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              Einträge, die älter als die Frist sind, löscht ein täglicher Aufräumlauf. Leer
              lassen heißt unbegrenzt aufbewahren. Jeder Lauf mit Löschungen steht selbst im
              Prüfprotokoll.
            </p>
          </div>

          <div className="flex flex-wrap items-end gap-4">
            <label className="block">
              <span className="text-xs font-medium">Prüfprotokoll (Tage)</span>
              <input
                inputMode="numeric"
                value={audit}
                onChange={(e) => setAudit(e.target.value)}
                placeholder="unbegrenzt"
                aria-label="Aufbewahrungsfrist Prüfprotokoll in Tagen"
                className={cn(EINGABE, "mt-1 block")}
              />
              <span className="mt-1 block text-[11px] text-muted-foreground">
                mindestens {stand.mindest_tage_audit} Tage
              </span>
            </label>
            <label className="block">
              <span className="text-xs font-medium">Chatverläufe (Tage)</span>
              <input
                inputMode="numeric"
                value={chat}
                onChange={(e) => setChat(e.target.value)}
                placeholder="unbegrenzt"
                aria-label="Aufbewahrungsfrist Chatverläufe in Tagen"
                className={cn(EINGABE, "mt-1 block")}
              />
              <span className="mt-1 block text-[11px] text-muted-foreground">
                mindestens {stand.mindest_tage_chat} Tage
              </span>
            </label>
            <button
              onClick={fristenSpeichern}
              disabled={!geaendert || speichert}
              className="mb-5 inline-flex items-center gap-2 rounded-lg bg-primary px-3.5 py-2 text-sm font-medium text-primary-foreground transition-all hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {speichert && <Loader2 className="h-4 w-4 animate-spin" />}
              Fristen speichern
            </button>
          </div>

          <p className="text-xs text-muted-foreground">
            {lauf
              ? `Letzter Aufräumlauf: ${zeitpunkt(lauf.zeit)} · ${lauf.audit_geloescht} Protokolleinträge, ${lauf.chat_nachrichten_geloescht} Chatnachrichten gelöscht`
              : "Noch kein Aufräumlauf — er startet, sobald eine Frist eingetragen ist."}
          </p>

          <div className="flex items-center justify-between gap-3 border-t border-foreground/[0.06] pt-4">
            <div className="flex min-w-0 items-start gap-2">
              <Radio className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
              <div className="min-w-0">
                <div className="text-sm font-medium">Lebenszeichen an den Anbieter</div>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  Einmal am Tag: zufällige Anlagenkennung, Version, Anzahl der Agenten und – falls
                  hinterlegt – ein Hash des Lizenzschlüssels. Keine Inhalte, keine Namen.
                </p>
              </div>
            </div>
            <button
              role="switch"
              aria-checked={stand.lebenszeichen_aktiv}
              aria-label="Lebenszeichen an den Anbieter senden"
              onClick={() =>
                speichern(
                  { lebenszeichen_aktiv: !stand.lebenszeichen_aktiv },
                  stand.lebenszeichen_aktiv ? "Lebenszeichen abgeschaltet" : "Lebenszeichen eingeschaltet",
                )
              }
              disabled={speichert}
              className={cn(
                "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors",
                stand.lebenszeichen_aktiv ? "bg-emerald-500" : "bg-foreground/[0.1]",
                speichert && "cursor-not-allowed opacity-40",
              )}
            >
              <span
                className={cn(
                  "inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform",
                  stand.lebenszeichen_aktiv ? "translate-x-6" : "translate-x-1",
                )}
              />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
