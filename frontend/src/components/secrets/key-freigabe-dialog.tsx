"use client";

/** Einen Key an einzelne Personen freigeben — an EINER Stelle.
 *
 *  Gebraucht in der Admin-Konsole (Key Management) und beim Agenten (Integrationen
 *  und Keys), wo Mitglieder ihre Keys anlegen. Freigegeben wird an Personen, nie an
 *  Rollen — das macht ein Admin in den Rollen. Der Server prüft das ohnehin
 *  (PUT /secrets/{id}/shares, nur Besitzer oder Admin).
 */

import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import * as api from "@/lib/api";
import type { AgentSecretEntry } from "@/lib/api";

interface Props {
  secret: AgentSecretEntry;
  onClose: () => void;
  onSaved: (meldung: string) => void;
  onError: (meldung: string) => void;
}

export function KeyFreigabeDialog({ secret, onClose, onSaved, onError }: Props) {
  const [personen, setPersonen] = useState<{ id: string; name: string; email?: string }[]>([]);
  const [auswahl, setAuswahl] = useState<Set<string>>(new Set());
  const [suche, setSuche] = useState("");
  const [laedt, setLaedt] = useState(true);
  const [speichert, setSpeichert] = useState(false);
  // Rueckrufe per Ref: Die Aufrufer uebergeben neue Funktionen bei jedem Rendern —
  // als Abhaengigkeit des Ladens wuerde der Dialog sonst endlos neu laden.
  const rueckruf = useRef({ onClose, onError });
  rueckruf.current = { onClose, onError };

  useEffect(() => {
    let weg = false;
    (async () => {
      try {
        const [{ users }, { shares }] = await Promise.all([api.listAppShareDirectory(), api.getSecretShares(secret.id)]);
        if (weg) return;
        setPersonen(users);
        setAuswahl(new Set(shares.map((x) => x.user_id)));
      } catch (e) {
        if (!weg) {
          rueckruf.current.onError(e instanceof Error ? e.message : "Freigaben konnten nicht geladen werden");
          rueckruf.current.onClose();
        }
      } finally {
        if (!weg) setLaedt(false);
      }
    })();
    return () => { weg = true; };
  }, [secret.id]);

  async function speichern() {
    setSpeichert(true);
    try {
      const { shares } = await api.setSecretShares(secret.id, [...auswahl]);
      onSaved(shares.length
        ? `„${secret.name}“ ist für ${shares.length} Person${shares.length === 1 ? "" : "en"} freigegeben.`
        : `„${secret.name}“ ist für niemanden mehr freigegeben.`);
      onClose();
    } catch (e) {
      onError(e instanceof Error ? e.message : "Freigabe fehlgeschlagen");
    } finally {
      setSpeichert(false);
    }
  }

  const treffer = personen.filter((p) => `${p.name} ${p.email ?? ""}`.toLowerCase().includes(suche.trim().toLowerCase()));

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="w-full max-w-md rounded-2xl border border-border bg-card p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-sm font-semibold">„{secret.name}“ freigeben</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Wer ihn bekommt, kann ihn eigenen Agenten zuweisen. Den Wert sieht niemand, ändern und
          weitergeben kann nur der Besitzer.
        </p>
        <input
          value={suche}
          onChange={(e) => setSuche(e.target.value)}
          placeholder="Person suchen …"
          className="mt-3 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
        />
        <div className="mt-2 max-h-64 overflow-y-auto">
          {laedt && <Loader2 className="mx-auto my-4 h-4 w-4 animate-spin text-muted-foreground" />}
          {!laedt && treffer.map((p) => (
            <label key={p.id} className="flex cursor-pointer items-center gap-3 rounded-lg px-2 py-2 hover:bg-foreground/[0.04]">
              <input
                type="checkbox"
                checked={auswahl.has(p.id)}
                onChange={() => setAuswahl((alt) => {
                  const neu = new Set(alt);
                  if (neu.has(p.id)) neu.delete(p.id); else neu.add(p.id);
                  return neu;
                })}
              />
              <span className="min-w-0">
                <span className="block truncate text-sm">{p.name}</span>
                {p.email && <span className="block truncate text-[11px] text-muted-foreground">{p.email}</span>}
              </span>
            </label>
          ))}
          {!laedt && personen.length === 0 && <p className="px-2 py-3 text-xs text-muted-foreground">Keine weiteren Personen.</p>}
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button onClick={onClose} className="rounded-lg px-3 py-1.5 text-sm text-muted-foreground hover:bg-foreground/[0.04]">
            Abbrechen
          </button>
          <button
            onClick={speichern}
            disabled={speichert || laedt}
            className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-50"
          >
            {speichert && <Loader2 size={13} className="animate-spin" />} Freigabe speichern
          </button>
        </div>
      </div>
    </div>
  );
}
