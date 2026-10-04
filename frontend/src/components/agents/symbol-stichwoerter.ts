/** Deutsche Stichwörter für die Symbolsuche (#902).
 *
 *  lucide benennt seine Symbole englisch — „LKW“ fand nichts, „truck“ schon.
 *  Hier steht je deutschem Begriff, welche Namensteile (klein, ohne Bindestrich)
 *  gemeint sind. Die Suche nimmt jeden Begriff, der mit der Eingabe beginnt, und
 *  zeigt alle Symbole, deren Name einen dieser Teile enthält — zusätzlich zur
 *  englischen Namenssuche.
 *
 *  Liegt neben ``lucide-catalog.tsx`` und wird nur von dort geladen, also erst,
 *  wenn jemand wirklich sucht.
 */
export const SYMBOL_STICHWOERTER: Record<string, string[]> = {
  // Fahrzeuge & Logistik
  lkw: ["truck"],
  lastwagen: ["truck"],
  transport: ["truck", "forklift", "container"],
  spedition: ["truck", "container", "package"],
  auto: ["car"],
  fahrzeug: ["car", "truck", "bus"],
  bus: ["bus"],
  zug: ["train"],
  bahn: ["train"],
  flugzeug: ["plane"],
  schiff: ["ship"],
  fahrrad: ["bike"],
  traktor: ["tractor"],
  landwirtschaft: ["tractor", "sprout", "wheat"],
  gabelstapler: ["forklift"],
  lager: ["warehouse", "boxes", "archive"],
  paket: ["package", "box"],
  lieferung: ["package", "truck"],
  versand: ["send", "package", "truck"],
  tankstelle: ["fuel"],
  // Geld & Büro
  geld: ["banknote", "euro", "coins", "wallet", "piggybank"],
  euro: ["euro"],
  rechnung: ["receipt", "filetext"],
  buchhaltung: ["calculator", "receipt", "bookopen"],
  rechner: ["calculator"],
  taschenrechner: ["calculator"],
  bank: ["landmark", "piggybank"],
  sparschwein: ["piggybank"],
  kreditkarte: ["creditcard"],
  bezahlen: ["creditcard", "wallet", "banknote"],
  geldbörse: ["wallet"],
  einkauf: ["shoppingcart", "shoppingbag", "store"],
  einkaufswagen: ["shoppingcart"],
  warenkorb: ["shoppingcart", "shoppingbasket"],
  laden: ["store"],
  geschäft: ["store", "briefcase"],
  aktentasche: ["briefcase"],
  vertrieb: ["handshake", "trendingup", "briefcase"],
  verkauf: ["handshake", "trendingup", "tag"],
  marketing: ["megaphone", "target"],
  werbung: ["megaphone"],
  personal: ["users", "idcard", "usercheck"],
  ausweis: ["idcard"],
  vertrag: ["filepen", "handshake", "signature"],
  diagramm: ["chart"],
  statistik: ["chart", "trendingup"],
  auswertung: ["chart", "presentation"],
  präsentation: ["presentation"],
  zeitung: ["newspaper"],
  nachrichten: ["newspaper", "message"],
  // Kommunikation
  brief: ["mail"],
  post: ["mail", "inbox"],
  email: ["mail"],
  telefon: ["phone"],
  handy: ["smartphone"],
  nachricht: ["message"],
  sprechblase: ["message"],
  mikrofon: ["mic"],
  kopfhörer: ["headphones"],
  glocke: ["bell"],
  benachrichtigung: ["bell"],
  // Zeit
  kalender: ["calendar"],
  termin: ["calendar", "clock"],
  uhr: ["clock", "alarmclock", "watch"],
  zeit: ["clock", "hourglass", "timer"],
  // Gebäude & Orte
  haus: ["house"],
  gebäude: ["building"],
  firma: ["building", "factory"],
  fabrik: ["factory"],
  produktion: ["factory", "cog"],
  krankenhaus: ["hospital"],
  schule: ["school", "graduationcap"],
  bildung: ["graduationcap", "school", "book"],
  schulung: ["graduationcap", "presentation"],
  welt: ["globe", "earth"],
  globus: ["globe"],
  karte: ["map"],
  ort: ["mappin"],
  baustelle: ["construction", "hardhat"],
  bau: ["construction", "hardhat", "hammer"],
  // Technik & IT
  werkzeug: ["wrench", "hammer"],
  schraubenschlüssel: ["wrench"],
  zahnrad: ["cog", "settings"],
  einstellungen: ["settings", "slidershorizontal"],
  computer: ["monitor", "laptop", "computer"],
  bildschirm: ["monitor"],
  laptop: ["laptop"],
  drucker: ["printer"],
  server: ["server"],
  datenbank: ["database"],
  netzwerk: ["network", "wifi"],
  wlan: ["wifi"],
  roboter: ["bot"],
  gehirn: ["brain"],
  ki: ["bot", "brain", "sparkles"],
  code: ["code", "terminal"],
  programm: ["code", "appwindow"],
  stecker: ["plug"],
  strom: ["zap", "plug", "power"],
  batterie: ["battery"],
  rakete: ["rocket"],
  // Sicherheit & Recht
  schloss: ["lock"],
  schlüssel: ["key"],
  sicherheit: ["shield", "lock"],
  schild: ["shield"],
  recht: ["scale", "gavel"],
  justiz: ["gavel", "scale"],
  waage: ["scale"],
  // Dateien & Ablage
  datei: ["file"],
  dokument: ["file", "filetext"],
  ordner: ["folder"],
  archiv: ["archive"],
  liste: ["list"],
  aufgabe: ["clipboard", "listtodo", "listchecks"],
  haken: ["check"],
  suche: ["search"],
  lupe: ["search", "zoomin"],
  buch: ["book"],
  stift: ["pen", "pencil"],
  schere: ["scissors"],
  papierkorb: ["trash"],
  // Menschen & Gesundheit
  person: ["user"],
  nutzer: ["user"],
  mensch: ["user", "personstanding"],
  team: ["users"],
  gruppe: ["users"],
  baby: ["baby"],
  arzt: ["stethoscope"],
  medizin: ["stethoscope", "pill", "syringe"],
  gesundheit: ["heartpulse", "stethoscope"],
  pflege: ["heartpulse", "handheart"],
  herz: ["heart"],
  auge: ["eye"],
  daumen: ["thumbsup"],
  // Natur & Alltag
  sonne: ["sun"],
  mond: ["moon"],
  wolke: ["cloud"],
  wetter: ["cloud", "sun", "umbrella", "thermometer"],
  regen: ["cloudrain", "umbrella"],
  wasser: ["droplet", "waves"],
  feuer: ["flame"],
  flamme: ["flame"],
  blitz: ["zap"],
  baum: ["tree"],
  blatt: ["leaf"],
  pflanze: ["sprout", "leaf", "flower"],
  blume: ["flower"],
  berg: ["mountain"],
  hund: ["dog"],
  katze: ["cat"],
  vogel: ["bird"],
  fisch: ["fish"],
  kaffee: ["coffee"],
  essen: ["utensils", "pizza", "apple"],
  restaurant: ["utensils", "chefhat"],
  getränk: ["wine", "beer", "cupsoda"],
  // Zeichen & Formen
  stern: ["star"],
  pfeil: ["arrow"],
  ziel: ["target", "flag"],
  fahne: ["flag"],
  flagge: ["flag"],
  preis: ["award", "trophy", "tag"],
  pokal: ["trophy"],
  geschenk: ["gift"],
  warnung: ["trianglealert", "octagonalert"],
  hinweis: ["info", "lightbulb"],
  idee: ["lightbulb"],
  glühbirne: ["lightbulb"],
  kamera: ["camera"],
  bild: ["image"],
  foto: ["camera", "image"],
  video: ["video"],
  musik: ["music"],
  farbe: ["palette", "paintbrush"],
  pinsel: ["paintbrush"],
  kleidung: ["shirt"],
  bett: ["bed"],
  kompass: ["compass"],
  anker: ["anchor"],
  schirm: ["umbrella"],
  thermometer: ["thermometer"],
  teilen: ["share"],
  link: ["link"],
  posteingang: ["inbox"],
  senden: ["send"],
};

