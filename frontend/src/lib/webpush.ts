/**
 * Web Push im Browser — an-/abmelden und Zustand erkennen.
 *
 * Der Ablauf ist zweigeteilt und das ist wichtig zu verstehen: Der Browser meldet
 * sich beim Push-Dienst des Herstellers an (Google/Mozilla/Apple) und bekommt von
 * dort eine Endpunkt-Adresse plus zwei Schlüssel. Diese drei Angaben schicken wir an
 * unseren Server. Der Server verschlüsselt jede Meldung für genau diesen Empfänger —
 * der Push-Dienst leitet nur weiter und kann nicht mitlesen.
 */

import { getBase } from "@/lib/config";

export type PushState =
  | "unsupported"   // Browser kann es nicht (z. B. iOS-Safari ohne Installation)
  | "denied"        // Nutzer hat abgelehnt — nur in den Browsereinstellungen umkehrbar
  | "subscribed"
  | "unsubscribed";

/**
 * Base64url → Bytes, was `applicationServerKey` verlangt.
 *
 * Der Puffer wird ausdrücklich als `ArrayBuffer` angelegt: `new Uint8Array(länge)`
 * ergibt `Uint8Array<ArrayBufferLike>`, was auch einen `SharedArrayBuffer` einschließt
 * — und den nimmt `pushManager.subscribe` nicht an.
 */
function urlBase64ToUint8Array(base64: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + padding).replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

function arrayBufferToBase64Url(buffer: ArrayBuffer | null): string {
  if (!buffer) return "";
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function pushSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

/** Service Worker registrieren (idempotent) und die Registrierung zurückgeben. */
export async function ensureServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (!pushSupported()) return null;
  try {
    return await navigator.serviceWorker.register("/sw.js", { scope: "/" });
  } catch {
    return null;
  }
}

export async function getPushState(): Promise<PushState> {
  if (!pushSupported()) return "unsupported";
  if (Notification.permission === "denied") return "denied";
  const reg = await navigator.serviceWorker.getRegistration("/");
  const sub = await reg?.pushManager.getSubscription();
  return sub ? "subscribed" : "unsubscribed";
}

export async function subscribeToPush(): Promise<PushState> {
  if (!pushSupported()) return "unsupported";

  const permission = await Notification.requestPermission();
  if (permission !== "granted") return permission === "denied" ? "denied" : "unsubscribed";

  const reg = await ensureServiceWorker();
  if (!reg) return "unsupported";
  // Erst wenn der Worker wirklich aktiv ist, nimmt der PushManager eine Anmeldung an.
  await navigator.serviceWorker.ready;

  const keyRes = await fetch(`${getBase()}/notifications/push/public-key`, {
    credentials: "include",
  });
  const { public_key: publicKey } = await keyRes.json();
  if (!publicKey) return "unsubscribed";

  const existing = await reg.pushManager.getSubscription();
  const sub =
    existing ||
    (await reg.pushManager.subscribe({
      // Ohne diese Zusage verweigern Chrome und Firefox die Anmeldung: sie bedeutet,
      // dass jede Push-Meldung dem Nutzer auch angezeigt wird (keine stillen Pushes).
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(publicKey),
    }));

  const json = sub.toJSON() as { endpoint?: string; keys?: { p256dh?: string; auth?: string } };
  await fetch(`${getBase()}/notifications/push/subscribe`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      endpoint: json.endpoint || sub.endpoint,
      p256dh: json.keys?.p256dh || arrayBufferToBase64Url(sub.getKey("p256dh")),
      auth: json.keys?.auth || arrayBufferToBase64Url(sub.getKey("auth")),
    }),
  });

  return "subscribed";
}

export async function unsubscribeFromPush(): Promise<PushState> {
  const reg = await navigator.serviceWorker.getRegistration("/");
  const sub = await reg?.pushManager.getSubscription();
  if (!sub) return "unsubscribed";

  // Erst dem Server sagen, dann lokal abmelden. Andersherum kennen wir den Endpunkt
  // nicht mehr und der Server schickt weiter an eine tote Anmeldung.
  await fetch(`${getBase()}/notifications/push/unsubscribe`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ endpoint: sub.endpoint }),
  }).catch(() => {});
  await sub.unsubscribe();
  return "unsubscribed";
}
