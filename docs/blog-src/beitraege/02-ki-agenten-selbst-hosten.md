---
slug: ki-agenten-selbst-hosten
title: KI-Agenten selbst hosten: Was du wirklich brauchst
description: KI-Agenten selbst hosten geht ohne Grafikkarte und ohne Rechenzentrum. Welche Hardware reicht, was du einrichten musst und wo die Grenzen liegen.
keyword: KI-Agenten selbst hosten
cover: ki-agenten-selbst-hosten.png
rubrik: Betrieb
symbol: server
farbe: cyan
tags: Betrieb, Self-Hosting, KI-Agenten
faq:
- Brauche ich eine Grafikkarte, um KI-Agenten selbst zu hosten? | Nein, solange das Sprachmodell über eine Schnittstelle kommt. Eine Grafikkarte brauchst du erst, wenn auch das Modell selbst auf deiner Hardware laufen soll.
- Läuft das auch auf einem kleinen Server? | Ja. Unsere eigene Anlage läuft auf einem Raspberry Pi 5 mit 8 GB Arbeitsspeicher. Für ein Team mit einem Dutzend Agenten, die gleichzeitig arbeiten, ist ein Server mit 32 GB die bessere Wahl.
- Verlassen bei selbst gehosteten Agenten gar keine Daten das Haus? | Die Plattform, das Wissen und die Protokolle bleiben bei dir. Die Anfragen an das Sprachmodell gehen an dessen Anbieter, außer du betreibst auch das Modell lokal.
- Wie aufwendig sind Updates? | Ein Update besteht aus dem Abruf der neuen Version und einem Neustart der Dienste. Die Arbeitsverzeichnisse und das Wissen der Agenten bleiben dabei erhalten.
---
KI-Agenten selbst hosten heißt: Die Plattform, auf der deine Agenten arbeiten, läuft auf einem Server, den du kontrollierst, und nicht in der Cloud eines Anbieters. Dafür brauchst du weniger, als die meisten denken: einen Linux-Server mit Docker, einen Zugang zu einem Sprachmodell und jemanden, der Updates einspielt. Eine Grafikkarte brauchst du nicht.

Wir betreiben unsere eigene Anlage seit Monaten auf einem Raspberry Pi 5. Daraus stammen die Zahlen und die Stolpersteine in diesem Beitrag.

## Warum KI-Agenten selbst hosten?

Ein Agent liest dein Postfach, deine Verträge und deine Tickets. Die Frage, auf wessen Server das geschieht, ist deshalb keine technische Nebensache.

- **Datenhoheit.** Wissen, Gesprächsverläufe, Dateien und Protokolle liegen in deiner Datenbank. Niemand außer dir kann sie auswerten.
- **Kein Preis je Kopf.** Eine eigene Anlage kostet dasselbe, egal ob fünf oder fünfzig Menschen damit arbeiten.
- **Freie Modellwahl.** Du trägst deinen eigenen Modellzugang ein und wechselst ihn, wenn ein anderes Modell besser oder günstiger wird.
- **Zugriff auf interne Systeme.** Ein Agent im eigenen Netz erreicht das Ticketsystem und die Dateiablage, ohne dass du sie nach außen öffnen musst.

Der Preis dafür ist Verantwortung: Du spielst Updates ein und sorgst für Sicherungen. Wer das nicht will, ist mit einer gemieteten Lösung besser bedient. Was beim Datenschutz zu beachten ist, steht in [KI-Agenten und DSGVO](/blog/ki-agenten-und-dsgvo).

## Welche Hardware reicht

Der größte Irrtum beim Eigenbetrieb: Man brauche teure Grafikkarten. Das stimmt nur, wenn auch das Sprachmodell lokal laufen soll. Die Agenten selbst sind gewöhnliche Programme in Containern.

| Einsatz | Arbeitsspeicher | Beispiel |
|---|---|---|
| Ausprobieren, zwei bis drei Agenten | 8 GB | Raspberry Pi 5, kleiner Mietserver |
| Team mit rund einem Dutzend Agenten | 32 GB | ein gewöhnlicher Server, auch virtuell |
| Lokales Sprachmodell zusätzlich | je nach Modell | Server mit Grafikkarte |

Zwei Erfahrungen aus dem Betrieb:

