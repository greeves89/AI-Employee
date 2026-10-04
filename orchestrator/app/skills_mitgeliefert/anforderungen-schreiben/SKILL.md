---
name: anforderungen-schreiben
description: "Macht aus Ideen, Wünschen und Anforderungen umsetzbare Pakete – Problem, Zielgruppe, Ziel mit Messgröße, User Stories mit Akzeptanzkriterien, Abgrenzung, Priorisierung und geschnittene Aufgaben für Agenten oder Team. Nutzen, wenn eine Produktidee oder ein Feature konkretisiert werden soll."
---

# Anforderungen schreiben

## Wann nutzen
- Jemand hat eine Idee, einen Wunsch oder ein Problem („Wir bräuchten …“, „Kunden beschweren sich über …“) und es soll umsetzbar werden.
- Eine bestehende Anforderung ist zu vage, zu groß oder ohne Akzeptanzkriterien.
- Mehrere Wünsche sollen priorisiert und in Aufgaben geschnitten werden.

## Grundsatz
Eine gute Anforderung beschreibt **das Problem und das gewünschte Ergebnis**, nicht die Lösung im Detail. Sie ist prüfbar (Akzeptanzkriterien), begrenzt (Abgrenzung) und klein genug, um in überschaubarer Zeit fertig zu werden.

## Vorgehen
1. **Ausgangslage sammeln.** Lies alles Vorhandene: Notizen, Mails, Tickets, Protokolle. Suche in Wissensbasis und Gedächtnis (`brain_search`, `memory_search`, falls verfügbar) nach verwandten Anforderungen, früheren Entscheidungen, bestehenden Funktionen. Doppelte Anforderungen vermeiden.
2. **Problem formulieren.** Wer hat welches Problem in welcher Situation, und was kostet es heute (Zeit, Fehler, Umsatz, Ärger)? Belege nennen, sonst als Annahme kennzeichnen.
3. **Zielgruppe benennen.** Konkrete Rollen (z. B. „Sachbearbeitung Einkauf“), nicht „alle Nutzer“.
4. **Ziel und Messgröße festlegen.** Woran erkennt man nach der Umsetzung, dass es funktioniert? Messgröße mit Ausgangswert und Zielwert — fehlen Werte, „Ausgangswert erheben“ als Aufgabe aufnehmen statt Zahlen zu erfinden.
5. **Rückfragen klären.** Fehlen Problem, Zielgruppe oder Ziel, stelle höchstens 5 gezielte Fragen, bevor du weiterschreibst. Bei Mehrdeutigkeit `escalate_if_unsure`.
6. **User Stories schreiben.** Format: „Als <Rolle> möchte ich <Fähigkeit>, damit <Nutzen>.“ Eine Story = ein Nutzen. Prüfe gegen INVEST: unabhängig, verhandelbar, wertvoll, schätzbar, klein, testbar.
7. **Akzeptanzkriterien je Story.** Konkret und prüfbar, gern als „Gegeben … wenn … dann …“. Auch Fehlerfälle, Berechtigungen, Datenschutz und Grenzwerte abdecken.
8. **Abgrenzung schreiben.** Was ausdrücklich **nicht** dazugehört (spätere Ausbaustufe, andere Abteilung, bewusst weggelassen). Das verhindert schleichende Ausweitung.
9. **Randbedingungen und Abhängigkeiten.** Bestehende Systeme, Schnittstellen, rechtliche Vorgaben (z. B. Datenschutz, Aufbewahrung), Termine, Budget — nur was bekannt ist.
10. **Priorisieren.** Nutzen und Aufwand je Story grob einschätzen (hoch/mittel/niedrig), dann sortieren: zuerst hoher Nutzen bei geringem Aufwand. Alternativ MoSCoW (Muss/Soll/Kann/Diesmal nicht). Die Einschätzung ist ein Vorschlag — begründen, Entscheidung beim Menschen.
11. **Aufgaben schneiden.** Jede Story in Aufgaben zerlegen, die einzeln fertig und prüfbar sind (Richtwert: in einem Arbeitstag bzw. einem Agentenlauf erledigbar). Je Aufgabe: Ergebnis, Abhängigkeiten, wer (Agent/Team/Person).
12. **Ablegen und vorlegen.** Als Markdown-Datei im Arbeitsordner (z. B. `anforderungen/<thema>.md`), `present_file`. Auf Wunsch nach Freigabe Aufgaben anlegen: `create_task` / `create_task_batch` für Agenten, Planner oder Tickets, falls verbunden.

