"use client";

import { useEffect, useCallback, useState } from "react";
import { useTaskStore } from "@/store/task-store";
import * as api from "@/lib/api";

export function useTasks(agentId?: string) {
  const { tasks, loading, error, setTasks, setLoading, setError } =
    useTaskStore();
  const [total, setTotal] = useState(0);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.getTasks(undefined, agentId);
      setTasks(data.tasks);
      setTotal(data.total ?? data.tasks.length);
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

  return { tasks, loading, error, refresh, total };
}
