---
name: uebersetzen-lokalisieren
description: "Übersetzt und lokalisiert Texte (u. a. Deutsch, Englisch, Französisch, Spanisch) mit Firmenglossar, passenden Formaten und Anrede, ohne sinnverändernde Kürzungen, mit Prüfung und Markierung unsicherer Stellen. Nutzen, wenn Texte, Dokumente oder Oberflächen in eine andere Sprache sollen."
---

# Übersetzen und lokalisieren

## Wann nutzen
- Ein Text, Dokument, eine E-Mail, Website, Produktbeschreibung oder Oberfläche soll in eine andere Sprache.
- Ein vorhandener Text soll an einen anderen Markt angepasst werden (z. B. Deutschland → Schweiz, britisches → amerikanisches Englisch).

## Grundsatz
Übersetzen heißt: **gleicher Inhalt, gleiche Wirkung, natürliche Zielsprache.** Nichts weglassen, nichts hinzuerfinden, Zahlen und Namen 1:1. Was du nicht sicher übertragen kannst, markierst du.

## Vorgehen
1. **Auftrag klären.** Ausgangs- und Zielsprache inkl. Variante (de-DE, de-AT, de-CH; en-GB, en-US; fr-FR, fr-CH; es-ES, es-MX …). Zweck und Zielgruppe (Kunde, Fachpublikum, intern, Behörde). Textsorte (Vertrag, Marketing, Anleitung, Oberfläche). Anrede: Sie oder Du? Fehlt etwas Wesentliches, nachfragen.
2. **Glossar laden.** Suche in Wissensbasis und Gedächtnis (`brain_search`, `memory_search`, falls verfügbar) nach Firmenglossar, Terminologieliste, Styleguide, früheren Übersetzungen. Feste Begriffe (Produktnamen, Abteilungen, Fachbegriffe) daraus übernehmen.
3. **Ausgangstext verstehen.** Ganzen Text lesen, bevor du übersetzt. Mehrdeutigkeiten, Fehler im Original und unklare Abkürzungen notieren — nicht stillschweigend „verbessern“.
4. **Übersetzen.** Satzweise sinngetreu, aber idiomatisch in der Zielsprache. Struktur (Überschriften, Listen, Tabellen, Platzhalter wie `{name}` oder `%s`, Markdown, HTML-Tags) unverändert lassen.
5. **Lokalisieren** (siehe Formate unten): Datum, Zahlen, Währung, Maßeinheiten, Anrede, Adress- und Telefonformate, kulturelle Bezüge. Beträge und Maße **nicht umrechnen**, außer es ist ausdrücklich gewünscht — dann Original in Klammern dazu.
6. **Nicht übersetzen:** Eigennamen, Marken, Produktnamen, Code, Dateinamen, URLs, Gesetzesbezeichnungen in Zitaten (ggf. Erläuterung in Klammern ergänzen).
7. **Prüfen** (siehe Prüfung).
8. **Unsichere Stellen markieren** mit `[PRÜFEN: Grund]` direkt im Text und gesammelt in einer Liste.
9. **Glossar pflegen.** Neue oder abgestimmte Begriffe als Vorschlag sammeln und nach Bestätigung in die Wissensbasis übernehmen (`brain_contribute`).
10. **Ablegen.** Übersetzung als Datei im Arbeitsordner (Dateiname mit Sprachkürzel, z. B. `angebot_en-GB.docx`), Format des Originals beibehalten, mit `present_file` bereitstellen.

## Formate (Richtwerte, Firmen-Styleguide geht vor)
| | Deutsch (DE/AT) | Deutsch (CH) | Englisch (US) | Englisch (GB) | Französisch (FR) | Spanisch (ES) |
|---|---|---|---|---|---|---|
| Datum | 31.12.2026 | 31.12.2026 | 12/31/2026 | 31/12/2026 | 31/12/2026 | 31/12/2026 |
| Dezimal | 1.234,56 | 1'234.56 (bei Beträgen üblich) | 1,234.56 | 1,234.56 | 1 234,56 | 1.234,56 |
| Währung | 12,50 € | CHF 12.50 | €12.50 | €12.50 | 12,50 € | 12,50 € |
| Anrede | Sie/Du nach Vorgabe | Sie/Du nach Vorgabe | you | you | vous/tu | usted/tú |

