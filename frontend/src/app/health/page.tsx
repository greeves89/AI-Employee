"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";

// Health lebt nur noch in der Admin-Konsole (Issue #787) — siehe
// ai-accounts/page.tsx fuer die Begruendung.
export default function HealthPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/admin?tab=health");
  }, [router]);
  return (
    <div className="flex h-64 items-center justify-center text-muted-foreground/50">
      <Loader2 className="h-5 w-5 animate-spin" />
    </div>
  );
}
