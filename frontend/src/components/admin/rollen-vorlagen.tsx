"use client";

import { useEffect, useState } from "react";
import { Loader2, Plus, ShieldCheck, Users } from "lucide-react";
import * as api from "@/lib/api";
import { useToast } from "@/components/ui/dialog-provider";

/**
 * Enterprise-Rollenmodell: fertige Vorlagen (core/rollen_vorlagen) und die Rechte-Übersicht
 * „wer darf was“. Eine Vorlage legt eine ganz normale Gruppe an — danach unten anpassen.
 */
const GRUNDROLLE: Record<string, string> = {
  admin: "Admin", manager: "Manager", member: "Mitglied", viewer: "Betrachter", unassigned: "ohne Rolle",
};

const SPALTEN: [string, string][] = [
  ["agenten", "Agenten"], ["autonomie", "Autonomie"], ["modelle", "Modelle"],
  ["mcp_server", "MCP-Server"], ["ki_konten", "KI-Konten"], ["secrets", "Secrets"],
];

export function RollenVorlagen({ onAngelegt }: { onAngelegt: () => void }) {
  const [vorlagen, setVorlagen] = useState<api.RollenVorlage[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const toast = useToast();

  useEffect(() => {
    api.getRollenVorlagen().then((r) => setVorlagen(r.vorlagen)).catch(() => setVorlagen([]));
  }, []);

  const anlegen = async (v: api.RollenVorlage) => {
    setBusy(v.schluessel);
    try {
      const g = await api.gruppeAusVorlage(v.schluessel);
      toast.success(`Gruppe „${g.name}“ angelegt`,
        `Empfohlene Grundrolle: ${GRUNDROLLE[g.grundrolle] || g.grundrolle}. Unten anpassen und zuweisen.`);
      onAngelegt();
    } catch (e) {
      toast.error("Nicht angelegt", e instanceof Error ? e.message : undefined);
    } finally {
      setBusy(null);
    }
  };

  if (vorlagen === null) return <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />;
  return (
    <section className="space-y-2">
      <h3 className="flex items-center gap-2 text-sm font-semibold"><ShieldCheck className="h-4 w-4" /> Rollen-Vorlagen</h3>
      <p className="text-xs text-muted-foreground">
        Fertige Rechtebündel für typische Aufgaben. Eine Vorlage legt eine Gruppe an, die du danach
        anpasst (Modelle, Vorlagen, Secrets freigeben) und unter „SSO-Gruppen“ einer Entra-Gruppe zuordnest.
      </p>
      <div className="divide-y divide-foreground/[0.06] rounded-xl border border-foreground/10">
        {vorlagen.map((v) => (
          <div key={v.schluessel} className="flex items-start gap-3 px-4 py-3">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">
                {v.name}
                <span className="ml-2 text-[11px] font-normal text-muted-foreground">Grundrolle: {GRUNDROLLE[v.grundrolle]}</span>
              </p>
              <p className="text-xs text-muted-foreground">{v.beschreibung}</p>
            </div>
            {v.rechte && (
              <button type="button" onClick={() => anlegen(v)} disabled={busy !== null}
                className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-foreground/10 px-3 py-1.5 text-xs font-medium hover:bg-foreground/5 disabled:opacity-50">
                {busy === v.schluessel ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
                Gruppe anlegen
              </button>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

export function RechteUebersicht({ stand }: { stand: number }) {
  const [zeilen, setZeilen] = useState<api.RechteZeile[] | null>(null);

  useEffect(() => {
    api.getRechteUebersicht().then((r) => setZeilen(r.nutzer)).catch(() => setZeilen([]));
  }, [stand]);

  if (zeilen === null) return <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />;
  return (
    <section className="space-y-2">
      <h3 className="flex items-center gap-2 text-sm font-semibold"><Users className="h-4 w-4" /> Wer darf was</h3>
      <p className="text-xs text-muted-foreground">
        Wirksame Rechte je Person — so, wie der Server sie prüft (Grundrolle plus Gruppe).
      </p>
      <div className="overflow-x-auto rounded-xl border border-foreground/10">
        <table className="w-full text-left text-xs">
          <thead className="bg-foreground/[0.03] text-muted-foreground">
            <tr>
              <th className="px-3 py-2 font-medium">Person</th>
              <th className="px-3 py-2 font-medium">Grundrolle</th>
              <th className="px-3 py-2 font-medium">Gruppe</th>
              <th className="px-3 py-2 font-medium">Anmeldung</th>
              {SPALTEN.map(([, titel]) => <th key={titel} className="px-3 py-2 font-medium">{titel}</th>)}
            </tr>
          </thead>
          <tbody className="divide-y divide-foreground/[0.06]">
            {zeilen.map((z) => (
              <tr key={z.id}>
                <td className="px-3 py-2"><div className="font-medium">{z.name || z.email}</div>
                  {z.name && <div className="text-muted-foreground">{z.email}</div>}</td>
                <td className="px-3 py-2">{GRUNDROLLE[z.grundrolle] || z.grundrolle}</td>
                <td className="px-3 py-2">{z.gruppe || <span className="text-muted-foreground">–</span>}</td>
                <td className="px-3 py-2">{z.anmeldung === "lokal" ? "lokal" : `SSO (${z.anmeldung})`}</td>
                {"alles" in z.rechte
                  ? <td colSpan={SPALTEN.length} className="px-3 py-2 text-muted-foreground">alles (Admin)</td>
                  : SPALTEN.map(([k]) => <td key={k} className="px-3 py-2">{String((z.rechte as Record<string, string | number>)[k] ?? "–")}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
