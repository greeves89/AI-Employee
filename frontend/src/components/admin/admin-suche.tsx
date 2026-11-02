"use client";

/**
 * Einstieg in den Admin-Bereich: eine Suche über alle Bereiche und die
 * wichtigsten Stellen darin. „budget“ zeigt „Kosten & Budget“ und „Budget je
 * Nutzer“; ein Klick (oder Enter) führt direkt hin, auch an die Stelle auf der
 * Seite. Was gefunden wird, steht in ``lib/admin-bereiche.ts`` (ADMIN_SUCHZIELE).
 */

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, CornerDownLeft, Search } from "lucide-react";

import {
  ADMIN_BEREICH_NAMEN,
  ADMIN_GRUPPEN,
  adminPfad,
  adminSuche,
  adminZiel,
  type AdminSuchziel,
} from "@/lib/admin-bereiche";
import { ADMIN_SYMBOLE } from "@/lib/admin-symbole";
import { cn } from "@/lib/utils";

export function AdminSuche() {
  const router = useRouter();
  const [text, setText] = useState("");
  const [aktiv, setAktiv] = useState(0);
  const treffer = useMemo(() => adminSuche(text), [text]);

  const oeffnen = (ziel: AdminSuchziel) => router.push(adminZiel(ziel));

  const tasten = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!treffer.length) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setAktiv((i) => Math.min(i + 1, treffer.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setAktiv((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      oeffnen(treffer[Math.min(aktiv, treffer.length - 1)]);
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div className="flex items-center gap-3 rounded-2xl border border-foreground/[0.08] bg-card/80 px-4 py-3 shadow-sm focus-within:border-primary/40">
        <Search className="h-5 w-5 text-muted-foreground" />
        <input
          autoFocus
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setAktiv(0);
          }}
          onKeyDown={tasten}
          placeholder="Was möchtest du einstellen? Zum Beispiel „Budget“, „Lizenz“, „Passwort“ …"
          aria-label="Admin-Bereich durchsuchen"
          className="w-full bg-transparent text-base outline-none placeholder:text-muted-foreground/60"
        />
      </div>

      {text.trim() ? (
        treffer.length ? (
          <ul className="space-y-1.5" role="listbox" aria-label="Treffer">
            {treffer.map((ziel, i) => {
              const Icon = ADMIN_SYMBOLE[ziel.bereich];
              return (
                <li key={`${ziel.bereich}-${ziel.anker ?? ""}`}>
                  <button
                    role="option"
                    aria-selected={i === aktiv}
                    onClick={() => oeffnen(ziel)}
                    onMouseEnter={() => setAktiv(i)}
                    className={cn(
                      "flex w-full items-center gap-3 rounded-xl border px-4 py-3 text-left transition-colors",
                      i === aktiv
                        ? "border-primary/30 bg-primary/[0.06]"
                        : "border-foreground/[0.06] bg-card/60 hover:bg-foreground/[0.03]",
                    )}
                  >
                    <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-medium text-foreground">{ziel.titel}</span>
                      <span className="block truncate text-xs text-muted-foreground">
                        {ziel.anker ? `${ADMIN_BEREICH_NAMEN[ziel.bereich]} · ` : ""}
                        {ziel.beschreibung}
                      </span>
                    </span>
                    {i === aktiv ? (
                      <CornerDownLeft className="h-4 w-4 shrink-0 text-muted-foreground/60" />
                    ) : (
                      <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground/40" />
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="text-center text-sm text-muted-foreground">
            Nichts gefunden für „{text.trim()}“. Andere Begriffe probieren oder unten einen Bereich wählen.
          </p>
        )
      ) : null}

      {!text.trim() || !treffer.length ? (
        <div className="grid gap-4 sm:grid-cols-2">
          {ADMIN_GRUPPEN.filter((g) => g.id !== "start").map((gruppe) => (
            <div key={gruppe.id} className="rounded-xl border border-foreground/[0.06] bg-card/60 p-4">
              <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground/70">
                {gruppe.titel}
              </p>
              <div className="space-y-0.5">
                {gruppe.bereiche.map((bereich) => {
                  const Icon = ADMIN_SYMBOLE[bereich];
                  return (
                    <button
                      key={bereich}
                      onClick={() => router.push(adminPfad(bereich))}
                      className="flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left text-sm text-foreground/90 hover:bg-foreground/[0.04]"
                    >
                      <Icon className="h-4 w-4 text-muted-foreground" />
                      {ADMIN_BEREICH_NAMEN[bereich]}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
