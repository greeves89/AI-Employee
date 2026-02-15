"use client";

import { useState } from "react";
import {
  Brain,
  Bell,
  ChevronDown,
  ChevronRight,
  Cpu,
  ListTodo,
  MessageSquare,
  Network,
  Calendar,
  Search,
  Save,
  Shield,
  Trash2,
  Users,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface McpServer {
  name: string;
  icon: typeof Brain;
  color: string;
  description: string;
  tools: { name: string; description: string; icon: typeof Save }[];
}

const MCP_SERVERS: McpServer[] = [
  {
    name: "Memory",
    icon: Brain,
    color: "text-purple-400",
    description:
      "Persistent long-term memory that survives across conversations. " +
      "The agent automatically saves user preferences, contacts, project details, " +
      "procedures, decisions, facts, and learnings.",
    tools: [
      {
        name: "memory_save",
        description: "Save important information to categorized memory",
        icon: Save,
      },
      {
        name: "memory_search",
        description: "Search memories by keyword and/or category",
        icon: Search,
      },
      {
        name: "memory_list",
        description: "List all memories, filtered by category",
        icon: ListTodo,
      },
      {
        name: "memory_delete",
        description: "Delete a specific memory",
        icon: Trash2,
      },
    ],
  },
  {
    name: "Notifications",
    icon: Bell,
    color: "text-amber-400",
    description:
      "Agents can notify you via Web UI and Telegram. High-priority " +
      "notifications are pushed to Telegram. Critical actions require explicit " +
      "approval before the agent proceeds.",
    tools: [
      {
        name: "notify_user",
        description: "Send notification (Web UI + Telegram for high/urgent)",
        icon: Bell,
      },
      {
        name: "request_approval",
        description: "Ask for explicit approval before critical actions",
        icon: Shield,
      },
    ],
  },
  {
    name: "Orchestrator",
    icon: Cpu,
    color: "text-blue-400",
    description:
      "Task management, team communication, and scheduling. Agents can " +
      "create tasks, delegate work, communicate with teammates, and set up " +
      "recurring schedules.",
    tools: [
      {
        name: "create_task",
        description: "Create tasks for self or other agents",
        icon: ListTodo,
      },
      {
        name: "list_team",
        description: "See all team members with roles and status",
        icon: Users,
      },
      {
        name: "send_message",
        description: "Send a text message to another agent",
        icon: MessageSquare,
      },
      {
        name: "create_schedule",
        description: "Create recurring task schedules",
        icon: Calendar,
      },
    ],
  },
];

export function McpInfo() {
  const [expanded, setExpanded] = useState(false);
  const [openServer, setOpenServer] = useState<string | null>(null);

  return (
    <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden">
      {/* Collapsible header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center justify-between w-full px-5 py-3 hover:bg-foreground/[0.02] transition-colors"
      >
        <div className="flex items-center gap-2.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br from-blue-500/10 to-purple-500/10">
            <Network className="h-3.5 w-3.5 text-blue-400" />
          </div>
          <div className="text-left">
            <span className="text-sm font-medium">MCP Tools</span>
            <span className="text-[10px] text-muted-foreground/60 ml-2">
              Model Context Protocol
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
            {MCP_SERVERS.length} servers active
          </span>
          <ChevronDown
            className={cn(
              "h-4 w-4 text-muted-foreground transition-transform duration-200",
              expanded && "rotate-180"
            )}
          />
        </div>
      </button>

      {expanded && (
        <div className="border-t border-foreground/[0.06]">
          {/* What is MCP? */}
          <div className="px-5 py-3 bg-foreground/[0.02]">
            <p className="text-[11px] text-muted-foreground leading-relaxed">
              <span className="font-semibold text-foreground">MCP (Model Context Protocol)</span>{" "}
              gives agents native tools that appear directly in Claude&apos;s tool list.
              Each MCP server is a standalone service that can be used by any MCP-compatible
              client (Claude Code, Claude Desktop, etc.). The agent communicates with
              the orchestrator API through these servers.
            </p>
          </div>

          {/* Server list */}
          <div className="divide-y divide-foreground/[0.04]">
            {MCP_SERVERS.map((server) => {
              const Icon = server.icon;
              const isOpen = openServer === server.name;
              return (
                <div key={server.name}>
                  <button
                    onClick={() =>
                      setOpenServer(isOpen ? null : server.name)
                    }
                    className="flex items-center gap-3 w-full px-5 py-3 hover:bg-foreground/[0.02] transition-colors"
                  >
                    <Icon className={cn("h-4 w-4 shrink-0", server.color)} />
                    <div className="flex-1 text-left min-w-0">
                      <span className="text-xs font-medium">
                        mcp-{server.name.toLowerCase()}
                      </span>
                      <span className="text-[10px] text-muted-foreground/60 ml-2">
                        {server.tools.length} tools
                      </span>
                    </div>
                    <ChevronRight
                      className={cn(
                        "h-3.5 w-3.5 text-muted-foreground/40 transition-transform duration-200",
                        isOpen && "rotate-90"
                      )}
                    />
                  </button>

                  {isOpen && (
                    <div className="px-5 pb-3 space-y-2">
                      <p className="text-[11px] text-muted-foreground leading-relaxed pl-7">
                        {server.description}
                      </p>
                      <div className="pl-7 space-y-1">
                        {server.tools.map((tool) => {
                          const ToolIcon = tool.icon;
                          return (
                            <div
                              key={tool.name}
                              className="flex items-center gap-2.5 py-1.5 px-3 rounded-lg bg-foreground/[0.02]"
                            >
                              <ToolIcon className="h-3 w-3 text-muted-foreground/60 shrink-0" />
                              <div className="min-w-0">
                                <span className="text-[11px] font-mono font-medium text-foreground/80">
                                  {tool.name}
                                </span>
                                <span className="text-[10px] text-muted-foreground/60 ml-2">
                                  {tool.description}
                                </span>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
