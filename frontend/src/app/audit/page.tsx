"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";

// Audit Log lebt nur noch in der Admin-Konsole (Issue #787) — siehe
// ai-accounts/page.tsx fuer die Begruendung.
export default function AuditPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/admin?tab=audit");
  }, [router]);
  return (
    <div className="flex h-64 items-center justify-center text-muted-foreground/50">
      <Loader2 className="h-5 w-5 animate-spin" />
    </div>
  );
}
