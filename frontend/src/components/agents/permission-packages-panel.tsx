"use client";

import { Check, Loader2, Package, Settings, ShieldOff } from "lucide-react";
import { cn } from "@/lib/utils";
import type { PermissionPackage } from "@/lib/types";

// Vorher an ZWEI Stellen fast identisch codiert: der Erstellungs-Dialog
// (create-agent-modal.tsx) und die Agent-Settings-Seite. Beide konnten schon
// heute auseinanderlaufen (Issue #787 Punkt 1) — jetzt eine gemeinsame
// Komponente statt einer Kopie.
export const PERMISSION_ICON_MAP: Record<string, React.ElementType> = {
  package: Package,
  settings: Settings,
  "shield-off": ShieldOff,
};

export interface PermissionPackagesPanelProps {
  packages: PermissionPackage[];
  autonomyLevel: string;
  derivedPermissions: Record<string, string[]>;
  permissionsMode: "auto" | "manual";
  onPermissionsModeChange: (mode: "auto" | "manual") => void;
  selected: string[];
  onTogglePermission: (id: string) => void;
  /** Zusätzliche Kopfzeilen-Aktion (z.B. ein Speichern-Button) — der
   * Aufrufer verwaltet den eigenen Speicher-Zustand, diese Komponente nicht. */
  headerActions?: React.ReactNode;
}

export function PermissionPackagesPanel({
  packages,
  autonomyLevel,
  derivedPermissions,
  permissionsMode,
  onPermissionsModeChange,
  selected,
  onTogglePermission,
  headerActions,
}: PermissionPackagesPanelProps) {
  return (
    <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden">
      <div className="flex items-center justify-between border-b border-foreground/[0.06] px-5 py-3">
        <div className="flex items-center gap-2">
          <Package className="h-4 w-4 text-primary" />
          <span className="text-sm font-medium">Berechtigungen (Sudo-Pakete)</span>
        </div>
        <button
          type="button"
          onClick={() => onPermissionsModeChange(permissionsMode === "auto" ? "manual" : "auto")}
          className="ml-auto mr-3 text-[11px] text-primary hover:underline"
        >
          {permissionsMode === "auto" ? "Selbst festlegen" : "Wieder an Stufe koppeln"}
        </button>
        {headerActions}
      </div>

      {permissionsMode === "auto" && (
        <p className="px-5 pt-4 text-[11px] text-muted-foreground">
          Folgt der Autonomiestufe {autonomyLevel.toUpperCase()}.{" "}
          {(derivedPermissions[autonomyLevel] || []).length === 0
            ? "Der Container bekommt keine sudo-Rechte."
            : `Der Container bekommt: ${(derivedPermissions[autonomyLevel] || [])
                .map((id) => packages.find((p) => p.id === id)?.label || id)
                .join(", ")}.`}
        </p>
      )}

      <div className={cn("p-5 space-y-2", permissionsMode === "auto" && "pointer-events-none opacity-40")}>
        {packages.map((pkg) => {
          const Icon = PERMISSION_ICON_MAP[pkg.icon] || Package;
          const isSelected = permissionsMode === "auto"
            ? (derivedPermissions[autonomyLevel] || []).includes(pkg.id)
            : selected.includes(pkg.id);
          const isFullAccess = pkg.id === "full-access";

          return (
            <button
              key={pkg.id}
              type="button"
              disabled={permissionsMode === "auto"}
              onClick={() => onTogglePermission(pkg.id)}
              className={cn(
                "w-full flex items-start gap-3 rounded-xl border p-3.5 text-left transition-all duration-200",
                isSelected
                  ? isFullAccess
                    ? "border-amber-500/40 bg-amber-500/[0.08]"
                    : "border-primary/40 bg-primary/[0.08]"
                  : "border-foreground/[0.06] bg-foreground/[0.02] hover:bg-foreground/[0.04]"
              )}
            >
              <div
                className={cn(
                  "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg transition-colors",
                  isSelected
                    ? isFullAccess
                      ? "bg-amber-500/20 text-amber-700 dark:text-amber-400"
                      : "bg-primary/20 text-primary"
                    : "bg-foreground/[0.06] text-muted-foreground"
                )}
              >
                <Icon className="h-4 w-4" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span
                    className={cn(
                      "text-sm font-medium",
                      isSelected ? "text-foreground" : "text-muted-foreground"
                    )}
                  >
                    {pkg.label}
                  </span>
                  {pkg.default && !isFullAccess && (
                    <span className="text-[10px] font-medium uppercase tracking-wider text-primary/60 bg-primary/10 px-1.5 py-0.5 rounded">
                      Default
                    </span>
                  )}
                </div>
                <p className="text-xs text-muted-foreground/70 mt-0.5">
                  {pkg.description}
                </p>
              </div>
              <div
                className={cn(
                  "flex h-5 w-5 shrink-0 items-center justify-center rounded-md border transition-all mt-0.5",
                  isSelected
                    ? isFullAccess
                      ? "border-amber-500 bg-amber-500 text-white"
                      : "border-primary bg-primary text-white"
                    : "border-foreground/20"
                )}
              >
                {isSelected && <Check className="h-3 w-3" />}
              </div>
            </button>
          );
        })}

        {packages.length === 0 && (
          <div className="flex items-center justify-center py-6">
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground mr-2" />
            <span className="text-xs text-muted-foreground/50">Berechtigungspakete werden geladen...</span>
          </div>
        )}
      </div>

      <div className="px-5 pb-4">
        <p className="text-[11px] text-muted-foreground/50">
          Ohne Auswahl: nur pip/npm install (kein sudo). Basis-Tools (git, curl, node) sind immer verfuegbar.
        </p>
      </div>
    </div>
  );
}
