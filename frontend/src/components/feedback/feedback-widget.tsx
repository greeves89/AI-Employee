"use client";
// In-App-Feedback-Widget ("Feedback-Gedöns") — global in der App-Shell gemountet,
// rendert auf jeder Route. Gestartet über den Feedback-Knopf im Sidebar-Kopf
// (Event "feedback-widget:open") — der frühere schwebende Knopf unten rechts
// ist raus, weil er Eingabefelder überdeckt hat. Flow: Element anpinnen
// (Pick-Mode) → Viewport-Screenshot mit rotem Rahmen → Sentiment + Kategorie +
// Freitext → genau EINE Requirements-Rückfrage vom LLM → Speichern (MD + PNG +
// DB-Eintrag).
// Attribution passiert serverseitig über die Session — hier wird der Name nur angezeigt.

import { useEffect, useRef, useState } from "react";
import {
  Bug, Camera, CheckCircle2, Crosshair, ExternalLink, Lightbulb, Loader2,
  MessageSquare, MessageSquarePlus, Send, ThumbsDown, ThumbsUp, TrendingUp,
  UserRound, X,
} from "lucide-react";
import { useAuthStore } from "@/lib/auth";
import * as api from "@/lib/api";
import type { FeedbackCategory } from "@/lib/types";
import "./feedback.css";

type Msg = { role: "user" | "bot"; text: string };
type Pin = { label: string; selector: string; page: string };

const SENT = [
  { id: "positiv", label: "Gefällt mir", icon: ThumbsUp, color: "#34d399" },
  { id: "negativ", label: "Stört mich", icon: ThumbsDown, color: "#fb923c" },
  { id: "wunsch", label: "Wunsch", icon: Lightbulb, color: "#fbbf24" },
] as const;

// Dieselben Kategorien wie das bisherige Feedback-Modal — bleiben als Zusatzfeld
// neben dem Sentiment erhalten.
const CATS: { id: FeedbackCategory; label: string; icon: typeof Bug }[] = [
  { id: "bug", label: "Bug", icon: Bug },
  { id: "feature", label: "Feature", icon: Lightbulb },
  { id: "improvement", label: "Verbesserung", icon: TrendingUp },
  { id: "general", label: "Allgemein", icon: MessageSquare },
];

function cssPath(el: Element): string {
  const parts: string[] = [];
  let e: Element | null = el;
  while (e && e.nodeType === 1 && parts.length < 5 && e.tagName.toLowerCase() !== "body") {
    let sel = e.tagName.toLowerCase();
    if ((e as HTMLElement).id) { parts.unshift(sel + "#" + (e as HTMLElement).id); break; }
    const p = e.parentElement;
    if (p) {
      const same = Array.from(p.children).filter(c => c.tagName === e!.tagName);
      if (same.length > 1) sel += `:nth-of-type(${same.indexOf(e) + 1})`;
    }
    parts.unshift(sel);
    e = e.parentElement;
  }
  return parts.join(" > ");
}

// Schneidet an der letzten Wortgrenze vor dem Limit statt mitten im Wort ab,
// und markiert den Schnitt mit "…" — sonst sieht ein abgeschnittenes Label
// (z.B. in der Feedback-Detail-Modal) wie ein kaputter String aus.
function truncateLabel(s: string, limit = 80): string {
  if (s.length <= limit) return s;
  const cut = s.lastIndexOf(" ", limit);
  const at = cut > limit * 0.5 ? cut : limit;
  return s.slice(0, at).trimEnd() + "…";
}

function labelOf(el: Element): string {
  const al = el.getAttribute("aria-label") || (el as HTMLElement).title;
  if (al) return truncateLabel(al.trim());
  // innerText statt textContent: textContent haengt den Text aller
  // Nachfahren-Knoten roh aneinander (zwei Block-Geschwister werden ohne
  // Trenner zu einem Wort verklebt, z.B. "erhaltenFeedback wird..."), waehrend
  // innerText das gerenderte Layout beruecksichtigt und zwischen Bloecken
  // Zeilenumbrueche einfuegt.
  const raw = (el as HTMLElement).innerText ?? el.textContent ?? "";
  const t = raw.replace(/\s+/g, " ").trim();
  return truncateLabel(t || el.tagName.toLowerCase());
}

// Zeichnet das angepinnte Element als roten Rahmen in den fertigen Screenshot.
function drawHighlight(dataUrl: string, rect: DOMRect, dpr: number): Promise<string> {
  return new Promise(resolve => {
    const img = new Image();
    img.onload = () => {
      try {
        const c = document.createElement("canvas");
        c.width = img.width; c.height = img.height;
        const cx = c.getContext("2d");
        if (!cx) return resolve(dataUrl);
        cx.drawImage(img, 0, 0);
        cx.strokeStyle = "#ef4444"; cx.lineWidth = Math.max(2, 3 * dpr);
        cx.strokeRect(rect.left * dpr, rect.top * dpr, rect.width * dpr, rect.height * dpr);
        resolve(c.toDataURL("image/png"));
      } catch { resolve(dataUrl); }
    };
    img.onerror = () => resolve(dataUrl);
    img.src = dataUrl;
  });
}

