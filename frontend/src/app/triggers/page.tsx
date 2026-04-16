"use client";

import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Zap, Plus, Trash2, X, Activity, Clock, Play, Pause,
  TestTube2, CheckCircle2, XCircle, Loader2, ChevronDown,
} from "lucide-react";
import { Header } from "@/components/layout/header";
import { useAgents } from "@/hooks/use-agents";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";

const containerVariants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { staggerChildren: 0.06 } },
};

const itemVariants = {
  hidden: { opacity: 0, y: 12 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.35, ease: [0.25, 0.46, 0.45, 0.94] as const } },
};

const SOURCE_PRESETS = ["github", "stripe", "slack", "linear", "custom"];
const EVENT_PRESETS: Record<string, string[]> = {
  github: ["push", "pull_request", "issues", "release", "workflow_run"],
  stripe: ["payment_intent.succeeded", "invoice.paid", "customer.created"],
  slack: ["message", "reaction_added", "app_mention"],
  linear: ["Issue", "Comment", "Project"],
  custom: [],
};

export default function TriggersPage() {
  const [triggers, setTriggers] = useState<api.EventTrigger[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);
  const { agents } = useAgents();

  // Form state
  const [name, setName] = useState("");
  const [agentId, setAgentId] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [eventTypeFilter, setEventTypeFilter] = useState("");
  const [promptTemplate, setPromptTemplate] = useState("");
  const [priority, setPriority] = useState(5);
  const [conditionsJson, setConditionsJson] = useState("");

  // Test state
  const [testingId, setTestingId] = useState<number | null>(null);
  const [testPayload, setTestPayload] = useState("{}");
  const [testResult, setTestResult] = useState<{ would_fire: boolean; interpolated_prompt: string } | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await api.getEventTriggers();
      setTriggers(data.triggers);
    } catch (e) {
      console.error("Failed to load triggers:", e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 15000);
    return () => clearInterval(interval);
  }, [refresh]);

  const handleCreate = async () => {
    if (!name.trim() || !agentId || !promptTemplate.trim()) return;
    setCreating(true);
    try {
      let conditions = null;
      if (conditionsJson.trim()) {
        try {
          conditions = JSON.parse(conditionsJson);
        } catch {
          alert("Payload conditions must be valid JSON");
          setCreating(false);
          return;
        }
      }
      await api.createEventTrigger({
        name: name.trim(),
        agent_id: agentId,
        source_filter: sourceFilter || null,
        event_type_filter: eventTypeFilter || null,
        payload_conditions: conditions,
        prompt_template: promptTemplate.trim(),
        priority,
      });
      setName("");
      setAgentId("");
      setSourceFilter("");
      setEventTypeFilter("");
      setPromptTemplate("");
      setPriority(5);
      setConditionsJson("");
      setShowCreate(false);
      await refresh();
    } catch (e) {
      console.error("Failed to create trigger:", e);
    } finally {
      setCreating(false);
    }
  };

  const handleToggle = async (trigger: api.EventTrigger) => {
    try {
      await api.toggleEventTrigger(trigger.id);
      await refresh();
    } catch (e) {
      console.error("Failed to toggle trigger:", e);
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await api.deleteEventTrigger(id);
      await refresh();
    } catch (e) {
      console.error("Failed to delete trigger:", e);
    }
  };

  const handleTest = async (triggerId: number) => {
    try {
      const payload = JSON.parse(testPayload);
      const result = await api.testEventTrigger(triggerId, payload);
      setTestResult(result);
    } catch (e) {
      console.error("Test failed:", e);
    }
  };

  const agentName = (id: string) => agents.find((a) => a.id === id)?.name || id;

  const eventPresets = EVENT_PRESETS[sourceFilter] || [];

  return (
    <>
      <Header title="Event Triggers" subtitle="Webhook-to-task routing rules" />
      <div className="space-y-6">
        {/* Header row */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-amber-500/10">
              <Zap className="h-5 w-5 text-amber-400" />
            </div>
            <div>
              <p className="text-sm font-medium">{triggers.length} Trigger{triggers.length !== 1 ? "s" : ""}</p>
              <p className="text-xs text-muted-foreground">
                {triggers.filter((t) => t.enabled).length} aktiv
              </p>
            </div>
          </div>
          <button
            onClick={() => setShowCreate(!showCreate)}
            className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 hover:opacity-90 transition-opacity"
          >
            {showCreate ? <X className="h-4 w-4" /> : <Plus className="h-4 w-4" />}
            {showCreate ? "Abbrechen" : "Neuer Trigger"}
          </button>
        </div>

        {/* Create form */}
        <AnimatePresence>
          {showCreate && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              className="overflow-hidden"
            >
              <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5 space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="text-[11px] font-medium text-muted-foreground/70">Name</label>
                    <input
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      placeholder="z.B. PR Review Trigger"
                      className="mt-1 w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm"
                    />
                  </div>
                  <div>
                    <label className="text-[11px] font-medium text-muted-foreground/70">Agent</label>
                    <select
                      value={agentId}
                      onChange={(e) => setAgentId(e.target.value)}
                      className="mt-1 w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm"
                    >
                      <option value="">Agent auswählen...</option>
                      {agents.map((a) => (
                        <option key={a.id} value={a.id}>{a.name} ({a.role || "general"})</option>
                      ))}
                    </select>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="text-[11px] font-medium text-muted-foreground/70">Source Filter</label>
                    <div className="mt-1 flex gap-2">
                      <select
                        value={sourceFilter}
                        onChange={(e) => { setSourceFilter(e.target.value); setEventTypeFilter(""); }}
                        className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm"
                      >
                        <option value="">Alle Sources</option>
                        {SOURCE_PRESETS.map((s) => (
                          <option key={s} value={s}>{s}</option>
                        ))}
                      </select>
                    </div>
                  </div>
                  <div>
                    <label className="text-[11px] font-medium text-muted-foreground/70">Event Type Filter</label>
                    <div className="mt-1">
                      {eventPresets.length > 0 ? (
                        <select
                          value={eventTypeFilter}
                          onChange={(e) => setEventTypeFilter(e.target.value)}
                          className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm"
                        >
                          <option value="">Alle Events</option>
                          {eventPresets.map((e) => (
                            <option key={e} value={e}>{e}</option>
                          ))}
                        </select>
                      ) : (
                        <input
                          value={eventTypeFilter}
                          onChange={(e) => setEventTypeFilter(e.target.value)}
                          placeholder="z.B. pull_request"
                          className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm"
                        />
                      )}
                    </div>
                  </div>
                </div>

                <div>
                  <label className="text-[11px] font-medium text-muted-foreground/70">
                    Payload Conditions (JSON, optional)
                  </label>
                  <input
                    value={conditionsJson}
                    onChange={(e) => setConditionsJson(e.target.value)}
                    placeholder='z.B. {"action": "opened", "pull_request.draft": false}'
                    className="mt-1 w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm font-mono"
                  />
                  <p className="mt-1 text-[10px] text-muted-foreground/50">
                    Operatoren: {`{"field": {"$in": [...]}}`}, {`{"$ne": "value"}`}, {`{"$contains": "x"}`}, {`{"$exists": true}`}
                  </p>
                </div>

                <div>
                  <label className="text-[11px] font-medium text-muted-foreground/70">
                    Prompt Template
                  </label>
                  <textarea
                    value={promptTemplate}
                    onChange={(e) => setPromptTemplate(e.target.value)}
                    rows={3}
                    placeholder={"Review PR: {{payload.pull_request.title}} by {{payload.pull_request.user.login}}\n\nPlease review the changes and provide feedback."}
                    className="mt-1 w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm font-mono"
                  />
                  <p className="mt-1 text-[10px] text-muted-foreground/50">
                    Nutze {"{{payload.field}}"} um Werte aus dem Webhook-Payload einzusetzen.
                  </p>
                </div>

                <div className="flex items-center gap-4">
                  <div>
                    <label className="text-[11px] font-medium text-muted-foreground/70">Priority</label>
                    <div className="mt-1 flex gap-1">
                      {[1, 3, 5, 7, 10].map((p) => (
                        <button
                          key={p}
                          onClick={() => setPriority(p)}
                          className={cn(
                            "rounded-lg px-3 py-1.5 text-xs font-medium transition-colors",
                            priority === p
                              ? "bg-primary text-primary-foreground"
                              : "bg-foreground/[0.04] text-muted-foreground hover:bg-foreground/[0.08]"
                          )}
                        >
                          {p}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="flex-1" />
                  <button
                    onClick={handleCreate}
                    disabled={creating || !name.trim() || !agentId || !promptTemplate.trim()}
                    className="flex items-center gap-2 rounded-xl bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 hover:opacity-90 transition-opacity disabled:opacity-40"
                  >
                    {creating && <Loader2 className="h-4 w-4 animate-spin" />}
                    Trigger erstellen
                  </button>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Trigger list */}
        {loading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : triggers.length === 0 ? (
          <div className="text-center py-20 text-muted-foreground">
            <Zap className="h-10 w-10 mx-auto mb-3 opacity-30" />
            <p className="text-sm">Keine Triggers definiert</p>
            <p className="text-xs mt-1">Erstelle einen Trigger um Webhooks automatisch an Agents zu routen.</p>
          </div>
        ) : (
          <motion.div variants={containerVariants} initial="hidden" animate="visible" className="space-y-3">
            {triggers.map((trigger) => (
              <motion.div
                key={trigger.id}
                variants={itemVariants}
                className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5"
              >
                <div className="flex items-start justify-between">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <h3 className="text-sm font-medium truncate">{trigger.name}</h3>
                      <span className={cn(
                        "inline-flex items-center rounded-full border px-2.5 py-0.5 text-[11px] font-medium",
                        trigger.enabled
                          ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                          : "bg-zinc-500/10 text-zinc-400 border-zinc-500/20"
                      )}>
                        {trigger.enabled ? "Aktiv" : "Pausiert"}
                      </span>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-2">
                      <span className="inline-flex items-center gap-1 rounded-md bg-foreground/[0.04] px-2 py-1 text-[11px] text-muted-foreground">
                        <Zap className="h-3 w-3" />
                        {agentName(trigger.agent_id)}
                      </span>
                      {trigger.source_filter && (
                        <span className="inline-flex items-center gap-1 rounded-md bg-blue-500/10 px-2 py-1 text-[11px] text-blue-400">
                          {trigger.source_filter}
                        </span>
                      )}
                      {trigger.event_type_filter && (
                        <span className="inline-flex items-center gap-1 rounded-md bg-purple-500/10 px-2 py-1 text-[11px] text-purple-400">
                          {trigger.event_type_filter}
                        </span>
                      )}
                      {trigger.payload_conditions && (
                        <span className="inline-flex items-center gap-1 rounded-md bg-amber-500/10 px-2 py-1 text-[11px] text-amber-400">
                          {Object.keys(trigger.payload_conditions).length} conditions
                        </span>
                      )}
                    </div>
                    <p className="mt-2 text-xs text-muted-foreground/60 font-mono truncate">
                      {trigger.prompt_template.substring(0, 120)}{trigger.prompt_template.length > 120 ? "..." : ""}
                    </p>
                  </div>

                  <div className="flex items-center gap-1 ml-4">
                    {/* Stats */}
                    <div className="text-right mr-3">
                      <p className="text-xs text-muted-foreground">
                        <Activity className="h-3 w-3 inline mr-1" />
                        {trigger.fire_count}x
                      </p>
                      {trigger.last_fired_at && (
                        <p className="text-[10px] text-muted-foreground/50">
                          {new Date(trigger.last_fired_at).toLocaleDateString()}
                        </p>
                      )}
                    </div>

                    {/* Test button */}
                    <button
                      onClick={() => setTestingId(testingId === trigger.id ? null : trigger.id)}
                      className="rounded-lg p-2 text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-colors"
                      title="Trigger testen"
                    >
                      <TestTube2 className="h-4 w-4" />
                    </button>

                    {/* Toggle */}
                    <button
                      onClick={() => handleToggle(trigger)}
                      className={cn(
                        "relative inline-flex h-6 w-11 items-center rounded-full transition-colors",
                        trigger.enabled ? "bg-emerald-500" : "bg-zinc-600"
                      )}
                    >
                      <span
                        className={cn(
                          "inline-block h-4 w-4 transform rounded-full bg-white transition-transform",
                          trigger.enabled ? "translate-x-6" : "translate-x-1"
                        )}
                      />
                    </button>

                    {/* Delete */}
                    <button
                      onClick={() => handleDelete(trigger.id)}
                      className="rounded-lg p-2 text-muted-foreground hover:text-red-400 hover:bg-red-500/10 transition-colors"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                </div>

                {/* Test panel */}
                <AnimatePresence>
                  {testingId === trigger.id && (
                    <motion.div
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: "auto" }}
                      exit={{ opacity: 0, height: 0 }}
                      className="overflow-hidden"
                    >
                      <div className="mt-4 pt-4 border-t border-foreground/[0.06] space-y-3">
                        <label className="text-[11px] font-medium text-muted-foreground/70">
                          Test-Payload (JSON)
                        </label>
                        <textarea
                          value={testPayload}
                          onChange={(e) => setTestPayload(e.target.value)}
                          rows={3}
                          className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm font-mono"
                          placeholder='{"action": "opened", "pull_request": {"title": "Test PR"}}'
                        />
                        <button
                          onClick={() => handleTest(trigger.id)}
                          className="flex items-center gap-2 rounded-xl px-4 py-2 text-sm text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04]"
                        >
                          <Play className="h-4 w-4" /> Test ausführen
                        </button>
                        {testResult && (
                          <div className={cn(
                            "rounded-lg p-3 text-sm",
                            testResult.would_fire
                              ? "bg-emerald-500/10 border border-emerald-500/20"
                              : "bg-red-500/10 border border-red-500/20"
                          )}>
                            <div className="flex items-center gap-2 mb-2">
                              {testResult.would_fire
                                ? <><CheckCircle2 className="h-4 w-4 text-emerald-400" /> <span className="text-emerald-400 font-medium">Trigger würde feuern</span></>
                                : <><XCircle className="h-4 w-4 text-red-400" /> <span className="text-red-400 font-medium">Trigger würde NICHT feuern</span></>
                              }
                            </div>
                            {testResult.would_fire && (
                              <pre className="text-xs text-muted-foreground font-mono whitespace-pre-wrap">
                                {testResult.interpolated_prompt}
                              </pre>
                            )}
                          </div>
                        )}
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </motion.div>
            ))}
          </motion.div>
        )}
      </div>
    </>
  );
}
