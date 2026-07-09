"use client";

/**
 * JarvisCore — the animated "presence" of the voice agent.
 *
 * Pure Tailwind (compiled utility classes + arbitrary-value animation timing),
 * no external deps and no injected <style>, so it stays inside the app's strict
 * CSP. Reacts to the session state: idle breathing, listening pulse, speaking
 * ripple, a rotating arc while thinking/connecting.
 */

type Theme = { ring: string; core: string; glow: string; iris: string };

const THEMES: Record<string, Theme> = {
  listening: {
    ring: "bg-fuchsia-500",
    core: "from-fuchsia-400 via-fuchsia-500 to-purple-700",
    glow: "shadow-[0_0_60px_-5px_rgba(217,70,239,0.7)]",
    iris: "bg-fuchsia-200/30",
  },
  speaking: {
    ring: "bg-emerald-500",
    core: "from-emerald-300 via-emerald-500 to-teal-700",
    glow: "shadow-[0_0_65px_-5px_rgba(16,185,129,0.75)]",
    iris: "bg-emerald-100/30",
  },
  processing: {
    ring: "bg-amber-500",
    core: "from-amber-300 via-amber-500 to-orange-700",
    glow: "shadow-[0_0_55px_-5px_rgba(245,158,11,0.7)]",
    iris: "bg-amber-100/30",
  },
  connecting: {
    ring: "bg-zinc-500",
    core: "from-zinc-400 via-zinc-500 to-zinc-700",
    glow: "shadow-[0_0_40px_-8px_rgba(161,161,170,0.5)]",
    iris: "bg-white/20",
  },
  ready: {
    ring: "bg-indigo-500",
    core: "from-indigo-400 via-violet-500 to-indigo-700",
    glow: "shadow-[0_0_50px_-6px_rgba(99,102,241,0.6)]",
    iris: "bg-indigo-100/25",
  },
  error: {
    ring: "bg-red-500",
    core: "from-red-400 via-red-500 to-rose-700",
    glow: "shadow-[0_0_45px_-6px_rgba(239,68,68,0.6)]",
    iris: "bg-red-100/25",
  },
};

/** `compact`: a calmer, smaller presence — the orb should breathe in the corner of
 *  your eye, not dominate the cockpit. Used by the full-height Speech tab. */
export function JarvisCore({ state, compact = false }: { state: string; compact?: boolean }) {
  const t = THEMES[state] ?? THEMES.ready;
  const rippling = state === "listening" || state === "speaking";
  const spinning = state === "processing" || state === "connecting";
  const breathing = !rippling && !spinning && state !== "error";
  const s = compact
    ? { box: "h-32 w-32", r1: "h-20 w-20", r2: "h-28 w-28", arc: "h-24 w-24", halo: "h-24 w-24", core: "h-16 w-16" }
    : { box: "h-52 w-52", r1: "h-36 w-36", r2: "h-48 w-48", arc: "h-44 w-44", halo: "h-40 w-40", core: "h-28 w-28" };
  // Soften the bloom in compact mode — same hue, far less shout.
  const glow = compact ? `${t.glow} opacity-90 saturate-[0.85]` : t.glow;

  return (
    <div className={`relative flex ${s.box} items-center justify-center select-none`}>
      {/* Expanding ripples while listening/speaking */}
      {rippling && (
        <>
          <span
            className={`absolute ${s.r1} rounded-full ${t.ring} ${compact ? "opacity-15" : "opacity-25"} animate-ping [animation-duration:1800ms]`}
          />
          <span
            className={`absolute ${s.r2} rounded-full ${t.ring} ${compact ? "opacity-[0.07]" : "opacity-10"} animate-ping [animation-duration:2600ms] [animation-delay:400ms]`}
          />
        </>
      )}

      {/* Rotating arc while thinking / connecting */}
      {spinning && (
        <span
          className={`absolute ${s.arc} rounded-full border-2 border-transparent border-t-current animate-spin ${
            state === "processing" ? "text-amber-400" : "text-zinc-400"
          } [animation-duration:1400ms]`}
        />
      )}

      {/* Steady halo ring */}
      <span className={`absolute ${s.halo} rounded-full border ${t.ring} border-opacity-20`} />

      {/* The core orb */}
      <div
        className={`relative flex ${s.core} items-center justify-center rounded-full bg-gradient-to-br ${t.core} ${glow} transition-all duration-500 ${
          state === "speaking"
            ? "scale-110 animate-pulse [animation-duration:700ms]"
            : breathing
            ? "animate-pulse [animation-duration:3800ms]"
            : ""
        }`}
      >
        {/* glass highlight */}
        <div className="absolute left-4 top-3 h-8 w-8 rounded-full bg-white/40 blur-md" />
        {/* iris */}
        <div className={`h-14 w-14 rounded-full ${t.iris} border border-white/25 backdrop-blur-sm`} />
      </div>
    </div>
  );
}
