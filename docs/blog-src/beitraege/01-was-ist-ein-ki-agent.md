---
slug: was-ist-ein-ki-agent
title: Was ist ein KI-Agent? Einfach erklärt, mit Beispielen
description: Ein KI-Agent beantwortet nicht nur Fragen, er erledigt Aufgaben in deinen Systemen. Was ihn vom Chatbot unterscheidet und woran du einen guten erkennst.
keyword: KI-Agent
cover: was-ist-ein-ki-agent.png
rubrik: Grundlagen
symbol: bot
farbe: blue
tags: Grundlagen, KI-Agenten, Chatbot
faq:
- Ist ChatGPT ein KI-Agent? | Im normalen Chat nicht: Du fragst, es antwortet. Zum Agenten wird ein Sprachmodell erst, wenn es Werkzeuge bekommt, ein Ziel über mehrere Schritte verfolgt und in anderen Systemen etwas verändern darf.
- Braucht ein KI-Agent ein eigenes Sprachmodell? | Nein. Der Agent nutzt ein vorhandenes Modell, etwa von Anthropic, OpenAI oder ein lokal betriebenes. Die Plattform darum herum liefert Werkzeuge, Gedächtnis, Regeln und Protokoll.
- Kann ein KI-Agent Fehler machen? | Ja. Deshalb gehören Freigaben für heikle Schritte, begrenzte Rechte und ein Protokoll zu jedem ernsthaften Einsatz. Ein Agent ohne diese drei Dinge ist ein Risiko, kein Mitarbeiter.
- Was ist der Unterschied zwischen KI-Agent und KI-Mitarbeiter? | Technisch keiner. „KI-Mitarbeiter“ beschreibt einen Agenten, der dauerhaft eine Rolle hat: eigene Zuständigkeiten, eigenes Wissen, feste Arbeitszeiten und einen Menschen, dem er berichtet.
---
Ein KI-Agent ist ein Programm, das ein Sprachmodell nutzt, um ein Ziel selbstständig in mehreren Schritten zu erreichen: Er plant, ruft Werkzeuge auf, prüft das Ergebnis und macht weiter, bis die Aufgabe erledigt ist. Ein Chatbot antwortet. Ein KI-Agent handelt.

Das klingt nach einem kleinen Unterschied. In der Praxis entscheidet er darüber, ob dir die KI einen Text liefert, den du selbst weiterverarbeiten musst, oder ob die Arbeit am Ende getan ist.

## Was ein KI-Agent anders macht als ein Chatbot

Ein Chatbot bekommt eine Frage und gibt eine Antwort. Danach bist du wieder dran. Ein KI-Agent bekommt einen Auftrag und arbeitet ihn ab.

| | Chatbot | KI-Agent |
|---|---|---|
| Eingabe | Eine Frage | Ein Ziel |
| Arbeitsweise | Eine Antwort je Frage | Viele Schritte bis zum Ergebnis |
| Zugriff | Nur das Gespräch | Postfach, Dateien, Tickets, Programme |
| Gedächtnis | Meist nur das laufende Gespräch | Wissen über Tage und Wochen |
| Ergebnis | Text | Erledigte Arbeit |

![Vergleich: Ein Chatbot antwortet auf eine Frage, ein KI-Agent arbeitet einen Auftrag in mehreren Schritten ab](/blog/media/grafik-chatbot-oder-agent.png)

Ein Beispiel. Du schreibst: „Mach mir die Morgenlage und beantworte die Mails.“ Ein Chatbot erklärt dir, wie man eine Morgenlage aufbaut. Ein Agent liest dein Postfach, sortiert nach Dringlichkeit, entwirft Antworten im Ton deiner früheren Mails, sendet die unkritischen und legt dir die eine Mail mit dem Angebot über 5.000 Euro zur Freigabe vor.

## Die vier Bausteine eines Agenten

Jeder ernsthafte KI-Agent besteht aus denselben vier Teilen.

