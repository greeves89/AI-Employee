// Fig: So arbeitet AI Employee — gebaut mit dem Standard-Skill „anatomy“
// (agent/skills_standard/anatomy). `node build.mjs` schreibt die dunkle Seite,
// `node build.mjs --light` die helle, beide neben dieses Skript.
//
// Das Objekt ist die Erklärung: eine Rohrpost-Sortieranlage auf einer Grundplatte.
//   Einwurf links        = ein Eingang für alle Kanäle (Chat, Telegram, Teams, Sprache)
//   Schlitten auf Schiene = Orchestrator: bringt den Auftrag zum Agenten des Besitzers
//   drei Kabinen         = Agenten, jeder in seinem eigenen Container
//   Schleuse mit Zeiger  = Prüfung jedes Werkzeugaufrufs gegen die Freigaben
//   Turm mit Stempel     = Egress-Proxy: Platzhalter → echter Schlüssel, nur am erlaubten Ziel
import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as k from "../../../../agent/skills_standard/anatomy/kit/iso-kit.mjs";
import { kitScript } from "../../../../agent/skills_standard/anatomy/scripts/inline-kit.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const theme = process.argv.includes("--light") ? "light" : "dark";
const NAME = "so-arbeitet-ai-employee";

const WIDTH = 640;
const HEIGHT = 420;

const PLATE = { x: -175, y: -72, w: 350, d: 144, r: 9 };
const PLATE_H = 12;
const FOOT_H = 5;

// Kabinen (Agenten) hinten, Schleusen davor, Schiene vorn.
const CELL_X = [-62, 0, 62];
const CELL = { y: -60, w: 40, d: 38, h: 46 };
const GATE_Y = -8;
const GATE_R = 7;
const GATE_Z = 16;
const RAIL_Y = [16, 28];
const RAIL_FROM = -128;
const RAIL_TO = 96;
const CAR = { w: 30, d: 22 };
const CAR_Z = 3;
const CAR_TOP = 9;
const PARK = -112;

const HOPPER = [-150, 26];
const HOPPER_TOP = 34;

const TOWER = { x: 122, y: -46, w: 34, d: 40 };
const TOWER_H = 54;
const STAMP = [139, -26];
const PAD_Z = 20;

const P = k.fitProjection(
  [...k.boxCorners(PLATE, -PLATE_H - FOOT_H, 0), [CELL_X[0] - 20, CELL.y, CELL.h + 10], [TOWER.x + TOWER.w, TOWER.y, TOWER_H + 14]],
  WIDTH,
  HEIGHT,
  { pad: 26, azimuth: 40 },
);

const at = (point) => k.iso(point, P);
const S = (paths, style) => k.solidSvg(paths, style);
const L = (d, style) => k.lineSvg(d, style);
const D = (points, style) => k.dotsSvg(points, style);
const byDepth = (points) => [...points].sort((a, b) => k.depthOf([a[0], a[1], 0], P) - k.depthOf([b[0], b[1], 0], P));

function plate() {
  const out = [`<path class="iso-halo" d="${k.haloOf(PLATE, -PLATE_H - FOOT_H, PLATE_H, P)}"/>`];
  for (const [x, y] of byDepth(k.corners(PLATE, 24))) {
    out.push(S(k.cylinder(x, y, 7, -PLATE_H - FOOT_H, 1.6, P, 28), { tone: "lo" }));
    out.push(S(k.cylinder(x, y, 5.4, -PLATE_H - FOOT_H + 1.6, FOOT_H - 1.6, P, 28), { tone: "mid" }));
  }
  out.push(S(k.slabOf(PLATE, -PLATE_H, PLATE_H, P, 8, 1.6), { tone: "mid" }));
  out.push(L(k.planOutline(k.insetPlan(PLATE, 5), 0, P), { tone: "lo" }));
  const screws = k.corners(PLATE, 11);
  out.push(L(screws.map(([x, y]) => k.ring(x, y, 2.2, 0, P, 16)).join(""), { tone: "lo" }));
  out.push(D(screws.map(([x, y]) => at([x, y, 0])), { size: 0.5 }));
  return out.join("");
}

