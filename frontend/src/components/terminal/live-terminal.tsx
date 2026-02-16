"use client";

import { useEffect, useRef } from "react";
import { useWebSocket } from "@/hooks/use-websocket";
import type { LogEvent } from "@/lib/types";

interface LiveTerminalProps {
  agentId: string;
}

export function LiveTerminal({ agentId }: LiveTerminalProps) {
  const { messages, isConnected, clearMessages } = useWebSocket(
    `/ws/agents/${agentId}/logs`
  );
  const containerRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom
  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [messages]);

  return (
    <div className="rounded-lg border border-border bg-black">
      {/* Terminal header */}
      <div className="flex items-center justify-between border-b border-border px-4 py-2">
        <div className="flex items-center gap-2">
          <div
            className={`h-2 w-2 rounded-full ${isConnected ? "bg-green-500" : "bg-red-500"}`}
          />
          <span className="text-xs text-muted-foreground">
            {isConnected ? "Connected" : "Disconnected"}
          </span>
        </div>
        <button
          onClick={clearMessages}
          className="text-xs text-muted-foreground hover:text-foreground"
        >
          Clear
        </button>
      </div>

      {/* Terminal content */}
      <div
        ref={containerRef}
        className="h-96 overflow-y-auto p-4 font-mono text-sm"
      >
        {messages.length === 0 ? (
          <div className="text-muted-foreground">
            Waiting for agent activity...
          </div>
        ) : (
          messages.map((msg, i) => (
            <LogLine key={i} event={msg} />
          ))
        )}
      </div>
    </div>
  );
}

function LogLine({ event }: { event: LogEvent }) {
  const time = new Date(event.timestamp).toLocaleTimeString();
  const data = event.data as Record<string, unknown>;

  switch (event.type) {
    case "text":
      return (
        <div className="text-green-400">
          <span className="text-muted-foreground text-xs mr-2">{time}</span>
          {String(data.text || "")}
        </div>
      );
    case "tool_call":
      return (
        <div className="text-blue-400">
          <span className="text-muted-foreground text-xs mr-2">{time}</span>
          <span className="text-yellow-400">[{String(data.tool || "")}]</span>{" "}
          <span className="text-muted-foreground">
            {JSON.stringify(data.input || {}).slice(0, 200)}
          </span>
        </div>
      );
    case "tool_result":
      return (
        <div className="text-gray-400 text-xs ml-4">
          {String(data.content || "").slice(0, 300)}
        </div>
      );
    case "error":
      return (
        <div className="text-red-400">
          <span className="text-muted-foreground text-xs mr-2">{time}</span>
          ERROR: {String(data.message || "")}
        </div>
      );
    case "system":
      return (
        <div className="text-purple-400 text-xs">
          <span className="text-muted-foreground mr-2">{time}</span>
          {String(data.message || "")}
        </div>
      );
    case "result":
      return (
        <div className="text-emerald-400 border-t border-border mt-2 pt-2">
          <span className="text-muted-foreground text-xs mr-2">{time}</span>
          Task completed - Cost: ${Number(data.cost_usd || 0).toFixed(4)} |
          Duration: {Number(data.duration_ms || 0)}ms |
          Turns: {Number(data.num_turns || 0)}
        </div>
      );
    default:
      return (
        <div className="text-muted-foreground text-xs">
          {JSON.stringify(event.data)}
        </div>
      );
  }
}
