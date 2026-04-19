"use client";

import { useCallback, useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Monitor,
  Globe,
  Download,
  Apple,
  Plus,
  Trash2,
  RefreshCw,
  Wifi,
  WifiOff,
  Copy,
  Check,
  Terminal,
  AlertCircle,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  listComputerUseSessions,
  createComputerUseSession,
  deleteComputerUseSession,
  updateAgentBrowserMode,
  type ComputerUseSession,
} from "@/lib/api";
import { getApiUrl } from "@/lib/config";

interface Props {
  agentId: string;
  browserMode?: boolean;
}

export function ComputerUseTab({ agentId, browserMode: initialBrowserMode = false }: Props) {
  const [sessions, setSessions] = useState<ComputerUseSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [showInstall, setShowInstall] = useState(false);
  const [browserMode, setBrowserMode] = useState(initialBrowserMode);
  const [togglingBrowser, setTogglingBrowser] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const data = await listComputerUseSessions();
      setSessions(data.sessions ?? []);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);

  const handleCreate = async () => {
    setCreating(true);
    try {
      await createComputerUseSession();
      await refresh();
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (sessionId: string) => {
    try {
      await deleteComputerUseSession(sessionId);
      setSessions((prev) => prev.filter((s) => s.session_id !== sessionId));
    } catch {
      // ignore
    }
  };

  const copyToClipboard = async (text: string, id: string) => {
    await navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const handleToggleBrowser = async () => {
    setTogglingBrowser(true);
    try {
      await updateAgentBrowserMode(agentId, !browserMode);
      setBrowserMode((prev) => !prev);
    } catch {
      // ignore
    } finally {
      setTogglingBrowser(false);
    }
  };

  const baseUrl = getApiUrl().replace(/\/$/, "");
  const wsBase = baseUrl.replace(/^http/, "ws");

  return (
    <div className="h-full overflow-y-auto space-y-4 pr-1">

      {/* Phase 1: Playwright Browser Control toggle */}
      <div className="rounded-xl border border-foreground/[0.08] bg-foreground/[0.02] p-4">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className={cn(
              "flex h-9 w-9 items-center justify-center rounded-lg shrink-0",
              browserMode ? "bg-blue-500/10" : "bg-foreground/[0.06]"
            )}>
              <Globe className={cn("h-4.5 w-4.5", browserMode ? "text-blue-400" : "text-muted-foreground")} />
            </div>
            <div>
              <p className="text-sm font-medium text-foreground">Browser Automation (Playwright)</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                Enables <code className="text-blue-400">browser_navigate</code>, <code className="text-blue-400">browser_click</code>,{" "}
                <code className="text-blue-400">browser_screenshot</code> and more — no screen-share required.
                Runs headlessly inside the agent container.
              </p>
            </div>
          </div>
          <button
            onClick={handleToggleBrowser}
            disabled={togglingBrowser}
            className={cn(
              "relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none disabled:opacity-50",
              browserMode ? "bg-blue-500" : "bg-foreground/[0.15]"
            )}
            role="switch"
            aria-checked={browserMode}
          >
            <span className={cn(
              "pointer-events-none inline-block h-5 w-5 rounded-full bg-white shadow transform transition-transform duration-200",
              browserMode ? "translate-x-5" : "translate-x-0"
            )} />
          </button>
        </div>
        {browserMode && (
          <div className="mt-3 flex items-start gap-2 rounded-lg bg-blue-500/5 border border-blue-500/20 px-3 py-2">
            <AlertCircle className="h-3.5 w-3.5 text-blue-400 shrink-0 mt-0.5" />
            <p className="text-xs text-blue-400/80">
              Browser mode is <strong>enabled</strong>. Restart the agent to activate Playwright MCP.
            </p>
          </div>
        )}
      </div>

      {/* Download Bridge App */}
      <div className="rounded-xl border border-foreground/[0.08] bg-foreground/[0.02] p-4">
        <div className="flex items-center gap-3 mb-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-foreground/[0.06] shrink-0">
            <Download className="h-4 w-4 text-muted-foreground" />
          </div>
          <div>
            <p className="text-sm font-medium text-foreground">Download Bridge App</p>
            <p className="text-xs text-muted-foreground mt-0.5">
              Runs in your menu bar / system tray. Connects this server to your desktop.
            </p>
          </div>
        </div>
        <div className="flex gap-2 flex-wrap">
          <a
            href={`${baseUrl}/api/v1/download/bridge/mac`}
            className="inline-flex items-center gap-2 rounded-lg bg-foreground/[0.06] border border-foreground/[0.08] px-3.5 py-2 text-xs font-medium text-foreground hover:bg-foreground/[0.1] transition-all"
          >
            <Apple className="h-3.5 w-3.5" />
            macOS (.dmg)
          </a>
          <a
            href={`${baseUrl}/api/v1/download/bridge/windows`}
            className="inline-flex items-center gap-2 rounded-lg bg-foreground/[0.06] border border-foreground/[0.08] px-3.5 py-2 text-xs font-medium text-foreground hover:bg-foreground/[0.1] transition-all"
          >
            <Monitor className="h-3.5 w-3.5" />
            Windows (.exe)
          </a>
        </div>
        <p className="text-[10px] text-muted-foreground mt-2.5">
          Immer die neueste Version — direkt vom Server.
        </p>
      </div>

      {/* Phase 2/3: Desktop bridge header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-foreground">Desktop Bridge</h3>
          <p className="text-xs text-muted-foreground mt-0.5">
            Connect your Mac or Windows machine so agents can control your desktop.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={refresh}
            className="inline-flex items-center gap-1.5 rounded-lg border border-foreground/[0.1] px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground hover:border-foreground/[0.2] hover:bg-foreground/[0.04] transition-all"
          >
            <RefreshCw className="h-3 w-3" />
            Refresh
          </button>
          <button
            onClick={handleCreate}
            disabled={creating}
            className="inline-flex items-center gap-1.5 rounded-lg bg-violet-500/10 border border-violet-500/20 px-3 py-1.5 text-xs font-medium text-violet-400 hover:bg-violet-500/20 transition-all disabled:opacity-50"
          >
            <Plus className={cn("h-3 w-3", creating && "animate-spin")} />
            New Session
          </button>
        </div>
      </div>

      {/* Sessions list */}
      {loading ? (
        <div className="space-y-2">
          {[1, 2].map((i) => (
            <div
              key={i}
              className="h-20 rounded-xl animate-pulse bg-foreground/[0.04] border border-foreground/[0.06]"
            />
          ))}
        </div>
      ) : sessions.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-foreground/[0.1] py-12 gap-3">
          <Monitor className="h-8 w-8 text-muted-foreground/40" />
          <div className="text-center">
            <p className="text-sm font-medium text-muted-foreground">No sessions yet</p>
            <p className="text-xs text-muted-foreground/60 mt-1">
              Create a session, then connect the bridge app on your machine.
            </p>
          </div>
          <button
            onClick={handleCreate}
            disabled={creating}
            className="inline-flex items-center gap-1.5 rounded-lg bg-violet-500/10 border border-violet-500/20 px-4 py-2 text-xs font-medium text-violet-400 hover:bg-violet-500/20 transition-all"
          >
            <Plus className="h-3.5 w-3.5" />
            Create Session
          </button>
        </div>
      ) : (
        <div className="space-y-2">
          <AnimatePresence initial={false}>
            {sessions.map((session) => (
              <SessionCard
                key={session.session_id}
                session={session}
                wsBase={wsBase}
                copiedId={copiedId}
                onCopy={copyToClipboard}
                onDelete={handleDelete}
              />
            ))}
          </AnimatePresence>
        </div>
      )}

      {/* Install instructions */}
      <div className="rounded-xl border border-foreground/[0.08] overflow-hidden">
        <button
          onClick={() => setShowInstall(!showInstall)}
          className="w-full flex items-center justify-between px-4 py-3 text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-foreground/[0.02] transition-all"
        >
          <div className="flex items-center gap-2">
            <Terminal className="h-3.5 w-3.5" />
            Setup instructions — bridge app on your machine
          </div>
          {showInstall ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
        </button>
        <AnimatePresence>
          {showInstall && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              className="overflow-hidden"
            >
              <div className="px-4 pb-4 space-y-3 border-t border-foreground/[0.06]">
                <p className="text-xs text-muted-foreground mt-3">
                  The bridge app runs on your Mac or Windows PC and relays desktop control commands to your agents.
                </p>

                <InstallStep step={1} title="Download the bridge">
                  <CodeBlock>
                    {`git clone ${baseUrl} && cd ai-employee/computer-use-bridge`}
                  </CodeBlock>
                  <p className="text-xs text-muted-foreground">
                    Or download{" "}
                    <span className="font-mono text-violet-400">computer-use-bridge/</span>
                    {" "}from the repository.
                  </p>
                </InstallStep>

                <InstallStep step={2} title="Install and start (macOS, auto-start on login)">
                  <CodeBlock>{`AI_EMPLOYEE_URL=${baseUrl} AI_EMPLOYEE_TOKEN=<your-token> bash install.sh`}</CodeBlock>
                </InstallStep>

                <InstallStep step={3} title="Or run manually">
                  <CodeBlock>
                    {`pip install -r requirements.txt
python bridge.py --url ${wsBase} --token <your-token>`}
                  </CodeBlock>
                </InstallStep>

                <InstallStep step={4} title="Grant Accessibility permission (macOS)">
                  <p className="text-xs text-muted-foreground">
                    System Settings → Privacy & Security → Accessibility → Add Terminal or Python.
                    Required for AX tree and input control.
                  </p>
                </InstallStep>

                <div className="flex items-start gap-2 rounded-lg bg-amber-500/5 border border-amber-500/20 px-3 py-2.5">
                  <AlertCircle className="h-3.5 w-3.5 text-amber-400 shrink-0 mt-0.5" />
                  <p className="text-xs text-amber-400/80">
                    The bridge can control your mouse, keyboard, and read the screen. Only connect
                    when you trust the agent and want to grant desktop access.
                  </p>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

function SessionCard({
  session,
  wsBase,
  copiedId,
  onCopy,
  onDelete,
}: {
  session: ComputerUseSession;
  wsBase: string;
  copiedId: string | null;
  onCopy: (text: string, id: string) => void;
  onDelete: (id: string) => void;
}) {
  const isConnected = session.status === "connected";
  const wsUrl = `${wsBase}/ws/computer-use/bridge?session_id=${session.session_id}`;
  const copyKey = `ws-${session.session_id}`;

  return (
    <motion.div
      initial={{ opacity: 0, y: -4 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.98 }}
      className="rounded-xl border border-foreground/[0.08] bg-foreground/[0.02] p-4 space-y-3"
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div
            className={cn(
              "flex h-8 w-8 items-center justify-center rounded-lg",
              isConnected ? "bg-emerald-500/10" : "bg-amber-500/10"
            )}
          >
            {isConnected ? (
              <Wifi className="h-4 w-4 text-emerald-400" />
            ) : (
              <WifiOff className="h-4 w-4 text-amber-400" />
            )}
          </div>
          <div>
            <p className="text-sm font-medium text-foreground font-mono">{session.session_id}</p>
            <div className="flex items-center gap-1.5 mt-0.5">
              <span
                className={cn(
                  "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium border",
                  isConnected
                    ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                    : "bg-amber-500/10 text-amber-400 border-amber-500/20"
                )}
              >
                <span
                  className={cn(
                    "h-1.5 w-1.5 rounded-full",
                    isConnected ? "bg-emerald-400" : "bg-amber-400"
                  )}
                />
                {isConnected ? "Bridge connected" : "Waiting for bridge"}
              </span>
            </div>
          </div>
        </div>
        <button
          onClick={() => onDelete(session.session_id)}
          className="p-1.5 rounded-lg text-muted-foreground hover:text-red-400 hover:bg-red-400/10 transition-all"
          title="Delete session"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* WS URL to give to bridge app */}
      <div className="space-y-1">
        <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">
          Bridge connection URL
        </p>
        <div className="flex items-center gap-2 rounded-lg bg-foreground/[0.04] border border-foreground/[0.06] px-3 py-2">
          <code className="flex-1 text-[11px] text-foreground/80 truncate">{wsUrl}</code>
          <button
            onClick={() => onCopy(wsUrl, copyKey)}
            className="shrink-0 p-1 rounded text-muted-foreground hover:text-foreground transition-colors"
            title="Copy URL"
          >
            {copiedId === copyKey ? (
              <Check className="h-3.5 w-3.5 text-emerald-400" />
            ) : (
              <Copy className="h-3.5 w-3.5" />
            )}
          </button>
        </div>
        <p className="text-[10px] text-muted-foreground">
          Pass this to the bridge app:{" "}
          <code className="text-violet-400">python bridge.py --url ... --session {session.session_id}</code>
        </p>
      </div>
    </motion.div>
  );
}

function InstallStep({
  step,
  title,
  children,
}: {
  step: number;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <span className="flex h-5 w-5 items-center justify-center rounded-full bg-violet-500/10 text-[10px] font-bold text-violet-400 border border-violet-500/20">
          {step}
        </span>
        <p className="text-xs font-medium text-foreground">{title}</p>
      </div>
      <div className="ml-7 space-y-1.5">{children}</div>
    </div>
  );
}

function CodeBlock({ children }: { children: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(children);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <div className="flex items-start gap-2 rounded-lg bg-foreground/[0.04] border border-foreground/[0.06] px-3 py-2 group">
      <pre className="flex-1 text-[11px] text-foreground/80 whitespace-pre-wrap break-all font-mono leading-relaxed">
        {children}
      </pre>
      <button
        onClick={copy}
        className="shrink-0 p-1 rounded text-muted-foreground opacity-0 group-hover:opacity-100 hover:text-foreground transition-all"
      >
        {copied ? <Check className="h-3 w-3 text-emerald-400" /> : <Copy className="h-3 w-3" />}
      </button>
    </div>
  );
}
