"use client";

/**
 * Browser-Arbeitsfläche des Agenten (#828).
 *
 * Der Agent bedient Seiten im Auftrag des Nutzers; hier sieht der Nutzer dabei
 * zu und kann jederzeit selbst übernehmen. Das ist nicht nur Komfort: Meldet
 * sich der Nutzer selbst an, laufen Zugangsdaten und Einmalcodes weder durch
 * den Chat noch durch das Modell.
 *
 * Die Bilder kommen als JPEG über einen WebSocket, den der Orchestrator vom
 * Agenten-Container durchreicht. Eingaben gehen denselben Weg zurück.
 *
 * Prototyp-Stand aus #828: noch ohne Geltungsbereiche und Prüfprotokoll.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Hand, Loader2, MousePointer2, Unplug } from "lucide-react";
import { getApiUrl, getWsUrl } from "@/lib/config";

type Zustand = "verbinde" | "verbunden" | "getrennt" | "fehler";

interface Props {
  agentId: string;
}

export default function BrowserWorkspace({ agentId }: Props) {
  const [zustand, setZustand] = useState<Zustand>("verbinde");
  const [meldung, setMeldung] = useState<string | null>(null);
  const [steuert, setSteuert] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const bildRef = useRef<HTMLImageElement | null>(null);
  // In einem ref, damit die einmal registrierten Ereignisbehandler nicht bei
  // jedem Umschalten neu gebunden werden müssen.
  const steuertRef = useRef(false);
  useEffect(() => {
    steuertRef.current = steuert;
  }, [steuert]);

  useEffect(() => {
    let abgebrochen = false;
    let ws: WebSocket | null = null;

    (async () => {
      try {
        const token = localStorage.getItem("token");
        const tr = await fetch(`${getApiUrl()}/api/v1/ws/ticket`, {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!tr.ok) throw new Error("Anmeldung für den Bildstrom fehlgeschlagen");
        const { ticket } = await tr.json();
        if (abgebrochen) return;

        ws = new WebSocket(
          `${getWsUrl()}/api/v1/ws/agents/${agentId}/browser?ticket=${ticket}`,
        );
        wsRef.current = ws;

        ws.onmessage = (e) => {
          const n = JSON.parse(e.data);
          if (n.typ === "bild" && bildRef.current) {
            bildRef.current.src = `data:image/jpeg;base64,${n.daten}`;
            setZustand("verbunden");
          } else if (n.typ === "bereit") {
            setZustand("verbunden");
          } else if (n.typ === "fehler") {
            setMeldung(n.text);
            setZustand("fehler");
          }
        };
        ws.onerror = () => {
          if (!abgebrochen) setZustand("fehler");
        };
        ws.onclose = () => {
          if (!abgebrochen) setZustand((z) => (z === "fehler" ? z : "getrennt"));
        };
      } catch (e) {
        if (!abgebrochen) {
          setMeldung(e instanceof Error ? e.message : String(e));
          setZustand("fehler");
        }
      }
    })();

    return () => {
      abgebrochen = true;
      try {
        ws?.close();
      } catch {
        /* schon zu */
      }
      wsRef.current = null;
    };
  }, [agentId]);

  const senden = useCallback((ereignis: Record<string, unknown>) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(ereignis));
  }, []);

  /**
   * Bildpunkte im angezeigten Bild in Bildpunkte der Seite umrechnen.
   *
   * Das Bild wird in der Oberfläche skaliert dargestellt; ohne diese Umrechnung
   * landet jeder Klick daneben — und zwar umso weiter, je schmaler das Fenster.
   */
  const position = (e: React.MouseEvent<HTMLImageElement>) => {
    const bild = e.currentTarget;
    const rahmen = bild.getBoundingClientRect();
    const skalierungX = (bild.naturalWidth || rahmen.width) / rahmen.width;
    const skalierungY = (bild.naturalHeight || rahmen.height) / rahmen.height;
    return {
      x: Math.round((e.clientX - rahmen.left) * skalierungX),
      y: Math.round((e.clientY - rahmen.top) * skalierungY),
    };
  };

  const klick = (e: React.MouseEvent<HTMLImageElement>) => {
    if (!steuertRef.current) return;
    e.preventDefault();
    const { x, y } = position(e);
    for (const typ of ["mousePressed", "mouseReleased"]) {
      senden({ art: "maus", typ, x, y, taste: "left", klicks: 1 });
    }
  };

  const taste = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (!steuertRef.current) return;
    e.preventDefault();
    // Druckbare Zeichen am Stück einsetzen; Sondertasten als Tastenereignis.
    if (e.key.length === 1 && !e.ctrlKey && !e.metaKey) {
      senden({ art: "text", text: e.key });
    } else {
      senden({ art: "taste", typ: "keyDown", taste: e.key, code: e.code, text: "" });
      senden({ art: "taste", typ: "keyUp", taste: e.key, code: e.code, text: "" });
    }
  };

  const rad = (e: React.WheelEvent<HTMLImageElement>) => {
    if (!steuertRef.current) return;
    const { x, y } = position(e as unknown as React.MouseEvent<HTMLImageElement>);
    senden({ art: "rad", x, y, dx: e.deltaX, dy: e.deltaY });
  };

  return (
    <div className="flex h-full flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm">
          {zustand === "verbinde" && (
            <>
              <Loader2 className="h-4 w-4 animate-spin text-zinc-500" />
              <span className="text-zinc-500">Verbinde …</span>
            </>
          )}
          {zustand === "verbunden" && (
            <>
              <span className="h-2 w-2 rounded-full bg-emerald-500" />
              <span className="text-zinc-600 dark:text-zinc-400">
                {steuert ? "Du steuerst" : "Der Agent arbeitet"}
              </span>
            </>
          )}
          {(zustand === "getrennt" || zustand === "fehler") && (
            <>
              <Unplug className="h-4 w-4 text-amber-600 dark:text-amber-400" />
              <span className="text-amber-700 dark:text-amber-300">
                {meldung ?? "Verbindung beendet"}
              </span>
            </>
          )}
        </div>

        <button
          type="button"
          onClick={() => setSteuert((s) => !s)}
          disabled={zustand !== "verbunden"}
          className={`inline-flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm transition
            disabled:cursor-not-allowed disabled:opacity-50 ${
              steuert
                ? "border-emerald-600 bg-emerald-600 text-white hover:bg-emerald-700"
                : "border-zinc-300 text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
            }`}
        >
          {steuert ? <Hand className="h-4 w-4" /> : <MousePointer2 className="h-4 w-4" />}
          {steuert ? "Steuerung zurückgeben" : "Steuerung übernehmen"}
        </button>
      </div>

      {steuert && (
        <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
          Du steuerst jetzt selbst. Passwörter und Einmalcodes hier direkt in die
          Seite eingeben — sie laufen dann weder durch den Chat noch durch das
          Modell.
        </p>
      )}

      {/* tabIndex, damit der Bereich Tastatureingaben bekommen kann. */}
      <div
        tabIndex={0}
        onKeyDown={taste}
        className="flex-1 overflow-hidden rounded-lg border border-zinc-200 bg-zinc-950 outline-none focus:ring-2 focus:ring-emerald-500 dark:border-zinc-800"
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          ref={bildRef}
          alt="Browser des Agenten"
          onClick={klick}
          onWheel={rad}
          draggable={false}
          className={`h-full w-full object-contain ${steuert ? "cursor-crosshair" : "cursor-default"}`}
        />
      </div>
    </div>
  );
}
