"use client";

import { useEffect, useState } from "react";
import { ArrowLeft, ShieldCheck } from "lucide-react";
import {
  mfaBestaetigen, mfaPflichtEinrichtungStarten, mfaPflichtEinrichtungBestaetigen,
  type MfaEinrichtungsDaten,
} from "@/lib/auth";
import { MfaQrSchritt, Wiederherstellungscodes } from "@/components/settings/zwei-faktor";

/**
 * Zweiter Faktor direkt nach dem Passwort (#915): Code eingeben, Pflicht-Einrichtung
 * und danach die Wiederherstellungscodes. Es gibt hier noch KEINE Sitzung, nur das
 * kurzlebige Zwischen-Token aus Anmeldung bzw. Registrierung — beide Seiten nutzen
 * diesen einen Ablauf.
 */
export function MfaAnmeldung({
  mfa,
  onAngemeldet,
  onZurueck,
}: {
  mfa: { token: string; setupRequired: boolean };
  onAngemeldet: () => void;
  onZurueck: () => void;
}) {
  const [code, setCode] = useState("");
  const [einrichtung, setEinrichtung] = useState<MfaEinrichtungsDaten | null>(null);
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!mfa.setupRequired) return;
    let aktiv = true;
    mfaPflichtEinrichtungStarten(mfa.token)
      .then((daten) => { if (aktiv) setEinrichtung(daten); })
      .catch((err) => {
        if (aktiv) setError(err instanceof Error ? err.message : "Die Einrichtung konnte nicht beginnen.");
      });
    return () => { aktiv = false; };
  }, [mfa.token, mfa.setupRequired]);

  const handleCode = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await mfaBestaetigen(mfa.token, code.trim());
      onAngemeldet();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Der Code stimmt nicht.");
    } finally {
      setLoading(false);
    }
  };

  const handleEinrichtung = async (eingabe: string) => {
    setError("");
    setLoading(true);
    try {
      const r = await mfaPflichtEinrichtungBestaetigen(mfa.token, eingabe);
      setEinrichtung(null);
      setRecoveryCodes(r.recoveryCodes);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Der Code stimmt nicht.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-4">
      {error && (
        <div className="rounded-lg bg-red-500/10 border border-red-500/20 px-4 py-3 text-sm text-red-400">
          {error}
        </div>
      )}
      <div className="flex items-center gap-2 text-sm font-medium">
        <ShieldCheck className="h-4 w-4 text-primary" />
        {recoveryCodes
          ? "Wiederherstellungscodes"
          : mfa.setupRequired
            ? "Zwei-Faktor einrichten"
            : "Bestätigungscode"}
      </div>
      {recoveryCodes ? (
        <Wiederherstellungscodes codes={recoveryCodes} onFertig={onAngemeldet} />
      ) : mfa.setupRequired ? (
        <>
          <p className="text-xs text-muted-foreground">
            Dein Administrator schreibt für Passwort-Anmeldungen einen zweiten Faktor vor.
            Richte ihn jetzt ein, um fortzufahren.
          </p>
          {einrichtung && (
            <MfaQrSchritt daten={einrichtung} onBestaetigen={handleEinrichtung} busy={loading} />
          )}
        </>
      ) : (
        <form onSubmit={handleCode} className="space-y-4">
          <div className="space-y-2">
            <label htmlFor="mfa-code" className="text-xs font-medium text-muted-foreground">
              Code aus deiner Authenticator-App
            </label>
            <input
              id="mfa-code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              required
              autoFocus
              autoComplete="one-time-code"
              className="flex h-10 w-full rounded-xl border border-border bg-card px-3 font-mono text-sm tracking-widest transition-colors focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
              placeholder="123456"
            />
            <p className="text-[11px] text-muted-foreground">
              Kein Zugriff auf dein Telefon? Gib stattdessen einen deiner Wiederherstellungscodes ein.
            </p>
          </div>
          <button
            type="submit"
            disabled={loading || !code.trim()}
            className="flex h-10 w-full items-center justify-center gap-2 rounded-xl bg-primary text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? (
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary-foreground/30 border-t-primary-foreground" />
            ) : (
              <>
                <ShieldCheck className="h-4 w-4" />
                Bestätigen
              </>
            )}
          </button>
        </form>
      )}
      {!recoveryCodes && (
        <button
          type="button"
          onClick={onZurueck}
          className="flex w-full items-center justify-center gap-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Zurück zur Anmeldung
        </button>
      )}
    </div>
  );
}
