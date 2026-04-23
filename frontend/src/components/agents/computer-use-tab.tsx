"use client";

import { useCallback, useEffect, useRef, useState } from "react";
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
  Eye,
  EyeOff,
  Loader2,
  Shield,
  ShieldOff,
  MousePointer2,
  Keyboard,
  Camera,
  FolderOpen,
  Clipboard,
  Terminal as TerminalIcon,
  Settings2,
  Activity,
  WifiIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  listComputerUseSessions,
  createComputerUseSession,
  deleteComputerUseSession,
  updateAgentBrowserMode,
  getComputerUseScreenshot,
  updateSessionCapabilities,
  type ComputerUseSession,
} from "@/lib/api";
import { getApiUrl } from "@/lib/config";

interface Props {
  agentId: string;
  browserMode?: boolean;
}

// ── Capability group metadata ─────────────────────────────────────────────────

interface CapabilityMeta {
  id: string;
  label: string;
  description: string;
  icon: React.ReactNode;
  risk: "low" | "medium" | "high";
  defaultOn: boolean;
}

const CAPABILITY_META: CapabilityMeta[] = [
  {
    id: "screenshots",
    label: "Screenshots",
    description: "Capture your screen to observe what's happening",
    icon: <Camera className="h-3.5 w-3.5" />,
    risk: "low",
    defaultOn: true,
  },
  {
    id: "accessibility",
    label: "Accessibility Tree",
    description: "Read UI element hierarchy (titles, roles, positions)",
    icon: <Eye className="h-3.5 w-3.5" />,
    risk: "low",
    defaultOn: true,
  },
  {
    id: "mouse",
    label: "Mouse Control",
    description: "Move cursor, click, scroll, and drag",
    icon: <MousePointer2 className="h-3.5 w-3.5" />,
    risk: "medium",
    defaultOn: true,
  },
  {
    id: "keyboard",
    label: "Keyboard Input",
    description: "Type text and press keyboard shortcuts",
    icon: <Keyboard className="h-3.5 w-3.5" />,
    risk: "medium",
    defaultOn: true,
  },
  {
    id: "apps",
    label: "App Control",
    description: "Open and close applications",
    icon: <FolderOpen className="h-3.5 w-3.5" />,
    risk: "medium",
    defaultOn: true,
  },
  {
    id: "clipboard",
    label: "Clipboard Access",
    description: "Read and write clipboard contents",
    icon: <Clipboard className="h-3.5 w-3.5" />,
    risk: "medium",
    defaultOn: false,
  },
  {
    id: "shell",
    label: "Shell Commands",
    description: "Execute terminal commands on your machine",
    icon: <TerminalIcon className="h-3.5 w-3.5" />,
    risk: "high",
    defaultOn: false,
  },
];

const RISK_COLORS = {
  low: "text-emerald-400",
  medium: "text-amber-400",
  high: "text-red-400",
};

// ── Main component ────────────────────────────────────────────────────────────

