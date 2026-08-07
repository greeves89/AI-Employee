# AI Employee — Was kann das Tool, und was habe ich als Mitarbeiter davon?

Stand: 2026-08-05 · Version 1.143.0
Grundlage: Inventar der Codebasis (API-Bereiche, Agenten-Werkzeuge, Sprach-Kommandos), nicht aus dem Gedächtnis.

---

## Die Kernidee in einem Satz

Kein Chatbot, der Texte schreibt — ein **Kollege mit Zugriff**: eigener Rechner (Container), eigenes Postfach, eigener Kalender, eigenes Gedächtnis, eigene Werkzeuge. Er arbeitet weiter, wenn man das Fenster schließt, und meldet sich, wenn er fertig ist.

Der Unterschied zu einem Assistenten: Ein Assistent antwortet. Ein Mitarbeiter **erledigt** — und man erfährt hinterher, was er getan hat.

---

## 1. Microsoft 365 — der Arbeitsplatz, den alle schon haben

**Was das Tool kann:** Postfach lesen, durchsuchen, schreiben, antworten, als gelesen markieren · Kalender lesen, Termine anlegen, ändern, absagen · Teams-Kanäle und -Chats lesen und schreiben · Planner-Pläne und -Aufgaben · To-Do-Listen · OneDrive durchsuchen und Dateien lesen · Besprechungen ansetzen. Anmeldung über Entra ID (SSO), keine zweite Benutzerverwaltung.

**Was ein Mitarbeiter konkret macht:**
- „Fass mir zusammen, was über Nacht im Postfach kam, und sag mir, was heute liegen bleibt, wenn ich nichts tue."
- „Der Termin Donnerstag kollidiert — such einen neuen Slot mit allen vier und verschieb ihn."
- „Aus dem Protokoll von gestern: leg die Aufgaben im Planner an und weise sie zu."
- „Was hat der Kunde in den letzten drei Mails zum Thema Schnittstelle gesagt?"

**Daily Doings, die wegfallen:** Posteingang sortieren · Terminfindung per Mail-Pingpong · Protokoll in Aufgaben übertragen · Statusmails schreiben · in OneDrive nach der letzten Fassung suchen.

---

## 2. Aufgaben, Zeitpläne, Auslöser — Arbeit, die von selbst passiert

**Was das Tool kann:** Echte Aufgaben, die den Dialog überleben und im Hintergrund laufen · mehrere gleichzeitig · wiederkehrende Zeitpläne (stündlich, täglich, Cron) · **ereignisgesteuerte Auslöser**: nicht „jede Stunde nachsehen", sondern „wenn X passiert, tu Y" · Aufgaben-Stapel für Serien.

**Was ein Mitarbeiter konkret macht:**
- „Jeden Morgen um sieben: Lage im Projekt zusammenfassen, offene Punkte, was heute ansteht."
- „Wenn im Postfach eine Reklamation eintrifft: Vorgang anlegen, Fachbereich informieren, mir Bescheid geben."
- „Schau stündlich, ob die Schnittstelle noch läuft — bei Störung sofort melden."
- „Bau mir bis morgen früh die Auswertung der letzten drei Monate als PDF."

**Daily Doings, die wegfallen:** Morgendliche Statusrunde zusammenklauben · Wiedervorlagen im Kopf behalten · manuelle Kontrollblicke auf Systeme · Standardberichte tippen.

---

## 3. Sprache — mit dem Agenten telefonieren

**Was das Tool kann:** Echtzeit-Sprachgespräch (AWS Nova Sonic oder Azure), keine Push-to-Talk-Bedienung · währenddessen: nachschlagen, Termine und Mails vorlesen, Wissen durchsuchen, sich etwas merken, **Aufgaben einplanen, die nach dem Auflegen weiterlaufen** · der Agent meldet sich im Gespräch, wenn eine Aufgabe fertig wird, und blendet Ergebnisdateien direkt ein · Fokus-Modus für freihändiges Arbeiten · proaktiver Hinweis vor dem nächsten Termin.

**Was ein Mitarbeiter konkret macht:**
- Auf dem Weg ins Büro: „Was steht heute an, und ist über Nacht etwas Kritisches gekommen?"
- Zwischen zwei Terminen: „Nimm das als Aufgabe mit: Angebot für Kunde Meier vorbereiten, Zahlen aus dem letzten Quartal."
- Beim Arbeiten: „Zeig mir das Dokument auf dem Bildschirm."

