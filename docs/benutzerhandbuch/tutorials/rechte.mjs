import { AGENT_ID, abschnitt, basis, vorbereitung } from './gemeinsam.mjs';
export default { ...basis, vorbereitung: vorbereitung('/agents/' + AGENT_ID), schritte: [
  ...abschnitt('oeffnen', { titel: '„Rechte“ öffnen', erklaerung: 'In der Leiste unter dem Chat.' }, [
    { zeigen: { rolle: 'button', name: 'Rechte' } }, { klick: { rolle: 'button', name: 'Rechte' } }]),
  ...abschnitt('stufen', { titel: 'Vier Stufen', erklaerung: 'Von „nur lesen“ bis „vollständig autonom“.' }, [
    { zeigen: { text: 'Autonomie-Level' }, spot: { text: 'Autonomie-Level' } }, { zeigen: { rolle: 'button', name: 'L4 — Vollständig autonom' } }]),
  ...abschnitt('l2', { titel: 'Stufe wählen', erklaerung: 'L2: Der Agent schlägt vor, du entscheidest.' }, [
    { klick: { rolle: 'button', name: 'L2 — Empfehlungen' } }]),
  ...abschnitt('faehigkeiten', { titel: 'Feiner je Fähigkeit', erklaerung: 'Erlaubt, Freigabe oder verboten — z. B. für E-Mails nach außen.' }, [
    { scroll: { text: 'Dateien schreiben' } }, { scroll: { text: 'E-Mail / M365 senden' } }, { zeigen: { text: 'E-Mail / M365 senden' } }]),
  ...abschnitt('l3', { titel: 'Zurück auf L3', erklaerung: 'Ausführen mit Freigabe: der übliche Mittelweg.' }, [
    { scroll: { rolle: 'button', name: 'L3 — Ausführen mit Freigabe' } }, { klick: { rolle: 'button', name: 'L3 — Ausführen mit Freigabe' } }]),
  { klick: { rolle: 'button', name: 'Schließen' }, einblendungWeg: true, halten: 600 },
] };
