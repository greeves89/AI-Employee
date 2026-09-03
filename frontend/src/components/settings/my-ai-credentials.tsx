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
  // Anmeldung im Browser: bei Claude wird ein Code zurueckgegeben, den der
  // Nutzer einfuegt; bei Codex zeigt ChatGPT einen Geraetecode an und die
  // ``auth.json`` entsteht lokal.
  const [anmeldung, setAnmeldung] = useState<null | {
    harness: string; code?: string; uri?: string; sitzung?: string; zustand?: string;
  }>(null);

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

  const anmeldenClaude = async () => {
    setBusy(true); setFehler("");
    try {
      const { auth_url } = await api.startMyAnthropicLogin();
      window.open(auth_url, "_blank");
      setAnmeldung({ harness: "claude_code" });
      setOffen("claude_code");
    } catch (e) {
      setFehler(e instanceof Error ? e.message : "Anmeldung nicht startbar.");
    } finally {
      setBusy(false);
    }
  };

  const anmeldenCodex = async () => {
    setBusy(true); setFehler("");
    try {
      const s = await api.startMyCodexLogin();
      window.open(s.verification_uri, "_blank");
      setAnmeldung({
        harness: "codex", code: s.user_code, uri: s.verification_uri,
        sitzung: s.session_id, zustand: s.status,
      });
      setOffen("codex");
      // Der Nutzer bekommt NIE eine Datei zu sehen: Codex legt sie im Container
      // an, der Dienst liest sie und raeumt sie weg. Ihn nach ihrem Inhalt zu
      // fragen war unerfuellbar — die ChatGPT-Seite meldet „kann geschlossen
      // werden", und danach gibt es nichts einzufuegen. Also fragen WIR nach.
      const bis = Date.now() + 5 * 60 * 1000;
      const takt = window.setInterval(async () => {
        try {
          const z = await api.getMyCodexLoginStatus(s.session_id);
          setAnmeldung((a) => (a ? { ...a, zustand: z.status } : a));
          if (z.status === "connected") {
            window.clearInterval(takt);
            setAnmeldung(null); setOffen(null);
            await laden();
          } else if (z.status !== "pending" || Date.now() > bis) {
            window.clearInterval(takt);
            setFehler(z.error || "Anmeldung nicht abgeschlossen.");
          }
        } catch {
          window.clearInterval(takt);
        }
      }, 2500);
    } catch (e) {
      setFehler(e instanceof Error ? e.message : "Anmeldung nicht startbar.");
    } finally {
      setBusy(false);
    }
  };

  // Was der Nutzer nach der Browser-Anmeldung einfuegt, ist bei Claude ein Code
  // (oder die ganze Callback-Adresse) und bei Codex der Inhalt der auth.json.
  const abschliessen = async (harness: string) => {
    if (harness !== "claude_code") return speichern(harness);
    setBusy(true); setFehler("");
    try {
      const roh = geheimnis.trim();
      // Ganze Callback-Adresse, „code#state" oder blosser Code — alles erlaubt.
      let code = roh, state = "";
      try {
        const u = new URL(roh);
        code = u.searchParams.get("code") || roh;
        state = u.searchParams.get("state") || "";
      } catch {
        if (roh.includes("#")) { [code, state] = roh.split("#"); }
      }
      await api.exchangeMyAnthropicLogin({ code, state, label: bezeichnung.trim() || null });
      setOffen(null); setAnmeldung(null); setGeheimnis(""); setBezeichnung("");
      await laden();
    } catch (e) {
      setFehler(e instanceof Error ? e.message : "Anmeldung fehlgeschlagen.");
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
                  onClick={() => (h.id === "claude_code" ? anmeldenClaude() : anmeldenCodex())}
                  disabled={busy}
                  className="rounded-lg bg-primary px-2.5 py-1.5 text-[11px] font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
                >
                  {verbunden ? "Neu anmelden" : `Mit ${h.name} anmelden`}
                </button>
                {/* Der Weg von Hand bleibt: wer sein Token schon hat (oder in
                    einer Umgebung ohne Browser arbeitet), soll nicht durch die
                    Anmeldung muessen. */}
                <button
                  onClick={() => { setAnmeldung(null); setOffen(offen === h.id ? null : h.id); setFehler(""); }}
                  title="Zugang von Hand einfügen"
                  className="rounded-lg border border-border px-2.5 py-1.5 text-[11px] text-muted-foreground hover:bg-accent"
                >
                  Manuell
                </button>
              </div>
            </div>

            {offen === h.id && (
              <div className="mt-3 space-y-2 border-t border-border pt-3">
                {anmeldung?.harness === h.id ? (
                  h.id === "claude_code" ? (
                    <p className="text-[11px] text-muted-foreground">
                      Im Browser bei Claude anmelden, dann den angezeigten Code
                      (oder die ganze Adresse aus der Adresszeile) hier einfügen.
                    </p>
                  ) : (
                    <div className="space-y-1.5">
                      <p className="text-[11px] text-muted-foreground">
                        Im Browser bei ChatGPT anmelden. Gerätecode:
                      </p>
                      <code className="block rounded-lg bg-foreground/[0.06] px-2 py-1.5 text-center font-mono text-sm tracking-widest">
                        {anmeldung.code}
                      </code>
                      <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                        <Loader2 className="h-3 w-3 animate-spin" />
                        Warte auf die Bestätigung… Sobald die ChatGPT-Seite sagt,
                        sie kann geschlossen werden, bist du fertig — hier ist
                        nichts einzufügen.
                      </p>
                    </div>
                  )
                ) : (
                  <p className="text-[11px] text-muted-foreground">{h.hilfe}</p>
                )}
                {!(anmeldung?.harness === "codex") && (<>
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
                </>)}
                <div className="flex justify-end gap-2">
                  <button
                    onClick={() => { setOffen(null); setAnmeldung(null); setGeheimnis(""); setFehler(""); }}
                    className="rounded-lg px-3 py-1.5 text-[11px] text-muted-foreground hover:bg-accent"
                  >
                    Abbrechen
                  </button>
                  <button
                    onClick={() => abschliessen(h.id)}
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
