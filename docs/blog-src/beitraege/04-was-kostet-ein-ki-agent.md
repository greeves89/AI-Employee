---
slug: was-kostet-ein-ki-agent
title: Was kostet ein KI-Agent? Die drei Kostenblöcke
description: Was kostet ein KI-Agent im Monat? Die Rechnung hat drei Teile: Plattform, Sprachmodell und Betrieb. So schätzt du sie vor dem Start realistisch ab.
keyword: Was kostet ein KI-Agent
cover: was-kostet-ein-ki-agent.png
rubrik: Kosten
symbol: coins
farbe: amber
tags: Kosten, KI-Agenten, Lizenz
faq:
- Gibt es KI-Agenten kostenlos? | Für die private Nutzung ja: AI Employee ist dafür kostenlos, du zahlst nur dein Sprachmodell. Im Unternehmen brauchst du eine Lizenz, kannst aber 30 Tage kostenlos testen.
- Warum sind die Modellkosten nicht im Preis enthalten? | Weil sie vom Verbrauch abhängen und stark schwanken. Mit eigenem Modellzugang zahlst du den Preis des Anbieters ohne Aufschlag und siehst jede Ausgabe je Agent.
- Wird es mit mehr Mitarbeitern teurer? | Bei einer Lizenz je Anlage nicht. Der Preis hängt an der Zahl der Agenten, nicht an der Zahl der Menschen, die mit ihnen arbeiten.
- Wie verhindere ich, dass ein Agent zu viel ausgibt? | Mit einem Budget je Agent. Ist es aufgebraucht, hält der Agent an und meldet sich, statt weiterzuarbeiten.
---
Was kostet ein KI-Agent? Die ehrliche Antwort hat drei Teile: die Plattform, auf der er läuft, das Sprachmodell, das er benutzt, und der Betrieb. Wer nur den ersten Teil vergleicht, vergleicht das Falsche, denn bei aktiven Agenten ist das Sprachmodell oft der größte Posten.

Dieser Beitrag zeigt, wie sich die drei Blöcke zusammensetzen und woran du erkennst, welches Preismodell zu deiner Größe passt.

## Was kostet ein KI-Agent im Monat? Die drei Blöcke

![Die drei Kostenblöcke eines KI-Agenten: Plattform, Sprachmodell und Betrieb](/blog/media/grafik-kostenbloecke.png)

### Block 1: Die Plattform

Die Plattform liefert Oberfläche, Werkzeuge, Gedächtnis, Freigaben und Protokoll. Am Markt gibt es dafür zwei Preismodelle.

- **Preis je Nutzer.** Jeder Mensch, der die Plattform nutzt, kostet einen festen Betrag im Monat. Das ist einfach zu verstehen und bei wenigen Nutzern günstig. Es wird teuer, wenn viele Menschen selten etwas brauchen.
- **Preis je Anlage.** Du zahlst für die Installation und eine Zahl von Agenten. Wie viele Menschen damit arbeiten, spielt keine Rolle.

Bei AI Employee ist es der zweite Weg. Die Listenpreise, netto, bei jährlicher Laufzeit:

| Edition | Preis im Monat | Agenten |
|---|---|---|
| Starter | 149 € | 3 |
| Team | 390 € | 10 |
| Business | 990 € | 30 |
| Enterprise | ab 2.490 € | 100 |

Private Nutzung ist kostenlos. Unternehmen testen 30 Tage ohne Lizenz.

### Block 2: Das Sprachmodell

Jeder Schritt eines Agenten ist eine Anfrage an ein Sprachmodell. Abgerechnet wird nach Verbrauch, gemessen in Tokens, also Textbausteinen. Drei Dinge treiben den Verbrauch.

- **Die Aufgabe.** Eine Mail zu beantworten kostet einen Bruchteil dessen, was ein Agent verbraucht, der eine Stunde lang ein Programm baut und testet.
- **Das Modell.** Zwischen dem kleinsten und dem größten Modell eines Anbieters liegt ein Vielfaches im Preis. Nicht jede Aufgabe braucht das größte.
- **Die Häufigkeit.** Ein Agent, der alle fünf Minuten das Postfach prüft, verbraucht mehr als einer, der es zweimal am Tag tut.

