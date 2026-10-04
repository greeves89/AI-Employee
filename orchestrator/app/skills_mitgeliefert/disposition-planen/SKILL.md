---
name: disposition-planen
description: "Plant Einsätze von Monteuren und Technikern: Aufträge sammeln, Qualifikation, Fahrzeit und Material abgleichen, Wochenplan erstellen, Konflikte melden und Kunden über Termine informieren. Nutzen, wenn Einsätze disponiert, ein Wochenplan erstellt oder bei Ausfällen umgeplant werden soll. Auslöser: Disposition, disponieren, Einsatzplanung, Einsatzplan, Wochenplan, Monteur, Techniker, umplanen."
---

# Einsätze disponieren

Du erstellst einen belastbaren Einsatzplan und machst Engpässe früh sichtbar. Den Plan gibt die Disposition oder Bereichsleitung frei; Kunden werden erst nach Freigabe benachrichtigt.

## Wann nutzen
- Wochen- oder Tagesplanung für Monteure, Servicetechniker oder Teams.
- Neue Aufträge oder Notfälle müssen eingeplant werden.
- Ein Mitarbeiter fällt aus, ein Fahrzeug ist defekt oder Material kommt zu spät: umplanen.
- Kunden sollen über Termine, Verschiebungen oder Ankunftszeiten informiert werden.

## Vorgehen
1. **Grundlagen laden.** In Wissensbasis und Gedächtnis (`memory_search`) nachsehen: Mitarbeiter mit Qualifikationen und Nachweisen, Arbeitszeitmodelle, Fahrzeuge, Lager und Standorte, Prioritätsregeln (Notdienst, Wartungsverträge, Fristen), Vorlagen für Kundennachrichten. Fehlendes einmal erfragen und im Gedächtnis ablegen (keine sensiblen Personaldaten).
2. **Aufträge sammeln.** Offene Aufträge mit Ort, gewünschtem Zeitfenster, geschätzter Dauer, benötigter Qualifikation, Material und Priorität. Fehlt die Dauer, nach Erfahrungswerten der Firma fragen, nicht raten.
3. **Verfügbarkeit prüfen.** Urlaub, Krankheit, Schulungen, Bereitschaft, Teilzeit. Kalender über die Microsoft-365-Werkzeuge, falls verbunden; sonst die Quelle der Firma.
4. **Abgleichen** je Auftrag:
   - **Qualifikation:** Hat die eingeplante Person die nötige Ausbildung, Berechtigung oder Unterweisung? Ablaufende Nachweise markieren.
   - **Fahrzeit:** Aufträge nach Region bündeln, Fahrzeiten realistisch einplanen, Rückweg zum Lager mitdenken.
   - **Material und Fahrzeug:** Ist das Material da oder bestellt, passt es ins Fahrzeug, wer holt es ab?
   - **Kundenfenster:** Zugang, Ansprechpartner vor Ort, Sperrzeiten.
5. **Wochenplan erstellen** (Ausgabeformat unten), als Datei im Arbeitsordner.
6. **Konflikte und Engpässe melden.** Doppelbelegung, fehlende Qualifikation, Überlastung einzelner Personen, Material fehlt, Terminwunsch nicht erfüllbar. Je Konflikt einen Lösungsvorschlag.
7. **Arbeitszeit beachten.** Hinweis an die Disposition: Höchstarbeitszeit und Ruhezeiten beachten (ArbZG). Lange Tage mit Anfahrt, Bereitschaft oder Notdienst ausdrücklich markieren. Du prüfst keine Grenzwerte, du machst Auffälligkeiten sichtbar.
8. **Freigabe einholen** über `request_approval`: Plan, Konflikte, vorgeschlagene Lösungen.
9. **Kunden informieren** nach Freigabe, mit den Vorlagen unten. Versand nur über den freigegebenen Kanal.

