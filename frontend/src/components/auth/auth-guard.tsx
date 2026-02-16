"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { initAuth, useAuthStore } from "@/lib/auth";
import { Sidebar } from "@/components/layout/sidebar";

const PUBLIC_PATHS = ["/login", "/register"];

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, loading, setupMode } = useAuthStore();

  // Initialize auth on mount
  useEffect(() => {
    initAuth();
  }, []);

  const isPublicPage = PUBLIC_PATHS.some((p) => pathname.startsWith(p));

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

  // Authenticated - show full layout with sidebar
  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <main className="ml-[260px] flex-1 min-h-screen">{children}</main>
    </div>
  );
}
