"use client";

import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { motion } from "framer-motion";
import { X, KeyRound, Copy, Check } from "lucide-react";

interface Props {
  email: string;
  tempPassword: string;
  onClose: () => void;
}

export function ResetPasswordModal({ email, tempPassword, onClose }: Props) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard
      .writeText(tempPassword)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1800);
      })
      .catch(() => {});
  };

  return (
    <Dialog.Root open onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay asChild>
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }} className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm" />
        </Dialog.Overlay>
        <Dialog.Content asChild>
          <div
            className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none"
            onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
          >
            <motion.div
              initial={{ opacity: 0, scale: 0.95, y: 8 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.95, y: 8 }}
              transition={{ duration: 0.18 }}
              className="pointer-events-auto w-full max-w-md rounded-2xl border border-foreground/[0.08] bg-card shadow-2xl shadow-black/40 outline-none flex flex-col"
            >
              <div className="flex items-start justify-between gap-3 px-6 pt-5 pb-4 border-b border-foreground/[0.06]">
                <div className="flex items-center gap-3 min-w-0">
                  <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 shrink-0">
                    <KeyRound className="h-4.5 w-4.5 text-primary" />
                  </div>
                  <div className="min-w-0">
                    <Dialog.Title className="text-base font-semibold leading-tight">Neues Passwort</Dialog.Title>
                    <Dialog.Description className="text-xs text-muted-foreground mt-0.5 truncate">
                      {email}
                    </Dialog.Description>
                  </div>
                </div>
                <button onClick={onClose} className="rounded p-1 text-muted-foreground/50 hover:text-foreground">
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="px-6 py-5 space-y-3">
                <p className="text-xs text-amber-400">
                  Jetzt kopieren — dieses Passwort wird nur einmal angezeigt und danach nie wieder.
                </p>
                <div className="flex items-center gap-2">
                  <input
                    readOnly
                    value={tempPassword}
                    onFocus={(e) => e.currentTarget.select()}
                    className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm font-mono"
                  />
                  <button
                    onClick={handleCopy}
                    className="flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
                  >
                    {copied ? <Check className="h-4 w-4 text-emerald-400" /> : <Copy className="h-4 w-4" />}
                    {copied ? "Kopiert" : "Kopieren"}
                  </button>
                </div>
                <p className="text-xs text-muted-foreground">
                  Bitte sicher an den Nutzer weitergeben. Das alte Passwort ist ab sofort ungültig.
                </p>
              </div>
            </motion.div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
