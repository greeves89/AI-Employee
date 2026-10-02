---
slug: ki-agent-oder-workflow
title: KI-Agent oder Workflow: Was passt zu welcher Aufgabe?
description: KI-Agent oder Workflow? Ein Workflow folgt festen Schritten, ein Agent entscheidet selbst. Woran du erkennst, was deine Aufgabe braucht, und wann beides zusammen.
keyword: KI-Agent oder Workflow
cover: ki-agent-oder-workflow.png
rubrik: Grundlagen
symbol: workflow
farbe: blue
tags: Grundlagen, Workflows, KI-Agenten
faq:
- Ist ein Workflow mit KI-Schritt schon ein Agent? | Nein. Wenn die Reihenfolge der Schritte feststeht und die KI nur einen davon ausfüllt, ist es ein Workflow. Ein Agent entscheidet selbst, welcher Schritt als Nächstes kommt.
- Was ist günstiger im Betrieb? | Der Workflow. Er verbraucht nur dort Rechenzeit eines Sprachmodells, wo ein KI-Schritt eingebaut ist. Ein Agent denkt bei jedem Schritt nach, und das kostet.
- Kann ein Agent einen Workflow starten? | Ja, und das ist oft die beste Verbindung: Der Agent erkennt, was zu tun ist, und stößt für den festen Teil einen Workflow an.
- Was ist zuverlässiger? | Für gleichförmige Abläufe der Workflow, weil er jedes Mal dasselbe tut. Für wechselnde Fälle der Agent, weil ein Workflow dort an jeder Ausnahme hängen bleibt.
---
KI-Agent oder Workflow: Die Entscheidung hängt an einer einzigen Frage. Stehen die Schritte vorher fest? Dann ist es ein Workflow. Muss unterwegs entschieden werden, was als Nächstes zu tun ist? Dann braucht es einen Agenten.

Beides hat seinen Platz. Der häufigste Fehler ist, das eine zu nehmen, wo das andere gebraucht wird: einen Agenten für eine Aufgabe, die ein einfacher Ablauf besser erledigt, oder einen Ablauf mit vierzig Verzweigungen für eine Aufgabe, die Urteilsvermögen verlangt.

![Vergleich: Ein Workflow folgt festen Schritten, ein KI-Agent wählt seinen Weg unterwegs](/blog/media/grafik-workflow-oder-agent.png)

## Was ein Workflow ist

Ein Workflow ist eine feste Folge von Schritten: Wenn A eintritt, tue B, dann C. Er wird einmal gebaut und läuft danach jedes Mal gleich.

- **Auslöser:** eine neue Mail, ein Zeitpunkt, ein ausgefülltes Formular.
- **Schritte:** Daten holen, umformen, weitergeben.
- **Verzweigungen:** wenn der Betrag über 1.000 Euro liegt, dann zusätzlich den Vorgesetzten fragen.

Ein Workflow kann KI-Schritte enthalten, etwa „fasse diesen Text zusammen“. Er bleibt trotzdem ein Workflow, weil die Reihenfolge feststeht.

## Was ein KI-Agent anders macht

Ein Agent bekommt ein Ziel und entscheidet selbst, wie er es erreicht. Er liest, was vorliegt, wählt ein Werkzeug, prüft das Ergebnis und wählt den nächsten Schritt. Derselbe Auftrag kann beim nächsten Mal anders ablaufen, weil die Lage eine andere ist. Die Grundlagen stehen in [Was ist ein KI-Agent?](/blog/was-ist-ein-ki-agent).

## KI-Agent oder Workflow im direkten Vergleich

| | Workflow | KI-Agent |
|---|---|---|
| Ablauf | Vorher festgelegt | Entsteht unterwegs |
| Stärke | Gleichförmige Fälle | Wechselnde Fälle |
| Verhalten | Jedes Mal gleich | An die Lage angepasst |
| Kosten je Lauf | Niedrig | Höher, je nach Aufwand |
| Aufwand beim Bauen | Jede Ausnahme einzeln | Auftrag, Wissen, Grenzen |
| Fehlerbild | Bleibt an Unbekanntem hängen | Kann sich irren |
| Kontrolle | Über den Aufbau | Über Freigaben und Protokoll |

