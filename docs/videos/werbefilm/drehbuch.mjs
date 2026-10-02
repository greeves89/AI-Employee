// Werbefilm der Landingpage: nutzenorientierter Rundgang, in dem die Aktionen laufen, WÄHREND gesprochen
// wird. Über der Einblendung steht „AI Employee“ statt einer Schrittzählung. Sprache über FILM_SPRACHE
// (de | en); in der englischen Fassung bleibt die Oberfläche deutsch, Ton und Einblendungen sind englisch.
// Logo-Intro und -Abschluss setzt docs/videos/film.py davor und dahinter (Sprechtexte in film-<sprache>.json).
import { abschnitt, basis, vorbereitung } from '../../benutzerhandbuch/tutorials/gemeinsam.mjs';

const T = {
  de: {
    agenten: ['Jeder Agent hat eine Rolle', 'Eigener Rechner, eigene Werkzeuge, eigenes Gedächtnis.'],
    auftrag: ['Auftrag in Alltagssprache', 'Der Agent recherchiert, schreibt und liefert ins Chatfenster.'],
    nachhaken: ['Nachhaken jederzeit', 'Wie bei einem Kollegen.'],
    tippen: 'Mach aus Idee 2 einen fertigen Post für Freitag.',
    kontrolle: ['Du behältst die Kontrolle', 'Je Fähigkeit: erlaubt, nur nach Freigabe – oder verboten.'],
    stufen: ['Autonomie in vier Stufen', 'Von „nur lesen“ bis „vollständig autonom“.'],
    freigaben: ['Heikles wartet auf dich', 'Alle Freigaben an einer Stelle.'],
    aufgaben: ['Einmal planen, laufend erledigt', 'Zeitpläne für wiederkehrende Arbeit – jedes Ergebnis nachvollziehbar.'],
    dashboard: ['Leistung und Kosten im Blick', 'Budgets deckeln die Ausgaben automatisch.'],
  },
  en: {
    agenten: ['Every agent has a role', 'Its own computer, its own tools, its own memory.'],
    auftrag: ['Tasks in plain language', 'The agent researches, writes and delivers right in the chat.'],
    nachhaken: ['Follow up anytime', 'Just like with a colleague.'],
    tippen: 'Turn idea 2 into a finished post for Friday.',
    kontrolle: ['You stay in control', 'Per capability: allowed, only with approval – or blocked.'],
    stufen: ['Four levels of autonomy', 'From “read only” to “fully autonomous”.'],
    freigaben: ['Sensitive steps wait for you', 'All approvals in one place.'],
    aufgaben: ['Plan once, done on schedule', 'Schedules for recurring work – every result traceable.'],
    dashboard: ['Output and costs at a glance', 'Budgets cap spending automatically.'],
  },
}[process.env.FILM_SPRACHE || 'de'];
const text = (n) => ({ titel: T[n][0], erklaerung: T[n][1] });

export default {
  ...basis, size: [1920, 1080], zoom: 1.5, marke: 'AI Employee', vorbereitung: vorbereitung('/dashboard'),
  schritte: [
    ...abschnitt('agenten', text('agenten'), [
      { klick: { rolle: 'link', name: 'Agenten' } }, { zeigen: { rolle: 'link', name: /^Marketing/ } }, { zeigen: { rolle: 'link', name: /^Recherche/ } },
      { zeigen: { rolle: 'link', name: /^Assistenz/ } }, { klick: { rolle: 'link', name: /^Marketing/ } }]),
    ...abschnitt('auftrag', text('auftrag'), [
      { klick: { text: /Schreib mir drei kurze Ideen/ } }, { scroll: { text: /Karussell/ } }, { scroll: { text: /Behind-the-Scenes/ } }]),
    ...abschnitt('nachhaken', text('nachhaken'), [{ tippen: { css: 'textarea' }, wert: T.tippen }]),
    ...abschnitt('kontrolle', text('kontrolle'), [
      { klick: { rolle: 'button', name: 'Rechte' } }, { zeigen: { text: 'Dateien lesen' } }, { scroll: { text: 'E-Mail / M365 senden' } }]),
    ...abschnitt('stufen', text('stufen'), [
      { scroll: { rolle: 'button', name: 'L2 — Empfehlungen' } }, { klick: { rolle: 'button', name: 'L3 — Ausführen mit Freigabe' } }]),
    { klick: { rolle: 'button', name: 'Schließen' }, einblendungWeg: true, halten: 0 },
    ...abschnitt('freigaben', text('freigaben'), [{ klick: { rolle: 'link', name: 'Freigaben' } }, { klick: { text: /^Eskalationen/ } }]),
    ...abschnitt('aufgaben', text('aufgaben'), [
      { klick: { rolle: 'link', name: 'Aufgaben' } }, { klick: { rolle: 'button', name: 'Zeitpläne' } }, { klick: { rolle: 'button', name: 'Einzelaufgaben' } },
      { klick: { rolle: 'button', name: /^Erledigt/ } }, { klick: { text: 'Wettbewerber-Übersicht Coworking in Potsdam' } }]),
    ...abschnitt('dashboard', text('dashboard'), [
      { klick: { rolle: 'link', name: 'Dashboard' } }, { zeigen: { text: 'Aktive Agenten' } }, { zeigen: { text: 'Erledigt' } },
      { zeigen: { text: 'Kosten gesamt' }, spot: { text: 'Kosten gesamt' } }]),
  ],
};
