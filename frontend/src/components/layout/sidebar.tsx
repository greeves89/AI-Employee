"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import {
  Activity,
  LayoutDashboard,
  Cpu,
  ListTodo,
  FolderOpen,
  Plug,
  Shield,
  ScrollText,
  Workflow,
  Bot,
  LifeBuoy,
  MessageSquarePlus,
  ShieldCheck,
  BookOpen,
  AppWindow,
  Sparkles,
  Zap,
  ClipboardCheck,
  Users,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  BarChart3,
  HelpCircle,
} from "lucide-react";
import { UpdateBanner } from "./update-banner";
import { UserMenu } from "./user-menu";
import { useAuthStore } from "@/lib/auth";
import { useSidebarCollapsed } from "@/hooks/use-sidebar";
import {
  getMyPermissions,
  getPendingApprovalCount,
  listMyCustomPages,
  type CustomPage,
  type RolePermissions,
} from "@/lib/api";
import { pageIcon } from "@/lib/page-icons";

type NavItem = {
  href: string;
  label: string;
  icon: React.ElementType;
  simpleVisible: boolean;
  /** Gesetzt bei selbst angelegten Menuepunkten der Art "Link": der Eintrag
   *  oeffnet die Adresse direkt in einem neuen Tab, statt erst unsere Seite zu
   *  laden, die nur einen Knopf dorthin zeigt. */
  external?: string;
};

/** Nur echte Webadressen taugen als Ziel eines Menuepunkts.
 *
 *  Alles andere — allen voran ``javascript:`` — waere fremder Code, der beim
 *  Klick in unserer eigenen Oberflaeche liefe, mit der Sitzung des Angemeldeten.
 *  Der Server prueft das bereits beim Anlegen und Aendern; hier steht die zweite
 *  Sperre fuer Eintraege, die auf anderem Weg in die Datenbank gelangt sind.
 *  Ein ungueltiger Eintrag verschwindet lieber, als still auf ``#`` zu zeigen —
 *  ein Menuepunkt, der nichts tut, sieht aus wie ein Fehler und wird gemeldet. */
function nurWebAdresse(url: string | undefined): string | undefined {
  if (!url) return undefined;
  return /^https?:\/\//i.test(url.trim()) ? url.trim() : undefined;
}

/** Ein Menuepunkt fuehrt entweder in die Anwendung (Next-Link) oder nach draussen
 *  (neuer Tab). Beide Darstellungen — eingeklappt und ausgeklappt — brauchen
 *  dieselbe Unterscheidung, deshalb steht sie hier an einer Stelle. */
function NavShell({
  item,
  className,
  title,
  onClick,
  children,
}: {
  item: NavItem;
  className: string;
  title?: string;
  onClick?: () => void;
  children: React.ReactNode;
}) {
  if (item.external) {
    // Zweites Schloss. Der Server laesst beim Anlegen und Aendern nur http/https
    // durch (custom_pages._validate_url) — aber Zeilen aus der Zeit davor oder
    // aus einem direkten Datenbankzugriff kaemen daran vorbei, und ein
    // ``javascript:``-Wert im ``href`` waere fremder Code in unserer Oberflaeche.
    return (
      <a
        href={item.external}
        target="_blank"
        rel="noopener noreferrer"
        title={title}
        onClick={onClick}
        className={className}
      >
        {children}
      </a>
    );
  }
  return (
    <Link href={item.href} title={title} onClick={onClick} className={className}>
      {children}
    </Link>
  );
}

type NavGroup = {
  label: string;
  key: string;
  items: NavItem[];
  adminOnly?: boolean;  // group only shown to admins
};

