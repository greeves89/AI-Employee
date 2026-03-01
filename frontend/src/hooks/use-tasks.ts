"use client";

import { useEffect, useCallback } from "react";
import { useTaskStore } from "@/store/task-store";
import * as api from "@/lib/api";

export function useTasks(agentId?: string) {
  const { tasks, loading, error, setTasks, setLoading, setError } =
    useTaskStore();

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.getTasks(undefined, agentId);
      setTasks(data.tasks);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load tasks");
    } finally {
      setLoading(false);
    }
  }, [agentId, setTasks, setLoading, setError]);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 15000);
    return () => clearInterval(interval);
  }, [refresh]);

  return { tasks, loading, error, refresh };
}
