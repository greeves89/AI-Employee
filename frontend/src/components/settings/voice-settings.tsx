"use client";

import { useEffect, useState } from "react";
import { Mic } from "lucide-react";
import { getBase } from "@/lib/config";

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
  const [openaiKey, setOpenaiKey] = useState("");
  const [elevenKey, setElevenKey] = useState("");
  const [azureKey, setAzureKey] = useState("");
  const [azureRegion, setAzureRegion] = useState("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      const token = localStorage.getItem("token");
      const r = await fetch(`${getBase()}/settings/voice`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (r.ok) setCfg(await r.json());
    })();
  }, []);

  const patch = async (body: Record<string, unknown>) => {
    setSaving(true);
    setMsg(null);
    const token = localStorage.getItem("token");
    const r = await fetch(`${getBase()}/settings/`, {
      method: "PATCH",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });
    setSaving(false);
    if (r.ok) {
      setMsg("Gespeichert.");
      const r2 = await fetch(`${getBase()}/settings/voice`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (r2.ok) setCfg(await r2.json());
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

  return (
    <section>
      <div className="flex items-center gap-2 mb-3">
        <Mic className="h-4 w-4 text-muted-foreground/60" />
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">
          Voice Live-Sessions
        </h2>
      </div>
      <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden">
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-foreground/[0.04]">
          <div>
            <h3 className="text-sm font-semibold">Provider-Konfiguration</h3>
            <p className="text-[11px] text-muted-foreground/60">
              STT-, TTS- und Interaction-LLM-Engines für Live-Sprachsessions
            </p>
          </div>
          {msg && (
            <span className="text-[11px] text-emerald-400">{msg}</span>
          )}
        </div>

        <div className="p-5 space-y-4">
          {/* STT */}
          <Field label="Speech-to-Text Engine">
            <select
              value={cfg.stt_provider}
              onChange={(e) => patch({ voice_stt_provider: e.target.value })}
              disabled={saving}
              className="w-full rounded-md bg-background border border-foreground/10 px-3 py-2 text-sm"
            >
              {cfg.available_stt.map((p) => (
                <option key={p} value={p}>
                  {STT_LABELS[p] || p}
                </option>
              ))}
            </select>
          </Field>

          {cfg.stt_provider === "openai_whisper" && (
            <Field
              label={`OpenAI API Key ${cfg.has_openai_key ? "(gesetzt)" : ""}`}
            >
              <div className="flex gap-2">
                <input
                  type="password"
                  value={openaiKey}
                  onChange={(e) => setOpenaiKey(e.target.value)}
                  placeholder={
                    cfg.has_openai_key ? "•••••••• (ändern)" : "sk-..."
                  }
                  className="flex-1 rounded-md bg-background border border-foreground/10 px-3 py-2 text-sm font-mono"
                />
                <button
                  onClick={() => {
                    patch({ voice_openai_api_key: openaiKey });
                    setOpenaiKey("");
                  }}
                  disabled={!openaiKey || saving}
                  className="rounded-md bg-foreground/[0.06] hover:bg-foreground/[0.10] px-3 text-xs disabled:opacity-40"
                >
                  Speichern
                </button>
              </div>
            </Field>
          )}

          {/* TTS */}
          <Field label="Text-to-Speech Engine">
            <select
              value={cfg.tts_provider}
              onChange={(e) => patch({ voice_tts_provider: e.target.value })}
              disabled={saving}
              className="w-full rounded-md bg-background border border-foreground/10 px-3 py-2 text-sm"
            >
              {cfg.available_tts.map((p) => (
                <option key={p} value={p}>
                  {TTS_LABELS[p] || p}
                </option>
              ))}
            </select>
          </Field>

          {(cfg.tts_provider === "edge_tts" || cfg.tts_provider === "azure_speech") && cfg.available_voices.length > 0 && (
            <Field label="Stimme">
              <select
                value={cfg.tts_voice}
                onChange={(e) => patch({ voice_tts_voice: e.target.value })}
                disabled={saving}
                className="w-full rounded-md bg-background border border-foreground/10 px-3 py-2 text-sm"
              >
                {cfg.available_voices.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.label}
                  </option>
                ))}
              </select>
            </Field>
          )}

          {cfg.tts_provider === "elevenlabs" && (
            <Field
              label={`ElevenLabs API Key ${cfg.has_elevenlabs_key ? "(gesetzt)" : ""}`}
            >
              <div className="flex gap-2">
                <input
                  type="password"
                  value={elevenKey}
                  onChange={(e) => setElevenKey(e.target.value)}
                  placeholder={
                    cfg.has_elevenlabs_key ? "•••••••• (ändern)" : "el_..."
                  }
                  className="flex-1 rounded-md bg-background border border-foreground/10 px-3 py-2 text-sm font-mono"
                />
                <button
                  onClick={() => {
                    patch({ voice_elevenlabs_api_key: elevenKey });
                    setElevenKey("");
                  }}
                  disabled={!elevenKey || saving}
                  className="rounded-md bg-foreground/[0.06] hover:bg-foreground/[0.10] px-3 text-xs disabled:opacity-40"
                >
                  Speichern
                </button>
              </div>
            </Field>
          )}

          {(cfg.stt_provider === "azure_speech" || cfg.tts_provider === "azure_speech") && (
            <Field
              label={`Azure Speech Key + Region ${cfg.has_azure_speech_key ? "(gesetzt)" : ""}`}
            >
              <div className="space-y-2">
                <input
                  type="text"
                  value={azureRegion}
                  onChange={(e) => setAzureRegion(e.target.value)}
                  placeholder={cfg.azure_speech_region || "Region, z.B. germanywestcentral"}
                  className="w-full rounded-md bg-background border border-foreground/10 px-3 py-2 text-sm font-mono"
                />
                <div className="flex gap-2">
                  <input
                    type="password"
                    value={azureKey}
                    onChange={(e) => setAzureKey(e.target.value)}
                    placeholder={cfg.has_azure_speech_key ? "•••••••• (ändern)" : "Azure Speech Key"}
                    className="flex-1 rounded-md bg-background border border-foreground/10 px-3 py-2 text-sm font-mono"
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
                    className="rounded-md bg-foreground/[0.06] hover:bg-foreground/[0.10] px-3 text-xs disabled:opacity-40"
                  >
                    Speichern
                  </button>
                </div>
                <p className="text-[10px] text-muted-foreground/50">
                  Offizielle Microsoft-Stimmen (gleiche IDs wie Edge) + STT über euren Azure-Speech-Key. Region z.B. germanywestcentral.
                </p>
              </div>
            </Field>
          )}

          {/* LLM */}
          <Field label="Interaction-Agent Modell">
            <select
              value={cfg.llm_model}
              onChange={(e) => patch({ voice_llm_model: e.target.value })}
              disabled={saving}
              className="w-full rounded-md bg-background border border-foreground/10 px-3 py-2 text-sm"
            >
              {Object.entries(LLM_LABELS).map(([id, label]) => (
                <option key={id} value={id}>
                  {label}
                </option>
              ))}
            </select>
          </Field>

          {/* Language */}
          <Field label="Sprache (Default = de, auto = Auto-Erkennung)">
            <input
              type="text"
              defaultValue={cfg.language || ""}
              onBlur={(e) => patch({ voice_language: e.target.value || "" })}
              placeholder="de, en, fr, auto, ..."
              className="w-full rounded-md bg-background border border-foreground/10 px-3 py-2 text-sm"
            />
          </Field>
        </div>
      </div>
    </section>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="block text-[11px] font-medium text-muted-foreground/80 mb-1.5">
        {label}
      </label>
      {children}
    </div>
  );
}
