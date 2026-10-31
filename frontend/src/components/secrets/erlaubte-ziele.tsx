"use client";

import { Globe } from "lucide-react";
import { cn } from "@/lib/utils";
import type { AgentSecretEntry } from "@/lib/api";

// Bekannte Ziele je Variable — nur als Vorschlag, wer den Key verwaltet, entscheidet.
const GITHUB_ZIELE = "api.github.com, github.com, uploads.github.com";
export const ZIEL_VORSCHLAEGE: Record<string, string> = {
  GITHUB_TOKEN: GITHUB_ZIELE,
  GH_TOKEN: GITHUB_ZIELE,
  GIT_PAT: GITHUB_ZIELE,
  GITLAB_TOKEN: "gitlab.com",
  NOTION_API_KEY: "api.notion.com",
  NOTION_TOKEN: "api.notion.com",
  SLACK_BOT_TOKEN: "slack.com",
  STRIPE_SECRET_KEY: "api.stripe.com",
  TAVILY_API_KEY: "api.tavily.com",
  BRAVE_API_KEY: "api.search.brave.com",
};

export function zieleListe(text: string): string[] {
  return text.split(/[\s,]+/).map(z => z.trim()).filter(Boolean);
}

/** Die Fehlermeldung des Servers (``detail``) statt eines allgemeinen Texts. */
export function fehlerText(e: unknown, ersatz: string): string {
  const m = e instanceof Error ? e.message.match(/"detail":"([^"]+)"/) : null;
  return m ? m[1] : ersatz;
}

/** Erlaubte Ziele eines Keys: gesetzt = Agent sieht nur einen Platzhalter (Egress-Proxy). */
export function ZieleFeld({ wert, keyName, onChange, kompakt = false }: {
  wert: string; keyName: string; onChange: (v: string) => void; kompakt?: boolean;
}) {
  const vorschlag = ZIEL_VORSCHLAEGE[keyName];
  return (
    <div className={cn("flex flex-col gap-1", !kompakt && "col-span-2")}>
      <label className={cn("font-medium", kompakt ? "text-[11px] text-muted-foreground" : "text-[11px] text-muted-foreground/70")}>
        Erlaubte Ziele (optional)
      </label>
      <input
        placeholder="z. B. api.github.com, github.com"
        value={wert}
        onChange={e => onChange(e.target.value)}
        className={cn("rounded-lg border font-mono",
          kompakt ? "border-foreground/[0.1] bg-background px-3 py-1.5 text-xs outline-none focus:border-primary/50"
                  : "border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm")}
      />
      <p className="text-[11px] leading-relaxed text-muted-foreground/60">
        Mit Zielen bekommt der Agent nur einen Platzhalter. Den echten Wert setzt der Egress-Proxy erst auf dem
        Weg zu diesen Adressen ein, an jedes andere Ziel geht der Key nicht. Leer = der Agent sieht den Wert.
        {vorschlag && !wert && (
          <button type="button" onClick={() => onChange(vorschlag)} className="ml-1 text-primary hover:underline">
            Vorschlag übernehmen: {vorschlag}
          </button>
        )}
      </p>
    </div>
  );
}

/** Abzeichen „Nur an …“: der Agent sieht diesen Key nur als Platzhalter. */
export function ZieleAbzeichen({ secret, klein = false }: { secret: AgentSecretEntry; klein?: boolean }) {
  if (!secret.ueber_proxy) return null;
  const ziele = secret.erlaubte_hosts ?? [];
  return (
    <span
      title={`Agenten sehen nur einen Platzhalter. Erlaubte Ziele: ${ziele.join(", ")}`}
      className={cn("inline-flex items-center gap-1 rounded-full border font-medium bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border-emerald-500/20",
        klein ? "px-1.5 py-0.5 text-[10px]" : "px-2.5 py-1 text-[11px]")}
    >
      <Globe size={klein ? 10 : 11} /> Nur an {ziele.length === 1 ? ziele[0] : `${ziele.length} Ziele`}
    </span>
  );
}
