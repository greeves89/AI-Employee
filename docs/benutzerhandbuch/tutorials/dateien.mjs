import { AGENT_ID, AGENT_NAME, basis, vorbereitung } from './gemeinsam.mjs';
export default { ...basis, vorbereitung: vorbereitung('/files'), schritte: [
  { karte: { titel: 'Dateien und Ergebnisse finden', untertitel: 'Alles, was deine Agenten anlegen' }, halten: 3000 },
  { zeigen: { rolle: 'heading', name: 'Explorer' }, text: { titel: '„Dateien“', erklaerung: 'In der Seitenleiste unter „Arbeitsplatz“.' } },
  { klick: { text: AGENT_NAME }, text: { titel: 'Agent aufklappen', erklaerung: 'Jeder Agent hat seinen eigenen Arbeitsbereich.' } },
  { klick: { text: 'knowledge.md' }, text: { titel: 'Datei anklicken', erklaerung: 'Rechts erscheint die Vorschau — herunterladen geht von dort.' } },
  { warten: 1500 },
  { zeigen: { text: 'knowledge.md' }, text: { titel: 'Auch im Chat', erklaerung: 'Im Agenten öffnet der Ordner-Knopf unter dem Chat dieselben Dateien.' }, halten: 3000 },
  { karte: { titel: 'Fertig', untertitel: 'Ergebnisse von Aufgaben findest du auch direkt in der Aufgabe.' }, halten: 3000, bleiben: true },
] };
