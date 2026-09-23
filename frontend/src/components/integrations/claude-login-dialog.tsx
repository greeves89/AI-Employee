"use client";

// Claude-Login per eingefuegtem Code — gemeinsam fuer Einstellungen und Integrationen.
//
// Anthropic leitet nach dem Login NICHT zu uns zurueck, sondern zeigt den Code auf
// der eigenen Seite an ("code#state"). Ein normaler Redirect-Flow endet dort in
// einer Sackgasse. Deshalb: Login im neuen Tab oeffnen, Code hier einfuegen,
// serverseitig ueber /integrations/anthropic/exchange-code eintauschen.

import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { AlertCircle, CheckCircle2, Loader2 } from "lucide-react";
import * as api from "@/lib/api";

/**
 * Anthropic-Login im neuen Tab starten. Gibt den ``state`` zurueck, der als
 * Rueckfall dient, falls der eingefuegte Text keinen eigenen enthaelt.
 */
export async function startClaudeLogin(): Promise<string> {
  const { auth_url } = await api.getAuthUrl("anthropic");
  const state = new URL(auth_url).searchParams.get("state") || "";
  window.open(auth_url, "_blank");
  return state;
}

/**
 * {code, state} aus dem, was eingefuegt wird: die volle Callback-URL
 * (…?code=X&state=Y), Anthropics "code#state"-Form oder ein nackter Code. Der
 * mitgelieferte state hat Vorrang vor dem gemerkten — sonst gibt es "invalid
 * state", wenn mehrere Login-Tabs offen waren.
 */
export function parsePastedClaudeCode(raw: string): { code: string; state: string } {
  const v = raw.trim();
  try {
    if (/^https?:\/\//i.test(v)) {
      const u = new URL(v);
      return { code: u.searchParams.get("code") || "", state: u.searchParams.get("state") || "" };
    }
  } catch {
    /* keine URL */
  }
  if (v.includes("#")) {
    const [c, s] = v.split("#");
    return { code: c.trim(), state: (s || "").trim() };
  }
  return { code: v, state: "" };
}

interface ClaudeLoginDialogProps {
  open: boolean;
  /** state aus startClaudeLogin() — Rueckfall, wenn der Code keinen eigenen traegt. */
  authState: string;
  onClose: () => void;
  onConnected: () => void | Promise<void>;
}

export function ClaudeLoginDialog({ open, authState, onClose, onConnected }: ClaudeLoginDialogProps) {
  const [code, setCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (open) {
      setCode("");
      setError("");
    }
  }, [open]);

  if (!open) return null;

  const submit = async () => {
    if (!code.trim()) return;
    setLoading(true);
    setError("");
    try {
      const parsed = parsePastedClaudeCode(code);
      await api.exchangeOAuthCode("anthropic", parsed.code, parsed.state || authState);
      setCode("");
      onClose();
      await onConnected();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Code exchange failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        className="w-full max-w-md rounded-2xl border border-foreground/[0.08] bg-card p-6 shadow-2xl"
      >
        <h3 className="text-base font-semibold mb-1">Claude Login Code eingeben</h3>
        <p className="text-xs text-muted-foreground/60 mb-4">
          Ein neuer Tab wurde geöffnet. Logge dich dort ein und kopiere den angezeigten Code hierher.
        </p>

        <input
          type="text"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          placeholder="Code hier einfügen..."
          autoFocus
          className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-3 text-sm font-mono outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all placeholder:text-muted-foreground/25"
        />

        {error && (
          <p className="text-xs text-red-400 mt-2 flex items-center gap-1">
            <AlertCircle className="h-3 w-3" />
            {error}
          </p>
        )}

        <div className="flex gap-2 mt-4">
          <button
            onClick={submit}
            disabled={loading || !code.trim()}
            className="flex-1 inline-flex items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 hover:bg-primary/90 disabled:opacity-50 transition-all"
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
            Verbinden
          </button>
          <button
            onClick={() => {
              setCode("");
              setError("");
              onClose();
            }}
            className="rounded-xl px-4 py-2 text-sm text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-all"
          >
            Abbrechen
          </button>
        </div>
      </motion.div>
    </div>
  );
}
