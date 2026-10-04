---
name: datenanalyse-bericht
description: "Wertet Daten aus Tabellen, CSV oder Excel reproduzierbar aus (Python/pandas), prüft die Datenqualität, erstellt passende Diagramme und berichtet mit Einordnung und Grenzen – ohne Scheinpräzision. Nutzen, wenn Zahlen ausgewertet, verglichen oder als Bericht aufbereitet werden sollen. Auslöser: Datenanalyse, Auswertung, auswerten, Diagramm, Kennzahlen, Statistik, Excel-Tabelle, CSV-Datei."
---

# Datenanalyse mit Bericht

## Wann nutzen
- Es liegen Daten vor (CSV, Excel, Export aus einem System, Datenbankabfrage) und es soll eine Frage beantwortet werden: Entwicklung, Vergleich, Verteilung, Auffälligkeiten.
- Ein wiederkehrender Bericht (Monat, Quartal) soll erstellt oder automatisiert werden.

## Grundsatz
Eine Analyse beantwortet **eine klare Frage** mit **nachvollziehbarem Weg**. Jede Zahl im Bericht lässt sich aus dem Skript im Arbeitsordner reproduzieren. Grenzen der Daten stehen im Bericht, nicht im Kleingedruckten.

## Vorgehen
1. **Frage klären.** Was genau soll beantwortet werden, für wen, wozu? Zeitraum, Abgrenzung (welche Standorte, Produkte, Kunden), Begriffsdefinitionen („Umsatz“ netto oder brutto? „aktiver Kunde“ = ?). Unklares nachfragen, bevor du rechnest.
2. **Daten sichten.** Herkunft, Stand/Exportdatum, Zeilen- und Spaltenanzahl, Datentypen, Einheiten, Währung. Originaldatei nie verändern — mit einer Kopie oder eingelesenen Daten arbeiten.
3. **Datenqualität prüfen** und Befunde notieren:
   - Vollständigkeit: fehlende Werte je Spalte, fehlende Zeiträume
   - Dubletten: exakt und fachlich (gleiche Belegnummer zweimal)
   - Plausibilität: negative Mengen, Daten in der Zukunft, Ausreißer (z. B. Interquartilsabstand), falsche Einheiten
   - Formate: Dezimalkomma vs. -punkt, Datumsformate, Text in Zahlenspalten, Leerzeichen
4. **Bereinigen — dokumentiert.** Jede Bereinigung im Skript mit Kommentar und Anzahl betroffener Zeilen. Ausreißer nicht stillschweigend löschen: prüfen, ob Fehler oder echter Wert; im Zweifel beide Varianten rechnen.
5. **Analyse als Skript.** Python mit pandas (für Excel `openpyxl`) als Datei im Arbeitsordner, z. B. `analyse/<thema>/analyse.py`. Eingaben, Bereinigung, Berechnung, Diagramme, Export — in dieser Reihenfolge. Feste Pfade und Parameter oben im Skript. Ist Python nicht verfügbar, Tabellenformeln nutzen und die Rechenschritte im Bericht beschreiben.
6. **Gegenprüfen.** Summen gegen Quelle (Gesamtsumme vor/nach Bereinigung), Stichproben einzelner Werte von Hand, Zeilenanzahl nach jedem Join prüfen (Verdopplung durch Join ist ein Klassiker).
7. **Diagramme wählen** (siehe unten), mit Titel, Achsenbeschriftung, Einheit, Quelle und Stand. Als PNG im Arbeitsordner speichern.
8. **Einordnen.** Was bedeuten die Zahlen für die Frage? Vergleich mit Vorperiode/Ziel, falls vorhanden. Korrelation nicht als Ursache darstellen.
9. **Grenzen benennen.** Datenlücken, Bereinigungen, kleine Fallzahlen, Annahmen, was die Daten nicht zeigen können.
10. **Bericht erstellen** als Markdown (Gerüst unten) und auf Wunsch als Excel (Rohdaten bereinigt, Auswertungstabellen, je ein Blatt). Mit `present_file` bereitstellen. Ergebnis und Definitionen bei wiederkehrenden Berichten in Gedächtnis/Wissensbasis festhalten.

