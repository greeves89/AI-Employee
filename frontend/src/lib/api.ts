import type { Agent, Task, Schedule, FileEntry, Settings } from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const BASE = `${API_URL}/api/v1`;

async function fetchJSON<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
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

export async function createAgent(name: string, model?: string, role?: string): Promise<Agent> {
  return fetchJSON(`${BASE}/agents/`, {
    method: "POST",
    body: JSON.stringify({ name, model, role }),
  });
}

export async function stopAgent(id: string): Promise<void> {
  await fetchJSON(`${BASE}/agents/${id}/stop`, { method: "POST" });
}

export async function startAgent(id: string): Promise<void> {
  await fetchJSON(`${BASE}/agents/${id}/start`, { method: "POST" });
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
    { method: "POST", body: formData }
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
