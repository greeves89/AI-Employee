"use client";

// Mein eigenes Claude-/Codex-Abo verbinden.
//
// Die Schnittstelle dafuer gibt es seit v1.185.0 — die Oberflaeche nicht. Kein
// Nutzer konnte sein Abo hinterlegen, obwohl die Plattform es an drei Stellen
// voraussetzt (Agenten-Anlage, Fehlermeldung „verbinde dein eigenes Abo",
// Rueckfall-Kette in agent_credentials). Aufgefallen am 2026-08-15, als die
// Agenten-Anlage genau darauf verwies.
//
// Bewusst KEINE Anzeige des Geheimnisses: die Schnittstelle gibt es nicht
// zurueck, und das soll auch so bleiben. Man sieht, DASS etwas hinterlegt ist,
// wann es zuletzt benutzt wurde und ob es funktioniert hat — mehr braucht man
// nicht, um es zu verwalten.

import { useCallback, useEffect, useState } from "react";
import { Check, KeyRound, Loader2, Trash2, TriangleAlert } from "lucide-react";
import * as api from "@/lib/api";

type Zugang = {
  harness: string;
  label: string | null;
  last_status: string | null;
  last_used_at: string | null;
  created_at: string | null;
};

const HARNESSE: { id: string; name: string; hilfe: string }[] = [
  {
    id: "claude_code",
    name: "Claude",
    hilfe: "OAuth-Token aus `claude setup-token`, oder ein API-Schlüssel (sk-ant-…).",
  },
  {
    id: "codex",
    name: "Codex",
    hilfe: "Der vollständige Inhalt deiner `auth.json` aus `~/.codex/`.",
  },
];

export function MyAiCredentials() {
  const [zugaenge, setZugaenge] = useState<Zugang[]>([]);
  const [teamlizenz, setTeamlizenz] = useState(false);
  const [laedt, setLaedt] = useState(true);
  const [offen, setOffen] = useState<string | null>(null);
  const [geheimnis, setGeheimnis] = useState("");
  const [bezeichnung, setBezeichnung] = useState("");
  const [busy, setBusy] = useState(false);
  const [fehler, setFehler] = useState("");

  const laden = useCallback(async () => {
    try {
      const d = await api.getMyAiCredentials();
      setZugaenge(d.credentials || []);
      setTeamlizenz(!!d.team_license_allowed);
    } catch {
      setFehler("Zugänge konnten nicht geladen werden.");
    } finally {
      setLaedt(false);
    }
  }, []);

  useEffect(() => { laden(); }, [laden]);

  const speichern = async (harness: string) => {
    if (geheimnis.trim().length < 8) {
      setFehler("Das sieht zu kurz aus für einen Zugang.");
      return;
    }
    setBusy(true); setFehler("");
    try {
      await api.putMyAiCredential({ harness, secret: geheimnis.trim(), label: bezeichnung.trim() || null });
      setOffen(null); setGeheimnis(""); setBezeichnung("");
      await laden();
    } catch (e) {
      setFehler(e instanceof Error ? e.message : "Speichern fehlgeschlagen.");
    } finally {
      setBusy(false);
    }
  };

  const trennen = async (harness: string) => {
    setBusy(true); setFehler("");
    try {
      await api.deleteMyAiCredential(harness);
      await laden();
    } catch (e) {
      setFehler(e instanceof Error ? e.message : "Trennen fehlgeschlagen.");
    } finally {
      setBusy(false);
    }
  };

  if (laedt) {
    return (
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Zugänge werden geladen…
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        Verbinde dein eigenes Abo, damit deine Agenten darüber laufen. Der Zugang
        gehört dir, gilt nur für deine Agenten und lässt sich jederzeit trennen.
        {teamlizenz && " Ohne eigenen Zugang greift die Firmenlizenz."}
        {!teamlizenz && " Ohne eigenen Zugang brauchst du ein freigegebenes KI-Konto."}
      </p>

      {fehler && (
        <div className="flex items-center gap-2 rounded-lg border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs text-red-400">
          <TriangleAlert className="h-3.5 w-3.5 shrink-0" /> {fehler}
        </div>
      )}

      {HARNESSE.map((h) => {
        const z = zugaenge.find((x) => x.harness === h.id);
        const verbunden = !!z?.created_at;
        return (
          <div key={h.id} className="rounded-xl border border-border bg-card/40 p-3">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2.5 min-w-0">
                <KeyRound className="h-4 w-4 shrink-0 text-muted-foreground/60" />
                <div className="min-w-0">
                  <p className="text-sm font-medium">{h.name}</p>
                  <p className="truncate text-[11px] text-muted-foreground">
                    {verbunden
                      ? `verbunden${z?.label ? ` · ${z.label}` : ""}${
                          z?.last_used_at ? ` · zuletzt benutzt ${new Date(z.last_used_at).toLocaleDateString()}` : ""
                        }`
                      : "nicht verbunden"}
                  </p>
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {verbunden && (
                  <>
                    {/* Der letzte Status ist die einzige ehrliche Auskunft darueber,
                        ob der Zugang noch gilt — ein Token kann ablaufen, ohne dass
                        hier etwas passiert. */}
                    {z?.last_status && z.last_status !== "ok" ? (
                      <span className="rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-500">
                        {z.last_status}
                      </span>
                    ) : (
                      <Check className="h-4 w-4 text-emerald-500" />
                    )}
                    <button
                      onClick={() => trennen(h.id)}
                      disabled={busy}
                      title="Zugang trennen"
                      className="rounded-lg p-1.5 text-muted-foreground hover:bg-accent hover:text-red-400 disabled:opacity-40"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </>
                )}
                <button
                  onClick={() => { setOffen(offen === h.id ? null : h.id); setFehler(""); }}
                  className="rounded-lg border border-border px-2.5 py-1.5 text-[11px] font-medium hover:bg-accent"
                >
                  {verbunden ? "Ersetzen" : "Verbinden"}
                </button>
              </div>
            </div>

            {offen === h.id && (
              <div className="mt-3 space-y-2 border-t border-border pt-3">
                <p className="text-[11px] text-muted-foreground">{h.hilfe}</p>
                <textarea
                  value={geheimnis}
                  onChange={(e) => setGeheimnis(e.target.value)}
                  rows={3}
                  placeholder="Zugang einfügen…"
                  className="w-full rounded-lg border border-input bg-transparent px-3 py-2 font-mono text-[11px] outline-none focus:border-ring"
                />
                <input
                  value={bezeichnung}
                  onChange={(e) => setBezeichnung(e.target.value)}
                  placeholder="Bezeichnung (optional, z. B. privates Abo)"
                  className="w-full rounded-lg border border-input bg-transparent px-3 py-2 text-xs outline-none focus:border-ring"
                />
                <div className="flex justify-end gap-2">
                  <button
                    onClick={() => { setOffen(null); setGeheimnis(""); setFehler(""); }}
                    className="rounded-lg px-3 py-1.5 text-[11px] text-muted-foreground hover:bg-accent"
                  >
                    Abbrechen
                  </button>
                  <button
                    onClick={() => speichern(h.id)}
                    disabled={busy}
                    className="flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-[11px] font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
                  >
                    {busy && <Loader2 className="h-3 w-3 animate-spin" />} Speichern
                  </button>
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
