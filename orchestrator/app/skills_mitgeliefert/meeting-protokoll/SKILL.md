---
name: meeting-protokoll
description: "Erstellt aus Transkript, Mitschrift oder Stichpunkten ein Ergebnisprotokoll mit Teilnehmern, Entscheidungen, Aufgaben (Verantwortlicher + Termin), offenen Punkten und nächstem Termin. Nutzen, wenn ein Meeting nachbereitet oder dessen Aufgaben angelegt werden sollen. Auslöser: Protokoll, Ergebnisprotokoll, Meeting, Besprechung, Transkript, Mitschrift."
---

# Ergebnisprotokoll aus einem Meeting

## Wann nutzen
- Du bekommst ein Transkript, eine Aufzeichnungs-Mitschrift, Chat-Notizen oder Stichpunkte zu einem Meeting.
- Jemand fragt „Was wurde beschlossen?“, „Mach ein Protokoll“ oder „Leg die Aufgaben aus dem Termin an“.
- Nicht für die Vorbereitung eines kommenden Termins (Agenda) — das ist eine andere Aufgabe.

## Grundsatz
Ein Ergebnisprotokoll hält fest, **was herausgekommen ist**, nicht wer wann was gesagt hat. Du übernimmst nur, was im Material belegt ist. Was unklar ist, wird zur Rückfrage — nie zur Vermutung.

## Vorgehen
1. **Material sichten.** Lies Transkript/Notizen vollständig. Prüfe, ob Datum, Uhrzeit, Anlass und Teilnehmer erkennbar sind. Fehlt etwas davon, notiere es als Rückfrage.
2. **Kontext laden.** Suche im Gedächtnis bzw. in der Wissensbasis (z. B. `memory_search`, `brain_search`, falls verfügbar) nach früheren Protokollen desselben Termins: offene Punkte von damals, Projektname, übliche Teilnehmer, Protokollvorlage der Firma. Gibt es eine Vorlage, nutze sie statt des Gerüsts unten.
3. **Teilnehmer erfassen.** Name, ggf. Rolle/Bereich. Unterscheide anwesend / entschuldigt / Gäste, wenn erkennbar. Sprechernamen in automatischen Transkripten („Sprecher 2“) nicht raten — als Rückfrage markieren.
4. **Entscheidungen herausziehen.** Nur, was ausdrücklich beschlossen oder bestätigt wurde („Wir machen …“, „Freigegeben“, „Einverstanden“). Ideen, Vorschläge und „müsste man mal“ sind keine Entscheidungen — die gehören zu offenen Punkten.
5. **Aufgaben herausziehen.** Je Aufgabe: Was genau (mit Verb beginnen), wer verantwortlich, bis wann. Fehlt Verantwortlicher oder Termin, trage „offen – Rückfrage“ ein, statt jemanden einzusetzen. Relative Angaben („bis Freitag“, „nächste Woche“) anhand des Meetingdatums in ein Datum umrechnen und kennzeichnen, dass du umgerechnet hast.
6. **Offene Punkte sammeln.** Ungeklärte Fragen, vertagte Themen, Abhängigkeiten, Risiken, die genannt wurden.
7. **Nächsten Termin festhalten,** wenn genannt (Datum, Uhrzeit, Ort/Link, Themen). Sonst „nicht vereinbart“.
8. **Widersprüche prüfen.** Wurde eine Entscheidung später im Gespräch zurückgenommen oder geändert? Dann gilt der letzte Stand; den Wechsel kurz vermerken.
9. **Rückfragen bündeln.** Alles Unklare in einer Liste am Ende. Bei wichtigen Lücken (z. B. Verantwortlicher einer kritischen Aufgabe) die Rückfrage über `request_approval` oder `escalate_if_unsure` stellen, statt weiterzuraten.
10. **Ablegen und zeigen.** Protokoll als Markdown-Datei im Arbeitsordner speichern (z. B. `protokolle/JJJJ-MM-TT_<thema>.md`), auf Wunsch zusätzlich als DOCX/PDF, und mit `present_file` bereitstellen.
11. **Aufgaben anlegen — nur auf Wunsch.** Wenn der Nutzer es möchte: Aufgaben für Agenten mit `create_task`, eigene To-dos mit `update_todos` (vorher `list_todos`), Microsoft To Do oder Planner mit `create_todo_task` / `create_planner_task`, falls verbunden. Vorher die Liste der anzulegenden Aufgaben zeigen und bestätigen lassen. Keine Aufgaben für Personen anlegen, die nicht zugestimmt haben oder deren Zuständigkeit offen ist.
12. **Gedächtnis pflegen.** Wichtige Entscheidungen kurz in Gedächtnis/Wissensbasis ablegen, damit der nächste Termin darauf aufbauen kann.

