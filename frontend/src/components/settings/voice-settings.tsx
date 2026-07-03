"use client";

import { useEffect, useState } from "react";
import { Mic, Radio, ChevronDown, ChevronRight, Sparkles } from "lucide-react";
import { getBase } from "@/lib/config";
import * as api from "@/lib/api";
import type { RealtimeModelOption } from "@/lib/api";

type VoiceConfig = {
  stt_provider: string;
  tts_provider: string;
  tts_voice: string;
  llm_model: string;
  language: string | null;
  available_stt: string[];
  available_tts: string[];
  available_llm: string[];
  available_voices: { id: string; label: string; lang: string }[];
  has_openai_key: boolean;
  has_elevenlabs_key: boolean;
  has_azure_speech_key?: boolean;
  azure_speech_region?: string;
  voice_interaction_model?: string;
  voice_interaction_account_id?: string;
};

const STT_LABELS: Record<string, string> = {
  faster_whisper: "faster-whisper (lokal, kostenlos)",
  openai_whisper: "OpenAI Whisper API",
  azure_speech: "Azure Speech (Microsoft, STT)",
};
const TTS_LABELS: Record<string, string> = {
  edge_tts: "Microsoft Edge-TTS (kostenlos)",
  elevenlabs: "ElevenLabs (premium)",
  azure_speech: "Azure Speech (Microsoft Neural Voices)",
};
const LLM_LABELS: Record<string, string> = {
  "claude-haiku-4-5-20251001": "Claude Haiku 4.5 (schnell, empfohlen)",
  "claude-sonnet-4-6": "Claude Sonnet 4.6 (smarter, langsamer)",
};

