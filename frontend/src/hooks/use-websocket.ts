"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import type { LogEvent } from "@/lib/types";
import { useAuthStore } from "@/lib/auth";

import { getWsUrl } from "@/lib/config";

export function useWebSocket(path: string) {
  const [messages, setMessages] = useState<LogEvent[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeout = useRef<ReturnType<typeof setTimeout>>();
  const mountedRef = useRef(false);
  const wsToken = useAuthStore((s) => s.wsToken);

  const connect = useCallback(() => {
    // Don't connect if unmounted (prevents StrictMode double-connection)
    if (!mountedRef.current) return;

    // Close any existing connection first
    if (wsRef.current) {
      wsRef.current.onclose = null; // Prevent reconnect from old WS
      wsRef.current.close();
    }

    const tokenParam = wsToken ? `${path.includes("?") ? "&" : "?"}token=${wsToken}` : "";
    const ws = new WebSocket(`${getWsUrl()}/api/v1${path}${tokenParam}`);
    wsRef.current = ws;

    ws.onopen = () => {
      if (mountedRef.current) setIsConnected(true);
    };

    ws.onclose = () => {
      if (!mountedRef.current) return; // Don't reconnect after unmount
      setIsConnected(false);
      reconnectTimeout.current = setTimeout(connect, 3000);
    };

    ws.onerror = () => ws.close();

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        // Server sends history_start before replaying history - clear old messages
        if (data.type === "history_start") {
          setMessages([]);
          return;
        }
        setMessages((prev) => [...prev.slice(-500), data as LogEvent]); // Keep last 500
      } catch {
        // Ignore non-JSON messages
      }
    };
  }, [path]);

  useEffect(() => {
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      clearTimeout(reconnectTimeout.current);
      if (wsRef.current) {
        wsRef.current.onclose = null; // Prevent reconnect on cleanup close
        wsRef.current.close();
      }
    };
  }, [connect]);

  const clearMessages = useCallback(() => setMessages([]), []);

  return { messages, isConnected, clearMessages };
}
