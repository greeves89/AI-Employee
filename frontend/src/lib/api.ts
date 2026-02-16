import type { AdminUser, Agent, AgentMemory, AgentTemplate, Notification, PermissionPackage, ProactiveResponse, Task, Schedule, FileEntry, Settings, Integration, WebhookEvent } from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const BASE = `${API_URL}/api/v1`;

let _refreshing: Promise<void> | null = null;

async function fetchJSON<T>(url: string, options?: RequestInit, _isRetry = false): Promise<T> {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    ...options,
  });

  // Auto-refresh on 401 and retry once
  if (res.status === 401 && !_isRetry) {
    if (!_refreshing) {
      _refreshing = fetch(`${API_URL}/api/v1/auth/refresh`, {
        method: "POST",
        credentials: "include",
      }).then((r) => {
        if (!r.ok) throw new Error("Refresh failed");
      }).finally(() => {
        _refreshing = null;
      });
    }
    try {
      await _refreshing;
      return fetchJSON(url, options, true);
    } catch {
      if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
        window.location.href = "/login";
      }
      throw new Error("Session expired");
    }
  }

  if (!res.ok) {
    const error = await res.text();
    throw new Error(`API Error ${res.status}: ${error}`);
  }
  return res.json();
}

// Agents
export async function getAgents(): Promise<{ agents: Agent[]; total: number }> {
  return fetchJSON(`${BASE}/agents/`);
}

export async function getAgent(id: string): Promise<Agent> {
  return fetchJSON(`${BASE}/agents/${id}`);
}

export async function createAgent(name: string, model?: string, role?: string, permissions?: string[], budget_usd?: number): Promise<Agent> {
  return fetchJSON(`${BASE}/agents/`, {
    method: "POST",
    body: JSON.stringify({ name, model, role, permissions, budget_usd }),
  });
}

export async function getPermissionPackages(): Promise<{ packages: PermissionPackage[]; defaults: string[] }> {
  return fetchJSON(`${BASE}/agents/permissions`);
}

export async function updateAgentPermissions(agentId: string, permissions: string[]): Promise<{ agent_id: string; permissions: string[]; warning?: string }> {
  return fetchJSON(`${BASE}/agents/${agentId}/permissions`, {
    method: "PATCH",
    body: JSON.stringify({ permissions }),
  });
}

export async function stopAgent(id: string): Promise<void> {
  await fetchJSON(`${BASE}/agents/${id}/stop`, { method: "POST" });
}

export async function startAgent(id: string): Promise<void> {
  await fetchJSON(`${BASE}/agents/${id}/start`, { method: "POST" });
}

export async function restartAgent(id: string): Promise<Agent> {
  return fetchJSON(`${BASE}/agents/${id}/restart`, { method: "POST" });
}

export async function updateAgent(id: string): Promise<Agent> {
  return fetchJSON(`${BASE}/agents/${id}/update`, { method: "POST" });
}

export async function removeAgent(id: string, removeData = false): Promise<void> {
  await fetchJSON(`${BASE}/agents/${id}?remove_data=${removeData}`, {
    method: "DELETE",
  });
}

// Tasks
export async function getTasks(
  status?: string,
  agentId?: string
): Promise<{ tasks: Task[]; total: number }> {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (agentId) params.set("agent_id", agentId);
  return fetchJSON(`${BASE}/tasks/?${params}`);
}

export async function getTask(id: string): Promise<Task> {
  return fetchJSON(`${BASE}/tasks/${id}`);
}

