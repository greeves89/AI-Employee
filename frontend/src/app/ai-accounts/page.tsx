"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";

// AI-Accounts lebt nur noch in der Admin-Konsole (Issue #787) — diese Seite
// existierte parallel zum eingebetteten Reiter dort, zwei Mount-Punkte
// derselben Komponente, die im Verhalten auseinanderlaufen konnten. Statt
// zwei Implementierungen zu pflegen, leitet diese Route auf den Reiter um.
export default function AIAccountsPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/admin?tab=ai-accounts");
  }, [router]);
  return (
    <div className="flex h-64 items-center justify-center text-muted-foreground/50">
      <Loader2 className="h-5 w-5 animate-spin" />
    </div>
  );
}
