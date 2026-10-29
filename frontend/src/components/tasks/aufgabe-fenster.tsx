"use client";

/**
 * Aufgaben öffnen sich überall im selben Fenster über der Seite — Aufgabenliste,
 * Agenten-Seite, Projekte, Verlauf, Benachrichtigungen. Man bleibt, wo man war.
 * Die Seite /tasks/[id] gibt es weiter: für Links von außen und für Strg/Cmd-Klick
 * (neuer Tab), den ``AufgabeLink`` bewusst durchlässt.
 */

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Fenster } from "@/components/ui/fenster";
import { TaskDetail } from "@/components/tasks/task-detail";

const AufgabeFensterKontext = createContext<{ oeffnen: (taskId: string) => void } | null>(null);

export function AufgabeFensterProvider({ children }: { children: ReactNode }) {
  const [taskId, setTaskId] = useState<string | null>(null);
  const schliessen = useCallback(() => setTaskId(null), []);
  const wert = useMemo(() => ({ oeffnen: setTaskId }), []);
  return (
    <AufgabeFensterKontext.Provider value={wert}>
      {children}
      <Fenster offen={taskId !== null} schliessen={schliessen} breite="max-w-5xl">
        {taskId && <TaskDetail key={taskId} taskId={taskId} imFenster zeigeAufgabe={setTaskId} />}
      </Fenster>
    </AufgabeFensterKontext.Provider>
  );
}

/** ``oeffnen(taskId)`` — im Fenster; ohne Provider (sollte nicht vorkommen) als Seite. */
export function useAufgabeFenster(): { oeffnen: (taskId: string) => void } {
  const kontext = useContext(AufgabeFensterKontext);
  const router = useRouter();
  return kontext ?? { oeffnen: (id: string) => router.push(`/tasks/${id}`) };
}

/** Link auf eine Aufgabe: ein normaler Klick öffnet das Fenster, Strg/Cmd/Mittelklick einen Tab. */
export function AufgabeLink({
  taskId, className, title, children,
}: { taskId: string; className?: string; title?: string; children: ReactNode }) {
  const { oeffnen } = useAufgabeFenster();
  return (
    <Link
      href={`/tasks/${taskId}`}
      className={className}
      title={title}
      onClick={(e) => {
        // Knöpfe IN der Zeile (Abbrechen, Wiederholen) haben schon reagiert.
        if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
        e.preventDefault();
        oeffnen(taskId);
      }}
    >
      {children}
    </Link>
  );
}
