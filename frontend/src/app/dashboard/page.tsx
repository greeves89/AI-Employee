"use client";

import { motion } from "framer-motion";
import Link from "next/link";
import { Plus, Bot } from "lucide-react";
import { useAgents } from "@/hooks/use-agents";
import { useTasks } from "@/hooks/use-tasks";
import { Header } from "@/components/layout/header";
import { AgentCard } from "@/components/dashboard/agent-card";
import { StatsOverview } from "@/components/dashboard/stats-overview";
import { RecentTasks } from "@/components/dashboard/recent-tasks";

const containerVariants = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.06 } },
};

const itemVariants = {
  hidden: { opacity: 0, y: 12 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.35, ease: [0.25, 0.46, 0.45, 0.94] as const },
  },
};

export default function DashboardPage() {
  const { agents, loading: agentsLoading } = useAgents();
  const { tasks } = useTasks();

  return (
    <div>
      <Header
        title="Dashboard"
        subtitle="Overview of your AI Employee agents"
        actions={
          <Link
            href="/agents"
            className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 hover:bg-primary/90 transition-all duration-200 hover:shadow-primary/30 hover:-translate-y-0.5"
          >
            <Plus className="h-4 w-4" />
            New Agent
          </Link>
        }
      />

      <motion.div
        className="px-8 py-8 space-y-8"
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3, ease: "easeOut" }}
      >
        {/* Stats */}
        <StatsOverview agents={agents} tasks={tasks} />

        {/* Agents */}
        <div>
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-semibold tracking-tight">Active Agents</h3>
            <Link
              href="/agents"
              className="text-[13px] text-muted-foreground hover:text-foreground transition-colors"
            >
              View all
            </Link>
          </div>

          {agentsLoading ? (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {[1, 2, 3].map((i) => (
                <div
                  key={i}
                  className="rounded-xl border border-foreground/[0.06] bg-card/50 p-5 h-[220px] animate-shimmer bg-[length:200%_100%] bg-gradient-to-r from-foreground/[0.03] via-foreground/[0.06] to-foreground/[0.03]"
                />
              ))}
            </div>
          ) : agents.length === 0 ? (
            <div className="rounded-xl border border-dashed border-foreground/[0.1] bg-card/30 p-12 text-center">
              <div className="flex justify-center mb-4">
                <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-primary/10">
                  <Bot className="h-6 w-6 text-primary" />
                </div>
              </div>
              <p className="text-muted-foreground mb-4">No agents running yet</p>
              <Link
                href="/agents"
                className="inline-flex items-center gap-2 rounded-xl bg-primary/10 px-4 py-2 text-sm font-medium text-primary hover:bg-primary/20 transition-colors"
              >
                <Plus className="h-4 w-4" />
                Create Agent
              </Link>
            </div>
          ) : (
            <motion.div
              className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4"
              variants={containerVariants}
              initial="hidden"
              animate="visible"
            >
              {agents.map((agent) => (
                <motion.div key={agent.id} variants={itemVariants}>
                  <AgentCard agent={agent} />
                </motion.div>
              ))}
            </motion.div>
          )}
        </div>

        {/* Recent Tasks */}
        <RecentTasks tasks={tasks} />
      </motion.div>
    </div>
  );
}
