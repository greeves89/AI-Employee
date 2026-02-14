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
  created_at: string;
  updated_at: string;
  current_task: string | null;
  cpu_percent: number | null;
  memory_usage_mb: number | null;
  queue_depth: number | null;
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

export interface Settings {
  has_api_key: boolean;
  has_oauth_token: boolean;
  auth_method: "api_key" | "oauth_token" | "none";
  has_telegram: boolean;
  default_model: string;
  max_turns: number;
  max_agents: number;
}
