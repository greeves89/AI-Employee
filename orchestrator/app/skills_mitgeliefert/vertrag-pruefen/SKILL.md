---
name: vertrag-pruefen
description: "Liest Verträge, prüft sie Klausel für Klausel nach einer Checkliste, markiert Risiken mit einer Ampel und erstellt eine Fragenliste für den Anwalt. Nutzen, wenn ein Vertrag, AGB, eine NDA oder ein AV-Vertrag vor Unterschrift geprüft werden soll. Auslöser: Vertrag, Vertragsprüfung, AGB, NDA, Geheimhaltungsvereinbarung, AV-Vertrag, Klausel."
---

# Vertrag prüfen

Du liest einen Vertrag vollständig, ordnest jede relevante Klausel ein und markierst Risiken. Du bereitest die Entscheidung vor — du änderst nichts eigenmächtig, verhandelst nicht und unterschreibst nichts. Die Freigabe trifft ein Mensch, bei Risiken mit Anwalt oder Rechtsabteilung.

## Wann nutzen
- Ein Vertrag (Dienst-, Werk-, Liefer-, Software-, Miet-, Rahmenvertrag), AGB, eine NDA oder ein Auftragsverarbeitungsvertrag liegt zur Unterschrift vor.
- Ein Vertragsentwurf der Gegenseite soll vor der Verhandlung eingeschätzt werden.
- Zwei Fassungen sollen verglichen werden (was hat sich geändert?).

## Vorgehen
1. **Kontext klären.** Welche Seite vertreten wir (Auftraggeber/Auftragnehmer, Käufer/Verkäufer)? Ist die Gegenseite Unternehmer oder Verbraucher? Was ist das Ziel (Unterschrift, Verhandlung, Kündigung)? Gibt es eigene Musterklauseln oder Vorgaben in der Wissensbasis? Fehlt das, frag nach.
2. **Vollständig lesen.** Alle Seiten, Anlagen, mitgeltende AGB, Leistungsbeschreibungen, Preisblätter. Fehlt eine in Bezug genommene Anlage, notiere das als Risiko „rot“ — nicht annehmen, was drinsteht.
3. **Checkliste abarbeiten** (Tabelle unten). Für jede Klausel: Fundstelle im Vertrag (Ziffer/Seite), Inhalt in einem Satz, Ampel, Begründung.
4. **Gesetzliche Bezüge nachschlagen**, wo nötig — mit `gesetze_search` (falls aktiv) oder auf gesetze-im-internet.de. Nur Normen nennen, die du im Wortlaut geprüft hast.
5. **Fragenliste für den Anwalt** erstellen: konkret, je eine Frage pro Punkt, mit Verweis auf die Klausel.
6. **Ergebnis ablegen** als Datei im Arbeitsordner (z. B. `vertraege/<vertrag>-pruefung-YYYY-MM-DD.md`). Den Originalvertrag nicht verändern.
7. **Übergabe.** Soll auf Grundlage der Prüfung etwas passieren (Rückmeldung an die Gegenseite, Unterschrift, Ablehnung), hol über `request_approval` die Entscheidung eines Menschen ein.

## Checkliste
| Bereich | Worauf achten |
|---|---|
| Parteien | Richtige Firmierung, Rechtsform, Anschrift, Vertretungsberechtigung; Konzerngesellschaften korrekt |
| Leistung | Konkret beschrieben? Abnahme, Mitwirkungspflichten, Service-Level, Änderungsverfahren |
| Vergütung | Preis, Fälligkeit, Zahlungsziel, Preisanpassung, Nebenkosten, Umsatzsteuer ausgewiesen |
| Laufzeit / Kündigung | Beginn, Mindestlaufzeit, automatische Verlängerung, Kündigungsfristen, Form der Kündigung, außerordentliche Kündigung |
| Haftung | Unbegrenzt oder begrenzt? Höhe der Begrenzung im Verhältnis zum Auftragswert; Ausschlüsse; Freistellungen |
| Gewährleistung / Mängel | Mängelrechte, Fristen, Ausschlüsse; bei Software: Updates, Fehlerklassen, Reaktionszeiten |
| Vertragsstrafe | Höhe, Auslöser, Deckelung, Anrechnung auf Schadensersatz |
| Geheimhaltung | Umfang, Dauer (auch nach Vertragsende), Ausnahmen |
| Rechte / Lizenzen | Wer erhält welche Nutzungsrechte an Ergebnissen? Exklusiv? Zeitlich/räumlich begrenzt? |
| Datenschutz | Werden personenbezogene Daten im Auftrag verarbeitet? Dann AV-Vertrag nach Art. 28 DSGVO nötig (siehe unten); Drittlandübermittlung (Art. 44 ff. DSGVO) |
| Gerichtsstand / Recht | Welches Recht gilt, welches Gericht ist zuständig, Schiedsklausel? Ausländisches Recht = immer Anwalt |
| Form / Schluss | Schriftform- oder Textformklauseln, Vorrang von Anlagen, salvatorische Klausel, Abtretungsverbot |

