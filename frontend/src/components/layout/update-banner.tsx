"use client";

import { useEffect, useState, useCallback } from "react";
import { ArrowUpCircle, X, GitCommit, Clock, Loader2 } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import * as Dialog from "@radix-ui/react-dialog";
import { getBase } from "@/lib/config";

interface VersionInfo {
  current: string;
  latest: string | null;
  update_available: boolean;
}

interface Commit {
  sha: string;
  message: string;
  date: string;
  author: string;
}

const CHECK_INTERVAL = 30 * 60 * 1000; // 30 minutes

export function UpdateBanner() {
  const [version, setVersion] = useState<VersionInfo | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [commits, setCommits] = useState<Commit[]>([]);
  const [loadingChangelog, setLoadingChangelog] = useState(false);

  useEffect(() => {
    async function checkVersion() {
      try {
        const res = await fetch(`${getBase()}/version/`, {
          credentials: "include",
        });
        if (res.ok) {
          const data: VersionInfo = await res.json();
          setVersion(data);
        }
      } catch {
        // silently ignore
      }
    }

    checkVersion();
    const interval = setInterval(checkVersion, CHECK_INTERVAL);
    return () => clearInterval(interval);
  }, []);

  const fetchChangelog = useCallback(async () => {
    if (commits.length > 0) return; // already fetched
    setLoadingChangelog(true);
    try {
      const res = await fetch(`${getBase()}/version/changelog`, {
        credentials: "include",
      });
      if (res.ok) {
        const data = await res.json();
        setCommits(data.commits || []);
      }
    } catch {
      // ignore
    } finally {
      setLoadingChangelog(false);
    }
  }, [commits.length]);

  const handleBannerClick = () => {
    setModalOpen(true);
    fetchChangelog();
  };

  const show = version?.update_available && !dismissed;

  function formatDate(dateStr: string) {
    if (!dateStr) return "";
    const d = new Date(dateStr);
    return d.toLocaleDateString("de-DE", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  // Categorize commit message
  function getCommitType(msg: string): { label: string; color: string } {
    const lower = msg.toLowerCase();
    if (lower.startsWith("feat")) return { label: "Feature", color: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" };
    if (lower.startsWith("fix")) return { label: "Fix", color: "bg-red-500/10 text-red-400 border-red-500/20" };
    if (lower.startsWith("chore")) return { label: "Chore", color: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20" };
    if (lower.startsWith("refactor")) return { label: "Refactor", color: "bg-purple-500/10 text-purple-400 border-purple-500/20" };
    if (lower.startsWith("docs")) return { label: "Docs", color: "bg-blue-500/10 text-blue-400 border-blue-500/20" };
    if (lower.startsWith("style")) return { label: "Style", color: "bg-pink-500/10 text-pink-400 border-pink-500/20" };
    if (lower.startsWith("test")) return { label: "Test", color: "bg-yellow-500/10 text-yellow-400 border-yellow-500/20" };
    return { label: "Update", color: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20" };
  }

  // Strip conventional commit prefix for cleaner display
  function cleanMessage(msg: string): string {
    return msg.replace(/^(feat|fix|chore|refactor|docs|style|test|perf|ci|build)(\(.+?\))?:\s*/i, "");
  }

  return (
    <>
      <AnimatePresence>
        {show && (
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 10 }}
            className="mx-3 mb-2 flex items-center gap-2 rounded-xl border border-blue-500/20 bg-blue-500/10 px-3 py-2 cursor-pointer hover:bg-blue-500/15 transition-colors"
            onClick={handleBannerClick}
          >
            <ArrowUpCircle className="h-4 w-4 shrink-0 text-blue-400" />
            <div className="flex-1 min-w-0">
              <p className="text-[11px] font-medium text-blue-300">
                Neue Version verfügbar!
              </p>
              <p className="text-[10px] text-blue-400/70 truncate">
                {version!.current} → {version!.latest}
              </p>
            </div>
            <button
              onClick={(e) => {
                e.stopPropagation();
                setDismissed(true);
              }}
              className="shrink-0 rounded-lg p-1 text-blue-400/50 hover:text-blue-300 hover:bg-blue-500/10 transition-colors"
            >
              <X className="h-3 w-3" />
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Changelog Modal */}
      <Dialog.Root open={modalOpen} onOpenChange={setModalOpen}>
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
            >
              <div className="w-[560px] max-h-[80vh] rounded-2xl border border-foreground/[0.06] bg-card shadow-2xl shadow-black/40 flex flex-col pointer-events-auto">
              {/* Header */}
              <div className="flex items-center gap-3 border-b border-foreground/[0.06] px-6 py-4">
                <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-500/10">
                  <ArrowUpCircle className="h-5 w-5 text-blue-400" />
                </div>
                <div className="flex-1">
                  <Dialog.Title className="text-base font-semibold">
                    Update verfügbar
                  </Dialog.Title>
                  <p className="text-sm text-muted-foreground">
                    <span className="font-mono text-xs text-muted-foreground/70">{version?.current}</span>
                    <span className="mx-2 text-muted-foreground/40">→</span>
                    <span className="font-mono text-xs font-semibold text-blue-400">{version?.latest}</span>
                  </p>
                </div>
                <Dialog.Close className="rounded-lg p-2 text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-colors">
                  <X className="h-4 w-4" />
                </Dialog.Close>
              </div>

              {/* Changelog */}
              <div className="flex-1 overflow-y-auto px-6 py-4">
                <p className="mb-3 text-[11px] font-medium uppercase tracking-widest text-muted-foreground/60">
                  Changelog
                </p>

                {loadingChangelog ? (
                  <div className="flex items-center justify-center py-8">
                    <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                  </div>
                ) : commits.length === 0 ? (
                  <p className="py-8 text-center text-sm text-muted-foreground">
                    Keine Änderungen gefunden.
                  </p>
                ) : (
                  <div className="space-y-2">
                    {commits.map((commit) => {
                      const type = getCommitType(commit.message);
                      return (
                        <div
                          key={commit.sha}
                          className="flex items-start gap-3 rounded-lg border border-foreground/[0.04] bg-foreground/[0.02] px-3 py-2.5"
                        >
                          <GitCommit className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground/40" />
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 mb-0.5">
                              <span className={`inline-flex items-center rounded-full border px-1.5 py-0.5 text-[10px] font-medium ${type.color}`}>
                                {type.label}
                              </span>
                              <span className="font-mono text-[10px] text-muted-foreground/40">
                                {commit.sha}
                              </span>
                            </div>
                            <p className="text-[13px] text-foreground/90 leading-snug">
                              {cleanMessage(commit.message)}
                            </p>
                            <div className="flex items-center gap-2 mt-1">
                              <Clock className="h-3 w-3 text-muted-foreground/30" />
                              <span className="text-[10px] text-muted-foreground/50">
                                {formatDate(commit.date)}
                                {commit.author && ` · ${commit.author}`}
                              </span>
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>

              {/* Footer */}
              <div className="border-t border-foreground/[0.06] px-6 py-3 flex items-center justify-between">
                <p className="text-[11px] text-muted-foreground/50">
                  Update: <code className="font-mono text-[10px]">docker compose up --build</code>
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
    </>
  );
}
