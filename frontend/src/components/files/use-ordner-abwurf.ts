"use client";

import { useCallback, useState } from "react";
import * as api from "@/lib/api";

/**
 * Dateien aus dem Betriebssystem auf einen Ordner im Dateibaum ziehen.
 *
 * Es gibt ZWEI Dateibaeume — den im Arbeitsbereich eines Agenten und den
 * agentenuebergreifenden unter /files. Die Abwurf-Logik liegt deshalb hier und
 * nicht in einem von beiden: zwei Fassungen desselben Verhaltens laufen
 * erfahrungsgemaess auseinander, sobald eine davon nachgebessert wird.
 */
export function useOrdnerAbwurf(opts: {
  /**
   * Uebersetzt das Abwurf-Ziel in Agent + Pfad. Im Arbeitsbereich EINES Agenten
   * ist das Ziel schlicht der Pfad; im agentenuebergreifenden Baum steckt die
   * Agentenkennung mit drin, weil dort mehrere Baeume nebeneinander stehen.
   */
  aufloesen: (ziel: string) => { agentId: string; pfad: string };
  /** Nach erfolgreichem Upload: Zielordner neu einlesen (und ggf. aufklappen). */
  nachAbwurf: (ziel: string) => void | Promise<void>;
  melden: {
    success: (titel: string, text?: string) => void;
    error: (titel: string, text?: string) => void;
  };
}) {
  const { aufloesen, nachAbwurf, melden } = opts;
  //: Ordner, ueber dem der Zeiger schwebt — nur fuer die Hervorhebung.
  const [dropZiel, setDropZiel] = useState<string | null>(null);
  //: Ordner, in den gerade hochgeladen wird.
  const [dropLaeuft, setDropLaeuft] = useState<string | null>(null);

  // Nur echte Datei-Zuege annehmen. Ohne diese Pruefung leuchtet der Baum auch
  // auf, wenn jemand Text markiert und verschiebt.
  const istDateiZug = (e: React.DragEvent) =>
    Array.from(e.dataTransfer.types || []).includes("Files");

  const beiDragOver = useCallback((e: React.DragEvent, ziel: string) => {
    if (!istDateiZug(e)) return;
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = "copy";
    setDropZiel(ziel);
  }, []);

  const beiDragLeave = useCallback((e: React.DragEvent) => {
    // Zwischen den Symbolen einer Zeile feuert `dragleave` staendig — ohne
    // diese Pruefung flackert die Hervorhebung.
    if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
    setDropZiel(null);
  }, []);

  const beiDrop = useCallback(
    async (e: React.DragEvent, ziel: string) => {
      if (!istDateiZug(e)) return;
      e.preventDefault();
      e.stopPropagation();
      setDropZiel(null);

      // Ganze Ordner kann die Upload-Schnittstelle nicht. Das hier sagt es,
      // statt mit einer leeren 0-Byte-Datei zu scheitern.
      const ordner = Array.from(e.dataTransfer.items || [])
        .map((it) => (it.webkitGetAsEntry ? it.webkitGetAsEntry() : null))
        .filter((eintrag) => eintrag?.isDirectory);
      if (ordner.length) {
        melden.error("Ordner koennen nicht hochgeladen werden", "Zieh die einzelnen Dateien herein.");
        return;
      }

      const dateien = Array.from(e.dataTransfer.files || []);
      if (!dateien.length) return;

      setDropLaeuft(ziel);
      try {
        const { agentId, pfad } = aufloesen(ziel);
        await api.uploadFiles(agentId, pfad, dateien);
        melden.success(
          dateien.length === 1
            ? `"${dateien[0].name}" hochgeladen`
            : `${dateien.length} Dateien hochgeladen`,
          aufloesen(ziel).pfad,
        );
        await nachAbwurf(ziel);
      } catch (err) {
        melden.error("Upload fehlgeschlagen", err instanceof Error ? err.message : undefined);
      } finally {
        setDropLaeuft(null);
      }
    },
    [aufloesen, nachAbwurf, melden],
  );

  return { dropZiel, dropLaeuft, beiDragOver, beiDragLeave, beiDrop };
}
