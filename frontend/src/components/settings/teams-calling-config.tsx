"use client";

import { useCallback, useEffect, useState } from "react";
import {
  PhoneCall,
  Loader2,
  Copy,
  Check,
  CheckCircle2,
  AlertTriangle,
  ExternalLink,
  ShieldCheck,
} from "lucide-react";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import { useToast } from "@/components/ui/dialog-provider";

/**
 * Agent mit Stimme im Teams-Termin — Einrichtung.
 *
 * Der Ablauf für den Administrator ist genau einer: die Adresse unten kopieren, in
 * Azure eintragen, drei Angaben zurückkopieren, prüfen. Deshalb steht die Adresse
 * ganz oben und ist mit einem Klick kopierbar — sie ist der eigentliche Schritt,
 * alles andere ist Beiwerk.
 *
 * Die HTTPS-Warnung steht bewusst VOR der Einrichtung: Microsoft ruft nur HTTPS
 * zurück, und läuft die Anlage unter http, bleibt der Agent stumm — ohne dass
 * irgendwo ein Fehler auftauchen würde.
 */
export function TeamsCallingConfig() {
  const [setup, setSetup] = useState<api.TeamsCallingSetup | null>(null);
  const [appId, setAppId] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [secret, setSecret] = useState("");
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [copied, setCopied] = useState(false);
  const [loading, setLoading] = useState(true);
  const toast = useToast();

  const load = useCallback(async () => {
    try {
      const s = await api.getTeamsCallingSetup();
      setSetup(s);
      setAppId(s.app_id);
      setTenantId(s.tenant_id);
    } catch {
      setSetup(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const copy = async () => {
    if (!setup) return;
    await navigator.clipboard.writeText(setup.callback_url);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const save = async () => {
    setSaving(true);
    try {
      await api.updateSettings({
        teams_calling_app_id: appId.trim(),
        teams_calling_tenant_id: tenantId.trim(),
        // Leeres Feld heisst „nicht anfassen" — sonst loescht ein Speichern ohne
        // erneutes Eintippen das Geheimnis, und der Agent bleibt Terminen fern.
        ...(secret.trim() ? { teams_calling_app_secret: secret.trim() } : {}),
      });
      setSecret("");
      await load();
      toast.success("Gespeichert");
    } catch (e) {
      toast.error("Speichern fehlgeschlagen", e instanceof Error ? e.message : undefined);
    } finally {
      setSaving(false);
    }
  };

  const test = async () => {
    setTesting(true);
    try {
      const res = await api.testTeamsCalling();
      if (res.ok) toast.success("Alles bereit", "Token wird ausgestellt, Zustimmung liegt vor.");
      else toast.error("Noch nicht fertig", res.reason);
    } catch (e) {
      toast.error("Prüfung fehlgeschlagen", e instanceof Error ? e.message : undefined);
    } finally {
      setTesting(false);
    }
  };

  const toggleEnabled = async () => {
    if (!setup) return;
    const next = !setup.enabled;
    try {
      await api.updateSettings({ teams_calling_enabled: next ? "true" : "false" });
      await load();
      toast.success(next ? "Teams-Anrufe aktiv" : "Teams-Anrufe aus");
    } catch (e) {
      toast.error("Fehlgeschlagen", e instanceof Error ? e.message : undefined);
    }
  };

  if (loading || !setup) return null;

  return (
    <div className="overflow-hidden rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm">
      <div className="flex items-center justify-between border-b border-foreground/[0.04] px-5 py-3.5">
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-indigo-500/20 bg-indigo-500/10">
            <PhoneCall className="h-4 w-4 text-indigo-400" />
          </div>
          <div>
            <h3 className="text-sm font-semibold">Agent im Teams-Termin (mit Stimme)</h3>
            <p className="text-[11px] text-muted-foreground/60">
              Beitreten, sprechen, auf Antworten reagieren — abwechselnd wie am Telefon
            </p>
          </div>
        </div>
        {setup.enabled ? (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/20 bg-emerald-500/10 px-2.5 py-1 text-[10px] font-medium text-emerald-400">
            <CheckCircle2 className="h-3 w-3" />
            Aktiv
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-zinc-500/20 bg-zinc-500/10 px-2.5 py-1 text-[10px] font-medium text-zinc-400">
            {setup.configured ? "Eingerichtet, aus" : "Nicht eingerichtet"}
          </span>
        )}
      </div>

      <div className="space-y-4 p-5">
        {!setup.https_ok && (
          <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/[0.07] p-3 text-[11px] text-amber-700 dark:text-amber-300">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              Diese Anlage ist nicht über <b>https</b> erreichbar. Microsoft ruft
              ausschließlich HTTPS zurück — der Agent bliebe im Termin stumm, ohne dass
              hier ein Fehler auftaucht. Das zuerst lösen.
            </span>
          </div>
        )}

        {/* Der eigentliche Schritt: diese Adresse in Azure eintragen. */}
        <div className="rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] p-3">
          <div className="text-[11px] font-medium">Schritt 1 — diese Adresse in Azure eintragen</div>
          <p className="mt-1 text-[11px] text-muted-foreground/60">
            Azure-Portal → <b>Azure Bot</b> → Kanäle → Microsoft Teams → Reiter{" "}
            <b>Anrufe</b> → „Webhook (für Anrufe)"
          </p>
          <div className="mt-2 flex items-center gap-2">
            <code className="flex-1 break-all rounded-lg border border-foreground/[0.08] bg-background/60 px-2.5 py-2 font-mono text-[11px]">
              {setup.callback_url}
            </code>
            <button
              onClick={copy}
              title="Kopieren"
              className="shrink-0 rounded-lg border border-foreground/[0.08] p-2 hover:bg-foreground/[0.06]"
            >
              {copied ? (
                <Check className="h-3.5 w-3.5 text-emerald-400" />
              ) : (
                <Copy className="h-3.5 w-3.5" />
              )}
            </button>
          </div>
          <a
            href="https://github.com/greeves89/AI-Employee/blob/main/docs/TEAMS_CALLING_SETUP.md"
            target="_blank"
            rel="noreferrer"
            className="mt-2 inline-flex items-center gap-1.5 text-[11px] text-primary hover:underline"
          >
            Vollständige Azure-Anleitung, Klick für Klick
            <ExternalLink className="h-3 w-3" />
          </a>
        </div>

        {/* Schritt 2 */}
        <div>
          <div className="mb-2 text-[11px] font-medium">
            Schritt 2 — Angaben aus Azure zurückkopieren
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label className="text-[11px] text-muted-foreground">
                Anwendungs-ID (Client) <span className="text-red-400">*</span>
              </label>
              <input
                value={appId}
                onChange={(e) => setAppId(e.target.value)}
                placeholder="00000000-0000-0000-0000-000000000000"
                className="mt-1 w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-2 font-mono text-[11px] outline-none focus:border-primary/50"
              />
            </div>
            <div>
              <label className="text-[11px] text-muted-foreground">
                Verzeichnis-ID (Mandant) <span className="text-red-400">*</span>
              </label>
              <input
                value={tenantId}
                onChange={(e) => setTenantId(e.target.value)}
                placeholder="00000000-0000-0000-0000-000000000000"
                className="mt-1 w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-2 font-mono text-[11px] outline-none focus:border-primary/50"
              />
            </div>
          </div>
          <div className="mt-3">
            <label className="text-[11px] text-muted-foreground">
              Client-Geheimnis {setup.has_secret ? "(hinterlegt — leer lassen zum Behalten)" : <span className="text-red-400">*</span>}
            </label>
            <input
              type="password"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              placeholder={setup.has_secret ? "••••••••" : "Wert aus „Zertifikate & Geheimnisse\""}
              className="mt-1 w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-2 font-mono text-[11px] outline-none focus:border-primary/50"
            />
            <p className="mt-1 text-[10px] text-muted-foreground/50">
              Der <b>Wert</b>, nicht die Geheimnis-ID. Er ist in Azure nur einmal sichtbar.
            </p>
          </div>
        </div>

        {/* Berechtigungen als Checkliste */}
        <div className="rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] p-3">
          <div className="flex items-center gap-1.5 text-[11px] font-medium">
            <ShieldCheck className="h-3.5 w-3.5 text-primary" />
            Schritt 3 — diese Anwendungsberechtigungen freigeben und zustimmen
          </div>
          <ul className="mt-2 space-y-1">
            {setup.permissions.map((p) => (
              <li key={p.name} className="flex items-baseline gap-2 text-[11px]">
                <code className="font-mono text-muted-foreground">{p.name}</code>
                <span className="text-muted-foreground/50">— {p.why}</span>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-[10px] text-muted-foreground/50">
            Anwendungsberechtigungen, nicht delegierte. Ohne den Klick auf
            „Administratorzustimmung erteilen" bekommt die App zwar ein Token, darf aber
            keinem Termin beitreten — der häufigste Grund, warum es beim ersten Versuch
            nicht klappt.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={save}
            disabled={saving}
            className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-40"
          >
            {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Speichern
          </button>
          <button
            onClick={test}
            disabled={testing || !setup.configured}
            className="inline-flex items-center gap-2 rounded-lg border border-foreground/[0.08] px-4 py-2 text-sm hover:bg-foreground/[0.06] disabled:opacity-40"
          >
            {testing && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Einrichtung prüfen
          </button>
          {setup.configured && (
            <button
              onClick={toggleEnabled}
              className={cn(
                "ml-auto inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm",
                setup.enabled
                  ? "border border-foreground/[0.08] hover:bg-foreground/[0.06]"
                  : "bg-emerald-500/15 text-emerald-400 hover:bg-emerald-500/20"
              )}
            >
              {setup.enabled ? "Deaktivieren" : "Teams-Anrufe aktivieren"}
            </button>
          )}
        </div>

        <p className="border-t border-foreground/[0.06] pt-3 text-[10px] text-muted-foreground/50">
          Der Agent spricht und hört <b>abwechselnd</b>, wie am Telefon. Durchgehend
          mithören und dazwischenreden bräuchte den rohen Audiostrom — dafür verlangt
          Microsoft ein eigenes .NET-Medienmodul, offene Medienports und die weit
          reichende Berechtigung <code>Calls.AccessMedia.All</code>. Die wird hier
          bewusst nicht angefordert.
        </p>
      </div>
    </div>
  );
}
