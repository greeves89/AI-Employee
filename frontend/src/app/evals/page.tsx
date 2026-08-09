"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ClipboardCheck,
  Plus,
  Play,
  Trash2,
  Loader2,
  Check,
  X,
  TrendingDown,
  ShieldCheck,
  ShieldAlert,
  ChevronRight,
} from "lucide-react";
import { Header } from "@/components/layout/header";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import { useAgents } from "@/hooks/use-agents";
import { useConfirm, useToast } from "@/components/ui/dialog-provider";

/**
 * Golden-Tests (#391) — der Regressionstest für eine Agentenrolle.
 *
 * Ein Prompt-, Modell- oder Skill-Update kann eine Rolle heimlich verschlechtern.
 * Man merkt es Wochen später an einem falschen Bericht — und weiss dann nicht mehr,
 * welche Änderung es war. Hier wird festgelegt, was „gut" heisst, und das Ergebnis
 * ist die Zahlenreihe, an der ein Rückschritt überhaupt erst sichtbar wird.
 *
 * Die Aufgaben laufen als **echte Aufträge** durch denselben Agenten wie die
 * tägliche Arbeit — samt Systemprompt, Skills und Modell. Ein Prüfstand daneben
 * prüfte einen Agenten, den es so nicht gibt.
 */

type Draft = {
  id: string | null;
  name: string;
  role: string;
  description: string;
  items: api.EvalItem[];
};

const EMPTY_ITEM: api.EvalItem = {
  title: "",
  prompt: "",
  weight: 1,
  expect_contains: [],
  expect_absent: [],
};

const NEW_DRAFT: Draft = {
  id: null,
  name: "",
  role: "",
  description: "",
  items: [{ ...EMPTY_ITEM }],
};

function splitList(text: string): string[] {
  return text
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
}