## Umplanung bei Ausfall
1. Betroffene Einsätze auflisten, nach Priorität sortieren (Notdienst, vertragliche Fristen, Kundenzusagen zuerst).
2. Ersatz suchen: gleiche Qualifikation, geringste Mehrfahrt, freie Kapazität.
3. Was nicht ersetzbar ist: Verschiebungsvorschlag mit neuem Termin.
4. Freigabe einholen, dann Monteure und Kunden informieren.
5. Gründe für den Ausfall (z. B. Krankheit) nie an Kunden oder Kollegen weitergeben.

## Prüfliste vor der Freigabe
- [ ] Jeder Auftrag hat genau eine verantwortliche Person (bei Teams: Teamleitung).
- [ ] Qualifikationen passen; ablaufende Nachweise markiert.
- [ ] Keine Doppelbelegung von Personen oder Fahrzeugen.
- [ ] Fahrzeiten eingeplant, Tagesumfang realistisch.
- [ ] Material für jeden Einsatz verfügbar oder Liefertermin vor Einsatz.
- [ ] Puffer für Notfälle nach Firmenvorgabe.
- [ ] Lange Arbeitstage und Bereitschaften markiert.

## Typische Fehler
- Einsätze ohne Fahrzeit dicht hintereinander legen.
- Einen Auftrag einplanen, ohne zu prüfen, ob das Material da ist.
- Kunden einen Termin zusagen, bevor der Plan freigegeben ist.
- Krankheits- oder Urlaubsgründe in Kundennachrichten nennen.
- Immer dieselben Personen für Notfälle einplanen, bis sie überlastet sind.

## Ausgabeformat

```markdown
# Einsatzplan KW <Nr.>, Stand <Datum> – Entwurf
Freigabe: <offen / erteilt am … durch …>

| Tag | Zeit | Mitarbeiter | Auftrag | Kunde/Ort | Tätigkeit | Qualifikation ok | Material | Fahrzeug | Hinweis |
|---|---|---|---|---|---|---|---|---|---|
| Mo | 08:00–11:00 | Monteur A | A-1001 | Muster GmbH, PLZ-Gebiet 1 | Wartung | ja | vorhanden | Fzg 1 | – |
| Mo | 12:00–16:00 | Monteur A | A-1002 | Muster GmbH, PLZ-Gebiet 1 | Reparatur | ja | Lieferung Di | Fzg 1 | Material fehlt |

## Konflikte und Engpässe
| Auftrag | Problem | Vorschlag |
|---|---|---|

## Nicht eingeplant
| Auftrag | Grund | Nächster möglicher Termin |
|---|---|---|
```

## Vorlagen für Kundennachrichten

**Terminbestätigung**
> Guten Tag, wir bestätigen Ihren Termin am <Datum> zwischen <Uhrzeit> und <Uhrzeit> in <Ort>. Unser Techniker meldet sich kurz vor Ankunft. Bitte stellen Sie den Zugang zu <Anlage/Raum> sicher. Bei Fragen erreichen Sie uns unter <Kontakt>.

**Terminverschiebung**
> Guten Tag, leider müssen wir Ihren Termin am <Datum> verschieben. Wir schlagen Ihnen <neuer Termin> vor. Passt Ihnen das nicht, nennen Sie uns gern einen Wunschtermin. Wir bitten um Entschuldigung.

**Ankunft**
> Guten Tag, unser Techniker ist auf dem Weg und voraussichtlich gegen <Uhrzeit> bei Ihnen.

Nachrichten nach den Firmenvorgaben anpassen (Anrede, Signatur, Kanal). Keine internen Gründe und keine Personaldaten nennen.

## Grenzen und Übergabe an einen Menschen
- Freigabe des Plans, Zusagen an Kunden und Überstundenanordnungen: Disposition oder Bereichsleitung.
- Arbeitszeitrechtliche Fragen, Bereitschaftsregelungen und Mitbestimmung: an Personal bzw. Betriebsrat; Normtexte nur über das Gesetzes-Werkzeug (`gesetze_search`), falls aktiv.
- Sicherheitsfragen (Gefährdungsbeurteilung, Unterweisungen, Alleinarbeit): an die Fachkraft für Arbeitssicherheit.
