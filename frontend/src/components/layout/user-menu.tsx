"use client";

import { useState, useRef, useEffect } from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";
import { motion } from "framer-motion";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { LogOut, Settings, Shield, Sun, Moon, Star, Info, Bot, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { logout, useAuthStore } from "@/lib/auth";
import { useTheme } from "@/components/theme-provider";
import { UserAvatar } from "@/components/ui/user-avatar";
import { NotificationBell } from "./notification-bell";

export function UserMenu({ collapsed = false }: { collapsed?: boolean }) {
  const { user } = useAuthStore();
  const { theme, toggleTheme } = useTheme();
  const router = useRouter();
  const [isOpen, setIsOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  const [aboutOpen, setAboutOpen] = useState(false);
  const [aboutVersion, setAboutVersion] = useState<string | null>(null);
  const [aboutChangelog, setAboutChangelog] = useState<string | null>(null);
  // GitHub-star nudge: highlight the Star link at most once per calendar day.
  const [starNudge, setStarNudge] = useState(false);

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    if (isOpen) document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [isOpen]);

  useEffect(() => {
    const today = new Date().toISOString().slice(0, 10);
    try {
      if (localStorage.getItem("star-nudge-day") !== today) {
        localStorage.setItem("star-nudge-day", today);
        setStarNudge(true);
        const t = setTimeout(() => setStarNudge(false), 10000);
        return () => clearTimeout(t);
      }
    } catch {
      /* localStorage unavailable — skip the nudge */
    }
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
      .then((d) => setAboutChangelog(d.content ?? null))
      .catch(() => {});
  }, [aboutOpen, aboutVersion]);

  if (!user) return null;

  const handleLogout = async () => {
    await logout();
    router.push("/login");
  };

  return (
    <div className="relative" ref={menuRef}>
      {collapsed ? (
        <button
          onClick={() => setIsOpen(!isOpen)}
          title={user.name}
          className="relative flex items-center justify-center h-9 w-9 rounded-xl text-muted-foreground hover:bg-accent/50 hover:text-foreground transition-all"
        >
          <UserAvatar name={user.name} className="h-7 w-7" />
          {user.role === "admin" && (
            <Shield className="absolute -bottom-0.5 -right-0.5 h-3 w-3 text-amber-500 bg-card rounded-full" />
          )}
        </button>
      ) : (
        <button
          onClick={() => setIsOpen(!isOpen)}
          className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-muted-foreground hover:bg-accent/50 hover:text-foreground transition-all duration-150"
        >
          <UserAvatar name={user.name} />
          <div className="flex-1 min-w-0 text-left">
            <p className="text-[12px] font-medium text-foreground truncate">{user.name}</p>
            <p className="flex items-center gap-1 text-[10px] text-muted-foreground/60 truncate">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 shrink-0" /> Online
            </p>
          </div>
          {user.role === "admin" && (
            <Shield className="h-3 w-3 text-amber-500 shrink-0" />
          )}
        </button>
      )}

      {isOpen && (
        <div className="absolute left-full ml-2 bottom-0 w-56 rounded-xl border border-border bg-card shadow-2xl z-50 overflow-hidden">
          <div className="py-1 border-b border-border">
            <NotificationBell variant="sidebar" />
            <button
              onClick={toggleTheme}
              className="flex w-full items-center gap-3 rounded-xl px-3 py-2 text-muted-foreground hover:bg-accent/50 hover:text-foreground transition-all duration-150"
            >
              {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
              <span className="text-[13px] font-medium">
                {theme === "dark" ? "Light Mode" : "Dark Mode"}
              </span>
            </button>
            <a
              href="https://github.com/greeves89/AI-Employee"
              target="_blank"
              rel="noopener noreferrer"
              className={cn(
                "flex w-full items-center gap-3 rounded-xl px-3 py-2 transition-all duration-150 hover:bg-yellow-500/10 hover:text-yellow-400",
                starNudge
                  ? "bg-yellow-500/10 text-yellow-400 ring-1 ring-yellow-500/30 animate-pulse"
                  : "text-muted-foreground"
              )}
            >
              <Star className="h-4 w-4" />
              <span className="text-[13px] font-medium">Star on GitHub</span>
            </a>
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
          </div>
          <div className="px-3 py-2 border-b border-border">
            <p className="text-xs font-medium truncate">{user.name}</p>
            <p className="text-[10px] text-muted-foreground truncate">{user.email}</p>
            <span className={cn(
              "inline-flex mt-1 px-1.5 py-0.5 rounded text-[9px] font-medium",
              user.role === "admin"
                ? "bg-amber-500/10 text-amber-500"
                : "bg-blue-500/10 text-blue-500"
            )}>
              {user.role}
            </span>
          </div>
          <div className="py-1">
            {/* Einstellungen gehoeren an den Nutzer, nicht in die Seitenleiste:
                dort standen sie bis 2026-08-15 gar nicht, weshalb niemand die
                Seite je gefunden hat — samt der Stelle, an der man sein eigenes
                Claude-/Codex-Abo verbindet. */}
            <button
              onClick={() => { setIsOpen(false); router.push("/settings"); }}
              className="flex w-full items-center gap-2 px-3 py-2 text-xs text-muted-foreground hover:bg-accent/50 hover:text-foreground transition-colors"
            >
              <Settings className="h-3.5 w-3.5" />
              Einstellungen
            </button>
            <button
              onClick={handleLogout}
              className="flex w-full items-center gap-2 px-3 py-2 text-xs text-red-400 hover:bg-accent/50 transition-colors"
            >
              <LogOut className="h-3.5 w-3.5" />
              Sign Out
            </button>
          </div>
        </div>
      )}

      {/* About Modal — portal to document.body to escape any transform context */}
      {aboutOpen && typeof document !== "undefined" && createPortal(
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.15 }}
            className="fixed inset-0 z-[100] bg-black/60 backdrop-blur-sm"
            onClick={() => setAboutOpen(false)}
          />
          <div className="fixed z-[101] left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2">
            <motion.div
              initial={{ opacity: 0, scale: 0.96, y: 8 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              transition={{ duration: 0.2 }}
              className="w-[90vw] max-w-2xl max-h-[80vh] flex flex-col rounded-2xl border border-foreground/[0.08] bg-card shadow-2xl"
            >
              <div className="flex items-center gap-3 px-6 pt-6 pb-4 border-b border-foreground/[0.06] shrink-0">
                <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10">
                  <Bot className="h-5 w-5 text-primary" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-base font-semibold">AI Employee</p>
                  {aboutVersion && <p className="text-[12px] text-muted-foreground font-mono">v{aboutVersion}</p>}
                </div>
                <button
                  onClick={() => setAboutOpen(false)}
                  className="flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] transition-all"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
              <div className="flex-1 overflow-y-auto px-6 py-4">
                {aboutChangelog ? (
                  <div className="prose prose-sm dark:prose-invert max-w-none text-[13px] [&_h1]:hidden [&_h2]:text-sm [&_h2]:font-semibold [&_h2]:mt-4 [&_h2]:mb-2 [&_h3]:text-xs [&_h3]:font-semibold [&_h3]:text-muted-foreground [&_h3]:mt-3 [&_h3]:mb-1 [&_ul]:space-y-1 [&_li]:text-muted-foreground [&_strong]:text-foreground [&_p]:text-muted-foreground [&_hr]:border-foreground/[0.06] [&_code]:rounded [&_code]:bg-muted [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:text-[11px] [&_code]:font-mono [&_code]:text-amber-600 dark:[&_code]:text-amber-300 [&_code]:before:content-[''] [&_code]:after:content-['']">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{aboutChangelog}</ReactMarkdown>
                  </div>
                ) : (
                  <div className="flex items-center justify-center h-24 text-muted-foreground text-sm">
                    Lade Changelog...
                  </div>
                )}
              </div>
              <div className="flex items-center justify-between px-6 py-3 border-t border-foreground/[0.06] shrink-0">
                <span className="text-[11px] text-muted-foreground/50">Made with ♥ by greeves89</span>
                <a href="https://github.com/greeves89/AI-Employee" target="_blank" rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground hover:text-yellow-400 transition-colors">
                  <Star className="h-3 w-3" />GitHub
                </a>
              </div>
            </motion.div>
          </div>
        </>,
        document.body
      )}
    </div>
  );
}
