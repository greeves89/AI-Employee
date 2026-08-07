"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  Check,
  ChevronDown,
  ChevronRight,
  Clock,
  FileText,
  Loader2,
  Plus,
  Save,
  Trash2,
  Zap,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { getAgents, getProactiveConfig, updateProactiveConfig } from "@/lib/api";
import type {
  ProactiveResponse,
  Responsibility,
  ResponsibilityPriority,
  ResponsibilityRhythm,
} from "@/lib/types";

// Spiegelt die serverseitigen Grenzen (agents.py) — hier nur, damit die UI gar nicht
// erst in ein 422 laeuft.
const MAX_RESPONSIBILITIES = 20;
const RHYTHMS: { value: ResponsibilityRhythm; label: string }[] = [
  { value: "daily", label: "taeglich" },
  { value: "weekly", label: "woechentlich" },
  { value: "monthly", label: "monatlich" },
  { value: "continuous", label: "laufend" },
];
const PRIORITIES: { value: ResponsibilityPriority; label: string }[] = [
  { value: "high", label: "hoch" },
  { value: "normal", label: "normal" },
  { value: "low", label: "niedrig" },
];

const INTERVALS = [
  { label: "15 min", seconds: 900 },
  { label: "30 min", seconds: 1800 },
  { label: "1h", seconds: 3600 },
  { label: "2h", seconds: 7200 },
  { label: "4h", seconds: 14400 },
  { label: "8h", seconds: 28800 },
];

interface ProactiveToggleProps {
  agentId: string;
}