/** Abkürzungen und Schreibweisen, die nicht einfach groß anfangen. */
const ANZEIGE_SONDERFAELLE: Record<string, string> = {
  lkw: "LKW", ki: "KI", wlan: "WLAN", email: "E-Mail",
};

/** Deutscher Anzeigename eines lucide-Symbols (#902) — für den Tooltip.
 *  Nimmt das erste Stichwort, dessen Liste genau diesen Namen enthält
 *  („Truck“ → „LKW“). Ohne Treffer bleibt der lucide-Name stehen; die interne
 *  Kennung ändert sich in keinem Fall. */
export function symbolAnzeigename(name: string): string {
  const kennung = name.toLowerCase().replace(/-/g, "");
  for (const [wort, liste] of Object.entries(SYMBOL_STICHWOERTER)) {
    if (liste.includes(kennung)) {
      return ANZEIGE_SONDERFAELLE[wort] ?? wort.charAt(0).toUpperCase() + wort.slice(1);
    }
  }
  return name;
}

/** Namensteile zu einer Eingabe — aus allen Stichwörtern, die mit ihr beginnen.
 *  Ab zwei Zeichen, sonst trifft „a“ die halbe Liste. */
export function stichwortTeile(eingabe: string): string[] {
  const q = eingabe.trim().toLowerCase();
  if (q.length < 2) return [];
  const teile = new Set<string>();
  for (const [wort, liste] of Object.entries(SYMBOL_STICHWOERTER)) {
    if (wort.startsWith(q)) liste.forEach((t) => teile.add(t));
  }
  return Array.from(teile);
}
