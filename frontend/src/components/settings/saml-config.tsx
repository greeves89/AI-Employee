"use client";

import { useEffect, useState } from "react";
import { KeyRound, Loader2, ExternalLink, CheckCircle2 } from "lucide-react";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import { getApiUrl } from "@/lib/config";
import { useToast } from "@/components/ui/dialog-provider";

/**
 * SAML 2.0 einrichten.
 *
 * Der Ablauf beim Kunden ist immer derselbe: unsere Metadaten beim
 * Identitätsanbieter eintragen, dessen Angaben hier eintragen, fertig. Deshalb steht
 * der Metadaten-Link ganz oben — das ist der erste Schritt, nicht ein Detail am Rand.
 *
 * Ohne Zertifikat wird SAML gar nicht erst als Anmeldeweg angeboten: keine Signatur
 * prüfbar heißt, der Knopf auf der Anmeldeseite würde sicher in einen Fehler laufen.
 */
const FIELDS = [
  {
    key: "saml_idp_entity_id",
    label: "Entity-ID des Anbieters",
    hint: "Steht in dessen Metadaten, z. B. https://sts.windows.net/<tenant>/",
    required: true,
  },
  {
    key: "saml_idp_sso_url",
    label: "Anmelde-Adresse (SSO-URL)",
    hint: "Wohin der Nutzer zum Anmelden geschickt wird.",
    required: true,
  },
  {
    key: "saml_idp_slo_url",
    label: "Abmelde-Adresse (optional)",
    hint: "Nur nötig, wenn zentrale Abmeldung gewünscht ist.",
    required: false,
  },
  {
    key: "saml_sp_entity_id",
    label: "Eigene Entity-ID (optional)",
    hint: "Leer lassen — dann wird die Metadaten-Adresse verwendet.",
    required: false,
  },
  {
    key: "saml_display_name",
    label: "Beschriftung des Knopfes",
    hint: 'Was auf der Anmeldeseite steht. Leer = "SAML".',
    required: false,
  },
  {
    key: "saml_group_attribute",
    label: "Gruppen-Attribut",
    hint: 'Aus welchem Attribut die Gruppen kommen. Leer = "groups", bei AD oft "memberOf".',
    required: false,
  },
] as const;

