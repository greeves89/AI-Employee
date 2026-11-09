"use client";

import { useEffect, useState } from "react";
import { ChevronDown, ListChecks, Loader2, MessagesSquare, TriangleAlert } from "lucide-react";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import { useToast } from "@/components/ui/dialog-provider";

/**
 * Kanäle eines Agenten: Teams, Slack, Discord, WhatsApp (core/kanaele, /agents/{id}/kanaele).
 *
 * Zugangsdaten gehen nur hinein, nie heraus: Die Karte zeigt „gesetzt“ oder „nicht gesetzt“.
 * Ein leeres Token-Feld lässt das gespeicherte stehen; „Token entfernen“ löscht es.
 * Bei Teams werden Chats und Kanäle aus Teams ausgewählt statt Kennungen abgetippt.
 */
const NAMEN: Record<string, string> = { teams: "Microsoft Teams", slack: "Slack", discord: "Discord", whatsapp: "WhatsApp" };
const ZIEL_NAME: Record<string, string> = { slack: "Kanal-IDs", discord: "Kanal-IDs" };

const zeilen = (t: string) => t.split("\n").map((z) => z.trim()).filter(Boolean);

function Schalter({ an, onClick, disabled, label }: { an: boolean; onClick: () => void; disabled?: boolean; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={an}
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className={cn("relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-50",
        an ? "bg-emerald-500" : "bg-foreground/15")}
    >
      <span className={cn("inline-block h-4 w-4 rounded-full bg-white transition-transform", an ? "translate-x-6" : "translate-x-1")} />
    </button>
  );
}

function Feld({ label, hilfe, children }: { label: string; hilfe?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <div className="text-xs font-medium">{label}</div>
      {children}
      {hilfe && <div className="text-[11px] text-muted-foreground/60">{hilfe}</div>}
    </div>
  );
}

const eingabe = "w-full rounded-lg border border-foreground/10 bg-background px-3 py-2 text-sm";

