"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, Copy, Download, KeyRound, Loader2, ShieldCheck, ShieldOff, Smartphone } from "lucide-react";
import * as api from "@/lib/api";
import { apiFehlertext } from "@/lib/api-fehler";
import { cn } from "@/lib/utils";
import { useToast } from "@/components/ui/dialog-provider";

/**
 * Zwei-Faktor-Anmeldung per TOTP (#915).
 *
 * Die Bausteine (QR-Schritt, Wiederherstellungscodes) nutzen zwei Stellen: die
 * eigenen Einstellungen und die Pflicht-Einrichtung auf der Anmeldeseite, wenn
 * ein Administrator Zwei-Faktor erzwingt. Den QR-Code baut der Server aus der
 * otpauth-Adresse (dieselbe Hilfe wie im Sprach-Cockpit) — im Frontend gibt es
 * keine QR-Bibliothek, und eine neue Abhängigkeit nur dafür lohnt nicht.
 */

const eingabeKlasse =
  "flex h-10 w-full rounded-xl border border-border bg-card px-3 text-sm transition-colors focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary";

export interface QrDaten {
  secret: string;
  otpauth_uri: string;
  qr_svg: string;
}

function qrBild(svg: string): string {
  // Das SVG besteht nur aus Rechtecken (ASCII) — als Bild eingebunden, nicht als HTML.
  return `data:image/svg+xml;base64,${typeof window !== "undefined" ? window.btoa(svg) : ""}`;
}

/** Geheimnis in Vierergruppen — leichter abzutippen. */
function gruppiert(secret: string): string {
  return secret.replace(/(.{4})/g, "$1 ").trim();
}

export function MfaQrSchritt({
  daten,
  onBestaetigen,
  busy,
  fehler,
}: {
  daten: QrDaten;
  onBestaetigen: (code: string) => void;
  busy: boolean;
  fehler?: string;
}) {
  const [code, setCode] = useState("");
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onBestaetigen(code.trim());
      }}
      className="space-y-4"
    >
      <ol className="space-y-1 text-[12px] text-muted-foreground list-decimal pl-4">
        <li>Öffne eine Authenticator-App auf deinem Telefon (z. B. Microsoft Authenticator oder Google Authenticator).</li>
        <li>Scanne den QR-Code oder tippe den Schlüssel ab.</li>
        <li>Gib den sechsstelligen Code ein, den die App anzeigt.</li>
      </ol>
      <div className="flex flex-col items-center gap-3">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={qrBild(daten.qr_svg)}
          alt="QR-Code für die Authenticator-App"
          width={180}
          height={180}
          className="rounded-lg bg-white p-2"
        />
        <div className="w-full rounded-lg border border-border bg-card/60 px-3 py-2 text-center">
          <p className="text-[10px] uppercase tracking-wider text-muted-foreground/70">Schlüssel zum Abtippen</p>
          <p className="mt-0.5 break-all font-mono text-[12px] select-all">{gruppiert(daten.secret)}</p>
        </div>
      </div>
      <div className="space-y-2">
        <label htmlFor="mfa-einrichtung-code" className="text-xs font-medium text-muted-foreground">
          Bestätigungscode
        </label>
        <input
          id="mfa-einrichtung-code"
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={6}
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
          className={cn(eingabeKlasse, "font-mono tracking-[0.3em]")}
          placeholder="123456"
          autoFocus
        />
      </div>
      {fehler && <p className="text-xs text-red-400">{fehler}</p>}
      <button
        type="submit"
        disabled={busy || code.length !== 6}
        className="flex h-10 w-full items-center justify-center gap-2 rounded-xl bg-primary text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />}
        Zwei-Faktor aktivieren
      </button>
    </form>
  );
}

