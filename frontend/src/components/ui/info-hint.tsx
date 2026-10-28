import { HelpCircle } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Kleiner Hilfe-Hinweis neben technischen Labels (Issue #787 Punkt 8, Rest-Lücke:
 * "Technische Labels ohne Erklärung für Nicht-Techniker"). Nutzt bewusst die native
 * `title`-Konvention, die im Projekt schon verwendet wird (z. B. page.tsx Neustart-Knopf),
 * macht sie aber über ein sichtbares Icon erst entdeckbar statt unsichtbar auf dem ganzen Element.
 */
export function InfoHint({ text, className }: { text: string; className?: string }) {
  return (
    <span title={text} aria-label={text} className="inline-flex shrink-0 cursor-help">
      <HelpCircle
        className={cn(
          "h-3.5 w-3.5 text-muted-foreground/40 hover:text-muted-foreground/70 transition-colors",
          className,
        )}
        aria-hidden="true"
      />
    </span>
  );
}
