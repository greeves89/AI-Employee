---
name: rechtsfrage-mit-fundstelle
description: "Beantwortet Rechtsfragen nur mit belegter Fundstelle (Norm im Wortlaut, Stand, knappe Subsumtion) und zeigt, wann ein Anwalt nötig ist. Nutzen, wenn jemand fragt „Darf ich …?“, „Was sagt das Gesetz zu …?“ oder eine Vorschrift erklärt haben möchte."
---

# Rechtsfrage mit Fundstelle beantworten

Du beantwortest Rechtsfragen so, dass jede Aussage nachprüfbar ist: Norm, Wortlaut, Stand. Was du nicht belegen kannst, schreibst du nicht als Tatsache hin. Du gibst eine Orientierung, keine Rechtsberatung. Entscheiden und handeln muss ein Mensch, im Zweifel mit Anwalt.

## Wann nutzen
- Allgemeine Fragen zum deutschen Recht („Welche Kündigungsfrist gilt …?“, „Muss eine Rechnung … enthalten?“).
- Eine Vorschrift soll erklärt oder auf einen einfachen Sachverhalt angewendet werden.
- Vorbereitung eines Gesprächs mit Rechtsabteilung oder Anwalt (Fragen sortieren, Normen vorab heraussuchen).

Nicht nutzen für Vertragsprüfungen (dafür Skill `vertrag-pruefen`) und nicht für laufende Streitfälle, in denen jemand „gewinnen“ will — dort gleich an einen Anwalt verweisen.

## Vorgehen
1. **Frage präzisieren.** Kläre vor der Recherche, falls unklar:
   - Wer fragt (Unternehmen, Arbeitnehmer, Verbraucher)? Gegenüber wem?
   - Welches Rechtsgebiet, welches Land (Deutschland, Österreich, Schweiz)? Bundes- oder Landesrecht?
   - Welcher Zeitpunkt ist maßgeblich (heute oder ein früherer Stichtag)?
   - Gibt es Verträge, Tarifverträge, Betriebsvereinbarungen oder AGB, die vorgehen könnten?
   Fehlt etwas Wesentliches, frag nach, statt zu raten.
2. **Einschlägige Normen finden.**
   - Ist das Werkzeug `gesetze_search` auf dieser Anlage aktiv, nutze es zuerst (Suchbegriffe auf Deutsch, auch Synonyme und Fachbegriffe).
   - Sonst: gesetze-im-internet.de (Bundesrecht, amtliche Fassung des Bundesjustizministeriums). Landesrecht über die Rechtsportale der Länder, EU-Recht über EUR-Lex.
   - Prüfe auch Ausnahmen, Verweisungen und Sondervorschriften (z. B. „§ … gilt nicht, wenn …“, Spezialgesetz vor allgemeinem Gesetz).
3. **Wortlaut nachschlagen.** Lies die Norm vollständig im Original, nicht nur den Suchtreffer-Ausschnitt. Notiere den Stand der Fassung, wie die Quelle ihn ausweist (z. B. „zuletzt geändert durch …“).
4. **Subsumtion knapp.** Voraussetzungen der Norm einzeln aufzählen, jeweils prüfen: erfüllt / nicht erfüllt / unklar (welche Angabe fehlt). Keine langen Gutachten — drei bis acht Zeilen reichen meist.
5. **Rechtsprechung nur belegt.** Nenne ein Urteil nur, wenn du es in einer Quelle tatsächlich gefunden und geöffnet hast (Gericht, Datum, Aktenzeichen, Fundort). Nie ein Aktenzeichen, Datum oder Zitat aus dem Gedächtnis ergänzen. Ohne Fund schreib: „Zur Auslegung gibt es Rechtsprechung; die konkrete Linie sollte ein Anwalt prüfen.“
6. **Antwort schreiben** im Ausgabeformat unten.
7. **Ablegen.** Bei umfangreicheren Fragen die Antwort als Datei im Arbeitsordner speichern (z. B. `rechtsfragen/YYYY-MM-DD-thema.md`). Wiederkehrende Fundstellen darfst du in deiner Wissensbasis bzw. deinem Gedächtnis vermerken — mit Stand, damit du sie später erneut prüfst statt sie blind zu übernehmen.

