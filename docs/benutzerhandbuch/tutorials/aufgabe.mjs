import { abschnitt, basis, vorbereitung } from './gemeinsam.mjs';
export default { ...basis, vorbereitung: vorbereitung('/tasks'), schritte: [
  ...abschnitt('neu', { titel: '„Neue Aufgabe“', erklaerung: 'Aufgaben laufen im Hintergrund weiter — auch wenn du die Seite schließt.' }, [
    { zeigen: { rolle: 'link', name: 'Neue Aufgabe' } }, { klick: { rolle: 'link', name: 'Neue Aufgabe' } }]),
  ...abschnitt('titel', { titel: 'Kurz benennen', erklaerung: 'Der Titel erscheint in der Aufgabenliste.' }, [
    { tippen: { css: 'input[placeholder="Kurz: Worum geht es?"]' }, wert: 'Wochenbericht Café' }]),
  ...abschnitt('auftrag', { titel: 'Auftrag beschreiben', erklaerung: 'Je genauer, desto besser das Ergebnis.' }, [
    { einfuegen: { css: 'textarea' }, wert: 'Erstelle aus den Notizen im Wissen einen kurzen Wochenbericht für das Café-Team: drei Erfolge, zwei offene Punkte.' }]),
  ...abschnitt('agent', { titel: 'Agent wählen — oder automatisch', erklaerung: 'Ohne Auswahl sucht die Plattform den passenden Agenten.' }, [
    { zeigen: { css: 'select' } }]),
  ...abschnitt('probelauf', { titel: 'Probelauf einschalten', erklaerung: 'Der Agent schreibt erst nur einen Plan und führt nichts aus.' }, [
    { klick: { rolle: 'button', name: /^Dry-Run/ } }]),
  ...abschnitt('vorschau', { titel: 'Vorschau erstellen', erklaerung: 'Passt der Plan, startest du ihn mit einem Klick wirklich.' }, [
    { klick: { rolle: 'button', name: 'Vorschau erstellen' } }]),
] };
