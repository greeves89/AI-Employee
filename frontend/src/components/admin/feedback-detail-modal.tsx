"use client";

import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { motion } from "framer-motion";
import { X, Bug, Lightbulb, TrendingUp, MessageSquare, ThumbsUp, ThumbsDown, Loader2, Camera, ExternalLink } from "lucide-react";
import { Github } from "@/components/icons/github";
import * as api from "@/lib/api";
import type { Feedback } from "@/lib/types";
import { cn } from "@/lib/utils";
import { MarkdownContent } from "@/components/ui/markdown-content";

/** Die MD-Datei ist die Source of Truth (siehe orchestrator/app/api/feedback.py
 *  build_md), aber ihr YAML-Frontmatter und die eingebettete Screenshot-/Issue-
 *  Referenz stehen hier in der Modal schon als eigene Badges/Sektionen — roh
 *  mitgerendert sah das nur "technisch" aus (sichtbare "---"/"id:"-Zeilen).
 *  Beides rausschneiden, den Rest als echtes Markdown darstellen. */
function stripFrontmatterAndDuplicates(md: string): string {
  return md
    .replace(/^---\n[\s\S]*?\n---\n/, "")
    .replace(/^!\[Screenshot\]\([^)]*\)\n?/m, "")
    .trim();
}

const CATEGORY_ICONS: Record<string, typeof Bug> = {
  bug: Bug,
  feature: Lightbulb,
  improvement: TrendingUp,
  general: MessageSquare,
};

const CATEGORY_COLORS: Record<string, string> = {
  bug: "text-red-400",
  feature: "text-amber-700 dark:text-amber-400",
  improvement: "text-blue-400",
  general: "text-zinc-400",
};

const STATUS_COLORS: Record<string, string> = {
  pending: "text-amber-700 dark:text-amber-400 bg-amber-500/10 border-amber-500/20",
  reviewed: "text-blue-400 bg-blue-500/10 border-blue-500/20",
  in_progress: "text-violet-400 bg-violet-500/10 border-violet-500/20",
  closed: "text-zinc-400 bg-zinc-500/10 border-zinc-500/20",
};

const SENTIMENTS: Record<string, { label: string; icon: typeof ThumbsUp; color: string }> = {
  positiv: { label: "Gefällt mir", icon: ThumbsUp, color: "text-emerald-400" },
  negativ: { label: "Stört mich", icon: ThumbsDown, color: "text-orange-400" },
  wunsch: { label: "Wunsch", icon: Lightbulb, color: "text-amber-700 dark:text-amber-400" },
};

interface Props {
  feedback: Feedback;
  onClose: () => void;
}