Eine pauschale Zahl wäre hier unseriös. Die einzig verlässliche Methode ist messen: Lass einen Agenten zwei Wochen mit echter Arbeit laufen und lies die Kosten je Aufgabe ab. Eine gute Plattform zeigt dir das je Agent und je Auftrag.

Manche Anbieter rechnen das Modell in den Preis je Nutzer ein. Das ist bequem, hat aber eine Grenze: Hinter „inklusive“ steht fast immer eine Regel zur angemessenen Nutzung. Für einen Chat reicht das. Für Agenten, die stundenlang selbstständig arbeiten, lohnt sich ein Blick in genau diese Regel.

### Block 3: Der Betrieb

Wer selbst betreibt, braucht einen Server und jemanden, der sich kümmert.

- **Server.** Für den Anfang reicht kleine Hardware. Unsere eigene Anlage läuft auf einem Raspberry Pi 5. Für ein Team ist ein gewöhnlicher Server mit 32 GB Arbeitsspeicher die richtige Größe. Eine Grafikkarte brauchst du nur für lokale Modelle.
- **Pflege.** Updates einspielen, Sicherungen prüfen, gelegentlich einen Agenten neu aufsetzen. Das ist keine Vollzeitstelle, aber es ist jemandes Aufgabe.

Details zur Hardware stehen in [KI-Agenten selbst hosten](/blog/ki-agenten-selbst-hosten).

## Preis je Nutzer oder je Anlage: eine Beispielrechnung

Nimm ein Unternehmen mit 80 Mitarbeitern, das zehn Agenten einsetzen will.

| | Preis je Nutzer | Preis je Anlage |
|---|---|---|
| Grundlage | 80 Nutzer | 10 Agenten |
| Rechnung | 80 × Preis je Nutzer | Edition Team |
| Bei 25 € je Nutzer | 2.000 € im Monat | 390 € im Monat |
| Sprachmodell | meist enthalten, mit Nutzungsregel | eigener Zugang, nach Verbrauch |
| Server und Pflege | entfällt | kommt dazu |

Die Rechnung dreht sich, wenn nur fünf Menschen die Plattform nutzen: Dann ist der Preis je Nutzer günstiger und du sparst dir den Betrieb. Die Faustregel: Je mehr Menschen auf wenige Agenten kommen, desto eher lohnt sich der Preis je Anlage.

## Wo die Kosten aus dem Ruder laufen

Aus dem eigenen Betrieb kennen wir drei Stellen.

1. **Agenten in der Schleife.** Ein Agent, der an einer Aufgabe hängt und es immer wieder versucht, verbraucht Tokens ohne Ergebnis. Dagegen helfen eine Obergrenze für Schritte und ein Budget je Agent.
2. **Das größte Modell für alles.** Wer jede Kleinigkeit vom teuersten Modell erledigen lässt, zahlt ein Vielfaches. Besser: einfache Aufgaben an ein kleines Modell, schwierige an ein großes.
3. **Agenten reden mit Agenten.** Mehrere Agenten, die sich gegenseitig Fragen stellen, können viel Text erzeugen, ohne dass die eigentliche Arbeit vorankommt. Rückfragen sollten begründet sein.

## So schätzt du deine Kosten vor dem Start

1. Wähle eine einzige Aufgabe, die heute regelmäßig Zeit kostet.
2. Lege einen Agenten dafür an und gib ihm ein kleines Budget.
3. Lass ihn zwei Wochen laufen und notiere Kosten und gesparte Zeit je Aufgabe.
4. Rechne hoch: Aufgaben im Monat mal Kosten je Aufgabe, plus Plattform, plus Betrieb.
5. Stell die Summe neben die Zeit, die heute dafür draufgeht.

Erst danach lohnt sich die Frage nach weiteren Agenten. Welche Aufgaben sich für den Anfang eignen, zeigt [Aufgaben für KI-Agenten](/blog/aufgaben-fuer-ki-agenten), und der ganze Weg vom Test zum Betrieb steht in [KI-Agenten einführen](/blog/ki-agenten-einfuehren).