export function ProactiveToggle({ agentId }: ProactiveToggleProps) {
  const [data, setData] = useState<ProactiveResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [toggling, setToggling] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [customDraft, setCustomDraft] = useState("");
  const [hoursStartDraft, setHoursStartDraft] = useState("");
  const [hoursEndDraft, setHoursEndDraft] = useState("");
  const [hoursTzDraft, setHoursTzDraft] = useState("");
  const [dutiesDraft, setDutiesDraft] = useState<Responsibility[]>([]);
  const [morningDraft, setMorningDraft] = useState("");
  const [weekdaysOnly, setWeekdaysOnly] = useState(true);
  const [deputyDraft, setDeputyDraft] = useState("");
  const [dutyStart, setDutyStart] = useState("");
  const [dutyEnd, setDutyEnd] = useState("");
  const [dutyWeekdays, setDutyWeekdays] = useState(false);
  const [absFrom, setAbsFrom] = useState("");
  const [absTo, setAbsTo] = useState("");
  const [agents, setAgents] = useState<{ id: string; name: string }[]>([]);
  const [savingPrompt, setSavingPrompt] = useState(false);
  const [savedFlash, setSavedFlash] = useState(false);
  const [saveError, setSaveError] = useState("");

  const load = useCallback(async () => {
    try {
      const res = await getProactiveConfig(agentId);
      setData(res);
    } catch {
      // ignore
    }
    setLoading(false);
  }, [agentId]);

  useEffect(() => {
    load();
  }, [load]);

  // Seed the editable draft whenever the server's saved value changes — keyed on
  // the value itself so interval/toggle reloads never clobber in-progress typing.
  const savedCustom = data?.proactive?.custom_instructions ?? "";
  useEffect(() => {
    setCustomDraft(savedCustom);
  }, [savedCustom]);

  const savedHours = data?.proactive?.contact_hours;
  const savedHoursStart = savedHours?.start ?? "";
  const savedHoursEnd = savedHours?.end ?? "";
  const savedHoursTz = savedHours?.timezone ?? "";
  useEffect(() => {
    setHoursStartDraft(savedHoursStart);
    setHoursEndDraft(savedHoursEnd);
    setHoursTzDraft(savedHoursTz);
  }, [savedHoursStart, savedHoursEnd, savedHoursTz]);

  // Seed the duties draft from the server value, keyed on its serialized form so a
  // toggle/interval reload never wipes half-typed entries.
  const savedDuties = JSON.stringify(data?.proactive?.responsibilities ?? []);
  useEffect(() => {
    setDutiesDraft(JSON.parse(savedDuties) as Responsibility[]);
  }, [savedDuties]);

  const addResponsibility = () =>
    setDutiesDraft((prev) =>
      prev.length >= MAX_RESPONSIBILITIES
        ? prev
        : [...prev, { title: "", rhythm: "daily", priority: "normal", notes: "" }],
    );
  const patchResponsibility = (idx: number, patch: Partial<Responsibility>) =>
    setDutiesDraft((prev) => prev.map((d, i) => (i === idx ? { ...d, ...patch } : d)));
  const removeResponsibility = (idx: number) =>
    setDutiesDraft((prev) => prev.filter((_, i) => i !== idx));

  // Vertreter-Auswahl braucht die anderen Agenten des Nutzers — dieselbe Liste, die
  // auch die Uebersicht zeigt (Sichtbarkeit steuert der Server).
  useEffect(() => {
    getAgents()
      .then((res) => setAgents(res.agents.map((a) => ({ id: a.id, name: a.name }))))
      .catch(() => {});
  }, []);

  const savedDeputy = data?.deputy_agent_id ?? "";
  const savedDutyStart = data?.working_hours?.start ?? "";
  const savedDutyEnd = data?.working_hours?.end ?? "";
  const savedDutyWeek = data?.working_hours?.weekdays_only ?? false;
  const savedAbsFrom = data?.proactive?.contact_absence?.from ?? "";
  const savedAbsTo = data?.proactive?.contact_absence?.to ?? "";
  useEffect(() => {
    setDeputyDraft(savedDeputy);
    setDutyStart(savedDutyStart);
    setDutyEnd(savedDutyEnd);
    setDutyWeekdays(savedDutyWeek);
    setAbsFrom(savedAbsFrom);
    setAbsTo(savedAbsTo);
  }, [savedDeputy, savedDutyStart, savedDutyEnd, savedDutyWeek, savedAbsFrom, savedAbsTo]);

  const savedMorning = data?.proactive?.morning_planning?.time ?? "";
  const savedWeekdays = data?.proactive?.morning_planning?.weekdays_only ?? true;
  useEffect(() => {
    setMorningDraft(savedMorning);
    setWeekdaysOnly(savedWeekdays);
  }, [savedMorning, savedWeekdays]);

  const hoursDirty =
    hoursStartDraft !== savedHoursStart ||
    hoursEndDraft !== savedHoursEnd ||
    hoursTzDraft !== savedHoursTz;
  const dutiesDirty = JSON.stringify(dutiesDraft) !== savedDuties;
  const morningDirty = morningDraft !== savedMorning || weekdaysOnly !== savedWeekdays;
  const dutyDirty =
    deputyDraft !== savedDeputy || dutyStart !== savedDutyStart || dutyEnd !== savedDutyEnd ||
    dutyWeekdays !== savedDutyWeek || absFrom !== savedAbsFrom || absTo !== savedAbsTo;
  const draftDirty = customDraft !== savedCustom || hoursDirty || dutiesDirty || morningDirty || dutyDirty;

  const handleSavePrompt = async () => {
    if (!data) return;
    if (!!hoursStartDraft !== !!hoursEndDraft) {
      setSaveError("Start- und Endzeit muessen zusammen gesetzt oder beide geleert werden.");
      return;
    }
    // Ein Bereich ohne Titel wuerde serverseitig 422 werfen — hier abfangen, damit die
    // Meldung am Feld steht statt als generischer Speicherfehler.
    if (dutiesDraft.some((d) => !d.title.trim())) {
      setSaveError("Jeder Verantwortungsbereich braucht einen Titel.");
      return;
    }
    setSavingPrompt(true);
    setSaveError("");
    try {
      await updateProactiveConfig(agentId, {
        enabled: data.proactive?.enabled ?? true,
        interval_seconds: data.proactive?.interval_seconds || 3600,
        custom_instructions: customDraft,
        contact_hours_start: hoursStartDraft,
        contact_hours_end: hoursEndDraft,
        contact_timezone: hoursTzDraft,
        responsibilities: dutiesDraft.map((d) => ({ ...d, title: d.title.trim() })),
        morning_planning_time: morningDraft,
        morning_planning_weekdays_only: weekdaysOnly,
        deputy_agent_id: deputyDraft,
        duty_start: dutyStart,
        duty_end: dutyEnd,
        duty_weekdays_only: dutyWeekdays,
        absence_from: absFrom,
        absence_to: absTo,
      });
      await load();
      setSavedFlash(true);
      setTimeout(() => setSavedFlash(false), 2000);
    } catch {
      setSaveError("Speichern fehlgeschlagen — pruefe Uhrzeit-Format (HH:MM) und Zeitzone.");
    }
    setSavingPrompt(false);
  };

  const handleToggle = async () => {
    if (!data) return;
    setToggling(true);
    try {
      const newEnabled = !data.proactive?.enabled;
      await updateProactiveConfig(agentId, {
        enabled: newEnabled,
        interval_seconds: data.proactive?.interval_seconds || 3600,
      });
      await load();
    } catch {
      // ignore
    }
    setToggling(false);
  };

  const handleIntervalChange = async (seconds: number) => {
    if (!data) return;
    try {
      await updateProactiveConfig(agentId, {
        enabled: data.proactive?.enabled ?? true,
        interval_seconds: seconds,
      });
      await load();
    } catch {
      // ignore
    }
  };

  if (loading) return null;

  const enabled = data?.proactive?.enabled ?? false;
  const interval = data?.proactive?.interval_seconds ?? 3600;
  const schedule = data?.schedule;

  const formatTime = (iso: string | null) => {
    if (!iso) return "never";
    const d = new Date(iso);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 0) {
      const absMin = Math.abs(diffMin);
      if (absMin < 60) return `in ${absMin}m`;
      return `in ${Math.floor(absMin / 60)}h`;
    }
    if (diffMin < 1) return "just now";
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffH = Math.floor(diffMin / 60);
    if (diffH < 24) return `${diffH}h ago`;
    return d.toLocaleDateString();
  };

  return (
    <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className={cn(
            "flex h-8 w-8 items-center justify-center rounded-lg transition-colors",
            enabled ? "bg-emerald-500/10" : "bg-foreground/[0.04]"
          )}>
            <Zap className={cn(
              "h-4 w-4 transition-colors",
              enabled ? "text-emerald-400" : "text-muted-foreground/40"
            )} />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium">Proactive Mode</span>
              {enabled && (
                <span className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                  <span className="relative flex h-1.5 w-1.5">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                    <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-500" />
                  </span>
                  Active
                </span>
              )}
            </div>
            <p className="text-[11px] text-muted-foreground/60">
              Agent checks periodically for work to do on its own
            </p>
          </div>
        </div>

        {/* Toggle switch */}
        <button
          onClick={handleToggle}
          disabled={toggling}
          className={cn(
            "relative inline-flex h-6 w-11 items-center rounded-full transition-colors",
            enabled ? "bg-emerald-500" : "bg-foreground/[0.1]"
          )}
        >
          {toggling ? (
            <Loader2 className="h-3 w-3 animate-spin mx-auto text-white" />
          ) : (
            <span
              className={cn(
                "inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform",
                enabled ? "translate-x-6" : "translate-x-1"
              )}
            />
          )}
        </button>
      </div>

      {/* Interval + stats (only when enabled) */}
      {enabled && (
        <div className="mt-3 pt-3 border-t border-foreground/[0.04] space-y-2">
          {/* Interval selector */}
          <div className="flex items-center gap-2">
            <Clock className="h-3 w-3 text-muted-foreground/40" />
            <span className="text-[11px] text-muted-foreground/60">Check every:</span>
            <div className="flex gap-1">
              {INTERVALS.map((opt) => (
                <button
                  key={opt.seconds}
                  onClick={() => handleIntervalChange(opt.seconds)}
                  className={cn(
                    "px-2 py-0.5 rounded text-[10px] font-medium transition-colors",
                    interval === opt.seconds
                      ? "bg-foreground/[0.08] text-foreground"
                      : "text-muted-foreground/50 hover:text-foreground hover:bg-foreground/[0.04]"
                  )}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          {/* Stats row */}
          {schedule && (
            <div className="flex items-center gap-4 text-[10px] text-muted-foreground/50">
              <span className="flex items-center gap-1">
                <Activity className="h-2.5 w-2.5" />
                {schedule.total_runs} runs
              </span>
              {schedule.last_run_at && (
                <span>Last: {formatTime(schedule.last_run_at)}</span>
              )}
              {schedule.next_run_at && (
                <span>Next: {formatTime(schedule.next_run_at)}</span>
              )}
              {schedule.total_runs > 0 && (
                <span className={cn(
                  schedule.success_count / schedule.total_runs >= 0.8
                    ? "text-emerald-400/60"
                    : "text-amber-400/60"
                )}>
                  {Math.round((schedule.success_count / schedule.total_runs) * 100)}% success
                </span>
              )}
            </div>
          )}

          {/* Prompt: base (read-only) + per-agent additions */}
          <div className="pt-2 mt-1 border-t border-foreground/[0.04]">
            <button
              onClick={() => setExpanded((v) => !v)}
              className="flex items-center gap-1.5 text-[11px] text-muted-foreground/60 hover:text-foreground transition-colors"
            >
              {expanded ? (
                <ChevronDown className="h-3 w-3" />
              ) : (
                <ChevronRight className="h-3 w-3" />
              )}
              <FileText className="h-3 w-3" />
              Prompt &amp; Anweisungen
            </button>

            {expanded && (
              <div className="mt-2 space-y-2.5">
                {data?.base_prompt && (
                  <div>
                    <div className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground/40">
                      Basis-Prompt — fest im System, gilt fuer alle Agenten
                    </div>
                    <pre className="max-h-44 overflow-auto whitespace-pre-wrap rounded-lg border border-foreground/[0.06] bg-background/60 p-2 text-[10px] leading-relaxed text-muted-foreground/70">
                      {data.base_prompt}
                    </pre>
                  </div>
                )}
                <div>
                  <div className="mb-1 flex items-center justify-between gap-2">
                    <span className="text-[10px] uppercase tracking-wide text-muted-foreground/40">
                      Verantwortungsbereiche
                    </span>
                    <button
                      onClick={addResponsibility}
                      disabled={dutiesDraft.length >= MAX_RESPONSIBILITIES}
                      className={cn(
                        "inline-flex items-center gap-1 rounded-lg border border-foreground/[0.08] px-2 py-0.5 text-[10px] text-muted-foreground transition-colors hover:bg-foreground/[0.04]",
                        dutiesDraft.length >= MAX_RESPONSIBILITIES && "opacity-40 cursor-not-allowed",
                      )}
                    >
                      <Plus className="h-3 w-3" />
                      Bereich
                    </button>
                  </div>
                  {dutiesDraft.length === 0 ? (
                    <div className="rounded-lg border border-dashed border-foreground/[0.08] p-2.5 text-[10px] leading-relaxed text-muted-foreground/50">
                      Noch keine Bereiche. Ohne sie plant der Agent nur, was jemand als Todo
                      angelegt hat — mit ihnen weiss er, wofuer er dauerhaft zustaendig ist,
                      und baut sich daraus selbst den Tag.
                    </div>
                  ) : (
                    <div className="space-y-1.5">
                      {dutiesDraft.map((duty, idx) => (
                        <div
                          key={idx}
                          className="rounded-lg border border-foreground/[0.06] bg-background/60 p-2"
                        >
                          <div className="flex items-center gap-1.5">
                            <input
                              type="text"
                              value={duty.title}
                              onChange={(e) => patchResponsibility(idx, { title: e.target.value })}
                              placeholder="z.B. Posteingang sichten"
                              className="min-w-0 flex-1 rounded-md border border-foreground/[0.08] bg-background/60 px-2 py-1 text-[11px] text-foreground placeholder:text-muted-foreground/30 focus:border-emerald-500/40 focus:outline-none"
                            />
                            <select
                              value={duty.rhythm}
                              onChange={(e) =>
                                patchResponsibility(idx, { rhythm: e.target.value as ResponsibilityRhythm })
                              }
                              className="rounded-md border border-foreground/[0.08] bg-background/60 px-1.5 py-1 text-[10px] text-foreground focus:border-emerald-500/40 focus:outline-none"
                            >
                              {RHYTHMS.map((r) => (
                                <option key={r.value} value={r.value}>{r.label}</option>
                              ))}
                            </select>
                            <select
                              value={duty.priority}
                              onChange={(e) =>
                                patchResponsibility(idx, { priority: e.target.value as ResponsibilityPriority })
                              }
                              className="rounded-md border border-foreground/[0.08] bg-background/60 px-1.5 py-1 text-[10px] text-foreground focus:border-emerald-500/40 focus:outline-none"
                            >
                              {PRIORITIES.map((p) => (
                                <option key={p.value} value={p.value}>{p.label}</option>
                              ))}
                            </select>
                            <button
                              onClick={() => removeResponsibility(idx)}
                              title="Bereich entfernen"
                              className="shrink-0 rounded-md p-1 text-muted-foreground/40 transition-colors hover:bg-foreground/[0.06] hover:text-red-400"
                            >
                              <Trash2 className="h-3 w-3" />
                            </button>
                          </div>
                          <input
                            type="text"
                            value={duty.notes ?? ""}
                            onChange={(e) => patchResponsibility(idx, { notes: e.target.value })}
                            placeholder="Praezisierung (optional) — woran genau erkennt er, dass es erledigt ist?"
                            className="mt-1.5 w-full rounded-md border border-foreground/[0.06] bg-background/40 px-2 py-1 text-[10px] text-muted-foreground placeholder:text-muted-foreground/25 focus:border-emerald-500/40 focus:outline-none"
                          />
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="mt-1.5 text-[10px] text-muted-foreground/40">
                    Dauerauftraege, keine Todos: der Lauf leitet daraus die Aufgaben des Tages ab
                    (STEP 1). Ein Bereich wird nie „fertig" — abgehakt wird der heutige Durchgang.
                  </div>
                </div>

                <div>
                  <div className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground/40">
                    Zusaetzliche Anweisungen fuer diesen Agenten
                  </div>
                  <textarea
                    value={customDraft}
                    onChange={(e) => setCustomDraft(e.target.value)}
                    rows={5}
                    placeholder="z.B. Pruefe bei jedem Lauf das IT-Operations Second Brain auf neue Druckerprobleme und ergaenze fehlende Loesungen als .md."
                    className="w-full resize-y rounded-lg border border-foreground/[0.08] bg-background/60 p-2 text-[11px] leading-relaxed text-foreground placeholder:text-muted-foreground/30 focus:border-emerald-500/40 focus:outline-none"
                  />
                  <div className="mt-1.5 text-[10px] text-muted-foreground/40">
                    Wird bei jedem proaktiven Lauf an den Basis-Prompt angehaengt.
                  </div>
                </div>

                <div>
                  <div className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground/40">
                    Vertretung bei Ausfall
                  </div>
                  <select
                    value={deputyDraft}
                    onChange={(e) => setDeputyDraft(e.target.value)}
                    className="w-full rounded-lg border border-foreground/[0.08] bg-background/60 px-2 py-1 text-[11px] text-foreground focus:border-emerald-500/40 focus:outline-none"
                  >
                    <option value="">— kein Vertreter (dann uebernimmt der Team-Lead) —</option>
                    {agents.filter((a) => a.id !== agentId).map((a) => (
                      <option key={a.id} value={a.id}>{a.name}</option>
                    ))}
                  </select>
                  <div className="mt-1.5 text-[10px] text-muted-foreground/40">
                    Haengt oder scheitert dieser Agent, gehen seine offenen Todos an den
                    Vertreter — und du bekommst eine Meldung. Ohne Vertreter und ohne
                    Team-Lead bleibt die Arbeit liegen.
                  </div>
                </div>

                <div>
                  <div className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground/40">
                    Dienstzeit des Agenten
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <input
                      type="time"
                      value={dutyStart}
                      onChange={(e) => setDutyStart(e.target.value)}
                      className="rounded-lg border border-foreground/[0.08] bg-background/60 px-2 py-1 text-[11px] text-foreground focus:border-emerald-500/40 focus:outline-none"
                    />
                    <span className="text-[11px] text-muted-foreground/40">bis</span>
                    <input
                      type="time"
                      value={dutyEnd}
                      onChange={(e) => setDutyEnd(e.target.value)}
                      className="rounded-lg border border-foreground/[0.08] bg-background/60 px-2 py-1 text-[11px] text-foreground focus:border-emerald-500/40 focus:outline-none"
                    />
                    <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                      <input
                        type="checkbox"
                        checked={dutyWeekdays}
                        onChange={(e) => setDutyWeekdays(e.target.checked)}
                        className="h-3 w-3 rounded border-foreground/20 bg-background text-emerald-500 focus:ring-emerald-500/30"
                      />
                      nur werktags
                    </label>
                  </div>
                  <div className="mt-1.5 text-[10px] text-muted-foreground/40">
                    Seine EIGENE Arbeitszeit (nicht deine). Ausserhalb laeuft kein proaktiver
                    Lauf. Leer = rund um die Uhr. Zeitzone kommt aus der Erreichbarkeit unten.
                  </div>
                </div>

                <div>
                  <div className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground/40">
                    Abwesenheit des Ansprechpartners
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <input
                      type="date"
                      value={absFrom}
                      onChange={(e) => setAbsFrom(e.target.value)}
                      className="rounded-lg border border-foreground/[0.08] bg-background/60 px-2 py-1 text-[11px] text-foreground focus:border-emerald-500/40 focus:outline-none"
                    />
                    <span className="text-[11px] text-muted-foreground/40">bis</span>
                    <input
                      type="date"
                      value={absTo}
                      onChange={(e) => setAbsTo(e.target.value)}
                      className="rounded-lg border border-foreground/[0.08] bg-background/60 px-2 py-1 text-[11px] text-foreground focus:border-emerald-500/40 focus:outline-none"
                    />
                    {(absFrom || absTo) && (
                      <button
                        type="button"
                        onClick={() => { setAbsFrom(""); setAbsTo(""); }}
                        className="text-[10px] text-muted-foreground/50 underline-offset-2 hover:underline"
                      >
                        loeschen
                      </button>
                    )}
                  </div>
                  <div className="mt-1.5 text-[10px] text-muted-foreground/40">
                    In diesem Zeitraum stellt der Agent keine Rueckfragen, sondern sammelt sie
                    und legt sie dir gebuendelt vor, wenn du zurueck bist.
                  </div>
                </div>

                <div>
                  <div className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground/40">
                    Tagesplanung am Morgen
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <input
                      type="time"
                      value={morningDraft}
                      onChange={(e) => setMorningDraft(e.target.value)}
                      className="rounded-lg border border-foreground/[0.08] bg-background/60 px-2 py-1 text-[11px] text-foreground focus:border-emerald-500/40 focus:outline-none"
                    />
                    <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                      <input
                        type="checkbox"
                        checked={weekdaysOnly}
                        onChange={(e) => setWeekdaysOnly(e.target.checked)}
                        className="h-3 w-3 rounded border-foreground/20 bg-background text-emerald-500 focus:ring-emerald-500/30"
                      />
                      nur werktags
                    </label>
                    {morningDraft && (
                      <button
                        type="button"
                        onClick={() => setMorningDraft("")}
                        className="text-[10px] text-muted-foreground/50 underline-offset-2 hover:underline"
                      >
                        abschalten
                      </button>
                    )}
                  </div>
                  <div className="mt-1.5 text-[10px] text-muted-foreground/40">
                    Zu dieser Uhrzeit plant der Agent seinen Tag aus den Verantwortungsbereichen
                    und legt den Plan in den Kalender — zusaetzlich zum Takt oben. Leer = aus.
                    Zeitzone kommt aus der Erreichbarkeit darunter.
                  </div>
                </div>

                <div>
                  <div className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground/40">
                    Erreichbarkeit des Ansprechpartners
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <input
                      type="time"
                      value={hoursStartDraft}
                      onChange={(e) => setHoursStartDraft(e.target.value)}
                      className="rounded-lg border border-foreground/[0.08] bg-background/60 px-2 py-1 text-[11px] text-foreground focus:border-emerald-500/40 focus:outline-none"
                    />
                    <span className="text-[11px] text-muted-foreground/40">bis</span>
                    <input
                      type="time"
                      value={hoursEndDraft}
                      onChange={(e) => setHoursEndDraft(e.target.value)}
                      className="rounded-lg border border-foreground/[0.08] bg-background/60 px-2 py-1 text-[11px] text-foreground focus:border-emerald-500/40 focus:outline-none"
                    />
                    <input
                      type="text"
                      value={hoursTzDraft}
                      onChange={(e) => setHoursTzDraft(e.target.value)}
                      placeholder="Europe/Berlin"
                      className="w-32 rounded-lg border border-foreground/[0.08] bg-background/60 px-2 py-1 text-[11px] text-foreground placeholder:text-muted-foreground/30 focus:border-emerald-500/40 focus:outline-none"
                    />
                  </div>
                  <div className="mt-1.5 text-[10px] text-muted-foreground/40">
                    Ausserhalb dieses Fensters meldet sich der Agent nur bei wirklich Dringendem
                    (STEP 4 der Basis-Regeln). Leer lassen = jeder Lauf gilt als Off-Hours.
                  </div>
                </div>

                <div className="flex items-center justify-between gap-2">
                  <span className="text-[10px] text-red-400/70">{saveError}</span>
                  <button
                    onClick={handleSavePrompt}
                    disabled={savingPrompt || !draftDirty}
                    className={cn(
                      "flex items-center gap-1 rounded-md px-2.5 py-1 text-[11px] font-medium transition-colors",
                      draftDirty && !savingPrompt
                        ? "bg-emerald-500/15 text-emerald-300 hover:bg-emerald-500/25"
                        : "cursor-not-allowed bg-foreground/[0.04] text-muted-foreground/40"
                    )}
                  >
                    {savingPrompt ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : savedFlash ? (
                      <Check className="h-3 w-3" />
                    ) : (
                      <Save className="h-3 w-3" />
                    )}
                    {savedFlash ? "Gespeichert" : "Speichern"}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
