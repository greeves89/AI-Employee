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

export type AgentMode = "claude_code" | "custom_llm";
export type LLMProviderType = "openai" | "anthropic" | "google" | "ollama" | "lm-studio";

export interface LLMConfig {
  provider_type: LLMProviderType;
  api_endpoint: string;
  api_key: string;
  model_name: string;
  max_tokens?: number;
  temperature?: number;
  system_prompt?: string;
  tools_enabled?: boolean;
}

export interface LLMConfigResponse {
  provider_type: string;
  api_endpoint: string;
  model_name: string;
  max_tokens: number;
  temperature: number;
  system_prompt: string;
  tools_enabled: boolean;
}

export interface Agent {
  id: string;
  name: string;
  container_id: string | null;
  state: AgentState;
  model: string;
  model_provider: ModelProvider;
  mode: AgentMode;
  llm_config: LLMConfigResponse | null;
  role: string | null;
  onboarding_complete: boolean;
  integrations: string[];
  permissions: string[];
  update_available: boolean;
  budget_usd: number | null;
  browser_mode: boolean;
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
  input_tokens: number | null;
  output_tokens: number | null;
  duration_ms: number | null;
  num_turns: number | null;
  parent_task_id: string | null;
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
  cron_expression: string | null;
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

export interface AuditLog {
  id: number;
  agent_id: string;
  task_id: string | null;
  approval_id: string | null;
  event_type: string;
  command: string | null;
  outcome: "success" | "failure" | "blocked";
  exit_code: number | null;
  user_id: string | null;
  meta: Record<string, unknown> | null;
  created_at: string;
}

export interface AuditSummary {
  by_event_type: Record<string, number>;
  by_outcome: Record<string, number>;
  total: number;
}

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
  // OAuth integrations
  has_google_oauth: boolean;
  has_microsoft_oauth: boolean;
  has_apple_oauth: boolean;
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
  project: string | null;
  project_path: string | null;
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
  projects: string[];
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
  is_published: boolean;
  published_at: string | null;
  created_by: string | null;
  created_at: string | null;
}

export interface ApprovalRequest {
  approval_id: string;
  agent_id: string;
  // Tool-based approval (bash commands)
  tool: string | null;
  input: Record<string, unknown> | null;
  reasoning: string | null;
  risk_level: "blocked" | "high" | "medium" | "low";
  // Question-based approval (custom LLM agents)
  question?: string | null;
  options?: string[] | null;
  context?: string | null;
  // Status
  status: "pending" | "approved" | "denied";
  created_at: string;
  approved_by?: string;
  approved_at?: string;
  denied_by?: string;
  denied_at?: string;
  deny_reason?: string;
}

// Docker Apps
export interface DockerAppPort {
  host_port: number;
  container_port: string;
  host_ip: string;
}

export interface DockerAppContainer {
  id: string;
  name: string;
  service: string;
  image: string;
  status: string;
  state: string;
  ports: DockerAppPort[];
  exposed_ports?: string[];
}

export interface DockerAppService {
  name: string;
  image: string;
  build: boolean;
  ports: (string | number)[];
}

export interface DockerApp {
  name: string;
  path: string;
  compose_file: string;
  services: DockerAppService[];
  status: "running" | "stopped" | "partial";
  containers: DockerAppContainer[];
  error?: string;
}

export interface DockerAppLog {
  service: string;
  line: string;
}

// Knowledge Base
export interface KnowledgeEntry {
  id: number;
  title: string;
  content: string;
  tags: string[];
  backlinks: string[];
  incoming_backlinks?: string[];
  created_by: string | null;
  updated_by: string | null;
  access_count: number;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeTag {
  name: string;
  count: number;
}

export interface KnowledgeGraphNode {
  id: number;
  title: string;
  tags: string[];
  size: number;
}

export interface KnowledgeGraphEdge {
  source: number;
  target: number;
}

export type MeetingRoomState = "idle" | "running" | "paused" | "completed";

export interface MeetingMessage {
  role: "agent" | "system";
  agent_id: string | null;
  content: string;
  timestamp: string;
  round?: number;
}

export interface MeetingRoom {
  id: string;
  name: string;
  topic: string;
  agent_ids: string[];
  agent_names?: Record<string, string>;
  state: MeetingRoomState;
  current_turn: number;
  rounds_completed: number;
  max_rounds: number;
  messages?: MeetingMessage[];
  message_count?: number;
  created_at: string | null;
}
