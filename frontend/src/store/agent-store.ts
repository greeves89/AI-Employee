import { create } from "zustand";
import type { Agent } from "@/lib/types";

interface AgentStore {
  agents: Agent[];
  loading: boolean;
  error: string | null;
  setAgents: (agents: Agent[]) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
}

export const useAgentStore = create<AgentStore>((set) => ({
  agents: [],
  loading: false,
  error: null,
  setAgents: (agents) => set({ agents }),
  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error }),
}));