function frontStrip() {
  const out = [];
  const strip = { x: -150, y: PLATE.y + PLATE.d - 16, w: 300, d: 7, r: 1 };
  out.push(S(k.slabOf(strip, 0, 0.6, P, 3), { tone: "lo", crease: "none" }));
  const ticks = k.topTicks(strip.x + 4, strip.x + strip.w - 4, 4, 5, strip.y + 0.8, 0.6, [2, 3.6], P);
  out.push(L(ticks.minor, { tone: "lo" }), L(ticks.major, { tone: "mid" }));
  out.push(D([at([strip.x + 2, strip.y + 3.5, 0.6]), at([strip.x + strip.w - 2, strip.y + 3.5, 0.6])], { size: 0.45 }));
  // Typenschild mit drei Regellinien und einer kleinen Gravur
  const label = { x: 104, y: PLATE.y + PLATE.d - 34, w: 44, d: 14, r: 1.2 };
  out.push(S(k.slabOf(label, 0, 0.8, P, 3, 0.3), { tone: "mid" }));
  out.push(k.faceTextSvg(k.topMatrix([label.x + 3, label.y + 5], 0.8, P, "x"), "AI EMPLOYEE", { size: 2.6, tone: "hi" }));
  for (const [i, w] of [26, 20, 14].entries()) {
    out.push(L(k.lineOnTop([label.x + 3, label.y + 7.6 + i * 1.9], [label.x + 3 + w, label.y + 7.6 + i * 1.9], 0.8, P), { tone: "faint", free: true }));
  }
  out.push(D([[label.x + 1.6, label.y + 1.6], [label.x + label.w - 1.6, label.y + 1.6], [label.x + 1.6, label.y + label.d - 1.6], [label.x + label.w - 1.6, label.y + label.d - 1.6]].map(([x, y]) => at([x, y, 0.8])), { size: 0.4 }));
  return out.join("");
}

function cell(cx, index) {
  const out = [];
  const base = { x: cx - CELL.w / 2 - 3, y: CELL.y - 3, w: CELL.w + 6, d: CELL.d + 6, r: 2 };
  out.push(`<path class="iso-halo" d="${k.haloOf(base, 0, 4, P)}"/>`);
  out.push(S(k.slabOf(base, 0, 4, P, 4, 0.6), { tone: "lo" }));
  const box = { x: cx - CELL.w / 2, y: CELL.y, w: CELL.w, d: CELL.d, r: 2 };
  // Gehäuse mit umlaufender Deckelnaht: ein Container, abgeschlossen
  out.push(S(k.slabOf(box, 4, CELL.h, P, 4, 0.8), { tone: "mid", inner: L(k.sideSeam(box, 4 + CELL.h - 6, P, 4), { tone: "faint" }) }));
  out.push(L(k.planOutline(k.insetPlan(box, 4), 4 + CELL.h, P, 3), { tone: "faint" }));
  out.push(D(k.corners(box, 2.6).map(([x, y]) => at([x, y, 4 + CELL.h])), { size: 0.45 }));
  // Lüftungsgitter oben und Kennplatte
  out.push(D(k.dotGrid(cx - 9, CELL.y + 8, 7, 3, 3, 4 + CELL.h, P), { size: 0.35, tone: "lo" }));
  const plateTop = { x: cx - 8, y: CELL.y + CELL.d - 12, w: 16, d: 8, r: 1 };
  out.push(S(k.slabOf(plateTop, 4 + CELL.h, 0.6, P, 2, 0.2), { tone: "hi", crease: "none" }));
  out.push(k.faceTextSvg(k.topMatrix([cx, plateTop.y + 5.4], 4 + CELL.h + 0.6, P, "x"), "ABC"[index], { size: 3.4, tone: "lo", anchor: "middle" }));
  // Arbeitslampe: aus und an übereinander, die Seite blendet um
  const lamp = [cx + 12, CELL.y + 9];
  out.push(S(k.cylinder(lamp[0], lamp[1], 3.4, 4 + CELL.h, 1.2, P, 20), { tone: "lo" }));
  out.push(S(k.cylinder(lamp[0], lamp[1], 2.4, 4 + CELL.h + 1.2, 2.6, P, 20), { tone: "mid" }));
  out.push(`<g data-lamp="${index}" style="opacity:0">${S(k.cylinder(lamp[0], lamp[1], 2.4, 4 + CELL.h + 1.2, 2.6, P, 20), { tone: "hi", lit: true })}</g>`);
  // Ausgabestutzen zur Schleuse
  out.push(S(k.slabOf({ x: cx - 4, y: CELL.y + CELL.d, w: 8, d: 6, r: 1 }, 10, 6, P, 2, 0.3), { tone: "mid" }));
  return out.join("");
}

