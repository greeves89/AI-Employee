---
name: recherche-mit-quellen
description: "Belegte Recherche statt Linkliste – Frage schärfen, Quellen selbst öffnen, Primärquellen bevorzugen, jede Aussage mit Quelle und Abrufdatum, Widersprüche und Sicherheitsgrad offenlegen. Nutzen, wenn Fakten, Marktinfos, Anbieter oder Hintergründe recherchiert werden sollen."
---

# Recherche mit Quellen

## Wann nutzen
- Jemand will etwas wissen, das du nicht sicher aus eigenem Wissen beantworten kannst: Fakten, Zahlen, Anbieter, Produkte, Regeln, aktuelle Entwicklungen.
- Ergebnisse sollen weitergegeben oder für Entscheidungen genutzt werden.
- Nicht für reine Meinungsfragen oder Brainstorming — dort sagst du offen, dass es keine Recherche ist.

## Grundsatz
Eine Recherche liefert **belegte Aussagen**, keine Linkliste und kein Bauchgefühl. Jede Aussage im Ergebnis hat eine Quelle, die du selbst geöffnet und gelesen hast. Was du nicht belegen kannst, steht als „nicht belegt“ da oder fehlt.

## Vorgehen
1. **Frage schärfen.** Formuliere die Frage in einem Satz neu und kläre: Wozu wird das Ergebnis gebraucht? Welcher Zeitraum, welche Region (Deutschland, DACH, EU, weltweit)? Wie tief (Überblick in 10 Minuten oder belastbare Grundlage)? Ist die Frage mehrdeutig und die Antwort nicht nachschlagbar, frage nach (`escalate_if_unsure` oder direkt im Chat), bevor du suchst.
2. **Vorhandenes Wissen prüfen.** Suche zuerst im Gedächtnis und in der Wissensbasis (`memory_search`, `brain_search`, falls verfügbar), ob es dazu schon eine Recherche gibt. Übernimm daraus nur, was noch aktuell ist.
3. **Suchplan.** Zerlege die Frage in 2–5 Teilfragen und lege Suchbegriffe fest — auf Deutsch und Englisch, mit Fachbegriffen und Synonymen.
4. **Suchen.** Mit `web_search` bzw. dem Browser-Werkzeug. Bei Rechtsfragen zusätzlich `gesetze_search`, falls auf der Anlage aktiv.
5. **Quellen selbst öffnen.** Öffne jede Quelle, die du verwenden willst, und lies die relevante Stelle. Ein Suchtreffer-Ausschnitt ist kein Beleg. Ist eine Seite nicht erreichbar oder hinter einer Bezahlschranke, verwende sie nicht als Beleg.
6. **Quellen bewerten.** Bevorzuge Primärquellen:
   - Gesetze, Verordnungen, Behörden, amtliche Statistik
   - Originalstudien, Normen, Herstellerdokumentation, Geschäftsberichte
   - danach Fachmedien und Verbände
   - zuletzt Blogs, Foren, Vergleichsportale, KI-generierte Seiten
   Achte auf Datum, Autor/Herausgeber, Interessenlage (Werbung, Anbieter über sich selbst) und ob Zahlen nachvollziehbar hergeleitet sind.
7. **Aussagen belegen.** Notiere zu jeder Aussage: Quelle (Titel, Herausgeber, URL), Datum der Quelle (falls angegeben) und dein Abrufdatum. Zahlen 1:1 mit Einheit und Bezugsjahr übernehmen.
8. **Gegenprüfen.** Wichtige Aussagen und alle Zahlen mit einer zweiten, unabhängigen Quelle bestätigen. Zwei Seiten, die voneinander abschreiben, zählen als eine.
9. **Widersprüche offenlegen.** Weichen Quellen ab, nenne beide Werte, beide Quellen und eine mögliche Erklärung (anderes Jahr, andere Abgrenzung, andere Methode). Entscheide nicht stillschweigend für einen Wert.
10. **Sicherheitsgrad angeben.** Je Kernaussage: hoch / mittel / niedrig (siehe unten).
11. **Ergebnis schreiben und ablegen.** Kurzfassung + Tabelle + Quellenliste, als Markdown-Datei im Arbeitsordner (z. B. `recherchen/JJJJ-MM-TT_<thema>.md`), mit `present_file` bereitstellen. Belastbare Ergebnisse mit Datum in die Wissensbasis übernehmen (`brain_contribute`), damit andere Agenten darauf aufbauen.

