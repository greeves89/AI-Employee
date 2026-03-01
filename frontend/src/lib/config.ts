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

  const { hostname, protocol, port } = window.location;
  // Behind reverse proxy (HTTPS or standard port) → same origin, Caddy routes /api
  if (protocol === "https:" || port === "" || port === "80") {
    return `${protocol}//${hostname}`;
  }
  return `${protocol}//${hostname}:8000`;
}

export function getWsUrl(): string {
  const envUrl = process.env.NEXT_PUBLIC_WS_URL;
  if (envUrl) return envUrl;

  if (typeof window === "undefined") return "ws://localhost:8000";

  const { hostname, protocol, port } = window.location;
  const wsProtocol = protocol === "https:" ? "wss:" : "ws:";
  // Behind reverse proxy → same origin, Caddy routes /ws
  if (protocol === "https:" || port === "" || port === "80") {
    return `${wsProtocol}//${hostname}`;
  }
  return `${wsProtocol}//${hostname}:8000`;
}

export function getBase(): string {
  return `${getApiUrl()}/api/v1`;
}
