"use client";

import { useEffect, useMemo, useState } from "react";
import { Search, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  AgentAvatar,
  AVATAR_ICONS,
  AVATAR_COLORS,
  HEX_RE,
  isCustomColor,
} from "./agent-avatar";

/**
 * Symbol- und Farbwahl (#523) — an EINER Stelle, benutzt beim Anlegen und beim
 * Bearbeiten.
 *
 * Vorher stand dieselbe Kachelreihe zweimal im Code, einmal im Anlege-Fenster und
 * einmal auf der Agentenseite. Jede Erweiterung hätte man doppelt bauen müssen,
 * und die freie Auswahl ist genau so eine Erweiterung.
 *
 * Der kuratierte Satz bleibt oben stehen und ohne Nachladen sichtbar — er deckt den
 * Normalfall ab. Wer sucht, bekommt den ganzen lucide-Satz; **erst dann** wird das
 * Bündel mit allen Sinnbildern geholt.
 */

type Value = { icon: string; color: string };

export function AppearancePicker({
  value,
  onChange,
  compact = false,
}: {
  value: Value;
  onChange: (next: Value) => void;
  compact?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [catalog, setCatalog] = useState<{
    names: string[];
    Icon: React.ComponentType<{ name: string; className?: string }>;
    search: (q: string, limit?: number) => string[];
  } | null>(null);
  const [loadingCatalog, setLoadingCatalog] = useState(false);

  // Der volle Satz kommt erst, wenn jemand sucht — oder wenn das aktuelle Sinnbild
  // gar nicht im kuratierten Satz steht, denn dann muss die Vorschau ihn zeigen.
  const needsCatalog = query.trim().length > 0 || (!!value.icon && !AVATAR_ICONS[value.icon]);

  useEffect(() => {
    if (!needsCatalog || catalog || loadingCatalog) return;
    setLoadingCatalog(true);
    import("./lucide-catalog")
      .then((m) =>
        setCatalog({ names: m.ALL_ICON_NAMES, Icon: m.CatalogIcon, search: m.searchIcons }),
      )
      .finally(() => setLoadingCatalog(false));
  }, [needsCatalog, catalog, loadingCatalog]);

  const results = useMemo(() => {
    if (!query.trim()) return Object.keys(AVATAR_ICONS);
    if (!catalog) return [];
    return catalog.search(query, compact ? 60 : 120);
  }, [query, catalog, compact]);

  const tile = compact ? "h-7 w-7" : "h-8 w-8";
  const glyph = compact ? "h-3.5 w-3.5" : "h-4 w-4";

  const customColor = isCustomColor(value.color) ? value.color : "#4f46e5";

  return (
    <div className="space-y-2.5">
      <div className="flex items-start gap-3">
        <AgentAvatar config={{ avatar: value }} size={compact ? "md" : "lg"} />
        <div className="min-w-0 flex-1">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground/50" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Symbol suchen — z. B. „truck“, „heart“, „chart“"
              className="w-full rounded-lg border border-foreground/[0.1] bg-background/80 py-1.5 pl-8 pr-3 text-xs outline-none transition-all focus:border-primary/50"
            />
            {loadingCatalog && (
              <Loader2 className="absolute right-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 animate-spin text-muted-foreground/50" />
            )}
          </div>
          <div
            className={cn(
              "mt-2 flex flex-wrap gap-1.5",
              query.trim() && "max-h-40 overflow-y-auto pr-1",
            )}
          >
            {results.map((name) => {
              const Curated = AVATAR_ICONS[name];
              const CatalogIcon = catalog?.Icon;
              return (
                <button
                  key={name}
                  type="button"
                  onClick={() => onChange({ ...value, icon: name })}
                  title={name}
                  className={cn(
                    "flex items-center justify-center rounded-md border transition-colors",
                    tile,
                    value.icon === name
                      ? "border-primary/50 bg-primary/10"
                      : "border-transparent hover:bg-foreground/[0.06]",
                  )}
                >
                  {Curated ? (
                    <Curated className={glyph} />
                  ) : CatalogIcon ? (
                    <CatalogIcon name={name} className={glyph} />
                  ) : null}
                </button>
              );
            })}
            {query.trim() && catalog && results.length === 0 && (
              <span className="py-2 text-[11px] text-muted-foreground/60">
                Kein Symbol mit diesem Namen. lucide benennt englisch — „lkw“ findet
                nichts, „truck“ schon.
              </span>
            )}
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        {Object.entries(AVATAR_COLORS).map(([n, c]) => (
          <button
            key={n}
            type="button"
            onClick={() => onChange({ ...value, color: n })}
            title={n}
            className={cn(
              "h-5 w-5 rounded-full transition-all",
              c.dot,
              value.color === n ? "ring-2 ring-offset-2 ring-offset-background ring-foreground/40" : "",
            )}
          />
        ))}

        {/* Freie Farbe. Der Farbwähler des Browsers liefert immer sechs Stellen —
            genau die Form, die der Server annimmt. */}
        <label
          title="Eigene Farbe"
          className={cn(
            "relative ml-1 flex h-5 w-5 cursor-pointer items-center justify-center rounded-full transition-all",
            isCustomColor(value.color)
              ? "ring-2 ring-offset-2 ring-offset-background ring-foreground/40"
              : "",
          )}
          style={{
            background: isCustomColor(value.color)
              ? (value.color as string)
              : "conic-gradient(#ef4444,#eab308,#22c55e,#06b6d4,#6366f1,#d946ef,#ef4444)",
          }}
        >
          <input
            type="color"
            value={customColor}
            onChange={(e) => onChange({ ...value, color: e.target.value.toLowerCase() })}
            className="absolute inset-0 cursor-pointer opacity-0"
          />
        </label>

        <input
          value={isCustomColor(value.color) ? value.color : ""}
          onChange={(e) => {
            const v = e.target.value.trim().toLowerCase();
            if (HEX_RE.test(v)) onChange({ ...value, color: v });
          }}
          placeholder="#4f46e5"
          spellCheck={false}
          className="w-[5.5rem] rounded-md border border-foreground/[0.1] bg-background/80 px-2 py-1 font-mono text-[11px] outline-none focus:border-primary/50"
        />
      </div>
    </div>
  );
}