export function Wiederherstellungscodes({ codes, onFertig }: { codes: string[]; onFertig: () => void }) {
  const [kopiert, setKopiert] = useState(false);
  const text = codes.join("\n");
  const kopieren = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setKopiert(true);
      setTimeout(() => setKopiert(false), 2000);
    } catch {
      /* Zwischenablage gesperrt — die Codes stehen weiter sichtbar da */
    }
  };
  const herunterladen = () => {
    const blob = new Blob([`AI Employee — Wiederherstellungscodes\n\n${text}\n`], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "wiederherstellungscodes.txt";
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-[12px] leading-relaxed text-amber-700 dark:text-amber-300">
        <strong>Jetzt sichern:</strong> Diese Wiederherstellungscodes werden nur dieses eine Mal angezeigt.
        Jeder Code funktioniert genau einmal — zum Beispiel, wenn du dein Telefon verlierst.
      </div>
      <ul className="grid grid-cols-2 gap-2 rounded-lg border border-border bg-card/60 p-3 font-mono text-[13px]">
        {codes.map((c) => (
          <li key={c} className="select-all">{c}</li>
        ))}
      </ul>
      <div className="flex gap-2">
        <button
          type="button"
          onClick={kopieren}
          className="flex h-9 flex-1 items-center justify-center gap-2 rounded-xl border border-border bg-card text-sm transition-colors hover:bg-accent/50"
        >
          {kopiert ? <Check className="h-4 w-4 text-emerald-400" /> : <Copy className="h-4 w-4" />}
          {kopiert ? "Kopiert" : "Kopieren"}
        </button>
        <button
          type="button"
          onClick={herunterladen}
          className="flex h-9 flex-1 items-center justify-center gap-2 rounded-xl border border-border bg-card text-sm transition-colors hover:bg-accent/50"
        >
          <Download className="h-4 w-4" />
          Herunterladen
        </button>
      </div>
      <button
        type="button"
        onClick={onFertig}
        className="flex h-10 w-full items-center justify-center gap-2 rounded-xl bg-primary text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
      >
        Ich habe die Codes gesichert
      </button>
    </div>
  );
}