## Prüfliste vor der Abgabe
- [ ] Jede Entscheidung ist im Material wörtlich oder sinngemäß belegt.
- [ ] Jede Aufgabe hat Was / Wer / Bis wann — oder einen sichtbaren „offen“-Vermerk.
- [ ] Keine Person wurde als verantwortlich eingetragen, die das nicht übernommen hat.
- [ ] Daten sind absolute Daten (TT.MM.JJJJ), relative Angaben umgerechnet und gekennzeichnet.
- [ ] Persönliche Bemerkungen, Smalltalk, Gesundheits- oder Personalthemen sind nicht im Protokoll, sofern sie nicht ausdrücklich protokolliert werden sollten.
- [ ] Offene Punkte aus dem Vorprotokoll sind aufgegriffen (erledigt / weiter offen).
- [ ] Rückfragen stehen gesammelt am Ende.

## Typische Fehler
- Vorschläge als Beschlüsse protokollieren.
- „Team“ oder „alle“ als Verantwortliche eintragen — Aufgaben brauchen eine Person.
- Verlaufsprotokoll statt Ergebnisprotokoll: seitenlange Nacherzählung.
- Fehler aus der Spracherkennung übernehmen (Namen, Fachbegriffe, Zahlen). Bei Zweifel als Rückfrage markieren.
- Aufgaben ungefragt im System anlegen oder Personen zuweisen.
- Wertende Formulierungen („X hat blockiert“) — neutral bleiben.

## Ausgabeformat
```markdown
# Ergebnisprotokoll: <Anlass/Thema>

| | |
|---|---|
| Datum / Uhrzeit | TT.MM.JJJJ, HH:MM–HH:MM |
| Ort / Medium | <Raum / Videokonferenz> |
| Teilnehmende | <Name (Rolle)>, … |
| Entschuldigt | <Name> oder – |
| Protokoll | <erstellt von / Agent, Entwurf> |

## Entscheidungen
1. <Entscheidung> — <kurzer Kontext, falls nötig>

## Aufgaben
| Nr. | Aufgabe | Verantwortlich | Termin | Status |
|---|---|---|---|---|
| A1 | <Verb + Ergebnis> | <Name> | TT.MM.JJJJ | offen |
| A2 | <…> | offen – Rückfrage | offen – Rückfrage | offen |

## Offene Punkte
- <Frage/Thema> — <wer klärt, falls bekannt>

## Nächster Termin
<Datum, Uhrzeit, Ort/Link, Themen> oder „nicht vereinbart“

## Rückfragen zum Protokoll
- <Was ist unklar und warum (z. B. „Sprecher 3 nicht zuordenbar“)>
```

## Grenzen und Übergabe an Menschen
- Das Protokoll ist ein **Entwurf**, bis eine teilnehmende Person es freigegeben hat. Kennzeichne es so, solange keine Freigabe vorliegt.
- Versand an Teilnehmende oder Externe nur nach Freigabe (`request_approval`), nicht eigenständig.
- Bei Betriebsrats-, Personal-, Vorstands- oder Aufsichtsratsprotokollen gelten oft besondere Form- und Vertraulichkeitsregeln: Struktur vorbereiten, Inhalt und Freigabe beim zuständigen Menschen lassen.
- Wurde das Meeting aufgezeichnet, ohne dass erkennbar ist, dass alle zugestimmt haben, weise darauf hin, statt das Material ungefragt weiterzuverbreiten.
