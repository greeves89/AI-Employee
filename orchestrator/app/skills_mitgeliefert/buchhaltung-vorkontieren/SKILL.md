---
name: buchhaltung-vorkontieren
description: "Prüft Eingangs- und Ausgangsbelege auf Pflichtangaben, schlägt Konto (SKR03/SKR04) und Steuerbehandlung vor und liefert eine Vorkontierungsliste für Buchhaltung oder Steuerberatung. Nutzen, wenn Belege gesichtet, vorkontiert oder für die Übergabe aufbereitet werden sollen."
---

# Belege vorkontieren

Du bereitest Belege so auf, dass die Buchhaltung oder die Steuerberatung sie ohne Rückfragen buchen kann. Du buchst selbst nichts. Die Entscheidung über Konto, Steuer und Buchung trifft ein Mensch.

## Wann nutzen
- Ein Stapel Rechnungen, Quittungen oder Gutschriften soll vor der Übergabe gesichtet werden.
- Jemand fragt „Auf welches Konto gehört das?“ oder „Ist die Rechnung so in Ordnung?“.
- Monats- oder Quartalsabschluss: offene Belege sollen vollständig und vorkontiert vorliegen.

## Vorgehen
1. **Rahmen klären.** Suche in Wissensbasis und Gedächtnis (`memory_search`) nach dem Kontenrahmen der Firma (SKR03, SKR04 oder eigener), nach firmenspezifischen Konten, Kostenstellen und der Frage, ob die Firma Kleinunternehmer ist oder Ist-/Soll-Versteuerung nutzt. Findest du nichts: einmal nachfragen und die Antwort im Gedächtnis ablegen.
2. **Beleg unverändert lassen.** Original nie bearbeiten, umbenennen oder überschreiben. Arbeite mit Notizen in einer eigenen Datei im Arbeitsordner. Bei E-Rechnungen (XRechnung, ZUGFeRD) ist der strukturierte Datensatz maßgeblich, nicht die Bildansicht.
3. **Belegart bestimmen.** Eingangsrechnung, Ausgangsrechnung, Gutschrift, Kleinbetragsrechnung, Quittung/Kassenbeleg, Reisekosten, Eigenbeleg. Eigenbelege nur als Notlösung und immer markieren.
4. **Pflichtangaben prüfen** (Prüfliste unten). Jede fehlende Angabe als Rückfrage festhalten, nicht stillschweigend ergänzen.
5. **Steuer bestimmen.**
   - Regelsatz 19 %, ermäßigter Satz 7 %; steuerfreie Umsätze nur mit Hinweis auf der Rechnung.
   - **Reverse Charge erkennen** (§ 13b UStG): Hinweis „Steuerschuldnerschaft des Leistungsempfängers“, Rechnung ohne Umsatzsteuer, typisch bei Leistungen ausländischer Unternehmer oder bei Bauleistungen zwischen Bauunternehmen. Kennzeichnen, die Buchung mit dem passenden Steuerschlüssel entscheidet die Buchhaltung.
   - **Innergemeinschaftliche Lieferung/Erwerb**: USt-IdNr. beider Seiten und Hinweis auf Steuerbefreiung prüfen; nur als Hinweis ausweisen, nicht selbst beurteilen.
   - Ausländische Umsatzsteuer (z. B. Hotel im Ausland) ist kein Vorsteuerabzug in Deutschland; als Hinweis markieren.
6. **Konto vorschlagen.** Nur Konten, die du sicher kennst oder die in der Wissensbasis stehen. Bei Unsicherheit „Konto offen“ eintragen und die Frage benennen, statt zu raten. Die Bezeichnung im System der Firma gegenprüfen; Automatikkonten (Vorsteuer wird mitgebucht) beachten.
7. **Sonderfälle markieren.** Bewirtung (Anlass und Teilnehmer nötig, nur teilweise als Betriebsausgabe abziehbar), Geschenke an Geschäftspartner (Freigrenze aktuell prüfen), Anlagegüter (GWG-Grenzen und Abschreibung aktuell prüfen), private Mitveranlassung, Anzahlungen, Dauerschuldverhältnisse.
8. **Rückfragen bündeln.** Eine Liste je Beleg, nicht ein Dutzend Einzelnachrichten. Bei Bedarf über die Freigabe-Funktion (`request_approval`) an die zuständige Person.
9. **Übergabe erstellen.** Tabelle (Ausgabeformat unten) als Datei im Arbeitsordner, dazu die Rückfragenliste.
10. **Nichts buchen.** Ein Export oder eine Buchung im Finanzsystem passiert nur nach ausdrücklicher Freigabe über `request_approval`.

## Prüfliste: Pflichtangaben (§ 14 Abs. 4 UStG)
- [ ] Vollständiger Name und Anschrift des leistenden Unternehmers und des Leistungsempfängers
- [ ] Steuernummer oder USt-IdNr. des leistenden Unternehmers
- [ ] Ausstellungsdatum
- [ ] Fortlaufende, einmalige Rechnungsnummer
- [ ] Menge und handelsübliche Bezeichnung der Lieferung bzw. Art und Umfang der Leistung
- [ ] Zeitpunkt der Lieferung oder Leistung (Monat genügt)
- [ ] Entgelt nach Steuersätzen aufgeschlüsselt, im Voraus vereinbarte Minderungen (Skonto, Boni)
- [ ] Steuersatz und Steuerbetrag oder Hinweis auf Steuerbefreiung
- [ ] Bei Reverse Charge: Hinweis „Steuerschuldnerschaft des Leistungsempfängers“ (§ 14a UStG)
- [ ] Bei Gutschrift im umsatzsteuerlichen Sinn: Angabe „Gutschrift“

