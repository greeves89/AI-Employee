/** Auswaehlbare Symbole fuer eigene Menuepunkte.
 *
 *  Bewusst eine feste Liste statt des ganzen lucide-Pakets: ein dynamischer
 *  Zugriff auf alle Symbole zoege beim Bauen jedes einzelne ins Bundle, und der
 *  Administrator muesste Namen raten. Steht ein gespeicherter Name nicht drin,
 *  faellt die Anzeige auf ``Globe`` zurueck — ein unbekanntes Symbol darf den
 *  Menuepunkt nicht verschlucken.
 */
import type React from "react";
import {
  AppWindow,
  BarChart3,
  Bot,
  BookOpen,
  Building2,
  Calendar,
  ClipboardList,
  Cloud,
  Cpu,
  Database,
  FileText,
  Globe,
  GraduationCap,
  LayoutDashboard,
  LifeBuoy,
  Link2,
  Lock,
  Mail,
  MessageSquare,
  Newspaper,
  Presentation,
  Search,
  Settings,
  Users,
  Video,
  Wrench,
} from "lucide-react";

export const PAGE_ICONS = {
  Globe,
  AppWindow,
  MessageSquare,
  Bot,
  BookOpen,
  BarChart3,
  FileText,
  Link2,
  LayoutDashboard,
  Cpu,
  Users,
  Calendar,
  Mail,
  Database,
  Cloud,
  Settings,
  Wrench,
  GraduationCap,
  Building2,
  Newspaper,
  Search,
  ClipboardList,
  LifeBuoy,
  Lock,
  Presentation,
  Video,
} as const;

export type PageIconName = keyof typeof PAGE_ICONS;

export const PAGE_ICON_NAMES = Object.keys(PAGE_ICONS) as PageIconName[];

export function pageIcon(name: string | null | undefined): React.ElementType {
  return PAGE_ICONS[(name ?? "") as PageIconName] ?? Globe;
}

/** Menuegruppen der Seitenleiste — Schluessel wie in ``navGroups`` dort und in
 *  ``GROUP_KEYS`` im Server. Weicht eine Seite davon ab, faende der Menuepunkt
 *  keine Gruppe und waere unsichtbar. */
export const PAGE_GROUPS: { key: string; label: string }[] = [
  { key: "overview", label: "Übersicht" },
  { key: "collab", label: "Zusammenarbeit" },
  { key: "automation", label: "Automation" },
  { key: "system", label: "System" },
  { key: "help", label: "Hilfe" },
];
