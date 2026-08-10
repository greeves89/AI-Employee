"use client";

import { Suspense, useEffect } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Menu, ShieldAlert } from "lucide-react";
import { initAuth, useAuthStore } from "@/lib/auth";
import { Sidebar } from "@/components/layout/sidebar";
import { VoiceSessionProvider } from "@/components/agents/voice-session-provider";
import { SidebarProvider, useSidebarCollapsed } from "@/hooks/use-sidebar";
import { cn } from "@/lib/utils";

const PUBLIC_PATHS = ["/login", "/register"];
const CUSTOM_LAYOUT_PATHS = ["/chat"];

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, loading, setupMode } = useAuthStore();

  // Initialize auth on mount
  useEffect(() => {
    initAuth();
  }, []);

  // Presence heartbeat — mark this user online while the app is open.
  useEffect(() => {
    if (!user) return;
    let alive = true;
    const beat = () => { import("@/lib/api").then((a) => a.presenceHeartbeat().catch(() => {})); };
    beat();
    const iv = setInterval(() => { if (alive) beat(); }, 45000);
    return () => { alive = false; clearInterval(iv); };
  }, [user]);

  const isPublicPage = PUBLIC_PATHS.some((p) => pathname.startsWith(p));

  // Kiosk: local-only fullscreen display on the Pi. No auth, no sidebar, no
  // redirect — rendered immediately. (Reachability is restricted to the device
  // by Caddy, which 404s /kiosk for tunnel traffic.)
  if (pathname.startsWith("/kiosk")) {
    return <>{children}</>;
  }

  // Show loading spinner while checking auth
  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary/30 border-t-primary" />
      </div>
    );
  }

  // Setup mode: no users registered yet - allow everything (backend returns anonymous admin)
  if (setupMode) {
    // Redirect to register if trying to access anything other than register
    if (!pathname.startsWith("/register")) {
      router.replace("/register");
      return null;
    }
    // Show register page without sidebar
    return <>{children}</>;
  }

  // Not logged in
  if (!user) {
    if (!isPublicPage) {
      router.replace("/login");
      return null;
    }
    // Show login/register without sidebar
    return <>{children}</>;
  }

  // Angemeldet, aber ohne zugewiesene Rolle: eine Erklaerung und sonst nichts.
  //
  // Ohne Seitenleiste, ohne Inhalt, ohne Umleitung — jede Seite endet hier. Die
  // Sperre selbst sitzt im Orchestrator (jede Anfrage bekaeme 403); das hier ist
  // die Erklaerung dazu. Eine leere Oberflaeche ohne Begruendung waere schlimmer
  // als eine Fehlermeldung.
  if (user.role === "unassigned") {
    return <NoRoleNotice email={user.email} />;
  }

  // Logged in but on public page - redirect to dashboard
  if (isPublicPage) {
    router.replace("/dashboard");
    return null;
  }

  // Pages with custom layout (e.g. /chat has its own sidebar)
  const hasCustomLayout = CUSTOM_LAYOUT_PATHS.some((p) => pathname.startsWith(p));

  // Authenticated — full layout, unless the page is being embedded somewhere.
  return (
    <VoiceSessionProvider>
      {hasCustomLayout ? (
        <>{children}</>
      ) : (
        <Suspense fallback={null}>
          <ShellOrBare>{children}</ShellOrBare>
        </Suspense>
      )}
    </VoiceSessionProvider>
  );
}

/** `?embed=1` renders the page WITHOUT the app chrome (no sidebar, no hamburger).
 *
 *  Used when a page is shown inside another view — e.g. the voice cockpit, which
 *  displays Analytics/Tasks/Knowledge in a panel instead of navigating away (that
 *  would unmount the live session and kill the microphone). Authentication is
 *  unaffected: everything above still had to pass, this only drops the frame.
 *
 *  Own component + Suspense because `useSearchParams` requires a boundary. */
function ShellOrBare({ children }: { children: React.ReactNode }) {
  const params = useSearchParams();
  if (params.get("embed") === "1") {
    return <div className="min-h-screen">{children}</div>;
  }
  return (
    <SidebarProvider>
      <AppShell>{children}</AppShell>
    </SidebarProvider>
  );
}

/** App shell: fixed sidebar on desktop, off-canvas drawer on mobile. The main content
 *  is full-width on mobile (no left margin) and offset by the sidebar width on lg+. */
function AppShell({ children }: { children: React.ReactNode }) {
  const { collapsed, mobileOpen, setMobileOpen } = useSidebarCollapsed();
  return (
    <div className="min-h-screen">
      <Sidebar />

      {/* Mobile backdrop — tap to close the drawer */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/50 backdrop-blur-sm lg:hidden"
          onClick={() => setMobileOpen(false)}
          aria-hidden
        />
      )}

      {/* Mobile hamburger — only when the drawer is closed */}
      {!mobileOpen && (
        <button
          onClick={() => setMobileOpen(true)}
          className="fixed left-3 top-3 z-30 inline-flex h-10 w-10 items-center justify-center rounded-xl border border-border bg-card/80 text-foreground backdrop-blur-xl shadow-lg lg:hidden"
          aria-label="Menü öffnen"
        >
          <Menu className="h-5 w-5" />
        </button>
      )}

      <main
        className={cn(
          "min-h-screen min-w-0 overflow-x-hidden transition-[margin] duration-300",
          collapsed ? "lg:ml-[64px]" : "lg:ml-[260px]"
        )}
      >
        {children}
      </main>
    </div>
  );
}

/** „Dein Konto ist da, aber du darfst noch nichts." */
function NoRoleNotice({ email }: { email: string }) {
  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <div className="w-full max-w-md rounded-2xl border border-amber-500/20 bg-card p-8 text-center shadow-2xl">
        <div className="mx-auto mb-5 flex h-16 w-16 items-center justify-center rounded-full bg-amber-500/15">
          <ShieldAlert className="h-8 w-8 text-amber-400" />
        </div>
        <h2 className="text-lg font-semibold text-foreground">Noch keine Rolle zugewiesen</h2>
        <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
          Dein Konto <b className="text-foreground">{email}</b> ist angelegt, aber noch
          keiner Rolle zugeordnet. Um diese Anwendung zu nutzen, brauchst du eine Rolle
          — bitte <b className="text-foreground">wende dich an einen Administrator</b>.
        </p>
        <p className="mt-4 text-[12px] text-muted-foreground/60">
          Angebundene Dienste, für die du dich hier angemeldet hast, funktionieren
          davon unabhängig weiter.
        </p>
        <button
          onClick={() => {
            import("@/lib/auth").then((a) => a.logout());
          }}
          className="mt-6 w-full rounded-xl border border-foreground/[0.08] px-4 py-2.5 text-sm font-medium text-muted-foreground transition-all hover:bg-foreground/[0.06] hover:text-foreground"
        >
          Abmelden
        </button>
      </div>
    </div>
  );
}
