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
  const failCountRef = useRef(0);
  const wsToken = useAuthStore((s) => s.wsToken);

  const connect = useCallback(() => {
    // Don't connect if unmounted (prevents StrictMode double-connection)
    if (!mountedRef.current) return;

    // Stop retrying after 5 consecutive failures (likely auth error)
    if (failCountRef.current >= 5) return;

    // Close any existing connection first
    if (wsRef.current) {
      wsRef.current.onclose = null; // Prevent reconnect from old WS
      wsRef.current.close();
    }

    const tokenParam = wsToken ? `${path.includes("?") ? "&" : "?"}token=${wsToken}` : "";
    const ws = new WebSocket(`${getWsUrl()}/api/v1${path}${tokenParam}`);
    wsRef.current = ws;
    let wasOpen = false;

    ws.onopen = () => {
      if (mountedRef.current) setIsConnected(true);
      wasOpen = true;
      failCountRef.current = 0; // Reset on successful connection
    };

    ws.onclose = () => {
      if (!mountedRef.current) return; // Don't reconnect after unmount
      setIsConnected(false);

      if (!wasOpen) {
        // Connection was rejected (403/401) — count as failure
        failCountRef.current++;
      }

      // Exponential backoff: 3s, 6s, 12s, 24s, then stop
      if (failCountRef.current >= 5) return;
      const delay = 3000 * Math.pow(2, Math.min(failCountRef.current, 3));
      reconnectTimeout.current = setTimeout(connect, delay);
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
  }, [path, wsToken]);

  useEffect(() => {
    mountedRef.current = true;
    failCountRef.current = 0; // Reset when token changes
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