const navGroups: NavGroup[] = [
  {
    label: "Übersicht",
    key: "overview",
    items: [
      { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard, simpleVisible: true },
      { href: "/agents", label: "Agents", icon: Cpu, simpleVisible: true },
      // Onboarding vorerst ausgeblendet (Seite bleibt unter /onboarding erreichbar)
      // { href: "/onboarding", label: "Onboarding", icon: Rocket, simpleVisible: true },
      { href: "/tasks", label: "Tasks", icon: ListTodo, simpleVisible: true },
      { href: "/activity", label: "Activity", icon: Activity, simpleVisible: true },
      { href: "/analytics", label: "Analytics", icon: BarChart3, simpleVisible: true },
      { href: "/learning", label: "Gelerntes", icon: Sparkles, simpleVisible: true },
    ],
  },
  {
    label: "Zusammenarbeit",
    key: "collab",
    items: [
      { href: "/knowledge", label: "Knowledge", icon: BookOpen, simpleVisible: true },
      { href: "/meeting-rooms", label: "Meeting Rooms", icon: Users, simpleVisible: false },
      { href: "/apps", label: "Apps", icon: AppWindow, simpleVisible: true },
    ],
  },
  {
    label: "Automation",
    key: "automation",
    items: [
      { href: "/workflows", label: "Workflows", icon: Workflow, simpleVisible: false },
      { href: "/skills", label: "Skill Marketplace", icon: Sparkles, simpleVisible: false },
      { href: "/triggers", label: "Triggers", icon: Zap, simpleVisible: false },
      { href: "/evals", label: "Golden-Tests", icon: ClipboardCheck, simpleVisible: false },
    ],
  },
  {
    label: "System",
    key: "system",
    items: [
      { href: "/approvals", label: "Approvals", icon: ShieldCheck, simpleVisible: false },
      { href: "/files", label: "Explorer", icon: FolderOpen, simpleVisible: true },
      { href: "/integrations", label: "Integrations", icon: Plug, simpleVisible: false },
    ],
  },
  {
    label: "Hilfe",
    key: "help",
    items: [
      { href: "/help", label: "Hilfe & FAQ", icon: HelpCircle, simpleVisible: true },
    ],
  },
  {
    label: "Admin",
    key: "admin",
    adminOnly: true,
    items: [
      // Settings, AI-Accounts, Key Management, Health are tabs inside the
      // Admin-Konsole — one entry instead of six. Audit Log is surfaced directly
      // here for quick access to the compliance trail.
      { href: "/admin", label: "Admin-Konsole", icon: Shield, simpleVisible: false },
      { href: "/audit", label: "Audit Log", icon: ScrollText, simpleVisible: false },
    ],
  },
];

