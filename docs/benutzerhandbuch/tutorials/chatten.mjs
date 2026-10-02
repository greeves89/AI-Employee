import { AGENT_ID, AGENT_NAME, abschnitt, basis, vorbereitung } from './gemeinsam.mjs';
export default { ...basis, vorbereitung: vorbereitung('/agents', { agent: AGENT_ID }), schritte: [
  ...abschnitt('oeffnen', { titel: 'Agent öffnen', erklaerung: 'Unter „Agenten“ auf einen Agenten klicken — der Chat öffnet sich.' }, [
    { zeigen: { rolle: 'link', name: new RegExp('^' + AGENT_NAME) } }, { klick: { rolle: 'link', name: new RegExp('^' + AGENT_NAME) } }]),
  ...abschnitt('gespraech', { titel: 'Neues Gespräch beginnen', erklaerung: 'Jedes Thema bekommt sein eigenes Gespräch.' }, [
    { klick: { rolle: 'button', name: 'Neues Gespräch' } }]),
  ...abschnitt('auftrag', { titel: 'Auftrag in Alltagssprache', erklaerung: 'Einfach schreiben, was der Agent tun soll.' }, [
    { tippen: { css: 'textarea' }, wert: 'Schreib mir drei kurze Ideen für einen Instagram-Post zu unserer neuen Herbstkarte im Café.' }]),
  // Der Satz zu „Senden“ läuft über die Wartezeit weiter; die Wartezeit selbst erscheint im Film als Zeitraffer.
  { klick: { css: 'button.h-9.w-9.bg-primary' }, ton: 'senden', text: { titel: 'Senden', erklaerung: 'Der Agent denkt nach und arbeitet mit seinen Werkzeugen.' }, halten: 400 },
  { warten: 20000, raffen: 5 },
  ...abschnitt('antwort', { titel: 'Die Antwort steht im Chat', erklaerung: 'Nachfragen? Einfach weiterschreiben — der Agent kennt das Gespräch.' }, [
    { zeigen: { css: 'textarea' } }]),
] };