/** Eigene Einstellungen: Status, Einrichten, Abschalten (nur mit Passwort + Code). */
export function ZweiFaktorEinstellungen() {
  const [status, setStatus] = useState<api.MfaStatus | null>(null);
  const [einrichtung, setEinrichtung] = useState<QrDaten | null>(null);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [abschalten, setAbschalten] = useState(false);
  const [passwort, setPasswort] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [fehler, setFehler] = useState("");
  const toast = useToast();

  const laden = useCallback(async () => {
    try {
      setStatus(await api.getMfaStatus());
    } catch (e) {
      setFehler(apiFehlertext(e, "Der Zwei-Faktor-Status konnte nicht geladen werden."));
    }
  }, []);

  useEffect(() => { laden(); }, [laden]);

  const starten = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setFehler("");
    try {
      setEinrichtung(await api.startMfaSetup(passwort));
      setPasswort("");
    } catch (e) {
      setFehler(apiFehlertext(e));
    } finally {
      setBusy(false);
    }
  };

  const bestaetigen = async (c: string) => {
    setBusy(true);
    setFehler("");
    try {
      const r = await api.confirmMfaSetup(c);
      setEinrichtung(null);
      setCodes(r.recovery_codes);
    } catch (e) {
      setFehler(apiFehlertext(e));
    } finally {
      setBusy(false);
    }
  };

  const ausschalten = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setFehler("");
    try {
      await api.disableMfa(passwort, code.trim());
      setAbschalten(false);
      setPasswort("");
      setCode("");
      toast.success("Zwei-Faktor abgeschaltet");
      await laden();
    } catch (err) {
      setFehler(apiFehlertext(err));
    } finally {
      setBusy(false);
    }
  };

  if (!status) {
    return fehler ? <p className="text-xs text-red-400">{fehler}</p> : null;
  }

  return (
    <div className="rounded-xl border border-foreground/[0.06] bg-card/80 p-5 backdrop-blur-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-blue-500/20 bg-blue-500/10">
            <Smartphone className="h-4 w-4 text-blue-400" />
          </div>
          <div>
            <p className="text-sm font-semibold">Zwei-Faktor-Anmeldung</p>
            <p className="mt-0.5 text-[11px] text-muted-foreground/70">
              {!status.password_account
                ? "Du meldest dich über SSO an — den zweiten Faktor verwaltet dein Identitätsanbieter."
                : status.enabled
                  ? `Aktiv. Bei der Anmeldung fragen wir nach dem Code aus deiner Authenticator-App. Noch ${status.recovery_codes_left} Wiederherstellungscodes übrig.`
                  : status.required
                    ? "Von deinem Administrator vorgeschrieben. Richte sie jetzt ein — sonst bei der nächsten Anmeldung."
                    : "Schützt dein Konto zusätzlich zum Passwort mit einem Code aus einer Authenticator-App."}
            </p>
          </div>
        </div>
        {status.password_account && (
          <span
            className={cn(
              "shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium",
              status.enabled ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" : "bg-foreground/[0.06] text-muted-foreground",
            )}
          >
            {status.enabled ? "Aktiv" : "Aus"}
          </span>
        )}
      </div>

      {status.password_account && (
        <div className="mt-4 space-y-4">
          {codes ? (
            <Wiederherstellungscodes codes={codes} onFertig={() => { setCodes(null); laden(); }} />
          ) : einrichtung ? (
            <MfaQrSchritt daten={einrichtung} onBestaetigen={bestaetigen} busy={busy} fehler={fehler} />
          ) : !status.enabled ? (
            <form onSubmit={starten} className="space-y-3">
              <p className="text-[12px] text-muted-foreground">
                Bestätige zuerst dein Passwort. Danach zeigen wir den QR-Code für deine Authenticator-App.
              </p>
              <input
                type="password"
                autoComplete="current-password"
                value={passwort}
                onChange={(e) => setPasswort(e.target.value)}
                placeholder="Passwort"
                className={eingabeKlasse}
              />
              {fehler && <p className="text-xs text-red-400">{fehler}</p>}
              <button
                type="submit"
                disabled={busy || !passwort}
                className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
              >
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />}
                Zwei-Faktor einrichten
              </button>
            </form>
          ) : abschalten ? (
            <form onSubmit={ausschalten} className="space-y-3">
              <p className="text-[12px] text-muted-foreground">
                Zum Abschalten brauchst du dein Passwort und einen aktuellen Code (oder einen Wiederherstellungscode).
              </p>
              <input
                type="password"
                autoComplete="current-password"
                value={passwort}
                onChange={(e) => setPasswort(e.target.value)}
                placeholder="Passwort"
                className={eingabeKlasse}
              />
              <input
                autoComplete="one-time-code"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                placeholder="Code aus der App"
                className={cn(eingabeKlasse, "font-mono")}
              />
              {fehler && <p className="text-xs text-red-400">{fehler}</p>}
              <div className="flex gap-2">
                <button
                  type="submit"
                  disabled={busy || !passwort || !code.trim()}
                  className="inline-flex items-center gap-2 rounded-xl bg-red-500/90 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-red-500 disabled:opacity-50"
                >
                  {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldOff className="h-4 w-4" />}
                  Abschalten
                </button>
                <button
                  type="button"
                  onClick={() => { setAbschalten(false); setFehler(""); }}
                  className="rounded-xl border border-border px-4 py-2 text-sm transition-colors hover:bg-accent/50"
                >
                  Abbrechen
                </button>
              </div>
            </form>
          ) : (
            <button
              type="button"
              onClick={() => setAbschalten(true)}
              className="inline-flex items-center gap-2 rounded-xl border border-border px-4 py-2 text-sm transition-colors hover:bg-accent/50"
            >
              <ShieldOff className="h-4 w-4" />
              Zwei-Faktor abschalten
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/** Admin: Zwei-Faktor für alle Passwort-Konten erzwingen. Speichert sofort. */
export function ZweiFaktorPflichtToggle() {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [saving, setSaving] = useState(false);
  const toast = useToast();

  useEffect(() => {
    api.getSettings()
      .then((s) => setEnabled(Boolean(s.require_mfa_for_password_accounts)))
      .catch(() => setEnabled(null));
  }, []);

  const toggle = async () => {
    if (enabled === null) return;
    setSaving(true);
    try {
      await api.updateSettings({ require_mfa_for_password_accounts: !enabled });
      setEnabled(!enabled);
    } catch (e) {
      toast.error("Konnte die Zwei-Faktor-Pflicht nicht ändern", apiFehlertext(e));
    } finally {
      setSaving(false);
    }
  };

  if (enabled === null) return null;

  return (
    <div className="p-5 pt-3 border-t border-foreground/[0.04]">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-[12px] font-medium">
            <KeyRound className="h-3.5 w-3.5 text-blue-400" />
            Zwei-Faktor für alle Passwort-Konten erzwingen
          </div>
          <p className="mt-0.5 text-[10px] text-muted-foreground/60">
            Wer sich mit Passwort anmeldet und noch keinen zweiten Faktor hat, richtet ihn direkt nach dem
            Passwort ein — vorher gibt es keinen Zugang. SSO-Anmeldungen sind ausgenommen, dort gilt die
            Mehrfaktor-Regel des Identitätsanbieters. Bereits angemeldete Sitzungen laufen bis zu ihrem Ablauf weiter.
          </p>
        </div>
        <button
          onClick={toggle}
          disabled={saving}
          aria-label="Zwei-Faktor für alle Passwort-Konten erzwingen"
          className={cn(
            "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors",
            enabled ? "bg-emerald-500" : "bg-foreground/[0.1]",
            saving && "opacity-40 cursor-not-allowed",
          )}
        >
          {saving ? (
            <Loader2 className="mx-auto h-3 w-3 animate-spin text-white" />
          ) : (
            <span
              className={cn(
                "inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform",
                enabled ? "translate-x-6" : "translate-x-1",
              )}
            />
          )}
        </button>
      </div>
    </div>
  );
}
