# Design: Chat-Eingabe Drag & Drop + Auto-Grow + mehrzeilige Bubbles

**Datum:** 2026-07-06
**Scope:** Frontend-only, `frontend/src/components/agents/chat.tsx`
**Status:** Vom User genehmigt

## Ausgangslage

Der Agent-Chat (`AgentChat`) hat bereits Drag & Drop für Datei-Uploads, aber nur
über dem Nachrichten-Scrollbereich. Das Eingabefeld ist kein Drop-Ziel. Die
Textarea ist fix einzeilig (`rows={1}`, `resize-none`) und wächst bei
mehrzeiligem Text (Shift+Enter) nicht mit. Zeilenumbrüche in gesendeten
User-Nachrichten kollabieren in der Chat-Bubble, weil der Content ohne
`whitespace-pre-wrap` gerendert wird.

## Anforderungen

1. Das Chat-Eingabefeld (Input-Area inkl. Textarea) muss ebenfalls Drop-Ziel
   für Dateien sein.
2. Das Eingabefeld wächst mit dem Inhalt, wenn mehrere Zeilen eingegeben
   werden.
3. Mehrzeilige Nachrichten werden in der Chat-Bubble mit erhaltenen
   Zeilenumbrüchen dargestellt.

## Lösung

### 1. Drop-Zone auf gesamten Chat erweitern

Die Drag-Handler (`onDragOver`/`onDragLeave`/`onDrop`) wandern vom
Nachrichten-Scrollbereich auf den äußeren Chat-Container, der auch die
Input-Area umfasst. Das bestehende Overlay („Dateien hier ablegen zum
Hochladen") legt sich damit über den ganzen Chat inklusive Eingabefeld.

`onDragLeave` nutzt einen Drag-Depth-Zähler (enter/leave-Zählung) statt des
bisherigen `currentTarget === target`-Vergleichs, damit das Overlay beim
Ziehen über Kind-Elemente (Textarea, Buttons) nicht flackert.

Das Drop-Verhalten bleibt unverändert: sofortiger Upload nach `/workspace`
über den bestehenden `handleFileUpload` + automatische Chat-Nachricht an den
Agenten. Kein Pending-Attachment-Modell (bewusste User-Entscheidung).

### 2. Textarea Auto-Grow

Bei jeder Eingabe wird die Höhe der Textarea auf `scrollHeight` gesetzt
(vorher auf `auto` zurücksetzen), begrenzt auf max. ca. 8 Zeilen
(`max-h-48`); darüber scrollt die Textarea intern. Nach dem Senden und beim
Leeren wird die Höhe auf eine Zeile zurückgesetzt. Shift+Enter für
Zeilenumbruch existiert bereits und bleibt unverändert.

### 3. Mehrzeilige Chat-Bubble

Der Content-Container in `UserMessage` erhält `whitespace-pre-wrap
break-words`, sodass Zeilenumbrüche und Absätze exakt wie eingegeben
erscheinen.

## Nicht im Scope

- Kein Backend-Change (Upload-Endpoint, WS-Protokoll unverändert).
- Kein Pending-Attachment-Modell (Chips vor dem Senden).
- Keine Änderung der automatischen Upload-Nachricht an den Agenten.

## Fehlerbehandlung

Unverändert: Upload-Fehler erscheinen wie bisher als Error-Message im Chat
(`handleFileUpload` catch-Zweig).

## Testen

- Manuell: Datei über Eingabefeld droppen → Overlay erscheint, Upload läuft,
  Agent-Nachricht kommt.
- Manuell: Mehrzeilige Eingabe via Shift+Enter → Textarea wächst bis 8
  Zeilen, danach interner Scroll; nach Senden zurück auf eine Zeile.
- Manuell: Gesendete mehrzeilige Nachricht zeigt Umbrüche in der Bubble.
- Frontend-Build (`npm run build`) als Verification-Loop.