Weitere Hinweise:
- **Schweiz:** kein „ß“, stattdessen „ss“.
- **Österreich:** eigene Begriffe beachten (z. B. „Jänner“), wenn der Text für Österreich bestimmt ist.
- **Englisch:** Schreibweise einheitlich US oder GB („organize/organise“). Ein eindeutiges Datumsformat (31 December 2026) vermeidet Verwechslungen.
- **Französisch (FR):** Leerzeichen vor `; : ! ?` und innerhalb von « Guillemets ».
- **Spanisch:** Lateinamerika nutzt teils andere Zahlenformate und Begriffe als Spanien — Zielmarkt klären.
- ISO-Format (2026-12-31) ist für technische Texte in allen Sprachen unmissverständlich.

## Prüfung
- [ ] **Vollständigkeit:** Absatz für Absatz abgleichen — nichts fehlt, nichts kam dazu.
- [ ] **Zahlen, Daten, Beträge, Namen, Artikelnummern** 1:1 gegen das Original geprüft.
- [ ] **Glossar** eingehalten, Begriffe durchgehend gleich übersetzt.
- [ ] **Rückübersetzung stichprobenartig:** 3–5 kritische Sätze (Zusagen, Einschränkungen, Zahlen, Verneinungen) gedanklich zurückübersetzen und mit dem Original vergleichen.
- [ ] **Verneinungen und Einschränkungen** („nicht“, „nur“, „mindestens“, „bis zu“) korrekt übertragen.
- [ ] Platzhalter, Tags und Formatierung intakt; Oberflächentexte passen in die Länge.
- [ ] Anrede durchgehend einheitlich.
- [ ] Unsichere Stellen markiert und gelistet.

## Typische Fehler
- Kürzen oder „glätten“, wo das Original umständlich ist — der Sinn ändert sich.
- Falsche Freunde: „eventuell“ ≠ eventually, „aktuell“ ≠ actual, „Chef“ ≠ chef, „sensibel“ ≠ sensible.
- Wörtlich übersetzte Redewendungen.
- Zwischen Sie und Du wechseln.
- Beträge oder Maße still umrechnen.
- Dezimaltrennzeichen vertauschen (1.500 vs. 1,500).

## Ausgabeformat
```markdown
# Übersetzung: <Dokument> (<Quelle> → <Ziel>)
Zweck/Zielgruppe: <…> · Anrede: <Sie/Du/…> · Glossar: <verwendet / keins vorhanden>

<übersetzter Text, Struktur wie Original>

## Hinweise zur Übersetzung
| Stelle | Hinweis | Vorschlag |
|---|---|---|
| Abs. 3 | [PRÜFEN] Begriff „…“ mehrdeutig | <Variante A / B> |

## Glossar-Vorschläge
| Ausgangsbegriff | Zielbegriff | Kontext |
|---|---|---|
```

## Grenzen und Übergabe an Menschen
- **Rechtlich verbindliche Texte** (Verträge, AGB, Datenschutzerklärungen), **Medizin/Sicherheit** (Gebrauchs- und Sicherheitshinweise) und **beglaubigte Übersetzungen** für Behörden: nur als Arbeitsübersetzung kennzeichnen; Prüfung durch Fachübersetzer bzw. Fachperson empfehlen. Beglaubigungen kann nur eine dafür ermächtigte bzw. beeidigte Person vornehmen.
- Marketing-Slogans und Wortspiele: Varianten anbieten, Entscheidung beim Menschen lassen.
- Sprachen, in denen du unsicher bist, offen benennen und Prüfung durch Muttersprachler empfehlen.
- Veröffentlichung oder Versand nur nach Freigabe (`request_approval`).
