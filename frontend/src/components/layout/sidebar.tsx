"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { cn } from "@/lib/utils";
import {
  Activity,
  LayoutDashboard,
  Cpu,
  ListTodo,
  FolderKanban,
  Sun,
  Wallet,
  FolderOpen,
  Plug,
  Shield,
  Workflow,
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
  LayoutGrid,
} from "lucide-react";
import { UpdateBanner } from "./update-banner";
import { StopReasonAlert } from "./stop-reason-alert";
import { UserMenu } from "./user-menu";
import { useAuthStore } from "@/lib/auth";
import { useSidebarCollapsed } from "@/hooks/use-sidebar";
import { useSimpleMode } from "@/hooks/use-simple-mode";
import { useOftGenutzt } from "@/hooks/use-nutzung";
import {
  getMyPermissions,
  getPendingApprovalCount,
  getUiPreferences,
  listMyCustomPages,
  patchUiPreferences,
  type CustomPage,
  type RolePermissions,
} from "@/lib/api";
import { ALTE_SEITEN_GRUPPEN, pageIcon } from "@/lib/page-icons";
import { setVisibleInterval } from "@/lib/visible-interval";

type NavItem = {
  href: string;
  label: string;
  icon: React.ElementType;
  simpleVisible: boolean;
  /** Für Einsteiger zu viel (#1047): steht in der vollen Ansicht unter „Weitere
   *  Bereiche“ — außer jemand öffnet ihn oft, dann steht er bei ihm wieder oben. */
  weitere?: boolean;
  /** Gesetzt bei selbst angelegten Menuepunkten der Art "Link": der Eintrag
   *  öffnet die Adresse direkt in einem neuen Tab, statt erst unsere Seite zu
   *  laden, die nur einen Knopf dorthin zeigt. */
  external?: string;
  /** Erklärt einen Begriff, der als Seitentitel/Produktname feststeht und
   *  sich daher nicht einfach übersetzen lässt (#787 Punkt 10) — als native
   *  Tooltip, ohne den etablierten Namen umzubenennen. */
  tooltip?: string;
};

/** Nur echte Webadressen taugen als Ziel eines Menuepunkts.
 *
 *  Alles andere — allen voran ``javascript:`` — wäre fremder Code, der beim
 *  Klick in unserer eigenen Oberfläche liefe, mit der Sitzung des Angemeldeten.
 *  Der Server prueft das bereits beim Anlegen und Aendern; hier steht die zweite
 *  Sperre für Eintraege, die auf anderem Weg in die Datenbank gelangt sind.
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
    // ``javascript:``-Wert im ``href`` wäre fremder Code in unserer Oberfläche.
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

// Ein deutscher Name je Punkt — in der Mitglieder- wie in der Expertenansicht
// derselbe (#902). Früher gab es englische Namen plus ``simpleLabel``; wer
// zwischen den Ansichten wechselte, fand seine Punkte unter anderem Namen.

// Redesign 2026-10 (Vorbild lavalake-Konsole): drei Gruppen fürs Arbeiten. Was
// die ganze Anlage betrifft, steht NICHT hier, sondern im eigenen Admin-Bereich
// (``admin-sidebar.tsx``); Persönliches liegt im Konto-Menü unten.
const navGroups: NavGroup[] = [
  {
    label: "Arbeiten",
    key: "overview",
    items: [
      { href: "/heute", label: "Start", icon: Sun, simpleVisible: true },
      { href: "/agents", label: "Agenten", icon: Cpu, simpleVisible: true },
      { href: "/tasks", label: "Aufgaben", icon: ListTodo, simpleVisible: true },
      { href: "/projekte", label: "Projekte", icon: FolderKanban, simpleVisible: true },
      { href: "/approvals", label: "Freigaben", icon: ShieldCheck, simpleVisible: true },
      { href: "/dashboard", label: "Übersicht", icon: LayoutDashboard, simpleVisible: true, weitere: true },
      { href: "/finanzen", label: "Kosten", icon: Wallet, simpleVisible: true, weitere: true },
      { href: "/activity", label: "Aktivität", icon: Activity, simpleVisible: false, weitere: true },
      { href: "/analytics", label: "Auswertung", icon: BarChart3, simpleVisible: false, weitere: true },
      { href: "/learning", label: "Gelerntes", icon: Sparkles, simpleVisible: false, weitere: true },
    ],
  },
  {
    label: "Ablage",
    key: "collab",
    items: [
      { href: "/files", label: "Dateien", icon: FolderOpen, simpleVisible: true },
      { href: "/knowledge", label: "Wissen", icon: BookOpen, simpleVisible: false },
      { href: "/integrations", label: "Verbindungen", icon: Plug, simpleVisible: false, weitere: true },
    ],
  },
  {
    label: "Automatisieren",
    key: "automation",
    items: [
      { href: "/workflows", label: "Abläufe", icon: Workflow, simpleVisible: false, tooltip: "Abläufe (Workflows): feste Folgen aus mehreren Schritten, die automatisch nacheinander laufen." },
      { href: "/skills", label: "Skills", icon: Sparkles, simpleVisible: false },
      { href: "/apps", label: "Apps", icon: AppWindow, simpleVisible: false, weitere: true },
      { href: "/meeting-rooms", label: "Besprechungsräume", icon: Users, simpleVisible: false, weitere: true },
      { href: "/triggers", label: "Auslöser", icon: Zap, simpleVisible: false, weitere: true },
      { href: "/evals", label: "Prüfaufgaben", icon: ClipboardCheck, simpleVisible: false, weitere: true, tooltip: "Prüfaufgaben (Golden-Tests): bekannte Aufgaben, an denen sich vor einem Update zeigt, ob ein Agent sie noch richtig löst." },
    ],
  },
];

/** Alle festen Menüpunkte mit Gruppe — für die Nutzungsübersicht der
 *  Verwaltung (#1047), damit auch nie geöffnete Bereiche dort auftauchen. */
