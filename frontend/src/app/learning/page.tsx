"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Sparkles,
  Loader2,
  BookOpen,
  Wrench,
  Brain,
  Moon,
  AlertCircle,
  CheckCircle2,
  RotateCcw,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { getSelfImprovement, type SelfImprovement, type LearnedSkill } from "@/lib/api";

/**
 * Was hat die Plattform dazugelernt?
 *
 * Die Mechanik lief längst: die Nachtschicht schreibt Skill-Entwürfe, der
 * Verbesserungs-Motor überarbeitet schlecht bewertete Skills, aus Gesprächen entstehen
 * dauerhafte Erinnerungen. Nur sah das niemand — es gab keine Fläche, auf der steht,
 * was dabei herauskommt. Sichtbar zu machen, was ohnehin passiert, ist hier die
 * gesamte Arbeit; erhoben wird nichts zusätzlich.
 */
const ORIGIN_LABEL: Record<string, string> = {
  nachtschicht: "Nachtschicht",
  agent: "Agent",
  import: "Import",
  mensch: "Mensch",
};

const STATUS_STYLE: Record<string, string> = {
  draft: "bg-amber-500/10 text-amber-400 border-amber-500/20",
  active: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  validated: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  rolled_back: "bg-red-500/10 text-red-400 border-red-500/20",
};

function Kennzahl({
  label,
  value,
  hint,
  icon: Icon,
}: {
  label: string;
  value: number;
  hint: string;
  icon: React.ElementType;
}) {
  return (
    <div className="rounded-xl border border-foreground/[0.06] bg-card/80 p-4 backdrop-blur-sm">
      <div className="flex items-center gap-2">
        <Icon className="h-3.5 w-3.5 text-primary" />
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground/50">{label}</span>
      </div>
      <div className="mt-1 text-2xl font-semibold">{value}</div>
      <div className="mt-0.5 text-[11px] text-muted-foreground/60">{hint}</div>
    </div>
  );
}

function SkillRow({ skill }: { skill: LearnedSkill }) {
  return (
    <div className="flex items-start gap-3 border-b border-foreground/[0.04] py-3 last:border-0">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[13px] font-medium">{skill.name}</span>
          <span
            className={cn(
              "rounded-full border px-2 py-0.5 text-[10px] font-medium",
              STATUS_STYLE[skill.status] ?? "border-zinc-500/20 bg-zinc-500/10 text-zinc-400"
            )}
          >
            {skill.status === "draft" ? "Entwurf" : skill.status}
          </span>
          {skill.version > 1 && (
            <span className="text-[10px] text-muted-foreground/50">v{skill.version}</span>
          )}
        </div>
        {skill.description && (
          <p className="mt-0.5 line-clamp-2 text-[11px] text-muted-foreground/60">
            {skill.description}
          </p>
        )}
      </div>
      <div className="shrink-0 text-right">
        <div className="text-[11px] text-muted-foreground/60">
          {ORIGIN_LABEL[skill.origin] ?? skill.origin}
        </div>
        <div className="text-[10px] text-muted-foreground/40">
          {skill.usage_count}× genutzt
          {skill.avg_rating !== null ? ` · ${skill.avg_rating} ★` : ""}
        </div>
      </div>
    </div>
  );
}

function Abschnitt({
  title,
  hint,
  skills,
  empty,
}: {
  title: string;
  hint: string;
  skills: LearnedSkill[];
  empty: string;
}) {
  return (
    <section className="rounded-xl border border-foreground/[0.06] bg-card/80 p-5 backdrop-blur-sm">
      <h2 className="text-sm font-semibold">{title}</h2>
      <p className="mt-0.5 mb-3 text-[11px] text-muted-foreground/60">{hint}</p>
      {skills.length === 0 ? (
        <p className="py-4 text-center text-[12px] text-muted-foreground/50">{empty}</p>
      ) : (
        <div>
          {skills.map((s) => (
            <SkillRow key={s.id} skill={s} />
          ))}
        </div>
      )}
    </section>
  );
}

