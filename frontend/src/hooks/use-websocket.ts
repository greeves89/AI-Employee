"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import type { LogEvent } from "@/lib/types";

const WS_URL = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";

export function useWebSocket(path: string) {
  const [messages, setMessages] = useState<LogEvent[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeout = useRef<ReturnType<typeof setTimeout>>();

  const connect = useCallback(() => {
    const ws = new WebSocket(`${WS_URL}/api/v1${path}`);
    wsRef.current = ws;

    ws.onopen = () => setIsConnected(true);

    ws.onclose = () => {
      setIsConnected(false);
      reconnectTimeout.current = setTimeout(connect, 3000);
    };

    ws.onerror = () => ws.close();

    ws.onmessage = (event) => {
      try {
        const data: LogEvent = JSON.parse(event.data);
        setMessages((prev) => [...prev.slice(-500), data]); // Keep last 500
      } catch {
        // Ignore non-JSON messages
      }
    };
  }, [path]);

  useEffect(() => {
    connect();
    return () => {
      wsRef.current?.close();
      clearTimeout(reconnectTimeout.current);
    };
  }, [connect]);

  const clearMessages = useCallback(() => setMessages([]), []);

  return { messages, isConnected, clearMessages };
}
