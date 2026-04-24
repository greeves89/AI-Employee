"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { useTheme } from "@/components/theme-provider";
import {
  LayoutDashboard,
  Cpu,
  ListTodo,
  FolderOpen,
  Plug,
  Settings,
  Shield,
  Bot,
  Sun,
  Moon,
  MessageSquarePlus,
  ShieldCheck,
  BookOpen,
  HeartPulse,
  Sparkles,
  Zap,
  ScrollText,
  Users,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  Bell,
  Star,
  BarChart3,
  Info,
  X,
} from "lucide-react";
import * as Dialog from "@radix-ui/react-dialog";
import { motion, AnimatePresence } from "framer-motion";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { NotificationBell } from "./notification-bell";
import { UpdateBanner } from "./update-banner";
import { UserMenu } from "./user-menu";
import { FeedbackModal } from "@/components/feedback/feedback-modal";
import { useAuthStore } from "@/lib/auth";
import { useSidebarCollapsed } from "@/hooks/use-sidebar";

type NavItem = {
  href: string;
  label: string;
  icon: React.ElementType;
  simpleVisible: boolean;
};

type NavGroup = {
  label: string;
  key: string;
  items: NavItem[];
};

const navGroups: NavGroup[] = [
  {
    label: "Übersicht",
    key: "overview",
    items: [
      { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard, simpleVisible: true },
      { href: "/agents", label: "Agents", icon: Cpu, simpleVisible: true },
      { href: "/tasks", label: "Tasks", icon: ListTodo, simpleVisible: true },
      { href: "/analytics", label: "Analytics", icon: BarChart3, simpleVisible: true },
    ],
  },
  {
    label: "Zusammenarbeit",
    key: "collab",
    items: [
      { href: "/knowledge", label: "Knowledge", icon: BookOpen, simpleVisible: true },
      { href: "/meeting-rooms", label: "Meeting Rooms", icon: Users, simpleVisible: false },
    ],
  },
  {
    label: "Automation",
    key: "automation",
    items: [
      { href: "/skills", label: "Skill Marketplace", icon: Sparkles, simpleVisible: false },
      { href: "/triggers", label: "Triggers", icon: Zap, simpleVisible: false },
    ],
  },
  {
    label: "System",
    key: "system",
    items: [
      { href: "/approvals", label: "Approvals", icon: ShieldCheck, simpleVisible: false },
      { href: "/health", label: "Health", icon: HeartPulse, simpleVisible: false },
      { href: "/audit", label: "Audit Log", icon: ScrollText, simpleVisible: false },
      { href: "/files", label: "Explorer", icon: FolderOpen, simpleVisible: true },
      { href: "/integrations", label: "Integrations", icon: Plug, simpleVisible: false },
      { href: "/settings", label: "Settings", icon: Settings, simpleVisible: false },
    ],
  },
];

