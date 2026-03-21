import type { AdminUser, Agent, AgentMemory, AgentMode, AgentTemplate, AgentTodo, ApprovalRequest, Feedback, FeedbackListResponse, KnowledgeEntry, KnowledgeGraphEdge, KnowledgeGraphNode, KnowledgeTag, LLMConfig, LLMConfigResponse, Notification, PermissionPackage, ProactiveResponse, Task, Schedule, FileEntry, Settings, Integration, TodoListResponse, WebhookEvent } from "./types";
import { getApiUrl, getBase } from "./config";

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
      _refreshing = fetch(`${getApiUrl()}/api/v1/auth/refresh`, {
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
  return fetchJSON(`${getBase()}/agents/`);
}

export async function getAgent(id: string): Promise<Agent> {
  return fetchJSON(`${getBase()}/agents/${id}`);
}

export async function createAgent(
  name: string,
  model?: string,
  role?: string,
  permissions?: string[],
  budget_usd?: number,
  mode: AgentMode = "claude_code",
  llm_config?: LLMConfig,
): Promise<Agent> {
  return fetchJSON(`${getBase()}/agents/`, {
    method: "POST",
    body: JSON.stringify({ name, model, role, permissions, budget_usd, mode, llm_config }),
  });
}

export async function getAgentMessages(minutes: number = 60): Promise<{
  connections: { from: string; to: string; count: number; last_at: string }[];
  messages: { from: string; to: string; text: string; from_name: string; timestamp: string }[];
  total: number;
}> {
  return fetchJSON(`${getBase()}/agents/team/messages?minutes=${minutes}`);
}

export async function updateAgentModel(
  agentId: string,
  modelProvider: string,
  model: string,
): Promise<{ agent_id: string; model: string; model_provider: string }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/model`, {
    method: "PATCH",
    body: JSON.stringify({ model_provider: modelProvider, model }),
  });
}

export async function updateLLMConfig(
  agentId: string,
  config: Partial<LLMConfig>,
): Promise<{ agent_id: string; llm_config: LLMConfigResponse }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/llm-config`, {
    method: "PATCH",
    body: JSON.stringify(config),
  });
}

export async function getPermissionPackages(): Promise<{ packages: PermissionPackage[]; defaults: string[] }> {
  return fetchJSON(`${getBase()}/agents/permissions`);
}

export async function updateAgentPermissions(agentId: string, permissions: string[]): Promise<{ agent_id: string; permissions: string[]; warning?: string }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/permissions`, {
    method: "PATCH",
    body: JSON.stringify({ permissions }),
  });
}

export async function stopAgent(id: string): Promise<void> {
  await fetchJSON(`${getBase()}/agents/${id}/stop`, { method: "POST" });
}

export async function startAgent(id: string): Promise<void> {
  await fetchJSON(`${getBase()}/agents/${id}/start`, { method: "POST" });
}

export async function restartAgent(id: string): Promise<Agent> {
  return fetchJSON(`${getBase()}/agents/${id}/restart`, { method: "POST" });
}

export async function updateAgent(id: string): Promise<Agent> {
  return fetchJSON(`${getBase()}/agents/${id}/update`, { method: "POST" });
}

export async function removeAgent(id: string, removeData = false): Promise<void> {
  await fetchJSON(`${getBase()}/agents/${id}?remove_data=${removeData}`, {
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
  return fetchJSON(`${getBase()}/tasks/?${params}`);
}

export async function getTask(id: string): Promise<Task> {
  return fetchJSON(`${getBase()}/tasks/${id}`);
}

export async function createTask(data: {
  title: string;
  prompt: string;
  priority?: number;
  agent_id?: string;
  model?: string;
}): Promise<Task> {
  return fetchJSON(`${getBase()}/tasks/`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function deleteTask(id: string): Promise<void> {
  await fetchJSON(`${getBase()}/tasks/${id}`, { method: "DELETE" });
}

export async function cancelTask(id: string): Promise<Task> {
  return fetchJSON(`${getBase()}/tasks/${id}/cancel`, { method: "POST" });
}

// Knowledge
export async function getAgentKnowledge(
  agentId: string
): Promise<{ knowledge: string; metrics: Record<string, number> }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/knowledge`);
}

export async function updateAgentKnowledge(
  agentId: string,
  content: string
): Promise<void> {
  await fetchJSON(`${getBase()}/agents/${agentId}/knowledge`, {
    method: "PUT",
    body: JSON.stringify({ content }),
  });
}

