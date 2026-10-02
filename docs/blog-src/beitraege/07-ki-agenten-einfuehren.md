---
slug: ki-agenten-einfuehren
title: KI-Agenten einführen: In fünf Schritten zum Betrieb
description: KI-Agenten einführen ohne Großprojekt: mit einer Aufgabe beginnen, Grenzen festlegen, zwei Wochen messen und erst dann erweitern. Der Weg in fünf Schritten.
keyword: KI-Agenten einführen
cover: ki-agenten-einfuehren.png
rubrik: Einführung
symbol: rocket
farbe: green
tags: Einführung, KI-Agenten, Praxis
faq:
- Wie lange dauert es, KI-Agenten einzuführen? | Der erste Agent läuft an einem Tag. Bis er zuverlässig allein arbeitet, vergehen meist zwei bis vier Wochen, in denen du seine Ergebnisse prüfst und sein Wissen ergänzt.
- Wer sollte im Unternehmen zuständig sein? | Eine Person, die die Aufgabe fachlich kennt, und eine, die die Plattform betreut. Ohne fachliche Zuständigkeit verwaist jeder Agent.
- Brauche ich dafür Programmierkenntnisse? | Für die Einrichtung eines Agenten aus einer Vorlage nicht. Für den Betrieb der Plattform auf einem eigenen Server braucht es jemanden, der mit Docker umgehen kann.
- Was mache ich, wenn die Mitarbeiter skeptisch sind? | Mit einer Aufgabe beginnen, die niemand gern macht, und die Ergebnisse offen zeigen. Skepsis legt sich, wenn der Agent lästige Arbeit abnimmt, nicht durch Ankündigungen.
---
KI-Agenten einführen gelingt am zuverlässigsten in kleinen Schritten: eine Aufgabe, ein Agent, enge Grenzen, zwei Wochen messen, dann erweitern. Wer mit zehn Agenten und einem Strategiepapier beginnt, hat nach drei Monaten zehn halbfertige Agenten und niemanden, der ihnen vertraut.

Die folgenden fünf Schritte sind der Weg, den wir selbst gegangen sind und den wir empfehlen.

![Die fünf Schritte: Aufgabe wählen, Grenzen festlegen, Wissen geben, zwei Wochen messen, erweitern](/blog/media/grafik-fuenf-schritte.png)

## Schritt 1: Eine Aufgabe auswählen

Nicht die wichtigste und nicht die schwierigste, sondern eine, die drei Bedingungen erfüllt.

- Sie fällt mehrmals pro Woche an.
- Ihr Ergebnis lässt sich in einer Minute prüfen.
- Ein Fehler richtet keinen Schaden an.

Typische Einstiege sind Recherchen, Zusammenfassungen, Entwürfe für Antworten und die tägliche Übersicht am Morgen. Eine Liste mit Ideen findest du in [Aufgaben für KI-Agenten](/blog/aufgaben-fuer-ki-agenten).

Schreib die Aufgabe so auf, wie du sie einer neuen Kollegin erklären würdest: Was ist das Ziel, woher kommen die Angaben, woran erkennt man ein gutes Ergebnis? Wenn dir das schwerfällt, ist die Aufgabe noch nicht reif für einen Agenten.

## Schritt 2: Grenzen festlegen, bevor der Agent startet

Entscheide vor dem ersten Lauf, was der Agent allein darf.

| Handlung | Stufe am Anfang |
|---|---|
| Lesen, suchen, zusammenfassen | Erlaubt |
| Entwürfe schreiben | Erlaubt |
| Etwas nach außen senden | Freigabe |
| Daten ändern | Freigabe |
| Löschen, bezahlen | Verboten |

Dazu gehören zwei Zahlen: ein Budget für das Sprachmodell und eine Obergrenze für Schritte je Aufgabe. Beides verhindert, dass ein Agent in einer Schleife stundenlang Geld verbraucht. Warum diese Grenzen technisch gelten müssen, steht in [Wie sicher sind KI-Agenten?](/blog/wie-sicher-sind-ki-agenten).

Wenn personenbezogene Daten im Spiel sind, kläre jetzt die Verträge und die Dokumentation, nicht nach dem ersten Vorfall. Die Punkte stehen in [KI-Agenten und DSGVO](/blog/ki-agenten-und-dsgvo).

