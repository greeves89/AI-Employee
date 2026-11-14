"use client";

/** Karte „Zugänge hinter dem Proxy“ unter Admin → Betrieb.
 *
 * Für den Claude- und den Codex-Zugang der Plattform je eine Stufe: aus, nur
 * ausgewählte Agenten oder alle. Hinter dem Proxy sieht ein Agent den Zugang nur
 * als Platzhalter; der Egress-Proxy setzt ihn erst beim Anbieter ein.
 *
 * Eine neue Stufe gilt für Agenten, die danach entstehen oder aktualisiert werden.
 * Die übrigen zeigen „Aktualisieren“; die Karte bietet das für sie gesammelt an —
 * nacheinander, nicht gleichzeitig (Codex-Agenten teilen sich eine Anmeldung).
 *
 * Oben der Hauptschalter für den Egress-Proxy (PUT /settings/egress-proxy, seit 1.435.0):
 * einschalten nur mit Dienst-Schlüssel und erreichbarem Proxy-Dienst, ausschalten mit
 * Rückfrage. Die Agenten ziehen danach im Leerlauf selbst nach.
 *
 * Serverseitig nur für Administratoren (GET/PUT /settings/egress-stufen); jede
 * Änderung steht im Prüfprotokoll. Der Wert aus der .env ist nur der Startwert.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { KeyRound, Loader2, RefreshCw } from "lucide-react";
import * as api from "@/lib/api";
import type { EgressStufe, EgressStufenStand } from "@/lib/api";
import { apiFehlertext } from "@/lib/api-fehler";
import { useConfirm, useToast } from "@/components/ui/dialog-provider";
import { cn } from "@/lib/utils";

type Art = "ki" | "codex";

const ZUGAENGE: { art: Art; name: string; laufzeit: string }[] = [
  { art: "ki", name: "Claude-Zugang", laufzeit: "claude_code" },
  { art: "codex", name: "Codex-Zugang", laufzeit: "codex_cli" },
];

const MODI: { modus: EgressStufe["modus"]; name: string }[] = [
  { modus: "aus", name: "Aus" },
  { modus: "auswahl", name: "Ausgewählte" },
  { modus: "alle", name: "Alle Agenten" },
];

function gleich(a: EgressStufe, b: EgressStufe): boolean {
  if (a.modus !== b.modus) return false;
  if (a.modus !== "auswahl") return true;
  return [...a.agenten].sort().join(",") === [...b.agenten].sort().join(",");
}

/** Deckt die neue Stufe weniger Agenten ab? Spiegel von ``egress.stufe_gesenkt``. */
function gesenkt(alt: EgressStufe, neu: EgressStufe): boolean {
  if (alt.modus === "aus") return false;
  if (alt.modus === "alle") return neu.modus !== "alle";
  return neu.modus === "aus" || (neu.modus === "auswahl" && alt.agenten.some((a) => !neu.agenten.includes(a)));
}

