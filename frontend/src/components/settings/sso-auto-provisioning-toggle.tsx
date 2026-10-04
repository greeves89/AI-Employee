"use client";

import { useEffect, useState } from "react";
import { KeyRound, Loader2 } from "lucide-react";
import * as api from "@/lib/api";
import { cn } from "@/lib/utils";
import { useToast } from "@/components/ui/dialog-provider";

/**
 * Konten bei der ersten SSO-Anmeldung automatisch anlegen (#914).
 *
 * Getrennt vom Schalter „Registrierung“: der gilt nur noch für die
 * Passwort-Selbstregistrierung auf der Anmeldeseite. Vorher sperrte, wer die
 * Registrierung schloss, auch jede neue Microsoft-Anmeldung aus.
 * Speichert sofort — wie die übrigen Sicherheitsschalter.
 */
export function SsoAutoProvisioningToggle() {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [saving, setSaving] = useState(false);
  const toast = useToast();

  useEffect(() => {
    api.getSettings()
      .then((s) => setEnabled(s.sso_auto_provisioning ?? true))
      .catch(() => setEnabled(null));
  }, []);

  const toggle = async () => {
    if (enabled === null) return;
    setSaving(true);
    try {
      await api.updateSettings({ sso_auto_provisioning: !enabled });
      setEnabled(!enabled);
    } catch (e) {
      toast.error("Konnte die SSO-Kontoanlage nicht ändern", e instanceof Error ? e.message : undefined);
    } finally {
      setSaving(false);
    }
  };

  if (enabled === null) return null;

  return (
    <div className="mt-4 flex items-center justify-between gap-3 border-t border-foreground/[0.04] pt-4">
      <div className="flex items-center gap-3">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-500/10 border border-blue-500/20">
          <KeyRound className="h-4 w-4 text-blue-400" />
        </div>
        <div>
          <p className="text-sm font-semibold">Konten bei SSO-Anmeldung anlegen</p>
          <p className="text-[11px] text-muted-foreground/60">
            {enabled
              ? "Wer sich zum ersten Mal per SSO anmeldet, bekommt ein Konto — ohne Rolle bzw. mit der Rolle aus den SSO-Gruppen."
              : "Per SSO kommen nur Personen herein, für die bereits ein Konto besteht."}
          </p>
        </div>
      </div>
      <button
        onClick={toggle}
        disabled={saving}
        aria-label="Konten bei SSO-Anmeldung anlegen"
        className={cn(
          "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors",
          enabled ? "bg-emerald-500" : "bg-zinc-600",
          saving && "opacity-40 cursor-not-allowed",
        )}
      >
        {saving ? (
          <Loader2 className="mx-auto h-3 w-3 animate-spin text-white" />
        ) : (
          <span
            className={cn(
              "inline-block h-4 w-4 transform rounded-full bg-white transition-transform",
              enabled ? "translate-x-6" : "translate-x-1",
            )}
          />
        )}
      </button>
    </div>
  );
}
