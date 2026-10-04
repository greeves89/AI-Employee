---
name: lohnabrechnung-vorbereiten
description: "Sammelt und prüft alle abrechnungsrelevanten Änderungen des Monats (Stammdaten, Ein- und Austritte, Fehlzeiten, Zuschläge) und erstellt eine Übergabeliste fürs Lohnbüro. Nutzen, wenn die monatliche Lohn- und Gehaltsabrechnung vorbereitet oder Personaldaten dafür geprüft werden sollen. Auslöser: Lohnabrechnung, Gehaltsabrechnung, Lohnbüro, Lohnbuchhaltung, Lohnsteuer, Sozialversicherung, Fehlzeiten."
---

# Lohnabrechnung vorbereiten

Du sammelst, prüfst und übergibst. Du rechnest keine Abrechnung selbst, entscheidest keine sozialversicherungs- oder steuerrechtlichen Fragen und gibst Personaldaten nicht weiter. Das Lohnbüro oder die Steuerberatung erstellt die Abrechnung, die Personalverantwortlichen geben frei.

## Wann nutzen
- Vor dem monatlichen Abrechnungslauf: Was hat sich geändert, was fehlt noch?
- Bei Ein- oder Austritten, neuen Verträgen, Gehaltsänderungen.
- Wenn Fehlzeiten, Überstunden oder Zuschläge für die Abrechnung zusammengestellt werden sollen.

## Datenschutz zuerst
- Lohn- und Personaldaten sind besonders schützenswert. Gesundheitsdaten (Arbeitsunfähigkeit) und die Konfession (Kirchensteuer) zählen zu den besonderen Kategorien nach Art. 9 DSGVO.
- **Minimalprinzip:** Nur die Daten lesen und übernehmen, die für die Abrechnung nötig sind. Keine Diagnosen, keine Begründungen von Fehlzeiten, nur Zeitraum und Art.
- **Keine Weitergabe:** Personaldaten gehen nur an das festgelegte Lohnbüro über den in der Wissensbasis festgelegten Weg. Nie in Chats mit Dritten, nie in Gruppenkanäle, nie in Beispiele oder Testdaten.
- Dateien mit Personaldaten nur im Arbeitsordner ablegen; keine Kopien in geteilten Ordnern ohne Anweisung. Nichts dauerhaft im Gedächtnis speichern, was einzelne Beschäftigte betrifft.
- Unsicher, ob du etwas sehen oder weitergeben darfst: nicht tun, nachfragen.

## Vorgehen
1. **Rahmen klären.** In Wissensbasis und Gedächtnis (`memory_search`) nachsehen: Wer ist das Lohnbüro, welcher Übergabeweg, welcher Stichtag, welche Lohnarten und Zuschlagsregeln gelten (Tarifvertrag, Betriebsvereinbarung, Arbeitsvertrag)? Fehlt etwas: einmal nachfragen, die Antwort (ohne Personenbezug) im Gedächtnis ablegen.
2. **Änderungen einsammeln** (Prüfliste unten). Quellen: Personalakte, Zeiterfassung, Mitteilungen der Führungskräfte, Postfach, falls Microsoft-365-Werkzeuge verbunden sind.
3. **Vollständigkeit prüfen.** Für jeden Eintritt die nötigen Unterlagen, für jeden Austritt das letzte Arbeitsdatum und offene Ansprüche (Resturlaub, Überstunden) als Frage ans Lohnbüro.
4. **Plausibilität prüfen** (Liste unten). Auffälligkeiten nicht korrigieren, sondern markieren.
5. **Fristen abgleichen.** Fristenkalender des Monats erstellen (Gerüst unten). Konkrete Termine nur aus dem aktuellen Kalender der Krankenkassen, des Finanzamts bzw. der Sozialversicherung übernehmen oder beim Lohnbüro erfragen, nie aus dem Kopf.
6. **Rückfragen klären.** Gebündelt an die zuständige Person, bei Freigaben über `request_approval`.
7. **Übergabeliste erstellen** (Ausgabeformat unten), als Datei im Arbeitsordner.
8. **Freigabe einholen.** Erst nach Freigabe durch die Personalverantwortlichen über `request_approval` geht die Liste ans Lohnbüro.

## Prüfliste: Was sammeln
- **Eintritte:** Eintrittsdatum, Vertragsart (Vollzeit, Teilzeit, Minijob, Werkstudent, Auszubildende), Arbeitszeit, Vergütung, Steuer-ID, Sozialversicherungsnummer, Krankenkasse, Bankverbindung; ggf. Nachweise (Studienbescheinigung, Elterneigenschaft). Prüfen, ob die Branche sofortmeldepflichtig ist (§ 28a Abs. 4 SGB IV) und ob die Meldung rechtzeitig erfolgt.
- **Austritte:** letzter Arbeitstag, Kündigungsart, Resturlaub, Überstunden, Rückgabe von Firmeneigentum (Dienstwagen, Geräte), Abfindungen nur als Hinweis ans Lohnbüro.
- **Stammdatenänderungen:** Anschrift, Bankverbindung, Familienstand, Kinder, Krankenkassenwechsel, Steuerklasse (ELStAM-Abruf macht das Lohnbüro), Arbeitszeit, Gehalt, Vertragsänderungen.
- **Fehlzeiten:** Urlaub, Arbeitsunfähigkeit (nur Zeitraum; Abruf der eAU über die Krankenkasse), Kind krank, Mutterschutz, Elternzeit, unbezahlter Urlaub, Kurzarbeit.
- **Variable Bezüge:** Überstunden, Zuschläge (Nacht, Sonntag, Feiertag, Schicht) laut gültiger Regelung, Prämien, Provisionen, Einmalzahlungen.
- **Sachbezüge und Abzüge:** Dienstwagen, Jobticket, Gutscheine, Vorschüsse, Pfändungen, betriebliche Altersversorgung, vermögenswirksame Leistungen. Steuer- und beitragsrechtliche Behandlung entscheidet das Lohnbüro.

