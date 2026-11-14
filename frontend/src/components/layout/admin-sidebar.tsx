"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  Shield,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { ADMIN_SEITEN_SYMBOLE } from "@/lib/admin-symbole";
import { useSidebarCollapsed } from "@/hooks/use-sidebar";
import {
  ADMIN_GRUPPEN,
  adminBereichAusTab,
  adminPfad,
  adminSeite,
} from "@/lib/admin-bereiche";


/** Leiste des Admin-Bereichs (Redesign 2026-10).
 *
 *  Admins arbeiten in derselben Ansicht wie alle anderen. Was der ganzen Anlage
 *  gehört, verwalten sie hier — in einem eigenen Bereich mit heller Leiste und
 *  „Admin“-Marke, damit jederzeit sichtbar ist, dass eine Änderung alle betrifft.
 *  Die Bereiche selbst kommen aus ``lib/admin-bereiche.ts``. */
export function AdminSidebar() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { collapsed, toggle, mobileOpen, setMobileOpen } = useSidebarCollapsed();
  const aktuell = pathname === "/admin" ? adminBereichAusTab(searchParams.get("tab")).bereich : null;
  const schliessen = () => setMobileOpen(false);

  return (
    <aside
      aria-label="Admin-Bereich"
      className={cn(
        "fixed left-0 top-0 z-40 flex h-screen flex-col border-r border-border bg-card transition-all duration-300",
        collapsed ? "lg:w-[64px]" : "lg:w-[240px]",
        "w-[240px]",
        mobileOpen ? "translate-x-0" : "-translate-x-full",
        "lg:translate-x-0"
      )}
    >
      <div className={cn("flex h-14 shrink-0 items-center", collapsed ? "lg:justify-center lg:px-0 gap-2 px-4" : "gap-2 px-4")}>
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-amber-500/15">
          <Shield className="h-4 w-4 text-amber-500" />
        </div>
        <span className={cn("text-sm font-semibold", collapsed && "lg:hidden")}>AI Employee</span>
        <span className={cn("ml-auto rounded-md bg-amber-500/15 px-2 py-0.5 text-[11px] font-bold uppercase tracking-[0.06em] text-amber-700 dark:text-amber-400", collapsed && "lg:hidden")}>
          Admin
        </span>
      </div>

      {/* Acht Seiten, eine Ebene (14.11.2026). Die Unterseiten stehen als Reiter oben
          auf der Seite (``app/admin/page.tsx``). */}
      <nav className="flex-1 overflow-y-auto px-2 py-2 scrollbar-thin" aria-label="Bereiche des Admin-Bereichs">
        <ul className="space-y-0.5">
          {ADMIN_GRUPPEN.map((g) => {
            const Icon = ADMIN_SEITEN_SYMBOLE[g.id];
            const aktiv = aktuell !== null && adminSeite(aktuell).id === g.id;
            return (
              <li key={g.id}>
                <Link
                  href={adminPfad(g.bereiche[0])}
                  onClick={schliessen}
                  title={g.titel}
                  aria-current={aktiv ? "page" : undefined}
                  className={cn(
                    "flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[14px] transition-colors",
                    collapsed && "lg:justify-center lg:px-0 lg:h-9 lg:w-9 lg:mx-auto",
                    aktiv
                      ? "bg-primary/10 font-medium text-foreground"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground"
                  )}
                >
                  <Icon className={cn("h-4 w-4 shrink-0", aktiv && "text-primary")} />
                  <span className={cn("truncate", collapsed && "lg:hidden")}>{g.titel}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      <div className="shrink-0 border-t border-border p-2">
        <Link
          href="/heute"
          onClick={schliessen}
          title="Zurück zur Arbeit"
          className={cn(
            "flex items-center justify-center gap-2 rounded-lg bg-foreground py-2 text-[13px] font-medium text-background transition-opacity hover:opacity-90",
            collapsed ? "lg:mx-auto lg:h-9 lg:w-9 lg:px-0 px-3" : "px-3"
          )}
        >
          <ArrowLeft className="h-4 w-4 shrink-0" />
          <span className={cn(collapsed && "lg:hidden")}>Zurück zur Arbeit</span>
        </Link>
      </div>

      <button
        onClick={toggle}
        className="absolute -right-3 top-[54px] z-50 hidden h-6 w-6 items-center justify-center rounded-full border border-border bg-card text-muted-foreground shadow-md transition-all hover:scale-110 hover:text-foreground lg:flex"
        title={collapsed ? "Seitenleiste ausklappen" : "Seitenleiste einklappen"}
      >
        {collapsed ? <ChevronRight className="h-3 w-3" /> : <ChevronLeft className="h-3 w-3" />}
      </button>
    </aside>
  );
}
