import { abschnitt, basis, vorbereitung } from './gemeinsam.mjs';
// Nebenwirkung: legt den Agenten „Marktbeobachtung“ an — nach der Aufnahme wieder löschen (README).
export default { ...basis, vorbereitung: vorbereitung('/agents'), schritte: [
  ...abschnitt('neu', { titel: '„Neuer Agent“', erklaerung: 'Oben rechts auf der Seite „Agenten“.' }, [
    { zeigen: { rolle: 'button', name: 'Neuer Agent' } }, { klick: { rolle: 'button', name: 'Neuer Agent' } }]),
  ...abschnitt('vorlage', { titel: 'Vorlage wählen', erklaerung: 'Vorlagen bringen Rolle, Werkzeuge und Rechte schon mit.' }, [
    { scroll: { rolle: 'button', name: /^Research Assistant/ } }, { klick: { rolle: 'button', name: /^Research Assistant/ } }]),
  ...abschnitt('name', { titel: 'Namen vergeben', erklaerung: 'Symbol, Farbe und Schlagwort sind optional.' }, [
    { tippen: { css: 'input[placeholder^="z.B."]' }, wert: 'Marktbeobachtung' }]),
  { klick: { rolle: 'button', name: 'Agent erstellen' }, ton: 'erstellen', text: { titel: 'Agent erstellen', erklaerung: 'Der Agent bekommt seinen eigenen Rechner und ist nach Sekunden bereit.' }, halten: 400 },
  { erwarten: { rolle: 'heading', name: 'Marktbeobachtung' }, raffen: 3 },
  ...abschnitt('fertig', { titel: 'Der neue Agent', erklaerung: 'Gleich im Chat loslegen — oder ihm eine Aufgabe geben.' }, [
    { zeigen: { rolle: 'heading', name: 'Marktbeobachtung' } }]),
] };
