# Agenten in Teams-Besprechungen — Stufe 1

Vorbild: „Claude Meetings“ (Anthropic, 12.11.2026). Claude sitzt in einer Besprechung, beantwortet
Fragen, und danach gibt es eine Seite mit Notizen, Aufgaben und Transkript. Externe Gäste sind
standardmäßig ausgeschlossen.

Stufe 1 braucht keine neue Einrichtung in Azure. Sie nutzt die delegierte Anmeldung, die es schon
gibt (eigenes Konto des Agenten oder das des Besitzers, `teams_gateway._agenten_token`).

## Was es schon gibt

| Baustein | Datei |
|---|---|
| Einladung annehmen, Termin-Chat beobachten (Beisitzer), Transkript als Wissen (Mitschreiber) | `services/teams_meetings.py` |
| Ansprache per Name im Chat (`_mentions_agent`, Vorgabe „nur bei Nennung“) | `services/teams_gateway.py` |
| Senden nach Regeln: Lesen / Entwurf (Freigabe) / Schreiben | `teams_gateway.senden_nach_regeln` |
| Protokoll durch einen Agenten: Nachricht `type=meeting`, Antwort über `meeting:{id}:response:{agent}` | `api/meeting_rooms._generate_todo_summary` |
| Gespeicherte Besprechungen (`meetings`), Schnittstelle ohne Oberfläche | `api/meetings.py`, `models/meeting.py` |

## Was Stufe 1 dazubaut

1. **Besprechungsseite je Teams-Termin.** Jeder Termin eines Agenten mit Besprechungsfunktion wird
   eine Zeile in `meetings`:
   - neue Spalten: `quelle` (`aufnahme`/`teams`), `agent_id`, `teams_event_id`, `beginn`, `ende`,
     `status`, `gaeste_extern`, `hinweis`, `protokoll`
   - Besitzer der Zeile ist der Besitzer des Agenten (`user_id`). Damit gilt dieselbe Prüfung wie
     für Mitschnitte (`_get_owned_meeting`).
   - Neue Seite **Besprechungen**: Liste und Detail mit Protokoll, Aufgaben und Transkript, für
     Mitschnitte und Teams-Termine.
2. **Hinweis „Ich schreibe mit“.** Beim ersten Durchlauf, in dem der Termin läuft, schreibt der
   Agent einmal in den Termin-Chat. Das geht über `senden_nach_regeln`:
   - Im Modus Entwurf wird daraus eine Freigabe.
   - Im Modus Lesen wird nichts gesendet. Die Seite zeigt den Stand.
3. **Externe Gäste.** Teilnehmer mit einer Domäne außerhalb der eigenen Organisation gelten als
   Gäste. Eigene Domänen sind die des Agenten-Kontos und die des Besitzers.
   - Voreinstellung: Bei Gästen hängt sich der Agent nicht in den Chat und schreibt nicht mit.
   - Die Seite zeigt „Gäste von außen“.
   - Einstellung `meetings.mit_gaesten` hebt das auf, nur für Besitzer und Admin.
4. **Ansprache per Name** ist schon da. Termin-Chats laufen über dieselbe Liste wie andere Chats.
5. **Protokoll und Aufgaben nach dem Termin.** Ist das Transkript da:
   - Es kommt in `meetings.transcript` und wie bisher ins Wissen.
   - Der Agent bekommt den Protokoll-Auftrag über denselben Weg wie die Besprechungsräume.
   - Das Ergebnis kommt in `meetings.protokoll`.
   - Aufgaben sind die Zeilen `- [ ]` daraus.

## Fremder Text im Transkript (Review 13.11.2026)

Seit 1.452.0 schreibt der Agent das Protokoll in einem Zug ohne Werkzeuge, in jeder Laufzeit
(`MessageConsumer._besprechungs_zug`, Feld `ohne_werkzeuge`). Eine Anweisung im Transkript kann
nichts ausführen, lesen oder wegschicken. Dazu kommen die Abgrenzung als fremder Text
(Zufallskennung je Auftrag) und die Gäste-Regel.

## Bewusst nicht in Stufe 1

- Live-Arbeitsfläche (Dokument oder Whiteboard) → Stufe 2.
- Durchgehendes Zuhören und Sprechen in der Audiospur → Stufe 3 (Medien-Bot, Azure-Registrierung).

## Tests

- Termin wird Zeile und wird nicht doppelt angelegt.
- Hinweis geht genau einmal hinaus. Je Modus: Freigabe bzw. nichts.
- Gäste-Matrix (MCDC):
  - intern / extern
  - mit_gaesten an / aus
  - Rolle Beisitzer / Mitschreiber
- Protokoll landet in der Zeile, Aufgaben werden erkannt.
- Nutzertrennung: Nur der Besitzer sieht die Zeile, 404 für andere.
