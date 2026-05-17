"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { Code2, PenTool, Headphones, Package, Loader2, ArrowLeft, CheckCircle2 } from "lucide-react";
import { Header } from "@/components/layout/header";
import * as api from "@/lib/api";
import type { VerticalPackSummary, VerticalPackDetail } from "@/lib/api";

const ICONS: Record<string, typeof Code2> = {
  Code2,
  PenTool,
  Headphones,
};

function PackIcon({ name, className }: { name: string; className?: string }) {
  const Icon = ICONS[name] || Package;
  return <Icon className={className} />;
}

type Step = "select" | "preview" | "provisioning" | "done";

export default function OnboardingPage() {
  const router = useRouter();
  const [step, setStep] = useState<Step>("select");
  const [packs, setPacks] = useState<VerticalPackSummary[]>([]);
  const [detail, setDetail] = useState<VerticalPackDetail | null>(null);
  const [result, setResult] = useState<{ message: string; agents: { id: string; name: string }[] } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    api.listVerticalPacks()
      .then((d) => setPacks(d.packs))
      .catch(() => setError("Pakete konnten nicht geladen werden."))
      .finally(() => setLoading(false));
  }, []);

  const selectPack = async (slug: string) => {
    setLoading(true);
    setError("");
    try {
      const d = await api.getVerticalPack(slug);
      setDetail(d);
      setStep("preview");
    } catch {
      setError("Paket-Details konnten nicht geladen werden.");
    } finally {
      setLoading(false);
    }
  };

  const provision = async () => {
    if (!detail) return;
    setStep("provisioning");
    setError("");
    try {
      const res = await api.provisionVerticalPack(detail.slug);
      setResult({ message: res.message, agents: res.agents });
      setStep("done");
    } catch {
      setError("Einrichtung fehlgeschlagen. Bitte erneut versuchen.");
      setStep("preview");
    }
  };

  return (
    <div className="min-h-screen">
      <Header title="Onboarding" subtitle="Branchen-Paket wählen — startklare Agenten in einem Schritt" />

      <div className="mx-auto max-w-3xl p-6">
        {error && (
          <div className="mb-4 rounded-lg border border-red-500/20 bg-red-500/5 px-4 py-3 text-sm text-red-400">
            {error}
          </div>
        )}

        {loading && step === "select" && (
          <div className="flex items-center gap-2 text-muted-foreground py-16 justify-center">
            <Loader2 className="h-4 w-4 animate-spin" /> Lädt…
          </div>
        )}

        {/* Step 1 — select */}
        {step === "select" && !loading && (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            {packs.map((p) => (
              <button
                key={p.slug}
                onClick={() => selectPack(p.slug)}
                className="rounded-xl border border-foreground/[0.06] bg-card p-5 text-left hover:border-blue-500/40 transition-all"
              >
                <PackIcon name={p.icon} className="h-6 w-6 text-blue-500 mb-3" />
                <p className="text-sm font-semibold">{p.name}</p>
                <p className="text-xs text-muted-foreground mt-1 line-clamp-3">{p.description}</p>
                <p className="text-[11px] text-muted-foreground/60 mt-3">{p.agent_count} Agenten</p>
              </button>
            ))}
          </div>
        )}

        {/* Step 2 — preview */}
        {step === "preview" && detail && (
          <div className="space-y-5">
            <button
              onClick={() => { setStep("select"); setDetail(null); }}
              className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              <ArrowLeft className="h-3.5 w-3.5" /> Zurück
            </button>

            <div className="flex items-center gap-3">
              <PackIcon name={detail.icon} className="h-7 w-7 text-blue-500" />
              <div>
                <h2 className="text-lg font-semibold">{detail.name}</h2>
                <p className="text-sm text-muted-foreground">{detail.description}</p>
              </div>
            </div>

            <div className="rounded-xl border border-foreground/[0.06] bg-card p-4">
              <p className="text-xs font-medium text-muted-foreground mb-2">
                Agenten die erstellt werden ({detail.agents.length})
              </p>
              <div className="space-y-2">
                {detail.agents.map((a) => (
                  <div key={a.name} className="rounded-lg bg-foreground/[0.03] px-3 py-2">
                    <p className="text-sm font-medium">
                      {a.display_name}
                      {!a.available && <span className="text-amber-400 text-[11px] ml-2">(Template fehlt)</span>}
                    </p>
                    {a.description && <p className="text-xs text-muted-foreground mt-0.5">{a.description}</p>}
                  </div>
                ))}
              </div>
            </div>

            {detail.knowledge_entries.length > 0 && (
              <div className="rounded-xl border border-foreground/[0.06] bg-card p-4">
                <p className="text-xs font-medium text-muted-foreground mb-2">Wissens-Einträge</p>
                <div className="flex flex-wrap gap-2">
                  {detail.knowledge_entries.map((k) => (
                    <span key={k.title} className="text-xs rounded-full bg-blue-500/10 text-blue-400 px-3 py-1">
                      {k.title}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {detail.demo_task && (
              <div className="rounded-xl border border-foreground/[0.06] bg-card p-4">
                <p className="text-xs font-medium text-muted-foreground mb-1">Erste Demo-Aufgabe</p>
                <p className="text-sm font-medium">{detail.demo_task.title}</p>
                <p className="text-xs text-muted-foreground mt-0.5">{detail.demo_task.prompt}</p>
              </div>
            )}

            <button
              onClick={provision}
              className="w-full rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-500 transition-colors"
            >
              Paket einrichten
            </button>
          </div>
        )}

        {/* Step 3 — provisioning */}
        {step === "provisioning" && (
          <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
            <Loader2 className="h-8 w-8 animate-spin mb-3" />
            <p className="text-sm">Agenten werden erstellt…</p>
          </div>
        )}

        {/* Step 4 — done */}
        {step === "done" && result && (
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <CheckCircle2 className="h-12 w-12 text-emerald-500 mb-4" />
            <h2 className="text-lg font-semibold">Einrichtung abgeschlossen</h2>
            <p className="text-sm text-muted-foreground mt-1">{result.message}</p>
            <div className="mt-5 w-full max-w-sm space-y-2">
              {result.agents.map((a) => (
                <div key={a.id} className="flex items-center gap-2 rounded-lg bg-foreground/[0.03] px-3 py-2">
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
                  <span className="text-sm">{a.name}</span>
                </div>
              ))}
            </div>
            <button
              onClick={() => router.push("/agents")}
              className="mt-6 rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-blue-500 transition-colors"
            >
              Zu den Agenten
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