export function FeedbackDetailModal({ feedback: f, onClose }: Props) {
  const [fullText, setFullText] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fid = f.md_file ? f.md_file.replace(/\.md$/, "") : null;

  useEffect(() => {
    if (!fid) return;
    setLoading(true);
    setError(null);
    api.getFeedbackItem(fid)
      .then((res) => setFullText(res.md))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [fid]);

  const CatIcon = CATEGORY_ICONS[f.category] || MessageSquare;
  const catColor = CATEGORY_COLORS[f.category] || "text-zinc-400";
  const statusColor = STATUS_COLORS[f.status] || STATUS_COLORS.pending;
  const sentiment = f.sentiment ? SENTIMENTS[f.sentiment] : undefined;

  return (
    <Dialog.Root open onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay asChild>
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }} className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm" />
        </Dialog.Overlay>
        <Dialog.Content asChild>
          {/* Radix zwingt hier pointer-events:auto per Inline-Style (ueberschreibt
              die pointer-events-none-Klasse) — dieser Wrapper faengt deshalb JEDEN
              Klick im Viewport ab, auch den auf den "Hintergrund". target===currentTarget
              unterscheidet Klick-auf-Hintergrund von Klick-auf-Karte (Bubbling). */}
          <div
            className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none"
            onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
          >
            <motion.div
              initial={{ opacity: 0, scale: 0.95, y: 8 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.95, y: 8 }}
              transition={{ duration: 0.18 }}
              className="pointer-events-auto w-full max-w-2xl max-h-[85vh] rounded-2xl border border-foreground/[0.08] bg-card shadow-2xl shadow-black/40 outline-none flex flex-col"
            >
              {/* Header */}
              <div className="flex items-start justify-between gap-3 px-6 pt-5 pb-4 border-b border-foreground/[0.06]">
                <div className="flex items-center gap-3 min-w-0">
                  <div className={cn("flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-foreground/[0.04]", catColor)}>
                    <CatIcon className="h-4.5 w-4.5" />
                  </div>
                  <div className="min-w-0">
                    <Dialog.Title className="text-base font-semibold leading-tight truncate">{f.title}</Dialog.Title>
                    <Dialog.Description className="text-xs text-muted-foreground mt-0.5">
                      {f.user_name || f.user_id} · {new Date(f.created_at).toLocaleString("de-DE")}
                    </Dialog.Description>
                  </div>
                </div>
                <button onClick={onClose} className="rounded p-1 text-muted-foreground/50 hover:text-foreground shrink-0">
                  <X className="h-4 w-4" />
                </button>
              </div>

              {/* Body */}
              <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
                {/* Badges */}
                <div className="flex items-center gap-2 flex-wrap">
                  <span className={cn("inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium", statusColor)}>
                    {f.status}
                  </span>
                  <span className="text-[10px] text-muted-foreground/50 capitalize">{f.category}</span>
                  {sentiment && (
                    <span className={cn("inline-flex items-center gap-1 text-[10px]", sentiment.color)}>
                      <sentiment.icon className="h-3 w-3" />
                      {sentiment.label}
                    </span>
                  )}
                  {f.page && <span className="text-[10px] font-mono text-muted-foreground/50">{f.page}</span>}
                  {f.element_label && (
                    <span className="text-[10px] text-muted-foreground/50" title={f.selector || undefined}>
                      Element: {f.element_label}
                    </span>
                  )}
                  {f.github_issue_url && (
                    <a href={f.github_issue_url} target="_blank" rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-[10px] text-primary hover:underline">
                      <Github className="h-3 w-3" />
                      Issue
                      <ExternalLink className="h-2.5 w-2.5" />
                    </a>
                  )}
                </div>

                {/* Full text: widget markdown if available, else the plain description */}
                {fid ? (
                  loading ? (
                    <div className="flex items-center justify-center py-8">
                      <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                    </div>
                  ) : error ? (
                    <p className="text-sm text-red-400">Volltext konnte nicht geladen werden: {error}</p>
                  ) : (
                    <MarkdownContent content={stripFrontmatterAndDuplicates(fullText || "")} />
                  )
                ) : f.description ? (
                  <p className="text-sm leading-relaxed text-foreground/90 whitespace-pre-wrap">{f.description}</p>
                ) : (
                  <p className="text-sm text-muted-foreground/50 italic">Keine Beschreibung vorhanden.</p>
                )}

                {/* Screenshot */}
                {f.screenshot_file && fid && (
                  <div className="space-y-1.5">
                    <p className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                      <Camera className="h-3.5 w-3.5" />
                      Screenshot
                    </p>
                    <a href={api.feedbackImageUrl(fid)} target="_blank" rel="noopener noreferrer">
                      <img
                        src={api.feedbackImageUrl(fid)}
                        alt="Feedback-Screenshot"
                        className="w-full rounded-lg border border-foreground/[0.08] hover:opacity-90 transition-opacity"
                      />
                    </a>
                  </div>
                )}

                {f.admin_notes && (
                  <div className="rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] px-3 py-2.5">
                    <p className="text-[11px] font-medium text-muted-foreground mb-1">Admin-Notizen</p>
                    <p className="text-sm text-foreground/80 whitespace-pre-wrap">{f.admin_notes}</p>
                  </div>
                )}
              </div>
            </motion.div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
