import type { ActivityTimelineResponse, AdminUser, Agent, AgentMemory, AgentMode, AgentTemplate, AgentTodo, AIAccount, ApprovalRequest, AuditLog, AuditSummary, Feedback, FeedbackListResponse, FeedbackStatus, KnowledgeEntry, KnowledgeGraphEdge, KnowledgeGraphNode, KnowledgeTag, LLMConfig, LLMConfigResponse, MeetingRoom, Notification, PermissionPackage, DayPlanItem, ProactiveResponse, Responsibility, ReflectionRun, ReflectionStatus, Task, Schedule, FileEntry, Settings, SecondBrain, Integration, TodoListResponse, WebhookEvent } from "./types";
import { getApiUrl, getBase, getWsUrl } from "./config";

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
export async function getAgents(
  scope: "own" | "all" = "own",
  roomPool = false,
): Promise<{ agents: Agent[]; total: number }> {
  return fetchJSON(`${getBase()}/agents/?scope=${scope}${roomPool ? "&room_pool=true" : ""}`);
}

// Admin-only: add/remove an agent from the Meeting-Room shared pool.
export async function setRoomSharing(
  agentId: string,
  shared: boolean,
): Promise<{ id: string; shared_for_rooms: boolean }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/room-sharing`, {
    method: "PATCH",
    body: JSON.stringify({ shared_for_rooms: shared }),
  });
}

export async function setPlatformAgent(
  agentId: string,
  isPlatformAgent: boolean,
): Promise<{ id: string; is_platform_agent: boolean }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/platform-agent`, {
    method: "PATCH",
    body: JSON.stringify({ is_platform_agent: isPlatformAgent }),
  });
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

export async function setAgentParallelSessions(
  agentId: string,
  parallel_sessions: number,
): Promise<{ agent_id: string; parallel_sessions: number }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/parallel-sessions`, {
    method: "POST",
    body: JSON.stringify({ parallel_sessions }),
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
  connections: { from: string; to: string; from_name: string; to_name: string; count: number; last_at: string }[];
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

export async function updateAgentDefaultReasoning(
  agentId: string,
  level: string,
): Promise<{ agent_id: string; default_reasoning: string; applies_after: string }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/default-reasoning`, {
    method: "PATCH",
    body: JSON.stringify({ default_reasoning: level }),
  });
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
// Returns only ENABLED (admin-freigeschaltete) models.
export async function getModelCatalog(): Promise<{ modes: ModelCatalogMode[] }> {
  return fetchJSON(`${getBase()}/agents/models`);
}

// ── Admin model catalog: auto-discovery + freischaltung ──────
export interface AdminModelCatalogModel extends ModelCatalogModel {
  enabled: boolean;
  source: "seed" | "discovered";
}
export interface AdminModelCatalogProvider {
  provider: string;
  models: AdminModelCatalogModel[];
}
export interface AdminModelCatalogMode {
  mode: string;
  label: string;
  default_provider: string;
  default_model: string;
  providers: AdminModelCatalogProvider[];
}
export interface AdminModelCatalog {
  modes: AdminModelCatalogMode[];
  discovered_at: string | null;
  last_discovery?: {
    anthropic_found: number;
    openai_found: number;
    foundry_found?: number;
    new_extras: number;
    anthropic_queried: boolean;
    openai_queried: boolean;
    foundry_queried?: boolean;
  };
}
// Full catalog incl. disabled models + source flags (admin only).
export async function getAdminModelCatalog(): Promise<AdminModelCatalog> {
  return fetchJSON(`${getBase()}/agents/models/admin`);
}
// Query provider APIs for available models and cache new ones (admin only).
export async function discoverModels(): Promise<AdminModelCatalog> {
  return fetchJSON(`${getBase()}/agents/models/discover`, { method: "POST" });
}
// Enable/disable models (freischaltung). overrides: { model_value: boolean }.
export async function setModelsEnabled(overrides: Record<string, boolean>): Promise<AdminModelCatalog> {
  return fetchJSON(`${getBase()}/agents/models/enabled`, {
    method: "PUT",
    body: JSON.stringify({ overrides }),
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

// ── Admin system control: remote status + restart (off-LAN management) ──────
export interface SystemStatus {
  version: string;
  containers: Record<string, string>;
  agent_containers: number | null;
}
export async function getSystemStatus(): Promise<SystemStatus> {
  return fetchJSON(`${getBase()}/admin/system/status`);
}
export async function restartSystemComponent(
  target: "orchestrator" | "frontend",
): Promise<{ status: string; target: string; note?: string }> {
  return fetchJSON(`${getBase()}/admin/system/restart`, {
    method: "POST",
    body: JSON.stringify({ target }),
  });
}

// `derived` = welche sudo-Pakete jede Autonomiestufe hergibt. Kommt vom Server,
// damit die Regel nicht ein zweites Mal in TypeScript steht.
export async function getPermissionPackages(): Promise<{ packages: PermissionPackage[]; defaults: string[]; derived: Record<string, string[]> }> {
  return fetchJSON(`${getBase()}/agents/permissions`);
}

// `mode: "auto"` gibt die Rechte an die Autonomiestufe zurueck — die Liste wird dann
// ignoriert und der Server leitet sie aus der Matrix ab.
export async function updateAgentPermissions(agentId: string, permissions: string[], mode: "auto" | "manual" = "manual"): Promise<{ agent_id: string; permissions: string[]; permissions_mode: "auto" | "manual"; warning?: string }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/permissions`, {
    method: "PATCH",
    body: JSON.stringify({ permissions, mode }),
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

/** Agent auf das neueste Abbild bringen.
 *
 *  Läuft vorher durch das Golden-Test-Gatter (#391) und wirft bei einem
 *  Rückschritt einen Fehler mit der Begründung. `force` geht trotzdem durch —
 *  ein Gatter ohne Notausgang wird beim ersten dringenden Fall umgangen, und zwar
 *  dauerhaft. */
export async function updateAgent(id: string, force = false): Promise<Agent> {
  return fetchJSON(`${getBase()}/agents/${id}/update${force ? "?force=true" : ""}`, {
    method: "POST",
  });
}

export async function renameAgent(id: string, name: string): Promise<{ agent_id: string; name: string; status: string }> {
  return fetchJSON(`${getBase()}/agents/${id}/name`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
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

export async function getDayPlan(
  agentId: string,
  date: string,
  days = 1,
): Promise<{ agent_id: string; from: string; to: string; items: DayPlanItem[] }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/day-plan?date=${date}&days=${days}`);
}

export async function patchDayPlanItem(
  itemId: number,
  patch: {
    status?: string;
    planned_start?: string;
    estimated_minutes?: number;
    title?: string;
    notes?: string;
  },
): Promise<DayPlanItem> {
  return fetchJSON(`${getBase()}/day-plan/${itemId}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export async function setMsgraphReadOnly(read_only: boolean): Promise<{ msgraph_read_only: boolean }> {
  return fetchJSON(`${getBase()}/settings/msgraph-read-only`, {
    method: "PUT",
    body: JSON.stringify({ read_only }),
  });
}

export async function setAgentIdleStop(agentId: string, idle_stop_minutes: number): Promise<{ agent_id: string; idle_stop_minutes: number | null }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/idle-stop`, {
    method: "PATCH",
    body: JSON.stringify({ idle_stop_minutes }),
  });
}

/** Always-on: exempt an agent from both idle sweeps (keeps running regardless of owner activity). */
export async function setAgentAlwaysOn(agentId: string, always_on: boolean): Promise<{ agent_id: string; always_on: boolean }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/always-on`, {
    method: "PATCH",
    body: JSON.stringify({ always_on }),
  });
}

export interface ModelRouterConfig {
  enabled: boolean;
  rules: { simple?: string; standard?: string; complex?: string };
}

export async function setAgentModelRouter(
  agentId: string,
  config: ModelRouterConfig,
): Promise<{ agent_id: string; model_router: ModelRouterConfig }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/model-router`, {
    method: "PATCH",
    body: JSON.stringify(config),
  });
}

/** Selbstheilung pro Agent (#390) — wie oft und wie lange gewartet wird. */
export interface SelfHealingPolicy {
  enabled: boolean;
  max_attempts: number;
  base_delay_seconds: number;
  max_delay_seconds: number;
  retry_unknown: boolean;
}

interface SelfHealingResponse {
  agent_id: string;
  policy: SelfHealingPolicy;
  customized: boolean;
}

export async function getAgentSelfHealing(agentId: string): Promise<SelfHealingResponse> {
  return fetchJSON(`${getBase()}/agents/${agentId}/self-healing`);
}

export async function updateAgentSelfHealing(
  agentId: string,
  policy: SelfHealingPolicy,
): Promise<SelfHealingResponse> {
  return fetchJSON(`${getBase()}/agents/${agentId}/self-healing`, {
    method: "PATCH",
    body: JSON.stringify(policy),
  });
}

