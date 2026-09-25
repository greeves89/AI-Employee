"use client";

import { useEffect, useCallback } from "react";
import { useAgentStore } from "@/store/agent-store";
import * as api from "@/lib/api";
import { setVisibleInterval } from "@/lib/visible-interval";

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
    return setVisibleInterval(refresh, 15000); // alle 15 s, nur bei sichtbarem Tab
  }, [refresh]);

  return { agents, loading, error, refresh };
}