function gate(cx, index) {
  const out = [];
  const foot = { x: cx - 9, y: GATE_Y - 7, w: 18, d: 14, r: 1.6 };
  out.push(S(k.slabOf(foot, 0, 3, P, 3, 0.5), { tone: "lo" }));
  out.push(D(k.corners(foot, 2.2).map(([x, y]) => at([x, y, 3])), { size: 0.4 }));
  out.push(S(k.cylinder(cx, GATE_Y, 2.4, 3, GATE_Z - 3, P, 18), { tone: "mid" }));
  out.push(L(k.sideArc(cx, GATE_Y, 2.4, 9, P, 12), { tone: "lo" }));
  out.push(S(k.cylinder(cx, GATE_Y, GATE_R, GATE_Z, 3, P, 48, 0.5), { tone: "hi", crease: "lo" }));
  out.push(L(k.ring(cx, GATE_Y, GATE_R - 1.6, GATE_Z + 3, P, 48), { tone: "lo" }));
  const ticks = k.radialTicks(cx, GATE_Y, GATE_R - 2, 24, 4, GATE_Z + 3, [0.9, 1.9], P, -Math.PI / 2, Math.PI);
  out.push(L(ticks.minor, { tone: "lo" }), L(ticks.major, { tone: "mid" }));
  out.push(L(k.ring(cx, GATE_Y, 0.9, GATE_Z + 3, P, 12), { tone: "mid" }));
  out.push(`<path data-part="needle-${index}" class="iso-line" data-tone="lit" data-free="start" d="${needleAt(cx, -0.5)}" style="stroke-width:0.9"/>`);
  return out.join("");
}

function needleAt(cx, swing) {
  // swing −0,5 = links (wartet), +0,5 = rechts (erlaubt); 0 Grad zeigt nach −y
  const angle = swing * Math.PI * 0.9;
  return k.lineOnTop([cx - Math.sin(angle) * 1.6, GATE_Y + Math.cos(angle) * 1.6], [cx + Math.sin(angle) * (GATE_R - 2.6), GATE_Y - Math.cos(angle) * (GATE_R - 2.6)], GATE_Z + 3.2, P);
}

function bus() {
  // Von jeder Schleuse eine Leitung zum Sammelkanal, der Kanal zum Proxy-Turm
  const out = [];
  const lane = GATE_Y + 9;
  for (const cx of CELL_X) out.push(k.wireSvg(at([cx + 9, GATE_Y + 4, 1.5]), at([cx + 14, lane, 1.2]), { tone: "lo", ends: "from", size: 0.9 }));
  out.push(k.wireSvg(at([CELL_X[0] + 14, lane, 1.2]), at([TOWER.x + 11, lane, 1.2]), { tone: "mid", flow: true, size: 1 }));
  return out.join("");
}

function riser() {
  // Letztes Stück des Sammelkanals: vom Boden hoch auf den Stutzen am Tisch. Liegt vor dem
  // Turmsockel, darum nach dem Turm gemalt.
  const lane = GATE_Y + 9;
  return k.wireSvg(at([TOWER.x + 11, lane, 1.2]), at([TOWER.x + 11, -2, 10]), { via: [at([TOWER.x + 11, lane, 10])], tone: "mid", ends: "to", size: 1 });
}

