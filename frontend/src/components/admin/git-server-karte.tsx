"use client";

/** Karte „Eigene Git-Server“ unter Admin → Betrieb (#1194).
 *
 * Für Projekte mit selbst betriebenem GitLab, Forgejo oder Gitea:
 * - Zertifizierungsstelle der Firma (PEM). Gilt für Abfragen der Plattform, im
 *   Egress-Proxy und in den Agenten (nach dem Aktualisieren).
 * - Server im Firmennetz, die trotz interner Adresse abgefragt werden dürfen.
 *
 * Serverseitig nur für Administratoren (GET/PUT /settings/git-server), mit Prüfprotokoll.
 */

import { useCallback, useEffect, useState } from "react";
import { Loader2, ShieldCheck } from "lucide-react";
import * as api from "@/lib/api";
import type { GitServerStand } from "@/lib/api";
import { apiFehlertext } from "@/lib/api-fehler";
import { useToast } from "@/components/ui/dialog-provider";

export function GitServerKarte() {
  const toast = useToast();
  const [stand, setStand] = useState<GitServerStand | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);
  const [ca, setCa] = useState("");
  const [server, setServer] = useState("");
  const [speichert, setSpeichert] = useState(false);

  const uebernehmen = (s: GitServerStand) => {
    setStand(s);
    setCa(s.ca_zertifikate);
    setServer(s.interne_server.join("\n"));
  };

  const laden = useCallback(async () => {
    try {
      uebernehmen(await api.getGitServer());
      setFehler(null);
    } catch (e) {
      setFehler(apiFehlertext(e, "Unbekannter Fehler"));
    }
  }, []);

  useEffect(() => { laden(); }, [laden]);

  async function speichern() {
    setSpeichert(true);
    try {
      uebernehmen(await api.setGitServer({ ca_zertifikate: ca, interne_server: server }));
      toast.success("Gespeichert", "Agenten übernehmen die Zertifizierungsstelle nach dem nächsten Aktualisieren.");
    } catch (e) {
      toast.error("Nicht gespeichert", apiFehlertext(e));
    } finally {
      setSpeichert(false);
    }
  }

  if (fehler) {
    return (
      <div className="mb-4 rounded-xl border border-red-500/20 bg-red-500/5 p-4 text-sm text-red-500">
        Eigene Git-Server nicht abrufbar: {fehler}
      </div>
    );
  }
  if (!stand) {
    return (
      <div className="mb-4 flex items-center gap-2 rounded-xl border border-foreground/[0.06] bg-card/80 p-4 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Eigene Git-Server werden geladen …
      </div>
    );
  }

  const geaendert = ca.trim() !== stand.ca_zertifikate.trim() || server.trim() !== stand.interne_server.join("\n");

  return (
    <div className="mb-4 rounded-xl border border-foreground/[0.06] bg-card/80 p-4">
      <div className="flex items-start gap-3">
        <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold">Eigene Git-Server</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            Für Projekte auf einem selbst betriebenen GitLab, Forgejo oder Gitea. Die Zertifizierungsstelle
            der Firma gilt für die Plattform, den Egress-Proxy und die Agenten.
          </p>

          <label className="mt-3 block text-xs font-medium text-muted-foreground" htmlFor="git-ca">
            Zertifizierungsstelle der Firma (PEM)
          </label>
          <textarea
            id="git-ca"
            value={ca}
            onChange={(e) => setCa(e.target.value)}
            rows={4}
            spellCheck={false}
            placeholder="-----BEGIN CERTIFICATE-----"
            className="mt-1 w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-3 py-2 font-mono text-[11px] outline-none focus:border-primary/50"
          />
          {stand.stellen.length > 0 && (
            <ul className="mt-1 space-y-0.5 text-[11px] text-muted-foreground">
              {stand.stellen.map((s) => (
                <li key={s.name + s.gueltig_bis}>{s.name} · gültig bis {s.gueltig_bis}</li>
              ))}
            </ul>
          )}

          <label className="mt-3 block text-xs font-medium text-muted-foreground" htmlFor="git-intern">
            Server im Firmennetz
          </label>
          <textarea
            id="git-intern"
            value={server}
            onChange={(e) => setServer(e.target.value)}
            rows={2}
            spellCheck={false}
            placeholder="gitlab.intern.example.com"
            className="mt-1 w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-3 py-2 text-xs outline-none focus:border-primary/50"
          />
          <p className="mt-1 text-[11px] text-muted-foreground">
            Ein Servername je Zeile. Sonst fragt die Plattform keine Adressen im eigenen Netz ab. Über den
            Egress-Proxy nur auf Port 443.
          </p>

          <div className="mt-3 flex justify-end">
            <button
              type="button"
              onClick={speichern}
              disabled={speichert || !geaendert}
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-50"
            >
              {speichert && <Loader2 className="h-3.5 w-3.5 animate-spin" />} Speichern
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
