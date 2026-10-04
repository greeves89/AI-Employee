---
name: angebot-kalkulieren
description: "Erstellt aus Anfrage oder Aufmaß ein strukturiertes Leistungsverzeichnis mit Kalkulation (Material, Lohn, Zuschläge nach Firmenwerten) und daraus zwei Dateien: das Kundenangebot als PDF mit echten Firmendaten und die interne Kalkulation. Nutzen, wenn ein Angebot, ein Nachtrag oder eine Preiskalkulation für Handwerks- oder Bauleistungen vorbereitet werden soll."
---

# Angebot kalkulieren

Du machst aus einer Anfrage oder einem Aufmaß ein prüfbares Angebot. Preise, Zuschläge, Vertragsbedingungen und der Absender kommen aus den Firmenwerten, nicht von dir. Versendet wird erst nach Freigabe durch einen Menschen.

## Wann nutzen
- Eine Kundenanfrage, ein Aufmaß, ein Leistungsverzeichnis oder Planunterlagen liegen vor und sollen zu einem Angebot werden.
- Ein Nachtrag zu einem laufenden Auftrag soll kalkuliert werden.
- Ein bestehendes Angebot soll überprüft oder angepasst werden.

## Firmenstammdaten (Absender) — zuerst, und nie erfunden
Ein Angebot ist ein Geschäftsbrief. Der Absender steht in der Wissensbasis im Eintrag **„Firmenstammdaten“** (`brain_search` nach „Firmenstammdaten“; ist die Wissensbasis nicht angebunden: `memory_search`).

Fehlt der Eintrag oder fehlt darin etwas, **frag einmal gebündelt** nach allem, was fehlt, und erstelle bis zur Antwort kein Kundendokument:
- Firma mit Rechtsform, Anschrift, Telefon, E-Mail, Webseite
- Pflichtangaben im Geschäftsbrief je nach Rechtsform: Sitz, Registergericht und Registernummer; bei GmbH und UG alle Geschäftsführer; bei nicht eingetragenen Einzelunternehmen Vor- und Nachname des Inhabers
- Steuernummer oder Umsatzsteuer-Identifikationsnummer, Bankverbindung (IBAN, BIC)
- Ansprechpartner für das Angebot, Logo (Datei), falls gewünscht

Die Antwort legst du als Eintrag „Firmenstammdaten“ in der Wissensbasis ab (`brain_contribute`, sonst `memory_save` mit dem Schlüssel `firmenstammdaten`), damit nie wieder gefragt werden muss.

**Kundendaten** (Name, Anschrift, Ansprechpartner) kommen ausschließlich aus der Anfrage oder vom Nutzer. Fehlen sie, fragst du nach. Platzhalter wie „Muster GmbH“, „Max Mustermann“ oder „Musterstraße“ stehen in keinem Dokument, das du erzeugst.

