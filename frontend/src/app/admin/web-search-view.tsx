"use client";

import { useCallback, useEffect, useState } from "react";
import { Search, Loader2, KeyRound } from "lucide-react";
import { cn } from "@/lib/utils";
import { useToast } from "@/components/ui/dialog-provider";
import * as api from "@/lib/api";

type Provider = "duckduckgo" | "brave" | "serp";

const PROVIDER_LABEL: Record<Provider, string> = {
  duckduckgo: "DuckDuckGo (integriert)",
  brave: "Brave Search API",
  serp: "SerpApi (Google)",
};
const PROVIDER_HINT: Record<Provider, string> = {
  duckduckgo: "Ohne API-Key, sofort einsatzbereit — Standard für alle Agenten und die Sprachfront.",
  brave: "Braucht einen API-Key von api.search.brave.com.",
  serp: "Braucht einen API-Key von serpapi.com.",
};

export function WebSearchView({ embedded = false }: { embedded?: boolean }) {
  const toast = useToast();
  const [loading, setLoading] = useState(true);
  const [provider, setProvider] = useState<Provider>("duckduckgo");
  const [hasKey, setHasKey] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const s = await api.getSettings();
      setProvider((s.web_search_provider as Provider) || "duckduckgo");
      setHasKey(!!s.has_web_search_api_key);
    } catch {
      toast.error("Websuche-Einstellungen konnten nicht geladen werden.");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { load(); }, [load]);

  const save = async () => {
    setSaving(true);
    try {
      const data: Record<string, unknown> = { web_search_provider: provider };
      if (apiKey.trim()) data.web_search_api_key = apiKey.trim();
      await api.updateSettings(data);
      if (apiKey.trim()) setHasKey(true);
      setApiKey("");
      toast.success("Websuche-Einstellungen gespeichert.");
    } catch {
      toast.error("Konnte die Websuche-Einstellungen nicht speichern.");
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /> Lade Websuche-Einstellungen…</div>;
  }

  const needsKey = provider !== "duckduckgo";

  return (
    <div className={cn("space-y-5", !embedded && "p-6")}>
      <div className="flex items-start gap-3">
        <Search className="mt-0.5 h-5 w-5 text-sky-400" />
        <div className="flex-1">
          <h3 className="text-sm font-semibold">Websuche</h3>
          <p className="mt-1 text-xs text-muted-foreground/70">
            Welcher Suchanbieter für alle Agenten (Claude Code, Codex, Custom-LLM) und die Sprachfront
            genutzt wird. Gilt platformweit, wie bei OpenWebUI.
          </p>
        </div>
      </div>

      <div className="rounded-xl border border-foreground/[0.06] bg-card/60 p-4 space-y-4">
        <div>
          <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground/60">Anbieter</p>
          <div className="space-y-2">
            {(Object.keys(PROVIDER_LABEL) as Provider[]).map((p) => (
              <label
                key={p}
                className={cn(
                  "flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2.5 transition-colors",
                  provider === p
                    ? "border-primary/40 bg-primary/[0.06]"
                    : "border-foreground/[0.08] hover:bg-foreground/[0.03]",
                )}
              >
                <input
                  type="radio"
                  name="web_search_provider"
                  checked={provider === p}
                  onChange={() => setProvider(p)}
                  className="mt-0.5"
                />
                <div>
                  <p className="text-[13px] font-medium">{PROVIDER_LABEL[p]}</p>
                  <p className="text-[11px] text-muted-foreground/60">{PROVIDER_HINT[p]}</p>
                </div>
              </label>
            ))}
          </div>
        </div>

        {needsKey && (
          <div>
            <p className="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground/60">
              <KeyRound className="h-3 w-3" /> API-Key
            </p>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={hasKey ? "•••••••••••••••• (hinterlegt — zum Ändern neu eingeben)" : "API-Key einfügen"}
              className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-2 text-[13px] focus:border-primary/30 focus:outline-none"
            />
            {!hasKey && !apiKey && (
              <p className="mt-1.5 text-[11px] text-amber-400/80">
                Kein Key hinterlegt — die Suche fällt bis dahin automatisch auf DuckDuckGo zurück.
              </p>
            )}
          </div>
        )}

        <button
          onClick={save}
          disabled={saving}
          className="rounded-lg bg-primary px-3.5 py-1.5 text-[12px] font-medium text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50"
        >
          {saving ? "Speichert…" : "Speichern"}
        </button>
      </div>
    </div>
  );
}
