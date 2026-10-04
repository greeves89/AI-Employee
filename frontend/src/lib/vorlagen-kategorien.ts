/** Kategorien der Agenten-Vorlagen: deutscher Anzeigename und Farbe (#902).
 *
 *  Schlüssel wie ``category`` in ``orchestrator/app/core/agent_templates.py``.
 *  ``tests/test_deutsche_anzeigenamen.py`` wacht darüber, dass jede dort
 *  benutzte Kategorie hier einen deutschen Namen hat — sonst stünde auf der
 *  Kachel der rohe englische Schlüssel.
 */
export const VORLAGEN_KATEGORIEN: Record<string, string> = {
  dev: "Entwicklung",
  data: "Daten & Auswertung",
  writing: "Texte & Dokumentation",
  ops: "Betrieb",
  creative: "Gestaltung",
  general: "Allgemein",
  marketing: "Marketing",
  support: "Kundenservice",
  sales: "Vertrieb",
  management: "Leitung",
  security: "Sicherheit",
  productivity: "Produktivität",
  finance: "Finanzen",
};

export const VORLAGEN_KATEGORIE_FARBEN: Record<string, string> = {
  dev: "bg-blue-500/10 text-blue-400 border-blue-500/20",
  data: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  writing: "bg-purple-500/10 text-purple-400 border-purple-500/20",
  ops: "bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/20",
  creative: "bg-pink-500/10 text-pink-400 border-pink-500/20",
  general: "bg-gray-500/10 text-gray-400 border-gray-500/20",
  marketing: "bg-orange-500/10 text-orange-400 border-orange-500/20",
  support: "bg-cyan-500/10 text-cyan-400 border-cyan-500/20",
  sales: "bg-rose-500/10 text-rose-400 border-rose-500/20",
  management: "bg-indigo-500/10 text-indigo-400 border-indigo-500/20",
  security: "bg-red-500/10 text-red-400 border-red-500/20",
  productivity: "bg-teal-500/10 text-teal-400 border-teal-500/20",
  finance: "bg-lime-500/10 text-lime-600 dark:text-lime-400 border-lime-500/20",
};

/** Anzeigename einer Kategorie; Unbekanntes bleibt sichtbar statt zu verschwinden. */
export function kategorieName(kategorie: string | null | undefined): string {
  if (!kategorie) return VORLAGEN_KATEGORIEN.general;
  return VORLAGEN_KATEGORIEN[kategorie] ?? kategorie;
}
