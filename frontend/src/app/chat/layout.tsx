"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import {
  Bot, Plus, MessageSquare, Search, Trash2,
  LayoutDashboard, ChevronLeft, ChevronRight,
  Settings, Sparkles,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useTheme } from "@/components/theme-provider";
import { UserMenu } from "@/components/layout/user-menu";
import { NotificationBell } from "@/components/layout/notification-bell";
import * as api from "@/lib/api";
import type { Agent } from "@/lib/types";

interface ConversationItem {
  sessionId: string;
  agentId: string;
  agentName: string;
  preview: string;
  messageCount: number;
  updatedAt: string;
}

export default function ChatLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const currentAgentId = searchParams.get("agent");
  const currentSessionId = searchParams.get("session");
  const { theme } = useTheme();

  const [agents, setAgents] = useState<Agent[]>([]);
  const [conversations, setConversations] = useState<ConversationItem[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [loading, setLoading] = useState(true);

  // Load agents
  useEffect(() => {
    const loadAgents = async () => {
      try {
        const data = await api.getAgents();
        setAgents(data.agents.filter((a) => ["running", "idle", "working"].includes(a.state)));
      } catch {
        // ignore
      }
    };
    loadAgents();
    const interval = setInterval(loadAgents, 30000);
    return () => clearInterval(interval);
  }, []);

  // Load conversations for all agents
  const loadConversations = useCallback(async () => {
    if (agents.length === 0) return;
    setLoading(true);
    try {
      const allConversations: ConversationItem[] = [];
      for (const agent of agents) {
        try {
          const { sessions } = await api.getChatSessions(agent.id);
          for (const session of sessions) {
            if (session.message_count > 0 || session.preview) {
              allConversations.push({
                sessionId: session.id,
                agentId: agent.id,
                agentName: agent.name,
                preview: session.preview || "New conversation",
                messageCount: session.message_count,
                updatedAt: session.last_message_at || session.started_at || "",
              });
            }
          }
        } catch {
          // ignore per-agent errors
        }
      }
      // Sort by most recent
      allConversations.sort((a, b) => {
        if (!a.updatedAt && !b.updatedAt) return 0;
        if (!a.updatedAt) return 1;
        if (!b.updatedAt) return -1;
        return new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime();
      });
      setConversations(allConversations);
    } finally {
      setLoading(false);
    }
  }, [agents]);

  useEffect(() => {
    loadConversations();
  }, [loadConversations]);

  const filteredConversations = searchQuery
    ? conversations.filter(
        (c) =>
          c.preview.toLowerCase().includes(searchQuery.toLowerCase()) ||
          c.agentName.toLowerCase().includes(searchQuery.toLowerCase())
      )
    : conversations;

  // Group by date
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  const lastWeek = new Date(today);
  lastWeek.setDate(lastWeek.getDate() - 7);

  const grouped = {
    today: filteredConversations.filter((c) => c.updatedAt && new Date(c.updatedAt).toDateString() === today.toDateString()),
    yesterday: filteredConversations.filter((c) => c.updatedAt && new Date(c.updatedAt).toDateString() === yesterday.toDateString()),
    lastWeek: filteredConversations.filter(
      (c) => c.updatedAt && new Date(c.updatedAt) >= lastWeek && new Date(c.updatedAt).toDateString() !== today.toDateString() && new Date(c.updatedAt).toDateString() !== yesterday.toDateString()
    ),
    older: filteredConversations.filter((c) => !c.updatedAt || new Date(c.updatedAt) < lastWeek),
  };

  const startNewChat = (agentId?: string) => {
    const targetAgent = agentId || agents[0]?.id;
    if (targetAgent) {
      // Add timestamp to force new session (key change in page.tsx)
      router.push(`/chat?agent=${targetAgent}&t=${Date.now()}`);
    }
  };

  const openConversation = (conv: ConversationItem) => {
    router.push(`/chat?agent=${conv.agentId}&session=${conv.sessionId}`);
  };

  const deleteConversation = async (conv: ConversationItem, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await api.deleteChatSession(conv.agentId, conv.sessionId);
      setConversations((prev) => prev.filter((c) => c.sessionId !== conv.sessionId));
    } catch {
      // ignore
    }
  };

  const ConversationGroup = ({ title, items }: { title: string; items: ConversationItem[] }) => {
    if (items.length === 0) return null;
    return (
      <div className="mb-3">
        <p className="px-3 mb-1.5 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground/50">
          {title}
        </p>
        {items.map((conv) => {
          const isActive = conv.sessionId === currentSessionId && conv.agentId === currentAgentId;
          return (
            <button
              key={conv.sessionId}
              onClick={() => openConversation(conv)}
              className={cn(
                "group w-full flex items-start gap-2.5 rounded-xl px-3 py-2.5 text-left transition-all duration-150",
                isActive
                  ? "bg-accent text-foreground"
                  : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
              )}
            >
              <MessageSquare className="h-4 w-4 mt-0.5 shrink-0 opacity-50" />
              <div className="flex-1 min-w-0">
                <p className="text-[13px] font-medium truncate leading-tight">
                  {conv.preview.length > 40 ? conv.preview.slice(0, 40) + "..." : conv.preview}
                </p>
                <p className="text-[10px] text-muted-foreground/60 mt-0.5 flex items-center gap-1.5">
                  <Bot className="h-2.5 w-2.5" />
                  {conv.agentName}
                </p>
              </div>
              <button
                onClick={(e) => deleteConversation(conv, e)}
                className="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-foreground/[0.1] transition-all shrink-0"
              >
                <Trash2 className="h-3 w-3" />
              </button>
            </button>
          );
        })}
      </div>
    );
  };

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      {/* Sidebar */}
      <AnimatePresence mode="wait">
        <motion.aside
          initial={false}
          animate={{ width: sidebarCollapsed ? 0 : 280 }}
          transition={{ duration: 0.2, ease: "easeInOut" }}
          className="relative h-screen border-r border-border bg-card/50 backdrop-blur-xl flex flex-col overflow-hidden shrink-0"
        >
          {/* Header */}
          <div className="flex h-14 items-center gap-3 px-4 border-b border-border shrink-0">
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
                <span className="text-[10px] text-muted-foreground">
                  {agents.length} Agent{agents.length !== 1 ? "s" : ""} online
                </span>
              </div>
            </div>
          </div>

          {/* New Chat Button */}
          <div className="px-3 pt-3 pb-2 shrink-0">
            <button
              onClick={() => startNewChat()}
              className="w-full flex items-center gap-2.5 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 hover:bg-primary/90 transition-all duration-200 hover:shadow-primary/30 hover:-translate-y-0.5"
            >
              <Plus className="h-4 w-4" />
              Neuer Chat
            </button>
          </div>

          {/* Search */}
          <div className="px-3 pb-2 shrink-0">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground/50" />
              <input
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Chats durchsuchen..."
                className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] pl-9 pr-3 py-2 text-xs outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all placeholder:text-muted-foreground/30"
              />
            </div>
          </div>

          {/* Conversation List */}
          <nav className="flex-1 overflow-y-auto px-2 py-1 scrollbar-thin">
            {loading && conversations.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-32 text-muted-foreground/50">
                <Sparkles className="h-5 w-5 mb-2 animate-pulse" />
                <p className="text-[11px]">Lade Unterhaltungen...</p>
              </div>
            ) : filteredConversations.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-32 text-muted-foreground/50">
                <MessageSquare className="h-5 w-5 mb-2" />
                <p className="text-[11px]">Noch keine Unterhaltungen</p>
                <p className="text-[10px] mt-1">Starte einen neuen Chat!</p>
              </div>
            ) : (
              <>
                <ConversationGroup title="Heute" items={grouped.today} />
                <ConversationGroup title="Gestern" items={grouped.yesterday} />
                <ConversationGroup title="Letzte 7 Tage" items={grouped.lastWeek} />
                <ConversationGroup title="Aelter" items={grouped.older} />
              </>
            )}
          </nav>

          {/* Bottom nav */}
          <div className="border-t border-border p-2 space-y-0.5 shrink-0">
            <Link
              href="/dashboard"
              className="flex items-center gap-3 rounded-xl px-3 py-2 text-[13px] font-medium text-muted-foreground hover:bg-accent/50 hover:text-foreground transition-all duration-150"
            >
              <LayoutDashboard className="h-4 w-4" />
              Dashboard
            </Link>
            <Link
              href="/agents"
              className="flex items-center gap-3 rounded-xl px-3 py-2 text-[13px] font-medium text-muted-foreground hover:bg-accent/50 hover:text-foreground transition-all duration-150"
            >
              <Settings className="h-4 w-4" />
              Agents verwalten
            </Link>
            <div className="flex items-center gap-1 px-1 pt-1">
              <NotificationBell variant="sidebar" />
              <div className="flex-1" />
              <UserMenu />
            </div>
          </div>
        </motion.aside>
      </AnimatePresence>

      {/* Collapse toggle */}
      <button
        onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
        className="absolute left-0 top-1/2 -translate-y-1/2 z-50 flex h-6 w-6 items-center justify-center rounded-r-lg bg-card border border-l-0 border-border text-muted-foreground hover:text-foreground transition-all"
        style={{ left: sidebarCollapsed ? 0 : 280 }}
      >
        {sidebarCollapsed ? <ChevronRight className="h-3 w-3" /> : <ChevronLeft className="h-3 w-3" />}
      </button>

      {/* Main content */}
      <main className="flex-1 min-w-0 h-screen overflow-hidden">
        {children}
      </main>
    </div>
  );
}