export function Sidebar() {
  const pathname = usePathname();
  const { theme, toggleTheme } = useTheme();
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === "admin";
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const { collapsed, toggle } = useSidebarCollapsed();
  const [starCount, setStarCount] = useState<number | null>(null);
  const [aboutOpen, setAboutOpen] = useState(false);
  const [aboutVersion, setAboutVersion] = useState<string | null>(null);
  const [aboutChangelog, setAboutChangelog] = useState<string | null>(null);

  useEffect(() => {
    fetch("https://api.github.com/repos/greeves89/AI-Employee")
      .then((r) => r.json())
      .then((d) => setStarCount(d.stargazers_count ?? null))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!aboutOpen || aboutVersion) return;
    const base = process.env.NEXT_PUBLIC_API_URL || "";
    fetch(`${base}/api/v1/version/`)
      .then((r) => r.json())
      .then((d) => setAboutVersion(d.current ?? d.version ?? null))
      .catch(() => {});
    fetch(`${base}/api/v1/version/changelog`)
      .then((r) => r.json())
      .then((d) => setAboutChangelog(d.changelog ?? null))
      .catch(() => {});
  }, [aboutOpen, aboutVersion]);

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

  // In collapsed mode, show all items (groups are irrelevant)
  const allItems = navGroups.flatMap((g) => g.items);

  // Check if any item in a group is active
  const isGroupActive = (group: NavGroup) =>
    group.items.some((item) => pathname.startsWith(item.href));

  return (
    <aside
      className={cn(
        "fixed left-0 top-0 z-40 h-screen border-r border-border bg-card/50 backdrop-blur-xl flex flex-col transition-all duration-300",
        collapsed ? "w-[64px]" : "w-[260px]"
      )}
    >
      {/* Logo */}
      <div className={cn(
        "flex h-14 items-center border-b border-border shrink-0",
        collapsed ? "justify-center px-0" : "gap-3 px-5"
      )}>
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-blue-500 to-cyan-400 shadow-lg shadow-blue-500/20">
          <Bot className="h-4 w-4 text-white" />
        </div>
        {!collapsed && (
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
            <button
              onClick={() => setFeedbackOpen(true)}
              className="flex h-7 w-7 items-center justify-center rounded-lg text-muted-foreground/50 hover:text-primary hover:bg-primary/10 transition-all"
              title="Feedback senden"
            >
              <MessageSquarePlus className="h-4 w-4" />
            </button>
          </>
        )}
      </div>

      <FeedbackModal open={feedbackOpen} onClose={() => setFeedbackOpen(false)} />

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto py-3 px-2 space-y-0.5 scrollbar-thin">
        {collapsed ? (
          // Collapsed: just icons
          allItems.map((item) => {
            const Icon = item.icon;
            const isActive = pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                title={item.label}
                className={cn(
                  "flex items-center justify-center h-9 w-9 mx-auto rounded-xl transition-all duration-150",
                  isActive
                    ? "bg-accent text-foreground shadow-sm"
                    : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
                )}
              >
                <Icon className={cn("h-4 w-4", isActive ? "text-primary" : "")} />
              </Link>
            );
          })
        ) : (
          // Expanded: grouped
          navGroups.map((group) => {
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
                      const isActive = pathname.startsWith(item.href);
                      return (
                        <Link
                          key={item.href}
                          href={item.href}
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
                          {isActive && (
                            <div className="ml-auto h-1.5 w-1.5 shrink-0 rounded-full bg-primary shadow-[0_0_6px_rgba(59,130,246,0.5)]" />
                          )}
                        </Link>
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
      {!collapsed && <UpdateBanner />}

      {/* Bottom */}
      <div className={cn(
        "border-t border-border py-2 shrink-0",
        collapsed ? "flex flex-col items-center gap-1 px-0 py-3" : "px-2 space-y-0.5"
      )}>
        {collapsed ? (
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
            <button
              onClick={() => setAboutOpen(true)}
              title="Über AI Employee"
              className="flex items-center justify-center h-9 w-9 rounded-xl text-muted-foreground hover:bg-accent/50 hover:text-foreground transition-all"
            >
              <Info className="h-4 w-4" />
            </button>
            <a
              href="https://github.com/greeves89/AI-Employee"
              target="_blank"
              rel="noopener noreferrer"
              title="Star on GitHub"
              className="flex items-center justify-center h-9 w-9 rounded-xl text-muted-foreground hover:bg-yellow-500/10 hover:text-yellow-400 transition-all"
            >
              <Star className="h-4 w-4" />
            </a>
            <button
              onClick={toggleTheme}
              title={theme === "dark" ? "Light Mode" : "Dark Mode"}
              className="flex items-center justify-center h-9 w-9 rounded-xl text-muted-foreground hover:bg-accent/50 hover:text-foreground transition-all"
            >
              {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>
            <NotificationBell variant="sidebar" />
          </>
        ) : (
          <>
            {isAdmin && (
              <Link
                href="/admin"
                className={cn(
                  "group flex items-center gap-3 rounded-xl px-3 py-2 text-[13px] font-medium transition-all duration-150",
                  pathname.startsWith("/admin")
                    ? "bg-accent text-foreground shadow-sm"
                    : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
                )}
              >
                <Shield className={cn(
                  "h-4 w-4 transition-colors",
                  pathname.startsWith("/admin") ? "text-amber-500" : "text-muted-foreground group-hover:text-amber-500"
                )} />
                Admin
                {pathname.startsWith("/admin") && (
                  <div className="ml-auto h-1.5 w-1.5 rounded-full bg-amber-500 shadow-[0_0_6px_rgba(245,158,11,0.5)]" />
                )}
              </Link>
            )}
            <NotificationBell variant="sidebar" />
            <button
              onClick={() => setAboutOpen(true)}
              className="flex w-full items-center gap-3 rounded-xl px-3 py-2 text-muted-foreground hover:bg-accent/50 hover:text-foreground transition-all duration-150"
            >
              <Info className="h-4 w-4" />
              <span className="text-[13px] font-medium">Über AI Employee</span>
              {aboutVersion && (
                <span className="ml-auto text-[11px] font-mono text-muted-foreground/50">v{aboutVersion}</span>
              )}
            </button>
            <a
              href="https://github.com/greeves89/AI-Employee"
              target="_blank"
              rel="noopener noreferrer"
              className="flex w-full items-center gap-3 rounded-xl px-3 py-2 text-muted-foreground hover:bg-yellow-500/10 hover:text-yellow-400 transition-all duration-150"
            >
              <Star className="h-4 w-4" />
              <span className="text-[13px] font-medium">Star on GitHub</span>
              {starCount !== null && (
                <span className="ml-auto text-[11px] font-medium px-1.5 py-0.5 rounded-full bg-yellow-500/10 text-yellow-400">
                  {starCount}
                </span>
              )}
            </a>
            <button
              onClick={toggleTheme}
              className="flex w-full items-center gap-3 rounded-xl px-3 py-2 text-muted-foreground hover:bg-accent/50 hover:text-foreground transition-all duration-150"
            >
              {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
              <span className="text-[13px] font-medium">
                {theme === "dark" ? "Light Mode" : "Dark Mode"}
              </span>
            </button>
            <UserMenu />
          </>
        )}
      </div>

      {/* Collapse Toggle */}
      <button
        onClick={toggle}
        className={cn(
          "absolute -right-3 top-[54px] z-50 flex h-6 w-6 items-center justify-center rounded-full border border-border bg-card shadow-md text-muted-foreground hover:text-foreground transition-all hover:scale-110"
        )}
        title={collapsed ? "Sidebar erweitern" : "Sidebar einklappen"}
      >
        {collapsed ? (
          <ChevronRight className="h-3 w-3" />
        ) : (
          <ChevronLeft className="h-3 w-3" />
        )}
      </button>

      {/* About Modal */}
      <Dialog.Root open={aboutOpen} onOpenChange={setAboutOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm" />
          <AnimatePresence>
            {aboutOpen && (
              <Dialog.Content asChild>
                <motion.div
                  initial={{ opacity: 0, scale: 0.96, y: 8 }}
                  animate={{ opacity: 1, scale: 1, y: 0 }}
                  exit={{ opacity: 0, scale: 0.96, y: 8 }}
                  transition={{ duration: 0.2 }}
                  className="fixed left-1/2 top-1/2 z-50 -translate-x-1/2 -translate-y-1/2 w-full max-w-lg max-h-[80vh] flex flex-col rounded-2xl border border-foreground/[0.08] bg-card shadow-2xl outline-none"
                >
                  {/* Header */}
                  <div className="flex items-center gap-3 px-6 pt-6 pb-4 border-b border-foreground/[0.06] shrink-0">
                    <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10">
                      <Bot className="h-5 w-5 text-primary" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <Dialog.Title className="text-base font-semibold">AI Employee</Dialog.Title>
                      {aboutVersion && (
                        <p className="text-[12px] text-muted-foreground font-mono">v{aboutVersion}</p>
                      )}
                    </div>
                    <Dialog.Close className="flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] transition-all">
                      <X className="h-4 w-4" />
                    </Dialog.Close>
                  </div>

                  {/* Changelog */}
                  <div className="flex-1 overflow-y-auto px-6 py-4">
                    {aboutChangelog ? (
                      <div className="prose prose-sm prose-invert max-w-none text-[13px] [&_h2]:text-sm [&_h2]:font-semibold [&_h2]:mt-4 [&_h2]:mb-2 [&_h3]:text-xs [&_h3]:font-semibold [&_h3]:text-muted-foreground [&_h3]:mt-3 [&_h3]:mb-1 [&_ul]:space-y-1 [&_li]:text-muted-foreground [&_strong]:text-foreground [&_p]:text-muted-foreground [&_hr]:border-foreground/[0.06]">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{aboutChangelog}</ReactMarkdown>
                      </div>
                    ) : (
                      <div className="flex items-center justify-center h-24 text-muted-foreground text-sm">
                        Lade Changelog...
                      </div>
                    )}
                  </div>

                  {/* Footer */}
                  <div className="flex items-center justify-between px-6 py-3 border-t border-foreground/[0.06] shrink-0">
                    <span className="text-[11px] text-muted-foreground/50">Made with ♥ by greeves89</span>
                    <a
                      href="https://github.com/greeves89/AI-Employee"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground hover:text-yellow-400 transition-colors"
                    >
                      <Star className="h-3 w-3" />
                      GitHub
                    </a>
                  </div>
                </motion.div>
              </Dialog.Content>
            )}
          </AnimatePresence>
        </Dialog.Portal>
      </Dialog.Root>
    </aside>
  );
}