## Prüfliste
- [ ] Problem ist ohne Lösung beschrieben und mit Belegen oder als Annahme gekennzeichnet.
- [ ] Ziel hat eine Messgröße; keine erfundenen Ausgangswerte.
- [ ] Jede Story hat Rolle, Fähigkeit, Nutzen und mindestens 2 prüfbare Akzeptanzkriterien.
- [ ] Fehlerfälle und Berechtigungen sind bedacht.
- [ ] Abgrenzung „nicht im Umfang“ ist ausgefüllt.
- [ ] Priorisierung ist begründet.
- [ ] Aufgaben sind klein, prüfbar und haben ein klares Ergebnis.
- [ ] Offene Fragen sind gelistet, nicht übergangen.

## Typische Fehler
- Lösung statt Problem („Wir brauchen einen Button“) — frage nach dem Warum.
- Akzeptanzkriterien wie „soll schnell sein“ oder „benutzerfreundlich“ — nicht prüfbar.
- Riesen-Stories („Kundenportal bauen“) ohne Schnitt.
- Abgrenzung vergessen — dann wächst der Umfang unbemerkt.
- Messgrößen, die niemand erheben kann.
- Annahmen als Fakten darstellen.

## Vorlage
```markdown
# Anforderung: <Titel>
Stand: TT.MM.JJJJ · Status: Entwurf · Ansprechperson fachlich: <Rolle/Name>

## Problem
<Wer hat welches Problem wann? Was kostet es heute? Beleg/Annahme>

## Zielgruppe
- <Rolle 1>, <Rolle 2>

## Ziel und Messgröße
| Ziel | Messgröße | Ausgangswert | Zielwert | Erhebung |
|---|---|---|---|---|
| <…> | <…> | <Wert oder „erheben“> | <…> | <wie/wann> |

## User Stories
### S1: <Kurztitel> — Priorität: <Muss/Soll/Kann> · Nutzen: <h/m/n> · Aufwand: <h/m/n>
Als <Rolle> möchte ich <Fähigkeit>, damit <Nutzen>.
Akzeptanzkriterien:
- Gegeben <Ausgangslage>, wenn <Aktion>, dann <Ergebnis>.
- <Fehlerfall/Berechtigung>

## Nicht im Umfang
- <…>

## Randbedingungen und Abhängigkeiten
- <…>

## Aufgaben
| Nr. | Aufgabe | Ergebnis | Story | Abhängig von | Zuständig |
|---|---|---|---|---|---|
| T1 | <…> | <prüfbares Ergebnis> | S1 | – | <Agent/Team> |

## Offene Fragen
- <…>
```

## Grenzen und Übergabe an Menschen
- Priorität, Budget und Termine entscheidet die verantwortliche Person (Produktverantwortung, Geschäftsführung) — du lieferst begründete Vorschläge.
- Aufwandsschätzungen sind grobe Einordnungen, keine Zusagen; die Umsetzenden schätzen verbindlich.
- Berührt die Anforderung Personaldaten, Mitarbeiterüberwachung, Verträge oder Datenschutz, auf Prüfung durch die zuständige Stelle (z. B. Datenschutz, Betriebsrat, Rechtsabteilung) hinweisen.
- Aufgaben erst nach Freigabe anlegen (`request_approval`).
