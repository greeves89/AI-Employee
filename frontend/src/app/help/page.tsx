"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useSimpleMode } from "@/hooks/use-simple-mode";
import {
  Search,
  BookOpen,
  Rocket,
  ChevronDown,
  ExternalLink,
  Download,
  Network,
  PlayCircle,
  ArrowRight,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Header } from "@/components/layout/header";
import { useTutorials } from "@/components/tutorials/tutorial-fenster";
import { TutorialVideo } from "@/components/tutorials/tutorial-video";
import { TUTORIALS } from "@/lib/tutorials";

// --- Hilfe-Index: alles was als Hilfe/Help identifizierbar ist -------------------
// Eine Quelle für FAQ + Funktions-How-Tos + Deep-Links. Die Suche filtert client-
// seitig (kein Backend, keine zusaetzliche Dependency) über title/body/keywords.
type HelpTopic = {
  id: string;
  category: string;
  title: string;
  body: string;
  keywords: string[];
  href?: string;
  hrefLabel?: string;
  /** Klick-Tutorial (lib/tutorials.ts), im aufgeklappten Eintrag abspielbar. */
  tutorial?: string;
};

const HELP_TOPICS: HelpTopic[] = [
  // --- Erste Schritte -----------------------------------------------------------
  {
    id: "begriffe",
    tutorial: "chatten",
    category: "Erste Schritte",
    title: "Agent, Chat, Aufgabe, Arbeitsbereich — was ist was?",
    body: "Agent = dein KI-Mitarbeiter (eigener Container + Gedächtnis). Chat = unterhalten (Hin und Her). Aufgabe = beauftragen (selbstständig, auch im Hintergrund). Arbeitsbereich = privater Dateibereich des Agenten (/workspace), bleibt bei Aktualisierungen erhalten.",
    keywords: ["agent", "chat", "task", "workspace", "begriffe", "grundlagen"],
    href: "/onboarding",
    hrefLabel: "Onboarding starten",
  },
  {
    id: "onboarding",
    category: "Erste Schritte",
    title: "Schnellstart mit einem Branchen-Paket (Onboarding)",
    body: "Über den Onboarding-Wizard ein vorkonfiguriertes Paket wählen (z. B. Entwickler-Team, Content-Studio, Support-Desk) — die passenden Agenten werden automatisch angelegt.",
    keywords: ["onboarding", "wizard", "start", "paket", "branche", "einrichten"],
    href: "/onboarding",
    hrefLabel: "Zum Onboarding",
  },
  // --- Agenten ------------------------------------------------------------------
  {
    id: "agent-erstellen",
    tutorial: "agent-anlegen",
    category: "Agenten",
    title: "Neuen Agenten erstellen",
    body: "Auf der Agenten-Seite oben rechts einen neuen Agenten anlegen: Name, Symbol und Farbe, Laufzeit und Modell wählen. Es werden nur freigegebene Modelle und KI-Konten angezeigt.",
    keywords: ["agent", "erstellen", "anlegen", "neu", "modell", "harness"],
    href: "/agents",
    hrefLabel: "Zu den Agenten",
  },
  {
    id: "agent-symbol",
    category: "Agenten",
    title: "Agent-Symbol (Icon + Farbe) ändern",
    body: "Das Symbol eines Agenten lässt sich beim Erstellen UND nachträglich anpassen: Agent öffnen, Einstellungen, Bereich 'Symbol', Icon und Farbe wählen (wird sofort gespeichert und auf den Karten angezeigt).",
    keywords: ["symbol", "icon", "avatar", "farbe", "bild", "aussehen", "agent"],
    href: "/agents",
    hrefLabel: "Zu den Agenten",
  },
  {
    id: "agent-rechte",
    category: "Agenten",
    title: "Rechte eines Agenten einstellen",
    body: "Im Agenten in der Leiste unter dem Chat auf „Rechte“ klicken. Die Autonomie-Stufe (L1 nur lesen bis L4 vollständig autonom) legt fest, was der Agent selbst darf; je Fähigkeit lässt sich erlauben, eine Freigabe verlangen oder verbieten. L3 arbeitet im eigenen Container selbstständig und fragt vor Außenwirkung. Die eigene Rolle kann eine Obergrenze setzen (Mitglieder: L3); Stufen darüber sind ausgegraut, Sudo-Pakete und Root-Zugriff vergibt nur, wessen Rolle keine Grenze hat. Änderungen wirken ab der nächsten Aufgabe.",
    keywords: ["rechte", "autonomie", "freigabe", "erlaubt", "verboten", "stufe", "l1", "l2", "l3", "l4"],
    tutorial: "rechte",
  },
  {
    id: "voice",
    category: "Agenten",
    title: "Mit einem Agenten sprechen (Voice-Live-Session)",
    body: "Im Agenten-Chat die Sprach-/Voice-Funktion starten und sprechen — der Agent antwortet per Sprache. Spracherkennung/-ausgabe (inkl. Microsoft/Azure-Stimmen) ist in den Einstellungen wählbar.",
    keywords: ["voice", "sprache", "sprechen", "mikrofon", "stt", "tts", "live"],
    href: "/agents",
    hrefLabel: "Zu den Agenten",
  },
  // --- Funktionen ---------------------------------------------------------------
  {
    id: "skills-download",
    category: "Funktionen",
    title: "Skills herunterladen",
    body: "Einen Skill als SKILL.md herunterladen: im Skill-Marktplatz auf das Download-Symbol neben „Installieren“ klicken oder im Detailfenster des Skills auf „Herunterladen“. Bereits installierte Skills lassen sich unter Agent, Wissen, Skills pro Skill herunterladen.",
    keywords: ["skill", "skills", "download", "herunterladen", "export", "skill.md"],
    href: "/skills",
    hrefLabel: "Zum Skill-Marktplatz",
  },
  {
    id: "skills-install",
    category: "Funktionen",
    title: "Skill in einen Agenten installieren",
    body: "Im Skill-Marktplatz zuerst oben einen Agenten auswählen, dann beim gewünschten Skill auf „Installieren“ klicken. Ohne ausgewählten Agenten weist ein Hinweis darauf hin; Fehler werden angezeigt statt verschluckt.",
    keywords: ["skill", "installieren", "hinzufügen", "agent"],
    href: "/skills",
    hrefLabel: "Zum Skill-Marktplatz",
  },
  {
    id: "meeting-planner",
    category: "Funktionen",
    title: "Besprechungsprotokoll nach MS Planner",
    body: "Aus Besprechungsaufzeichnungen erkannte Aufgaben werden automatisch in einen MS-Planner-Plan übertragen — über das M365-Konto der Person, der die Besprechung gehört. Voraussetzung: Admin hat die Planner-Plan-ID hinterlegt.",
    keywords: ["meeting", "transkription", "planner", "aufgaben", "action items", "protokoll"],
    href: "/meeting-rooms",
    hrefLabel: "Zu den Besprechungsräumen",
  },
  {
    id: "notification-task",
    category: "Funktionen",
    title: "Benachrichtigung zu Aufgaben-Details öffnen",
    body: "Ein Klick auf eine Benachrichtigung (Glocke oben) öffnet ein Fenster mit allen Details der Aufgabe: Status, Ergebnis, Kosten, Dauer, Tokens und ggf. Fehlermeldung.",
    keywords: ["benachrichtigung", "notification", "glocke", "task", "details", "ergebnis"],
    href: "/tasks",
    hrefLabel: "Zu den Aufgaben",
  },
  {
    id: "tasks-vs-chat",
    tutorial: "aufgabe",
    category: "Funktionen",
    title: "Aufgabe oder Chat — wann was?",
    body: "Frag den Agenten im Chat (schnelle Fragen, Hin und Her). Beauftrage ihn mit einer Aufgabe (klar umrissener Auftrag, läuft selbstständig, auch im Hintergrund; Ergebnis später abholen und bewerten).",
    keywords: ["task", "chat", "unterschied", "auftrag", "hintergrund"],
    href: "/tasks",
    hrefLabel: "Zu den Aufgaben",
  },
  // --- Admin --------------------------------------------------------------------
  {
    id: "exchange",
    category: "Admin",
    title: "Exchange on-prem (Mail + Kalender) einrichten",
    body: "Admin: Admin-Konsole → Integrationen (Anlage) → „Exchange (on-prem)“: Server-URL (EWS) + Auth-Modus (service_account, modern_auth oder basic) hinterlegen. Danach erscheint Exchange bei den Agent-Integrationen. Jeder Agent greift nur auf das Postfach der Person zu, der er gehört.",
    keywords: ["exchange", "on-prem", "mail", "kalender", "ews", "outlook", "integration", "admin"],
    href: "/admin?tab=integrationen-anlage",
    hrefLabel: "Zur Admin-Konsole",
  },
  {
    id: "azure-voice",
    category: "Admin",
    title: "Microsoft-/Azure-Stimmen (Speech) aktivieren",
    body: "Admin: Admin-Konsole → Sprache (Voice), Azure-Speech-Key + Region eintragen. Danach sind Azure-STT/TTS als Sprach-Option wählbar (Standard bleibt sonst faster-whisper/Edge).",
    keywords: ["azure", "speech", "stimme", "voice", "microsoft", "stt", "tts", "admin"],
    href: "/admin?tab=sprache",
    hrefLabel: "Zur Admin-Konsole",
  },
  {
    id: "dreaming",
    category: "Admin",
    title: "Dreaming-Memory (adaptives Nutzerprofil)",
    body: "Admin-Funktion (standardmäßig aus): Der Scheduler frischt periodisch das adaptive Nutzerprofil aus den Memories auf. Aktivierbar in der Admin-Konsole unter System & Lizenz → Automatisierung.",
    keywords: ["dreaming", "memory", "profil", "gedaechtnis", "automatisierung", "admin"],
    href: "/admin?tab=system",
    hrefLabel: "Zur Admin-Konsole",
  },
  {
    id: "rollen",
    category: "Admin",
    title: "Modelle, KI-Konten und Werkzeuge per Rolle freigeben",
    body: "Es werden nur freigegebene Optionen angezeigt. Der Admin legt ein KI-Konto an und gibt es per Rolle (Rechtebündel) frei — erst dann ist es für Benutzer wählbar.",
    keywords: ["rolle", "freigabe", "rechte", "ai-account", "modell", "admin", "gruppe"],
    href: "/admin?tab=roles",
    hrefLabel: "Zur Admin-Konsole",
  },
  {
    id: "betrieb-datenschutz",
    category: "Admin",
    title: "Betrieb & Datenschutz (für IT): Sichern, Rückspielen, Update, Löschen",
    body: "Täglich scripts/backup.sh ausführen (Datenbank, Arbeitsordner der Agenten, .env und Verschlüsselungsschlüssel); die letzte Sicherung steht unter Admin → Betrieb. Rückspielen mit scripts/restore.sh, Update mit scripts/update.sh (sichert vorher automatisch). Was wo liegt, welche Daten an KI-Anbieter gehen, das Lebenszeichen und die Löschwege beschreibt das Handbuch im Kapitel „Betrieb & Datenschutz (für IT)“.",
    keywords: ["backup", "sicherung", "wiederherstellen", "restore", "update", "datenschutz", "dsgvo", "löschen", "lebenszeichen", "it", "betrieb", "schlüssel"],
    href: "/admin?tab=health",
    hrefLabel: "Zur Datensicherung",
  },
  // --- Problemloesung (FAQ aus dem Benutzerhandbuch) ----------------------------
  {
    id: "faq-keine-agenten",
    category: "Problemlösung (FAQ)",
    title: "Ich sehe keinen Agenten",
    body: "Auf der Agenten-Seite siehst du nur deine eigenen Agenten. Admins finden alle unter Admin-Konsole → Alle Agenten.",
    keywords: ["agent", "leer", "sehe nichts", "faq"],
    href: "/agents",
    hrefLabel: "Zu den Agenten",
  },
  {
    id: "faq-modell-nicht-wählbar",
    category: "Problemlösung (FAQ)",
    title: "Modell oder KI-Konto nicht wählbar",
    body: "Es werden nur freigegebene Optionen angezeigt. Der Admin muss das KI-Konto anlegen und per Rolle freigeben.",
    keywords: ["modell", "account", "wählbar", "freigabe", "faq"],
  },
  {
    id: "faq-agent-hängt",
    category: "Problemlösung (FAQ)",
    title: "Agent reagiert nicht / arbeitet ewig",
    body: "Status auf der Detailseite prüfen; bei Bedarf neu starten. Lange Aufgaben (Rendern, Bauen) brauchen Zeit.",
    keywords: ["agent", "hängt", "reagiert nicht", "restart", "faq"],
    href: "/agents",
    hrefLabel: "Zu den Agenten",
  },
  {
    id: "faq-update",
    category: "Problemlösung (FAQ)",
    title: "„Aktualisierung verfügbar“ beim Agenten",
    body: "Auf „Jetzt aktualisieren“ klicken — die Daten im Arbeitsbereich bleiben erhalten.",
    keywords: ["update", "aktualisierung", "aktualisieren", "version", "agent", "faq"],
  },
  {
    id: "faq-approval",
    tutorial: "rechte",
    category: "Problemlösung (FAQ)",
    title: "Freigabe-Anfrage blockiert den Agenten",
    body: "Unter Freigaben bzw. in der Benachrichtigung eine Option wählen — erst dann macht der Agent weiter.",
    keywords: ["approval", "freigabe", "blockiert", "genehmigung", "faq"],
    href: "/approvals",
    hrefLabel: "Zu den Freigaben",
  },
  {
    id: "faq-datei-finden",
    tutorial: "dateien",
    category: "Problemlösung (FAQ)",
    title: "Datei / Ergebnis finden",
    body: "Im Agenten unter Arbeitsbereich → Dateien oder links unter Dateien; dort herunterladen.",
    keywords: ["datei", "ergebnis", "workspace", "explorer", "download", "faq"],
    href: "/files",
    hrefLabel: "Zu den Dateien",
  },
  {
    id: "faq-benachrichtigungen",
    category: "Problemlösung (FAQ)",
    title: "Benachrichtigungen aktualisieren nicht live",
    body: "Seite einmal neu laden. Du siehst nur Benachrichtigungen deiner Agenten.",
    keywords: ["benachrichtigung", "live", "aktualisieren", "neu laden", "faq"],
  },
];

