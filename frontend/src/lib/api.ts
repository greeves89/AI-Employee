import type { AdminUser, Agent, AgentMemory, AgentMode, AgentTemplate, AgentTodo, AIAccount, ApprovalRequest, AuditLog, AuditSummary, Feedback, FeedbackListResponse, KnowledgeEntry, KnowledgeGraphEdge, KnowledgeGraphNode, KnowledgeTag, LLMConfig, LLMConfigResponse, MeetingRoom, Notification, PermissionPackage, ProactiveResponse, Task, Schedule, FileEntry, Settings, SecondBrain, Integration, TodoListResponse, WebhookEvent } from "./types";
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
export async function getAgents(scope: "own" | "all" = "own"): Promise<{ agents: Agent[]; total: number }> {
  return fetchJSON(`${getBase()}/agents/?scope=${scope}`);
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
  autonomy_level?: string,
  budget_exceeded_action: "haiku" | "stop" = "haiku",
  ai_account_id?: number,
): Promise<Agent> {
  return fetchJSON(`${getBase()}/agents/`, {
    method: "POST",
    body: JSON.stringify({ name, model, role, permissions, budget_usd, mode, llm_config, autonomy_level, budget_exceeded_action, ai_account_id }),
  });
}

export async function setAgentAutonomyLevel(
  agentId: string,
  level: string,
): Promise<{ agent_id: string; autonomy_level: string }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/autonomy-level`, {
    method: "POST",
    body: JSON.stringify({ level }),
  });
}

// --- Autonomy capability matrix (3-state: allow/ask/deny) ---
export type AutonomyState = "allow" | "ask" | "deny";
export interface AutonomyCapability { key: string; group: string; label: string; description: string; }
export interface AutonomyTaxonomy {
  groups: { key: string; label: string }[];
  states: AutonomyState[];
  capabilities: AutonomyCapability[];
  presets: Record<string, { label: string; matrix: Record<string, AutonomyState> }>;
}
export interface AutonomyMatrixResponse {
  agent_id: string;
  autonomy_level: string;
  matrix: Record<string, AutonomyState>;
  taxonomy: AutonomyTaxonomy;
}
export async function getAutonomyMatrix(agentId: string): Promise<AutonomyMatrixResponse> {
  return fetchJSON(`${getBase()}/agents/${agentId}/autonomy-matrix`);
}
export async function updateAutonomyMatrix(
  agentId: string,
  matrix: Record<string, AutonomyState>,
): Promise<{ agent_id: string; autonomy_level: string; matrix: Record<string, AutonomyState> }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/autonomy-matrix`, {
    method: "PUT",
    body: JSON.stringify({ matrix }),
  });
}

export async function getAgentMessages(minutes: number = 60): Promise<{
  connections: { from: string; to: string; count: number; last_at: string }[];
  messages: { from: string; to: string; text: string; from_name: string; timestamp: string }[];
  total: number;
}> {
  return fetchJSON(`${getBase()}/agents/team/messages?minutes=${minutes}`);
}

export async function getAgentConversation(agentA: string, agentB: string): Promise<{
  messages: { from_id: string; from_name: string; to_id: string; text: string; timestamp: string }[];
  total: number;
}> {
  return fetchJSON(`${getBase()}/agents/team/conversation?agent_a=${agentA}&agent_b=${agentB}`);
}

export interface AgentTeam {
  id: string;
  name: string;
  description?: string;
  member_agent_ids: string[];
  lead_agent_id: string | null;
  is_active?: boolean;
}

export async function getTeams(): Promise<{ teams: AgentTeam[] }> {
  return fetchJSON(`${getBase()}/teams/`);
}

/** Delegation edges: tasks one agent handed to another (delegator -> assignee). */
export async function getDelegations(minutes: number = 1440): Promise<{
  edges: { from: string; to: string; count: number; last_title: string; last_at: string | null }[];
  total: number;
}> {
  return fetchJSON(`${getBase()}/agents/team/delegations?minutes=${minutes}`);
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

export interface RealtimeModelOption {
  account_id: number;
  account_name: string;
  provider_type: string;
  provider_label: string;
  engine: string;
  implemented: boolean;
  model_id: string;
  model_label: string;
  value: string;  // "<account_id>:<model_id>"
  label: string;  // "<model> · <account>"
}

/** Realtime voice models available from configured AI-accounts (for the selector). */
export async function getRealtimeModels(): Promise<RealtimeModelOption[]> {
  const r = await fetchJSON<{ models: RealtimeModelOption[] }>(`${getBase()}/ai-accounts/realtime-models`);
  return r.models || [];
}

/** Set the agent's realtime voice front — null interactionModel = classic pipeline. */
export async function updateAgentInteractionModel(
  agentId: string,
  opts: {
    interactionModel: string | null;
    interactionAccountId?: number | null;
    interactionModelId?: string | null;
    interactionVoice?: string | null;
  },
): Promise<{ agent_id: string; interaction_model: string | null }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/interaction-model`, {
    method: "PUT",
    body: JSON.stringify({
      interaction_model: opts.interactionModel,
      interaction_account_id: opts.interactionAccountId ?? null,
      interaction_model_id: opts.interactionModelId ?? null,
      interaction_voice: opts.interactionVoice ?? null,
    }),
  });
}

export interface ModelCatalogModel {
  value: string;
  label: string;
  tier: string;
}
export interface ModelCatalogProvider {
  provider: string;
  models: ModelCatalogModel[];
}
export interface ModelCatalogMode {
  mode: string;
  label: string;
  default_provider: string;
  default_model: string;
  providers: ModelCatalogProvider[];
}
// Provider/model catalog per harness — single source of truth served by the
// backend so create-modal and settings don't keep divergent hardcoded lists.
export async function getModelCatalog(): Promise<{ modes: ModelCatalogMode[] }> {
  return fetchJSON(`${getBase()}/agents/models`);
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

export async function updateAgentBrowserMode(agentId: string, browserMode: boolean): Promise<{ browser_mode: boolean }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/browser-mode`, {
    method: "PATCH",
    body: JSON.stringify({ browser_mode: browserMode }),
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

// Volume Mounts
export interface MountCatalogEntry {
  label: string;
  container_path: string;
  mode: "ro" | "rw";
}

export async function getAgentMountCatalog(): Promise<{ mounts: MountCatalogEntry[] }> {
  return fetchJSON(`${getBase()}/settings/agent-mounts`);
}

export interface MountAccessGrant {
  mount_label: string;
  mode: "ro" | "rw";
}

export async function getUserMountAccess(userId: string): Promise<{ grants: MountAccessGrant[] }> {
  return fetchJSON(`${getBase()}/settings/agent-mounts/access/${userId}`);
}

export async function setUserMountAccess(userId: string, grants: MountAccessGrant[]): Promise<{ user_id: string; grants: MountAccessGrant[] }> {
  return fetchJSON(`${getBase()}/settings/agent-mounts/access/${userId}`, {
    method: "PUT",
    body: JSON.stringify({ grants }),
  });
}

export async function getIdleStopMax(): Promise<{ max_idle_minutes: number }> {
  return fetchJSON(`${getBase()}/settings/idle-stop`);
}

export async function setIdleStopMax(max_idle_minutes: number): Promise<{ max_idle_minutes: number }> {
  return fetchJSON(`${getBase()}/settings/idle-stop`, {
    method: "PUT",
    body: JSON.stringify({ max_idle_minutes }),
  });
}

export async function setMsgraphMcpExternal(enabled: boolean): Promise<{ msgraph_mcp_external_enabled: boolean }> {
  return fetchJSON(`${getBase()}/settings/msgraph-mcp-external`, {
    method: "PUT",
    body: JSON.stringify({ enabled }),
  });
}

export async function setAgentIdleStop(agentId: string, idle_stop_minutes: number): Promise<{ agent_id: string; idle_stop_minutes: number | null }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/idle-stop`, {
    method: "PATCH",
    body: JSON.stringify({ idle_stop_minutes }),
  });
}

export interface RolePermissions {
  max_agents?: number | null;
  template_ids?: number[] | null;
  llm_providers?: string[] | null;
  mount_labels?: string[] | null;
  ai_account_ids?: number[] | null;
  secret_ids?: number[] | null;
  mcp_server_ids?: number[] | null;
  url_host_patterns?: string[] | null;
  menu_paths?: string[] | null;
}

export interface CustomRole {
  id: number;
  name: string;
  description: string | null;
  permissions: RolePermissions;
  is_system?: boolean;
}

export async function listRoles(): Promise<{ roles: CustomRole[] }> {
  return fetchJSON(`${getBase()}/roles/`);
}

export async function createRole(name: string, description: string, permissions: RolePermissions): Promise<CustomRole> {
  return fetchJSON(`${getBase()}/roles/`, {
    method: "POST",
    body: JSON.stringify({ name, description, permissions }),
  });
}

export async function updateRole(id: number, body: { name?: string; description?: string; permissions?: RolePermissions }): Promise<CustomRole> {
  return fetchJSON(`${getBase()}/roles/${id}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function deleteRole(id: number): Promise<{ deleted: number }> {
  return fetchJSON(`${getBase()}/roles/${id}`, { method: "DELETE" });
}

export async function assignUserRole(userId: string, custom_role_id: number | null): Promise<{ user_id: string; custom_role_id: number | null }> {
  return fetchJSON(`${getBase()}/roles/users/${userId}/assign`, {
    method: "PUT",
    body: JSON.stringify({ custom_role_id }),
  });
}

export async function getMyPermissions(): Promise<{ permissions: RolePermissions; custom_role_id: number | null }> {
  return fetchJSON(`${getBase()}/roles/me/permissions`);
}

export async function getAgentMounts(agentId: string): Promise<{ agent_id: string; mounts: string[] }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/mounts`);
}

export async function updateAgentMounts(
  agentId: string,
  mounts: string[],
): Promise<{ agent_id: string; mounts: string[]; mount_modes?: Record<string, "ro" | "rw"> }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/mounts`, {
    method: "PATCH",
    body: JSON.stringify({ mounts }),
  });
}