export function VoiceSettings() {
  const [cfg, setCfg] = useState<VoiceConfig | null>(null);
  const [models, setModels] = useState<RealtimeModelOption[]>([]);
  const [openaiKey, setOpenaiKey] = useState("");
  const [elevenKey, setElevenKey] = useState("");
  const [azureKey, setAzureKey] = useState("");
  const [azureRegion, setAzureRegion] = useState("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [showClassic, setShowClassic] = useState(false);

  const reload = async () => {
    const token = localStorage.getItem("token");
    const r = await fetch(`${getBase()}/settings/voice`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (r.ok) setCfg(await r.json());
  };

  useEffect(() => {
    reload();
    api.getRealtimeModels().then(setModels).catch(() => setModels([]));
  }, []);

  const patch = async (body: Record<string, unknown>) => {
    setSaving(true);
    setMsg(null);
    const token = localStorage.getItem("token");
    const r = await fetch(`${getBase()}/settings/`, {
      method: "PATCH",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    setSaving(false);
    if (r.ok) {
      setMsg("Gespeichert.");
      await reload();
    } else {
      setMsg("Speichern fehlgeschlagen.");
    }
    setTimeout(() => setMsg(null), 3000);
  };

  if (!cfg) {
    return (
      <div className="rounded-xl border border-foreground/[0.06] bg-card/80 p-5">
        <p className="text-sm text-muted-foreground">Lade Voice-Settings…</p>
      </div>
    );
  }

  const realtimeActive = !!(cfg.voice_interaction_model && cfg.voice_interaction_model !== "");
  const classicActive = !realtimeActive;
  const isActiveModel = (o: RealtimeModelOption) =>
    cfg.voice_interaction_model === o.engine &&
    String(cfg.voice_interaction_account_id || "") === String(o.account_id);

  const selectRealtime = (o: RealtimeModelOption) => {
    if (!o.implemented) {
      setMsg(`${o.provider_label} ist noch nicht verfügbar.`);
      setTimeout(() => setMsg(null), 3000);
      return;
    }
    patch({ voice_interaction_model: o.engine, voice_interaction_account_id: String(o.account_id) });
  };
  const selectClassic = () =>
    patch({ voice_interaction_model: "", voice_interaction_account_id: "" });

  return (
    <section>
      <div className="mb-3 flex items-center gap-2">
        <Mic className="h-4 w-4 text-muted-foreground/60" />
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">
          Voice Live-Sessions
        </h2>
      </div>

      <div className="overflow-hidden rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm">
        <div className="flex items-center justify-between border-b border-foreground/[0.04] px-5 py-3.5">
          <div>
            <h3 className="text-sm font-semibold">Sprach-Interaktion</h3>
            <p className="text-[11px] text-muted-foreground/60">
              Wähle das Echtzeit-Sprachmodell (empfohlen) — oder die klassische Pipeline als Fallback.
            </p>
          </div>
          {msg && <span className="text-[11px] text-emerald-400">{msg}</span>}
        </div>

        <div className="space-y-5 p-5">
          {/* ── Realtime models (primary) ─────────────────────────── */}
          <div>
            <div className="mb-2 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/70">
              <Sparkles className="h-3.5 w-3.5 text-fuchsia-400" /> Echtzeit-Sprachmodell
            </div>
            <div className="space-y-2">
              {models.map((o) => {
                const active = isActiveModel(o);
                return (
                  <button
                    key={o.value}
                    onClick={() => selectRealtime(o)}
                    disabled={saving}
                    className={`flex w-full items-center gap-3 rounded-lg border px-3.5 py-2.5 text-left transition ${
                      active
                        ? "border-fuchsia-500/50 bg-fuchsia-500/10"
                        : "border-foreground/10 bg-background hover:bg-foreground/[0.04]"
                    } ${!o.implemented ? "opacity-50" : ""}`}
                  >
                    <Radio className={`h-4 w-4 shrink-0 ${active ? "text-fuchsia-400" : "text-muted-foreground/50"}`} />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">{o.model_label}</div>
                      <div className="truncate text-[11px] text-muted-foreground/60">
                        {o.provider_label} · {o.account_name}
                        {!o.implemented && " · noch nicht verfügbar"}
                      </div>
                    </div>
                    {active && (
                      <span className="shrink-0 rounded-full bg-fuchsia-500/20 px-2 py-0.5 text-[10px] font-medium text-fuchsia-300">
                        Aktiv
                      </span>
                    )}
                  </button>
                );
              })}
              {models.length === 0 && (
                <div className="rounded-lg border border-dashed border-foreground/10 px-3.5 py-3 text-[11px] text-muted-foreground/60">
                  Noch kein Echtzeit-Provider eingerichtet. Lege unter{" "}
                  <a href="/?tab=ai-accounts" className="text-fuchsia-400 hover:underline">
                    AI-Accounts
                  </a>{" "}
                  ein AWS-Bedrock- (Nova Sonic) oder Azure-Realtime-Konto an — dann erscheint es hier.
                </div>
              )}
            </div>
          </div>

          {/* ── Classic pipeline (fallback, collapsible) ──────────── */}
          <div className="rounded-lg border border-foreground/[0.06]">
            <button
              onClick={() => setShowClassic((s) => !s)}
              className="flex w-full items-center justify-between px-3.5 py-2.5 text-left"
            >
              <div className="flex items-center gap-2">
                {showClassic ? (
                  <ChevronDown className="h-4 w-4 text-muted-foreground/50" />
                ) : (
                  <ChevronRight className="h-4 w-4 text-muted-foreground/50" />
                )}
                <div>
                  <div className="text-sm font-medium">Klassische Pipeline (Fallback)</div>
                  <div className="text-[11px] text-muted-foreground/60">
                    STT → LLM → TTS. Wird genutzt, wenn kein Echtzeit-Modell aktiv ist
                    {classicActive ? " — derzeit aktiv." : "."}
                  </div>
                </div>
              </div>
              {classicActive && (
                <span className="shrink-0 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-medium text-emerald-300">
                  Aktiv
                </span>
              )}
            </button>

            {(showClassic || classicActive) && (
              <div className="space-y-4 border-t border-foreground/[0.06] p-4">
                {realtimeActive && (
                  <button
                    onClick={selectClassic}
                    disabled={saving}
                    className="rounded-md bg-foreground/[0.06] px-3 py-1.5 text-xs hover:bg-foreground/[0.10]"
                  >
                    Auf klassische Pipeline zurückschalten
                  </button>
                )}

                <Field label="Speech-to-Text Engine">
                  <select
                    value={cfg.stt_provider}
                    onChange={(e) => patch({ voice_stt_provider: e.target.value })}
                    disabled={saving}
                    className="w-full rounded-md border border-foreground/10 bg-background px-3 py-2 text-sm"
                  >
                    {cfg.available_stt.map((p) => (
                      <option key={p} value={p}>{STT_LABELS[p] || p}</option>
                    ))}
                  </select>
                </Field>

                {cfg.stt_provider === "openai_whisper" && (
                  <Field label={`OpenAI API Key ${cfg.has_openai_key ? "(gesetzt)" : ""}`}>
                    <div className="flex gap-2">
                      <input
                        type="password"
                        value={openaiKey}
                        onChange={(e) => setOpenaiKey(e.target.value)}
                        placeholder={cfg.has_openai_key ? "•••••••• (ändern)" : "sk-..."}
                        className="flex-1 rounded-md border border-foreground/10 bg-background px-3 py-2 font-mono text-sm"
                      />
                      <button
                        onClick={() => { patch({ voice_openai_api_key: openaiKey }); setOpenaiKey(""); }}
                        disabled={!openaiKey || saving}
                        className="rounded-md bg-foreground/[0.06] px-3 text-xs hover:bg-foreground/[0.10] disabled:opacity-40"
                      >
                        Speichern
                      </button>
                    </div>
                  </Field>
                )}

                <Field label="Text-to-Speech Engine">
                  <select
                    value={cfg.tts_provider}
                    onChange={(e) => patch({ voice_tts_provider: e.target.value })}
                    disabled={saving}
                    className="w-full rounded-md border border-foreground/10 bg-background px-3 py-2 text-sm"
                  >
                    {cfg.available_tts.map((p) => (
                      <option key={p} value={p}>{TTS_LABELS[p] || p}</option>
                    ))}
                  </select>
                </Field>

                {(cfg.tts_provider === "edge_tts" || cfg.tts_provider === "azure_speech") &&
                  cfg.available_voices.length > 0 && (
                    <Field label="Stimme">
                      <select
                        value={cfg.tts_voice}
                        onChange={(e) => patch({ voice_tts_voice: e.target.value })}
                        disabled={saving}
                        className="w-full rounded-md border border-foreground/10 bg-background px-3 py-2 text-sm"
                      >
                        {cfg.available_voices.map((v) => (
                          <option key={v.id} value={v.id}>{v.label}</option>
                        ))}
                      </select>
                    </Field>
                  )}

                {cfg.tts_provider === "elevenlabs" && (
                  <Field label={`ElevenLabs API Key ${cfg.has_elevenlabs_key ? "(gesetzt)" : ""}`}>
                    <div className="flex gap-2">
                      <input
                        type="password"
                        value={elevenKey}
                        onChange={(e) => setElevenKey(e.target.value)}
                        placeholder={cfg.has_elevenlabs_key ? "•••••••• (ändern)" : "el_..."}
                        className="flex-1 rounded-md border border-foreground/10 bg-background px-3 py-2 font-mono text-sm"
                      />
                      <button
                        onClick={() => { patch({ voice_elevenlabs_api_key: elevenKey }); setElevenKey(""); }}
                        disabled={!elevenKey || saving}
                        className="rounded-md bg-foreground/[0.06] px-3 text-xs hover:bg-foreground/[0.10] disabled:opacity-40"
                      >
                        Speichern
                      </button>
                    </div>
                  </Field>
                )}

                {(cfg.stt_provider === "azure_speech" || cfg.tts_provider === "azure_speech") && (
                  <Field label={`Azure Speech Key + Region ${cfg.has_azure_speech_key ? "(gesetzt)" : ""}`}>
                    <div className="space-y-2">
                      <input
                        type="text"
                        value={azureRegion}
                        onChange={(e) => setAzureRegion(e.target.value)}
                        placeholder={cfg.azure_speech_region || "Region, z.B. germanywestcentral"}
                        className="w-full rounded-md border border-foreground/10 bg-background px-3 py-2 font-mono text-sm"
                      />
                      <div className="flex gap-2">
                        <input
                          type="password"
                          value={azureKey}
                          onChange={(e) => setAzureKey(e.target.value)}
                          placeholder={cfg.has_azure_speech_key ? "•••••••• (ändern)" : "Azure Speech Key"}
                          className="flex-1 rounded-md border border-foreground/10 bg-background px-3 py-2 font-mono text-sm"
                        />
                        <button
                          onClick={() => {
                            const body: Record<string, unknown> = {};
                            if (azureKey) body.voice_azure_speech_key = azureKey;
                            if (azureRegion) body.voice_azure_speech_region = azureRegion;
                            if (Object.keys(body).length) patch(body);
                            setAzureKey("");
                          }}
                          disabled={(!azureKey && !azureRegion) || saving}
                          className="rounded-md bg-foreground/[0.06] px-3 text-xs hover:bg-foreground/[0.10] disabled:opacity-40"
                        >
                          Speichern
                        </button>
                      </div>
                    </div>
                  </Field>
                )}

                <Field label="Interaction-Agent Modell (klassisch)">
                  <select
                    value={cfg.llm_model}
                    onChange={(e) => patch({ voice_llm_model: e.target.value })}
                    disabled={saving}
                    className="w-full rounded-md border border-foreground/10 bg-background px-3 py-2 text-sm"
                  >
                    {Object.entries(LLM_LABELS).map(([id, label]) => (
                      <option key={id} value={id}>{label}</option>
                    ))}
                  </select>
                </Field>

                <Field label="Sprache (Default = de, auto = Auto-Erkennung)">
                  <input
                    type="text"
                    defaultValue={cfg.language || ""}
                    onBlur={(e) => patch({ voice_language: e.target.value || "" })}
                    placeholder="de, en, fr, auto, ..."
                    className="w-full rounded-md border border-foreground/10 bg-background px-3 py-2 text-sm"
                  />
                </Field>
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="mb-1.5 block text-[11px] font-medium text-muted-foreground/80">{label}</label>
      {children}
    </div>
  );
}