## Vorgehen
1. **Firmenwerte laden.** In Wissensbasis und Gedächtnis (`memory_search`) suchen: Stundensätze bzw. Mittellohn, Zuschlagssätze (Gemeinkosten, Wagnis, Gewinn), Materialpreislisten oder Lieferantenkonditionen, Standardtexte, Zahlungs- und Gewährleistungsbedingungen, Bindefrist. **Fehlt ein Wert, setzt du keinen eigenen ein**, sondern trägst „Firmenwert fehlt“ ein und fragst nach.
2. **Anfrage verstehen.** Was genau soll geleistet werden, wo, bis wann, durch wen beigestellt? Unklarheiten sofort als Rückfrageliste notieren.
3. **Vertragsgrundlage prüfen.** Nur feststellen, was vereinbart ist oder werden soll (VOB/B einbezogen oder BGB-Werkvertrag, Verbraucher oder Unternehmer). Du beurteilst das nicht rechtlich, sondern weist darauf hin, was zu prüfen ist.
4. **Positionen strukturieren.** Leistungsverzeichnis mit Ordnungszahl (Titel, Position), Kurztext, Langtext, Menge, Einheit. Ist ein LV vorgegeben (z. B. GAEB-Export des Auftraggebers), Struktur und Texte unverändert übernehmen.
5. **Mengen übernehmen und plausibilisieren** (Prüfliste unten). Rechenansatz je Menge nachvollziehbar notieren.
6. **Kalkulieren** je Position: Material (Menge × Preis, Verschnitt nur nach Firmenwert), Lohn (Zeitansatz × Stundensatz aus der Wissensbasis), Geräte und Fremdleistungen, darauf die Zuschläge nach Firmenwerten. Zeitansätze, die du schätzt, kennzeichnest du als Schätzung.
7. **Nachträge trennen.** Leistungen außerhalb des ursprünglichen Auftrags in eigenen Nachtragspositionen mit eigener Nummer und Begründung (geänderte oder zusätzliche Leistung, Anlass, Datum der Anordnung). Nie in Hauptpositionen einrechnen.
8. **Zwei Dateien erzeugen** (Ausgabeformat unten): das **Kundenangebot** ohne jede interne Angabe und die **interne Kalkulation** mit allem, was nur ihr braucht. Das Kundenangebot als PDF ausschließlich mit `dokument pdf angebot-<Nr>.md -o /workspace/transfer/angebot-<Nr>.pdf --fusszeile "<Firma>"`, danach `dokument pruefen` auf das PDF. Nie per Browser drucken.
9. **Freigabe einholen** über `request_approval`, mit Summen, offenen Punkten und den markierten Schätzungen aus der internen Kalkulation. Erst danach versenden oder in ein anderes System übertragen. Dem Nutzer zeigst du beide Dateien mit `present_file`.

## Prüfliste: Mengen und Positionen
- [ ] Jede Position hat Menge und Einheit; Einheiten passen zur Leistung (m, m², m³, Stk, h, psch).
- [ ] Mengen sind aus dem Aufmaß nachvollziehbar (Rechenansatz, Plan, Raum).
- [ ] Abzüge (Öffnungen, Aussparungen) nach den vereinbarten Abrechnungsregeln berücksichtigt; welche gelten, prüfen.
- [ ] Größenordnungen plausibel (Fläche passt zur Raumgröße, Stückzahl zum Plan).
- [ ] Keine Doppelungen zwischen Positionen (z. B. Material in Position und Pauschale).
- [ ] Beistellungen des Kunden und Leistungen anderer Gewerke klar ausgeschlossen.
- [ ] Eventual- und Alternativpositionen gekennzeichnet und nicht in der Gesamtsumme.

## Kalkulationsschema (Werte nur aus der Wissensbasis)

| Baustein | Herkunft |
|---|---|
| Material | Preisliste/Angebot des Lieferanten, Datum angeben |
| Lohn | Zeitansatz × Stundensatz bzw. Mittellohn der Firma |
| Geräte, Fremdleistungen | Firmenwerte oder Nachunternehmerangebot |
| = Einzelkosten | Summe |
| + Gemeinkostenzuschlag | Firmenwert |
| + Wagnis und Gewinn | Firmenwert |
| = Einheitspreis netto | je Einheit gerundet nach Firmenvorgabe |

## Hinweise, die du je nach Fall mitgibst
- **Umsatzsteuer:** Netto, 19 % Umsatzsteuer, Brutto. Bei Bauleistungen an Unternehmen, die selbst Bauleistungen erbringen, kann Reverse Charge (§ 13b UStG) greifen: als Prüfpunkt markieren.
- **Privatkunden:** Arbeitskosten getrennt von Materialkosten ausweisen (Steuerermäßigung für Handwerkerleistungen, § 35a EStG).
- **Vertragsart:** „Prüfen, ob VOB/B wirksam vereinbart ist oder BGB-Werkvertragsrecht gilt“; bei Verbrauchern zusätzlich Widerrufsrecht und Bauvertragsregeln prüfen lassen.
- **Bindefrist, Zahlungsplan, Abschläge:** nur nach Firmenvorgabe.