## Diagrammwahl
| Frage | Diagramm |
|---|---|
| Entwicklung über Zeit | Liniendiagramm |
| Vergleich weniger Kategorien | Balkendiagramm (horizontal bei langen Namen), sortiert |
| Anteile (wenige Teile) | gestapelter Balken; Kreisdiagramm nur bei 2–4 Teilen |
| Verteilung | Histogramm oder Boxplot |
| Zusammenhang zweier Größen | Streudiagramm |
Balkenachsen beginnen bei null. Keine 3D-Effekte, keine doppelten y-Achsen ohne Not.

## Keine Scheinpräzision
- Runden auf eine sinnvolle Stelle: „rund 12 %“ statt „12,3471 %“; Beträge passend zur Größenordnung (Tsd. €).
- Prozentangaben immer mit Basis („12 % von 230 Aufträgen“). Bei kleinen Fallzahlen absolute Zahlen nennen.
- Prozentpunkte und Prozent unterscheiden.
- Prognosen und Hochrechnungen als solche kennzeichnen, mit Annahmen.

## Prüfliste
- [ ] Frage und Begriffe sind definiert und stehen im Bericht.
- [ ] Datenquelle, Stand und Zeilenanzahl sind genannt.
- [ ] Qualitätsbefunde und Bereinigungen sind dokumentiert (mit Anzahl).
- [ ] Skript läuft vollständig durch und erzeugt alle Zahlen und Diagramme.
- [ ] Summen und Stichproben gegen die Quelle geprüft.
- [ ] Diagramme beschriftet, Einheiten und Quelle angegeben.
- [ ] Grenzen der Daten sind benannt.
- [ ] Keine personenbezogenen Einzeldaten im Bericht, wo eine Aggregation genügt.

## Typische Fehler
- Ohne klare Frage „mal alles auswerten“.
- Dubletten oder Join-Verdopplungen übersehen.
- Mittelwert bei schiefen Verteilungen — Median dazu angeben.
- Fehlende Werte als null zählen.
- Ursache behaupten, wo nur ein Zusammenhang besteht.
- Bericht ohne Stand und Quelle.

## Berichtsgerüst
```markdown
# Auswertung: <Frage>
Stand der Daten: TT.MM.JJJJ · Quelle: <System/Datei> · Zeitraum: <…> · Erstellt: TT.MM.JJJJ

## Ergebnis in Kürze
- <Kernaussage mit gerundeter Zahl und Basis>
- <…>

## Kennzahlen
| Kennzahl | Wert | Vorperiode | Veränderung | Anmerkung |
|---|---|---|---|---|

## Details
![<Beschreibung>](diagramme/<name>.png)
<Einordnung in 2–4 Sätzen>

## Datenqualität und Bereinigung
| Befund | Betroffene Zeilen | Behandlung |
|---|---|---|

## Grenzen
- <…>

## Definitionen und Reproduktion
- <Begriff>: <Definition>
- Skript: `analyse/<thema>/analyse.py` · Eingabedatei: `<…>`
```

## Grenzen und Übergabe an Menschen
- Personenbezogene Daten (Mitarbeitende, Kunden) nur im nötigen Umfang verarbeiten, im Bericht aggregieren; Auswertungen über Leistung oder Verhalten einzelner Mitarbeitender nicht ohne ausdrückliche Klärung mit den Verantwortlichen (Datenschutz, ggf. Betriebsrat).
- Zahlen für Jahresabschluss, Steuer, Controlling-Berichte an Geschäftsführung oder Banken: als Vorbereitung kennzeichnen, Freigabe durch die verantwortliche Person (`request_approval`).
- Statistische Verfahren jenseits beschreibender Auswertung (Signifikanztests, Modelle) nur mit genannten Voraussetzungen; bei weitreichenden Entscheidungen fachliche Prüfung empfehlen.
- Sind die Daten für die Frage ungeeignet, sag das — statt eine Antwort zu konstruieren.