## Schritt 3: Dem Agenten Wissen geben

Ein Agent ohne Wissen über dein Unternehmen arbeitet wie eine Aushilfe am ersten Tag: bemüht, aber ahnungslos. Gib ihm, was eine neue Kollegin bekäme.

- **Wer wir sind:** Angebot, Kunden, Tonfall.
- **Wie wir es machen:** die Regeln für genau diese Aufgabe, mit zwei oder drei guten Beispielen.
- **Was nie passieren darf:** die Ausnahmen und die heiklen Fälle.

Das muss kein Handbuch sein. Eine Seite reicht für den Anfang. Wichtiger ist, dass der Agent sein Wissen ergänzt: Jede Korrektur, die du ihm gibst, sollte er sich merken, damit derselbe Fehler nicht zweimal passiert.

## Schritt 4: Zwei Wochen mitlesen und messen

In den ersten zwei Wochen prüfst du jedes Ergebnis. Das ist Aufwand, aber er ist die eigentliche Einführung.

Halte drei Dinge fest.

1. **Wie oft stimmt das Ergebnis ohne Nacharbeit?**
2. **Wie viel Zeit spart es je Aufgabe?**
3. **Was kostet es je Aufgabe an Sprachmodell?**

Bewerte die Ergebnisse dort, wo der Agent arbeitet, mit Daumen hoch oder runter und einem Satz dazu. Aus den schlechten Bewertungen entsteht die Liste dessen, was im Wissen noch fehlt.

Nach zwei Wochen hast du eine Zahl statt eines Gefühls. Wenn neun von zehn Ergebnissen ohne Nacharbeit stimmen, kannst du die Aufsicht lockern. Wenn es fünf von zehn sind, fehlt Wissen oder die Aufgabe passt nicht.

## Schritt 5: Erweitern, einen Schritt nach dem anderen

Erst wenn der erste Agent zuverlässig läuft, kommt der nächste Schritt. Es gibt drei Richtungen.

- **Mehr Spielraum.** Stufe für Stufe von „Freigabe“ zu „Erlaubt“, beginnend mit den Handlungen, bei denen du nie etwas ändern musstest.
- **Mehr Aufgaben.** Ein zweiter Agent mit eigener Rolle, nicht eine zweite Aufgabe für denselben Agenten.
- **Zusammenarbeit.** Agenten, die sich Arbeit übergeben: Einer recherchiert, einer schreibt, einer prüft. Das lohnt sich erst, wenn jeder einzelne seine Rolle beherrscht.

Bei Teams aus mehreren Agenten gilt eine Erfahrung besonders: Sie reden gern miteinander. Achte darauf, dass jede Übergabe sagt, warum sie nötig ist und was davon abhängt, sonst entsteht viel Abstimmung und wenig Ergebnis.

## Die häufigsten Fehler, wenn Unternehmen KI-Agenten einführen

- **Zu groß anfangen.** Ein Agent, der „den Vertrieb unterstützt“, hat keinen Auftrag. Ein Agent, der „offene Angebote nach sieben Tagen nachfasst“, hat einen.
- **Niemand ist zuständig.** Jeder Agent braucht einen Menschen, dem er berichtet und der seine Ergebnisse ansieht.
- **Freigaben, die niemand sieht.** Wenn die Rückfrage des Agenten in einer Oberfläche landet, die niemand öffnet, steht die Arbeit. Freigaben gehören dorthin, wo du ohnehin bist: in den Messenger oder aufs Telefon.
- **Kosten nicht ansehen.** Ohne Blick auf die Ausgaben je Agent fällt eine Schleife erst auf der Rechnung auf. Die Zusammensetzung der Kosten erklärt [Was kostet ein KI-Agent?](/blog/was-kostet-ein-ki-agent).

## Was du für den Start brauchst

Eine Plattform, einen Zugang zu einem Sprachmodell und eine Aufgabe. Wenn die Plattform auf deinem eigenen Server laufen soll, steht in [KI-Agenten selbst hosten](/blog/ki-agenten-selbst-hosten), was dazu nötig ist. Der Rest ist Geduld für zwei Wochen.