function KanalBlock({ agentId, stand, onGespeichert }: {
  agentId: string; stand: api.KanalStand; onGespeichert: (s: api.KanalStand) => void;
}) {
  const [offen, setOffen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [ziele, setZiele] = useState((stand.ziele || []).join("\n"));
  const [stichworte, setStichworte] = useState((stand.stichworte || []).join("\n"));
  const [absender, setAbsender] = useState((stand.absender || []).join("\n"));
  const [telefonId, setTelefonId] = useState(stand.telefon_id || "");
  const [botKennung, setBotKennung] = useState(stand.bot_kennung || "");
  const [token, setToken] = useState("");
  const [teamsKanaele, setTeamsKanaele] = useState<api.TeamsKanal[]>(stand.teams_kanaele || []);
  const [auswahl, setAuswahl] = useState<api.TeamsAuswahl | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);
  const toast = useToast();

  const speichern = async (patch: api.KanalUpdate, meldung = "Gespeichert") => {
    setBusy(true);
    setFehler(null);
    try {
      const s = await api.setzeAgentKanal(agentId, stand.kanal, patch);
      onGespeichert(s);
      setToken("");
      toast.success(meldung);
    } catch (e) {
      setFehler(e instanceof Error ? e.message : "Speichern fehlgeschlagen");
    } finally {
      setBusy(false);
    }
  };

  const allesSpeichern = () => {
    const patch: api.KanalUpdate = { nur_bei_erwaehnung: stand.nur_bei_erwaehnung };
    if (stand.kanal === "whatsapp") {
      patch.telefon_id = telefonId.trim();
      patch.absender = zeilen(absender);
    } else {
      patch.ziele = zeilen(ziele);
      patch.stichworte = zeilen(stichworte);
    }
    if (stand.kanal === "teams") patch.teams_kanaele = teamsKanaele;
    if (stand.kanal === "slack" || stand.kanal === "discord") patch.bot_kennung = botKennung.trim();
    if (token.trim()) patch.token = token.trim();
    return speichern(patch);
  };

  const auswahlLaden = async () => {
    setBusy(true);
    setFehler(null);
    try {
      setAuswahl(await api.getTeamsAuswahl(agentId));
    } catch (e) {
      setFehler(e instanceof Error ? e.message : "Chats ließen sich nicht laden");
    } finally {
      setBusy(false);
    }
  };

  const chatGewaehlt = (id: string) => zeilen(ziele).includes(id);
  const chatUmschalten = (id: string) => {
    const liste = zeilen(ziele);
    setZiele((liste.includes(id) ? liste.filter((z) => z !== id) : [...liste, id]).join("\n"));
  };
  const kanalGewaehlt = (k: api.TeamsKanal) => teamsKanaele.some((x) => x.team_id === k.team_id && x.channel_id === k.channel_id);
  const kanalUmschalten = (k: api.TeamsKanal) => setTeamsKanaele(kanalGewaehlt(k)
    ? teamsKanaele.filter((x) => !(x.team_id === k.team_id && x.channel_id === k.channel_id))
    : [...teamsKanaele, { team_id: k.team_id, channel_id: k.channel_id }]);

  const hatToken = stand.kanal !== "teams";
  return (
    <div className="border-t border-foreground/[0.06] first:border-t-0">
      <div className="flex items-center gap-3 px-5 py-3">
        <button type="button" onClick={() => setOffen(!offen)} className="flex min-w-0 flex-1 items-center gap-2 text-left">
          <ChevronDown className={cn("h-4 w-4 shrink-0 text-muted-foreground transition-transform", !offen && "-rotate-90")} />
          <span className="text-[13px] font-medium">{NAMEN[stand.kanal]}</span>
          <span className="truncate text-[11px] text-muted-foreground/70">
            {stand.aktiv ? "aktiv" : "aus"}
            {hatToken && ` · Token ${stand.token_gesetzt ? "gesetzt" : "nicht gesetzt"}`}
          </span>
        </button>
        <Schalter an={stand.aktiv} disabled={busy} label={`${NAMEN[stand.kanal]} aktiv`}
          onClick={() => speichern({ aktiv: !stand.aktiv }, stand.aktiv ? "Ausgeschaltet" : "Eingeschaltet")} />
      </div>
      {offen && (
        <div className="space-y-3 px-5 pb-4">
          {stand.kanal === "teams" && (
            <>
              <div className="text-[11px] text-muted-foreground/70">
                Der Agent liest die gewählten Chats und Kanäle mit dem Microsoft-Konto, das er nutzt (eigenes
                oder deins). Ob er antworten darf, entscheidet seine Microsoft-Einstellung (Lesen, Entwurf mit
                Freigabe, Schreiben).
                {(stand.besprechungs_chats || 0) > 0 && ` Dazu kommen ${stand.besprechungs_chats} Chat(s) aus Besprechungen.`}
              </div>
              <button type="button" onClick={auswahlLaden} disabled={busy}
                className="inline-flex items-center gap-1.5 rounded-lg border border-foreground/10 px-3 py-1.5 text-xs font-medium hover:bg-foreground/5 disabled:opacity-50">
                {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ListChecks className="h-3.5 w-3.5" />}
                Chats und Kanäle aus Teams auswählen
              </button>
              {auswahl && (
                <div className="max-h-64 space-y-2 overflow-y-auto rounded-lg border border-foreground/10 p-3">
                  {auswahl.chats.length === 0 && auswahl.kanaele.length === 0 && (
                    <div className="text-[11px] text-muted-foreground">Keine Chats oder Kanäle gefunden.</div>
                  )}
                  {auswahl.chats.map((c) => (
                    <label key={c.id} className="flex items-center gap-2 text-xs">
                      <input type="checkbox" checked={chatGewaehlt(c.id)} onChange={() => chatUmschalten(c.id)} />
                      <span className="truncate">{c.name}</span>
                    </label>
                  ))}
                  {auswahl.kanaele.map((k) => (
                    <label key={`${k.team_id}/${k.channel_id}`} className="flex items-center gap-2 text-xs">
                      <input type="checkbox" checked={kanalGewaehlt(k)} onChange={() => kanalUmschalten(k)} />
                      <span className="truncate">{k.name}</span>
                    </label>
                  ))}
                </div>
              )}
              <div className="text-[11px] text-muted-foreground/70">
                Gewählt: {zeilen(ziele).length} Chat(s), {teamsKanaele.length} Kanal/Kanäle
              </div>
            </>
          )}
          {(stand.kanal === "slack" || stand.kanal === "discord") && (
            <>
              <Feld label={ZIEL_NAME[stand.kanal]} hilfe="Eine je Zeile — welche Kanäle der Agent mitliest.">
                <textarea value={ziele} onChange={(e) => setZiele(e.target.value)} rows={3} className={eingabe} />
              </Feld>
              <Feld label="Bot-Kennung" hilfe="Damit der Agent eine Erwähnung erkennt (@Bot).">
                <input value={botKennung} onChange={(e) => setBotKennung(e.target.value)} className={eingabe} />
              </Feld>
            </>
          )}
          {stand.kanal === "whatsapp" && (
            <>
              <Feld label="Telefonnummer-ID" hilfe="Aus der WhatsApp-Business-Plattform von Meta.">
                <input value={telefonId} onChange={(e) => setTelefonId(e.target.value)} className={eingabe} />
              </Feld>
              <Feld label="Erlaubte Absender" hilfe="Telefonnummern, eine je Zeile. Andere bekommen keine Antwort.">
                <textarea value={absender} onChange={(e) => setAbsender(e.target.value)} rows={3} className={eingabe} />
              </Feld>
            </>
          )}
          {stand.kanal !== "whatsapp" && (
            <>
              <label className="flex items-center gap-2 text-xs">
                <input type="checkbox" checked={stand.nur_bei_erwaehnung ?? true}
                  onChange={(e) => onGespeichert({ ...stand, nur_bei_erwaehnung: e.target.checked })} />
                Nur antworten, wenn der Agent erwähnt oder mit einem Stichwort angesprochen wird
              </label>
              <Feld label="Stichworte" hilfe="Eines je Zeile — zusätzlich zum Namen des Agenten.">
                <textarea value={stichworte} onChange={(e) => setStichworte(e.target.value)} rows={2} className={eingabe} />
              </Feld>
            </>
          )}
          {hatToken && (
            <Feld label={stand.kanal === "whatsapp" ? "Zugriffstoken" : "Bot-Token"}
              hilfe="Wird verschlüsselt gespeichert und nie wieder angezeigt. Leer lassen, um es zu behalten.">
              <div className="flex gap-2">
                <input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)}
                  placeholder={stand.token_gesetzt ? "gesetzt" : "nicht gesetzt"} className={eingabe} />
                {stand.token_gesetzt && (
                  <button type="button" disabled={busy} onClick={() => speichern({ token: "" }, "Token entfernt")}
                    className="shrink-0 rounded-lg px-2.5 text-[11px] text-muted-foreground hover:bg-red-500/10 hover:text-red-400">
                    Token entfernen
                  </button>
                )}
              </div>
            </Feld>
          )}
          {fehler && (
            <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[12px] text-amber-800 dark:text-amber-300">
              <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{fehler}</span>
            </div>
          )}
          <button type="button" onClick={allesSpeichern} disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
            {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Speichern
          </button>
        </div>
      )}
    </div>
  );
}

export function KanaeleCard({ agentId }: { agentId: string }) {
  const [kanaele, setKanaele] = useState<api.KanalStand[] | null>(null);

  useEffect(() => {
    api.getAgentKanaele(agentId).then((r) => setKanaele(r.kanaele)).catch(() => setKanaele(null));
  }, [agentId]);

  if (!kanaele) return null;
  return (
    <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden">
      <div className="flex items-center gap-2.5 border-b border-foreground/[0.06] px-5 py-3.5">
        <div className="rounded-lg bg-primary/10 p-1.5">
          <MessagesSquare className="h-4 w-4 text-primary" />
        </div>
        <div>
          <h3 className="text-sm font-medium">Kanäle</h3>
          <p className="mt-0.5 text-[11px] text-muted-foreground/60">
            Wo der Agent außer im Chat erreichbar ist. Nachrichten dort wecken ihn, wenn er gestoppt ist.
          </p>
        </div>
      </div>
      {kanaele.map((k) => (
        <KanalBlock key={k.kanal} agentId={agentId} stand={k}
          onGespeichert={(s) => setKanaele(kanaele.map((x) => (x.kanal === s.kanal ? s : x)))} />
      ))}
    </div>
  );
}
