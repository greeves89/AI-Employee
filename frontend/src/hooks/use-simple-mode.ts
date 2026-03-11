"use client";

import { create } from "zustand";

const STORAGE_KEY = "ui_mode";

interface SimpleModeState {
  simpleMode: boolean;
  setSimpleMode: (simple: boolean) => void;
  toggleSimpleMode: () => void;
}

export const useSimpleMode = create<SimpleModeState>((set, get) => ({
  simpleMode:
    typeof window !== "undefined"
      ? localStorage.getItem(STORAGE_KEY) === "simple"
      : false,
  setSimpleMode: (simple: boolean) => {
    localStorage.setItem(STORAGE_KEY, simple ? "simple" : "advanced");
    set({ simpleMode: simple });
  },
  toggleSimpleMode: () => {
    const next = !get().simpleMode;
    localStorage.setItem(STORAGE_KEY, next ? "simple" : "advanced");
    set({ simpleMode: next });
  },
}));
