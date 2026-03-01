"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  CheckCircle2,
  Circle,
  Clock,
  FolderOpen,
  Loader2,
  ListTodo,
  Plus,
  RefreshCw,
  Trash2,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  getAgentTodos,
  createAgentTodo,
  updateAgentTodo,
  deleteAgentTodo,
} from "@/lib/api";
import type { AgentTodo, TodoStatus } from "@/lib/types";

const priorityColors: Record<number, string> = {
  1: "bg-red-500/20 text-red-400 border-red-500/30",
  2: "bg-orange-500/20 text-orange-400 border-orange-500/30",
  3: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
  4: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  5: "bg-zinc-500/20 text-zinc-400 border-zinc-500/30",
};

const priorityLabels: Record<number, string> = {
  1: "Urgent",
  2: "High",
  3: "Medium",
  4: "Low",
  5: "Optional",
};

const statusIcons: Record<TodoStatus, typeof Circle> = {
  pending: Circle,
  in_progress: Clock,
  completed: CheckCircle2,
};

interface TodoTabProps {
  agentId: string;
}

export function TodoTab({ agentId }: TodoTabProps) {
  const [todos, setTodos] = useState<AgentTodo[]>([]);
  const [loading, setLoading] = useState(true);
  const [counts, setCounts] = useState({ pending: 0, in_progress: 0, completed: 0 });
  const [projects, setProjects] = useState<string[]>([]);
  const [selectedProject, setSelectedProject] = useState<string | null>(null);
  const [showCompleted, setShowCompleted] = useState(false);
  const [showAddForm, setShowAddForm] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [newPriority, setNewPriority] = useState(3);
  const [newProject, setNewProject] = useState("");
  const pollRef = useRef<ReturnType<typeof setInterval>>();

  const fetchTodos = useCallback(async () => {
    try {
      const data = await getAgentTodos(
        agentId,
        undefined,
        undefined,
        selectedProject ?? undefined,
      );
      setTodos(data.todos);
      setCounts({
        pending: data.pending,
        in_progress: data.in_progress,
        completed: data.completed,
      });
      // Only update project list when not filtering (so we always see all projects)
      if (!selectedProject) {
        setProjects(data.projects || []);
      }
    } catch {
      // ignore
    }
    setLoading(false);
  }, [agentId, selectedProject]);

  useEffect(() => {
    fetchTodos();
    pollRef.current = setInterval(fetchTodos, 15000);
    return () => clearInterval(pollRef.current);
  }, [fetchTodos]);

  const handleToggle = async (todo: AgentTodo) => {
    const newStatus: TodoStatus =
      todo.status === "completed" ? "pending" : "completed";
    try {
      await updateAgentTodo(todo.id, { status: newStatus });
      fetchTodos();
    } catch {
      // ignore
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await deleteAgentTodo(id);
      setTodos((prev) => prev.filter((t) => t.id !== id));
    } catch {
      // ignore
    }
  };

  const handleAdd = async () => {
    if (!newTitle.trim()) return;
    try {
      await createAgentTodo(agentId, {
        title: newTitle.trim(),
        description: newDescription.trim() || undefined,
        priority: newPriority,
        project: newProject.trim() || undefined,
      });
      setNewTitle("");
      setNewDescription("");
      setNewPriority(3);
      setNewProject("");
      setShowAddForm(false);
      fetchTodos();
    } catch {
      // ignore
    }
  };

  const activeTodos = todos.filter((t) => t.status !== "completed");
  const completedTodos = todos.filter((t) => t.status === "completed");

  // Group active todos by project (primary) then task_id (secondary)
  const projectGroups = new Map<string, AgentTodo[]>();
  for (const todo of activeTodos) {
    const key = todo.project || "_general";
    if (!projectGroups.has(key)) projectGroups.set(key, []);
    projectGroups.get(key)!.push(todo);
  }

  // Sort: named projects alphabetically first, then "_general" last
  const sortedGroups = Array.from(projectGroups.entries()).sort((a, b) => {
    if (a[0] === "_general" && b[0] !== "_general") return 1;
    if (a[0] !== "_general" && b[0] === "_general") return -1;
    return a[0].localeCompare(b[0]);
  });

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
            <ListTodo className="h-4 w-4 text-primary" />
          </div>
          <div>
            <h3 className="text-sm font-semibold">
              TODOs
              <span className="ml-2 text-xs font-normal text-muted-foreground">
                ({counts.pending + counts.in_progress} offen, {counts.completed} erledigt)
              </span>
            </h3>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={fetchTodos}
            className="flex h-7 items-center gap-1.5 rounded-lg px-2.5 text-[11px] text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] transition-colors"
          >
            <RefreshCw className="h-3 w-3" />
          </button>
          <button
            onClick={() => setShowAddForm(!showAddForm)}
            className="flex h-7 items-center gap-1.5 rounded-lg px-2.5 text-[11px] font-medium bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
          >
            <Plus className="h-3 w-3" />
            Hinzufuegen
          </button>
        </div>
      </div>

      {/* Project filter pills */}
      {projects.length > 0 && (
        <div className="flex items-center gap-1.5 flex-wrap">
          <button
            onClick={() => setSelectedProject(null)}
            className={cn(
              "rounded-full px-3 py-1 text-[10px] font-medium transition-colors border",
              selectedProject === null
                ? "bg-primary/15 text-primary border-primary/30"
                : "text-muted-foreground/60 border-foreground/[0.08] hover:text-foreground hover:border-foreground/20"
            )}
          >
            Alle
          </button>
          {projects.map((p) => (
            <button
              key={p}
              onClick={() => setSelectedProject(selectedProject === p ? null : p)}
              className={cn(
                "rounded-full px-3 py-1 text-[10px] font-medium transition-colors border",
                selectedProject === p
                  ? "bg-primary/15 text-primary border-primary/30"
                  : "text-muted-foreground/60 border-foreground/[0.08] hover:text-foreground hover:border-foreground/20"
              )}
            >
              <FolderOpen className="h-2.5 w-2.5 inline mr-1 -mt-px" />
              {p}
            </button>
          ))}
        </div>
      )}

      {/* Progress bar */}
      {todos.length > 0 && (
        <div className="space-y-1.5">
          <div className="h-2 rounded-full bg-foreground/[0.06] overflow-hidden">
            <div
              className="h-full rounded-full bg-gradient-to-r from-emerald-500 to-emerald-400 transition-all duration-500"
              style={{
                width: `${todos.length > 0 ? (counts.completed / todos.length) * 100 : 0}%`,
              }}
            />
          </div>
          <div className="flex items-center gap-4 text-[10px] text-muted-foreground/60 tabular-nums">
            <span className="flex items-center gap-1">
              <span className="h-1.5 w-1.5 rounded-full bg-zinc-500" />
              {counts.pending} pending
            </span>
            <span className="flex items-center gap-1">
              <span className="h-1.5 w-1.5 rounded-full bg-blue-500" />
              {counts.in_progress} in progress
            </span>
            <span className="flex items-center gap-1">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
              {counts.completed} completed
            </span>
          </div>
        </div>
      )}

      {/* Add form */}
      {showAddForm && (
        <div className="rounded-xl border border-foreground/[0.08] bg-card/80 p-4 space-y-3">
          <input
            type="text"
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
            placeholder="TODO Titel..."
            className="w-full rounded-lg border border-foreground/[0.08] bg-background/80 px-3 py-2 text-sm outline-none focus:border-primary/50"
            autoFocus
            onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && handleAdd()}
          />
          <textarea
            value={newDescription}
            onChange={(e) => setNewDescription(e.target.value)}
            placeholder="Beschreibung (optional)..."
            className="w-full rounded-lg border border-foreground/[0.08] bg-background/80 px-3 py-2 text-sm outline-none focus:border-primary/50 resize-none"
            rows={2}
          />
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1.5">
              <FolderOpen className="h-3 w-3 text-muted-foreground/50" />
              <input
                type="text"
                value={newProject}
                onChange={(e) => setNewProject(e.target.value)}
                placeholder="Projekt (optional)"
                list="project-suggestions"
                className="w-36 rounded-lg border border-foreground/[0.08] bg-background/80 px-2 py-1 text-xs outline-none focus:border-primary/50"
              />
              <datalist id="project-suggestions">
                {projects.map((p) => (
                  <option key={p} value={p} />
                ))}
              </datalist>
            </div>
          </div>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-[11px] text-muted-foreground">Prioritaet:</span>
              {[1, 2, 3, 4, 5].map((p) => (
                <button
                  key={p}
                  onClick={() => setNewPriority(p)}
                  className={cn(
                    "h-6 rounded-md px-2 text-[10px] font-medium border transition-all",
                    newPriority === p
                      ? priorityColors[p]
                      : "border-transparent text-muted-foreground/40 hover:text-muted-foreground"
                  )}
                >
                  {priorityLabels[p]}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setShowAddForm(false)}
                className="flex h-7 items-center gap-1 rounded-lg px-2.5 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
              >
                <X className="h-3 w-3" />
                Abbrechen
              </button>
              <button
                onClick={handleAdd}
                disabled={!newTitle.trim()}
                className="flex h-7 items-center gap-1 rounded-lg px-2.5 text-[11px] font-medium bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-40 transition-colors"
              >
                Erstellen
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Empty state */}
      {todos.length === 0 && (
        <div className="rounded-xl border border-dashed border-foreground/[0.1] bg-card/30 p-12 text-center">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-foreground/[0.06] mb-3">
            <ListTodo className="h-6 w-6 text-muted-foreground" />
          </div>
          <h3 className="text-sm font-semibold mb-1">Keine TODOs</h3>
          <p className="text-xs text-muted-foreground">
            Der Agent erstellt automatisch TODOs wenn er Aufgaben bekommt.
            Du kannst auch manuell welche hinzufuegen.
          </p>
        </div>
      )}

      {/* Active TODOs grouped by project */}
      {sortedGroups.map(([projectKey, groupTodos]) => (
        <div key={projectKey} className="space-y-1">
          {/* Group header */}
          <div className="flex items-center gap-2 px-1 pb-1">
            {projectKey !== "_general" && (
              <FolderOpen className="h-3 w-3 text-muted-foreground/40 shrink-0" />
            )}
            <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground/50">
              {projectKey !== "_general" ? projectKey : "Allgemein"}
            </span>
            <div className="flex-1 h-px bg-foreground/[0.06]" />
            <span className="text-[10px] text-muted-foreground/40 tabular-nums">
              {groupTodos.filter((t) => t.status === "completed").length}/{groupTodos.length}
            </span>
          </div>

          {/* TODO items */}
          {groupTodos.map((todo) => (
            <TodoItem
              key={todo.id}
              todo={todo}
              onToggle={handleToggle}
              onDelete={handleDelete}
            />
          ))}
        </div>
      ))}

      {/* Completed section */}
      {completedTodos.length > 0 && (
        <div className="space-y-1">
          <button
            onClick={() => setShowCompleted(!showCompleted)}
            className="flex items-center gap-2 px-1 pb-1 w-full text-left"
          >
            <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground/50">
              Erledigt ({completedTodos.length})
            </span>
            <div className="flex-1 h-px bg-foreground/[0.06]" />
            <span className="text-[10px] text-emerald-500">
              {showCompleted ? "Verbergen" : "Anzeigen"}
            </span>
          </button>
          {showCompleted &&
            completedTodos.map((todo) => (
              <TodoItem
                key={todo.id}
                todo={todo}
                onToggle={handleToggle}
                onDelete={handleDelete}
              />
            ))}
        </div>
      )}
    </div>
  );
}

/* --- Single TODO Item --- */

function TodoItem({
  todo,
  onToggle,
  onDelete,
}: {
  todo: AgentTodo;
  onToggle: (todo: AgentTodo) => void;
  onDelete: (id: number) => void;
}) {
  const StatusIcon = statusIcons[todo.status];
  const isCompleted = todo.status === "completed";
  const isInProgress = todo.status === "in_progress";

  return (
    <div
      className={cn(
        "group flex items-start gap-3 rounded-lg px-3 py-2.5 transition-colors",
        isInProgress
          ? "bg-blue-500/[0.06] border border-blue-500/20"
          : "hover:bg-foreground/[0.03]",
        isCompleted && "opacity-50"
      )}
    >
      {/* Status toggle */}
      <button
        onClick={() => onToggle(todo)}
        className={cn(
          "mt-0.5 shrink-0 transition-colors",
          isCompleted
            ? "text-emerald-500"
            : isInProgress
            ? "text-blue-400"
            : "text-muted-foreground/30 hover:text-muted-foreground"
        )}
      >
        <StatusIcon className="h-4 w-4" />
      </button>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span
            className={cn(
              "text-sm font-medium",
              isCompleted && "line-through text-muted-foreground"
            )}
          >
            {todo.title}
          </span>
          {isInProgress && (
            <span className="flex items-center gap-1 rounded-md bg-blue-500/20 px-1.5 py-0.5 text-[9px] font-medium text-blue-400">
              <Loader2 className="h-2.5 w-2.5 animate-spin" />
              In Arbeit
            </span>
          )}
          <span
            className={cn(
              "rounded-md px-1.5 py-0.5 text-[9px] font-medium border",
              priorityColors[todo.priority]
            )}
          >
            {priorityLabels[todo.priority]}
          </span>
          {todo.project && (
            <span className="rounded-md bg-violet-500/10 px-1.5 py-0.5 text-[9px] font-medium text-violet-400 border border-violet-500/20">
              {todo.project}
            </span>
          )}
        </div>
        {todo.description && (
          <p className="mt-0.5 text-xs text-muted-foreground/60 line-clamp-2">
            {todo.description}
          </p>
        )}
      </div>

      {/* Delete */}
      <button
        onClick={() => onDelete(todo.id)}
        className="mt-0.5 shrink-0 text-muted-foreground/20 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-all"
      >
        <Trash2 className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