### Datenschutz: AV-Vertrag nach Art. 28 DSGVO
Verarbeitet der Vertragspartner personenbezogene Daten in unserem Auftrag (z. B. Hosting, Cloud-Software, Lohnabrechnung, Fernwartung), braucht es einen Vertrag nach Art. 28 Abs. 3 DSGVO. Prüfe, ob er enthält:
- Gegenstand und Dauer, Art und Zweck der Verarbeitung, Art der Daten, Kategorien betroffener Personen, Rechte und Pflichten des Verantwortlichen
- Verarbeitung nur auf dokumentierte Weisung (auch für Drittlandübermittlungen)
- Vertraulichkeitsverpflichtung der eingesetzten Personen
- technische und organisatorische Maßnahmen nach Art. 32 DSGVO
- Bedingungen für Unterauftragsverarbeiter (vorherige Genehmigung, Weitergabe der Pflichten)
- Unterstützung bei Betroffenenrechten und bei den Pflichten aus Art. 32 bis 36 DSGVO
- Löschung oder Rückgabe der Daten nach Ende der Leistung
- Nachweise und Überprüfungen, einschließlich Inspektionen
Fehlt einer dieser Punkte: Ampel „rot“ und an den Datenschutzbeauftragten geben.

## Ampel
- **Grün:** marktüblich, ausgewogen, kein Handlungsbedarf.
- **Gelb:** ungünstig oder unklar formuliert; nachverhandeln oder klären.
- **Rot:** erhebliches Risiko (unbegrenzte Haftung, fehlende Anlage, einseitige Kündigungsrechte, fehlender AV-Vertrag, fremdes Recht) — nicht ohne Anwalt unterschreiben.

Hinweis zu AGB: Vorformulierte Klauseln unterliegen der AGB-Kontrolle (§§ 305 bis 310 BGB). Ob eine Klausel deshalb unwirksam ist, hängt vom Einzelfall und davon ab, ob die Gegenseite Unternehmer oder Verbraucher ist — markiere den Verdacht, entscheide ihn nicht.

## Typische Fehler
- Anlagen und mitgeltende AGB nicht gelesen.
- Haftungsbegrenzung „grün“ gesetzt, ohne die Höhe ins Verhältnis zum Auftragswert und zum möglichen Schaden zu setzen.
- Automatische Verlängerung übersehen.
- Datenverarbeitung im Auftrag nicht erkannt, weil das Wort „Datenschutz“ im Vertrag fehlt.
- Eigene Formulierungsvorschläge direkt in den Vertrag schreiben — Vorschläge gehören in die Fragenliste.
- Eine Klausel als „unwirksam“ bezeichnen, statt sie als prüfbedürftig zu markieren.

## Ausgabeformat

```markdown
# Vertragsprüfung: <Vertragsart> mit <Muster GmbH>
Geprüfte Fassung: <Dateiname, Datum/Version> · Unsere Rolle: <…> · Gegenseite: Unternehmer/Verbraucher

## Gesamteinschätzung
<2–4 Sätze; Anzahl rot/gelb/grün; wichtigstes Risiko zuerst>

## Klauseln
| Nr. | Bereich | Fundstelle | Inhalt (kurz) | Ampel | Begründung / Empfehlung |
|---|---|---|---|---|---|
| 1 | Haftung | Ziff. 9.2 | Haftung unbegrenzt | rot | … |

## Fehlende Regelungen / Anlagen
- …

## Fragen an den Anwalt / die Rechtsabteilung
1. Ziff. … : …

_Diese Prüfung ist eine Vorbereitung und keine Rechtsberatung. Die Entscheidung über Unterschrift und Änderungen trifft ein Mensch, bei roten Punkten nach anwaltlicher Prüfung._
```

## Grenzen
- Du änderst den Vertrag nicht, schickst nichts an die Gegenseite und gibst keine Zusagen — nur nach ausdrücklicher Freigabe über `request_approval`.
- Ausländisches Recht, Gesellschafts-, Kartell-, Arbeitsrecht mit Betriebsrat, hohe Summen oder rote Punkte: immer an einen Anwalt bzw. die Rechtsabteilung.
- Datenschutzfragen gehen zusätzlich an den Datenschutzbeauftragten.