// Screenshot des SICHTBAREN Viewports (ohne das Widget selbst), Element-Highlight
// eingezeichnet. html-to-image wird dynamisch geladen → fehlt das Paket, scheitert
// die Aufnahme leise und das Feedback geht trotzdem (text-only) raus.
async function captureViewport(el: Element): Promise<string | null> {
  try {
    const { toPng } = await import("html-to-image");
    const rect = el.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2); // 2 = scharf genug, hält die Payload klein
    const dataUrl = await toPng(document.body, {
      pixelRatio: dpr,
      width: window.innerWidth,
      height: window.innerHeight,
      // nur den sichtbaren Ausschnitt rendern (nicht die ganze Scrollhöhe)
      style: { transform: `translate(${-window.scrollX}px, ${-window.scrollY}px)`, transformOrigin: "top left" },
      // das Feedback-Widget selbst nicht mit aufnehmen
      filter: (n: HTMLElement) => !(n.classList && n.classList.contains("fbw")),
      cacheBust: true,
    });
    return await drawHighlight(dataUrl, rect, dpr);
  } catch (e) {
    console.error("Feedback-Screenshot fehlgeschlagen:", e);
    return null;
  }
}

export function FeedbackWidget() {
  const authUser = useAuthStore(s => s.user);
  const [mode, setMode] = useState<"idle" | "picking" | "panel">("idle");
  const [pin, setPin] = useState<Pin | null>(null);
  const [sentiment, setSentiment] = useState("");
  const [category, setCategory] = useState<FeedbackCategory>("general");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [step, setStep] = useState<"input" | "followup" | "done">("input");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [issueUrl, setIssueUrl] = useState("");
  const [shot, setShot] = useState<string | null>(null); // Screenshot-dataURL (PNG)
  const [shotOn, setShotOn] = useState(true);            // anhängen ja/nein (User-Kontrolle)
  const [shotBusy, setShotBusy] = useState(false);
  const hi = useRef<HTMLDivElement>(null);

  // Einziger Einstieg: der Feedback-Knopf im Sidebar-Kopf.
  useEffect(() => {
    const open = () => setMode(m => (m === "idle" ? "picking" : m));
    window.addEventListener("feedback-widget:open", open);
    return () => window.removeEventListener("feedback-widget:open", open);
  }, []);

  useEffect(() => {
    if (mode !== "picking") return;
    const move = (e: MouseEvent) => {
      const el = e.target as Element;
      if (!el || el.closest(".fbw")) { if (hi.current) hi.current.style.display = "none"; return; }
      const r = el.getBoundingClientRect();
      if (hi.current) Object.assign(hi.current.style,
        { display: "block", left: r.left + "px", top: r.top + "px", width: r.width + "px", height: r.height + "px" });
    };
    const click = (e: MouseEvent) => {
      const el = e.target as Element;
      if (!el || el.closest(".fbw")) return;
      e.preventDefault(); e.stopPropagation();
      setPin({ label: labelOf(el), selector: cssPath(el), page: window.location.pathname });
      setMode("panel"); setStep("input"); setMsgs([]); setInput(""); setSentiment(""); setCategory("general"); setError("");
      // Screenshot sofort beim Anpinnen aufnehmen (Seitenzustand = was der Nutzer sieht).
      setShot(null); setShotOn(true); setShotBusy(true);
      captureViewport(el).then(setShot).finally(() => setShotBusy(false));
    };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") setMode("idle"); };
    document.addEventListener("mousemove", move, true);
    document.addEventListener("click", click, true);
    document.addEventListener("keydown", key, true);
    return () => {
      document.removeEventListener("mousemove", move, true);
      document.removeEventListener("click", click, true);
      document.removeEventListener("keydown", key, true);
    };
  }, [mode]);

  // Ohne Session kein Widget (Login, Register, Kiosk) — Feedback braucht Attribution.
  if (!authUser) return null;

  const ctx = () => ({
    page: pin?.page, element_label: pin?.label, selector: pin?.selector,
    sentiment, kategorie: category,
  });

  async function weiter() {
    if (!input.trim()) return;
    const m: Msg[] = [...msgs, { role: "user", text: input.trim() }];
    setMsgs(m); setInput(""); setBusy(true);
    try {
      const r = await api.feedbackWidgetReply(m, ctx());
      setMsgs([...m, { role: "bot", text: r.reply || "Danke, festgehalten." }]); setStep("followup");
    } catch {
      setMsgs([...m, { role: "bot", text: "(Rückfrage nicht erreichbar — du kannst direkt speichern.)" }]);
      setStep("followup");
    } finally { setBusy(false); }
  }

  async function speichern(answer?: string) {
    const m = answer && answer.trim() ? [...msgs, { role: "user" as const, text: answer.trim() }] : msgs;
    setBusy(true); setError("");
    try {
      const r = await api.feedbackWidgetSave(m, ctx(), shotOn ? shot : null);
      setMsgs(m); setIssueUrl(r?.issue_url || ""); setStep("done");
    } catch {
      setMsgs(m);
      setError("Speichern fehlgeschlagen — bitte nochmal versuchen.");
    } finally { setBusy(false); }
  }

  function reset() {
    setMode("idle"); setPin(null); setMsgs([]); setInput(""); setSentiment(""); setCategory("general");
    setIssueUrl(""); setStep("input"); setShot(null); setShotBusy(false); setError("");
  }

  return (
    <div className="fbw">
      {mode === "picking" && <div ref={hi} className="fbw-hi" />}
      {mode === "picking" && (
        <div className="fbw-hint"><Crosshair size={15} /> Klick das Element an, zu dem du Feedback geben willst
          <button onClick={() => setMode("idle")}>Abbrechen (ESC)</button></div>
      )}
      {mode === "panel" && pin && (
        <div className="fbw-panel">
          <div className="fbw-head"><span><MessageSquarePlus size={15} /> Feedback</span>
            <button onClick={reset} title="Schließen"><X size={16} /></button></div>
          <div className="fbw-pin"><span>zu:</span> <b>{pin.label}</b><span className="fbw-page">{pin.page}</span></div>
          <div className="fbw-user"><UserRound size={12} /> als {authUser.name || authUser.email}</div>
          {step !== "done" && (
            <div className="fbw-sent">
              {SENT.map(s => {
                const SIcon = s.icon;
                return (
                  <button key={s.id} className={sentiment === s.id ? "on" : ""}
                    style={sentiment === s.id ? { borderColor: s.color, color: s.color } : {}}
                    onClick={() => setSentiment(s.id)}><SIcon size={14} /> {s.label}</button>
                );
              })}
            </div>
          )}
          {step !== "done" && (
            <div className="fbw-cats">
              {CATS.map(c => {
                const CIcon = c.icon;
                return (
                  <button key={c.id} className={category === c.id ? "on" : ""}
                    onClick={() => setCategory(c.id)}><CIcon size={13} /> {c.label}</button>
                );
              })}
            </div>
          )}
          {step !== "done" && (
            <div className="fbw-shot">
              <label className="fbw-shot-toggle">
                <input type="checkbox" checked={shotOn} onChange={e => setShotOn(e.target.checked)} disabled={!shot && !shotBusy} />
                <Camera size={13} /> Screenshot anhängen
                {shotBusy && <span className="fbw-shot-busy">… wird aufgenommen</span>}
                {!shotBusy && !shot && <span className="fbw-shot-busy">nicht verfügbar</span>}
              </label>
              {shotOn && shot && <img className="fbw-shot-thumb" src={shot} alt="Screenshot-Vorschau" />}
            </div>
          )}
          <div className="fbw-msgs">{msgs.map((m, i) => <div key={i} className={`fbw-msg ${m.role}`}>{m.text}</div>)}</div>
          {error && <div className="fbw-error">{error}</div>}
          {step === "input" && (
            <>
              <textarea autoFocus value={input} onChange={e => setInput(e.target.value)}
                placeholder="Was gefällt dir / stört dich / wünschst du dir hier?" rows={3} />
              <div className="fbw-actions">
                <button className="fbw-btn ghost" disabled={busy || !input.trim()} onClick={() => speichern(input)}>Direkt speichern</button>
                <button className="fbw-btn" disabled={busy || !input.trim()} onClick={weiter}>
                  {busy ? <Loader2 size={13} className="fbw-spin" /> : <Send size={13} />} Weiter</button>
              </div>
            </>
          )}
          {step === "followup" && (
            <>
              <textarea autoFocus value={input} onChange={e => setInput(e.target.value)}
                placeholder="Kurze Antwort auf die Rückfrage (optional) …" rows={2} />
              <div className="fbw-actions">
                <button className="fbw-btn" disabled={busy} onClick={() => speichern(input)}>
                  {busy ? <Loader2 size={13} className="fbw-spin" /> : <Send size={13} />} {busy ? "Speichern…" : "Speichern"}</button>
              </div>
            </>
          )}
          {step === "done" && (
            <div className="fbw-done"><CheckCircle2 size={16} /> Danke! Festgehalten.
              <button className="fbw-btn ghost" onClick={reset}>Neues Feedback</button>
              {issueUrl && <a className="fbw-issue" href={issueUrl} target="_blank" rel="noreferrer"><ExternalLink size={13} /> Als Issue angelegt</a>}</div>
          )}
        </div>
      )}
    </div>
  );
}
