import { basis, vorbereitung } from './gemeinsam.mjs';
export default { ...basis, vorbereitung: vorbereitung('/tasks'), schritte: [
  { karte: { titel: 'Aufgabe vergeben', untertitel: 'Erst den Plan ansehen, dann starten' }, halten: 3000 },
  { klick: { rolle: 'link', name: 'Neue Aufgabe' }, text: { titel: '„Neue Aufgabe“', erklaerung: 'Aufgaben laufen im Hintergrund weiter — auch wenn du die Seite schließt.' } },
  { tippen: { css: 'input[placeholder="Kurz: Worum geht es?"]' }, wert: 'Wochenbericht Café', text: { titel: 'Kurz benennen', erklaerung: 'Der Titel erscheint in der Aufgabenliste.' } },
  { einfuegen: { css: 'textarea' }, wert: 'Erstelle aus den Notizen im Wissen einen kurzen Wochenbericht für das Café-Team: drei Erfolge, zwei offene Punkte.', text: { titel: 'Auftrag beschreiben', erklaerung: 'Je genauer, desto besser das Ergebnis.' } },
  { zeigen: { css: 'select' }, text: { titel: 'Agent wählen — oder automatisch', erklaerung: 'Ohne Auswahl sucht die Plattform den passenden Agenten.' } },
  { klick: { rolle: 'button', name: /^Dry-Run/ }, text: { titel: 'Probelauf einschalten', erklaerung: 'Der Agent schreibt erst nur einen Plan und führt nichts aus.' } },
  { klick: { rolle: 'button', name: 'Vorschau erstellen' }, text: { titel: 'Vorschau erstellen', erklaerung: 'Passt der Plan, startest du ihn mit einem Klick wirklich.' } },
  { warten: 4000 },
  { karte: { titel: 'Fertig', untertitel: 'Den Stand jeder Aufgabe siehst du unter „Aufgaben“.' }, halten: 3000, bleiben: true },
] };
