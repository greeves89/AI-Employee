"use client";

/** Ansichten, die ein Agent im Gespräch einblenden kann.
 *
 *  **Warum eine Liste und kein Markup vom Agenten.** Ein Modell, das HTML in
 *  die Oberfläche schreiben darf, ist ein Einfallstor mit Zwischenschritt —
 *  und der Inhalt, aus dem es schöpft (Webseiten, Dateien, E-Mails), kommt oft
 *  von außen. Der Agent wählt deshalb einen Namen aus dieser Liste und liefert
 *  Daten; gezeichnet wird hier. Der Server führt dieselbe Liste
 *  (`ERLAUBTE_ANSICHTEN` in `api/approvals.py`) und wirft alles andere weg.
 *
 *  **Warum es trotzdem immer Wortoptionen gibt.** Telegram, die Telefon-App
 *  und der reine Sprachbetrieb können keine Ansicht zeichnen. Jede Rückfrage
 *  mit Ansicht trägt dieselbe Frage als Optionsliste — wer die Ansicht nicht
 *  sieht, antwortet mit Worten. Ohne das wäre eine Ansicht eine Sackgasse für
 *  alle außerhalb des Browsers.
 *
 *  **Der Rückweg ist der Freigabe-Weg.** Eine Ansicht ist eine Rückfrage, die
 *  anders aussieht: derselbe Endpunkt, dasselbe Anhalten des Agenten, dasselbe
 *  `user_response`. Deshalb bekommt jede Ansicht schlicht ein `antworten`.
 */

import Image from "next/image";
import { useState } from "react";
import { Check, ImageOff, Loader2 } from "lucide-react";
import * as api from "@/lib/api";
import { cn } from "@/lib/utils";

export interface AgentViewProps {
  agentId: string;
  data: Record<string, unknown>;
  /** Die Wahl zurückgeben — geht denselben Weg wie eine Freigabe. */
  antworten: (antwort: string) => void | Promise<void>;
  /** Läuft gerade eine Antwort? Dann sperren, sonst zählt der Doppelklick zweimal. */
  beschaeftigt?: boolean;
}

interface Bild {
  path: string;
  label?: string;
}

/** Mehrere Bilder nebeneinander, der Nutzer wählt eines.
 *
 *  Der häufigste Fall überhaupt: ein Marketing-Agent erzeugt Varianten und
 *  will wissen, welche. Per Stimme oder in Worten ist „das dritte, mit dem
 *  größeren Schriftzug" mühsam — hier ist es ein Klick.
 *
 *  Die Bilder kommen als PFAD, nicht als Inhalt. Sie liegen in der Zeile der
 *  Rückfrage und gingen sonst durch Datenbank und Redis; ein einziges
 *  eingebettetes Bild sprengt beides. Geladen wird über den vorhandenen
 *  Dateiweg des Agenten, der über das Sitzungs-Cookie mitautorisiert — daher
 *  ist hier kein eigener Token nötig und es entsteht keine zweite Zugriffsregel.
 */
function ImageChoice({ agentId, data, antworten, beschaeftigt }: AgentViewProps) {
  const bilder = (Array.isArray(data.images) ? data.images : []) as Bild[];
  const [gewaehlt, setGewaehlt] = useState<string | null>(null);
  const [kaputt, setKaputt] = useState<Record<string, boolean>>({});

  if (bilder.length === 0) {
    // Kein stiller Leerraum: der Agent wartet, und der Nutzer müsste raten,
    // warum nichts da ist. Die Wortoptionen darunter funktionieren weiter.
    return (
      <p className="text-xs text-muted-foreground/60">
        Der Agent hat keine Bilder mitgeschickt — bitte unten in Worten antworten.
      </p>
    );
  }

  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
      {bilder.map((bild, i) => {
        const beschriftung = bild.label || `Bild ${i + 1}`;
        const laeuft = beschaeftigt && gewaehlt === beschriftung;
        return (
          <button
            key={bild.path || i}
            onClick={() => {
              setGewaehlt(beschriftung);
              antworten(beschriftung);
            }}
            disabled={beschaeftigt}
            className={cn(
              "group relative overflow-hidden rounded-xl border text-left transition-all",
              "disabled:cursor-not-allowed",
              gewaehlt === beschriftung
                ? "border-primary ring-1 ring-primary/30"
                : "border-border hover:border-primary/40"
            )}
          >
            <div className="relative aspect-[4/3] w-full bg-foreground/[0.03]">
              {kaputt[bild.path] ? (
                /* Ein fehlender Pfad darf nicht wie ein leeres Feld aussehen —
                   sonst hält der Nutzer die Ansicht für kaputt und antwortet
                   gar nicht, während der Agent wartet. */
                <div className="flex h-full flex-col items-center justify-center gap-1 text-muted-foreground/40">
                  <ImageOff className="h-5 w-5" />
                  <span className="text-[10px]">nicht gefunden</span>
                </div>
              ) : (
                <Image
                  src={api.getFileDownloadUrl(agentId, bild.path)}
                  alt={beschriftung}
                  fill
                  unoptimized
                  sizes="(max-width: 640px) 50vw, 240px"
                  className="object-cover"
                  onError={() => setKaputt((k) => ({ ...k, [bild.path]: true }))}
                />
              )}
              {laeuft && (
                <div className="absolute inset-0 flex items-center justify-center bg-background/60">
                  <Loader2 className="h-5 w-5 animate-spin text-primary" />
                </div>
              )}
              {gewaehlt === beschriftung && !laeuft && (
                <div className="absolute right-1.5 top-1.5 rounded-full bg-primary p-1">
                  <Check className="h-3 w-3 text-primary-foreground" />
                </div>
              )}
            </div>
            <p className="truncate px-2 py-1.5 text-[11px] text-muted-foreground group-hover:text-foreground">
              {beschriftung}
            </p>
          </button>
        );
      })}
    </div>
  );
}

/** Die Liste. Ein Name, den es hier nicht gibt, wird nicht gezeichnet — der
 *  Aufrufer fällt dann auf die Wortoptionen zurück. */
export const AGENT_VIEWS: Record<string, (p: AgentViewProps) => React.ReactElement> = {
  image_choice: ImageChoice,
};

export function AgentView(
  props: AgentViewProps & { name: string },
): React.ReactElement | null {
  const Ansicht = AGENT_VIEWS[props.name];
  return Ansicht ? <Ansicht {...props} /> : null;
}
