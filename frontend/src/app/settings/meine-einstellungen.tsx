"use client";

import { useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { motion } from "framer-motion";
import { Cpu, FolderSync, KeyRound, Moon, Palette, Plug, Shield, ShieldCheck, Sun, UserRound, ArrowRight } from "lucide-react";
import { Header } from "@/components/layout/header";
import { MyAiCredentials } from "@/components/settings/my-ai-credentials";
import { ZweiFaktorEinstellungen } from "@/components/settings/zwei-faktor";
import { AvailableModels } from "@/components/settings/available-models";
import { PushToggle } from "@/components/settings/push-toggle";
import { BridgeOrdner } from "@/components/settings/bridge-ordner";
import { useTheme } from "@/components/theme-provider";
import { useSimpleMode } from "@/hooks/use-simple-mode";
import { useAuthStore } from "@/lib/auth";
import { alteEinstellungenWeiterleitung } from "@/lib/admin-bereiche";
import { cn } from "@/lib/utils";

/** Sprungziele der frueheren Reiter, die hier geblieben sind. */
const KONTO_BEREICHE = [
  { id: "sicherheit", titel: "Anmeldung & Sicherheit" },
  { id: "meine-ki-zugaenge", titel: "KI-Zugänge & Modelle" },
  { id: "integrationen", titel: "Meine Verbindungen" },
  { id: "bridge-ordner", titel: "Bridge & Ordner" },
  { id: "anzeige", titel: "Darstellung & Mitteilungen" },
];

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
 * Alles Anlagenweite steht im Admin-Bereich. Diese Seite ruft deshalb keine
 * Schnittstelle auf, die einem Mitglied verwehrt ist.
 */
export function MeineEinstellungenView() {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const { theme, toggleTheme } = useTheme();
  const { simpleMode, istAdmin, mitgliederAnsicht, setMitgliederAnsicht } = useSimpleMode();

  // Alte Verweise (Lesezeichen, Handbuch, Hinweis-Streifen) auf die frueheren
  // Reiter: Anlagenweites fuehrt Admins in den Admin-Bereich, alles andere springt
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
        title="Mein Konto"
        subtitle="Gilt nur für dich. Niemand sonst sieht diese Angaben, auch kein Admin."
      />

      <div className="mx-auto flex max-w-5xl flex-col gap-6 px-4 py-6 sm:px-8 lg:flex-row lg:items-start">
        {/* Unterbereiche wie im Entwurf (Redesign 2026-10): links eine kurze Liste,
            rechts die Abschnitte. Auf dem Handy eine waagerechte Zeile oben. */}
        <nav aria-label="Bereiche von Mein Konto" className="flex gap-1 overflow-x-auto lg:sticky lg:top-20 lg:w-52 lg:shrink-0 lg:flex-col">
          {KONTO_BEREICHE.map((b) => (
            <a
              key={b.id}
              href={`#${b.id}`}
              className="shrink-0 rounded-lg px-3 py-2 text-[14px] text-muted-foreground transition-colors hover:bg-card hover:text-foreground"
            >
              {b.titel}
            </a>
          ))}
        </nav>

      <motion.div
        className="min-w-0 flex-1 space-y-8"
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        {/* Anmeldesicherheit des eigenen Kontos (#915) — für jeden, nicht nur Admins. */}
        <Abschnitt id="sicherheit" titel="Anmeldung & Sicherheit" icon={ShieldCheck}>
          <ZweiFaktorEinstellungen />
        </Abschnitt>

        <Abschnitt id="meine-ki-zugaenge" titel="Meine KI-Zugänge" icon={KeyRound}>
          <MyAiCredentials />
        </Abschnitt>

        <Abschnitt id="verfuegbare-modelle" titel="Modelle, die du nutzen kannst" icon={Cpu}>
          <AvailableModels />
        </Abschnitt>

        <Abschnitt id="integrationen" titel="Meine Verbindungen" icon={Plug}>
          <p className="rounded-xl border border-border bg-card p-5 text-sm text-muted-foreground">
            Dein Postfach, deinen Kalender und weitere eigene Konten verbindest du unter{" "}
            <Link href="/integrations" className="font-medium text-primary hover:underline">Verbindungen</Link>.
            Dienste für alle richtet ein Admin im Admin-Bereich ein.
          </p>
        </Abschnitt>

        <Abschnitt id="bridge-ordner" titel="Bridge & Ordner" icon={FolderSync}>
          <BridgeOrdner />
        </Abschnitt>

        <Abschnitt id="anzeige" titel="Darstellung & Mitteilungen" icon={Palette}>
          <div className="rounded-xl border border-border bg-card p-5 mb-3">
            <PushToggle />
          </div>
          <div className="divide-y divide-border rounded-xl border border-border bg-card">
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
    </div>
  );
}
