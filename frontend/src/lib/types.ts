export type AgentState =
  | "created"
  | "running"
  | "idle"
  | "working"
  | "stopped"
  | "error";

export type TaskStatus =
  | "pending"
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export interface Agent {
  id: string;
  name: string;
  container_id: string | null;
  state: AgentState;
  model: string;
  role: string | null;
  onboarding_complete: boolean;
  integrations: string[];
  permissions: string[];
  update_available: boolean;
  budget_usd: number | null;
  total_cost_usd: number;
  user_id: string | null;
  created_at: string;
  updated_at: string;
  current_task: string | null;
  cpu_percent: number | null;
  memory_usage_mb: number | null;
  queue_depth: number | null;
}

export type UserRole = "admin" | "manager" | "member" | "viewer";

export interface AdminUser {
  id: string;
  email: string;
  name: string;
  role: UserRole;
  is_active: boolean;
}

export interface Integration {
  provider: string;
  display_name: string;
  icon: string;
  description: string;
  connected: boolean;
  account_label: string | null;
  expires_at: string | null;
  scopes: string;
  available: boolean;
  auth_type: "oauth" | "pat";
}

export interface Task {
  id: string;
  title: string;
  prompt: string;
  status: TaskStatus;
  priority: number;
  agent_id: string | null;
  model: string | null;
  result: string | null;
  error: string | null;
  cost_usd: number | null;
  duration_ms: number | null;
  num_turns: number | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface LogEvent {
  agent_id: string;
  task_id: string;
  type: "text" | "tool_call" | "tool_result" | "error" | "system" | "result" | "raw";
  data: Record<string, unknown>;
  timestamp: string;
}

export interface FileEntry {
  name: string;
  type: "file" | "directory";
  size: number;
  modified: number;
  path: string;
}

export interface Schedule {
  id: string;
  name: string;
  prompt: string;
  interval_seconds: number;
  priority: number;
  agent_id: string | null;
  model: string | null;
  enabled: boolean;
  next_run_at: string;
  last_run_at: string | null;
  total_runs: number;
  success_count: number;
  fail_count: number;
  success_rate: number;
  created_at: string;
  updated_at: string;
}

export type ModelProvider = "anthropic" | "bedrock" | "vertex" | "foundry";

export interface Settings {
  has_api_key: boolean;
  has_oauth_token: boolean;
  auth_method: "api_key" | "oauth_token" | "none";
  has_telegram: boolean;
  default_model: string;
  max_turns: number;
  max_agents: number;
  registration_open: boolean;
  // Provider info
  model_provider: ModelProvider;
  has_bedrock: boolean;
  has_vertex: boolean;
  has_foundry: boolean;
  aws_region: string;
  vertex_region: string;
  foundry_resource: string;
}

export interface AgentMemory {
  id: number;
  agent_id: string;
  category: string;
  key: string;
  content: string;
  importance: number;
  access_count: number;
  created_at: string;
  updated_at: string;
}

export interface Notification {
  id: number;
  agent_id: string;
  type: "info" | "warning" | "error" | "success" | "approval";
  title: string;
  message: string;
  priority: "low" | "normal" | "high" | "urgent";
  read: boolean;
  action_url: string | null;
  meta: Record<string, unknown> | null;
  created_at: string;
}

export interface ProactiveConfig {
  enabled: boolean;
  schedule_id: string | null;
  interval_seconds: number;
}

export interface ProactiveResponse {
  agent_id: string;
  proactive: ProactiveConfig;
  schedule: {
    enabled: boolean;
    interval_seconds: number;
    next_run_at: string | null;
    last_run_at: string | null;
    total_runs: number;
    success_count: number;
    fail_count: number;
  } | null;
}

export interface PermissionPackage {
  id: string;
  label: string;
  description: string;
  icon: string;
  default: boolean;
}

export interface WebhookEvent {
  id: number;
  source: string;
  event_type: string;
  status: string;
  task_id: string | null;
  created_at: string;
}

export type TodoStatus = "pending" | "in_progress" | "completed";

export interface AgentTodo {
  id: number;
  agent_id: string;
  task_id: string | null;
  title: string;
  description: string | null;
  status: TodoStatus;
  priority: number;
  sort_order: number;
  created_at: string;
  updated_at: string | null;
  completed_at: string | null;
}

export interface TodoListResponse {
  todos: AgentTodo[];
  total: number;
  pending: number;
  in_progress: number;
  completed: number;
}

export type FeedbackStatus = "pending" | "reviewed" | "in_progress" | "closed";
export type FeedbackCategory = "bug" | "feature" | "improvement" | "general";

export interface Feedback {
  id: number;
  user_id: string;
  user_name: string | null;
  title: string;
  description: string | null;
  category: FeedbackCategory;
  status: FeedbackStatus;
  admin_notes: string | null;
  github_issue_url: string | null;
  created_at: string;
  updated_at: string | null;
}

export interface FeedbackListResponse {
  feedback: Feedback[];
  total: number;
  pending: number;
  reviewed: number;
  in_progress: number;
  closed: number;
}

export interface AgentTemplate {
  id: number;
  name: string;
  display_name: string;
  description: string;
  icon: string;
  category: string;
  model: string;
  role: string;
  permissions: string[];
  integrations: string[];
  mcp_server_ids: number[];
  knowledge_template: string;
  is_builtin: boolean;
  created_by: string | null;
  created_at: string | null;
}

export interface ApprovalRequest {
  approval_id: string;
  agent_id: string;
  tool: string;
  input: Record<string, unknown>;
  reasoning: string;
  risk_level: "blocked" | "high" | "medium" | "low";
  status: "pending" | "approved" | "denied";
  created_at: string;
  approved_by?: string;
  approved_at?: string;
  denied_by?: string;
  denied_at?: string;
  deny_reason?: string;
}