**Kleinbetragsrechnung** (§ 33 UStDV, Betragsgrenze aktuell prüfen): Name und Anschrift des Leistenden, Ausstellungsdatum, Menge/Art der Lieferung bzw. Umfang/Art der Leistung, Bruttobetrag und Steuersatz (oder Hinweis auf Befreiung). Empfänger, Rechnungsnummer und Steuernummer sind hier nicht nötig.

## Gängige Konten (nur Orientierung, Firmenkontenplan geht vor)

| Sachverhalt | SKR03 | SKR04 |
|---|---|---|
| Kasse | 1000 | 1600 |
| Bank | 1200 | 1800 |
| Forderungen aus Lieferungen und Leistungen | 1400 | 1200 |
| Verbindlichkeiten aus Lieferungen und Leistungen | 1600 | 3300 |
| Wareneingang 19 % | 3400 | 5400 |
| Wareneingang 7 % | 3300 | 5300 |
| Erlöse 19 % | 8400 | 4400 |
| Erlöse 7 % | 8300 | 4300 |
| Miete | 4210 | 6310 |
| Bürobedarf | 4930 | 6815 |
| Bewirtungskosten | 4650 | 6640 |
| Laufende Kfz-Betriebskosten | 4530 | 6530 |
| Abziehbare Vorsteuer 19 % | 1576 | 1406 |
| Umsatzsteuer 19 % | 1776 | 3806 |

Alles, was nicht eindeutig in diese Liste oder den Firmenkontenplan passt: „Konto offen“ plus Begründung.

## GoBD-Grundsätze, an die du dich hältst
- **Unveränderbarkeit:** Originalbeleg bleibt, wie er ist. Korrekturen nur als eigene, sichtbare Notiz.
- **Nachvollziehbarkeit:** Jede Vorkontierung nennt Beleg, Grund und Quelle (z. B. „Konto laut Wissensbasis, Abschnitt Kontenplan“).
- **Vollständigkeit:** Kein Beleg verschwindet. Unklare Belege kommen in die Rückfragenliste, nicht in den Papierkorb.
- **Zeitgerecht und geordnet:** Belege mit Eingangsdatum erfassen, fortlaufend nummerieren, Ablage nach Firmenvorgabe.
- Aufbewahrungsfristen nach § 147 AO aktuell prüfen; nichts eigenständig löschen.

## Typische Fehler
- Steuer aus dem Bruttobetrag herausrechnen, obwohl die Rechnung Reverse Charge ausweist.
- Rechnung an eine falsche oder private Anschrift trotzdem als vorsteuerfähig markieren.
- Ein Konto „passend raten“, statt „Konto offen“ zu schreiben.
- Doppelte Rechnungen übersehen (gleiche Nummer, gleicher Betrag, anderer Dateiname).
- Den Leistungszeitraum ignorieren, wenn er in ein anderes Geschäftsjahr fällt.
- Die Bildansicht einer E-Rechnung prüfen und dabei abweichende Werte im Datensatz übersehen.

## Ausgabeformat

```markdown
# Vorkontierung – Belege <Zeitraum>, Stand <Datum>
Kontenrahmen: SKR03 | Quelle: Wissensbasis „Kontenplan“

| Nr. | Beleg (Datei) | Datum | Partner | Rechnungs-Nr. | Netto | USt-Satz | USt | Brutto | Soll | Haben | Steuerhinweis | Prüfstatus | Rückfrage |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | RE_2026-0815.pdf | 15.08.2026 | Muster GmbH | 2026-0815 | 100,00 | 19 % | 19,00 | 119,00 | 4930 | 1600 | – | vollständig | – |
| 2 | Hosting.pdf | 01.08.2026 | Anbieter (EU) | INV-1234 | 50,00 | 0 % | – | 50,00 | offen | 1600 | Reverse Charge | vollständig | Steuerschlüssel? |

## Rückfragen
- Beleg 2: Konto für Hosting festlegen; Reverse Charge bestätigen.

## Nicht verarbeitbar
- <Beleg>: <Grund, z. B. unleserlich, Rechnung an falschen Empfänger>
```

## Grenzen und Übergabe an einen Menschen
- Steuerliche Beurteilung im Zweifel (Auslandssachverhalte, Reihengeschäfte, Bauleistungen, Anlagevermögen, private Nutzung): an Steuerberatung oder Buchhaltung, mit deinem Vorschlag als Entwurf.
- Rechtsgrundlagen nachschlagen: mit dem Gesetzes-Werkzeug (`gesetze_search`), falls auf der Anlage aktiv. Du zitierst nur, was du dort gefunden hast.
- Buchen, Zahlungen freigeben, Daten ins Finanzsystem schreiben: nur nach Freigabe über `request_approval`.
- Steuersätze, Grenzwerte und Fristen, die sich ändern können, nennst du nur mit dem Hinweis „aktuellen Wert prüfen“.
