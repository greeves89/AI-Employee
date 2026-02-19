/**
 * Frontend authentication helpers.
 * All auth state lives in httpOnly cookies managed by the backend.
 * This module provides convenience wrappers for login/register/logout
 * and a Zustand store for the current user.
 */

import { create } from "zustand";

import { getApiUrl } from "./config";
function authBase() { return `${getApiUrl()}/api/v1/auth`; }

export interface AuthUser {
  id: string;
  email: string;
  name: string;
  role: "admin" | "member";
  is_active: boolean;
}

interface AuthStore {
  user: AuthUser | null;
  loading: boolean;
  /** Null = unknown, true = setup mode (no users), false = normal */
  setupMode: boolean | null;
  /** Access token for WebSocket connections (not used for HTTP - cookies handle that) */
  wsToken: string | null;
  setUser: (user: AuthUser | null) => void;
  setLoading: (loading: boolean) => void;
  setSetupMode: (mode: boolean | null) => void;
  setWsToken: (token: string | null) => void;
}

export const useAuthStore = create<AuthStore>((set) => ({
  user: null,
  loading: true,
  setupMode: null,
  wsToken: null,
  setUser: (user) => set({ user }),
  setLoading: (loading) => set({ loading }),
  setSetupMode: (setupMode) => set({ setupMode }),
  setWsToken: (wsToken) => set({ wsToken }),
}));

async function authFetch<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    ...options,
  });
  if (!res.ok) {
    const body = await res.text();
    let detail = `Error ${res.status}`;
    try {
      const json = JSON.parse(body);
      detail = json.detail || detail;
    } catch {
      detail = body || detail;
    }
    throw new Error(detail);
  }
  return res.json();
}

export async function login(email: string, password: string): Promise<AuthUser> {
  const data = await authFetch<{ user: AuthUser; access_token: string }>(`${authBase()}/login`, {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  const store = useAuthStore.getState();
  store.setUser(data.user);
  store.setWsToken(data.access_token);
  store.setSetupMode(false);
  return data.user;
}

export async function register(name: string, email: string, password: string): Promise<AuthUser> {
  const data = await authFetch<{ user: AuthUser; access_token: string }>(`${authBase()}/register`, {
    method: "POST",
    body: JSON.stringify({ name, email, password }),
  });
  const store = useAuthStore.getState();
  store.setUser(data.user);
  store.setWsToken(data.access_token);
  store.setSetupMode(false);
  return data.user;
}

export async function logout(): Promise<void> {
  await authFetch(`${authBase()}/logout`, { method: "POST" });
  const store = useAuthStore.getState();
  store.setUser(null);
  store.setWsToken(null);
}

export async function getMe(): Promise<AuthUser> {
  return authFetch<AuthUser>(`${authBase()}/me`);
}

export async function refreshToken(): Promise<AuthUser> {
  const data = await authFetch<{ user: AuthUser; access_token: string }>(`${authBase()}/refresh`, {
    method: "POST",
  });
  const store = useAuthStore.getState();
  store.setUser(data.user);
  store.setWsToken(data.access_token);
  return data.user;
}

export async function getRegistrationStatus(): Promise<{
  registration_open: boolean;
  needs_setup: boolean;
}> {
  return authFetch(`${authBase()}/registration-status`);
}

export interface SSOProvider {
  name: string;
  display_name: string;
  icon: string;
}

export async function getSSOProviders(): Promise<SSOProvider[]> {
  const data = await authFetch<{ providers: SSOProvider[] }>(`${authBase()}/sso/providers`);
  return data.providers;
}

/**
 * Initialize auth state: check if user is logged in, handle setup mode.
 * Called once on app load.
 */
export async function initAuth(): Promise<void> {
  const store = useAuthStore.getState();
  store.setLoading(true);

  try {
    // Check registration status first (public endpoint)
    const status = await getRegistrationStatus();
    store.setSetupMode(status.needs_setup);

    if (status.needs_setup) {
      // No users yet - setup mode, no auth needed
      store.setUser(null);
      store.setLoading(false);
      return;
    }

    // Try to get current user via /me (uses cookie)
    const user = await getMe();
    store.setUser(user);
    // Refresh to get a WS token (access_token in response body)
    try {
      await refreshToken();
    } catch {
      // Token refresh failed but /me worked - user is still logged in, WS just won't have token
    }
  } catch {
    // Not logged in or token expired - try refresh
    try {
      await refreshToken();
    } catch {
      store.setUser(null);
    }
  } finally {
    store.setLoading(false);
  }
}
