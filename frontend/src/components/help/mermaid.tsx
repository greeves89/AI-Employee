"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Client-only Mermaid renderer. Mermaid touches the DOM, so it must not run during
 * SSR — it is dynamically imported on mount. Renders the diagram to inline SVG; on
 * any parse/render error it falls back to showing the raw diagram source so the page
 * never breaks.
 */
let counter = 0;

export function Mermaid({ chart, caption }: { chart: string; caption?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({
          startOnLoad: false,
          theme: "dark",
          securityLevel: "strict",
          flowchart: { curve: "basis", htmlLabels: true },
          themeVariables: { fontFamily: "inherit" },
        });
        const id = `mmd-${++counter}`;
        const { svg } = await mermaid.render(id, chart.trim());
        if (!cancelled && ref.current) ref.current.innerHTML = svg;
      } catch {
        if (!cancelled) setError(true);
      }
    })();
    return () => { cancelled = true; };
  }, [chart]);

  return (
    <figure className="my-4 rounded-xl border border-border bg-foreground/[0.02] p-4">
      {error ? (
        <pre className="overflow-x-auto text-[11px] leading-relaxed text-muted-foreground">{chart.trim()}</pre>
      ) : (
        <div ref={ref} className="mermaid-host flex justify-center overflow-x-auto [&_svg]:max-w-full [&_svg]:h-auto" />
      )}
      {caption && (
        <figcaption className="mt-2 text-center text-[11px] text-muted-foreground/60">{caption}</figcaption>
      )}
    </figure>
  );
}