function ZugangZeile({
  name,
  stufe,
  agenten,
  speichert,
  onSpeichern,
}: {
  name: string;
  stufe: EgressStufe;
  agenten: EgressStufenStand["agenten"];
  speichert: boolean;
  onSpeichern: (neu: EgressStufe) => void;
}) {
  const [entwurf, setEntwurf] = useState<EgressStufe>(stufe);
  useEffect(() => setEntwurf(stufe), [stufe]);
  const geaendert = !gleich(entwurf, stufe);
  const auswahlLeer = entwurf.modus === "auswahl" && entwurf.agenten.length === 0;

  function umschalten(id: string) {
    setEntwurf((e) => ({
      ...e,
      agenten: e.agenten.includes(id) ? e.agenten.filter((a) => a !== id) : [...e.agenten, id],
    }));
  }

  return (
    <div className="py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-[13px] font-medium">{name}</p>
          <p className="text-xs text-muted-foreground">
            {agenten.length === 0
              ? "Kein Agent nutzt diesen Zugang."
              : `${agenten.length} Agent(en) nutzen diesen Zugang.`}
          </p>
        </div>
        <div role="radiogroup" aria-label={`Stufe ${name}`} className="inline-flex rounded-lg border border-foreground/[0.08] p-0.5">
          {MODI.map((m) => (
            <button
              key={m.modus}
              type="button"
              role="radio"
              aria-checked={entwurf.modus === m.modus}
              onClick={() => setEntwurf((e) => ({ ...e, modus: m.modus }))}
              className={cn(
                "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                entwurf.modus === m.modus
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {m.name}
            </button>
          ))}
        </div>
      </div>

      {entwurf.modus === "auswahl" && (
        <div className="mt-2 grid gap-1 sm:grid-cols-2">
          {agenten.length === 0 && (
            <p className="text-xs text-muted-foreground">Keine passenden Agenten vorhanden.</p>
          )}
          {agenten.map((a) => (
            <label key={a.id} className="flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                checked={entwurf.agenten.includes(a.id)}
                onChange={() => umschalten(a.id)}
                className="h-3.5 w-3.5 accent-primary"
              />
              <span className="truncate">{a.name}</span>
              {a.veraltet && <span className="text-[11px] text-amber-600 dark:text-amber-400">Aktualisieren</span>}
            </label>
          ))}
        </div>
      )}

      {geaendert && (
        <div className="mt-2 flex items-center gap-2">
          <button
            type="button"
            disabled={speichert || auswahlLeer}
            onClick={() => onSpeichern(entwurf)}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-50"
          >
            {speichert && <Loader2 className="h-3 w-3 animate-spin" />} Speichern
          </button>
          <button
            type="button"
            onClick={() => setEntwurf(stufe)}
            className="rounded-lg px-3 py-1.5 text-xs font-medium text-muted-foreground hover:text-foreground"
          >
            Verwerfen
          </button>
          {auswahlLeer && <span className="text-xs text-muted-foreground">Mindestens einen Agenten wählen.</span>}
        </div>
      )}
    </div>
  );
}

export function EgressStufenKarte() {
  const toast = useToast();
  const confirm = useConfirm();
  const [stand, setStand] = useState<EgressStufenStand | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);
  const [speichert, setSpeichert] = useState<Art | null>(null);
  const [aktualisiert, setAktualisiert] = useState<string | null>(null);
  const [schaltet, setSchaltet] = useState(false);

  const laden = useCallback(async () => {
    try {
      setStand(await api.getEgressStufen());
      setFehler(null);
    } catch (e) {
      setFehler(apiFehlertext(e, "Unbekannter Fehler"));
    }
  }, []);

  useEffect(() => { laden(); }, [laden]);

  const veraltet = useMemo(() => (stand?.agenten ?? []).filter((a) => a.veraltet), [stand]);

  async function speichern(art: Art, neu: EgressStufe, name: string) {
    if (stand?.proxy_aktiv && gesenkt(stand[art], neu)) {
      const ok = await confirm({
        title: `${name} für weniger Agenten schützen?`,
        message:
          "Agenten, die nicht mehr in der Stufe sind, bekommen den Zugang nach dem nächsten " +
          "Aktualisieren wieder im Klartext und könnten ihn ausgeben oder weitergeben. " +
          "Die Änderung steht im Prüfprotokoll.",
        variant: "warning",
        confirmLabel: "Trotzdem speichern",
      });
      if (!ok) return;
    }
    setSpeichert(art);
    try {
      setStand(await api.setEgressStufe(art, neu));
      toast.success(`${name}: Stufe gespeichert`, "Gilt für Agenten, die danach entstehen oder aktualisiert werden.");
    } catch (e) {
      toast.error("Nicht gespeichert", apiFehlertext(e));
    } finally {
      setSpeichert(null);
    }
  }

  async function proxySchalten(an: boolean) {
    if (!an) {
      const ok = await confirm({
        title: "Egress-Proxy ausschalten?",
        message:
          "Laufende Agenten arbeiten weiter wie bisher. Sobald sie neu erstellt werden (im " +
          "Leerlauf automatisch), bekommen sie alle Keys und Zugänge wieder im Klartext und " +
          "können sie an beliebige Ziele schicken. Die Änderung steht im Prüfprotokoll.",
        variant: "warning",
        confirmLabel: "Ausschalten",
      });
      if (!ok) return;
    }
    setSchaltet(true);
    try {
      setStand(await api.setEgressProxy(an));
      toast.success(an ? "Egress-Proxy eingeschaltet" : "Egress-Proxy ausgeschaltet",
        "Die Agenten ziehen im Leerlauf nach, oder sofort über „Jetzt aktualisieren“.");
    } catch (e) {
      toast.error("Nicht umgeschaltet", apiFehlertext(e));
    } finally {
      setSchaltet(false);
    }
  }

  async function alleAktualisieren() {
    const ok = await confirm({
      title: `${veraltet.length} Agent(en) aktualisieren?`,
      message:
        "Die Agenten werden nacheinander neu erstellt, damit die neue Stufe in ihnen gilt. " +
        "Laufende Antworten brechen dabei ab; Arbeitsdaten bleiben erhalten.",
      variant: "warning",
      confirmLabel: "Aktualisieren",
    });
    if (!ok) return;
    let fehlgeschlagen = 0;
    for (const a of veraltet) {
      setAktualisiert(a.name);
      try {
        await api.updateAgent(a.id);
      } catch {
        fehlgeschlagen += 1;
      }
    }
    setAktualisiert(null);
    await laden();
    if (fehlgeschlagen) toast.error("Nicht alle aktualisiert", `${fehlgeschlagen} Agent(en) bitte einzeln aktualisieren.`);
    else toast.success("Agenten aktualisiert");
  }

  if (fehler) {
    return (
      <div className="mb-4 rounded-xl border border-red-500/20 bg-red-500/5 p-4 text-sm text-red-500">
        Zugänge hinter dem Proxy nicht abrufbar: {fehler}
      </div>
    );
  }
  if (!stand) {
    return (
      <div className="mb-4 flex items-center gap-2 rounded-xl border border-foreground/[0.06] bg-card/80 p-4 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Zugänge hinter dem Proxy werden geladen …
      </div>
    );
  }

  return (
    <div className="mb-4 rounded-xl border border-foreground/[0.06] bg-card/80 p-4">
      <div className="flex items-start gap-3">
        <KeyRound className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold">Zugänge hinter dem Proxy</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            Hinter dem Proxy sieht ein Agent den Claude- bzw. Codex-Zugang der Plattform nur als
            Platzhalter; erst der Egress-Proxy setzt ihn beim Anbieter ein. Schrittweise
            einschalten: erst einzelne Agenten, nach ein paar Tagen alle. Eine neue Stufe gilt
            für Agenten, die danach entstehen oder aktualisiert werden. Was hier gespeichert
            ist, gilt auch nach einem Neustart; die Einträge in der .env sind nur der Startwert.
          </p>

          <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-foreground/[0.06] px-3 py-2.5">
            <div className="min-w-0">
              <p className="text-[13px] font-medium">Egress-Proxy</p>
              <p className="text-xs text-muted-foreground">
                {stand.proxy_aktiv
                  ? "An: Keys aus der Schlüsselverwaltung, Git- und MCP-Zugänge sehen Agenten nur als Platzhalter."
                  : !stand.dienst_schluessel
                    ? "Aus. Zum Einschalten fehlt der Dienst-Schlüssel — auf dem Server einmal ./scripts/update.sh ausführen."
                    : !stand.proxy_erreichbar
                      ? "Aus. Der Proxy-Dienst antwortet nicht — erst ihn starten (docker compose up -d)."
                      : "Aus: Agenten bekommen Keys im Klartext."}
              </p>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={stand.proxy_eingeschaltet}
              aria-label="Egress-Proxy ein- oder ausschalten"
              disabled={schaltet || (!stand.proxy_eingeschaltet && (!stand.dienst_schluessel || !stand.proxy_erreichbar))}
              onClick={() => proxySchalten(!stand.proxy_eingeschaltet)}
              className={cn(
                "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors disabled:opacity-50",
                stand.proxy_eingeschaltet ? "bg-primary" : "bg-foreground/20",
              )}
            >
              <span
                className={cn(
                  "inline-block h-4 w-4 rounded-full bg-white shadow transition-transform",
                  stand.proxy_eingeschaltet ? "translate-x-4" : "translate-x-0.5",
                )}
              />
            </button>
          </div>

          <div className="mt-2 divide-y divide-foreground/[0.05]">
            {ZUGAENGE.map((z) => (
              <ZugangZeile
                key={z.art}
                name={z.name}
                stufe={stand[z.art]}
                agenten={stand.agenten.filter((a) => a.laufzeit === z.laufzeit)}
                speichert={speichert === z.art}
                onSpeichern={(neu) => speichern(z.art, neu, z.name)}
              />
            ))}
          </div>

          {veraltet.length > 0 && (
            <div className="mt-2 flex flex-wrap items-center justify-between gap-2 rounded-lg bg-amber-500/10 px-3 py-2">
              <p className="text-xs text-amber-700 dark:text-amber-300">
                {aktualisiert
                  ? `Aktualisiere ${aktualisiert} …`
                  : `${veraltet.length} Agent(en) entstanden mit einer anderen Stufe: ${veraltet.map((a) => a.name).slice(0, 5).join(", ")}${veraltet.length > 5 ? " …" : ""}.`}
              </p>
              <button
                type="button"
                disabled={aktualisiert !== null}
                onClick={alleAktualisieren}
                className="inline-flex items-center gap-1.5 rounded-lg border border-amber-500/30 px-2.5 py-1 text-xs font-medium text-amber-700 hover:bg-amber-500/10 disabled:opacity-50 dark:text-amber-300"
              >
                <RefreshCw className={cn("h-3 w-3", aktualisiert && "animate-spin")} /> Jetzt aktualisieren
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