/** Leerer Rumpf = Vorgaben wiederherstellen (der Agent hat dann nichts Eigenes). */
export async function resetAgentSelfHealing(agentId: string): Promise<SelfHealingResponse> {
  return fetchJSON(`${getBase()}/agents/${agentId}/self-healing`, {
    method: "PATCH",
    body: JSON.stringify({}),
  });
}

/** Konfidenz-Routing pro Agent (#389) — ab wann ein Mensch geholt wird. */
export interface ConfidenceSettings {
  agent_id: string;
  enabled: boolean;
  threshold: number;
  default_threshold: number;
  customized: boolean;
}

export async function getAgentConfidence(agentId: string): Promise<ConfidenceSettings> {
  return fetchJSON(`${getBase()}/agents/${agentId}/confidence`);
}

export async function updateAgentConfidence(
  agentId: string,
  patch: { enabled?: boolean; threshold?: number },
): Promise<ConfidenceSettings> {
  return fetchJSON(`${getBase()}/agents/${agentId}/confidence`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

/** Golden-Tests (#391): Sammlungen, Läufe und das Update-Gatter. */
export interface EvalItem {
  id?: string;
  title?: string;
  prompt: string;
  weight?: number;
  expect_contains?: string[];
  expect_absent?: string[];
  expect_regex?: string[];
  min_length?: number;
}

export interface EvalSet {
  id: string;
  name: string;
  role: string;
  description: string;
  version: number;
  items: EvalItem[];
  item_count: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface EvalCheck {
  kind: string;
  value: unknown;
  ok: boolean;
  error?: string;
}

export interface EvalResult {
  id: string;
  title: string;
  ok: boolean;
  weight: number;
  checks: EvalCheck[];
  answer_excerpt: string;
}

export interface EvalRun {
  id: string;
  set_id: string;
  set_version: number;
  agent_id: string;
  status: "running" | "completed" | "failed";
  score: number | null;
  passed: number;
  total: number;
  baseline_score: number | null;
  regression: boolean;
  trigger: string;
  results: EvalResult[];
  created_at: string | null;
  completed_at: string | null;
}

export interface EvalGate {
  allowed: boolean;
  reason: string;
  message: string;
  run_id?: string;
  score?: number | null;
  baseline?: number | null;
}

export async function getEvalSets(role?: string): Promise<{ sets: EvalSet[] }> {
  const q = role ? `?role=${encodeURIComponent(role)}` : "";
  return fetchJSON(`${getBase()}/evals/sets${q}`);
}

export async function createEvalSet(body: {
  name: string;
  role?: string;
  description?: string;
  items: EvalItem[];
}): Promise<EvalSet> {
  return fetchJSON(`${getBase()}/evals/sets`, { method: "POST", body: JSON.stringify(body) });
}

export async function updateEvalSet(
  setId: string,
  body: { name: string; role?: string; description?: string; items: EvalItem[] },
): Promise<EvalSet> {
  return fetchJSON(`${getBase()}/evals/sets/${setId}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function deleteEvalSet(setId: string): Promise<void> {
  await fetchJSON(`${getBase()}/evals/sets/${setId}`, { method: "DELETE" });
}

export async function runEvalSet(setId: string, agentId: string): Promise<EvalRun> {
  return fetchJSON(`${getBase()}/evals/sets/${setId}/run`, {
    method: "POST",
    body: JSON.stringify({ agent_id: agentId }),
  });
}

export async function getEvalRuns(params: {
  agentId?: string;
  setId?: string;
  limit?: number;
} = {}): Promise<{ runs: EvalRun[] }> {
  const q = new URLSearchParams();
  if (params.agentId) q.set("agent_id", params.agentId);
  if (params.setId) q.set("set_id", params.setId);
  if (params.limit) q.set("limit", String(params.limit));
  const qs = q.toString();
  return fetchJSON(`${getBase()}/evals/runs${qs ? `?${qs}` : ""}`);
}

export async function getEvalGate(agentId: string): Promise<EvalGate> {
  return fetchJSON(`${getBase()}/evals/gate/${agentId}`);
}

export async function updateEvalGate(
  agentId: string,
  patch: { enabled?: boolean; require_run?: boolean; tolerance?: number },
): Promise<{ agent_id: string; gate: Record<string, unknown> }> {
  return fetchJSON(`${getBase()}/evals/gate/${agentId}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export interface RolePermissions {
  max_agents?: number | null;
  template_ids?: number[] | null;
  llm_providers?: string[] | null;
  models?: string[] | null;
  mount_labels?: string[] | null;
  ai_account_ids?: number[] | null;
  secret_ids?: number[] | null;
  mcp_server_ids?: number[] | null;
  integration_providers?: string[] | null;
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
  params.set("limit", "500");
  if (status) params.set("status", status);
  if (agentId) params.set("agent_id", agentId);
  return fetchJSON(`${getBase()}/tasks/?${params}`);
}

export async function getActivityTimeline(
  start: Date,
  end: Date,
  agentId?: string
): Promise<ActivityTimelineResponse> {
  const params = new URLSearchParams();
  params.set("start", start.toISOString());
  params.set("end", end.toISOString());
  if (agentId) params.set("agent_id", agentId);
  return fetchJSON(`${getBase()}/activity/timeline?${params}`);
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

export interface TaskArtifact {
  name: string;
  path: string;
  size: number;
  modified: number;
}

export async function getTaskSteps(
  id: string,
): Promise<{ task_id: string; total_steps: number; steps: TaskStep[] }> {
  return fetchJSON(`${getBase()}/tasks/${id}/steps`);
}

export async function getTaskArtifacts(
  id: string,
): Promise<{ task_id: string; agent_id: string | null; artifacts: TaskArtifact[] }> {
  return fetchJSON(`${getBase()}/tasks/${id}/artifacts`);
}

// Decision-Trace / Zeitreise (#387): enriched, grouped timeline with per-step
// duration, folded tool results, governance audit events and a cost summary.
export interface TaskTraceEntry {
  sequence: number;
  type: string;
  timestamp: string | null;
  duration_ms: number | null;
  text?: string;
  tool?: string;
  input?: unknown;
  result?: unknown;
  tool_duration_ms?: number | null;
  content?: unknown;
  error?: unknown;
  summary?: Record<string, unknown>;
  data?: Record<string, unknown>;
}
export interface TaskTrace {
  task_id: string;
  agent_id: string | null;
  summary: {
    title: string;
    status: string;
    model: string | null;
    cost_usd: number | null;
    input_tokens: number | null;
    output_tokens: number | null;
    duration_ms: number | null;
    num_turns: number | null;
    started_at: string | null;
    completed_at: string | null;
  };
  governance: Array<{
    event_type: string;
    command: string | null;
    outcome: string | null;
    exit_code: number | null;
    timestamp: string | null;
  }>;
  total_steps: number;
  entries: TaskTraceEntry[];
}

export async function getTaskTrace(id: string): Promise<TaskTrace> {
  return fetchJSON(`${getBase()}/tasks/${id}/trace`);
}

// URL for the JSON export download (served with a Content-Disposition attachment).
export function taskTraceExportUrl(id: string): string {
  return `${getBase()}/tasks/${id}/export?format=json`;
}

// Dry-Run (#386): run a plan-preview task for real (same agent, original prompt).
export async function executeDryRun(taskId: string): Promise<Task> {
  return fetchJSON(`${getBase()}/tasks/${taskId}/execute`, { method: "POST" });
}

export async function createTask(data: {
  title: string;
  prompt: string;
  priority?: number;
  agent_id?: string;
  model?: string;
  dry_run?: boolean;
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

/**
 * Einen ganzen Ordner aus dem Arbeitsbereich als ZIP herunterladen.
 *
 * Der Browser laedt selbst; die Anmeldung reist im Cookie mit. Genutzt von der
 * App-Uebersicht (Verzeichnis einer App) und vom Dateibaum (beliebiger Ordner).
 */
export function getFolderDownloadUrl(agentId: string, path: string): string {
  return `${getBase()}/agents/${agentId}/files/download-folder?path=${encodeURIComponent(path)}`;
}

export async function saveFileContent(
  agentId: string,
  path: string,
  content: string,
): Promise<{ path: string; bytes: number }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/files/content`, {
    method: "PUT",
    body: JSON.stringify({ path, content }),
  });
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

// Meeting recording: transcribe one recorded audio chunk via the STT service.
export async function transcribeMeetingChunk(blob: Blob): Promise<string> {
  const fd = new FormData();
  fd.append("file", blob, "chunk.webm");
  const res = await fetch(`${getBase()}/meetings/transcribe`, {
    method: "POST",
    body: fd,
    credentials: "include",
  });
  if (!res.ok) throw new Error("Transkription fehlgeschlagen");
  return ((await res.json()).text as string) || "";
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
    reasoning_tokens?: number;
    cached_tokens?: number;
    cache_write_tokens?: number;
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
  reasoning?: string | null;  // persisted thinking depth; "" → Auto (harness default)
}

export async function getChatSessions(
  agentId: string,
): Promise<{ sessions: ChatSession[] }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/chat/sessions`);
}

// Rename, pin and/or set the thinking depth of a chat session (metadata is
// created lazily server-side).
export async function updateChatSession(
  agentId: string,
  sessionId: string,
  patch: { title?: string | null; pinned?: boolean; reasoning?: string },
): Promise<{ id: string; title: string | null; pinned: boolean; reasoning?: string }> {
  return fetchJSON(`${getBase()}/agents/${agentId}/chat/sessions/${sessionId}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

// Send a meeting transcript into the agent's chat as a normal message, so the
// transcript + the agent's protocol reply show up as a visible chat thread (in the
// agent's Chat tab) — not a headless background task. Opens a one-shot chat
// WebSocket, sends the message, waits for the agent to finish (so the reply is
// persisted), then closes. Returns the session id it wrote to.
export async function sendMeetingTranscriptToChat(
  agentId: string,
  transcript: string,
): Promise<string> {
  const token = localStorage.getItem("token");
  const tr = await fetch(`${getApiUrl()}/api/v1/ws/ticket`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!tr.ok) throw new Error("ticket failed");
  const { ticket } = await tr.json();
  const sessionId = `meeting-${Date.now().toString(36)}`;
  const prompt =
    "Aus dem folgenden Meeting-Transkript ein strukturiertes Protokoll erstellen " +
    "(Teilnehmer, Zusammenfassung, Entscheidungen, Action-Items mit Verantwortlichen). " +
    "Speichere es zusätzlich als Knowledge-Eintrag.\n\nTRANSKRIPT:\n" +
    transcript;
  await new Promise<void>((resolve, reject) => {
    const ws = new WebSocket(`${getWsUrl()}/api/v1/ws/agents/${agentId}/chat?ticket=${ticket}`);
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      try { ws.close(); } catch { /* already closed */ }
      resolve();
    };
    ws.onopen = () =>
      ws.send(JSON.stringify({ text: prompt, session_id: sessionId, source: "webapp" }));
    ws.onmessage = (e) => {
      try {
        const evt = JSON.parse(e.data);
        if (evt.type === "done" || evt.type === "error") finish();
      } catch { /* ignore non-JSON frames */ }
    };
    ws.onerror = () => { if (!settled) { settled = true; reject(new Error("chat ws error")); } };
    // Safety: the agent turn (protocol generation) can take a while; close after a
    // generous cap even if no explicit done event arrives. The message is already
    // persisted server-side on receipt, so the thread exists regardless.
    setTimeout(finish, 180_000);
  });
  return sessionId;
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

export async function getAgentIntegrations(agentId: string): Promise<{ agent_id: string; integrations: string[]; msgraph_access?: string; exchange_access?: string; microsoft_read_only?: boolean }> {
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

/** Symbol, Farbe und Schlagwort — kosmetisch, kein Neustart.
 *  Weggelassene Felder bleiben unangetastet; ein leerer Text entfernt den Wert. */
export async function updateAgentAppearance(
  agentId: string,
  patch: { icon?: string; color?: string; tag?: string },
): Promise<void> {
  await fetchJSON(`${getBase()}/agents/${agentId}/appearance`, {
    method: "PATCH",
    body: JSON.stringify(patch),
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

// PAT-based integrations (GitHub). base_url points at a self-hosted instance
// (GitHub Enterprise Server) instead of github.com — #532 phase 2.
export async function savePatToken(provider: string, token: string, baseUrl?: string): Promise<{ status: string; provider: string; account_label?: string }> {
  return fetchJSON(`${getBase()}/integrations/${provider}/pat`, {
    method: "POST",
    body: JSON.stringify({ token, base_url: baseUrl || undefined }),
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
  offset = 0,
): Promise<{ memories: AgentMemory[]; total: number; has_more: boolean; categories: Record<string, number> }> {
  const params = new URLSearchParams();
  if (category) params.set("category", category);
  if (offset) params.set("offset", String(offset));
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

export async function getMemoryHistory(
  memoryId: number,
): Promise<{ history: AgentMemory[] }> {
  return fetchJSON(`${getBase()}/memory/${memoryId}/history`);
}

// Second Brain cross-system "related" (#157)
export interface RelatedMemoryItem { id: number; key: string; category: string; snippet: string; similarity: number }
export interface RelatedKnowledgeItem { id: number; title: string; similarity: number }
export async function getRelatedMemory(
  memoryId: number,
): Promise<{ related_memories: RelatedMemoryItem[]; related_knowledge: RelatedKnowledgeItem[] }> {
  return fetchJSON(`${getBase()}/memory/${memoryId}/related`);
}

// Second Brain cross-system bridge from the knowledge side (#157). Reuses the
// existing /brain/related endpoint (knowledge neighbors from brain_links) which was
// extended to also return cross-system agent memories; maps to the shared shape.
export async function getRelatedKnowledge(
  entryId: number,
): Promise<{ related_knowledge: RelatedKnowledgeItem[]; related_memories: RelatedMemoryItem[] }> {
  const data = await fetchJSON<{
    related?: Array<{ id: number; title: string; similarity?: number | null }>;
    related_memories?: RelatedMemoryItem[];
  }>(`${getBase()}/brain/related/${entryId}`);
  return {
    related_knowledge: (data.related ?? []).map((r) => ({
      id: r.id,
      title: r.title,
      similarity: r.similarity ?? 0,
    })),
    related_memories: data.related_memories ?? [],
  };
}

// Reflection ("Nachtschicht")
export async function getReflectionStatus(): Promise<ReflectionStatus> {
  return fetchJSON(`${getBase()}/reflection/status`);
}

export async function getReflectionRuns(
  limit = 20,
): Promise<{ runs: ReflectionRun[] }> {
  return fetchJSON(`${getBase()}/reflection/runs?limit=${limit}`);
}

export async function runReflectionNow(): Promise<{ started: boolean; at?: string }> {
  return fetchJSON(`${getBase()}/reflection/run-now`, { method: "POST" });
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
    contact_hours_start?: string;
    contact_hours_end?: string;
    contact_timezone?: string;
    responsibilities?: Responsibility[];
    morning_planning_time?: string;
    morning_planning_weekdays_only?: boolean;
    deputy_agent_id?: string;
    duty_start?: string;
    duty_end?: string;
    duty_weekdays_only?: boolean;
    absence_from?: string;
    absence_to?: string;
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
  has_headers?: boolean;
  created_at: string | null;
  last_checked_at: string | null;
  last_status: "ok" | "auth_failed" | "unreachable" | "protocol_error" | "needs_oauth" | null;
  last_error: string | null;
  // Client-side OAuth (#426)
  oauth_enabled?: boolean;
  oauth_client_id?: string | null;
  oauth_connected?: boolean;  // a refresh token is stored → the flow completed
  oauth_scope?: string | null;
  oauth_expires_at?: string | null;
}

export interface McpTool {
  name: string;
  description?: string;
  inputSchema?: Record<string, unknown>;
}

export async function getMcpServers(): Promise<{ servers: McpServerInfo[] }> {
  return fetchJSON(`${getBase()}/mcp-servers`);
}

export async function addMcpServer(
  name: string, url: string, bearerToken?: string, headers?: Record<string, string>,
  /** Private Adresse fuer DIESEN Server zulassen (Admin-Entscheidung, siehe
   *  Integrationen-Seite). Loopback und Metadaten-Adressen bleiben gesperrt. */
  allowPrivateHost?: boolean,
): Promise<McpServerInfo> {
  return fetchJSON(`${getBase()}/mcp-servers`, {
    method: "POST",
    body: JSON.stringify({
      name, url,
      ...(bearerToken ? { bearer_token: bearerToken } : {}),
      ...(headers && Object.keys(headers).length ? { headers } : {}),
      ...(allowPrivateHost ? { allow_private_host: true } : {}),
    }),
  });
}

export async function refreshMcpServer(id: number): Promise<McpServerInfo> {
  return fetchJSON(`${getBase()}/mcp-servers/${id}/refresh`, { method: "POST" });
}

export type AgentMcpStatus = "connected" | "failed" | "needs_auth" | "unknown";

export interface McpAgentHealthEntry {
  name: string;
  connected: number;
  failed: number;
  needs_auth: number;
  unknown: number;
  agent_status: AgentMcpStatus | null;
  agents: { agent_id: string; agent_name: string; status: AgentMcpStatus }[];
}

export interface McpAgentHealth {
  agents_checked: number;
  agents_total: number;
  // Keyed by MCP server id (as a string).
  servers: Record<string, McpAgentHealthEntry>;
}

// Agent-side MCP connection health (#425 Phase 2): what each running agent's
// `claude mcp list` reports, independent of the orchestrator's own discovery
// check. On-demand (admin-only) — each call runs live probes across all agents.
export async function getMcpAgentHealth(): Promise<McpAgentHealth> {
  return fetchJSON(`${getBase()}/mcp-servers/agent-health`);
}

export async function updateMcpServer(
  id: number,
  data: { name?: string; url?: string; enabled?: boolean; bearer_token?: string; headers?: Record<string, string> },
): Promise<McpServerInfo> {
  return fetchJSON(`${getBase()}/mcp-servers/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export async function deleteMcpServer(id: number): Promise<void> {
  await fetchJSON(`${getBase()}/mcp-servers/${id}`, { method: "DELETE" });
}

// Probe an MCP server WITHOUT saving — must include the same auth (bearer token +
// custom headers) so a protected server can actually be reached during the test.
export async function probeMcpServer(
  name: string, url: string, bearerToken?: string, headers?: Record<string, string>,
  allowPrivateHost?: boolean,
): Promise<{
  url: string;
  tools: McpTool[];
  tool_count: number;
  last_checked_at: string;
  last_status: McpServerInfo["last_status"];
  last_error: string | null;
}> {
  return fetchJSON(`${getBase()}/mcp-servers/probe`, {
    method: "POST",
    body: JSON.stringify({
      name, url,
      ...(bearerToken ? { bearer_token: bearerToken } : {}),
      ...(headers && Object.keys(headers).length ? { headers } : {}),
      ...(allowPrivateHost ? { allow_private_host: true } : {}),
    }),
  });
}

export interface McpToolCallResult {
  server_id: number;
  tool: string;
  result: Record<string, unknown>;  // raw JSON-RPC object (may carry an `error` member)
  is_error: boolean;
}

// Admin: invoke a single tool on a saved MCP server by hand (#414). The attempt is
// audit-logged server-side. Returns the raw JSON-RPC response verbatim.
export async function callMcpTool(
  id: number, name: string, args: Record<string, unknown>,
): Promise<McpToolCallResult> {
  return fetchJSON(`${getBase()}/mcp-servers/${id}/call`, {
    method: "POST",
    body: JSON.stringify({ name, arguments: args }),
  });
}

// Client-side OAuth for OAuth-protected MCP servers (#426).
export interface McpOAuthDiscovery {
  oauth_enabled: boolean;
  authorization_endpoint: string | null;
  token_endpoint: string | null;
  registration_endpoint: string | null;
  scope: string | null;
  resource: string | null;
  client_id: string | null;
  dynamically_registered: boolean;
  needs_client_id: boolean;
  redirect_uri: string;
}

// Admin: discover a server's OAuth config (RFC 9728 → RFC 8414) and register a
// client via DCR when available. Pass a client_id for servers without DCR.
export async function discoverMcpOAuth(id: number, clientId?: string): Promise<McpOAuthDiscovery> {
  return fetchJSON(`${getBase()}/mcp-servers/${id}/oauth/discover`, {
    method: "POST",
    body: JSON.stringify(clientId ? { client_id: clientId } : {}),
  });
}

// Admin: start authorization_code + PKCE — returns the URL to open in the browser.
export async function connectMcpOAuth(id: number): Promise<{ authorization_url: string }> {
  return fetchJSON(`${getBase()}/mcp-servers/${id}/oauth/connect`);
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

export async function resetUserPassword(
  userId: string,
): Promise<{ user_id: string; email: string; temp_password: string }> {
  return fetchJSON(`${getBase()}/auth/users/${userId}/reset-password`, { method: "POST" });
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

// Discovered model list + connection state for an AI account (#435). status ∈
// ok | auth_failed | unreachable | protocol_error | unsupported.
export interface DiscoveredModels {
  status: string;
  models: { id: string; label: string }[];
  error: string | null;
}

// Probe a provider's /v1/models (OpenAI-compatible, Anthropic, Google). Pass the
// typed credentials to check an unsaved account, or an account_id to re-check a
// saved one with its stored key (which also stamps that account's health state).
export async function discoverAIAccountModels(payload: {
  provider_type?: string;
  api_endpoint?: string | null;
  api_key?: string | null;
  account_id?: number;
}): Promise<DiscoveredModels> {
  return fetchJSON(`${getBase()}/ai-accounts/discover-models`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
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
  kind?: "backlink" | "semantic";
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
/** Vault als ZIP herunterladen — Ordnerstruktur bleibt erhalten. */
export function getBrainExportUrl(brainId: number): string {
  return `${getBase()}/brains/${brainId}/export`;
}

/**
 * Vault aus einem ZIP einspielen.
 *
 * `replace=false` fuegt zusammen (loescht nichts), `replace=true` macht den
 * Vault zum Abbild des Archivs. Der Server zieht danach die Einbettungen nach —
 * ohne das waeren die Notizen semantisch unauffindbar.
 */
export async function importBrainZip(
  brainId: number,
  file: File,
  replace = false,
): Promise<{
  ok: boolean; written: number; deleted: number; bytes: number;
  skipped: string[]; skipped_total: number; index: Record<string, unknown>;
}> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(
    `${getBase()}/brains/${brainId}/import?replace=${replace}`,
    { method: "POST", body: fd, credentials: "include" },
  );
  if (!res.ok) throw new Error((await res.text()).slice(0, 300));
  return res.json();
}

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
  responsibilities?: Responsibility[];
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
    responsibilities?: Responsibility[];
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

// Feedback-Widget: genau EINE Requirements-Rückfrage vom LLM.
export async function feedbackWidgetReply(
  messages: { role: "user" | "bot"; text: string }[],
  context: Record<string, unknown>,
): Promise<{ reply: string }> {
  return fetchJSON(`${getBase()}/feedback/reply`, {
    method: "POST",
    body: JSON.stringify({ messages, context }),
  });
}

// Feedback-Widget: speichert MD (+PNG) serverseitig und legt den DB-Eintrag an.
// Der User kommt aus der Session — bewusst kein user-Feld im Payload.
export async function feedbackWidgetSave(
  messages: { role: "user" | "bot"; text: string }[],
  context: Record<string, unknown>,
  screenshot: string | null,
): Promise<{ ok: boolean; id: string; screenshot: string | null; issue_url?: string }> {
  return fetchJSON(`${getBase()}/feedback/save`, {
    method: "POST",
    body: JSON.stringify({ messages, context, screenshot }),
  });
}

// URL des gespeicherten Feedback-Screenshots (admin-only, Auth via Cookie).
export function feedbackImageUrl(fid: string): string {
  return `${getBase()}/feedback/image/${encodeURIComponent(fid)}`;
}

// Volltext (Markdown) eines Widget-Feedbacks (admin-only).
export async function getFeedbackItem(fid: string): Promise<{ id: string; md: string }> {
  return fetchJSON(`${getBase()}/feedback/item/${encodeURIComponent(fid)}`);
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

export async function exportFeedback(status?: FeedbackStatus): Promise<void> {
  const base = getBase();
  const q = status ? `?status=${encodeURIComponent(status)}` : "";
  const res = await fetch(`${base}/feedback/export${q}`, { credentials: "include" });
  if (!res.ok) throw new Error(`Export fehlgeschlagen: ${res.status}`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "feedback-export.zip";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
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

// --- Skill Sources (admin) — configurable crawl sources incl. self-hosted Git (#371) ---

export interface SkillSource {
  id: number;
  name: string;
  kind: "github" | "git";
  location: string;
  ref: string | null;
  subdir: string | null;
  has_credential: boolean;
  enabled: boolean;
  trusted: boolean;
  last_crawled_at: string | null;
  last_status: string | null;
  created_by: string;
}

export interface SkillSourceInput {
  name: string;
  kind: "github" | "git";
  location: string;
  ref?: string | null;
  subdir?: string | null;
  credential?: string | null;
  enabled?: boolean;
  trusted?: boolean;
}

export interface BuiltinSkillSource { location: string; kind: string; from_env: boolean }

export async function getSkillSources(): Promise<{ sources: SkillSource[]; builtin: BuiltinSkillSource[] }> {
  return fetchJSON(`${getBase()}/skills/sources`);
}

export async function createSkillSource(data: SkillSourceInput): Promise<SkillSource> {
  return fetchJSON(`${getBase()}/skills/sources`, { method: "POST", body: JSON.stringify(data) });
}

export async function updateSkillSource(id: number, data: Partial<SkillSourceInput>): Promise<SkillSource> {
  return fetchJSON(`${getBase()}/skills/sources/${id}`, { method: "PATCH", body: JSON.stringify(data) });
}

export async function deleteSkillSource(id: number): Promise<void> {
  return fetchJSON(`${getBase()}/skills/sources/${id}`, { method: "DELETE" });
}

export async function recrawlSkillSources(): Promise<{ status: string }> {
  return fetchJSON(`${getBase()}/skills/sources/recrawl`, { method: "POST" });
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

/** Nur die Zahl der offenen Freigaben — fuer das Abzeichen im Menue. */
/** Werkzeuge und Befehle DIESES Agenten — je nach Laufzeit verschieden. */
export interface AgentToolset {
  mode: string;
  commands: { name: string; hint: string; runtime_only?: boolean }[];
  groups: { key: string; label: string; note: string; tools: string[] }[];
  total: number;
}

export async function getAgentToolset(agentId: string): Promise<AgentToolset> {
  return fetchJSON(`${getBase()}/agents/${agentId}/toolset`);
}

/** Wie voll das Kontextfenster ist — die Zahlen für /compact. */
export interface ChatContextInfo {
  /** null = Fenstergröße dieses Modells unbekannt — dann zeigt die Oberfläche
   *  das auch so, statt eine erfundene Zahl. */
  window: number | null;
  used_estimate: number;
  percent: number | null;
  messages: number;
  compacted: number;
  keeps_verbatim: number;
  can_compact: boolean;
  model: string;
}

export async function getChatContext(
  agentId: string,
  sessionId: string,
): Promise<ChatContextInfo> {
  return fetchJSON(
    `${getBase()}/agents/${agentId}/chat/sessions/${encodeURIComponent(sessionId)}/context`,
  );
}

/** Verlauf im SELBEN Gespräch verdichten. Ältere Nachrichten werden markiert,
 *  nicht gelöscht — verdichten heißt nicht verlieren. */
export async function compactChatSession(
  agentId: string,
  sessionId: string,
): Promise<{ ok: boolean; folded: number; kept: number }> {
  return fetchJSON(
    `${getBase()}/agents/${agentId}/chat/sessions/${encodeURIComponent(sessionId)}/compact`,
    { method: "POST" },
  );
}

export async function getPendingApprovalCount(): Promise<number> {
  const res = await fetchJSON<{ count: number }>(`${getBase()}/approvals/pending/count`);
  return res.count;
}

/** Alle offenen Freigaben verwerfen. Sie werden als abgelehnt vermerkt, nicht
 *  geloescht — die Pruefspur muss erhalten bleiben. */
export async function clearPendingApprovals(): Promise<{ cleared: number }> {
  return fetchJSON(`${getBase()}/approvals/pending`, { method: "DELETE" });
}

export async function getPendingApprovals(): Promise<{ approvals: ApprovalRequest[]; count: number }> {
  return fetchJSON(`${getBase()}/approvals/pending`);
}

// `answer` ist die gewaehlte Antwortmoeglichkeit (oder freier Text), wenn der
// Agent eine Rueckfrage mit Optionen gestellt hat. Ohne sie verhaelt sich der
// Aufruf wie bisher — der Server nimmt dann „Approved by <mail>".
export async function approveCommand(
  approvalId: string,
  answer?: string,
): Promise<{ approval_id: string; status: string }> {
  return fetchJSON(`${getBase()}/approvals/${approvalId}/approve`, {
    method: "POST",
    body: JSON.stringify({ answer: answer || null }),
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

export async function getKnowledgeEntries(
  q?: string,
  tag?: string,
  limit?: number,
  offset?: number,
): Promise<{ entries: KnowledgeEntry[]; total: number }> {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (tag) params.set("tag", tag);
  if (limit != null) params.set("limit", String(limit));
  if (offset != null) params.set("offset", String(offset));
  const qs = params.toString() ? `?${params}` : "";
  return fetchJSON(`${getBase()}/knowledge/entries${qs}`);
}

/**
 * Fetch ALL knowledge entries, transparently paging via the endpoint's
 * offset/limit so the UI is never silently capped at the server default.
 */
export async function getAllKnowledgeEntries(
  q?: string,
  tag?: string,
): Promise<{ entries: KnowledgeEntry[]; total: number }> {
  const PAGE = 200;
  const first = await getKnowledgeEntries(q, tag, PAGE, 0);
  const entries = [...first.entries];
  while (entries.length < first.total) {
    const next = await getKnowledgeEntries(q, tag, PAGE, entries.length);
    if (next.entries.length === 0) break; // safety: avoid infinite loop
    entries.push(...next.entries);
  }
  return { entries, total: first.total };
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

// ── Wochensynthese (#384) ────────────────────────────────────────────────────
// Eine Synthese IST ein Wissenseintrag (created_by = "synthesis"); diese beiden
// Aufrufe sind nur eine gefilterte Sicht bzw. der Anstoss — kein zweiter Speicher.
export interface Synthesis {
  id: number;
  title: string;
  content: string;
  tags: string[];
  created_at: string | null;
}

export async function listSyntheses(limit = 20): Promise<{ syntheses: Synthesis[] }> {
  return fetchJSON(`${getBase()}/brain/syntheses?limit=${limit}`);
}

export async function synthesizeNow(): Promise<{
  trigger: string;
  users: number;
  written: number;
  skipped: string[];
  errors: string[];
}> {
  return fetchJSON(`${getBase()}/brain/synthesize-now`, { method: "POST" });
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

// Workflow engine (#392/#394)
export interface WorkflowStep {
  type: "agent_task" | "condition" | "wait";
  title?: string;
  prompt?: string;
  agent_id?: string | null;
  next?: string | null;
  check?: { step: string; op: string; value?: string };
  true?: string | null;
  false?: string | null;
  seconds?: number;
  _pos?: { x: number; y: number };
}
export interface WorkflowDefinition {
  start: string | null;
  steps: Record<string, WorkflowStep>;
}
export interface Workflow {
  id: string;
  name: string;
  user_id: string | null;
  enabled: boolean;
  folder_id?: string | null;
  role?: string;   // owner | editor | viewer
  definition: WorkflowDefinition;
  trigger: Record<string, unknown> | null;
  created_at: string | null;
  updated_at: string | null;
}
export interface WorkflowFolder { id: string; name: string; user_id: string; shared: boolean; created_at: string | null }
export interface WorkflowShare { id: string; user_id: string; user_name: string | null; role: string; workflow_id: string | null; folder_id: string | null }
export interface DirectoryUser { id: string; name: string; email: string }
export interface WorkflowRun {
  id: string;
  workflow_id: string;
  status: string;
  current_step: string | null;
  current_task_id: string | null;
  steps_done: number;
  context: Record<string, { result?: string; task_id?: string }>;
  error: string | null;
  resume_at: string | null;
  started_at: string | null;
  completed_at: string | null;
}
export async function getWorkflows(): Promise<{ workflows: Workflow[] }> {
  return fetchJSON(`${getBase()}/workflows`);
}
export async function getWorkflow(id: string): Promise<Workflow> {
  return fetchJSON(`${getBase()}/workflows/${id}`);
}
export async function createWorkflow(body: { name: string; definition: WorkflowDefinition; trigger?: Record<string, unknown> | null; enabled?: boolean; folder_id?: string | null }): Promise<Workflow> {
  return fetchJSON(`${getBase()}/workflows`, { method: "POST", body: JSON.stringify(body) });
}
export async function updateWorkflow(id: string, body: { name: string; definition: WorkflowDefinition; trigger?: Record<string, unknown> | null; enabled?: boolean; folder_id?: string | null }): Promise<Workflow> {
  return fetchJSON(`${getBase()}/workflows/${id}`, { method: "PUT", body: JSON.stringify(body) });
}
// Organisation: folders + sharing
export async function getWorkflowFolders(): Promise<{ folders: WorkflowFolder[] }> {
  return fetchJSON(`${getBase()}/workflows/folders`);
}
export async function createWorkflowFolder(name: string): Promise<WorkflowFolder> {
  return fetchJSON(`${getBase()}/workflows/folders`, { method: "POST", body: JSON.stringify({ name }) });
}
export async function deleteWorkflowFolder(id: string): Promise<{ deleted: string }> {
  return fetchJSON(`${getBase()}/workflows/folders/${id}`, { method: "DELETE" });
}
export async function shareWorkflowFolder(folderId: string, userId: string, role: string): Promise<WorkflowShare> {
  return fetchJSON(`${getBase()}/workflows/folders/${folderId}/share`, { method: "POST", body: JSON.stringify({ user_id: userId, role }) });
}
export async function getWorkflowDirectory(): Promise<{ users: DirectoryUser[] }> {
  return fetchJSON(`${getBase()}/workflows/directory`);
}
export async function getWorkflowShares(id: string): Promise<{ shares: WorkflowShare[] }> {
  return fetchJSON(`${getBase()}/workflows/${id}/shares`);
}
export async function shareWorkflow(id: string, userId: string, role: string): Promise<WorkflowShare> {
  return fetchJSON(`${getBase()}/workflows/${id}/share`, { method: "POST", body: JSON.stringify({ user_id: userId, role }) });
}
export async function revokeWorkflowShare(shareId: string): Promise<{ deleted: string }> {
  return fetchJSON(`${getBase()}/workflows/shares/${shareId}`, { method: "DELETE" });
}
export async function deleteWorkflow(id: string): Promise<{ deleted: string }> {
  return fetchJSON(`${getBase()}/workflows/${id}`, { method: "DELETE" });
}
export async function runWorkflow(id: string): Promise<WorkflowRun> {
  return fetchJSON(`${getBase()}/workflows/${id}/run`, { method: "POST" });
}
export async function getWorkflowRuns(id: string): Promise<{ runs: WorkflowRun[] }> {
  return fetchJSON(`${getBase()}/workflows/${id}/runs`);
}
export async function getWorkflowRun(runId: string): Promise<WorkflowRun> {
  return fetchJSON(`${getBase()}/workflows/runs/${runId}`);
}
// Import/export (#470) — portable snapshot for sharing a workflow as a file
export interface WorkflowExportSnapshot {
  format: string;
  version: number;
  name: string;
  definition: WorkflowDefinition;
  trigger: Record<string, unknown> | null;
  exported_at: string;
}
export async function exportWorkflow(id: string): Promise<WorkflowExportSnapshot> {
  return fetchJSON(`${getBase()}/workflows/${id}/export`);
}
export async function importWorkflow(body: { definition: WorkflowDefinition; name?: string | null; trigger?: Record<string, unknown> | null; folder_id?: string | null; format?: string; version?: number }): Promise<Workflow> {
  return fetchJSON(`${getBase()}/workflows/import`, { method: "POST", body: JSON.stringify(body) });
}

// DLP egress filter admin (#388)
export interface DlpSettings { enabled: boolean; classes: string[]; actions: string[] }
export interface DlpRule { id: number; pii_class: string; agent_id: string | null; action: string; enabled: boolean }
export interface DlpAuditEvent {
  id: number; agent_id: string; event_type: string; channel: string | null;
  outcome: string; meta: Record<string, unknown> | null; created_at: string | null;
}
export async function getDlpSettings(): Promise<DlpSettings> {
  return fetchJSON(`${getBase()}/dlp/settings`);
}
export async function setDlpEnabled(enabled: boolean): Promise<{ enabled: boolean }> {
  return fetchJSON(`${getBase()}/dlp/settings`, { method: "PATCH", body: JSON.stringify({ enabled }) });
}
export async function getDlpRules(): Promise<{ rules: DlpRule[] }> {
  return fetchJSON(`${getBase()}/dlp/rules`);
}
export async function upsertDlpRule(rule: { pii_class: string; action: string; agent_id?: string | null; enabled?: boolean }): Promise<DlpRule> {
  return fetchJSON(`${getBase()}/dlp/rules`, { method: "POST", body: JSON.stringify(rule) });
}
export async function deleteDlpRule(id: number): Promise<{ deleted: number }> {
  return fetchJSON(`${getBase()}/dlp/rules/${id}`, { method: "DELETE" });
}
export async function getDlpAudit(limit = 100): Promise<{ events: DlpAuditEvent[] }> {
  return fetchJSON(`${getBase()}/dlp/audit?limit=${limit}`);
}
export async function testDlpScan(text: string): Promise<{ classes: Record<string, number> }> {
  return fetchJSON(`${getBase()}/dlp/test`, { method: "POST", body: JSON.stringify({ text }) });
}

// Computer-Use Bridge Sessions
export interface ComputerUseSession {
  session_id: string;
  status: "connected" | "waiting_for_bridge" | "waiting";
  created_at: number;
  action_count: number;
  platform: string;
  bridge_version?: string | null;
  bridge_host?: string | null;
  bridge_public_url?: string | null;
  capabilities: string[];
  allowed_capabilities: string[];
  last_disconnected_at: number | null;
  bridge_last_seen_at: number | null;
  recording?: boolean;
}

/* Replay-Modus: record a workflow once (agent actions and/or the human
   demonstrating it), then turn the transcript into a reusable skill. */

export interface RecordingStep {
  action: string;
  params: Record<string, unknown>;
  ts: number;
  screenshot_b64?: string | null;
  source?: "human" | string;
}

export async function startRecording(
  sessionId: string,
  captureHuman = false,
): Promise<{ session_id: string; recording: boolean; capture_human: boolean }> {
  return fetchJSON(`${getBase()}/computer-use/sessions/${sessionId}/recording/start`, {
    method: "POST",
    body: JSON.stringify({ capture_human: captureHuman }),
  });
}

export async function stopRecording(
  sessionId: string,
): Promise<{ session_id: string; recording: boolean; steps: RecordingStep[]; step_count: number }> {
  return fetchJSON(`${getBase()}/computer-use/sessions/${sessionId}/recording/stop`, {
    method: "POST",
  });
}

export async function getRecording(
  sessionId: string,
): Promise<{ session_id: string; recording: boolean; steps: RecordingStep[]; step_count: number }> {
  return fetchJSON(`${getBase()}/computer-use/sessions/${sessionId}/recording`);
}

export async function recordingToSkill(
  sessionId: string,
  goalHint = "",
): Promise<{ skill_id: number; name: string; description: string; status: string; step_count: number }> {
  return fetchJSON(`${getBase()}/computer-use/sessions/${sessionId}/recording/to-skill`, {
    method: "POST",
    body: JSON.stringify({ goal_hint: goalHint }),
  });
}

export interface CapabilityGroup {
  id: string;
  actions: string[];
  default: boolean;
}

export async function listComputerUseSessions(): Promise<{ sessions: ComputerUseSession[] }> {
  return fetchJSON(`${getBase()}/computer-use/sessions`);
}

/** Bridge-Session holen oder anlegen.
 *
 *  Ohne Argument wird eine bestehende Session WIEDERVERWENDET — beim Öffnen des
 *  Tabs soll sich die ID nicht ändern, sonst müsste die Bridge jedes Mal neu
 *  eingerichtet werden. `forceNew` ist der bewusste Klick auf „Neue Session":
 *  der muss auch wirklich eine neue ID liefern. */
export async function createComputerUseSession(
  forceNew = false,
): Promise<{ session_id: string; status: string; ws_url: string; allowed_capabilities: string[] }> {
  const q = forceNew ? "?reuse=false" : "";
  return fetchJSON(`${getBase()}/computer-use/sessions${q}`, { method: "POST" });
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
  deliverable?: boolean;
}): Promise<MeetingRoom> {
  return fetchJSON(`${getBase()}/meeting-rooms/`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export interface DeliverableFile { path: string; size: number; }
export interface DeliverableBuildTask { agent_id: string; status: string; }
export interface DeliverableStatus {
  room_id: string;
  base: string;
  files: DeliverableFile[];
  deliverable_integrated: boolean;
  build_tasks: DeliverableBuildTask[];
  integration_status: string | null;
}

export async function listDeliverableFiles(roomId: string): Promise<DeliverableStatus> {
  return fetchJSON(`${getBase()}/meeting-rooms/${roomId}/deliverable/files`);
}

export async function getDeliverableFile(
  roomId: string, path: string,
): Promise<{ path: string; size: number; truncated: boolean; content: string }> {
  return fetchJSON(`${getBase()}/meeting-rooms/${roomId}/deliverable/file?path=${encodeURIComponent(path)}`);
}

export async function launchDeliverable(
  roomId: string,
): Promise<{ project: string; host_agent: string; containers: unknown[]; url: string | null }> {
  return fetchJSON(`${getBase()}/meeting-rooms/${roomId}/deliverable/launch`, { method: "POST" });
}

// --- Global Apps overview (only the caller's own agents' apps; admin: all) ---
export interface AppContainer { name: string; status: string; service: string }
export interface AppEntry {
  project: string;
  agent_id: string;
  agent_name: string;
  /** Wem der Agent gehört, der die App gebaut hat. Bei freigegebenen Apps die
   *  eigentliche Frage: von wem stammt das hier? */
  owner_id?: string | null;
  owner_name?: string | null;
  owned_by_me?: boolean;
  name: string;
  path: string | null;              // set for workspace apps (start via docker-apps up)
  status: "running" | "stopped" | "not_started" | string;
  containers: AppContainer[];
  url: string | null;
  /** Set when the app is NOT mine but shared with me — then only "open" is allowed. */
  shared_with_me?: "user" | "authenticated" | null;
}

export async function listApps(): Promise<{ apps: AppEntry[] }> {
  return fetchJSON(`${getBase()}/apps`);
}

// --- App sharing (#467): default deny, only the owner may manage shares ---
export type AppShareScope = "user" | "authenticated" | "public";

export interface AppShare {
  id: string;
  project: string;
  scope: AppShareScope;
  user_id: string | null;
  user_name: string | null;
  expires_at: string | null;
  expired: boolean;
  has_token: boolean;
  created_at: string | null;
  /** Klartext-Link-Token. Nur für den Besitzer, in der Antwort aufs Anlegen UND
   *  in der Freigabe-Liste einer App — seit 1.176.0 wird er verschlüsselt
   *  aufbewahrt. Fehlt bei Freigaben, die vor dieser Version entstanden sind. */
  token?: string;
}

export interface AppDetailContainer {
  name: string; service: string; status: string; image: string;
  port: string | null; created: string;
}

export interface AppDetail {
  project: string;
  agent_id: string;
  agent_name: string;
  owner_id?: string | null;
  owner_name?: string | null;
  owned_by_me?: boolean;
  status: string;
  containers: AppDetailContainer[];
  running: number;
  total: number;
  url: string | null;
  proxy_container: string | null;
  proxy_port: string | null;
  can_manage: boolean;
  shares: AppShare[];
}

export async function getAppDetail(project: string): Promise<AppDetail> {
  return fetchJSON(`${getBase()}/apps/${encodeURIComponent(project)}`);
}

export async function listAppShares(project: string): Promise<{ shares: AppShare[] }> {
  return fetchJSON(`${getBase()}/apps/${encodeURIComponent(project)}/shares`);
}

export async function createAppShare(
  project: string,
  body: { scope: AppShareScope; user_id?: string; expires_in_days?: number },
): Promise<AppShare> {
  return fetchJSON(`${getBase()}/apps/${encodeURIComponent(project)}/shares`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function revokeAppShare(shareId: string): Promise<{ deleted: string }> {
  return fetchJSON(`${getBase()}/apps/shares/${encodeURIComponent(shareId)}`, { method: "DELETE" });
}

export async function listAppShareDirectory(): Promise<{ users: { id: string; name: string; email: string }[] }> {
  return fetchJSON(`${getBase()}/apps/directory`);
}

export async function stopApp(project: string): Promise<{ project: string; stopped: number }> {
  return fetchJSON(`${getBase()}/apps/stop?project=${encodeURIComponent(project)}`, { method: "POST" });
}

export async function startAppByProject(project: string): Promise<{ project: string; started: number }> {
  return fetchJSON(`${getBase()}/apps/start?project=${encodeURIComponent(project)}`, { method: "POST" });
}

export async function removeApp(project: string): Promise<{ project: string; removed: number }> {
  return fetchJSON(`${getBase()}/apps/remove?project=${encodeURIComponent(project)}`, { method: "POST" });
}

export interface AppLogContainer { name: string; service: string; status: string; logs: string }
export async function getAppLogs(project: string, tail = 200): Promise<{ project: string; containers: AppLogContainer[] }> {
  return fetchJSON(`${getBase()}/apps/logs?project=${encodeURIComponent(project)}&tail=${tail}`);
}

export async function reportApp(project: string, error: string, path: string | null): Promise<{ ok: boolean; task_id: string; agent_id: string; agent_name: string }> {
  return fetchJSON(`${getBase()}/apps/report?project=${encodeURIComponent(project)}`, {
    method: "POST",
    body: JSON.stringify({ error, path }),
  });
}

// --- Presence (who is online) ---
export interface OnlineUser { id: string; name: string; email: string; role: string; last_seen_seconds_ago: number }

export async function presenceHeartbeat(invisible = false): Promise<{ ok: boolean }> {
  return fetchJSON(`${getBase()}/presence/heartbeat?invisible=${invisible}`, { method: "POST" });
}

export async function getOnlineUsers(): Promise<{ online: OnlineUser[]; count: number }> {
  return fetchJSON(`${getBase()}/presence/online`);
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

export interface AgentDevelopment {
  agent_id: string;
  days: number;
  tasks: { total: number; failed: number; failure_rate: number };
  failure_rate_recent: number;
  failure_rate_older: number;
  ratings: { count: number; avg_recent: number | null; avg_older: number | null };
  /** Anteil der Aufgaben, die noch einmal angefasst werden mussten: fortgesetzte
   *  Laeufe (`resumed_from_task`) plus vom Menschen zurueckgegebene (Bewertung <= 2). */
  rework: {
    count: number;
    rate: number;
    rate_recent: number;
    rate_older: number;
    resumed: number;
    poorly_rated: number;
  };
  plan_adherence: { planned: number; done: number; rate: number };
  trend: string;
  probation: {
    days_active: number;
    review_due: boolean;
    onboarded: boolean;
    has_responsibilities: boolean;
  };
}

// Gespraech verzweigen, zurueckspulen, zusammenfassen (#538). Alle drei arbeiten
// auf "die Nachrichten bis hierher" und liefern die Kennung des neuen Gespraechs.
export async function forkChatSession(agentId: string, sessionId: string, messageId: string) {
  return fetchJSON<{ ok: boolean; session_id: string; copied: number }>(
    `${getBase()}/agents/${agentId}/chat/sessions/${encodeURIComponent(sessionId)}/fork`,
    { method: "POST", body: JSON.stringify({ message_id: messageId }) },
  );
}

export async function rewindChatSession(agentId: string, sessionId: string, messageId: string) {
  return fetchJSON<{ ok: boolean; removed: number; backup_session_id: string | null }>(
    `${getBase()}/agents/${agentId}/chat/sessions/${encodeURIComponent(sessionId)}/rewind`,
    { method: "POST", body: JSON.stringify({ message_id: messageId }) },
  );
}

export async function summarizeChatSession(agentId: string, sessionId: string) {
  return fetchJSON<{ ok: boolean; session_id: string; summarized: number }>(
    `${getBase()}/agents/${agentId}/chat/sessions/${encodeURIComponent(sessionId)}/summarize`,
    { method: "POST" },
  );
}

// Teams-Anrufe: Agent mit Stimme im Termin (service-hosted media).
export interface TeamsCallingSetup {
  callback_url: string;
  https_ok: boolean;
  app_id: string;
  tenant_id: string;
  has_secret: boolean;
  configured: boolean;
  enabled: boolean;
  permissions: { name: string; why: string }[];
}

export async function getTeamsCallingSetup(): Promise<TeamsCallingSetup> {
  return fetchJSON(`${getBase()}/teams/calling/setup`);
}

export async function testTeamsCalling(): Promise<{ ok: boolean; reason: string }> {
  return fetchJSON(`${getBase()}/teams/calling/test`, { method: "POST" });
}

export async function joinTeamsMeeting(joinUrl: string, agentId: string) {
  return fetchJSON(`${getBase()}/teams/calling/join`, {
    method: "POST",
    body: JSON.stringify({ join_url: joinUrl, agent_id: agentId }),
  });
}

// Admin-Concierge (#11): setzt vorhandene Abfragen zusammen — bewusst ohne
// Sprachmodell, ein Concierge der eine Zahl halluziniert ist schlimmer als keiner.
/** Ein Punkt, der eine Entscheidung oder einen Handgriff braucht. */
export interface ConciergeItem {
  kind: string;
  /** "broken" = kaputt (rot) · "waiting" = wartet auf eine Entscheidung (gelb). */
  severity: "broken" | "waiting";
  title: string;
  detail: string;
  agent_id: string | null;
  /** Eine der wenigen sicheren Aktionen — direkt hier ausführbar. */
  action: string | null;
  action_label: string | null;
  /** Seite, auf der es genauer angesehen/entschieden wird. */
  link: string | null;
  count: number;
}

export interface ConciergeOverview {
  verdict: string;
  /** Die eigentliche Liste. Leer heißt: nichts wartet auf dich. */
  items?: ConciergeItem[];
  /** Kennzahlen als Fußnote — sie verlangen keine Handlung. */
  stats?: {
    agents: number;
    resting: number;
    tasks_24h: number;
    failed_24h: number;
    cost_24h_usd: number;
  };
  agents: {
    total: number;
    by_state: Record<string, number>;
    /** Wirklich kaputt (Fehlerzustand) — das Einzige, was rot rechtfertigt. */
    broken?: { id: string; name: string; state: string }[];
    /** Angehalten. Normalzustand: der Nutzer oder der Idle-Stopp war es, und beim
     *  nächsten Auftrag wacht der Agent von allein auf. `skips_proactive` markiert
     *  die Ausnahme: mit Verantwortungsbereichen fallen proaktive Läufe aus. */
    resting?: { id: string; name: string; state: string; skips_proactive: boolean }[];
    /** Alte Bezeichnung, inhaltlich = `broken`. */
    unhealthy: { id: string; name: string; state: string }[];
  };
  tasks_24h: { total: number; failed: number; running: number; stale: number };
  cost_24h_usd: number;
  pending_approvals: number;
  actions: { id: string; label: string }[];
}

export async function getConciergeOverview(): Promise<ConciergeOverview> {
  return fetchJSON(`${getBase()}/concierge/overview`);
}

export async function runConciergeAction(action: string, agentId?: string) {
  return fetchJSON(`${getBase()}/concierge/action`, {
    method: "POST",
    body: JSON.stringify({ action, agent_id: agentId ?? null }),
  });
}

// Was hat die Plattform dazugelernt? Setzt vorhandene Daten zusammen (Skill-Entwuerfe
// der Nachtschicht, ueberarbeitete Skills, Erinnerungen aus der Reflexion) — die
// Mechanik lief laengst, sichtbar war sie nirgends.
export interface LearnedSkill {
  id: number;
  name: string;
  description: string;
  status: string;
  origin: "nachtschicht" | "agent" | "import" | "mensch";
  version: number;
  usage_count: number;
  avg_rating: number | null;
  created_at: string | null;
}

export interface SelfImprovement {
  period_days: number;
  summary: {
    skills_learned: number;
    skills_awaiting_review: number;
    skills_improved: number;
    improvements_kept: number;
    improvements_reverted: number;
    memories_from_reflection: number;
    reflection_runs: number;
  };
  awaiting_review: LearnedSkill[];
  learned: LearnedSkill[];
  improved: LearnedSkill[];
  runs: {
    id: number;
    started_at: string | null;
    status: string;
    facts_new: number;
    skills_drafted: number;
    kb_entries: number;
  }[];
  scoped: boolean;
}

export async function getSelfImprovement(days = 30): Promise<SelfImprovement> {
  return fetchJSON<SelfImprovement>(`${getBase()}/analytics/self-improvement?days=${days}`);
}

/** Wird dieser Agent messbar besser? Backend: analytics.agent_development. */
export async function getAgentDevelopment(agentId: string, days = 30): Promise<AgentDevelopment> {
  return fetchJSON<AgentDevelopment>(
    `${getBase()}/analytics/agents/${agentId}/development?days=${days}`,
  );
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

export async function getTeamTasks(id: string): Promise<{ tasks: Task[]; total: number; team_id: string }> {
  return fetchJSON(`${getBase()}/teams/${id}/tasks`);
}

// --- Eigene Menuepunkte: fremde Seiten als Rahmen oder Link -------------------
// Der Server liefert unter /mine nur, was die Rolle sehen darf (menu_paths) —
// die Seitenleiste filtert nicht selbst nach, sie zeigt einfach was ankommt.

export type CustomPageOpenMode = "iframe" | "link";

export interface CustomPage {
  id: number;
  slug: string;
  title: string;
  description: string | null;
  url: string;
  icon: string;
  group_key: string;
  open_mode: CustomPageOpenMode;
  sort_order: number;
  enabled: boolean;
  allow_media: boolean;
  menu_path: string;
}

export interface CustomPageInput {
  slug: string;
  title: string;
  url: string;
  description?: string | null;
  icon?: string;
  group_key?: string;
  open_mode?: CustomPageOpenMode;
  sort_order?: number;
  enabled?: boolean;
  allow_media?: boolean;
}

/** Menuepunkte fuer den angemeldeten Nutzer (bereits nach Rolle gefiltert). */
export async function listMyCustomPages(): Promise<{ pages: CustomPage[] }> {
  return fetchJSON(`${getBase()}/custom-pages/mine`);
}

/** Eine Seite zum Anzeigen. 403, wenn die Rolle sie nicht sehen darf. */
export async function getCustomPageBySlug(slug: string): Promise<CustomPage> {
  return fetchJSON(`${getBase()}/custom-pages/by-slug/${encodeURIComponent(slug)}`);
}

/** Alle Seiten inkl. abgeschalteter — nur fuer Administratoren. */
export async function listCustomPages(): Promise<{ pages: CustomPage[]; groups: string[]; modes: CustomPageOpenMode[] }> {
  return fetchJSON(`${getBase()}/custom-pages/`);
}

export async function createCustomPage(body: CustomPageInput): Promise<CustomPage> {
  return fetchJSON(`${getBase()}/custom-pages/`, { method: "POST", body: JSON.stringify(body) });
}

export async function updateCustomPage(id: number, body: Partial<CustomPageInput>): Promise<CustomPage> {
  return fetchJSON(`${getBase()}/custom-pages/${id}`, { method: "PATCH", body: JSON.stringify(body) });
}

export async function deleteCustomPage(id: number): Promise<{ deleted: number; menu_path: string }> {
  return fetchJSON(`${getBase()}/custom-pages/${id}`, { method: "DELETE" });
}

/* ── Meine KI-Zugaenge (eigenes Claude-/Codex-Abo) ────────────────────────
   Die Schnittstelle gibt es seit v1.185.0; bis 2026-08-15 rief sie niemand auf,
   weil die Oberflaeche dazu fehlte. Das Geheimnis kommt bewusst NIE zurueck —
   man sieht nur, dass etwas hinterlegt ist. */
export async function getMyAiCredentials(): Promise<{
  credentials: {
    harness: string; label: string | null; last_status: string | null;
    last_used_at: string | null; created_at: string | null;
  }[];
  team_license_allowed: boolean;
}> {
  return fetchJSON(`${getBase()}/me/ai-credentials`);
}

export async function putMyAiCredential(body: {
  harness: string; secret: string; label: string | null;
}): Promise<unknown> {
  return fetchJSON(`${getBase()}/me/ai-credentials`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function deleteMyAiCredential(harness: string): Promise<unknown> {
  return fetchJSON(`${getBase()}/me/ai-credentials/${harness}`, { method: "DELETE" });
}

/* Anmeldung fuer den EIGENEN Zugang — gleicher Ablauf wie beim Administrator,
   aber das Ergebnis landet in `user_ai_credentials` statt als plattformweite
   Integration. Nur aus dieser Ablage liest der Agentenbau. */
export async function startMyAnthropicLogin(): Promise<{ auth_url: string }> {
  return fetchJSON(`${getBase()}/me/ai-credentials/anthropic/start`, { method: "POST" });
}

export async function exchangeMyAnthropicLogin(body: {
  code: string; state: string; label: string | null;
}): Promise<{ status: string; label: string; hint: string }> {
  return fetchJSON(`${getBase()}/me/ai-credentials/anthropic/exchange`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function startMyCodexLogin(): Promise<{
  session_id: string; verification_uri: string; user_code: string;
  expires_at: string; status: string;
}> {
  return fetchJSON(`${getBase()}/me/ai-credentials/codex/start`, { method: "POST" });
}

export async function getMyCodexLoginStatus(sessionId: string): Promise<{
  status: string; account_label: string | null; error: string | null;
  user_code: string | null; verification_uri: string | null;
}> {
  return fetchJSON(`${getBase()}/me/ai-credentials/codex/status/${sessionId}`);
}

// --- SSO-Gruppen auf Rollen abbilden (Entra/Azure AD, SAML) -------------------
// Ersetzt die freie JSON-Textbox: Zuordnungen leben serverseitig in einer Tabelle,
// die Verwaltung zeigt tatsaechlich gesehene Gruppennamen zum Anklicken an.

export type SsoProvider = "microsoft" | "saml";
export type SsoTargetKind = "role" | "custom_role";

export interface SsoGroupRoleMapping {
  id: number;
  provider: SsoProvider;
  group_name: string;
  target_kind: SsoTargetKind;
  target_value: string;
  custom_role_name: string | null;
  priority: number;
}

export interface SsoObservedGroup {
  group_name: string;
  first_seen_at: string | null;
  last_seen_at: string | null;
  mapped: boolean;
}

export async function listSsoGroupMappings(provider?: SsoProvider): Promise<{
  mappings: SsoGroupRoleMapping[];
  providers: SsoProvider[];
  target_kinds: SsoTargetKind[];
  roles: string[];
}> {
  const qs = provider ? `?provider=${provider}` : "";
  return fetchJSON(`${getBase()}/sso-group-mappings/${qs}`);
}

export async function listSsoObservedGroups(provider: SsoProvider): Promise<{ groups: SsoObservedGroup[] }> {
  return fetchJSON(`${getBase()}/sso-group-mappings/observed?provider=${provider}`);
}

export async function createSsoGroupMapping(body: {
  provider: SsoProvider; group_name: string; target_kind: SsoTargetKind;
  target_value: string; priority?: number;
}): Promise<SsoGroupRoleMapping> {
  return fetchJSON(`${getBase()}/sso-group-mappings/`, { method: "POST", body: JSON.stringify(body) });
}

export async function updateSsoGroupMapping(
  id: number,
  body: Partial<{ target_kind: SsoTargetKind; target_value: string; priority: number }>,
): Promise<SsoGroupRoleMapping> {
  return fetchJSON(`${getBase()}/sso-group-mappings/${id}`, { method: "PATCH", body: JSON.stringify(body) });
}

export async function deleteSsoGroupMapping(id: number): Promise<{ deleted: number }> {
  return fetchJSON(`${getBase()}/sso-group-mappings/${id}`, { method: "DELETE" });
}
