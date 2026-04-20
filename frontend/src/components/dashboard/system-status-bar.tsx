"use client";

import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Database, Radio, Container, Wifi, RefreshCw } from "lucide-react";
import { getApiUrl } from "@/lib/config";
import { cn } from "@/lib/utils";

interface ServiceCheck {
  status: "healthy" | "unhealthy" | "degraded" | "unknown";
  error?: string;
  agent_containers?: number;
}

interface HealthData {
  status: "healthy" | "unhealthy";
  checks: {
    database: ServiceCheck;
    redis: ServiceCheck;
    docker: ServiceCheck;
  };
}

type DotStatus = "healthy" | "degraded" | "unhealthy" | "unknown";

function StatusDot({
  status,
  label,
  icon: Icon,
  detail,
}: {
  status: DotStatus;
  label: string;
  icon: React.ElementType;
  detail?: string;
}) {
  const colors: Record<DotStatus, string> = {
    healthy: "bg-emerald-500",
    degraded: "bg-amber-400",
    unhealthy: "bg-red-500",
    unknown: "bg-foreground/20",
  };
  const textColors: Record<DotStatus, string> = {
    healthy: "text-emerald-400",
    degraded: "text-amber-400",
    unhealthy: "text-red-400",
    unknown: "text-muted-foreground",
  };
  const pulse = status === "unhealthy" || status === "degraded";

  return (
    <div className="group relative flex items-center gap-1.5">
      <div className="relative flex items-center justify-center">
        <span
          className={cn(
            "h-2 w-2 rounded-full",
            colors[status],
            pulse && "animate-ping absolute opacity-60"
          )}
        />
        <span className={cn("h-2 w-2 rounded-full relative", colors[status])} />
      </div>
      <Icon className={cn("h-3.5 w-3.5", textColors[status])} />
      <span className={cn("text-[11px] font-medium", textColors[status])}>
        {label}
      </span>

      {/* Tooltip */}
      {detail && (
        <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 hidden group-hover:block z-50 pointer-events-none">
          <div className="rounded-lg border border-foreground/[0.08] bg-popover px-2.5 py-1.5 text-[11px] text-muted-foreground shadow-lg whitespace-nowrap">
            {detail}
          </div>
        </div>
      )}
    </div>
  );
}

export function SystemStatusBar() {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const fetchHealth = async () => {
    try {
      const res = await fetch(`${getApiUrl()}/api/v1/health`, {
        credentials: "include",
        signal: AbortSignal.timeout(5000),
      });
      if (!res.ok) { setHealth(null); return; }
      const data = await res.json();
      // Validate shape — ignore error responses like {"detail": "Not Found"}
      if (!data?.checks?.database) { setHealth(null); return; }
      setHealth(data as HealthData);
      setLastUpdated(new Date());
    } catch {
      setHealth(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchHealth();
    const interval = setInterval(fetchHealth, 30_000);
    return () => clearInterval(interval);
  }, []);

  const toStatus = (check?: ServiceCheck): DotStatus => {
    if (!check) return "unknown";
    if (check.status === "healthy") return "healthy";
    if (check.status === "degraded") return "degraded";
    return "unhealthy";
  };

  const apiStatus: DotStatus = loading
    ? "unknown"
    : health
    ? "healthy"
    : "unhealthy";

  const dbStatus = toStatus(health?.checks.database);
  const redisStatus = toStatus(health?.checks.redis);
  const dockerStatus = toStatus(health?.checks.docker);
  const agentCount = health?.checks.docker.agent_containers;

  const overallHealthy =
    !loading &&
    health !== null &&
    dbStatus === "healthy" &&
    redisStatus === "healthy";

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0, y: -4 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3, delay: 0.2 }}
        className="flex items-center gap-4 rounded-xl border border-foreground/[0.06] bg-card/60 backdrop-blur-sm px-4 py-2"
      >
        {/* Overall pill */}
        <div
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium",
            loading
              ? "border-foreground/10 text-muted-foreground"
              : overallHealthy
              ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
              : "bg-red-500/10 text-red-400 border-red-500/20"
          )}
        >
          <span
            className={cn(
              "h-1.5 w-1.5 rounded-full",
              loading
                ? "bg-foreground/20"
                : overallHealthy
                ? "bg-emerald-400"
                : "bg-red-400 animate-pulse"
            )}
          />
          {loading ? "Checking…" : overallHealthy ? "All Systems Go" : "Degraded"}
        </div>

        <div className="h-4 w-px bg-foreground/[0.06]" />

        {/* Individual service dots */}
        <div className="flex items-center gap-4">
          <StatusDot
            status={apiStatus}
            label="API"
            icon={Wifi}
            detail={apiStatus === "unhealthy" ? "Orchestrator unreachable" : "Orchestrator API"}
          />
          <StatusDot
            status={dbStatus}
            label="DB"
            icon={Database}
            detail={
              health?.checks.database.error ??
              (dbStatus === "healthy" ? "PostgreSQL healthy" : "Database error")
            }
          />
          <StatusDot
            status={redisStatus}
            label="Redis"
            icon={Radio}
            detail={
              health?.checks.redis.error ??
              (redisStatus === "healthy" ? "Redis healthy" : "Redis error")
            }
          />
          <StatusDot
            status={dockerStatus}
            label={agentCount !== undefined ? `${agentCount} Agents` : "Docker"}
            icon={Container}
            detail={
              health?.checks.docker.error ??
              (agentCount !== undefined
                ? `${agentCount} agent container${agentCount !== 1 ? "s" : ""} running`
                : "Docker healthy")
            }
          />
        </div>

        {/* Refresh + last updated */}
        <button
          onClick={fetchHealth}
          className="ml-auto text-muted-foreground/50 hover:text-muted-foreground transition-colors"
          title={
            lastUpdated
              ? `Last checked ${lastUpdated.toLocaleTimeString()}`
              : "Refresh"
          }
        >
          <RefreshCw className="h-3.5 w-3.5" />
        </button>
      </motion.div>
    </AnimatePresence>
  );
}
