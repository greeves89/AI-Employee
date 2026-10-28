"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import {
  AppWindow,
  ArrowLeft,
  Brain,
  ChevronLeft,
  ChevronRight,
  Cpu,
  DollarSign,
  HeartPulse,
  KeyRound,
  Layers,
  Lock,
  MessageSquare,
  Mic,
  Plug,
  Scale,
  ScrollText,
  Search,
  Shield,
  ShieldAlert,
  UserCog,
  Users,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useSidebarCollapsed } from "@/hooks/use-sidebar";
import {
  ADMIN_BEREICH_NAMEN,
  ADMIN_GRUPPEN,
  adminBereichAusTab,
  adminPfad,
  type AdminBereich,
} from "@/lib/admin-bereiche";

const SYMBOLE: Record<AdminBereich, React.ElementType> = {
  users: Users,
  roles: Shield,
  "sso-groups": KeyRound,
  agents: Cpu,
  assignments: UserCog,
  vorlagen: Layers,
  modelle: Brain,
  "ai-accounts": Cpu,
  sprache: Mic,
  "second-brains": Brain,
  "web-search": Search,
  "integrationen-anlage": Plug,
  system: Lock,
  pages: AppWindow,
  "master-rules": ShieldAlert,
  secrets: KeyRound,
  dlp: Shield,
  audit: ScrollText,
  gesetze: Scale,
  health: HeartPulse,
  budget: DollarSign,
  feedback: MessageSquare,
};

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
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[9px] bg-sidebar">
          <Shield className="h-4 w-4 text-brand" />
        </div>
        <span className={cn("font-[family-name:var(--font-display)] text-[15px] font-semibold", collapsed && "lg:hidden")}>AI Employee</span>
        <span className={cn("ml-auto rounded-md bg-primary/10 px-2 py-0.5 text-[11px] font-bold uppercase tracking-[0.06em] text-primary", collapsed && "lg:hidden")}>
          Admin
        </span>
      </div>

      <nav className="flex-1 space-y-3 overflow-y-auto px-2 py-2 scrollbar-thin" aria-label="Bereiche des Admin-Bereichs">
        {ADMIN_GRUPPEN.map((g) => (
          <div key={g.id}>
            <h2 className={cn("px-2.5 pb-1 text-[11px] font-medium uppercase tracking-[0.06em] text-muted-foreground", collapsed && "lg:hidden")}>
              {g.titel}
            </h2>
            <ul className="space-y-px">
              {g.bereiche.map((id) => {
                const Icon = SYMBOLE[id];
                const aktiv = aktuell === id;
                return (
                  <li key={id}>
                    <Link
                      href={adminPfad(id)}
                      onClick={schliessen}
                      title={ADMIN_BEREICH_NAMEN[id]}
                      aria-current={aktiv ? "page" : undefined}
                      className={cn(
                        "flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-[14px] transition-colors",
                        collapsed && "lg:justify-center lg:px-0 lg:h-9 lg:w-9 lg:mx-auto",
                        aktiv
                          ? "bg-primary/10 font-medium text-foreground"
                          : "text-muted-foreground hover:bg-muted hover:text-foreground"
                      )}
                    >
                      <Icon className={cn("h-4 w-4 shrink-0", aktiv && "text-primary")} />
                      <span className={cn("truncate", collapsed && "lg:hidden")}>{ADMIN_BEREICH_NAMEN[id]}</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      <div className="shrink-0 border-t border-border p-2">
        <Link
          href="/heute"
          onClick={schliessen}
          title="Zurück zur Arbeit"
          className={cn(
            "flex items-center justify-center gap-2 rounded-lg bg-sidebar py-2 text-[13px] font-medium text-sidebar-foreground transition-opacity hover:opacity-90",
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