function tower() {
  const out = [];
  const base = { x: TOWER.x - 4, y: TOWER.y - 4, w: TOWER.w + 8, d: TOWER.d + 8, r: 2 };
  out.push(`<path class="iso-halo" d="${k.haloOf(base, 0, 5, P)}"/>`);
  out.push(S(k.slabOf(base, 0, 5, P, 4, 0.6), { tone: "lo" }));
  out.push(D(k.corners(base, 2.6).map(([x, y]) => at([x, y, 5])), { size: 0.45 }));
  // Ausgang nach hinten zur Kante, endet auf einer Kupplung — liegt hinter der Säule, also zuerst
  out.push(S(k.slabOf({ x: TOWER.x + 12, y: PLATE.y + 4, w: 10, d: 6, r: 1 }, 0, 7, P, 2, 0.4), { tone: "mid" }));
  out.push(k.wireSvg(at([TOWER.x + 17, TOWER.y, 3]), at([TOWER.x + 17, PLATE.y + 10, 3.5]), { tone: "lo", size: 0.9 }));
  // Säule hinten, Tisch vorn mit Stempelkissen
  const column = { x: TOWER.x, y: TOWER.y, w: TOWER.w, d: 14, r: 1.6 };
  out.push(S(k.slabOf(column, 5, TOWER_H - 5, P, 3, 0.6), { tone: "mid", inner: L(k.sideSeam(column, 30, P, 3), { tone: "faint" }) }));
  const head = { x: TOWER.x - 1, y: TOWER.y + 8, w: TOWER.w + 2, d: 26, r: 2 };
  out.push(S(k.slabOf(head, TOWER_H - 8, 8, P, 4, 0.8), { tone: "mid" }));
  out.push(L(k.planOutline(k.insetPlan(head, 3), TOWER_H, P, 3), { tone: "faint" }));
  // Zielscheibe oben: nur dieses Ziel bekommt den Schlüssel
  const dial = [TOWER.x + 9, TOWER.y + 20];
  out.push(S(k.cylinder(dial[0], dial[1], 6, TOWER_H, 2.4, P, 40, 0.4), { tone: "hi", crease: "lo" }));
  const dt = k.radialTicks(dial[0], dial[1], 4.6, 16, 4, TOWER_H + 2.4, [0.8, 1.6], P);
  out.push(L(dt.minor, { tone: "lo" }), L(dt.major, { tone: "mid" }));
  out.push(L(k.lineOnTop([dial[0], dial[1]], [dial[0] + 3.6, dial[1] - 2.6], TOWER_H + 2.6, P), { tone: "hi", free: "end" }));
  out.push(D([at([dial[0], dial[1], TOWER_H + 2.6])], { size: 0.5, tone: "hi" }));
  const table = { x: TOWER.x + 6, y: TOWER.y + 16, w: 24, d: 22, r: 1.6 };
  out.push(S(k.slabOf(table, 5, PAD_Z - 5, P, 3, 0.6), { tone: "mid" }));
  out.push(S(k.slabOf({ x: TOWER.x + 8, y: table.y + table.d, w: 6, d: 3, r: 0.8 }, 8, 4, P, 2, 0.3), { tone: "mid" }));
  out.push(S(k.slabOf({ x: STAMP[0] - 6, y: STAMP[1] - 5, w: 12, d: 10, r: 1 }, PAD_Z, 1.2, P, 2, 0.3), { tone: "lo" }));
  // Marke auf dem Kissen: Ring = Platzhalter, voller Punkt = echter Schlüssel
  out.push(`<g data-part="token-ring">${L(k.ring(STAMP[0], STAMP[1], 2.2, PAD_Z + 1.2, P, 20), { tone: "hi" })}</g>`);
  out.push(`<g data-part="token-key" style="opacity:0">${D([at([STAMP[0], STAMP[1], PAD_Z + 1.2])], { size: 1.6, tone: "hi" })}</g>`);
  return out.join("");
}

function stamp() {
  // Stößel: wird verschoben, nicht neu gebaut
  const out = [];
  out.push(S(k.cylinder(STAMP[0], STAMP[1], 1.6, PAD_Z + 6, TOWER_H - 8 - PAD_Z - 6, P, 16), { tone: "hi", crease: "none", lit: true }));
  out.push(S(k.cylinder(STAMP[0], STAMP[1], 4, PAD_Z + 3, 3, P, 24, 0.4), { tone: "hi", lit: true }));
  return out.join("");
}

function hopper() {
  const out = [];
  const [x, y] = HOPPER;
  const foot = { x: x - 10, y: y - 10, w: 20, d: 20, r: 2 };
  out.push(`<path class="iso-halo" d="${k.haloOf(foot, 0, 3, P)}"/>`);
  out.push(S(k.slabOf(foot, 0, 3, P, 3, 0.5), { tone: "lo" }));
  out.push(D(k.corners(foot, 2.2).map(([px, py]) => at([px, py, 3])), { size: 0.4 }));
  out.push(S(k.slabOf({ x: x - 5, y: y - 5, w: 10, d: 10, r: 1.2 }, 3, HOPPER_TOP - 9, P, 3, 0.4), { tone: "mid" }));
  out.push(S(k.cylinder(x, y, 9, HOPPER_TOP - 6, 6, P, 40, 0.6), { tone: "mid", crease: "lo" }));
  out.push(L(k.ring(x, y, 6.6, HOPPER_TOP, P, 36), { tone: "lo" }));
  out.push(L(k.knurl(x, y, 9, HOPPER_TOP - 5, HOPPER_TOP - 1, 30, P), { tone: "faint" }));
  // Zuführung zur Schiene
  out.push(S(k.slabOf({ x: x + 8, y: y - 2, w: PARK - x - 12, d: 4, r: 1 }, 6, 2, P, 2), { tone: "lo" }));
  return out.join("");
}

