---
slug: ki-agenten-und-dsgvo
title: KI-Agenten und DSGVO: Was du vor dem Start klären musst
description: KI-Agenten und DSGVO schließen sich nicht aus. Entscheidend sind vier Fragen: wo die Daten liegen, wer zugreift, wer freigibt und was protokolliert wird.
keyword: KI-Agenten und DSGVO
cover: ki-agenten-und-dsgvo.png
rubrik: Datenschutz
symbol: scale
farbe: green
tags: Datenschutz, DSGVO, KI-Agenten
faq:
- Sind KI-Agenten grundsätzlich DSGVO-konform? | Nein, und auch nicht grundsätzlich unzulässig. Es kommt auf den Aufbau an: Rechtsgrundlage, Verträge mit den Dienstleistern, begrenzte Zugriffe, Freigaben und ein Protokoll.
- Reicht es, wenn die Plattform auf meinem eigenen Server läuft? | Es ist der größte einzelne Schritt, aber nicht alles. Die Anfragen an das Sprachmodell gehen weiter an dessen Anbieter, solange du kein lokales Modell betreibst. Dafür brauchst du einen Vertrag zur Auftragsverarbeitung.
- Darf ein KI-Agent allein über Kundenanliegen entscheiden? | Bei Entscheidungen mit rechtlicher oder ähnlich erheblicher Wirkung verlangt Art. 22 DSGVO in der Regel, dass ein Mensch entscheidet. In der Praxis heißt das: Der Agent bereitet vor, ein Mensch gibt frei.
- Muss ich den Betriebsrat einbinden? | Wenn der Agent Daten von Beschäftigten verarbeitet oder ihr Verhalten sichtbar macht, ist das regelmäßig mitbestimmungspflichtig. Früh einbinden spart Zeit.
---
KI-Agenten und DSGVO passen zusammen, wenn vier Dinge geklärt sind: wo die Daten verarbeitet werden, worauf der Agent zugreifen darf, welche Handlungen ein Mensch freigibt und was protokolliert wird. Ein Verbot gibt es nicht. Ein Agent ist datenschutzrechtlich aber anspruchsvoller als ein Chatbot, weil er selbst auf Systeme zugreift und handelt.

Dieser Beitrag ist ein Leitfaden aus der Praxis und keine Rechtsberatung. Die Bewertung im Einzelfall gehört zu deiner Datenschutzbeauftragten oder deinem Datenschutzbeauftragten.

## Warum Agenten anders sind als ein Chatbot

Bei einem Chatbot entscheidet ein Mensch, was er hineinkopiert. Bei einem Agenten entscheidet der Agent, welche Mail er öffnet und welche Datei er liest. Daraus folgen drei Unterschiede.

- **Der Zugriff ist breiter.** Ein Agent mit Zugang zum Postfach sieht alles, was darin liegt, nicht nur das, was jemand ausgewählt hat.
- **Er handelt nach außen.** Er verschickt Mails, legt Tickets an, ändert Daten. Ein Fehler bleibt nicht im Chatfenster.
- **Er arbeitet unbeaufsichtigt.** Nachts, am Wochenende, nach Zeitplan. Niemand liest in dem Moment mit.

Was ein Agent genau ist und was nicht, erklärt [Was ist ein KI-Agent?](/blog/was-ist-ein-ki-agent).

## KI-Agenten und DSGVO: die vier Fragen

![Die vier Fragen zu KI-Agenten und DSGVO: Verarbeitungsort, Zugriff, Freigabe, Protokoll](/blog/media/grafik-dsgvo-vier-fragen.png)

### 1. Wo werden die Daten verarbeitet?

Es gibt zwei Orte, und beide zählen.

- **Die Plattform:** Hier liegen Wissen, Gesprächsverläufe, Dateien und Protokolle. Betreibst du sie selbst, bleibt das alles in deinem Haus. Wie das geht, steht in [KI-Agenten selbst hosten](/blog/ki-agenten-selbst-hosten).
- **Das Sprachmodell:** Hierhin geht der Teil des Auftrags, den das Modell zum Antworten braucht. Für diesen Anbieter brauchst du einen Vertrag zur Auftragsverarbeitung nach Art. 28 DSGVO und Klarheit über den Verarbeitungsort.

