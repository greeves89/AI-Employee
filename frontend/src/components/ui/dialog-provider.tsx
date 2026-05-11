"use client";

import { createContext, useCallback, useContext, useRef, useState, ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { motion, AnimatePresence } from "framer-motion";
import { AlertTriangle, Trash2, X, Info, CheckCircle2, XCircle, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

export type ConfirmVariant = "destructive" | "warning" | "default";

export interface ConfirmOptions {
  title: string;
  message?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: ConfirmVariant;
}

export type ToastVariant = "info" | "success" | "warning" | "error";

interface Toast {
  id: number;
  title: string;
  message?: string;
  variant: ToastVariant;
}

interface DialogContextValue {
  confirm: (opts: ConfirmOptions) => Promise<boolean>;
  toast: {
    info: (title: string, message?: string) => void;
    success: (title: string, message?: string) => void;
    warning: (title: string, message?: string) => void;
    error: (title: string, message?: string) => void;
  };
}

const DialogContext = createContext<DialogContextValue | null>(null);

// ─────────────────────────────────────────────────────────────────────────────
// Provider
// ─────────────────────────────────────────────────────────────────────────────

interface PendingConfirm {
  options: ConfirmOptions;
  resolve: (ok: boolean) => void;
}

export function DialogProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<PendingConfirm | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const confirm = useCallback((options: ConfirmOptions) => {
    return new Promise<boolean>((resolve) => {
      setPending({ options, resolve });
    });
  }, []);

  const closeConfirm = useCallback((ok: boolean) => {
    setPending((p) => {
      if (p) p.resolve(ok);
      return null;
    });
  }, []);

  const pushToast = useCallback((variant: ToastVariant, title: string, message?: string) => {
    const id = nextId.current++;
    setToasts((t) => [...t, { id, title, message, variant }]);
    // Auto-dismiss after 5s (8s for errors)
    const ttl = variant === "error" ? 8000 : 5000;
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), ttl);
  }, []);

  const dismissToast = useCallback((id: number) => {
    setToasts((t) => t.filter((x) => x.id !== id));
  }, []);

  const value: DialogContextValue = {
    confirm,
    toast: {
      info: (t, m) => pushToast("info", t, m),
      success: (t, m) => pushToast("success", t, m),
      warning: (t, m) => pushToast("warning", t, m),
      error: (t, m) => pushToast("error", t, m),
    },
  };

  return (
    <DialogContext.Provider value={value}>
      {children}
      <ConfirmDialog pending={pending} onClose={closeConfirm} />
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </DialogContext.Provider>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Hooks
// ─────────────────────────────────────────────────────────────────────────────

export function useDialogs(): DialogContextValue {
  const ctx = useContext(DialogContext);
  if (!ctx) throw new Error("useDialogs must be used inside <DialogProvider>");
  return ctx;
}

export function useConfirm() {
  return useDialogs().confirm;
}

export function useToast() {
  return useDialogs().toast;
}

// ─────────────────────────────────────────────────────────────────────────────
// Confirm Dialog
// ─────────────────────────────────────────────────────────────────────────────

const VARIANT_STYLES: Record<ConfirmVariant, {
  iconBg: string;
  iconColor: string;
  Icon: React.ElementType;
  confirmBg: string;
  defaultLabel: string;
}> = {
  destructive: {
    iconBg: "bg-red-500/10",
    iconColor: "text-red-400",
    Icon: Trash2,
    confirmBg: "bg-red-500 hover:bg-red-600 shadow-red-500/30",
    defaultLabel: "Delete",
  },
  warning: {
    iconBg: "bg-amber-500/10",
    iconColor: "text-amber-400",
    Icon: AlertTriangle,
    confirmBg: "bg-amber-500 hover:bg-amber-600 shadow-amber-500/30 text-amber-950",
    defaultLabel: "Confirm",
  },
  default: {
    iconBg: "bg-primary/10",
    iconColor: "text-primary",
    Icon: AlertCircle,
    confirmBg: "bg-primary hover:bg-primary/90 shadow-primary/30",
    defaultLabel: "OK",
  },
};

function ConfirmDialog({ pending, onClose }: { pending: PendingConfirm | null; onClose: (ok: boolean) => void }) {
  const opts = pending?.options;
  const variant = opts?.variant ?? "default";
  const style = VARIANT_STYLES[variant];
  const Icon = style.Icon;

  return (
    <Dialog.Root open={!!pending} onOpenChange={(open) => !open && onClose(false)}>
      <AnimatePresence>
        {pending && (
          <Dialog.Portal forceMount>
            <Dialog.Overlay asChild>
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.15 }}
                className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm"
              />
            </Dialog.Overlay>
            <Dialog.Content asChild>
              <div className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none">
                <motion.div
                  initial={{ opacity: 0, scale: 0.95, y: 8 }}
                  animate={{ opacity: 1, scale: 1, y: 0 }}
                  exit={{ opacity: 0, scale: 0.95, y: 8 }}
                  transition={{ duration: 0.18 }}
                  className="pointer-events-auto w-full max-w-md rounded-2xl border border-foreground/[0.08] bg-card shadow-2xl shadow-black/40 outline-none"
                >
                  <div className="p-6">
                  <div className="flex items-start gap-4">
                    <div className={cn("flex h-10 w-10 shrink-0 items-center justify-center rounded-xl", style.iconBg)}>
                      <Icon className={cn("h-5 w-5", style.iconColor)} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <Dialog.Title className="text-base font-semibold leading-tight">{opts?.title}</Dialog.Title>
                      {opts?.message && (
                        <Dialog.Description className="mt-1.5 text-sm text-muted-foreground leading-relaxed">
                          {opts.message}
                        </Dialog.Description>
                      )}
                    </div>
                  </div>

                  <div className="mt-6 flex items-center justify-end gap-2">
                    <button
                      onClick={() => onClose(false)}
                      className="rounded-xl px-4 py-2 text-sm text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-colors"
                    >
                      {opts?.cancelLabel ?? "Cancel"}
                    </button>
                    <button
                      onClick={() => onClose(true)}
                      autoFocus
                      className={cn(
                        "rounded-xl px-4 py-2 text-sm font-medium text-white shadow-lg transition-colors",
                        style.confirmBg
                      )}
                    >
                      {opts?.confirmLabel ?? style.defaultLabel}
                    </button>
                  </div>
                  </div>
                </motion.div>
              </div>
            </Dialog.Content>
          </Dialog.Portal>
        )}
      </AnimatePresence>
    </Dialog.Root>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Toast
// ─────────────────────────────────────────────────────────────────────────────

const TOAST_STYLES: Record<ToastVariant, { icon: React.ElementType; color: string; bg: string; ring: string }> = {
  info: { icon: Info, color: "text-sky-400", bg: "bg-sky-500/10", ring: "ring-sky-500/20" },
  success: { icon: CheckCircle2, color: "text-emerald-400", bg: "bg-emerald-500/10", ring: "ring-emerald-500/20" },
  warning: { icon: AlertTriangle, color: "text-amber-400", bg: "bg-amber-500/10", ring: "ring-amber-500/20" },
  error: { icon: XCircle, color: "text-red-400", bg: "bg-red-500/10", ring: "ring-red-500/20" },
};

function ToastContainer({ toasts, onDismiss }: { toasts: Toast[]; onDismiss: (id: number) => void }) {
  return (
    <div className="fixed bottom-4 right-4 z-[60] flex flex-col items-end gap-2 pointer-events-none">
      <AnimatePresence>
        {toasts.map((t) => {
          const s = TOAST_STYLES[t.variant];
          const Icon = s.icon;
          return (
            <motion.div
              key={t.id}
              layout
              initial={{ opacity: 0, x: 24, scale: 0.95 }}
              animate={{ opacity: 1, x: 0, scale: 1 }}
              exit={{ opacity: 0, x: 24, scale: 0.95 }}
              transition={{ duration: 0.18 }}
              className={cn(
                "pointer-events-auto flex w-80 max-w-[92vw] items-start gap-3 rounded-xl border border-foreground/[0.08] bg-card/95 backdrop-blur-md p-3 shadow-xl shadow-black/30 ring-1",
                s.ring
              )}
            >
              <div className={cn("flex h-7 w-7 shrink-0 items-center justify-center rounded-lg", s.bg)}>
                <Icon className={cn("h-4 w-4", s.color)} />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium leading-snug truncate">{t.title}</p>
                {t.message && <p className="mt-0.5 text-xs text-muted-foreground leading-snug">{t.message}</p>}
              </div>
              <button
                onClick={() => onDismiss(t.id)}
                className="shrink-0 rounded p-1 text-muted-foreground/50 hover:text-foreground hover:bg-foreground/[0.06]"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}
