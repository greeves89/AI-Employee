"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";

// Key Management lebt nur noch in der Admin-Konsole (Issue #787) — siehe
// ai-accounts/page.tsx fuer die Begruendung. Diese Standalone-Route hatte
// zudem keine eigene Rollenpruefung — jeder eingeloggte Nutzer konnte sie
// direkt aufrufen, obwohl derselbe Reiter in der Admin-Konsole laengst hinter
// dem Admin-Gate liegt. Die Umleitung schliesst diese Luecke gleich mit.
export default function SecretsPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/admin?tab=secrets");
  }, [router]);
  return (
    <div className="flex h-64 items-center justify-center text-muted-foreground/50">
      <Loader2 className="h-5 w-5 animate-spin" />
    </div>
  );
}
