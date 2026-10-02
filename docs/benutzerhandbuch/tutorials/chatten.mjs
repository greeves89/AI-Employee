import { AGENT_ID, AGENT_NAME, basis, vorbereitung } from './gemeinsam.mjs';
export default { ...basis, vorbereitung: vorbereitung('/agents', { agent: AGENT_ID }), schritte: [
  { karte: { titel: 'Mit einem Agenten chatten', untertitel: 'Auftrag schreiben, Antwort lesen' }, halten: 3000 },
  { klick: { rolle: 'link', name: new RegExp('^' + AGENT_NAME) }, text: { titel: 'Agent öffnen', erklaerung: 'Unter „Agenten“ auf einen Agenten klicken — der Chat öffnet sich.' } },
  { klick: { rolle: 'button', name: 'Neues Gespräch' }, text: { titel: 'Neues Gespräch beginnen', erklaerung: 'Jedes Thema bekommt sein eigenes Gespräch.' } },
  { tippen: { css: 'textarea' }, wert: 'Schreib mir drei kurze Ideen für einen Instagram-Post zu unserer neuen Herbstkarte im Café.', text: { titel: 'Auftrag in Alltagssprache', erklaerung: 'Einfach schreiben, was der Agent tun soll.' } },
  { klick: { css: 'button.h-9.w-9.bg-primary' }, text: { titel: 'Senden', erklaerung: 'Der Agent denkt nach und arbeitet mit seinen Werkzeugen.' } },
  { warten: 20000 },
  { zeigen: { css: 'textarea' }, text: { titel: 'Die Antwort steht im Chat', erklaerung: 'Nachfragen? Einfach weiterschreiben — der Agent kennt das Gespräch.' }, halten: 4000 },
  { karte: { titel: 'Fertig', untertitel: 'Längere Arbeit lieber als Aufgabe vergeben — siehe nächstes Video.' }, halten: 3000, bleiben: true },
] };
