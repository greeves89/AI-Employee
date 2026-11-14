"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Cpu, ListTodo, Menu, ShieldCheck, Sun } from "lucide-react";
import { cn } from "@/lib/utils";

/** Untere Leiste am Handy: die vier häufigsten Ziele plus „Mehr“ (öffnet das Menü).
 *  Das Menü oben links liegt für den Daumen am weitesten weg (Persona Mobil,
 *  Runde 4 bis 7). Dieselben Ziele wie oben in der Seitenleiste, keine eigenen. */
const ZIELE = [
  { href: "/heute", label: "Start", icon: Sun },
  { href: "/agents", label: "Agenten", icon: Cpu },
  { href: "/tasks", label: "Aufgaben", icon: ListTodo },
  { href: "/approvals", label: "Freigaben", icon: ShieldCheck },
] as const;

/** Wo die Leiste stört: im Chat eines Agenten (eigenes Eingabefeld unten) und im
 *  Admin-Bereich (eigene Navigation). */
export function handyLeisteSichtbar(pfad: string): boolean {
  if (pfad.startsWith("/admin")) return false;
  if (/^\/agents\/[^/]+/.test(pfad)) return false;
  if (pfad.startsWith("/chat")) return false;
  return true;
}

export function HandyLeiste({ onMehr }: { onMehr: () => void }) {
  const pfad = usePathname();
  if (!handyLeisteSichtbar(pfad)) return null;
  return (
    <nav
      aria-label="Schnellnavigation"
      className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 border-t border-border bg-background/95 pb-[env(safe-area-inset-bottom)] backdrop-blur-lg lg:hidden"
    >
      {ZIELE.map(({ href, label, icon: Icon }) => {
        const aktiv = pfad === href || pfad.startsWith(`${href}/`);
        return (
          <Link
            key={href}
            href={href}
            aria-current={aktiv ? "page" : undefined}
            className={cn(
              "flex h-16 flex-col items-center justify-center gap-1 text-[11px] font-medium",
              aktiv ? "text-primary" : "text-muted-foreground",
            )}
          >
            <Icon className="h-5 w-5" aria-hidden="true" />
            {label}
          </Link>
        );
      })}
      <button
        type="button"
        onClick={onMehr}
        className="flex h-16 flex-col items-center justify-center gap-1 text-[11px] font-medium text-muted-foreground"
      >
        <Menu className="h-5 w-5" aria-hidden="true" />
        Mehr
      </button>
    </nav>
  );
}