export const MENUE_PUNKTE = navGroups.flatMap((g) =>
  g.items.map((i) => ({ href: i.href, label: i.label, gruppe: g.label, weitere: !!i.weitere })),
);

export function Sidebar() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === "admin";
  const { simpleMode } = useSimpleMode();
  const oftGenutzt = useOftGenutzt();
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

  // „Weitere Bereiche“ offen oder zu — am Konto gemerkt, gilt auf jedem Gerät.
  const [weitereOffen, setWeitereOffen] = useState(false);
  useEffect(() => {
    if (!user) return;
    getUiPreferences()
      .then((p) => setWeitereOffen(Boolean((p.menue as { weitere_offen?: boolean } | undefined)?.weitere_offen)))
      .catch(() => {});
  }, [user?.id]);
  const weitereUmschalten = () => {
    const neu = !weitereOffen;
    setWeitereOffen(neu);
    patchUiPreferences({ menue: { weitere_offen: neu } }).catch(() => {});
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
    const stop = setVisibleInterval(load, 30000);
    return () => { alive = false; stop(); };
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
      .filter((p) => (ALTE_SEITEN_GRUPPEN[p.group_key] ?? p.group_key) === groupKey)
      .map((p) => ({
        href: p.menu_path,
        label: p.title,
        icon: pageIcon(p.icon),
        simpleVisible: true,
        external: p.open_mode === "link" ? nurWebAdresse(p.url) : undefined,
      }));

  // Mitglieder-Ansicht: nur die einfachen Punkte. Hat der
  // Administrator der Rolle ausdruecklich Menuepunkte zugeteilt, gilt seine
  // Liste — sonst verschwaende hier, was er bewusst freigegeben hat.
  const einfach = simpleMode && !permissions?.menu_paths;
  const erlaubteGruppen = navGroups
    .filter((group) => !group.adminOnly || isAdmin)
    .map((group) => ({
      ...group,
      items: [...group.items, ...extraItemsFor(group.key)]
        .filter((item) => canSeePath(item.href) && (!einfach || item.simpleVisible)),
    }));

  // Volle Ansicht, aufgeräumt (#1047): Selteneres wandert nach „Weitere
  // Bereiche“. Oben bleibt es, wenn man es oft öffnet oder gerade dort ist.
  const istHier = (href: string) => {
    const ziel = href.split("?")[0];
    return pathname === ziel || pathname.startsWith(`${ziel}/`);
  };
  const zurueckgestellt = (item: NavItem) =>
    !einfach && !!item.weitere && !oftGenutzt(item.href) && !istHier(item.href);
  const weitereItems = erlaubteGruppen.flatMap((g) => g.items.filter(zurueckgestellt));
  const visibleGroups = erlaubteGruppen
    .map((group) => ({ ...group, items: group.items.filter((item) => !zurueckgestellt(item)) }))
    .filter((group) => group.items.length > 0);

  // In collapsed mode, show all visible items (groups are irrelevant)
  const allItems = [...visibleGroups.flatMap((g) => g.items), ...(weitereOffen ? weitereItems : [])];

  // Genauer Pfad oder ein Unterpfad davon — nicht blosses startsWith. Sonst
  // faerbte /p/kunde auch /p/kunden-portal mit ein, sobald zwei angelegte Seiten
  // mit demselben Wortanfang beginnen. Nach draussen fuehrende Punkte sind nie aktiv.
  const passtQuery = (query: string) => {
    const params = new URLSearchParams(query);
    return Array.from(params.entries()).every(([k, v]) => searchParams.get(k) === v);
  };

  const isItemActive = (item: NavItem) => {
    if (item.external) return false;
    const [zielPfad, zielQuery] = item.href.split("?");
    if (pathname !== zielPfad && !pathname.startsWith(`${zielPfad}/`)) return false;
    // Ein Eintrag mit Query-Parameter (z.B. "/admin?tab=roles") ist nur aktiv,
    // wenn genau dieser Reiter offen ist — sonst wäre "Rechte" nie markiert
    // (der Pfad allein reicht nicht).
    if (zielQuery) return passtQuery(zielQuery);
    // Eintrag OHNE Query (z.B. "/admin"): nicht blind bei jedem passenden Pfad
    // aktiv setzen, sonst leuchtet "Admin-Bereich" gleichzeitig mit "Rechte"
    // mit, sobald ?tab=roles in der URL steht. Nur aktiv, wenn kein anderer
    // Eintrag mit demselben Basispfad UND eigenem Query gerade genauer passt.
    const spezifischererEintragPasst = allItems.some((other) => {
      if (other === item || other.external || !other.href.includes("?")) return false;
      const [otherPfad, otherQuery] = other.href.split("?");
      return otherPfad === zielPfad && passtQuery(otherQuery);
    });
    return !spezifischererEintragPasst;
  };

  // Check if any item in a group is active
  const isGroupActive = (group: NavGroup) => group.items.some(isItemActive);

  // Ein Eintrag der ausgeklappten Leiste — in den Gruppen wie unter „Weitere Bereiche“.
  const eintrag = (item: NavItem) => {
    const Icon = item.icon;
    const isActive = isItemActive(item);
    return (
      <NavShell
        key={item.href}
        item={item}
        title={item.tooltip}
        onClick={closeMobile}
        className={cn(
          "group flex items-center gap-3 rounded-lg px-2.5 py-2 text-[14px] transition-colors duration-150",
          isActive
            ? "bg-sidebar-active text-sidebar-foreground font-medium shadow-sm"
            : "text-sidebar-foreground/85 hover:bg-sidebar-hover hover:text-sidebar-foreground"
        )}
      >
        <Icon
          className={cn(
            "h-[18px] w-[18px] shrink-0 transition-colors",
            isActive ? "text-primary" : "text-sidebar-muted group-hover:text-sidebar-foreground"
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
        ) : null}
      </NavShell>
    );
  };

  return (
    <aside
      className={cn(
        "fixed left-0 top-0 z-40 h-screen border-r border-border bg-sidebar/80 backdrop-blur-xl text-sidebar-foreground flex flex-col transition-all duration-300",
        effectiveCollapsed ? "w-[64px]" : "w-[240px]",
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
          <Cpu className="h-4 w-4 text-white" />
        </div>
        {!effectiveCollapsed && (
          <>
            <div className="flex-1 min-w-0">
              <span className="text-sm font-semibold tracking-tight">AI Employee</span>
            </div>
            {/* Concierge und Feedback wohnen hier oben als Paar — die frueheren
                schwebenden Knoepfe unten rechts haben Eingabefelder ueberdeckt. */}
            {isAdmin && (
              <button
                onClick={() => {
                  closeMobile();
                  window.dispatchEvent(new CustomEvent("concierge-widget:open"));
                }}
                className="flex h-7 w-7 items-center justify-center rounded-lg text-sidebar-muted hover:text-sidebar-foreground hover:bg-sidebar-hover transition-all"
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
              className="flex h-7 w-7 items-center justify-center rounded-lg text-sidebar-muted hover:text-sidebar-foreground hover:bg-sidebar-hover transition-all"
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
                title={item.tooltip ?? item.label}
                onClick={closeMobile}
                className={cn(
                  "flex items-center justify-center h-9 w-9 mx-auto rounded-lg transition-all duration-150",
                  isActive
                    ? "bg-sidebar-active text-sidebar-foreground"
                    : "text-sidebar-muted hover:bg-sidebar-hover hover:text-sidebar-foreground"
                )}
              >
                <span className="relative">
                  <Icon className={cn("h-4 w-4", isActive ? "text-primary" : "")} />
                  {/* Eingeklappt ist kein Platz für eine Zahl — der Punkt sagt
                      trotzdem, dass dort etwas wartet. */}
                  {item.href === "/approvals" && pendingApprovals > 0 && (
                    <span className="absolute -right-1 -top-1 h-2 w-2 rounded-full bg-amber-400" />
                  )}
                </span>
              </NavShell>
            );
          }).concat(weitereItems.length > 0 ? [
            <button
              key="weitere-bereiche"
              onClick={weitereUmschalten}
              title={weitereOffen ? "Weitere Bereiche ausblenden" : "Weitere Bereiche zeigen"}
              aria-label={weitereOffen ? "Weitere Bereiche ausblenden" : "Weitere Bereiche zeigen"}
              aria-expanded={weitereOffen}
              className={cn(
                "flex items-center justify-center h-9 w-9 mx-auto rounded-xl transition-all duration-150",
                weitereOffen ? "bg-sidebar-hover text-sidebar-foreground" : "text-sidebar-muted hover:bg-sidebar-hover hover:text-sidebar-foreground"
              )}
            >
              <LayoutGrid className="h-4 w-4" />
            </button>,
          ] : [])
        ) : (
          // Expanded: grouped
          <>
          {visibleGroups.map((group) => {
            const isOpen = openGroups[group.key] ?? true;
            const hasActive = isGroupActive(group);
            return (
              <div key={group.key} className="mb-1">
                <button
                  onClick={() => toggleGroup(group.key)}
                  className={cn(
                    "flex w-full items-center gap-2 px-2.5 pb-1 pt-2 rounded-lg transition-colors",
                    "text-[11px] font-medium uppercase tracking-[0.06em]",
                    hasActive ? "text-sidebar-foreground/90" : "text-sidebar-muted",
                    "hover:text-sidebar-foreground"
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
                    {group.items.map(eintrag)}
                  </div>
                )}
              </div>
            );
          })}
          {weitereItems.length > 0 && (
            <div className="mt-2 border-t border-border/60 pt-2">
              <button
                onClick={weitereUmschalten}
                aria-expanded={weitereOffen}
                className="flex w-full items-center gap-2 px-2.5 py-1.5 rounded-lg text-[13px] text-sidebar-muted hover:text-sidebar-foreground hover:bg-sidebar-hover transition-colors"
              >
                <LayoutGrid className="h-3.5 w-3.5" />
                <span className="flex-1 text-left">Weitere Bereiche</span>
                <span className="text-[11px] tabular-nums text-sidebar-muted">{weitereItems.length}</span>
                <ChevronDown className={cn("h-3 w-3 transition-transform duration-200", weitereOffen ? "rotate-0" : "-rotate-90")} />
              </button>
              {weitereOffen && <div className="mt-0.5 space-y-0.5">{weitereItems.map(eintrag)}</div>}
            </div>
          )}
          </>
        )}
      </nav>

      {/* Update Banner (only when expanded) */}
      {!effectiveCollapsed && <UpdateBanner />}
      {/* Speicherquote-Stopp-Popup — Portal-Modal, unabhaengig vom Ein-/Ausklappen */}
      <StopReasonAlert />

      {/* Unten: Hilfe, Konto-Menü und — nur für Admins — der Wechsel in den
          eigenen Admin-Bereich (Redesign 2026-10: Admins arbeiten in derselben
          Ansicht wie alle und verwalten die Anlage in einem getrennten Bereich). */}
      <div className={cn(
        "border-t border-border shrink-0 mt-auto",
        effectiveCollapsed ? "flex flex-col items-center gap-1 px-0 py-3" : "px-2 py-2 space-y-1"
      )}>
        <Link
          href="/help"
          onClick={closeMobile}
          title="Hilfe & FAQ"
          className={cn(
            "flex items-center rounded-lg transition-colors",
            effectiveCollapsed ? "h-9 w-9 justify-center" : "gap-3 px-2.5 py-2 text-[14px]",
            pathname.startsWith("/help")
              ? "bg-sidebar-active text-sidebar-foreground"
              : "text-sidebar-foreground/85 hover:bg-sidebar-hover hover:text-sidebar-foreground"
          )}
        >
          <HelpCircle className={cn("h-[18px] w-[18px] shrink-0", pathname.startsWith("/help") ? "text-primary" : "text-sidebar-muted")} />
          {!effectiveCollapsed && <span>Hilfe</span>}
        </Link>
        <UserMenu collapsed={effectiveCollapsed} />
        {isAdmin && (
          <Link
            href="/admin"
            onClick={closeMobile}
            title="Zum Admin-Bereich"
            className={cn(
              "flex items-center justify-center rounded-lg border border-border text-sidebar-foreground transition-colors hover:bg-sidebar-hover",
              effectiveCollapsed ? "h-9 w-9" : "gap-2 px-3 py-2 text-[13px]"
            )}
          >
            <Shield className="h-4 w-4 shrink-0" />
            {!effectiveCollapsed && <span>Zum Admin-Bereich</span>}
          </Link>
        )}
      </div>

      {/* Collapse Toggle */}
      <button
        onClick={toggle}
        className={cn(
          "absolute -right-3 top-[54px] z-50 hidden h-6 w-6 items-center justify-center rounded-full border border-border bg-card shadow-md text-muted-foreground hover:text-foreground transition-all hover:scale-110 lg:flex"
        )}
        title={collapsed ? "Seitenleiste ausklappen" : "Seitenleiste einklappen"}
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