export default function EvalsPage() {
  const { agents } = useAgents();
  const toast = useToast();
  const confirm = useConfirm();

  const [sets, setSets] = useState<api.EvalSet[]>([]);
  const [runs, setRuns] = useState<api.EvalRun[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [agentId, setAgentId] = useState("");
  const [gate, setGate] = useState<api.EvalGate | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [openRun, setOpenRun] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [s, r] = await Promise.all([api.getEvalSets(), api.getEvalRuns({ limit: 50 })]);
      setSets(s.sets);
      setRuns(r.runs);
    } catch (e) {
      console.error("Golden-Tests laden fehlgeschlagen:", e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    // Solange ein Lauf noch laeuft, aendert sich der Stand von selbst — die
    // Aufgaben stehen in der Warteschlange des Agenten.
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    if (!agentId) {
      setGate(null);
      return;
    }
    api.getEvalGate(agentId).then(setGate).catch(() => setGate(null));
  }, [agentId, runs]);

  const selected = useMemo(
    () => sets.find((s) => s.id === selectedId) ?? null,
    [sets, selectedId],
  );

  const runsOfSelected = useMemo(
    () => (selected ? runs.filter((r) => r.set_id === selected.id) : runs),
    [runs, selected],
  );

  const edit = (s: api.EvalSet) => {
    setSelectedId(s.id);
    setDraft({
      id: s.id,
      name: s.name,
      role: s.role,
      description: s.description,
      items: s.items.length ? s.items : [{ ...EMPTY_ITEM }],
    });
  };

  const save = async () => {
    if (!draft) return;
    setBusy(true);
    try {
      const body = {
        name: draft.name.trim(),
        role: draft.role.trim(),
        description: draft.description.trim(),
        items: draft.items,
      };
      const saved = draft.id
        ? await api.updateEvalSet(draft.id, body)
        : await api.createEvalSet(body);
      toast.success(
        draft.id ? `Gespeichert — Fassung ${saved.version}` : "Sammlung angelegt",
      );
      setDraft(null);
      setSelectedId(saved.id);
      await load();
    } catch (e) {
      // Die Meldung vom Server nennt die Aufgabe und den Grund — die gehoert
      // ungekuerzt hierher, sonst sucht man selbst.
      toast.error("Nicht gespeichert", e instanceof Error ? e.message : undefined);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (s: api.EvalSet) => {
    const ok = await confirm({
      title: `„${s.name}" löschen?`,
      message: "Die Sammlung wird entfernt. Bisherige Läufe verlieren damit ihren Bezug.",
      variant: "destructive",
      confirmLabel: "Löschen",
    });
    if (!ok) return;
    await api.deleteEvalSet(s.id);
    if (selectedId === s.id) setSelectedId(null);
    await load();
  };

  const run = async (s: api.EvalSet) => {
    if (!agentId) {
      toast.error("Kein Agent gewählt", "Wähle oben rechts, gegen wen getestet wird.");
      return;
    }
    setBusy(true);
    try {
      await api.runEvalSet(s.id, agentId);
      toast.success(
        "Lauf gestartet",
        `${s.item_count} Aufgabe(n) stehen in der Warteschlange. Das Ergebnis erscheint hier, sobald der Agent geantwortet hat.`,
      );
      await load();
    } catch (e) {
      toast.error("Lauf fehlgeschlagen", e instanceof Error ? e.message : undefined);
    } finally {
      setBusy(false);
    }
  };

  const patchItem = (index: number, patch: Partial<api.EvalItem>) => {
    if (!draft) return;
    const items = [...draft.items];
    items[index] = { ...items[index], ...patch };
    setDraft({ ...draft, items });
  };

  return (
    <div>
      <Header
        title="Golden-Tests"
        subtitle="Regressionstests je Agentenrolle — und das Gatter vor dem Update"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={agentId}
              onChange={(e) => setAgentId(e.target.value)}
              className="rounded-lg border border-foreground/[0.08] bg-card/50 px-3 py-2 text-sm outline-none"
            >
              <option value="">Agent wählen…</option>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
            <button
              onClick={() => {
                setDraft({ ...NEW_DRAFT, items: [{ ...EMPTY_ITEM }] });
                setSelectedId(null);
              }}
              className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground"
            >
              <Plus className="h-4 w-4" />
              Neue Sammlung
            </button>
          </div>
        }
      />

      <div className="px-8 py-8">
        {gate && (
          <div
            className={cn(
              "mb-6 flex items-start gap-2.5 rounded-xl border p-4 text-sm",
              gate.allowed
                ? "border-emerald-500/20 bg-emerald-500/[0.06]"
                : "border-red-500/20 bg-red-500/[0.06]",
            )}
          >
            {gate.allowed ? (
              <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" />
            ) : (
              <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-red-400" />
            )}
            <div>
              <div className="font-medium">
                {gate.allowed ? "Update wäre erlaubt" : "Update wäre blockiert"}
              </div>
              <div className="text-[12px] text-muted-foreground">{gate.message}</div>
            </div>
          </div>
        )}

        {draft ? (
          <div className="space-y-4 rounded-xl border border-foreground/[0.06] bg-card/80 p-5">
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block">
                <span className="text-[11px] text-muted-foreground">Name</span>
                <input
                  value={draft.name}
                  onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                  placeholder="z. B. Buchhaltung — Grundlagen"
                  className="mt-1 w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-2 text-sm outline-none focus:border-primary/50"
                />
              </label>
              <label className="block">
                <span className="text-[11px] text-muted-foreground">Rolle (frei wählbar)</span>
                <input
                  value={draft.role}
                  onChange={(e) => setDraft({ ...draft, role: e.target.value })}
                  placeholder="z. B. Buchhaltung"
                  className="mt-1 w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-2 text-sm outline-none focus:border-primary/50"
                />
              </label>
            </div>

            <div className="space-y-3">
              {draft.items.map((item, i) => (
                <div
                  key={i}
                  className="rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] p-4"
                >
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <span className="text-[11px] font-medium text-muted-foreground">
                      Aufgabe {i + 1}
                    </span>
                    {draft.items.length > 1 && (
                      <button
                        onClick={() =>
                          setDraft({
                            ...draft,
                            items: draft.items.filter((_, j) => j !== i),
                          })
                        }
                        className="text-muted-foreground/60 hover:text-red-400"
                        title="Aufgabe entfernen"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                  <div className="grid gap-2 sm:grid-cols-[2fr_1fr]">
                    <input
                      value={item.title ?? ""}
                      onChange={(e) => patchItem(i, { title: e.target.value })}
                      placeholder="Kurzer Titel — z. B. „USt korrekt berechnen“"
                      className="rounded-lg border border-foreground/[0.08] bg-background/60 px-3 py-1.5 text-xs outline-none focus:border-primary/50"
                    />
                    <label className="flex items-center gap-2 text-[11px] text-muted-foreground">
                      Gewicht
                      <input
                        type="number"
                        min={1}
                        max={100}
                        value={item.weight ?? 1}
                        onChange={(e) => patchItem(i, { weight: Number(e.target.value) })}
                        className="w-full rounded-lg border border-foreground/[0.08] bg-background/60 px-2 py-1.5 text-xs outline-none focus:border-primary/50"
                      />
                    </label>
                  </div>
                  <textarea
                    value={item.prompt}
                    onChange={(e) => patchItem(i, { prompt: e.target.value })}
                    rows={2}
                    placeholder="Der Auftrag an den Agenten"
                    className="mt-2 w-full rounded-lg border border-foreground/[0.08] bg-background/60 px-3 py-2 text-xs outline-none focus:border-primary/50"
                  />
                  <div className="mt-2 grid gap-2 sm:grid-cols-2">
                    <label className="block">
                      <span className="text-[10px] text-muted-foreground">
                        Muss enthalten (eine Angabe je Zeile)
                      </span>
                      <textarea
                        value={(item.expect_contains ?? []).join("\n")}
                        onChange={(e) =>
                          patchItem(i, { expect_contains: splitList(e.target.value) })
                        }
                        rows={2}
                        className="mt-1 w-full rounded-lg border border-foreground/[0.08] bg-background/60 px-3 py-1.5 font-mono text-[11px] outline-none focus:border-primary/50"
                      />
                    </label>
                    <label className="block">
                      <span className="text-[10px] text-muted-foreground">
                        Darf nicht enthalten
                      </span>
                      <textarea
                        value={(item.expect_absent ?? []).join("\n")}
                        onChange={(e) =>
                          patchItem(i, { expect_absent: splitList(e.target.value) })
                        }
                        rows={2}
                        className="mt-1 w-full rounded-lg border border-foreground/[0.08] bg-background/60 px-3 py-1.5 font-mono text-[11px] outline-none focus:border-primary/50"
                      />
                    </label>
                  </div>
                </div>
              ))}
              <button
                onClick={() => setDraft({ ...draft, items: [...draft.items, { ...EMPTY_ITEM }] })}
                className="inline-flex items-center gap-1.5 text-[11px] text-primary hover:underline"
              >
                <Plus className="h-3 w-3" />
                Aufgabe hinzufügen
              </button>
            </div>

            <p className="text-[10px] text-muted-foreground/50">
              Jede Aufgabe braucht mindestens eine Erwartung — eine Aufgabe, die nie
              durchfallen kann, ist kein Test und würde den Wert stillschweigend nach
              oben ziehen. Ändern sich die Aufgaben, steigt die Fassung: sonst könnte
              ein besserer Wert auch nur eine leichtere Aufgabe bedeuten.
            </p>

            <div className="flex items-center gap-2">
              <button
                onClick={save}
                disabled={busy}
                className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-40"
              >
                {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                Speichern
              </button>
              <button
                onClick={() => setDraft(null)}
                className="rounded-lg border border-foreground/[0.08] px-4 py-2 text-sm hover:bg-foreground/[0.06]"
              >
                Abbrechen
              </button>
            </div>
          </div>
        ) : loading ? (
          <div className="rounded-xl border border-foreground/[0.06] bg-card/50 p-8 text-sm text-muted-foreground">
            Lädt…
          </div>
        ) : sets.length === 0 ? (
          <div className="rounded-xl border border-dashed border-foreground/[0.1] bg-card/30 p-16 text-center">
            <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-foreground/[0.06]">
              <ClipboardCheck className="h-7 w-7 text-muted-foreground" />
            </div>
            <h3 className="mb-1.5 text-lg font-semibold">Noch keine Golden-Tests</h3>
            <p className="mx-auto mb-5 max-w-lg text-sm text-muted-foreground">
              Lege für eine Rolle ein paar Aufgaben mit erwartetem Ergebnis an. Vor
              jedem Update wird geprüft, ob der Agent sie noch löst — sonst merkt man
              eine Verschlechterung erst Wochen später am falschen Bericht.
            </p>
          </div>
        ) : (
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
            <div className="space-y-2">
              {sets.map((s) => (
                <div
                  key={s.id}
                  className={cn(
                    "rounded-xl border bg-card/80 p-4 transition-colors",
                    selectedId === s.id
                      ? "border-primary/40"
                      : "border-foreground/[0.06] hover:border-foreground/[0.12]",
                  )}
                >
                  <button
                    onClick={() => setSelectedId(selectedId === s.id ? null : s.id)}
                    className="flex w-full items-start justify-between gap-2 text-left"
                  >
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium">{s.name}</div>
                      <div className="text-[11px] text-muted-foreground/60">
                        {s.role || "ohne Rolle"} · {s.item_count} Aufgabe
                        {s.item_count !== 1 ? "n" : ""} · Fassung {s.version}
                      </div>
                    </div>
                    <ChevronRight
                      className={cn(
                        "mt-0.5 h-4 w-4 shrink-0 text-muted-foreground/40 transition-transform",
                        selectedId === s.id && "rotate-90",
                      )}
                    />
                  </button>
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    <button
                      onClick={() => run(s)}
                      disabled={busy || !agentId}
                      title={agentId ? "Gegen den gewählten Agenten laufen lassen" : "Erst einen Agenten wählen"}
                      className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-500/15 px-3 py-1.5 text-[11px] text-emerald-400 hover:bg-emerald-500/25 disabled:opacity-40"
                    >
                      <Play className="h-3 w-3" />
                      Laufen lassen
                    </button>
                    <button
                      onClick={() => edit(s)}
                      className="rounded-lg border border-foreground/[0.08] px-3 py-1.5 text-[11px] hover:bg-foreground/[0.06]"
                    >
                      Bearbeiten
                    </button>
                    <button
                      onClick={() => remove(s)}
                      className="ml-auto text-muted-foreground/60 hover:text-red-400"
                      title="Löschen"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              ))}
            </div>

            <div className="space-y-2">
              <h3 className="text-sm font-medium">
                Verlauf {selected ? `— ${selected.name}` : "(alle Sammlungen)"}
              </h3>
              {runsOfSelected.length === 0 ? (
                <div className="rounded-xl border border-dashed border-foreground/[0.1] p-8 text-center text-[12px] text-muted-foreground">
                  Noch kein Lauf. Ein Rückschritt zeigt sich erst an der Reihe —
                  der erste Lauf wird die Grundlinie.
                </div>
              ) : (
                runsOfSelected.map((r) => (
                  <div
                    key={r.id}
                    className="rounded-xl border border-foreground/[0.06] bg-card/80 p-4"
                  >
                    <button
                      onClick={() => setOpenRun(openRun === r.id ? null : r.id)}
                      className="flex w-full items-center gap-3 text-left"
                    >
                      <div
                        className={cn(
                          "flex h-9 w-12 shrink-0 items-center justify-center rounded-lg font-mono text-sm font-semibold",
                          r.status === "running"
                            ? "bg-foreground/[0.06] text-muted-foreground"
                            : r.regression
                            ? "bg-red-500/10 text-red-400"
                            : "bg-emerald-500/10 text-emerald-400",
                        )}
                      >
                        {r.status === "running" ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          r.score?.toFixed(0)
                        )}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2 text-[12px]">
                          <span className="font-medium">
                            {agents.find((a) => a.id === r.agent_id)?.name ?? r.agent_id}
                          </span>
                          {r.regression && (
                            <span className="inline-flex items-center gap-1 rounded-full border border-red-500/20 bg-red-500/10 px-1.5 py-0.5 text-[10px] text-red-400">
                              <TrendingDown className="h-2.5 w-2.5" />
                              Rückschritt
                            </span>
                          )}
                        </div>
                        <div className="text-[11px] text-muted-foreground/60">
                          {r.passed}/{r.total} bestanden · Fassung {r.set_version}
                          {r.baseline_score !== null
                            ? ` · Grundlinie ${r.baseline_score.toFixed(0)}`
                            : " · erster Lauf"}
                        </div>
                      </div>
                      <ChevronRight
                        className={cn(
                          "h-4 w-4 shrink-0 text-muted-foreground/40 transition-transform",
                          openRun === r.id && "rotate-90",
                        )}
                      />
                    </button>

                    {openRun === r.id && (
                      <div className="mt-3 space-y-2 border-t border-foreground/[0.06] pt-3">
                        {r.results.map((res) => (
                          <div key={res.id} className="text-[11px]">
                            <div className="flex items-center gap-1.5">
                              {res.ok ? (
                                <Check className="h-3 w-3 shrink-0 text-emerald-400" />
                              ) : (
                                <X className="h-3 w-3 shrink-0 text-red-400" />
                              )}
                              <span className="font-medium">{res.title || res.id}</span>
                              <span className="text-muted-foreground/40">
                                ×{res.weight}
                              </span>
                            </div>
                            {/* Ein Fehlschlag ohne Begruendung zwingt jemanden, von
                                Hand nachzustellen, was erwartet war. */}
                            {!res.ok && (
                              <ul className="ml-4.5 mt-1 space-y-0.5 text-muted-foreground/70">
                                {res.checks
                                  .filter((c) => !c.ok)
                                  .map((c, i) => (
                                    <li key={i}>
                                      {c.kind === "contains" && `fehlt: „${String(c.value)}“`}
                                      {c.kind === "absent" && `verboten, kam vor: „${String(c.value)}“`}
                                      {c.kind === "regex" && `Muster passt nicht: ${String(c.value)}`}
                                      {c.kind === "min_length" && `Antwort zu kurz (< ${String(c.value)})`}
                                      {c.kind === "task" && `Auftrag ${String(c.value)}`}
                                      {c.kind === "none" && "Aufgabe ohne Erwartung"}
                                      {c.error ? ` — ${c.error}` : ""}
                                    </li>
                                  ))}
                              </ul>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ))
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
