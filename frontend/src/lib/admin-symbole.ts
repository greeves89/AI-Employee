/**
 * Ein Symbol je Bereich des Admin-Bereichs — fuer die Leiste und die Suche.
 *
 * Getrennt von ``admin-bereiche.ts``, weil jene Datei ohne Importe auskommen muss
 * (ein Test fuehrt sie mit Node aus).
 */

import {
  AppWindow,
  Brain,
  Cpu,
  DollarSign,
  HeartPulse,
  KeyRound,
  Layers,
  Lock,
  MessageSquare,
  Mic,
  Plug,
  Scale,
  ScrollText,
  Search,
  Shield,
  ShieldAlert,
  UserCog,
  Users,
} from "lucide-react";

import type { AdminBereich } from "@/lib/admin-bereiche";

export const ADMIN_SYMBOLE: Record<AdminBereich, React.ElementType> = {
  suche: Search,
  users: Users,
  roles: Shield,
  "sso-groups": KeyRound,
  agents: Cpu,
  assignments: UserCog,
  vorlagen: Layers,
  modelle: Brain,
  "ai-accounts": Cpu,
  sprache: Mic,
  "second-brains": Brain,
  "web-search": Search,
  "integrationen-anlage": Plug,
  system: Lock,
  pages: AppWindow,
  "master-rules": ShieldAlert,
  secrets: KeyRound,
  dlp: Shield,
  audit: ScrollText,
  gesetze: Scale,
  health: HeartPulse,
  budget: DollarSign,
  feedback: MessageSquare,
};
