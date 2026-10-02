import { AGENT_NAME, abschnitt, basis, vorbereitung } from './gemeinsam.mjs';
export default { ...basis, vorbereitung: vorbereitung('/files'), schritte: [
  ...abschnitt('explorer', { titel: '„Dateien“', erklaerung: 'In der Seitenleiste unter „Arbeitsplatz“.' }, [
    { zeigen: { rolle: 'link', name: 'Dateien' } }, { zeigen: { rolle: 'heading', name: 'Explorer' } }]),
  ...abschnitt('ordner', { titel: 'Agent aufklappen', erklaerung: 'Jeder Agent hat seinen eigenen Arbeitsbereich.' }, [
    { klick: { text: AGENT_NAME } }]),
  ...abschnitt('datei', { titel: 'Datei anklicken', erklaerung: 'Rechts erscheint die Vorschau — herunterladen geht von dort.' }, [
    { klick: { text: 'knowledge.md' } }]),
  ...abschnitt('chat', { titel: 'Auch im Chat', erklaerung: 'Im Agenten öffnet der Ordner-Knopf unter dem Chat dieselben Dateien.' }, [
    { zeigen: { text: 'knowledge.md' } }]),
] };