## Plausibilitätsprüfung
- Stunden passen zur vertraglichen Arbeitszeit; Ausreißer markieren.
- Zuschläge nur für Zeiten, für die eine Regelung existiert; Quelle angeben.
- Fehlzeiten überschneiden sich nicht (z. B. Urlaub und Krankheit am selben Tag).
- Ein- und Austrittsdaten liegen im Abrechnungsmonat und passen zum Vertrag.
- Minijob- und Übergangsbereich-Grenzen, Mindestlohn und Beitragsbemessungsgrenzen: nicht selbst prüfen, aber auffällige Fälle markieren mit „aktuellen Grenzwert prüfen (Quelle: Minijob-Zentrale, Bundesregierung, Sozialversicherung)“.
- Bankverbindung neu oder geändert: Änderung über einen zweiten Weg bestätigen lassen (Schutz vor Betrugsversuchen).
- Vormonat zum Vergleich: Wer fehlt, wer ist neu, wessen Betrag ändert sich auffällig?

## Fristenkalender (Gerüst, Termine immer aktuell prüfen)

| Was | Wer | Termin | Quelle |
|---|---|---|---|
| Stichtag Änderungen an das Lohnbüro | Personal | laut Vereinbarung mit dem Lohnbüro | Wissensbasis |
| Beitragsnachweis an die Krankenkassen | Lohnbüro | Termin aus aktuellem Kalender der Krankenkasse prüfen | Krankenkasse |
| Fälligkeit der Sozialversicherungsbeiträge | Lohnbüro | Termin aus aktuellem Kalender der Krankenkasse prüfen | Krankenkasse |
| Lohnsteuer-Anmeldung | Lohnbüro | Termin aus aktuellem Kalender des Finanzamts prüfen | Finanzamt |
| An- und Abmeldungen (DEÜV) | Lohnbüro | Fristen aktuell prüfen | Krankenkasse |
| Auszahlung Löhne und Gehälter | Buchhaltung | laut Arbeitsvertrag/Firmenvorgabe | Wissensbasis |

## Typische Fehler
- Diagnosen oder Gründe für Fehlzeiten in die Übergabeliste schreiben.
- Zuschläge aus dem Gedächtnis ansetzen statt aus der gültigen Regelung.
- Beitragssätze, Grenzwerte oder Fristen als feste Zahl nennen.
- Eine Bankänderung per E-Mail ungeprüft übernehmen.
- Die Liste ohne Freigabe verschicken oder an einen anderen Empfänger als das Lohnbüro.

## Ausgabeformat

```markdown
# Übergabe Lohnabrechnung <Monat/Jahr>, Stand <Datum>
Freigabe: <offen / erteilt am … durch …>

## Eintritte
| Pers.-Nr. | Name | Eintritt | Vertragsart | Unterlagen vollständig | Offen |
|---|---|---|---|---|---|

## Austritte
| Pers.-Nr. | Name | Letzter Tag | Resturlaub | Offen |
|---|---|---|---|---|

## Stammdatenänderungen
| Pers.-Nr. | Name | Feld | Alt | Neu | gültig ab | Beleg |
|---|---|---|---|---|---|---|

## Fehlzeiten
| Pers.-Nr. | Name | Art | von | bis | Tage |
|---|---|---|---|---|---|

## Variable Bezüge
| Pers.-Nr. | Name | Lohnart | Menge/Betrag | Grundlage |
|---|---|---|---|---|

## Auffälligkeiten und Rückfragen
- <Pers.-Nr.>: <Auffälligkeit, was geprüft werden soll>
```

## Grenzen und Übergabe an einen Menschen
- Abrechnung, Meldungen und steuer- oder sozialversicherungsrechtliche Beurteilung macht das Lohnbüro bzw. die Steuerberatung.
- Arbeitsrechtliche Fragen (Kündigung, Zeugnis, Abfindung, Mutterschutz): an die Personalverantwortlichen. Rechtsgrundlagen nur mit dem Gesetzes-Werkzeug (`gesetze_search`) nachschlagen, falls aktiv, und als Hinweis kennzeichnen.
- Österreich und Schweiz haben eigene Melde- und Abgaberegeln: dort nur sammeln und die fachliche Prüfung vollständig dem Lohnbüro überlassen.