**Daily Doings, die wegfallen:** Tippen, wenn die Hände voll sind · Notizzettel für später · „ich mach das gleich, wenn ich am Rechner bin".

---

## 4. Zweites Gehirn — Firmenwissen, das antwortet

**Was das Tool kann:** Wissens-Vaults mit semantischer Suche · ganze Beiträge lesen, nicht nur Schnipsel · Verbindungen zwischen Themen (Graph, 2D und 3D) · Agenten tragen selbst Wissen ein und pflegen es · geteilte Vaults mit Rechten pro Gruppe · Wissensquellen automatisch beobachten (Feeds) · anbindbar an OpenWebUI und andere Systeme über MCP.

**Was ein Mitarbeiter konkret macht:**
- „Gibt es eine Anleitung für den Prozess, und wer hat sie zuletzt angefasst?"
- „Womit hängt dieses Verfahren sonst noch zusammen?"
- „Merk dir: Bei diesem Kunden läuft die Freigabe immer über die Teamleitung."

**Daily Doings, die wegfallen:** Kollegen nach dem Ablageort fragen · Wiki durchklicken · Wissen geht mit Urlaub oder Kündigung verloren.

---

## 5. Gedächtnis — er lernt den Betrieb

**Was das Tool kann:** Erinnerungen mit Kategorie, Raum und Haltbarkeit (dauerhaft/vorübergehend) · semantische Suche · **automatische Verknüpfung**: neue Erinnerungen werden mit verwandten verbunden · Brücke zwischen persönlichem Gedächtnis und Firmenwissen · Verdichtung langer Verläufe, damit nichts wegläuft · nächtliche Selbstreflexion über die eigenen Gespräche.

**Was ein Mitarbeiter davon hat:** Man erklärt Dinge **einmal**. Präferenzen, Zuständigkeiten, Sonderfälle, Tonfall gegenüber bestimmten Kunden — beim nächsten Mal sitzt es.

---

## 6. Der Agent am Bildschirm (Computer-Use)

**Was das Tool kann:** Tray-App für macOS und Windows, verbunden über eine gesicherte Leitung · Bildschirmfoto ansehen und auswerten · klicken, tippen, scrollen, ziehen · Programme öffnen und schließen · Bedienelemente über den Bedienungshilfen-Baum finden statt über Pixelraten · Zwischenablage lesen und setzen · Freigabe pro Aktion einstellbar.

**Was ein Mitarbeiter konkret macht:**
- „Schau auf meinen Bildschirm — warum meckert das Programm?"
- Altsysteme ohne Schnittstelle bedienen lassen (genau dort, wo sonst jede Automatisierung endet).

**Ehrlicher Stand:** Öffnen und Ansehen läuft. **Navigieren und Formulare ausfüllen ist noch nicht verlässlich** — der Agent greift zu oft zu anderen Mitteln, statt die Bridge zu nutzen. Offener Punkt aus dem Kundenfeedback, kein fertiges Versprechen.

---

## 7. Team — mehrere Agenten, die zusammenarbeiten

**Was das Tool kann:** Mehrere Agenten mit eigenen Rollen · sie fragen sich gegenseitig und delegieren · Team-Aufgaben mit Übersicht für die Leitung · **Besprechungsräume**: Agenten diskutieren ein Thema, halten Beschlüsse fest und arbeiten die Punkte danach wirklich ab · Taskforce-Modus für ein gemeinsames Ergebnis · Folgetermine entstehen automatisch.

**Was ein Mitarbeiter konkret macht:** Statt einem Alleskönner ein kleines Team — Recherche, Entwicklung, Marketing, Projektsteuerung. Man gibt der Fachkraft den Auftrag, die holt sich den Rest.

---

## 8. Eigene Anwendungen bauen und betreiben

**Was das Tool kann:** Der Agent baut kleine Werkzeuge, startet sie als Container, liest ihre Protokolle, baut sie nach Änderungen neu · **Freigabe gezielt**: an eine Person, an alle Angemeldeten oder als Link ohne Anmeldung · Übersicht über alle laufenden Anwendungen.

**Was ein Mitarbeiter konkret macht:**
- „Bau mir ein kleines Formular, mit dem die Abteilung Störungen meldet."
- „Die Auswertung soll als Seite laufen, die ich der Leitung schicken kann."

**Daily Doings, die wegfallen:** Excel-Wildwuchs · Warten auf IT-Kapazität für Kleinigkeiten.

---

## 9. Fähigkeiten-Marktplatz und Workflows

