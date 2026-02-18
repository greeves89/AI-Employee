"use client";

import { useState, useEffect } from "react";
import { createPortal } from "react-dom";
import { motion, AnimatePresence } from "framer-motion";
import {
  X, Send, Loader2, CheckCircle2,
  Bug, Lightbulb, TrendingUp, MessageSquare,
} from "lucide-react";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import type { FeedbackCategory } from "@/lib/types";

const CATEGORIES: { id: FeedbackCategory; label: string; icon: typeof Bug; color: string }[] = [
  { id: "bug", label: "Bug", icon: Bug, color: "text-red-400 bg-red-500/10 border-red-500/20" },
  { id: "feature", label: "Feature", icon: Lightbulb, color: "text-amber-400 bg-amber-500/10 border-amber-500/20" },
  { id: "improvement", label: "Verbesserung", icon: TrendingUp, color: "text-blue-400 bg-blue-500/10 border-blue-500/20" },
  { id: "general", label: "Allgemein", icon: MessageSquare, color: "text-zinc-400 bg-zinc-500/10 border-zinc-500/20" },
];

interface FeedbackModalProps {
  open: boolean;
  onClose: () => void;
}

export function FeedbackModal({ open, onClose }: FeedbackModalProps) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState<FeedbackCategory>("general");
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  const handleSubmit = async () => {
    if (!title.trim()) return;
    setSubmitting(true);
    try {
      await api.createFeedback({
        title: title.trim(),
        description: description.trim() || undefined,
        category,
      });
      setSubmitted(true);
      setTimeout(() => {
        onClose();
        // Reset after close animation
        setTimeout(() => {
          setTitle("");
          setDescription("");
          setCategory("general");
          setSubmitted(false);
        }, 200);
      }, 1500);
    } catch {
      // ignore
    } finally {
      setSubmitting(false);
    }
  };

  if (!mounted) return null;

  return createPortal(
    <AnimatePresence>
      {open && (
        <>
          {/* Backdrop */}
          <motion.div
            className="fixed inset-0 z-[100] bg-black/50 backdrop-blur-sm"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
          />

          {/* Modal centering wrapper */}
          <div className="fixed inset-0 z-[100] flex items-center justify-center px-4 pointer-events-none">
          <motion.div
            className="w-full max-w-md pointer-events-auto"
            initial={{ opacity: 0, scale: 0.95, y: 10 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 10 }}
            transition={{ duration: 0.2 }}
          >
            <div className="rounded-2xl border border-border bg-card shadow-2xl overflow-hidden">
              {/* Header */}
              <div className="flex items-center justify-between px-5 py-4 border-b border-border">
                <div className="flex items-center gap-2.5">
                  <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
                    <MessageSquare className="h-4 w-4 text-primary" />
                  </div>
                  <div>
                    <h2 className="text-sm font-semibold">Feedback senden</h2>
                    <p className="text-[10px] text-muted-foreground">Hilf uns die Plattform zu verbessern</p>
                  </div>
                </div>
                <button
                  onClick={onClose}
                  className="flex h-7 w-7 items-center justify-center rounded-lg text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] transition-colors"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              {submitted ? (
                /* Success state */
                <div className="flex flex-col items-center justify-center py-12 px-5">
                  <motion.div
                    initial={{ scale: 0 }}
                    animate={{ scale: 1 }}
                    transition={{ type: "spring", stiffness: 300, damping: 20 }}
                  >
                    <CheckCircle2 className="h-12 w-12 text-emerald-400 mb-3" />
                  </motion.div>
                  <p className="text-sm font-medium text-emerald-400">Feedback gesendet!</p>
                  <p className="text-xs text-muted-foreground mt-1">Danke fuer dein Feedback.</p>
                </div>
              ) : (
                /* Form */
                <div className="p-5 space-y-4">
                  {/* Category */}
                  <div>
                    <label className="text-[11px] font-medium text-muted-foreground/70 mb-2 block">
                      Kategorie
                    </label>
                    <div className="grid grid-cols-4 gap-1.5">
                      {CATEGORIES.map((cat) => {
                        const Icon = cat.icon;
                        const active = category === cat.id;
                        return (
                          <button
                            key={cat.id}
                            onClick={() => setCategory(cat.id)}
                            className={cn(
                              "flex flex-col items-center gap-1 rounded-lg border px-2 py-2 text-[10px] font-medium transition-all",
                              active
                                ? cat.color
                                : "border-foreground/[0.06] text-muted-foreground hover:bg-foreground/[0.04]"
                            )}
                          >
                            <Icon className="h-3.5 w-3.5" />
                            {cat.label}
                          </button>
                        );
                      })}
                    </div>
                  </div>

                  {/* Title */}
                  <div>
                    <label className="text-[11px] font-medium text-muted-foreground/70 mb-1.5 block">
                      Titel
                    </label>
                    <input
                      type="text"
                      value={title}
                      onChange={(e) => setTitle(e.target.value)}
                      placeholder="Kurze Beschreibung..."
                      maxLength={200}
                      className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all placeholder:text-muted-foreground/25"
                      autoFocus
                    />
                  </div>

                  {/* Description */}
                  <div>
                    <label className="text-[11px] font-medium text-muted-foreground/70 mb-1.5 block">
                      Details (optional)
                    </label>
                    <textarea
                      value={description}
                      onChange={(e) => setDescription(e.target.value)}
                      placeholder="Was genau ist passiert? Was wuerdest du dir wuenschen?"
                      rows={3}
                      className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all placeholder:text-muted-foreground/25 resize-none"
                    />
                  </div>

                  {/* Submit */}
                  <button
                    onClick={handleSubmit}
                    disabled={!title.trim() || submitting}
                    className="w-full inline-flex items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 hover:bg-primary/90 disabled:opacity-40 disabled:shadow-none transition-all"
                  >
                    {submitting ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Send className="h-4 w-4" />
                    )}
                    {submitting ? "Wird gesendet..." : "Feedback senden"}
                  </button>
                </div>
              )}
            </div>
          </motion.div>
          </div>
        </>
      )}
    </AnimatePresence>,
    document.body
  );
}
