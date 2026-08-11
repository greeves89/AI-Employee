"use client";

import { useState, useEffect, useCallback } from "react";
import {
  AppWindow, Play, Square, Loader2, Cpu, RefreshCw, ScrollText, Trash2, X, Flag,
  CheckCircle2, Hammer, Share2, Users, Globe, UserPlus, Link2, Copy, Check,
  AlertTriangle, ShieldCheck, Box, User,
} from "lucide-react";
import { Header } from "@/components/layout/header";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";

export default function AppsPage() {
  const [apps, setApps] = useState<api.AppEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<Set<string>>(new Set());   // per-app: multiple in parallel
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [reported, setReported] = useState<Record<string, string>>({});  // project -> agent name
  const [reporting, setReporting] = useState<Set<string>>(new Set());
  const [logsFor, setLogsFor] = useState<api.AppEntry | null>(null);
  const [detailFor, setDetailFor] = useState<api.AppEntry | null>(null);

  const report = async (app: api.AppEntry) => {
    const err = errors[app.project];
    if (!err) return;
    setReporting((s) => new Set(s).add(app.project));
    try {
      const r = await api.reportApp(app.project, err, app.path);
      setReported((m) => ({ ...m, [app.project]: r.agent_name }));
    } catch {
      /* ignore */
    } finally {
      setReporting((s) => { const n = new Set(s); n.delete(app.project); return n; });
    }
  };

  const load = useCallback(async () => {
    try {
      const r = await api.listApps();
      setApps(r.apps);
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const iv = setInterval(load, 5000);
    return () => clearInterval(iv);
  }, [load]);

  // Per-app action: each spins its OWN card until it finishes — independent of other
  // clicks. Errors land on the card (not as uncaught console rejections).
  const act = async (key: string, fn: () => Promise<unknown>) => {
    setBusy((s) => new Set(s).add(key));
    setErrors((e) => { const n = { ...e }; delete n[key]; return n; });
    try {
      await fn();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Fehlgeschlagen.";
      // Strip the "API Error 500: {json}" envelope → short readable hint.
      const short = msg.replace(/^API Error \d+:\s*/, "").slice(0, 240);
      setErrors((e) => ({ ...e, [key]: short }));
    } finally {
      setBusy((s) => { const n = new Set(s); n.delete(key); return n; });
      load();
    }
  };

  const start = (app: api.AppEntry) =>
    act(app.project, () =>
      app.path && app.containers.length === 0
        ? api.startDockerApp(app.agent_id, app.path)   // never-started workspace app → compose up
        : api.startAppByProject(app.project),          // stopped container → start
    );

  return (
    <div className="flex flex-col h-[calc(100vh-2rem)]">
      <Header title="Apps" subtitle="Alle Apps deiner Agenten — laufend, gestoppt und noch nicht gestartet" />

      <div className="flex-1 overflow-y-auto p-6">
        <div className="flex items-center justify-between mb-4">
          <p className="text-sm text-muted-foreground">{apps.length} App{apps.length === 1 ? "" : "s"}</p>
          <button
            onClick={load}
            className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors"
          >
            <RefreshCw className="h-4 w-4" /> Aktualisieren
          </button>
        </div>

        {loading && apps.length === 0 ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : apps.length === 0 ? (
          <div className="rounded-xl border border-border bg-card/60 p-10 text-center">
            <AppWindow className="h-8 w-8 mx-auto text-muted-foreground/50 mb-3" />
            <p className="text-sm text-muted-foreground">
              Noch keine Apps. Sie erscheinen hier, sobald einer deiner Agenten ein docker-compose-Projekt hat
              (Taskforce-Ergebnis oder Agenten-Workspace).
            </p>
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {apps.map((app) => {
              const running = app.status === "running";
              const notStarted = app.status === "not_started";
              const isBusy = busy.has(app.project);
              const err = errors[app.project];
              // Fremde, mir freigegebene App: nur öffnen. Steuernde Aktionen sind
              // serverseitig ohnehin ownership-gated — hier nur die ehrliche UI dazu.
              const readOnly = !!app.shared_with_me;
              return (
                <div key={app.project} className="rounded-xl border border-border bg-card/80 p-4 flex flex-col gap-3">
                  <button
                    onClick={() => setDetailFor(app)}
                    title="Details und Freigaben anzeigen"
                    className="text-left -m-1 p-1 rounded-lg hover:bg-accent/40 transition-colors"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <div className={cn(
                          "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg",
                          running ? "bg-emerald-500/10" : "bg-foreground/[0.05]",
                        )}>
                          <AppWindow className={cn("h-4 w-4", running ? "text-emerald-400" : "text-muted-foreground")} />
                        </div>
                        <div className="min-w-0">
                          <p className="text-sm font-medium truncate">{app.name}</p>
                          <p className="flex items-center gap-1 text-[11px] text-muted-foreground truncate">
                            <Cpu className="h-3 w-3" /> {app.agent_name}
                          </p>
                          {app.owner_name && (
                            // Bei fremden Apps die eigentliche Frage: von wem ist das?
                            // Bei eigenen erklaert es, wieso sie ueberhaupt hier steht.
                            <p className="flex items-center gap-1 text-[11px] text-muted-foreground/70 truncate">
                              <User className="h-3 w-3" />
                              {app.owned_by_me ? app.owner_name : `von ${app.owner_name}`}
                            </p>
                          )}
                        </div>
                      </div>
                      <div className="flex flex-col items-end gap-1 shrink-0">
                        <span className={cn(
                          "flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full border font-medium",
                          isBusy ? "bg-primary/10 text-primary border-primary/20"
                            : running ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                            : notStarted ? "bg-blue-500/10 text-blue-400 border-blue-500/20"
                            : "bg-amber-500/10 text-amber-400 border-amber-500/20",
                        )}>
                          {isBusy && <Loader2 className="h-2.5 w-2.5 animate-spin" />}
                          {isBusy ? "startet…" : running ? "läuft" : notStarted ? "nicht gestartet" : "gestoppt"}
                        </span>
                        {readOnly && (
                          <span className="flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full border border-violet-500/20 bg-violet-500/10 text-violet-300 font-medium">
                            <Share2 className="h-2.5 w-2.5" /> für mich freigegeben
                          </span>
                        )}
                      </div>
                    </div>

                    <p className="mt-2 text-[11px] text-muted-foreground/70 truncate">
                      {app.containers.length > 0
                        ? `${app.containers.length} Container · ${app.containers.map((c) => c.service || c.name).slice(0, 3).join(", ")}`
                        : app.path ? `Workspace: ${app.path}` : app.project}
                    </p>
                  </button>

                  <div className="flex flex-wrap items-center gap-2 mt-auto">
                    {running && app.url && (
                      <a href={app.url} target="_blank" rel="noopener noreferrer"
                        className="flex items-center gap-1.5 rounded-lg bg-emerald-500/15 px-3 py-1.5 text-sm font-medium text-emerald-400 hover:bg-emerald-500/25 transition-colors">
                        <Play className="h-4 w-4" /> Öffnen
                      </a>
                    )}
                    {!running && !readOnly && (
                      <button onClick={() => start(app)} disabled={isBusy}
                        className="flex items-center gap-1.5 rounded-lg bg-emerald-500/15 px-3 py-1.5 text-sm font-medium text-emerald-400 hover:bg-emerald-500/25 disabled:opacity-60 transition-colors">
                        {isBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                        {isBusy ? "startet…" : "Starten"}
                      </button>
                    )}
                    {running && !readOnly && (
                      <button onClick={() => act(app.project, () => api.stopApp(app.project))} disabled={isBusy}
                        className="flex items-center gap-1.5 rounded-lg bg-amber-500/10 px-3 py-1.5 text-sm font-medium text-amber-400 hover:bg-amber-500/20 disabled:opacity-50 transition-colors">
                        {isBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Square className="h-4 w-4" />} Stoppen
                      </button>
                    )}
                    {!readOnly && (
                      <button onClick={() => setDetailFor(app)}
                        title="App für andere freigeben"
                        className="flex items-center gap-1.5 rounded-lg bg-violet-500/10 px-3 py-1.5 text-sm font-medium text-violet-300 hover:bg-violet-500/20 transition-colors">
                        <Share2 className="h-4 w-4" /> Freigeben
                      </button>
                    )}
                    {app.path && !readOnly && (
                      <button onClick={() => act(app.project, () => api.rebuildDockerApp(app.agent_id, app.path!))} disabled={isBusy}
                        title="Image aus dem aktuellen Code neu bauen (--build --force-recreate) — übernimmt Code-/Datenänderungen"
                        className="flex items-center gap-1.5 rounded-lg bg-blue-500/10 px-3 py-1.5 text-sm font-medium text-blue-400 hover:bg-blue-500/20 disabled:opacity-50 transition-colors">
                        {isBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Hammer className="h-4 w-4" />} Neu bauen
                      </button>
                    )}
                    {app.containers.length > 0 && !readOnly && (
                      <button onClick={() => setLogsFor(app)}
                        className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors">
                        <ScrollText className="h-4 w-4" /> Logs
                      </button>
                    )}
                    {!running && app.containers.length > 0 && !readOnly && (
                      <button onClick={() => act(app.project, () => api.removeApp(app.project))} disabled={isBusy}
                        title="Container endgültig entfernen"
                        className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm text-red-400/80 hover:text-red-400 hover:bg-red-500/10 transition-colors">
                        <Trash2 className="h-4 w-4" />
                      </button>
                    )}
                  </div>
                  {err && (
                    <div className="space-y-1.5">
                      <p className="text-[11px] text-red-400/90 bg-red-500/[0.06] rounded-lg px-2.5 py-1.5 break-words">
                        {err}{app.containers.length > 0 ? " — Details unter Logs." : ""}
                      </p>
                      {reported[app.project] ? (
                        <p className="flex items-center gap-1.5 text-[11px] text-emerald-400">
                          <CheckCircle2 className="h-3.5 w-3.5" />
                          An {reported[app.project]} gemeldet — der Agent kümmert sich darum.
                        </p>
                      ) : (
                        <button
                          onClick={() => report(app)}
                          disabled={reporting.has(app.project)}
                          className="flex items-center gap-1.5 rounded-lg bg-amber-500/10 px-3 py-1.5 text-xs font-medium text-amber-400 hover:bg-amber-500/20 disabled:opacity-50 transition-colors"
                        >
                          {reporting.has(app.project) ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Flag className="h-3.5 w-3.5" />}
                          An Agent melden (soll beheben)
                        </button>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {logsFor && <LogsModal app={logsFor} onClose={() => setLogsFor(null)} />}
      {detailFor && (
        <DetailModal
          app={detailFor}
          onClose={() => setDetailFor(null)}
          onShowLogs={() => { setLogsFor(detailFor); setDetailFor(null); }}
        />
      )}
    </div>
  );
}

// ── Detail + Freigaben ────────────────────────────────────────────────────────
// Default ist deny: eine App sieht nur, wem sie gehört. Hier vergibt der Besitzer
// gezielt Zugriff — namentlich, an alle Eingeloggten, oder als öffentlicher Link
// mit Ablaufdatum. Der Link-Token wird EINMAL angezeigt und danach nie wieder.

const SCOPE_LABEL: Record<api.AppShareScope, string> = {
  user: "Einzelne Person",
  authenticated: "Alle eingeloggten Nutzer",
  public: "Öffentlicher Link (ohne Login)",
};

function DetailModal({ app, onClose, onShowLogs }: {
  app: api.AppEntry;
  onClose: () => void;
  onShowLogs: () => void;
}) {
  const [detail, setDetail] = useState<api.AppDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [scope, setScope] = useState<api.AppShareScope>("user");
  const [userId, setUserId] = useState("");
  const [days, setDays] = useState(7);
  // 0 Tage = unbefristet. Eigener Zustand statt „days === 0", damit die
  // eingestellte Dauer erhalten bleibt, wenn man den Haken wieder wegnimmt.
  const [neverExpires, setNeverExpires] = useState(false);
  const [directory, setDirectory] = useState<{ id: string; name: string; email: string }[]>([]);
  const [saving, setSaving] = useState(false);
  const [shareErr, setShareErr] = useState("");
  const [freshLink, setFreshLink] = useState("");
  const [copiedShareId, setCopiedShareId] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(() => {
    api.getAppDetail(app.project)
      .then(setDetail)
      .catch(() => setDetail(null))
      .finally(() => setLoading(false));
  }, [app.project]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!detail?.can_manage) return;
    api.listAppShareDirectory().then((r) => setDirectory(r.users)).catch(() => setDirectory([]));
  }, [detail?.can_manage]);

  const submit = async () => {
    setSaving(true);
    setShareErr("");
    setFreshLink("");
    try {
      const created = await api.createAppShare(app.project, {
        scope,
        ...(scope === "user" ? { user_id: userId } : {}),
        ...(scope === "public" ? { expires_in_days: neverExpires ? 0 : days } : {}),
      });
      if (created.token && detail?.proxy_container && detail?.proxy_port) {
        setFreshLink(
          `${window.location.origin}/api/v1/agents/${detail.agent_id}/apps/proxy/` +
          `${detail.proxy_container}/${detail.proxy_port}/?__aie_share=${encodeURIComponent(created.token)}`,
        );
      } else if (created.token) {
        setShareErr("Link erstellt — aber die App läuft gerade nicht. Starte sie, dann erscheint der Link beim nächsten Öffnen dieses Dialogs.");
      }
      setUserId("");
      load();
    } catch (e) {
      setShareErr((e instanceof Error ? e.message : "Freigabe fehlgeschlagen.").replace(/^API Error \d+:\s*/, ""));
    } finally {
      setSaving(false);
    }
  };

  // Aus Token + Proxy-Ziel den vollstaendigen Link bauen — dieselbe Formel wie
  // beim Anlegen, damit es nur EINE Stelle gibt, die weiss, wie er aussieht.
  const shareLink = (s: api.AppShare): string =>
    s.token && detail?.proxy_container && detail?.proxy_port
      ? `${window.location.origin}/api/v1/agents/${detail.agent_id}/apps/proxy/` +
        `${detail.proxy_container}/${detail.proxy_port}/?__aie_share=${encodeURIComponent(s.token)}`
      : "";

  const copyShareLink = (s: api.AppShare) => {
    const link = shareLink(s);
    if (!link) return;
    navigator.clipboard.writeText(link).then(() => {
      setCopiedShareId(s.id);
      setTimeout(() => setCopiedShareId(null), 1500);
    });
  };

  const revoke = async (id: string) => {
    try {
      await api.revokeAppShare(id);
      load();
    } catch { /* ignore */ }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" onClick={onClose}>
      <div
        className="w-full max-w-2xl max-h-[85vh] flex flex-col rounded-2xl border border-border bg-card shadow-xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-3 border-b border-border shrink-0">
          <div className="flex items-center gap-2 min-w-0">
            <AppWindow className="h-4 w-4 text-primary shrink-0" />
            <span className="font-medium truncate">{app.name}</span>
          </div>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground"><X className="h-4 w-4" /></button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-6">
          {loading && !detail ? (
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          ) : !detail ? (
            <p className="text-sm text-muted-foreground">Details konnten nicht geladen werden.</p>
          ) : (
            <>
              {/* Eckdaten */}
              <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
                <Field label="Agent" value={detail.agent_name} />
                <Field
                  label="Besitzer"
                  value={detail.owner_name ? (detail.owned_by_me ? `${detail.owner_name} (du)` : detail.owner_name) : "—"}
                />
                <Field label="Status" value={
                  detail.status === "running" ? `läuft (${detail.running}/${detail.total})`
                    : detail.status === "partial" ? `teilweise (${detail.running}/${detail.total})`
                    : detail.status === "not_started" ? "nicht gestartet" : "gestoppt"
                } />
                <Field label="Workspace-Pfad" value={app.path || "—"} />
                <Field label="Compose-Projekt" value={detail.project} mono />
              </div>

              {detail.url && (
                <a href={detail.url} target="_blank" rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-500/15 px-3 py-1.5 text-sm font-medium text-emerald-400 hover:bg-emerald-500/25 transition-colors">
                  <Play className="h-4 w-4" /> App öffnen
                </a>
              )}

              {/* Container */}
              <section>
                <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
                  <Box className="h-3.5 w-3.5" /> Container ({detail.containers.length})
                </h3>
                {detail.containers.length === 0 ? (
                  <p className="text-sm text-muted-foreground">Noch keine Container — die App wurde nie gestartet.</p>
                ) : (
                  <div className="rounded-lg border border-border divide-y divide-border overflow-hidden">
                    {detail.containers.map((c) => (
                      <div key={c.name} className="flex items-center justify-between gap-3 px-3 py-2">
                        <div className="min-w-0">
                          <p className="text-sm truncate">{c.service || c.name}</p>
                          <p className="text-[11px] text-muted-foreground/70 truncate font-mono">
                            {c.image || c.name}{c.port ? ` · Port ${c.port}` : ""}
                          </p>
                        </div>
                        <span className={cn(
                          "text-[10px] px-2 py-0.5 rounded-full border shrink-0",
                          c.status === "running" ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                            : "bg-amber-500/10 text-amber-400 border-amber-500/20",
                        )}>{c.status}</span>
                      </div>
                    ))}
                  </div>
                )}
                {detail.containers.length > 0 && detail.can_manage && (
                  <button onClick={onShowLogs}
                    className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors">
                    <ScrollText className="h-3.5 w-3.5" /> Logs ansehen
                  </button>
                )}
              </section>

              {/* Freigaben */}
              {detail.can_manage ? (
                <section>
                  <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
                    <Share2 className="h-3.5 w-3.5" /> Freigaben
                  </h3>
                  <p className="text-[11px] text-muted-foreground/70 mb-3">
                    Ohne Freigabe kommt nur du an diese App. Freigegebene Nutzer dürfen sie ausschließlich
                    öffnen — Starten, Stoppen, Neu bauen und Logs bleiben bei dir.
                  </p>

                  {detail.shares.length > 0 && (
                    <div className="rounded-lg border border-border divide-y divide-border overflow-hidden mb-3">
                      {detail.shares.map((s) => (
                        <div key={s.id} className="flex items-center justify-between gap-3 px-3 py-2">
                          <div className="flex items-center gap-2 min-w-0">
                            {s.scope === "public" ? <Globe className="h-4 w-4 text-amber-400 shrink-0" />
                              : s.scope === "authenticated" ? <Users className="h-4 w-4 text-blue-400 shrink-0" />
                              : <ShieldCheck className="h-4 w-4 text-emerald-400 shrink-0" />}
                            <div className="min-w-0">
                              <p className="text-sm truncate">
                                {s.scope === "user" ? (s.user_name || s.user_id) : SCOPE_LABEL[s.scope]}
                              </p>
                              <p className="text-[11px] text-muted-foreground/70">
                                {s.expired ? "abgelaufen"
                                  : s.expires_at ? `läuft ab am ${new Date(s.expires_at).toLocaleDateString("de-DE")}`
                                  : "unbefristet"}
                              </p>
                            </div>
                          </div>
                          <div className="flex items-center gap-1 shrink-0">
                            {/* Der Link gehoert in die Liste, nicht nur in den Moment
                                seiner Entstehung. Wer ihn verlor, legte frueher einen
                                neuen an und liess den alten stehen — am Ende lebten
                                mehr Links, als jemand ueberblickte. */}
                            {s.scope === "public" && shareLink(s) && (
                              <button onClick={() => copyShareLink(s)}
                                title="Link kopieren"
                                className="text-muted-foreground/70 hover:text-foreground">
                                {copiedShareId === s.id
                                  ? <Check className="h-4 w-4 text-emerald-400" />
                                  : <Copy className="h-4 w-4" />}
                              </button>
                            )}
                            <button onClick={() => revoke(s.id)} title="Freigabe zurückziehen"
                              className="text-red-400/80 hover:text-red-400">
                              <Trash2 className="h-4 w-4" />
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Neue Freigabe */}
                  <div className="rounded-lg border border-border p-3 space-y-3">
                    <div className="flex flex-wrap gap-1.5">
                      {(["user", "authenticated", "public"] as api.AppShareScope[]).map((sc) => (
                        <button key={sc} onClick={() => { setScope(sc); setShareErr(""); setFreshLink(""); }}
                          className={cn(
                            "flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium border transition-colors",
                            scope === sc
                              ? "bg-primary/10 text-primary border-primary/25"
                              : "border-border text-muted-foreground hover:text-foreground hover:bg-accent/50",
                          )}>
                          {sc === "public" ? <Globe className="h-3.5 w-3.5" />
                            : sc === "authenticated" ? <Users className="h-3.5 w-3.5" />
                            : <UserPlus className="h-3.5 w-3.5" />}
                          {SCOPE_LABEL[sc]}
                        </button>
                      ))}
                    </div>

                    {scope === "user" && (
                      <select value={userId} onChange={(e) => setUserId(e.target.value)}
                        className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm">
                        <option value="">Person wählen…</option>
                        {directory.map((u) => (
                          <option key={u.id} value={u.id}>{u.name} ({u.email})</option>
                        ))}
                      </select>
                    )}

                    {scope === "public" && (
                      <div className="space-y-2">
                        <div className="flex items-start gap-2 rounded-lg bg-amber-500/[0.07] border border-amber-500/20 px-3 py-2">
                          <AlertTriangle className="h-4 w-4 text-amber-400 shrink-0 mt-0.5" />
                          <p className="text-[11px] text-amber-200/90">
                            Jeder mit dem Link kommt <strong>ohne Anmeldung</strong> an die App — auch außerhalb
                            eures Netzes. Nur für Demos, und nie für Apps mit echten Daten.
                          </p>
                        </div>
                        <label className="flex items-center gap-2 text-xs text-muted-foreground">
                          Gültig für
                          <input type="number" min={1} max={90} value={days || ""}
                            disabled={neverExpires}
                            onChange={(e) => setDays(Math.max(1, Math.min(90, Number(e.target.value) || 1)))}
                            className="w-20 rounded-lg border border-border bg-background px-2 py-1 text-sm text-foreground disabled:opacity-40" />
                          Tage (max. 90)
                        </label>
                        {/* Unbefristet ist eine bewusste Ausnahme, keine Vorgabe — der
                            Link bleibt offen, bis ihn jemand zurueckzieht. */}
                        <label className="flex items-center gap-2 text-xs text-muted-foreground">
                          <input type="checkbox" checked={neverExpires}
                            onChange={(e) => setNeverExpires(e.target.checked)}
                            className="h-3.5 w-3.5 rounded border-border accent-violet-500" />
                          Unbefristet — läuft nie ab
                        </label>
                        {neverExpires && (
                          <p className="text-[11px] text-amber-300/90">
                            Dieser Link bleibt gültig, bis du ihn zurückziehst. Niemand
                            erinnert dich daran.
                          </p>
                        )}
                      </div>
                    )}

                    {scope === "authenticated" && (
                      <p className="text-[11px] text-muted-foreground/80">
                        Jeder angemeldete Nutzer dieser Plattform darf die App öffnen.
                      </p>
                    )}

                    <button onClick={submit} disabled={saving || (scope === "user" && !userId)}
                      className="flex items-center gap-1.5 rounded-lg bg-violet-500/15 px-3 py-1.5 text-sm font-medium text-violet-300 hover:bg-violet-500/25 disabled:opacity-50 transition-colors">
                      {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Share2 className="h-4 w-4" />}
                      Freigeben
                    </button>

                    {shareErr && (
                      <p className="text-[11px] text-red-400/90 bg-red-500/[0.06] rounded-lg px-2.5 py-1.5 break-words">{shareErr}</p>
                    )}

                    {freshLink && (
                      <div className="space-y-1.5">
                        <p className="flex items-center gap-1.5 text-[11px] text-amber-300">
                          <Link2 className="h-3.5 w-3.5" />
                          Jetzt kopieren — dieser Link wird nur einmal angezeigt.
                        </p>
                        <div className="flex items-center gap-2">
                          <input readOnly value={freshLink} onFocus={(e) => e.currentTarget.select()}
                            className="flex-1 rounded-lg border border-border bg-background px-2.5 py-1.5 text-[11px] font-mono" />
                          <button
                            onClick={() => {
                              navigator.clipboard.writeText(freshLink).then(() => {
                                setCopied(true);
                                setTimeout(() => setCopied(false), 1800);
                              }).catch(() => { /* ignore */ });
                            }}
                            className="flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors">
                            {copied ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
                            {copied ? "Kopiert" : "Kopieren"}
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                </section>
              ) : (
                <p className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Share2 className="h-3.5 w-3.5" />
                  Diese App wurde dir freigegeben{detail.owner_name ? ` von ${detail.owner_name}` : ""}. Du kannst
                  sie öffnen — verwalten darf sie nur {detail.owner_name || `${detail.agent_name}s Besitzer`}.
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <p className="text-[11px] text-muted-foreground/70">{label}</p>
      <p className={cn("truncate", mono && "font-mono text-[12px]")} title={value}>{value}</p>
    </div>
  );
}

function LogsModal({ app, onClose }: { app: api.AppEntry; onClose: () => void }) {
  const [data, setData] = useState<api.AppLogContainer[] | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    api.getAppLogs(app.project, 300)
      .then((r) => setData(r.containers))
      .catch(() => setData([]))
      .finally(() => setLoading(false));
  }, [app.project]);

  useEffect(() => {
    load();
    const iv = setInterval(load, 4000);
    return () => clearInterval(iv);
  }, [load]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" onClick={onClose}>
      <div
        className="w-full max-w-3xl max-h-[85vh] flex flex-col rounded-2xl border border-border bg-card shadow-xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-3 border-b border-border shrink-0">
          <div className="flex items-center gap-2 min-w-0">
            <ScrollText className="h-4 w-4 text-primary shrink-0" />
            <span className="font-medium truncate">Logs — {app.name}</span>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={load} className="text-muted-foreground hover:text-foreground"><RefreshCw className="h-4 w-4" /></button>
            <button onClick={onClose} className="text-muted-foreground hover:text-foreground"><X className="h-4 w-4" /></button>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {loading && !data ? (
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          ) : !data || data.length === 0 ? (
            <p className="text-sm text-muted-foreground">Keine Logs verfügbar.</p>
          ) : (
            data.map((c) => (
              <div key={c.name}>
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-xs font-medium">{c.service || c.name}</span>
                  <span className={cn(
                    "text-[10px] px-1.5 py-0.5 rounded-full border",
                    c.status === "running" ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                      : "bg-amber-500/10 text-amber-400 border-amber-500/20",
                  )}>{c.status}</span>
                </div>
                <pre className="text-[11px] leading-relaxed whitespace-pre-wrap break-words font-mono text-foreground/75 bg-foreground/[0.03] rounded-lg p-3 max-h-72 overflow-auto">
                  {c.logs || "(leer)"}
                </pre>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
