"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Menu } from "lucide-react";
import { initAuth, useAuthStore } from "@/lib/auth";
import { Sidebar } from "@/components/layout/sidebar";
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

  // Logged in but on public page - redirect to dashboard
  if (isPublicPage) {
    router.replace("/dashboard");
    return null;
  }

  // Pages with custom layout (e.g. /chat has its own sidebar)
  const hasCustomLayout = CUSTOM_LAYOUT_PATHS.some((p) => pathname.startsWith(p));
  if (hasCustomLayout) {
    return <>{children}</>;
  }

  // Authenticated - show full layout with sidebar
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