## Wann ein Anwalt nötig ist (immer ausdrücklich sagen)
- **Fristen** laufen oder könnten laufen (Kündigung, Widerspruch, Klage, Verjährung, Einspruch).
- **Streit** besteht oder droht: Abmahnung, Mahnbescheid, Klage, Behördenschreiben, Anhörung.
- **Haftung**, Schadensersatz, Vertragsstrafe, Bußgeld oder Strafbarkeit stehen im Raum.
- Es geht um viel Geld, Arbeitsplätze, Gesellschaftsrecht oder Ausland.
- Die Norm ist auslegungsbedürftig und es kommt auf Rechtsprechung an.
- Die Frage betrifft eine konkrete fremde Angelegenheit im Einzelfall — Rechtsdienstleistungen sind nach dem Rechtsdienstleistungsgesetz (RDG) grundsätzlich Anwälten und anderen befugten Stellen vorbehalten.

Bei Fristen: Weise sofort und an erster Stelle darauf hin, dass gehandelt werden muss. Berechne keine Frist verbindlich — nenne die Norm, die sie regelt, und empfiehl die Prüfung durch einen Anwalt.

## Prüfliste vor dem Absenden
- [ ] Jede rechtliche Aussage hat eine Fundstelle (Gesetz, Paragraph, Absatz, Satz/Nummer).
- [ ] Der zitierte Wortlaut stimmt mit der Quelle überein (wörtlich, in Anführungszeichen).
- [ ] Der Stand der Fassung ist angegeben.
- [ ] Ausnahmen und vorrangige Regelungen (Vertrag, Tarifvertrag, Spezialgesetz) sind geprüft oder als offen benannt.
- [ ] Unsicherheiten sind als solche markiert („unklar, weil …“).
- [ ] Kein erfundenes Urteil, Aktenzeichen, Datum oder Zitat.
- [ ] Hinweis „keine Rechtsberatung“ und Anwalts-Hinweis bei Frist/Streit/Haftung sind enthalten.
- [ ] Bei DACH-Bezug: richtiges Land? Österreichisches und Schweizer Recht weichen ab — nicht deutsches Recht übertragen.

## Typische Fehler
- Paragraphen aus dem Gedächtnis zitieren, ohne nachzuschlagen — Nummerierungen ändern sich.
- Nur den Suchtreffer lesen und den nächsten Absatz mit der Ausnahme übersehen.
- Alte Fassungen verwenden (Stand nicht geprüft).
- „Das ist rechtlich eindeutig“ schreiben, wo es auf den Einzelfall ankommt.
- Einen Fall abschließend entscheiden („Du hast Anspruch auf …“) statt die Voraussetzungen zu zeigen.
- Deutsches Recht auf Sachverhalte in Österreich oder der Schweiz anwenden.

## Ausgabeformat

```markdown
**Kurzantwort:** <1–3 Sätze; mit „voraussichtlich“, „wenn …“ wo nötig>

**Fundstelle(n)**
| Norm | Wortlaut (Auszug) | Stand / Quelle |
|---|---|---|
| § … Abs. … <Gesetz> | „…“ | <Stand laut Quelle>, gesetze-im-internet.de |

**Prüfung**
1. <Voraussetzung 1> — erfüllt / nicht erfüllt / unklar (fehlt: …)
2. <Voraussetzung 2> — …

**Offene Punkte:** <welche Angaben fehlen, welche Regelungen vorgehen könnten>

**Wann zum Anwalt:** <konkret für diesen Fall, z. B. „sobald eine Frist läuft …“>

_Hinweis: Dies ist eine allgemeine Information auf Grundlage der genannten Vorschriften und keine Rechtsberatung. Für eine verbindliche Einschätzung des Einzelfalls wende dich an eine Rechtsanwältin oder einen Rechtsanwalt bzw. die Rechtsabteilung._
```

## Grenzen
- Du entscheidest keinen Rechtsfall und gibst keine verbindliche Auskunft.
- Du verschickst keine rechtlich wirksamen Erklärungen (Kündigung, Widerspruch, Mahnung) eigenmächtig. Soll so etwas raus, hol vorher über `request_approval` die Freigabe eines Menschen ein — und empfiehl die Prüfung durch einen Anwalt.
- Findest du keine passende Norm, sag das offen: „Ich habe keine einschlägige Vorschrift gefunden; bitte fachlich prüfen lassen.“
