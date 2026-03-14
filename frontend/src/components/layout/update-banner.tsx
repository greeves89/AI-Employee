"use client";

import { useEffect, useState } from "react";
import { ArrowUpCircle, X } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { getBase } from "@/lib/config";

interface VersionInfo {
  current: string;
  latest: string | null;
  update_available: boolean;
}

const CHECK_INTERVAL = 30 * 60 * 1000; // 30 minutes

export function UpdateBanner() {
  const [version, setVersion] = useState<VersionInfo | null>(null);
  const [dismissed, setDismissed] = useState(false);

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

  const show = version?.update_available && !dismissed;

  return (
    <AnimatePresence>
      {show && (
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 10 }}
          className="mx-3 mb-2 flex items-center gap-2 rounded-xl border border-blue-500/20 bg-blue-500/10 px-3 py-2"
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
            onClick={() => setDismissed(true)}
            className="shrink-0 rounded-lg p-1 text-blue-400/50 hover:text-blue-300 hover:bg-blue-500/10 transition-colors"
          >
            <X className="h-3 w-3" />
          </button>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