## Sicherheitsgrad
| Grad | Bedeutung |
|---|---|
| hoch | Primärquelle oder mindestens zwei unabhängige, seriöse Quellen stimmen überein; aktuell |
| mittel | eine seriöse Quelle, oder Quellen weichen leicht ab, oder Stand älter |
| niedrig | nur Sekundärquellen mit Interessenlage, widersprüchlich oder veraltet |

## Prüfliste vor der Abgabe
- [ ] Die Kurzfassung beantwortet die geschärfte Frage direkt.
- [ ] Jede Aussage in Kurzfassung und Tabelle hat eine Quellennummer.
- [ ] Jede Quelle wurde selbst geöffnet; URL und Abrufdatum stehen dabei.
- [ ] Zahlen stimmen exakt mit der Quelle überein (Einheit, Jahr, Bezugsgröße).
- [ ] Widersprüche und Lücken sind genannt, nicht geglättet.
- [ ] Sicherheitsgrad je Kernaussage ist angegeben.
- [ ] Nichts Erfundenes: keine geratenen URLs, Studien, Zitate oder Zahlen.

## Typische Fehler
- Eine Linkliste abliefern statt Antworten.
- URLs, Studientitel oder Zitate aus dem Gedächtnis „ergänzen“ — jede Quelle muss real geöffnet worden sein.
- Herstellerangaben als neutrale Tatsache darstellen.
- Veraltete Zahlen ohne Bezugsjahr übernehmen.
- Aus einer Einzelquelle eine allgemeine Aussage machen.
- Die Frage stillschweigend umdeuten, weil dazu leichter etwas zu finden war.

## Ausgabeformat
```markdown
# Recherche: <geschärfte Frage>
Stand: TT.MM.JJJJ · Umfang: <Region, Zeitraum> · Erstellt von: <Agent>

## Kurzfassung
- <Kernaussage 1> [1][3] — Sicherheit: hoch
- <Kernaussage 2> [2] — Sicherheit: mittel
- Nicht belegbar: <was offen blieb>

## Ergebnisse im Detail
| Aspekt | Ergebnis | Quelle(n) | Sicherheit | Anmerkung |
|---|---|---|---|---|
| <…> | <…> | [1], [3] | hoch | <z. B. Wert von 2025> |

## Widersprüche und Lücken
- <Quelle [2] nennt X, Quelle [4] nennt Y — vermutlich andere Abgrenzung>

## Quellen
1. <Titel> — <Herausgeber>, <Datum der Quelle>. <URL> (abgerufen am TT.MM.JJJJ) — Primärquelle
2. …
```

## Grenzen und Übergabe an Menschen
- Die Recherche ist Entscheidungsgrundlage, nicht die Entscheidung. Bei Recht, Steuern, Medizin, Finanzen oder Personal: Ergebnis als Vorbereitung kennzeichnen und auf Prüfung durch eine Fachperson hinweisen.
- Keine Recherche zu Privatpersonen über das hinaus, was für den Auftrag nötig und erkennbar zulässig ist (Datenschutz). Im Zweifel nachfragen.
- Zugangsgeschützte Quellen (Logins, Bezahlinhalte) nur nutzen, wenn der Nutzer das ausdrücklich ermöglicht hat.
- Ist die Lage nach gründlicher Suche unklar, sag das deutlich — ein ehrliches „nicht belastbar zu klären“ ist ein gültiges Ergebnis.
