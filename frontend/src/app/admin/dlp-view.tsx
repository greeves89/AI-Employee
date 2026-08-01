"use client";

import { useCallback, useEffect, useState } from "react";
import { ShieldCheck, Loader2, FlaskConical, ScrollText } from "lucide-react";
import { cn } from "@/lib/utils";
import { useToast } from "@/components/ui/dialog-provider";
import * as api from "@/lib/api";

const CLASS_LABEL: Record<string, string> = {
  secret: "Secrets / Keys",
  iban: "IBAN",
  credit_card: "Kreditkarte",
  email: "E-Mail-Adresse",
  de_tax_id: "Steuer-ID (11-stellig)",
};
const ACTION_LABEL: Record<string, string> = {
  allow: "Erlauben", log: "Nur loggen", mask: "Maskieren", block: "Blockieren",
};
const ACTION_COLOR: Record<string, string> = {
  allow: "text-muted-foreground", log: "text-sky-400", mask: "text-amber-400", block: "text-red-400",
};

export function DlpView({ embedded = false }: { embedded?: boolean }) {
  const toast = useToast();
  const [loading, setLoading] = useState(true);
  const [settings, setSettings] = useState<api.DlpSettings | null>(null);
  const [rules, setRules] = useState<api.DlpRule[]>([]);
  const [audit, setAudit] = useState<api.DlpAuditEvent[]>([]);
  const [saving, setSaving] = useState(false);
  const [testText, setTestText] = useState("");
  const [testResult, setTestResult] = useState<Record<string, number> | null>(null);

  const load = useCallback(async () => {
    try {
      const [s, r, a] = await Promise.all([
        api.getDlpSettings(),
        api.getDlpRules(),
        api.getDlpAudit(50).catch(() => ({ events: [] })),
      ]);
      setSettings(s);
      setRules(r.rules);
      setAudit(a.events);
    } catch {
      toast.error("DLP-Konfiguration konnte nicht geladen werden.");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { load(); }, [load]);

  const toggleEnabled = async () => {
    if (!settings) return;
    setSaving(true);
    try {
      const res = await api.setDlpEnabled(!settings.enabled);
      setSettings({ ...settings, enabled: res.enabled });
      toast.success(res.enabled ? "DLP-Filter aktiviert." : "DLP-Filter deaktiviert.");
    } catch {
      toast.error("Konnte den DLP-Filter nicht umschalten.");
    } finally {
      setSaving(false);
    }
  };

  // Global rule (agent_id === null) per class, defaulting so every class has a row.
  const globalRules = settings?.classes.map((cls) => {
    const existing = rules.find((r) => r.pii_class === cls && r.agent_id === null);
    return existing ?? { id: -1, pii_class: cls, agent_id: null, action: "", enabled: true };
  }) ?? [];

  const setAction = async (cls: string, action: string) => {
    try {
      const saved = await api.upsertDlpRule({ pii_class: cls, action, agent_id: null, enabled: true });
      setRules((prev) => {
        const rest = prev.filter((r) => !(r.pii_class === cls && r.agent_id === null));
        return [...rest, saved];
      });
    } catch {
      toast.error("Regel konnte nicht gespeichert werden.");
    }
  };

  const runTest = async () => {
    if (!testText.trim()) return;
    try {
      const r = await api.testDlpScan(testText);
      setTestResult(r.classes);
    } catch {
      toast.error("Test-Scan fehlgeschlagen.");
    }
  };

  if (loading) {
    return <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /> Lade DLP-Konfiguration…</div>;
  }

  return (
    <div className={cn("space-y-5", !embedded && "p-6")}>
      <div className="flex items-start gap-3">
        <ShieldCheck className="mt-0.5 h-5 w-5 text-emerald-400" />
        <div className="flex-1">
          <h3 className="text-sm font-semibold">DLP-Egress-Filter</h3>
          <p className="mt-1 text-xs text-muted-foreground/70">
            Scannt ausgehenden Agent-Text (Telegram, Benachrichtigungen) auf PII/Secrets und blockiert bzw. maskiert vor dem Versand. Opt-in.
          </p>
        </div>
        <button
          onClick={toggleEnabled}
          disabled={saving}
          className={cn(
            "relative h-6 w-11 shrink-0 rounded-full transition-colors",
            settings?.enabled ? "bg-emerald-500" : "bg-foreground/15",
          )}
          title={settings?.enabled ? "Aktiviert" : "Deaktiviert"}
        >
          <span className={cn(
            "absolute top-0.5 h-5 w-5 rounded-full bg-white transition-transform",
            settings?.enabled ? "translate-x-5" : "translate-x-0.5",
          )} />
        </button>
      </div>

      {/* Global rules per class */}
      <div className="rounded-xl border border-foreground/[0.06] bg-card/60 p-4">
        <p className="mb-3 text-[11px] font-medium uppercase tracking-wide text-muted-foreground/60">Aktion pro Datenklasse (global)</p>
        <div className="space-y-2">
          {globalRules.map((r) => (
            <div key={r.pii_class} className="flex items-center gap-3">
              <span className="w-44 shrink-0 text-[13px]">{CLASS_LABEL[r.pii_class] ?? r.pii_class}</span>
              <select
                value={r.action || ""}
                onChange={(e) => setAction(r.pii_class, e.target.value)}
                className={cn(
                  "rounded-lg border border-foreground/[0.1] bg-foreground/[0.03] px-2.5 py-1 text-[12px] focus:border-primary/40 focus:outline-none",
                  ACTION_COLOR[r.action] ?? "text-muted-foreground",
                )}
              >
                {r.action === "" && <option value="">— Standard —</option>}
                {(settings?.actions ?? ["allow", "log", "mask", "block"]).map((a) => (
                  <option key={a} value={a}>{ACTION_LABEL[a] ?? a}</option>
                ))}
              </select>
            </div>
          ))}
        </div>
        <p className="mt-3 text-[11px] text-muted-foreground/50">Standard: Secrets → blockieren · IBAN/Kreditkarte → maskieren · Steuer-ID → loggen · E-Mail → erlauben.</p>
      </div>

      {/* Test scan */}
      <div className="rounded-xl border border-foreground/[0.06] bg-card/60 p-4">
        <p className="mb-2 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground/60"><FlaskConical className="h-3 w-3" /> Test-Scan</p>
        <textarea
          value={testText}
          onChange={(e) => setTestText(e.target.value)}
          placeholder="Beispieltext einfügen, um zu sehen, welche Klassen erkannt werden…"
          className="h-20 w-full resize-none rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] p-2.5 text-[12px] focus:border-primary/30 focus:outline-none"
        />
        <div className="mt-2 flex items-center gap-3">
          <button onClick={runTest} className="rounded-lg bg-foreground/[0.06] px-3 py-1.5 text-[12px] font-medium hover:bg-foreground/[0.1] transition-colors">Scannen</button>
          {testResult && (
            <span className="text-[12px] text-muted-foreground/70">
              {Object.keys(testResult).length === 0 ? "Keine sensiblen Daten erkannt." :
                Object.entries(testResult).map(([c, n]) => `${CLASS_LABEL[c] ?? c} (${n})`).join(" · ")}
            </span>
          )}
        </div>
      </div>

      {/* Recent audit */}
      <div className="rounded-xl border border-foreground/[0.06] bg-card/60 p-4">
        <p className="mb-3 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground/60"><ScrollText className="h-3 w-3" /> Letzte DLP-Treffer ({audit.length})</p>
        {audit.length === 0 ? (
          <p className="text-[12px] text-muted-foreground/50">Keine DLP-Treffer aufgezeichnet.</p>
        ) : (
          <div className="space-y-1">
            {audit.slice(0, 20).map((e) => {
              const classes = (e.meta?.classes ?? {}) as Record<string, number>;
              return (
                <div key={e.id} className="flex items-center gap-2 text-[12px]">
                  <span className={cn("w-16 shrink-0 text-[11px]",
                    e.event_type === "dlp_blocked" ? "text-red-400" : e.event_type === "dlp_masked" ? "text-amber-400" : "text-sky-400")}>
                    {e.event_type.replace("dlp_", "")}
                  </span>
                  <span className="w-20 shrink-0 text-muted-foreground/50">{e.channel}</span>
                  <span className="truncate text-muted-foreground/70">{Object.keys(classes).map((c) => CLASS_LABEL[c] ?? c).join(", ")}</span>
                  <span className="ml-auto shrink-0 text-[10px] text-muted-foreground/40">{e.created_at ? new Date(e.created_at).toLocaleString() : ""}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
