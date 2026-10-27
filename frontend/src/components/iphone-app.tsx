"use client";

import { Smartphone } from "lucide-react";

/** Die iOS-App „AI Employee Hub“ im App Store. */
export const IPHONE_APP_URL = "https://apps.apple.com/de/app/ai-employee-hub/id6787380062";

/** QR-Code + Link zur iPhone-App — am Rechner mit dem iPhone abfotografieren. */
export function IphoneApp({ kompakt = false }: { kompakt?: boolean }) {
  return (
    <div className="flex items-center gap-4 rounded-xl border border-foreground/[0.08] bg-foreground/[0.02] p-4">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src="/iphone-app-qr.png"
        alt="QR-Code zur App AI Employee Hub im App Store"
        width={kompakt ? 88 : 112}
        height={kompakt ? 88 : 112}
        className="shrink-0 rounded-lg bg-white p-1"
      />
      <div className="min-w-0">
        <p className="text-sm font-medium text-foreground">AI Employee Hub fürs iPhone</p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Kamera des iPhones auf den Code halten, App laden und mit denselben Zugangsdaten anmelden.
          Dann sprichst du unterwegs mit deinem Agenten und bekommst Freigaben aufs Handy.
        </p>
        <a
          href={IPHONE_APP_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-2.5 inline-flex items-center gap-2 rounded-lg bg-foreground/[0.06] border border-foreground/[0.08] px-3.5 py-2 text-xs font-medium text-foreground hover:bg-foreground/[0.1] transition-all"
        >
          <Smartphone className="h-3.5 w-3.5" />
          Im App Store öffnen
        </a>
      </div>
    </div>
  );
}