export async function createTask(data: {
  title: string;
  prompt: string;
  priority?: number;
  agent_id?: string;
  model?: string;
}): Promise<Task> {
  return fetchJSON(`${BASE}/tasks/`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function deleteTask(id: string): Promise<void> {
  await fetchJSON(`${BASE}/tasks/${id}`, { method: "DELETE" });
}

export async function cancelTask(id: string): Promise<Task> {
  return fetchJSON(`${BASE}/tasks/${id}/cancel`, { method: "POST" });
}

// Knowledge
export async function getAgentKnowledge(
  agentId: string
): Promise<{ knowledge: string; metrics: Record<string, number> }> {
  return fetchJSON(`${BASE}/agents/${agentId}/knowledge`);
}

export async function updateAgentKnowledge(
  agentId: string,
  content: string
): Promise<void> {
  await fetchJSON(`${BASE}/agents/${agentId}/knowledge`, {
    method: "PUT",
    body: JSON.stringify({ content }),
  });
}

// Schedules
export async function getSchedules(): Promise<{ schedules: Schedule[]; total: number }> {
  return fetchJSON(`${BASE}/schedules/`);
}

export async function createSchedule(data: {
  name: string;
  prompt: string;
  interval_seconds: number;
  priority?: number;
  agent_id?: string;
  model?: string;
}): Promise<Schedule> {
  return fetchJSON(`${BASE}/schedules/`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateSchedule(
  id: string,
  data: Record<string, unknown>
): Promise<Schedule> {
  return fetchJSON(`${BASE}/schedules/${id}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export async function deleteSchedule(id: string): Promise<void> {
  await fetchJSON(`${BASE}/schedules/${id}`, { method: "DELETE" });
}

export async function pauseSchedule(id: string): Promise<void> {
  await fetchJSON(`${BASE}/schedules/${id}/pause`, { method: "POST" });
}

export async function resumeSchedule(id: string): Promise<void> {
  await fetchJSON(`${BASE}/schedules/${id}/resume`, { method: "POST" });
}

// Files
export async function getFiles(
  agentId: string,
  path = "/workspace"
): Promise<{ path: string; entries: FileEntry[] }> {
  return fetchJSON(
    `${BASE}/agents/${agentId}/files?path=${encodeURIComponent(path)}`
  );
}

export function getFileDownloadUrl(agentId: string, path: string): string {
  return `${BASE}/agents/${agentId}/files/download?path=${encodeURIComponent(path)}`;
}

export async function uploadFiles(
  agentId: string,
  path: string,
  files: FileList | File[]
): Promise<{ uploaded: number; path: string }> {
  const formData = new FormData();
  for (const file of Array.from(files)) {
    formData.append("files", file);
  }
  const res = await fetch(
    `${BASE}/agents/${agentId}/files/upload?path=${encodeURIComponent(path)}`,
    { method: "POST", body: formData, credentials: "include" }
  );
  if (!res.ok) {
    const error = await res.text();
    throw new Error(`Upload failed: ${error}`);
  }
  return res.json();
}

// Chat History & Sessions
export interface ChatHistoryMessage {
  id: string;
  role: "user" | "assistant" | "system" | "error";
  content: string;
  timestamp: string;
  toolCalls?: { tool: string; input: string }[];
  meta?: { cost_usd?: number; duration_ms?: number; num_turns?: number };
  sessionId?: string;
}

export interface ChatSession {
  id: string;
  started_at: string | null;
  last_message_at: string | null;
  message_count: number;
  preview: string;
}

export async function getChatSessions(
  agentId: string,
): Promise<{ sessions: ChatSession[] }> {
  return fetchJSON(`${BASE}/agents/${agentId}/chat/sessions`);
}

export async function getChatHistory(
  agentId: string,
  limit = 100,
  sessionId?: string,
): Promise<{ messages: ChatHistoryMessage[]; has_more: boolean }> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (sessionId) params.set("session_id", sessionId);
  return fetchJSON(`${BASE}/agents/${agentId}/chat/history?${params}`);
}

export async function deleteChatSession(
  agentId: string,
  sessionId: string,
): Promise<{ deleted: number }> {
  return fetchJSON(`${BASE}/agents/${agentId}/chat/sessions/${sessionId}`, {
    method: "DELETE",
  });
}

// Integrations (OAuth)
export async function getIntegrations(): Promise<{ integrations: Integration[] }> {
  return fetchJSON(`${BASE}/integrations/`);
}

export async function getAuthUrl(provider: string): Promise<{ auth_url: string; provider: string }> {
  return fetchJSON(`${BASE}/integrations/${provider}/auth`);
}

export async function disconnectIntegration(provider: string): Promise<void> {
  await fetchJSON(`${BASE}/integrations/${provider}`, { method: "DELETE" });
}

export async function getAgentIntegrations(agentId: string): Promise<{ agent_id: string; integrations: string[] }> {
  return fetchJSON(`${BASE}/agents/${agentId}/integrations`);
}

export async function updateAgentIntegrations(agentId: string, integrations: string[]): Promise<void> {
  await fetchJSON(`${BASE}/agents/${agentId}/integrations`, {
    method: "PATCH",
    body: JSON.stringify({ integrations }),
  });
}

// Settings
export async function getSettings(): Promise<Settings> {
  return fetchJSON(`${BASE}/settings/`);
}

export async function updateSettings(data: Record<string, unknown>): Promise<void> {
  await fetchJSON(`${BASE}/settings/`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

// Agent Memory
export async function getAgentMemories(
  agentId: string,
  category?: string,
): Promise<{ memories: AgentMemory[]; total: number; categories: Record<string, number> }> {
  const params = new URLSearchParams();
  if (category) params.set("category", category);
  return fetchJSON(`${BASE}/memory/agents/${agentId}?${params}`);
}

export async function updateMemory(
  memoryId: number,
  data: { content?: string; importance?: number; category?: string },
): Promise<AgentMemory> {
  return fetchJSON(`${BASE}/memory/${memoryId}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export async function deleteMemory(memoryId: number): Promise<void> {
  await fetchJSON(`${BASE}/memory/${memoryId}`, { method: "DELETE" });
}

// Notifications
export async function getNotifications(
  unreadOnly = false,
): Promise<{ notifications: Notification[] }> {
  return fetchJSON(`${BASE}/notifications/?unread_only=${unreadOnly}`);
}

export async function getUnreadCount(): Promise<{ unread: number }> {
  return fetchJSON(`${BASE}/notifications/count`);
}

export async function markNotificationRead(id: number): Promise<void> {
  await fetchJSON(`${BASE}/notifications/${id}/read`, { method: "POST" });
}

export async function markAllNotificationsRead(): Promise<void> {
  await fetchJSON(`${BASE}/notifications/read-all`, { method: "POST" });
}

export async function deleteNotification(id: number): Promise<void> {
  await fetchJSON(`${BASE}/notifications/${id}`, { method: "DELETE" });
}

// Proactive Mode
export async function getProactiveConfig(agentId: string): Promise<ProactiveResponse> {
  return fetchJSON(`${BASE}/agents/${agentId}/proactive`);
}

export async function updateProactiveConfig(
  agentId: string,
  config: { enabled: boolean; interval_seconds: number; prompt?: string },
): Promise<void> {
  await fetchJSON(`${BASE}/agents/${agentId}/proactive`, {
    method: "POST",
    body: JSON.stringify(config),
  });
}

export async function deleteProactiveConfig(agentId: string): Promise<void> {
  await fetchJSON(`${BASE}/agents/${agentId}/proactive`, { method: "DELETE" });
}

// Webhooks
export async function getWebhookEvents(
  agentId: string,
): Promise<{ events: WebhookEvent[] }> {
  return fetchJSON(`${BASE}/webhooks/agents/${agentId}/events`);
}

// MCP Servers
export interface McpServerInfo {
  id: number;
  name: string;
  url: string;
  tools: McpTool[];
  enabled: boolean;
  created_at: string | null;
}

export interface McpTool {
  name: string;
  description?: string;
  inputSchema?: Record<string, unknown>;
}

export async function getMcpServers(): Promise<{ servers: McpServerInfo[] }> {
  return fetchJSON(`${BASE}/mcp-servers`);
}

export async function addMcpServer(name: string, url: string): Promise<McpServerInfo> {
  return fetchJSON(`${BASE}/mcp-servers`, {
    method: "POST",
    body: JSON.stringify({ name, url }),
  });
}

export async function refreshMcpServer(id: number): Promise<McpServerInfo> {
  return fetchJSON(`${BASE}/mcp-servers/${id}/refresh`, { method: "POST" });
}

export async function updateMcpServer(id: number, data: { name?: string; url?: string; enabled?: boolean }): Promise<McpServerInfo> {
  return fetchJSON(`${BASE}/mcp-servers/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export async function deleteMcpServer(id: number): Promise<void> {
  await fetchJSON(`${BASE}/mcp-servers/${id}`, { method: "DELETE" });
}

export async function probeMcpServer(name: string, url: string): Promise<{ url: string; tools: McpTool[]; tool_count: number }> {
  return fetchJSON(`${BASE}/mcp-servers/probe`, {
    method: "POST",
    body: JSON.stringify({ name, url }),
  });
}

// Admin: User Management
export async function getUsers(): Promise<{ users: AdminUser[] }> {
  return fetchJSON(`${BASE}/auth/users`);
}

export async function createUser(data: {
  name: string;
  email: string;
  password: string;
  role?: string;
}): Promise<AdminUser> {
  return fetchJSON(`${BASE}/auth/users`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateUser(
  userId: string,
  data: { name?: string; role?: string; is_active?: boolean },
): Promise<AdminUser> {
  return fetchJSON(`${BASE}/auth/users/${userId}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export async function deleteUser(userId: string): Promise<void> {
  await fetchJSON(`${BASE}/auth/users/${userId}`, { method: "DELETE" });
}

// Admin: Agent Stats
export interface AdminAgentStats {
  agent: {
    id: string;
    name: string;
    container_id: string | null;
    state: string;
    model: string;
    role: string;
    created_at: string | null;
    updated_at: string | null;
  };
  owner: { id: string; name: string; email: string; role: string } | null;
  stats: {
    total_tasks: number;
    completed_tasks: number;
    failed_tasks: number;
    total_cost_usd: number;
    total_duration_ms: number;
    total_turns: number;
    chat_sessions: number;
    chat_messages: number;
  };
  visibility: { scope: string; reason?: string; user?: Record<string, string>; count?: number }[];
  recent_tasks: {
    id: string;
    title: string;
    status: string;
    cost_usd: number | null;
    duration_ms: number | null;
    num_turns: number | null;
    created_at: string | null;
    completed_at: string | null;
  }[];
}

export async function getAdminAgentStats(agentId: string): Promise<AdminAgentStats> {
  return fetchJSON(`${BASE}/admin/agents/${agentId}/stats`);
}

export interface AdminOverview {
  users: { total: number; active: number };
  agents: { total: number };
  tasks: { total: number; completed: number; failed: number };
  cost: { total_usd: number };
}

export async function getAdminOverview(): Promise<AdminOverview> {
  return fetchJSON(`${BASE}/admin/overview`);
}

// Agent Templates
export async function getTemplates(): Promise<{ templates: AgentTemplate[] }> {
  return fetchJSON(`${BASE}/templates`);
}

export async function createAgentFromTemplate(
  templateId: number,
  name?: string,
): Promise<Agent> {
  return fetchJSON(`${BASE}/templates/${templateId}/create-agent`, {
    method: "POST",
    body: JSON.stringify({ name: name || undefined }),
  });
}
