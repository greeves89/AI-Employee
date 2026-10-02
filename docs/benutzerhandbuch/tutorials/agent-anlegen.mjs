import { basis, vorbereitung } from './gemeinsam.mjs';
export default { ...basis, vorbereitung: vorbereitung('/agents'), schritte: [
  { karte: { titel: 'Neuen Agenten anlegen', untertitel: 'Vorlage wählen, Namen vergeben, fertig' }, halten: 3000 },
  { klick: { rolle: 'button', name: 'Neuer Agent' }, text: { titel: '„Neuer Agent“', erklaerung: 'Oben rechts auf der Seite „Agenten“.' } },
  { scroll: { rolle: 'button', name: /^Research Assistant/ } },
  { klick: { rolle: 'button', name: /^Research Assistant/ }, text: { titel: 'Vorlage wählen', erklaerung: '34 Vorlagen bringen Rolle, Werkzeuge und Rechte schon mit.' } },
  { tippen: { css: 'input[placeholder^="z.B."]' }, wert: 'Marktbeobachtung', text: { titel: 'Namen vergeben', erklaerung: 'Symbol, Farbe und Schlagwort sind optional.' } },
  { klick: { rolle: 'button', name: 'Agent erstellen' }, text: { titel: 'Agent erstellen', erklaerung: 'Der Agent bekommt seinen eigenen Rechner und ist nach Sekunden bereit.' } },
  { erwarten: { rolle: 'heading', name: 'Marktbeobachtung' } },
  { zeigen: { rolle: 'heading', name: 'Marktbeobachtung' }, text: { titel: 'Der neue Agent', erklaerung: 'Gleich im Chat loslegen — oder ihm eine Aufgabe geben.' }, halten: 3500 },
  { karte: { titel: 'Fertig', untertitel: 'Der Agent erscheint ab jetzt unter „Agenten“.' }, halten: 3000, bleiben: true },
] };
