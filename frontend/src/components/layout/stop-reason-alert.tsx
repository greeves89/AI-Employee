"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, HardDrive, X } from "lucide-react";
import { motion } from "framer-motion";
import * as Dialog from "@radix-ui/react-dialog";
import Link from "next/link";
import * as api from "@/lib/api";
import type { Agent, AgentStopReason } from "@/lib/types";

// Einmal pro Login zeigen, nicht bei jedem Seitenwechsel innerhalb derselben
// Sitzung nerven — der Grund bleibt trotzdem in der Agenten-Uebersicht sichtbar
// (Badge auf der Karte), das Popup hier ist nur der einmalige Weckruf.
const SESSION_KEY = "stopReasonAlertShown";

export function StopReasonAlert() {
  const [affected, setAffected] = useState<{ agent: Agent; reason: AgentStopReason }[]>([]);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (sessionStorage.getItem(SESSION_KEY)) return;

    let cancelled = false;
    api.getAgents("own").then(({ agents }) => {
      if (cancelled) return;
      const hits = agents
        .filter((a) => a.config?.stop_reason)
        .map((a) => ({ agent: a, reason: a.config!.stop_reason as AgentStopReason }));
      if (hits.length > 0) {
        setAffected(hits);
        setOpen(true);
      }
      sessionStorage.setItem(SESSION_KEY, "1");
    }).catch(() => {
      // Keine Meldung ist besser als ein kaputtes Popup — beim naechsten Login erneut versucht.
    });
    return () => { cancelled = true; };
  }, []);

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Portal>
        <Dialog.Overlay asChild>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm"
          />
        </Dialog.Overlay>
        <Dialog.Content asChild>
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 20 }}
            transition={{ type: "spring", duration: 0.4, bounce: 0.15 }}
            className="fixed inset-0 z-50 ml-[130px] flex items-center justify-center pointer-events-none"
            onClick={(e) => { if (e.target === e.currentTarget) setOpen(false); }}
          >
            <div className="w-[520px] max-h-[80vh] rounded-2xl border border-red-500/20 bg-card shadow-2xl shadow-black/40 flex flex-col pointer-events-auto">
              <div className="flex items-center gap-3 border-b border-foreground/[0.06] px-6 py-4">
                <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-red-500/10">
                  <AlertTriangle className="h-5 w-5 text-red-700 dark:text-red-400" />
                </div>
                <div className="flex-1">
                  <Dialog.Title className="text-base font-semibold">
                    {affected.length === 1 ? "Ein Agent wurde angehalten" : `${affected.length} Agenten wurden angehalten`}
                  </Dialog.Title>
                  <p className="text-sm text-muted-foreground">Speicherplatz voll — manuelles Aufräumen nötig</p>
                </div>
                <Dialog.Close className="rounded-lg p-2 text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-colors">
                  <X className="h-4 w-4" />
                </Dialog.Close>
              </div>

              <div className="flex-1 overflow-y-auto px-6 py-4 space-y-3">
                {affected.map(({ agent, reason }) => (
                  <Link
                    key={agent.id}
                    href={`/agents/${agent.id}`}
                    onClick={() => setOpen(false)}
                    className="block rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] px-3 py-2.5 hover:border-red-500/30 transition-colors"
                  >
                    <div className="flex items-center gap-2 mb-1">
                      <HardDrive className="h-3.5 w-3.5 shrink-0 text-red-700 dark:text-red-400" />
                      <span className="text-[13px] font-medium text-foreground/90">{agent.name}</span>
                      <span className="ml-auto text-[11px] font-mono text-red-700 dark:text-red-400">{reason.disk_percent}%</span>
                    </div>
                    <p className="text-[12px] text-muted-foreground/70 leading-snug">{reason.detail}</p>
                  </Link>
                ))}
              </div>

              <div className="border-t border-foreground/[0.06] px-6 py-3 flex items-center justify-between">
                <p className="text-[11px] text-muted-foreground/50">
                  Aufräumen im Workspace, dann Agent neu starten.
                </p>
                <Dialog.Close className="rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 hover:bg-primary/90 transition-colors">
                  Verstanden
                </Dialog.Close>
              </div>
            </div>
          </motion.div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
