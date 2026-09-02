"use client";

import React from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";

interface Props {
  children: React.ReactNode;
  /** Wird beim Wechsel zurueckgesetzt — z.B. der Pfad der gezeigten Datei. */
  schluessel?: string;
  /** Kurze Beschreibung dessen, was hier kaputt ging. */
  bereich?: string;
}

interface State {
  fehler: Error | null;
}

/**
 * Faengt Fehler eines Teilbereichs ab, damit sie nicht die ganze Seite reissen.
 *
 * Anlass (18.08.2026): das Oeffnen einer Datei liess den PDF-Betrachter werfen
 * — und weil es im gesamten Frontend KEINE einzige Fehlergrenze gab, kippte
 * React den kompletten Baum. Der Nutzer sah nicht „Datei kaputt", sondern
 * „This page couldn't load" und war den Agenten los, an dem er gerade
 * arbeitete.
 *
 * Bewusst eine Klassenkomponente: Fehlergrenzen gibt es in React nur so.
 */
export class FehlerGrenze extends React.Component<Props, State> {
  state: State = { fehler: null };

  static getDerivedStateFromError(fehler: Error): State {
    return { fehler };
  }

  componentDidUpdate(vorher: Props) {
    // Neue Datei ausgewaehlt -> neuer Versuch. Ohne das bliebe die
    // Fehlermeldung stehen, bis die Seite neu geladen wird.
    if (vorher.schluessel !== this.props.schluessel && this.state.fehler) {
      this.setState({ fehler: null });
    }
  }

  componentDidCatch(fehler: Error, info: React.ErrorInfo) {
    console.error("Fehlergrenze hat gefangen:", fehler, info.componentStack);
  }

  render() {
    if (!this.state.fehler) return this.props.children;

    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-2 p-6 text-center">
        <AlertTriangle className="h-6 w-6 text-amber-700 dark:text-amber-400/70" />
        <p className="text-sm font-medium">
          {this.props.bereich ?? "Dieser Bereich"} konnte nicht angezeigt werden
        </p>
        <p className="max-w-md text-[11px] text-muted-foreground/60">
          {this.state.fehler.message}
        </p>
        <button
          onClick={() => this.setState({ fehler: null })}
          className="mt-1 inline-flex items-center gap-1.5 rounded-lg border border-foreground/[0.08] px-3 py-1.5 text-[11px] font-medium hover:bg-foreground/[0.04] transition-colors"
        >
          <RotateCcw className="h-3 w-3" />
          Nochmal versuchen
        </button>
        <p className="text-[10px] text-muted-foreground/40">
          Der Rest der Seite laeuft weiter.
        </p>
      </div>
    );
  }
}
