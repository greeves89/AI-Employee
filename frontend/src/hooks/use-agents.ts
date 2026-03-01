"use client";

import { useEffect, useCallback } from "react";
import { useAgentStore } from "@/store/agent-store";
import * as api from "@/lib/api";

export function useAgents() {
  const { agents, loading, error, setAgents, setLoading, setError } =
    useAgentStore();

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.getAgents();
      setAgents(data.agents);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load agents");
    } finally {
      setLoading(false);
    }
  }, [setAgents, setLoading, setError]);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 15000); // Poll every 15s
    return () => clearInterval(interval);
  }, [refresh]);

  return { agents, loading, error, refresh };
}
