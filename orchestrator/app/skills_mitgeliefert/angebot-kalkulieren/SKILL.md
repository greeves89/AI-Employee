---
name: angebot-kalkulieren
description: "Erstellt aus Anfrage oder Aufmaß ein strukturiertes Leistungsverzeichnis mit Kalkulation (Material, Lohn, Zuschläge nach Firmenwerten) und ein Angebotsgerüst. Nutzen, wenn ein Angebot, ein Nachtrag oder eine Preiskalkulation für Handwerks- oder Bauleistungen vorbereitet werden soll."
---

# Angebot kalkulieren

Du machst aus einer Anfrage oder einem Aufmaß ein prüfbares Angebot. Preise, Zuschläge und Vertragsbedingungen kommen aus den Firmenwerten, nicht von dir. Versendet wird erst nach Freigabe durch einen Menschen.

## Wann nutzen
- Eine Kundenanfrage, ein Aufmaß, ein Leistungsverzeichnis oder Planunterlagen liegen vor und sollen zu einem Angebot werden.
- Ein Nachtrag zu einem laufenden Auftrag soll kalkuliert werden.
- Ein bestehendes Angebot soll überprüft oder angepasst werden.

## Vorgehen
1. **Firmenwerte laden.** In Wissensbasis und Gedächtnis (`memory_search`) suchen: Stundensätze bzw. Mittellohn, Zuschlagssätze (Gemeinkosten, Wagnis, Gewinn), Materialpreislisten oder Lieferantenkonditionen, Standardtexte, Zahlungs- und Gewährleistungsbedingungen, Bindefrist. **Fehlt ein Wert, setzt du keinen eigenen ein**, sondern trägst „Firmenwert fehlt“ ein und fragst nach.
2. **Anfrage verstehen.** Was genau soll geleistet werden, wo, bis wann, durch wen beigestellt? Unklarheiten sofort als Rückfrageliste notieren.
3. **Vertragsgrundlage prüfen.** Nur feststellen, was vereinbart ist oder werden soll (VOB/B einbezogen oder BGB-Werkvertrag, Verbraucher oder Unternehmer). Du beurteilst das nicht rechtlich, sondern weist darauf hin, was zu prüfen ist.
4. **Positionen strukturieren.** Leistungsverzeichnis mit Ordnungszahl (Titel, Position), Kurztext, Langtext, Menge, Einheit. Ist ein LV vorgegeben (z. B. GAEB-Export des Auftraggebers), Struktur und Texte unverändert übernehmen.
5. **Mengen übernehmen und plausibilisieren** (Prüfliste unten). Rechenansatz je Menge nachvollziehbar notieren.
6. **Kalkulieren** je Position: Material (Menge × Preis, Verschnitt nur nach Firmenwert), Lohn (Zeitansatz × Stundensatz aus der Wissensbasis), Geräte und Fremdleistungen, darauf die Zuschläge nach Firmenwerten. Zeitansätze, die du schätzt, kennzeichnest du als Schätzung.
7. **Nachträge trennen.** Leistungen außerhalb des ursprünglichen Auftrags in eigenen Nachtragspositionen mit eigener Nummer und Begründung (geänderte oder zusätzliche Leistung, Anlass, Datum der Anordnung). Nie in Hauptpositionen einrechnen.
8. **Angebotsgerüst bauen** (Ausgabeformat unten) als Datei im Arbeitsordner.
9. **Freigabe einholen** über `request_approval`, mit Summen, offenen Punkten und den markierten Schätzungen. Erst danach versenden oder in ein anderes System übertragen.

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

## Ausgabeformat

```markdown
# Angebot <Nr.> – <Bauvorhaben>, Entwurf vom <Datum>
Kunde: Muster GmbH, <Anschrift> | Grundlage: Anfrage vom <Datum>, Aufmaß vom <Datum>
Vertragsgrundlage: <laut Vereinbarung prüfen: VOB/B oder BGB-Werkvertrag>

| OZ | Kurztext | Menge | Einheit | Material EP | Lohn EP | EP netto | GP netto | Hinweis |
|---|---|---|---|---|---|---|---|---|
| 01 | Titel: Vorarbeiten | | | | | | | |
| 01.01 | Untergrund vorbereiten | 42,50 | m² | … | … | … | … | Menge aus Aufmaß Raum 1–3 |
| 01.02 | Eventualposition … | 1 | psch | … | … | … | (nicht in Summe) | Eventualposition |

| Summe | Betrag |
|---|---|
| Summe netto | … |
| Umsatzsteuer 19 % | … |
| Summe brutto | … |

## Nachträge (getrennt vom Hauptauftrag)
| N-Nr. | Bezug | Anlass/Anordnung | Kurztext | Menge | Einheit | EP netto | GP netto |
|---|---|---|---|---|---|---|---|

## Offene Punkte und Annahmen
- <fehlender Firmenwert / geschätzter Zeitansatz / Rückfrage an den Kunden>

## Bedingungen
Bindefrist, Zahlungsbedingungen, Ausschlüsse: <laut Firmenvorgabe>
```

## Grenzen und Übergabe an einen Menschen
- Preise, Nachlässe, Vertragsbedingungen und Versand entscheidet die Geschäftsführung oder Kalkulation; du holst die Freigabe über `request_approval` ein.
- Rechtsfragen (VOB/B, Bauvertragsrecht, Gewährleistung, Vertragsstrafen): als Prüfpunkt markieren; Normtexte nur über das Gesetzes-Werkzeug (`gesetze_search`), falls aktiv, und nicht selbst auslegen.
- Statik, Brandschutz, technische Normen und Zulassungen: an die zuständige Fachperson.
- In Österreich und der Schweiz gelten andere Vertragsnormen (z. B. ÖNORM, SIA): dort nur strukturieren und die Vertragsgrundlage prüfen lassen.
