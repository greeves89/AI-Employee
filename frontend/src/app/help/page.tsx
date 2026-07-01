"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  HelpCircle,
  Search,
  BookOpen,
  Rocket,
  ChevronDown,
  ExternalLink,
  Download,
} from "lucide-react";
import { cn } from "@/lib/utils";

// --- Hilfe-Index: alles was als Hilfe/Help identifizierbar ist -------------------
// Eine Quelle fuer FAQ + Funktions-How-Tos + Deep-Links. Die Suche filtert client-
// seitig (kein Backend, keine zusaetzliche Dependency) ueber title/body/keywords.
type HelpTopic = {
  id: string;
  category: string;
  title: string;
  body: string;
  keywords: string[];
  href?: string;
  hrefLabel?: string;
};

const HELP_TOPICS: HelpTopic[] = [
  // --- Erste Schritte -----------------------------------------------------------
  {
    id: "begriffe",
    category: "Erste Schritte",
    title: "Agent, Chat, Task, Workspace — was ist was?",
    body: "Agent = dein KI-Mitarbeiter (eigener Container + Gedaechtnis). Chat = unterhalten (Hin und Her). Task = beauftragen (autonom, auch im Hintergrund). Workspace = privater Dateibereich des Agenten (/workspace), bleibt ueber Updates erhalten.",
    keywords: ["agent", "chat", "task", "workspace", "begriffe", "grundlagen"],
    href: "/onboarding",
    hrefLabel: "Onboarding starten",
  },
  {
    id: "onboarding",
    category: "Erste Schritte",
    title: "Schnellstart mit einem Branchen-Paket (Onboarding)",
    body: "Ueber den Onboarding-Wizard ein vorkonfiguriertes Paket waehlen (z. B. Entwickler-Team, Content-Studio, Support-Desk) — die passenden Agenten werden automatisch angelegt.",
    keywords: ["onboarding", "wizard", "start", "paket", "branche", "einrichten"],
    href: "/onboarding",
    hrefLabel: "Zum Onboarding",
  },
  // --- Agenten ------------------------------------------------------------------
  {
    id: "agent-erstellen",
    category: "Agenten",
    title: "Neuen Agenten erstellen",
    body: "Auf der Agents-Seite oben rechts einen neuen Agenten anlegen: Name, Symbol (Icon + Farbe), Harness/Modus und Modell waehlen. Es werden nur freigegebene Modelle/Accounts angezeigt.",
    keywords: ["agent", "erstellen", "anlegen", "neu", "modell", "harness"],
    href: "/agents",
    hrefLabel: "Zu den Agents",
  },
  {
    id: "agent-symbol",
    category: "Agenten",
    title: "Agent-Symbol (Icon + Farbe) aendern",
    body: "Das Symbol eines Agenten laesst sich beim Erstellen UND nachtraeglich anpassen: Agent oeffnen, Einstellungen, Bereich 'Symbol', Icon und Farbe waehlen (wird sofort gespeichert und auf den Karten angezeigt).",
    keywords: ["symbol", "icon", "avatar", "farbe", "bild", "aussehen", "agent"],
    href: "/agents",
    hrefLabel: "Zu den Agents",
  },
  {
    id: "voice",
    category: "Agenten",
    title: "Mit einem Agenten sprechen (Voice-Live-Session)",
    body: "Im Agenten-Chat die Sprach-/Voice-Funktion starten und sprechen — der Agent antwortet per Sprache. Spracherkennung/-ausgabe (inkl. Microsoft/Azure-Stimmen) ist in den Einstellungen waehlbar.",
    keywords: ["voice", "sprache", "sprechen", "mikrofon", "stt", "tts", "live"],
    href: "/agents",
    hrefLabel: "Zu den Agents",
  },
  // --- Funktionen ---------------------------------------------------------------
  {
    id: "skills-download",
    category: "Funktionen",
    title: "Skills herunterladen",
    body: "Einen Skill als SKILL.md herunterladen: im Skill Store auf das Download-Symbol neben 'Installieren' klicken oder im Skill-Detail-Fenster auf 'Herunterladen'. Bereits installierte Skills lassen sich unter Agent, Wissen, Skills pro Skill herunterladen.",
    keywords: ["skill", "skills", "download", "herunterladen", "export", "skill.md"],
    href: "/skills",
    hrefLabel: "Zum Skill Marketplace",
  },
  {
    id: "skills-install",
    category: "Funktionen",
    title: "Skill in einen Agenten installieren",
    body: "Im Skill Store zuerst oben einen Agenten auswaehlen, dann beim gewuenschten Skill auf 'Installieren' klicken. Ohne ausgewaehlten Agenten weist ein Hinweis darauf hin; Fehler werden angezeigt statt verschluckt.",
    keywords: ["skill", "installieren", "hinzufuegen", "agent"],
    href: "/skills",
    hrefLabel: "Zum Skill Marketplace",
  },
  {
    id: "meeting-planner",
    category: "Funktionen",
    title: "Meeting-Transkription zu MS Planner",
    body: "Aus Meeting-Aufzeichnungen erkannte Aufgaben (Action-Items) werden automatisch in einen MS-Planner-Plan gespiegelt — ueber das M365-Konto des Meeting-Owners. Voraussetzung: Admin hat die Planner-Plan-ID hinterlegt.",
    keywords: ["meeting", "transkription", "planner", "aufgaben", "action items", "protokoll"],
    href: "/meeting-rooms",
    hrefLabel: "Zu den Meeting Rooms",
  },
  {
    id: "notification-task",
    category: "Funktionen",
    title: "Benachrichtigung zu Task-Details oeffnen",
    body: "Ein Klick auf eine Benachrichtigung (Glocke oben) oeffnet ein zentriertes Fenster mit allen Task-Details: Status, Ergebnis, Kosten, Dauer, Tokens und ggf. Fehlermeldung.",
    keywords: ["benachrichtigung", "notification", "glocke", "task", "details", "ergebnis"],
    href: "/tasks",
    hrefLabel: "Zu den Tasks",
  },
  {
    id: "tasks-vs-chat",
    category: "Funktionen",
    title: "Task vs. Chat — wann was?",
    body: "Frag den Agenten im Chat (schnelle Fragen, Hin und Her). Beauftrage ihn als Task (klar umrissener Auftrag, laeuft autonom, auch im Hintergrund; Ergebnis spaeter abholen/bewerten).",
    keywords: ["task", "chat", "unterschied", "auftrag", "hintergrund"],
    href: "/tasks",
    hrefLabel: "Zu den Tasks",
  },
  // --- Admin --------------------------------------------------------------------
  {
    id: "exchange",
    category: "Admin",
    title: "Exchange on-prem (Mail + Kalender) einrichten",
    body: "Admin: Admin-Konsole, Einstellungen, Integrationen, 'Exchange (on-prem)': Server-URL (EWS) + Auth-Modus (service_account, modern_auth oder basic) hinterlegen. Danach erscheint Exchange bei den Agent-Integrationen. Jeder Agent greift nur auf das Postfach seines Owners zu.",
    keywords: ["exchange", "on-prem", "mail", "kalender", "ews", "outlook", "integration", "admin"],
    href: "/admin",
    hrefLabel: "Zur Admin-Konsole",
  },
  {
    id: "azure-voice",
    category: "Admin",
    title: "Microsoft-/Azure-Stimmen (Speech) aktivieren",
    body: "Admin: Einstellungen, Voice, Azure-Speech-Key + Region eintragen. Danach sind Azure-STT/TTS als Sprach-Option waehlbar (Standard bleibt sonst faster-whisper/Edge).",
    keywords: ["azure", "speech", "stimme", "voice", "microsoft", "stt", "tts", "admin"],
    href: "/admin",
    hrefLabel: "Zur Admin-Konsole",
  },
  {
    id: "dreaming",
    category: "Admin",
    title: "Dreaming-Memory (adaptives Nutzerprofil)",
    body: "Admin-Funktion (standardmaessig aus): Der Scheduler frischt periodisch das adaptive Nutzerprofil aus den Memories auf. Aktivierbar ueber die Automatisierungs-Einstellungen in der Admin-Konsole.",
    keywords: ["dreaming", "memory", "profil", "gedaechtnis", "automatisierung", "admin"],
    href: "/admin",
    hrefLabel: "Zur Admin-Konsole",
  },
  {
    id: "rollen",
    category: "Admin",
    title: "Modelle/Accounts/Tools per Rolle freigeben",
    body: "Es werden nur freigegebene Optionen angezeigt. Admin legt einen AI-Account an und gibt ihn per Rolle (Rechtebuendel) frei — erst dann ist er fuer Benutzer waehlbar.",
    keywords: ["rolle", "freigabe", "rechte", "ai-account", "modell", "admin", "gruppe"],
    href: "/admin",
    hrefLabel: "Zur Admin-Konsole",
  },
  // --- Problemloesung (FAQ aus dem Benutzerhandbuch) ----------------------------
  {
    id: "faq-keine-agenten",
    category: "Problemloesung (FAQ)",
    title: "Ich sehe keinen Agenten",
    body: "Auf der Agents-Seite siehst du nur deine eigenen Agenten. Admins: Admin-Konsole, All Agents fuer alle.",
    keywords: ["agent", "leer", "sehe nichts", "faq"],
    href: "/agents",
    hrefLabel: "Zu den Agents",
  },
  {
    id: "faq-modell-nicht-waehlbar",
    category: "Problemloesung (FAQ)",
    title: "Modell/Account nicht waehlbar",
    body: "Es werden nur freigegebene Optionen angezeigt. Der Admin muss den AI-Account anlegen und per Rolle freigeben.",
    keywords: ["modell", "account", "waehlbar", "freigabe", "faq"],
  },
  {
    id: "faq-agent-haengt",
    category: "Problemloesung (FAQ)",
    title: "Agent reagiert nicht / arbeitet ewig",
    body: "Status auf der Detailseite pruefen; bei Bedarf Restart. Lange Aufgaben (Render/Build) brauchen Zeit.",
    keywords: ["agent", "haengt", "reagiert nicht", "restart", "faq"],
    href: "/agents",
    hrefLabel: "Zu den Agents",
  },
  {
    id: "faq-update",
    category: "Problemloesung (FAQ)",
    title: "Update available beim Agenten",
    body: "Auf 'Update Now' klicken — Workspace-Daten bleiben erhalten.",
    keywords: ["update", "available", "agent", "faq"],
  },
  {
    id: "faq-approval",
    category: "Problemloesung (FAQ)",
    title: "Freigabe-Anfrage blockiert den Agenten",
    body: "Unter Approvals bzw. in der Benachrichtigung eine Option waehlen — erst dann macht der Agent weiter.",
    keywords: ["approval", "freigabe", "blockiert", "genehmigung", "faq"],
    href: "/approvals",
    hrefLabel: "Zu den Approvals",
  },
  {
    id: "faq-datei-finden",
    category: "Problemloesung (FAQ)",
    title: "Datei / Ergebnis finden",
    body: "Im Agenten Workspace-Tab oder unter Explorer; dort herunterladen.",
    keywords: ["datei", "ergebnis", "workspace", "explorer", "download", "faq"],
    href: "/files",
    hrefLabel: "Zum Explorer",
  },
  {
    id: "faq-benachrichtigungen",
    category: "Problemloesung (FAQ)",
    title: "Benachrichtigungen aktualisieren nicht live",
    body: "Seite einmal neu laden. Du siehst nur Benachrichtigungen deiner Agenten.",
    keywords: ["benachrichtigung", "live", "aktualisieren", "neu laden", "faq"],
  },
];

