/** Werkzeugnamen, wie Nutzer sie lesen — für die einfache Ansicht.
 *
 * Im Chat standen bis v1.342 die Rohnamen der Laufzeiten: „Bash", „Read",
 * „mcp__orchestrator__list_todos". Für Admins bleiben sie (sie suchen damit im
 * Protokoll), Mitglieder sehen, WAS der Agent tut. Unbekannte Werkzeuge fallen
 * auf ihren lesbar gemachten Kurznamen zurück, nie auf einen leeren Eintrag.
 */

const NAMEN: Record<string, string> = {
  bash: "Befehl ausgeführt",
  shell: "Befehl ausgeführt",
  exec_command: "Befehl ausgeführt",
  read: "Datei gelesen",
  write: "Datei geschrieben",
  edit: "Datei bearbeitet",
  multiedit: "Datei bearbeitet",
  apply_patch: "Datei bearbeitet",
  grep: "Dateien durchsucht",
  glob: "Dateien gesucht",
  websearch: "Im Web gesucht",
  web_search: "Im Web gesucht",
  webfetch: "Webseite gelesen",
  web_fetch: "Webseite gelesen",
  task: "Helfer beauftragt",
  agent: "Helfer beauftragt",
  todowrite: "Arbeitsliste aktualisiert",
  toolsearch: "Passendes Werkzeug gesucht",
  list_todos: "To-dos abgerufen",
  create_todo: "To-do angelegt",
  add_todo: "To-do angelegt",
  update_todo: "To-do aktualisiert",
  complete_todo: "To-do erledigt",
  create_task: "Aufgabe angelegt",
  get_task_status: "Aufgabe geprüft",
  create_schedule: "Zeitplan angelegt",
  list_schedules: "Zeitpläne abgerufen",
  memory_save: "Gemerkt",
  memory_search: "Im Gedächtnis gesucht",
  memory_list: "Gedächtnis durchgesehen",
  memory_delete: "Erinnerung gelöscht",
  notify_user: "Benachrichtigung gesendet",
  request_approval: "Freigabe angefragt",
  send_message: "Nachricht an Kollegen",
  list_team: "Team abgerufen",
  knowledge_search: "Wissen durchsucht",
  present_file: "Datei bereitgestellt",
};

export function werkzeugAufDeutsch(tool: string | null | undefined): string {
  const roh = (tool || "").trim();
  if (!roh) return "Werkzeug";
  // mcp__server__name → name
  const kurz = roh.includes("__") ? roh.split("__").pop() || roh : roh;
  const bekannt = NAMEN[kurz.toLowerCase()];
  if (bekannt) return bekannt;
  const lesbar = kurz.replace(/[_-]+/g, " ").trim();
  return lesbar.charAt(0).toUpperCase() + lesbar.slice(1);
}

/** Kurze Anzeigenamen für die ausführliche Ansicht (Admins).
 *
 * Dort stand bisher der Rohname der Laufzeit („Edit", „Task", „TodoWrite").
 * Gezeigt wird jetzt ein deutsches Wort; die interne Kennung bleibt unverändert
 * und steht im Tooltip — wer im Protokoll sucht, findet sie dort.
 */
const KURZNAMEN: Record<string, string> = {
  bash: "Befehl",
  shell: "Befehl",
  exec_command: "Befehl",
  read: "Lesen",
  write: "Schreiben",
  edit: "Bearbeiten",
  multiedit: "Bearbeiten",
  apply_patch: "Bearbeiten",
  notebookedit: "Notebook bearbeiten",
  grep: "Textsuche",
  glob: "Dateisuche",
  websearch: "Websuche",
  web_search: "Websuche",
  webfetch: "Webseite",
  web_fetch: "Webseite",
  task: "Helfer",
  agent: "Helfer",
  todowrite: "Arbeitsliste",
  toolsearch: "Werkzeugsuche",
};

export function werkzeugKurzname(tool: string | null | undefined): string {
  const roh = (tool || "").trim();
  const kurz = roh.includes("__") ? roh.split("__").pop() || roh : roh;
  return KURZNAMEN[kurz.toLowerCase()] ?? werkzeugAufDeutsch(roh);
}