// Schedules
export async function getSchedules(): Promise<{ schedules: Schedule[]; total: number }> {
  return fetchJSON(`${getBase()}/schedules/`);
}

export async function createSchedule(data: {
  name: string;
  prompt: string;
  interval_seconds: number;
  priority?: number;
  agent_id?: string;
  model?: string;
}): Promise<Schedule> {
  return fetchJSON(`${getBase()}/schedules/`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateSchedule(
  id: string,
  data: Record<string, unknown>
): Promise<Schedule> {
  return fetchJSON(`${getBase()}/schedules/${id}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export async function deleteSchedule(id: string): Promise<void> {
  await fetchJSON(`${getBase()}/schedules/${id}`, { method: "DELETE" });
}

export async function pauseSchedule(id: string): Promise<void> {
  await fetchJSON(`${getBase()}/schedules/${id}/pause`, { method: "POST" });
}

export async function resumeSchedule(id: string): Promise<void> {
  await fetchJSON(`${getBase()}/schedules/${id}/resume`, { method: "POST" });
}

export async function triggerSchedule(id: string): Promise<{ status: string; task_id: string }> {
  return fetchJSON(`${getBase()}/schedules/${id}/trigger`, { method: "POST" });
}

// Files
export async function getFiles(
  agentId: string,
  path = "/workspace"
): Promise<{ path: string; entries: FileEntry[] }> {
  return fetchJSON(
    `${getBase()}/agents/${agentId}/files?path=${encodeURIComponent(path)}`
  );
}

export function getFileDownloadUrl(agentId: string, path: string): string {
  return `${getBase()}/agents/${agentId}/files/download?path=${encodeURIComponent(path)}`;
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
    `${getBase()}/agents/${agentId}/files/upload?path=${encodeURIComponent(path)}`,
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
  return fetchJSON(`${getBase()}/agents/${agentId}/chat/sessions`);
}

export async function getChatHistory(
  agentId: string,
  limit = 500,
  sessionId?: string,
  beforeId?: number,
): Promise<{ messages: ChatHistoryMessage[]; has_more: boolean }> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (sessionId) params.set("session_id", sessionId);
  if (beforeId !== undefined) params.set("before_id", String(beforeId));
  return fetchJSON(`${getBase()}/agents/${agentId}/chat/history?${params}`);
}

export async function deleteChatSession(
  agentId: string,
  sessionId: string,
): Promise<{ deleted: number }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/chat/sessions/${sessionId}`, {
    method: "DELETE",
  });
}

// Integrations (OAuth)
export async function getIntegrations(): Promise<{ integrations: Integration[] }> {
  return fetchJSON(`${getBase()}/integrations/`);
}

export async function getAuthUrl(provider: string): Promise<{ auth_url: string; provider: string }> {
  return fetchJSON(`${getBase()}/integrations/${provider}/auth`);
}

export async function disconnectIntegration(provider: string): Promise<void> {
  await fetchJSON(`${getBase()}/integrations/${provider}`, { method: "DELETE" });
}

export async function getAgentIntegrations(agentId: string): Promise<{ agent_id: string; integrations: string[] }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/integrations`);
}

export async function updateAgentIntegrations(agentId: string, integrations: string[]): Promise<void> {
  await fetchJSON(`${getBase()}/agents/${agentId}/integrations`, {
    method: "PATCH",
    body: JSON.stringify({ integrations }),
  });
}

// Manual code exchange (Anthropic OAuth)
export async function exchangeOAuthCode(
  provider: string,
  code: string,
  state: string,
): Promise<{ status: string; provider: string; account_label?: string; expires_at?: string }> {
  return fetchJSON(`${getBase()}/integrations/${provider}/exchange-code`, {
    method: "POST",
    body: JSON.stringify({ code, state }),
  });
}

// PAT-based integrations (GitHub)
export async function savePatToken(provider: string, token: string): Promise<{ status: string; provider: string; account_label?: string }> {
  return fetchJSON(`${getBase()}/integrations/${provider}/pat`, {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

// Per-agent MCP servers
export async function getAgentMcpServers(agentId: string): Promise<{ agent_id: string; mcp_servers: number[] | null }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/mcp-servers`);
}

export async function updateAgentMcpServers(agentId: string, mcpServers: number[] | null): Promise<void> {
  await fetchJSON(`${getBase()}/agents/${agentId}/mcp-servers`, {
    method: "PATCH",
    body: JSON.stringify({ mcp_servers: mcpServers }),
  });
}

