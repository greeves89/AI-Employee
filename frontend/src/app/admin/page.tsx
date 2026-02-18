"use client";

import { useCallback, useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  Users,
  Cpu,
  Container,
  Shield,
  ShieldCheck,
  ShieldX,
  Trash2,
  Loader2,
  UserCog,
  ToggleLeft,
  ToggleRight,
  Box,
  Plus,
  X,
  Eye,
  EyeOff,
  MessageSquare,
  ExternalLink,
  Github,
  Bug,
  Lightbulb,
  TrendingUp,
  ChevronDown,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Header } from "@/components/layout/header";
import { useAuthStore } from "@/lib/auth";
import { useRouter } from "next/navigation";
import * as api from "@/lib/api";
import type { AdminUser, Agent, Feedback, FeedbackStatus } from "@/lib/types";

type Tab = "users" | "agents" | "feedback";

const stateColors: Record<string, string> = {
  running: "bg-emerald-500",
  idle: "bg-blue-500",
  working: "bg-amber-500",
  stopped: "bg-zinc-500",
  error: "bg-red-500",
  created: "bg-zinc-400",
};

export default function AdminPage() {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const [tab, setTab] = useState<Tab>("users");
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [showAddUser, setShowAddUser] = useState(false);
  const [addUserForm, setAddUserForm] = useState({ name: "", email: "", password: "", role: "member" });
  const [addUserLoading, setAddUserLoading] = useState(false);
  const [addUserError, setAddUserError] = useState<string | null>(null);
  const [showPassword, setShowPassword] = useState(false);
  // Feedback
  const [feedbackItems, setFeedbackItems] = useState<Feedback[]>([]);
  const [feedbackLoading, setFeedbackLoading] = useState(false);

  // Redirect non-admins
  useEffect(() => {
    if (user && user.role !== "admin") {
      router.replace("/dashboard");
    }
  }, [user, router]);

  const fetchUsers = useCallback(async () => {
    try {
      const { users: u } = await api.getUsers();
      setUsers(u);
    } catch {
      // ignore
    }
  }, []);

  const fetchAgents = useCallback(async () => {
    try {
      const { agents: a } = await api.getAgents();
      setAgents(a);
    } catch {
      // ignore
    }
  }, []);

  const fetchFeedback = useCallback(async () => {
    try {
      const data = await api.getFeedback();
      setFeedbackItems(data.feedback);
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    Promise.all([fetchUsers(), fetchAgents()]).finally(() => setLoading(false));
  }, [fetchUsers, fetchAgents]);

  useEffect(() => {
    if (tab === "feedback" && feedbackItems.length === 0) {
      setFeedbackLoading(true);
      fetchFeedback().finally(() => setFeedbackLoading(false));
    }
  }, [tab, fetchFeedback, feedbackItems.length]);

  const ROLE_CYCLE = ["viewer", "member", "manager", "admin"] as const;

  const handleCycleRole = async (u: AdminUser) => {
    if (u.id === user?.id) return;
    const idx = ROLE_CYCLE.indexOf(u.role);
    const newRole = ROLE_CYCLE[(idx + 1) % ROLE_CYCLE.length];
    setActionLoading(u.id);
    try {
      await api.updateUser(u.id, { role: newRole });
      await fetchUsers();
    } catch (e) {
      alert(e instanceof Error ? e.message : "Failed to update user");
    } finally {
      setActionLoading(null);
    }
  };

  const handleToggleActive = async (u: AdminUser) => {
    if (u.id === user?.id) return;
    setActionLoading(u.id);
    try {
      await api.updateUser(u.id, { is_active: !u.is_active });
      await fetchUsers();
    } catch (e) {
      alert(e instanceof Error ? e.message : "Failed to update user");
    } finally {
      setActionLoading(null);
    }
  };

  const handleDeleteUser = async (u: AdminUser) => {
    if (u.id === user?.id) return;
    if (!confirm(`Delete user "${u.name}" (${u.email})? This cannot be undone.`)) return;
    setActionLoading(u.id);
    try {
      await api.deleteUser(u.id);
      await fetchUsers();
    } catch (e) {
      alert(e instanceof Error ? e.message : "Failed to delete user");
    } finally {
      setActionLoading(null);
    }
  };

  const handleStopAgent = async (id: string) => {
    setActionLoading(id);
    try {
      await api.stopAgent(id);
      await fetchAgents();
    } finally {
      setActionLoading(null);
    }
  };

  const handleStartAgent = async (id: string) => {
    setActionLoading(id);
    try {
      await api.startAgent(id);
      await fetchAgents();
    } finally {
      setActionLoading(null);
    }
  };

  const handleRemoveAgent = async (id: string) => {
    if (!confirm("Remove this agent? This will stop and remove the container.")) return;
    setActionLoading(id);
    try {
      await api.removeAgent(id);
      await fetchAgents();
    } finally {
      setActionLoading(null);
    }
  };

  const handleCreateUser = async () => {
    setAddUserError(null);
    if (!addUserForm.name.trim() || !addUserForm.email.trim() || !addUserForm.password) {
      setAddUserError("All fields are required");
      return;
    }
    if (addUserForm.password.length < 8) {
      setAddUserError("Password must be at least 8 characters");
      return;
    }
    setAddUserLoading(true);
    try {
      await api.createUser(addUserForm);
      setShowAddUser(false);
      setAddUserForm({ name: "", email: "", password: "", role: "member" });
      setShowPassword(false);
      await fetchUsers();
    } catch (e) {
      setAddUserError(e instanceof Error ? e.message : "Failed to create user");
    } finally {
      setAddUserLoading(false);
    }
  };

  // Find user name by id
  const getUserName = (userId: string | null) => {
    if (!userId) return "Unowned";
    const found = users.find((u) => u.id === userId);
    return found ? found.name : userId;
  };

  if (user?.role !== "admin") return null;

  const tabs: { id: Tab; label: string; icon: typeof Users; count?: number }[] = [
    { id: "users", label: "Users", icon: Users, count: users.length },
    { id: "agents", label: "All Agents", icon: Cpu, count: agents.length },
    { id: "feedback", label: "Feedback", icon: MessageSquare, count: feedbackItems.filter((f) => f.status === "pending").length || undefined },
  ];

  return (
    <div>
      <Header
        title="Administration"
        subtitle="User management, agent overview, and system settings"
      />

      <motion.div
        className="px-8 py-6"
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        {/* Tabs */}
        <div className="flex gap-1 mb-6 rounded-xl bg-card/50 p-1 w-fit border border-border/50">
          {tabs.map((t) => {
            const Icon = t.icon;
            return (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={cn(
                  "flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all",
                  tab === t.id
                    ? "bg-accent text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                <Icon className="h-4 w-4" />
                {t.label}
                {t.count != null && t.count > 0 && (
                  <span className={cn(
                    "ml-1 px-1.5 py-0.5 rounded text-[10px]",
                    t.id === "feedback" ? "bg-amber-500/20 text-amber-400" : "bg-foreground/10"
                  )}>
                    {t.count}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <>
            {/* Users Tab */}
            {tab === "users" && (
              <div className="space-y-3">
                {/* Add User Button */}
                <div className="flex justify-end mb-2">
                  <button
                    onClick={() => setShowAddUser(true)}
                    className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 hover:bg-primary/90 transition-all duration-200"
                  >
                    <Plus className="h-4 w-4" />
                    Add User
                  </button>
                </div>
                {users.map((u, i) => (
                  <motion.div
                    key={u.id}
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: i * 0.04 }}
                    className={cn(
                      "flex items-center gap-4 p-4 rounded-xl border transition-colors",
                      u.is_active
                        ? "border-border/50 bg-card/50"
                        : "border-red-500/20 bg-red-500/5 opacity-60"
                    )}
                  >
                    {/* Avatar */}
                    <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary text-sm font-bold shrink-0">
                      {u.name
                        .split(" ")
                        .map((n) => n[0])
                        .join("")
                        .slice(0, 2)
                        .toUpperCase()}
                    </div>

                    {/* Info */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <p className="text-sm font-medium truncate">{u.name}</p>
                        {u.id === user?.id && (
                          <span className="px-1.5 py-0.5 rounded text-[9px] font-medium bg-blue-500/10 text-blue-500">
                            You
                          </span>
                        )}
                      </div>
                      <p className="text-xs text-muted-foreground truncate">{u.email}</p>
                    </div>

                    {/* Role badge */}
                    <span
                      className={cn(
                        "shrink-0 px-2.5 py-1 rounded-lg text-[11px] font-semibold flex items-center gap-1",
                        u.role === "admin"   ? "bg-amber-500/10 text-amber-500" :
                        u.role === "manager" ? "bg-purple-500/10 text-purple-400" :
                        u.role === "viewer"  ? "bg-zinc-500/10 text-zinc-400" :
                                               "bg-blue-500/10 text-blue-500"
                      )}
                    >
                      {u.role === "admin" ? <ShieldCheck className="h-3 w-3" /> : <UserCog className="h-3 w-3" />}
                      {u.role.charAt(0).toUpperCase() + u.role.slice(1)}
                    </span>

                    {/* Actions */}
                    {u.id !== user?.id && (
                      <div className="flex items-center gap-1 shrink-0">
                        {actionLoading === u.id ? (
                          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                        ) : (
                          <>
                            <button
                              onClick={() => handleCycleRole(u)}
                              className="p-2 rounded-lg text-xs text-muted-foreground hover:bg-accent transition-colors"
                              title={`Cycle role (current: ${u.role})`}
                            >
                              <Shield className="h-4 w-4" />
                            </button>
                            <button
                              onClick={() => handleToggleActive(u)}
                              className={cn(
                                "p-2 rounded-lg transition-colors",
                                u.is_active
                                  ? "text-emerald-500 hover:bg-emerald-500/10"
                                  : "text-red-400 hover:bg-red-500/10"
                              )}
                              title={u.is_active ? "Deactivate" : "Activate"}
                            >
                              {u.is_active ? (
                                <ToggleRight className="h-4 w-4" />
                              ) : (
                                <ToggleLeft className="h-4 w-4" />
                              )}
                            </button>
                            <button
                              onClick={() => handleDeleteUser(u)}
                              className="p-2 rounded-lg text-muted-foreground hover:text-red-400 hover:bg-red-500/10 transition-colors"
                              title="Delete user"
                            >
                              <Trash2 className="h-4 w-4" />
                            </button>
                          </>
                        )}
                      </div>
                    )}
                  </motion.div>
                ))}

                {users.length === 0 && (
                  <div className="text-center py-12 text-muted-foreground">
                    <Users className="h-8 w-8 mx-auto mb-2 opacity-30" />
                    <p className="text-sm">No users found</p>
                  </div>
                )}
              </div>
            )}

            {/* Agents Tab */}
            {tab === "agents" && (
              <div className="space-y-3">
                {agents.map((agent, i) => (
                  <motion.div
                    key={agent.id}
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: i * 0.04 }}
                    className="flex items-center gap-4 p-4 rounded-xl border border-border/50 bg-card/50 hover:bg-card/80 transition-colors cursor-pointer"
                    onClick={() => router.push(`/admin/agents/${agent.id}`)}
                  >
                    {/* State indicator */}
                    <div className="relative shrink-0">
                      <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-foreground/[0.06]">
                        <Cpu className="h-5 w-5 text-muted-foreground" />
                      </div>
                      <div
                        className={cn(
                          "absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full border-2 border-card",
                          stateColors[agent.state] || "bg-zinc-500"
                        )}
                      />
                    </div>

                    {/* Agent info */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <p className="text-sm font-medium truncate">{agent.name}</p>
                        <span className="px-1.5 py-0.5 rounded text-[9px] font-medium bg-foreground/5 text-muted-foreground">
                          {agent.state}
                        </span>
                      </div>
                      <div className="flex items-center gap-3 mt-0.5">
                        <p className="text-[11px] text-muted-foreground">{agent.model}</p>
                        {agent.role && (
                          <p className="text-[11px] text-muted-foreground/60 truncate max-w-[200px]">
                            {agent.role}
                          </p>
                        )}
                      </div>
                    </div>

                    {/* Owner */}
                    <div className="shrink-0 text-right">
                      <p className="text-[11px] text-muted-foreground">Owner</p>
                      <p className="text-xs font-medium">{getUserName(agent.user_id)}</p>
                    </div>

                    {/* Container */}
                    <div className="shrink-0 text-right min-w-[100px]">
                      <p className="text-[11px] text-muted-foreground">Container</p>
                      {agent.container_id ? (
                        <p className="text-[11px] font-mono text-foreground/80 flex items-center gap-1 justify-end">
                          <Box className="h-3 w-3" />
                          {agent.container_id.slice(0, 12)}
                        </p>
                      ) : (
                        <p className="text-[11px] text-muted-foreground/40">None</p>
                      )}
                    </div>

                    {/* Metrics */}
                    <div className="shrink-0 flex items-center gap-3 text-[11px]">
                      {agent.cpu_percent !== null && (
                        <span className="text-muted-foreground">
                          CPU {agent.cpu_percent.toFixed(1)}%
                        </span>
                      )}
                      {agent.memory_usage_mb !== null && (
                        <span className="text-muted-foreground">
                          {agent.memory_usage_mb.toFixed(0)} MB
                        </span>
                      )}
                    </div>

                    {/* Actions */}
                    <div className="flex items-center gap-1 shrink-0" onClick={(e) => e.stopPropagation()}>
                      {actionLoading === agent.id ? (
                        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                      ) : (
                        <>
                          {agent.state === "stopped" ? (
                            <button
                              onClick={() => handleStartAgent(agent.id)}
                              className="p-2 rounded-lg text-muted-foreground hover:text-emerald-400 hover:bg-emerald-500/10 transition-colors"
                              title="Start"
                            >
                              <Container className="h-4 w-4" />
                            </button>
                          ) : (
                            <button
                              onClick={() => handleStopAgent(agent.id)}
                              className="p-2 rounded-lg text-muted-foreground hover:text-amber-400 hover:bg-amber-500/10 transition-colors"
                              title="Stop"
                            >
                              <Container className="h-4 w-4" />
                            </button>
                          )}
                          <button
                            onClick={() => handleRemoveAgent(agent.id)}
                            className="p-2 rounded-lg text-muted-foreground hover:text-red-400 hover:bg-red-500/10 transition-colors"
                            title="Remove"
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>
                        </>
                      )}
                    </div>
                  </motion.div>
                ))}

                {agents.length === 0 && (
                  <div className="text-center py-12 text-muted-foreground">
                    <Cpu className="h-8 w-8 mx-auto mb-2 opacity-30" />
                    <p className="text-sm">No agents created yet</p>
                  </div>
                )}
              </div>
            )}

            {/* Feedback Tab */}
            {tab === "feedback" && (
              <FeedbackTab
                items={feedbackItems}
                loading={feedbackLoading}
                onRefresh={async () => {
                  setFeedbackLoading(true);
                  await fetchFeedback();
                  setFeedbackLoading(false);
                }}
                onUpdate={(updated) => {
                  setFeedbackItems((prev) =>
                    prev.map((f) => (f.id === updated.id ? updated : f))
                  );
                }}
                onDelete={(id) => {
                  setFeedbackItems((prev) => prev.filter((f) => f.id !== id));
                }}
              />
            )}
          </>
        )}
      </motion.div>

      {/* Add User Modal */}
      {showAddUser && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            className="w-full max-w-md rounded-2xl border border-border bg-card p-6 shadow-2xl mx-4"
          >
            <div className="flex items-center justify-between mb-5">
              <h3 className="text-lg font-semibold">Add User</h3>
              <button
                onClick={() => { setShowAddUser(false); setAddUserError(null); }}
                className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-muted-foreground mb-1.5">Name</label>
                <input
                  type="text"
                  value={addUserForm.name}
                  onChange={(e) => setAddUserForm((f) => ({ ...f, name: e.target.value }))}
                  placeholder="John Doe"
                  className="w-full rounded-xl border border-border bg-background px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-muted-foreground mb-1.5">Email</label>
                <input
                  type="email"
                  value={addUserForm.email}
                  onChange={(e) => setAddUserForm((f) => ({ ...f, email: e.target.value }))}
                  placeholder="john@example.com"
                  className="w-full rounded-xl border border-border bg-background px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-muted-foreground mb-1.5">Password</label>
                <div className="relative">
                  <input
                    type={showPassword ? "text" : "password"}
                    value={addUserForm.password}
                    onChange={(e) => setAddUserForm((f) => ({ ...f, password: e.target.value }))}
                    placeholder="Min. 8 characters"
                    className="w-full rounded-xl border border-border bg-background px-4 py-2.5 text-sm pr-10 focus:outline-none focus:ring-2 focus:ring-primary/30"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword(!showPassword)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                  >
                    {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
              </div>
              <div>
                <label className="block text-xs font-medium text-muted-foreground mb-1.5">Role</label>
                <div className="grid grid-cols-4 gap-1.5">
                  {(["viewer", "member", "manager", "admin"] as const).map((r) => {
                    const colors: Record<string, string> = {
                      admin: "border-amber-500 bg-amber-500/10 text-amber-500",
                      manager: "border-purple-500 bg-purple-500/10 text-purple-400",
                      member: "border-primary bg-primary/10 text-primary",
                      viewer: "border-zinc-500 bg-zinc-500/10 text-zinc-400",
                    };
                    const active = addUserForm.role === r;
                    return (
                      <button
                        key={r}
                        onClick={() => setAddUserForm((f) => ({ ...f, role: r }))}
                        className={cn(
                          "flex items-center justify-center rounded-xl border px-2 py-2.5 text-xs font-medium transition-all",
                          active
                            ? colors[r]
                            : "border-border text-muted-foreground hover:text-foreground hover:bg-accent/50"
                        )}
                      >
                        {r.charAt(0).toUpperCase() + r.slice(1)}
                      </button>
                    );
                  })}
                </div>
              </div>

              {addUserError && (
                <p className="text-sm text-red-400 bg-red-500/10 px-3 py-2 rounded-lg">{addUserError}</p>
              )}

              <button
                onClick={handleCreateUser}
                disabled={addUserLoading}
                className="w-full inline-flex items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 hover:bg-primary/90 transition-all disabled:opacity-50"
              >
                {addUserLoading ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Plus className="h-4 w-4" />
                )}
                Create User
              </button>
            </div>
          </motion.div>
        </div>
      )}
    </div>
  );
}

// --- Feedback Tab Component ---

const STATUS_OPTIONS: { value: FeedbackStatus; label: string; color: string }[] = [
  { value: "pending", label: "Pending", color: "text-amber-400 bg-amber-500/10 border-amber-500/20" },
  { value: "reviewed", label: "Reviewed", color: "text-blue-400 bg-blue-500/10 border-blue-500/20" },
  { value: "in_progress", label: "In Progress", color: "text-violet-400 bg-violet-500/10 border-violet-500/20" },
  { value: "closed", label: "Closed", color: "text-zinc-400 bg-zinc-500/10 border-zinc-500/20" },
];

const CATEGORY_ICONS: Record<string, typeof Bug> = {
  bug: Bug,
  feature: Lightbulb,
  improvement: TrendingUp,
  general: MessageSquare,
};

const CATEGORY_COLORS: Record<string, string> = {
  bug: "text-red-400",
  feature: "text-amber-400",
  improvement: "text-blue-400",
  general: "text-zinc-400",
};

function FeedbackTab({
  items,
  loading,
  onRefresh,
  onUpdate,
  onDelete,
}: {
  items: Feedback[];
  loading: boolean;
  onRefresh: () => Promise<void>;
  onUpdate: (feedback: Feedback) => void;
  onDelete: (id: number) => void;
}) {
  const [actionLoading, setActionLoading] = useState<number | null>(null);
  const [expandedNotes, setExpandedNotes] = useState<number | null>(null);
  const [noteText, setNoteText] = useState("");

  const handleStatusChange = async (f: Feedback, newStatus: FeedbackStatus) => {
    setActionLoading(f.id);
    try {
      const updated = await api.updateFeedback(f.id, { status: newStatus });
      onUpdate(updated);
    } catch {
      // ignore
    } finally {
      setActionLoading(null);
    }
  };

  const handleSaveNotes = async (f: Feedback) => {
    setActionLoading(f.id);
    try {
      const updated = await api.updateFeedback(f.id, { admin_notes: noteText });
      onUpdate(updated);
      setExpandedNotes(null);
    } catch {
      // ignore
    } finally {
      setActionLoading(null);
    }
  };

  const handleCreateIssue = async (f: Feedback) => {
    setActionLoading(f.id);
    try {
      const result = await api.createGithubIssueFromFeedback(f.id);
      onUpdate(result.feedback);
    } catch (e) {
      alert(e instanceof Error ? e.message : "Failed to create issue");
    } finally {
      setActionLoading(null);
    }
  };

  const handleDelete = async (f: Feedback) => {
    if (!confirm(`Feedback "${f.title}" wirklich loeschen?`)) return;
    setActionLoading(f.id);
    try {
      await api.deleteFeedback(f.id);
      onDelete(f.id);
    } catch {
      // ignore
    } finally {
      setActionLoading(null);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {items.map((f, i) => {
        const CatIcon = CATEGORY_ICONS[f.category] || MessageSquare;
        const catColor = CATEGORY_COLORS[f.category] || "text-zinc-400";
        const statusCfg = STATUS_OPTIONS.find((s) => s.value === f.status) || STATUS_OPTIONS[0];
        const isExpanded = expandedNotes === f.id;

        return (
          <motion.div
            key={f.id}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.03, duration: 0.2 }}
            className="rounded-xl border border-border bg-card/80 backdrop-blur-sm overflow-hidden"
          >
            <div className="p-4">
              <div className="flex items-start gap-3">
                {/* Category icon */}
                <div className={cn("flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-foreground/[0.04]", catColor)}>
                  <CatIcon className="h-4 w-4" />
                </div>

                {/* Content */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <h4 className="text-sm font-medium truncate">{f.title}</h4>
                    <span className={cn(
                      "inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium",
                      statusCfg.color
                    )}>
                      {statusCfg.label}
                    </span>
                    <span className="text-[10px] text-muted-foreground/50 capitalize">{f.category}</span>
                  </div>
                  {f.description && (
                    <p className="text-xs text-muted-foreground mt-1 line-clamp-2">{f.description}</p>
                  )}
                  <div className="flex items-center gap-3 mt-2 text-[10px] text-muted-foreground/50">
                    <span>{f.user_name || f.user_id}</span>
                    <span>{new Date(f.created_at).toLocaleString("de-DE")}</span>
                    {f.github_issue_url && (
                      <a
                        href={f.github_issue_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-primary hover:underline"
                      >
                        <Github className="h-3 w-3" />
                        Issue
                        <ExternalLink className="h-2.5 w-2.5" />
                      </a>
                    )}
                  </div>
                  {f.admin_notes && !isExpanded && (
                    <p className="text-[11px] text-muted-foreground/70 mt-1.5 italic">
                      Admin: {f.admin_notes}
                    </p>
                  )}
                </div>

                {/* Actions */}
                <div className="flex items-center gap-1.5 shrink-0">
                  {/* Status dropdown */}
                  <div className="relative">
                    <select
                      value={f.status}
                      onChange={(e) => handleStatusChange(f, e.target.value as FeedbackStatus)}
                      disabled={actionLoading === f.id}
                      className="appearance-none rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] pl-2.5 pr-6 py-1.5 text-[11px] font-medium outline-none cursor-pointer hover:bg-foreground/[0.06] transition-colors"
                    >
                      {STATUS_OPTIONS.map((s) => (
                        <option key={s.value} value={s.value}>{s.label}</option>
                      ))}
                    </select>
                    <ChevronDown className="absolute right-1.5 top-1/2 -translate-y-1/2 h-3 w-3 text-muted-foreground pointer-events-none" />
                  </div>

                  {/* Notes button */}
                  <button
                    onClick={() => {
                      if (isExpanded) {
                        setExpandedNotes(null);
                      } else {
                        setNoteText(f.admin_notes || "");
                        setExpandedNotes(f.id);
                      }
                    }}
                    className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] transition-colors"
                    title="Admin-Notizen"
                  >
                    <MessageSquare className="h-3.5 w-3.5" />
                  </button>

                  {/* GitHub Issue button */}
                  {!f.github_issue_url && (
                    <button
                      onClick={() => handleCreateIssue(f)}
                      disabled={actionLoading === f.id}
                      className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] transition-colors disabled:opacity-50"
                      title="GitHub Issue erstellen"
                    >
                      {actionLoading === f.id ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Github className="h-3.5 w-3.5" />
                      )}
                    </button>
                  )}

                  {/* Delete */}
                  <button
                    onClick={() => handleDelete(f)}
                    className="p-1.5 rounded-lg text-muted-foreground hover:text-red-400 hover:bg-red-500/10 transition-colors"
                    title="Loeschen"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            </div>

            {/* Expanded admin notes */}
            {isExpanded && (
              <div className="px-4 pb-4 pt-0 border-t border-foreground/[0.04]">
                <div className="pt-3 space-y-2">
                  <textarea
                    value={noteText}
                    onChange={(e) => setNoteText(e.target.value)}
                    placeholder="Admin-Notizen..."
                    rows={2}
                    className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-2 text-xs outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all resize-none"
                  />
                  <div className="flex gap-2 justify-end">
                    <button
                      onClick={() => setExpandedNotes(null)}
                      className="px-3 py-1.5 rounded-lg text-xs text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] transition-colors"
                    >
                      Abbrechen
                    </button>
                    <button
                      onClick={() => handleSaveNotes(f)}
                      disabled={actionLoading === f.id}
                      className="px-3 py-1.5 rounded-lg text-xs bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
                    >
                      Speichern
                    </button>
                  </div>
                </div>
              </div>
            )}
          </motion.div>
        );
      })}

      {items.length === 0 && (
        <div className="text-center py-12 text-muted-foreground">
          <MessageSquare className="h-8 w-8 mx-auto mb-2 opacity-30" />
          <p className="text-sm">Noch kein Feedback erhalten</p>
          <p className="text-xs text-muted-foreground/50 mt-1">Feedback wird hier angezeigt, sobald User welches senden.</p>
        </div>
      )}
    </div>
  );
}