export function Sidebar() {
  const pathname = usePathname();
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === "admin";
  const { collapsed, toggle, mobileOpen, setMobileOpen } = useSidebarCollapsed();
  // The desktop icon-rail (collapsed) must NOT apply on mobile — there the sidebar is
  // an off-canvas drawer that always shows the full menu. Track the lg breakpoint.
  const [isDesktop, setIsDesktop] = useState(true);
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 1024px)");
    const update = () => setIsDesktop(mq.matches);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);
  const effectiveCollapsed = isDesktop && collapsed;
  const closeMobile = () => setMobileOpen(false);
  const [permissions, setPermissions] = useState<RolePermissions | null>(null);
  const [customPages, setCustomPages] = useState<CustomPage[]>([]);

  useEffect(() => {
    if (!user) {
      setPermissions(null);
      return;
    }
    getMyPermissions()
      .then((d) => setPermissions(d.permissions))
      .catch(() => setPermissions(null));
  }, [user?.id, user?.custom_role_id, user?.role]);

  // Vom Administrator angelegte Menuepunkte. Der Server liefert hier bereits nur,
  // was diese Rolle sehen darf — die Adresse einer fremden Seite soll niemand
  // bekommen, nur weil die Seitenleiste sie hinterher ausgeblendet haette.
  useEffect(() => {
    if (!user) {
      setCustomPages([]);
      return;
    }
    listMyCustomPages()
      .then((d) => setCustomPages(d.pages))
      .catch(() => setCustomPages([]));
  }, [user?.id, user?.custom_role_id, user?.role]);

  // Track which groups are open (all open by default)
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({
    overview: true,
    collab: true,
    automation: true,
    system: true,
  });

  const toggleGroup = (key: string) => {
    setOpenGroups((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  // Offene Freigaben als Abzeichen am Menuepunkt. Eigener Zaehl-Endpunkt statt der
  // vollen Liste: das hier fragt im Takt, und die Liste kann Hunderte Eintraege samt
  // Begruendungstexten haben.
  const [pendingApprovals, setPendingApprovals] = useState(0);
  useEffect(() => {
    let alive = true;
    const load = () =>
      getPendingApprovalCount()
        .then((n) => { if (alive) setPendingApprovals(n); })
        .catch(() => {});
    load();
    const timer = setInterval(load, 30000);
    return () => { alive = false; clearInterval(timer); };
  }, [pathname]);

  const canSeePath = (href: string) => {
    const allowed = permissions?.menu_paths;
    if (!allowed) return true;
    return allowed.some((path) => href === path || href.startsWith(`${path.replace(/\/$/, "")}/`));
  };

  // Angelegte Seiten in ihre Gruppe einsortieren. Sie stehen ans Ende der Gruppe,
  // damit die gewohnten Punkte ihren Platz behalten; untereinander entscheidet die
  // im Verwaltungsbereich vergebene Reihenfolge (der Server liefert schon sortiert).
  const extraItemsFor = (groupKey: string): NavItem[] =>
    customPages
      .filter((p) => p.group_key === groupKey)
      .map((p) => ({
        href: p.menu_path,
        label: p.title,
        icon: pageIcon(p.icon),
        simpleVisible: true,
        external: p.open_mode === "link" ? nurWebAdresse(p.url) : undefined,
      }));

  const visibleGroups = navGroups
    .filter((group) => !group.adminOnly || isAdmin)
    .map((group) => ({
      ...group,
      items: [...group.items, ...extraItemsFor(group.key)].filter((item) => canSeePath(item.href)),
    }))
    .filter((group) => group.items.length > 0);

  // In collapsed mode, show all visible items (groups are irrelevant)
  const allItems = visibleGroups.flatMap((g) => g.items);

  // Genauer Pfad oder ein Unterpfad davon — nicht blosses startsWith. Sonst
  // faerbte /p/kunde auch /p/kunden-portal mit ein, sobald zwei angelegte Seiten
  // mit demselben Wortanfang beginnen. Nach draussen fuehrende Punkte sind nie aktiv.
  const isItemActive = (item: NavItem) =>
    !item.external && (pathname === item.href || pathname.startsWith(`${item.href}/`));

  // Check if any item in a group is active
  const isGroupActive = (group: NavGroup) => group.items.some(isItemActive);

  return (
    <aside
      className={cn(
        "fixed left-0 top-0 z-40 h-screen border-r border-border bg-card/50 backdrop-blur-xl flex flex-col transition-all duration-300",
        effectiveCollapsed ? "w-[64px]" : "w-[260px]",
        // Mobile: off-canvas drawer (hidden unless opened). Desktop (lg+): always shown.
        mobileOpen ? "translate-x-0" : "-translate-x-full",
        "lg:translate-x-0"
      )}
    >
      {/* Logo */}
      <div className={cn(
        "flex h-14 items-center border-b border-border shrink-0",
        effectiveCollapsed ? "justify-center px-0" : "gap-3 px-5"
      )}>
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-blue-500 to-cyan-400 shadow-lg shadow-blue-500/20">
          <Bot className="h-4 w-4 text-white" />
        </div>
        {!effectiveCollapsed && (
          <>
            <div className="flex-1 min-w-0">
              <span className="text-sm font-semibold tracking-tight">AI Employee</span>
              <div className="flex items-center gap-1.5">
                <span className="relative flex h-1.5 w-1.5">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                  <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-500" />
                </span>
                <span className="text-[10px] text-muted-foreground">Online</span>
              </div>
            </div>
            {/* Concierge und Feedback wohnen hier oben als Paar — die frueheren
                schwebenden Knoepfe unten rechts haben Eingabefelder ueberdeckt. */}
            {isAdmin && (
              <button
                onClick={() => {
                  closeMobile();
                  window.dispatchEvent(new CustomEvent("concierge-widget:open"));
                }}
                className="flex h-7 w-7 items-center justify-center rounded-lg text-muted-foreground/50 hover:text-primary hover:bg-primary/10 transition-all"
                title="Concierge"
                aria-label="Concierge öffnen"
              >
                <LifeBuoy className="h-4 w-4" />
              </button>
            )}
            <button
              onClick={() => {
                // Startet den Widget-Flow (Element anpinnen) statt des alten Modals.
                closeMobile();
                window.dispatchEvent(new CustomEvent("feedback-widget:open"));
              }}
              className="flex h-7 w-7 items-center justify-center rounded-lg text-muted-foreground/50 hover:text-primary hover:bg-primary/10 transition-all"
              title="Feedback senden"
              aria-label="Feedback geben"
            >
              <MessageSquarePlus className="h-4 w-4" />
            </button>
          </>
        )}
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto py-3 px-2 space-y-0.5 scrollbar-thin">
        {effectiveCollapsed ? (
          // Collapsed: just icons
          allItems.map((item) => {
            const Icon = item.icon;
            const isActive = isItemActive(item);
            return (
              <NavShell
                key={item.href}
                item={item}
                title={item.label}
                onClick={closeMobile}
                className={cn(
                  "flex items-center justify-center h-9 w-9 mx-auto rounded-xl transition-all duration-150",
                  isActive
                    ? "bg-accent text-foreground shadow-sm"
                    : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
                )}
              >
                <span className="relative">
                  <Icon className={cn("h-4 w-4", isActive ? "text-primary" : "")} />
                  {/* Eingeklappt ist kein Platz fuer eine Zahl — der Punkt sagt
                      trotzdem, dass dort etwas wartet. */}
                  {item.href === "/approvals" && pendingApprovals > 0 && (
                    <span className="absolute -right-1 -top-1 h-2 w-2 rounded-full bg-amber-400" />
                  )}
                </span>
              </NavShell>
            );
          })
        ) : (
          // Expanded: grouped
          visibleGroups.map((group) => {
            const isOpen = openGroups[group.key] ?? true;
            const hasActive = isGroupActive(group);
            return (
              <div key={group.key} className="mb-1">
                <button
                  onClick={() => toggleGroup(group.key)}
                  className={cn(
                    "flex w-full items-center gap-2 px-3 py-1.5 rounded-lg transition-colors",
                    "text-[10px] font-semibold uppercase tracking-widest",
                    hasActive ? "text-primary/80" : "text-muted-foreground/50",
                    "hover:text-muted-foreground hover:bg-accent/30"
                  )}
                >
                  <span className="flex-1 text-left">{group.label}</span>
                  <ChevronDown
                    className={cn(
                      "h-3 w-3 transition-transform duration-200",
                      isOpen ? "rotate-0" : "-rotate-90"
                    )}
                  />
                </button>

                {isOpen && (
                  <div className="mt-0.5 space-y-0.5">
                    {group.items.map((item) => {
                      const Icon = item.icon;
                      const isActive = isItemActive(item);
                      return (
                        <NavShell
                          key={item.href}
                          item={item}
                          onClick={closeMobile}
                          className={cn(
                            "group flex items-center gap-3 rounded-xl px-3 py-2 text-[13px] font-medium transition-all duration-150",
                            isActive
                              ? "bg-accent text-foreground shadow-sm"
                              : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
                          )}
                        >
                          <Icon
                            className={cn(
                              "h-4 w-4 shrink-0 transition-colors",
                              isActive ? "text-primary" : "text-muted-foreground group-hover:text-foreground"
                            )}
                          />
                          <span className="truncate">{item.label}</span>
                          {item.href === "/approvals" && pendingApprovals > 0 ? (
                            <span
                              title={`${pendingApprovals} offene Freigabe(n)`}
                              className="ml-auto shrink-0 rounded-full bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-semibold tabular-nums text-amber-700 dark:text-amber-400"
                            >
                              {pendingApprovals > 99 ? "99+" : pendingApprovals}
                            </span>
                          ) : isActive ? (
                            <div className="ml-auto h-1.5 w-1.5 shrink-0 rounded-full bg-primary shadow-[0_0_6px_rgba(59,130,246,0.5)]" />
                          ) : null}
                        </NavShell>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })
        )}
      </nav>

      {/* Update Banner (only when expanded) */}
      {!effectiveCollapsed && <UpdateBanner />}

      {/* Bottom */}
      <div className={cn(
        "border-t border-border py-2 shrink-0 mt-auto",
        effectiveCollapsed ? "flex flex-col items-center gap-1 px-0 py-3" : "px-2 space-y-0.5"
      )}>
        {effectiveCollapsed ? (
          <>
            {isAdmin && (
              <Link
                href="/admin"
                title="Admin"
                className={cn(
                  "flex items-center justify-center h-9 w-9 rounded-xl transition-all",
                  pathname.startsWith("/admin")
                    ? "bg-accent text-amber-500"
                    : "text-muted-foreground hover:bg-accent/50 hover:text-amber-500"
                )}
              >
                <Shield className="h-4 w-4" />
              </Link>
            )}
            <UserMenu collapsed />
          </>
        ) : (
          <UserMenu />
        )}
      </div>

      {/* Collapse Toggle */}
      <button
        onClick={toggle}
        className={cn(
          "absolute -right-3 top-[54px] z-50 hidden h-6 w-6 items-center justify-center rounded-full border border-border bg-card shadow-md text-muted-foreground hover:text-foreground transition-all hover:scale-110 lg:flex"
        )}
        title={collapsed ? "Sidebar erweitern" : "Sidebar einklappen"}
      >
        {collapsed ? (
          <ChevronRight className="h-3 w-3" />
        ) : (
          <ChevronLeft className="h-3 w-3" />
        )}
      </button>
    </aside>
  );
}
