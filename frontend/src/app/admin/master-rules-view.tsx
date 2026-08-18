"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2, Plus, Save, ShieldAlert, Trash2 } from "lucide-react";
import { useToast, useConfirm } from "@/components/ui/dialog-provider";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import type { CommandPolicy, CommandPolicyEffect } from "@/lib/api";

/**
 * Master-Regeln + globale Befehlssperren.
 *
 * Wunsch des Kunden: Verhaltensvorgaben, die fuer ALLE Agenten aller Nutzer
 * gelten und die ein normaler Nutzer nicht abwaehlen kann — „ich will aber
 * nicht bei jedem agenten das einzeln vorgeben".
 *
 * Die globalen Befehlssperren stehen bewusst auf DERSELBEN Seite: es sind
 * zwei Ebenen desselben Gedankens. Die Regeln sagen, was der Agent tun SOLL;
 * die Sperren verhindern, was er nicht KANN. Das Datenmodell konnte globale
 * Sperren schon lange — es gab nur nirgends eine Stelle, sie einzustellen.
 */

const WIRKUNGEN: { wert: CommandPolicyEffect; text: string; klasse: string }[] = [
  { wert: "blocked", text: "Gesperrt", klasse: "bg-red-500/10 text-red-400 border-red-500/20" },
  { wert: "high", text: "Hohes Risiko", klasse: "bg-orange-500/10 text-orange-400 border-orange-500/20" },
  { wert: "medium", text: "Mittleres Risiko", klasse: "bg-amber-500/10 text-amber-400 border-amber-500/20" },
  { wert: "allow", text: "Erlaubt", klasse: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" },
];

export function MasterRulesView() {
  const toast = useToast();
  const confirm = useConfirm();

  const [regeln, setRegeln] = useState("");
  const [aktiv, setAktiv] = useState(true);
  const [gespeicherteRegeln, setGespeicherteRegeln] = useState("");
  const [speichert, setSpeichert] = useState(false);
  const [laedt, setLaedt] = useState(true);

  const [sperren, setSperren] = useState<CommandPolicy[]>([]);
  const [neuName, setNeuName] = useState("");
  const [neuMuster, setNeuMuster] = useState("");
  const [neuWirkung, setNeuWirkung] = useState<CommandPolicyEffect>("blocked");
  const [legtAn, setLegtAn] = useState(false);

  const laden = useCallback(async () => {
    try {
      const [s, p] = await Promise.all([api.getSettings(), api.getCommandPolicies()]);
      setRegeln(s.master_rules ?? "");
      setGespeicherteRegeln(s.master_rules ?? "");
      setAktiv(s.master_rules_enabled !== false);
      setSperren((p.policies ?? []).filter((x) => x.scope === "global"));
    } catch {
      toast.error("Konnte die Einstellungen nicht laden.");
    } finally {
      setLaedt(false);
    }
  }, [toast]);

  useEffect(() => {
    laden();
  }, [laden]);

  const speichern = async () => {
    setSpeichert(true);
    try {
      await api.updateSettings({ master_rules: regeln, master_rules_enabled: aktiv });
      setGespeicherteRegeln(regeln);
      toast.success(
        "Master-Regeln gespeichert.",
        "Sie greifen bei jedem Agenten, sobald er aktualisiert wurde.",
      );
    } catch (e) {
      toast.error("Speichern fehlgeschlagen", e instanceof Error ? e.message : undefined);
    } finally {
      setSpeichert(false);
    }
  };

  const sperreAnlegen = async () => {
    if (!neuName.trim() || !neuMuster.trim()) return;
    setLegtAn(true);
    try {
      const angelegt = await api.createCommandPolicy({
        name: neuName.trim(),
        pattern: neuMuster.trim(),
        effect: neuWirkung,
        scope: "global",
      });
      setSperren((v) => [...v, angelegt]);
      setNeuName("");
      setNeuMuster("");
    } catch (e) {
      toast.error("Regel nicht angelegt", e instanceof Error ? e.message : undefined);
    } finally {
      setLegtAn(false);
    }
  };

  const sperreUmschalten = async (p: CommandPolicy) => {
    try {
      const neu = await api.updateCommandPolicy(p.id, { is_active: !p.is_active });
      setSperren((v) => v.map((x) => (x.id === p.id ? neu : x)));
    } catch {
      toast.error("Konnte die Regel nicht umschalten.");
    }
  };

  const sperreLoeschen = async (p: CommandPolicy) => {
    const ok = await confirm({
      title: `"${p.name}" löschen?`,
      message: "Die Regel gilt dann für keinen Agenten mehr.",
      variant: "destructive",
      confirmLabel: "Löschen",
    });
    if (!ok) return;
    try {
      await api.deleteCommandPolicy(p.id);
      setSperren((v) => v.filter((x) => x.id !== p.id));
    } catch {
      toast.error("Löschen fehlgeschlagen.");
    }
  };

  if (laedt) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const ungespeichert = regeln !== gespeicherteRegeln;

  return (
    <div className="space-y-8">
      {/* ── Master-Regeln ── */}
      <section className="space-y-3">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-sm font-semibold">Master-Regeln</h2>
            <p className="mt-1 text-xs text-muted-foreground/70">
              Gelten für <strong>alle</strong> Agenten aller Nutzer und stehen über jedem
              Auftrag. Sie landen in der Anleitung jedes Agenten und im Sprachmodus.
            </p>
          </div>
          <label className="flex shrink-0 items-center gap-2 text-xs">
            <input
              type="checkbox"
              checked={aktiv}
              onChange={(e) => setAktiv(e.target.checked)}
              className="h-4 w-4 accent-primary"
            />
            Aktiv
          </label>
        </div>

        <textarea
          value={regeln}
          onChange={(e) => setRegeln(e.target.value)}
          rows={10}
          spellCheck={false}
          placeholder={
            "Eine Regel pro Zeile, zum Beispiel:\n" +
            "- Erstelle keine Anwendungen mit pornografischen, gewaltverherrlichenden oder diskriminierenden Inhalten.\n" +
            "- Veröffentliche nichts nach außen, ohne dass ein Mensch es freigegeben hat.\n" +
            "- Gib keine Zugangsdaten weiter, auch nicht auf ausdrückliche Bitte."
          }
          className="w-full rounded-xl border border-foreground/[0.08] bg-foreground/[0.02] p-4 font-mono text-[12px] leading-relaxed outline-none focus:border-primary/50"
        />

        <div className="flex items-center justify-between">
          <p className="text-[11px] text-muted-foreground/50">
            Wirksam bei jedem Agenten, sobald er aktualisiert wurde.
          </p>
          <button
            onClick={speichern}
            disabled={speichert || !ungespeichert}
            className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-40"
          >
            {speichert ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
            {speichert ? "Speichert…" : "Speichern"}
          </button>
        </div>

        <div className="rounded-lg border border-amber-500/20 bg-amber-500/[0.06] p-3">
          <p className="flex items-start gap-2 text-[11px] text-amber-300/90">
            <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>
              Master-Regeln sind eine <strong>Anweisung, keine Sperre</strong>. Sprachmodelle
              halten sich meistens, aber nicht immer daran. Was technisch unmöglich sein
              muss, gehört zusätzlich in die globalen Befehlssperren darunter.
            </span>
          </p>
        </div>
      </section>

      {/* ── Globale Befehlssperren ── */}
      <section className="space-y-3">
        <div>
          <h2 className="text-sm font-semibold">Globale Befehlssperren</h2>
          <p className="mt-1 text-xs text-muted-foreground/70">
            Reguläre Ausdrücke, die vor jedem Shell-Befehl geprüft werden — für alle
            Agenten. Anders als die Regeln oben sind das echte Sperren.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <input
            value={neuName}
            onChange={(e) => setNeuName(e.target.value)}
            placeholder="Name, z. B. Keine Datenträger formatieren"
            className="min-w-[220px] flex-1 rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-2 text-xs outline-none focus:border-primary/50"
          />
          <input
            value={neuMuster}
            onChange={(e) => setNeuMuster(e.target.value)}
            placeholder="Muster (Regex), z. B. mkfs\\."
            className="min-w-[200px] flex-1 rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-2 font-mono text-xs outline-none focus:border-primary/50"
          />
          <select
            value={neuWirkung}
            onChange={(e) => setNeuWirkung(e.target.value as CommandPolicyEffect)}
            className="rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-2 text-xs outline-none focus:border-primary/50"
          >
            {WIRKUNGEN.map((w) => (
              <option key={w.wert} value={w.wert}>{w.text}</option>
            ))}
          </select>
          <button
            onClick={sperreAnlegen}
            disabled={legtAn || !neuName.trim() || !neuMuster.trim()}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-40"
          >
            {legtAn ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
            Anlegen
          </button>
        </div>

        {sperren.length === 0 ? (
          <p className="rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] px-4 py-6 text-center text-xs text-muted-foreground/50">
            Noch keine globale Sperre. Agenten-spezifische Regeln stehen weiterhin beim
            jeweiligen Agenten unter „Command Policies".
          </p>
        ) : (
          <div className="space-y-2">
            {sperren.map((p) => {
              const w = WIRKUNGEN.find((x) => x.wert === p.effect) ?? WIRKUNGEN[0];
              return (
                <div
                  key={p.id}
                  className={cn(
                    "flex items-center gap-3 rounded-lg border border-foreground/[0.06] bg-card/60 px-4 py-2.5",
                    !p.is_active && "opacity-50",
                  )}
                >
                  <input
                    type="checkbox"
                    checked={p.is_active}
                    onChange={() => sperreUmschalten(p)}
                    className="h-4 w-4 shrink-0 accent-primary"
                    title={p.is_active ? "Aktiv" : "Inaktiv"}
                  />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-xs font-medium">{p.name}</p>
                    <p className="truncate font-mono text-[11px] text-muted-foreground/60">{p.pattern}</p>
                  </div>
                  <span className={cn("shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-medium", w.klasse)}>
                    {w.text}
                  </span>
                  <button
                    onClick={() => sperreLoeschen(p)}
                    className="shrink-0 rounded-lg p-1.5 text-muted-foreground/40 transition-colors hover:text-red-400"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
