"use client";

import { createContext, createElement, useContext, useState, useEffect, type ReactNode } from "react";

interface SidebarState {
  collapsed: boolean;       // desktop icon-rail mode
  toggle: () => void;       // toggle desktop collapse
  mobileOpen: boolean;      // mobile off-canvas drawer open
  setMobileOpen: (v: boolean) => void;
}

const SidebarContext = createContext<SidebarState | null>(null);

export function SidebarProvider({ children }: { children: ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    if (localStorage.getItem("sidebar-collapsed") === "true") setCollapsed(true);
  }, []);

  const toggle = () => {
    setCollapsed((v) => {
      localStorage.setItem("sidebar-collapsed", String(!v));
      return !v;
    });
  };

  return createElement(
    SidebarContext.Provider,
    { value: { collapsed, toggle, mobileOpen, setMobileOpen } },
    children
  );
}

// Shared across the whole app shell (sidebar + main + hamburger + backdrop) so the
// desktop collapse and the mobile drawer stay in sync. Falls back to a no-op state
// for components rendered outside the provider.
export function useSidebarCollapsed(): SidebarState {
  const ctx = useContext(SidebarContext);
  if (ctx) return ctx;
  return { collapsed: false, toggle: () => {}, mobileOpen: false, setMobileOpen: () => {} };
}