**Was das Tool kann:** Marktplatz mit Fertigkeiten, die Agenten selbst finden, installieren und bewerten · eigene Quellen (auch selbst gehostet) mit Sicherheitsprüfung und Herkunftsnachweis · **Aufzeichnungsmodus**: einen Arbeitsablauf einmal vormachen, daraus wird eine wiederverwendbare Fertigkeit · Workflow-Editor für feste Abläufe · Branchenpakete für schnellen Start.

**Was ein Mitarbeiter konkret macht:** Einen Ablauf **einmal** zeigen statt ihn zu beschreiben. Ab dann kann der Agent ihn — und alle Kollegen auch.

---

## 10. Vertrauen und Kontrolle — der Teil, der Betriebsräte überzeugt

**Was das Tool kann:**
- **Autonomie-Matrix**: pro Agent festlegen, was ohne Rückfrage geht und was nicht
- **Freigaben**: kritische Schritte erfordern eine menschliche Bestätigung
- **Entscheidungs-Zeitreise**: für jede Aufgabe Schritt für Schritt nachspielen, warum der Agent was getan hat — mit Dauer und Kosten
- **DLP-Ausgangsfilter**: ausgehende Texte auf personenbezogene Daten und Geheimnisse prüfen (blockieren, schwärzen, protokollieren)
- **Protokoll** aller sicherheitsrelevanten Vorgänge, ohne Klartext-Geheimnisse
- **Budgets** pro Agent, mit automatischem Sparmodus
- **Rollen und Rechtebündel**, geteilte Wissensbereiche mit Gruppenrechten
- **Selbst gehostet**: läuft im eigenen Rechenzentrum, auch auf einem Kleinstrechner. Modelle wahlweise über Azure, AWS Bedrock, Vertex oder direkt.

**Was ein Mitarbeiter davon hat:** Die Frage „was hat das Ding da eigentlich gemacht?" ist beantwortbar — nicht mit einem Gefühl, sondern mit einer Aufzeichnung.

---

## 11. Erreichbarkeit

Weboberfläche · Sprache · **Telegram** (Text, Sprachnachrichten werden transkribiert, Fotos werden angesehen, Dateien kommen als Anhang zurück) · **iOS-App** mit Push · Kiosk-Ansicht fürs Büro · Anbindung an OpenWebUI und andere Systeme über MCP.

Der Agent ist damit dort, wo der Mitarbeiter ohnehin ist — auch unterwegs.

---

## Ein Tag mit dem Agenten

| Uhrzeit | Was passiert | Ohne Agent |
|---|---|---|
| 07:00 | Lagebericht liegt bereit: Postfach, Kalender, offene Punkte, Auffälligkeiten | 30–45 Min Sichten |
| 08:15 | Auf dem Weg per Sprache: „Nimm das als Aufgabe mit …" | Notizzettel, später abtippen |
| 09:00 | Protokoll von gestern ist in Planner-Aufgaben übersetzt und zugewiesen | 20 Min Übertragen |
| Laufend | Störungsmails lösen automatisch einen Vorgang aus | Jemand muss hinsehen |
| 14:00 | „Schau auf meinen Bildschirm, warum meckert das?" | Kollegen holen |
| 16:00 | Auswertung fertig, als PDF eingeblendet, Ergebnis wird vorgelesen | 2 Std Arbeit |
| Nachts | Reflexion über den Tag, Wissen wird verknüpft und verdichtet | — |

---

## Was heute noch nicht verlässlich läuft

Bewusst mit aufgeführt, weil ein Pitch ohne diesen Teil beim ersten Test auffliegt:

- **Navigieren und Ausfüllen am fremden Bildschirm** — Öffnen und Ansehen ja, geführte Bedienung noch nicht zuverlässig
- **Sprachausgabe verstummt gelegentlich** unter AWS (in Beobachtung)
- **Live-Fortschritt im Sprach-Panel** — der Stand kommt derzeit nur auf Nachfrage

---

## Die drei Sätze für die Geschäftsführung

1. **Nicht noch ein Chatbot, sondern Arbeitskraft**: er hat Zugriff, arbeitet eigenständig weiter und liefert Ergebnisse — keine Vorschläge.
2. **Er wächst in den Betrieb hinein**: Gedächtnis, Firmenwissen und aufgezeichnete Abläufe machen ihn mit jeder Woche passgenauer. Diesen Vorsprung kann kein Zukauf ersetzen.
3. **Er ist prüfbar und bleibt im Haus**: Autonomie regelbar, jede Entscheidung nachspielbar, Datenabfluss gefiltert, Betrieb im eigenen Rechenzentrum.
