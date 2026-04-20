"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { motion } from "framer-motion";
import {
  Plus,
  Trash2,
  Loader2,
  Users,
  Play,
  Square,
  MessageCircle,
  Clock,
} from "lucide-react";
import { Header } from "@/components/layout/header";
import * as api from "@/lib/api";
import type { MeetingRoom, Agent } from "@/lib/types";
import { cn } from "@/lib/utils";

const STATE_COLORS: Record<string, string> = {
  idle: "bg-gray-500/20 text-gray-400",
  running: "bg-emerald-500/20 text-emerald-400",
  paused: "bg-amber-500/20 text-amber-400",
  completed: "bg-blue-500/20 text-blue-400",
};

export default function MeetingRoomsPage() {
  const router = useRouter();
  const [rooms, setRooms] = useState<MeetingRoom[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  // Create form state
  const [name, setName] = useState("");
  const [topic, setTopic] = useState("");
  const [selectedAgents, setSelectedAgents] = useState<string[]>([]);
  const [maxRounds, setMaxRounds] = useState(10);
  const [creating, setCreating] = useState(false);

  const fetchRooms = useCallback(async () => {
    try {
      const data = await api.getMeetingRooms();
      setRooms(data.rooms);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchRooms();
    api.getAgents().then((d) => setAgents(d.agents)).catch(() => {});
    const interval = setInterval(fetchRooms, 5000);
    return () => clearInterval(interval);
  }, [fetchRooms]);

  const runningAgents = agents.filter((a) =>
    ["running", "idle", "working"].includes(a.state),
  );

  const handleCreate = async () => {
    if (!name.trim() || selectedAgents.length < 2) return;
    setCreating(true);
    try {
      const room = await api.createMeetingRoom({
        name: name.trim(),
        topic: topic.trim(),
        agent_ids: selectedAgents,
        max_rounds: maxRounds,
      });
      setShowCreate(false);
      setName("");
      setTopic("");
      setSelectedAgents([]);
      setMaxRounds(10);
      router.push(`/meeting-rooms/${room.id}`);
    } catch (e) {
      alert(`Error: ${e}`);
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this meeting room?")) return;
    setActionLoading(id);
    try {
      await api.deleteMeetingRoom(id);
      await fetchRooms();
    } finally {
      setActionLoading(null);
    }
  };

  const handleStart = async (id: string) => {
    setActionLoading(id);
    try {
      await api.startMeetingRoom(id);
      await fetchRooms();
    } finally {
      setActionLoading(null);
    }
  };

  const handleStop = async (id: string) => {
    setActionLoading(id);
    try {
      await api.stopMeetingRoom(id);
      await fetchRooms();
    } finally {
      setActionLoading(null);
    }
  };

  const toggleAgent = (agentId: string) => {
    setSelectedAgents((prev) =>
      prev.includes(agentId)
        ? prev.filter((id) => id !== agentId)
        : prev.length < 6
          ? [...prev, agentId]
          : prev,
    );
  };

  return (
    <div>
      <Header
        title="Meeting Rooms"
        subtitle="Create group discussions between your agents"
      />

      <div className="p-6">
        {/* Actions */}
        <div className="flex items-center justify-between mb-6">
          <div className="text-sm text-muted-foreground">
            {rooms.length} room{rooms.length !== 1 ? "s" : ""}
            {rooms.filter((r) => r.state === "running").length > 0 && (
              <span className="ml-2 text-emerald-400">
                ({rooms.filter((r) => r.state === "running").length} active)
              </span>
            )}
          </div>
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
          >
            <Plus className="h-4 w-4" />
            New Room
          </button>
        </div>

        {/* Create Modal */}
        {showCreate && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              className="w-full max-w-lg rounded-2xl border border-border bg-card p-6 shadow-xl"
            >
              <h2 className="text-lg font-semibold mb-4">
                Create Meeting Room
              </h2>

              <div className="space-y-4">
                <div>
                  <label className="text-sm font-medium text-muted-foreground">
                    Name
                  </label>
                  <input
                    type="text"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="e.g. Architecture Review"
                    className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50"
                  />
                </div>

                <div>
                  <label className="text-sm font-medium text-muted-foreground">
                    Topic
                  </label>
                  <input
                    type="text"
                    value={topic}
                    onChange={(e) => setTopic(e.target.value)}
                    placeholder="What should the agents discuss?"
                    className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50"
                  />
                </div>

                <div>
                  <label className="text-sm font-medium text-muted-foreground">
                    Agents (2-6)
                  </label>
                  <div className="mt-2 grid grid-cols-2 gap-2 max-h-48 overflow-y-auto">
                    {runningAgents.length === 0 ? (
                      <p className="col-span-2 text-sm text-muted-foreground py-4 text-center">
                        No running agents. Start at least 2 agents first.
                      </p>
                    ) : (
                      runningAgents.map((agent) => (
                        <button
                          key={agent.id}
                          onClick={() => toggleAgent(agent.id)}
                          className={cn(
                            "flex items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-all",
                            selectedAgents.includes(agent.id)
                              ? "border-primary bg-primary/10 text-primary"
                              : "border-border hover:border-primary/50",
                          )}
                        >
                          <Users className="h-3.5 w-3.5" />
                          <span className="truncate">{agent.name}</span>
                        </button>
                      ))
                    )}
                  </div>
                  {selectedAgents.length > 0 && (
                    <p className="mt-1 text-xs text-muted-foreground">
                      {selectedAgents.length} selected
                    </p>
                  )}
                </div>

                <div>
                  <label className="text-sm font-medium text-muted-foreground">
                    Max Rounds
                  </label>
                  <input
                    type="number"
                    value={maxRounds}
                    onChange={(e) =>
                      setMaxRounds(Math.max(1, parseInt(e.target.value) || 1))
                    }
                    min={1}
                    max={100}
                    className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50"
                  />
                  <p className="mt-1 text-xs text-muted-foreground">
                    Each round = every agent speaks once
                  </p>
                </div>
              </div>

              <div className="flex justify-end gap-3 mt-6">
                <button
                  onClick={() => setShowCreate(false)}
                  className="rounded-lg px-4 py-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={handleCreate}
                  disabled={
                    creating || !name.trim() || selectedAgents.length < 2
                  }
                  className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
                >
                  {creating && <Loader2 className="h-4 w-4 animate-spin" />}
                  Create
                </button>
              </div>
            </motion.div>
          </div>
        )}

        {/* Room List */}
        {loading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : rooms.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <Users className="h-12 w-12 text-muted-foreground/30 mb-4" />
            <h3 className="text-lg font-medium mb-1">No meeting rooms yet</h3>
            <p className="text-sm text-muted-foreground">
              Create a room to start a group discussion between agents.
            </p>
          </div>
        ) : (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {rooms.map((room, i) => (
              <motion.div
                key={room.id}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.05 }}
                onClick={() => router.push(`/meeting-rooms/${room.id}`)}
                className="group cursor-pointer rounded-2xl border border-border bg-card/50 p-5 hover:border-primary/30 hover:bg-card/80 transition-all"
              >
                <div className="flex items-start justify-between mb-3">
                  <div className="flex-1 min-w-0">
                    <h3 className="font-semibold truncate">{room.name}</h3>
                    {room.topic && (
                      <p className="text-sm text-muted-foreground mt-0.5 line-clamp-2">
                        {room.topic}
                      </p>
                    )}
                  </div>
                  <span
                    className={cn(
                      "ml-2 shrink-0 rounded-full px-2.5 py-0.5 text-xs font-medium",
                      STATE_COLORS[room.state] || STATE_COLORS.idle,
                    )}
                  >
                    {room.state}
                  </span>
                </div>

                <div className="flex items-center gap-4 text-xs text-muted-foreground mb-4">
                  <span className="flex items-center gap-1">
                    <Users className="h-3.5 w-3.5" />
                    {room.agent_ids.length} agents
                  </span>
                  <span className="flex items-center gap-1">
                    <MessageCircle className="h-3.5 w-3.5" />
                    {room.message_count || 0} messages
                  </span>
                  <span className="flex items-center gap-1">
                    <Clock className="h-3.5 w-3.5" />
                    {room.rounds_completed}/{room.max_rounds} rounds
                  </span>
                </div>

                <div
                  className="flex items-center gap-2"
                  onClick={(e) => e.stopPropagation()}
                >
                  {room.state === "idle" || room.state === "paused" ? (
                    <button
                      onClick={() => handleStart(room.id)}
                      disabled={actionLoading === room.id}
                      className="flex items-center gap-1.5 rounded-lg bg-emerald-500/10 px-3 py-1.5 text-xs font-medium text-emerald-400 hover:bg-emerald-500/20 disabled:opacity-50 transition-colors"
                    >
                      {actionLoading === room.id ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Play className="h-3.5 w-3.5" />
                      )}
                      Start
                    </button>
                  ) : room.state === "running" ? (
                    <button
                      onClick={() => handleStop(room.id)}
                      disabled={actionLoading === room.id}
                      className="flex items-center gap-1.5 rounded-lg bg-amber-500/10 px-3 py-1.5 text-xs font-medium text-amber-400 hover:bg-amber-500/20 disabled:opacity-50 transition-colors"
                    >
                      {actionLoading === room.id ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Square className="h-3.5 w-3.5" />
                      )}
                      Stop
                    </button>
                  ) : null}
                  <button
                    onClick={() => handleDelete(room.id)}
                    disabled={actionLoading === room.id}
                    className="flex items-center gap-1.5 rounded-lg bg-red-500/10 px-3 py-1.5 text-xs font-medium text-red-400 hover:bg-red-500/20 disabled:opacity-50 transition-colors ml-auto"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </motion.div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
