"use client";

import { useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { motion } from "framer-motion";
import { Cpu, KeyRound, Moon, Palette, Plug, Shield, ShieldCheck, Sun, UserRound, ArrowRight } from "lucide-react";
import { Header } from "@/components/layout/header";
import { MyAiCredentials } from "@/components/settings/my-ai-credentials";
import { ZweiFaktorEinstellungen } from "@/components/settings/zwei-faktor";
import { AvailableModels } from "@/components/settings/available-models";
import { PushToggle } from "@/components/settings/push-toggle";
import { useTheme } from "@/components/theme-provider";
import { useSimpleMode } from "@/hooks/use-simple-mode";
import { useAuthStore } from "@/lib/auth";
import { alteEinstellungenWeiterleitung } from "@/lib/admin-bereiche";
import { cn } from "@/lib/utils";

/** Sprungziele der frueheren Reiter, die hier geblieben sind. */
const ABSCHNITT_JE_REITER: Record<string, string> = {
  meine: "meine-ki-zugaenge",
  modelle: "verfuegbare-modelle",
  integrationen: "integrationen",
  konto: "sicherheit",
};

function Abschnitt({
  id,
  titel,
  icon: Icon,
  children,
}: {
  id: string;
  titel: string;
  icon: typeof Cpu;
  children: React.ReactNode;
}) {
  return (
    <section id={id} className="scroll-mt-20">
      <div className="mb-3 flex items-center gap-2">
        <Icon className="h-4 w-4 text-muted-foreground/60" />
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">{titel}</h2>
      </div>
      {children}
    </section>
  );
}

/**
 * „Meine Einstellungen“ (#899): nur, was dem angemeldeten Nutzer selbst gehoert —
 * eigene KI-Zugaenge, die fuer ihn freigegebenen Modelle, Benachrichtigungen in
 * diesem Browser und die Anzeige. Fuer jeden gleich, ob Admin oder Mitglied.
 *
 * Alles Anlagenweite steht in der Admin-Konsole. Diese Seite ruft deshalb keine
 * Schnittstelle auf, die einem Mitglied verwehrt ist.
 */
export function MeineEinstellungenView() {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const { theme, toggleTheme } = useTheme();
  const { simpleMode, istAdmin, mitgliederAnsicht, setMitgliederAnsicht } = useSimpleMode();

  // Alte Verweise (Lesezeichen, Handbuch, Hinweis-Streifen) auf die frueheren
  // Reiter: Anlagenweites fuehrt Admins in die Konsole, alles andere springt
  // zum passenden Abschnitt dieser Seite.
  useEffect(() => {
    if (!user) return;
    const tab = new URLSearchParams(window.location.search).get("tab");
    const ziel = alteEinstellungenWeiterleitung(tab, window.location.hash, user.role === "admin");
    if (ziel) {
      router.replace(ziel);
      return;
    }
    const abschnitt = (tab && ABSCHNITT_JE_REITER[tab]) || window.location.hash.slice(1);
    if (abschnitt) document.getElementById(abschnitt)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [user, router]);

  return (
    <div>
      <Header
        title="Meine Einstellungen"
        subtitle="Was nur dich betrifft: eigene KI-Zugänge, Benachrichtigungen und Anzeige"
      />

      <motion.div
        className="mx-auto max-w-4xl space-y-8 px-4 py-6 sm:px-8"
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        {istAdmin && !simpleMode && (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-500/20 bg-amber-500/[0.06] px-5 py-4">
            <div className="flex min-w-0 items-start gap-3">
              <Shield className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
              <p className="text-sm text-muted-foreground">
                Einstellungen für die ganze Anlage — Modelle &amp; Anbieter, Integrationen der
                Anlage, Sprache, System &amp; Lizenz — stehen in der Admin-Konsole.
              </p>
            </div>
            <Link
              href="/admin"
              className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-foreground/[0.06] px-3 py-1.5 text-sm font-medium transition-colors hover:bg-foreground/[0.1]"
            >
              Zur Admin-Konsole
              <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </div>
        )}

        <Abschnitt id="meine-ki-zugaenge" titel="Meine KI-Zugänge" icon={KeyRound}>
          <MyAiCredentials />
        </Abschnitt>

        {/* Anmeldesicherheit des eigenen Kontos (#915) — für jeden, nicht nur Admins. */}
        <Abschnitt id="sicherheit" titel="Anmeldung & Sicherheit" icon={ShieldCheck}>
          <ZweiFaktorEinstellungen />
        </Abschnitt>

        <Abschnitt id="verfuegbare-modelle" titel="Verfügbare Modelle" icon={Cpu}>
          <AvailableModels />
        </Abschnitt>

        <Abschnitt id="integrationen" titel="Integrationen und Benachrichtigungen" icon={Plug}>
          <div className="space-y-3">
            <div className="rounded-xl border border-foreground/[0.06] bg-card/80 p-5 backdrop-blur-sm">
              <PushToggle />
            </div>
            <p className="text-[12px] text-muted-foreground">
              Eigene Konten wie Microsoft 365, Google oder MCP-Server verbindest du unter{" "}
              <Link href="/integrations" className="text-primary hover:underline">Integrationen</Link>.
            </p>
          </div>
        </Abschnitt>

        <Abschnitt id="anzeige" titel="Anzeige" icon={Palette}>
          <div className="divide-y divide-foreground/[0.04] rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm">
            <div className="flex items-center justify-between gap-3 px-5 py-4">
              <div className="min-w-0">
                <div className="text-sm font-medium">Erscheinungsbild</div>
                <p className="mt-0.5 text-[11px] text-muted-foreground/70">Gilt für diesen Browser.</p>
              </div>
              <button
                type="button"
                onClick={toggleTheme}
                className="inline-flex shrink-0 items-center gap-2 rounded-lg border border-foreground/[0.08] px-3 py-1.5 text-sm font-medium transition-colors hover:bg-foreground/[0.04]"
              >
                {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
                {theme === "dark" ? "Helles Design" : "Dunkles Design"}
              </button>
            </div>
            {istAdmin && (
              <div className="flex items-center justify-between gap-3 px-5 py-4">
                <div className="min-w-0">
                  <div className="text-sm font-medium">Mitglieder-Ansicht</div>
                  <p className="mt-0.5 text-[11px] text-muted-foreground/70">
                    Zeigt die Oberfläche so, wie sie Nutzer ohne Admin-Rechte sehen.
                  </p>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={mitgliederAnsicht}
                  onClick={() => setMitgliederAnsicht(!mitgliederAnsicht)}
                  className={cn(
                    "inline-flex shrink-0 items-center gap-2 rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors",
                    mitgliederAnsicht
                      ? "border-violet-500/30 bg-violet-500/10 text-violet-600 dark:text-violet-400"
                      : "border-foreground/[0.08] hover:bg-foreground/[0.04]"
                  )}
                >
                  <UserRound className="h-4 w-4" />
                  {mitgliederAnsicht ? "An" : "Aus"}
                </button>
              </div>
            )}
          </div>
        </Abschnitt>
      </motion.div>
    </div>
  );
}