Für das Modell hast du drei Wege: einen Zugang in einem europäischen Rechenzentrum, den direkten Zugang beim Anbieter mit den passenden Verträgen oder ein lokal betriebenes Modell. Der dritte Weg ist der strengste und kostet eigene Hardware.

### 2. Worauf darf der Agent zugreifen?

Der Grundsatz der Datenminimierung gilt auch für Maschinen. Ein Agent für Terminabstimmung braucht den Kalender, nicht das Personalarchiv.

- Jeder Agent bekommt nur die Zugänge, die seine Aufgabe verlangt.
- Agenten verschiedener Nutzer sehen die Daten der anderen nicht.
- Zugangsdaten liegen verschlüsselt und nie im Klartext im Auftrag.

Die Trennung zwischen Nutzern muss im Server geprüft werden, bei jedem einzelnen Zugriff. Eine Oberfläche, die fremde Einträge nur ausblendet, ist keine Trennung.

### 3. Wer gibt frei?

Art. 22 DSGVO schützt Menschen davor, einer rein automatisierten Entscheidung unterworfen zu werden, die sie erheblich betrifft. Für Agenten heißt das praktisch: Bei allem, was Gewicht hat, entscheidet ein Mensch.

Bewährt hat sich eine einfache Einteilung in drei Stufen.

| Stufe | Bedeutung | Beispiel |
|---|---|---|
| Erlaubt | Der Agent handelt allein | Mail lesen, Entwurf schreiben |
| Freigabe | Ein Mensch bestätigt vorher | Mail an Kunden senden, Angebot verschicken |
| Verboten | Der Agent kann es gar nicht | Daten löschen, Zahlungen auslösen |

Wichtig ist, dass die Stufe „Verboten“ technisch durchgesetzt wird und nicht nur als Bitte im Auftrag steht. Mehr dazu in [Wie sicher sind KI-Agenten?](/blog/wie-sicher-sind-ki-agenten).

### 4. Was wird protokolliert?

Die Rechenschaftspflicht verlangt, dass du zeigen kannst, was geschehen ist. Bei einem Agenten heißt das: Jeder Schritt einer Aufgabe ist nachvollziehbar, Werkzeug für Werkzeug, mit Zeitpunkt und Auftraggeber. Wer eine Auskunft nach Art. 15 beantworten oder einen Vorfall aufklären muss, braucht genau dieses Protokoll.

## Die Unterlagen, die du brauchst

Vor dem Start mit personenbezogenen Daten sollten diese Punkte erledigt sein.

1. **Verzeichnis der Verarbeitungstätigkeiten** um den Einsatz der Agenten ergänzen: Zweck, Datenarten, Empfänger.
2. **Verträge zur Auftragsverarbeitung** mit dem Anbieter des Sprachmodells und, falls du nicht selbst betreibst, mit dem Anbieter der Plattform.
3. **Datenschutz-Folgenabschätzung** prüfen. Bei umfangreicher Verarbeitung sensibler Daten oder systematischer Auswertung ist sie in der Regel nötig.
4. **Löschkonzept** für Gedächtnis, Dateien und Protokolle der Agenten.
5. **Information der Betroffenen**, wenn Kunden oder Beschäftigte mit einem Agenten zu tun haben.
6. **Betriebsrat** einbinden, sobald Beschäftigtendaten berührt sind.

## Ein Schutz, der oft vergessen wird

Agenten geben Daten auch nach außen: in Mails, in Suchanfragen, in Aufrufen fremder Dienste. Ein Filter für ausgehende Inhalte, der personenbezogene Daten und Zugangsdaten erkennt und zurückhält, fängt genau die Fälle ab, in denen ein Agent mehr mitschickt, als er sollte. Diesen Filter schaltet man am besten ein, bevor der erste Agent Zugang zu echten Daten bekommt.

## Womit du anfangen kannst

Der einfachste Einstieg ist ein Agent, der gar keine personenbezogenen Daten Dritter braucht: Recherche, interne Dokumentation, Auswertung eigener Zahlen. So sammelst du Erfahrung mit Freigaben und Protokoll, bevor Kundendaten im Spiel sind. Den Weg in Schritten beschreibt [KI-Agenten einführen](/blog/ki-agenten-einfuehren).