1. **Ein Sprachmodell.** Es versteht den Auftrag und entscheidet, was als Nächstes zu tun ist. Welches Modell das ist, kannst du bei guten Plattformen wählen und später wechseln.
2. **Werkzeuge.** Ohne Werkzeuge bleibt jedes Modell ein Gesprächspartner. Werkzeuge sind der Zugriff auf Mail, Kalender, Dateien, Ticketsystem, Browser oder eine Kommandozeile.
3. **Gedächtnis.** Ein Agent, der jeden Morgen bei null anfängt, macht dieselben Fehler immer wieder. Er braucht Wissen, das bleibt: über dich, deine Abläufe und das, was beim letzten Mal schiefging.
4. **Regeln.** Was darf der Agent allein, was nur mit Freigabe, was nie? Diese Grenze ist kein Zusatz, sondern der Teil, der aus einem Experiment etwas macht, das man laufen lassen kann.

Der vierte Baustein fehlt in den meisten Vorführungen. Dort sieht alles beeindruckend aus, weil niemand fragt, was passiert, wenn der Agent sich irrt. Mehr dazu steht im Beitrag [Wie sicher sind KI-Agenten?](/blog/wie-sicher-sind-ki-agenten).

## Woran du einen echten KI-Agenten erkennst

Der Begriff wird inzwischen auf fast alles geklebt. Drei Fragen trennen einen Agenten von einem Chatbot mit neuem Namen.

- **Arbeitet er weiter, wenn du nicht hinsiehst?** Ein Agent hat Zeitpläne und Zuständigkeiten. Er prüft morgens das Postfach, ohne dass ihn jemand anstößt.
- **Verändert er etwas außerhalb des Gesprächs?** Er schreibt die Datei, legt das Ticket an, verschickt die Mail. Wenn am Ende nur Text im Chatfenster steht, war es kein Agent.
- **Kann er mit anderen zusammenarbeiten?** Ein Agent kann eine Teilaufgabe an einen Kollegen abgeben und das Ergebnis wieder einsammeln, so wie Menschen in einem Team.

Bei uns hat jeder Agent dafür einen eigenen abgeschotteten Container: einen kleinen Rechner mit eigenem Arbeitsverzeichnis, eigenen Werkzeugen und eigenem Gedächtnis. Das ist der Grund, warum ein Entwickler-Agent auf Zuruf ein fertiges Windows-Programm bauen und als Datei im Chat abliefern kann, statt nur den Quelltext zu zeigen.

## Wofür sich ein KI-Agent lohnt

Agenten sind dort stark, wo eine Aufgabe klar umrissen ist, aber jedes Mal etwas anders abläuft: Anfragen einordnen und beantworten, Tickets vorqualifizieren, Berichte aus mehreren Quellen zusammenstellen, Termine abstimmen, Unterlagen prüfen. Eine längere Liste mit Beispielen findest du unter [Aufgaben für KI-Agenten](/blog/aufgaben-fuer-ki-agenten).

Wenig sinnvoll sind Agenten für Abläufe, die immer exakt gleich sind. Dafür reicht eine klassische Automatisierung, die billiger und berechenbarer ist. Wann welche Lösung passt, erklärt der Beitrag [KI-Agent oder Workflow](/blog/ki-agent-oder-workflow).

## Was du vor dem ersten Agenten klären solltest

Bevor du dich für eine Plattform entscheidest, lohnen sich drei Fragen.

- **Wo laufen die Daten?** Ein Agent liest Mails, Verträge und Kundendaten. Ob das in einer fremden Cloud oder auf deinem eigenen Server passiert, ist die wichtigste Entscheidung überhaupt. Wie der Eigenbetrieb aussieht, steht in [KI-Agenten selbst hosten](/blog/ki-agenten-selbst-hosten).
- **Wer gibt frei?** Lege vor dem Start fest, welche Handlungen ein Mensch bestätigen muss. Nachträglich macht das niemand.
- **Was kostet es wirklich?** Die Plattform ist nur ein Teil. Dazu kommen die Kosten für das Sprachmodell und der Betrieb. Die Rechnung steht in [Was kostet ein KI-Agent?](/blog/was-kostet-ein-ki-agent).

Ein KI-Agent ist kein besserer Chatbot. Er ist ein Mitarbeiter mit sehr engem Auftrag, der nie müde wird und alles protokolliert. Wer ihn so behandelt, mit klarer Aufgabe, klaren Grenzen und jemandem, der hinsieht, bekommt Arbeit abgenommen. Wer ihn wie ein Spielzeug behandelt, bekommt ein Spielzeug.
