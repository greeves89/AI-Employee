"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Box,
  ChevronDown,
  ChevronRight,
  Circle,
  Container,
  ExternalLink,
  Hammer,
  Loader2,
  Play,
  RefreshCw,
  RotateCcw,
  ScrollText,
  Square,
  Wrench,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  getDockerApps,
  startDockerApp,
  stopDockerApp,
  rebuildDockerApp,
  restartDockerService,
  getDockerAppLogs,
} from "@/lib/api";
import type { DockerApp, DockerAppLog } from "@/lib/types";

interface DockerAppsTabProps {
  agentId: string;
}

const statusConfig = {
  running: {
    label: "Running",
    color: "text-emerald-400",
    bg: "bg-emerald-500/10",
    border: "border-emerald-500/20",
    dot: "bg-emerald-400",
  },
  stopped: {
    label: "Stopped",
    color: "text-zinc-400",
    bg: "bg-zinc-500/10",
    border: "border-zinc-500/20",
    dot: "bg-zinc-500",
  },
  partial: {
    label: "Partial",
    color: "text-amber-400",
    bg: "bg-amber-500/10",
    border: "border-amber-500/20",
    dot: "bg-amber-400",
  },
};

// Phases shown during start
const BUILD_PHASES = [
  "Preparing environment...",
  "Building images...",
  "Pulling dependencies...",
  "Starting containers...",
];

