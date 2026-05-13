"use client";

import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { motion, AnimatePresence } from "framer-motion";
import { X, Box, Loader2, Save } from "lucide-react";
import * as api from "@/lib/api";
import type { MountCatalogEntry, MountAccessGrant } from "@/lib/api";
import { useToast } from "@/components/ui/dialog-provider";
import { cn } from "@/lib/utils";

interface Props {
  userId: string;
  userName?: string;
  onClose: () => void;
}

export function MountPermissionsModal({ userId, userName, onClose }: Props) {
  const toast = useToast();
  const [catalog, setCatalog] = useState<MountCatalogEntry[]>([]);
  const [grants, setGrants] = useState<Record<string, "ro" | "rw" | "none">>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        // Catalog endpoint filters by caller — admin sees all
        const [cat, access] = await Promise.all([
          api.getAgentMountCatalog(),
          api.getUserMountAccess(userId),
        ]);
        setCatalog(cat.mounts);
        const map: Record<string, "ro" | "rw" | "none"> = {};
        cat.mounts.forEach((m) => { map[m.label] = "none"; });
        access.grants.forEach((g) => { map[g.mount_label] = g.mode; });
        setGrants(map);
      } catch (e) {
        toast.error("Mount-Permissions konnten nicht geladen werden", String(e));
      } finally {
        setLoading(false);
      }
    })();
  }, [userId, toast]);

  const handleSave = async () => {
    setSaving(true);
    try {
      const list: MountAccessGrant[] = Object.entries(grants)
        .filter(([, mode]) => mode !== "none")
        .map(([label, mode]) => ({ mount_label: label, mode: mode as "ro" | "rw" }));
      await api.setUserMountAccess(userId, list);
      toast.success("Mount-Permissions gespeichert", `${list.length} Mount(s) zugewiesen`);
      onClose();
    } catch (e) {
      toast.error("Speichern fehlgeschlagen", String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog.Root open onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay asChild>
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }} className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm" />
        </Dialog.Overlay>
        <Dialog.Content asChild>
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none">
            <motion.div
              initial={{ opacity: 0, scale: 0.95, y: 8 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.95, y: 8 }}
              transition={{ duration: 0.18 }}
              className="pointer-events-auto w-full max-w-lg max-h-[85vh] rounded-2xl border border-foreground/[0.08] bg-card shadow-2xl shadow-black/40 outline-none flex flex-col"
            >
              {/* Header */}
              <div className="flex items-start justify-between gap-3 px-6 pt-5 pb-4 border-b border-foreground/[0.06]">
                <div className="flex items-center gap-3 min-w-0">
                  <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 shrink-0">
                    <Box className="h-4.5 w-4.5 text-primary" />
                  </div>
                  <div className="min-w-0">
                    <Dialog.Title className="text-base font-semibold leading-tight">Mount-Permissions</Dialog.Title>
                    <Dialog.Description className="text-xs text-muted-foreground mt-0.5 truncate">
                      {userName ?? userId}
                    </Dialog.Description>
                  </div>
                </div>
                <button onClick={onClose} className="rounded p-1 text-muted-foreground/50 hover:text-foreground">
                  <X className="h-4 w-4" />
                </button>
              </div>

              {/* Body */}
              <div className="flex-1 overflow-y-auto px-6 py-4">
                {loading ? (
                  <div className="flex items-center justify-center py-12">
                    <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                  </div>
                ) : catalog.length === 0 ? (
                  <p className="text-center text-sm text-muted-foreground py-12">
                    Kein Mount-Katalog konfiguriert. Setze <code>AGENT_MOUNT_CATALOG</code> in der Server-Config.
                  </p>
                ) : (
                  <div className="space-y-2">
                    <p className="text-xs text-muted-foreground/70 mb-3">
                      Wähle pro Mount den maximalen Zugriff. Der User kann diese Mounts dann seinen Agents zuweisen.
                    </p>
                    {catalog.map((m) => (
                      <div key={m.label} className="flex items-center justify-between gap-3 rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] px-3 py-2.5">
                        <div className="min-w-0">
                          <p className="text-sm font-medium truncate">{m.label}</p>
                          <p className="text-[11px] text-muted-foreground/60 truncate font-mono">{m.container_path}</p>
                        </div>
                        <div className="flex items-center gap-1 shrink-0">
                          {(["none", "ro", "rw"] as const).map((mode) => (
                            <button
                              key={mode}
                              onClick={() => setGrants((g) => ({ ...g, [m.label]: mode }))}
                              className={cn(
                                "rounded-md px-2.5 py-1 text-[11px] font-medium uppercase tracking-wider transition-colors",
                                grants[m.label] === mode
                                  ? mode === "none"
                                    ? "bg-zinc-500/20 text-zinc-300"
                                    : mode === "ro"
                                    ? "bg-amber-500/20 text-amber-400"
                                    : "bg-emerald-500/20 text-emerald-400"
                                  : "text-muted-foreground/50 hover:text-foreground hover:bg-foreground/[0.04]"
                              )}
                            >
                              {mode === "none" ? "—" : mode}
                            </button>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Footer */}
              <div className="flex items-center justify-end gap-2 px-6 py-4 border-t border-foreground/[0.06]">
                <button onClick={onClose}
                  className="rounded-xl px-4 py-2 text-sm text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04]">
                  Abbrechen
                </button>
                <button onClick={handleSave} disabled={saving || loading}
                  className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 disabled:opacity-50">
                  {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                  Speichern
                </button>
              </div>
            </motion.div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