- **Arbeitsspeicher zählt mehr als Rechenleistung.** Jeder laufende Agent ist ein eigener Container. Agenten, die gerade nichts zu tun haben, sollten sich schlafen legen und bei Bedarf wecken lassen. Auf dem Pi laufen deshalb meist nur ein oder zwei gleichzeitig.
- **Plattenplatz wächst mit den Arbeitsverzeichnissen.** Jeder Agent hat ein eigenes Verzeichnis. Eine Obergrenze je Agent verhindert, dass ein einzelner die Platte füllt. Bei uns sind es 10 GB.

## Was du einrichten musst

Die Einrichtung besteht aus fünf Schritten.

1. **Server mit Docker.** Ein aktuelles Linux und Docker Compose genügen.
2. **Plattform starten.** Datenbank, Zwischenspeicher, Oberfläche und Steuerung kommen als fertige Container.
3. **Zugang von außen.** Nach außen gehört nur der Reverse-Proxy. Datenbank und Steuerung bleiben im internen Netz. Wer keinen Port öffnen will, nutzt einen Tunnel.
4. **Modellzugang eintragen.** Ein Schlüssel eines Modellanbieters, ein Zugang über Azure oder Bedrock oder die Adresse eines lokalen Modells.
5. **Ersten Agenten anlegen.** Aus einer Vorlage, mit einer klaren Rolle und einer niedrigen Autonomiestufe.

Für den ersten Agenten lohnt es sich, klein anzufangen. Wie du von dort zum Regelbetrieb kommst, beschreibt [KI-Agenten einführen](/blog/ki-agenten-einfuehren).

## Wo die Daten wirklich hingehen

„Selbst gehostet“ wird oft mit „nichts verlässt das Haus“ gleichgesetzt. Das stimmt nur zum Teil, und es ist besser, das vorher zu wissen.

- **Bleibt bei dir:** Wissen und Gedächtnis der Agenten, Dateien, Gesprächsverläufe, Protokolle, Zugangsdaten. Auch die Suche im Wissen kann lokal rechnen, ohne fremden Dienst.
- **Geht an den Modellanbieter:** der Teil des Auftrags, den das Sprachmodell zum Antworten braucht.

![Schaubild: Wissen, Dateien, Verläufe, Protokolle und Zugangsdaten bleiben auf dem eigenen Server, nur die Anfrage geht an das Sprachmodell](/blog/media/grafik-eigener-betrieb.png)

Wer auch das vermeiden muss, betreibt ein lokales Modell oder nutzt einen Modellzugang in einem europäischen Rechenzentrum. Beides ändert nichts an der Plattform, nur an einem Eintrag in den Einstellungen.

## Die Stolpersteine aus dem eigenen Betrieb

Drei Dinge hätten wir gern früher gewusst.

- **Der Verschlüsselungsschlüssel ist heilig.** Zugangsdaten zu Postfächern und Modellen liegen verschlüsselt in der Datenbank. Geht der Schlüssel verloren oder wird er versehentlich neu erzeugt, sind sie nicht mehr lesbar. Er gehört in die Sicherung, getrennt von der Datenbank.
- **Sicherung vor jedem Update.** Ein Abzug der Datenbank dauert Sekunden und erspart im Ernstfall Tage.
- **Ein Update erreicht nicht automatisch jeden Agenten.** Bestehende Container laufen auf dem alten Stand weiter, bis sie neu erstellt werden. Das sollte nacheinander geschehen und nicht, während jemand mit dem Agenten arbeitet.

## Für wen sich der Eigenbetrieb nicht lohnt

Wenn du nur einen Chat mit Firmenwissen für alle Mitarbeiter suchst und niemanden hast, der einen Server betreut, ist eine gemietete Plattform die ehrlichere Wahl. Der Eigenbetrieb lohnt sich, wenn Agenten selbstständig in deinen Systemen arbeiten sollen, wenn Daten das Haus nicht verlassen dürfen oder wenn ein Preis je Mitarbeiter bei deiner Größe nicht aufgeht. Was das im Vergleich kostet, steht in [Was kostet ein KI-Agent?](/blog/was-kostet-ein-ki-agent).

Wenn du noch am Anfang stehst und wissen willst, was ein Agent überhaupt ist, lies zuerst [Was ist ein KI-Agent?](/blog/was-ist-ein-ki-agent).
