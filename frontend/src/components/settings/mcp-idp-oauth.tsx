"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Copy, Loader2, ShieldCheck } from "lucide-react";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import type { McpIdpOAuthConfig } from "@/lib/types";
import { useToast } from "@/components/ui/dialog-provider";

/**
 * Eigene MCP-Endpunkte (Second Brains, Agenten) per OAuth 2.1 über den
 * Identitätsanbieter der Firma (#1195).
 *
 * AI Employee stellt dabei keine Tokens aus, es prüft sie nur. Deshalb sind hier
 * nur drei Angaben nötig: wer ausstellt, für wen das Token gilt, und optional eine
 * Berechtigung, die im Token stehen muss. Der Brain-Schlüssel bleibt daneben
 * gültig — für Dienstkonten und Clients ohne OAuth.
 */
const FIELDS = [
  {
    key: "issuer",
    label: "Aussteller (Issuer)",
    hint: "Z. B. https://login.microsoftonline.com/<Verzeichnis-ID>/v2.0. Leer = aus der Microsoft-Verzeichnis-ID oben abgeleitet.",
  },
  {
    key: "audience",
    label: "Empfänger (Audience)",
    hint: "Kennung der eigenen API-Registrierung, steht im Token als aud. Bei Entra: deren Anwendungs-ID, nicht die Client-ID des SSO-Logins. Mehrere mit Komma.",
  },
  {
    key: "allowed_clients",
    label: "Erlaubte Anwendungen",
    hint: "Client-IDs der MCP-Clients (im Token azp bzw. appid), mehrere mit Komma. Diese Liste oder die Berechtigung ist Pflicht.",
  },
  {
    key: "required_scope",
    label: "Erforderliche Berechtigung",
    hint: "Z. B. api://<Anwendungs-ID>/mcp.access. Fehlt sie im Token, wird abgewiesen.",
  },
  {
    key: "jwks_uri",
    label: "Schlüsseladresse (optional)",
    hint: "Leer = aus der OpenID-Konfiguration des Ausstellers. Nur https.",
  },
] as const;

type FieldKey = (typeof FIELDS)[number]["key"];

export function McpIdpOAuthConfigPanel() {
  const [config, setConfig] = useState<McpIdpOAuthConfig | null>(null);
  const [values, setValues] = useState<Record<FieldKey, string>>({
    issuer: "", audience: "", allowed_clients: "", required_scope: "", jwks_uri: "",
  });
  const [enabled, setEnabled] = useState(false);
  const [saving, setSaving] = useState(false);
  const toast = useToast();

  const uebernehmen = (c: McpIdpOAuthConfig) => {
    setConfig(c);
    setEnabled(c.enabled);
    setValues({
      issuer: c.issuer, audience: c.audience, allowed_clients: c.allowed_clients,
      required_scope: c.required_scope, jwks_uri: c.jwks_uri,
    });
  };

  useEffect(() => {
    api.getMcpIdpOAuth().then(uebernehmen).catch(() => {});
  }, []);

  const save = async (nextEnabled = enabled) => {
    setSaving(true);
    try {
      uebernehmen(await api.setMcpIdpOAuth({ enabled: nextEnabled, ...values }));
      toast.success(nextEnabled ? "MCP-Anmeldung über die Firma gespeichert" : "MCP-Anmeldung über die Firma ausgeschaltet");
    } catch (e) {
      toast.error("Speichern fehlgeschlagen", e instanceof Error ? e.message : undefined);
    } finally {
      setSaving(false);
    }
  };

  if (!config) return null;

  return (
    <div className="overflow-hidden rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm">
      <div className="flex items-center justify-between gap-3 border-b border-foreground/[0.04] px-5 py-3.5">
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-sky-500/20 bg-sky-500/10">
            <ShieldCheck className="h-4 w-4 text-sky-400" />
          </div>
          <div>
            <h3 className="text-sm font-semibold">MCP-Zugang mit Firmenanmeldung</h3>
            <p className="text-[11px] text-muted-foreground/60">
              Second Brains und Agenten per OAuth 2.1 über den eigenen Identitätsanbieter statt über einen geteilten Schlüssel
            </p>
          </div>
        </div>
        <button
          onClick={() => save(!enabled)}
          disabled={saving}
          aria-label="MCP-Zugang mit Firmenanmeldung ein- oder ausschalten"
          className={cn(
            "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors",
            enabled ? "bg-emerald-500" : "bg-foreground/[0.1]",
            saving && "cursor-not-allowed opacity-40",
          )}
        >
          {saving ? (
            <Loader2 className="mx-auto h-3 w-3 animate-spin text-white" />
          ) : (
            <span className={cn(
              "inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform",
              enabled ? "translate-x-6" : "translate-x-1",
            )} />
          )}
        </button>
      </div>

      <div className="space-y-4 p-5">
        <div className="grid gap-3 sm:grid-cols-2">
          {FIELDS.map((f) => (
            <div key={f.key}>
              <label className="text-[11px] font-medium">{f.label}</label>
              <input
                value={values[f.key]}
                onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}
                placeholder={f.key === "issuer" ? config.derived_issuer : undefined}
                spellCheck={false}
                className="mt-1 w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-2 font-mono text-[12px] outline-none focus:border-primary/50"
              />
              <p className="mt-1 text-[10px] text-muted-foreground/50">{f.hint}</p>
            </div>
          ))}
        </div>

        {config.issuer && values.issuer.trim() !== config.issuer && (
          <p className="flex items-start gap-1.5 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-[11px] text-amber-700 dark:text-amber-300">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            Der Aussteller ändert sich. Danach gelten nur noch Tokens dieses Anbieters; alle bisher verbundenen MCP-Clients müssen sich neu anmelden. Nur ändern, wenn du dem neuen Aussteller vertraust.
          </p>
        )}

        {config.active && (
          <div className="space-y-1.5 rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-3">
            <p className="flex items-center gap-1.5 text-[11px] font-medium text-emerald-700 dark:text-emerald-300">
              <CheckCircle2 className="h-3.5 w-3.5" />
              Aktiv. Im MCP-Client diese Adresse eintragen, Anmeldung „OAuth“:
            </p>
            <div className="flex items-center gap-2 rounded-md border border-foreground/10 bg-background/50 px-3 py-1.5 font-mono text-[11px]">
              <span className="flex-1 break-all">{config.brain_url_example}</span>
              <button
                onClick={() => navigator.clipboard.writeText(config.brain_url_example)}
                className="shrink-0 text-muted-foreground/40 transition-colors hover:text-muted-foreground"
                title="Kopieren"
              >
                <Copy className="h-3 w-3" />
              </button>
            </div>
            <p className="text-[10px] text-muted-foreground/60">
              Jede Person sieht nur die Second Brains, die ihr freigegeben sind. Wer in AI Employee kein Konto hat, wird abgewiesen. Der Brain-Schlüssel gilt daneben weiter.
            </p>
          </div>
        )}
        {enabled && !config.active && (
          <p className="text-[11px] text-amber-700 dark:text-amber-400/80">
            Eingeschaltet, aber noch nicht wirksam: Aussteller, Empfänger und erlaubte Anwendungen oder Berechtigung fehlen.
          </p>
        )}

        <button
          onClick={() => save()}
          disabled={saving}
          className={cn(
            "inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground",
            saving && "opacity-40",
          )}
        >
          {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          Speichern
        </button>
      </div>
    </div>
  );
}
