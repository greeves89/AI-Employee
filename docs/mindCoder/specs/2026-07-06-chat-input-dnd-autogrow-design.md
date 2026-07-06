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
Anhängen") legt sich damit über den ganzen Chat inklusive Eingabefeld.

`onDragLeave` nutzt einen Drag-Depth-Zähler (enter/leave-Zählung) statt des
bisherigen `currentTarget === target`-Vergleichs, damit das Overlay beim
Ziehen über Kind-Elemente (Textarea, Buttons) nicht flackert.

**Pending-Attachment-Modell (Nachschärfung durch User nach erster
Iteration, v1.99.90):** Ein Drop löst KEINEN sofortigen Upload und keine
automatische Chat-Nachricht mehr aus. Gedroppte Dateien verhalten sich wie
per Strg+V eingefügte Bilder:

- Bilddateien (`image/*`, max. 5 MB) landen als Thumbnail in
  `pendingImages` — identisch zum Paste-Pfad.
- Alle anderen Dateien landen als Chips (Icon + Name + Größe +
  Entfernen-Button) in `pendingFiles` über dem Eingabefeld; Duplikate
  (gleicher Name + Größe) werden ignoriert.
- Der Büroklammer-Button füttert denselben Pfad (ein konsistentes
  Verhalten statt zwei verschiedener).
- Erst beim Senden werden die `pendingFiles` nach `/workspace` hochgeladen;
  schlägt der Upload fehl, bleiben Text und Chips erhalten (Error-Zeile im
  Chat, kein Teilversand). Die WS-Nachricht an den Agenten erhält den
  User-Text plus Hinweis
  `[Angehängte Dateien, bereits in /workspace hochgeladen: …]`; die
  User-Bubble zeigt den reinen Text plus Datei-Chips (`files` auf
  `ChatMessage`).

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
- Keine Persistenz der Datei-Chips über einen Reload hinaus (nach Reload
  zeigt die Historie den an den Agenten gesendeten Text inkl.
  Datei-Hinweis).

## Fehlerbehandlung

Upload-Fehler beim Senden erscheinen als Error-Message im Chat; Text und
angehängte Dateien bleiben im Eingabebereich erhalten (kein Teilversand).

## Testen

- Manuell: Datei über Eingabefeld droppen → Overlay erscheint, Datei-Chip
  am Eingabefeld; Text dazu tippen, Senden → Upload + EINE Nachricht mit
  Chips in der Bubble.
- Manuell: Mehrzeilige Eingabe via Shift+Enter → Textarea wächst bis 8
  Zeilen, danach interner Scroll; nach Senden zurück auf eine Zeile.
- Manuell: Gesendete mehrzeilige Nachricht zeigt Umbrüche in der Bubble.
- Frontend-Build (`npm run build`) als Verification-Loop.
