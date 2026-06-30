"use client";

import { useState, useEffect } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { motion, AnimatePresence } from "framer-motion";
import { X, Send, Loader2 } from "lucide-react";
import * as api from "@/lib/api";
import type { Team } from "@/lib/api";
import { useToast } from "@/components/ui/dialog-provider";

interface DelegateToTeamModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  team: Team | null;
  onDelegated?: () => void;
}

export function DelegateToTeamModal({
  open,
  onOpenChange,
  team,
  onDelegated,
}: DelegateToTeamModalProps) {
  const toast = useToast();

  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [priority, setPriority] = useState("5");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setTitle("");
      setPrompt("");
      setPriority("5");
      setError(null);
    }
  }, [open]);

  const handleDelegate = async () => {
    if (!team) return;
    if (!title.trim() || !prompt.trim()) {
      setError("Titel und Prompt sind erforderlich");
      return;
    }
    setSending(true);
    setError(null);
    try {
      const res = await api.delegateToTeam(team.id, {
        title: title.trim(),
        prompt: prompt.trim(),
        priority: parseInt(priority) || undefined,
      });
      toast.success("Task delegiert", `Task ${res.task_id}`);
      onOpenChange(false);
      onDelegated?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Task konnte nicht delegiert werden");
    } finally {
      setSending(false);
    }
  };

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <AnimatePresence>
        {open && (
          <Dialog.Portal forceMount>
            <Dialog.Overlay asChild>
              <motion.div
                className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.15 }}
              />
            </Dialog.Overlay>

            <Dialog.Content asChild>
              <motion.div
                className="fixed inset-0 z-50 flex items-center justify-center p-4"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2, ease: "easeOut" }}
              >
                <motion.div
                  className="w-full max-w-lg rounded-2xl border border-foreground/[0.08] bg-card shadow-2xl shadow-black/40 overflow-hidden max-h-[90vh] overflow-y-auto"
                  initial={{ scale: 0.95, y: 10 }}
                  animate={{ scale: 1, y: 0 }}
                  exit={{ scale: 0.95, y: 10 }}
                  transition={{ duration: 0.2, ease: "easeOut" }}
                >
                  {/* Header */}
                  <div className="flex items-center justify-between border-b border-foreground/[0.06] px-6 py-4">
                    <Dialog.Title className="text-lg font-semibold">
                      Task delegieren{team ? ` — ${team.name}` : ""}
                    </Dialog.Title>
                    <Dialog.Close className="rounded-lg p-1.5 text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] transition-colors">
                      <X className="h-4 w-4" />
                    </Dialog.Close>
                  </div>

                  {/* Body */}
                  <div className="px-6 py-5 space-y-5">
                    {!team?.lead_agent_id && (
                      <div className="rounded-lg border border-amber-500/20 bg-amber-500/10 px-4 py-2.5 text-xs text-amber-400">
                        Dieses Team hat keinen Lead. Lege zuerst einen Lead fest, damit der Task zugewiesen werden kann.
                      </div>
                    )}

                    {/* Title */}
                    <div>
                      <label className="block text-xs font-medium text-muted-foreground mb-1.5">
                        Titel <span className="text-red-400">*</span>
                      </label>
                      <input
                        type="text"
                        value={title}
                        onChange={(e) => setTitle(e.target.value)}
                        placeholder="z.B. Marktanalyse erstellen"
                        autoFocus
                        className="w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-4 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all"
                      />
                    </div>

                    {/* Prompt */}
                    <div>
                      <label className="block text-xs font-medium text-muted-foreground mb-1.5">
                        Prompt <span className="text-red-400">*</span>
                      </label>
                      <textarea
                        value={prompt}
                        onChange={(e) => setPrompt(e.target.value)}
                        placeholder="Beschreibe die Aufgabe fuer das Team..."
                        rows={5}
                        className="w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-4 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all resize-none"
                      />
                    </div>

                    {/* Priority */}
                    <div>
                      <label className="block text-xs font-medium text-muted-foreground mb-1.5">
                        Prioritaet <span className="text-muted-foreground/40">(optional)</span>
                      </label>
                      <select
                        value={priority}
                        onChange={(e) => setPriority(e.target.value)}
                        className="w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-4 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all"
                      >
                        <option value="1">1 — Niedrig</option>
                        <option value="5">5 — Normal</option>
                        <option value="10">10 — Hoch</option>
                      </select>
                    </div>

                    {/* Error */}
                    {error && (
                      <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
                        {error}
                      </div>
                    )}
                  </div>

                  {/* Footer */}
                  <div className="flex items-center justify-end gap-3 border-t border-foreground/[0.06] px-6 py-4 bg-foreground/[0.02]">
                    <Dialog.Close className="rounded-lg px-4 py-2.5 text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-all">
                      Abbrechen
                    </Dialog.Close>
                    <button
                      onClick={handleDelegate}
                      disabled={sending || !title.trim() || !prompt.trim()}
                      className="inline-flex items-center gap-2 rounded-lg bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground disabled:opacity-50 transition-all shadow-lg shadow-primary/20 hover:bg-primary/90"
                    >
                      {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                      {sending ? "Delegiere..." : "Delegieren"}
                    </button>
                  </div>
                </motion.div>
              </motion.div>
            </Dialog.Content>
          </Dialog.Portal>
        )}
      </AnimatePresence>
    </Dialog.Root>
  );
}
