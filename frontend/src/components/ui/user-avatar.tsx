"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { getBase } from "@/lib/config";

/**
 * Profile photo of the logged-in user (from SSO via /auth/me/photo),
 * with initials as fallback when there is no photo.
 *
 * The photo is fetched once per page load and shared across all instances
 * (module-level cache) — chat renders one avatar per message bubble.
 */

let cachedUrl: string | null | undefined; // undefined = not fetched yet, null = no photo
let inflight: Promise<string | null> | null = null;

async function fetchPhoto(): Promise<string | null> {
  try {
    const res = await fetch(`${getBase()}/auth/me/photo`, { credentials: "include" });
    if (!res.ok) return null;
    const blob = await res.blob();
    return URL.createObjectURL(blob);
  } catch {
    return null;
  }
}

function useUserPhoto(): string | null {
  const [url, setUrl] = useState<string | null>(cachedUrl ?? null);
  useEffect(() => {
    if (cachedUrl !== undefined) {
      setUrl(cachedUrl);
      return;
    }
    if (!inflight) {
      inflight = fetchPhoto().then((u) => {
        cachedUrl = u;
        return u;
      });
    }
    let mounted = true;
    inflight.then((u) => {
      if (mounted) setUrl(u);
    });
    return () => {
      mounted = false;
    };
  }, []);
  return url;
}

export function userInitials(name: string): string {
  return name
    .split(" ")
    .filter(Boolean)
    .map((n) => n[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

export function UserAvatar({ name, className }: { name: string; className?: string }) {
  const photoUrl = useUserPhoto();

  if (photoUrl) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={photoUrl}
        alt={name}
        className={cn("h-8 w-8 rounded-lg object-cover", className)}
      />
    );
  }
  return (
    <div
      className={cn(
        "flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10 text-primary text-xs font-bold",
        className
      )}
    >
      {userInitials(name)}
    </div>
  );
}
