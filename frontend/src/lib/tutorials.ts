/**
 * Klick-Tutorials: EINE Liste fuer das Willkommensfenster beim ersten Start und
 * fuer Hilfe & FAQ. Die Videos (ohne Ton, mit Schritt-Einblendungen) liegen in
 * public/hilfe/ und werden mit dem Produkt ausgeliefert — auch Anlagen ohne
 * Internet zeigen sie. Aufgenommen am Demokonto mit erfundenen Inhalten.
 */
export type Tutorial = {
  id: string;
  titel: string;
  kurz: string;
  dauer: string;
};

export const TUTORIALS: Tutorial[] = [
  { id: "chatten", titel: "Mit einem Agenten chatten", kurz: "Agent öffnen, Auftrag schreiben, Antwort lesen.", dauer: "1:17" },
  { id: "agent-anlegen", titel: "Neuen Agenten anlegen", kurz: "Vorlage wählen, Namen und Symbol vergeben.", dauer: "0:54" },
  { id: "aufgabe", titel: "Aufgabe vergeben", kurz: "Mit Probelauf: erst den Plan ansehen, dann starten.", dauer: "1:03" },
  { id: "rechte", titel: "Rechte eines Agenten einstellen", kurz: "Was der Agent selbst darf und was eine Freigabe braucht.", dauer: "0:54" },
  { id: "dateien", titel: "Dateien und Ergebnisse finden", kurz: "Wo die Dateien deiner Agenten liegen.", dauer: "0:40" },
];

export const tutorialVideo = (id: string) => `/hilfe/${id}.mp4`;
export const tutorialPoster = (id: string) => `/hilfe/${id}.jpg`;
export const tutorialFinden = (id: string) => TUTORIALS.find((t) => t.id === id);