export function SamlConfig() {
  const [values, setValues] = useState<Record<string, string>>({});
  const [cert, setCert] = useState("");
  const [roleMap, setRoleMap] = useState("");
  const [configured, setConfigured] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const toast = useToast();

  useEffect(() => {
    api
      .getSettings()
      .then((s) => {
        const bag = s as unknown as Record<string, string | undefined>;
        const next: Record<string, string> = {};
        for (const f of FIELDS) next[f.key] = bag[f.key] ?? "";
        setValues(next);
        setCert(s.saml_idp_x509_cert ?? "");
        setRoleMap(s.saml_group_role_map ?? "");
        setConfigured(Boolean(s.saml_configured));
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const save = async () => {
    // Kaputtes JSON hier abfangen und nicht erst beim Anmelden: eine unlesbare
    // Zuordnung wird serverseitig ignoriert, und dann wundert sich jemand, warum
    // niemand Administrator wird.
    if (roleMap.trim()) {
      try {
        const parsed = JSON.parse(roleMap);
        if (typeof parsed !== "object" || Array.isArray(parsed)) throw new Error();
      } catch {
        toast.error(
          "Gruppen-Zuordnung ungültig",
          'Erwartet wird ein JSON-Objekt, z. B. {"IT-Admins": "admin", "Alle": "member"}'
        );
        return;
      }
    }

    setSaving(true);
    try {
      await api.updateSettings({
        ...values,
        saml_idp_x509_cert: cert.trim(),
        saml_group_role_map: roleMap.trim(),
      });
      const fresh = await api.getSettings();
      setConfigured(Boolean(fresh.saml_configured));
      toast.success("SAML gespeichert");
    } catch (e) {
      toast.error("Speichern fehlgeschlagen", e instanceof Error ? e.message : undefined);
    } finally {
      setSaving(false);
    }
  };

  if (loading) return null;

  const metadataUrl = `${getApiUrl()}/api/v1/auth/sso/saml/metadata`;

  return (
    <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden">
      <div className="flex items-center justify-between border-b border-foreground/[0.04] px-5 py-3.5">
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-violet-500/20 bg-violet-500/10">
            <KeyRound className="h-4 w-4 text-violet-400" />
          </div>
          <div>
            <h3 className="text-sm font-semibold">SAML 2.0</h3>
            <p className="text-[11px] text-muted-foreground/60">
              Anmeldung über den eigenen Identitätsanbieter (ADFS, Entra ID, Keycloak)
            </p>
          </div>
        </div>
        {configured ? (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/20 bg-emerald-500/10 px-2.5 py-1 text-[10px] font-medium text-emerald-400">
            <CheckCircle2 className="h-3 w-3" />
            Aktiv
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-zinc-500/20 bg-zinc-500/10 px-2.5 py-1 text-[10px] font-medium text-zinc-400">
            Nicht eingerichtet
          </span>
        )}
      </div>

      <div className="space-y-4 p-5">
        <div className="rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] p-3">
          <div className="text-[11px] font-medium">Schritt 1 — unsere Metadaten eintragen</div>
          <p className="mt-1 text-[11px] text-muted-foreground/60">
            Diese Adresse beim Identitätsanbieter als Dienstanbieter hinterlegen:
          </p>
          <a
            href={metadataUrl}
            target="_blank"
            rel="noreferrer"
            className="mt-1.5 inline-flex items-center gap-1.5 break-all text-[11px] text-primary hover:underline"
          >
            {metadataUrl}
            <ExternalLink className="h-3 w-3 shrink-0" />
          </a>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          {FIELDS.map((f) => (
            <div key={f.key}>
              <label className="text-[11px] font-medium">
                {f.label}
                {f.required && <span className="ml-1 text-red-400">*</span>}
              </label>
              <input
                value={values[f.key] ?? ""}
                onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}
                className="mt-1 w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-2 text-sm outline-none focus:border-primary/50"
              />
              <p className="mt-1 text-[10px] text-muted-foreground/50">{f.hint}</p>
            </div>
          ))}
        </div>

        <div>
          <label className="text-[11px] font-medium">
            Signatur-Zertifikat des Anbieters <span className="text-red-400">*</span>
          </label>
          <textarea
            value={cert}
            onChange={(e) => setCert(e.target.value)}
            rows={5}
            spellCheck={false}
            placeholder="MIIC..."
            className="mt-1 w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-2 font-mono text-[11px] outline-none focus:border-primary/50"
          />
          <p className="mt-1 text-[10px] text-muted-foreground/50">
            Damit wird jede Antwort geprüft. Ohne Zertifikat wird SAML nicht als
            Anmeldeweg angeboten.
          </p>
        </div>

        <div>
          <label className="text-[11px] font-medium">Gruppen auf Rollen abbilden (optional)</label>
          <textarea
            value={roleMap}
            onChange={(e) => setRoleMap(e.target.value)}
            rows={3}
            spellCheck={false}
            placeholder={'{"IT-Admins": "admin", "Teamleitung": "manager"}'}
            className="mt-1 w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-2 font-mono text-[11px] outline-none focus:border-primary/50"
          />
          <p className="mt-1 text-[10px] text-muted-foreground/50">
            Trifft mehr als eine Gruppe zu, gilt die höchste Rolle. Ohne Treffer bleibt
            die Rolle unverändert — eine leere Zuordnung nimmt also niemandem etwas weg.
          </p>
        </div>

        <button
          onClick={save}
          disabled={saving}
          className={cn(
            "inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground",
            saving && "opacity-40"
          )}
        >
          {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          Speichern
        </button>
      </div>
    </div>
  );
}