## Typische Fehler
- Zuschläge oder Stundensätze „branchenüblich“ schätzen. Ohne Firmenwert bleibt das Feld offen.
- Mengen ungeprüft aus dem Aufmaß übernehmen, obwohl Einheiten oder Rechenansatz nicht stimmen.
- Nachtragsleistungen still in Hauptpositionen verstecken.
- Eventualpositionen in die Angebotssumme rechnen.
- Materialpreise ohne Datum oder Quelle übernehmen.
- Das Angebot ohne Freigabe verschicken.
- Absender oder Kunden erfinden („Muster GmbH“) statt nach den Firmenstammdaten bzw. Kundendaten zu fragen.
- Interne Hinweise, offene Punkte oder Einkaufspreise im Kundenangebot stehen lassen.
- Das PDF per Browser drucken (Datum und Dateipfad am Seitenrand) statt mit `dokument pdf`.

## Ausgabeformat: zwei Dateien

### 1. Kundenangebot (`angebot-<Nr>.md`, daraus das PDF)
Nur, was der Kunde lesen soll: keine Hinweisspalte, keine offenen Punkte, keine Schätzungsvermerke, keine Zuschlagssätze, keine Einkaufspreise.

```markdown
**<Firma mit Rechtsform>** · <Straße Nr.> · <PLZ Ort>

<Kunde laut Anfrage>
<Anschrift laut Anfrage>

# Angebot <Nr.> – <Bauvorhaben>
Datum: <TT.MM.JJJJ> · Ihre Anfrage vom <TT.MM.JJJJ> · Ansprechpartner: <aus Firmenstammdaten>

Sehr geehrte …, vielen Dank für Ihre Anfrage. Wir bieten Ihnen an:

| Pos. | Leistung | Menge | Einheit | Einzelpreis netto | Gesamt netto |
|---|---|---|---|---|---|
| 01.01 | Untergrund vorbereiten | 42,50 | m² | … € | … € |

Eventualpositionen (nur auf Abruf, nicht in der Summe):
| Pos. | Leistung | Menge | Einheit | Einzelpreis netto |
|---|---|---|---|---|

| | Betrag |
|---|---|
| Summe netto | … € |
| Umsatzsteuer 19 % | … € |
| **Summe brutto** | … € |

Bei Privatkunden zusätzlich: darin enthaltene Arbeitskosten … € (für § 35a EStG).

Bindefrist, Zahlungsbedingungen, Ausführungszeitraum, Ausschlüsse: <laut Firmenstammdaten>

---
<Firma> · Sitz <Ort> · <Registergericht, Registernummer> · Geschäftsführung: <Namen>
Steuernummer/USt-IdNr.: <…> · <Bank, IBAN, BIC>
```

### 2. Interne Kalkulation (`/workspace/angebote/kalkulation-<Nr>.xlsx` oder `.md`)
Geht nicht an den Kunden.

| OZ | Kurztext | Menge | Einheit | Rechenansatz | Material EP | Zeitansatz | Lohn EP | Geräte/Fremd | Zuschläge | EP netto | GP netto | Herkunft der Werte | Hinweis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|

Dazu: Nachträge (N-Nr., Bezug, Anlass/Anordnung), offene Punkte und Annahmen, geschätzte Zeitansätze, fehlende Firmenwerte, Prüfpunkte (Vertragsgrundlage, § 13b UStG).

## Grenzen und Übergabe an einen Menschen
- Preise, Nachlässe, Vertragsbedingungen und Versand entscheidet die Geschäftsführung oder Kalkulation; du holst die Freigabe über `request_approval` ein.
- Rechtsfragen (VOB/B, Bauvertragsrecht, Gewährleistung, Vertragsstrafen): als Prüfpunkt markieren; Normtexte nur über das Gesetzes-Werkzeug (`gesetze_search`), falls aktiv, und nicht selbst auslegen.
- Statik, Brandschutz, technische Normen und Zulassungen: an die zuständige Fachperson.
- In Österreich und der Schweiz gelten andere Vertragsnormen (z. B. ÖNORM, SIA): dort nur strukturieren und die Vertragsgrundlage prüfen lassen.
