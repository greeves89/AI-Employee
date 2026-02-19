"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Bell, Check, CheckCheck, Trash2, X } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  getNotifications,
  getUnreadCount,
  markNotificationRead,
  markAllNotificationsRead,
  deleteNotification,
} from "@/lib/api";
import type { Notification } from "@/lib/types";
import { useAuthStore } from "@/lib/auth";

import { getWsUrl } from "@/lib/config";

const typeColors: Record<string, string> = {
  info: "bg-blue-500",
  warning: "bg-amber-500",
  error: "bg-red-500",
  success: "bg-emerald-500",
  approval: "bg-purple-500",
};

const priorityIndicator: Record<string, string> = {
  urgent: "animate-pulse ring-2 ring-red-400",
  high: "ring-2 ring-amber-400",
  normal: "",
  low: "opacity-80",
};

export function NotificationBell({ variant = "icon" }: { variant?: "icon" | "sidebar" }) {
  const [isOpen, setIsOpen] = useState(false);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const panelRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeout = useRef<ReturnType<typeof setTimeout>>();
  const intentionalClose = useRef(false);

  // Fetch unread count on mount and periodically
  const fetchCount = useCallback(async () => {
    try {
      const { unread } = await getUnreadCount();
      setUnreadCount(unread);
    } catch {
      // ignore
    }
  }, []);

  const fetchNotifications = useCallback(async () => {
    try {
      const { notifications: notifs } = await getNotifications();
      setNotifications(notifs);
    } catch {
      // ignore
    }
  }, []);

  const wsToken = useAuthStore((s) => s.wsToken);

  // WebSocket for live notifications
  useEffect(() => {
    intentionalClose.current = false;

    const connect = () => {
      const tokenParam = wsToken ? `?token=${wsToken}` : "";
      const ws = new WebSocket(`${getWsUrl()}/api/v1/ws/notifications${tokenParam}`);
      wsRef.current = ws;

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === "notification" && data.data) {
            setNotifications((prev) => [data.data, ...prev]);
            setUnreadCount((prev) => prev + 1);
          }
        } catch {
          // ignore
        }
      };

      ws.onclose = () => {
        if (intentionalClose.current) return;
        // Reconnect after 5 seconds
        reconnectTimeout.current = setTimeout(connect, 5000);
      };

      ws.onerror = () => ws.close();
    };

    connect();
    fetchCount();

    // Poll count every 30 seconds as fallback
    const interval = setInterval(fetchCount, 30000);

    return () => {
      clearInterval(interval);
      clearTimeout(reconnectTimeout.current);
      intentionalClose.current = true;
      wsRef.current?.close();
    };
  }, [fetchCount, wsToken]);

  // Close panel on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    if (isOpen) document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [isOpen]);

  const handleOpen = () => {
    setIsOpen(!isOpen);
    if (!isOpen) fetchNotifications();
  };

  const handleMarkRead = async (id: number) => {
    try {
      await markNotificationRead(id);
      setNotifications((prev) =>
        prev.map((n) => (n.id === id ? { ...n, read: true } : n))
      );
      setUnreadCount((prev) => Math.max(0, prev - 1));
    } catch {
      // ignore
    }
  };

  const handleMarkAllRead = async () => {
    try {
      await markAllNotificationsRead();
      setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
      setUnreadCount(0);
    } catch {
      // ignore
    }
  };

  const handleDelete = async (id: number) => {
    try {
      const notif = notifications.find((n) => n.id === id);
      await deleteNotification(id);
      setNotifications((prev) => prev.filter((n) => n.id !== id));
      if (notif && !notif.read) setUnreadCount((prev) => Math.max(0, prev - 1));
    } catch {
      // ignore
    }
  };

  const formatTime = (iso: string) => {
    const d = new Date(iso);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return "just now";
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffH = Math.floor(diffMin / 60);
    if (diffH < 24) return `${diffH}h ago`;
    return d.toLocaleDateString();
  };

  return (
    <div className="relative" ref={panelRef}>
      {variant === "sidebar" ? (
        <button
          onClick={handleOpen}
          className={cn(
            "flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-[13px] font-medium transition-all duration-150",
            isOpen
              ? "bg-accent text-foreground shadow-sm"
              : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
          )}
        >
          <div className="relative">
            <Bell className="h-4 w-4" />
            {unreadCount > 0 && (
              <span className="absolute -top-1.5 -right-1.5 flex h-3.5 min-w-[14px] items-center justify-center rounded-full bg-red-500 px-0.5 text-[8px] font-bold text-white">
                {unreadCount > 99 ? "99+" : unreadCount}
              </span>
            )}
          </div>
          Notifications
          {unreadCount > 0 && (
            <span className="ml-auto px-1.5 py-0.5 rounded text-[10px] font-semibold bg-red-500/10 text-red-400">
              {unreadCount}
            </span>
          )}
        </button>
      ) : (
        <button
          onClick={handleOpen}
          className={cn(
            "relative flex items-center justify-center w-9 h-9 rounded-xl transition-all",
            "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
            isOpen && "bg-accent text-foreground"
          )}
        >
          <Bell className="h-4 w-4" />
          {unreadCount > 0 && (
            <span className="absolute -top-0.5 -right-0.5 flex h-4 min-w-[16px] items-center justify-center rounded-full bg-red-500 px-1 text-[9px] font-bold text-white">
              {unreadCount > 99 ? "99+" : unreadCount}
            </span>
          )}
        </button>
      )}

      {isOpen && (
        <div className="absolute left-full ml-2 bottom-0 w-[360px] max-h-[480px] rounded-xl border border-border bg-card shadow-2xl z-50 overflow-hidden flex flex-col">
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-border">
            <h3 className="text-sm font-semibold">Notifications</h3>
            <div className="flex items-center gap-1">
              {unreadCount > 0 && (
                <button
                  onClick={handleMarkAllRead}
                  className="flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
                  title="Mark all as read"
                >
                  <CheckCheck className="h-3 w-3" />
                  Read all
                </button>
              )}
              <button
                onClick={() => setIsOpen(false)}
                className="p-1 rounded-lg text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>

          {/* Notification list */}
          <div className="flex-1 overflow-y-auto">
            {notifications.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
                <Bell className="h-8 w-8 mb-2 opacity-30" />
                <p className="text-xs">No notifications yet</p>
              </div>
            ) : (
              notifications.map((notif) => (
                <div
                  key={notif.id}
                  className={cn(
                    "group flex gap-3 px-4 py-3 border-b border-border/50 transition-colors hover:bg-accent/30",
                    !notif.read && "bg-accent/10",
                    priorityIndicator[notif.priority]
                  )}
                >
                  {/* Type dot */}
                  <div className="pt-0.5">
                    <div className={cn("h-2 w-2 rounded-full", typeColors[notif.type] || typeColors.info)} />
                  </div>

                  {/* Content */}
                  <div className="flex-1 min-w-0">
                    <p className={cn("text-xs font-medium", !notif.read && "text-foreground")}>
                      {notif.title}
                    </p>
                    {notif.message && (
                      <p className="text-[11px] text-muted-foreground mt-0.5 line-clamp-2">
                        {notif.message}
                      </p>
                    )}
                    {/* Approval buttons */}
                    {notif.type === "approval" && Array.isArray(notif.meta?.options) && (
                      <div className="flex gap-1.5 mt-2">
                        {(notif.meta.options as string[]).map((opt, i) => (
                          <button
                            key={i}
                            className={cn(
                              "px-2.5 py-1 rounded-lg text-[11px] font-medium transition-colors",
                              i === 0
                                ? "bg-primary text-primary-foreground hover:bg-primary/90"
                                : "bg-accent text-foreground hover:bg-accent/80"
                            )}
                          >
                            {opt}
                          </button>
                        ))}
                      </div>
                    )}
                    <p className="text-[10px] text-muted-foreground/60 mt-1">
                      {formatTime(notif.created_at)}
                    </p>
                  </div>

                  {/* Actions */}
                  <div className="flex flex-col gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    {!notif.read && (
                      <button
                        onClick={() => handleMarkRead(notif.id)}
                        className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
                        title="Mark as read"
                      >
                        <Check className="h-3 w-3" />
                      </button>
                    )}
                    <button
                      onClick={() => handleDelete(notif.id)}
                      className="p-1 rounded text-muted-foreground hover:text-red-400 hover:bg-accent transition-colors"
                      title="Delete"
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
