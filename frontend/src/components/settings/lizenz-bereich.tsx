"use client";

import { useEffect, useState } from "react";
import { AlertCircle, CheckCircle2, ExternalLink, Loader2, Lock, Mail } from "lucide-react";
import * as api from "@/lib/api";
import { apiFehlertext } from "@/lib/api-fehler";
import { cn } from "@/lib/utils";
import { useConfirm } from "@/components/ui/dialog-provider";

/**
 * Lizenz der Anlage: Edition, Belegung, Hinweise und das Feld zum Eintragen.
 *
 * Steht in der Admin-Konsole unter „System & Lizenz“ ganz oben, damit der Knopf
 * „Lizenzschlüssel eintragen“ ohne Blättern hier ankommt (#899). Bis dahin ein
 * Abschnitt mitten in der grossen Einstellungsseite.
 */
export function LizenzBereich() {
  const confirm = useConfirm();
  const [license, setLicense] = useState<api.License | null>(null);
  const [licenseKeyInput, setLicenseKeyInput] = useState("");
  const [licenseBusy, setLicenseBusy] = useState(false);
  const [licenseError, setLicenseError] = useState("");

  const loadLicense = async () => {
    try {
      setLicense(await api.getLicenseStatus());
    } catch {
      // ignore — fallback community tier displayed
    }
  };

  useEffect(() => {
    loadLicense();
  }, []);

  // Sprung per #lizenz (aeltere Verweise). Der Abschnitt hat seine endgueltige
  // Hoehe erst, wenn die Lizenzdaten da sind.
  useEffect(() => {
    if (window.location.hash !== "#lizenz") return;
    document.getElementById("lizenz")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [license]);

  const handleApplyLicense = async () => {
    if (!licenseKeyInput.trim()) return;
    setLicenseBusy(true);
    setLicenseError("");
    try {
      await api.applyLicense(licenseKeyInput.trim());
      setLicenseKeyInput("");
      await loadLicense();
    } catch (e) {
      setLicenseError(apiFehlertext(e, "Kein gültiger Lizenzschlüssel."));
    } finally {
      setLicenseBusy(false);
    }
  };

  const handleNutzung = async (privat: boolean) => {
    setLicenseBusy(true);
    try {
      setLicense(await api.setLicenseNutzung(privat));
    } finally {
      setLicenseBusy(false);
    }
  };

  const handleRemoveLicense = async () => {
    const ok = await confirm({
      title: "Lizenz entfernen?",
      message: "Das Agentenlimit dieser Lizenz gilt weiter, bis eine neue Lizenz eingetragen ist.",
      variant: "destructive",
      confirmLabel: "Entfernen",
    });
    if (!ok) return;
    setLicenseBusy(true);
    try {
      await api.removeLicense();
      await loadLicense();
    } finally {
      setLicenseBusy(false);
    }
  };

  const anbieterUrl = license?.anbieter?.url?.trim() || "";
  const anbieterKontakt = license?.anbieter?.kontakt?.trim() || "";
  let anbieterAnzeige = anbieterUrl;
  try {
    if (anbieterUrl) anbieterAnzeige = new URL(anbieterUrl).host;
  } catch {
    // keine gueltige Adresse: so anzeigen, wie sie eingetragen ist
  }

  return (
    <section id="lizenz" className="scroll-mt-6">
      <div className="flex items-center gap-2 mb-3">
        <Lock className="h-4 w-4 text-muted-foreground/60" />
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">
          Lizenz
        </h2>
      </div>
      <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5">
        {license && (
          <div className="mb-4 flex items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="flex items-center gap-2 mb-1">
                <span className={cn(
                  "inline-flex items-center rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider",
                  license.tier === "enterprise" ? "bg-purple-500/10 text-purple-400 border-purple-500/20" :
                  license.tier === "business" ? "bg-blue-500/10 text-blue-400 border-blue-500/20" :
                  license.tier === "team" ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" :
                  license.tier === "starter" ? "bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/20" :
                  "bg-zinc-500/10 text-zinc-400 border-zinc-500/20"
                )}>
                  {license.tier}
                </span>
                {license.zustand === "aktiv" && (
                  <span className="inline-flex items-center gap-1 text-[11px] text-emerald-400">
                    <CheckCircle2 className="h-3 w-3" />
                    Aktiv
                  </span>
                )}
                {(license.zustand === "abgelaufen" || license.zustand === "widerrufen") && (
                  <span className="inline-flex items-center gap-1 text-[11px] text-red-400">
                    <AlertCircle className="h-3 w-3" />
                    {license.zustand === "widerrufen" ? "Widerrufen" : "Abgelaufen"}
                  </span>
                )}
              </div>
              <p className="text-sm text-muted-foreground">
                {license.zustand === "ohne"
                  ? "Community Edition — kostenlos für private und nicht-gewerbliche Nutzung. Unternehmen dürfen 30 Tage testen, danach ist eine Lizenz nötig."
                  : `Lizenziert für ${license.issued_to}`}
              </p>
              {typeof license.agenten === "number" && (
                <p className="text-[11px] text-muted-foreground/70 mt-1">
                  {license.agentenlimit
                    ? `${license.agenten} von ${license.agentenlimit} Agenten belegt`
                    : `${license.agenten} Agenten, unbegrenzt`}
                </p>
              )}
              {license.expires_at && (
                <p className="text-[11px] text-muted-foreground/60 mt-1">
                  {license.is_expired ? "Abgelaufen am" : "Gültig bis"}: {new Date(license.expires_at).toLocaleDateString("de-DE")}
                </p>
              )}
              {license.hinweis && (
                <p className="mt-2 flex items-start gap-1.5 rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-[12px] text-amber-800 dark:text-amber-200">
                  <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  <span>{license.hinweis}</span>
                </p>
              )}
              {license.zustand === "ohne" && license.limit_quelle !== "gemerkt" && (
                <label className="mt-3 flex items-start gap-2 text-[12px] text-muted-foreground">
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    checked={!!license.private_nutzung}
                    disabled={licenseBusy}
                    onChange={(e) => handleNutzung(e.target.checked)}
                  />
                  <span>Diese Anlage wird ausschließlich privat oder nicht-gewerblich genutzt.</span>
                </label>
              )}
            </div>
            {license.zustand !== "ohne" && (
              <button
                onClick={handleRemoveLicense}
                disabled={licenseBusy}
                className="shrink-0 text-[11px] text-red-400 hover:text-red-300 underline underline-offset-2"
              >
                Lizenz entfernen
              </button>
            )}
          </div>
        )}
        {(!license || license.zustand !== "aktiv") && (
          <div className="space-y-3 border-t border-foreground/[0.04] pt-4">
            <div>
              <label htmlFor="lizenzschluessel" className="text-[11px] font-medium text-muted-foreground/70 mb-1.5 block">
                Lizenzschlüssel
              </label>
              <textarea
                id="lizenzschluessel"
                value={licenseKeyInput}
                onChange={(e) => setLicenseKeyInput(e.target.value)}
                placeholder="Lizenzschlüssel hier einfügen"
                rows={3}
                className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-xs font-mono outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 resize-none"
              />
            </div>
            {licenseError && (
              <p className="text-[11px] text-red-400 flex items-center gap-1">
                <AlertCircle className="h-3 w-3" />
                {licenseError}
              </p>
            )}
            <div className="flex flex-wrap items-center justify-between gap-3">
              {/* Editionen, Preise und Kontakt kommen vom Anbieter der Anlage
                  (ANBIETER_URL / ANBIETER_KONTAKT), nicht aus dem Quellcode-Repository. */}
              {(anbieterUrl || anbieterKontakt) ? (
                <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-muted-foreground/60">
                  <span>Editionen, Preise und Lizenzschlüssel beim Anbieter:</span>
                  {anbieterUrl && (
                    <a href={anbieterUrl} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline">
                      <ExternalLink className="h-3 w-3" />
                      {anbieterAnzeige}
                    </a>
                  )}
                  {anbieterKontakt && (
                    <a href={`mailto:${anbieterKontakt}`} className="inline-flex items-center gap-1 text-primary hover:underline">
                      <Mail className="h-3 w-3" />
                      {anbieterKontakt}
                    </a>
                  )}
                </p>
              ) : (
                <span />
              )}
              <button
                onClick={handleApplyLicense}
                disabled={licenseBusy || !licenseKeyInput.trim()}
                className="rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
              >
                {licenseBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                Lizenz eintragen
              </button>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
