"use client";

import { useRef, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

interface DocxViewerProps {
  fileData: ArrayBuffer;
}

export default function DocxViewer({ fileData }: DocxViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!fileData || !containerRef.current) return;
    setLoading(true);
    setError(null);

    import("docx-preview").then(({ renderAsync }) => {
      renderAsync(fileData, containerRef.current!, undefined, {
        className: "docx-preview",
        inWrapper: true,
        ignoreWidth: false,
        ignoreHeight: false,
        ignoreFonts: false,
        breakPages: true,
        ignoreLastRenderedPageBreak: true,
      })
        .then(() => setLoading(false))
        .catch((e: Error) => {
          setError(e.message);
          setLoading(false);
        });
    });
  }, [fileData]);

  return (
    <div className="h-full overflow-auto bg-white relative">
      {loading && (
        <div className="flex items-center justify-center absolute inset-0 z-10 bg-white/80">
          <Loader2 className="h-5 w-5 animate-spin text-zinc-400" />
        </div>
      )}
      {error && (
        <div className="flex items-center justify-center h-full text-red-500 text-sm">
          {error}
        </div>
      )}
      <div ref={containerRef} className="min-h-full" />
    </div>
  );
}