const CATEGORY_ORDER = ["Erste Schritte", "Agenten", "Funktionen", "Admin", "Problemloesung (FAQ)"];

export default function HelpPage() {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<string | null>(null);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return HELP_TOPICS;
    return HELP_TOPICS.filter((t) =>
      [t.title, t.body, ...t.keywords].join(" ").toLowerCase().includes(q),
    );
  }, [query]);

  const byCategory = useMemo(() => {
    const map = new Map<string, HelpTopic[]>();
    for (const t of filtered) {
      const arr = map.get(t.category) || [];
      arr.push(t);
      map.set(t.category, arr);
    }
    return CATEGORY_ORDER.filter((c) => map.has(c)).map((c) => [c, map.get(c)!] as const);
  }, [filtered]);

  return (
    <div className="flex h-full min-h-0 flex-col gap-6 p-6">
      {/* Titel */}
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-foreground/[0.08] bg-foreground/[0.03]">
          <HelpCircle className="h-5 w-5 text-primary" />
        </div>
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">Hilfe &amp; FAQ</h2>
          <p className="text-sm text-muted-foreground mt-0.5">
            Anleitungen, Antworten und Direktlinks zu allen Funktionen.
          </p>
        </div>
      </div>

      {/* Schnellzugriff */}
      <div className="grid gap-3 sm:grid-cols-3">
        <a
          href="/benutzerhandbuch.pdf"
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-3 rounded-xl border border-foreground/[0.08] bg-card p-4 hover:bg-foreground/[0.04] transition-colors"
        >
          <BookOpen className="h-5 w-5 text-primary shrink-0" />
          <div className="min-w-0">
            <div className="text-sm font-medium flex items-center gap-1.5">
              Benutzerhandbuch <Download className="h-3.5 w-3.5 text-muted-foreground" />
            </div>
            <div className="text-xs text-muted-foreground truncate">Klick-fuer-Klick-Anleitung (PDF)</div>
          </div>
        </a>
        <Link
          href="/onboarding"
          className="flex items-center gap-3 rounded-xl border border-foreground/[0.08] bg-card p-4 hover:bg-foreground/[0.04] transition-colors"
        >
          <Rocket className="h-5 w-5 text-primary shrink-0" />
          <div className="min-w-0">
            <div className="text-sm font-medium">Schnellstart</div>
            <div className="text-xs text-muted-foreground truncate">Onboarding-Wizard oeffnen</div>
          </div>
        </Link>
        <a
          href="https://github.com/greeves89/AI-Employee/blob/main/CHANGELOG.md"
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-3 rounded-xl border border-foreground/[0.08] bg-card p-4 hover:bg-foreground/[0.04] transition-colors"
        >
          <ExternalLink className="h-5 w-5 text-primary shrink-0" />
          <div className="min-w-0">
            <div className="text-sm font-medium">Was ist neu?</div>
            <div className="text-xs text-muted-foreground truncate">Changelog ansehen</div>
          </div>
        </a>
      </div>

      {/* Suche */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Hilfe durchsuchen... (z. B. Skill herunterladen, Exchange, Symbol)"
          className="w-full rounded-xl border border-foreground/[0.08] bg-card pl-10 pr-4 py-3 text-sm outline-none focus:border-primary/40"
        />
      </div>

      {/* Ergebnisse */}
      <div className="flex-1 overflow-y-auto space-y-6 pb-4">
        {byCategory.length === 0 && (
          <div className="flex flex-col items-center justify-center py-16 text-muted-foreground/60">
            <Search className="h-8 w-8 mb-2" />
            <p className="text-sm">Kein Treffer fuer diese Suche.</p>
          </div>
        )}
        {byCategory.map(([category, topics]) => (
          <div key={category}>
            <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/60 mb-2">
              {category}
            </div>
            <div className="space-y-2">
              {topics.map((t) => {
                const isOpen = open === t.id || query.trim().length > 0;
                return (
                  <div key={t.id} className="rounded-xl border border-foreground/[0.08] bg-card overflow-hidden">
                    <button
                      onClick={() => setOpen(open === t.id ? null : t.id)}
                      className="w-full flex items-center justify-between gap-3 px-4 py-3 text-left hover:bg-foreground/[0.03] transition-colors"
                    >
                      <span className="text-sm font-medium">{t.title}</span>
                      <ChevronDown
                        className={cn(
                          "h-4 w-4 text-muted-foreground shrink-0 transition-transform",
                          isOpen && "rotate-180",
                        )}
                      />
                    </button>
                    {isOpen && (
                      <div className="px-4 pb-3.5 -mt-1 space-y-2.5">
                        <p className="text-sm text-muted-foreground leading-relaxed">{t.body}</p>
                        {t.href && (
                          <Link
                            href={t.href}
                            className="inline-flex items-center gap-1.5 text-xs font-medium text-primary hover:underline"
                          >
                            {t.hrefLabel || "Oeffnen"} <ExternalLink className="h-3 w-3" />
                          </Link>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