export async function updateAgentResourceLimits(
  id: string,
  limits: { idle_timeout_minutes?: number | null; workspace_size_gb?: number | null },
): Promise<{ idle_timeout_minutes: number | null; workspace_size_gb: number | null }> {
  return fetchJSON(`${getBase()}/agents/${id}/resource-limits`, {
    method: "PATCH",
    body: JSON.stringify(limits),
  });
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

export interface TaskStep {
  sequence: number;
  type: string;
  data: Record<string, unknown>;
  timestamp: string | null;
}

export async function getTaskSteps(
  id: string,
): Promise<{ task_id: string; total_steps: number; steps: TaskStep[] }> {
  return fetchJSON(`${getBase()}/tasks/${id}/steps`);
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

export async function estimateTaskCost(data: {
  prompt: string;
  model?: string;
  agent_id?: string;
}): Promise<{
  estimated_input_tokens: number;
  model: string;
  min_usd: number;
  avg_usd: number;
  max_usd: number;
  agent_avg_usd: number | null;
}> {
  return fetchJSON(`${getBase()}/tasks/estimate`, {
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
  interval_seconds?: number;
  cron_expression?: string;
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

export async function deleteFile(agentId: string, path: string): Promise<void> {
  const res = await fetch(
    `${getBase()}/agents/${agentId}/files?path=${encodeURIComponent(path)}`,
    { method: "DELETE", credentials: "include" }
  );
  if (!res.ok) {
    const error = await res.text();
    throw new Error(`Delete failed: ${error}`);
  }
}

// Chat History & Sessions
export interface ChatHistoryMessage {
  id: string;
  role: "user" | "assistant" | "system" | "error";
  content: string;
  timestamp: string;
  toolCalls?: { tool: string; input: string }[];
  meta?: {
    cost_usd?: number;
    duration_ms?: number;
    num_turns?: number;
    input_tokens?: number;
    output_tokens?: number;
    presented_images?: { media_type: string; data: string }[];
    presented_files?: {
      path: string;
      filename: string;
      media_type?: string;
      size?: number;
      caption?: string;
    }[];
  };
  images?: { media_type: string; data: string }[];
  sessionId?: string;
}

export interface ChatSession {
  id: string;
  started_at: string | null;
  last_message_at: string | null;
  message_count: number;
  preview: string;
  title?: string | null;   // custom rename; falls back to preview when null
  pinned?: boolean;
}

export async function getChatSessions(
  agentId: string,
): Promise<{ sessions: ChatSession[] }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/chat/sessions`);
}

// Rename and/or pin a chat session (metadata is created lazily server-side).
export async function updateChatSession(
  agentId: string,
  sessionId: string,
  patch: { title?: string | null; pinned?: boolean },
): Promise<{ id: string; title: string | null; pinned: boolean }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/chat/sessions/${sessionId}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export async function deleteAllChatSessions(
  agentId: string,
): Promise<{ deleted: number }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/chat/sessions`, {
    method: "DELETE",
  });
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

export async function getAgentIntegrations(agentId: string): Promise<{ agent_id: string; integrations: string[]; msgraph_access?: string; exchange_access?: string }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/integrations`);
}

export async function updateAgentIntegrations(agentId: string, integrations: string[], msgraphAccess?: string, exchangeAccess?: string): Promise<void> {
  await fetchJSON(`${getBase()}/agents/${agentId}/integrations`, {
    method: "PATCH",
    body: JSON.stringify({
      integrations,
      ...(msgraphAccess ? { msgraph_access: msgraphAccess } : {}),
      ...(exchangeAccess ? { exchange_access: exchangeAccess } : {}),
    }),
  });
}

export async function updateAgentAppearance(agentId: string, icon: string, color: string): Promise<void> {
  await fetchJSON(`${getBase()}/agents/${agentId}/appearance`, {
    method: "PATCH",
    body: JSON.stringify({ icon, color }),
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

export async function saveAuthJson(provider: string, authJson: string): Promise<{ status: string; provider: string; account_label?: string }> {
  return fetchJSON(`${getBase()}/integrations/${provider}/auth-json`, {
    method: "POST",
    body: JSON.stringify({ auth_json: authJson }),
  });
}

export interface DeviceAuthStart {
  session_id: string;
  verification_uri: string;
  user_code: string;
  expires_at: string;
  status: string;
}

export interface DeviceAuthStatus {
  session_id: string;
  status: "pending" | "connected" | "error" | "expired" | "cancelled";
  expires_at: string;
  verification_uri?: string | null;
  user_code?: string | null;
  account_label?: string | null;
  error?: string | null;
}

export async function startDeviceAuth(provider: string): Promise<DeviceAuthStart> {
  return fetchJSON(`${getBase()}/integrations/${provider}/device-auth/start`, {
    method: "POST",
  });
}

export async function getDeviceAuthStatus(provider: string, sessionId: string): Promise<DeviceAuthStatus> {
  return fetchJSON(`${getBase()}/integrations/${provider}/device-auth/${sessionId}`);
}

export async function cancelDeviceAuth(provider: string, sessionId: string): Promise<{ status: string; provider: string }> {
  return fetchJSON(`${getBase()}/integrations/${provider}/device-auth/${sessionId}`, {
    method: "DELETE",
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

export async function respondToApproval(notificationId: number, choice: string): Promise<{ status: string; choice: string }> {
  return fetchJSON(`${getBase()}/notifications/${notificationId}/respond`, {
    method: "POST",
    body: JSON.stringify({ choice }),
  });
}

// Proactive Mode
export async function getProactiveConfig(agentId: string): Promise<ProactiveResponse> {
  return fetchJSON(`${getBase()}/agents/${agentId}/proactive`);
}

export async function updateProactiveConfig(
  agentId: string,
  config: {
    enabled: boolean;
    interval_seconds: number;
    prompt?: string;
    custom_instructions?: string;
  },
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
  has_auth?: boolean;
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

export async function addMcpServer(name: string, url: string, bearerToken?: string): Promise<McpServerInfo> {
  return fetchJSON(`${getBase()}/mcp-servers`, {
    method: "POST",
    body: JSON.stringify({ name, url, ...(bearerToken ? { bearer_token: bearerToken } : {}) }),
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
  custom_role_id?: number | null;
}): Promise<AdminUser> {
  return fetchJSON(`${getBase()}/auth/users`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateUser(
  userId: string,
  data: { name?: string; role?: string; is_active?: boolean; approved?: boolean },
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

export async function updateAgentBudget(
  agentId: string,
  budgetUsd: number | null,
  budgetExceededAction?: "haiku" | "stop",
): Promise<{ agent_id: string; budget_usd: number | null; budget_exceeded_action: string; status: string }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/budget`, {
    method: "PATCH",
    body: JSON.stringify({ budget_usd: budgetUsd, budget_exceeded_action: budgetExceededAction }),
  });
}

// AI Accounts (admin-managed, reusable LLM model accounts)
export interface AIAccountPayload {
  name: string;
  provider_type: string;
  api_endpoint?: string | null;
  api_key?: string | null;
  models: { name: string; provider_type: string; api_endpoint: string }[];
  extra?: Record<string, unknown>;
  is_active?: boolean;
}

export async function listAIAccounts(activeOnly = false): Promise<AIAccount[]> {
  return fetchJSON(`${getBase()}/ai-accounts/?active_only=${activeOnly}`);
}

export async function createAIAccount(payload: AIAccountPayload): Promise<AIAccount> {
  return fetchJSON(`${getBase()}/ai-accounts/`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateAIAccount(id: number, payload: Partial<AIAccountPayload>): Promise<AIAccount> {
  return fetchJSON(`${getBase()}/ai-accounts/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteAIAccount(id: number): Promise<{ ok: boolean; id: number }> {
  return fetchJSON(`${getBase()}/ai-accounts/${id}`, { method: "DELETE" });
}

// ── Second Brains (department-shared knowledge vaults) ──
export interface SecondBrainPayload {
  name: string;
  slug?: string;
  default_mode?: "ro" | "rw";
  standard?: "freeform" | "wikimedia" | "it_support";
  description?: string | null;
  is_active?: boolean;
}

export interface BrainFileEntry {
  path: string;
  name: string;
  type: "dir" | "file";
}

export async function getBrainTree(id: number): Promise<{ entries: BrainFileEntry[]; standard: string }> {
  return fetchJSON(`${getBase()}/brains/${id}/tree`);
}

export async function getBrainFile(id: number, path: string): Promise<{ path: string; content: string }> {
  return fetchJSON(`${getBase()}/brains/${id}/file?path=${encodeURIComponent(path)}`);
}

export async function saveBrainFile(id: number, path: string, content: string): Promise<{ ok: boolean; path: string }> {
  return fetchJSON(`${getBase()}/brains/${id}/file`, { method: "PUT", body: JSON.stringify({ path, content }) });
}

export async function deleteBrainFile(id: number, path: string): Promise<{ ok: boolean; path: string }> {
  return fetchJSON(`${getBase()}/brains/${id}/file?path=${encodeURIComponent(path)}`, { method: "DELETE" });
}

// ── Vault knowledge graph (Obsidian-style: notes = nodes, [[wikilinks]] = edges) ──
// Named "Vault*" to stay distinct from getBrainGraph() (the personal Knowledge
// Base graph at /brain/graph) — same brain-vs-vault split as the backend tools.
export interface VaultGraphNode {
  id: string;
  name: string;
  path: string;
  folder: string;
  tags: string[];
  in: number;
  out: number;
  degree: number;
}

export interface VaultGraphEdge {
  source: string;
  target: string;
}

export interface VaultGraph {
  nodes: VaultGraphNode[];
  edges: VaultGraphEdge[];
  truncated?: boolean;
  brain?: { id: number; name: string; slug: string };
}

export async function getVaultGraph(id: number): Promise<VaultGraph> {
  return fetchJSON(`${getBase()}/brains/${id}/graph`);
}

export async function listSecondBrains(activeOnly = false): Promise<SecondBrain[]> {
  return fetchJSON(`${getBase()}/brains/?active_only=${activeOnly}`);
}

export async function createSecondBrain(payload: SecondBrainPayload): Promise<SecondBrain> {
  return fetchJSON(`${getBase()}/brains/`, { method: "POST", body: JSON.stringify(payload) });
}

export async function updateSecondBrain(id: number, payload: Partial<SecondBrainPayload>): Promise<SecondBrain> {
  return fetchJSON(`${getBase()}/brains/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
}

export async function deleteSecondBrain(id: number): Promise<{ ok: boolean; id: number }> {
  return fetchJSON(`${getBase()}/brains/${id}`, { method: "DELETE" });
}

// MCP exposure: generate/rotate the Bearer token (plaintext returned ONCE), or disable.
export async function generateBrainMcpToken(
  id: number,
): Promise<{ mcp_enabled: boolean; mcp_path: string; token: string }> {
  return fetchJSON(`${getBase()}/brains/${id}/mcp/token`, { method: "POST" });
}

export async function disableBrainMcp(id: number): Promise<{ ok: boolean; mcp_enabled: boolean }> {
  return fetchJSON(`${getBase()}/brains/${id}/mcp`, { method: "DELETE" });
}

export async function updateAgentAIAccount(
  agentId: string,
  aiAccountId: number,
  model?: string,
): Promise<{ agent_id: string; ai_account_id: number; status: string }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/ai-account`, {
    method: "PATCH",
    body: JSON.stringify({ ai_account_id: aiAccountId, model }),
  });
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

export async function publishTemplate(templateId: number): Promise<AgentTemplate> {
  return fetchJSON(`${getBase()}/templates/${templateId}/publish`, { method: "POST" });
}

export async function unpublishTemplate(templateId: number): Promise<AgentTemplate> {
  return fetchJSON(`${getBase()}/templates/${templateId}/unpublish`, { method: "POST" });
}

export async function createAgentFromTemplate(
  templateId: number,
  name?: string,
  budgetUsd?: number,
  budgetExceededAction: "haiku" | "stop" = "haiku",
): Promise<Agent> {
  return fetchJSON(`${getBase()}/templates/${templateId}/create-agent`, {
    method: "POST",
    body: JSON.stringify({
      name: name || undefined,
      budget_usd: budgetUsd,
      budget_exceeded_action: budgetExceededAction,
    }),
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

// Agent Assignments (Admin)
export async function assignAgentToUser(userId: string, templateId: number, name?: string, budgetUsd?: number): Promise<{ status: string; agent_id: string; agent_name: string; user_name: string; template_name: string }> {
  return fetchJSON(`${getBase()}/admin/assign-agent`, {
    method: "POST",
    body: JSON.stringify({ user_id: userId, template_id: templateId, name: name || undefined, budget_usd: budgetUsd || undefined }),
  });
}

export interface DistributeResult {
  status: string;
  source_agent_id: string;
  source_agent_name: string;
  created: { user_id: string; user_name: string; agent_id: string; agent_name: string }[];
  skipped: { user_id: string; user_name?: string; reason: string; agent_id?: string }[];
  created_count: number;
  skipped_count: number;
}

// Distribute a trained agent as an independent per-user copy (explicit users + a role's members).
export async function distributeAgent(
  sourceAgentId: string,
  opts: { userIds?: string[]; roleId?: number | null; namePrefix?: string },
): Promise<DistributeResult> {
  return fetchJSON(`${getBase()}/admin/distribute-agent`, {
    method: "POST",
    body: JSON.stringify({
      source_agent_id: sourceAgentId,
      user_ids: opts.userIds || [],
      role_id: opts.roleId ?? null,
      name_prefix: opts.namePrefix || undefined,
    }),
  });
}

export async function getAssignments(): Promise<{ assignments: { agent_id: string; agent_name: string; user_id: string; user_name: string; user_email: string; template_id: number | null; template_name: string | null; state: string; model: string; role: string; created_at: string }[]; total: number }> {
  return fetchJSON(`${getBase()}/admin/assignments`);
}

export async function revokeAssignment(agentId: string): Promise<void> {
  await fetchJSON(`${getBase()}/admin/assignments/${agentId}`, { method: "DELETE" });
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

// --- Health & Performance ---

export interface HealthDashboard {
  status?: string;
  overall_status?: string;
  uptime_pct?: number;
  agents?: { id: string; name: string; state: string; health: string }[];
  agent_ratings?: Record<string, unknown>;
  recent_tests?: { id: string; status: string; passed: number; failed: number; created_at: string }[];
  improvements?: { agent_id: string; suggestion: string; priority: string }[];
  latest_run?: Record<string, unknown>;
  pass_rate_trend?: unknown[];
  response_time_trend?: unknown[];
  failure_categories?: Record<string, number>;
  open_auto_issues?: number;
  total_cost_7d?: number;
  total_tasks_7d?: number;
}

export interface TestRun {
  id: string;
  status: string;
  total_tests: number;
  passed: number;
  failed: number;
  skipped: number;
  duration_ms: number;
  results: { name: string; status: string; message?: string }[];
  created_at: string;
}

export async function getHealthDashboard(): Promise<HealthDashboard> {
  return fetchJSON(`${getBase()}/health/dashboard`);
}

export async function getTestRuns(): Promise<{ runs: TestRun[]; total: number }> {
  return fetchJSON(`${getBase()}/health/test-runs`);
}

export async function getLatestTestRun(): Promise<TestRun | null> {
  try { return await fetchJSON(`${getBase()}/health/test-runs/latest`); } catch { return null; }
}

export async function triggerTestRun(): Promise<TestRun> {
  return fetchJSON(`${getBase()}/health/test-runs/trigger`, { method: "POST" });
}

export interface ImprovementReport {
  agent_id: string;
  agent_name: string;
  total_ratings: number;
  average_rating: number | null;
  rating_trend: number[];
  cost_trend: (number | null)[];
  duration_trend: (number | null)[];
  top_issues: string[];
  summary: string;
}

export async function getImprovementReport(agentId: string): Promise<ImprovementReport> {
  return fetchJSON(`${getBase()}/ratings/agents/${agentId}/improvement-report`);
}

export interface AgentAutoMetrics {
  agent_id: string;
  agent_name: string;
  total_tasks: number;
  succeeded: number;
  failed: number;
  success_rate: number;
  avg_cost_usd: number | null;
  total_cost_usd: number | null;
  avg_duration_ms: number | null;
  avg_turns: number | null;
  daily: {
    date: string;
    total: number;
    succeeded: number;
    success_rate: number;
    cost: number;
    avg_duration_ms: number;
  }[];
  top_errors: { error: string; count: number }[];
}

export interface AutoMetrics {
  days: number;
  total_tasks: number;
  total_cost_usd: number;
  success_rate: number;
  agents: AgentAutoMetrics[];
}

export async function getAutoMetrics(days = 7): Promise<AutoMetrics> {
  return fetchJSON(`${getBase()}/health/auto-metrics?days=${days}`);
}

// --- Approval Rules ---

export interface ApprovalRule {
  id: number;
  name: string;
  description: string;
  category: string;
  threshold: number | null;
  is_active: boolean;
  agent_id: string | null;
  created_by: string | null;
  is_preset: boolean;
  created_at: string | null;
}

export interface PresetRule {
  id: number;
  level: string;
  name: string;
  description: string;
  category: string;
  sort_order: number;
  created_at: string | null;
}

export interface LevelPreset {
  level: string;
  label: string;
  description: string;
  rules: PresetRule[];
  rule_count: number;
}

export async function getApprovalRules(): Promise<{ rules: ApprovalRule[] }> {
  return fetchJSON(`${getBase()}/approval-rules/`);
}

export async function getLevelPresets(): Promise<{ presets: Record<string, LevelPreset> }> {
  return fetchJSON(`${getBase()}/approval-rules/level-presets`);
}

export async function createApprovalRule(data: {
  name: string;
  description: string;
  category: string;
  threshold?: number | null;
  agent_id?: string | null;
}): Promise<ApprovalRule> {
  return fetchJSON(`${getBase()}/approval-rules/`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateApprovalRule(
  id: number,
  data: Partial<Omit<ApprovalRule, "id" | "created_at">>,
): Promise<ApprovalRule> {
  return fetchJSON(`${getBase()}/approval-rules/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export async function deleteApprovalRule(id: number): Promise<{ status: string }> {
  return fetchJSON(`${getBase()}/approval-rules/${id}`, { method: "DELETE" });
}

// --- Command Policies ---

export type CommandPolicyEffect = "blocked" | "high" | "medium" | "allow";
export type CommandPolicyScope = "global" | "agent";

export interface CommandPolicy {
  id: number;
  name: string;
  pattern: string;
  effect: CommandPolicyEffect;
  scope: CommandPolicyScope;
  agent_id: string | null;
  description: string;
  is_active: boolean;
  sort_order: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface CommandPolicyInput {
  name: string;
  pattern: string;
  effect: CommandPolicyEffect;
  scope: CommandPolicyScope;
  agent_id?: string | null;
  description?: string;
  is_active?: boolean;
  sort_order?: number;
}

export async function getCommandPolicies(): Promise<{ policies: CommandPolicy[] }> {
  return fetchJSON(`${getBase()}/command-policies/`);
}

export async function createCommandPolicy(data: CommandPolicyInput): Promise<CommandPolicy> {
  return fetchJSON(`${getBase()}/command-policies/`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateCommandPolicy(
  id: number,
  data: Partial<CommandPolicyInput>,
): Promise<CommandPolicy> {
  return fetchJSON(`${getBase()}/command-policies/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export async function deleteCommandPolicy(id: number): Promise<{ status: string }> {
  return fetchJSON(`${getBase()}/command-policies/${id}`, { method: "DELETE" });
}

export async function addPresetRule(
  level: string,
  data: { name: string; description: string; category: string; sort_order?: number },
): Promise<PresetRule> {
  return fetchJSON(`${getBase()}/approval-rules/level-presets/${level}/rules`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function deletePresetRule(level: string, ruleId: number): Promise<{ status: string }> {
  return fetchJSON(`${getBase()}/approval-rules/level-presets/${level}/rules/${ruleId}`, {
    method: "DELETE",
  });
}

// --- Event Triggers ---

export interface EventTrigger {
  id: number;
  name: string;
  agent_id: string;
  source_filter: string | null;
  event_type_filter: string | null;
  payload_conditions: Record<string, unknown> | null;
  prompt_template: string;
  priority: number;
  model: string | null;
  enabled: boolean;
  fire_count: number;
  last_fired_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export async function getEventTriggers(agentId?: string): Promise<{ triggers: EventTrigger[]; total: number }> {
  const params = new URLSearchParams();
  if (agentId) params.set("agent_id", agentId);
  const qs = params.toString() ? `?${params}` : "";
  return fetchJSON(`${getBase()}/event-triggers${qs}`);
}

export async function createEventTrigger(data: {
  name: string;
  agent_id: string;
  source_filter?: string | null;
  event_type_filter?: string | null;
  payload_conditions?: Record<string, unknown> | null;
  prompt_template: string;
  priority?: number;
  model?: string | null;
}): Promise<EventTrigger> {
  return fetchJSON(`${getBase()}/event-triggers`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateEventTrigger(
  id: number,
  data: Partial<Omit<EventTrigger, "id" | "created_at" | "updated_at" | "fire_count" | "last_fired_at">>,
): Promise<EventTrigger> {
  return fetchJSON(`${getBase()}/event-triggers/${id}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export async function deleteEventTrigger(id: number): Promise<{ deleted: number }> {
  return fetchJSON(`${getBase()}/event-triggers/${id}`, { method: "DELETE" });
}

export async function toggleEventTrigger(id: number): Promise<EventTrigger> {
  return fetchJSON(`${getBase()}/event-triggers/${id}/toggle`, { method: "POST" });
}

export async function testEventTrigger(id: number, payload: Record<string, unknown>): Promise<{
  trigger_id: number;
  would_fire: boolean;
  interpolated_prompt: string;
}> {
  return fetchJSON(`${getBase()}/event-triggers/${id}/test`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// --- Skill Marketplace ---

export interface MarketplaceSkill {
  id: number;
  name: string;
  description: string;
  content: string;
  category: string;
  status: string;
  created_by: string;
  source_url: string | null;
  source_repo: string | null;
  paths: string[] | null;
  roles: string[] | null;
  usage_count: number;
  avg_rating: number | null;
  avg_agent_duration_ms: number | null;
  manual_duration_seconds: number | null;
  is_public: boolean;
  assigned_agents: string[];
  assigned_to_agent?: boolean;
  created_at: string | null;
  updated_at: string | null;
  improvement_status?: string | null;
  improvement_proposal?: {
    old_content: string;
    suggested_content: string;
    reason: string;
    avg_helpfulness_before?: number;
    rated_count_before?: number;
    generated_at?: string;
  } | null;
  improvement_proposed_at?: string | null;
  improvement_review_reason?: string | null;
}

export async function getMarketplaceSkills(params?: {
  category?: string; status?: string; q?: string; agent_id?: string;
}): Promise<{ skills: MarketplaceSkill[]; total: number }> {
  const sp = new URLSearchParams();
  if (params?.category) sp.set("category", params.category);
  if (params?.status) sp.set("status", params.status);
  if (params?.q) sp.set("q", params.q);
  if (params?.agent_id) sp.set("agent_id", params.agent_id);
  const qs = sp.toString() ? `?${sp}` : "";
  return fetchJSON(`${getBase()}/skills/marketplace${qs}`);
}

export async function getMarketplaceSkill(id: number): Promise<MarketplaceSkill> {
  return fetchJSON(`${getBase()}/skills/marketplace/${id}`);
}

export async function createMarketplaceSkill(data: {
  name: string; description?: string; content?: string; category?: string;
  paths?: string[] | null; roles?: string[] | null;
}): Promise<MarketplaceSkill> {
  return fetchJSON(`${getBase()}/skills/marketplace`, {
    method: "POST", body: JSON.stringify(data),
  });
}

export async function updateMarketplaceSkill(id: number, data: Record<string, unknown>): Promise<MarketplaceSkill> {
  return fetchJSON(`${getBase()}/skills/marketplace/${id}`, {
    method: "PUT", body: JSON.stringify(data),
  });
}

export async function deleteMarketplaceSkill(id: number): Promise<{ deleted: number }> {
  return fetchJSON(`${getBase()}/skills/marketplace/${id}`, { method: "DELETE" });
}

export async function assignSkill(skillId: number, agentId: string): Promise<{ status: string }> {
  return fetchJSON(`${getBase()}/skills/marketplace/${skillId}/assign`, {
    method: "POST", body: JSON.stringify({ agent_id: agentId, skill_id: skillId }),
  });
}

export async function unassignSkill(skillId: number, agentId: string): Promise<{ status: string }> {
  return fetchJSON(`${getBase()}/skills/marketplace/${skillId}/unassign/${agentId}`, { method: "DELETE" });
}

export async function approveSkill(id: number): Promise<MarketplaceSkill> {
  return fetchJSON(`${getBase()}/skills/marketplace/${id}/approve`, { method: "POST" });
}

export async function rejectSkill(id: number): Promise<MarketplaceSkill> {
  return fetchJSON(`${getBase()}/skills/marketplace/${id}/reject`, { method: "POST" });
}

export async function getPendingImprovements(): Promise<{ skills: MarketplaceSkill[] }> {
  return fetchJSON(`${getBase()}/skills/marketplace/improvements/pending`);
}

export async function approveSkillImprovement(id: number): Promise<{ id: number; improvement_status: string }> {
  return fetchJSON(`${getBase()}/skills/marketplace/${id}/approve-improvement`, { method: "POST" });
}

export async function rejectSkillImprovement(id: number): Promise<{ id: number; improvement_status: string | null }> {
  return fetchJSON(`${getBase()}/skills/marketplace/${id}/reject-improvement`, { method: "POST" });
}

export async function seedSkillsFromCrawler(): Promise<{ status: string; imported: number }> {
  return fetchJSON(`${getBase()}/skills/marketplace/seed`, { method: "POST" });
}

// --- License ---

export interface License {
  tier: string;
  issued_to: string;
  issued_at: string | null;
  expires_at: string | null;
  license_id: string | null;
  instance_limit: number;
  valid: boolean;
  is_expired: boolean;
  error: string | null;
  features: string[];
}

export async function getLicenseStatus(): Promise<License> {
  return fetchJSON(`${getBase()}/license/`);
}

export async function applyLicense(licenseKey: string): Promise<{ status: string; license: License }> {
  return fetchJSON(`${getBase()}/license/apply`, {
    method: "POST",
    body: JSON.stringify({ license_key: licenseKey }),
  });
}

export async function removeLicense(): Promise<{ status: string; tier: string }> {
  return fetchJSON(`${getBase()}/license/`, { method: "DELETE" });
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

// --- Skills Catalog ---

export interface CatalogSkill {
  name: string;
  description: string;
  repo: string;
  category: string;
  install_cmd: string;
  id?: number;
  type?: "db" | "github";
}

export interface AgentSkill {
  name: string;
  description: string;
  content: string;
}

export async function getSkillCatalog(): Promise<{
  skills: CatalogSkill[];
  crawled_at: string | null;
  repo_count: number;
  skill_count: number;
}> {
  return fetchJSON(`${getBase()}/skills/catalog`);
}

export async function refreshSkillCatalog(): Promise<{ detail: string }> {
  return fetchJSON(`${getBase()}/skills/catalog/refresh`, { method: "POST" });
}

export async function getAgentSkills(agentId: string): Promise<AgentSkill[]> {
  return fetchJSON(`${getBase()}/agents/${agentId}/skills`);
}

export async function installSkill(
  agentId: string,
  repo: string,
  skill: string,
): Promise<{ detail: string }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/skills/install`, {
    method: "POST",
    body: JSON.stringify({ repo, skill }),
  });
}

export async function assignDbSkill(
  skillId: number,
  agentId: string,
): Promise<{ status: string }> {
  return fetchJSON(`${getBase()}/skills/marketplace/${skillId}/assign`, {
    method: "POST",
    body: JSON.stringify({ agent_id: agentId, skill_id: skillId }),
  });
}

export async function createAgentSkill(
  agentId: string,
  skill: { name: string; description: string; content: string },
): Promise<AgentSkill> {
  return fetchJSON(`${getBase()}/agents/${agentId}/skills`, {
    method: "POST",
    body: JSON.stringify(skill),
  });
}

export async function updateAgentSkill(
  agentId: string,
  skillName: string,
  skill: { name: string; description: string; content: string },
): Promise<AgentSkill> {
  return fetchJSON(`${getBase()}/agents/${agentId}/skills/${encodeURIComponent(skillName)}`, {
    method: "PUT",
    body: JSON.stringify(skill),
  });
}

export async function deleteAgentSkill(agentId: string, skillName: string): Promise<void> {
  await fetchJSON(`${getBase()}/agents/${agentId}/skills/${encodeURIComponent(skillName)}`, {
    method: "DELETE",
  });
}

// --- Skill File Attachments ---

export interface SkillFileAttachment {
  id: number;
  skill_id: number;
  filename: string;
  content_type: string;
  size_bytes: number;
  created_at: string | null;
}

export async function getSkillFiles(skillId: number): Promise<{ files: SkillFileAttachment[] }> {
  return fetchJSON(`${getBase()}/skills/marketplace/${skillId}/files`);
}

export async function uploadSkillFile(skillId: number, file: File): Promise<SkillFileAttachment> {
  const base = getBase();
  const token = typeof window !== "undefined" ? localStorage.getItem("auth_token") : null;
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${base}/skills/marketplace/${skillId}/files`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Upload failed: ${res.status}`);
  }
  return res.json();
}

export async function downloadSkillFile(skillId: number, filename: string): Promise<void> {
  const base = getBase();
  // Cookie-based auth like the rest of the API — the auth cookie is only sent
  // with credentials:"include" (the old Bearer-from-localStorage was always null
  // → 401 → silent failure / "click does nothing").
  const res = await fetch(`${base}/skills/marketplace/${skillId}/files/${encodeURIComponent(filename)}`, {
    credentials: "include",
  });
  if (!res.ok) throw new Error(`Download failed: ${res.status}`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a); // some browsers require the anchor in the DOM
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export async function deleteSkillFile(skillId: number, filename: string): Promise<{ deleted: string }> {
  return fetchJSON(`${getBase()}/skills/marketplace/${skillId}/files/${encodeURIComponent(filename)}`, {
    method: "DELETE",
  });
}

// --- Agent Webhooks ---

export interface WebhookSettings {
  webhook_enabled: boolean;
  webhook_token: string | null;
}

export async function getWebhookSettings(agentId: string): Promise<WebhookSettings> {
  return fetchJSON(`${getBase()}/webhooks/agents/${agentId}/settings`);
}

export async function updateWebhookSettings(agentId: string, enabled: boolean): Promise<WebhookSettings> {
  return fetchJSON(`${getBase()}/webhooks/agents/${agentId}/settings`, {
    method: "PATCH",
    body: JSON.stringify({ webhook_enabled: enabled }),
  });
}

export async function regenerateWebhookToken(agentId: string): Promise<{ webhook_token: string }> {
  return fetchJSON(`${getBase()}/webhooks/agents/${agentId}/regenerate-token`, { method: "POST" });
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

export async function getBrainGraph(): Promise<{ nodes: KnowledgeGraphNode[]; edges: KnowledgeGraphEdge[] }> {
  return fetchJSON(`${getBase()}/brain/graph`);
}

// Audit Logs
export async function getAuditLogs(params?: {
  agent_id?: string;
  event_type?: string;
  outcome?: string;
  since?: string;
  limit?: number;
  offset?: number;
}): Promise<{ logs: AuditLog[]; total: number }> {
  const q = new URLSearchParams();
  if (params?.agent_id) q.set("agent_id", params.agent_id);
  if (params?.event_type) q.set("event_type", params.event_type);
  if (params?.outcome) q.set("outcome", params.outcome);
  if (params?.since) q.set("since", params.since);
  if (params?.limit != null) q.set("limit", String(params.limit));
  if (params?.offset != null) q.set("offset", String(params.offset));
  const qs = q.toString();
  return fetchJSON(`${getBase()}/audit/logs${qs ? `?${qs}` : ""}`);
}

export async function getAuditSummary(): Promise<AuditSummary> {
  return fetchJSON(`${getBase()}/audit/logs/summary`);
}

// Computer-Use Bridge Sessions
export interface ComputerUseSession {
  session_id: string;
  status: "connected" | "waiting_for_bridge" | "waiting";
  created_at: number;
  action_count: number;
  platform: string;
  bridge_version?: string | null;
  capabilities: string[];
  allowed_capabilities: string[];
  last_disconnected_at: number | null;
  bridge_last_seen_at: number | null;
}

export interface CapabilityGroup {
  id: string;
  actions: string[];
  default: boolean;
}

export async function listComputerUseSessions(): Promise<{ sessions: ComputerUseSession[] }> {
  return fetchJSON(`${getBase()}/computer-use/sessions`);
}

export async function createComputerUseSession(): Promise<{ session_id: string; status: string; ws_url: string; allowed_capabilities: string[] }> {
  return fetchJSON(`${getBase()}/computer-use/sessions`, { method: "POST" });
}

export async function deleteComputerUseSession(sessionId: string): Promise<void> {
  return fetchJSON(`${getBase()}/computer-use/sessions/${sessionId}`, { method: "DELETE" });
}

export async function getComputerUseSession(sessionId: string): Promise<ComputerUseSession> {
  return fetchJSON(`${getBase()}/computer-use/sessions/${sessionId}`);
}

export async function getComputerUseScreenshot(sessionId: string): Promise<{ screenshot_b64: string; ts: number }> {
  return fetchJSON(`${getBase()}/computer-use/sessions/${sessionId}/screenshot`);
}

export async function updateSessionCapabilities(
  sessionId: string,
  allowedCapabilities: string[],
): Promise<{ session_id: string; allowed_capabilities: string[] }> {
  return fetchJSON(`${getBase()}/computer-use/sessions/${sessionId}/capabilities`, {
    method: "PATCH",
    body: JSON.stringify({ allowed_capabilities: allowedCapabilities }),
  });
}

export async function getCapabilityGroups(): Promise<{ groups: CapabilityGroup[] }> {
  return fetchJSON(`${getBase()}/computer-use/capabilities`);
}

// --- Meeting Rooms ---

export async function getMeetingRooms(): Promise<{ rooms: MeetingRoom[] }> {
  return fetchJSON(`${getBase()}/meeting-rooms/`);
}

export async function getMeetingRoom(id: string): Promise<MeetingRoom> {
  return fetchJSON(`${getBase()}/meeting-rooms/${id}`);
}

export async function createMeetingRoom(data: {
  name: string;
  topic?: string;
  agent_ids: string[];
  max_rounds?: number;
  stages_config?: { name: string; rounds: number; focus: string }[] | null;
  use_moderator?: boolean;
  moderator_ai_account_id?: string;
}): Promise<MeetingRoom> {
  return fetchJSON(`${getBase()}/meeting-rooms/`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function deleteMeetingRoom(id: string): Promise<void> {
  return fetchJSON(`${getBase()}/meeting-rooms/${id}`, { method: "DELETE" });
}

export async function startMeetingRoom(
  id: string,
  initialMessage?: string,
): Promise<{ status: string; room_id: string }> {
  return fetchJSON(`${getBase()}/meeting-rooms/${id}/start`, {
    method: "POST",
    body: JSON.stringify({ initial_message: initialMessage || "" }),
  });
}

export async function stopMeetingRoom(id: string): Promise<{ status: string }> {
  return fetchJSON(`${getBase()}/meeting-rooms/${id}/stop`, { method: "POST" });
}

// Analytics
export async function getAnalyticsOverview(days = 30) {
  return fetchJSON<{
    period_days: number;
    total_tasks: number;
    completed_tasks: number;
    success_rate_pct: number;
    total_cost_usd: number;
    avg_duration_ms: number;
    total_time_saved_seconds: number;
    active_agents: number;
    avg_task_rating: number | null;
    daily_tasks: { date: string; count: number; cost: number }[];
  }>(`${getBase()}/analytics/overview?days=${days}`);
}

export async function getAnalyticsSkills(days = 30) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return fetchJSON<{ period_days: number; skills: any[] }>(`${getBase()}/analytics/skills?days=${days}`);
}

export async function getAnalyticsAgents(days = 30) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return fetchJSON<{ period_days: number; agents: any[] }>(`${getBase()}/analytics/agents?days=${days}`);
}

export async function getAnalyticsAgentDetail(agentId: string, days = 30) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return fetchJSON<any>(`${getBase()}/analytics/agents/${agentId}?days=${days}`);
}

export async function getSkillTrend(skillId: number, days = 60) {
  return fetchJSON<{
    skill_id: number;
    skill_name: string;
    manual_duration_seconds: number | null;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    trend: any[];
  }>(`${getBase()}/analytics/skills/${skillId}/trend?days=${days}`);
}

export async function updateSkillManualDuration(skillId: number, seconds: number | null) {
  return fetchJSON(`${getBase()}/skills/marketplace/${skillId}/manual-duration`, {
    method: "PATCH",
    body: JSON.stringify({ manual_duration_seconds: seconds }),
  });
}

// --- Cost Attribution ---

export interface AgentCostEntry {
  agent_id: string;
  agent_name: string;
  total_cost_usd: number;
  total_input_tokens: number;
  total_output_tokens: number;
  task_count: number;
}

export interface CostAttribution {
  top_agents: AgentCostEntry[];
  platform_total_usd: number;
  platform_total_input_tokens: number;
  platform_total_output_tokens: number;
}

export async function getCostAttribution(limit = 5): Promise<CostAttribution> {
  return fetchJSON(`${getBase()}/tasks/cost-attribution?limit=${limit}`);
}

// ── URL Allowlist ──────────────────────────────────────────────────────────────

export interface UrlAllowlistEntry {
  id: number;
  url_pattern: string;
  description: string;
  is_active: boolean;
}

export interface UrlAllowlistTemplate {
  id: number;
  name: string;
  description: string;
  is_builtin: boolean;
  entries: { url_pattern: string; description: string }[];
}

export async function getAgentUrlAllowlist(agentId: string): Promise<UrlAllowlistEntry[]> {
  const data = await fetchJSON(`${getBase()}/url-allowlist/agent/${agentId}`) as { entries?: UrlAllowlistEntry[] };
  return data.entries ?? [];
}

export async function getUrlAllowlistTemplates(): Promise<UrlAllowlistTemplate[]> {
  const data = await fetchJSON(`${getBase()}/url-allowlist/templates`) as { templates?: UrlAllowlistTemplate[] };
  return data.templates ?? [];
}

export async function addAgentUrl(agentId: string, url_pattern: string, description = ""): Promise<void> {
  await fetchJSON(`${getBase()}/url-allowlist/agent/${agentId}`, {
    method: "POST",
    body: JSON.stringify({ url_pattern, description }),
  });
}

export async function deleteAgentUrl(agentId: string, entryId: number): Promise<void> {
  await fetchJSON(`${getBase()}/url-allowlist/agent/${agentId}/${entryId}`, { method: "DELETE" });
}

export async function applyUrlTemplate(agentId: string, templateId: number): Promise<void> {
  await fetchJSON(`${getBase()}/url-allowlist/agent/${agentId}/apply-template`, {
    method: "POST",
    body: JSON.stringify({ template_id: templateId }),
  });
}

// ---------------------------------------------------------------------------
// Key Management System (KMS)
// ---------------------------------------------------------------------------

export interface AgentSecretEntry {
  id: number;
  name: string;
  key_name: string;
  secret_type: "api_key" | "sso_profile" | "oauth_token";
  description: string;
  is_active: boolean;
  masked_value: string | null;
  created_at: string | null;
  assigned_agent_ids: string[];
}

export async function listSecrets(): Promise<AgentSecretEntry[]> {
  const data = await fetchJSON(`${getBase()}/secrets`) as { secrets?: AgentSecretEntry[] };
  return data.secrets ?? [];
}

export async function createSecret(payload: {
  name: string;
  key_name: string;
  value: string;
  secret_type?: "api_key" | "sso_profile" | "oauth_token";
  description?: string;
}): Promise<AgentSecretEntry> {
  return fetchJSON(`${getBase()}/secrets`, {
    method: "POST",
    body: JSON.stringify(payload),
  }) as Promise<AgentSecretEntry>;
}

export async function updateSecret(id: number, payload: {
  name?: string;
  description?: string;
  value?: string;
  is_active?: boolean;
}): Promise<AgentSecretEntry> {
  return fetchJSON(`${getBase()}/secrets/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  }) as Promise<AgentSecretEntry>;
}

export async function deleteSecret(id: number): Promise<void> {
  await fetchJSON(`${getBase()}/secrets/${id}`, { method: "DELETE" });
}

export async function getAgentSecrets(agentId: string): Promise<AgentSecretEntry[]> {
  const data = await fetchJSON(`${getBase()}/secrets/agent/${agentId}`) as { secrets?: AgentSecretEntry[] };
  return data.secrets ?? [];
}

export async function assignSecret(agentId: string, secretId: number): Promise<void> {
  await fetchJSON(`${getBase()}/secrets/agent/${agentId}/${secretId}`, { method: "POST" });
}

export async function unassignSecret(agentId: string, secretId: number): Promise<void> {
  await fetchJSON(`${getBase()}/secrets/agent/${agentId}/${secretId}`, { method: "DELETE" });
}

// Vertical packs (industry starter kits — issue #159)
export interface VerticalPackSummary {
  slug: string;
  name: string;
  description: string;
  icon: string;
  industry: string;
  agent_count: number;
}

export interface VerticalPackDetail extends VerticalPackSummary {
  agents: { name: string; display_name: string; description: string; available: boolean }[];
  knowledge_entries: { title: string; tags: string[] }[];
  demo_task: { title: string; prompt: string } | null;
}

export async function listVerticalPacks(): Promise<{ packs: VerticalPackSummary[] }> {
  return fetchJSON(`${getBase()}/vertical-packs`);
}

export async function getVerticalPack(slug: string): Promise<VerticalPackDetail> {
  return fetchJSON(`${getBase()}/vertical-packs/${slug}`);
}

export async function provisionVerticalPack(slug: string): Promise<{
  status: string;
  message: string;
  agents: { id: string; name: string }[];
  knowledge_created: number;
  demo_task_id: string | null;
}> {
  return fetchJSON(`${getBase()}/vertical-packs/${slug}/provision`, { method: "POST" });
}

// Teams
export interface Team {
  id: string;
  name: string;
  description: string | null;
  member_agent_ids: string[];
  lead_agent_id: string | null;
  is_active: boolean;
  created_by: string | null;
}

export async function listTeams(): Promise<{ teams: Team[] }> {
  return fetchJSON(`${getBase()}/teams/`);
}

export async function createTeam(body: {
  name: string;
  description?: string;
  member_agent_ids?: string[];
  lead_agent_id?: string | null;
}): Promise<Team> {
  return fetchJSON(`${getBase()}/teams/`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function getTeam(id: string): Promise<Team> {
  return fetchJSON(`${getBase()}/teams/${id}`);
}

export async function updateTeam(
  id: string,
  body: { name?: string; description?: string; member_agent_ids?: string[]; lead_agent_id?: string | null },
): Promise<Team> {
  return fetchJSON(`${getBase()}/teams/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function deleteTeam(id: string): Promise<void> {
  await fetchJSON(`${getBase()}/teams/${id}`, { method: "DELETE" });
}

export async function changeTeamMembers(
  id: string,
  changes: { add?: string[]; remove?: string[] },
): Promise<Team> {
  return fetchJSON(`${getBase()}/teams/${id}/members`, {
    method: "POST",
    body: JSON.stringify(changes),
  });
}

export async function setTeamLead(id: string, leadAgentId: string | null): Promise<Team> {
  return fetchJSON(`${getBase()}/teams/${id}/lead`, {
    method: "PATCH",
    body: JSON.stringify({ lead_agent_id: leadAgentId }),
  });
}

export async function delegateToTeam(
  id: string,
  body: { title: string; prompt: string; priority?: number },
): Promise<{ task_id: string; lead_agent_id: string; status: string }> {
  return fetchJSON(`${getBase()}/teams/${id}/tasks`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
