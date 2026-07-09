"use client";

import { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import { LogOut, Shield } from "lucide-react";
import { cn } from "@/lib/utils";
import { logout, useAuthStore } from "@/lib/auth";
import { UserAvatar } from "@/components/ui/user-avatar";

export function UserMenu() {
  const { user } = useAuthStore();
  const router = useRouter();
  const [isOpen, setIsOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    if (isOpen) document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [isOpen]);

  if (!user) return null;

  const handleLogout = async () => {
    await logout();
    router.push("/login");
  };

  return (
    <div className="relative" ref={menuRef}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-muted-foreground hover:bg-accent/50 hover:text-foreground transition-all duration-150"
      >
        <UserAvatar name={user.name} />
        <div className="flex-1 min-w-0 text-left">
          <p className="text-[12px] font-medium text-foreground truncate">{user.name}</p>
          <p className="flex items-center gap-1 text-[10px] text-muted-foreground/60 truncate">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 shrink-0" /> Online
          </p>
        </div>
        {user.role === "admin" && (
          <Shield className="h-3 w-3 text-amber-500 shrink-0" />
        )}
      </button>

      {isOpen && (
        <div className="absolute left-full ml-2 bottom-0 w-48 rounded-xl border border-border bg-card shadow-2xl z-50 overflow-hidden">
          <div className="px-3 py-2 border-b border-border">
            <p className="text-xs font-medium truncate">{user.name}</p>
            <p className="text-[10px] text-muted-foreground truncate">{user.email}</p>
            <span className={cn(
              "inline-flex mt-1 px-1.5 py-0.5 rounded text-[9px] font-medium",
              user.role === "admin"
                ? "bg-amber-500/10 text-amber-500"
                : "bg-blue-500/10 text-blue-500"
            )}>
              {user.role}
            </span>
          </div>
          <div className="py-1">
            <button
              onClick={handleLogout}
              className="flex w-full items-center gap-2 px-3 py-2 text-xs text-red-400 hover:bg-accent/50 transition-colors"
            >
              <LogOut className="h-3.5 w-3.5" />
              Sign Out
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
