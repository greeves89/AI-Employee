/**
 * Runtime configuration that adapts to the current browser hostname.
 *
 * Exported as getter functions so the URL is resolved lazily on each call,
 * ensuring window.location is always used on the client (never cached from SSR).
 *
 * If NEXT_PUBLIC_API_URL / NEXT_PUBLIC_WS_URL is set at build time, it takes precedence.
 * Otherwise, the API URL is derived from the current page hostname
 * so the app works from any device (localhost, LAN IP, domain, etc.).
 */

export function getApiUrl(): string {
  const envUrl = process.env.NEXT_PUBLIC_API_URL;
  if (envUrl) return envUrl;

  if (typeof window === "undefined") return "http://localhost:8000";

  const { hostname, protocol } = window.location;
  return `${protocol}//${hostname}:8000`;
}

export function getWsUrl(): string {
  const envUrl = process.env.NEXT_PUBLIC_WS_URL;
  if (envUrl) return envUrl;

  if (typeof window === "undefined") return "ws://localhost:8000";

  const { hostname } = window.location;
  const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${wsProtocol}//${hostname}:8000`;
}

export function getBase(): string {
  return `${getApiUrl()}/api/v1`;
}