Die Zeile „Fehlerbild“ ist die wichtigste. Ein Workflow scheitert laut: Er bleibt stehen, wenn etwas nicht passt. Ein Agent scheitert leise: Er liefert ein Ergebnis, das falsch sein kann. Deshalb braucht ein Agent Freigaben für alles, was Gewicht hat, und ein Workflow braucht jemanden, der sich um die hängengebliebenen Fälle kümmert.

## Drei Fragen für die Entscheidung

**1. Kannst du den Ablauf als Liste aufschreiben, ohne „kommt darauf an“?** Wenn ja, bau einen Workflow. Er ist billiger, schneller und berechenbarer.

**2. Wie viele Ausnahmen gibt es?** Bei drei Ausnahmen kommst du mit Verzweigungen zurecht. Bei dreißig pflegst du ein Geflecht, das niemand mehr versteht. Spätestens dann ist ein Agent die einfachere Lösung.

**3. Braucht der Schritt Sprachverständnis?** Eine Mail verstehen, eine Anfrage einordnen, einen passenden Ton treffen: Das sind Aufgaben für ein Sprachmodell. Eine Zahl von einem System ins andere übertragen ist es nicht.

## Beispiele

| Aufgabe | Besser als |
|---|---|
| Rechnung aus dem Postfach in die Buchhaltung übertragen | Workflow |
| Jeden Montag einen Bericht aus festen Quellen erstellen | Workflow |
| Neue Mitarbeiter in fünf Systemen anlegen | Workflow |
| Kundenanfragen lesen, einordnen und beantworten | Agent |
| Ein Angebot aus Anfrage und Preisliste entwerfen | Agent |
| Einen Fehler im Protokoll finden und einschätzen | Agent |

Weitere Beispiele für Agenten stehen in [Aufgaben für KI-Agenten](/blog/aufgaben-fuer-ki-agenten).

## Am stärksten zusammen

In der Praxis ist es selten ein Entweder-oder. Die tragfähigste Form verbindet beides.

- **Der Agent entscheidet, der Workflow führt aus.** Ein Support-Agent erkennt, dass ein Kunde eine Rückerstattung will, und startet den festen Ablauf dafür. Die Erkennung braucht Urteilsvermögen, die Rückerstattung nicht.
- **Der Workflow ruft den Agenten.** Ein fester Ablauf verarbeitet eingehende Formulare und übergibt nur die unklaren Fälle an einen Agenten.
- **Der Workflow hält den Agenten auf Kurs.** Bei langen Vorhaben gibt ein Ablauf die Etappen vor, und Agenten erledigen die einzelnen Etappen.

Für diese Verbindung brauchst du eine Plattform, die beides kennt: Agenten mit eigenen Werkzeugen und Abläufe, die sie anstoßen oder von ihnen angestoßen werden.

## Was das für die Kosten bedeutet

Ein Workflow kostet beim Bauen und fast nichts im Betrieb. Ein Agent ist schnell eingerichtet und kostet bei jedem Lauf, weil jeder Schritt eine Anfrage an ein Sprachmodell ist. Für eine Aufgabe, die tausendmal im Monat gleich abläuft, ist der Workflow deshalb fast immer die wirtschaftlichere Wahl. Für eine Aufgabe, die fünfzigmal im Monat anfällt und jedes Mal anders aussieht, lohnt sich der Bau eines Workflows oft nie. Wie sich die Kosten eines Agenten zusammensetzen, steht in [Was kostet ein KI-Agent?](/blog/was-kostet-ein-ki-agent).

## Die Faustregel

Beginne mit dem Einfachsten, das funktioniert. Lässt sich die Aufgabe als fester Ablauf bauen, bau den Ablauf. Erst wenn du merkst, dass du Ausnahme um Ausnahme nachträgst, ist es Zeit für einen Agenten. Und wenn du einen Agenten einsetzt, dann mit klarer Aufgabe und engen Grenzen. Wie das Schritt für Schritt geht, beschreibt [KI-Agenten einführen](/blog/ki-agenten-einfuehren).
