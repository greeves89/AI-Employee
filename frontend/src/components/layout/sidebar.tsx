"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { useTheme } from "@/components/theme-provider";
import {
  LayoutDashboard,
  Cpu,
  ListTodo,
  Clock,
  FolderOpen,
  Plug,
  Settings,
  Shield,
  Bot,
  Sun,
  Moon,
  MessageSquarePlus,
} from "lucide-react";
import { NotificationBell } from "./notification-bell";
import { UserMenu } from "./user-menu";
import { FeedbackModal } from "@/components/feedback/feedback-modal";
import { useAuthStore } from "@/lib/auth";

const navItems = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/agents", label: "Agents", icon: Cpu },
  { href: "/tasks", label: "Tasks", icon: ListTodo },
  { href: "/schedules", label: "Schedules", icon: Clock },
  { href: "/files", label: "Explorer", icon: FolderOpen },
  { href: "/integrations", label: "Integrations", icon: Plug },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function Sidebar() {
  const pathname = usePathname();
  const { theme, toggleTheme } = useTheme();
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === "admin";
  const [feedbackOpen, setFeedbackOpen] = useState(false);

  return (
    <aside className="fixed left-0 top-0 z-40 h-screen w-[260px] border-r border-border bg-card/50 backdrop-blur-xl flex flex-col">
      {/* Logo + Feedback */}
      <div className="flex h-14 items-center gap-3 px-5 border-b border-border">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-blue-500 to-cyan-400 shadow-lg shadow-blue-500/20">
          <Bot className="h-4 w-4 text-white" />
        </div>
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
      </div>

      <FeedbackModal open={feedbackOpen} onClose={() => setFeedbackOpen(false)} />

      {/* Navigation */}
      <nav className="flex-1 space-y-0.5 p-3 pt-4">
        <p className="px-3 mb-2 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground/60">
          Navigation
        </p>
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "group flex items-center gap-3 rounded-xl px-3 py-2.5 text-[13px] font-medium transition-all duration-150",
                isActive
                  ? "bg-accent text-foreground shadow-sm"
                  : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
              )}
            >
              <Icon
                className={cn(
                  "h-4 w-4 transition-colors",
                  isActive ? "text-primary" : "text-muted-foreground group-hover:text-foreground"
                )}
              />
              {item.label}
              {isActive && (
                <div className="ml-auto h-1.5 w-1.5 rounded-full bg-primary shadow-[0_0_6px_rgba(59,130,246,0.5)]" />
              )}
            </Link>
          );
        })}

      </nav>

      {/* Bottom */}
      <div className="border-t border-border p-3 space-y-1">
        {/* Admin link */}
        {isAdmin && (
          <Link
            href="/admin"
            className={cn(
              "group flex items-center gap-3 rounded-xl px-3 py-2.5 text-[13px] font-medium transition-all duration-150",
              pathname.startsWith("/admin")
                ? "bg-accent text-foreground shadow-sm"
                : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
            )}
          >
            <Shield
              className={cn(
                "h-4 w-4 transition-colors",
                pathname.startsWith("/admin") ? "text-amber-500" : "text-muted-foreground group-hover:text-amber-500"
              )}
            />
            Admin
            {pathname.startsWith("/admin") && (
              <div className="ml-auto h-1.5 w-1.5 rounded-full bg-amber-500 shadow-[0_0_6px_rgba(245,158,11,0.5)]" />
            )}
          </Link>
        )}
        {/* Notification Bell */}
        <NotificationBell variant="sidebar" />
        <button
          onClick={toggleTheme}
          className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-muted-foreground hover:bg-accent/50 hover:text-foreground transition-all duration-150"
        >
          {theme === "dark" ? (
            <Sun className="h-4 w-4" />
          ) : (
            <Moon className="h-4 w-4" />
          )}
          <span className="text-[13px] font-medium">
            {theme === "dark" ? "Light Mode" : "Dark Mode"}
          </span>
        </button>
        <UserMenu />
      </div>
    </aside>
  );
}
