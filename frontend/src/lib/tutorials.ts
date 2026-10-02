/**
 * Klick-Tutorials: EINE Liste fuer das Willkommensfenster beim ersten Start und
 * fuer Hilfe & FAQ. Die Videos (mit Sprecher, Schritt-Einblendungen und
 * Untertiteln) liegen in public/hilfe/ und werden mit dem Produkt ausgeliefert —
 * auch Anlagen ohne Internet zeigen sie. Aufgenommen am Demokonto mit erfundenen
 * Inhalten; Drehbuecher und Sprechtexte in docs/benutzerhandbuch/tutorials/.
 */
export type Tutorial = {
  id: string;
  titel: string;
  kurz: string;
  dauer: string;
};

export const TUTORIALS: Tutorial[] = [
  { id: "chatten", titel: "Mit einem Agenten chatten", kurz: "Agent öffnen, Auftrag schreiben, Antwort lesen.", dauer: "0:40" },
  { id: "agent-anlegen", titel: "Neuen Agenten anlegen", kurz: "Vorlage wählen, Namen und Symbol vergeben.", dauer: "0:36" },
  { id: "aufgabe", titel: "Aufgabe vergeben", kurz: "Mit Probelauf: erst den Plan ansehen, dann starten.", dauer: "0:43" },
  { id: "rechte", titel: "Rechte eines Agenten einstellen", kurz: "Was der Agent selbst darf und was eine Freigabe braucht.", dauer: "0:44" },
  { id: "dateien", titel: "Dateien und Ergebnisse finden", kurz: "Wo die Dateien deiner Agenten liegen.", dauer: "0:34" },
];

export const tutorialVideo = (id: string) => `/hilfe/${id}.mp4`;
export const tutorialPoster = (id: string) => `/hilfe/${id}.jpg`;
export const tutorialUntertitel = (id: string) => `/hilfe/${id}.vtt`;
export const tutorialFinden = (id: string) => TUTORIALS.find((t) => t.id === id);