// Settings
export async function getSettings(): Promise<Settings> {
  return fetchJSON(`${getBase()}/settings/`);
}

export async function updateSettings(data: Record<string, unknown>): Promise<void> {
  await fetchJSON(`${getBase()}/settings/`, {
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
  return fetchJSON(`${getBase()}/memory/agents/${agentId}?${params}`);
}

export async function updateMemory(
  memoryId: number,
  data: { content?: string; importance?: number; category?: string },
): Promise<AgentMemory> {
  return fetchJSON(`${getBase()}/memory/${memoryId}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export async function deleteMemory(memoryId: number): Promise<void> {
  await fetchJSON(`${getBase()}/memory/${memoryId}`, { method: "DELETE" });
}

// Notifications
export async function getNotifications(
  unreadOnly = false,
): Promise<{ notifications: Notification[] }> {
  return fetchJSON(`${getBase()}/notifications/?unread_only=${unreadOnly}`);
}

export async function getUnreadCount(): Promise<{ unread: number }> {
  return fetchJSON(`${getBase()}/notifications/count`);
}

export async function markNotificationRead(id: number): Promise<void> {
  await fetchJSON(`${getBase()}/notifications/${id}/read`, { method: "POST" });
}

export async function markAllNotificationsRead(): Promise<void> {
  await fetchJSON(`${getBase()}/notifications/read-all`, { method: "POST" });
}

export async function deleteNotification(id: number): Promise<void> {
  await fetchJSON(`${getBase()}/notifications/${id}`, { method: "DELETE" });
}

// Proactive Mode
export async function getProactiveConfig(agentId: string): Promise<ProactiveResponse> {
  return fetchJSON(`${getBase()}/agents/${agentId}/proactive`);
}

export async function updateProactiveConfig(
  agentId: string,
  config: { enabled: boolean; interval_seconds: number; prompt?: string },
): Promise<void> {
  await fetchJSON(`${getBase()}/agents/${agentId}/proactive`, {
    method: "POST",
    body: JSON.stringify(config),
  });
}

export async function deleteProactiveConfig(agentId: string): Promise<void> {
  await fetchJSON(`${getBase()}/agents/${agentId}/proactive`, { method: "DELETE" });
}

// Webhooks
export async function getWebhookEvents(
  agentId: string,
): Promise<{ events: WebhookEvent[] }> {
  return fetchJSON(`${getBase()}/webhooks/agents/${agentId}/events`);
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
  return fetchJSON(`${getBase()}/mcp-servers`);
}

export async function addMcpServer(name: string, url: string): Promise<McpServerInfo> {
  return fetchJSON(`${getBase()}/mcp-servers`, {
    method: "POST",
    body: JSON.stringify({ name, url }),
  });
}

export async function refreshMcpServer(id: number): Promise<McpServerInfo> {
  return fetchJSON(`${getBase()}/mcp-servers/${id}/refresh`, { method: "POST" });
}

export async function updateMcpServer(id: number, data: { name?: string; url?: string; enabled?: boolean }): Promise<McpServerInfo> {
  return fetchJSON(`${getBase()}/mcp-servers/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export async function deleteMcpServer(id: number): Promise<void> {
  await fetchJSON(`${getBase()}/mcp-servers/${id}`, { method: "DELETE" });
}

export async function probeMcpServer(name: string, url: string): Promise<{ url: string; tools: McpTool[]; tool_count: number }> {
  return fetchJSON(`${getBase()}/mcp-servers/probe`, {
    method: "POST",
    body: JSON.stringify({ name, url }),
  });
}

// Admin: User Management
export async function getUsers(): Promise<{ users: AdminUser[] }> {
  return fetchJSON(`${getBase()}/auth/users`);
}

export async function createUser(data: {
  name: string;
  email: string;
  password: string;
  role?: string;
}): Promise<AdminUser> {
  return fetchJSON(`${getBase()}/auth/users`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateUser(
  userId: string,
  data: { name?: string; role?: string; is_active?: boolean },
): Promise<AdminUser> {
  return fetchJSON(`${getBase()}/auth/users/${userId}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export async function deleteUser(userId: string): Promise<void> {
  await fetchJSON(`${getBase()}/auth/users/${userId}`, { method: "DELETE" });
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
  return fetchJSON(`${getBase()}/admin/agents/${agentId}/stats`);
}

export interface AdminOverview {
  users: { total: number; active: number };
  agents: { total: number };
  tasks: { total: number; completed: number; failed: number };
  cost: { total_usd: number };
}

export async function getAdminOverview(): Promise<AdminOverview> {
  return fetchJSON(`${getBase()}/admin/overview`);
}

// Agent Templates
export async function getTemplates(): Promise<{ templates: AgentTemplate[] }> {
  return fetchJSON(`${getBase()}/templates`);
}

export async function createTemplate(data: {
  name: string;
  display_name: string;
  description?: string;
  icon?: string;
  category?: string;
  model?: string;
  role?: string;
  permissions?: string[];
  integrations?: string[];
  knowledge_template?: string;
}): Promise<AgentTemplate> {
  return fetchJSON(`${getBase()}/templates`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateTemplate(
  templateId: number,
  data: {
    display_name?: string;
    description?: string;
    icon?: string;
    category?: string;
    model?: string;
    role?: string;
    permissions?: string[];
    integrations?: string[];
    knowledge_template?: string;
  },
): Promise<AgentTemplate> {
  return fetchJSON(`${getBase()}/templates/${templateId}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export async function deleteTemplate(templateId: number): Promise<void> {
  await fetchJSON(`${getBase()}/templates/${templateId}`, { method: "DELETE" });
}

export async function createAgentFromTemplate(
  templateId: number,
  name?: string,
): Promise<Agent> {
  return fetchJSON(`${getBase()}/templates/${templateId}/create-agent`, {
    method: "POST",
    body: JSON.stringify({ name: name || undefined }),
  });
}

// --- Agent TODOs ---

export async function getAgentTodos(
  agentId: string,
  status?: string,
  taskId?: string,
  project?: string,
): Promise<TodoListResponse> {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (taskId) params.set("task_id", taskId);
  if (project) params.set("project", project);
  const qs = params.toString() ? `?${params}` : "";
  return fetchJSON(`${getBase()}/todos/agents/${agentId}${qs}`);
}

export async function createAgentTodo(
  agentId: string,
  data: { title: string; description?: string; task_id?: string; project?: string; project_path?: string; priority?: number },
): Promise<AgentTodo> {
  return fetchJSON(`${getBase()}/todos/agents/${agentId}`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateAgentTodo(
  todoId: number,
  data: { title?: string; description?: string; status?: string; priority?: number; sort_order?: number },
): Promise<AgentTodo> {
  return fetchJSON(`${getBase()}/todos/${todoId}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export async function deleteAgentTodo(todoId: number): Promise<void> {
  return fetchJSON(`${getBase()}/todos/${todoId}`, { method: "DELETE" });
}

// --- Feedback ---

export async function createFeedback(data: {
  title: string;
  description?: string;
  category?: string;
}): Promise<Feedback> {
  return fetchJSON(`${getBase()}/feedback/`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function getFeedback(status?: string): Promise<FeedbackListResponse> {
  const params = status ? `?status=${status}` : "";
  return fetchJSON(`${getBase()}/feedback/${params}`);
}

export async function updateFeedback(
  feedbackId: number,
  data: { status?: string; admin_notes?: string },
): Promise<Feedback> {
  return fetchJSON(`${getBase()}/feedback/${feedbackId}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export async function deleteFeedback(feedbackId: number): Promise<void> {
  await fetchJSON(`${getBase()}/feedback/${feedbackId}`, { method: "DELETE" });
}

export async function createGithubIssueFromFeedback(
  feedbackId: number,
): Promise<{ issue_url: string; issue_number: number; feedback: Feedback }> {
  return fetchJSON(`${getBase()}/feedback/${feedbackId}/github-issue`, {
    method: "POST",
  });
}

// --- Command Approvals ---

export async function getPendingApprovals(): Promise<{ approvals: ApprovalRequest[]; count: number }> {
  return fetchJSON(`${getBase()}/approvals/pending`);
}

export async function approveCommand(approvalId: string): Promise<{ approval_id: string; status: string }> {
  return fetchJSON(`${getBase()}/approvals/${approvalId}/approve`, {
    method: "POST",
  });
}

export async function denyCommand(approvalId: string, reason?: string): Promise<{ approval_id: string; status: string }> {
  return fetchJSON(`${getBase()}/approvals/${approvalId}/deny`, {
    method: "POST",
    body: JSON.stringify({ decision: "deny", reason: reason || null }),
  });
}

// --- Docker Apps ---

import type { DockerApp, DockerAppContainer, DockerAppLog } from "./types";

export async function getDockerApps(agentId: string): Promise<{ apps: DockerApp[] }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/apps`);
}

export async function startDockerApp(
  agentId: string,
  path: string,
): Promise<{ project: string; status: string; containers: DockerAppContainer[]; output: string }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/apps/up?path=${encodeURIComponent(path)}`, {
    method: "POST",
  });
}

export async function stopDockerApp(
  agentId: string,
  path: string,
): Promise<{ project: string; status: string; output: string }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/apps/down?path=${encodeURIComponent(path)}`, {
    method: "POST",
  });
}

export async function getDockerAppStatus(
  agentId: string,
  path: string,
): Promise<{ project: string; status: string; containers: DockerAppContainer[]; running: number; total: number }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/apps/status?path=${encodeURIComponent(path)}`);
}

export async function getDockerAppLogs(
  agentId: string,
  path: string,
  service?: string,
  lines = 100,
): Promise<{ logs: DockerAppLog[]; project: string; total_lines: number }> {
  const params = new URLSearchParams({ path, lines: String(lines) });
  if (service) params.set("service", service);
  return fetchJSON(`${getBase()}/agents/${agentId}/apps/logs?${params}`);
}

export async function rebuildDockerApp(
  agentId: string,
  path: string,
): Promise<{ project: string; status: string; containers: DockerAppContainer[]; output: string }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/apps/rebuild?path=${encodeURIComponent(path)}`, {
    method: "POST",
  });
}

export async function restartDockerService(
  agentId: string,
  path: string,
  service: string,
): Promise<{ project: string; service: string; status: string; containers: DockerAppContainer[] }> {
  const params = new URLSearchParams({ path, service });
  return fetchJSON(`${getBase()}/agents/${agentId}/apps/restart-service?${params}`, {
    method: "POST",
  });
}

// --- Per-Agent Telegram ---

export interface AgentTelegramConfig {
  agent_id: string;
  has_token: boolean;
  auth_key: string;
  bot_running: boolean;
  error?: string;
}

export async function getAgentTelegram(agentId: string): Promise<AgentTelegramConfig> {
  return fetchJSON(`${getBase()}/agents/${agentId}/telegram`);
}

export async function setAgentTelegram(agentId: string, botToken: string): Promise<AgentTelegramConfig> {
  return fetchJSON(`${getBase()}/agents/${agentId}/telegram`, {
    method: "PUT",
    body: JSON.stringify({ bot_token: botToken }),
  });
}

export async function removeAgentTelegram(agentId: string): Promise<void> {
  return fetchJSON(`${getBase()}/agents/${agentId}/telegram`, { method: "DELETE" });
}

export async function regenerateTelegramKey(agentId: string): Promise<{ agent_id: string; auth_key: string }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/telegram/regenerate-key`, { method: "POST" });
}

// --- Knowledge Base ---

export async function getKnowledgeEntries(q?: string, tag?: string): Promise<{ entries: KnowledgeEntry[]; total: number }> {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (tag) params.set("tag", tag);
  const qs = params.toString() ? `?${params}` : "";
  return fetchJSON(`${getBase()}/knowledge/entries${qs}`);
}

export async function getKnowledgeEntry(id: number): Promise<KnowledgeEntry> {
  return fetchJSON(`${getBase()}/knowledge/entries/${id}`);
}

export async function createKnowledgeEntry(data: { title: string; content: string; tags: string[] }): Promise<KnowledgeEntry> {
  return fetchJSON(`${getBase()}/knowledge/entries`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateKnowledgeEntry(id: number, data: { title?: string; content?: string; tags?: string[] }): Promise<KnowledgeEntry> {
  return fetchJSON(`${getBase()}/knowledge/entries/${id}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export async function deleteKnowledgeEntry(id: number): Promise<void> {
  return fetchJSON(`${getBase()}/knowledge/entries/${id}`, { method: "DELETE" });
}

export async function getKnowledgeTags(): Promise<{ tags: KnowledgeTag[] }> {
  return fetchJSON(`${getBase()}/knowledge/tags`);
}

export async function getKnowledgeGraph(): Promise<{ nodes: KnowledgeGraphNode[]; edges: KnowledgeGraphEdge[] }> {
  return fetchJSON(`${getBase()}/knowledge/graph`);
}
