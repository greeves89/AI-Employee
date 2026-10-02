import { AGENT_ID, AGENT_NAME, basis, vorbereitung } from './gemeinsam.mjs';
export default { ...basis, vorbereitung: vorbereitung('/agents/' + AGENT_ID), schritte: [
  { karte: { titel: 'Rechte eines Agenten einstellen', untertitel: 'Was er selbst darf — und was eine Freigabe braucht' }, halten: 3000 },
  { klick: { rolle: 'button', name: 'Rechte' }, text: { titel: '„Rechte“ öffnen', erklaerung: 'In der Leiste unter dem Chat.' } },
  { zeigen: { text: 'Autonomie-Level' }, spot: { text: 'Autonomie-Level' }, text: { titel: 'Vier Stufen', erklaerung: 'Von „nur lesen“ bis „vollständig autonom“.' } },
  { klick: { rolle: 'button', name: 'L2 — Empfehlungen' }, text: { titel: 'Stufe wählen', erklaerung: 'L2: Der Agent schlägt vor, du entscheidest.' } },
  { scroll: { text: 'E-Mail / M365 senden' }, text: { titel: 'Feiner je Fähigkeit', erklaerung: 'Erlaubt, Freigabe oder verboten — z. B. für E-Mails nach außen.' } },
  { scroll: { rolle: 'button', name: 'L3 — Ausführen mit Freigabe' } },
  { klick: { rolle: 'button', name: 'L3 — Ausführen mit Freigabe' }, text: { titel: 'Zurück auf L3', erklaerung: 'Ausführen mit Freigabe: der übliche Mittelweg.' } },
  { klick: { rolle: 'button', name: 'Schließen' }, einblendungWeg: true },
  { karte: { titel: 'Fertig', untertitel: 'Änderungen gelten ab der nächsten Aufgabe des Agenten.' }, halten: 3000, bleiben: true },
] };