function card() {
  return S(k.slabOf({ x: -5, y: -3.5, w: 10, d: 7, r: 0.6 }, 0, 1, P, 2, 0.2), { tone: "hi", lit: true, crease: "none" });
}

function rails() {
  const out = [];
  for (const y of RAIL_Y) {
    out.push(S(k.slabOf({ x: RAIL_FROM, y, w: RAIL_TO - RAIL_FROM, d: 4, r: 1 }, 0, 3, P, 3, 0.4), { tone: "mid" }));
    out.push(D([RAIL_FROM + 6, (RAIL_FROM + RAIL_TO) / 2, RAIL_TO - 6].map((x) => at([x, y + 2, 3])), { size: 0.4 }));
  }
  for (const x of [RAIL_FROM - 5, RAIL_TO]) {
    out.push(S(k.slabOf({ x, y: RAIL_Y[0] - 3, w: 5, d: RAIL_Y[1] - RAIL_Y[0] + 10, r: 1.2 }, 0, 7, P, 3, 0.5), { tone: "mid" }));
    out.push(D([at([x + 2.5, RAIL_Y[0], 7]), at([x + 2.5, RAIL_Y[1] + 4, 7])], { size: 0.45 }));
  }
  return out.join("");
}

function carriage() {
  const plan = { x: -CAR.w / 2, y: RAIL_Y[0] - 3, w: CAR.w, d: CAR.d, r: 3 };
  const out = [`<path class="iso-halo" d="${k.haloOf(k.insetPlan(plan, 3), CAR_Z, 0.1, P)}" style="filter:blur(4px)"/>`];
  out.push(S(k.slabOf(plan, CAR_Z, CAR_TOP - CAR_Z, P, 5, 0.6), { tone: "mid" }));
  out.push(D([-10, -3.4, 3.4, 10].map((x) => at([x, plan.y + plan.d - 2.4, CAR_TOP])), { size: 0.4, tone: "lo" }));
  // Arm nach hinten bis kurz vor die Schleuse
  const arm = { x: -3, y: GATE_Y + 8, w: 6, d: plan.y - GATE_Y - 6, r: 1.2 };
  out.push(S(k.slabOf(arm, CAR_TOP - 2, 3, P, 2, 0.3), { tone: "mid" }));
  out.push(S(k.slabOf({ x: -6, y: plan.y + 3, w: 12, d: 9, r: 1 }, CAR_TOP, 1.4, P, 2, 0.3), { tone: "lo" }));
  out.push(`<g data-part="car-card" style="opacity:0" transform="${k.translateAlong(P, [0, plan.y + 7.5, CAR_TOP + 1.4])}">${card()}</g>`);
  const knob = [CAR.w / 2 - 5, plan.y + plan.d + 2.6];
  out.push(S(k.cylinder(knob[0], knob[1], 2.6, CAR_Z, 5, P, 20), { tone: "mid" }));
  out.push(L(k.knurl(knob[0], knob[1], 2.6, CAR_Z, CAR_Z + 5, 16, P), { tone: "lo" }));
  return out.join("");
}

const cells = CELL_X.map((cx, i) => cell(cx, i)).join("");
const gates = CELL_X.map((cx, i) => gate(cx, i)).join("");

const svg = k.figureSvg({
  width: WIDTH,
  height: HEIGHT,
  label:
    "AI Employee als Rohrpost-Sortieranlage. Links fällt ein Auftrag in den Einwurf, ein Schlitten bringt ihn zu einer von drei geschlossenen Kabinen (Agenten in eigenen Containern), vor jeder Kabine prüft eine Schleuse jeden Werkzeugaufruf, und alles nach außen geht durch einen Turm rechts, der einen Platzhalter erst dort gegen den echten Schlüssel tauscht.",
  body: [
    plate(),
    cells,
    gates,
    bus(),
    tower(),
    riser(),
    `<g data-part="stamp">${stamp()}</g>`,
    hopper(),
    `<g data-part="hopper-card" transform="${k.translateAlong(P, [HOPPER[0], HOPPER[1], HOPPER_TOP + 10])}">${card()}</g>`,
    rails(),
    `<g data-part="carriage" transform="${k.translateAlong(P, [PARK, 0, 0])}">${carriage()}</g>`,
    frontStrip(),
  ],
});

