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
  ChevronDown,
  ChevronRight,
  Bot,
  Gavel,
  FileText,
  Download,
  X,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
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
  const [maxRounds, setMaxRounds] = useState(5);
  const [creating, setCreating] = useState(false);
  // Advanced settings
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [useStages, setUseStages] = useState(true);
  const [useModerator, setUseModerator] = useState(true);
  const [customStages, setCustomStages] = useState([
    { name: "Eröffnung", rounds: 1 },
    { name: "Analyse", rounds: 2 },
    { name: "Synthese", rounds: 1 },
  ]);
  const [topicError, setTopicError] = useState(false);
  const [summaryRoom, setSummaryRoom] = useState<MeetingRoom | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);

  const openSummary = async (room: MeetingRoom) => {
    setSummaryLoading(true);
    setSummaryRoom(room);
    try {
      const full = await api.getMeetingRoom(room.id);
      setSummaryRoom(full);
    } catch {
      // keep basic room data
    } finally {
      setSummaryLoading(false);
    }
  };

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
    if (!topic.trim()) { setTopicError(true); return; }
    setTopicError(false);
    setCreating(true);
    try {
      // Auto-assign focus: first stage = intro, last = synthesis, middle = research
      const stages = useStages ? customStages.map((s, i) => ({
        name: s.name,
        rounds: s.rounds,
        focus: i === 0 ? "intro" : i === customStages.length - 1 ? "synthesis" : "research",
      })) : null;
      const room = await api.createMeetingRoom({
        name: name.trim(),
        topic: topic.trim(),
        agent_ids: selectedAgents,
        max_rounds: stages ? stages.reduce((s, r) => s + r.rounds, 0) : maxRounds,
        stages_config: stages,
        use_moderator: useModerator,
      });
      setShowCreate(false);
      setName(""); setTopic(""); setSelectedAgents([]);
      setMaxRounds(5); setShowAdvanced(false);
      setCustomStages([{ name: "Eröffnung", rounds: 1 }, { name: "Analyse", rounds: 2 }, { name: "Synthese", rounds: 1 }]);
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

  const handleSummaryPDF = (room: MeetingRoom) => {
    const messages = room.messages || [];
    const summary = messages.find((m) => m.role === "summary");
    const agentMsgs = messages.filter((m) => m.role === "agent" || m.role === "moderator");
    const toMd = (text: string) => text
      .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*(.*?)\*/g, "<em>$1</em>")
      .replace(/^## (.+)$/gm, "<h3>$1</h3>")
      .replace(/^- \[ \] (.+)$/gm, "<li>☐ $1</li>")
      .replace(/^- \[x\] (.+)$/gm, "<li>☑ $1</li>")
      .replace(/^- (.+)$/gm, "<li>$1</li>")
      .replace(/\n/g, "<br/>");
    const html = `<!DOCTYPE html><html><head><meta charset="utf-8"/>
      <title>${room.name}</title>
      <style>body{font-family:sans-serif;max-width:800px;margin:40px auto;color:#111;line-height:1.6}
      h1{font-size:1.4rem;margin-bottom:4px}h2{font-size:1.1rem;margin-top:2rem;border-bottom:1px solid #ddd;padding-bottom:4px}
      h3{font-size:1rem;margin:1rem 0 0.25rem}
      .meta{color:#666;font-size:.85rem;margin-bottom:2rem}
      .summary{background:#eff6ff;border-left:4px solid #3b82f6;padding:16px;border-radius:4px;margin-bottom:2rem}
      .msg{margin-bottom:1rem;padding:12px;border-radius:6px;border:1px solid #e5e7eb}
      .moderator{background:#f5f3ff;border-color:#8b5cf6}
      .agent-name{font-weight:600;font-size:.85rem;margin-bottom:4px}
      li{margin:.2rem 0}@media print{body{margin:20px}}</style></head>
      <body>
      <h1>${room.name}</h1>
      <div class="meta">Thema: ${room.topic || "–"} · ${agentMsgs.length} Beiträge · ${room.rounds_completed}/${room.max_rounds} Runden</div>
      ${summary ? `<div class="summary"><h2>Zusammenfassung</h2>${toMd(summary.content)}</div>` : ""}
      <h2>Gesprächsverlauf</h2>
      ${agentMsgs.map(m => `<div class="msg ${m.role === "moderator" ? "moderator" : ""}">
        <div class="agent-name">${m.role === "moderator" ? "🎙 Moderator" : (m.agent_id || "Agent")}</div>
        <div>${toMd(m.content)}</div></div>`).join("")}
      </body></html>`;
    const w = window.open("", "_blank");
    if (!w) return;
    w.document.write(html);
    w.document.close();
    w.focus();
    setTimeout(() => w.print(), 500);
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
                {/* Name */}
                <div>
                  <label className="text-[11px] font-medium text-muted-foreground/70 uppercase tracking-wider">Name</label>
                  <input
                    type="text"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="z.B. Marketing Strategie Review"
                    className="mt-1 w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50"
                  />
                </div>

                {/* Topic — required */}
                <div>
                  <label className="text-[11px] font-medium text-muted-foreground/70 uppercase tracking-wider flex items-center gap-1.5">
                    Agenda / Thema
                    <span className="text-red-400">*</span>
                  </label>
                  <textarea
                    value={topic}
                    onChange={(e) => { setTopic(e.target.value); if (e.target.value.trim()) setTopicError(false); }}
                    placeholder="Was genau soll besprochen werden? Je konkreter, desto besser. (Pflichtfeld)"
                    rows={2}
                    className={cn(
                      "mt-1 w-full rounded-lg border bg-foreground/[0.02] px-3.5 py-2.5 text-sm resize-none focus:outline-none focus:ring-2",
                      topicError
                        ? "border-red-500/50 focus:ring-red-500/50"
                        : "border-foreground/[0.08] focus:ring-primary/50"
                    )}
                  />
                  {topicError && (
                    <p className="mt-1 text-[11px] text-red-400">Eine Agenda ist Pflicht — ohne Thema kein Meeting.</p>
                  )}
                </div>

                {/* Agents */}
                <div>
                  <label className="text-[11px] font-medium text-muted-foreground/70 uppercase tracking-wider">
                    Agenten <span className="normal-case">(2–6)</span>
                  </label>
                  <div className="mt-2 grid grid-cols-2 gap-2 max-h-40 overflow-y-auto">
                    {runningAgents.length === 0 ? (
                      <p className="col-span-2 text-sm text-muted-foreground py-4 text-center">
                        Keine laufenden Agenten.
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
                              : "border-foreground/[0.08] hover:border-primary/50",
                          )}
                        >
                          <Bot className="h-3.5 w-3.5 shrink-0" />
                          <span className="truncate">{agent.name}</span>
                        </button>
                      ))
                    )}
                  </div>
                  {selectedAgents.length > 0 && (
                    <p className="mt-1 text-xs text-muted-foreground">{selectedAgents.length} ausgewählt</p>
                  )}
                </div>

                {/* Advanced Settings (collapsible) */}
                <div className="rounded-xl border border-foreground/[0.06] overflow-hidden">
                  <button
                    type="button"
                    onClick={() => setShowAdvanced(!showAdvanced)}
                    className="w-full flex items-center justify-between px-4 py-3 text-sm text-muted-foreground hover:text-foreground hover:bg-foreground/[0.02] transition-colors"
                  >
                    <span className="font-medium">Erweiterte Einstellungen</span>
                    {showAdvanced ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                  </button>

                  {showAdvanced && (
                    <div className="border-t border-foreground/[0.06] px-4 py-4 space-y-5 bg-foreground/[0.01]">

                      {/* Moderator toggle */}
                      <div className="flex items-start justify-between gap-4">
                        <div className="flex items-start gap-3">
                          <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10">
                            <Gavel className="h-4 w-4 text-primary" />
                          </div>
                          <div>
                            <p className="text-sm font-medium">Moderator</p>
                            <p className="text-[11px] text-muted-foreground mt-0.5">
                              Ein virtueller Moderator führt das Meeting, stellt gezielte Fragen und leitet Übergänge ein.
                            </p>
                          </div>
                        </div>
                        <button
                          type="button"
                          onClick={() => setUseModerator(!useModerator)}
                          className={cn(
                            "relative shrink-0 h-6 w-11 rounded-full transition-colors duration-200",
                            useModerator ? "bg-primary" : "bg-foreground/20"
                          )}
                        >
                          <span className={cn(
                            "absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform duration-200",
                            useModerator ? "translate-x-5" : "translate-x-0"
                          )} />
                        </button>
                      </div>

                      {/* Stages toggle */}
                      <div className="flex items-start justify-between gap-4">
                        <div className="flex items-start gap-3">
                          <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-emerald-500/10">
                            <ChevronRight className="h-4 w-4 text-emerald-400" />
                          </div>
                          <div>
                            <p className="text-sm font-medium">Meeting Phasen</p>
                            <p className="text-[11px] text-muted-foreground mt-0.5">
                              Strukturiert das Meeting in Eröffnung, Analyse und Synthese statt endlosem Round-Robin.
                            </p>
                          </div>
                        </div>
                        <button
                          type="button"
                          onClick={() => setUseStages(!useStages)}
                          className={cn(
                            "relative shrink-0 h-6 w-11 rounded-full transition-colors duration-200",
                            useStages ? "bg-primary" : "bg-foreground/20"
                          )}
                        >
                          <span className={cn(
                            "absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform duration-200",
                            useStages ? "translate-x-5" : "translate-x-0"
                          )} />
                        </button>
                      </div>

                      {/* Custom editable stages */}
                      {useStages && (
                        <div className="rounded-lg border border-foreground/[0.06] p-3 space-y-2">
                          {customStages.map((stage, i) => (
                            <div key={i} className="flex items-center gap-2">
                              {/* Stage name */}
                              <input
                                type="text"
                                value={stage.name}
                                onChange={(e) => setCustomStages(prev => prev.map((s, j) => j === i ? { ...s, name: e.target.value } : s))}
                                className="flex-1 min-w-0 rounded border border-foreground/[0.08] bg-foreground/[0.02] px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-primary/50"
                              />
                              {/* Rounds */}
                              <div className="flex items-center gap-1 shrink-0">
                                <button type="button"
                                  onClick={() => setCustomStages(prev => prev.map((s, j) => j === i ? { ...s, rounds: Math.max(1, s.rounds - 1) } : s))}
                                  className="h-5 w-5 rounded flex items-center justify-center text-muted-foreground hover:bg-foreground/[0.06] text-sm"
                                >−</button>
                                <span className="w-4 text-center text-xs font-medium">{stage.rounds}</span>
                                <button type="button"
                                  onClick={() => setCustomStages(prev => prev.map((s, j) => j === i ? { ...s, rounds: Math.min(10, s.rounds + 1) } : s))}
                                  className="h-5 w-5 rounded flex items-center justify-center text-muted-foreground hover:bg-foreground/[0.06] text-sm"
                                >+</button>
                              </div>
                              {/* Delete (only if >1 stage) */}
                              {customStages.length > 1 && (
                                <button type="button"
                                  onClick={() => setCustomStages(prev => prev.filter((_, j) => j !== i))}
                                  className="h-5 w-5 rounded flex items-center justify-center text-muted-foreground/50 hover:text-red-400 hover:bg-red-500/10 text-xs"
                                >×</button>
                              )}
                            </div>
                          ))}
                          {/* Add stage */}
                          <button type="button"
                            onClick={() => setCustomStages(prev => [...prev, { name: "Neue Phase", rounds: 1 }])}
                            className="w-full rounded border border-dashed border-foreground/[0.08] py-1 text-[11px] text-muted-foreground/50 hover:text-muted-foreground hover:border-foreground/20 transition-colors"
                          >
                            + Phase hinzufügen
                          </button>
                          <div className="pt-1 border-t border-foreground/[0.06] text-[10px] text-muted-foreground/60">
                            Gesamt: {customStages.reduce((s, r) => s + r.rounds, 0)} Runden
                            × {selectedAgents.length || "n"} Agenten
                            {useModerator && " + Moderator"}
                          </div>
                        </div>
                      )}

                      {/* Plain max rounds (only shown without stages) */}
                      {!useStages && (
                        <div>
                          <label className="text-[11px] font-medium text-muted-foreground/70 uppercase tracking-wider">Max Runden</label>
                          <input
                            type="number"
                            value={maxRounds}
                            onChange={(e) => setMaxRounds(Math.max(1, parseInt(e.target.value) || 1))}
                            min={1} max={50}
                            className="mt-1 w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50"
                          />
                        </div>
                      )}
                    </div>
                  )}
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
                    creating || !name.trim() || !topic.trim() || selectedAgents.length < 2
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
                  {room.state === "completed" && (
                    <button
                      onClick={() => openSummary(room)}
                      className="flex items-center gap-1.5 rounded-lg bg-blue-500/10 border border-blue-500/20 px-3 py-1.5 text-xs font-medium text-blue-400 hover:bg-blue-500/20 transition-colors"
                    >
                      <FileText className="h-3.5 w-3.5" />
                      Zusammenfassung
                    </button>
                  )}
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

      {/* Summary Modal */}
      {summaryRoom && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            className="w-full max-w-2xl max-h-[80vh] flex flex-col rounded-2xl border border-border bg-card shadow-xl"
          >
            {/* Header */}
            <div className="flex items-start justify-between p-5 border-b border-border shrink-0">
              <div className="flex-1 min-w-0 pr-4">
                <h2 className="font-semibold truncate">{summaryRoom.name}</h2>
                {summaryRoom.topic && (
                  <p className="text-xs text-muted-foreground mt-0.5 line-clamp-1">{summaryRoom.topic}</p>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <button
                  onClick={() => handleSummaryPDF(summaryRoom)}
                  className="flex items-center gap-1.5 rounded-lg bg-blue-500/10 border border-blue-500/20 px-3 py-1.5 text-sm font-medium text-blue-400 hover:bg-blue-500/20 transition-colors"
                >
                  <Download className="h-4 w-4" />
                  PDF
                </button>
                <button
                  onClick={() => setSummaryRoom(null)}
                  className="rounded-lg p-1.5 text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] transition-colors"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            </div>

            {/* Content */}
            <div className="flex-1 overflow-y-auto p-5 space-y-4">
              {summaryLoading ? (
                <div className="flex items-center justify-center py-12">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
              ) : (() => {
                const msgs = summaryRoom.messages || [];
                const summary = msgs.find((m) => m.role === "summary");
                const actionItems = msgs.filter((m) => m.role === "agent" && m.content?.includes("☐"));
                return (
                  <>
                    {summary ? (
                      <div className="rounded-xl border border-blue-500/20 bg-blue-500/5 p-4">
                        <div className="flex items-center gap-2 mb-3">
                          <FileText className="h-4 w-4 text-blue-400" />
                          <span className="text-sm font-medium text-blue-300">Meeting-Zusammenfassung</span>
                        </div>
                        <div className="prose prose-sm dark:prose-invert max-w-none text-sm text-foreground/90">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>{summary.content}</ReactMarkdown>
                        </div>
                      </div>
                    ) : (
                      <div className="text-center py-8 text-muted-foreground text-sm">
                        Keine Zusammenfassung vorhanden.
                      </div>
                    )}
                    <div className="text-xs text-muted-foreground pt-2 border-t border-border">
                      {msgs.filter(m => m.role === "agent").length} Beiträge · {summaryRoom.rounds_completed}/{summaryRoom.max_rounds} Runden · {summaryRoom.agent_ids.length} Agenten
                    </div>
                  </>
                );
              })()}
            </div>
          </motion.div>
        </div>
      )}
    </div>
  );
}