const CATEGORY_ORDER = ["Erste Schritte", "Agenten", "Funktionen", "Admin", "Problemlösung (FAQ)"];

export default function HelpPage() {
  const [query, setQuery] = useState("");
  // Mitglieder: ohne Admin-Rubrik und Technik-Karten (Architektur, GitHub-Changelog).
  const { simpleMode } = useSimpleMode();
  const [open, setOpen] = useState<string | null>(null);
  const tutorialsOeffnen = useTutorials((x) => x.oeffnen);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const themen = simpleMode ? HELP_TOPICS.filter((t) => t.category !== "Admin") : HELP_TOPICS;
    if (!q) return themen;
    return themen.filter((t) =>
      [t.title, t.body, ...t.keywords].join(" ").toLowerCase().includes(q),
    );
  }, [query, simpleMode]);

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
    <div className="flex h-full min-h-0 flex-col">
      {/* Gemeinsame Kopfzeile: sie hält links Platz für den Menüknopf frei.
          Der eigene Titel mit Symbol lag auf dem Handy darunter (#907). */}
      <Header title="Hilfe & FAQ" subtitle="Anleitungen, Antworten und Direktlinks zu allen Funktionen." />
      <div className="flex min-h-0 flex-1 flex-col gap-6 p-6">
        {/* Klick-Tutorials: der schnellste Einstieg, deshalb breit über dem Schnellzugriff */}
        <button
          onClick={() => tutorialsOeffnen()}
          className="group flex w-full items-center gap-4 rounded-2xl border border-primary/25 bg-card p-5 text-left shadow-sm transition-colors hover:border-primary/40 hover:bg-primary/[0.03]"
        >
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-primary/10">
            <PlayCircle className="h-6 w-6 text-primary" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-base font-semibold">Klick-Tutorials</div>
            <div className="text-sm text-muted-foreground">
              Der schnellste Einstieg: {TUTORIALS.length} kurze Videos mit Sprecher, der Reihe nach.
            </div>
          </div>
          <ArrowRight className="h-5 w-5 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
        </button>

        {/* Schnellzugriff */}
        <div className={cn("grid gap-3 sm:grid-cols-2", !simpleMode && "lg:grid-cols-4")}>
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
              <div className="text-xs text-muted-foreground truncate">Klick-für-Klick-Anleitung (PDF)</div>
            </div>
          </a>
          <Link
            href="/onboarding"
            className="flex items-center gap-3 rounded-xl border border-foreground/[0.08] bg-card p-4 hover:bg-foreground/[0.04] transition-colors"
          >
            <Rocket className="h-5 w-5 text-primary shrink-0" />
            <div className="min-w-0">
              <div className="text-sm font-medium">Schnellstart</div>
              <div className="text-xs text-muted-foreground truncate">Onboarding-Wizard öffnen</div>
            </div>
          </Link>
          {!simpleMode && (<>
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
          <Link
            href="/help/architecture"
            className="flex items-center gap-3 rounded-xl border border-foreground/[0.08] bg-card p-4 hover:bg-foreground/[0.04] transition-colors"
          >
            <Network className="h-5 w-5 text-primary shrink-0" />
            <div className="min-w-0">
              <div className="text-sm font-medium">Architektur &amp; Schnittstellen</div>
              <div className="text-xs text-muted-foreground truncate">Diagramme, API, Tools, Modelle</div>
            </div>
          </Link>
          </>)}
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
              <p className="text-sm">Kein Treffer für diese Suche.</p>
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
                          {t.tutorial && (
                            <TutorialVideo id={t.tutorial} preload="none" className="max-w-2xl rounded-lg" />
                          )}
                          {t.href && (
                            <Link
                              href={t.href}
                              className="inline-flex items-center gap-1.5 text-xs font-medium text-primary hover:underline"
                            >
                              {t.hrefLabel || "Öffnen"} <ExternalLink className="h-3 w-3" />
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
    </div>
  );
}