// Das Modell: sechs Schritte je Auftrag, reihum an die drei Agenten.
const STEPS = [
  { from: 0.0, to: 1.4, text: "1 · Eingang: Chat, Telegram, Teams, Sprache" },
  { from: 1.4, to: 3.6, text: "2 · Orchestrator bringt den Auftrag zu Agent " },
  { from: 3.6, to: 5.6, text: "3 · Agent  arbeitet im eigenen Container" },
  { from: 5.6, to: 7.6, text: "4 · Prüfung: Werkzeug gegen die Freigaben" },
  { from: 7.6, to: 10.0, text: "5 · Proxy setzt den Schlüssel erst am Ziel ein" },
  { from: 10.0, to: 12.0, text: "6 · Antwort zurück an den Nutzer" },
];

const live = `
const P = ${JSON.stringify(P)};
const CELL_X = ${JSON.stringify(CELL_X)}, PARK = ${PARK}, GATE_Y = ${GATE_Y}, GATE_R = ${GATE_R}, GATE_Z = ${GATE_Z};
const HOPPER = ${JSON.stringify(HOPPER)}, HOPPER_TOP = ${HOPPER_TOP};
const STEPS = ${JSON.stringify(STEPS)}, LOOP = 12;
const stage = document.querySelector(".iso-stage");
const svg = stage.querySelector("svg");
const q = (s) => svg.querySelector(s);
const carriage = q('[data-part="carriage"]'), carCard = q('[data-part="car-card"]');
const hopperCard = q('[data-part="hopper-card"]'), stampEl = q('[data-part="stamp"]');
const tokenRing = q('[data-part="token-ring"]'), tokenKey = q('[data-part="token-key"]');
const lamps = [0, 1, 2].map((i) => q('[data-lamp="' + i + '"]'));
const needles = [0, 1, 2].map((i) => q('[data-part="needle-' + i + '"]'));
const readout = document.querySelector("[data-readout]");
const still = matchMedia("(prefers-reduced-motion: reduce)");
const needleAt = ${needleAt.toString().replace("k.lineOnTop", "lineOnTop")};
const ease = (t) => (t <= 0 ? 0 : t >= 1 ? 1 : t * t * (3 - 2 * t));
const span = (t, a, b) => ease((t - a) / (b - a));
let agent = 0, clock = 0, last = 0, frame = 0, visible = false, chosen = null, idle = 0;
const x = { at: PARK, rate: 0 }, swing = [-0.5, -0.5, -0.5], lampOn = [0, 0, 0];
let press = 0, token = 0;
function model(t) {
  const target = CELL_X[agent];
  const goTo = t < 1.4 ? PARK : t < 10.0 ? target : PARK;
  const step = STEPS.findIndex((s) => t >= s.from && t < s.to);
  return {
    goTo, step,
    drop: span(t, 0.2, 1.2),
    carried: t >= 1.3 && t < 10.4,
    back: span(t, 10.4, 11.4),
    lamp: t >= 3.6 && t < 10.0 ? 1 : 0,
    swing: t < 5.6 ? -0.5 : t < 7.2 ? -0.5 + span(t, 5.8, 7.2) : 0.5,
    press: t < 7.8 ? 0 : t < 8.6 ? span(t, 7.8, 8.6) : 1 - span(t, 9.0, 9.8),
    token: t >= 8.5 && t < 10.0 ? 1 : 0,
  };
}
function draw(m) {
  carriage.setAttribute("transform", translateAlong(P, [x.at, 0, 0]));
  carCard.style.opacity = m.carried ? 1 : 0;
  const z = m.carried ? 40 : m.back > 0 ? HOPPER_TOP + 10 * m.back : HOPPER_TOP + 10 - 10 * m.drop;
  hopperCard.style.opacity = m.carried ? 0 : 1;
  hopperCard.setAttribute("transform", translateAlong(P, [HOPPER[0], HOPPER[1], z]));
  stampEl.setAttribute("transform", translateAlong(P, [0, 0, -press * 4.5]));
  tokenRing.style.opacity = 1 - token;
  tokenKey.style.opacity = token;
  lamps.forEach((l, i) => (l.style.opacity = lampOn[i]));
  needles.forEach((n, i) => n.setAttribute("d", needleAt(CELL_X[i], swing[i])));
  const s = STEPS[Math.max(0, m.step)];
  const name = "ABC"[agent];
  const text = s.text.replace("Agent  ", "Agent " + name + " ").replace(/zu Agent $/, "zu Agent " + name);
  if (readout.textContent !== text) readout.textContent = text;
  stage.setAttribute("aria-valuetext", text);
}
function tick(now) {
  const dt = last ? Math.min((now - last) / 1000, 1 / 30) : 1 / 60;
  last = now;
  const calm = still.matches;
  if (!calm) clock += dt;
  if (clock >= LOOP) { clock -= LOOP; agent = chosen ?? (agent + 1) % 3; }
  const m = model(calm ? 6.5 : clock);
  if (calm) { x.at = m.goTo; } else {
    for (let i = 0; i < 4; i++) { x.rate += ((m.goTo - x.at) * 60 - x.rate * 14) * dt / 4; x.at += x.rate * dt / 4; }
  }
  const follow = (v, to, tau) => (calm ? to : v + (to - v) * (1 - Math.exp(-dt / tau)));
  for (let i = 0; i < 3; i++) {
    swing[i] = follow(swing[i], i === agent ? m.swing : -0.5, 0.12);
    lampOn[i] = follow(lampOn[i], i === agent ? m.lamp : 0, 0.15);
  }
  press = follow(press, m.press, 0.06);
  token = follow(token, m.token, 0.12);
  draw(m);
  frame = visible && !calm ? requestAnimationFrame(tick) : 0;
}
const run = () => { if (!frame && visible) { last = 0; frame = requestAnimationFrame(tick); } };
const choose = (i) => { chosen = i; agent = i; clock = 0; clearTimeout(idle); idle = setTimeout(() => { chosen = null; }, 8000); run(); if (still.matches) tick(performance.now()); };
new IntersectionObserver((e) => { visible = e[e.length - 1].isIntersecting; if (visible) run(); }, { rootMargin: "120px 0px" }).observe(stage);
stage.addEventListener("pointermove", (event) => {
  const box = svg.getBoundingClientRect();
  const share = (event.clientX - box.left) / box.width;
  const i = share < 0.42 ? 0 : share < 0.58 ? 1 : 2;
  if (i !== chosen) choose(i);
});
stage.addEventListener("keydown", (event) => {
  let to = null;
  if (event.key === "ArrowRight") to = Math.min(2, agent + 1);
  if (event.key === "ArrowLeft") to = Math.max(0, agent - 1);
  if (to === null) return;
  event.preventDefault();
  choose(to);
});
tick(performance.now());
`;