export function DockerAppsTab({ agentId }: DockerAppsTabProps) {
  const [apps, setApps] = useState<DockerApp[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [actionType, setActionType] = useState<"start" | "rebuild" | "stop" | null>(null);
  const [actionPhase, setActionPhase] = useState(0);
  const [actionError, setActionError] = useState<{ path: string; message: string } | null>(null);
  const [expandedApp, setExpandedApp] = useState<string | null>(null);
  const [logs, setLogs] = useState<DockerAppLog[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);
  const [logsApp, setLogsApp] = useState<string | null>(null);
  const [restartingService, setRestartingService] = useState<string | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const phaseTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const logPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchApps = useCallback(async () => {
    try {
      const data = await getDockerApps(agentId);
      setApps(data.apps);
    } catch {
      // ignore
    }
    setLoading(false);
  }, [agentId]);

  useEffect(() => {
    fetchApps();
    const interval = setInterval(fetchApps, 10000);
    return () => clearInterval(interval);
  }, [fetchApps]);

  // Cleanup timers on unmount
  useEffect(() => {
    return () => {
      if (phaseTimerRef.current) clearInterval(phaseTimerRef.current);
      if (logPollRef.current) clearInterval(logPollRef.current);
    };
  }, []);

  const pollLogs = useCallback(async (appPath: string) => {
    try {
      const data = await getDockerAppLogs(agentId, appPath, undefined, 200);
      setLogs(data.logs);
      setTimeout(() => {
        logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
      }, 50);
    } catch {
      // ignore - containers might not have logs yet during build
    }
  }, [agentId]);

  const handleStart = async (app: DockerApp) => {
    setActionLoading(app.path);
    setActionType("start");
    setActionError(null);
    setActionPhase(0);

    // Open logs panel immediately
    setLogsApp(app.path);
    setLogs([]);
    setExpandedApp(app.path);

    // Cycle through build phases for visual feedback
    let phase = 0;
    phaseTimerRef.current = setInterval(() => {
      phase = Math.min(phase + 1, BUILD_PHASES.length - 1);
      setActionPhase(phase);
    }, 4000);

    // Poll logs every 3s during build
    logPollRef.current = setInterval(() => {
      pollLogs(app.path);
    }, 3000);

    try {
      await startDockerApp(agentId, app.path);
      await fetchApps();
      // Final log fetch after success
      await pollLogs(app.path);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setActionError({ path: app.path, message: msg });
    }

    // Cleanup timers
    if (phaseTimerRef.current) { clearInterval(phaseTimerRef.current); phaseTimerRef.current = null; }
    if (logPollRef.current) { clearInterval(logPollRef.current); logPollRef.current = null; }
    setActionLoading(null);
    setActionType(null);
    setActionPhase(0);
  };

  const handleStop = async (app: DockerApp) => {
    setActionLoading(app.path);
    setActionType("stop");
    setActionError(null);
    try {
      await stopDockerApp(agentId, app.path);
      await fetchApps();
      setLogsApp(null);
      setLogs([]);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setActionError({ path: app.path, message: msg });
    }
    setActionLoading(null);
    setActionType(null);
  };

  const handleRebuild = async (app: DockerApp) => {
    setActionLoading(app.path);
    setActionType("rebuild");
    setActionError(null);
    setActionPhase(0);
    setLogsApp(app.path);
    setLogs([]);
    setExpandedApp(app.path);

    let phase = 0;
    phaseTimerRef.current = setInterval(() => {
      phase = Math.min(phase + 1, BUILD_PHASES.length - 1);
      setActionPhase(phase);
    }, 4000);

    logPollRef.current = setInterval(() => {
      pollLogs(app.path);
    }, 3000);

    try {
      await rebuildDockerApp(agentId, app.path);
      await fetchApps();
      await pollLogs(app.path);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setActionError({ path: app.path, message: msg });
    }

    if (phaseTimerRef.current) { clearInterval(phaseTimerRef.current); phaseTimerRef.current = null; }
    if (logPollRef.current) { clearInterval(logPollRef.current); logPollRef.current = null; }
    setActionLoading(null);
    setActionType(null);
    setActionPhase(0);
  };

  const handleRestartService = async (app: DockerApp, serviceName: string) => {
    setRestartingService(serviceName);
    try {
      await restartDockerService(agentId, app.path, serviceName);
      await fetchApps();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setActionError({ path: app.path, message: msg });
    }
    setRestartingService(null);
  };

  const handleViewLogs = async (app: DockerApp) => {
    if (logsApp === app.path) {
      setLogsApp(null);
      setLogs([]);
      return;
    }
    setLogsApp(app.path);
    setLogsLoading(true);
    try {
      const data = await getDockerAppLogs(agentId, app.path, undefined, 200);
      setLogs(data.logs);
      setTimeout(() => {
        logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
      }, 100);
    } catch {
      setLogs([]);
    }
    setLogsLoading(false);
  };

  const refreshLogs = async () => {
    if (!logsApp) return;
    setLogsLoading(true);
    try {
      const data = await getDockerAppLogs(agentId, logsApp, undefined, 200);
      setLogs(data.logs);
      setTimeout(() => {
        logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
      }, 100);
    } catch {
      // ignore
    }
    setLogsLoading(false);
  };

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-muted-foreground/50">
        <Loader2 className="h-6 w-6 animate-spin mb-3" />
        <span className="text-sm">Scanning workspace for Docker apps...</span>
      </div>
    );
  }

  if (apps.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <div className="rounded-xl border border-dashed border-foreground/[0.1] bg-card/30 p-16 text-center max-w-lg">
          <Container className="h-10 w-10 mx-auto mb-3 text-muted-foreground/30" />
          <p className="text-sm font-medium text-muted-foreground/60 mb-1">
            No Docker Apps Found
          </p>
          <p className="text-[11px] text-muted-foreground/40 leading-relaxed">
            When your agent creates a project with a docker-compose.yml file,
            it will appear here. You can then start, stop, and monitor the app
            directly from this panel.
          </p>
          <button
            onClick={() => { setLoading(true); fetchApps(); }}
            className="mt-4 inline-flex items-center gap-2 rounded-lg px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-all"
          >
            <RefreshCw className="h-3 w-3" /> Rescan
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="p-4 space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <Container className="h-4 w-4 text-muted-foreground/60" />
          <span className="text-sm font-medium">
            {apps.length} {apps.length === 1 ? "App" : "Apps"} found
          </span>
        </div>
        <button
          onClick={() => { setLoading(true); fetchApps(); }}
          className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-all"
        >
          <RefreshCw className="h-3 w-3" /> Refresh
        </button>
      </div>

      {/* App List */}
      <AnimatePresence>
        {apps.map((app) => {
          const config = statusConfig[app.status] || statusConfig.stopped;
          const isExpanded = expandedApp === app.path;
          const isActionLoading = actionLoading === app.path;
          const isLogsOpen = logsApp === app.path;
          const isBuilding = isActionLoading && (actionType === "start" || actionType === "rebuild");

          return (
            <motion.div
              key={app.path}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden"
            >
              {/* App Header */}
              <div className="p-4">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-3 flex-1 min-w-0">
                    {/* Expand Toggle */}
                    <button
                      onClick={() => setExpandedApp(isExpanded ? null : app.path)}
                      className="text-muted-foreground/40 hover:text-foreground transition-colors"
                    >
                      {isExpanded ? (
                        <ChevronDown className="h-4 w-4" />
                      ) : (
                        <ChevronRight className="h-4 w-4" />
                      )}
                    </button>

                    {/* Status Dot / Spinner */}
                    {isBuilding ? (
                      <Loader2 className="h-3 w-3 animate-spin text-blue-400 shrink-0" />
                    ) : (
                      <div className={cn("h-2.5 w-2.5 rounded-full shrink-0", config.dot)} />
                    )}

                    {/* App Name + Path */}
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-semibold truncate">
                          {app.name}
                        </span>
                        {isBuilding ? (
                          <span className="inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider bg-blue-500/10 text-blue-400 border-blue-500/20">
                            {actionType === "rebuild" ? "Rebuilding" : "Starting"}
                          </span>
                        ) : (
                          <span
                            className={cn(
                              "inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider",
                              config.bg, config.color, config.border,
                            )}
                          >
                            {config.label}
                          </span>
                        )}
                      </div>
                      <p className="text-[11px] text-muted-foreground/50 font-mono mt-0.5">
                        /workspace/{app.path}/{app.compose_file}
                      </p>
                    </div>
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-2 shrink-0">
                    {/* Logs Button */}
                    {(app.status !== "stopped" || isLogsOpen) && (
                      <button
                        onClick={() => handleViewLogs(app)}
                        className={cn(
                          "inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs transition-all",
                          isLogsOpen
                            ? "bg-foreground/[0.08] text-foreground"
                            : "text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04]"
                        )}
                      >
                        <ScrollText className="h-3.5 w-3.5" />
                        Logs
                      </button>
                    )}

                    {/* Start / Stop / Rebuild */}
                    {app.status === "stopped" && !isBuilding ? (
                      <button
                        onClick={() => handleStart(app)}
                        disabled={isActionLoading}
                        className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-500/10 border border-emerald-500/20 px-3.5 py-1.5 text-xs font-medium text-emerald-400 hover:bg-emerald-500/20 transition-all disabled:opacity-50"
                      >
                        <Play className="h-3.5 w-3.5" />
                        Start
                      </button>
                    ) : isBuilding ? (
                      <span className="inline-flex items-center gap-1.5 rounded-lg bg-blue-500/10 border border-blue-500/20 px-3.5 py-1.5 text-xs font-medium text-blue-400">
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        {actionType === "rebuild" ? "Rebuilding..." : "Building..."}
                      </span>
                    ) : (
                      <>
                        <button
                          onClick={() => handleRebuild(app)}
                          disabled={isActionLoading}
                          className="inline-flex items-center gap-1.5 rounded-lg bg-amber-500/10 border border-amber-500/20 px-3 py-1.5 text-xs font-medium text-amber-400 hover:bg-amber-500/20 transition-all disabled:opacity-50"
                          title="Rebuild images & recreate containers"
                        >
                          {isActionLoading && actionLoading === app.path ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <Wrench className="h-3.5 w-3.5" />
                          )}
                          Rebuild
                        </button>
                        <button
                          onClick={() => handleStop(app)}
                          disabled={isActionLoading}
                          className="inline-flex items-center gap-1.5 rounded-lg bg-red-500/10 border border-red-500/20 px-3.5 py-1.5 text-xs font-medium text-red-400 hover:bg-red-500/20 transition-all disabled:opacity-50"
                        >
                          {isActionLoading ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <Square className="h-3.5 w-3.5" />
                          )}
                          {isActionLoading ? "Stopping..." : "Stop"}
                        </button>
                      </>
                    )}
                  </div>
                </div>

                {/* Build Progress Banner */}
                {isBuilding && (
                  <motion.div
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: "auto" }}
                    className="mt-3 ml-[52px]"
                  >
                    <div className="rounded-lg bg-blue-500/5 border border-blue-500/10 px-3 py-2.5">
                      <div className="flex items-center gap-2 mb-2">
                        <Hammer className="h-3.5 w-3.5 text-blue-400" />
                        <span className="text-xs font-medium text-blue-400">
                          {BUILD_PHASES[actionPhase]}
                        </span>
                      </div>
                      {/* Progress dots */}
                      <div className="flex items-center gap-1.5">
                        {BUILD_PHASES.map((_, i) => (
                          <div
                            key={i}
                            className={cn(
                              "h-1 rounded-full transition-all duration-500",
                              i <= actionPhase
                                ? "bg-blue-400 flex-[2]"
                                : "bg-blue-400/20 flex-1",
                            )}
                          />
                        ))}
                      </div>
                    </div>
                  </motion.div>
                )}

                {/* Services Summary (compact) */}
                {!isExpanded && !isBuilding && app.services.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mt-2.5 ml-[52px]">
                    {app.services.map((svc) => (
                      <span
                        key={svc.name}
                        className="inline-flex items-center gap-1 rounded-md bg-foreground/[0.04] border border-foreground/[0.06] px-2 py-0.5 text-[10px] text-muted-foreground"
                      >
                        <Box className="h-2.5 w-2.5" />
                        {svc.name}
                      </span>
                    ))}
                  </div>
                )}
              </div>

              {/* Expanded: Services Detail */}
              <AnimatePresence>
                {isExpanded && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: "auto", opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.2 }}
                    className="overflow-hidden"
                  >
                    <div className="border-t border-foreground/[0.04] px-4 py-3 space-y-2">
                      <span className="text-[10px] font-medium text-muted-foreground/50 uppercase tracking-wider">
                        Services
                      </span>
                      {app.services.map((svc) => {
                        // Find matching container
                        const container = app.containers?.find(
                          (c) => c.service === svc.name,
                        );
                        const isRunning = container?.status === "running";

                        return (
                          <div
                            key={svc.name}
                            className="flex items-center justify-between rounded-lg bg-foreground/[0.02] border border-foreground/[0.04] px-3 py-2"
                          >
                            <div className="flex items-center gap-2.5">
                              <Circle
                                className={cn(
                                  "h-2 w-2 fill-current",
                                  isRunning ? "text-emerald-400" : "text-zinc-500",
                                )}
                              />
                              <span className="text-xs font-medium">{svc.name}</span>
                              {svc.image && (
                                <span className="text-[10px] text-muted-foreground/40 font-mono">
                                  {svc.image}
                                </span>
                              )}
                              {svc.build && !svc.image && (
                                <span className="text-[10px] text-muted-foreground/40 font-mono">
                                  [build]
                                </span>
                              )}
                            </div>

                            {/* Ports + Restart */}
                            <div className="flex items-center gap-1.5">
                              {container?.ports?.map((p) => (
                                <a
                                  key={`${p.host_port}-${p.container_port}`}
                                  href={`http://localhost:${p.host_port}`}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="inline-flex items-center gap-1 rounded-md bg-primary/10 border border-primary/20 px-2 py-0.5 text-[10px] font-mono text-primary hover:bg-primary/20 transition-colors"
                                >
                                  :{p.host_port}
                                  <ExternalLink className="h-2.5 w-2.5" />
                                </a>
                              ))}
                              {!container?.ports?.length && svc.ports.length > 0 && (
                                svc.ports.map((port) => (
                                  <span
                                    key={String(port)}
                                    className="inline-flex items-center rounded-md bg-foreground/[0.04] border border-foreground/[0.06] px-2 py-0.5 text-[10px] font-mono text-muted-foreground/50"
                                  >
                                    {String(port)}
                                  </span>
                                ))
                              )}
                              {container && (
                                <span className="text-[10px] text-muted-foreground/30">
                                  {container.status}
                                </span>
                              )}
                              {container && isRunning && (
                                <button
                                  onClick={() => handleRestartService(app, svc.name)}
                                  disabled={restartingService === svc.name}
                                  className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] text-muted-foreground/50 hover:text-foreground hover:bg-foreground/[0.06] transition-all disabled:opacity-50"
                                  title={`Restart ${svc.name}`}
                                >
                                  {restartingService === svc.name ? (
                                    <Loader2 className="h-2.5 w-2.5 animate-spin" />
                                  ) : (
                                    <RotateCcw className="h-2.5 w-2.5" />
                                  )}
                                  Restart
                                </button>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>

              {/* Logs Panel */}
              <AnimatePresence>
                {isLogsOpen && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: "auto", opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.2 }}
                    className="overflow-hidden"
                  >
                    <div className="border-t border-foreground/[0.04]">
                      {/* Log Header */}
                      <div className="flex items-center justify-between px-4 py-2 bg-foreground/[0.02]">
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] font-medium text-muted-foreground/50 uppercase tracking-wider">
                            Logs
                          </span>
                          {isBuilding && (
                            <span className="inline-flex items-center gap-1 text-[10px] text-blue-400">
                              <Loader2 className="h-2.5 w-2.5 animate-spin" />
                              auto-refreshing
                            </span>
                          )}
                        </div>
                        <div className="flex items-center gap-2">
                          <button
                            onClick={refreshLogs}
                            disabled={logsLoading}
                            className="inline-flex items-center gap-1 rounded px-2 py-0.5 text-[10px] text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-all"
                          >
                            <RefreshCw className={cn("h-2.5 w-2.5", logsLoading && "animate-spin")} />
                            Refresh
                          </button>
                          <button
                            onClick={() => { setLogsApp(null); setLogs([]); }}
                            className="rounded p-0.5 text-muted-foreground/40 hover:text-foreground transition-colors"
                          >
                            <X className="h-3 w-3" />
                          </button>
                        </div>
                      </div>

                      {/* Log Content */}
                      <div
                        ref={logRef}
                        className="max-h-72 overflow-y-auto bg-background dark:bg-[#0d1117] px-4 py-2 font-mono text-[11px] leading-5"
                      >
                        {isBuilding && logs.length === 0 ? (
                          <div className="flex items-center gap-2 py-4 text-blue-400/60">
                            <Loader2 className="h-3 w-3 animate-spin" />
                            Building &amp; starting containers... logs will appear here shortly
                          </div>
                        ) : logsLoading && logs.length === 0 ? (
                          <div className="flex items-center gap-2 py-4 text-muted-foreground/40">
                            <Loader2 className="h-3 w-3 animate-spin" />
                            Loading logs...
                          </div>
                        ) : logs.length === 0 ? (
                          <div className="py-4 text-muted-foreground/30 text-center">
                            No logs available
                          </div>
                        ) : (
                          logs.map((log, i) => (
                            <div key={i} className="flex gap-2 hover:bg-foreground/[0.02]">
                              <span className="text-primary/60 shrink-0 select-none">
                                {log.service}
                              </span>
                              <span className="text-foreground/80 break-all whitespace-pre-wrap">
                                {log.line}
                              </span>
                            </div>
                          ))
                        )}
                      </div>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>

              {/* Parse Error */}
              {app.error && (
                <div className="border-t border-red-500/10 px-4 py-2 bg-red-500/5">
                  <p className="text-[11px] text-red-400">{app.error}</p>
                </div>
              )}

              {/* Action Error (start/stop failure) */}
              {actionError?.path === app.path && (
                <div className="border-t border-red-500/10 px-4 py-3 bg-red-500/5">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex-1 min-w-0">
                      <p className="text-xs font-medium text-red-400 mb-1">
                        Failed to {app.status === "stopped" ? "start" : "stop"} app
                      </p>
                      <pre className="text-[11px] text-red-300/80 whitespace-pre-wrap break-all font-mono leading-relaxed max-h-40 overflow-y-auto">
                        {actionError.message}
                      </pre>
                    </div>
                    <button
                      onClick={() => setActionError(null)}
                      className="shrink-0 rounded p-1 text-red-400/50 hover:text-red-400 hover:bg-red-500/10 transition-colors"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              )}
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}
