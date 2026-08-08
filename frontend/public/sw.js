/**
 * Service Worker — nimmt Push-Meldungen entgegen, wenn die Seite zu ist.
 *
 * Bewusst OHNE Zwischenspeicher für Seiten und Daten: Die Oberfläche zeigt
 * Agentenzustände, laufende Aufgaben und offene Freigaben. Eine zwischengespeicherte
 * Version davon wäre nicht "offline verfügbar", sondern schlicht falsch — jemand
 * würde eine längst erteilte Freigabe erneut sehen oder einen gestoppten Agenten für
 * laufend halten. Der Nutzen der PWA liegt hier im eigenen Fenster und in den
 * Meldungen, nicht im Offline-Betrieb.
 */

const NOTIFICATION_TAG = "ai-employee";

self.addEventListener("install", () => {
  // Sofort übernehmen, statt auf das Schließen aller alten Tabs zu warten —
  // sonst bleibt nach einem Update wochenlang die alte Fassung aktiv.
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch {
    // Kein JSON: dann wenigstens den Rohtext zeigen, statt gar nichts.
    payload = { title: "AI Employee", body: event.data ? event.data.text() : "" };
  }

  const title = payload.title || "AI Employee";
  const data = payload.data || {};
  const options = {
    body: payload.body || "",
    icon: "/favicon.png",
    badge: "/favicon.png",
    // Gleicher Tag = die neue Meldung ersetzt die alte, statt den Sperrbildschirm
    // mit fünf Zeilen desselben Agenten zu füllen.
    tag: data.tag || NOTIFICATION_TAG,
    renotify: true,
    data,
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = event.notification.data?.action_url || "/";

  // Ein bereits offenes Fenster wiederverwenden, statt bei jedem Klick ein neues
  // aufzumachen — sonst sammeln sich Tabs derselben Anwendung.
  event.waitUntil(
    self.clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((clientList) => {
        for (const client of clientList) {
          if ("focus" in client) {
            if ("navigate" in client && target !== "/") client.navigate(target);
            return client.focus();
          }
        }
        return self.clients.openWindow(target);
      })
  );
});
