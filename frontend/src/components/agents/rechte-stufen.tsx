"use client";

import { Eye, Lock, MessageCircleQuestionMark, Rocket } from "lucide-react";
import { cn } from "@/lib/utils";

/** Rechte in drei Stufen (#1047) — statt L1–L4 und Matrix für Einsteiger.
 *
 * Jede Stufe ist eine der bestehenden Voreinstellungen; der Server kennt nichts
 * Neues. L2 („Empfehlungen“) und eine von Hand angepasste Matrix bleiben über
 * die Feineinstellung erreichbar und erscheinen hier als „Eigene Einstellung“.
 * Die Texte sind ehrlich (#910): „Fragt vorher“ arbeitet im eigenen Bereich
 * ohne Rückfrage und fragt erst, bevor etwas nach außen geht.
 */
export const RECHTE_STUFEN = [
  {
    stufe: "l1",
    name: "Nur zuschauen",
    text: "Liest und sucht, ändert nichts. Gut zum Ausprobieren.",
    icon: Eye,
  },
  {
    stufe: "l3",
    name: "Fragt vorher",
    text: "Erledigt die Arbeit in seinem Bereich selbst und fragt, bevor etwas nach außen geht: Mails, fremde Dienste, Käufe.",
    icon: MessageCircleQuestionMark,
    empfohlen: true,
  },
  {
    stufe: "l4",
    name: "Darf selbst",
    text: "Handelt eigenständig, auch nach außen, ohne Rückfrage. Nur für Abläufe, die du gut kennst.",
    icon: Rocket,
  },
] as const;

/** Anzeigename einer gespeicherten Stufe — auch für L2 und angepasste Matrizen. */
export function rechteStufeName(stufe: string | null | undefined): string {
  return RECHTE_STUFEN.find((s) => s.stufe === (stufe ?? "l3"))?.name ?? "Eigene Einstellung";
}

export function RechteStufen({
  wert,
  setWert,
  gesperrt,
  disabled = false,
}: {
  wert: string;
  setWert: (stufe: string) => void;
  /** Liegt die Stufe über der Grenze der eigenen Rolle? */
  gesperrt?: (stufe: string) => boolean;
  disabled?: boolean;
}) {
  return (
    <div role="radiogroup" aria-label="Was darf der Agent?" className="grid gap-2 sm:grid-cols-3">
      {RECHTE_STUFEN.map((s) => {
        const Icon = s.icon;
        const aktiv = wert === s.stufe;
        // Bestandsschutz: was der Agent schon hat, bleibt wählbar.
        const zu = !aktiv && (gesperrt?.(s.stufe) ?? false);
        return (
          <button
            key={s.stufe}
            type="button"
            role="radio"
            aria-checked={aktiv}
            disabled={disabled || zu}
            onClick={() => setWert(s.stufe)}
            title={zu ? "Deine Rolle erlaubt diese Stufe nicht." : undefined}
            className={cn(
              "flex flex-col items-start gap-1.5 rounded-xl border p-3 text-left transition-colors disabled:cursor-not-allowed",
              aktiv
                ? "border-primary/40 bg-primary/[0.08]"
                : "border-foreground/[0.08] hover:bg-foreground/[0.04]",
              zu && "opacity-40",
            )}
          >
            <span className="flex w-full items-center gap-2">
              <Icon className={cn("h-4 w-4", aktiv ? "text-primary" : "text-muted-foreground")} />
              <span className="text-sm font-medium">{s.name}</span>
              {zu ? (
                <Lock className="ml-auto h-3 w-3 text-muted-foreground" />
              ) : "empfohlen" in s && s.empfohlen ? (
                <span className="ml-auto rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
                  Empfohlen
                </span>
              ) : null}
            </span>
            <span className="text-[12px] leading-snug text-muted-foreground">{s.text}</span>
          </button>
        );
      })}
    </div>
  );
}