const body = k.plateHtml({
  fig: "Abb. 1",
  title: "So arbeitet AI Employee",
  hint: "Zeige auf einen Agenten oder nutze ← →",
  readout: STEPS[0].text,
  keys: [
    { mark: "lit", label: "Der Auftrag, die Lampe des arbeitenden Agenten und der Stempel" },
    { mark: "raised", label: "Drei Agenten, jeder in seinem eigenen Container" },
    { mark: "dotted", label: "Alles nach außen läuft durch den Proxy-Turm" },
  ],
  caption:
    "Ein Auftrag kommt über einen Eingang, gleich aus welchem Kanal. Der Orchestrator bringt ihn zum Agenten seines Besitzers; jeder Agent läuft abgeschlossen in seinem eigenen Container. Jeder Werkzeugaufruf geht zuerst durch die Prüfung gegen die Freigaben. Was nach außen geht, läuft durch den Proxy: Der Agent kennt nur Platzhalter, den echten Schlüssel setzt erst der Proxy ein, und nur am erlaubten Ziel.",
  body: `<div class="iso-stage" tabindex="0" role="slider" aria-label="So arbeitet AI Employee: Pfeiltasten wählen den Agenten" aria-valuemin="0" aria-valuemax="2" aria-valuenow="0">${svg}</div>`,
});

const datei = theme === "light" ? `${NAME}-light.html` : `${NAME}.html`;
writeFileSync(join(HERE, datei), k.pageHtml({ title: "So arbeitet AI Employee", theme, body, script: kitScript() + live }));
console.log("wrote", datei);