export default function LearningPage() {
  const [data, setData] = useState<SelfImprovement | null>(null);
  const [days, setDays] = useState(30);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await getSelfImprovement(days));
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => {
    load();
  }, [load]);

  const s = data?.summary;

  return (
    <div className="flex h-full min-h-0 flex-col gap-6 overflow-y-auto p-6">
      <div className="flex flex-col items-start gap-3 pl-10 sm:flex-row sm:items-center sm:justify-between sm:pl-0">
        <div className="flex items-center gap-3">
          <Sparkles className="h-6 w-6 text-primary" />
          <h1 className="text-xl font-semibold">Was dazugelernt wurde</h1>
        </div>
        <select
          value={days}
          onChange={(e) => setDays(parseInt(e.target.value, 10))}
          className="rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-1.5 text-[12px] outline-none focus:border-primary/50"
        >
          <option value={7}>Letzte 7 Tage</option>
          <option value={30}>Letzte 30 Tage</option>
          <option value={90}>Letzte 90 Tage</option>
        </select>
      </div>

      {loading ? (
        <div className="flex flex-1 items-center justify-center">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : !data || !s ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2">
          <AlertCircle className="h-6 w-6 text-muted-foreground/40" />
          <p className="text-sm text-muted-foreground">Noch keine Daten.</p>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Kennzahl
              label="Neue Fähigkeiten"
              value={s.skills_learned}
              hint="von Agenten oder der Nachtschicht"
              icon={BookOpen}
            />
            <Kennzahl
              label="Überarbeitet"
              value={s.skills_improved}
              hint={`${s.improvements_kept} behalten · ${s.improvements_reverted} verworfen`}
              icon={Wrench}
            />
            <Kennzahl
              label="Gemerktes"
              value={s.memories_from_reflection}
              hint="dauerhafte Erinnerungen aus Gesprächen"
              icon={Brain}
            />
            <Kennzahl
              label="Nachtläufe"
              value={s.reflection_runs}
              hint="nächtliche Reflexion"
              icon={Moon}
            />
          </div>

          {s.skills_awaiting_review > 0 && (
            <div className="flex items-start gap-2 rounded-xl border border-amber-500/30 bg-amber-500/[0.07] p-3 text-[12px] text-amber-700 dark:text-amber-300">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>
                {s.skills_awaiting_review} Entwurf/Entwürfe warten auf deine Durchsicht.
                Bis dahin nutzt sie niemand.
              </span>
            </div>
          )}

          <Abschnitt
            title="Wartet auf Durchsicht"
            hint="Aus Läufen abgeleitet, aber noch von niemandem freigegeben."
            skills={data.awaiting_review}
            empty="Nichts offen."
          />

          <Abschnitt
            title="Selbst entstanden"
            hint="Fähigkeiten, die nicht von Hand angelegt wurden."
            skills={data.learned}
            empty="In diesem Zeitraum ist nichts Neues entstanden."
          />

          <Abschnitt
            title="Nachgebessert"
            hint="Überarbeitet, weil die Bewertungen schlecht waren. Wurde es danach nicht besser, wird zurückgenommen."
            skills={data.improved}
            empty="Nichts überarbeitet."
          />

          {data.runs.length > 0 && (
            <section className="rounded-xl border border-foreground/[0.06] bg-card/80 p-5 backdrop-blur-sm">
              <h2 className="text-sm font-semibold">Nachtläufe</h2>
              <p className="mt-0.5 mb-3 text-[11px] text-muted-foreground/60">
                Was jede Nacht dabei herauskam.
              </p>
              <div className="overflow-x-auto">
                <table className="w-full text-[12px]">
                  <thead>
                    <tr className="border-b border-foreground/[0.06] text-[10px] uppercase tracking-wide text-muted-foreground/50">
                      <th className="pb-2 text-left">Lauf</th>
                      <th className="pb-2 text-right">Gemerkt</th>
                      <th className="pb-2 text-right">Entwürfe</th>
                      <th className="pb-2 text-right">Wissen</th>
                      <th className="pb-2 text-right">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-foreground/[0.04]">
                    {data.runs.map((r) => (
                      <tr key={r.id}>
                        <td className="py-2">
                          {r.started_at ? new Date(r.started_at).toLocaleDateString("de-DE") : "–"}
                        </td>
                        <td className="py-2 text-right">{r.facts_new}</td>
                        <td className="py-2 text-right">{r.skills_drafted}</td>
                        <td className="py-2 text-right">{r.kb_entries}</td>
                        <td className="py-2 text-right">
                          {r.status === "completed" ? (
                            <CheckCircle2 className="ml-auto h-3.5 w-3.5 text-emerald-500" />
                          ) : (
                            <RotateCcw className="ml-auto h-3.5 w-3.5 text-muted-foreground/40" />
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </>
      )}
    </div>
  );
}