export function ComputerUseTab({ agentId, browserMode: initialBrowserMode = false }: Props) {
  const [sessions, setSessions] = useState<ComputerUseSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [browserMode, setBrowserMode] = useState(initialBrowserMode);
  const [togglingBrowser, setTogglingBrowser] = useState(false);
  const [showInstall, setShowInstall] = useState(false);

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
    const t = setInterval(refresh, 3000);
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

  const handleCapabilitiesChange = async (sessionId: string, caps: string[]) => {
    try {
      await updateSessionCapabilities(sessionId, caps);
      setSessions((prev) =>
        prev.map((s) =>
          s.session_id === sessionId ? { ...s, allowed_capabilities: caps } : s
        )
      );
    } catch {
      // ignore
    }
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

      {/* Browser Automation toggle */}
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
                Headless browser inside the container —{" "}
                <code className="text-blue-400">browser_navigate</code>,{" "}
                <code className="text-blue-400">browser_click</code>,{" "}
                <code className="text-blue-400">browser_screenshot</code> and more. No screen-share needed.
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
            <p className="text-sm font-medium text-foreground">Bridge App herunterladen</p>
            <p className="text-xs text-muted-foreground mt-0.5">
              Läuft in der Menüleiste / im System-Tray. Verbindet diesen Server mit deinem Desktop.
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
          Die App enthält alle Berechtigungen — du siehst im Tray-Menü genau, was erlaubt ist.
        </p>
      </div>

      {/* Desktop Bridge sessions */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-foreground">Desktop Bridge</h3>
          <p className="text-xs text-muted-foreground mt-0.5">
            Verbinde Mac oder Windows — Agents können dann deinen Desktop steuern.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={refresh}
            className="inline-flex items-center gap-1.5 rounded-lg border border-foreground/[0.1] px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground hover:border-foreground/[0.2] hover:bg-foreground/[0.04] transition-all"
          >
            <RefreshCw className="h-3 w-3" />
          </button>
          <button
            onClick={handleCreate}
            disabled={creating}
            className="inline-flex items-center gap-1.5 rounded-lg bg-violet-500/10 border border-violet-500/20 px-3 py-1.5 text-xs font-medium text-violet-400 hover:bg-violet-500/20 transition-all disabled:opacity-50"
          >
            <Plus className={cn("h-3 w-3", creating && "animate-spin")} />
            Neue Session
          </button>
        </div>
      </div>

      {loading ? (
        <div className="space-y-2">
          {[1, 2].map((i) => (
            <div key={i} className="h-24 rounded-xl animate-pulse bg-foreground/[0.04] border border-foreground/[0.06]" />
          ))}
        </div>
      ) : sessions.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-foreground/[0.1] py-12 gap-3">
          <Monitor className="h-8 w-8 text-muted-foreground/40" />
          <div className="text-center">
            <p className="text-sm font-medium text-muted-foreground">Noch keine Sessions</p>
            <p className="text-xs text-muted-foreground/60 mt-1">
              Erstelle eine Session und verbinde danach die Bridge-App.
            </p>
          </div>
          <button
            onClick={handleCreate}
            disabled={creating}
            className="inline-flex items-center gap-1.5 rounded-lg bg-violet-500/10 border border-violet-500/20 px-4 py-2 text-xs font-medium text-violet-400 hover:bg-violet-500/20 transition-all"
          >
            <Plus className="h-3.5 w-3.5" />
            Session erstellen
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
                onDelete={handleDelete}
                onCapabilitiesChange={handleCapabilitiesChange}
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
            Einrichtung — Bridge-App auf deinem Rechner
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
                  Die Bridge-App läuft im Hintergrund auf Mac oder Windows und leitet Desktop-Steuerbefehle an deine Agents weiter.
                </p>
                <InstallStep step={1} title="Bridge herunterladen (s. oben) oder manuell:">
                  <CodeBlock>{`git clone ${baseUrl} && cd ai-employee/computer-use-bridge`}</CodeBlock>
                </InstallStep>
                <InstallStep step={2} title="Installieren und starten (macOS, Autostart beim Login)">
                  <CodeBlock>{`AI_EMPLOYEE_URL=${baseUrl} AI_EMPLOYEE_TOKEN=<dein-token> bash install.sh`}</CodeBlock>
                </InstallStep>
                <InstallStep step={3} title="Oder manuell starten">
                  <CodeBlock>{`pip install -r requirements.txt\npython tray_app.py`}</CodeBlock>
                </InstallStep>
                <InstallStep step={4} title="Accessibility-Berechtigung (macOS)">
                  <p className="text-xs text-muted-foreground">
                    Systemeinstellungen → Datenschutz & Sicherheit → Bedienungshilfen → Terminal oder Python hinzufügen.
                    Erforderlich für AX-Tree und Eingabe-Steuerung.
                  </p>
                </InstallStep>
                <div className="flex items-start gap-2 rounded-lg bg-amber-500/5 border border-amber-500/20 px-3 py-2.5">
                  <AlertCircle className="h-3.5 w-3.5 text-amber-400 shrink-0 mt-0.5" />
                  <p className="text-xs text-amber-400/80">
                    Die Bridge kann Maus, Tastatur und Bildschirm steuern. Nur verbinden, wenn du dem Agent vertraust.
                    Erlaubte Aktionen kannst du per Session einschränken.
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

// ── Session Card ──────────────────────────────────────────────────────────────

function SessionCard({
  session,
  wsBase,
  onDelete,
  onCapabilitiesChange,
}: {
  session: ComputerUseSession;
  wsBase: string;
  onDelete: (id: string) => void;
  onCapabilitiesChange: (id: string, caps: string[]) => void;
}) {
  const isConnected = session.status === "connected";
  const wsUrl = `${wsBase}/ws/computer-use/bridge?session_id=${session.session_id}`;

  const [copiedWs, setCopiedWs] = useState(false);
  const [liveView, setLiveView] = useState(false);
  const [screenshotSrc, setScreenshotSrc] = useState<string | null>(null);
  const [screenshotLoading, setScreenshotLoading] = useState(false);
  const [screenshotError, setScreenshotError] = useState<"disconnected" | "error" | null>(null);
  const [showPermissions, setShowPermissions] = useState(false);
  const [savingCaps, setSavingCaps] = useState(false);
  const liveRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const allowedSet = new Set(session.allowed_capabilities ?? []);

  const copyWs = async () => {
    await navigator.clipboard.writeText(wsUrl);
    setCopiedWs(true);
    setTimeout(() => setCopiedWs(false), 2000);
  };

  const fetchScreenshot = useCallback(async () => {
    if (screenshotLoading) return;
    setScreenshotLoading(true);
    try {
      const data = await getComputerUseScreenshot(session.session_id);
      setScreenshotError(null);
      if (data.screenshot_b64) {
        setScreenshotSrc(`data:image/png;base64,${data.screenshot_b64}`);
      }
    } catch (e: unknown) {
      const status = (e as { status?: number })?.status;
      setScreenshotError(status === 503 ? "disconnected" : "error");
    } finally {
      setScreenshotLoading(false);
    }
  }, [session.session_id, screenshotLoading]);

  // Start/stop polling based on liveView + isConnected
  useEffect(() => {
    if (liveRef.current) {
      clearInterval(liveRef.current);
      liveRef.current = null;
    }
    if (liveView && isConnected) {
      setScreenshotError(null);
      fetchScreenshot();
      liveRef.current = setInterval(fetchScreenshot, 4000);
    } else if (liveView && !isConnected) {
      setScreenshotError("disconnected");
    }
    return () => {
      if (liveRef.current) clearInterval(liveRef.current);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveView, isConnected]);

  const toggleCapability = async (capId: string) => {
    setSavingCaps(true);
    const next = allowedSet.has(capId)
      ? session.allowed_capabilities.filter((c) => c !== capId)
      : [...session.allowed_capabilities, capId];
    await onCapabilitiesChange(session.session_id, next);
    setSavingCaps(false);
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: -4 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.98 }}
      className="rounded-xl border border-foreground/[0.08] bg-foreground/[0.02] overflow-hidden"
    >
      {/* Header row */}
      <div className="flex items-center justify-between px-4 py-3">
        <div className="flex items-center gap-3 min-w-0">
          <div className={cn(
            "flex h-8 w-8 items-center justify-center rounded-lg shrink-0",
            isConnected ? "bg-emerald-500/10" : "bg-amber-500/10"
          )}>
            {isConnected
              ? <Wifi className="h-4 w-4 text-emerald-400" />
              : <WifiOff className="h-4 w-4 text-amber-400" />}
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <p className="text-sm font-medium text-foreground font-mono truncate">{session.session_id}</p>
              {session.platform && session.platform !== "unknown" && (
                <span className="inline-flex items-center rounded-full border border-foreground/[0.1] bg-foreground/[0.04] px-2 py-0.5 text-[10px] text-muted-foreground font-medium">
                  {session.platform}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2 mt-0.5">
              <span className={cn(
                "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium border",
                isConnected
                  ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                  : "bg-amber-500/10 text-amber-400 border-amber-500/20"
              )}>
                <span className={cn("h-1.5 w-1.5 rounded-full", isConnected ? "bg-emerald-400 animate-pulse" : "bg-amber-400")} />
                {isConnected ? "Verbunden" : "Warte auf Bridge"}
              </span>
              {session.action_count > 0 && (
                <span className="text-[10px] text-muted-foreground flex items-center gap-1">
                  <Activity className="h-2.5 w-2.5" />
                  {session.action_count} Aktionen
                </span>
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={() => setShowPermissions((v) => !v)}
            className={cn(
              "inline-flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-[10px] font-medium transition-all",
              showPermissions
                ? "bg-violet-500/10 border border-violet-500/20 text-violet-400"
                : "text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04]"
            )}
          >
            <Settings2 className="h-3 w-3" />
            Berechtigungen
          </button>
          <button
            onClick={() => onDelete(session.session_id)}
            className="p-1.5 rounded-lg text-muted-foreground hover:text-red-400 hover:bg-red-400/10 transition-all"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {/* Permissions panel */}
      <AnimatePresence>
        {showPermissions && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden"
          >
            <div className="px-4 pb-3 pt-1 border-t border-foreground/[0.06] space-y-1">
              <p className="text-[10px] font-medium text-muted-foreground/70 uppercase tracking-wider mb-2">
                Was darf der Agent auf diesem Rechner?
              </p>
              {CAPABILITY_META.map((cap) => {
                const enabled = allowedSet.has(cap.id);
                return (
                  <div key={cap.id} className="flex items-center justify-between gap-3 py-1.5">
                    <div className="flex items-center gap-2.5 min-w-0">
                      <span className={cn("shrink-0", enabled ? RISK_COLORS[cap.risk] : "text-muted-foreground/40")}>
                        {cap.icon}
                      </span>
                      <div className="min-w-0">
                        <p className={cn("text-xs font-medium truncate", enabled ? "text-foreground" : "text-muted-foreground/60")}>
                          {cap.label}
                        </p>
                        <p className="text-[10px] text-muted-foreground/50 truncate">{cap.description}</p>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <span className={cn(
                        "text-[9px] font-medium uppercase",
                        cap.risk === "high" ? "text-red-400/60" : cap.risk === "medium" ? "text-amber-400/60" : "text-emerald-400/60"
                      )}>
                        {cap.risk === "high" ? "⚠ hoch" : cap.risk === "medium" ? "mittel" : "gering"}
                      </span>
                      <button
                        disabled={savingCaps}
                        onClick={() => toggleCapability(cap.id)}
                        className={cn(
                          "relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none disabled:opacity-40",
                          enabled
                            ? cap.risk === "high" ? "bg-red-500" : cap.risk === "medium" ? "bg-amber-500" : "bg-emerald-500"
                            : "bg-foreground/[0.12]"
                        )}
                        role="switch"
                        aria-checked={enabled}
                      >
                        <span className={cn(
                          "pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow transform transition-transform duration-200",
                          enabled ? "translate-x-4" : "translate-x-0"
                        )} />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* WS URL */}
      <div className="px-4 pb-3 space-y-1 border-t border-foreground/[0.04]">
        <p className="text-[10px] font-medium text-muted-foreground/60 uppercase tracking-wider pt-3">
          Bridge Connection URL
        </p>
        <div className="flex items-center gap-2 rounded-lg bg-foreground/[0.04] border border-foreground/[0.06] px-3 py-2">
          <code className="flex-1 text-[11px] text-foreground/70 truncate">{wsUrl}</code>
          <button
            onClick={copyWs}
            className="shrink-0 p-1 rounded text-muted-foreground hover:text-foreground transition-colors"
          >
            {copiedWs ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
          </button>
        </div>
        {!isConnected && (
          <p className="text-[10px] text-muted-foreground/50">
            Bridge-App starten und diese URL einfügen:{" "}
            <code className="text-violet-400/80">python tray_app.py</code>
          </p>
        )}
      </div>

      {/* Live screen (only when connected) */}
      {isConnected && (
        <div className="px-4 pb-4 border-t border-foreground/[0.04]">
          <div className="flex items-center justify-between pt-3 mb-2">
            <p className="text-[10px] font-medium text-muted-foreground/60 uppercase tracking-wider">Live Screen</p>
            <div className="flex items-center gap-1.5">
              {liveView && screenshotLoading && (
                <Loader2 className="h-3 w-3 text-muted-foreground animate-spin" />
              )}
              {liveView && screenshotSrc && !screenshotError && (
                <button
                  onClick={fetchScreenshot}
                  className="p-1 rounded text-muted-foreground hover:text-foreground transition-colors"
                >
                  <RefreshCw className="h-3 w-3" />
                </button>
              )}
              <button
                onClick={() => setLiveView((v) => !v)}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-[10px] font-medium transition-all",
                  liveView
                    ? "bg-violet-500/10 border border-violet-500/20 text-violet-400"
                    : "bg-foreground/[0.04] border border-foreground/[0.08] text-muted-foreground hover:text-foreground"
                )}
              >
                {liveView ? <Eye className="h-3 w-3" /> : <EyeOff className="h-3 w-3" />}
                {liveView ? "Live" : "Watch"}
              </button>
            </div>
          </div>
          <AnimatePresence>
            {liveView && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                exit={{ opacity: 0, height: 0 }}
                className="overflow-hidden"
              >
                {screenshotError === "disconnected" ? (
                  <div className="flex flex-col items-center justify-center h-32 rounded-lg border border-dashed border-amber-500/30 bg-amber-500/5 gap-2">
                    <WifiOff className="h-5 w-5 text-amber-400/60" />
                    <p className="text-xs text-amber-400/80 font-medium">Bridge getrennt</p>
                    <p className="text-[10px] text-muted-foreground/60">Bridge-App neu starten — Verbindung wird automatisch erkannt.</p>
                  </div>
                ) : screenshotError === "error" ? (
                  <div className="flex flex-col items-center justify-center h-32 rounded-lg border border-dashed border-red-500/30 bg-red-500/5 gap-2">
                    <AlertCircle className="h-5 w-5 text-red-400/60" />
                    <p className="text-xs text-red-400/80">Screenshot fehlgeschlagen</p>
                    <button onClick={fetchScreenshot} className="text-[10px] text-muted-foreground hover:text-foreground underline">
                      Erneut versuchen
                    </button>
                  </div>
                ) : screenshotSrc ? (
                  <img
                    src={screenshotSrc}
                    alt="Desktop screenshot"
                    className="w-full rounded-lg border border-foreground/[0.08] object-contain bg-black"
                  />
                ) : (
                  <div className="flex items-center justify-center h-32 rounded-lg border border-dashed border-foreground/[0.1] text-xs text-muted-foreground gap-2">
                    {screenshotLoading
                      ? <><Loader2 className="h-4 w-4 animate-spin" /> Screenshot wird geladen…</>
                      : "Noch kein Screenshot"}
                  </div>
                )}
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}
    </motion.div>
  );
}

// ── Helper components ─────────────────────────────────────────────────────────

function InstallStep({ step, title, children }: { step: number; title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <span className="flex h-5 w-5 items-center justify-center rounded-full bg-violet-500/10 text-[10px] font-bold text-violet-400 border border-violet-500/20">
          {step}
        </span>
        <p className="text-xs font-medium text-foreground">{step}. {title}</p>
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
